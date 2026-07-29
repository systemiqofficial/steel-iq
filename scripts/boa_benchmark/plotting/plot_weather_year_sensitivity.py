"""Plot `runners/run_weather_year_sensitivity.py`'s output CSV (columns: site, lat, lon, cost_region,
coverage_threshold, metric, standing_loss, soc_mode, method, year, lcoe, solar, wind,
battery, coverage, n_evaluations, seconds -- `method` in {"gbs", "gbs_robust"}, see that
script's docstring).

Two figure types:

1. World map (`weather_year_map.png`) -- each site colored by its "robustness premium":
   `(robust_lcoe - mean(per_year_lcoe)) / mean(per_year_lcoe) * 100`, i.e. how much more
   the design that must meet every weather year at once costs versus the average of each
   year's own hindsight-optimal design. This is the number that answers "is picking one
   weather year and building for it actually risky here" -- see README.md's weather-year
   sensitivity section for why this replaced simply averaging a fixed design's LCOE across
   years (it doesn't vary with weather at all, so that number would be a no-op).

2. Per-site year spread (`weather_year_spread.png`) -- one panel per site: a point per
   weather year's own optimal LCOE, plus a dashed horizontal line at the robust design's
   LCOE. The robust line landing *above every single year's own point* (not just the worst
   one) is expected, not a bug: different years can bind on different (solar, wind) mixes,
   so the design meeting all of them at once can cost more than even the worst year's own
   hindsight optimum -- see `core/gbs.py`'s `find_robust_gbs_design` docstring.

Sites with no feasible design in the search box for a given year, or jointly across years,
are excluded from that computation (logged, not silently dropped) rather than crashing --
see `runners/run_weather_year_sensitivity.py`'s NaN-row convention.

Usage:
    uv run python -m scripts.boa_benchmark.plotting.plot_weather_year_sensitivity \\
        --csv scripts/boa_benchmark/results/weather_year_sensitivity.csv
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


def _site_summary(df: pd.DataFrame) -> pd.DataFrame:
    """One row per site: per-year LCOE stats and the robust design's LCOE, dropping sites
    where either side is entirely infeasible (all-NaN) rather than propagating NaN silently
    into a plotted color/position."""
    per_year = df[df["method"] == "gbs"]
    robust = df[df["method"] == "gbs_robust"].set_index("site")["lcoe"]

    rows = []
    for site, site_years in per_year.groupby("site"):
        year_lcoes = site_years.set_index("year")["lcoe"].dropna()
        robust_lcoe = robust.get(site, float("nan"))
        infeasible_years = site_years["year"][site_years["lcoe"].isna()].tolist()
        if infeasible_years:
            logger.warning(f"{site}: no feasible design for year(s) {infeasible_years} -- excluded from its mean.")
        if year_lcoes.empty or pd.isna(robust_lcoe):
            logger.warning(f"{site}: excluded from summary (no feasible per-year design, or robust design, or both).")
            continue
        mean_lcoe = year_lcoes.mean()
        rows.append(
            {
                "site": site,
                "lat": site_years["lat"].iloc[0],
                "lon": site_years["lon"].iloc[0],
                "mean_lcoe": mean_lcoe,
                "min_lcoe": year_lcoes.min(),
                "max_lcoe": year_lcoes.max(),
                "spread_pct": (year_lcoes.max() - year_lcoes.min()) / mean_lcoe * 100,
                "robust_lcoe": robust_lcoe,
                "robust_premium_pct": (robust_lcoe - mean_lcoe) / mean_lcoe * 100,
            }
        )
    return pd.DataFrame(rows)


def plot_weather_year_map(df: pd.DataFrame, out_path: Path, coverage_threshold: float) -> None:
    """Scatter of sites on a world map, colored by robustness premium (%)."""
    summary = _site_summary(df)
    if summary.empty:
        raise ValueError("No site has both a feasible per-year and robust design -- nothing to map.")

    fig = plt.figure(figsize=(12.5, 6))
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree())
    ax.add_feature(cfeature.LAND, facecolor="#f0f0f0")
    ax.add_feature(cfeature.OCEAN, facecolor="#ffffff")
    ax.add_feature(cfeature.COASTLINE, linewidth=0.4)
    ax.add_feature(cfeature.BORDERS, linewidth=0.2)
    ax.set_global()

    sc = ax.scatter(
        summary["lon"],
        summary["lat"],
        c=summary["robust_premium_pct"],
        cmap="magma_r",
        vmin=0,
        s=140,
        edgecolor="black",
        linewidth=0.6,
        transform=ccrs.PlateCarree(),
        zorder=3,
    )
    for _, row in summary.iterrows():
        ax.annotate(
            f"{row['site']} (+{row['robust_premium_pct']:.1f}%)",
            (row["lon"], row["lat"]),
            xycoords=ccrs.PlateCarree()._as_mpl_transform(ax),
            textcoords="offset points",
            xytext=(6, 4),
            fontsize=7,
            zorder=4,
        )

    all_sites = set(df["site"].unique())
    excluded = sorted(all_sites - set(summary["site"]))
    if excluded:
        excl_df = df[df["site"].isin(excluded)].groupby(["site", "lat", "lon"]).first().reset_index()
        ax.scatter(
            excl_df["lon"],
            excl_df["lat"],
            c="#bbbbbb",
            s=100,
            marker="x",
            transform=ccrs.PlateCarree(),
            zorder=3,
            label="infeasible in search box",
        )
        ax.legend(loc="lower left", fontsize=8)

    fig.colorbar(sc, ax=ax, label="Robustness premium vs. per-year mean (%)", shrink=0.7, pad=0.03)
    ax.set_title(f"Weather-year robustness premium by site (coverage_threshold={coverage_threshold})")
    fig.tight_layout()
    fig.savefig(out_path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"Wrote {out_path}")


def plot_weather_year_spread(df: pd.DataFrame, out_path: Path, coverage_threshold: float) -> None:
    """Small-multiples grid, one panel per site: per-year optimal LCOE points plus a dashed
    line at the robust design's LCOE."""
    summary = _site_summary(df)
    if summary.empty:
        raise ValueError("No site has both a feasible per-year and robust design -- nothing to plot.")

    per_year = df[(df["method"] == "gbs") & (df["site"].isin(summary["site"]))]
    sites = sorted(summary["site"])
    years = sorted(per_year["year"].dropna().unique())

    ncols = min(4, len(sites))
    nrows = -(-len(sites) // ncols)  # ceil
    fig, axes = plt.subplots(nrows, ncols, figsize=(3.4 * ncols, 3.0 * nrows), squeeze=False)

    for i, site in enumerate(sites):
        ax = axes[i // ncols][i % ncols]
        site_years = per_year[per_year["site"] == site].dropna(subset=["lcoe"]).sort_values("year")
        ax.scatter(site_years["year"], site_years["lcoe"], color="#1b9e77", zorder=3, label="per-year optimum")
        robust_lcoe = summary.loc[summary["site"] == site, "robust_lcoe"].iloc[0]
        ax.axhline(robust_lcoe, color="#d95f02", linestyle="--", linewidth=1.5, label="robust (all years)")
        ax.set_xticks(years)
        ax.set_xticklabels([str(int(y)) for y in years], rotation=45, fontsize=7)
        ax.set_title(site, fontsize=9)

    for j in range(len(sites), nrows * ncols):
        axes[j // ncols][j % ncols].axis("off")

    handles, labels = axes[0][0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=2, fontsize=9, bbox_to_anchor=(0.5, 1.02))
    fig.supylabel("LCOE (USD/MWh)")
    fig.suptitle(f"Per-site LCOE by weather year vs. robust design (coverage_threshold={coverage_threshold})", y=1.06)
    fig.tight_layout()
    fig.savefig(out_path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"Wrote {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("scripts/boa_benchmark/plots"))
    args = parser.parse_args()

    df = pd.read_csv(args.csv)
    coverage_threshold = df["coverage_threshold"].iloc[0]
    if df["coverage_threshold"].nunique() > 1:
        raise ValueError(
            f"Multiple coverage_threshold values present ({sorted(df['coverage_threshold'].unique())}) -- "
            "this script assumes a single threshold per CSV, matching run_weather_year_sensitivity.py's CLI."
        )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    plot_weather_year_map(df, args.out_dir / "weather_year_map.png", coverage_threshold)
    plot_weather_year_spread(df, args.out_dir / "weather_year_spread.png", coverage_threshold)

    summary = _site_summary(df).sort_values("robust_premium_pct", ascending=False)
    logger.info("Per-site summary (sorted by robustness premium):\n" + summary.to_string(index=False))


if __name__ == "__main__":
    main()
