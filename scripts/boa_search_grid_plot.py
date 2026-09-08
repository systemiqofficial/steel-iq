"""
Per-pixel search-grid diagnostic plot.

Overlays, in `(solar overscale, wind overscale)` space, everything a bisection-search
build and a query-time LCOE lookup did for one pixel:

  1. The coarse grid the build swept, as a `b_min` lower-bound heatmap (infeasible cells
     masked).
  2. The dense patch rectangles the build resolved around its seeds.
  3. The query-time LCOE winner (`argmin_lcoe`'s pick) for each requested investment year,
     connected as a trajectory, colored by year, sized by the winning battery, and outlined
     in red where the winner sits against a patch edge (`Optimum.argmin_truncated`).
  4. Each requested year's own frozen-ratio anchor seed placement, for comparison against
     where that year's real-price winner (#3) actually lands. This is the ad hoc
     "option A" anchor from BOA_SEARCH_GRID_PLOT_HANDOVER.md: it replicates the build's
     seed-selection step against the frontier's already-final coarse grid, using only the
     requested year's own cost ratios -- not the literal anchor set the region build used.

See BOA_SEARCH_GRID_PLOT_HANDOVER.md (same directory, gitignored) for the full spec.
Cache loading and cost-array plumbing are copied from boa_frontier_selection_analysis.py
rather than re-derived.

Run:
    python scripts/boa_search_grid_plot.py --region INDO_AUS --lat -6.2 --lon 106.8 \
        --years 2025 2030 2040 2050 2060
    python scripts/boa_search_grid_plot.py --region INDO_AUS --pixel-index 1234 \
        --years 2025 2060 --output plots/indo_aus_pixel.png
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle

from boa.cli.run_simulation import _validate_cost_set
from boa.config.constants import STATUS_CODES
from boa.config.paths import PathConfig
from boa.config.physical_parameters import ERA5_DATA_RESOLUTION
from boa.inputs.profiles import detect_weather_year
from boa.model.bisection import (
    STATUS_OK,
    CostCoefficients,
    Optimum,
    PixelFrontier,
    SearchParams,
    _sub_lattice,
    anchor_score,
    argmin_lcoe,
    select_seeds,
)
from boa.model.cost_calculations import lcoe_coefficients
from boa.model.frontier_cache import frontier_at, frontier_cache_path, read_frontier_cache
from boa.model.global_extension import _derive_cost_arrays

DEFAULT_BASELOAD = 1000.0
DEFAULT_COVERAGE = 0.85


def _nearest_pixel(cache, lat: float, lon: float) -> int:
    """Nearest cached point to `(lat, lon)`. `cache.lats`/`lons` are land points only."""
    return int(np.argmin((cache.lats - lat) ** 2 + (cache.lons - lon) ** 2))


def _anchor_seed_points(
    frontier: PixelFrontier, params: SearchParams, coeffs: CostCoefficients
) -> list[tuple[float, float]]:
    """
    Where a sole build-time anchor at this year's own cost ratios would place its seeds.

    Replicates the seed-selection step (not the build's patch-widening loop) against the
    frontier's already-final `s_coarse`/`w_coarse`/`b_coarse`. Illustrative, not necessarily
    an anchor the real build actually used -- see "option A" in the handover doc.
    """
    li = _sub_lattice(frontier.s_coarse.shape[0], params.coarse_stride)
    scores = anchor_score(
        frontier.s_coarse[li].astype(np.float64),
        frontier.w_coarse[li].astype(np.float64),
        frontier.b_coarse[np.ix_(li, li)].astype(np.float64),
        coeffs,
    )
    seed_cells = select_seeds(scores, params.seed_tolerance, 2, params.max_seeds)
    return [(float(frontier.s_coarse[li[a]]), float(frontier.w_coarse[li[b]])) for a, b in seed_cells]


def plot_search_grid(
    frontier: PixelFrontier,
    region: str,
    k: int,
    lat: float,
    lon: float,
    coverage: float,
    baseload: float | None,
    years: list[int],
    winners: dict[int, Optimum | None],
    anchors: dict[int, list[tuple[float, float]]],
    output_path: Path | None = None,
    colorbar_label: str = "investment year",
    tick_labels: dict[int, str] | None = None,
    anchor_legend_label: str = "year's own anchor seed",
) -> None:
    """
    `colorbar_label`/`tick_labels` let a caller repurpose the `years` axis for a discrete
    set of labelled scenarios instead of real investment years -- e.g. one slot per
    build-time anchor plus a held-out query -- without changing the default behaviour real
    CLI usage (`main`, real years) relies on. `tick_labels` maps a `years` entry to its
    display name; annotation text and the colorbar ticks use it when given, otherwise the
    raw year number is shown as before.
    """
    fig, ax = plt.subplots(figsize=(9, 7))

    s_c = frontier.s_coarse.astype(np.float64)
    w_c = frontier.w_coarse.astype(np.float64)
    b_c = frontier.b_coarse.astype(np.float64)
    s_grid, w_grid = np.meshgrid(s_c, w_c, indexing="ij")  # (i, j) -> (s_coarse[i], w_coarse[j])
    finite = np.isfinite(b_c)

    if finite.any():
        grid_pts = ax.scatter(
            s_grid[finite],
            w_grid[finite],
            c=b_c[finite],
            cmap="Greys",
            marker="D",
            s=45,
            edgecolors="black",
            linewidths=0.4,
            zorder=2,
        )
        fig.colorbar(grid_pts, ax=ax, label="coarse b_min lower bound (baseload-hours)", pad=0.02)
    if (~finite).any():
        ax.scatter(
            s_grid[~finite],
            w_grid[~finite],
            marker="x",
            color="lightgray",
            s=25,
            linewidths=0.8,
            zorder=2,
        )

    for slot in range(frontier.n_patches):
        i0, i1, j0, j1 = (int(x) for x in frontier.patch_bounds[slot])
        ax.add_patch(
            Rectangle(
                (s_c[i0], w_c[j0]),
                s_c[i1] - s_c[i0],
                w_c[j1] - w_c[j0],
                fill=False,
                edgecolor="black",
                linestyle="--",
                linewidth=1.3,
                zorder=3,
            )
        )
        # The patch's own dense lattice, masked to its real extent (patch_points) -- past
        # that is zero-padding, and plotting it unmasked would show a false dense grid
        # filling the whole padded allocation.
        n_s, n_w = (int(x) for x in frontier.patch_points[slot])
        s_dense = frontier.s_patch[slot, :n_s].astype(np.float64)
        w_dense = frontier.w_patch[slot, :n_w].astype(np.float64)
        s_dense_grid, w_dense_grid = np.meshgrid(s_dense, w_dense, indexing="ij")
        ax.scatter(
            s_dense_grid,
            w_dense_grid,
            marker=".",
            s=8,
            color="tab:blue",
            alpha=0.5,
            linewidths=0,
            zorder=3.5,
        )

    if len(years) > 1:
        norm = mcolors.Normalize(vmin=min(years), vmax=max(years))
    else:
        norm = mcolors.Normalize(vmin=years[0] - 1, vmax=years[0] + 1)
    cmap = plt.get_cmap("viridis")

    solved_years = [y for y in years if winners[y] is not None]
    batteries = [winners[y].battery for y in solved_years]  # type: ignore[union-attr]
    max_battery = max(batteries) if batteries else 0.0

    if len(solved_years) > 1:
        ax.plot(
            [winners[y].solar for y in solved_years],  # type: ignore[union-attr]
            [winners[y].wind for y in solved_years],  # type: ignore[union-attr]
            color="black",
            linewidth=1.0,
            alpha=0.5,
            zorder=4,
        )

    for y in years:
        for s, w in anchors[y]:
            ax.scatter(s, w, marker="^", s=90, facecolors="none", edgecolors=cmap(norm(y)), linewidths=1.6, zorder=6)

        opt = winners[y]
        if opt is None:
            continue
        size = 70.0 if max_battery <= 0.0 else 70.0 + 330.0 * (opt.battery / max_battery)
        edge = "red" if opt.argmin_truncated else "black"
        lw = 2.2 if opt.argmin_truncated else 0.8
        ax.scatter(opt.solar, opt.wind, s=size, color=cmap(norm(y)), edgecolors=edge, linewidths=lw, zorder=7)
        label = tick_labels[y] if tick_labels else str(y)
        ax.annotate(
            f"{label}  b={opt.battery:.2f}",
            (opt.solar, opt.wind),
            textcoords="offset points",
            xytext=(6, 6),
            fontsize=7,
        )

    sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
    sm.set_array([])
    cbar = fig.colorbar(sm, ax=ax, label=colorbar_label, pad=0.08)
    if tick_labels:
        ticks = sorted(tick_labels)
        cbar.set_ticks(ticks)
        cbar.set_ticklabels([tick_labels[t] for t in ticks])

    legend_elems = [
        Line2D(
            [0],
            [0],
            marker="o",
            linestyle="none",
            markerfacecolor="grey",
            markeredgecolor="black",
            markersize=9,
            label="query-time winner (size = battery)",
        ),
        Line2D(
            [0],
            [0],
            marker="o",
            linestyle="none",
            markerfacecolor="none",
            markeredgecolor="red",
            markeredgewidth=2,
            markersize=9,
            label="winner truncated against patch edge",
        ),
        Line2D(
            [0],
            [0],
            marker="^",
            linestyle="none",
            markerfacecolor="none",
            markeredgecolor="grey",
            markersize=9,
            label=anchor_legend_label,
        ),
        Rectangle((0, 0), 1, 1, fill=False, edgecolor="black", linestyle="--", label="patch box"),
        Line2D(
            [0],
            [0],
            marker=".",
            linestyle="none",
            markerfacecolor="tab:blue",
            markeredgecolor="none",
            markersize=6,
            alpha=0.7,
            label="patch dense-grid node",
        ),
    ]
    ax.legend(handles=legend_elems, loc="upper right", fontsize=8, framealpha=0.9)

    ax.set_xlim(left=0)
    ax.set_ylim(bottom=0)
    ax.set_xlabel("solar overscale (s)")
    ax.set_ylabel("wind overscale (w)")
    status = STATUS_CODES.get(int(frontier.status), "unknown")
    # LCOE and the argmin design are exactly baseload-invariant (every CostCoefficients
    # entry scales linearly with it and it cancels in the LCOE ratio -- see
    # cost_calculations.lcoe_coefficients), so it is display-only even when given: it says
    # what physical scale the caller's coeffs were built at, nothing the search itself used.
    # `None` (e.g. coeffs built baseload=1-normalised, with no real MW figure behind them)
    # omits it rather than showing a number that would imply otherwise.
    baseload_part = f"  baseload={baseload:g} MW" if baseload is not None else ""
    ax.set_title(
        f"{region} pixel k={k}  (lat={lat:.3f}, lon={lon:.3f})\n"
        f"coverage={coverage:g}{baseload_part}  status={status}  n_patches={frontier.n_patches}"
    )
    ax.grid(True, alpha=0.2)
    plt.tight_layout()

    if output_path:
        plt.savefig(output_path, dpi=150, bbox_inches="tight")
        plt.close()
    else:
        plt.show()
        plt.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", required=True)
    ap.add_argument("--years", type=int, nargs="+", required=True)
    ap.add_argument("--input-set", default="cds-2024-lulc+excl")
    ap.add_argument("--cost-set", default="default")
    ap.add_argument("--run", default=None, help="defaults to <input-set>__<cost-set>")
    ap.add_argument("--baseload", type=float, default=DEFAULT_BASELOAD)
    ap.add_argument("--coverage", type=float, default=DEFAULT_COVERAGE)
    ap.add_argument("--lat", type=float, default=None)
    ap.add_argument("--lon", type=float, default=None)
    ap.add_argument("--pixel-index", type=int, default=None, dest="pixel_index")
    ap.add_argument("--output", type=Path, default=None, help="Save path; shown interactively if omitted.")
    args = ap.parse_args()

    if args.pixel_index is None and (args.lat is None or args.lon is None):
        ap.error("provide either --pixel-index, or both --lat and --lon")
    if args.pixel_index is not None and (args.lat is not None or args.lon is not None):
        ap.error("--pixel-index is mutually exclusive with --lat/--lon")

    path_config = PathConfig.from_auto_detect(input_set=args.input_set, cost_set=args.cost_set, run=args.run)
    params = SearchParams()
    weather_year = detect_weather_year(path_config)
    cache_dir = path_config.frontier_cache_dir(weather_year)
    cache_file = frontier_cache_path(cache_dir, args.region, args.coverage, params, weather_year, ERA5_DATA_RESOLUTION)
    cache = read_frontier_cache(cache_file, params, args.coverage, weather_year)

    k = args.pixel_index if args.pixel_index is not None else _nearest_pixel(cache, args.lat, args.lon)
    frontier = frontier_at(cache, k)
    lat, lon = float(cache.lats[k]), float(cache.lons[k])
    status = STATUS_CODES.get(int(frontier.status), "unknown")
    print(f"pixel k={k}: lat={lat:.4f}, lon={lon:.4f}, status={status!r}, n_patches={frontier.n_patches}")

    usable = cache.status == STATUS_OK
    winners: dict[int, Optimum | None] = {}
    anchors: dict[int, list[tuple[float, float]]] = {}
    for year in args.years:
        costs, horizon = _validate_cost_set(path_config, year)
        _, capex_per_tech, opex_per_tech, coc_arr = _derive_cost_arrays(
            cache.lats, cache.lons, usable, costs, path_config
        )
        capex_k = {tech: capex_per_tech[tech][k] for tech in ("solar", "wind", "battery")}
        opex_k = {tech: float(opex_per_tech[tech][k]) for tech in ("solar", "wind", "battery")}
        coeffs = lcoe_coefficients(horizon, capex_k, opex_k, float(coc_arr[k]), args.baseload)

        winners[year] = argmin_lcoe(frontier, coeffs) if frontier.n_patches > 0 else None
        anchors[year] = _anchor_seed_points(frontier, params, coeffs)

    plot_search_grid(
        frontier, args.region, k, lat, lon, args.coverage, args.baseload, args.years, winners, anchors, args.output
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
