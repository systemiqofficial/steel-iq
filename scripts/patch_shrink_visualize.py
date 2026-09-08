"""
Throwaway visualization (not committed -- scripts/ is gitignored): reuses
boa_search_grid_plot.plot_search_grid to show, for a few representative EUROPE
pixels, what the coarse grid + patches actually look like under production
SearchParams() versus the reduced candidate under discussion
(patch_halfwidth=0.2, coarse_grid=17, coarse_stride=2).

Not a real CLI-pipeline run: frontiers are built directly with build_pixel_frontier
using the same synthetic anchor set as patch_shrink_benchmark.py, rather than reading
a frontier cache built from a real cost workbook. "years" in plot_search_grid's API is
repurposed as a scenario label (build anchor index, or "query" for the held-out winner)
since we have no real investment years here.

Run:
    .venv\\Scripts\\python.exe scripts/patch_shrink_visualize.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import xarray as xr

sys.path.insert(0, str(Path(__file__).parent))
from boa_search_grid_plot import _anchor_seed_points, plot_search_grid  # noqa: E402

from boa.config.constants import AVERAGE_IMPLIED_STORAGE, HOURS_IN_YEAR, KILO_TO_MEGA  # noqa: E402
from boa.config.physical_parameters import LIFETIMES  # noqa: E402
from boa.model.bisection import (  # noqa: E402
    GAMMA,
    CostCoefficients,
    Optimum,
    SearchParams,
    argmin_lcoe,
    build_pixel_frontier,
)

REGION = "EUROPE"
LIVE_DIR = Path.home() / ".steelo/boa/inputs/cds-2023-lulc+excl/cds-zarr"
COVERAGE = 0.85
OUT_DIR = Path(__file__).parent / "plots"


def _horizon() -> int:
    return max(LIFETIMES.values())


def make_coeffs(capex_kw, capex_kwh_battery, opex_pct, wacc) -> CostCoefficients:
    horizon = _horizon()
    discount = np.array([1.0 / (1.0 + wacc) ** t for t in range(horizon + 1)])

    def w0(opex: float) -> float:
        return 1.0 + opex * discount[1 : horizon + 1].sum()

    return CostCoefficients(
        a_s=w0(opex_pct["solar"]) * capex_kw["solar"] * KILO_TO_MEGA,
        a_w=w0(opex_pct["wind"]) * capex_kw["wind"] * KILO_TO_MEGA,
        a_b=(AVERAGE_IMPLIED_STORAGE ** (1.0 - GAMMA)) * w0(opex_pct["battery"]) * capex_kwh_battery * KILO_TO_MEGA,
        d0=HOURS_IN_YEAR * discount[1 : horizon + 1].sum(),
    )


ANCHOR_SPECS = {
    "baseline": dict(
        capex_kw={"solar": 1115.52, "wind": 1821.12},
        capex_kwh_battery=235.43,
        opex_pct={"solar": 0.01, "wind": 0.02, "battery": 0.02},
        wacc=0.0548,
    ),
    "solar_cheap": dict(
        capex_kw={"solar": 550.0, "wind": 1821.12},
        capex_kwh_battery=235.43,
        opex_pct={"solar": 0.01, "wind": 0.02, "battery": 0.02},
        wacc=0.0548,
    ),
    "wind_cheap": dict(
        capex_kw={"solar": 1115.52, "wind": 900.0},
        capex_kwh_battery=235.43,
        opex_pct={"solar": 0.01, "wind": 0.02, "battery": 0.02},
        wacc=0.0548,
    ),
    "battery_cheap": dict(
        capex_kw={"solar": 1115.52, "wind": 1821.12},
        capex_kwh_battery=90.0,
        opex_pct={"solar": 0.01, "wind": 0.02, "battery": 0.02},
        wacc=0.0548,
    ),
    "held_out": dict(
        capex_kw={"solar": 750.0, "wind": 2200.0},
        capex_kwh_battery=140.0,
        opex_pct={"solar": 0.015, "wind": 0.025, "battery": 0.015},
        wacc=0.07,
    ),
}
BUILD_ANCHOR_NAMES = ["baseline", "solar_cheap", "wind_cheap", "battery_cheap"]
QUERY_ANCHOR_NAME = "held_out"

CANDIDATE_PARAMS = SearchParams(patch_halfwidth=0.2, coarse_grid=17, coarse_stride=2)
CONFIGS = {"default": SearchParams(), "candidate_gc17_stride2_hw0.2": CANDIDATE_PARAMS}


def pick_representative_pixels(live_dir: Path, region: str) -> list[tuple[str, int, int, float, float]]:
    """(label, iy, ix, lat, lon) for a solar-heavy, a wind-heavy, and a mixed land pixel."""
    with xr.open_zarr(live_dir / f"max_capacity_{region}_2023_025_deg.zarr", consolidated=True) as ds:
        valid = (ds["pv"].values > 0) | (ds["wind"].values > 0)
        lats = ds["y"].values
        lons = ds["x"].values
    with xr.open_zarr(live_dir / f"pv_and_wind_potential_{region}_2023_025_deg.zarr", consolidated=True) as ds:
        solar_cf = ds["solar"].mean(dim="time").values
        wind_cf = ds["wind"].mean(dim="time").values

    iy, ix = np.nonzero(valid)
    s_vals, w_vals = solar_cf[iy, ix], wind_cf[iy, ix]
    # A handful of "land" pixels (per max_capacity) have NaN profiles (data gaps); argmax
    # treats NaN as the max, so it must be excluded up front rather than just skipped later.
    finite = np.isfinite(s_vals) & np.isfinite(w_vals)
    iy, ix, s_vals, w_vals = iy[finite], ix[finite], s_vals[finite], w_vals[finite]

    solar_heavy = int(np.argmax(s_vals - w_vals))
    wind_heavy = int(np.argmax(w_vals - s_vals))
    # "mixed": both resources reasonably strong (high rank) and close to each other in rank
    # -- rank-based rather than raw CF, since solar and wind CF live on very different scales
    # over EUROPE land and a raw-value percentile filter on both can be empty.
    rank_s = s_vals.argsort().argsort() / (len(s_vals) - 1)
    rank_w = w_vals.argsort().argsort() / (len(w_vals) - 1)
    mixed = int(np.argmax(np.minimum(rank_s, rank_w) - np.abs(rank_s - rank_w)))

    picks = [("solar_heavy", solar_heavy), ("wind_heavy", wind_heavy), ("mixed", mixed)]
    return [(label, int(iy[k]), int(ix[k]), float(lats[iy[k]]), float(lons[ix[k]])) for label, k in picks]


def main() -> int:
    OUT_DIR.mkdir(exist_ok=True)
    print(f"Picking representative pixels in {REGION}...")
    pixels = pick_representative_pixels(LIVE_DIR, REGION)
    for label, iy, ix, lat, lon in pixels:
        print(f"  {label}: (iy={iy}, ix={ix}) lat={lat:.2f} lon={lon:.2f}")

    with xr.open_zarr(LIVE_DIR / f"pv_and_wind_potential_{REGION}_2023_025_deg.zarr", consolidated=True) as ds:
        iy_arr = np.array([p[1] for p in pixels])
        ix_arr = np.array([p[2] for p in pixels])
        solar_all = ds["solar"].values[:, iy_arr, ix_arr].astype(np.float64)
        wind_all = ds["wind"].values[:, iy_arr, ix_arr].astype(np.float64)

    build_anchors = [make_coeffs(**ANCHOR_SPECS[name]) for name in BUILD_ANCHOR_NAMES]
    query_coeffs = make_coeffs(**ANCHOR_SPECS[QUERY_ANCHOR_NAME])

    for idx, (label, iy, ix, lat, lon) in enumerate(pixels):
        solar = np.ascontiguousarray(solar_all[:, idx])
        wind = np.ascontiguousarray(wind_all[:, idx])

        for config_name, params in CONFIGS.items():
            frontier = build_pixel_frontier(solar, wind, COVERAGE, params, build_anchors)
            print(
                f"  [{label}/{config_name}] status={frontier.status} n_patches={frontier.n_patches} "
                f"box_widenings={frontier.box_widenings}"
            )

            # Repurpose plot_search_grid's "year" axis: one slot per build anchor showing
            # where THAT anchor alone would have seeded (triangles), plus one "query" slot
            # showing the held-out-coefficient winner (the actual query-time answer).
            winners: dict[int, Optimum | None] = {}
            anchors: dict[int, list[tuple[float, float]]] = {}
            for i, name in enumerate(BUILD_ANCHOR_NAMES):
                winners[i] = None
                anchors[i] = _anchor_seed_points(frontier, params, build_anchors[i])
            query_slot = len(BUILD_ANCHOR_NAMES)
            winners[query_slot] = argmin_lcoe(frontier, query_coeffs) if frontier.n_patches > 0 else None
            anchors[query_slot] = []

            out_path = OUT_DIR / f"{label}_{config_name}.png"
            plot_search_grid(
                frontier,
                REGION,
                idx,
                lat,
                lon,
                COVERAGE,
                baseload=1000.0,
                years=list(range(query_slot + 1)),
                winners=winners,
                anchors=anchors,
                output_path=out_path,
            )
            print(f"    wrote {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
