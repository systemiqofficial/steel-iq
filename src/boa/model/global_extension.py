import numpy as np
import xarray as xr
from concurrent.futures import ThreadPoolExecutor
import time
import logging
from datetime import datetime, timezone
from pathlib import Path

from boa.geo.iso3_finder import (
    iso3_at_batch,
    load_subregion_polygons,
    validate_subregion_coverage,
    validate_subregion_keys,
)
from boa.geo.geospatial import choose_land_points_in_cutout
from boa.config.paths import PathConfig
from boa.config.settings import (
    REGION_COORDS,
    ERA5_DATA_RESOLUTION,
    ERA5_DATA_YEAR,
    MIN_SURVIVOR_FRACTION,
    RANDOM_SEED,
    OVERSCALE_SAMPLING_MEANS,
)
from boa.inputs.costs import (
    process_global_baseload_simulation_costs,
)
from boa.model.single_point_run import (
    build_cost_lookup_indices,
    cost_key_for_point,
    costs_for_key,
)
from boa.model.logic import (
    PointDesignState,
    calculate_served_fraction,
    compute_lcoe_from_state,
    min_survivors_required,
    precompute_point_state,
    state_of_charge,
)
from boa.model import design_cache
from boa.inputs.profiles import open_regional_dataset
from boa.model.diagnostics import (
    plot_regional_optimum_baseload_power_simulation_map,
    plot_global_optimum_baseload_power_simulation_map,
)

_ZERO_RESULT = {
    "design": {"solar": 0.0, "wind": 0.0, "battery": 0.0},
    "lcoe": 0.0,
    "lcoe_coverage_based": 0.0,
    "installation_cost": 0.0,
    "installation_cost_breakdown": {"solar": 0.0, "wind": 0.0, "battery": 0.0},
    "coverage": 0.0,
    "served_fraction": 0.0,
    "cost_of_capital": 0.0,
    "cost_key": "",
    # Per-pixel feasibility status (see boa.config.constants.STATUS_CODES); always overwritten
    # per point, so the template default is the "never written" code.
    "status": 0,
}


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


def _precompute_tile(
    tile_indices: np.ndarray,
    solar_arr: np.ndarray,
    wind_arr: np.ndarray,
    pv_max: np.ndarray,
    wind_max: np.ndarray,
    baseload_demand: float,
    p: int,
    n: int,
    seed: int,
) -> tuple[float, list[tuple[np.ndarray, np.ndarray]]]:
    """
    Build-phase worker. Runs the year-independent compute (Monte Carlo + coverage
    filter + battery sizing) for every point in `tile_indices`, returning each
    point's accepted designs as a CSR-ready (designs, coverage) pair. Zero-potential
    points and points where no design passed the filter both emit empty arrays.
    """
    t0 = time.time()
    empty_d = np.empty((0, 3), dtype=np.float64)
    empty_c = np.empty(0, dtype=np.float64)
    out: list[tuple[np.ndarray, np.ndarray]] = []
    for k in tile_indices:
        solar = solar_arr[k]
        wind = wind_arr[k]
        if solar.sum() == 0 and wind.sum() == 0:
            out.append((empty_d, empty_c))
            continue
        overbuild_limit = {
            "solar": float(pv_max[k]) / baseload_demand,
            "wind": float(wind_max[k]) / baseload_demand,
        }
        state = precompute_point_state(solar, wind, p, n, seed, mus=OVERSCALE_SAMPLING_MEANS, limit=overbuild_limit)
        accepted = state.accepted_mask
        if accepted is None or not accepted.any():
            out.append((empty_d, empty_c))
            continue
        out.append(
            (
                np.ascontiguousarray(state.designs[accepted], dtype=np.float64),
                np.ascontiguousarray(state.coverage[accepted], dtype=np.float64),
            )
        )
    return time.time() - t0, out


def _query_lcoe_tile(
    tile_indices: np.ndarray,
    designs_flat: np.ndarray,
    design_offsets: np.ndarray,
    coverage_flat: np.ndarray,
    capex_per_tech: dict,
    opex_per_tech: dict,
    coc_arr: np.ndarray,
    cost_keys: np.ndarray,
    solar_profiles: np.ndarray,
    wind_profiles: np.ndarray,
    baseload_demand: float,
    investment_horizon: int,
    min_survivors: int = 1,
) -> tuple[float, list[dict]]:
    """
    Query-phase worker. Per point in `tile_indices`: look up cached surviving
    designs, run closed-form LCOE on all of them, pick the minimum, then re-run
    a single-design SoC dispatch on the picked optimum to swap the LCOE
    denominator from binary coverage to served_fraction. Points with no cached
    designs, and points whose survivor count is below `min_survivors`, emit a
    _ZERO_RESULT (with their cost_key). Every result carries a `status` code
    (see STATUS_CODES) recording which of those cases it is.
    """
    t0 = time.time()
    results: list[dict] = []

    def _zero_result(cost_key: str, status: int) -> dict:
        zero = dict(_ZERO_RESULT)
        zero["installation_cost_breakdown"] = dict(_ZERO_RESULT["installation_cost_breakdown"])
        zero["cost_key"] = cost_key
        zero["status"] = status
        return zero

    for k in tile_indices:
        cost_key = str(cost_keys[k]) if cost_keys[k] else ""
        lo, hi = int(design_offsets[k]), int(design_offsets[k + 1])
        if lo == hi:
            # The cache conflates "no design met coverage" with "no resource at all";
            # split them on the query-time profiles the worker already holds.
            zero_potential = solar_profiles[k].sum() == 0 and wind_profiles[k].sum() == 0
            results.append(_zero_result(cost_key, 3 if zero_potential else 2))
            continue
        if hi - lo < min_survivors:
            results.append(_zero_result(cost_key, 4))
            continue
        state = PointDesignState(
            designs=designs_flat[lo:hi],
            coverage=coverage_flat[lo:hi],
            accepted_mask=None,
        )
        capex_k = {tech: capex_per_tech[tech][k] for tech in ("solar", "wind", "battery")}
        opex_k = {tech: float(opex_per_tech[tech][k]) for tech in ("solar", "wind", "battery")}
        coc_k = float(coc_arr[k])
        lcoe = compute_lcoe_from_state(
            state,
            baseload_demand,
            capex_k,
            opex_k,
            coc_k,
            investment_horizon,
        )
        lcoes = lcoe["lcoes"]
        j = int(np.argmin(lcoes))

        # Refine the picked optimum's LCOE with served_fraction.
        coverage_pick = float(state.coverage[j])
        lcoe_coverage_based = float(lcoes[j])
        net_nrg_pick = state.designs[j, 0] * solar_profiles[k] + state.designs[j, 1] * wind_profiles[k] - 1.0
        soc_pick = state_of_charge(net_nrg_pick, float(state.designs[j, 2]))
        served_fraction_pick = calculate_served_fraction(soc_pick, net_nrg_pick)
        lcoe_refined = lcoe_coverage_based * coverage_pick / served_fraction_pick

        results.append(
            {
                "design": {
                    "solar": float(state.designs[j, 0]),
                    "wind": float(state.designs[j, 1]),
                    "battery": float(state.designs[j, 2]),
                },
                "lcoe": float(lcoe_refined),
                "lcoe_coverage_based": lcoe_coverage_based,
                "installation_cost": float(lcoe["installation_costs"][j]),
                "installation_cost_breakdown": {
                    "solar": float(lcoe["ic_solar"][j]),
                    "wind": float(lcoe["ic_wind"][j]),
                    "battery": float(lcoe["ic_battery"][j]),
                },
                "coverage": coverage_pick,
                "served_fraction": float(served_fraction_pick),
                "cost_of_capital": coc_k,
                "cost_key": cost_key,
                # A zero served_fraction makes the refined LCOE NaN; the assembly loop drops
                # such points, so they are "no usable optimum" rather than a found optimum.
                "status": 1 if np.isfinite(lcoe_refined) else 2,
            }
        )
    return time.time() - t0, results


def build_design_cache_for_region(
    region: str,
    baseload_demand: float,
    p: int,
    n: int,
    profile: xr.Dataset,
    costs: xr.Dataset,
    path_config: PathConfig,
    n_workers: int,
    force: bool = False,
) -> Path:
    """
    Build the year-independent design cache for one region and persist it under
    `path_config.design_cache_dir`. Idempotent: if a cache matching the parameter
    hash already exists, returns its path without rebuilding.

    The build phase no longer touches the iso3 grid or the costs dataset —
    cost_keys are derived fresh per query (see ``query_design_cache_for_region``)
    so the cache stays canonical-iso3-grid-agnostic. ``costs`` is accepted only
    for API parity and is unused here.
    """
    # Auto-migrate any pre-v1.2 caches (flat layout) sitting at the top of the
    # cache dir into the current nested layout. Idempotent: fast no-op once done.
    design_cache.migrate_legacy_cache_filenames(path_config.design_cache_dir)

    cache_file = design_cache.cache_path(
        path_config.design_cache_dir,
        region,
        baseload_demand,
        p,
        n,
        RANDOM_SEED,
        ERA5_DATA_YEAR,
        ERA5_DATA_RESOLUTION,
    )
    if cache_file.exists() and not force:
        rel = cache_file.relative_to(path_config.design_cache_dir)
        logging.info(f"Design cache for {region} already exists at {rel}; skipping build (use --force to rebuild).")
        return cache_file

    logging.info(f"Building design cache for {region} at {cache_file.relative_to(path_config.design_cache_dir)}.")

    # Choose land points + map to output-grid indices.
    land_points, all_lats, all_lons = choose_land_points_in_cutout(profile, path_config.lsm_path)
    npts = len(land_points)
    pt_lats = land_points[:, 0]
    pt_lons = land_points[:, 1]
    lat_to_iy = {v: i for i, v in enumerate(all_lats)}
    lon_to_ix = {v: i for i, v in enumerate(all_lons)}
    iy = np.array([lat_to_iy[la] for la in pt_lats], dtype=np.int32)
    ix = np.array([lon_to_ix[lo] for lo in pt_lons], dtype=np.int32)

    # Compact (npts, T) extraction shared across threads via views.
    max_cap = open_regional_dataset("max_cap", region, path_config)
    DTYPE = np.float64
    t_extract = time.time()
    sel = dict(y=xr.DataArray(pt_lats, dims="point"), x=xr.DataArray(pt_lons, dims="point"))
    prof_pts = profile[["solar", "wind"]].sel(**sel, method="nearest")
    solar_arr = np.ascontiguousarray(prof_pts["solar"].transpose("point", "time").values, dtype=DTYPE)
    wind_arr = np.ascontiguousarray(prof_pts["wind"].transpose("point", "time").values, dtype=DTYPE)
    mc_pts = max_cap[["pv", "wind"]].sel(**sel, method="nearest")
    pv_max = np.asarray(mc_pts["pv"].values, dtype=np.float64)
    wind_max = np.asarray(mc_pts["wind"].values, dtype=np.float64)
    logging.info(f"[timing] profile extraction: {time.time() - t_extract:.1f}s ({npts} points)")

    # Strided tile assignment + adaptive tile count (load balance for
    # geographically-clustered expensive points; see OPTIMIZATION_NOTES.md).
    n_tiles = _adaptive_n_tiles(npts, n_workers)
    order = np.arange(npts)
    tiles = [order[i::n_tiles] for i in range(n_tiles)]
    tiles = [t for t in tiles if len(t)]
    logging.info(f"[timing] precompute tile partition: {len(tiles)} tiles, ~{npts / len(tiles):.0f} pts/tile")
    t_compute = time.time()
    with ThreadPoolExecutor(max_workers=n_workers) as ex:
        tile_results = list(
            ex.map(
                lambda t: _precompute_tile(
                    t,
                    solar_arr,
                    wind_arr,
                    pv_max,
                    wind_max,
                    baseload_demand,
                    p,
                    n,
                    RANDOM_SEED,
                ),
                tiles,
            )
        )

    tile_times = np.array([tr[0] for tr in tile_results])
    wall = time.time() - t_compute
    logging.info(
        f"[timing] precompute parallel ({len(tiles)} tiles, n_workers={n_workers}): {wall:.1f}s | "
        f"tile compute min/mean/max {tile_times.min():.1f}/{tile_times.mean():.1f}/{tile_times.max():.1f}s "
        f"(max/mean={tile_times.max() / tile_times.mean():.2f}), "
        f"parallel efficiency ~{tile_times.sum() / (wall * n_workers):.0%}"
    )

    # Reassemble per-point order (tiles are strided index lists).
    empty_d = np.empty((0, 3), dtype=np.float64)
    empty_c = np.empty(0, dtype=np.float64)
    per_point_designs: list[np.ndarray] = [empty_d] * npts
    per_point_coverage: list[np.ndarray] = [empty_c] * npts
    for tile_indices, (_, tile_out) in zip(tiles, tile_results):
        for j, k in enumerate(tile_indices):
            per_point_designs[int(k)] = tile_out[j][0]
            per_point_coverage[int(k)] = tile_out[j][1]

    designs_flat, design_offsets, coverage_flat = design_cache.pack_csr(
        per_point_designs,
        per_point_coverage,
    )

    cache = design_cache.RegionDesignCache(
        region=region,
        all_lats=np.asarray(all_lats, dtype=np.float64),
        all_lons=np.asarray(all_lons, dtype=np.float64),
        lats=pt_lats.astype(np.float64),
        lons=pt_lons.astype(np.float64),
        iy=iy,
        ix=ix,
        pv_max=pv_max,
        wind_max=wind_max,
        designs_flat=designs_flat,
        design_offsets=design_offsets,
        coverage_flat=coverage_flat,
        meta=design_cache.build_cache_meta(
            region,
            npts,
            designs_flat.shape[0],
            baseload_demand,
            p,
            n,
            RANDOM_SEED,
            ERA5_DATA_YEAR,
            ERA5_DATA_RESOLUTION,
        ),
    )
    written = design_cache.write_cache(cache, cache_file)
    rel = written.relative_to(path_config.design_cache_dir)
    logging.info(
        f"Wrote design cache for {region}: {rel} ({npts} pts, {designs_flat.shape[0]} surviving designs total)."
    )
    return written


def query_design_cache_for_region(
    year: int,
    region: str,
    baseload_demand: float,
    p: int,
    profile: xr.Dataset,
    costs: xr.Dataset,
    investment_horizon: int,
    n: int,
    path_config: PathConfig,
    n_workers: int,
    force: bool = False,
) -> xr.Dataset:
    """
    LCOE-only re-derivation from a pre-built design cache. Loads the per-region
    cache, derives cost_keys fresh from ``data/iso3_grid.nc`` (so canonical-iso3
    grid changes flow through without rebuilding the cache), looks up year-specific
    costs via a once-per-query ``{cost_key: (capex, opex, coc)}`` table, computes
    LCOE for every surviving design, picks the minimum per point, and assembles +
    writes the NetCDF.
    """
    optimal_sol_path = path_config.optimal_sol_path(baseload_demand, p, region, year)
    if optimal_sol_path.exists() and not force:
        logging.info(
            f"Optimal solution for {region} y{year} already exists; loading from disk (use --force to re-derive)."
        )
        return xr.open_dataset(optimal_sol_path)

    design_cache.migrate_legacy_cache_filenames(path_config.design_cache_dir)
    cache_file = design_cache.cache_path(
        path_config.design_cache_dir,
        region,
        baseload_demand,
        p,
        n,
        RANDOM_SEED,
        ERA5_DATA_YEAR,
        ERA5_DATA_RESOLUTION,
    )
    cache = design_cache.read_cache(cache_file)
    npts = cache.n_points
    logging.info(f"Loaded design cache for {region}: {npts} pts, {cache.n_designs_total} surviving designs.")

    # Derive cost_keys fresh from the canonical iso3 grid + subregion polygons.
    # Empty cached cost_keys are skipped (zero-potential pixels — no LCOE to compute).
    # Hot-path optimisation: pre-compute the (iso3_to_subregions, full_cost_key_set) indices
    # once, then resolve each pixel's cost_key string without xarray .sel() calls. After the
    # per-point pass we build a {cost_key: (capex, opex, coc)} table covering only the
    # distinct keys actually seen (~250 vs ~30k pixels) and reuse it across all points.
    t_costs = time.time()
    grid_iso3 = _preload_iso3_from_grid(cache.lats, cache.lons, path_config.iso3_grid_path)
    iso3_to_subregions, full_cost_key_set = build_cost_lookup_indices(costs)
    fallback_counts: dict[str, int] = {}
    national_counts: dict[str, int] = {}

    class _FallbackCounter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            msg = str(record.getMessage())
            if msg.startswith("[FALLBACK]"):
                # Suppress all per-pixel fallback spam; tally global-average fallbacks
                # and province→national ones (the latter resolve to a real cost row,
                # but a large share signals an authoring gap worth surfacing).
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
    has_design = cache.design_offsets[1:] > cache.design_offsets[:-1]
    try:
        for i in range(npts):
            if not has_design[i]:
                continue  # zero-potential point; LCOE won't run.
            cost_keys[i] = cost_key_for_point(
                float(cache.lats[i]),
                float(cache.lons[i]),
                iso3_to_subregions,
                full_cost_key_set,
                country_code=grid_iso3[i],
            )
    finally:
        logging.getLogger().removeFilter(_filt)
    if fallback_counts:
        summary = ", ".join(f"{k}={v}" for k, v in sorted(fallback_counts.items(), key=lambda kv: -kv[1])[:10])
        total = sum(fallback_counts.values())
        logging.info(f"[FALLBACK] {total} pixels used GLOBAL_AVG costs (top iso3s: {summary})")
    if national_counts:
        summary = ", ".join(f"{k}={v}" for k, v in sorted(national_counts.items(), key=lambda kv: -kv[1]))
        logging.info(
            f"[FALLBACK] {sum(national_counts.values())} pixels used national CAPEX "
            f"(province not authored in the cost sheet): {summary}"
        )

    # Build {cost_key: (capex, opex, coc)} once for the distinct keys, then scatter to per-point.
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
    logging.info(
        f"[timing] cost_key derive + lookup: {time.time() - t_costs:.1f}s "
        f"({npts} points, {len(unique_keys)} distinct keys)"
    )

    # Per-point hourly profiles for the served-fraction refinement.
    t_prof = time.time()
    prof_pts = (
        profile[["solar", "wind"]]
        .sel(
            x=xr.DataArray(cache.lons, dims="point"),
            y=xr.DataArray(cache.lats, dims="point"),
            method="nearest",
        )
        .transpose("point", "time")
    )  # enforce (npts, T) so solar_profiles[k] is per-point
    solar_profiles = prof_pts["solar"].values  # (npts, T)
    wind_profiles = prof_pts["wind"].values  # (npts, T)
    logging.info(f"[timing] profile extraction for refinement: {time.time() - t_prof:.1f}s")

    # Parallel LCOE — cheap (~5 ms / point), so threading is mostly for code parity
    # with the build phase rather than a meaningful wall-time win.
    n_tiles = _adaptive_n_tiles(npts, n_workers)
    order = np.arange(npts)
    tiles = [order[i::n_tiles] for i in range(n_tiles)]
    tiles = [t for t in tiles if len(t)]
    min_survivors = min_survivors_required(n)
    t_lcoe = time.time()
    with ThreadPoolExecutor(max_workers=n_workers) as ex:
        tile_results = list(
            ex.map(
                lambda t: _query_lcoe_tile(
                    t,
                    cache.designs_flat,
                    cache.design_offsets,
                    cache.coverage_flat,
                    capex_per_tech,
                    opex_per_tech,
                    coc_arr,
                    cost_keys,
                    solar_profiles,
                    wind_profiles,
                    baseload_demand,
                    investment_horizon,
                    min_survivors,
                ),
                tiles,
            )
        )
    wall = time.time() - t_lcoe
    tile_times = np.array([tr[0] for tr in tile_results])
    logging.info(
        f"[timing] LCOE parallel ({len(tiles)} tiles, n_workers={n_workers}): {wall:.1f}s | "
        f"tile compute min/mean/max {tile_times.min():.2f}/{tile_times.mean():.2f}/{tile_times.max():.2f}s"
    )
    tile_results = [tr[1] for tr in tile_results]

    # Assemble per-field flat arrays then vectorised assignment into the output grid.
    t_assemble = time.time()
    fields = {
        "lcoe": np.zeros(npts),
        "lcoe_coverage_based": np.zeros(npts),
        "installation_cost": np.zeros(npts),
        "installation_cost_solar": np.zeros(npts),
        "installation_cost_wind": np.zeros(npts),
        "installation_cost_battery": np.zeros(npts),
        "solar_factor": np.zeros(npts),
        "wind_factor": np.zeros(npts),
        "battery_factor": np.zeros(npts),
        "coverage": np.zeros(npts),
        "served_fraction": np.zeros(npts),
        "cost_of_capital": np.zeros(npts),
    }
    cost_key_flat = np.full(npts, "", dtype=object)
    status_flat = np.zeros(npts, dtype=np.int8)
    for tile_indices, res in zip(tiles, tile_results):
        for j, k in enumerate(tile_indices):
            r = res[j]
            # Status is recorded even for the dropped points — that is the whole point of it.
            status_flat[int(k)] = r["status"]
            if np.isnan(r["lcoe"]):
                continue
            k_int = int(k)
            fields["lcoe"][k_int] = r["lcoe"]
            fields["lcoe_coverage_based"][k_int] = r["lcoe_coverage_based"]
            fields["installation_cost"][k_int] = r["installation_cost"]
            fields["installation_cost_solar"][k_int] = r["installation_cost_breakdown"]["solar"]
            fields["installation_cost_wind"][k_int] = r["installation_cost_breakdown"]["wind"]
            fields["installation_cost_battery"][k_int] = r["installation_cost_breakdown"]["battery"]
            fields["solar_factor"][k_int] = r["design"]["solar"]
            fields["wind_factor"][k_int] = r["design"]["wind"]
            fields["battery_factor"][k_int] = r["design"]["battery"]
            fields["coverage"][k_int] = r["coverage"]
            fields["served_fraction"][k_int] = r["served_fraction"]
            fields["cost_of_capital"][k_int] = r["cost_of_capital"]
            cost_key_flat[k_int] = r.get("cost_key", "")

    n_rejected = int((status_flat == 4).sum())
    if n_rejected > 0:
        logging.info(
            f"{n_rejected} pixels rejected by minimum-survivor cut "
            f"(<{min_survivors} of n={n} designs surviving) in {region}."
        )

    # Fallback summary (cost_keys carry the :GLOBAL_AVG marker for pixels whose
    # iso3 has no cost-data row — mostly Antarctica plus a handful of micro-states).
    valid = fields["lcoe"] > 0
    is_fallback = np.array([isinstance(ck, str) and ck.endswith(":GLOBAL_AVG") for ck in cost_keys])
    fallback_mask = valid & is_fallback
    n_fallback = int(fallback_mask.sum())
    if n_fallback > 0:
        n_valid = int(valid.sum())
        global_mean_wacc = float(costs["Cost of capital"].mean(dim="iso3").values)
        logging.warning(
            f"[FALLBACK] {n_fallback}/{n_valid} valid gridpoints "
            f"({100 * n_fallback / max(n_valid, 1):.1f}%) in {region} used global-average "
            f"costs (WACC={global_mean_wacc:.4f})."
        )

    all_lats = cache.all_lats
    all_lons = cache.all_lons
    iy = cache.iy
    ix = cache.ix

    def _to_grid(flat: np.ndarray) -> np.ndarray:
        grid = np.zeros((len(all_lats), len(all_lons)))
        grid[iy, ix] = flat
        return grid

    def _to_grid_int8(flat: np.ndarray) -> np.ndarray:
        grid = np.zeros((len(all_lats), len(all_lons)), dtype=np.int8)
        grid[iy, ix] = flat
        return grid

    cost_key_grid = np.full((len(all_lats), len(all_lons)), "", dtype=object)
    cost_key_grid[iy, ix] = cost_key_flat

    optimal_sol = xr.Dataset(
        coords={"lat": all_lats, "lon": all_lons},
        data_vars={
            **{name: (("lat", "lon"), _to_grid(flat)) for name, flat in fields.items()},
            "cost_key": (("lat", "lon"), cost_key_grid),
            "status": (("lat", "lon"), _to_grid_int8(status_flat)),
        },
    )
    logging.info(f"[timing] result assembly: {time.time() - t_assemble:.1f}s")
    optimal_sol.attrs.update(
        {
            "investment_year": year,
            "investment_horizon_years": investment_horizon,
            "baseload_demand_mw": baseload_demand,
            "coverage_fraction": 1 - p / 100,
            "p_percentile": p,
            "n_samples": n,
            "random_seed": RANDOM_SEED,
            "min_survivor_fraction": MIN_SURVIVOR_FRACTION,
            "min_survivors": min_survivors,
            "region": region,
            "era5_weather_year": ERA5_DATA_YEAR,
            "era5_resolution_deg": ERA5_DATA_RESOLUTION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": "Baseload Optimisation Atlas (BOA)",
            "design_cache_path": str(cache_file.relative_to(path_config.design_cache_dir)),
        }
    )
    optimal_sol_path.parent.mkdir(parents=True, exist_ok=True)
    optimal_sol.to_netcdf(optimal_sol_path, mode="w", format="NETCDF4")
    return optimal_sol


def execute_baseload_optimization_for_region(
    year: int,
    region: str,
    baseload_demand: float,
    p: int,
    profile: xr.Dataset,
    costs: xr.Dataset,
    investment_horizon: int,
    n: int,
    path_config: PathConfig,
    n_workers: int,
    force: bool = False,
) -> xr.Dataset:
    """
    Run the per-region pipeline: build the design cache if missing, then query it
    with year-specific costs to produce the optimal-solution NetCDF. Caller
    contract is unchanged from the pre-cache implementation; the speedup comes
    from cache hits on subsequent (year, scenario) runs over the same region.
    """
    optimal_sol_path = path_config.optimal_sol_path(baseload_demand, p, region, year)
    if optimal_sol_path.exists() and not force:
        logging.info(
            f"Optimal solution for {region} already exists. Loading from {optimal_sol_path} (use --force to re-derive)."
        )
        return xr.open_dataset(optimal_sol_path)

    build_design_cache_for_region(
        region,
        baseload_demand,
        p,
        n,
        profile,
        costs,
        path_config,
        n_workers,
        force=force,
    )
    return query_design_cache_for_region(
        year,
        region,
        baseload_demand,
        p,
        profile,
        costs,
        investment_horizon,
        n,
        path_config,
        n_workers,
        force=force,
    )


def combine_regional_datasets_into_global_dataset(
    year: int,
    p: int,
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
    global_output_path = path_config.optimal_sol_path(baseload_demand, p, "GLOBAL", year)
    if global_output_path.exists() and not force:
        logging.info(f"Global optimal solution already exists at {global_output_path}. (use --force to re-derive)")
        return xr.open_dataset(global_output_path)
    else:
        logging.info(f"Combining regional datasets into global dataset for {year}.")
        # Load all regional datasets
        for region in regions:
            optimal_sol_path = path_config.optimal_sol_path(baseload_demand, p, region, year)
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

        # Remove zero values
        global_ds = global_ds.where(global_ds != 0)

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

        # A region re-queried under a changed threshold must not silently combine with
        # stale neighbours — the GLOBAL attrs below claim a single fraction for the lot.
        fractions = {ds.attrs.get("min_survivor_fraction") for ds in regional_datasets.values()}
        assert len(fractions) == 1, (
            f"regional files disagree on min_survivor_fraction: {sorted(map(str, fractions))}. "
            "Re-query the stale regions before combining."
        )

        # Carry run/provenance metadata from regional files; override region and refresh timestamp
        global_ds.attrs.update(next(iter(regional_datasets.values())).attrs)
        global_ds.attrs["region"] = "GLOBAL"
        global_ds.attrs["created_at"] = datetime.now(timezone.utc).isoformat()

        # Save the global dataset
        global_output_path.parent.mkdir(parents=True, exist_ok=True)
        global_ds.to_netcdf(global_output_path)

        return global_ds


def execute_baseload_power_simulation(
    path_config: PathConfig,
    year: int,
    region: str,
    baseload_demand: float,
    p: int,
    n: int,
    n_workers: int,
    generate_plots: bool = True,
    force: bool = False,
) -> xr.Dataset | None:
    """
    Execute the baseload power simulation for a given region or globally. Land grid points are
    optimised in parallel via a ThreadPoolExecutor; results are saved to NetCDF and optionally
    plotted.

    Parameters:
        - path_config: Configuration object containing all necessary paths for the baseload power simulation.
        - year: Investment year for the simulation.
        - region: Region to run the simulation for. If "GLOBAL", the simulation is run for all regions and combined to a
        global map by the end.
        - baseload_demand: Baseload demand in MW (default: 500.0).
        - p: Percentile of time where we don't cover the demand (e.g. 15 means 15th percentile).
        - n: Number of random samples to generate for each grid point. Controls the variability of the distributions of
        overscale factors for solar, wind, and batteries.
        - generate_plots: If True, generate regional and global map plots during the run. If False, skip plotting
        (useful for long runs where plots can be regenerated later from saved NetCDFs).
        - n_workers: Number of threads to use for parallel grid-point optimization.

    Returns:
        xr.Dataset | None: Optimal solution dataset containing lcoe, installation_cost, solar_factor, wind_factor,
        and battery_factor. Returns None if the simulation fails.
    """
    start = time.time()

    # Preprocess costs globally and save to file
    projected_cost_per_country, investment_horizon = process_global_baseload_simulation_costs(
        investment_year=year,
        input_data_path=path_config.input_data_path,
        cost_cache_dir=path_config.cost_cache_dir,
    )
    # Subregion polygons are needed only when the cost dataset declares multi-subregion iso3s.
    # Load (no-op if file missing) then validate that any such iso3 has matching polygons.
    load_subregion_polygons(path_config.admin1_10m_shapefile_path)
    validate_subregion_coverage(projected_cost_per_country)
    validate_subregion_keys(projected_cost_per_country, path_config.admin1_10m_shapefile_path)

    if region == "GLOBAL":
        logging.info(f"Running global baseload optimization for year {year}.")
        # Run for all continents
        for continent in REGION_COORDS.keys():
            logging.info(f"Running baseload optimization for {continent}")
            # Profiles for REs (backend selected by PROFILE_DATA_SOURCE; spatially-chunked Zarr is
            # dramatically faster for the per-point reads than scanning the regional NetCDF)
            profile = open_regional_dataset("profile", continent, path_config)
            # Optimization
            execute_baseload_optimization_for_region(
                year,
                continent,
                baseload_demand,
                p,
                profile,
                projected_cost_per_country,
                investment_horizon,
                n=n,
                path_config=path_config,
                n_workers=n_workers,
                force=force,
            )
            # Plot results for single continent
            if generate_plots:
                plot_regional_optimum_baseload_power_simulation_map(year, continent, p, baseload_demand, path_config)
        # Combine all regions into a single dataset and save to file
        global_optimal_sol = combine_regional_datasets_into_global_dataset(
            year, p, baseload_demand, path_config, force=force
        )
        # Plot global results
        if generate_plots and global_optimal_sol is not None:
            plot_global_optimum_baseload_power_simulation_map(global_optimal_sol, year, p, baseload_demand, path_config)

        end = time.time()
        logging.info(f"Total runtime: {(end - start) / 60} minutes")
        return global_optimal_sol

    else:
        logging.info(f"Running baseload optimization for {region} in {year}.")
        # Profiles for REs (backend selected by PROFILE_DATA_SOURCE; spatially-chunked Zarr is
        # dramatically faster for the per-point reads than scanning the regional NetCDF)
        profile = open_regional_dataset("profile", region, path_config)
        # Optimization
        regional_optimal_sol = execute_baseload_optimization_for_region(
            year,
            region,
            baseload_demand,
            p,
            profile,
            projected_cost_per_country,
            investment_horizon,
            n=n,
            path_config=path_config,
            n_workers=n_workers,
            force=force,
        )
        # Plot results for selected region
        if generate_plots:
            plot_regional_optimum_baseload_power_simulation_map(year, region, p, baseload_demand, path_config)

        end = time.time()
        logging.info(f"Total runtime: {(end - start) / 60} minutes")
        return regional_optimal_sol
