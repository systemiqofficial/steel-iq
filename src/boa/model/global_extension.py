import numpy as np
import xarray as xr
from concurrent.futures import ThreadPoolExecutor
import time
import logging
from datetime import datetime, timezone
from pathlib import Path

from boa.geo.iso3_finder import iso3_at_batch
from boa.geo.geospatial import choose_land_points_in_cutout
from boa.config.paths import PathConfig
from boa.config.settings import REGION_COORDS, ERA5_DATA_RESOLUTION
from boa.model.single_point_run import (
    build_cost_lookup_indices,
    cost_key_for_point,
    costs_for_key,
)
from boa.model.bisection import (
    STATUS_OK,
    STATUS_ZERO_POTENTIAL,
    CostCoefficients,
    PixelFrontier,
    SearchParams,
    argmin_lcoe,
    build_pixel_frontier,
)
from boa.model.cost_calculations import installation_cost_breakdown, lcoe_coefficients
from boa.model.frontier_cache import (
    RegionFrontierCache,
    build_frontier_meta,
    frontier_at,
    frontier_cache_path,
    read_frontier_cache,
    stack_pixel_frontiers,
    write_frontier_cache,
)
from boa.inputs.profiles import detect_weather_year


def _preload_iso3_from_grid(
    pt_lats: np.ndarray,
    pt_lons: np.ndarray,
    iso3_grid_path: Path,
) -> list[str | None]:
    """Look up the iso3 for every land point via the single-source-of-truth grid.

    Returns a list parallel to pt_lats/pt_lons. Each entry is the ISO3 string,
    or ``None`` for cells with no land tag (oceans / unmapped). Multi-country
    cells are disambiguated via the cities kd-tree.
    """
    if not iso3_grid_path.exists():
        raise FileNotFoundError(
            f"iso3_grid.nc not found at {iso3_grid_path}. "
            "Run `python scripts/build_summary_fixtures.py --only iso3_grid`."
        )
    codes = iso3_at_batch(pt_lats, pt_lons, iso3_grid_path)
    return [c if c else None for c in codes]


# Tile-count heuristics for dynamic scheduling of per-point optimisation (see _adaptive_n_tiles):
# clamp the tile count to [n_workers * TILE_MIN_PER_WORKER, n_workers * TILE_MAX_PER_WORKER],
# targeting ~TARGET_PTS_PER_TILE points per tile between the clamps.
TILE_MIN_PER_WORKER = 4
TILE_MAX_PER_WORKER = 32
TARGET_PTS_PER_TILE = 200


def _adaptive_n_tiles(npts: int, n_workers: int, target_pts_per_tile: int = TARGET_PTS_PER_TILE) -> int:
    """
    Pick a tile count that balances scheduler overhead against dynamic-scheduling granularity.
    Small regions (~13k pts) get the lower clamp (n_workers * TILE_MIN_PER_WORKER); large regions
    (~32k pts) get more tiles so work stealing can rebalance across stragglers. Strided assignment
    is preserved by the caller.
    """
    n_tiles = max(n_workers * TILE_MIN_PER_WORKER, min(n_workers * TILE_MAX_PER_WORKER, npts // target_pts_per_tile))
    return min(n_tiles, npts)


def _float32_output_encoding(ds: xr.Dataset) -> dict[str, dict[str, str]]:
    """
    netCDF encoding that stores float data variables as float32: the values are
    Monte-Carlo estimates (n=1000), so float32's ~7 significant digits lose nothing
    while halving the numeric payload. Coordinates keep float64 — the global combiner
    matches regional grids by exact coordinate equality.
    """
    return {name: {"dtype": "float32"} for name, v in ds.data_vars.items() if v.dtype.kind == "f"}


def _query_frontier_tile(
    tile_indices: np.ndarray,
    cache: RegionFrontierCache,
    capex_per_tech: dict,
    opex_per_tech: dict,
    coc_arr: np.ndarray,
    cost_keys: np.ndarray,
    baseload_demand: float,
    investment_horizon: int,
) -> tuple[float, list[dict], dict[str, int]]:
    """
    Query-phase worker for the grid-bisection search: price one tile's frontiers.

    Far less work than `_query_lcoe_tile`, and the reductions are the point of the rewrite
    rather than a simplification of it. There is no capacity mask, no corner screen and no
    top-up, because the search already resolved the optimum densely. There is no second
    dispatch either: the sampler had to re-run state-of-charge on its winner to swap the LCOE
    denominator from binary coverage to served fraction, whereas here the served fraction was
    stored at build time, so ranking and reporting use the same number by construction.

    What remains is arithmetic: four cost scalars against cached physics, an argmin, and the
    installation-cost breakdown.

    **The capacity ceiling is not applied.** `argmin_lcoe` takes no capacity parameter -- the
    ceiling belongs to Grid 2 -- so between M3 and M4 this reports the *unconstrained* optimum.
    The caller warns about it. Delete that warning together with this note when Grid 2 lands.

    Counters carry the certificate telemetry: how often the patches provably held the optimum,
    and how often the winner sat against a patch edge.
    """
    t0 = time.time()
    results: list[dict] = []
    counters = {"certified": 0, "truncated": 0, "no_optimum": 0, "zero_potential": 0}

    for k in tile_indices:
        cost_key = str(cost_keys[k]) if cost_keys[k] else ""
        status = int(cache.status[k])
        if status != STATUS_OK:
            counters["zero_potential" if status == STATUS_ZERO_POTENTIAL else "no_optimum"] += 1
            # Built explicitly rather than copied from `_ZERO_RESULT`: the template's nested
            # breakdown has to be copied separately or every zero result shares one dict, and
            # spelling the zeros out removes that trap along with the aliasing it guards.
            results.append(
                {
                    "design": {"solar": 0.0, "wind": 0.0, "battery": 0.0},
                    "lcoe": 0.0,
                    "lcoe_coverage_based": 0.0,
                    "installation_cost": 0.0,
                    "installation_cost_breakdown": {"solar": 0.0, "wind": 0.0, "battery": 0.0},
                    "coverage": 0.0,
                    "served_fraction": 0.0,
                    "cost_of_capital": 0.0,
                    "cost_key": cost_key,
                    "status": status,
                }
            )
            continue

        capex_k = {tech: capex_per_tech[tech][k] for tech in ("solar", "wind", "battery")}
        opex_k = {tech: float(opex_per_tech[tech][k]) for tech in ("solar", "wind", "battery")}
        coeffs = lcoe_coefficients(investment_horizon, capex_k, opex_k, float(coc_arr[k]), baseload_demand)
        optimum = argmin_lcoe(frontier_at(cache, int(k)), coeffs)
        total, ic_s, ic_w, ic_b = installation_cost_breakdown(
            optimum.solar, optimum.wind, optimum.battery, baseload_demand, capex_k
        )
        counters["certified"] += int(optimum.patch_certified)
        counters["truncated"] += int(optimum.argmin_truncated)

        results.append(
            {
                "design": {"solar": optimum.solar, "wind": optimum.wind, "battery": optimum.battery},
                "lcoe": optimum.lcoe,
                # Redefined, not dropped: it used to be the ranking value that disagreed with
                # the reported one. Ranking and reporting now agree, so the variable becomes
                # "LCOE if every hour were served", which is the curtailment-free reference.
                "lcoe_coverage_based": optimum.lcoe * optimum.served_fraction,
                "installation_cost": total,
                "installation_cost_breakdown": {"solar": ic_s, "wind": ic_w, "battery": ic_b},
                "coverage": optimum.hours_covered,
                "served_fraction": optimum.served_fraction,
                "cost_of_capital": float(coc_arr[k]),
                "cost_key": cost_key,
                "status": STATUS_OK,
            }
        )

    return time.time() - t0, results, counters


def _store_size_mb(path: Path) -> float:
    """Total on-disk size of a Zarr store directory in MB."""
    return sum(f.stat().st_size for f in Path(path).rglob("*") if f.is_file()) / 1e6


def _frontier_tile(
    tile_indices: np.ndarray,
    solar_arr: np.ndarray,
    wind_arr: np.ndarray,
    coverage: float,
    params: SearchParams,
    anchors: list[CostCoefficients],
) -> tuple[float, list[PixelFrontier]]:
    """
    Build-phase worker for the grid-bisection search: one frontier per point in the tile.

    Counterpart to `_precompute_tile`, which does the same job for the Monte Carlo sampler.
    The degenerate case is handled inside `build_pixel_frontier` rather than screened here,
    so every point produces a frontier and the tile output lines up with `tile_indices`
    positionally — no empty-array sentinels to reassemble.

    TODO: carry a `b_min` hint from one pixel to the next within a tile. The kernels accept
    one and it reaches only the patch bisections, so it can change cost but not results.
    Deferred because it needs a decision about *which* of a frontier's many `b_min` values to
    carry, and a wrong choice is a slower warm start rather than a wrong answer.
    """
    t0 = time.time()
    frontiers = [build_pixel_frontier(solar_arr[k], wind_arr[k], coverage, params, anchors) for k in tile_indices]
    return time.time() - t0, frontiers


def build_frontier_cache_for_region(
    region: str,
    coverage: float,
    profile: xr.Dataset,
    anchors: list[CostCoefficients],
    path_config: PathConfig,
    n_workers: int,
    params: SearchParams | None = None,
    force: bool = False,
) -> Path:
    """
    Build the schema v3 frontier cache for one region and persist it.

    Idempotent: an existing store matching the parameter tuple is returned untouched unless
    `force`. One store serves every baseload, every cost year and every cost scenario — it
    holds dispatch physics and nothing else.

    Three properties worth stating, because the Monte Carlo build it replaces had none of
    them and each is load-bearing:

    - **No capacity ceiling is read or stored.** The search never uses one, so the store
      depends on no land-availability assumption and every layer set built on the same
      weather shares it. The query reads the ceiling separately, and that read must raise
      rather than fall back, because it is now the only thing keeping a stale ceiling out.
    - **The cache directory keys on the weather year**, not the input set, for the same
      reason.
    - **`anchors` is an input, not derived here.** Anchors decide where the dense patches
      go and must cover the cost keys as well as the years (`boa.model.anchors`), but
      deriving them here would tie the store to the canonical iso3 grid, which is precisely
      what the build has always avoided.
    """
    params = SearchParams() if params is None else params
    if not anchors:
        # Fail before the land-point scan and the profile extraction. `build_pixel_frontier`
        # would raise per pixel, but only after a region's worth of setup had been paid for.
        raise ValueError("build_frontier_cache_for_region needs at least one anchor")
    weather_year = detect_weather_year(path_config)
    cache_dir = path_config.frontier_cache_dir(weather_year)
    cache_file = frontier_cache_path(cache_dir, region, coverage, params, weather_year, ERA5_DATA_RESOLUTION)
    if cache_file.exists() and not force:
        logging.info(
            f"Frontier cache for {region} already exists at {cache_file.relative_to(cache_dir)}; "
            f"skipping build (use --force to rebuild)."
        )
        return cache_file

    logging.info(
        f"Building frontier cache for {region} at {cache_file.relative_to(cache_dir)} ({len(anchors)} anchors)."
    )

    land_points, all_lats, all_lons = choose_land_points_in_cutout(profile, path_config.lsm_path)
    npts = len(land_points)
    pt_lats = land_points[:, 0]
    pt_lons = land_points[:, 1]
    lat_to_iy = {v: i for i, v in enumerate(all_lats)}
    lon_to_ix = {v: i for i, v in enumerate(all_lons)}
    iy = np.array([lat_to_iy[la] for la in pt_lats], dtype=np.int32)
    ix = np.array([lon_to_ix[lo] for lo in pt_lons], dtype=np.int32)

    t_extract = time.time()
    # Indexers passed directly rather than through a dict: the sampler's build shares one `sel`
    # between the profiles and the capacity ceiling, and there is no ceiling lookup here.
    prof_pts = profile[["solar", "wind"]].sel(
        y=xr.DataArray(pt_lats, dims="point"),
        x=xr.DataArray(pt_lons, dims="point"),
        method="nearest",
    )
    solar_arr = np.ascontiguousarray(prof_pts["solar"].transpose("point", "time").values, dtype=np.float64)
    wind_arr = np.ascontiguousarray(prof_pts["wind"].transpose("point", "time").values, dtype=np.float64)
    logging.info(f"[timing] profile extraction: {time.time() - t_extract:.1f}s ({npts} points)")

    # Strided tile assignment, kept from the sampler and worth *more* here. The sampler's cost
    # was near-fixed per pixel; a bisection's tracks how hard the site is, so expensive points
    # cluster geographically and contiguous tiles would leave workers idle.
    n_tiles = _adaptive_n_tiles(npts, n_workers)
    order = np.arange(npts)
    tiles = [t for t in (order[i::n_tiles] for i in range(n_tiles)) if len(t)]
    logging.info(f"[timing] frontier tile partition: {len(tiles)} tiles, ~{npts / len(tiles):.0f} pts/tile")

    t_compute = time.time()
    with ThreadPoolExecutor(max_workers=n_workers) as ex:
        tile_results = list(ex.map(lambda t: _frontier_tile(t, solar_arr, wind_arr, coverage, params, anchors), tiles))

    tile_times = np.array([tr[0] for tr in tile_results])
    wall = time.time() - t_compute
    logging.info(
        f"[timing] frontier build ({len(tiles)} tiles, n_workers={n_workers}): {wall:.1f}s | "
        f"tile compute min/mean/max {tile_times.min():.1f}/{tile_times.mean():.1f}/{tile_times.max():.1f}s "
        f"(max/mean={tile_times.max() / tile_times.mean():.2f}), "
        f"parallel efficiency ~{tile_times.sum() / (wall * n_workers):.0%}"
    )

    per_point: list[PixelFrontier | None] = [None] * npts
    for tile_indices, (_, tile_frontiers) in zip(tiles, tile_results):
        for j, k in enumerate(tile_indices):
            per_point[int(k)] = tile_frontiers[j]
    if any(f is None for f in per_point):
        raise RuntimeError(f"{sum(f is None for f in per_point)} of {npts} points produced no frontier")

    cache = stack_pixel_frontiers(
        [f for f in per_point if f is not None],
        region=region,
        all_lats=np.asarray(all_lats),
        all_lons=np.asarray(all_lons),
        lats=pt_lats,
        lons=pt_lons,
        iy=iy,
        ix=ix,
        meta=build_frontier_meta(region, npts, coverage, params, weather_year, ERA5_DATA_RESOLUTION),
    )
    written = write_frontier_cache(cache, cache_file)
    solved = int(np.sum(cache.status == STATUS_OK))
    logging.info(
        f"Wrote frontier cache for {region}: {written.relative_to(cache_dir)} "
        f"({npts} pts, {solved} solved, {_store_size_mb(written):.1f} MB)."
    )
    return written


def query_frontier_cache_for_region(
    year: int,
    region: str,
    baseload_demand: float,
    coverage: float,
    costs: xr.Dataset,
    investment_horizon: int,
    path_config: PathConfig,
    n_workers: int,
    params: SearchParams | None = None,
    force: bool = False,
) -> xr.Dataset:
    """
    Price one region's cached frontiers for one investment year and write the NetCDF.

    No profile dataset: the sampler needed one at query time to re-run dispatch on its winner,
    and the frontier store already holds every dispatch result the pricing needs. That is the
    property the cache exists for — a 36-year sweep simulates the physics once and prices it
    36 times, and the pricing is arithmetic.

    **The capacity ceiling is not applied.** Grid 2 lands in M4; until then this reports the
    unconstrained optimum, and the warning below says so at every call. Delete the warning with
    the note in `_query_frontier_tile` when the constrained search arrives.
    """
    params = SearchParams() if params is None else params
    weather_year = detect_weather_year(path_config)
    cache_dir = path_config.frontier_cache_dir(weather_year)
    cache_file = frontier_cache_path(cache_dir, region, coverage, params, weather_year, ERA5_DATA_RESOLUTION)
    out_path = path_config.optimal_sol_path(baseload_demand, coverage, region, year)
    if out_path.exists() and not force:
        logging.info(f"{out_path.name} already exists; skipping (use --force to re-derive).")
        return xr.open_dataset(out_path)

    logging.warning(
        "[UNCONSTRAINED] The capacity ceiling is not applied yet (Grid 2 arrives in M4), so "
        "these LCOEs are the unconstrained optimum. With the availability layers on the ceiling "
        "binds nearly everywhere, so the results will be optimistic and plausible-looking. Do "
        "not promote them."
    )

    cache = read_frontier_cache(cache_file, params, coverage, weather_year)
    npts = cache.n_points
    logging.info(f"Querying frontier cache for {region} y{year}: {npts} points, {len(cache.all_lats)} lat rows.")

    usable = cache.status == STATUS_OK
    cost_keys, capex_per_tech, opex_per_tech, coc_arr = _derive_cost_arrays(
        cache.lats, cache.lons, usable, costs, path_config
    )

    n_tiles = _adaptive_n_tiles(npts, n_workers)
    order = np.arange(npts)
    tiles = [t for t in (order[i::n_tiles] for i in range(n_tiles)) if len(t)]

    t_lcoe = time.time()
    with ThreadPoolExecutor(max_workers=n_workers) as ex:
        raw = list(
            ex.map(
                lambda t: _query_frontier_tile(
                    t,
                    cache,
                    capex_per_tech,
                    opex_per_tech,
                    coc_arr,
                    cost_keys,
                    baseload_demand,
                    investment_horizon,
                ),
                tiles,
            )
        )
    logging.info(f"[timing] LCOE parallel ({len(tiles)} tiles, n_workers={n_workers}): {time.time() - t_lcoe:.1f}s")

    counters = {key: sum(r[2][key] for r in raw) for key in ("certified", "truncated", "no_optimum", "zero_potential")}
    solved = npts - counters["no_optimum"] - counters["zero_potential"]
    if solved > 0:
        # Certificate telemetry. A low certified rate is a symptom of the coarse tier's
        # deliberate looseness, not of a wrong answer; truncation is the sharper signal,
        # since it says this particular winner sat against an edge the patch imposed.
        logging.info(
            f"[certificate] {region} y{year}: {100 * counters['certified'] / solved:.1f}% of "
            f"{solved} solved pixels provably contained, {100 * counters['truncated'] / solved:.1f}% "
            f"truncated against a patch edge."
        )

    attrs = {
        "investment_year": year,
        "investment_horizon_years": investment_horizon,
        "baseload_demand_mw": baseload_demand,
        "coverage_fraction": coverage,
        "region": region,
        "era5_weather_year": weather_year,
        "era5_resolution_deg": ERA5_DATA_RESOLUTION,
        "search_params_hash": params.identity_hash(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": "Baseload Optimisation Atlas (BOA)",
        "frontier_cache_path": str(cache_file.relative_to(cache_dir)),
        "capacity_ceiling_applied": 0,
    }
    optimal_sol = _assemble_optimal_sol(
        tiles,
        [r[1] for r in raw],
        npts,
        cache.all_lats,
        cache.all_lons,
        cache.iy,
        cache.ix,
        attrs,
        out_path,
    )
    logging.info(f"Wrote {out_path.name} ({solved}/{npts} pixels solved).")
    return optimal_sol


def _derive_cost_arrays(
    lats: np.ndarray,
    lons: np.ndarray,
    usable: np.ndarray,
    costs: xr.Dataset,
    path_config: PathConfig,
) -> tuple[np.ndarray, dict, dict, np.ndarray]:
    """
    Per-point cost keys and the capex/opex/WACC arrays they resolve to.

    Geocoding happens at query time, not build time, so the frontier cache stays independent
    of the canonical iso3 grid — the same reason it holds no capacity ceiling. `usable` marks
    the points worth a lookup; a point with no optimum is never priced, so paying for its
    geocode would be waste.

    Per-pixel fallback logging is suppressed and tallied instead: at ~30k points per region a
    line each would bury everything else, while the totals are what actually signal an
    authoring gap in the cost sheet.
    """
    npts = len(lats)
    grid_iso3 = _preload_iso3_from_grid(lats, lons, path_config.iso3_grid_path)
    iso3_to_subregions, full_cost_key_set = build_cost_lookup_indices(costs)
    fallback_counts: dict[str, int] = {}
    national_counts: dict[str, int] = {}

    class _FallbackCounter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            msg = str(record.getMessage())
            if msg.startswith("[FALLBACK]"):
                if "global" in msg.lower():
                    key = msg.split("Cost key", 1)[-1].split("(", 1)[0].strip() or "UNKNOWN"
                    fallback_counts[key] = fallback_counts.get(key, 0) + 1
                elif "using national" in msg:
                    iso3 = msg.rsplit("using national", 1)[-1].strip(" .") or "UNKNOWN"
                    national_counts[iso3] = national_counts.get(iso3, 0) + 1
                return False
            return True

    _filt = _FallbackCounter()
    logging.getLogger().addFilter(_filt)
    cost_keys = np.full(npts, "", dtype="<U32")
    try:
        for i in range(npts):
            if not usable[i]:
                continue
            cost_keys[i] = cost_key_for_point(
                float(lats[i]),
                float(lons[i]),
                iso3_to_subregions,
                full_cost_key_set,
                country_code=grid_iso3[i],
            )
    finally:
        logging.getLogger().removeFilter(_filt)

    if fallback_counts:
        top = ", ".join(f"{k}={v}" for k, v in sorted(fallback_counts.items(), key=lambda kv: -kv[1])[:10])
        logging.info(f"[FALLBACK] {sum(fallback_counts.values())} pixels used GLOBAL_AVG costs (top iso3s: {top})")
    if national_counts:
        top = ", ".join(f"{k}={v}" for k, v in sorted(national_counts.items(), key=lambda kv: -kv[1]))
        logging.info(
            f"[FALLBACK] {sum(national_counts.values())} pixels used national CAPEX "
            f"(province not authored in the cost sheet): {top}"
        )

    unique_keys = set(str(k) for k in cost_keys.tolist()) - {""}
    key_to_costs = {k: costs_for_key(k, costs) for k in unique_keys}
    n_years = costs["Capex solar"].sizes["year"]
    capex_per_tech = {tech: np.zeros((npts, n_years)) for tech in ("solar", "wind", "battery")}
    opex_per_tech = {tech: np.zeros(npts) for tech in ("solar", "wind", "battery")}
    coc_arr = np.zeros(npts)
    for i in range(npts):
        key_i = str(cost_keys[i])
        if not key_i:
            continue
        capex_i, opex_i, coc_i = key_to_costs[key_i]
        for tech in ("solar", "wind", "battery"):
            capex_per_tech[tech][i] = capex_i[tech]
            opex_per_tech[tech][i] = opex_i[tech]
        coc_arr[i] = coc_i
    logging.info(f"[timing] cost_key derive + lookup: {npts} points, {len(unique_keys)} distinct keys")
    return cost_keys, capex_per_tech, opex_per_tech, coc_arr


def _assemble_optimal_sol(
    tiles: list[np.ndarray],
    tile_results: list[list[dict]],
    npts: int,
    all_lats: np.ndarray,
    all_lons: np.ndarray,
    iy: np.ndarray,
    ix: np.ndarray,
    attrs: dict,
    out_path: Path,
) -> xr.Dataset:
    """
    Scatter per-point results onto the region grid, write the NetCDF, return the dataset.

    `status` is written for every point including the ones with no optimum -- recording why a
    pixel produced nothing is the whole purpose of the code. Everything else stays zero there,
    which is what the output has always meant by "not modelled".
    """
    fields = {
        name: np.zeros(npts)
        for name in (
            "lcoe",
            "lcoe_coverage_based",
            "installation_cost",
            "installation_cost_solar",
            "installation_cost_wind",
            "installation_cost_battery",
            "solar_factor",
            "wind_factor",
            "battery_factor",
            "coverage",
            "served_fraction",
            "cost_of_capital",
        )
    }
    cost_key_flat = np.full(npts, "", dtype=object)
    status_flat = np.zeros(npts, dtype=np.int8)

    for tile_indices, res in zip(tiles, tile_results):
        for j, k in enumerate(tile_indices):
            r = res[j]
            k_int = int(k)
            status_flat[k_int] = r["status"]
            cost_key_flat[k_int] = r.get("cost_key", "")
            if np.isnan(r["lcoe"]):
                continue
            breakdown = r["installation_cost_breakdown"]
            design = r["design"]
            fields["lcoe"][k_int] = r["lcoe"]
            fields["lcoe_coverage_based"][k_int] = r["lcoe_coverage_based"]
            fields["installation_cost"][k_int] = r["installation_cost"]
            fields["installation_cost_solar"][k_int] = breakdown["solar"]
            fields["installation_cost_wind"][k_int] = breakdown["wind"]
            fields["installation_cost_battery"][k_int] = breakdown["battery"]
            fields["solar_factor"][k_int] = design["solar"]
            fields["wind_factor"][k_int] = design["wind"]
            fields["battery_factor"][k_int] = design["battery"]
            fields["coverage"][k_int] = r["coverage"]
            fields["served_fraction"][k_int] = r["served_fraction"]
            fields["cost_of_capital"][k_int] = r["cost_of_capital"]

    shape = (len(all_lats), len(all_lons))

    def _to_grid(flat: np.ndarray, dtype=float) -> np.ndarray:
        grid = np.zeros(shape, dtype=dtype)
        grid[iy, ix] = flat
        return grid

    cost_key_grid = np.full(shape, "", dtype=object)
    cost_key_grid[iy, ix] = cost_key_flat

    optimal_sol = xr.Dataset(
        coords={"lat": all_lats, "lon": all_lons},
        data_vars={
            **{name: (("lat", "lon"), _to_grid(flat)) for name, flat in fields.items()},
            "cost_key": (("lat", "lon"), cost_key_grid),
            "status": (("lat", "lon"), _to_grid(status_flat, np.int8)),
        },
    )
    optimal_sol.attrs.update(attrs)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    optimal_sol.to_netcdf(out_path, mode="w", format="NETCDF4", encoding=_float32_output_encoding(optimal_sol))
    return optimal_sol


def combine_regional_datasets_into_global_dataset(
    year: int,
    coverage: float,
    baseload_demand: float,
    path_config: PathConfig,
    force: bool = False,
) -> xr.Dataset | None:
    """
    Combine all regional datasets into a single global dataset. The datasets are interpolated onto the same grid and merged.
    If any region is missing, the function will return None.
    """
    regions = list(REGION_COORDS.keys())
    regional_datasets = {}

    # Check if the global dataset already exists
    global_output_path = path_config.optimal_sol_path(baseload_demand, coverage, "GLOBAL", year)
    if global_output_path.exists() and not force:
        logging.info(f"Global optimal solution already exists at {global_output_path}. (use --force to re-derive)")
        return xr.open_dataset(global_output_path)
    else:
        logging.info(f"Combining regional datasets into global dataset for {year}.")
        # Load all regional datasets
        for region in regions:
            optimal_sol_path = path_config.optimal_sol_path(baseload_demand, coverage, region, year)
            if not optimal_sol_path.exists():
                logging.warning(f"Optimal solution for {region} not found. Please check processing.")
                return None
            regional_datasets[region] = xr.open_dataset(optimal_sol_path)

        logging.info("Generating global maps from regional datasets.")

        # Define global grid
        lat_global = np.arange(-90, 90.1, 0.25)  # Adjust resolution if needed
        lon_global = np.arange(-180, 180.1, 0.25)

        # Strip the string-typed cost_key and the int8 status from the numeric flow; both are
        # merged separately below (the NaN init would promote status to float, and the
        # zero-strip would erase its "not modelled" code 0).
        NON_NUMERIC_VARS = ("cost_key", "status")
        numeric_datasets = {
            region: ds.drop_vars([v for v in NON_NUMERIC_VARS if v in ds.data_vars])
            for region, ds in regional_datasets.items()
        }
        interpolated_datasets = {
            region: ds.interp(lat=lat_global, lon=lon_global, method="nearest")
            for region, ds in numeric_datasets.items()
        }

        # Initialize global dataset with NaN values
        global_ds = xr.full_like(next(iter(interpolated_datasets.values())), fill_value=np.nan)

        # Merge interpolated datasets into the global dataset
        for region, ds in interpolated_datasets.items():
            for var in ds.data_vars:
                if var not in global_ds:
                    global_ds[var] = xr.full_like(ds[var], fill_value=np.nan)
                global_ds[var] = xr.where(global_ds[var].isnull(), ds[var], global_ds[var])

        # Merge cost_key onto the global grid via per-region exact reindex.
        if any("cost_key" in ds.data_vars for ds in regional_datasets.values()):
            ck_grid = np.full((len(lat_global), len(lon_global)), "", dtype=object)
            for region, ds in regional_datasets.items():
                if "cost_key" not in ds.data_vars:
                    continue
                rds = ds[["cost_key"]].reindex(lat=lat_global, lon=lon_global)
                arr = np.asarray(rds["cost_key"].values, dtype=object)
                nonempty = np.array([v is not None and str(v) not in ("", "nan") for v in arr.flat]).reshape(arr.shape)
                mask = (ck_grid == "") & nonempty
                ck_grid[mask] = arr[mask]
            global_ds["cost_key"] = (("lat", "lon"), ck_grid)

        # Same treatment for the int8 status band: exact reindex per region, first non-zero wins.
        if any("status" in ds.data_vars for ds in regional_datasets.values()):
            status_grid = np.zeros((len(lat_global), len(lon_global)), dtype=np.int8)
            for region, ds in regional_datasets.items():
                if "status" not in ds.data_vars:
                    continue
                rds = ds[["status"]].reindex(lat=lat_global, lon=lon_global, fill_value=0)
                arr = np.asarray(rds["status"].values).astype(np.int8)
                mask = (status_grid == 0) & (arr != 0)
                status_grid[mask] = arr[mask]
            global_ds["status"] = (("lat", "lon"), status_grid)

        # A region built under a different SearchParams (or re-queried after a frontier
        # rebuild) must not silently combine with stale neighbours — the GLOBAL attrs below
        # claim a single hash for the lot.
        hashes = {ds.attrs.get("search_params_hash") for ds in regional_datasets.values()}
        assert len(hashes) == 1, (
            f"regional files disagree on search_params_hash: {sorted(map(str, hashes))}. "
            "Re-query the stale regions before combining."
        )

        # Carry run/provenance metadata from regional files; override region and refresh timestamp
        global_ds.attrs.update(next(iter(regional_datasets.values())).attrs)
        global_ds.attrs["region"] = "GLOBAL"
        global_ds.attrs["created_at"] = datetime.now(timezone.utc).isoformat()

        # Save the global dataset
        global_output_path.parent.mkdir(parents=True, exist_ok=True)
        global_ds.to_netcdf(global_output_path, encoding=_float32_output_encoding(global_ds))

        return global_ds
