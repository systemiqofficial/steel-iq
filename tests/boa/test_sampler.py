"""Contracts for the symmetric design sampler (phase 2 of the sampler proposal).

Covers the mechanisms the v2 sampler introduced: the box-truncated top-up draw,
the corner screen, the query-time ceiling mask, baseload-independence of the
design cache, and the v1-cache rejection. Synthetic profiles throughout; the
heavyweight real-data parity suite lives in the atlas repo.
"""

from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from boa.model import design_cache
from boa.model.global_extension import _query_lcoe_tile
from boa.model.logic import (
    PointDesignState,
    _draw_truncated_overscale_samples,
    _state_from_draws,
    _truncated_exponential,
    compute_lcoe_from_state,
    corner_design_feasible,
    min_survivors_required,
    optimize_point,
    overscale_mus_from_cf,
    precompute_point_state,
    top_up_point_state,
    top_up_quality_threshold,
)

T = 24 * 21  # three synthetic weeks
P = 15
N = 400
SEED = 42
HORIZON = 3


def _synthetic_profiles(solar_amp: float = 0.9, wind_mean: float = 0.35, seed: int = 0):
    """Daily half-sine solar + noisy wind, both float64 p.u. capacity factors."""
    rng = np.random.RandomState(seed)
    hours = np.arange(T) % 24
    solar = solar_amp * np.clip(np.sin(np.pi * (hours - 6) / 12.0), 0.0, None)
    wind = np.clip(wind_mean + 0.15 * np.sin(2 * np.pi * np.arange(T) / (24 * 7)) + 0.1 * rng.randn(T), 0.0, 1.0)
    return solar.astype(np.float64), wind.astype(np.float64)


def _costs():
    capex = {
        "solar": np.full(HORIZON + 1, 800_000.0),
        "wind": np.full(HORIZON + 1, 1_200_000.0),
        "battery": np.full(HORIZON + 1, 300_000.0),
    }
    opex = {"solar": 0.02, "wind": 0.03, "battery": 0.01}
    return capex, opex, 0.08


def _best(designs: np.ndarray, coverage: np.ndarray, baseload: float):
    """Argmin coverage-based LCOE over pre-filtered designs; None if empty."""
    if designs.shape[0] == 0:
        return None
    state = PointDesignState(
        designs=np.asarray(designs, dtype=np.float64),
        coverage=np.asarray(coverage, dtype=np.float64),
        accepted_mask=None,
    )
    capex, opex, coc = _costs()
    lcoe = compute_lcoe_from_state(state, baseload, capex, opex, coc, HORIZON)
    j = int(np.argmin(lcoe["lcoes"]))
    return float(lcoe["lcoes"][j]), tuple(float(x) for x in state.designs[j])


# ----- truncated top-up draw --------------------------------------------------


def test_truncated_draws_stay_in_box_and_are_deterministic():
    mus = {"solar": 5.0, "wind": 2.0}
    limit = {"solar": 3.0, "wind": 0.5}
    C_w1, C_s1 = _draw_truncated_overscale_samples(N, mus, limit, SEED)
    C_w2, C_s2 = _draw_truncated_overscale_samples(N, mus, limit, SEED)
    assert np.array_equal(C_w1, C_w2) and np.array_equal(C_s1, C_s2)
    assert C_s1.max() <= limit["solar"] and C_w1.max() <= limit["wind"]
    assert C_s1.min() >= 0.0 and C_w1.min() >= 0.0


def test_truncated_draw_degrades_to_uniform_at_huge_mu():
    # mu/L -> infinity is the doldrums regime; the inverse-CDF must become U(0, L).
    u = np.linspace(0.0, 0.999, 100)
    limit = 8.0
    x = _truncated_exponential(u, mu=0.75e9, limit=limit)
    assert np.allclose(x, u * limit, rtol=1e-6)


# ----- corner screen ----------------------------------------------------------


def test_infeasible_corner_proves_the_whole_box_infeasible():
    solar, wind = _synthetic_profiles(solar_amp=0.3, wind_mean=0.1)
    limit = {"solar": 1.5, "wind": 1.0}  # too small to ever cover demand
    assert not corner_design_feasible(solar, wind, P, limit)

    # Brute force the box: corner infeasible + coverage monotonicity => nothing inside passes.
    gs = np.linspace(0.0, limit["solar"], 20)
    gw = np.linspace(0.0, limit["wind"], 20)
    C_s, C_w = (a.ravel() for a in np.meshgrid(gs, gw, indexing="ij"))
    state = _state_from_draws(solar, wind, P, C_s, C_w)
    assert state.accepted_mask is not None and not state.accepted_mask.any()

    # Positive control: a roomier box has a feasible corner.
    assert corner_design_feasible(solar, wind, P, {"solar": 30.0, "wind": 30.0})


def test_optimize_point_reports_no_optimum_when_corner_infeasible():
    solar, wind = _synthetic_profiles(solar_amp=0.3, wind_mean=0.1)
    limit = {"solar": 1.5, "wind": 1.0}
    mus = overscale_mus_from_cf(float(solar.mean()), float(wind.mean()))
    capex, opex, coc = _costs()
    optimum = optimize_point(
        {"solar": solar, "wind": wind},
        P,
        1000.0,
        capex,
        opex,
        coc,
        HORIZON,
        N,
        limit=limit,
        seed=SEED,
        mus=mus,
        min_survivors=min_survivors_required(N),
    )
    assert optimum is None


# ----- degenerate-CF pixel routes to top-up -----------------------------------


def test_degenerate_wind_cf_pixel_resolves_via_top_up():
    # Doldrums pixel: zero wind CF puts every base draw outside the wind ceiling; top-up must resolve it.
    solar, _ = _synthetic_profiles()
    wind = np.zeros(T)
    limit = {"solar": 60.0, "wind": 8.0}
    mus = overscale_mus_from_cf(float(solar.mean()), float(wind.mean()))
    base = precompute_point_state(solar, wind, P, N, SEED, mus=mus)
    inbox = (base.designs[:, 0] <= limit["solar"]) & (base.designs[:, 1] <= limit["wind"])
    assert base.accepted_mask is not None
    min_survivors = min_survivors_required(N)
    assert int((base.accepted_mask & inbox).sum()) < min_survivors  # genuinely starved

    capex, opex, coc = _costs()
    optimum = optimize_point(
        {"solar": solar, "wind": wind},
        P,
        1000.0,
        capex,
        opex,
        coc,
        HORIZON,
        N,
        limit=limit,
        seed=SEED,
        mus=mus,
        min_survivors=min_survivors,
    )
    assert optimum is not None  # usable optimum, not a false status 4
    assert optimum["design"]["solar"] > 0.0
    assert optimum["design"]["wind"] <= limit["wind"]


def test_sparse_in_box_pixel_gets_quality_top_up():
    # Marginal-wind pixel: enough masked survivors to dodge the adequacy cut, but a
    # sparse sample — the quality trigger must re-sample and can only improve the argmin.
    solar, _ = _synthetic_profiles()
    wind = np.full(T, 0.004)
    limit = {"solar": 60.0, "wind": 8.0}
    mus = overscale_mus_from_cf(float(solar.mean()), float(wind.mean()))
    base = precompute_point_state(solar, wind, P, N, SEED, mus=mus)
    inbox = (base.designs[:, 0] <= limit["solar"]) & (base.designs[:, 1] <= limit["wind"])
    assert base.accepted_mask is not None
    survivors = base.accepted_mask & inbox
    min_surv = min_survivors_required(N)
    assert min_surv <= int(survivors.sum()) < top_up_quality_threshold(N)  # sparse, not starved

    capex, opex, coc = _costs()
    masked_only = compute_lcoe_from_state(
        PointDesignState(designs=base.designs[survivors], coverage=base.coverage[survivors], accepted_mask=None),
        1000.0,
        capex,
        opex,
        coc,
        HORIZON,
    )["lcoes"].min()
    optimum, intermediates = optimize_point(
        {"solar": solar, "wind": wind},
        P,
        1000.0,
        capex,
        opex,
        coc,
        HORIZON,
        N,
        limit=limit,
        seed=SEED,
        mus=mus,
        return_intermediates=True,
        min_survivors=min_surv,
    )
    assert optimum is not None
    assert len(intermediates["coverages"]) == 2 * N  # quality top-up ran
    assert optimum["lcoe_coverage_based"] <= float(masked_only) + 1e-9


# ----- top-up determinism -----------------------------------------------------


def test_top_up_is_deterministic_and_threaded_matches_serial():
    solar, _ = _synthetic_profiles()
    wind = np.zeros(T)
    limit = {"solar": 60.0, "wind": 8.0}
    mus = overscale_mus_from_cf(float(solar.mean()), float(wind.mean()))
    capex, opex, coc = _costs()

    def run():
        return optimize_point(
            {"solar": solar, "wind": wind},
            P,
            1000.0,
            capex,
            opex,
            coc,
            HORIZON,
            N,
            limit=limit,
            seed=SEED,
            mus=mus,
            min_survivors=min_survivors_required(N),
        )

    serial = [run() for _ in range(2)]
    with ThreadPoolExecutor(max_workers=4) as ex:
        threaded = list(ex.map(lambda _: run(), range(4)))
    reference = serial[0]
    assert reference is not None
    for result in serial[1:] + threaded:
        assert result == reference

    st1 = top_up_point_state(solar, wind, P, N, SEED, mus, limit)
    st2 = top_up_point_state(solar, wind, P, N, SEED, mus, limit)
    assert np.array_equal(st1.designs, st2.designs)
    assert np.array_equal(st1.coverage, st2.coverage)


# ----- ceiling mask boundary --------------------------------------------------


def test_design_exactly_at_the_ceiling_stays_in_box():
    solar, wind = _synthetic_profiles()
    baseload = 1000.0
    pv_max = np.array([2000.0], dtype=np.float32)  # L_s = 2.0, exactly representable
    wind_max = np.array([3000.0], dtype=np.float32)  # L_w = 3.0
    # Design A sits exactly on the corner; design B is cheaper but out of box on wind.
    designs = np.array([[2.0, 3.0, 1.0], [0.1, 3.5, 0.5]], dtype=np.float32)
    coverage = np.array([0.95, 0.95], dtype=np.float32)
    offsets = np.array([0, 2], dtype=np.int32)
    capex, opex, coc = _costs()
    capex_per_tech = {tech: capex[tech][None, :] for tech in ("solar", "wind", "battery")}
    opex_per_tech = {tech: np.array([opex[tech]]) for tech in ("solar", "wind", "battery")}

    # n=4 keeps the quality threshold at 1, so no top-up competes with the cached pair.
    _, results, counters = _query_lcoe_tile(
        np.array([0]),
        designs,
        offsets,
        coverage,
        pv_max,
        wind_max,
        capex_per_tech,
        opex_per_tech,
        np.array([coc]),
        np.array(["TST"]),
        solar[None, :],
        wind[None, :],
        baseload,
        HORIZON,
        P,
        4,
        SEED,
        min_survivors=1,
    )
    assert counters["starved"] == 0 and counters["quality"] == 0
    assert results[0]["status"] == 1
    # The boundary design survives the <= mask; the cheaper out-of-box one is excluded.
    assert results[0]["design"]["solar"] == pytest.approx(2.0)
    assert results[0]["design"]["wind"] == pytest.approx(3.0)


# ----- cache: baseload independence + v1 rejection ----------------------------


def _write_cache(tmp_path, name: str, per_point_designs, per_point_coverage, lats, lons, pv_max, wind_max, meta=None):
    designs_flat, offsets, coverage_flat = design_cache.pack_csr(per_point_designs, per_point_coverage)
    npts = len(lats)
    cache = design_cache.RegionDesignCache(
        region="SYNTH",
        all_lats=np.unique(lats),
        all_lons=np.unique(lons),
        lats=lats,
        lons=lons,
        iy=np.zeros(npts, dtype=np.int32),
        ix=np.arange(npts, dtype=np.int32),
        pv_max=pv_max,
        wind_max=wind_max,
        designs_flat=designs_flat,
        design_offsets=offsets,
        coverage_flat=coverage_flat,
        meta=meta
        or design_cache.build_cache_meta(
            "SYNTH", npts, designs_flat.shape[0], P, N, SEED, 2024, 0.25, {"wind": 0.75, "solar": 0.75}
        ),
    )
    return design_cache.read_cache(design_cache.write_cache(cache, tmp_path / name))


def test_shared_cache_queried_at_two_baseloads_matches_dedicated_builds(tmp_path):
    # One baseload-free build + query masks must equal dedicated box-filtered builds per baseload.
    baseloads = (1000.0, 2500.0)
    points = [
        _synthetic_profiles(0.9, 0.35, seed=1),
        _synthetic_profiles(0.7, 0.45, seed=2),
        _synthetic_profiles(0.8, 0.25, seed=3),
    ]
    npts = len(points)
    lats = np.linspace(40.0, 42.0, npts)
    lons = np.linspace(5.0, 7.0, npts)
    pv_max = np.full(npts, 30_000.0, dtype=np.float32)
    wind_max = np.full(npts, 9_000.0, dtype=np.float32)

    states = []
    for solar, wind in points:
        mus = overscale_mus_from_cf(float(solar.mean()), float(wind.mean()))
        states.append(precompute_point_state(solar, wind, P, N, SEED, mus=mus))

    def per_point(box_baseload):
        designs, coverage = [], []
        for j, st in enumerate(states):
            assert st.accepted_mask is not None
            keep = st.accepted_mask.copy()
            if box_baseload is not None:
                keep &= (st.designs[:, 0] <= pv_max[j] / box_baseload) & (
                    st.designs[:, 1] <= wind_max[j] / box_baseload
                )
            designs.append(np.ascontiguousarray(st.designs[keep], dtype=np.float64))
            coverage.append(np.ascontiguousarray(st.coverage[keep], dtype=np.float64))
        return designs, coverage

    shared = _write_cache(tmp_path, "shared.zarr", *per_point(None), lats, lons, pv_max, wind_max)
    for baseload in baseloads:
        dedicated = _write_cache(
            tmp_path, f"dedicated_{baseload:g}.zarr", *per_point(baseload), lats, lons, pv_max, wind_max
        )
        for j in range(npts):
            L_s, L_w = pv_max[j] / baseload, wind_max[j] / baseload
            d, c = shared.designs_for_point(j)
            keep = (d[:, 0] <= L_s) & (d[:, 1] <= L_w)
            a = _best(d[keep], c[keep], baseload)
            b = _best(*dedicated.designs_for_point(j), baseload)
            assert (a is None) == (b is None)
            if a is not None and b is not None:
                assert a[1] == b[1]  # identical optimum design
                assert a[0] == b[0]  # identical LCOE


def test_read_cache_refuses_v1_schema(tmp_path):
    solar, wind = _synthetic_profiles()
    mus = overscale_mus_from_cf(float(solar.mean()), float(wind.mean()))
    st = precompute_point_state(solar, wind, P, N, SEED, mus=mus).filter_to_accepted()
    v1_meta = {"schema_version": 1, "region": "SYNTH", "params": {"baseload_demand_mw": 1000.0}}
    with pytest.raises(ValueError, match="schema version 1"):
        _write_cache(
            tmp_path,
            "v1.zarr",
            [st.designs],
            [st.coverage],
            np.array([40.0]),
            np.array([5.0]),
            np.array([30_000.0], dtype=np.float32),
            np.array([9_000.0], dtype=np.float32),
            meta=v1_meta,
        )
