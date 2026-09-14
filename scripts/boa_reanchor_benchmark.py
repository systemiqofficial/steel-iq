"""
Re-anchoring benchmark (dev script, not production code).

Tests the proposal raised after the repair-rate benchmark: does periodically rebuilding
the frontier's patches against updated costs ("re-anchoring", e.g. every 5 years across a
25-year horizon) bound the excess-LCOE tail, since that tail was found to grow with how
far a query's real costs have drifted from the frontier's build-time anchor?

Method: reuses boa_repair_rate_benchmark's dense (s, w) -> (b_min, sf) ground-truth grid
(cost-independent, computed once per pixel) and argmin_lcoe (patches-only). Builds ONE
frontier per pixel at "year 0" anchor costs, then queries it at increasing amounts of
drift along a fixed cost trajectory (solar capex declining linearly from 1.0x to 0.35x
over a 25-year horizon -- the same endpoint as the repair-rate benchmark's most extreme
"solar_much_cheaper" scenario, which produced a 10.4% worst-case miss at full drift).

This directly answers the re-anchoring question without needing to build multiple
frontiers: re-anchoring every N years caps the worst-case drift any query ever
experiences at N/2 years (a query lands, worst case, at the midpoint between two
anchors). So reading this curve at drift = N/2 gives the worst-case miss a re-anchor
interval of N years would leave, and at drift = 25 gives today's single-anchor-for-the-
whole-horizon worst case, for direct comparison.

Run: python scripts/boa_reanchor_benchmark.py [--points 150] [--inputs cds-2024-lulc+excl]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from boa_repair_rate_benchmark import COVERAGE, dense_physics_grid, scenario_coeffs, true_lcoe_argmin  # noqa: E402
from boa_s3_ladder_benchmark import build_cost_coefficients, sample_points  # noqa: E402

from boa.config.paths import PathConfig  # noqa: E402
from boa.inputs.profiles import open_regional_dataset  # noqa: E402
from boa.model.bisection import STATUS_OK, SearchParams, argmin_lcoe, build_pixel_frontier  # noqa: E402

HORIZON_YEARS = 25.0
END_SOLAR_MULT = 0.35  # matches the repair-rate benchmark's "solar_much_cheaper" endpoint
DRIFT_YEARS = [0.0, 1.25, 2.5, 3.75, 5.0, 7.5, 10.0, 15.0, 20.0, 25.0]


def solar_mult_at(year: float) -> float:
    """Linear decline from 1.0x (year 0) to END_SOLAR_MULT (year HORIZON_YEARS)."""
    return 1.0 + (END_SOLAR_MULT - 1.0) * (year / HORIZON_YEARS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--points", type=int, default=150)
    parser.add_argument("--inputs", default="cds-2024-lulc+excl")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    path_config = PathConfig.from_auto_detect(input_set=args.inputs)
    anchor = build_cost_coefficients()  # "year 0"
    params = SearchParams()

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

            for drift in DRIFT_YEARS:
                coeffs = scenario_coeffs(anchor, solar_mult_at(drift), 1.0)
                optimum = argmin_lcoe(frontier, coeffs)
                true_lcoe = true_lcoe_argmin(s_vals, w_vals, b_grid, sf_grid, coeffs)
                if not np.isfinite(true_lcoe) or true_lcoe <= 0:
                    continue
                excess_pct = max(0.0, 100.0 * (optimum.lcoe / true_lcoe - 1.0))
                rows.append(
                    {
                        "region": region,
                        "drift_years": drift,
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

    print(f"{'drift (yrs)':>12} {'solar mult':>10} {'certified':>10} {'median':>9} {'p90':>9} {'max':>9}")
    for drift in DRIFT_YEARS:
        sub = [r for r in rows if r["drift_years"] == drift]
        cert_rate = 100.0 * sum(r["certified"] for r in sub) / len(sub)
        excess = np.array([r["excess_pct"] for r in sub])
        print(
            f"{drift:12.2f} {solar_mult_at(drift):10.3f} {cert_rate:9.1f}% "
            f"{np.median(excess):8.4f}% {np.percentile(excess, 90):8.4f}% {excess.max():8.4f}%"
        )

    print("\nRe-anchor interval -> worst-case drift ever seen -> implied worst-case miss (read off the table above):")
    for interval in (5.0, 10.0, 25.0):
        worst_drift = interval / 2.0 if interval < HORIZON_YEARS else HORIZON_YEARS
        nearest = min(DRIFT_YEARS, key=lambda d: abs(d - worst_drift))
        sub = [r for r in rows if r["drift_years"] == nearest]
        excess = np.array([r["excess_pct"] for r in sub])
        label = "single anchor, full horizon" if interval >= HORIZON_YEARS else f"re-anchor every {interval:g}y"
        print(
            f"  {label:32s} worst-case drift ~{worst_drift:.1f}y (nearest measured {nearest:.2f}y): max excess {excess.max():.4f}%"
        )


if __name__ == "__main__":
    main()
