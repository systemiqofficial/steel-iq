"""Analysis for the calculate_npv_pct gating benchmark (npv_pct_tuning.yaml /
run_npv_pct_tuning.sbatch): baseline (current ungated 10% sample) vs npv_pct_full
(gated to 100%) vs npv_pct_narrowed (100% + priority_pct shrunk 5 -> 0.5), each run at
3 seeds plus a same-seed repeat. Answers the questions
TRADE_FLOWS_HANDOFF.md-adjacent work couldn't:

1. Runtime delta per config (stage-level, via parse_stage_timings.py's
   new_plant_opening_s bucket -- the geo_identify_opportunities operation, which wraps
   select_location_subset's calculate_npv_pct sampling).
2. Same-seed determinism: are the two seed=1 runs in each scenario byte-identical in
   their post_processed/market_prices output? (hashes each file; run_sweep.py's
   manifest doesn't capture this on its own.)
3. Tie-break exposure (priority_kpi.py's operation=priority_tiebreak log line, parsed
   by parse_stage_timings.py into priority_tiebreak_count): does narrowing priority_pct
   in npv_pct_narrowed increase how often the <20-unique-values branch fires, versus
   baseline/npv_pct_full?
4. Whether npv_pct_narrowed's tech mix / location choices differ from baseline's beyond
   added noise (a methodology shift, not just a variance change) -- delegates the
   within-scenario seed-spread computation to analyze_seed_sensitivity.py (run once per
   scenario against a seed-only manifest, excluding the repeat run), then diffs the
   three scenarios' mean shares against each other.

Expects --sweep-root to contain one subdirectory per scenario (baseline/,
npv_pct_full/, npv_pct_narrowed/), each with a manifest.csv (run_npv_pct_tuning.sbatch's
analyze step concatenates that scenario's manifest_fragments/*.csv into this) whose
run_ids are run_seed1/run_seed2/run_seed3/run_seed1_repeat.

Writes, under --out-dir:
  - stage_timing_summary.csv: parse_stage_timings.py's per-run breakdown, all 12 runs.
  - determinism_check.csv: one row per scenario, whether seed1 and seed1_repeat match.
  - tiebreak_summary.csv: total/mean operation=priority_tiebreak count by scenario.
  - <scenario>/seed_spread/...: analyze_seed_sensitivity.py's full output for that
    scenario's 3 distinct seeds.
  - cross_scenario_tech_share.csv / cross_scenario_location_share.csv: each scenario's
    mean-across-seeds share, side by side.

Usage:
    uv run python -m scripts.sensitivity.analyze_npv_pct_tuning \\
        --sweep-root outputs/sensitivity/npv_pct_tuning \\
        --out-dir outputs/sensitivity/npv_pct_tuning/analysis
"""

import argparse
import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from scripts.sensitivity.parse_stage_timings import parse_log

_SCENARIOS = ["baseline", "npv_pct_full", "npv_pct_narrowed"]
_SEED_ONLY_RUN_IDS = {"run_seed1", "run_seed2", "run_seed3"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _find_one(directory: Path, pattern: str) -> Path | None:
    matches = sorted(directory.glob(pattern))
    return matches[-1] if matches else None


def _load_manifest(scenario_dir: Path) -> list[dict]:
    manifest_path = scenario_dir / "manifest.csv"
    with manifest_path.open(newline="") as f:
        return list(csv.DictReader(f))


def _write_stage_timings(sweep_root: Path, out_dir: Path) -> None:
    fieldnames = [
        "config",
        "run_id",
        "seed",
        "wall_clock_s",
        "peak_rss_mb",
        "trade_module_s",
        "geospatial_s",
        "pam_s",
        "lp_build_s",
        "lp_solve_s",
        "npv_plant_decision_s",
        "new_plant_opening_s",
        "geo_update_status_s",
        "priority_tiebreak_count",
        "peak_lp_build_rss_mb",
        "peak_lp_solve_rss_mb",
    ]
    rows = []
    for scenario in _SCENARIOS:
        scenario_dir = sweep_root / scenario
        manifest_path = scenario_dir / "manifest.csv"
        if not manifest_path.exists():
            print(f"Skipping {scenario}: no manifest.csv")
            continue
        for row in _load_manifest(scenario_dir):
            seed = json.loads(row["params_json"])["random_seed"]
            log_path = Path(row["output_dir"]) / "task.err"
            if not log_path.exists():
                print(f"Skipping {scenario} {row['run_id']}: no task.err at {log_path}")
                continue
            stats = parse_log(log_path)
            rows.append({"config": scenario, "run_id": row["run_id"], "seed": seed, **stats})

    out_path = out_dir / "stage_timing_summary.csv"
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {out_path}")


def _check_determinism(sweep_root: Path, out_dir: Path) -> None:
    rows = []
    for scenario in _SCENARIOS:
        scenario_dir = sweep_root / scenario
        manifest_path = scenario_dir / "manifest.csv"
        if not manifest_path.exists():
            continue
        manifest = {row["run_id"]: row for row in _load_manifest(scenario_dir)}
        seed1 = manifest.get("run_seed1")
        repeat = manifest.get("run_seed1_repeat")
        if seed1 is None or repeat is None or seed1["status"] != "success" or repeat["status"] != "success":
            rows.append({"scenario": scenario, "match": "SKIPPED (missing or failed run)"})
            continue
        seed1_dir = Path(seed1["output_dir"])
        repeat_dir = Path(repeat["output_dir"])
        mismatches = []
        for pattern in ("post_processed_*.csv", "data/market_prices_*.csv"):
            a = _find_one(seed1_dir, pattern)
            b = _find_one(repeat_dir, pattern)
            if a is None or b is None:
                mismatches.append(f"{pattern}: file missing")
                continue
            if _sha256(a) != _sha256(b):
                mismatches.append(f"{pattern}: hash differs")
        rows.append({"scenario": scenario, "match": "IDENTICAL" if not mismatches else "; ".join(mismatches)})

    out_path = out_dir / "determinism_check.csv"
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["scenario", "match"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {out_path}")
    for row in rows:
        print(row)


def _run_seed_spread_analysis(sweep_root: Path, out_dir: Path) -> None:
    for scenario in _SCENARIOS:
        scenario_dir = sweep_root / scenario
        manifest_path = scenario_dir / "manifest.csv"
        if not manifest_path.exists():
            continue
        seed_only_rows = [row for row in _load_manifest(scenario_dir) if row["run_id"] in _SEED_ONLY_RUN_IDS]
        seed_spread_dir = out_dir / scenario / "seed_spread"
        seed_spread_dir.mkdir(parents=True, exist_ok=True)
        with (seed_spread_dir / "manifest.csv").open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["run_id", "output_dir", "status", "duration_s", "params_json"])
            for row in seed_only_rows:
                writer.writerow(
                    [row["run_id"], row["output_dir"], row["status"], row["duration_s"], row["params_json"]]
                )

        analysis_out = seed_spread_dir / "analysis"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.sensitivity.analyze_seed_sensitivity",
                "--sweep-dir",
                str(seed_spread_dir),
                "--out-dir",
                str(analysis_out),
            ],
            check=True,
        )


_SUMMARY_ROW_LABELS = {"range", "std", "n_seeds_present"}


def _mean_across_seeds(csv_path: Path, header_rows: list[int]) -> pd.Series:
    """Read one of analyze_seed_sensitivity.py's final_*_share_by_seed.csv tables
    (index: seed, plus 'range'/'std'/'n_seeds_present' summary rows; columns: the
    unstacked group levels -- (product, technology) for tech share, iso3 for location
    share) and return the mean share across the actual seed rows only."""
    df = pd.read_csv(csv_path, header=header_rows, index_col=0)
    seed_rows = df.loc[[idx for idx in df.index if str(idx) not in _SUMMARY_ROW_LABELS]]
    return seed_rows.mean()


def _cross_scenario_comparison(out_dir: Path, table_name: str, header_rows: list[int]) -> None:
    columns = {}
    for scenario in _SCENARIOS:
        csv_path = out_dir / scenario / "seed_spread" / "analysis" / table_name
        if not csv_path.exists():
            print(f"Skipping {scenario} for {table_name}: not found at {csv_path}")
            continue
        columns[scenario] = _mean_across_seeds(csv_path, header_rows)
    if not columns:
        return
    combined = pd.DataFrame(columns)
    out_path = out_dir / f"cross_scenario_{table_name}"
    combined.to_csv(out_path)
    print(f"Wrote {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--sweep-root", type=Path, required=True, help="Contains baseline/, npv_pct_full/, npv_pct_narrowed/"
    )
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    _write_stage_timings(args.sweep_root, args.out_dir)
    _check_determinism(args.sweep_root, args.out_dir)
    _run_seed_spread_analysis(args.sweep_root, args.out_dir)
    _cross_scenario_comparison(args.out_dir, "final_tech_share_by_seed.csv", header_rows=[0, 1])
    _cross_scenario_comparison(args.out_dir, "final_location_share_by_seed.csv", header_rows=[0])


if __name__ == "__main__":
    main()
