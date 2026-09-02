"""Contracts for the per-baseload top-up supplement cache.

The supplement persists the query-time top-up results (truncated re-sample
designs + corner-screen verdicts) per (parent cache, baseload). Core contract:
a query replayed from the supplement is bit-identical to one computing the
top-up fresh. Synthetic profiles throughout, covering every verdict path:
healthy, starved-resolved, corner-infeasible, quality-sparse, zero-potential,
and cached-empty-with-potential pixels.
"""

from types import SimpleNamespace

import numpy as np
import pytest

import boa.model.global_extension as ge
from boa.config.settings import MIN_SURVIVOR_FRACTION, TOPUP_QUALITY_FRACTION
from boa.model import design_cache
from boa.model.global_extension import (
    _load_topup_supplement,
    _pack_topup_supplement,
    _query_lcoe_tile,
    _topup_tile,
)
from boa.model.logic import (
    min_survivors_required,
    overscale_mus_from_cf,
    precompute_point_state,
    top_up_quality_threshold,
)

T = 24 * 21  # three synthetic weeks
# `logic.py` and `design_cache` still speak the uncovered percentile; `global_extension`
# now takes the coverage fraction. Both are carried until M3 deletes the first two.
P = 15
COVERAGE = 0.85
N = 400
SEED = 42
HORIZON = 3
BASELOAD = 1000.0
NPTS = 6


def _synthetic_profiles(solar_amp: float = 0.9, wind_mean: float = 0.35, seed: int = 0):
    """Daily half-sine solar + noisy wind, both float64 p.u. capacity factors."""
    rng = np.random.RandomState(seed)
    hours = np.arange(T) % 24
    solar = solar_amp * np.clip(np.sin(np.pi * (hours - 6) / 12.0), 0.0, None)
    wind = np.clip(wind_mean + 0.15 * np.sin(2 * np.pi * np.arange(T) / (24 * 7)) + 0.1 * rng.randn(T), 0.0, 1.0)
    return solar.astype(np.float64), wind.astype(np.float64)


@pytest.fixture(scope="module")
def tile_env():
    """One six-pixel tile exercising every top-up verdict path in a single query."""
    solar_hi, wind_hi = _synthetic_profiles()
    weak = _synthetic_profiles(0.3, 0.1)
    profiles = [
        (solar_hi, wind_hi),  # 0: healthy, big box -> verdict 0
        (solar_hi, np.zeros(T)),  # 1: doldrums wind -> starved, top-up resolves
        weak,  # 2: tiny box -> starved, corner-infeasible
        (solar_hi, np.full(T, 0.004)),  # 3: marginal wind -> sparse, quality top-up
        (np.zeros(T), np.zeros(T)),  # 4: zero potential -> status 3, verdict 0
        weak,  # 5: cached-empty with potential, tiny box -> corner-infeasible, status 2
    ]
    pv_max = np.array([60_000, 60_000, 1_500, 60_000, 10_000, 1_500], dtype=np.float32)
    wind_max = np.array([30_000, 8_000, 1_000, 8_000, 10_000, 1_000], dtype=np.float32)

    per_point_d: list[np.ndarray] = []
    per_point_c: list[np.ndarray] = []
    for i, (solar, wind) in enumerate(profiles):
        if i in (4, 5):
            # 4 is skipped by the build phase; 5 deliberately has no cached designs.
            per_point_d.append(np.empty((0, 3)))
            per_point_c.append(np.empty(0))
            continue
        mus = overscale_mus_from_cf(float(solar.mean()), float(wind.mean()))
        st = precompute_point_state(solar, wind, P, N, SEED, mus=mus).filter_to_accepted()
        per_point_d.append(st.designs)
        per_point_c.append(st.coverage)
    designs_flat, offsets, coverage_flat = design_cache.pack_csr(per_point_d, per_point_c)

    # Pin the intended band composition so profile drift fails loudly here, not downstream.
    min_surv = min_survivors_required(N)
    quality_min = top_up_quality_threshold(N)
    for i, expected in [(0, "healthy"), (1, "starved"), (3, "sparse")]:
        d = per_point_d[i]
        inbox = (d[:, 0] <= pv_max[i] / BASELOAD) & (d[:, 1] <= wind_max[i] / BASELOAD)
        count = int(inbox.sum())
        if expected == "healthy":
            assert count >= quality_min
        elif expected == "starved":
            assert count < min_surv
        else:
            assert min_surv <= count < quality_min

    capex = {
        "solar": np.full(HORIZON + 1, 800_000.0),
        "wind": np.full(HORIZON + 1, 1_200_000.0),
        "battery": np.full(HORIZON + 1, 300_000.0),
    }
    opex = {"solar": 0.02, "wind": 0.03, "battery": 0.01}
    env = SimpleNamespace(
        designs_flat=designs_flat,
        offsets=offsets,
        coverage_flat=coverage_flat,
        pv_max=pv_max,
        wind_max=wind_max,
        solar_profiles=np.stack([s for s, _ in profiles]),
        wind_profiles=np.stack([w for _, w in profiles]),
        min_survivors=min_surv,
        parent_meta=design_cache.build_cache_meta(
            "SYNTH", NPTS, designs_flat.shape[0], P, N, SEED, 2024, 0.25, {"wind": 0.75, "solar": 0.75}
        ),
    )

    def run_tile(supplement=None):
        return _query_lcoe_tile(
            np.arange(NPTS),
            env.designs_flat,
            env.offsets,
            env.coverage_flat,
            env.pv_max,
            env.wind_max,
            {tech: np.tile(capex[tech], (NPTS, 1)) for tech in ("solar", "wind", "battery")},
            {tech: np.full(NPTS, opex[tech]) for tech in ("solar", "wind", "battery")},
            np.full(NPTS, 0.08),
            np.array(["TST"] * NPTS),
            env.solar_profiles,
            env.wind_profiles,
            BASELOAD,
            HORIZON,
            COVERAGE,
            N,
            SEED,
            min_survivors=min_surv,
            supplement=supplement,
        )

    env.run_tile = run_tile
    return env


def _round_trip_supplement(env, tmp_path):
    """Build the supplement from a fresh query and round-trip it through disk."""
    _, _, _, topup_out = env.run_tile()
    supplement = _pack_topup_supplement(NPTS, [np.arange(NPTS)], [topup_out], env.parent_meta, BASELOAD)
    path = design_cache.write_topup_supplement(supplement, tmp_path / "topup.zarr")
    return design_cache.read_topup_supplement(
        path, env.parent_meta, BASELOAD, TOPUP_QUALITY_FRACTION, MIN_SURVIVOR_FRACTION
    )


def _assert_results_identical(a: list[dict], b: list[dict]):
    """Exact (bit-identical) equality of per-pixel result dicts, NaN-tolerant."""
    assert len(a) == len(b)
    for ra, rb in zip(a, b):
        assert ra.keys() == rb.keys()
        for key in ra:
            va, vb = ra[key], rb[key]
            if isinstance(va, dict):
                assert va.keys() == vb.keys(), key
                for sub in va:
                    assert np.array_equal(va[sub], vb[sub], equal_nan=True), f"{key}.{sub}: {va[sub]} != {vb[sub]}"
            elif isinstance(va, float):
                assert np.array_equal(va, vb, equal_nan=True), f"{key}: {va} != {vb}"
            else:
                assert va == vb, f"{key}: {va} != {vb}"


# ----- round trip: replay == fresh compute ------------------------------------


def test_query_from_supplement_is_bit_identical_to_fresh_compute(tile_env, tmp_path):
    _, fresh_results, fresh_counters, topup_out = tile_env.run_tile()
    assert [v for v, _, _ in topup_out] == [0, 1, 2, 1, 0, 2]
    assert fresh_counters["starved"] == 3 and fresh_counters["quality"] == 1
    assert fresh_counters["corner_infeasible"] == 2 and fresh_counters["topped_up"] == 1
    assert fresh_results[0]["status"] == 1  # healthy
    assert fresh_results[1]["status"] == 1  # starved, resolved via top-up
    assert fresh_results[4]["status"] == 3  # zero potential
    assert fresh_results[5]["status"] == 2  # cached-empty, corner-proved infeasible

    supplement = _round_trip_supplement(tile_env, tmp_path)
    _, replay_results, replay_counters, replay_out = tile_env.run_tile(supplement=supplement)
    assert replay_out is None
    _assert_results_identical(replay_results, fresh_results)
    assert replay_counters["from_supplement"] == 4  # every trigger-band pixel served
    for key in ("starved", "corner_infeasible", "topped_up", "resolved", "quality"):
        assert replay_counters[key] == fresh_counters[key]


# ----- pure function: identical arrays on every build path --------------------


def test_supplement_build_is_pure_and_prebuild_matches_query_side_effect(tile_env):
    out_a = tile_env.run_tile()[3]
    out_b = tile_env.run_tile()[3]
    _, out_tile, _ = _topup_tile(
        np.arange(NPTS),
        tile_env.designs_flat,
        tile_env.offsets,
        tile_env.pv_max,
        tile_env.wind_max,
        tile_env.solar_profiles,
        tile_env.wind_profiles,
        BASELOAD,
        COVERAGE,
        N,
        SEED,
        tile_env.min_survivors,
    )
    for other in (out_b, out_tile):
        for (va, da, ca), (vb, db, cb) in zip(out_a, other):
            assert va == vb
            assert np.array_equal(da, db) and da.dtype == db.dtype == np.float64
            assert np.array_equal(ca, cb)

    packed_a = _pack_topup_supplement(NPTS, [np.arange(NPTS)], [out_a], tile_env.parent_meta, BASELOAD)
    packed_b = _pack_topup_supplement(NPTS, [np.arange(NPTS)], [out_tile], tile_env.parent_meta, BASELOAD)
    assert np.array_equal(packed_a.verdict, packed_b.verdict)
    assert np.array_equal(packed_a.row_offsets, packed_b.row_offsets)
    assert np.array_equal(packed_a.designs_flat, packed_b.designs_flat)
    assert np.array_equal(packed_a.coverage_flat, packed_b.coverage_flat)


# ----- validity: exact-match refusal, query degrades to rebuild ---------------


def test_supplement_refused_on_any_mismatch_and_loader_degrades_to_none(tile_env, tmp_path):
    _, _, _, topup_out = tile_env.run_tile()
    supplement = _pack_topup_supplement(NPTS, [np.arange(NPTS)], [topup_out], tile_env.parent_meta, BASELOAD)
    path = design_cache.write_topup_supplement(supplement, tmp_path / "topup.zarr")

    ok = design_cache.read_topup_supplement(
        path, tile_env.parent_meta, BASELOAD, TOPUP_QUALITY_FRACTION, MIN_SURVIVOR_FRACTION
    )
    assert ok.n_points == NPTS

    with pytest.raises(ValueError, match="topup_quality_fraction"):
        design_cache.read_topup_supplement(path, tile_env.parent_meta, BASELOAD, 0.10, MIN_SURVIVOR_FRACTION)
    with pytest.raises(ValueError, match="min_survivor_fraction"):
        design_cache.read_topup_supplement(path, tile_env.parent_meta, BASELOAD, TOPUP_QUALITY_FRACTION, 0.05)
    with pytest.raises(ValueError, match="baseload_demand_mw"):
        design_cache.read_topup_supplement(
            path, tile_env.parent_meta, 2500.0, TOPUP_QUALITY_FRACTION, MIN_SURVIVOR_FRACTION
        )
    rebuilt_parent = dict(tile_env.parent_meta, built_at="2099-01-01T00:00:00+00:00")
    with pytest.raises(ValueError, match="parent_built_at"):
        design_cache.read_topup_supplement(
            path, rebuilt_parent, BASELOAD, TOPUP_QUALITY_FRACTION, MIN_SURVIVOR_FRACTION
        )
    reparam_parent = dict(tile_env.parent_meta, params=dict(tile_env.parent_meta["params"], n_samples=2 * N))
    with pytest.raises(ValueError, match="parent_params"):
        design_cache.read_topup_supplement(
            path, reparam_parent, BASELOAD, TOPUP_QUALITY_FRACTION, MIN_SURVIVOR_FRACTION
        )
    with pytest.raises(FileNotFoundError):
        design_cache.read_topup_supplement(
            tmp_path / "missing.zarr", tile_env.parent_meta, BASELOAD, TOPUP_QUALITY_FRACTION, MIN_SURVIVOR_FRACTION
        )

    # The query-side loader turns every refusal into None (compute fresh + persist), never an error.
    assert _load_topup_supplement(tmp_path / "missing.zarr", tile_env.parent_meta, BASELOAD) is None
    assert _load_topup_supplement(path, rebuilt_parent, BASELOAD) is None
    assert _load_topup_supplement(path, tile_env.parent_meta, BASELOAD) is not None


# ----- verdict semantics: replay runs no screen and no top-up dispatch --------


def test_replay_serves_all_verdicts_without_any_dispatch_call(tile_env, tmp_path, monkeypatch):
    _, fresh_results, _, _ = tile_env.run_tile()
    supplement = _round_trip_supplement(tile_env, tmp_path)

    def _boom(*args, **kwargs):
        raise AssertionError("top-up dispatch ran despite a valid supplement")

    monkeypatch.setattr(ge, "corner_design_feasible", _boom)
    monkeypatch.setattr(ge, "top_up_point_state", _boom)
    _, replay_results, _, _ = tile_env.run_tile(supplement=supplement)
    assert [r["status"] for r in replay_results] == [r["status"] for r in fresh_results]
    assert replay_results[5]["status"] == 2  # corner verdict honoured without re-running the screen
