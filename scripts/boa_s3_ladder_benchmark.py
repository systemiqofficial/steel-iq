"""
S3 ladder benchmark (dev script, not production code).

BOA_BISECTION_PLAN.md, "S3. Ladder benchmark": on real sites, find the
current-production-equivalent optimal (s, w) via the existing S2 grid search
(`bisection.build_pixel_frontier`, which resolves `b_min` but has no battery
ladder yet), then compute LCOE(b) densely at that (s, w) to locate the true
argmin, and check whether a 5-rung ladder spanning 2.5x b_min would have
found it. Per the plan this is a STOP FOR REVIEW step -- it does not
implement `battery_ladder`.

Cost coefficients are the real EU + Schengen project figures already cited
in the plan's "S2 measurements" (solar 1115.52, wind 1821.12 USD/kW, battery
235.43 USD/kWh, opex 1/2/2%, WACC 0.0548), applied uniformly to every point
in this benchmark rather than per-country. That is a deliberate simplification:
this is a physics benchmark (how far above b_min the LCOE argmin sits), not a
cost-accurate global run, and `lcoe_coefficients` (S4) -- which would derive
real per-region coefficients -- is not implemented yet.

Points are sampled from cells where the just-rebuilt max-capacity ceiling is
nonzero for at least one tech (pv_max > 0 or wind_max > 0), i.e. sites the
current LULC + cds_exclusion layers would actually let BOA build on -- not
raw ocean cells, which the profile stores also carry a (nonzero) wind CF for.
Quotas are proportional to each region's count of such cells, so a small
resource-rich region does not get the same weight as a large one. Region
bounding boxes overlap slightly at their edges (REGION_COORDS), so a handful
of points may double-count a border cell across two regions -- not corrected
for, immaterial at this sample size.

Run: python scripts/boa_s3_ladder_benchmark.py [--points 1000] [--inputs cds-2024-lulc+excl]
"""

from __future__ import annotations

import argparse
import csv
import tempfile
import time
from pathlib import Path

import numpy as np

from boa.config.constants import AVERAGE_IMPLIED_STORAGE, HOURS_IN_YEAR, KILO_TO_MEGA
from boa.config.paths import PathConfig
from boa.config.settings import LIFETIMES, RANDOM_SEED, REGION_COORDS
from boa.inputs.profiles import open_regional_dataset
from boa.model.bisection import (
    GAMMA,
    STATUS_OK,
    CostCoefficients,
    SearchParams,
    build_pixel_frontier,
    dispatch_metrics,
)

LADDER_RUNGS = 5
LADDER_SPAN = 2.5
DENSE_SWEEP_POINTS = 401
DENSE_SWEEP_MAX_WIDENINGS = 4


def _tech_weight_w0(discount: np.ndarray, opex_pct: float, lifetime: int, horizon: int) -> float:
    """CAPEX+OPEX weight on year-0 capex, for a tech whose lifetime == horizon (wH == 0).

    Full closed form is `cost_calculations._lcoe_tech_weights`; inlined here rather than
    importing that private helper, and simplified to the wH == 0 case since every LIFETIMES
    entry equals the investment horizon today (asserted, not assumed).
    """
    if lifetime != horizon:
        raise ValueError(f"expected lifetime == horizon (wH == 0 assumption); got {lifetime} != {horizon}")
    return 1.0 + opex_pct * discount[1 : lifetime + 1].sum()


def build_cost_coefficients() -> CostCoefficients:
    horizon = max(LIFETIMES.values())
    capex_kw = {"solar": 1115.52, "wind": 1821.12}
    capex_kwh_battery = 235.43
    opex_pct = {"solar": 0.01, "wind": 0.02, "battery": 0.02}
    wacc = 0.0548
    discount = np.array([1.0 / (1.0 + wacc) ** t for t in range(horizon + 1)])

    w0_solar = _tech_weight_w0(discount, opex_pct["solar"], LIFETIMES["solar"], horizon)
    w0_wind = _tech_weight_w0(discount, opex_pct["wind"], LIFETIMES["wind"], horizon)
    w0_battery = _tech_weight_w0(discount, opex_pct["battery"], LIFETIMES["battery"], horizon)

    # Baseload is normalised to 1 throughout: LCOE is exactly baseload-invariant (bisection.py's
    # own docstring), so the coefficients below serve every baseload unchanged.
    a_s = w0_solar * capex_kw["solar"] * KILO_TO_MEGA
    a_w = w0_wind * capex_kw["wind"] * KILO_TO_MEGA
    a_b = (AVERAGE_IMPLIED_STORAGE ** (1.0 - GAMMA)) * w0_battery * capex_kwh_battery * KILO_TO_MEGA
    d0 = HOURS_IN_YEAR * discount[1 : horizon + 1].sum()
    return CostCoefficients(a_s=a_s, a_w=a_w, a_b=a_b, d0=d0)


def _region_quotas(regions: list[str], sizes: dict[str, int], total: int) -> dict[str, int]:
    grand = sum(sizes.values())
    quotas = {r: max(1, round(total * sizes[r] / grand)) for r in regions if sizes[r] > 0}
    biggest = max(quotas, key=lambda r: sizes[r])
    quotas[biggest] += total - sum(quotas.values())
    return quotas


def sample_points(path_config: PathConfig, total_points: int, seed: int) -> list[tuple[str, int, int, float, float]]:
    """Rejection-sample `total_points` (region, iy, ix, lat, lon) cells, proportionally by
    region, from cells with a nonzero installable ceiling on at least one tech."""
    regions = list(REGION_COORDS.keys())
    rng = np.random.default_rng(seed)
    sizes: dict[str, int] = {}
    valid_by_region: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = {}
    for region in regions:
        ds = open_regional_dataset("max_cap", region, path_config)
        valid = (ds["pv"].values > 0) | (ds["wind"].values > 0)
        iy_all, ix_all = np.nonzero(valid)
        valid_by_region[region] = (iy_all, ix_all, ds["y"].values, ds["x"].values)
        sizes[region] = len(iy_all)
        ds.close()

    quotas = _region_quotas(regions, sizes, total_points)
    points: list[tuple[str, int, int, float, float]] = []
    for region, quota in quotas.items():
        iy_all, ix_all, lats, lons = valid_by_region[region]
        quota = min(quota, len(iy_all))
        chosen = rng.choice(len(iy_all), size=quota, replace=False)
        for k in chosen:
            iy, ix = int(iy_all[k]), int(ix_all[k])
            points.append((region, iy, ix, float(lats[iy]), float(lons[ix])))
    return points


def _argmin_over_frontier(frontier, a_s: float, a_w: float, a_b: float, d0: float):
    """Cheapest patch node across every seed's patch, using the rung-0 (== b_min today) LCOE.
    Returns (s_star, w_star, b_min_star, sf_star, lcoe_star), or None if no patch node is finite."""
    best = None
    for slot in range(frontier.n_patches):
        s_vals = frontier.s_patch[slot].astype(np.float64)
        w_vals = frontier.w_patch[slot].astype(np.float64)
        b = frontier.b_patch[slot, :, :, 0].astype(np.float64)
        sf = frontier.sf_patch[slot, :, :, 0]
        with np.errstate(divide="ignore", invalid="ignore"):
            lcoe = (a_s * s_vals[:, None] + a_w * w_vals[None, :] + a_b * np.power(b, GAMMA)) / (d0 * sf)
        lcoe = np.where(np.isfinite(b) & (sf > 0), lcoe, np.inf)
        idx = np.unravel_index(int(np.argmin(lcoe)), lcoe.shape)
        val = float(lcoe[idx])
        if not np.isfinite(val):
            continue
        if best is None or val < best[0]:
            best = (val, float(s_vals[idx[0]]), float(w_vals[idx[1]]), float(b[idx]), float(sf[idx]))
    if best is None:
        return None
    val, s_star, w_star, b_min_star, sf_star = best
    return s_star, w_star, b_min_star, sf_star, val


def dense_lcoe_argmin(
    solar: np.ndarray,
    wind: np.ndarray,
    s_star: float,
    w_star: float,
    b_min_star: float,
    target: float,
    a_s: float,
    a_w: float,
    a_b: float,
    d0: float,
) -> tuple[float, float]:
    """Locate the true argmin of LCOE(b) at fixed (s_star, w_star) by dense sweep, widening the
    span outward if the argmin lands on the sweep's right edge. Returns (b_true, lcoe_true)."""
    lo, hi = 0.0, max(b_min_star * LADDER_SPAN * 1.2, 0.01)
    for _ in range(DENSE_SWEEP_MAX_WIDENINGS):
        b_grid = np.linspace(lo, hi, DENSE_SWEEP_POINTS)
        lcoes = np.empty_like(b_grid)
        for i, b in enumerate(b_grid):
            cov, sf = dispatch_metrics(solar, wind, s_star, w_star, float(b))
            lcoes[i] = np.inf if cov < target or sf <= 0 else (a_s * s_star + a_w * w_star + a_b * b**GAMMA) / (d0 * sf)
        j = int(np.argmin(lcoes))
        if j < DENSE_SWEEP_POINTS - 2:
            return float(b_grid[j]), float(lcoes[j])
        hi *= 2.0
    return float(b_grid[j]), float(lcoes[j])


def ladder_rung_lcoe(
    solar: np.ndarray,
    wind: np.ndarray,
    s_star: float,
    w_star: float,
    b_min_star: float,
    a_s: float,
    a_w: float,
    a_b: float,
    d0: float,
) -> float:
    """Best LCOE achievable by a 5-rung geometric ladder from b_min_star to LADDER_SPAN*b_min_star."""
    if b_min_star <= 0.0:
        cov, sf = dispatch_metrics(solar, wind, s_star, w_star, 0.0)
        return (a_s * s_star + a_w * w_star) / (d0 * sf) if sf > 0 else np.inf
    r = LADDER_SPAN ** (1.0 / (LADDER_RUNGS - 1))
    best = np.inf
    for k in range(LADDER_RUNGS):
        b = b_min_star * r**k
        cov, sf = dispatch_metrics(solar, wind, s_star, w_star, b)
        if sf <= 0:
            continue
        lcoe = (a_s * s_star + a_w * w_star + a_b * b**GAMMA) / (d0 * sf)
        best = min(best, lcoe)
    return best


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--points", type=int, default=1000)
    parser.add_argument("--inputs", default="cds-2024-lulc+excl")
    parser.add_argument("--seed", type=int, default=RANDOM_SEED)
    parser.add_argument(
        "--out", default=None, help="CSV output path (default: a temp dir, not this repo -- scratch output)"
    )
    args = parser.parse_args()

    out_path = Path(args.out) if args.out else Path(tempfile.gettempdir()) / f"s3_ladder_benchmark_{args.points}.csv"

    path_config = PathConfig.from_auto_detect(input_set=args.inputs)
    cost_coeffs = build_cost_coefficients()
    a_s, a_w, a_b, d0 = cost_coeffs.a_s, cost_coeffs.a_w, cost_coeffs.a_b, cost_coeffs.d0
    params = SearchParams()

    t0 = time.time()
    points = sample_points(path_config, args.points, args.seed)
    print(f"Sampled {len(points)} points from {len(set(p[0] for p in points))} regions in {time.time() - t0:.1f}s")

    points_by_region: dict[str, list[tuple[str, int, int, float, float]]] = {}
    for pt in points:
        points_by_region.setdefault(pt[0], []).append(pt)

    rows: list[dict] = []
    n_no_optimum = 0
    n_zero_potential = 0
    t_start = time.time()
    n_done = 0
    for region, region_points in points_by_region.items():
        profile = open_regional_dataset("profile", region, path_config)
        for _, iy, ix, lat, lon in region_points:
            solar = np.ascontiguousarray(profile["solar"].isel(y=iy, x=ix).values, dtype=np.float64)
            wind = np.ascontiguousarray(profile["wind"].isel(y=iy, x=ix).values, dtype=np.float64)
            for p in (5, 15):
                target = 1.0 - p / 100.0
                frontier = build_pixel_frontier(solar, wind, p, params, cost_coeffs)
                if frontier.status == 3:
                    n_zero_potential += 1
                    continue
                if frontier.status != STATUS_OK:
                    n_no_optimum += 1
                    continue
                found = _argmin_over_frontier(frontier, a_s, a_w, a_b, d0)
                if found is None:
                    n_no_optimum += 1
                    continue
                s_star, w_star, b_min_star, sf_star, lcoe_star = found
                b_true, lcoe_true = dense_lcoe_argmin(
                    solar, wind, s_star, w_star, b_min_star, target, a_s, a_w, a_b, d0
                )
                rung_lcoe = ladder_rung_lcoe(solar, wind, s_star, w_star, b_min_star, a_s, a_w, a_b, d0)
                ladder_excess_pct = 100.0 * (rung_lcoe / lcoe_true - 1.0) if np.isfinite(lcoe_true) else np.nan
                brackets = b_true <= b_min_star * LADDER_SPAN * 1.001
                rows.append(
                    {
                        "region": region,
                        "lat": lat,
                        "lon": lon,
                        "p": p,
                        "s_star": s_star,
                        "w_star": w_star,
                        "b_min": b_min_star,
                        "b_true": b_true,
                        "ratio_b_true_over_b_min": (b_true / b_min_star) if b_min_star > 1e-9 else np.nan,
                        "lcoe_at_b_min": lcoe_star,
                        "lcoe_true": lcoe_true,
                        "ladder_best_lcoe": rung_lcoe,
                        "ladder_excess_pct": ladder_excess_pct,
                        "brackets_numerically": brackets,
                    }
                )
            n_done += 1
            if n_done % 100 == 0:
                elapsed = time.time() - t_start
                print(
                    f"  {n_done}/{len(points)} points, {elapsed:.0f}s elapsed, {elapsed / n_done * 1000:.0f} ms/point"
                )
        profile.close()

    print(
        f"Done: {len(rows)} (point, p) evaluations, {n_zero_potential} zero-potential, {n_no_optimum} no-optimum, "
        f"in {time.time() - t_start:.1f}s"
    )

    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {out_path}")

    for p in (5, 15):
        sub = [r for r in rows if r["p"] == p]
        ratios = np.array([r["ratio_b_true_over_b_min"] for r in sub if np.isfinite(r["ratio_b_true_over_b_min"])])
        excess = np.array([r["ladder_excess_pct"] for r in sub if np.isfinite(r["ladder_excess_pct"])])
        brackets_frac = np.mean([r["brackets_numerically"] for r in sub])
        print(f"\n--- p={p} ({len(sub)} points, {len(ratios)} with b_min > 0) ---")
        print(
            f"  b_true / b_min:      median={np.median(ratios):.3f}  p90={np.percentile(ratios, 90):.3f}  "
            f"p99={np.percentile(ratios, 99):.3f}  max={ratios.max():.3f}"
        )
        print(
            f"  ladder excess LCOE:  median={np.median(excess):.4f}%  p90={np.percentile(excess, 90):.4f}%  "
            f"p99={np.percentile(excess, 99):.4f}%  max={excess.max():.4f}%"
        )
        print(f"  numerically bracketed by [b_min, {LADDER_SPAN}*b_min]: {100 * brackets_frac:.1f}%")


if __name__ == "__main__":
    main()
