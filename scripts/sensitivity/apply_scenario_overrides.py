"""Patch specific data-prep fixture files with targeted scenario overrides, on a copy
of an already-prepared data directory.

The normal data pipeline (master Excel -> `steelo-data-prepare` -> `fixtures/*.json`)
is left untouched. This script runs *after* that, copies the prepared --data-dir to
--output-dir, and overwrites individual fixture files based on a small declarative
scenario YAML -- the same idiom scripts/sensitivity/grids/*.yaml already uses for
SimulationConfig sweeps. Each top-level key in the scenario YAML maps to one
fixture-specific patcher function below; adding a new kind of targeted override (e.g.
technology-ban timing) means adding one more key + one more patcher, not rewriting this
script.

Currently supported scenario keys:
  carbon_cost_trajectory: {type: linear_ramp, start_price, ramp_start_year, end_price,
                            ramp_end_year, hold_after} -- see scenarios/co2_ramp.yaml.
    Replaces (not adds to) fixtures/carbon_costs.json's per-ISO3 carbon_cost series
    with the same uniform schedule for every country.

Usage:
    uv run python -m scripts.sensitivity.apply_scenario_overrides \\
        --data-dir ~/.steelo/preparation_cache/<hash>/data \\
        --scenario scripts/sensitivity/scenarios/co2_ramp.yaml \\
        --output-dir ~/.steelo/preparation_cache/co2_ramp_scenario/data
"""

import argparse
import json
import shutil
from pathlib import Path

import yaml


def _linear_ramp_schedule(
    years: list[int],
    start_price: float,
    ramp_start_year: int,
    end_price: float,
    ramp_end_year: int,
    hold_after: bool,
) -> dict[str, float]:
    if not hold_after:
        # No scenario needs this yet -- decide what "after the ramp" means once one does,
        # rather than guessing now.
        raise NotImplementedError("linear_ramp currently only supports hold_after: true")

    schedule: dict[str, float] = {}
    for year in years:
        if year <= ramp_start_year:
            price = start_price
        elif year >= ramp_end_year:
            price = end_price
        else:
            frac = (year - ramp_start_year) / (ramp_end_year - ramp_start_year)
            price = start_price + frac * (end_price - start_price)
        schedule[str(year)] = round(price, 4)
    return schedule


def _apply_carbon_cost_trajectory(output_dir: Path, params: dict) -> None:
    fixture_path = output_dir / "fixtures" / "carbon_costs.json"
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    entries = data["root"]
    if not entries:
        raise ValueError(f"No carbon cost entries found in {fixture_path}")

    trajectory_type = params.get("type")
    if trajectory_type != "linear_ramp":
        raise NotImplementedError(f"Unsupported carbon_cost_trajectory type: {trajectory_type!r}")

    # Every ISO3 entry has the same set of year keys (see CarbonCostsJsonRepository) --
    # use the first entry's to build one schedule, then apply it uniformly to all.
    years = sorted(int(y) for y in entries[0]["carbon_cost"])
    schedule = _linear_ramp_schedule(
        years,
        start_price=params["start_price"],
        ramp_start_year=params["ramp_start_year"],
        end_price=params["end_price"],
        ramp_end_year=params["ramp_end_year"],
        hold_after=params.get("hold_after", True),
    )

    for entry in entries:
        entry["carbon_cost"] = dict(schedule)

    fixture_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"  {fixture_path}: {len(entries)} countries set to a uniform schedule ({years[0]}-{years[-1]})")


_PATCHERS = {
    "carbon_cost_trajectory": _apply_carbon_cost_trajectory,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, required=True, help="Prepared data directory (contains fixtures/)")
    parser.add_argument("--scenario", type=Path, required=True, help="Scenario YAML, e.g. scenarios/co2_ramp.yaml")
    parser.add_argument("--output-dir", type=Path, required=True, help="Where to write the patched copy of --data-dir")
    args = parser.parse_args()

    scenario = yaml.safe_load(args.scenario.read_text())
    unknown_keys = set(scenario) - set(_PATCHERS)
    if unknown_keys:
        raise ValueError(f"Unknown scenario key(s) {sorted(unknown_keys)}; supported: {sorted(_PATCHERS)}")

    print(f"Copying {args.data_dir} -> {args.output_dir}")
    shutil.copytree(args.data_dir, args.output_dir, dirs_exist_ok=True)

    for key, params in scenario.items():
        print(f"Applying override: {key}")
        _PATCHERS[key](args.output_dir, params)

    print("Done.")


if __name__ == "__main__":
    main()
