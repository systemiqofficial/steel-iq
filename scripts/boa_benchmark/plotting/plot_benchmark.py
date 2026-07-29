"""Plot results produced by `runners/run_methodology_comparison.py`'s long-format CSV (columns:
site, lat, lon, cost_region, coverage_threshold, metric, standing_loss, soc_mode, method,
budget, n_evaluations, seed, lcoe, battery, seconds -- `method` in {"boa", "gbs",
"lp"}, see that script's docstring).

Three figure types:

1. Site map + per-site-by-year panel -- `site_map.png` shows each site's (lat, lon) colored
   by its mean LP-optimal LCOE (`method == "lp"`, `metric == "energy"` -- the only
   combination the LP's dispatch-equivalence certification applies to; note its `lcoe` is
   the LP's design rescored through the true objective, not the LP's own certified-optimal
   linear objective value -- see (2)); `site_lcoe_by_year.png` is a small-multiples grid,
   one bar chart per site, of that same LCOE across weather years.
   `runners/run_methodology_comparison.py` sweeps a single `--year` per run and its output CSV has
   no `year` column -- run it once per weather year and concatenate the CSVs yourself,
   adding a `year` column, to unlock the by-year panel.

2. Methodology convergence -- for one (site, metric), two stacked panels sharing an
   n_evaluations x-axis: LCOE (top) and runtime (bottom). Draws `boa` vs `gbs` for
   every coverage threshold at once (color = threshold, reusing `_coverage_palette`;
   `gbs` uses a darker shade of its threshold's color than `boa`, undifferentiated in
   the legend since linestyle/marker already carry the method encoding; linestyle = method,
   dotted `boa` vs solid `gbs`, with matching markers as a second cue) so convergence
   behavior across thresholds is visible in one figure. Fixed to
   `soc_mode == "empty_start"` -- see (3) below for the cyclic-vs-empty_start comparison,
   which these plots don't duplicate. `boa` lines are mean-across-seeds with a min/max band;
   `gbs` lines are deterministic (no band). For `metric == "energy"` the top panel
   also draws a dashed `lp` line per threshold present (same color as that threshold),
   labeled "certified for linear proxy" -- the LP is certified optimal for its own linear
   battery-capex objective, not for the true concave `score_lcoe` plotted here, so
   `gbs` (which optimizes the true objective directly) can legitimately come in below
   it -- see `README.md`'s "Running the benchmark" section. For `metric == "hours"` no
   such line is drawn at all, since no ground truth of any kind exists there (see
   `core/gbs.py`'s module docstring).

3. SOC-mode sensitivity -- one bar chart per (coverage_threshold, metric), two bars per
   site (`boa`, `gbs`): `(lcoe_empty_start - lcoe_cyclic) / lcoe_cyclic` at each
   method's highest budget (most exact -- isolates the SOC-mode effect from sampling/
   search-resolution noise). The two bars are **not** expected to agree: BOA's battery
   sizing (`estimate_battery_capacity`) doesn't take `soc_mode` as an input at all -- it's
   a dispatch-agnostic percentile heuristic, so `boa`'s SOC-mode "sensitivity" only reflects
   which sampled candidates happen to pass the coverage filter under each dispatch rule, not
   a resized battery. `gbs` directly resolves `b_min` under each `soc_mode` and is the
   number that answers "how much would switching production dispatch to cyclic actually
   save," which `boa`'s own bar does not.

Usage:
    uv run python -m scripts.boa_benchmark.plotting.plot_benchmark --csv methodology_comparison.csv
    uv run python -m scripts.boa_benchmark.plotting.plot_benchmark --csv results.csv --skip-map \\
        --sites inner_mongolia --metrics energy
"""

import argparse
import logging
from pathlib import Path

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.lines import Line2D

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Fixed categorical order (never cycled/reassigned) so a given method/threshold keeps its
# color across plots.
METHOD_COLORS = {"boa": "#d95f02", "gbs": "#1b9e77"}
COVERAGE_COLORS = ["#1b9e77", "#d95f02", "#7570b3", "#e7298a", "#66a61e", "#e6ab02"]


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


def _lp_rows(df: pd.DataFrame, coverage_threshold: float, standing_loss: float) -> pd.DataFrame:
    sub = df[
        (df["method"] == "lp")
        & (df["coverage_threshold"] == coverage_threshold)
        & (df["standing_loss"] == standing_loss)
    ]
    if sub.empty:
        raise ValueError(
            f"No 'lp' rows at coverage_threshold={coverage_threshold}, standing_loss={standing_loss}. "
            f"Available coverage_thresholds={sorted(df['coverage_threshold'].unique())}, "
            f"standing_losses={sorted(df['standing_loss'].unique())}."
        )
    return sub


def plot_site_map(df: pd.DataFrame, out_path: Path, coverage_threshold: float, standing_loss: float) -> None:
    """Scatter of sites on a world map, colored by mean LP-optimal-design LCOE."""
    sub = _lp_rows(df, coverage_threshold, standing_loss)
    site_summary = sub.groupby(["site", "lat", "lon"])["lcoe"].mean().reset_index()

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
        c=site_summary["lcoe"],
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

    fig.colorbar(sc, ax=ax, label="Mean LP-optimal-design LCOE (USD/MWh)", shrink=0.7, pad=0.02)
    ax.set_title(
        f"Site LP-optimal-design LCOE (coverage_threshold={coverage_threshold}, standing_loss={standing_loss})"
    )
    fig.tight_layout()
    fig.savefig(out_path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"Wrote {out_path}")


def plot_site_lcoe_by_year(df: pd.DataFrame, out_path: Path, coverage_threshold: float, standing_loss: float) -> None:
    """Small-multiples grid, one bar chart per site, of LP-optimal-design LCOE across weather years."""
    sub = _lp_rows(df, coverage_threshold, standing_loss)
    if sub["year"].nunique() <= 1:
        logger.warning("Only one weather year present -- skipping site_lcoe_by_year (nothing to compare).")
        return

    site_year_lcoe = sub.groupby(["site", "year"])["lcoe"].mean().reset_index()
    sites = sorted(site_year_lcoe["site"].unique())
    years = sorted(site_year_lcoe["year"].unique(), key=str)

    ncols = min(4, len(sites))
    nrows = -(-len(sites) // ncols)  # ceil
    fig, axes = plt.subplots(nrows, ncols, figsize=(3.2 * ncols, 2.8 * nrows), squeeze=False, sharey=True)

    for i, site in enumerate(sites):
        ax = axes[i // ncols][i % ncols]
        site_df = site_year_lcoe[site_year_lcoe["site"] == site].set_index("year").reindex(years)
        ax.bar([str(y) for y in years], site_df["lcoe"], color=COVERAGE_COLORS[0])
        ax.set_title(site, fontsize=9)
        ax.tick_params(axis="x", labelrotation=45, labelsize=7)

    for j in range(len(sites), nrows * ncols):
        axes[j // ncols][j % ncols].axis("off")

    fig.supylabel("LP-optimal-design LCOE (USD/MWh)")
    fig.suptitle(
        f"Per-site LCOE by weather year (coverage_threshold={coverage_threshold}, standing_loss={standing_loss})"
    )
    fig.tight_layout()
    fig.savefig(out_path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"Wrote {out_path}")


_METHOD_LINESTYLE = {"boa": ":", "gbs": "-"}
_METHOD_MARKER = {"boa": "o", "gbs": "s"}
_GBS_DARKEN_FACTOR = 0.65  # 'gbs' lines get a darker shade of the same per-threshold color as 'boa'


def _darken(color: str, factor: float = _GBS_DARKEN_FACTOR) -> tuple[float, float, float]:
    r, g, b = mcolors.to_rgb(color)
    return (r * factor, g * factor, b * factor)


def plot_methodology_convergence(
    df: pd.DataFrame,
    out_path: Path,
    site: str,
    metric: str,
    standing_loss: float,
) -> None:
    """Two stacked panels (LCOE, runtime) sharing an n_evaluations x-axis: `boa` vs
    `gbs` at `soc_mode == "empty_start"`, one color per coverage threshold (see (3)
    for the cyclic-vs-empty_start comparison this doesn't duplicate), plus a per-threshold
    certified `lp` reference line when `metric == "energy"`."""
    base = df[
        (df["site"] == site)
        & (df["metric"] == metric)
        & (df["standing_loss"] == standing_loss)
        & (df["soc_mode"] == "empty_start")
        & (df["method"].isin(["boa", "gbs"]))
    ]
    if base.empty:
        raise ValueError(f"No rows for site={site}, metric={metric}, soc_mode=empty_start.")

    coverage_thresholds = sorted(base["coverage_threshold"].unique())
    palette = _coverage_palette(coverage_thresholds)
    line_alpha = 0.85
    band_alpha = 0.06

    fig, (ax_lcoe, ax_time) = plt.subplots(2, 1, sharex=True, figsize=(8, 7), gridspec_kw={"height_ratios": [2, 1]})

    for coverage in coverage_thresholds:
        base_color = palette[coverage]
        for method in ("boa", "gbs"):
            series = base[(base["coverage_threshold"] == coverage) & (base["method"] == method)]
            if series.empty:
                continue
            color = base_color if method == "boa" else _darken(base_color)
            style = _METHOD_LINESTYLE[method]
            marker = _METHOD_MARKER[method]
            if method == "boa":
                lcoe_stats = series.groupby("n_evaluations")["lcoe"].agg(["mean", "min", "max"]).sort_index()
                ax_lcoe.plot(
                    lcoe_stats.index, lcoe_stats["mean"], marker=marker, color=color, linestyle=style, alpha=line_alpha
                )
                ax_lcoe.fill_between(
                    lcoe_stats.index, lcoe_stats["min"], lcoe_stats["max"], color=color, alpha=band_alpha
                )
                time_stats = series.groupby("n_evaluations")["seconds"].mean().sort_index()
                ax_time.plot(
                    time_stats.index, time_stats.values, marker=marker, color=color, linestyle=style, alpha=line_alpha
                )
            else:
                s = series.sort_values("n_evaluations")
                ax_lcoe.plot(
                    s["n_evaluations"], s["lcoe"], marker=marker, color=color, linestyle=style, alpha=line_alpha
                )
                ax_time.plot(
                    s["n_evaluations"], s["seconds"], marker=marker, color=color, linestyle=style, alpha=line_alpha
                )

    has_lp = False
    if metric == "energy":
        lp = df[
            (df["site"] == site)
            & (df["method"] == "lp")
            & (df["standing_loss"] == standing_loss)
            & (df["coverage_threshold"].isin(coverage_thresholds))
        ]
        if lp.empty:
            logger.warning(f"metric=energy but no 'lp' rows for site={site} -- skipping truth line(s).")
        else:
            has_lp = True
            # Certified for the LP's own *linear* battery-capex objective, not for the true
            # LCOE plotted here (`score_lcoe`'s exact concave modular-installation curve) --
            # `gbs`, which optimizes the true objective directly, can legitimately beat
            # this line. See README.md's "Running the full comparison" section.
            for _, row in lp.iterrows():
                ax_lcoe.axhline(
                    row["lcoe"], color=palette[row["coverage_threshold"]], linestyle="--", linewidth=1, alpha=line_alpha
                )

    # Two separate legends (color = coverage threshold, marker/linestyle = method) instead of
    # one combined entry per (method, threshold) pair -- the combined legend grows with the
    # number of thresholds and duplicates the method encoding on every row.
    color_handles = [
        Line2D([0], [0], color=palette[c], linestyle="-", linewidth=2, label=f"p={c}") for c in coverage_thresholds
    ]
    method_handles = [
        Line2D([0], [0], color="black", linestyle=_METHOD_LINESTYLE[m], marker=_METHOD_MARKER[m], label=m)
        for m in ("boa", "gbs")
    ]
    if has_lp:
        method_handles.append(
            Line2D([0], [0], color="black", linestyle="--", linewidth=1, label="lp (certified for linear proxy)")
        )
    color_legend = ax_lcoe.legend(
        handles=color_handles,
        title="coverage threshold",
        fontsize=8,
        loc="upper left",
        bbox_to_anchor=(1.02, 1.0),
        borderaxespad=0,
    )
    ax_lcoe.add_artist(color_legend)
    method_legend = ax_lcoe.legend(
        handles=method_handles,
        title="method",
        fontsize=8,
        loc="lower left",
        bbox_to_anchor=(1.02, 0.0),
        borderaxespad=0,
    )

    ax_lcoe.set_xscale("log")
    ax_lcoe.set_ylabel("LCOE (USD/MWh)")
    ax_lcoe.set_title(f"{site} -- metric={metric}")

    ax_time.set_xscale("log")
    ax_time.set_yscale("log")
    ax_time.set_xlabel("n_evaluations")
    ax_time.set_ylabel("Runtime (s)")

    # No fig.tight_layout() here: it doesn't account for the two legends anchored outside
    # the axes via bbox_to_anchor. `color_legend` also needs to be passed explicitly via
    # bbox_extra_artists -- savefig's automatic tight-bbox detection only picks up the axes'
    # "current" legend (`method_legend`, the second `ax.legend()` call), not ones kept alive
    # separately via `ax.add_artist()`.
    fig.savefig(out_path, dpi=200, bbox_inches="tight", bbox_extra_artists=(color_legend, method_legend))
    plt.close(fig)
    logger.info(f"Wrote {out_path}")


def _soc_pct_diff_by_site(method_df: pd.DataFrame) -> pd.Series:
    """`(lcoe_empty_start - lcoe_cyclic) / lcoe_cyclic * 100` per site, using each site's
    highest-`budget` row per soc_mode (most exact available for that method)."""
    best = method_df.loc[method_df.groupby(["site", "soc_mode"])["budget"].idxmax()]
    pivot = best.pivot(index="site", columns="soc_mode", values="lcoe")
    if "cyclic" not in pivot.columns or "empty_start" not in pivot.columns:
        raise ValueError(f"Need both 'cyclic' and 'empty_start' soc_mode rows; found columns {list(pivot.columns)}.")
    return (pivot["empty_start"] - pivot["cyclic"]) / pivot["cyclic"] * 100


def plot_soc_mode_sensitivity(df: pd.DataFrame, out_path: Path, coverage_threshold: float, metric: str) -> None:
    """Grouped bar chart, two bars per site (`boa`, `gbs`): relative LCOE change from
    cyclic to empty-start SOC, each at that method's own highest budget. The two bars are
    not expected to agree -- see the module docstring's note (3) on why `boa`'s battery
    sizing doesn't actually depend on soc_mode, only its candidate filter does."""
    base = df[(df["coverage_threshold"] == coverage_threshold) & (df["metric"] == metric)]
    per_method = {}
    for method in ("boa", "gbs"):
        method_df = base[base["method"] == method]
        if not method_df.empty and method_df["soc_mode"].nunique() >= 2:
            per_method[method] = _soc_pct_diff_by_site(method_df)
    if not per_method:
        raise ValueError(
            f"No method with both soc_modes present at coverage_threshold={coverage_threshold}, metric={metric}."
        )

    sites = sorted(set.union(*(set(s.index) for s in per_method.values())))
    x = range(len(sites))
    width = 0.8 / len(per_method)

    fig, ax = plt.subplots(figsize=(max(6, 0.8 * len(sites)), 5))
    for i, (method, pct_diff) in enumerate(per_method.items()):
        offsets = [xi + (i - (len(per_method) - 1) / 2) * width for xi in x]
        values = [pct_diff.get(site, float("nan")) for site in sites]
        ax.bar(offsets, values, width=width, color=METHOD_COLORS[method], label=method)

    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_xticks(list(x))
    ax.set_xticklabels(sites, rotation=45, ha="right")
    ax.set_ylabel("LCOE change, empty_start vs cyclic (%)")
    ax.set_title(f"SOC-mode sensitivity (coverage_threshold={coverage_threshold}, metric={metric})")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=200, bbox_inches="tight")
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
    parser.add_argument(
        "--sites", type=str, default=None, help="Comma-separated site names for the per-method plots (default: all)."
    )
    parser.add_argument(
        "--metrics",
        type=str,
        default=None,
        help="Comma-separated metrics for the per-method plots (default: all present).",
    )
    parser.add_argument("--skip-map", action="store_true")
    parser.add_argument("--skip-convergence", action="store_true")
    parser.add_argument("--skip-soc-sensitivity", action="store_true")
    args = parser.parse_args()

    df = _ensure_year_column(pd.read_csv(args.csv))
    args.out_dir.mkdir(parents=True, exist_ok=True)

    standing_loss = _resolve_single_value(df, "standing_loss", args.standing_loss, "standing-loss")
    sites = args.sites.split(",") if args.sites else sorted(df["site"].unique())
    metrics = args.metrics.split(",") if args.metrics else sorted(df["metric"].unique())
    coverage_thresholds = (
        [args.coverage_threshold] if args.coverage_threshold is not None else sorted(df["coverage_threshold"].unique())
    )

    if not args.skip_map:
        map_coverage = _resolve_single_value(df, "coverage_threshold", args.coverage_threshold, "coverage-threshold")
        plot_site_map(df, args.out_dir / "site_map.png", map_coverage, standing_loss)
        plot_site_lcoe_by_year(df, args.out_dir / "site_lcoe_by_year.png", map_coverage, standing_loss)

    if not args.skip_convergence:
        convergence_dir = args.out_dir / "convergence"
        convergence_dir.mkdir(parents=True, exist_ok=True)
        for site in sites:
            for metric in metrics:
                subset = df[
                    (df["site"] == site)
                    & (df["metric"] == metric)
                    & (df["soc_mode"] == "empty_start")
                    & (df["method"].isin(["boa", "gbs"]))
                ]
                if subset.empty:
                    continue
                out_path = convergence_dir / f"{site}_{metric}.png"
                plot_methodology_convergence(df, out_path, site, metric, standing_loss)

    if not args.skip_soc_sensitivity:
        sensitivity_dir = args.out_dir / "soc_sensitivity"
        sensitivity_dir.mkdir(parents=True, exist_ok=True)
        for coverage in coverage_thresholds:
            for metric in metrics:
                subset = df[(df["coverage_threshold"] == coverage) & (df["metric"] == metric)]
                has_both_modes = any(subset[subset["method"] == m]["soc_mode"].nunique() >= 2 for m in ("boa", "gbs"))
                if not has_both_modes:
                    continue
                out_path = sensitivity_dir / f"{coverage}_{metric}.png"
                plot_soc_mode_sensitivity(df, out_path, coverage, metric)


if __name__ == "__main__":
    main()
