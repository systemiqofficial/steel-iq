#!/usr/bin/env python
"""CDS input-data pipeline CLI: `boa-cds-prepare` and `boa-cds-download`.

Builds the model's input Zarr stores (renewable profiles + max-capacity
ceilings) for one input set. The raw monthly files (dataset
sis-energy-global-reanalysis) live in the single-slot raw dir
(`<root>/data/cds/`); stores are built into the input set's staging dir and
promoted into its live dir (`<root>/inputs/<set>/cds-zarr/`), which is what
the model reads.

`boa-cds-prepare` is idempotent: it reuses every store already in the live dir
and builds + installs only what is missing for the requested weather year
(`--force` rebuilds everything). The input set is tagged automatically as
`cds-<year>`; pass `--inputs` only to override. `boa-cds-download` fetches the
raw monthly files from CDS (needs ~/.cdsapirc and `uv sync --extra cds`).

Examples:
    boa-cds-prepare --weather_year 2024          # builds + installs into inputs/cds-2024/
    boa-cds-prepare --weather_year 2024 --force  # rebuild everything
    boa-cds-download --year 2025
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import time
import warnings
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    track,
)

from boa.cds import availability as cds_availability
from boa.cds import convert as cds_convert
from boa.cds import download as cds_download
from boa.cds import install as cds_install
from boa.cds import max_capacity as cds_max_capacity
from boa.cds.spec import CDS_VARS, TECHS, lulc_nc_name, masks_extract_dir_name
from boa.config.paths import DEFAULT_SET, PathConfig
from boa.config.settings import CAPACITY_DENSITY_MW_PER_KM2, ERA5_DATA_YEAR, REGION_COORDS
from boa.store_schema import max_cap_store_stem, profile_store_stem

console = Console()


def _parser(prog: str, description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"boa-cds-{prog}",
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--inputs",
        default=None,
        help="Input-set name under <root>/inputs/ to build into (default: cds-<year>, from the command's year flag)",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser


def _setup_logging(verbose: bool) -> None:
    # Route the pipeline modules' logging through the shared rich console so
    # log lines print cleanly above any active progress bar.
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(console=console, show_time=False, show_path=False, show_level=False)],
        force=True,
    )
    warnings.filterwarnings("ignore", message="Consolidated metadata is currently not part in the Zarr format 3")


def _stage_progress() -> Progress:
    """Per-region progress bar in the boa-data-prepare style."""
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    )


def _add_density_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--pv-density",
        type=float,
        default=CAPACITY_DENSITY_MW_PER_KM2["pv"],
        help=f"Solar PV density in MW/km^2 (default: {CAPACITY_DENSITY_MW_PER_KM2['pv']})",
    )
    parser.add_argument(
        "--wind-density",
        type=float,
        default=CAPACITY_DENSITY_MW_PER_KM2["wind"],
        help=f"Wind density in MW/km^2 (default: {CAPACITY_DENSITY_MW_PER_KM2['wind']}; "
        "20.5 vs 10.42 is an open team decision)",
    )


def _missing_raw_techs(cds_dir: Path, year: int) -> list[str]:
    """Technologies whose 12 extracted monthly NetCDFs for `year` are not on disk."""
    missing = []
    for tech in CDS_VARS:
        try:
            cds_convert.cds_month_files(cds_dir, tech, year)
        except FileNotFoundError:
            missing.append(tech)
    return missing


def _add_layer_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--layers",
        default="",
        help=(
            "Comma-separated availability layers applied to the capacity ceiling "
            f"(known: {','.join(cds_availability.LAYER_ORDER)}; default: none, pure geometry). "
            "A layer set is part of the input-set identity, so a layered build lands in its "
            "own input set rather than overwriting the geometry-only stores."
        ),
    )
    parser.add_argument("--lulc-path", type=Path, help="ESA-CCI land-cover NetCDF (default: under the lulc dir)")
    parser.add_argument("--masks-dir", type=Path, help="CDS exclusion mask directory (default: under the raw CDS dir)")


def _resolve_layers(args: argparse.Namespace, path_config: PathConfig) -> list[cds_availability.LayerSpec]:
    """Turn `--layers` into configured specs, defaulting each layer's source path."""
    names = [name.strip() for name in args.layers.split(",") if name.strip()]
    if not names:
        return []
    return cds_availability.layer_specs(
        names,
        lulc_path=args.lulc_path or (path_config.lulc_dir / lulc_nc_name()),
        masks_dir=args.masks_dir or (path_config.cds_dir / masks_extract_dir_name()),
    )


def default_input_set(year: int, layer_names: list[str]) -> str:
    """
    The input-set name a prepare defaults to.

    The layer set is part of the input-set identity. Different ceilings then land in
    different zarr_dirs and, through them, different design-cache dirs, so a cache built
    against one ceiling cannot be reused by a run with another. Geometry-only keeps the
    bare `cds-<year>` name it has always had, which is correct rather than merely
    convenient: geometry-only is what every existing store already holds.
    """
    if not layer_names:
        return f"cds-{year}"
    return f"cds-{year}-{cds_availability.availability_tag(layer_names)}"


def _max_cap_rebuild_reason(live: Path, region: str, year: int, signature: str) -> str | None:
    """Why this region's ceiling store cannot be reused, or None if it can.

    Presence alone is not enough. The ceilings are baked into the design cache, so a store
    built from a different layer set or different densities is silently wrong rather than
    merely stale -- which is exactly the defect the signature exists to catch.
    """
    store = live / (max_cap_store_stem(region, year) + ".zarr")
    if not store.exists():
        return "missing"
    stored = cds_install.stored_signature(store)
    if stored is None:
        return "no availability signature (built before layers existed)"
    if stored != signature:
        return f"availability changed: {stored} -> {signature}"
    return None


def main_prepare(argv: list[str]) -> int:
    parser = _parser(
        "prepare",
        "Idempotently build + install the input set's profile and max-capacity stores for one weather year.",
    )
    parser.add_argument(
        "--weather_year", type=int, default=ERA5_DATA_YEAR, help=f"Weather year to prepare (default: {ERA5_DATA_YEAR})"
    )
    parser.add_argument(
        "--region", action="append", help="Region to prepare (repeatable; default: all production regions)"
    )
    _add_density_args(parser)
    _add_layer_args(parser)
    parser.add_argument(
        "--force", action="store_true", help="Rebuild and reinstall every region store even if it already exists"
    )
    parser.add_argument(
        "--rebuild-global",
        action="store_true",
        help="Also delete and rebuild the shared global intermediate (otherwise reused)",
    )
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    started = time.monotonic()
    regions = args.region or list(REGION_COORDS)
    year = args.weather_year

    layer_names = [name.strip() for name in args.layers.split(",") if name.strip()]
    input_set = args.inputs or default_input_set(year, layer_names)
    path_config = PathConfig.from_auto_detect(input_set=input_set)
    layers = _resolve_layers(args, path_config)
    densities = {"pv": args.pv_density, "wind": args.wind_density}
    signature = cds_availability.availability_signature(layers, densities)
    live = path_config.zarr_dir
    staging = path_config.cds_staging_dir
    console.print(
        f"[bold]boa-cds-prepare[/bold] — weather year [cyan]{year}[/cyan], input set [cyan]{input_set}[/cyan]"
    )
    console.print(f"  raw data    [dim]{path_config.cds_dir}[/dim]")
    console.print(f"  staging     [dim]{staging}[/dim]")
    console.print(f"  live stores [dim]{live}[/dim]")

    console.print(
        f"  availability [cyan]{','.join(layer_names) or 'none (pure geometry)'}[/cyan] [dim]{signature}[/dim]"
    )

    need_profile = [r for r in regions if args.force or not (live / (profile_store_stem(r, year) + ".zarr")).exists()]
    rebuild_reasons = {r: _max_cap_rebuild_reason(live, r, year, signature) for r in regions}
    need_max_cap = [r for r in regions if args.force or rebuild_reasons[r] is not None]
    for region in need_max_cap:
        if not args.force and rebuild_reasons[region] != "missing":
            console.print(f"  [yellow]rebuilding {region} max-capacity: {rebuild_reasons[region]}[/yellow]")
    complete = [r for r in regions if r not in need_profile and r not in need_max_cap]
    if complete:
        console.print(
            f"[green]✓ Reusing {len(complete)} complete region(s):[/green] {', '.join(complete)} "
            f"[dim](--force rebuilds)[/dim]"
        )
    if not need_profile and not need_max_cap:
        console.print(f"[green]✓ All {year} stores present — nothing to do.[/green]")
        return 0

    staging.mkdir(parents=True, exist_ok=True)

    if need_profile:
        global_store = cds_convert.global_store_path(path_config.cds_dir, year)
        if args.rebuild_global and global_store.exists():
            shutil.rmtree(global_store)
        if not global_store.exists():
            missing = _missing_raw_techs(path_config.cds_dir, year)
            if missing:
                console.print(
                    f"[red]✗ Raw CDS data for {year} ({', '.join(missing)}) not found under {path_config.cds_dir}[/red]"
                )
                console.print(f"  Fetch it with [cyan]boa-cds-download --year {year}[/cyan]")
                return 1
        use_global = global_store.exists() or len(need_profile) > 1
        if use_global:
            if global_store.exists():
                console.print(f"Reusing the global intermediate [dim]{global_store}[/dim]")
            else:
                with console.status(f"Building the global intermediate for {year} (one decompression pass)..."):
                    cds_convert.build_global_store(path_config.cds_dir, year, global_store)
        with _stage_progress() as progress:
            task = progress.add_task("Converting profiles", total=len(need_profile))
            for region in need_profile:
                progress.update(task, description=f"Converting profiles: {region}")
                cds_convert.convert_region(
                    region,
                    year,
                    list(CDS_VARS),
                    path_config.cds_dir,
                    staging,
                    path_config,
                    global_store=global_store if use_global else None,
                )
                progress.advance(task)
            progress.update(task, description="Converted profiles")
        console.print(f"[green]✓ Converted {len(need_profile)} profile store(s).[/green]")
    if need_max_cap:
        for region in track(need_max_cap, description="Building max-capacity...", console=console):
            cds_max_capacity.build_region(
                region,
                staging,
                path_config,
                layers=layers,
                pv_density=args.pv_density,
                wind_density=args.wind_density,
                year=year,
            )
        console.print(f"[green]✓ Built {len(need_max_cap)} max-capacity store(s).[/green]")

    try:
        if need_profile:
            cds_install.install_regions(
                need_profile, year, ["profile"], staging, live, force=args.force, kind_explicit=True
            )
        if need_max_cap:
            cds_install.install_regions(
                need_max_cap, year, ["max-cap"], staging, live, force=args.force, kind_explicit=True
            )
    except (FileNotFoundError, FileExistsError, ValueError) as e:
        console.print(f"[red]✗ install failed: {e}[/red]")
        return 1
    console.print(
        f"[green]✓ Installed {len(need_profile)} profile + {len(need_max_cap)} max-capacity store(s) into[/green] "
        f"[dim]{live}[/dim]"
    )
    console.print(f"Run the model against it via [cyan]boa-run ... --weather-input {input_set}[/cyan]")
    console.print(f"[green]boa-cds-prepare completed in {time.monotonic() - started:.1f} s[/green]")
    return 0


def main_download(argv: list[str]) -> int:
    parser = _parser("download", "Fetch hourly capacity factors from CDS (one request per technology per year).")
    parser.add_argument(
        "--tech", action="append", choices=list(TECHS), help="Technology to download (repeatable; default: both)"
    )
    parser.add_argument(
        "--year",
        action="append",
        help=f"Year to download (repeatable; default: {ERA5_DATA_YEAR}). One CDS request per year.",
    )
    parser.add_argument("--month", action="append", help="Month to download, e.g. 01 (repeatable; default: all 12)")
    parser.add_argument("--out-dir", type=Path, help="Output directory (default: data/cds/)")
    parser.add_argument("--dry-run", action="store_true", help="Print requests without downloading")
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    if not args.dry_run and not cds_download.cdsapi_available():
        logging.error(cds_download.CDSAPI_INSTALL_HINT)
        return 1

    path_config = PathConfig.from_auto_detect(input_set=DEFAULT_SET)  # raw dir is input-set-independent
    cds_download.download_capacity_factors(
        out_dir=args.out_dir or path_config.cds_dir,
        techs=args.tech or list(TECHS),
        years=args.year or [str(ERA5_DATA_YEAR)],
        months=args.month,
        dry_run=args.dry_run,
    )
    return 0


def prepare() -> int:
    """Console entry for `boa-cds-prepare`."""
    return main_prepare(sys.argv[1:])


def download() -> int:
    """Console entry for `boa-cds-download`."""
    return main_download(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(prepare())
