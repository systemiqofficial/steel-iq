"""Benchmark BOA's Monte-Carlo sampling approach (`boa_logic.capacity_sampling`) against
an actual capacity-expansion optimization (PyPSA) for the same solar+wind+battery design
problem, across a sweep of sample sizes and coverage thresholds, at a handful of
representative grid cells.

Prerequisites (run once, see each script's own docstring):
    uv run python scripts/boa_benchmark/preprocess_copernicus.py --year 2025
    uv run python scripts/boa_benchmark/preprocess_costs.py
    uv run python scripts/boa_benchmark/select_sites.py --year 2025   # then hand-write sites.yaml

sites.yaml format:
    - name: north_sea_coast
      lat: 54.0
      lon: 6.0
      cost_region: "EU + Schengen"   # must match a region in flat_costs.csv
"""

import argparse
import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from baseload_optimisation_atlas.boa_logic import (
    capacity_sampling,
    filter_designs_according_to_coverage_and_calculate_costs,
)

from cost_inputs import load_benchmark_costs
from design_metrics import score_lcoe, simulate_design
from point_profile import load_point_profile
from pypsa_model import solve_optimal_design

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def _best_empty_start_production(candidates, baseload_demand, costs, profile, coverage_p):
    """Zero-loss path only: reuses today's unmodified production
    filter_designs_according_to_coverage_and_calculate_costs (hardcoded empty-start,
    zero-loss state_of_charge) as-is, as a validation anchor."""
    capex = dict(costs.capex)
    accepted, installation_costs, lcoes = filter_designs_according_to_coverage_and_calculate_costs(
        candidates,
        baseload_demand,
        capex,
        costs.storage_costs,
        costs.opex_pct,
        profile,
        costs.cost_of_capital,
        costs.investment_horizon,
        coverage_p,
    )
    if len(accepted) == 0:
        return None, None
    idx = int(np.argmin(lcoes))
    return accepted[idx], float(lcoes[idx])


def _best_design(candidates, baseload_demand, costs, profile, coverage_p, soc_mode, standing_loss):
    """soc_mode='empty_start' with standing_loss != 0 needs this manual path -- production
    filter_designs_according_to_coverage_and_calculate_costs has no standing_loss support."""
    best_design, best_lcoe = None, None
    for design in candidates:
        metrics = simulate_design(design, profile, baseload_demand, soc_mode=soc_mode, standing_loss=standing_loss)
        if metrics.hours_coverage < 1 - coverage_p / 100:
            continue
        lcoe = score_lcoe(design, baseload_demand, costs, profile)
        if best_lcoe is None or lcoe < best_lcoe:
            best_design, best_lcoe = design, lcoe
    return best_design, best_lcoe


def _best_empty_start(candidates, baseload_demand, costs, profile, coverage_p, standing_loss):
    if standing_loss == 0.0:
        return _best_empty_start_production(candidates, baseload_demand, costs, profile, coverage_p)
    return _best_design(candidates, baseload_demand, costs, profile, coverage_p, "empty_start", standing_loss)


def _best_cyclic(candidates, baseload_demand, costs, profile, coverage_p, standing_loss):
    return _best_design(candidates, baseload_demand, costs, profile, coverage_p, "cyclic", standing_loss)


def run_sweep(
    sites: list[dict],
    year: int,
    data_dir: Path,
    cache_dir: Path,
    flat_costs_csv: Path,
    baseload_demand: float,
    coverage_thresholds: list[float],
    sample_sizes: list[int],
    n_seeds: int,
    solver: str,
    standing_loss: float = 0.0,
    coverage_metric: str = "hours",
) -> pd.DataFrame:
    rows = []

    for site in sites:
        profile = load_point_profile(data_dir, year, site["lat"], site["lon"], cache_dir)
        costs = load_benchmark_costs(flat_costs_csv, site["cost_region"])
        logger.info(
            f"Site: {site['name']} ({site['lat']}, {site['lon']}) cost_region={site['cost_region']} "
            f"cost_of_capital={costs.cost_of_capital:.4f}"
        )

        for coverage in coverage_thresholds:
            coverage_p = (1 - coverage) * 100

            pypsa_result = solve_optimal_design(
                profile,
                baseload_demand,
                costs,
                coverage_p,
                solver=solver,
                standing_loss=standing_loss,
                coverage_metric=coverage_metric,
            )
            pypsa_lcoe = score_lcoe(pypsa_result.design, baseload_demand, costs, profile)
            pypsa_metrics_cyclic = simulate_design(
                pypsa_result.design, profile, baseload_demand, "cyclic", standing_loss=standing_loss
            )
            pypsa_metrics_empty = simulate_design(
                pypsa_result.design, profile, baseload_demand, "empty_start", standing_loss=standing_loss
            )
            logger.info(
                f"  coverage={coverage} coverage_metric={coverage_metric} PyPSA lcoe={pypsa_lcoe:.2f} "
                f"design={pypsa_result.design} solve_s={pypsa_result.solve_seconds:.1f}"
            )

            for n_samples in sample_sizes:
                for seed in range(n_seeds):
                    t0 = time.time()
                    candidates = capacity_sampling(profile, coverage_p, n_samples=n_samples, seed=seed)
                    sample_seconds = time.time() - t0

                    t0 = time.time()
                    empty_design, empty_lcoe = _best_empty_start(
                        candidates, baseload_demand, costs, profile, coverage_p, standing_loss
                    )
                    empty_seconds = time.time() - t0

                    t0 = time.time()
                    cyclic_design, cyclic_lcoe = _best_cyclic(
                        candidates, baseload_demand, costs, profile, coverage_p, standing_loss
                    )
                    cyclic_seconds = time.time() - t0

                    row = {
                        "site": site["name"],
                        "year": year,
                        "lat": site["lat"],
                        "lon": site["lon"],
                        "cost_region": site["cost_region"],
                        "coverage_threshold": coverage,
                        "coverage_metric": coverage_metric,
                        "n_samples": n_samples,
                        "seed": seed,
                        "solver": solver,
                        "standing_loss": standing_loss,
                        "pypsa_lcoe": pypsa_lcoe,
                        "pypsa_solar": pypsa_result.design["solar"],
                        "pypsa_wind": pypsa_result.design["wind"],
                        "pypsa_battery": pypsa_result.design["battery"],
                        "pypsa_hours_coverage_cyclic": pypsa_metrics_cyclic.hours_coverage,
                        "pypsa_energy_coverage_cyclic": pypsa_metrics_cyclic.energy_coverage,
                        "pypsa_hours_coverage_empty_start": pypsa_metrics_empty.hours_coverage,
                        "pypsa_energy_coverage_empty_start": pypsa_metrics_empty.energy_coverage,
                        "pypsa_solve_seconds": pypsa_result.solve_seconds,
                        "boa_sample_seconds": sample_seconds,
                        "boa_empty_start_lcoe": empty_lcoe,
                        "boa_empty_start_seconds": empty_seconds,
                        "boa_cyclic_lcoe": cyclic_lcoe,
                        "boa_cyclic_seconds": cyclic_seconds,
                    }
                    if empty_design is not None:
                        row["boa_empty_start_solar"] = empty_design["solar"]
                        row["boa_empty_start_wind"] = empty_design["wind"]
                        row["boa_empty_start_battery"] = empty_design["battery"]
                        row["gap_pct_empty_start"] = (empty_lcoe - pypsa_lcoe) / pypsa_lcoe
                        if row["gap_pct_empty_start"] < 0:
                            logger.warning(
                                f"  NEGATIVE empty_start gap ({row['gap_pct_empty_start']:.4f}) at "
                                f"{site['name']} coverage={coverage} n={n_samples} seed={seed} -- "
                                "sampling beat the 'optimal' PyPSA design; likely a formulation mismatch."
                            )
                    if cyclic_design is not None:
                        row["boa_cyclic_solar"] = cyclic_design["solar"]
                        row["boa_cyclic_wind"] = cyclic_design["wind"]
                        row["boa_cyclic_battery"] = cyclic_design["battery"]
                        row["gap_pct_cyclic"] = (cyclic_lcoe - pypsa_lcoe) / pypsa_lcoe
                        if row["gap_pct_cyclic"] < 0:
                            logger.warning(
                                f"  NEGATIVE cyclic gap ({row['gap_pct_cyclic']:.4f}) at "
                                f"{site['name']} coverage={coverage} n={n_samples} seed={seed}"
                            )
                    rows.append(row)

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
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--baseload-demand", type=float, default=500.0)
    parser.add_argument("--coverage-thresholds", type=str, default="0.99,0.95,0.9,0.85")
    parser.add_argument("--sample-sizes", type=str, default="100,300,1000,3000,10000")
    parser.add_argument("--n-seeds", type=int, default=5)
    parser.add_argument("--solver", type=str, choices=["highs", "gurobi"], default="highs")
    parser.add_argument(
        "--coverage-metric",
        type=str,
        choices=["hours", "energy"],
        default="hours",
        help="'hours' (default): MILP, binary per-hour coverage -- matches BOA's own "
        "calculate_coverage exactly (any nonzero shortfall marks that hour uncovered), so "
        "PyPSA's design is a genuine apples-to-apples ground truth for BOA's real filter. "
        "'energy': faster LP, caps total unserved energy for the year with no regard for "
        "how many hours it's spread across -- NOT equivalent to 'hours', a strictly weaker "
        "constraint kept only as a fast relaxed-bound sanity check.",
    )
    parser.add_argument(
        "--standing-loss",
        type=float,
        default=0.0,
        help="Fraction of stored battery energy lost per hour (0 = no loss, the default; "
        "matches PyPSA Store's standing_loss semantics).",
    )
    parser.add_argument("--out", type=Path, default=Path("scripts/boa_benchmark/results/boa_benchmark_results.csv"))
    args = parser.parse_args()

    with open(args.sites_file) as f:
        sites = yaml.safe_load(f)

    if args.site_names is not None:
        wanted = set(args.site_names.split(","))
        sites = [s for s in sites if s["name"] in wanted]
        missing = wanted - {s["name"] for s in sites}
        if missing:
            raise ValueError(f"Site name(s) {sorted(missing)} not found in {args.sites_file}")

    coverage_thresholds = [float(x) for x in args.coverage_thresholds.split(",")]
    sample_sizes = [int(x) for x in args.sample_sizes.split(",")]

    df = run_sweep(
        sites=sites,
        year=args.year,
        data_dir=args.data_dir,
        cache_dir=args.cache_dir,
        flat_costs_csv=args.flat_costs_csv,
        baseload_demand=args.baseload_demand,
        coverage_thresholds=coverage_thresholds,
        sample_sizes=sample_sizes,
        n_seeds=args.n_seeds,
        solver=args.solver,
        standing_loss=args.standing_loss,
        coverage_metric=args.coverage_metric,
    )
    df.to_csv(args.out, index=False)
    logger.info(f"Wrote {len(df)} rows to {args.out}")


if __name__ == "__main__":
    main()
