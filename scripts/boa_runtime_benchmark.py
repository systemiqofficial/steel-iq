"""
D3 runtime benchmark: grid-bisection search versus the Monte Carlo sampler (dev script).

BOA_BISECTION_PLAN.md, "Load normalisation and reporting", D3. Times the two
implementations on the same pixels and the same profiles, each in its own worktree:

  new  `bisection.build_pixel_frontier`  + `bisection.argmin_lcoe`
  old  `logic.precompute_point_state`    + `logic.compute_lcoe_from_state`

Both split the same way -- an expensive per-pixel build that is cached, and a cheap
per-year query that reprices it -- so the two phases are timed and reported separately.
The build is what the design cache stores; the query is what a 36-year sweep repeats.

Scope: the new side is Grid 1 (the load-independent frontier) and its query. The Grid 2
constrained sweep is not included, so this is not yet the full shipped method.

Run the old side from the `boa-refactor` worktree rather than from a pre-M1 state of the
bisection branch. S4/S5 refactored `calculate_lcoe_of_re_installation_vectorised` onto
`lcoe_coefficients`, so the sampler as it stands in the bisection tree prices through new
code and is a hybrid that exists on no branch. Which side to time is auto-detected from
whichever module imports.

Both sides run **unconstrained**: no capacity ceiling is applied (`limit=None` on the old
side; Grid 1 is ceiling-free by construction). That is deliberate. `boa-refactor` predates
the availability layers and builds pure-geometry ceilings, so applying each side's own
ceiling would compare two different search problems and attribute the difference to the
search. Comparing the unconstrained search on both sides is the like-for-like measurement,
and it is also exactly the load-independent optimum D2 promotes.

Stores are addressed by absolute path (`--live-dir`), not through `PathConfig`, because the
two worktrees disagree about input-set naming (`cds-2024` vs `cds-2024-lulc+excl`) and the
benchmark must read one identical set of files from both.

Profiles are dumped to one `.npz` and both sides read that, rather than each opening the
stores itself. `boa-refactor`'s venv has no zarr engine installed, and the dump is the
honest fix rather than mutating a sibling worktree's environment to suit a benchmark: it
also makes the two sides provably identical in their inputs instead of merely nominally so.

Run:
    # once, from the bisection worktree (needs zarr):
    python scripts/boa_runtime_benchmark.py --points 200 --dump-profiles p.npz
    # then in each worktree, against that same dump:
    python scripts/boa_runtime_benchmark.py --profiles-file p.npz --out new.json
    python scripts/boa_runtime_benchmark.py --profiles-file p.npz --out old.json
    python scripts/boa_runtime_benchmark.py --compare new.json old.json
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import xarray as xr

DEFAULT_LIVE = Path.home() / ".steelo/boa/inputs/cds-2024-lulc+excl/cds-zarr"
COVERAGE = 0.85  # boa-run --coverage default
N_SAMPLES = 1000  # boa-run --samples default, i.e. what the MC side ships with
SEED = 42  # settings.RANDOM_SEED
WARMUP_PIXELS = 3  # numba compile on the new side, cache warmth on both

# Real EU + Schengen project figures, as used by the S3 ladder benchmark. Applied
# uniformly rather than per-country: this is a runtime measurement, and the operation
# count barely depends on price. They are not identical no-ops, though -- on the new side
# the anchor ratios place the seeds, so using the same numbers on both sides keeps the
# amount of work comparable as well as the inputs.
CAPEX_KW = {"solar": 1115.52, "wind": 1821.12}
CAPEX_KWH_BATTERY = 235.43
OPEX_PCT = {"solar": 0.01, "wind": 0.02, "battery": 0.02}
WACC = 0.0548


def detect_side() -> str:
    """Which implementation is importable here. The bisection module exists only on the new branch."""
    try:
        import boa.model.bisection  # noqa: F401
    except ImportError:
        return "old"
    return "new"


# ---- shared inputs -------------------------------------------------------------------


def region_stems(live_dir: Path) -> list[str]:
    return sorted(p.name.split("max_capacity_")[1].split("_2024")[0] for p in live_dir.glob("max_capacity_*.zarr"))


def choose_points(live_dir: Path, total: int, seed: int) -> list[dict]:
    """
    Pick `total` buildable cells, proportionally by region, deterministically.

    Buildable means a nonzero ceiling on at least one tech, which is the same screen the S3
    benchmark uses: the profile stores carry a nonzero wind CF over ocean, so an unscreened
    sample would time cells the model would never site on.
    """
    rng = np.random.default_rng(seed)
    valid: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for region in region_stems(live_dir):
        with xr.open_zarr(live_dir / f"max_capacity_{region}_2024_025_deg.zarr", consolidated=True) as ds:
            iy, ix = np.nonzero((ds["pv"].values > 0) | (ds["wind"].values > 0))
        valid[region] = (iy, ix)

    grand = sum(len(v[0]) for v in valid.values())
    points: list[dict] = []
    for region, (iy, ix) in valid.items():
        quota = min(max(1, round(total * len(iy) / grand)), len(iy))
        for k in rng.choice(len(iy), size=quota, replace=False):
            points.append({"region": region, "iy": int(iy[k]), "ix": int(ix[k])})
    return points[:total]


def load_profiles(live_dir: Path, points: list[dict]) -> list[tuple[np.ndarray, np.ndarray]]:
    """Materialise (solar, wind) hourly profiles per point, grouped by region to read each store once."""
    by_region: dict[str, list[int]] = {}
    for i, pt in enumerate(points):
        by_region.setdefault(pt["region"], []).append(i)

    out: list[tuple[np.ndarray, np.ndarray] | None] = [None] * len(points)
    for region, idxs in by_region.items():
        with xr.open_zarr(live_dir / f"pv_and_wind_potential_{region}_2024_025_deg.zarr", consolidated=True) as ds:
            iy = np.array([points[i]["iy"] for i in idxs])
            ix = np.array([points[i]["ix"] for i in idxs])
            # One vectorised pull beats len(idxs) separate lazy reads into the same chunks.
            solar = ds["solar"].values[:, iy, ix].astype(np.float64)
            wind = ds["wind"].values[:, iy, ix].astype(np.float64)
        for j, i in enumerate(idxs):
            out[i] = (np.ascontiguousarray(solar[:, j]), np.ascontiguousarray(wind[:, j]))
    return [o for o in out if o is not None]


def dump_profiles(path: Path, profiles: list[tuple[np.ndarray, np.ndarray]]) -> None:
    """Stack to (npoints, nhours) and save, so the side without a zarr engine can still read them."""
    np.savez_compressed(
        path,
        solar=np.stack([s for s, _ in profiles]),
        wind=np.stack([w for _, w in profiles]),
    )


def load_dumped_profiles(path: Path) -> list[tuple[np.ndarray, np.ndarray]]:
    with np.load(path) as z:
        solar, wind = z["solar"], z["wind"]
    return [(np.ascontiguousarray(solar[i]), np.ascontiguousarray(wind[i])) for i in range(solar.shape[0])]


def _discount() -> np.ndarray:
    from boa.config.settings import LIFETIMES

    horizon = max(LIFETIMES.values())
    return np.array([1.0 / (1.0 + WACC) ** t for t in range(horizon + 1)])


# ---- the two sides -------------------------------------------------------------------


def time_new(profiles: list[tuple[np.ndarray, np.ndarray]]) -> dict:
    from boa.config.constants import AVERAGE_IMPLIED_STORAGE, HOURS_IN_YEAR, KILO_TO_MEGA
    from boa.config.settings import LIFETIMES
    from boa.model.bisection import (
        GAMMA,
        STATUS_OK,
        CostCoefficients,
        SearchParams,
        argmin_lcoe,
        build_pixel_frontier,
    )

    horizon = max(LIFETIMES.values())
    discount = _discount()

    def w0(opex: float) -> float:
        return 1.0 + opex * discount[1 : horizon + 1].sum()

    coeffs = CostCoefficients(
        a_s=w0(OPEX_PCT["solar"]) * CAPEX_KW["solar"] * KILO_TO_MEGA,
        a_w=w0(OPEX_PCT["wind"]) * CAPEX_KW["wind"] * KILO_TO_MEGA,
        a_b=(AVERAGE_IMPLIED_STORAGE ** (1.0 - GAMMA)) * w0(OPEX_PCT["battery"]) * CAPEX_KWH_BATTERY * KILO_TO_MEGA,
        d0=HOURS_IN_YEAR * discount[1 : horizon + 1].sum(),
    )
    params = SearchParams()

    for solar, wind in profiles[:WARMUP_PIXELS]:
        f = build_pixel_frontier(solar, wind, COVERAGE, params, coeffs)
        if f.status == STATUS_OK and f.n_patches > 0:
            argmin_lcoe(f, coeffs)

    # The build runs on every pixel -- that is what a build pass does. The query runs only
    # where there is something to query: `argmin_lcoe` raises on a frontier with no patches,
    # and production would not call it there either. So the two phases are averaged over
    # different denominators, and `solved` records which.
    builds, queries, solved = [], [], 0
    for solar, wind in profiles:
        t0 = time.perf_counter()
        frontier = build_pixel_frontier(solar, wind, COVERAGE, params, coeffs)
        t1 = time.perf_counter()
        builds.append(t1 - t0)
        if frontier.status != STATUS_OK or frontier.n_patches == 0:
            continue
        t1 = time.perf_counter()
        opt = argmin_lcoe(frontier, coeffs)
        queries.append(time.perf_counter() - t1)
        solved += int(opt is not None and np.isfinite(getattr(opt, "lcoe", np.inf)))
    return {"build_s": builds, "query_s": queries, "solved": solved}


def time_old(profiles: list[tuple[np.ndarray, np.ndarray]]) -> dict:
    from boa.config.settings import LIFETIMES
    from boa.model.logic import compute_lcoe_from_state, overscale_mus_from_cf, precompute_point_state

    horizon = max(LIFETIMES.values())
    capex = {
        "solar": np.full(horizon + 1, CAPEX_KW["solar"]),
        "wind": np.full(horizon + 1, CAPEX_KW["wind"]),
        "battery": np.full(horizon + 1, CAPEX_KWH_BATTERY),
    }

    def once(solar: np.ndarray, wind: np.ndarray) -> tuple[float, float, bool]:
        mus = overscale_mus_from_cf(float(solar.mean()), float(wind.mean()))
        t0 = time.perf_counter()
        state = precompute_point_state(solar, wind, COVERAGE, N_SAMPLES, SEED, mus=mus)
        t1 = time.perf_counter()
        res = compute_lcoe_from_state(state, 1.0, capex, OPEX_PCT, WACC, horizon)
        t2 = time.perf_counter()
        # `compute_lcoe_from_state` prices every sampled design, feasible or not; a pixel is
        # solved only if some design that actually met coverage has a finite LCOE.
        lcoes = np.asarray(res["lcoes"], dtype=np.float64)
        accepted = state.accepted_mask
        ok = lcoes if accepted is None else lcoes[accepted]
        return t1 - t0, t2 - t1, bool(ok.size and np.isfinite(ok).any())

    for solar, wind in profiles[:WARMUP_PIXELS]:
        once(solar, wind)

    builds, queries, solved = [], [], 0
    for solar, wind in profiles:
        b, q, ok = once(solar, wind)
        builds.append(b)
        queries.append(q)
        solved += int(ok)
    return {"build_s": builds, "query_s": queries, "solved": solved}


# ---- reporting -----------------------------------------------------------------------


def summarise(raw: dict, side: str, n_points: int) -> dict:
    b = np.array(raw["build_s"]) * 1e3
    q = np.array(raw["query_s"]) * 1e3
    if q.size == 0:
        raise SystemExit(f"{side}: no pixel produced a queryable result; nothing to time")
    return {
        "side": side,
        "points": n_points,
        "queried": int(q.size),
        "solved": raw["solved"],
        "build_ms_median": float(np.median(b)),
        "build_ms_mean": float(b.mean()),
        "build_ms_p95": float(np.percentile(b, 95)),
        "query_ms_median": float(np.median(q)),
        "query_ms_mean": float(q.mean()),
        "total_build_s": float(b.sum() / 1e3),
    }


def print_summary(s: dict) -> None:
    print(f"\n  side {s['side']}   {s['points']} pixels built   {s['queried']} queried   {s['solved']} solved")
    print(
        f"    build  median {s['build_ms_median']:8.2f} ms   mean {s['build_ms_mean']:8.2f}   p95 {s['build_ms_p95']:8.2f}"
    )
    print(f"    query  median {s['query_ms_median']:8.3f} ms   mean {s['query_ms_mean']:8.3f}")


def compare(a: dict, b: dict) -> None:
    new, old = (a, b) if a["side"] == "new" else (b, a)
    print(f"\n{'=' * 72}\nD3 runtime: grid bisection vs Monte Carlo sampler\n{'=' * 72}")
    for s in (old, new):
        print_summary(s)
    print(f"\n{'phase':<10}{'old ms':>12}{'new ms':>12}{'speedup':>12}")
    for phase in ("build", "query"):
        o, n = old[f"{phase}_ms_median"], new[f"{phase}_ms_median"]
        print(f"{phase:<10}{o:>12.3f}{n:>12.3f}{o / n:>11.2f}x")
    print(
        f"\n  A 36-year sweep costs one build plus 36 queries per pixel:\n"
        f"    old {old['build_ms_median'] + 36 * old['query_ms_median']:.1f} ms/pixel"
        f"    new {new['build_ms_median'] + 36 * new['query_ms_median']:.1f} ms/pixel"
    )
    if old["solved"] != new["solved"]:
        print(
            f"\n  NOTE: solved counts differ ({old['solved']} old vs {new['solved']} new); runtime is not like-for-like on the unsolved ones."
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--points", type=int, default=200)
    ap.add_argument("--live-dir", type=Path, default=DEFAULT_LIVE)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--compare", nargs=2, type=Path, metavar=("A.json", "B.json"))
    ap.add_argument("--dump-profiles", type=Path, help="read the stores, save profiles, and exit")
    ap.add_argument("--profiles-file", type=Path, help="time against a dump instead of the stores")
    args = ap.parse_args()

    if args.compare:
        compare(*(json.loads(p.read_text()) for p in args.compare))
        return 0

    if args.profiles_file:
        profiles = load_dumped_profiles(args.profiles_file)
        print(f"loaded {len(profiles)} profiles from {args.profiles_file}")
    else:
        points = choose_points(args.live_dir, args.points, SEED)
        print(f"selected {len(points)} buildable cells; loading profiles...")
        profiles = load_profiles(args.live_dir, points)
        if args.dump_profiles:
            dump_profiles(args.dump_profiles, profiles)
            print(f"wrote {len(profiles)} profiles to {args.dump_profiles}")
            return 0

    side = detect_side()
    print(f"side: {side}   {len(profiles)} profiles of {len(profiles[0][0])} hours; timing...")

    raw = time_new(profiles) if side == "new" else time_old(profiles)
    summary = summarise(raw, side, len(profiles))
    print_summary(summary)
    if args.out:
        args.out.write_text(json.dumps(summary, indent=2))
        print(f"\n  wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
