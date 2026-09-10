# Capacity-Replacement Policy: Technical Reference

Companion to the [China Capacity-Replacement Policy](capacity_replacement_policy.md) page, which explains what the policy does and why. This page is the reference for authoring the input sheets, running the policy and working on the code: the module layout and binding lifecycle, the three master-Excel sheets column by column with their validation rules, how routes are classified and ratios resolved at run time, the pool's arithmetic and units, the sequence of events within a year, and the run artefacts with their reconciliation contract. The artefact file formats themselves are listed in [Outputs and Post-Processing](outputs_and_postprocessing.md#fleet-motions-and-capacity-policy-artefacts).

## Module layout

| Module | Role |
|---|---|
| `steelo/capacity_policy/config.py` | `CapacityPolicyConfig`, nested on `SimulationConfig.capacity_policy`; validates its own values |
| `steelo/capacity_policy/inputs.py` | Row types for the three sheets (`RegionRow`, `TechnologyRow`, `OpeningCreditRow`), the swap-ratio precedence (`resolve_swap_ratio`) and the effective ratio grid |
| `steelo/capacity_policy/validation.py` | Cross-row validation, shared by data preparation and bootstrap |
| `steelo/capacity_policy/pool.py` | `CapacityPool`, `Credit`, `SeedEntry`, `WithdrawResult` |
| `steelo/capacity_policy/tree.py` | `TreeEvaluator`: the decision tree as pure logic over the fixture rows and the config; `WithdrawSpec` |
| `steelo/capacity_policy/handlers.py` | The module-level binding, the hook accessors and their adapters, the deposit, attribution, refund and year-boundary handlers, and the motion handlers |
| `steelo/capacity_policy/bootstrap.py` | `configure_capacity_policy`: per-run activation |
| `steelo/capacity_policy/recorder.py` | `CapacityPolicyRecorder`: the four CSVs and their vocabularies |
| `steelo/capacity_policy/plotter.py` | `CapacityPoolPlotter`: the `plots/capacity_pool/` charts |
| `steelo/motions.py` | The always-bound global motions recorder behind `data/pam_motions.csv` |

Outside the package, the feature uses the standard data pipeline (the sheet readers in `excel_reader.py`, the recreation functions, the fixture repositories in `json_repository.py`, `DataPathResolver`, and the master-Excel validator's geo-key check), two `SimulationConfig` fields (`capacity_policy` and `policy_output_dir`), the two CLI flags, and the domain decision paths, which accept the hooks as optional parameters defaulting to `None`. The policy module never imports the domain's decision code and the domain never imports the policy: the adapters in `handlers.py` translate the domain's plain values onto the tree, and all applicability (China only) is decided there.

## Binding lifecycle

1. `bootstrap_simulation` calls `configure_capacity_policy(config.capacity_policy, repository_json, start_year=…)` once per run. It **always unbinds first**: the binding is module-level state, and a disabled run must clear whatever an earlier enabled run in the same process left behind (web app, test suites).
2. With `enabled=False` it returns. Every hook accessor keeps returning `None`, every handler keeps returning immediately, and the run is byte-identical to a build without the package.
3. With `enabled=True` it refuses, naming the culprit, when any of the three fixtures is missing, when the provinces or technologies fixture is empty, or when the cross-row validation finds any issue, warnings included. An empty opening-credits fixture is accepted as a zero-pool start.
4. It warns when the geo-unit reference data (admin-1 shapefile and geo hierarchy) is absent, since greenfield sites would then resolve to the bare `CHN` key and the whole greenfield side would run region-blind, and it warns for every opening-credit `plant_group_id` that matches no plant group.
5. It builds a fresh recorder, evaluator and pool, seeds the pool from the opening credits (vintages after the start year are clamped down to it with a warning), writes one `seed` ledger row per entry, purges credits already past their shelf life and, when the start year is at or past the swap cutoff, the unowned ones, then binds.
6. The global motions recorder is bound on every run, enabled or not.

### How the hooks reach the decision paths

The plant agent reads each accessor once per stage and threads the result into the domain method as a keyword argument.

| Accessor in `handlers.py` | Parameter | Consumed in |
|---|---|---|
| `replace_capacity_hook()` | `permitted_replace_capacity` | `Plant.evaluate_furnace_group_strategy` |
| `increase_sizing_hook()` | `increase_sizing_query` | `PlantGroup.evaluate_expansion_options` and `evaluate_expansion`; `PlantGroup.identify_new_business_opportunities_4indi` via `calculate_business_opportunity_npvs`; `FurnaceGroup.track_business_opportunities` |
| `expansion_capacity_hook()` | `permitted_expansion_capacity` | `PlantGroup.evaluate_expansion` |
| `greenfield_feasibility_hook()` | `greenfield_feasibility_probe` | `FurnaceGroup.track_business_opportunities` |
| `greenfield_capacity_hook()` | `permitted_greenfield_capacity` | `FurnaceGroup.track_business_opportunities` |
| `greenfield_retry_cap()` | `capacity_pool_max_retry_years` | `FurnaceGroup.track_business_opportunities` |

The event handlers are registered unconditionally in `EVENT_HANDLERS` and stay inert while unbound:

| Event | Policy handlers, in order |
|---|---|
| `FurnaceGroupClosed` | `deposit_on_furnace_group_closed`, `record_motion_on_furnace_group_closed` |
| `FurnaceGroupTechChanged` | `deposit_on_furnace_group_tech_changed`, `record_motion_on_furnace_group_tech_changed` |
| `FurnaceGroupRenovated` | `deposit_on_furnace_group_renovated`, `record_motion_on_furnace_group_renovated` |
| `FurnaceGroupAdded` | `attribute_greenfield_on_furnace_group_added`, then `record_motion_on_furnace_group_added`, so a credit-funded greenfield records its new owner |
| `IterationOver` | `snapshot_pool_state` before `finalise_iteration`, `purge_expired_credits` after it |

Four things happen through direct calls because no event exists at those points: `deposit_on_end_of_life_closure` from `finalise_iteration`, `refund_greenfield_on_discard` from the announced → discarded branch of the status handler, `record_motion_on_pipeline_group_operating` at the construction → operating flip in the simulation loop, and the two flushes (`flush_capacity_policy_outputs`, `flush_global_motions`) at the end of the run.

## Sequence within a year

1. **Trade allocation.** `update_furnace_utilization_rates` records every active group's utilisation under the year; this is the history the ② utilisation gate reads.
2. **Plant agent model.** Per furnace group, the strategy evaluation runs with the ② hook; then, per plant group, the expansion evaluation runs with the ③ sizing query in every candidate NPV and the consuming expansion gate at commitment.
3. **Geospatial model.** Existing opportunities are advanced first (pre-draw feasibility probe, announcement draw, consuming greenfield gate, construction transition), then new opportunities are identified with the ③ sizing query in their NPVs.
4. **Year boundary** (`IterationOver`). The pool is snapshotted for the year; `finalise_iteration` increments the year, executes the scheduled switches (their shrink deposits carry the new year's vintage) and closes expired groups (end-of-life deposits, same vintage); then the boundary purge removes credits whose shelf life ran out and, on entering the cutoff year, the unowned opening credits.

Consequences: a credit deposited at a year boundary is spendable from the next year's first decision; the year-`Y` snapshot never shows a credit that died on entering `Y + 1`; and the final boundary increments by zero, so the recorder re-snapshots the last year at flush time to keep the invariant below true.

## The input sheets

Three optional sheets in the master Excel, prefixed `Capacity pool - `. Column by column — the allowed values, what the model does with each, the rules enforced on them and the parameters that interact — they are documented in the [Master Input Reference](../user_guide/master_input_reference.md#china-capacity-replacement-policy-sheets). This section covers the pipeline behind them.

**Fixtures.** Absence means no fixture is written (a *missing* fixture stays distinguishable from an *empty* one, and only the opening credits may be empty on an enabled run). Data preparation writes `fixtures/capacity_pool_provinces.json`, `fixtures/capacity_pool_technologies.json` and `fixtures/capacity_pool_opening_credits.json`, plus the generated diagnostic `fixtures/capacity_pool_ratio_grid.csv`. The fixtures are recreated on every preparation whether or not a run will enable the policy, so authoring errors fail every preparation; unauthored flags only matter to an enabled run.

**Two layers check a present sheet.** The reader checks structure: a present sheet missing a required column is an error, fully blank rows are dropped, and any extra column is ignored, so free-text provenance columns and authoring helpers never reach the fixtures. Cross-row validation checks meaning: errors are authoring mistakes and fail preparation, listing every error found; warnings are content gaps, printed as such while the fixtures still build. Bootstrap re-runs the same checks with warnings promoted to errors, so a run with the policy enabled refuses on any of them. Names are checked against the model's vocabularies: technologies against the `Technology` column of `Techno-economic details`, reductants against the `Reductant` column of `Bill of Materials` (matched up to name normalisation), and geo keys against the model's Chinese first-order units. The master-Excel validator additionally runs its standard geo-key check over the `geo_key` columns of the provinces and opening-credits sheets.

**The effective ratio grid** (`capacity_pool_ratio_grid.csv`) is written beside the technologies fixture. Its axes are the classification keys as authored (`BF`, or `DRI|Coal` for a reductant-specific row), old routes on the rows and new routes on the columns; each cell holds the resolved ratio or `unauthored`. It is generated from the same precedence and derivation the evaluator uses, so it cannot disagree with the rules, and it is the at-a-glance view to check after editing the sheet.

### Worked examples from the authored master

**Provinces.** The authored sheet lists all 31 units: eleven key provinces in three clusters, two exempt provinces and eighteen non-key ones. The key-region definition is the policy's city-cluster list projected onto provinces: any listed city promotes its whole province, and no province has cities in two clusters, so one cluster per province is a property of the source rather than a constraint the model imposes.

| Type | Cluster | Provinces |
|---|---|---|
| `key` | Jing-Jin-Ji | Beijing, Tianjin, Hebei, Shandong, Henan |
| `key` | Yangtze River Delta | Shanghai, Jiangsu, Zhejiang, Anhui |
| `key` | Fenwei Plain | Shanxi, Shaanxi |
| `exempt` | — | Qinghai, Tibet |
| blank | — | Chongqing, Fujian, Gansu, Guangdong, Guangxi, Guizhou, Hainan, Heilongjiang, Hubei, Hunan, Inner Mongolia, Jiangxi, Jilin, Liaoning, Ningxia, Sichuan, Xinjiang, Yunnan |

**Technologies.** 31 classification rows, no override rows. Six routes are emission-intense: `BF`, `BF_CHARCOAL`, `BOF`, `SR`, `DRI` on coal and `DRI+ESF` on coal. Every capture variant (`+CCS`, `+CCU`) and every non-coal DRI variant is `FALSE`, as are `EAF`, `MOE` and `E-WIN`. The DRI family is classified per reductant (`Coal`, `Natural gas`, `Hydrogen`); everything else as a whole.

**Opening credits.** The sheet holds the pool's opening *state*, not its history: the regime predates any simulation window, and raw retirements would count already-spent credit as unspent. A sheet with no owners named (a plausible authoring, since retirement statistics rarely name the company) yields a pool that is fully drawable until the cutoff and empty from it, unless the `persist` rule is chosen.

## Classification at run time

`TreeEvaluator` resolves a route's classification row most-specific first: the `(technology, reductant)` row, else the technology's blank-reductant row. Reductants are matched up to name normalisation. Two edge cases are deliberate:

- **A reductant-split technology asked about with no reductant** (a switch candidate for which the model has no reductant hypothesis yet, typically because no plant in the fleet runs it) resolves to the *conservative* classification over its authored reductant rows: emission-intense if any variant is. That never grants the favourable 1:1 to an unresolved route, never blocks it either, corrects itself once the route resolves to a real reductant, and is flagged in the gate-decisions CSV (`old_used_conservative_fallback`, `new_used_conservative_fallback`) and logged as `decision=conservative_fallback`.
- **A named reductant with no row, or a route with no row at all, is an error**, as is a conservative fallback that would rest on an unauthored flag: sheet gaps are not runtime unknowns.

Since the sizing query runs on every Chinese candidate at valuation time, a classification gap that used to be latent surfaces on the first Chinese plant that considers the route. Every technology China may build therefore needs an authored row before an enabled run.

For the ② REPLACE gate, the new side is classified by the candidate's own operating-start reductant pick, the same series the CO2 gate builds at the same year anchor, and the incumbent by the group's current reductant. The utilisation gate runs first: it blocks when the most recent window of `utilization_window_years` consecutive recorded years, anchored at the latest recorded year at or before the decision year, is fully recorded and every year sits at or below `min_utilization_for_renovation`. Missing, short or gappy history never blocks.

## Pool arithmetic and units

A `Credit` carries `amount_mt`, `vintage_year`, `region_tag` (cluster name or `None`), `owner_id` (plant group by membership, or `None` for an unowned opening credit) and `product`. The pool is an age-ordered queue: deposits arrive in simulation-year order, seeds are inserted in vintage order, and a refund is re-inserted after every credit of the same or older vintage so it resumes the FIFO position it left.

**Applicable pool.** A credit is applicable to a withdrawal when it passes all of:

1. region: `region_tag` requested ⇒ the credit carries exactly that tag; no tag requested ⇒ any credit;
2. product: exact match;
3. owner, only from the cutoff year: a pre-cutoff vintage follows `banked_credit_rule`; an unowned credit is refused; otherwise the credit's owner must be the withdrawer (for single-holder withdrawals the owner restriction is enforced by holder selection instead, see below).

| `banked_credit_rule` | Pre-cutoff credits from the cutoff on |
|---|---|
| `reassign` (default) | Stay with their depositor like any other credit; unowned ones are swept at the boundary |
| `persist` | Freely spendable by anyone, unowned included; only later deposits partition by owner |
| `expire` | Unusable by everyone; only post-cutoff deposits are spendable, each by its depositor. This is a withdrawal-time rule, not the shelf life; the credits stay in the queue |

**Withdrawals** are all-or-nothing against the applicable sum, with a relative tolerance of 1e-9 on the sufficiency check, and consume oldest first; a partially consumed credit splits and the remainder keeps its vintage, absorbing float dust rather than leaving sliver credits. A **single-holder** withdrawal (greenfield) is served entirely by one holder: the one whose oldest applicable credit sits earliest in the queue among the holders whose applicable credits cover the amount; the unowned pot counts as one holder, and holders are never mixed. `WithdrawResult.blocked_reason` is `insufficient_applicable_pool` when the applicable sum is short and `no_single_owner_with_sufficient_credits` when the pool is ample but no one holder covers the build. `can_withdraw` applies the same rules without consuming anything and backs the pre-draw greenfield probe.

**Purges** run on entering a year: `purge_expired` removes credits with `vintage_year + credit_validity_years <= year` (the shelf life applies to unowned credits too, since age is not ownership); `purge_unowned` removes the unowned credits once the year reaches the cutoff, except under `persist`.

**Units.** The sheets author megatonnes; the model works in tonnes. Seeding multiplies by 10⁶ and logs both numbers on one line. Runtime deposits and withdrawals flow in model tonnes end to end, so the pool's `amount_mt` parameter and field names carry tonnes at run time; the ledger and state CSVs name their columns `amount_t` and `remaining_t` accordingly.

## Artefacts and the reconciliation contract

The recorder appends a row beside every `[CAPACITY POOL]` log line that states the same fact, so files and log can be cross-checked line for line. The four CSVs and the global motions file are described in [Outputs and Post-Processing](outputs_and_postprocessing.md#fleet-motions-and-capacity-policy-artefacts); what follows is the contract behind them.

**Ledger operations**

| Operation | Meaning | In the reconciliation sum |
|---|---|---|
| `seed` | An opening credit, stamped with its vintage year | + |
| `deposit_close` | Branch ① deposit from an agent-decided closure | + |
| `deposit_close_end_of_life` | Branch ① deposit from an end-of-life closure in `finalise_iteration` | + |
| `deposit_replace` | Branch ② delta deposit from a switch or renovation that shrank | + |
| `withdraw_expansion`, `withdraw_greenfield` | Granted ③ withdrawals; `credits_consumed` lists the consumed slices as `[owner_id, vintage_year, region_tag, amount_t]` | − |
| `expired`, `expired_unowned` | Boundary purges, one row per credit | − |
| `refunded` | Slices handed back by a discarded announced greenfield, at their original vintage | + |
| `blocked_expansion`, `blocked_greenfield` | Refusals, with `blocked_reason`; the greenfield rows come from the pre-draw probe and from the consuming gate | outside |
| `greenfield_discard` | The discard fact itself, annotating a withdrawal that has been refunded | outside |

**Invariant.** For every recorded year `Y`, the state file's total equals `seed + deposits − consumed − expired + refunded` over the ledger rows stamped up to `Y`. Seed rows are stamped with their vintage so every seed is inside the window; end-of-life and scheduled-switch deposits are stamped with the post-increment year and therefore belong to the next snapshot; the last year is re-snapshotted at flush time.

**Gate decisions** hold one row per ② evaluation, switch candidates and renovations alike, with `decision` either `ratio` (and the resolved `ratio` and `permitted_t`) or `blocked_utilization`, plus the two conservative-fallback flags. Joining a switch's `new_reductant` here against the `reductant` on its later `switch` motion row measures reductant drift.

**Motions** stamp the year the motion took effect: closures and switches at execution, expansions at the decision year (the command executes in-year), greenfields at construction start, pipeline arrivals at their first operating year. `source` is `pam` for a model decision and `input_data` for capacity the input dataset scheduled: pipeline arrivals, and end-of-life closures of groups delivered with their retirement already on the clock and never touched by a build, switch or in-run renovation. The China file is the `CHN` slice of the global file.

## Logging

All policy lines carry the `[CAPACITY POOL]` prefix at INFO: the resolved config at bootstrap, the seeding line with both units, `policy bound`, one `evaluation=…` line per ② evaluation and per conservative-fallback classification, one `gate=… decision=…` line per ③ withdrawal, probe and refusal (with `reason=` on blocks and `attributed_owner=` on greenfield grants), the boundary purges, and the flush line with row counts. Two warnings matter for data quality: the region-blind warning at bootstrap when the geo-unit reference data is missing, and the bare-`CHN` warning the first time a Chinese location without a `geo_unit` reaches a gate or deposit (later occurrences log at DEBUG per context). The global motions flush logs `[MOTIONS]`. The per-candidate reductant-pick computation in the ② adapter logs its count and timing at DEBUG.

## Tests

The package is covered at three levels: unit tests per module in `tests/unit/capacity_policy/` (pool, tree, handlers, bootstrap, recorder, plotter, config) and for the data layer in `tests/unit/test_capacity_pool_readers.py`, `test_capacity_pool_recreation.py` and `test_capacity_pool_validation.py`; integration tests driving the real decision paths with the hooks bound in `tests/integration/test_furnace_strategy_capacity_policy.py`, `test_expansion_capacity_policy.py` and `test_greenfield_capacity_policy.py`; and end-to-end tests pinning the CLI flags onto the nested config. The inertness of a disabled run is pinned by tests that assert every accessor returns `None` while unbound and that bootstrapping through the real entry point without the fixtures refuses an enabled run and binds nothing on a disabled one.
