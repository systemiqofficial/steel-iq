"""
Repair-rate benchmark (dev script, not production code).

Measures two things across real global sites: how often argmin_lcoe's containment
certificate fails to fire, and -- when it does -- how much LCOE is actually left on the
table by trusting the patches-only answer instead of searching the whole physics box.

Method: build each pixel's frontier once with an "anchor" cost set (build_pixel_frontier
already needs one for seed placement). Separately compute a dense, cost-independent
(s, w) -> (b_min, served_fraction) grid over the WHOLE box at much finer resolution than
the coarse/patch grids (60x60 here) -- this is the ground truth. For a handful of "query"
cost scenarios representing plausible cost drift away from the anchor (solar getting
cheaper, wind getting cheaper, or unchanged), cheaply re-rank both the frontier's cached
patches (via argmin_lcoe) and the dense ground-truth grid, and compare.

Battery axis is deliberately not re-litigated here: the dense grid uses b_min directly
at every (s, w), since S3 already measured that axis's effect at under 1% of LCOE --
isolating this benchmark to the (s, w) location question the containment certificate
guards.

Run: python scripts/boa_repair_rate_benchmark.py [--points 200] [--inputs cds-2024-lulc+excl]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from boa_s3_ladder_benchmark import build_cost_coefficients, sample_points  # noqa: E402

from boa.config.paths import PathConfig  # noqa: E402
from boa.inputs.profiles import open_regional_dataset  # noqa: E402
from boa.model.bisection import (  # noqa: E402
    GAMMA,
    STATUS_OK,
    CostCoefficients,
    SearchParams,
    argmin_lcoe,
    b_min_at,
    build_pixel_frontier,
)

DENSE_N = 60
COVERAGE = 0.85


def dense_physics_grid(
    solar: np.ndarray, wind: np.ndarray, coverage: float, params: SearchParams, n: int = DENSE_N
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Cost-independent (s, w) -> (b_min, served_fraction) over the whole physics box,
    at `n x n` resolution -- much finer than the coarse (25x25) or patch (15x15) grids.
    Ground truth for "how much would a full-box search have found beyond the patches."
    """
    from boa.model.bisection import search_box

    s_max, w_max = search_box(solar, wind, params)
    s_vals = np.linspace(0.0, s_max, n)
    w_vals = np.linspace(0.0, w_max, n)
    b_grid = np.full((n, n), np.inf)
    sf_grid = np.zeros((n, n))
    hint = -1.0
    for i, s in enumerate(s_vals):
        row_hint = hint
        for j, w in enumerate(w_vals):
            b_min, _cov, sf = b_min_at(solar, wind, float(s), float(w), coverage, params, row_hint)
            b_grid[i, j] = b_min
            sf_grid[i, j] = sf
            if np.isfinite(b_min) and b_min > 0.0:
                row_hint = b_min
                if j == 0:
                    hint = b_min
    return s_vals, w_vals, b_grid, sf_grid


def true_lcoe_argmin(
    s_vals: np.ndarray, w_vals: np.ndarray, b_grid: np.ndarray, sf_grid: np.ndarray, coeffs: CostCoefficients
) -> float:
    with np.errstate(divide="ignore", invalid="ignore"):
        lcoe = (coeffs.a_s * s_vals[:, None] + coeffs.a_w * w_vals[None, :] + coeffs.a_b * np.power(b_grid, GAMMA)) / (
            coeffs.d0 * sf_grid
        )
    lcoe = np.where(np.isfinite(b_grid) & (sf_grid > 0), lcoe, np.inf)
    return float(np.min(lcoe))


def scenario_coeffs(anchor: CostCoefficients, a_s_mult: float, a_w_mult: float) -> CostCoefficients:
    """A plausible cost-drift scenario: anchor ratios scaled on the solar/wind axes only
    (battery and the energy denominator held fixed), representing one technology's capex
    falling relative to the other -- not sourced from real projections, illustrative only."""
    return CostCoefficients(a_s=anchor.a_s * a_s_mult, a_w=anchor.a_w * a_w_mult, a_b=anchor.a_b, d0=anchor.d0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--points", type=int, default=200)
    parser.add_argument("--inputs", default="cds-2024-lulc+excl")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    path_config = PathConfig.from_auto_detect(input_set=args.inputs)
    anchor = build_cost_coefficients()
    params = SearchParams()

    scenarios = {
        "same": scenario_coeffs(anchor, 1.0, 1.0),
        "solar_cheaper": scenario_coeffs(anchor, 0.6, 1.0),
        "wind_cheaper": scenario_coeffs(anchor, 1.0, 0.6),
        "solar_much_cheaper": scenario_coeffs(anchor, 0.35, 1.0),
    }

    points = sample_points(path_config, args.points, args.seed)
    print(f"Sampled {len(points)} points from {len(set(p[0] for p in points))} regions")

    points_by_region: dict[str, list] = {}
    for pt in points:
        points_by_region.setdefault(pt[0], []).append(pt)

    rows: list[dict] = []
    n_skipped = 0
    t0 = time.time()
    n_done = 0
    for region, region_points in points_by_region.items():
        profile = open_regional_dataset("profile", region, path_config)
        for _, iy, ix, lat, lon in region_points:
            solar = np.ascontiguousarray(profile["solar"].isel(y=iy, x=ix).values, dtype=np.float64)
            wind = np.ascontiguousarray(profile["wind"].isel(y=iy, x=ix).values, dtype=np.float64)

            frontier = build_pixel_frontier(solar, wind, COVERAGE, params, anchor)
            if frontier.status != STATUS_OK:
                n_skipped += 1
                continue

            s_vals, w_vals, b_grid, sf_grid = dense_physics_grid(solar, wind, COVERAGE, params)

            for name, coeffs in scenarios.items():
                optimum = argmin_lcoe(frontier, coeffs)
                true_lcoe = true_lcoe_argmin(s_vals, w_vals, b_grid, sf_grid, coeffs)
                if not np.isfinite(true_lcoe) or true_lcoe <= 0:
                    continue
                # The dense grid can occasionally be coarser than the patch grid near a
                # sharp optimum; clip to 0 rather than report a spurious negative excess.
                excess_pct = max(0.0, 100.0 * (optimum.lcoe / true_lcoe - 1.0))
                rows.append(
                    {
                        "region": region,
                        "scenario": name,
                        "certified": optimum.patch_certified,
                        "excess_pct": excess_pct,
                    }
                )
            n_done += 1
            if n_done % 25 == 0:
                elapsed = time.time() - t0
                print(f"  {n_done}/{len(points)} points, {elapsed:.0f}s elapsed, {elapsed / n_done:.2f} s/point")
        profile.close()

    print(f"\nDone: {n_done} points ({n_skipped} skipped, no optimum), {time.time() - t0:.0f}s total\n")

    for name in scenarios:
        sub = [r for r in rows if r["scenario"] == name]
        cert_rate = 100.0 * sum(r["certified"] for r in sub) / len(sub)
        excess = np.array([r["excess_pct"] for r in sub])
        excess_uncert = np.array([r["excess_pct"] for r in sub if not r["certified"]])
        print(f"--- scenario: {name} ({len(sub)} points) ---")
        print(f"  certified: {cert_rate:.1f}%  (repair would trigger on {100 - cert_rate:.1f}%)")
        print(
            f"  excess LCOE, all points:        median={np.median(excess):.4f}%  p90={np.percentile(excess, 90):.4f}%  max={excess.max():.4f}%"
        )
        if len(excess_uncert):
            print(
                f"  excess LCOE, uncertified only:  median={np.median(excess_uncert):.4f}%  p90={np.percentile(excess_uncert, 90):.4f}%  max={excess_uncert.max():.4f}%"
            )
        else:
            print("  excess LCOE, uncertified only:  n/a (nothing uncertified)")
        print()


if __name__ == "__main__":
    main()
