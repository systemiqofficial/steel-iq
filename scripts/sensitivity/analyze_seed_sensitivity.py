"""Compare outcomes across the seed-only runs in a run_sweep.py sweep directory, to
answer: how much does randomness that probabilistic_agents=False can't eliminate
(evaluation order, geospatial siting lottery, announcement/construction draws -- see
uncertainty_study_considerations.md §2 rows 6-11) move the final technology mix and
where plants end up?

Reads --sweep-dir/manifest.csv (written by run_sweep.py) plus each successful run's
post_processed_*.csv (FG-level per-year data: year, region, country, iso3, technology,
product, capacity, ...) and data/market_prices_*.csv (steel/iron/scrap price series).
post_processed has no lat/lon, so "plant locations" here means capacity share by
region/country -- coarser than a map, but it's what's available and it's exactly the
granularity the geospatial siting lottery (rows 6-11) operates at. Writes, all under
--out-dir:

  - final_tech_share_by_seed.csv: capacity share by (product, technology) in the final
    simulated year, one row per seed plus range/std/n_seeds_present across seeds -- the
    primary technology-mix comparison. A seed that builds zero capacity of a given
    technology counts as 0% share (not dropped), so range/std reflect the full swing
    between "built" and "never built"; n_seeds_present says how many of the seeds built
    any capacity of it at all, since a technology built in only 1-2 seeds can still show
    a small range/std purely because its typical share is small. Range/std close to 0
    supports "this randomness doesn't matter here"; a wide spread means it does.
  - tech_share_over_time_by_seed.csv: same shares, every year (long format), so
    divergence that shows up mid-run and later cancels out isn't missed by only
    looking at the final year.
  - capacity_share_by_tech_over_time.png: mean +/- range across seeds, one line per
    technology, faceted by product.
  - final_location_share_by_seed.csv: capacity share by country (iso3) in the final
    year, one row per seed plus range/std/n_seeds_present -- the location analogue of
    the tech-share table (same zero-fill treatment for a seed with no capacity there).
  - location_share_over_time_by_seed.csv: capacity share by region, every year, long
    format.
  - capacity_share_by_region_over_time.png: mean +/- range across seeds, one line per
    region.
  - price_trajectories.png: steel/iron price per seed overlaid -- secondary to the
    above, but a free cross-check of whether the §9 n=1 "2045 price jump" observation
    is a robust feature (present, same year, across all seeds) or a seed artifact.

Usage:
    uv run python -m scripts.sensitivity.analyze_seed_sensitivity \\
        --sweep-dir outputs/sensitivity/co2_ramp_seed_sweep \\
        --out-dir outputs/sensitivity/co2_ramp_seed_sweep/analysis
"""

import argparse
import csv
import json
import logging
from pathlib import Path
from typing import cast

import matplotlib.pyplot as plt
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def _load_manifest(sweep_dir: Path) -> list[dict]:
    manifest_path = sweep_dir / "manifest.csv"
    with manifest_path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    runs = []
    for row in rows:
        if row["status"] != "success":
            logger.warning(f"Skipping {row['run_id']}: status={row['status']}")
            continue
        params = json.loads(row["params_json"])
        if "random_seed" not in params:
            raise ValueError(f"{row['run_id']}: params_json has no random_seed -- is this a seed sweep manifest?")
        runs.append({"run_id": row["run_id"], "output_dir": Path(row["output_dir"]), "seed": params["random_seed"]})
    if not runs:
        raise ValueError(f"No successful runs found in {manifest_path}")
    return runs


def _find_one(directory: Path, pattern: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if not matches:
        raise FileNotFoundError(f"No file matching {pattern!r} under {directory}")
    return matches[-1]  # timestamped filenames sort chronologically; take the newest


def _load_run(run: dict) -> dict:
    post_processed_path = _find_one(run["output_dir"], "post_processed_*.csv")
    market_prices_path = _find_one(run["output_dir"], "data/market_prices_*.csv")
    post_processed = pd.read_csv(post_processed_path)
    market_prices = pd.read_csv(market_prices_path)
    return {**run, "post_processed": post_processed, "market_prices": market_prices}


def _share_within_product_over_time(post_processed: pd.DataFrame, group_col: str) -> pd.Series:
    """Capacity share of `group_col` (e.g. technology) within its product, per year --
    index (year, product, group_col)."""
    by_group = post_processed.groupby(["year", "product", group_col])["capacity"].sum()
    by_product = post_processed.groupby(["year", "product"])["capacity"].sum()
    return (by_group / by_product).rename("capacity_share")


def _share_of_total_over_time(post_processed: pd.DataFrame, group_col: str) -> pd.Series:
    """Capacity share of `group_col` (e.g. iso3, region) of that year's global total,
    across both products -- index (year, group_col)."""
    by_group = post_processed.groupby(["year", group_col])["capacity"].sum()
    total = post_processed.groupby("year")["capacity"].sum()
    return (by_group / total).rename("capacity_share")


def _final_year_slice(share_series: pd.Series) -> pd.Series:
    final_year = share_series.index.get_level_values("year").max()
    return cast(pd.Series, share_series.xs(final_year, level="year"))


def _summarize_across_seeds(per_seed_final: dict[int, pd.Series]) -> pd.DataFrame:
    table = pd.concat(per_seed_final, names=["seed"]).unstack(list(next(iter(per_seed_final.values())).index.names))
    # A (product, technology)/(iso3) combo missing for a seed means that seed built zero
    # capacity of it, not "unknown" -- unstack() leaves it NaN, which max()/min()/std()
    # silently skip (pandas default skipna=True). Left unfilled, a technology built in
    # 4 of 5 seeds looks like a small range (only the 4 non-zero seeds are compared)
    # instead of the true, much larger swing between "built" and "not built at all".
    n_seeds_present = table.notna().sum()
    table = table.fillna(0.0)
    table.loc["range"] = table.max() - table.min()
    table.loc["std"] = table.iloc[:-1].std()
    table.loc["n_seeds_present"] = n_seeds_present
    return table


def _plot_share_over_time(
    per_seed_series: dict[int, pd.Series], group_levels: list[str], title: str, out_path: Path
) -> None:
    combined = pd.concat(per_seed_series, names=["seed"]).unstack("seed")
    if "product" in group_levels:
        products = combined.index.get_level_values("product").unique()
        fig, axes = plt.subplots(len(products), 1, figsize=(10, 4 * len(products)), sharex=True)
        if len(products) == 1:
            axes = [axes]
        product_groups = [(p, cast(pd.DataFrame, combined.xs(p, level="product"))) for p in products]
    else:
        fig, ax = plt.subplots(figsize=(10, 5))
        axes = [ax]
        product_groups = [(None, combined)]

    for ax, (product, product_slice) in zip(axes, product_groups):
        group_col = [level for level in group_levels if level != "product"][0]
        for group_value in product_slice.index.get_level_values(group_col).unique():
            over_time = cast(pd.DataFrame, product_slice.xs(group_value, level=group_col))
            mean = over_time.mean(axis=1)
            ax.plot(mean.index.to_numpy(), mean.to_numpy(), label=group_value)
            ax.fill_between(
                mean.index.to_numpy(),
                over_time.min(axis=1).to_numpy(),
                over_time.max(axis=1).to_numpy(),
                alpha=0.2,
            )
        ax.set_ylabel(f"{product} capacity share" if product else "Capacity share")
        ax.legend(fontsize="small", ncol=2)

    axes[-1].set_xlabel("Year")
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sweep-dir", type=Path, required=True, help="run_sweep.py --out-dir")
    parser.add_argument("--out-dir", type=Path, required=True, help="Where to write comparison outputs")
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    runs = [_load_run(run) for run in _load_manifest(args.sweep_dir)]
    runs.sort(key=lambda r: r["seed"])
    logger.info(f"Loaded {len(runs)} run(s): seeds={[r['seed'] for r in runs]}")

    # --- technology mix ---
    tech_share_series = {
        run["seed"]: _share_within_product_over_time(run["post_processed"], "technology") for run in runs
    }

    tech_over_time_path = args.out_dir / "tech_share_over_time_by_seed.csv"
    pd.concat(tech_share_series, names=["seed"]).to_csv(tech_over_time_path)
    logger.info(f"Wrote {tech_over_time_path}")

    tech_final_table = _summarize_across_seeds({seed: _final_year_slice(s) for seed, s in tech_share_series.items()})
    tech_final_path = args.out_dir / "final_tech_share_by_seed.csv"
    tech_final_table.to_csv(tech_final_path)
    logger.info(f"Wrote {tech_final_path}")
    logger.info(f"Max range across seeds, technology share: {tech_final_table.loc['range'].max():.4f}")

    _plot_share_over_time(
        tech_share_series,
        group_levels=["product", "technology"],
        title=f"Technology share of capacity over time (mean +/- range across {len(runs)} seeds)",
        out_path=args.out_dir / "capacity_share_by_tech_over_time.png",
    )

    # --- plant locations (capacity share by country/region -- no lat/lon in post_processed) ---
    location_series_by_country = {run["seed"]: _share_of_total_over_time(run["post_processed"], "iso3") for run in runs}
    location_final_table = _summarize_across_seeds(
        {seed: _final_year_slice(s) for seed, s in location_series_by_country.items()}
    )
    location_final_path = args.out_dir / "final_location_share_by_seed.csv"
    location_final_table.to_csv(location_final_path)
    logger.info(f"Wrote {location_final_path}")
    logger.info(f"Max range across seeds, country capacity share: {location_final_table.loc['range'].max():.4f}")

    location_series_by_region = {
        run["seed"]: _share_of_total_over_time(run["post_processed"], "region") for run in runs
    }
    location_over_time_path = args.out_dir / "location_share_over_time_by_seed.csv"
    pd.concat(location_series_by_region, names=["seed"]).to_csv(location_over_time_path)
    logger.info(f"Wrote {location_over_time_path}")

    _plot_share_over_time(
        location_series_by_region,
        group_levels=["region"],
        title=f"Regional capacity share over time (mean +/- range across {len(runs)} seeds)",
        out_path=args.out_dir / "capacity_share_by_region_over_time.png",
    )

    # --- price (secondary cross-check, see §9) ---
    price_columns = [c for c in runs[0]["market_prices"].columns if c != "year"]
    fig, axes = plt.subplots(len(price_columns), 1, figsize=(10, 4 * len(price_columns)), sharex=True)
    if len(price_columns) == 1:
        axes = [axes]
    for ax, column in zip(axes, price_columns):
        for run in runs:
            prices = run["market_prices"]
            ax.plot(prices["year"], prices[column], label=f"seed={run['seed']}")
        ax.set_ylabel(column)
        ax.legend()
    axes[-1].set_xlabel("Year")
    fig.suptitle("Price trajectories by seed (cross-check: does the ~2045 step-change repeat across seeds?)")
    fig.tight_layout()
    fig.savefig(args.out_dir / "price_trajectories.png", dpi=150)
    plt.close(fig)

    logger.info(f"Done. Outputs in {args.out_dir}")


if __name__ == "__main__":
    main()
