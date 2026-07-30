# BOA sampling-vs-optimization benchmark

Standalone benchmark (not part of the main `steelo` pipeline) measuring how close BOA's
production design method — `baseload_optimisation_atlas.boa_logic.capacity_sampling`
(random `(solar, wind)` sampling) plus `estimate_battery_capacity` (a percentile-based
battery-sizing heuristic) — comes to the true lowest-LCOE design meeting a demand-coverage
target, at real sites using real Copernicus weather profiles.

**Headline finding:** BOA's designs come in ~19-21% more expensive (LCOE) than true optimal
at the site/threshold measured so far, and the gap is driven almost entirely by the
battery-sizing heuristic overestimating, not by how well `(solar, wind)` gets sampled.
See [OVERVIEW.md](OVERVIEW.md) for the full methodology, validation, and all findings
(including weather-year sensitivity across all 10 sites).

## Setup

A "design" is a triple of overscale factors `(solar, wind, battery)` relative to a fixed
baseload demand, scored by simulating hourly battery dispatch and computing LCOE. Coverage
is measured two ways — `energy` (caps total unserved energy across the year) and `hours`
(BOA's actual production metric: an hour counts as covered only if zero demand went
unserved that hour) — see [OVERVIEW.md](OVERVIEW.md#setup) for why these aren't a
strict/relaxed pair of each other.

## Running the benchmark

`runners/run_methodology_comparison.py` sweeps sites x coverage thresholds x coverage metrics x SOC
modes, producing one long-format CSV with three `method` values per combination: `boa`
(sampling), `gbs` (the grid search), and `lp` (the PyPSA LP — `energy` metric only,
`soc_mode="cyclic"` only, since that's the only combination its dispatch-equivalence
certification applies to; its `lcoe` column is its design rescored through the true
objective, and per the note in OVERVIEW.md `gbs` can legitimately beat it).

```bash
uv run python -m scripts.boa_benchmark.runners.run_methodology_comparison \
    --site-names inner_mongolia --coverage-thresholds 0.95 --metrics energy,hours
```

The CLI has ~18 flags for sweep scoping; `--config path.yaml` loads them as defaults instead
(individual CLI flags still override specific keys) — see `example_config.yaml`.

> [!NOTE]
> A boundary warning can mean the answer is badly wrong, not just imprecise. GBS's default
> search box (`--s-max 8 --w-max 8`) is wide enough for every site here except
> `ecuador_colombia_coast` (near-zero wind resource), where rerunning with a wider box didn't
> just refine the answer, it roughly halved its LCOE.

The `gbs` method's budget knob is `--refinement-levels` (n_refinements), **not**
`--gbs-coarse-grid`: refinement dominates total search work once `n_refinements >= 1`, so
sweeping `coarse_grid` alone barely moves total work or the answer. See `find_gbs_design`'s
docstring in `core/gbs.py` for the full cost breakdown.

Then `plotting/plot_benchmark.py` reads that CSV and produces:
- `site_map.png` / `site_lcoe_by_year.png`: global overview of the LP-optimal design's LCOE
  across sites (`site_lcoe_by_year.png` is skipped if the CSV only has one weather year, the
  default for the sweep above).
- `convergence/{site}_{metric}.png`: `boa` vs `gbs` LCOE and runtime vs. `n_evaluations`,
  all coverage thresholds overlaid, fixed to `soc_mode == "empty_start"`.
- `soc_sensitivity/{coverage}_{metric}.png`: empty-start-vs-cyclic comparison (see
  [OVERVIEW.md](OVERVIEW.md#soc-mode-sensitivity)), one bar per site.

```bash
uv run python -m scripts.boa_benchmark.plotting.plot_benchmark --csv scripts/boa_benchmark/results/methodology_comparison.csv
```

## Weather-year sensitivity sweep

```bash
uv run python -m scripts.boa_benchmark.runners.run_weather_year_sensitivity \
    --years 2010,2015,2020,2025 --coverage-threshold 0.95 --n-refinements 3 \
    --s-max 20 --w-max 20
uv run python -m scripts.boa_benchmark.plotting.plot_weather_year_sensitivity \
    --csv scripts/boa_benchmark/results/weather_year_sensitivity.csv
```

`--s-max`/`--w-max` widen the `(solar, wind)` search box above the `find_gbs_design` default
of 8x baseload overscale -- needed for `ecuador_colombia_coast` (near-zero wind resource) to
be feasible at any threshold; 20x covers p90 through p99 for all 10 sites.

Requires `preprocessing/preprocess_copernicus.py --year {year}` to have already been run for
each year. Produces one CSV per coverage threshold (`weather_year_sensitivity.csv` for p95,
`_p90`/`_p99` suffixed variants otherwise; `method` in `{"gbs", "gbs_robust"}`, one row per
site x year plus one robust row per site) and two plots per CSV:
- `weather_year_map_{coverage_threshold}.png` -- mean per-year LCOE by site, plus each
  weather year's premium over that site's own cheapest year (see OVERVIEW.md for a labeled
  example).
- `weather_year_spread_{coverage_threshold}.png` -- per-site LCOE range across the 4 weather
  years, sites sorted by median.

`--metric energy --include-lp` additionally validates the `gbs` rows against the certified
PyPSA LP per weather year (see
[OVERVIEW.md](OVERVIEW.md#validating-the-sensitivity-against-a-certified-ground-truth)) --
`soc_mode` should be set to `cyclic` to match the LP's own assumption:

```bash
uv run python -m scripts.boa_benchmark.runners.run_weather_year_sensitivity \
    --years 2010,2015,2020,2025 --coverage-threshold 0.95 --metric energy \
    --soc-mode cyclic --include-lp --s-max 20 --w-max 20 \
    --out scripts/boa_benchmark/results/weather_year_sensitivity_energy_lp_p95.csv
```

See [OVERVIEW.md](OVERVIEW.md#weather-year-sensitivity-how-much-does-the-choice-of-weather-year-matter)
for the results (robustness premium ranges +1.6% to +21.5% across sites and thresholds).
