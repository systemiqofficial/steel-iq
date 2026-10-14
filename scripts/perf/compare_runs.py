"""Compare benchmark runs made with bench_run.py: CPU time per stage and output equality.

Usage:

    uv run python scripts/perf/compare_runs.py <run_dir_a> <run_dir_b> [<run_dir_c> ...]

Each argument is the --out-dir of a bench_run.py run. The first run is the reference.

Prints, per run: total CPU and wall time, peak memory, the CPU time per stage summed over all
years, and the CPU time of the start-up and post-run work outside the year loop. Then, for every
run after the first, compares the outputs with the reference:

- the SHA-256 of ``post_processed_*.csv`` and of the market price CSV (equal hashes mean the runs
  are byte-identical);
- when the hashes differ, the largest relative difference per numeric column of the
  post-processed table, matched on (furnace_group_id, year).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

OUTPUT_PATTERNS = {
    "post_processed": "post_processed_*.csv",
    "market_prices": "**/market_prices_*.csv",
}
KEY_COLUMNS = ["furnace_group_id", "year"]


def load_stage_table(run_dir: Path) -> pd.DataFrame:
    return pd.read_csv(run_dir / "run" / "stage_timing.csv")


def load_summary(run_dir: Path) -> dict:
    return json.loads((run_dir / "bench_summary.json").read_text(encoding="utf-8"))


def find_output(run_dir: Path, pattern: str) -> Path | None:
    matches = sorted((run_dir / "run").glob(pattern))
    return matches[-1] if matches else None


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def print_timing(run_dirs: list[Path]) -> None:
    tables = {d: load_stage_table(d) for d in run_dirs}
    summaries = {d: load_summary(d) for d in run_dirs}
    stages = list(dict.fromkeys(s for t in tables.values() for s in t["stage"]))
    labels = [d.name for d in run_dirs]
    width = max(12, *(len(label) for label in labels)) + 2

    print(f"{'':<28}" + "".join(f"{label:>{width}}" for label in labels))
    for name, getter in (
        ("total cpu_s", lambda d: summaries[d]["cpu_s"]),
        ("total wall_s", lambda d: summaries[d]["wall_s"]),
        ("peak memory MB", lambda d: summaries[d]["peak_memory_mb"]),
    ):
        print(f"{name:<28}" + "".join(f"{getter(d):>{width}.0f}" for d in run_dirs))
    year_cpu = {d: tables[d].loc[tables[d]["stage"] == "year_complete", "cpu_s"].sum() for d in run_dirs}
    print(
        f"{'outside year loop cpu_s':<28}"
        + "".join(f"{summaries[d]['cpu_s'] - year_cpu[d]:>{width}.0f}" for d in run_dirs)
    )
    print()
    print("cpu_s per stage, all years")
    for stage in stages:
        values = [tables[d].loc[tables[d]["stage"] == stage, "cpu_s"].sum() for d in run_dirs]
        print(f"{stage:<28}" + "".join(f"{v:>{width}.1f}" for v in values))
    print()
    print("year_complete cpu_s / wall_s per year")
    years = sorted(set(tables[run_dirs[0]]["year"].dropna().astype(int)))
    for year in years:
        cells = []
        for d in run_dirs:
            rows = tables[d][(tables[d]["stage"] == "year_complete") & (tables[d]["year"] == year)]
            cells.append(f"{rows['cpu_s'].sum():.0f}/{rows['wall_s'].sum():.0f}")
        print(f"{year:<28}" + "".join(f"{c:>{width}}" for c in cells))


def compare_tables(reference: Path, other: Path) -> None:
    a = pd.read_csv(reference).set_index(KEY_COLUMNS).sort_index()
    b = pd.read_csv(other).set_index(KEY_COLUMNS).sort_index()
    if not a.index.equals(b.index):
        print(
            f"    row keys differ: {len(a)} against {len(b)} rows, {len(a.index.symmetric_difference(b.index))} unmatched"
        )
        common = a.index.intersection(b.index)
        a, b = a.loc[common], b.loc[common]
    worst = []
    for column in a.select_dtypes("number").columns:
        if column not in b.columns:
            continue
        x, y = a[column].astype(float), b[column].astype(float)
        scale = x.abs().clip(lower=1e-12)
        diff = ((x - y).abs() / scale).where(~(x.isna() & y.isna()), 0.0)
        if diff.max() > 0:
            worst.append((diff.max(), column, int((diff > 0).sum())))
    worst.sort(reverse=True)
    print(f"    {len(worst)} numeric columns differ; largest relative differences:")
    for rel, column, count in worst[:8]:
        print(f"      {column}: {rel:.3g} ({count} rows)")


def compare_outputs(run_dirs: list[Path]) -> None:
    reference = run_dirs[0]
    for other in run_dirs[1:]:
        print(f"{other.name} against {reference.name}")
        for name, pattern in OUTPUT_PATTERNS.items():
            ref_file, other_file = find_output(reference, pattern), find_output(other, pattern)
            if ref_file is None or other_file is None:
                print(f"  {name}: missing ({'reference' if ref_file is None else other.name})")
                continue
            same = sha256(ref_file) == sha256(other_file)
            print(f"  {name}: {'identical' if same else 'DIFFERENT'}")
            if not same and name == "post_processed":
                compare_tables(ref_file, other_file)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dirs", nargs="+", type=Path)
    args = parser.parse_args()
    run_dirs = [d.resolve() for d in args.run_dirs]
    print_timing(run_dirs)
    if len(run_dirs) > 1:
        print()
        compare_outputs(run_dirs)


if __name__ == "__main__":
    main()
