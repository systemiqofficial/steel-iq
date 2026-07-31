"""Plot `runners/run_weather_year_sensitivity.py`'s output CSV as a three-row composite
figure (`weather_year_composition_{coverage_threshold}.png`), one x-position per site (sorted
by median LCOE, ascending -- same order and site-selection logic as
`plot_weather_year_sensitivity.plot_weather_year_spread`, copied here as row 1's starting
point rather than imported, so the two plotting scripts can evolve independently):

1. **LCOE spread** (row 1) -- identical to `plot_weather_year_sensitivity`'s spread plot:
   black min-max range bar + median dash per site, the 4 individual weather-year points
   jittered and muted as supporting detail. See that module's docstring for the full
   rationale; not repeated here.
2. **Overscale factors** (row 2) -- grouped bars (solar / wind / battery), each site's mean
   installed capacity (a multiple of baseload demand) across its 4 per-year "gbs" designs, with
   a min-max error bar per bar showing how much *that* number moves across weather years.
   Answers "how much do you have to build here, and how sensitive is that to which year you
   size against." Deliberately grouped, not stacked: solar/wind (generation capacity) and
   battery (storage capacity) are different physical quantities with no meaningful combined
   total, unlike row 3 below.
3. **Energy-share composition** (row 3) -- stacked bars (unmet / solar-direct /
   battery-via-solar / battery-via-wind / wind-direct), each site's mean across its 4 years'
   `core.design_metrics.decompose_energy_flows` output, normalized to demand = 100%. Answers
   "where did the energy actually come from," including how much of what the battery
   delivered originated from each source under that function's well-mixed-pool attribution
   rule (see its docstring). Battery segments intentionally carry their *origin's* hue (a
   lighter tint, plus diagonal hatching as a secondary channel), not a separate "battery"
   color: the entire point of this row is dissolving the battery's discharge back into
   solar/wind origin, unlike row 2 where battery is its own bar because there it genuinely is
   a separate (storage, not generation) quantity. Unmet demand (dark grey) sits at the base of
   the stack rather than at the 100% line. Curtailment (generation reaching neither demand nor
   the battery) is a categorically different thing -- it never serves demand at all -- so it's
   stacked *above* a dashed 100% reference line rather than folded into the demand stack below
   it, still split into its own solar/wind origin (same base hue) but muted (lower alpha,
   black cross-hatch rather than the battery segments' white diagonal hatch) so it reads as
   wasted/receded rather than more of the same stack.

Rows 2-3 aggregate each site's 4 per-year "gbs" designs (not the "gbs_robust" design) by plain
mean -- cheaper than decomposing the robust design against each weather year separately, and
it matches what row 1 already shows (the same 4 per-year points). Sites excluded from row 1
(no feasible per-year and robust design both -- see `_site_summary`) are excluded here too.

Usage:
    uv run python -m scripts.boa_benchmark.plotting.plot_weather_year_composition \\
        --csv scripts/boa_benchmark/results/weather_year_sensitivity.csv
"""

import argparse
import logging
from pathlib import Path

import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Same site-label lookup as plot_weather_year_sensitivity.py -- kept as a separate copy (not
# imported) so this module has no dependency on that one, per the same "let them evolve
# independently" reasoning as row 1 itself.
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
    where either side is entirely infeasible (all-NaN). Copied from
    plot_weather_year_sensitivity.py -- see that module for the full rationale."""
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
        rows.append(
            {
                "site": site,
                "mean_lcoe": year_lcoes.mean(),
                "min_lcoe": year_lcoes.min(),
                "max_lcoe": year_lcoes.max(),
                "median_lcoe": year_lcoes.median(),
            }
        )
    return pd.DataFrame(rows)


def _lighten(hex_color: str, factor: float) -> tuple[float, float, float]:
    """Blend `hex_color` toward white by `factor` (0 = unchanged, 1 = white) -- used for the
    battery-via-X segments' tint of their origin's base hue, since no pre-validated lighter
    step exists for the non-blue categorical hues (see module docstring on row 3's coloring)."""
    r, g, b = mcolors.to_rgb(hex_color)
    return (r + (1 - r) * factor, g + (1 - g) * factor, b + (1 - b) * factor)


# Categorical hues, reused from the documented default palette (color-formula.md): slot 1
# (blue) for wind, slot 2 (orange) for solar -- an already-validated adjacent pair (worst
# adjacent CVD deltaE 9.1 light / 8.4 dark) -- plus slot 3 (aqua) for battery capacity in row
# 2 only, since the first three slots are the palette's strongest, all-pairs-validated subset.
# `_COLOR_UNMET` is a dark, distinct neutral grey (not the palette's status-critical red) --
# unmet demand is flagged by color contrast and its position (the base of the stack) and
# legend label, not an alarm color; curtailment gets the "this is different" visual treatment
# instead (muted alpha + black cross-hatch, see `_plot_energy_composition`), since that's the
# larger and more actionable number at most sites here.
_COLOR_SOLAR = "#eb6834"
_COLOR_WIND = "#2a78d6"
_COLOR_BATTERY = "#1baf7a"
_COLOR_UNMET = "#404040"
_COLOR_SOLAR_TINT = _lighten(_COLOR_SOLAR, 0.55)
_COLOR_WIND_TINT = _lighten(_COLOR_WIND, 0.55)
# Hatching on the battery-via-X segments (diagonal) and the curtailed segments (cross-hatch):
# a secondary encoding channel for exactly the pairs most likely to sit close together in
# color (same-hue tint or full-saturation reuse next to their own base) -- this project has no
# `node` available to run the palette validator, so the tints above are a
# defensible-but-unvalidated adaptation of the documented blue ramp's "lighter step" pattern to
# a hue (orange) with no published ramp, and hatching is added defensively rather than
# confirmed unnecessary. Curtailment reuses solar/wind's full-saturation base color (same
# origin, different fate) rather than a tint, so cross-hatch plus the bold border (see
# `_plot_energy_composition`) is what marks it apart, not lightness.
_HATCH_BATTERY = "//"
_HATCH_CURTAILMENT = "xx"

_YEAR_MARKERS = ["o", "s", "^", "D", "v", "P", "X"]
_YEAR_MUTED_COLORS = ["#8ecae6", "#ffb703", "#8fbf7f", "#e58a8a", "#b497d6", "#c2a15c", "#7fc4ae"]


def _composition_summary(df: pd.DataFrame, sites: list[str]) -> pd.DataFrame:
    """Per-site mean, across each site's 4 per-year "gbs" designs, of overscale factors and
    `decompose_energy_flows` shares -- not the "gbs_robust" design (see module docstring).
    `sites` is `_site_summary`'s already-feasibility-filtered list; a site with some (but not
    all) infeasible years is silently averaged over its feasible years only (pandas' default
    skipna) -- the infeasibility itself was already logged once, by `_site_summary`.

    Also carries each overscale factor's min/max across those same 4 years (as `{tech}_min`/
    `{tech}_max` columns) -- row 2 draws these as error bars around the mean bar, the same
    "how much does weather-year choice move this number" question row 1 already asks of LCOE,
    just asked of build-out instead."""
    gbs = df[(df["method"] == "gbs") & (df["site"].isin(sites))]
    cols = [
        "solar",
        "wind",
        "battery",
        "energy_share_solar_direct",
        "energy_share_wind_direct",
        "energy_share_battery_solar",
        "energy_share_battery_wind",
        "energy_share_unmet",
        "curtailment_solar",
        "curtailment_wind",
    ]
    summary = gbs.groupby("site")[cols].mean().reindex(sites)
    overscale_range = gbs.groupby("site")[["solar", "wind", "battery"]].agg(["min", "max"]).reindex(sites)
    overscale_range.columns = [f"{tech}_{stat}" for tech, stat in overscale_range.columns]
    return summary.join(overscale_range)


def _plot_lcoe_spread(ax: plt.Axes, df: pd.DataFrame, summary: pd.DataFrame, sites: list[str]) -> None:
    """Row 1 -- copied from `plot_weather_year_sensitivity.plot_weather_year_spread`, adapted
    to draw into a given `ax` instead of its own figure/legend/title/savefig. See that
    function's docstring for the full design rationale (min-max range bar + median as the
    headline signal, per-year points as muted supporting detail)."""
    per_year = df[(df["method"] == "gbs") & (df["site"].isin(sites))]
    site_x = {site: i for i, site in enumerate(sites)}
    years = sorted(per_year["year"].dropna().unique())
    n_years = len(years)
    jitter = np.linspace(-0.14, 0.14, n_years) if n_years > 1 else [0.0]

    for j, year in enumerate(years):
        year_rows = per_year[per_year["year"] == year].dropna(subset=["lcoe"])
        xs = [site_x[s] + jitter[j] for s in year_rows["site"]]
        ax.scatter(
            xs,
            year_rows["lcoe"],
            marker=_YEAR_MARKERS[j % len(_YEAR_MARKERS)],
            color=_YEAR_MUTED_COLORS[j % len(_YEAR_MUTED_COLORS)],
            alpha=0.6,
            edgecolor="#555555",
            linewidth=0.5,
            s=32,
            zorder=2,
            label=str(int(year)),
        )

    # The range bar (vertical spine + T-caps) is the outer/structural element, so its caps are
    # deliberately the widest horizontal marks here -- the median used to be a *wider* crossing
    # hline than the caps, inverting that hierarchy and reading as a second competing line
    # rather than a detail sitting on the bar. A small marker (not a line) avoids the
    # crossing-lines clutter entirely; the white edge halos it off the black spine it sits on.
    cap_half = 0.08
    for site in sites:
        xi = site_x[site]
        row = summary.loc[summary["site"] == site].iloc[0]
        ax.vlines(xi, row["min_lcoe"], row["max_lcoe"], color="black", linewidth=1.4, zorder=3)
        ax.hlines(
            [row["min_lcoe"], row["max_lcoe"]], xi - cap_half, xi + cap_half, color="black", linewidth=1.4, zorder=3
        )
        ax.plot(
            xi,
            row["median_lcoe"],
            marker="D",
            markersize=5.0,
            color="black",
            markeredgecolor="white",
            markeredgewidth=0.8,
            linestyle="none",
            zorder=5,
        )

    range_handle = Line2D(
        [0],
        [0],
        marker="|",
        color="black",
        markersize=14,
        markeredgewidth=1.4,
        linestyle="none",
        label="min–max range across years",
    )
    median_handle = Line2D(
        [0],
        [0],
        marker="D",
        color="black",
        markersize=5.0,
        markeredgecolor="white",
        markeredgewidth=0.8,
        linestyle="none",
        label="median (across years)",
    )
    ax.set_ylim(bottom=0)
    ax.tick_params(axis="y", labelsize=11)
    ax.set_ylabel("LCOE (USD/MWh)", fontsize=13)
    ax.grid(axis="y", alpha=0.3, zorder=0)
    year_handles, year_labels = ax.get_legend_handles_labels()
    # Anchored outside the axes (not "upper left" inside it, as in the original single-panel
    # version) -- with row 3's stack reaching well above the 100% line at some sites, no
    # in-plot corner is safe from some site's bar landing under it, so all three panels'
    # legends live in a right-hand margin instead. `bbox_inches="tight"` on save expands the
    # saved image to include them. A single vertical column here (no blank-row padding to
    # fake a second column, unlike the original in-plot version this was copied from) -- that
    # padding trick only made sense for a horizontal 2-column in-plot layout.
    ax.legend(
        handles=[range_handle, median_handle] + year_handles,
        labels=[range_handle.get_label(), median_handle.get_label()] + year_labels,
        loc="upper left",
        bbox_to_anchor=(1.01, 1.0),
        fontsize=9,
        ncol=1,
    )


def _plot_overscale(ax: plt.Axes, composition: pd.DataFrame, sites: list[str]) -> None:
    """Row 2 -- grouped (not stacked; see module docstring) bars of each site's mean
    solar/wind/battery overscale factor across its 4 per-year "gbs" designs, with a min-max
    error bar on each showing how much that particular number moves across weather years --
    the same spread question row 1 asks of LCOE, asked here of build-out instead. A tall mean
    bar with a tight error bar and a short mean bar with a wide one are different situations
    (a consistently-large build vs. a build whose size itself is weather-year-dependent), and
    only the error bar distinguishes them."""
    width = 0.26
    xs = np.arange(len(sites))
    bar_specs = [
        (-width, "solar", _COLOR_SOLAR, "Solar"),
        (0.0, "wind", _COLOR_WIND, "Wind"),
        (width, "battery", _COLOR_BATTERY, "Battery"),
    ]
    for offset, tech, color, label in bar_specs:
        means = composition[tech].to_numpy()
        yerr = [means - composition[f"{tech}_min"].to_numpy(), composition[f"{tech}_max"].to_numpy() - means]
        ax.bar(
            xs + offset,
            means,
            width=width,
            color=color,
            label=label,
            zorder=2,
            yerr=yerr,
            capsize=3,
            error_kw={"ecolor": "black", "elinewidth": 1.0, "zorder": 4},
        )

    ax.set_ylabel("Mean overscale factor\n(× baseload demand)", fontsize=12)
    ax.grid(axis="y", alpha=0.3, zorder=0)
    ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0), fontsize=9, ncol=1)


def _plot_energy_composition(ax: plt.Axes, composition: pd.DataFrame, sites: list[str]) -> None:
    """Row 3 -- stacked bars of each site's mean annual energy composition (as % of demand)
    across its 4 per-year "gbs" designs' `decompose_energy_flows` output, in two tiers:

    - Below the 100% line: what served demand. Unmet demand (dark grey) sits at the *base* of
      the stack, then solar-direct / battery-via-solar cluster together, then
      battery-via-wind / wind-direct cluster together -- each hue-family reads as one
      contiguous block split into "direct" and "via battery".
    - Above the 100% line: curtailed generation (solar / wind), which never served demand at
      all -- a categorically different quantity, not more of the same stack. It's set apart by
      position (above a dashed 100% reference line) and a muted, black-cross-hatched
      treatment (lower alpha, black hatch lines rather than white) instead of a bold outline --
      curtailment reads as receded/wasted, not alarmed-red. Same base hues as the direct-serve
      segments (same origin) so origin stays readable at a glance, just muted."""
    xs = np.arange(len(sites))
    width = 0.6
    demand_pct = (
        composition[
            [
                "energy_share_unmet",
                "energy_share_solar_direct",
                "energy_share_battery_solar",
                "energy_share_battery_wind",
                "energy_share_wind_direct",
            ]
        ]
        * 100.0
    )
    curtailment_pct = composition[["curtailment_solar", "curtailment_wind"]] * 100.0

    demand_segments = [
        ("energy_share_unmet", "Unmet demand", _COLOR_UNMET, None),
        ("energy_share_solar_direct", "Solar (direct)", _COLOR_SOLAR, None),
        ("energy_share_battery_solar", "Solar (via battery)", _COLOR_SOLAR_TINT, _HATCH_BATTERY),
        ("energy_share_battery_wind", "Wind (via battery)", _COLOR_WIND_TINT, _HATCH_BATTERY),
        ("energy_share_wind_direct", "Wind (direct)", _COLOR_WIND, None),
    ]
    bottom = np.zeros(len(sites))
    for col, label, color, hatch in demand_segments:
        values = demand_pct[col].to_numpy()
        ax.bar(
            xs,
            values,
            width=width,
            bottom=bottom,
            color=color,
            hatch=hatch,
            edgecolor="white",
            linewidth=0.6,
            label=label,
            zorder=2,
        )
        bottom += values

    curtailment_segments = [
        ("curtailment_solar", "Curtailed (solar)", _COLOR_SOLAR),
        ("curtailment_wind", "Curtailed (wind)", _COLOR_WIND),
    ]
    curtailment_bottom = bottom.copy()
    for col, label, color in curtailment_segments:
        values = curtailment_pct[col].to_numpy()
        ax.bar(
            xs,
            values,
            width=width,
            bottom=curtailment_bottom,
            color=color,
            alpha=0.55,
            hatch=_HATCH_CURTAILMENT,
            edgecolor="black",
            linewidth=0.6,
            label=label,
            zorder=2,
        )
        curtailment_bottom += values

    curtailment_total = curtailment_pct.sum(axis=1).to_numpy()
    ax.axhline(100, color="black", linewidth=1.0, linestyle="--", alpha=0.6, zorder=3)

    top = float((bottom + curtailment_total).max()) if len(sites) else 100.0
    ax.set_ylim(0, max(108.0, top * 1.08))
    ax.set_ylabel("Mean share of annual\ndemand (%)", fontsize=12)
    ax.grid(axis="y", alpha=0.3, zorder=0)
    ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0), fontsize=8, ncol=1)
    ax.set_xticks(xs)
    ax.set_xticklabels([_display_name(s) for s in sites], rotation=45, ha="right", fontsize=11)
    ax.set_xlabel("Site (sorted by median per-year LCOE)", fontsize=13)


def plot_weather_year_composition(df: pd.DataFrame, out_path: Path, coverage_threshold: float) -> None:
    summary = _site_summary(df)
    if summary.empty:
        raise ValueError("No site has both a feasible per-year and robust design -- nothing to plot.")

    per_year = df[(df["method"] == "gbs") & (df["site"].isin(summary["site"]))]
    median_lcoe = per_year.groupby("site")["lcoe"].median()
    summary = summary.assign(sort_key=summary["site"].map(median_lcoe)).sort_values("sort_key")
    summary = summary.reset_index(drop=True)
    sites = summary["site"].tolist()

    composition = _composition_summary(df, sites)

    fig, (ax_lcoe, ax_overscale, ax_energy) = plt.subplots(
        3,
        1,
        figsize=(max(9.0, 1.0 * len(sites) + 2), 12.0),
        sharex=True,
        gridspec_kw={"height_ratios": [2.4, 1.3, 1.8], "hspace": 0.08},
    )

    _plot_lcoe_spread(ax_lcoe, df, summary, sites)
    _plot_overscale(ax_overscale, composition, sites)
    _plot_energy_composition(ax_energy, composition, sites)

    ax_lcoe.tick_params(axis="x", labelbottom=False)
    ax_overscale.tick_params(axis="x", labelbottom=False)
    for ax in (ax_lcoe, ax_overscale, ax_energy):
        ax.set_xlim(-0.6, len(sites) - 0.4)

    # Matplotlib's default top margin (~12% of figure height, sized for a single-axes figure)
    # leaves a large gap above row 1 once stacked into 3 rows -- `bbox_inches="tight"` on save
    # doesn't remove it, since the gap is real blank space between two pieces of content
    # (title, axes), not outer padding beyond them. Pull the axes block up explicitly instead.
    fig.subplots_adjust(top=0.95)
    fig.suptitle(
        f"Weather-year LCOE spread, build-out, and energy-source composition by site "
        f"(coverage_threshold={coverage_threshold})",
        fontsize=15,
    )
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
    plot_weather_year_composition(
        df, args.out_dir / f"weather_year_composition_{coverage_threshold}.png", coverage_threshold
    )


if __name__ == "__main__":
    main()
