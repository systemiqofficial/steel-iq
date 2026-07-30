"""Compare BOA sampling, Grid-Bisection Search (GBS -- `core/gbs.py`), and (where
certified) the PyPSA LP -- across sites, coverage thresholds, coverage metrics, and battery
SOC dispatch modes.

Replaces the retired `run_benchmark.py`, which only ever compared BOA sampling against
PyPSA (including, historically, an MILP for the `hours` metric that never certified a tight
gap -- see `core/gbs.py`'s module docstring and `README.md`).

Produces one long-format CSV, one row per run:
    site, lat, lon, cost_region, coverage_threshold, metric, standing_loss, soc_mode,
    method, budget, n_evaluations, seed, lcoe, battery, seconds

`method` is one of:
    "boa" -- BOA's `capacity_sampling` + best-of-sample selection (`_best_boa_design`
             below, the same production logic `run_benchmark.py` used).
             `budget` = n_samples, `seed` = sampling seed.
    "gbs" -- Grid-Bisection Search (`gbs.find_gbs_design`), run at a fixed
             `--gbs-coarse-grid` and swept over `--refinement-levels`
             (n_refinements) -- the actual cost/accuracy knob here, since coarse-grid
             resolution alone barely moves total work once n_refinements >= 1 (see
             `find_gbs_design`'s docstring). `budget` = n_refinements, `seed` =
             NaN (deterministic). If no design in the `[0, s_max] x [0, w_max]` search
             box meets the coverage threshold, `lcoe`/`battery`/`n_evaluations` are NaN
             (one such row per site/coverage/metric/soc_mode -- remaining refinement
             levels are skipped, since the infeasibility is a property of the search
             box, not of n_refinements, and would just repeat). `--s-max`/`--w-max`
             widen the box above the 8x baseload-overscale default -- needed for
             low-resource sites (e.g. `ecuador_colombia_coast`, near-zero wind) where
             the default box is either infeasible outright or where GBS's optimum
             sits on the box boundary (a warning, not a crash -- the true optimum may
             lie outside what was searched).
    "lp"  -- `pypsa_model.solve_optimal_design`'s certified LP. Only emitted for
             `metric="energy"` (the only metric it's a certified ground truth for --
             see `core/pypsa_model.py`'s docstring) and `soc_mode="cyclic"` (PyPSA's
             `Store(e_cyclic=True)` models a periodic SOC; there's no empty-start LP
             variant). `budget`/`seed`/`n_evaluations` are NaN -- one solve, not swept.
             `energy` is mainly this validation role -- see `--energy-coverage-thresholds`
             to sweep it at fewer thresholds than `hours`.

Prerequisites (run once, see each script's own docstring):
    uv run python -m scripts.boa_benchmark.preprocessing.preprocess_copernicus --year 2025
    uv run python -m scripts.boa_benchmark.preprocessing.preprocess_costs
    uv run python -m scripts.boa_benchmark.preprocessing.select_sites --year 2025   # then hand-write sites.yaml

Usage:
    uv run python -m scripts.boa_benchmark.runners.run_methodology_comparison \\
        --site-names inner_mongolia --coverage-thresholds 0.95 --metrics energy,hours

`--config path.yaml` loads defaults for any flag below from a YAML file, e.g.:
    coverage_thresholds: [0.99, 0.95, 0.9, 0.85]
    metrics: [energy, hours]
    soc_modes:
      gbs: [cyclic, empty_start]
      boa: [empty_start]
    n_seeds: 10
See `example_config.yaml` for a complete example. Individual CLI flags still override
specific config keys for one-off tweaks (`--config base.yaml --n-seeds 3`).
"""

import argparse
import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from baseload_optimisation_atlas.boa_logic import capacity_sampling

from ..core.cost_inputs import BenchmarkCosts, load_benchmark_costs
from ..core.design_metrics import score_lcoe, simulate_design
from ..core.gbs import find_gbs_design
from ..core.point_profile import load_point_profile
from ..core.pypsa_model import solve_optimal_design

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def _best_boa_design(
    candidates: list[dict[str, float]],
    baseload_demand: float,
    costs: BenchmarkCosts,
    profile: dict[str, np.ndarray],
    coverage_p: float,
    soc_mode: str,
    standing_loss: float,
    metric: str,
) -> tuple[dict[str, float] | None, float | None]:
    """Best-of-sample selection: filter sampled designs on the requested coverage metric,
    return the lowest-LCOE survivor. BOA's production logic, unchanged from the retired
    `run_benchmark.py`'s `_best_design`."""
    best_design, best_lcoe = None, None
    threshold = 1 - coverage_p / 100
    for design in candidates:
        metrics = simulate_design(design, profile, baseload_demand, soc_mode=soc_mode, standing_loss=standing_loss)
        coverage = metrics.hours_coverage if metric == "hours" else metrics.energy_coverage
        if coverage < threshold:
            continue
        lcoe = score_lcoe(design, baseload_demand, costs, profile)
        if best_lcoe is None or lcoe < best_lcoe:
            best_design, best_lcoe = design, lcoe
    return best_design, best_lcoe


def _row(
    site: dict,
    coverage_threshold: float,
    metric: str,
    standing_loss: float,
    soc_mode: str,
    method: str,
    budget: float,
    n_evaluations: float,
    seed: float,
    lcoe: float | None,
    battery: float,
    seconds: float,
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
        "budget": budget,
        "n_evaluations": n_evaluations,
        "seed": seed,
        "lcoe": lcoe if lcoe is not None else np.nan,
        "battery": battery,
        "seconds": seconds,
    }


def run_sweep(
    sites: list[dict],
    data_dir: Path,
    cache_dir: Path,
    flat_costs_csv: Path,
    year: int,
    baseload_demand: float,
    coverage_thresholds: list[float],
    metrics: list[str],
    soc_modes: list[str],
    sample_sizes: list[int],
    n_seeds: int,
    gbs_coarse_grid: int,
    refinement_levels: list[int],
    solver: str,
    standing_loss: float,
    energy_coverage_thresholds: list[float] | None = None,
    boa_soc_modes: list[str] | None = None,
    s_max: float = 8.0,
    w_max: float = 8.0,
) -> pd.DataFrame:
    """`energy_coverage_thresholds`, if given, overrides `coverage_thresholds` for the
    `energy` metric only -- useful since `energy` is mainly a validation device (its `lp`
    row is the only certified ground truth in this benchmark; see `core/gbs.py`'s
    `--validate`), so it's often only worth sweeping at one threshold while `hours` (the
    metric BOA's production filter actually enforces) gets the full sweep.

    `boa_soc_modes`, if given, restricts which of `soc_modes` also get a `boa` run
    (`gbs` always runs the full `soc_modes` list). Worth restricting to
    `["empty_start"]`: BOA's battery sizing (`estimate_battery_capacity`) doesn't take
    `soc_mode` as an input at all, so its `soc_mode="cyclic"` run costs the same as
    `empty_start` but tells you nothing about cyclic dispatch that GBS's own cyclic run
    doesn't already answer more directly -- see README.md's SOC-mode sensitivity section.
    """
    rows = []

    for site in sites:
        profile = load_point_profile(data_dir, year, site["lat"], site["lon"], cache_dir)
        costs = load_benchmark_costs(flat_costs_csv, site["cost_region"])
        logger.info(f"Site: {site['name']} ({site['lat']}, {site['lon']}) cost_region={site['cost_region']}")

        for metric in metrics:
            thresholds = (
                energy_coverage_thresholds
                if metric == "energy" and energy_coverage_thresholds is not None
                else coverage_thresholds
            )
            for coverage in thresholds:
                coverage_p = (1 - coverage) * 100

                if metric == "energy":
                    t0 = time.time()
                    lp = solve_optimal_design(
                        profile, baseload_demand, costs, coverage_p, solver=solver, standing_loss=standing_loss
                    )
                    lp_lcoe = score_lcoe(lp.design, baseload_demand, costs, profile)
                    logger.info(
                        f"  coverage={coverage} LP (certified, energy) lcoe={lp_lcoe:.2f} "
                        f"design={lp.design} solve_s={lp.solve_seconds:.1f}"
                    )
                    rows.append(
                        _row(
                            site,
                            coverage,
                            "energy",
                            standing_loss,
                            "cyclic",
                            "lp",
                            np.nan,
                            np.nan,
                            np.nan,
                            lp_lcoe,
                            lp.design["battery"],
                            time.time() - t0,
                        )
                    )

                boa_modes = boa_soc_modes if boa_soc_modes is not None else soc_modes
                for soc_mode in soc_modes:
                    logger.info(f"  coverage={coverage} metric={metric} soc_mode={soc_mode}")

                    if soc_mode in boa_modes:
                        for n_samples in sample_sizes:
                            for seed in range(n_seeds):
                                t0 = time.time()
                                candidates = capacity_sampling(profile, coverage_p, n_samples=n_samples, seed=seed)
                                design, lcoe = _best_boa_design(
                                    candidates,
                                    baseload_demand,
                                    costs,
                                    profile,
                                    coverage_p,
                                    soc_mode,
                                    standing_loss,
                                    metric,
                                )
                                rows.append(
                                    _row(
                                        site,
                                        coverage,
                                        metric,
                                        standing_loss,
                                        soc_mode,
                                        "boa",
                                        n_samples,
                                        n_samples,
                                        seed,
                                        lcoe,
                                        design["battery"] if design else np.nan,
                                        time.time() - t0,
                                    )
                                )

                    for n_refinements in refinement_levels:
                        t0 = time.time()
                        try:
                            result = find_gbs_design(
                                profile,
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
                            # Infeasibility is a property of the search box (s_max/w_max) and
                            # coarse grid, not of n_refinements -- it recurs identically for
                            # every remaining refinement level, so log one row and stop instead
                            # of re-discovering the same failure n_refinements more times.
                            logger.warning(
                                f"  coverage={coverage} metric={metric} soc_mode={soc_mode} "
                                f"gbs infeasible at n_refinements={n_refinements}: {exc}"
                            )
                            rows.append(
                                _row(
                                    site,
                                    coverage,
                                    metric,
                                    standing_loss,
                                    soc_mode,
                                    "gbs",
                                    n_refinements,
                                    np.nan,
                                    np.nan,
                                    None,
                                    np.nan,
                                    time.time() - t0,
                                )
                            )
                            break
                        rows.append(
                            _row(
                                site,
                                coverage,
                                metric,
                                standing_loss,
                                soc_mode,
                                "gbs",
                                n_refinements,
                                result.n_evaluations,
                                np.nan,
                                result.lcoe,
                                result.design["battery"],
                                result.search_seconds,
                            )
                        )

    return pd.DataFrame(rows)


def _load_config_defaults(config_path: Path) -> dict:
    """Translate a `--config` YAML file into argparse dest-name -> default-value overrides.

    Scalar keys map straight to the flag of the same name (`n_seeds: 10` -> `--n-seeds`).
    List-valued keys are joined into the same comma-separated string the CLI flag itself
    expects (`coverage_thresholds: [0.99, 0.95]` -> `"0.99,0.95"`), so the comma-split
    parsing in `main()` doesn't need to care whether a value came from YAML or the command
    line. `soc_modes` nests the paired gbs/boa override naturally:
        soc_modes: {gbs: [cyclic, empty_start], boa: [empty_start]}
    -> `soc_modes="cyclic,empty_start"`, `boa_soc_modes="empty_start"`.
    """
    with open(config_path) as f:
        config = yaml.safe_load(f) or {}

    defaults = {}
    soc_modes = config.pop("soc_modes", None)
    if soc_modes is not None:
        if "gbs" in soc_modes:
            defaults["soc_modes"] = ",".join(soc_modes["gbs"])
        if "boa" in soc_modes:
            defaults["boa_soc_modes"] = ",".join(soc_modes["boa"])

    for key, value in config.items():
        defaults[key] = ",".join(str(v) for v in value) if isinstance(value, list) else value
    return defaults


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="YAML file of default values for any flag below -- see this module's docstring "
        "and example_config.yaml. Individual CLI flags still override specific config keys.",
    )
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
    parser.add_argument(
        "--energy-coverage-thresholds",
        type=str,
        default=None,
        help="Coverage thresholds for the 'energy' metric only, overriding --coverage-thresholds for it "
        "(default: same as --coverage-thresholds). 'energy' is mainly a validation device -- its 'lp' row "
        "is the only certified ground truth this benchmark has -- so it's often only worth one threshold "
        "while 'hours' (what BOA's production filter actually enforces) gets the full sweep.",
    )
    parser.add_argument(
        "--metrics", type=str, default="energy,hours", help="Coverage metrics to sweep (subset of energy,hours)."
    )
    parser.add_argument(
        "--soc-modes",
        type=str,
        default="cyclic,empty_start",
        help="SOC dispatch modes to sweep for 'gbs' -- 'boa' is separately controlled by "
        "--boa-soc-modes (subset of cyclic,empty_start).",
    )
    parser.add_argument(
        "--boa-soc-modes",
        type=str,
        default=None,
        help="SOC dispatch modes to run 'boa' for, subset of --soc-modes (default: same as --soc-modes). "
        "Worth restricting to empty_start: BOA's battery sizing doesn't depend on soc_mode at all, so its "
        "cyclic run costs the same as empty_start but adds no signal GBS's own cyclic run doesn't already "
        "give more directly -- see README.md's SOC-mode sensitivity section.",
    )
    parser.add_argument("--sample-sizes", type=str, default="100,300,1000,3000,10000,30000")
    parser.add_argument("--n-seeds", type=int, default=10, help="Random seeds per BOA sample size.")
    parser.add_argument(
        "--gbs-coarse-grid",
        type=int,
        default=21,
        help="Fixed (solar, wind) coarse-grid resolution for the GBS search -- not swept: "
        "it contributes only a few percent of total search work once n_refinements >= 1, so varying "
        "it barely moves the answer or the runtime (see gbs.py's find_gbs_design "
        "docstring). Use --refinement-levels for the actual budget sweep.",
    )
    parser.add_argument(
        "--refinement-levels",
        type=str,
        default="0,1,2,3,4,5",
        help="n_refinements values to sweep for the GBS method's budget/convergence curve -- "
        "this, not coarse-grid resolution, is what actually trades off search cost against accuracy "
        "(each level adds ~n_seeds*refine_grid**2 evaluations; 0 = the coarse grid's own best point, "
        "no local refinement at all).",
    )
    parser.add_argument("--solver", type=str, choices=["highs", "gurobi"], default="highs")
    parser.add_argument(
        "--s-max",
        type=float,
        default=8.0,
        help="Solar overscale search-box upper bound for 'gbs' -- widen for sites where it reports "
        "'No feasible design' or sits on the search-box boundary (e.g. ecuador_colombia_coast, "
        "near-zero wind resource, needs well beyond 8x baseload overscale).",
    )
    parser.add_argument("--w-max", type=float, default=8.0, help="Wind overscale search-box upper bound for 'gbs'.")
    parser.add_argument(
        "--standing-loss",
        type=float,
        default=0.0,
        help="Fraction of stored battery energy lost per hour (0 = no loss, the default).",
    )
    parser.add_argument("--out", type=Path, default=Path("scripts/boa_benchmark/results/methodology_comparison.csv"))

    args, _ = parser.parse_known_args()
    if args.config is not None:
        config_defaults = _load_config_defaults(args.config)
        unknown = set(config_defaults) - vars(args).keys()
        if unknown:
            raise ValueError(f"Unknown --config key(s) {sorted(unknown)} in {args.config}")
        parser.set_defaults(**config_defaults)
    args = parser.parse_args()

    with open(args.sites_file) as f:
        sites = yaml.safe_load(f)

    if args.site_names is not None:
        wanted = set(args.site_names.split(","))
        sites = [s for s in sites if s["name"] in wanted]
        missing = wanted - {s["name"] for s in sites}
        if missing:
            raise ValueError(f"Site name(s) {sorted(missing)} not found in {args.sites_file}")

    df = run_sweep(
        sites=sites,
        data_dir=args.data_dir,
        cache_dir=args.cache_dir,
        flat_costs_csv=args.flat_costs_csv,
        year=args.year,
        baseload_demand=args.baseload_demand,
        coverage_thresholds=[float(x) for x in args.coverage_thresholds.split(",")],
        metrics=args.metrics.split(","),
        soc_modes=args.soc_modes.split(","),
        sample_sizes=[int(x) for x in args.sample_sizes.split(",")],
        n_seeds=args.n_seeds,
        gbs_coarse_grid=args.gbs_coarse_grid,
        refinement_levels=[int(x) for x in args.refinement_levels.split(",")],
        solver=args.solver,
        standing_loss=args.standing_loss,
        energy_coverage_thresholds=(
            [float(x) for x in args.energy_coverage_thresholds.split(",")]
            if args.energy_coverage_thresholds is not None
            else None
        ),
        boa_soc_modes=(args.boa_soc_modes.split(",") if args.boa_soc_modes is not None else None),
        s_max=args.s_max,
        w_max=args.w_max,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False)
    logger.info(f"Wrote {len(df)} rows to {args.out}")


if __name__ == "__main__":
    main()
