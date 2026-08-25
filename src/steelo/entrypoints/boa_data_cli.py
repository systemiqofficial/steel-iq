"""CLI for preparing the BOA cost fixtures from a master excel workbook.

A scenario is a whole workbook variant (hand-edited copy of the master excel). The
four sheets the vendored ``boa`` package reads are extracted into
``<boa root>/costs/<scenario>/boa_cost_data.xlsx``, which doubles as the provenance
record of the cost data a run used. ``run_boa --costs <scenario>`` consumes it.
"""

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from rich.console import Console
from rich.progress import track

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
    """Extract the boa cost sheets from a master excel into costs/<scenario>/boa_cost_data.xlsx."""
    from boa.config.paths import PathConfig

    parser = argparse.ArgumentParser(
        description="Prepare BOA cost fixtures (costs/<scenario>/boa_cost_data.xlsx) from a master excel workbook."
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
    parser.add_argument(
        "--full",
        action="store_true",
        help="Also pre-build the per-year cost cache for all years in the RES CAPEX projections sheet.",
    )
    parser.add_argument(
        "--year_start", type=int, help="First cache year (default: earliest in the sheet); implies --full."
    )
    parser.add_argument("--year_end", type=int, help="Last cache year (default: latest in the sheet); implies --full.")
    parser.add_argument("--year_step", type=int, help="Step between cache years (default: 1); implies --full.")

    try:
        args = parser.parse_args()
        build_cache = args.full or any(v is not None for v in (args.year_start, args.year_end, args.year_step))

        source = _resolve_source(args.input_file)
        console.print(f"Extracting boa sheets from [cyan]{source}[/cyan]")
        sheets = _extract_sheets(source)

        paths = PathConfig.from_auto_detect(cost_set=args.scenario)
        target = paths.input_data_path
        if target.exists() and _sheets_match(target, sheets):
            console.print(f"[green]✓ Costs scenario '{args.scenario}' is already up to date; cost cache kept.[/green]")
            if build_cache:
                _build_cost_cache(target, paths.cost_cache_dir, _cache_years(sheets, args))
            console.print(f"Run a full BOA simulation with it via [cyan]run_boa --costs {args.scenario}[/cyan]")
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
        if build_cache:
            _build_cost_cache(target, paths.cost_cache_dir, _cache_years(sheets, args))
        console.print(f"Run a full BOA simulation with it via [cyan]run_boa --costs {args.scenario}[/cyan]")
        return f"{verb} scenario '{args.scenario}'"

    except Exception as e:
        console.print(f"[red]✗ boa-data-prepare failed: {e}[/red]")
        sys.exit(1)


def main() -> None:
    """Console-script entry: swallow the status string so success exits 0 (sys.exit(str) would exit 1)."""
    boa_data_prepare()
