"""How much does BOA/GBS's optimal design change depending on which historical weather
year it's sized against, and what does it cost to hedge against that instead of picking one
year and hoping?

For each site, runs `gbs.find_gbs_design` (hours metric, GBS as the accuracy/cost-tractable
stand-in for a certified optimum -- see `core/gbs.py`'s module docstring) separately against each
of `--years`' weather profiles, plus one `gbs.find_robust_gbs_design` run that must meet the
coverage threshold in *every* one of those years at once. The per-year runs answer "what
would have been optimal in hindsight for that year alone"; the robust run answers "what do
you actually have to build if you must commit before knowing which year's weather occurs" --
these are not the same design, and comparing their LCOEs (the "robustness premium") is the
point. See README.md's weather-year sensitivity section for why this replaced a simpler
"evaluate one design against each year and average the LCOE" idea: LCOE is a pure function
of installed capacity under `use_curtailment=True` (see `find_robust_gbs_design`'s
docstring), so it does not vary across weather years for a fixed design at all -- averaging
it would be a no-op. What actually varies across years for a fixed design is *coverage*, and
the robust design is the smallest one that never falls short of the threshold across the
years it was checked against.

Produces one long-format CSV, one row per (site, year) plus one "robust" row per site:
    site, lat, lon, cost_region, coverage_threshold, metric, standing_loss, soc_mode,
    method, year, lcoe, solar, wind, battery, coverage, n_evaluations, seconds,
    energy_share_solar_direct, energy_share_wind_direct, energy_share_battery_solar,
    energy_share_battery_wind, energy_share_unmet, curtailment_solar, curtailment_wind

`method` is "gbs" (year = the weather year) or "gbs_robust" (year = NaN, coverage = the
worst/binding year's realized coverage). If no design in the search box meets the threshold
for a given site/year (or jointly across years, for the robust row), that row's
lcoe/solar/wind/battery/coverage/n_evaluations/energy_share_*/curtailment_* are NaN and a
warning is logged -- same convention as `run_methodology_comparison.py`.

The `energy_share_*`/`curtailment_*` columns (see `design_metrics.decompose_energy_flows`)
attribute a "gbs" row's realized annual dispatch back to solar vs. wind origin -- direct
generation-to-demand, battery-mediated (discharge attributed by the battery's accumulated
composition, not just that hour's generation mix), and curtailed, respectively. The first
five (`*_direct`, `*_battery`, `energy_share_unmet`) are fractions of annual demand and sum
to ~1.0; `curtailment_*` is a separate multiple of annual demand (same units as the
solar/wind overscale columns), since curtailed generation never reaches demand at all. Only
populated for "gbs" rows -- not "gbs_robust" (whose single design has no one weather year to
decompose against) or "lp" rows.

`--include-lp` (metric="energy" only) additionally solves `pypsa_model.solve_optimal_design`
-- the certified LP -- per weather year, emitting extra `method="lp"` rows (`soc_mode`
always "cyclic" for these, matching PyPSA's `Store(e_cyclic=True)`, regardless of the
`--soc-mode` used for the "gbs" rows) so "gbs" can be checked directly against a genuine
ground truth per year, not just in aggregate (as `core/gbs.py --validate` already does for a
single design). There is no "lp_robust" row: a joint multi-year LP (one shared design,
separate per-year dispatch/cyclic-SOC/unserved-cap constraints) isn't implemented here --
"gbs_robust" is the only robust-design row, at whatever accuracy GBS itself has (already
shown to track the single-year LP to ~0.0000% relative gap for the energy metric, via
`core/gbs.py --validate`).

Usage:
    uv run python -m scripts.boa_benchmark.runners.run_weather_year_sensitivity \\
        --years 2010,2015,2020,2025 --coverage-threshold 0.95 --n-refinements 3
    uv run python -m scripts.boa_benchmark.runners.run_weather_year_sensitivity \\
        --metric energy --coverage-threshold 0.95 --include-lp
"""

import argparse
import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from ..core.cost_inputs import load_benchmark_costs
from ..core.design_metrics import EnergyFlowShares, decompose_energy_flows, score_lcoe, simulate_design
from ..core.gbs import find_gbs_design, find_robust_gbs_design
from ..core.point_profile import load_point_profile
from ..core.pypsa_model import solve_optimal_design

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def _row(
    site: dict,
    coverage_threshold: float,
    metric: str,
    standing_loss: float,
    soc_mode: str,
    method: str,
    year: float,
    lcoe: float | None,
    solar: float,
    wind: float,
    battery: float,
    coverage: float,
    n_evaluations: float,
    seconds: float,
    energy_flows: EnergyFlowShares | None = None,
) -> dict:
    return {
        "site": site["name"],
        "lat": site["lat"],
        "lon": site["lon"],
        "cost_region": site["cost_region"],
        "coverage_threshold": coverage_threshold,
        "metric": metric,
        "standing_loss": standing_loss,
        "soc_mode": soc_mode,
        "method": method,
        "year": year,
        "lcoe": lcoe if lcoe is not None else np.nan,
        "solar": solar,
        "wind": wind,
        "battery": battery,
        "coverage": coverage,
        "n_evaluations": n_evaluations,
        "seconds": seconds,
        "energy_share_solar_direct": energy_flows.solar_direct if energy_flows is not None else np.nan,
        "energy_share_wind_direct": energy_flows.wind_direct if energy_flows is not None else np.nan,
        "energy_share_battery_solar": energy_flows.battery_solar if energy_flows is not None else np.nan,
        "energy_share_battery_wind": energy_flows.battery_wind if energy_flows is not None else np.nan,
        "energy_share_unmet": energy_flows.unmet if energy_flows is not None else np.nan,
        "curtailment_solar": energy_flows.curtailment_solar if energy_flows is not None else np.nan,
        "curtailment_wind": energy_flows.curtailment_wind if energy_flows is not None else np.nan,
    }


def run_sensitivity(
    sites: list[dict],
    data_dir: Path,
    cache_dir: Path,
    flat_costs_csv: Path,
    years: list[int],
    baseload_demand: float,
    coverage_threshold: float,
    metric: str,
    soc_mode: str,
    standing_loss: float,
    gbs_coarse_grid: int,
    n_refinements: int,
    s_max: float = 8.0,
    w_max: float = 8.0,
    include_lp: bool = False,
    solver: str = "highs",
) -> pd.DataFrame:
    if include_lp and metric != "energy":
        raise ValueError(
            "--include-lp requires --metric energy -- pypsa_model's LP is only a certified "
            "ground truth for the energy metric (see core/pypsa_model.py's docstring)."
        )
    rows = []
    coverage_p = (1 - coverage_threshold) * 100

    for site in sites:
        logger.info(f"Site: {site['name']} ({site['lat']}, {site['lon']}) cost_region={site['cost_region']}")
        costs = load_benchmark_costs(flat_costs_csv, site["cost_region"])
        profiles = {year: load_point_profile(data_dir, year, site["lat"], site["lon"], cache_dir) for year in years}

        for year in years:
            t0 = time.time()
            try:
                result = find_gbs_design(
                    profiles[year],
                    baseload_demand,
                    costs,
                    coverage_p,
                    soc_mode=soc_mode,
                    metric=metric,
                    standing_loss=standing_loss,
                    coarse_grid=gbs_coarse_grid,
                    n_refinements=n_refinements,
                    s_max=s_max,
                    w_max=w_max,
                )
            except RuntimeError as exc:
                logger.warning(f"  year={year} infeasible: {exc}")
                rows.append(
                    _row(
                        site,
                        coverage_threshold,
                        metric,
                        standing_loss,
                        soc_mode,
                        "gbs",
                        year,
                        None,
                        np.nan,
                        np.nan,
                        np.nan,
                        np.nan,
                        np.nan,
                        time.time() - t0,
                    )
                )
                continue
            logger.info(f"  year={year} lcoe={result.lcoe:.2f} design={result.design}")
            energy_flows = decompose_energy_flows(
                result.design, profiles[year], soc_mode=soc_mode, standing_loss=standing_loss
            )
            rows.append(
                _row(
                    site,
                    coverage_threshold,
                    metric,
                    standing_loss,
                    soc_mode,
                    "gbs",
                    year,
                    result.lcoe,
                    result.design["solar"],
                    result.design["wind"],
                    result.design["battery"],
                    result.coverage,
                    result.n_evaluations,
                    result.search_seconds,
                    energy_flows=energy_flows,
                )
            )

            if include_lp:
                t0 = time.time()
                lp = solve_optimal_design(
                    profiles[year], baseload_demand, costs, coverage_p, solver=solver, standing_loss=standing_loss
                )
                lp_lcoe = score_lcoe(lp.design, baseload_demand, costs, profiles[year])
                lp_coverage = simulate_design(
                    lp.design, profiles[year], baseload_demand, soc_mode="cyclic", standing_loss=standing_loss
                ).energy_coverage
                logger.info(f"  year={year} LP (certified) lcoe={lp_lcoe:.2f} design={lp.design}")
                rows.append(
                    _row(
                        site,
                        coverage_threshold,
                        metric,
                        standing_loss,
                        "cyclic",
                        "lp",
                        year,
                        lp_lcoe,
                        lp.design["solar"],
                        lp.design["wind"],
                        lp.design["battery"],
                        lp_coverage,
                        np.nan,
                        time.time() - t0,
                    )
                )

        t0 = time.time()
        try:
            robust = find_robust_gbs_design(
                list(profiles.values()),
                baseload_demand,
                costs,
                coverage_p,
                soc_mode=soc_mode,
                metric=metric,
                standing_loss=standing_loss,
                coarse_grid=gbs_coarse_grid,
                n_refinements=n_refinements,
                s_max=s_max,
                w_max=w_max,
            )
        except RuntimeError as exc:
            logger.warning(f"  robust (years={years}) infeasible: {exc}")
            rows.append(
                _row(
                    site,
                    coverage_threshold,
                    metric,
                    standing_loss,
                    soc_mode,
                    "gbs_robust",
                    np.nan,
                    None,
                    np.nan,
                    np.nan,
                    np.nan,
                    np.nan,
                    np.nan,
                    time.time() - t0,
                )
            )
            continue
        logger.info(f"  robust lcoe={robust.lcoe:.2f} design={robust.design} worst_coverage={robust.coverage:.4f}")
        rows.append(
            _row(
                site,
                coverage_threshold,
                metric,
                standing_loss,
                soc_mode,
                "gbs_robust",
                np.nan,
                robust.lcoe,
                robust.design["solar"],
                robust.design["wind"],
                robust.design["battery"],
                robust.coverage,
                robust.n_evaluations,
                robust.search_seconds,
            )
        )

    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sites-file", type=Path, default=Path("scripts/boa_benchmark/sites.yaml"))
    parser.add_argument(
        "--site-names",
        type=str,
        default=None,
        help="Comma-separated subset of site 'name' values from --sites-file to run (default: all sites).",
    )
    parser.add_argument("--data-dir", type=Path, default=Path("scripts/boa_benchmark/preprocessed_data"))
    parser.add_argument("--cache-dir", type=Path, default=Path("scripts/boa_benchmark/preprocessed_data/cache"))
    parser.add_argument(
        "--flat-costs-csv", type=Path, default=Path("scripts/boa_benchmark/preprocessed_data/flat_costs.csv")
    )
    parser.add_argument(
        "--years",
        type=str,
        default="2010,2015,2020,2025",
        help="Weather years to compare -- each must already have preprocessed "
        "solar_{year}.nc/wind_{year}.nc (see preprocess_copernicus.py).",
    )
    parser.add_argument("--baseload-demand", type=float, default=500.0)
    parser.add_argument("--coverage-threshold", type=float, default=0.95)
    parser.add_argument("--metric", type=str, choices=["hours", "energy"], default="hours")
    parser.add_argument("--soc-mode", type=str, choices=["empty_start", "cyclic"], default="empty_start")
    parser.add_argument("--standing-loss", type=float, default=0.0)
    parser.add_argument("--gbs-coarse-grid", type=int, default=21)
    parser.add_argument(
        "--s-max",
        type=float,
        default=8.0,
        help="Solar overscale search-box upper bound -- widen for sites where GBS reports "
        "'No feasible design' (e.g. low-resource sites need more than 8x baseload overscale "
        "to reach high coverage thresholds at all, regardless of battery size).",
    )
    parser.add_argument("--w-max", type=float, default=8.0, help="Wind overscale search-box upper bound.")
    parser.add_argument(
        "--include-lp",
        action="store_true",
        help="Also solve the certified PyPSA LP per weather year (requires --metric energy) and emit "
        "extra 'lp' rows for direct per-year comparison against 'gbs' -- see module docstring.",
    )
    parser.add_argument(
        "--solver", type=str, choices=["highs", "gurobi"], default="highs", help="Solver for --include-lp."
    )
    parser.add_argument(
        "--n-refinements",
        type=int,
        default=3,
        help="GBS refinement level to use as the 'ground truth' design for both the per-year and "
        "robust runs -- not swept here (see run_methodology_comparison.py's convergence sweep for "
        "that); 3 balances search accuracy against the site x year x (years+1) run count.",
    )
    parser.add_argument("--out", type=Path, default=Path("scripts/boa_benchmark/results/weather_year_sensitivity.csv"))
    args = parser.parse_args()

    with open(args.sites_file) as f:
        sites = yaml.safe_load(f)

    if args.site_names is not None:
        wanted = set(args.site_names.split(","))
        sites = [s for s in sites if s["name"] in wanted]
        missing = wanted - {s["name"] for s in sites}
        if missing:
            raise ValueError(f"Site name(s) {sorted(missing)} not found in {args.sites_file}")

    df = run_sensitivity(
        sites=sites,
        data_dir=args.data_dir,
        cache_dir=args.cache_dir,
        flat_costs_csv=args.flat_costs_csv,
        years=[int(x) for x in args.years.split(",")],
        baseload_demand=args.baseload_demand,
        coverage_threshold=args.coverage_threshold,
        metric=args.metric,
        soc_mode=args.soc_mode,
        standing_loss=args.standing_loss,
        gbs_coarse_grid=args.gbs_coarse_grid,
        n_refinements=args.n_refinements,
        s_max=args.s_max,
        w_max=args.w_max,
        include_lp=args.include_lp,
        solver=args.solver,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False)
    logger.info(f"Wrote {len(df)} rows to {args.out}")


if __name__ == "__main__":
    main()
