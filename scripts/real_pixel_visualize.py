"""
Search-grid diagnostic plots for real pixels from the actual global run
`boa_review_full_global_2023_2025_2060` (2023 weather, "default" cost set,
coverage 0.85, queried across the real 2025-2060 horizon that run actually
computed -- see rebuild_and_unify_plotting_prompt.md / this session's history
for why the original `boa_review_full_global_2023` run only had one year and
one build anchor).

Unlike patch_shrink_visualize.py's synthetic anchors, this reuses the real CLI
machinery from boa_search_grid_plot.py's main() -- _validate_cost_set,
_derive_cost_arrays, lcoe_coefficients -- so the winners shown are exactly what
that run's own real cost data would produce.

This cache was built under today's SearchParams() defaults (patch_halfwidth=0.20,
ladder_rungs=1 -- see bisection.py commit 9ac4960), so plain SearchParams() finds
it directly; no historical-params reconstruction needed here, unlike the first
version of this script against the old single-anchor cache.

Picks one pixel per region across 5 regions, chosen by resource mix (solar-heavy,
wind-heavy, mixed) from real land-masked capacity-factor data, restricted to a
STATUS_OK cached pixel, and queries each at several years spanning the horizon --
the multi-year winner trajectory plot_search_grid supports but no real run before
this one had more than a single build/query year to exercise it with.

Run:
    .venv\\Scripts\\python.exe scripts/real_pixel_visualize.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import xarray as xr

sys.path.insert(0, str(Path(__file__).parent))
from boa_search_grid_plot import _anchor_seed_points, _nearest_pixel, plot_search_grid  # noqa: E402

from boa.cds.max_capacity import pixel_area  # noqa: E402
from boa.cli.run_simulation import _validate_cost_set  # noqa: E402
from boa.config.paths import PathConfig  # noqa: E402
from boa.config.physical_parameters import ERA5_DATA_RESOLUTION  # noqa: E402
from boa.model.bisection import STATUS_OK, Optimum, SearchParams, argmin_lcoe  # noqa: E402
from boa.model.cost_calculations import lcoe_coefficients  # noqa: E402
from boa.model.frontier_cache import frontier_at, frontier_cache_path, read_frontier_cache  # noqa: E402
from boa.model.global_extension import _derive_cost_arrays  # noqa: E402

RUN = "boa_review_full_global_2023_2025_2060"
INPUT_SET = "cds-2023-lulc+excl"
COST_SET = "default"
COVERAGE = 0.85
WEATHER_YEAR = 2023
QUERY_YEARS = [2025, 2035, 2050, 2060]  # spans the real anchor horizon this cache was built
#                                         against, so seed placement should track real drift
#                                         across the trajectory rather than being frozen.
LOAD_DENSITY_MW_KM2 = 1.0  # this run's own recorded --load-density (run.json), the real
#                            pipeline's per-pixel absolute demand is
#                            load_density * pixel_area(lat) (global_extension.py) -- not a
#                            flat baseload -- so it is latitude-dependent, real per pixel.
OUT_DIR = Path(__file__).parent / "plots" / "real_pixels"

# This cache was built under today's SearchParams() defaults -- see module docstring.
CACHE_PARAMS = SearchParams()

LIVE_DIR = Path.home() / f".steelo/boa/inputs/{INPUT_SET}/cds-zarr"

# One target resource profile per region, picked for cross-region diversity.
REGION_TARGETS = [
    ("EUROPE", "mixed"),
    ("NORTH_AFRICA", "solar_heavy"),
    ("SOUTH_AMERICA", "wind_heavy"),
    ("SOUTH_ASIA", "solar_heavy"),
    ("NORTH_AMERICA", "mixed"),
]


def pick_candidate_latlon(region: str, kind: str) -> tuple[float, float]:
    """A (lat, lon) with the requested resource profile, from real land-masked CF data."""
    with xr.open_zarr(LIVE_DIR / f"max_capacity_{region}_{WEATHER_YEAR}_025_deg.zarr", consolidated=True) as ds:
        valid = (ds["pv"].values > 0) | (ds["wind"].values > 0)
        lats_grid = ds["y"].values
        lons_grid = ds["x"].values
    with xr.open_zarr(
        LIVE_DIR / f"pv_and_wind_potential_{region}_{WEATHER_YEAR}_025_deg.zarr", consolidated=True
    ) as ds:
        solar_cf = ds["solar"].mean(dim="time").values
        wind_cf = ds["wind"].mean(dim="time").values

    iy, ix = np.nonzero(valid)
    s_vals, w_vals = solar_cf[iy, ix], wind_cf[iy, ix]
    finite = np.isfinite(s_vals) & np.isfinite(w_vals)
    iy, ix, s_vals, w_vals = iy[finite], ix[finite], s_vals[finite], w_vals[finite]

    if kind == "solar_heavy":
        i = int(np.argmax(s_vals - w_vals))
    elif kind == "wind_heavy":
        i = int(np.argmax(w_vals - s_vals))
    else:
        rank_s = s_vals.argsort().argsort() / (len(s_vals) - 1)
        rank_w = w_vals.argsort().argsort() / (len(w_vals) - 1)
        i = int(np.argmax(np.minimum(rank_s, rank_w) - np.abs(rank_s - rank_w)))

    return float(lats_grid[iy[i]]), float(lons_grid[ix[i]])


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path_config = PathConfig.from_auto_detect(input_set=INPUT_SET, cost_set=COST_SET, run=RUN)
    cache_dir = path_config.frontier_cache_dir(WEATHER_YEAR)

    print(f"Loading real costs for {QUERY_YEARS} from run {RUN!r}...")
    costs_by_year = {}
    horizon = None
    for year in QUERY_YEARS:
        costs, horizon = _validate_cost_set(path_config, year)
        costs_by_year[year] = costs
    assert horizon is not None

    for region, kind in REGION_TARGETS:
        print(f"\n[{region}/{kind}]")
        cache_file = frontier_cache_path(cache_dir, region, COVERAGE, CACHE_PARAMS, WEATHER_YEAR, ERA5_DATA_RESOLUTION)
        if not cache_file.exists():
            print(f"  SKIP: no frontier cache at {cache_file}")
            continue
        cache = read_frontier_cache(cache_file, CACHE_PARAMS, COVERAGE, WEATHER_YEAR)

        cand_lat, cand_lon = pick_candidate_latlon(region, kind)
        k = _nearest_pixel(cache, cand_lat, cand_lon)
        lat, lon = float(cache.lats[k]), float(cache.lons[k])
        status = int(cache.status[k])
        print(
            f"  candidate ({cand_lat:.2f}, {cand_lon:.2f}) -> cached pixel k={k} at ({lat:.3f}, {lon:.3f}), status={status}"
        )
        if status != STATUS_OK:
            print(f"  SKIP: nearest cached pixel is not STATUS_OK ({status})")
            continue

        frontier = frontier_at(cache, k)
        usable = cache.status == STATUS_OK
        # This pixel's real absolute demand, exactly as global_extension.py computes it for
        # the actual run -- not a flat placeholder. Affects only the installed-MW/MWh scale
        # shown, never the winning design or its LCOE (both are baseload-invariant). Latitude
        # doesn't move between years, so it is computed once per pixel, outside the year loop.
        load_mw = LOAD_DENSITY_MW_KM2 * float(pixel_area(np.array([lat]))[0])

        winners: dict[int, Optimum | None] = {}
        anchors: dict[int, list[tuple[float, float]]] = {}
        for year in QUERY_YEARS:
            _, capex_per_tech, opex_per_tech, coc_arr = _derive_cost_arrays(
                cache.lats, cache.lons, usable, costs_by_year[year], path_config
            )
            capex_k = {tech: capex_per_tech[tech][k] for tech in ("solar", "wind", "battery")}
            opex_k = {tech: float(opex_per_tech[tech][k]) for tech in ("solar", "wind", "battery")}
            coeffs = lcoe_coefficients(horizon, capex_k, opex_k, float(coc_arr[k]), load_mw)

            winner: Optimum | None = argmin_lcoe(frontier, coeffs) if frontier.n_patches > 0 else None
            winners[year] = winner
            anchors[year] = _anchor_seed_points(frontier, CACHE_PARAMS, coeffs)
            if winner is not None:
                print(
                    f"  y{year}: s={winner.solar:.3f} w={winner.wind:.3f} b={winner.battery:.3f} lcoe={winner.lcoe:.2f}"
                )
            else:
                print(f"  y{year}: no queryable optimum (n_patches=0)")

        out_path = OUT_DIR / f"{region}_{kind}.png"
        plot_search_grid(
            frontier,
            region,
            k,
            lat,
            lon,
            COVERAGE,
            load_mw,
            years=QUERY_YEARS,
            winners=winners,
            anchors=anchors,
            output_path=out_path,
        )
        print(f"  wrote {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
