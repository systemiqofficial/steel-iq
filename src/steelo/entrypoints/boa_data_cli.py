"""CLI for preparing the BOA input data: static geo files and cost fixtures.

Geo side: the pinned Natural Earth shapefiles and ERA5 land-sea mask are installed
into ``<boa root>/data/`` from the ``boa-data`` S3 package, and the per-pixel iso3
grid is built locally from the 1:50m shapefile (``boa.geo.iso3_grid_builder``).

Cost side: a scenario is a whole workbook variant (hand-edited copy of the master
excel). The four sheets the vendored ``boa`` package reads are extracted into
``<boa root>/costs/<scenario>/boa_cost_data.xlsx``, which doubles as the provenance
record of the cost data a run used. ``run_boa --costs <scenario>`` consumes it.
"""

import argparse
import hashlib
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    track,
)

from ..data import DataManager

console = Console()

# Sheet -> {master column: boa column}. Sheet names are kept as-is; the renamed columns
# are what boa's loaders read (boa.inputs.costs and boa.geo.geospatial.CountryMappings).
SHEET_RENAMES: dict[str, dict[str, str]] = {
    "RES CAPEX projections": {},
    "RES OPEX": {},
    "Cost of capital": {"ISO-3 Code": "Code"},
    "Country mapping": {"ISO 3-letter code": "Code"},
}


# Contents of the boa-data S3 package, installed verbatim into <boa root>/data/.
GEO_DATA_PACKAGE = "boa-data"
GEO_DATA_ITEMS = ("ne_50m_admin_0_map_subunits", "ne_10m_admin_1_states_provinces", "lsm_025_deg.nc")


def _install_geo_data(data_dir: Path) -> None:
    """Copy any missing NE shapefiles / land-sea mask from the boa-data package into ``data_dir``."""
    missing = [item for item in GEO_DATA_ITEMS if not (data_dir / item).exists()]
    if not missing:
        return
    console.print(
        f"Fetching the [cyan]{GEO_DATA_PACKAGE}[/cyan] package "
        f"(pinned NE shapefiles + ERA5 land-sea mask) for: {', '.join(missing)}"
    )
    manager = DataManager()
    manager.download_package(GEO_DATA_PACKAGE, force=False)
    package_dir = manager.get_package_path(GEO_DATA_PACKAGE)
    if package_dir is None:
        raise ValueError(f"Failed to resolve the {GEO_DATA_PACKAGE} package")
    data_dir.mkdir(parents=True, exist_ok=True)
    for item in missing:
        source = package_dir / item
        if not source.exists():
            raise ValueError(f"{GEO_DATA_PACKAGE} package is missing {item}")
        if source.is_dir():
            shutil.copytree(source, data_dir / item)
        else:
            shutil.copy2(source, data_dir / item)
        console.print(f"Installed [cyan]{item}[/cyan] into {data_dir}")


def _prepare_geo_data(data_dir: Path, iso3_grid_path: Path, subunits_shapefile_path: Path) -> None:
    """Ensure the static geo inputs exist and build the per-pixel iso3 grid from them."""
    from boa.geo.iso3_grid_builder import BUILD_STAGE_COUNT, build_iso3_grid_from_shapefile, iso3_grid_is_current

    _install_geo_data(data_dir)
    if not iso3_grid_is_current(iso3_grid_path, subunits_shapefile_path):
        if iso3_grid_path.exists():
            console.print("iso3 grid is stale (built from a different NE 1:50m shapefile); rebuilding.")
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Building the iso3 grid...", total=BUILD_STAGE_COUNT)

            def on_stage(description: str) -> None:
                progress.update(task, description=f"Building the iso3 grid: {description}", advance=1)

            # force=True: a stale grid is only replaced once the new one is written.
            build_iso3_grid_from_shapefile(
                iso3_grid_path, shapefile_path=subunits_shapefile_path, force=True, on_stage=on_stage
            )
            progress.update(task, description="Built the iso3 grid", completed=BUILD_STAGE_COUNT)
    console.print("[green]✓ Geo data ready (NE shapefiles, land-sea mask, iso3 grid).[/green]")


def _resolve_source(input_file: str | None) -> Path:
    """Use the given workbook path, else download the master-input package (as steelo-data-prepare does)."""
    if input_file:
        path = Path(input_file)
        if not path.exists():
            raise FileNotFoundError(f"Input workbook not found: {path}")
        return path
    manager = DataManager()
    manager.download_package("master-input", force=False)
    cache_path = manager.get_package_path("master-input")
    if cache_path is None or not (workbook := cache_path / "master_input.xlsx").exists():
        raise ValueError("Failed to resolve the master-input package workbook")
    return workbook


def _extract_sheets(source: Path) -> dict[str, pd.DataFrame]:
    available = set(pd.ExcelFile(source).sheet_names)
    missing = sorted(set(SHEET_RENAMES) - available)
    if missing:
        raise ValueError(f"{source.name} is missing sheet(s) required by boa: {missing}")
    sheets = {}
    for name, renames in track(SHEET_RENAMES.items(), description="Extracting sheets...", console=console):
        sheets[name] = pd.read_excel(source, sheet_name=name).rename(columns=renames)
    # boa only consumes the Renewables rows; drop the steel/hydrogen techs. Territories
    # sharing a sovereign's ISO-3 code (e.g. Canarias/Spain) duplicate rows in the master
    # sheet; collapse same-value duplicates — boa keys by code and rejects duplicates, so
    # conflicting values still fail the smoke test.
    # reset_index so the frame matches a re-read of the written sheet in _sheets_match.
    cost_of_capital = sheets["Cost of capital"]
    sheets["Cost of capital"] = (
        cost_of_capital[cost_of_capital["Tech"] == "Renewables"]
        .drop_duplicates(subset=["Code", "Tech", "Cost of capital"])
        .reset_index(drop=True)
    )
    return sheets


def _sheets_match(target: Path, sheets: dict[str, pd.DataFrame]) -> bool:
    """Semantic comparison — xlsx bytes are not stable across writes, DataFrames are."""
    try:
        existing_names = set(pd.ExcelFile(target).sheet_names)
    except Exception:
        return False
    if existing_names != set(sheets):
        return False
    return all(pd.read_excel(target, sheet_name=name).equals(df) for name, df in sheets.items())


def _smoke_test(fixture: Path) -> None:
    """Run boa's cost loader on the fixture so a bad extraction fails at prepare time, not mid-run."""
    from boa.geo.geospatial import CountryMappings
    from boa.inputs.costs import preprocess_renewable_energy_cost_data

    mappings = CountryMappings.from_excel(fixture)
    code_map = {k: v for k, v in mappings.code_to_irena_region_map.items() if isinstance(v, str)}
    iso3_df = pd.DataFrame({"iso3": list(code_map)})
    preprocess_renewable_energy_cost_data(iso3_df, code_map, fixture)


def _cache_years(sheets: dict[str, pd.DataFrame], args: argparse.Namespace) -> range:
    """All years available in the RES CAPEX projections sheet, narrowed by the --year_* flags."""
    available = sorted(c for c in sheets["RES CAPEX projections"].columns if isinstance(c, (int, np.integer)))
    start = args.year_start if args.year_start is not None else available[0]
    end = args.year_end if args.year_end is not None else available[-1]
    step = args.year_step if args.year_step is not None else 1
    return range(int(start), int(end) + 1, step)


def _build_cost_cache(input_data_path: Path, cost_cache_dir: Path, years: range) -> None:
    """Pre-build the per-year cost cache the model would otherwise build on first run."""
    from boa.inputs.costs import process_global_baseload_simulation_costs

    for year in track(years, description="Building cost cache...", console=console):
        process_global_baseload_simulation_costs(year, input_data_path, cost_cache_dir)
    console.print(
        f"[green]✓ Cost cache ready for {len(years)} year(s) "
        f"{years.start}-{years.stop - 1} (step {years.step}) in {cost_cache_dir}[/green]"
    )


def boa_data_prepare():
    """Install the static geo data, then extract the boa cost sheets into costs/<scenario>/boa_cost_data.xlsx."""
    from boa.config.paths import PathConfig

    parser = argparse.ArgumentParser(
        description=(
            "Prepare BOA input data: install the static geo files (NE shapefiles, land-sea mask, iso3 grid) "
            "and extract cost fixtures (costs/<scenario>/boa_cost_data.xlsx) from a master excel workbook."
        )
    )
    parser.add_argument(
        "--input-file",
        type=str,
        help="Path to the source workbook (default: download the master-input package from S3).",
    )
    parser.add_argument(
        "--scenario",
        type=str,
        default="default",
        help="Cost-set name under <boa root>/costs/ (default: default).",
    )
    parser.add_argument("--year_start", type=int, help="First cache year (default: earliest in the sheet).")
    parser.add_argument("--year_end", type=int, help="Last cache year (default: latest in the sheet).")
    parser.add_argument("--year_step", type=int, help="Step between cache years (default: 1).")

    try:
        started = time.monotonic()
        args = parser.parse_args()

        source = _resolve_source(args.input_file)
        console.print(f"Extracting boa sheets from [cyan]{source}[/cyan]")
        sheets = _extract_sheets(source)

        paths = PathConfig.from_auto_detect(cost_set=args.scenario)
        _prepare_geo_data(paths.data_dir, paths.iso3_grid_path, paths.subunits_50m_shapefile_path)
        target = paths.input_data_path
        if target.exists() and _sheets_match(target, sheets):
            console.print(f"[green]✓ Costs scenario '{args.scenario}' is already up to date.[/green]")
            _build_cost_cache(target, paths.cost_cache_dir, _cache_years(sheets, args))
            console.print(f"Run a full BOA simulation with it via [cyan]run_boa --costs {args.scenario}[/cyan]")
            console.print(f"[green]boa-data-prepare completed in {time.monotonic() - started:.1f} s[/green]")
            return "Already up to date"

        target.parent.mkdir(parents=True, exist_ok=True)
        staged = target.with_suffix(".staged.xlsx")
        try:
            with pd.ExcelWriter(staged) as writer:
                for name, df in track(sheets.items(), description="Writing cost data workbook...", console=console):
                    df.to_excel(writer, sheet_name=name, index=False)
            _smoke_test(staged)
            replaced = target.exists()
            staged.replace(target)
        finally:
            staged.unlink(missing_ok=True)

        if paths.cost_cache_dir.exists():
            shutil.rmtree(paths.cost_cache_dir)
            console.print("Removed the scenario's stale cost cache.")

        provenance = {
            "scenario": args.scenario,
            "prepared_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source_workbook": str(source.resolve()),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        }
        (paths.costs_dir / "source.json").write_text(json.dumps(provenance, indent=2) + "\n")

        verb = "Updated" if replaced else "Created"
        console.print(f"[green]✓ {verb} costs scenario '{args.scenario}'[/green] [dim]({target})[/dim]")
        _build_cost_cache(target, paths.cost_cache_dir, _cache_years(sheets, args))
        console.print(f"Run a full BOA simulation with it via [cyan]run_boa --costs {args.scenario}[/cyan]")
        console.print(f"[green]boa-data-prepare completed in {time.monotonic() - started:.1f} s[/green]")
        return f"{verb} scenario '{args.scenario}'"

    except Exception as e:
        console.print(f"[red]✗ boa-data-prepare failed: {e}[/red]")
        sys.exit(1)


def main() -> None:
    """Console-script entry: swallow the status string so success exits 0 (sys.exit(str) would exit 1)."""
    boa_data_prepare()
