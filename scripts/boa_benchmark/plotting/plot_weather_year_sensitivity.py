"""Plot `runners/run_weather_year_sensitivity.py`'s output CSV (columns: site, lat, lon, cost_region,
coverage_threshold, metric, standing_loss, soc_mode, method, year, lcoe, solar, wind,
battery, coverage, n_evaluations, seconds -- `method` in {"gbs", "gbs_robust"}, see that
script's docstring).

Two figure types:

1. World map (`weather_year_map_{coverage_threshold}.png`) -- two markers per site, side by
   side. The left, solid circle is colored by mean per-year LCOE (absolute, one colorbar
   shared across sites) -- lets you compare sites by baseline cost. The right marker is
   split into 4 quadrants (clockwise from upper-right: earliest to latest weather year),
   each colored by that year's own optimal LCOE expressed as a **premium over that site's
   own cheapest weather year** (`(year_lcoe - site_min_lcoe) / site_min_lcoe * 100`), on a
   second colorbar shared across sites. Because it's normalized per site, color intensity
   directly means "how much worse is this year than my best year" and is comparable across
   sites regardless of their absolute cost level -- a global colorbar on absolute LCOE would
   wash out small-in-absolute-but-large-in-relative spreads at cheap sites. The worst
   quadrant's premium is annotated below each site's diamond marker as "Δ+X.X%" -- a proxy
   for weather-year robustness risk: it's not the same number as the robust design's premium
   over the mean
   (see `core/gbs.py`'s `find_robust_gbs_design`), but a large spread between a site's best
   and worst weather year is the same underlying risk that metric was built to surface.
   Requires exactly 4 weather years (the quadrant layout is fixed, not swept).

2. Per-site year spread (`weather_year_spread_{coverage_threshold}.png`) -- single axes,
   one x-position per site (sorted by median LCOE, ascending), LCOE on y. Foreground is a
   black min-max range bar (T-caps at each end) plus a black median dash per site -- the
   summary stat that answers "how much does weather-year choice move this site's cost." The
   4 individual weather-year points are still drawn, each a distinct marker shape in a muted
   color, jittered so they don't stack, as supporting detail rather than the headline.
   Sharing one y-axis across all sites (unlike the old per-site small-multiples layout)
   makes spread *magnitude* directly comparable -- a site whose years span 1 USD/MWh and one
   spanning 15 USD/MWh no longer look alike just because each got its own independent
   y-scale.

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
import matplotlib.colors as mcolors
import matplotlib.path as mpath
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Wedge

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Human-readable labels for plot display -- the CSV's `site` slugs (snake_case, abbreviated
# region names) are fine as data keys but read poorly as chart labels. Falls back to a
# title-cased, underscore-stripped version of the slug for any site not listed here, so an
# unmapped future site degrades gracefully instead of raising.
_SITE_DISPLAY_NAMES = {
    "patagonia_chile": "Patagonia, Chile",
    "n_adriatic": "Northern Adriatic",
    "wyoming_usa": "Wyoming, USA",
    "wa_gascoyne_coast": "WA Gascoyne Coast",
    "ecuador_colombia_coast": "Ecuador–Colombia Coast",
    "inner_mongolia": "Inner Mongolia, China",
    "sahara_libya_egypt": "Sahara (Libya/Egypt)",
    "namibia_kunene": "Namibia (Kunene)",
    "atacama_desert": "Atacama Desert, Chile",
    "iran_desert": "Iranian Desert",
}


def _display_name(site: str) -> str:
    return _SITE_DISPLAY_NAMES.get(site, site.replace("_", " ").title())


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


# Quadrant angle ranges (degrees, standard math convention: 0=east, counter-clockwise
# positive), ordered so that zipping against sorted(years) reads clockwise from
# upper-right: NE (earliest) -> SE -> SW -> NW (latest).
_QUADRANT_ANGLES = [(0, 90), (270, 360), (180, 270), (90, 180)]
_INFEASIBLE_COLOR = (0.75, 0.75, 0.75, 1.0)
# The two per-site markers fan upward from the true site coordinate (a "V", apex at the
# site dot) rather than sitting level with it on a horizontal line -- keeps the mean/premium
# pair visually anchored to its site while lifting the markers clear of dense horizontal
# clusters of neighboring sites (e.g. the Middle East/Mediterranean group).
_LON_OFFSET = 6.0  # degrees, horizontal separation between the two per-site markers
_LAT_OFFSET = 6.5  # degrees, vertical lift of both markers above the site dot
# Distinct-but-subtle greyscale per quadrant in the legend inset (independent of the
# quadrant's premium-colorbar fill on the map itself), so the 4 quadrants read apart at a
# glance even before matching their position to a year.
_LEGEND_QUADRANT_SHADES = ["#a8a8a8", "#8c8c8c", "#707070", "#545454"]


def _wedge_marker_path(theta1: float, theta2: float, n: int = 20) -> mpath.Path:
    """A pie-slice `Path` (center -> arc -> center) usable as a scatter/Line2D `marker`."""
    theta = np.radians(np.linspace(theta1, theta2, n))
    x = np.concatenate([[0.0], np.cos(theta), [0.0]])
    y = np.concatenate([[0.0], np.sin(theta), [0.0]])
    return mpath.Path(np.column_stack([x, y]))


def plot_weather_year_map(df: pd.DataFrame, out_path: Path, coverage_threshold: float) -> None:
    """Two markers per site, side by side: a solid circle colored by mean per-year LCOE
    (absolute), and a 4-quadrant wedge marker colored by each weather year's LCOE premium
    over that site's own cheapest year -- see module docstring for the reasoning behind
    each."""
    summary = _site_summary(df)
    if summary.empty:
        raise ValueError("No site has both a feasible per-year and robust design -- nothing to map.")

    per_year = df[(df["method"] == "gbs") & (df["site"].isin(summary["site"]))]
    years = sorted(per_year["year"].dropna().unique())
    if len(years) != 4:
        raise ValueError(
            f"Expected exactly 4 weather years, found {years} -- this plot's quadrant layout is fixed to 4."
        )
    pivot = per_year.pivot(index="site", columns="year", values="lcoe").reindex(summary["site"])

    # Each year's premium over that *site's own* cheapest year, so color intensity means
    # "how much worse is this year than my best year" regardless of the site's absolute
    # cost level -- see module docstring for why a global-absolute colorbar washes this out.
    site_min = pivot.min(axis=1)
    premium_pivot = pivot.sub(site_min, axis=0).div(site_min, axis=0) * 100
    worst_year_premium = premium_pivot.max(axis=1)

    mean_norm = mcolors.Normalize(vmin=summary["mean_lcoe"].min(), vmax=summary["mean_lcoe"].max())
    mean_cmap = plt.get_cmap("viridis")
    finite_premium = premium_pivot.to_numpy()[np.isfinite(premium_pivot.to_numpy())]
    premium_norm = mcolors.Normalize(vmin=0, vmax=finite_premium.max() if finite_premium.size else 1.0)
    premium_cmap = plt.get_cmap("magma_r")

    fig = plt.figure(figsize=(17, 7.5))
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree())
    ax.add_feature(cfeature.LAND, facecolor="#f0f0f0")
    ax.add_feature(cfeature.OCEAN, facecolor="#ffffff")
    ax.add_feature(cfeature.COASTLINE, linewidth=0.4)
    ax.add_feature(cfeature.BORDERS, linewidth=0.2)
    ax.set_global()

    # Both markers fan upward from the site dot (a "V", apex at the site) -- visually
    # unambiguous that the pair belongs to one site, and lifts them clear of the label/line
    # clutter of horizontally-packed neighbors instead of sitting level with the site.
    for _, row in summary.iterrows():
        ax.plot(
            [row["lon"] - _LON_OFFSET, row["lon"], row["lon"] + _LON_OFFSET],
            [row["lat"] + _LAT_OFFSET, row["lat"], row["lat"] + _LAT_OFFSET],
            color="#666666",
            linewidth=0.8,
            transform=ccrs.PlateCarree(),
            zorder=2,
        )
    ax.scatter(
        summary["lon"],
        summary["lat"],
        c="#000000",
        marker="D",
        s=26,
        transform=ccrs.PlateCarree(),
        zorder=3,
    )

    mean_sc = ax.scatter(
        summary["lon"] - _LON_OFFSET,
        summary["lat"] + _LAT_OFFSET,
        c=summary["mean_lcoe"],
        cmap=mean_cmap,
        norm=mean_norm,
        s=210,
        edgecolor="black",
        linewidth=0.6,
        transform=ccrs.PlateCarree(),
        zorder=3,
    )

    for (theta1, theta2), year in zip(_QUADRANT_ANGLES, years):
        marker = _wedge_marker_path(theta1, theta2)
        values = premium_pivot[year].to_numpy()
        facecolors = [premium_cmap(premium_norm(v)) if np.isfinite(v) else _INFEASIBLE_COLOR for v in values]
        ax.scatter(
            summary["lon"] + _LON_OFFSET,
            summary["lat"] + _LAT_OFFSET,
            marker=marker,
            s=480,
            facecolor=facecolors,
            edgecolor="black",
            linewidth=0.4,
            transform=ccrs.PlateCarree(),
            zorder=3,
        )

    for _, row in summary.iterrows():
        ax.annotate(
            f"{_display_name(row['site'])}\nΔ+{worst_year_premium[row['site']]:.1f}%",
            (row["lon"], row["lat"]),
            xycoords=ccrs.PlateCarree()._as_mpl_transform(ax),
            textcoords="offset points",
            xytext=(0, -5),
            ha="center",
            va="top",
            fontsize=9,
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

    # A literal miniature of the right-hand marker (a quadrant circle with year labels) reads
    # far more clearly than a matplotlib legend built from the same wedge Path scaled down --
    # at legend-icon size the wedges become illegible slivers.
    legend_inset = ax.inset_axes([0.85, 0.02, 0.145, 0.32], transform=ax.transAxes)
    legend_inset.set_xlim(-1.9, 1.9)
    legend_inset.set_ylim(-2.2, 2.0)
    legend_inset.set_aspect("equal")
    legend_inset.set_xticks([])
    legend_inset.set_yticks([])
    legend_inset.patch.set_visible(False)
    for spine in legend_inset.spines.values():
        spine.set_visible(False)
    _circle_y = -0.3  # nudge the whole mini-diagram down within its inset panel
    for (theta1, theta2), year, shade in zip(_QUADRANT_ANGLES, years, _LEGEND_QUADRANT_SHADES):
        legend_inset.add_patch(
            Wedge((0, _circle_y), 1, theta1, theta2, facecolor=shade, edgecolor="black", linewidth=0.6)
        )
        mid_angle = np.radians((theta1 + theta2) / 2)
        legend_inset.text(
            1.4 * np.cos(mid_angle),
            1.4 * np.sin(mid_angle) + _circle_y,
            str(int(year)),
            ha="center",
            va="center",
            fontsize=10,
            color="black",
            zorder=2,
        )
    legend_inset.text(0, _circle_y - 1.55, "weather year", ha="center", va="top", fontsize=8)

    if excluded:
        ax.legend(
            handles=[Line2D([0], [0], marker="x", color="#bbbbbb", linestyle="none", label="infeasible in search box")],
            loc="lower left",
            fontsize=9,
        )

    mean_cbar = fig.colorbar(
        mean_sc,
        ax=ax,
        location="left",
        label="Mean per-year LCOE (USD/MWh)",
        shrink=0.6,
        pad=0.02,
    )
    mean_cbar.set_label("Mean per-year LCOE (USD/MWh)", fontsize=12)
    mean_cbar.ax.tick_params(labelsize=10)

    premium_cbar = fig.colorbar(
        plt.cm.ScalarMappable(cmap=premium_cmap, norm=premium_norm),
        ax=ax,
        location="right",
        label="Per-year LCOE premium vs. site's cheapest year (%)\n(robustness-risk proxy)",
        shrink=0.6,
        pad=0.02,
    )
    premium_cbar.set_label("Per-year LCOE premium vs. site's cheapest year (%)\n(robustness-risk proxy)", fontsize=12)
    premium_cbar.ax.tick_params(labelsize=10)

    ax.set_title(
        f"Mean LCOE (left) and per-year premium vs. cheapest year (right, robustness-risk proxy) by site "
        f"(coverage_threshold={coverage_threshold})",
        fontsize=15,
        pad=12,
    )
    fig.savefig(out_path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"Wrote {out_path}")


_YEAR_MARKERS = ["o", "s", "^", "D", "v", "P", "X"]
_YEAR_MUTED_COLORS = ["#8ecae6", "#ffb703", "#8fbf7f", "#e58a8a", "#b497d6", "#c2a15c", "#7fc4ae"]


def plot_weather_year_spread(df: pd.DataFrame, out_path: Path, coverage_threshold: float) -> None:
    """Single axes: one x-position per site (sorted by median LCOE, ascending), LCOE on y.
    The foreground signal is a min-max range bar plus a median marker per site -- the
    summary stat that answers "how much does weather-year choice move this site's cost."
    The 4 individual weather-year points are still shown, but small, jittered, and muted
    (low alpha, no fill), as supporting detail rather than the headline -- see them if you
    look, but they don't compete with the median/spread read. One shared y-axis (unlike the
    old per-site small-multiples layout) makes spread magnitude directly comparable across
    sites."""
    summary = _site_summary(df)
    if summary.empty:
        raise ValueError("No site has both a feasible per-year and robust design -- nothing to plot.")

    per_year = df[(df["method"] == "gbs") & (df["site"].isin(summary["site"]))]
    median_lcoe = per_year.groupby("site")["lcoe"].median()
    summary = summary.assign(median_lcoe=summary["site"].map(median_lcoe)).sort_values("median_lcoe")
    summary = summary.reset_index(drop=True)

    sites = summary["site"].tolist()
    site_x = {site: i for i, site in enumerate(sites)}
    years = sorted(per_year["year"].dropna().unique())
    n_years = len(years)
    jitter = np.linspace(-0.14, 0.14, n_years) if n_years > 1 else [0.0]

    fig, ax = plt.subplots(figsize=(max(9.0, 1.0 * len(sites) + 2), 6.5))

    for j, year in enumerate(years):
        year_rows = per_year[per_year["year"] == year].dropna(subset=["lcoe"])
        xs = [site_x[s] + jitter[j] for s in year_rows["site"]]
        ax.scatter(
            xs,
            year_rows["lcoe"],
            marker=_YEAR_MARKERS[j % len(_YEAR_MARKERS)],
            color=_YEAR_MUTED_COLORS[j % len(_YEAR_MUTED_COLORS)],
            alpha=0.95,
            edgecolor="#555555",
            linewidth=0.5,
            s=50,
            zorder=2,
            label=str(int(year)),
        )

    cap_half = 0.06
    median_half = 0.10
    for site in sites:
        xi = site_x[site]
        row = summary.loc[summary["site"] == site].iloc[0]
        ax.vlines(xi, row["min_lcoe"], row["max_lcoe"], color="black", linewidth=1.6, zorder=3)
        ax.hlines(
            [row["min_lcoe"], row["max_lcoe"]], xi - cap_half, xi + cap_half, color="black", linewidth=1.6, zorder=3
        )
        ax.hlines(row["median_lcoe"], xi - median_half, xi + median_half, color="black", linewidth=2.0, zorder=4)

    range_handle = Line2D(
        [0],
        [0],
        marker="|",
        color="black",
        markersize=14,
        markeredgewidth=1.6,
        linestyle="none",
        label="min–max range across years",
    )
    median_handle = Line2D(
        [0],
        [0],
        marker="_",
        color="black",
        markersize=16,
        markeredgewidth=2.0,
        linestyle="none",
        label="median (across years)",
    )
    blank_handle = Line2D([], [], color="none", label="")

    ax.set_xticks(range(len(sites)))
    ax.set_xticklabels([_display_name(s) for s in sites], rotation=45, ha="right", fontsize=11)
    ax.tick_params(axis="y", labelsize=11)
    ax.set_xlabel("Site (sorted by median per-year LCOE)", fontsize=13)
    ax.set_ylabel("LCOE (USD/MWh)", fontsize=13)
    ax.grid(axis="y", alpha=0.3, zorder=0)
    year_handles, year_labels = ax.get_legend_handles_labels()
    # Column-major fill (matplotlib's default for ncol>1): padding the range/median column
    # to the same row count as the year column keeps them as two visually distinct groups
    # -- stats on the left, weather years on the right -- rather than interleaved.
    n_pad = len(year_handles) - 2
    ax.legend(
        handles=[range_handle, median_handle] + [blank_handle] * n_pad + year_handles,
        labels=[range_handle.get_label(), median_handle.get_label()] + [""] * n_pad + year_labels,
        loc="upper left",
        fontsize=10,
        ncol=2,
    )
    ax.set_title(
        f"Per-site LCOE spread across weather years (coverage_threshold={coverage_threshold})", fontsize=15, pad=12
    )
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
    plot_weather_year_map(df, args.out_dir / f"weather_year_map_{coverage_threshold}.png", coverage_threshold)
    plot_weather_year_spread(df, args.out_dir / f"weather_year_spread_{coverage_threshold}.png", coverage_threshold)

    summary = _site_summary(df).sort_values("robust_premium_pct", ascending=False)
    logger.info("Per-site summary (sorted by robustness premium):\n" + summary.to_string(index=False))


if __name__ == "__main__":
    main()
