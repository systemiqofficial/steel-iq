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
  - final_emissions_by_region_by_seed.csv / final_emissions_by_technology_by_seed.csv:
    absolute emissions (tCO2e) by region, and by (product, technology), in the final
    year -- one row per seed plus range/std/n_seeds_present, same zero-fill treatment as
    the capacity tables. Every emissions_{boundary}_{scope} column post_processed has
    (see DataCollector) is kept as its own metric rather than summed together --
    boundaries (e.g. plant_boundary vs supply_chain) are alternative accounting
    conventions, not additive line items.
  - emissions_by_region_over_time_by_seed.csv / emissions_by_technology_over_time_by_seed.csv:
    same totals, every year, long format.
  - emissions_by_region_over_time__{boundary}.png / emissions_by_technology_over_time__{boundary}.png:
    one file per emissions boundary (boundaries are alternative accounting conventions,
    not directly comparable, so never combined into one figure) -- mean +/- range across
    seeds, one line per region/technology, faceted by scope within that boundary.
  - price_trajectories.png: steel/iron price per seed overlaid -- secondary to the
    above, but a free cross-check of whether the §9 n=1 "2045 price jump" observation
    is a robust feature (present, same year, across all seeds) or a seed artifact.

Trade flows between regions are explicitly NOT covered here -- the underlying bilateral
flow data isn't captured by any existing run (see TRADE_FLOWS_HANDOFF.md at repo root).

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


# The fixed set calculate_emissions.calculate_emissions returns per boundary --
# boundary names are open-ended (e.g. "rs-inspired", "worldsteel_no_opt_credits") and
# may themselves contain underscores, so metric columns are parsed by matching one of
# these as a known suffix rather than splitting on a fixed underscore position.
_EMISSIONS_SCOPES = ("direct_with_biomass_ghg", "direct_ghg", "indirect_ghg")


def _split_emissions_metric(metric: str) -> tuple[str, str]:
    """Split an 'emissions_{boundary}_{scope}' column name into (boundary, scope)."""
    body = metric.removeprefix("emissions_")
    for scope in _EMISSIONS_SCOPES:
        suffix = f"_{scope}"
        if body.endswith(suffix):
            return body[: -len(suffix)], scope
    raise ValueError(f"Could not parse boundary/scope from emissions column {metric!r}")


def _emissions_columns(post_processed: pd.DataFrame) -> list[str]:
    """Every `emissions_{boundary}_{scope}` column DataCollector wrote (see
    src/steelo/domain/datacollector.py's record[f"emissions_{boundary}_{scope}"]).
    Boundaries (e.g. "plant_boundary", "supply_chain") are alternative accounting
    conventions, not additive with each other, so each column is kept as its own
    metric throughout rather than summed into one "total emissions" number."""
    return sorted(c for c in post_processed.columns if c.startswith("emissions_"))


def _emissions_sum_over_time(post_processed: pd.DataFrame, group_col: str, emissions_cols: list[str]) -> pd.Series:
    """Total tCO2e of each emissions metric by `group_col` (e.g. region), per year --
    index (year, group_col, metric). Absolute totals, not shares: unlike capacity,
    summing emissions across boundaries would double-count, so there's no meaningful
    "share of total" here the way there is for capacity."""
    by_group = post_processed.groupby(["year", group_col])[emissions_cols].sum()
    stacked = cast(pd.Series, by_group.stack())
    return stacked.rename_axis([*by_group.index.names, "metric"]).rename("emissions_tco2e")


def _emissions_sum_within_product_over_time(
    post_processed: pd.DataFrame, group_col: str, emissions_cols: list[str]
) -> pd.Series:
    """Total tCO2e of each emissions metric by (product, `group_col`) (e.g. technology),
    per year -- index (year, product, group_col, metric)."""
    by_group = post_processed.groupby(["year", "product", group_col])[emissions_cols].sum()
    stacked = cast(pd.Series, by_group.stack())
    return stacked.rename_axis([*by_group.index.names, "metric"]).rename("emissions_tco2e")


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
    per_seed_series: dict[int, pd.Series],
    color_level: str,
    title: str,
    out_path: Path,
    facet_level: str | None = "product",
    y_label: str = "Capacity share",
) -> None:
    """One line per `color_level` value (e.g. technology, region, or emissions metric),
    mean +/- range across seeds. `facet_level` (e.g. product) gets one subplot each;
    pass None for a single-panel plot (e.g. region/metric series with no product axis)."""
    combined = pd.concat(per_seed_series, names=["seed"]).unstack("seed")
    if facet_level is not None:
        facet_values = combined.index.get_level_values(facet_level).unique()
        fig, axes = plt.subplots(len(facet_values), 1, figsize=(10, 4 * len(facet_values)), sharex=True)
        if len(facet_values) == 1:
            axes = [axes]
        facet_groups = [(v, cast(pd.DataFrame, combined.xs(v, level=facet_level))) for v in facet_values]
    else:
        fig, ax = plt.subplots(figsize=(10, 5))
        axes = [ax]
        facet_groups = [(None, combined)]

    for ax, (facet_value, facet_slice) in zip(axes, facet_groups):
        for group_value in facet_slice.index.get_level_values(color_level).unique():
            over_time = cast(pd.DataFrame, facet_slice.xs(group_value, level=color_level))
            mean = over_time.mean(axis=1)
            ax.plot(mean.index.to_numpy(), mean.to_numpy(), label=group_value)
            ax.fill_between(
                mean.index.to_numpy(),
                over_time.min(axis=1).to_numpy(),
                over_time.max(axis=1).to_numpy(),
                alpha=0.2,
            )
        ax.set_ylabel(f"{facet_value} {y_label.lower()}" if facet_value else y_label)
        ax.legend(fontsize="small", ncol=2)

    axes[-1].set_xlabel("Year")
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def _plot_emissions_by_boundary(
    per_seed_series: dict[int, pd.Series],
    color_level: str,
    title_prefix: str,
    n_seeds: int,
    out_dir: Path,
    out_stem: str,
) -> None:
    """One plot file per emissions boundary, faceted by scope within that boundary --
    NOT one plot faceted by (boundary, scope) together, since boundaries are alternative
    accounting conventions for the same underlying emissions (see _emissions_columns) and
    plotting them side by side as if they were comparable slices would be misleading."""
    by_boundary: dict[str, dict[int, pd.Series]] = {}
    for seed, series in per_seed_series.items():
        df = series.reset_index()
        df[["boundary", "scope"]] = df["metric"].apply(lambda m: pd.Series(_split_emissions_metric(m)))
        other_levels = [name for name in series.index.names if name != "metric"]
        for boundary, group in df.groupby("boundary"):
            indexed = cast(pd.Series, group.set_index([*other_levels, "scope"])[series.name])
            by_boundary.setdefault(str(boundary), {})[seed] = indexed

    for boundary, series_by_seed in sorted(by_boundary.items()):
        _plot_share_over_time(
            series_by_seed,
            color_level=color_level,
            facet_level="scope",
            y_label="Emissions (tCO2e)",
            title=f"{title_prefix} -- {boundary!r} boundary (mean +/- range across {n_seeds} seeds)",
            out_path=out_dir / f"{out_stem}__{boundary}.png",
        )


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
        color_level="technology",
        facet_level="product",
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
        color_level="region",
        facet_level=None,
        title=f"Regional capacity share over time (mean +/- range across {len(runs)} seeds)",
        out_path=args.out_dir / "capacity_share_by_region_over_time.png",
    )

    # --- emissions by region and by technology (absolute tCO2e, not shares -- boundaries
    # aren't additive with each other, see _emissions_columns) ---
    emissions_cols = _emissions_columns(runs[0]["post_processed"])
    if not emissions_cols:
        logger.warning("No emissions_* columns found in post_processed data -- skipping emissions analysis")
    else:
        logger.info(f"Found {len(emissions_cols)} emissions metric(s): {emissions_cols}")

        emissions_by_region_series = {
            run["seed"]: _emissions_sum_over_time(run["post_processed"], "region", emissions_cols) for run in runs
        }
        emissions_region_over_time_path = args.out_dir / "emissions_by_region_over_time_by_seed.csv"
        pd.concat(emissions_by_region_series, names=["seed"]).to_csv(emissions_region_over_time_path)
        logger.info(f"Wrote {emissions_region_over_time_path}")

        emissions_region_final_table = _summarize_across_seeds(
            {seed: _final_year_slice(s) for seed, s in emissions_by_region_series.items()}
        )
        emissions_region_final_path = args.out_dir / "final_emissions_by_region_by_seed.csv"
        emissions_region_final_table.to_csv(emissions_region_final_path)
        logger.info(f"Wrote {emissions_region_final_path}")

        _plot_emissions_by_boundary(
            emissions_by_region_series,
            color_level="region",
            title_prefix="Regional emissions over time",
            n_seeds=len(runs),
            out_dir=args.out_dir,
            out_stem="emissions_by_region_over_time",
        )

        emissions_by_tech_series = {
            run["seed"]: _emissions_sum_within_product_over_time(run["post_processed"], "technology", emissions_cols)
            for run in runs
        }
        emissions_tech_over_time_path = args.out_dir / "emissions_by_technology_over_time_by_seed.csv"
        pd.concat(emissions_by_tech_series, names=["seed"]).to_csv(emissions_tech_over_time_path)
        logger.info(f"Wrote {emissions_tech_over_time_path}")

        emissions_tech_final_table = _summarize_across_seeds(
            {seed: _final_year_slice(s) for seed, s in emissions_by_tech_series.items()}
        )
        emissions_tech_final_path = args.out_dir / "final_emissions_by_technology_by_seed.csv"
        emissions_tech_final_table.to_csv(emissions_tech_final_path)
        logger.info(f"Wrote {emissions_tech_final_path}")

        # Sum across products first (a technology's total emissions across both steel and
        # iron) -- plotting facets on scope already, faceting on product too would be a lot
        # of near-empty panels since most technologies only make one product.
        emissions_by_tech_across_products = {
            seed: s.groupby(["year", "technology", "metric"]).sum() for seed, s in emissions_by_tech_series.items()
        }
        _plot_emissions_by_boundary(
            emissions_by_tech_across_products,
            color_level="technology",
            title_prefix="Technology emissions over time, both products combined",
            n_seeds=len(runs),
            out_dir=args.out_dir,
            out_stem="emissions_by_technology_over_time",
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
