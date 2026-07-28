"""Plot results produced by `run_benchmark.py`.

Two figures:

1. Site map + per-site-by-year panel -- `site_map.png` shows each site's (lat, lon)
   colored by its mean PyPSA-optimal LCOE; `site_lcoe_by_year.png` is a small-multiples
   grid, one bar chart per site, of PyPSA LCOE across weather years. `run_benchmark.py`
   sweeps a single `--year` per run and its output CSV has no `year` column -- run it
   once per weather year and concatenate the CSVs yourself, adding a `year` column, to
   unlock the by-year panel. Without a `year` column every row is treated as one
   implicit year and the by-year panel is skipped.

2. Sample-size convergence -- for one (site, year, standing_loss), two stacked panels
   sharing the n_samples x-axis: LCOE (top) and runtime (bottom). Each panel draws one
   line per coverage threshold: a dashed horizontal PyPSA reference (constant, doesn't
   depend on n_samples) and a solid line connecting the BOA-sampling mean across seeds,
   with a min/max band. Deliberately two stacked panels rather than one dual-axis plot
   -- LCOE and runtime are different units/scales and a shared y-axis invites spurious
   visual correlation between them.

Usage:
    uv run python scripts/boa_benchmark/plot_benchmark.py --csv boa_benchmark_results.csv
    uv run python scripts/boa_benchmark/plot_benchmark.py --csv results.csv --skip-map \\
        --sites north_sea_coast --years 2025
"""

import argparse
import logging
from pathlib import Path

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import matplotlib.pyplot as plt
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Fixed categorical order (never cycled/reassigned) for coverage thresholds -- sorted
# ascending and zipped positionally, so a given threshold keeps its color across plots
# as long as the same sorted set of thresholds is passed in.
COVERAGE_COLORS = ["#1b9e77", "#d95f02", "#7570b3", "#e7298a", "#66a61e", "#e6ab02"]
PYPSA_REFERENCE_COLOR = "#555555"


def _coverage_palette(coverage_thresholds: list[float]) -> dict[float, str]:
    ordered = sorted(coverage_thresholds)
    if len(ordered) > len(COVERAGE_COLORS):
        raise ValueError(
            f"{len(ordered)} coverage thresholds requested but only {len(COVERAGE_COLORS)} palette colors "
            "defined -- add more colors to COVERAGE_COLORS."
        )
    return dict(zip(ordered, COVERAGE_COLORS))


def _resolve_single_value(df: pd.DataFrame, col: str, cli_value: float | None, arg_name: str) -> float:
    if cli_value is not None:
        return cli_value
    values = sorted(df[col].unique())
    if len(values) == 1:
        return values[0]
    raise ValueError(f"Multiple {col} values present ({values}); pass --{arg_name} to pick one.")


def _ensure_year_column(df: pd.DataFrame) -> pd.DataFrame:
    if "year" not in df.columns:
        logger.warning("No 'year' column in CSV -- treating all rows as a single weather year.")
        df = df.copy()
        df["year"] = "single"
    return df


def plot_site_map(df: pd.DataFrame, out_path: Path, coverage_threshold: float, standing_loss: float) -> None:
    """Scatter of sites on a world map, colored by mean PyPSA LCOE across weather years."""
    sub = df[(df["coverage_threshold"] == coverage_threshold) & (df["standing_loss"] == standing_loss)]
    if sub.empty:
        raise ValueError(
            f"No rows at coverage_threshold={coverage_threshold}, standing_loss={standing_loss}. "
            f"Available coverage_thresholds={sorted(df['coverage_threshold'].unique())}, "
            f"standing_losses={sorted(df['standing_loss'].unique())}."
        )
    site_summary = sub.groupby(["site", "lat", "lon"])["pypsa_lcoe"].mean().reset_index()

    fig = plt.figure(figsize=(11, 6))
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree())
    ax.add_feature(cfeature.LAND, facecolor="#f0f0f0")
    ax.add_feature(cfeature.OCEAN, facecolor="#ffffff")
    ax.add_feature(cfeature.COASTLINE, linewidth=0.4)
    ax.add_feature(cfeature.BORDERS, linewidth=0.2)
    ax.set_global()

    sc = ax.scatter(
        site_summary["lon"],
        site_summary["lat"],
        c=site_summary["pypsa_lcoe"],
        cmap="viridis",
        s=140,
        edgecolor="black",
        linewidth=0.6,
        transform=ccrs.PlateCarree(),
        zorder=3,
    )
    for _, row in site_summary.iterrows():
        ax.annotate(
            row["site"],
            (row["lon"], row["lat"]),
            xycoords=ccrs.PlateCarree()._as_mpl_transform(ax),
            textcoords="offset points",
            xytext=(6, 4),
            fontsize=8,
            zorder=4,
        )

    fig.colorbar(sc, ax=ax, label="Mean PyPSA LCOE", shrink=0.7, pad=0.02)
    ax.set_title(f"Site PyPSA-optimal LCOE (coverage_threshold={coverage_threshold}, standing_loss={standing_loss})")
    fig.tight_layout()
    fig.savefig(out_path, dpi=200)
    plt.close(fig)
    logger.info(f"Wrote {out_path}")


def plot_site_lcoe_by_year(df: pd.DataFrame, out_path: Path, coverage_threshold: float, standing_loss: float) -> None:
    """Small-multiples grid, one bar chart per site, of PyPSA LCOE across weather years."""
    sub = df[(df["coverage_threshold"] == coverage_threshold) & (df["standing_loss"] == standing_loss)]
    if sub.empty:
        raise ValueError(f"No rows at coverage_threshold={coverage_threshold}, standing_loss={standing_loss}.")
    if sub["year"].nunique() <= 1:
        logger.warning("Only one weather year present -- skipping site_lcoe_by_year (nothing to compare).")
        return

    site_year_lcoe = sub.groupby(["site", "year"])["pypsa_lcoe"].mean().reset_index()
    sites = sorted(site_year_lcoe["site"].unique())
    years = sorted(site_year_lcoe["year"].unique(), key=str)

    ncols = min(4, len(sites))
    nrows = -(-len(sites) // ncols)  # ceil
    fig, axes = plt.subplots(nrows, ncols, figsize=(3.2 * ncols, 2.8 * nrows), squeeze=False, sharey=True)

    for i, site in enumerate(sites):
        ax = axes[i // ncols][i % ncols]
        site_df = site_year_lcoe[site_year_lcoe["site"] == site].set_index("year").reindex(years)
        ax.bar([str(y) for y in years], site_df["pypsa_lcoe"], color=COVERAGE_COLORS[0])
        ax.set_title(site, fontsize=9)
        ax.tick_params(axis="x", labelrotation=45, labelsize=7)

    for j in range(len(sites), nrows * ncols):
        axes[j // ncols][j % ncols].axis("off")

    fig.supylabel("PyPSA LCOE")
    fig.suptitle(
        f"Per-site LCOE by weather year (coverage_threshold={coverage_threshold}, standing_loss={standing_loss})"
    )
    fig.tight_layout()
    fig.savefig(out_path, dpi=200)
    plt.close(fig)
    logger.info(f"Wrote {out_path}")


def plot_sample_convergence(
    df: pd.DataFrame,
    out_path: Path,
    site: str,
    year: object,
    standing_loss: float,
    soc_mode: str,
    coverage_thresholds: list[float] | None = None,
) -> None:
    """Two stacked panels (LCOE, runtime) sharing an n_samples x-axis, one line per
    coverage threshold, for a single site/year/standing_loss."""
    filtered = df[(df["site"] == site) & (df["year"] == year) & (df["standing_loss"] == standing_loss)]
    if filtered.empty:
        raise ValueError(f"No rows for site={site}, year={year}, standing_loss={standing_loss}.")

    if coverage_thresholds is None:
        coverage_thresholds = sorted(filtered["coverage_threshold"].unique())
    palette = _coverage_palette(coverage_thresholds)

    lcoe_col = f"boa_{soc_mode}_lcoe"
    seconds_col = f"boa_{soc_mode}_seconds"

    fig, (ax_lcoe, ax_time) = plt.subplots(2, 1, sharex=True, figsize=(8, 7), gridspec_kw={"height_ratios": [2, 1]})

    for coverage in coverage_thresholds:
        cov_df = filtered[filtered["coverage_threshold"] == coverage]
        if cov_df.empty:
            continue
        color = palette[coverage]

        pypsa_lcoe = cov_df["pypsa_lcoe"].mean()
        ax_lcoe.axhline(pypsa_lcoe, color=color, linestyle="--", linewidth=1, alpha=0.7)

        lcoe_stats = cov_df.groupby("n_samples")[lcoe_col].agg(["mean", "min", "max"]).sort_index()
        ax_lcoe.plot(lcoe_stats.index, lcoe_stats["mean"], marker="o", color=color, label=f"{coverage:.0%} coverage")
        ax_lcoe.fill_between(lcoe_stats.index, lcoe_stats["min"], lcoe_stats["max"], color=color, alpha=0.15)

        total_seconds = cov_df["boa_sample_seconds"] + cov_df[seconds_col]
        time_stats = total_seconds.groupby(cov_df["n_samples"]).mean().sort_index()
        ax_time.plot(time_stats.index, time_stats.values, marker="o", color=color)
        ax_time.axhline(cov_df["pypsa_solve_seconds"].mean(), color=color, linestyle="--", linewidth=1, alpha=0.7)

    ax_lcoe.plot([], [], color=PYPSA_REFERENCE_COLOR, linestyle="--", label="PyPSA optimum (dashed)")
    ax_lcoe.set_xscale("log")
    ax_lcoe.set_ylabel("LCOE")
    ax_lcoe.set_title(f"{site} ({year}) -- BOA sampling convergence [{soc_mode}, standing_loss={standing_loss}]")
    ax_lcoe.legend(title="Coverage threshold", fontsize=8)

    ax_time.set_xscale("log")
    ax_time.set_xlabel("n_samples")
    ax_time.set_ylabel("Runtime (s)\n(dashed = PyPSA solve)")

    fig.tight_layout()
    fig.savefig(out_path, dpi=200)
    plt.close(fig)
    logger.info(f"Wrote {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("scripts/boa_benchmark/plots"))
    parser.add_argument(
        "--coverage-threshold",
        type=float,
        default=None,
        help="Coverage threshold for the map/by-year plots (required if the CSV has more than one).",
    )
    parser.add_argument(
        "--standing-loss",
        type=float,
        default=None,
        help="Standing loss to use everywhere (required if the CSV has more than one).",
    )
    parser.add_argument("--soc-mode", choices=["cyclic", "empty_start"], default="cyclic")
    parser.add_argument(
        "--sites",
        type=str,
        default=None,
        help="Comma-separated site names for the convergence plot (default: all sites in the CSV).",
    )
    parser.add_argument(
        "--years",
        type=str,
        default=None,
        help="Comma-separated weather years for the convergence plot (default: all years in the CSV).",
    )
    parser.add_argument("--skip-map", action="store_true")
    parser.add_argument("--skip-convergence", action="store_true")
    args = parser.parse_args()

    df = _ensure_year_column(pd.read_csv(args.csv))
    args.out_dir.mkdir(parents=True, exist_ok=True)

    standing_loss = _resolve_single_value(df, "standing_loss", args.standing_loss, "standing-loss")

    if not args.skip_map:
        coverage_threshold = _resolve_single_value(
            df, "coverage_threshold", args.coverage_threshold, "coverage-threshold"
        )
        plot_site_map(df, args.out_dir / "site_map.png", coverage_threshold, standing_loss)
        plot_site_lcoe_by_year(df, args.out_dir / "site_lcoe_by_year.png", coverage_threshold, standing_loss)

    if not args.skip_convergence:
        sites = args.sites.split(",") if args.sites else sorted(df["site"].unique())
        years = args.years.split(",") if args.years else sorted(df["year"].unique(), key=str)
        convergence_dir = args.out_dir / "convergence"
        convergence_dir.mkdir(parents=True, exist_ok=True)
        for site in sites:
            for year in years:
                site_year_df = df[(df["site"].astype(str) == str(site)) & (df["year"].astype(str) == str(year))]
                if site_year_df.empty:
                    logger.warning(f"No rows for site={site}, year={year} -- skipping.")
                    continue
                out_path = convergence_dir / f"{site}_{year}.png"
                plot_sample_convergence(df, out_path, site, site_year_df["year"].iloc[0], standing_loss, args.soc_mode)


if __name__ == "__main__":
    main()
