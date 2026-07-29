# BOA sampling-vs-optimization benchmark

Standalone benchmark (not part of the main `steelo` pipeline) measuring how close BOA's
production design method — `baseload_optimisation_atlas.boa_logic.capacity_sampling`
(random `(solar, wind)` sampling) plus `estimate_battery_capacity` (a percentile-based
battery-sizing heuristic) — comes to the true lowest-LCOE design meeting a demand-coverage
target, at real sites using real Copernicus weather profiles.

**Headline finding:** BOA's designs come in ~19-21% more expensive (LCOE) than true optimal
at the site/threshold measured so far. Almost all of that gap traces to the battery-sizing
heuristic, not to how well `(solar, wind)` gets sampled — see
["Gap decomposition"](#gap-decomposition-the-battery-heuristic-is-the-culprit) below.

## Setup

A "design" is a triple of overscale factors `(solar, wind, battery)` relative to a fixed
baseload demand. BOA scores a design by simulating its hourly battery dispatch
(`cyclic_soc.py`), checking whether it meets a coverage target, then computing LCOE
(`baseload_optimisation_atlas.boa_cost_calculations.calculate_lcoe_of_re_installation`, via
`design_metrics.score_lcoe`).

Coverage is measured two genuinely different ways (not a strict/relaxed pair of each other
— see the note in `gbs.py`'s module docstring):

- `energy`: caps *total* unserved energy across the year — one continuous constraint.
- `hours`: BOA's actual production metric (`boa_logic.calculate_coverage`) — an hour counts
  as covered only if literally zero demand went unserved that hour; caps how many hours may
  be uncovered.

## Method: Grid-Bisection Search (GBS, `gbs.py`)

To know how far BOA's sampled design is from optimal, we need a trustworthy optimum to
compare against. GBS is that ground truth. Two structural facts about this specific problem
make a direct search work well here, no MILP required:

1. **The objective is monotone and separable.** LCOE's denominator (`sold_elect_ih_all`,
   `use_curtailment=True`) is `baseload_demand * HOURS_IN_YEAR` — independent of the design.
   So LCOE is a positive constant times total cost, and total cost is additive across
   solar/wind/battery and strictly increasing in each.
2. **Feasibility is monotone.** Raising any of solar/wind/battery raises the SOC trajectory
   pointwise (by induction through the clipped dispatch recursion), which can only turn
   uncovered hours into covered ones.

Together: at fixed `(solar, wind)`, the optimal battery is the *smallest feasible* one, so
`b_min(solar, wind)` is a 1-D **bisection** over a monotone predicate, and the whole problem
reduces to a 2-D coarse-to-fine **grid search** over `(solar, wind)` — hence
"Grid-Bisection Search." Every evaluation runs BOA's own exact dispatch and coverage rule —
no linearization, no foresight dispatch, no rescoring mismatch between what was optimized
and what's reported.

**Validation.** GBS isn't a certified solver either, so it's checked against the one metric
where a certified answer exists: `energy`, via a PyPSA LP (`pypsa_model.py`). Running GBS
with that LP's own linear objective reproduced the certified LP optimum to **5e-8
relative** (`--validate`). That calibrates the search machinery on the metric where truth
exists, which is the basis for trusting it on `hours`, where no certified optimum is
tractable at all. `--self-test` separately checks the jitted coverage/dispatch code
bit-for-bit against the unmodified `design_metrics.simulate_design`, and checks
monotonicity (the property the bisection depends on) against 200 random designs.

```bash
uv run python scripts/boa_benchmark/gbs.py --site-name inner_mongolia --self-test
uv run python scripts/boa_benchmark/gbs.py --site-name inner_mongolia --coverage 0.95 --validate
uv run python scripts/boa_benchmark/gbs.py --site-name inner_mongolia --coverage 0.95 --metric hours
```

**A subtlety worth knowing before reading the CSV: "certified" is scoped to the LP's own
objective, not to true LCOE.** The LP's `energy`-metric certification above is for its own
*linear* battery-capex objective (a reference-size proxy — see `pypsa_model.py`'s
docstring), not the true, concave `score_lcoe` that gets reported everywhere else. GBS
optimizes the true objective directly, so it can legitimately find a *lower* true LCOE than
the LP's design rescored through `score_lcoe` — that's the LP's known linearization gap
showing up, not a bug. Observed at Inner Mongolia, p95, `energy`: the LP's rescored design
comes to ~26.1 LCOE; GBS finds ~25.5 (about 2.3% lower), which is why the sweep CSV can show
`gbs` beating `lp`.

### Why not a MILP?

The original ground truth for `hours` coverage was a MILP variant of the same PyPSA model
(one binary `hour_covered[t]` per snapshot) — since removed from `pypsa_model.py`. It didn't
work, for two structural reasons: (1) its LP relaxation collapses exactly to the `energy`
LP's constraint, so its bound was stuck there from node zero — on a real 8760-hour profile
it never certified better than a ~12.5% gap even at a 300s time cap; (2) it dispatches with
full foresight, but greedy dispatch (what BOA and GBS both use) is only provably optimal for
`energy`, not `hours` — so the MILP's designs were sometimes *infeasible* under BOA's own
dispatch, an unattainable "ground truth." GBS avoids both problems by using BOA's exact
dispatch at every evaluation instead of a relaxed/foresight approximation of it.

## Findings

### Gap decomposition: the battery heuristic is the culprit

BOA's design has two independent error sources: how good its `(solar, wind)` samples are,
and how good `estimate_battery_capacity`'s heuristic battery size is. GBS makes it possible
to separate them — take BOA's actual sampled `(solar, wind)` points, but size the battery
exactly via `b_min` instead of the heuristic, and see how much of the gap disappears.

At Inner Mongolia, p95, `hours` metric:

| n_samples | BOA total gap | same (solar, wind), exact battery | → sampling error | → battery-heuristic error |
|---:|---:|---:|---:|---:|
| 300 | +21.3% | +1.3% | +1.3% | +20.0% |
| 3,000 | +19.5% | +0.3% | +0.3% | +19.2% |
| 10,000 | +19.1% | +0.3% | +0.3% | +18.8% |

At the optimal `(solar, wind)` point itself, `estimate_battery_capacity` returns `b=10.99`
against a true minimum of `b_min=4.10` — nearly 3x oversized. Sampling density converges
fast and contributes almost nothing to the total gap past a few thousand samples; the
heuristic battery sizer is responsible for essentially all of it, and more samples cannot
fix that.

**This is one site, one threshold, five seeds** — a documented example, not yet a swept
result. ["Running the benchmark"](#running-the-benchmark) below produces the fuller sweep
needed before treating this as a general finding, but the mechanism (heuristic evaluated at
a single percentile pair vs. an exact per-design bisection) is structural and should hold
broadly.

**Actionable conclusion.** `estimate_battery_capacity` is the biggest culprit behind BOA's
gap to true-optimal LCOE, not sampling density — more/better `(solar, wind)` samples cannot
close it. The lowest-risk production fix is narrower than swapping in all of GBS: keep
BOA's existing `capacity_sampling` candidate generation, and replace only the battery-sizing
step with an exact `b_min` bisection (`gbs._b_min_jit` / `gbs._b_min_grid`, already
implemented and validated here) evaluated per sampled `(solar, wind)` candidate. That
targets the ~19-20pp component directly without changing the sampling/candidate-generation
interface the rest of the codebase depends on. Full GBS (replacing the search itself, not
just the battery step) only adds the remaining ~0.3-1.3pp from sampling error, at
considerably more engineering and runtime cost — see the caveats above on generalizing this
beyond Inner Mongolia before committing to either.

### SOC-mode sensitivity

BOA's production dispatch (`state_of_charge`) always starts the year with an empty battery.
`cyclic_soc.state_of_charge_cyclic` is a periodic alternative (the battery's start-of-year
SOC equals its end-of-year SOC), matching what a battery that's been running for years
actually looks like, and also what PyPSA's `Store(e_cyclic=True)` enforces. Both `boa` and
`gbs` methods can be run under either mode. `plot_soc_mode_sensitivity` isolates how much
this choice alone moves the answer, using the `gbs` method's most-resolved rows so the
effect isn't confounded with sampling or search-resolution noise.

### Weather-year sensitivity: how much does the choice of weather year matter?

BOA's own inputs (and every finding above) come from a single weather year's Copernicus
profile. `run_weather_year_sensitivity.py` checks how much that choice alone moves the
answer, at all 10 sites, using GBS (`hours` metric, `n_refinements=3`) against four weather
years (2010, 2015, 2020, 2025).

For each site, two designs are compared:
- **Per-year optimal** — `find_gbs_design` run separately against each year's profile:
  "what would have been the cheapest design, in hindsight, for that year alone."
- **Robust** — `find_robust_gbs_design` run against all four years at once: "what do you
  actually have to build if you must commit to a design before knowing which year's weather
  will occur." Valid by the same monotonicity argument GBS already relies on: at fixed
  `(solar, wind)`, the smallest battery meeting every year is the *max* of each year's own
  `b_min`, so the elementwise max of the per-year battery grids is still pointwise-optimal
  and the same coarse-to-fine search applies unchanged (see `find_robust_gbs_design`'s
  docstring in `gbs.py`).

**Rejected alternative: don't average a fixed design's LCOE across years.** An earlier idea
was to evaluate one design against each year and average the resulting LCOE. This doesn't
work: `score_lcoe` always calls `calculate_lcoe_of_re_installation` with
`use_curtailment=True`, under which `sold_elect_ih_all` is a hardcoded constant and
`total_costs_all` depends only on installed capacity — LCOE for a *fixed* design does not
depend on the weather profile at all. Averaging it across years would just return the same
number four times. What actually varies across years for a fixed design is *coverage*
(does it still clear the threshold), which is what the robust design directly optimizes
against instead.

**Finding: the robustness premium
(`(robust_lcoe - mean(per_year_lcoe)) / mean(per_year_lcoe)`) ranges from +2.5% to +21.5%
across sites** (coverage_threshold=0.95, `n_refinements=3`):

| site | mean per-year LCOE | LCOE spread across years | robust LCOE | robustness premium |
|---|---:|---:|---:|---:|
| patagonia_chile | 33.6 | 35.5% | 40.8 | +21.5% |
| n_adriatic | 99.6 | 18.0% | 110.9 | +11.3% |
| wyoming_usa | 61.4 | 16.2% | 67.3 | +9.6% |
| wa_gascoyne_coast | 55.1 | 21.0% | 60.0 | +9.0% |
| inner_mongolia | 32.9 | 10.1% | 34.7 | +5.4% |
| sahara_libya_egypt | 62.2 | 9.1% | 65.2 | +4.8% |
| namibia_kunene | 99.3 | 6.1% | 102.2 | +2.9% |
| atacama_desert | 74.1 | 4.9% | 76.1 | +2.6% |
| iran_desert | 79.3 | 7.0% | 81.3 | +2.5% |

At 8 of the 9 feasible sites, the robust design equals (to grid resolution) the single worst
year's own optimal design — the premium there is purely "you have to build for your worst
historical year, and that year alone is meaningfully worse than average." **`inner_mongolia`
is the exception**: its robust LCOE (34.66) is *higher* than even its own worst individual
year's optimum (34.54, 2020) — no single year's design satisfies all four years at once, so
the robust design has to compromise on `(solar, wind)` in a way that's suboptimal for every
individual year. This is only possible because different years can bind on genuinely
different points of the design space, not just different severities of the same point.

`ecuador_colombia_coast` is infeasible for all four years in the default `[0,8]x[0,8]`
search box under `hours`/p95/`empty_start` (consistent with the box-too-small finding
already noted for `energy`/cyclic at the same site) — excluded from the table above, shown
as an "x" on the map rather than silently dropped.

**Caveat.** Four snapshot years (2010, 2015, 2020, 2025), not a full climatology — a
genuinely anomalous year outside this sample (e.g. a multi-decade-rare low-wind year) would
not show up here. This is enough to establish that weather-year choice is *not* a negligible
effect at several sites, not a substitute for a longer reanalysis record if this becomes a
production design input.

## Running the benchmark

`run_methodology_comparison.py` sweeps sites x coverage thresholds x coverage metrics x SOC
modes, producing one long-format CSV with three `method` values per combination: `boa`
(sampling), `gbs` (the grid search), and `lp` (the PyPSA LP — `energy` metric only,
`soc_mode="cyclic"` only, since that's the only combination its dispatch-equivalence
certification applies to; its `lcoe` column is its design rescored through the true
objective, and per the note above `gbs` can legitimately beat it).

```bash
uv run python scripts/boa_benchmark/run_methodology_comparison.py \
    --site-names inner_mongolia --coverage-thresholds 0.95 --metrics energy,hours
```

The `gbs` method's budget knob is `--refinement-levels` (n_refinements), **not**
`--gbs-coarse-grid`: total search work is `coarse_grid**2 + n_refinements * n_seeds *
refine_grid**2`, and refinement dominates once `n_refinements >= 1` (441 to 11,466
evaluations over levels 0-5 at the defaults, LCOE visibly converging over that range) —
sweeping `coarse_grid` alone barely moves total work or the answer. See
`find_gbs_design`'s docstring in `gbs.py` for the full breakdown.

Then `plot_benchmark.py` reads that CSV and produces:
- `site_map.png` / `site_lcoe_by_year.png`: global overview of the LP-optimal design's LCOE
  across sites (reads `method == "lp"` rows).
- `convergence/{site}_{metric}.png`: `boa` vs `gbs` LCOE and runtime vs. `n_evaluations`
  (the fair "work" axis — a `gbs` evaluation costs ~20x a `boa` evaluation, since `b_min`
  needs a bisection where BOA's heuristic is O(1)), all coverage thresholds overlaid in one
  figure (color = threshold, `gbs` a darker shade of `boa`'s color at the same threshold,
  linestyle/marker = method), fixed to `soc_mode == "empty_start"` (see the SOC-mode
  sensitivity plots for the cyclic comparison). For `metric == "energy"` this includes a
  dashed `lp` line per threshold, labeled "certified for linear proxy" to flag the nuance
  above; for `metric == "hours"` it doesn't, since no ground truth exists.
- `soc_sensitivity/{coverage}_{metric}.png`: the empty-start-vs-cyclic comparison above, one
  bar per site.

```bash
uv run python scripts/boa_benchmark/plot_benchmark.py --csv scripts/boa_benchmark/results/methodology_comparison.csv
```

**Note on SOC-mode cost.** Cyclic dispatch needs an extra bisection
(`cyclic_soc.state_of_charge_cyclic`) that empty-start skips. For `boa` this is negligible
(~1-2% runtime difference, dominated by `score_lcoe`'s Python-level LCOE calculation); for
`gbs`, where every evaluation is fully jitted with no such overhead to hide behind, cyclic
is genuinely **~6x** slower than empty-start (measured: 4.4s vs. 26.6s for the same search).
Sweeping both modes costs little on the `boa` side and a real but bounded amount on the
`gbs` side; `gbs` is the cheaper method overall regardless.

## Weather-year sensitivity sweep

```bash
uv run python scripts/boa_benchmark/run_weather_year_sensitivity.py \
    --years 2010,2015,2020,2025 --coverage-threshold 0.95 --n-refinements 3
uv run python scripts/boa_benchmark/plot_weather_year_sensitivity.py \
    --csv scripts/boa_benchmark/results/weather_year_sensitivity.csv
```

Requires `preprocess_copernicus.py --year {year}` to have already been run for each year.
Produces `weather_year_sensitivity.csv` (`method` in `{"gbs", "gbs_robust"}`, one row per
site x year plus one robust row per site) and two plots: `weather_year_map.png` (world map
colored by robustness premium) and `weather_year_spread.png` (per-site small multiples:
each year's own optimum as a point, the robust design as a dashed line).

## Future work

`run_methodology_comparison.py`'s CLI has grown to ~18 flags as sweep scoping got more
granular (`--energy-coverage-thresholds`, `--boa-soc-modes`, etc.). Worth revisiting once
the flag set stabilizes: an optional `--config path.yaml` that loads these as defaults
(nesting the paired overrides naturally, e.g. `soc_modes: {gbs: [...], boa: [...]}`),
with individual CLI flags still able to override specific keys for one-off tweaks. Not done
yet since the sweep's shape is still changing run to run.
