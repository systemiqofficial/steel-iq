"""
Frontier-cache selection analysis: which rung and which patch actually wins the argmin.

`argmin_lcoe` (boa.model.bisection) searches every populated patch slot and every battery
rung per pixel, but the winning rung index never leaves the function -- `Optimum` carries
the chosen `battery` value, not which of the `ladder_rungs` rungs it came from -- and
`patch_index` is reported but not broken down across a whole region. This script
replicates `argmin_lcoe`'s per-pixel loop (same formula, same tie-break: first `<` win)
to recover both, then answers two selection-structure questions a whole region's worth of
solved pixels can be checked against:

  rungs  For pixels where the global argmin's battery sits on a non-baseline rung
         (rung != 0), does restricting the search to rung 0 only change *which cell*
         (patch, i, j) wins, or does the same cell win with its own rung-0 battery?
         Distinguishes "using the base rung here loses a little accuracy at the same
         site" from "using the base rung sends the optimum to a different site
         entirely" -- the latter is a bigger deal for anyone considering trimming
         `SearchParams.ladder_rungs`.

  patches How often does the winning patch differ from slot 0 (`patch_index != 0`)?
          Slot 0 is ordinarily the anchor's own favourite; `argmin_lcoe`'s docstring
          notes a year's real prices can promote a basin an anchor only ranked second.
          This quantifies how often that actually happens.

  bound   How often does the winner sit on an *interior* patch edge -- not a corner
          solution (s=0 or w=0) and not the outer coarse-grid ring (which build-time
          box widening already had a chance to fix, see `max_box_widenings`)? This is
          exactly what `argmin_lcoe` itself reports as `Optimum.argmin_truncated`
          (computed by `_argmin_truncated`, reused here rather than reimplemented) --
          production already logs this rate per (region, year) as the `[certificate]`
          log line's "truncated against a patch edge" figure, but nothing currently
          *acts* on it: a truncated winner is reported as-is, the patch is not
          widened or re-searched at query time (patches are frozen at build time;
          only the coarse search box gets widened, and only during build, only when
          a *seed* -- not an arbitrary year's winner -- lands on the outer ring).

Reads the real frontier cache + real per-year costs via `PathConfig.from_auto_detect` --
no synthetic data, no separate worktree needed (unlike boa_runtime_benchmark.py, this
only concerns this branch's own search structure, not an old-vs-new comparison).

Run:
    python scripts/boa_frontier_selection_analysis.py --region INDO_AUS --years 2025 2030
    python scripts/boa_frontier_selection_analysis.py --region NORTH_ASIA --years 2025 2060 \
        --input-set cds-2024-lulc+excl --cost-set default --run cds-2024-lulc+excl__default
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field

import numpy as np

from boa.cli.run_simulation import _validate_cost_set
from boa.config.paths import PathConfig
from boa.inputs.profiles import detect_weather_year
from boa.model.bisection import GAMMA, STATUS_OK, SearchParams, _argmin_truncated
from boa.model.cost_calculations import lcoe_coefficients
from boa.model.frontier_cache import frontier_at, frontier_cache_path, read_frontier_cache
from boa.model.global_extension import _derive_cost_arrays

DEFAULT_BASELOAD = 1000.0
DEFAULT_COVERAGE = 0.85


@dataclass
class PixelSelection:
    patch_index: int
    rung_index: int
    lcoe: float


@dataclass
class RegionYearResult:
    region: str
    year: int
    n_solved: int = 0
    rung_counts: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int64))
    patch0_wins: int = 0
    patch_other_wins: int = 0
    # Of the pixels whose global winner sits on a non-baseline rung: how many, when
    # restricted to rung 0 only, still pick the *same* (patch, i, j) cell vs a different one.
    nonbase_same_cell: int = 0
    nonbase_diff_cell: int = 0
    # Winner sits on an interior patch edge (excludes s=0/w=0 corners and the outer
    # coarse-grid ring) -- same definition as `Optimum.argmin_truncated`.
    n_truncated: int = 0


def _argmin_over_rung(frontier, coeffs, rung: int | None) -> tuple[int, int, int, int, float] | None:
    """
    Replicates `argmin_lcoe`'s per-pixel argmin. `rung=None` searches every rung (matches
    production exactly); `rung=r` restricts every patch to that one rung index, for the
    "what if the ladder were shorter" comparison. Returns (k, i, j, r, lcoe) or None.
    """
    a_s, a_w, a_b, d0 = coeffs.a_s, coeffs.a_w, coeffs.a_b, coeffs.d0
    best_lcoe = np.inf
    best = None
    for k in range(frontier.n_patches):
        n_s, n_w = (int(x) for x in frontier.patch_points[k])
        if n_s == 0 or n_w == 0:
            continue
        s_vals = frontier.s_patch[k, :n_s].astype(np.float64)
        w_vals = frontier.w_patch[k, :n_w].astype(np.float64)
        b = frontier.b_patch[k, :n_s, :n_w].astype(np.float64)
        sf = frontier.energy_served_frac[k, :n_s, :n_w]
        if rung is not None:
            b = b[:, :, rung : rung + 1]
            sf = sf[:, :, rung : rung + 1]
        with np.errstate(divide="ignore", invalid="ignore"):
            lcoe = (a_s * s_vals[:, None, None] + a_w * w_vals[None, :, None] + a_b * np.power(b, GAMMA)) / (d0 * sf)
        lcoe = np.where(np.isfinite(b) & (sf > 0), lcoe, np.inf)
        i, j, r_local = (int(x) for x in np.unravel_index(int(np.argmin(lcoe)), lcoe.shape))
        val = float(lcoe[i, j, r_local])
        if val < best_lcoe:
            best_lcoe = val
            r_actual = rung if rung is not None else r_local
            best = (k, i, j, r_actual, val)
    return best


def analyze(region: str, year: int, path_config: PathConfig, baseload: float, coverage: float) -> RegionYearResult:
    params = SearchParams()
    weather_year = detect_weather_year(path_config)
    cache_dir = path_config.frontier_cache_dir(weather_year)
    from boa.config.settings import ERA5_DATA_RESOLUTION

    cache_file = frontier_cache_path(cache_dir, region, coverage, params, weather_year, ERA5_DATA_RESOLUTION)
    cache = read_frontier_cache(cache_file, params, coverage, weather_year)
    npts = cache.n_points

    costs, horizon = _validate_cost_set(path_config, year)
    usable = cache.status == STATUS_OK
    _, capex_per_tech, opex_per_tech, coc_arr = _derive_cost_arrays(cache.lats, cache.lons, usable, costs, path_config)

    result = RegionYearResult(region=region, year=year, rung_counts=np.zeros(params.ladder_rungs, dtype=np.int64))

    for k in range(npts):
        if int(cache.status[k]) != STATUS_OK:
            continue
        capex_k = {tech: capex_per_tech[tech][k] for tech in ("solar", "wind", "battery")}
        opex_k = {tech: float(opex_per_tech[tech][k]) for tech in ("solar", "wind", "battery")}
        coeffs = lcoe_coefficients(horizon, capex_k, opex_k, float(coc_arr[k]), baseload)
        frontier = frontier_at(cache, k)

        winner = _argmin_over_rung(frontier, coeffs, rung=None)
        if winner is None:
            continue
        pk, pi, pj, pr, _ = winner

        result.n_solved += 1
        result.rung_counts[pr] += 1
        if _argmin_truncated(frontier, pk, pi, pj):
            result.n_truncated += 1
        if pk == 0:
            result.patch0_wins += 1
        else:
            result.patch_other_wins += 1

        if pr != 0:
            base_only = _argmin_over_rung(frontier, coeffs, rung=0)
            assert base_only is not None, "rung 0 must be populated wherever any rung is"
            bk, bi, bj, _, _ = base_only
            if (bk, bi, bj) == (pk, pi, pj):
                result.nonbase_same_cell += 1
            else:
                result.nonbase_diff_cell += 1

    return result


def print_result(r: RegionYearResult) -> None:
    print(f"\n=== {r.region} {r.year}  (n_solved={r.n_solved}) ===")
    rung_pct = 100 * r.rung_counts / max(r.n_solved, 1)
    print("  rung distribution: " + ", ".join(f"rung{i}={p:.2f}%" for i, p in enumerate(rung_pct)))
    print(
        f"  patch distribution: patch0={100 * r.patch0_wins / max(r.n_solved, 1):.2f}%  "
        f"other_patch={100 * r.patch_other_wins / max(r.n_solved, 1):.2f}%"
    )
    print(
        f"  truncated against an interior patch edge: {100 * r.n_truncated / max(r.n_solved, 1):.2f}% ({r.n_truncated} pixels)"
    )
    n_nonbase = r.nonbase_same_cell + r.nonbase_diff_cell
    if n_nonbase == 0:
        print("  no non-baseline-rung winners this year -- rung-restriction question is moot")
    else:
        print(
            f"  of {n_nonbase} pixels whose global winner used a non-baseline rung: "
            f"{r.nonbase_same_cell} ({100 * r.nonbase_same_cell / n_nonbase:.1f}%) keep the "
            f"SAME cell restricted to rung 0; {r.nonbase_diff_cell} "
            f"({100 * r.nonbase_diff_cell / n_nonbase:.1f}%) move to a DIFFERENT cell"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", required=True)
    ap.add_argument("--years", type=int, nargs="+", required=True)
    ap.add_argument("--input-set", default="cds-2024-lulc+excl")
    ap.add_argument("--cost-set", default="default")
    ap.add_argument("--run", default=None, help="defaults to <input-set>__<cost-set>")
    ap.add_argument("--baseload", type=float, default=DEFAULT_BASELOAD)
    ap.add_argument("--coverage", type=float, default=DEFAULT_COVERAGE)
    args = ap.parse_args()

    path_config = PathConfig.from_auto_detect(input_set=args.input_set, cost_set=args.cost_set, run=args.run)
    for year in args.years:
        result = analyze(args.region, year, path_config, args.baseload, args.coverage)
        print_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
