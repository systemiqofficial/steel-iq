# Trade LP Model - High-Level Overview

## Purpose

The `TradeLPModel` class (`trade_lp_modelling.py`) is the core linear programming optimization engine that solves for cost-minimal material flows in the global steel value chain. It represents the network of suppliers, production facilities, and demand centers, then determines how materials should flow to meet demand at minimum cost.

## Core Concept

The model is a **network flow optimization problem**:
- **Nodes**: Process centers (suppliers, furnaces, demand regions) with capacities
- **Arcs**: Possible material flows with associated costs
- **Objective**: Minimize total cost (production + transport + tariffs + energy)
- **Subject to**: Capacity limits, material balance, demand fulfillment, trade policy

## Key Classes

### 1. `Commodity`

**Purpose:** Represents a tradeable material (steel, iron ore, scrap, etc.)

**What it models:**
- Material name (normalized to lowercase)
- Can be products, semi-finished products, or raw materials

**Usage:** Commodities are the "what" that flows through the network.

---

### 2. `Process`

**Purpose:** Defines a technology type or transformation process

**What it models:**
- Technology name (e.g., "BF", "EAF", "iron_ore_supply")
- Process type (PRODUCTION, SUPPLY, or DEMAND)
- Bill of materials (possible input-output combinations)

**Key insight:** Multiple facilities can share the same Process definition. For example, all blast furnaces use the same "BF" process, but each is a separate ProcessCenter.

---

### 3. `ProcessCenter`

**Purpose:** Represents a specific facility or location in the network

**What it models:**
- Unique identifier (furnace group ID, supplier ID, demand center ID)
- Which Process it uses
- Production capacity (tons/year)
- Geographic location (for distance-based costs)
- Production cost (typically carbon cost per ton)
- Optional soft minimum utilization target

**Key insight:** ProcessCenters are the nodes in the optimization network. The LP solver decides how much each produces/supplies and where materials flow.

---

### 4. `BOMElement` (Bill of Materials Element)

**Purpose:** Defines one input-output relationship in a production process

**What it models:**
- Input commodity (e.g., iron ore)
- Output commodities (e.g., hot metal, slag)
- Input ratio (tons input per ton output)
- Min/max share constraints (for flexible feedstocks)
- Dependent commodities (secondary inputs like flux)
- Energy cost per ton of input

**Example:** An EAF might have multiple BOM elements:
- BOM 1: Scrap → Steel (80-100% share allowed)
- BOM 2: DRI → Steel (0-20% share allowed)

---

### 5. `ProcessConnector`

**Purpose:** Defines which technology-to-technology flows are allowed

**What it models:**
- From process type → to process type connections
- Determines which allocation variables are created

**Example:** A connector "DRI → EAF" would allow iron from DRI furnaces to flow to electric arc furnaces.

---

### 6. `TransportationCost`

**Purpose:** Location-specific transport costs between countries

**What it models:**
- From country → to country → commodity-specific costs
- Cost per ton of material moved

**Usage:** Replaces older global "cost per km" with more realistic country-pair costs based on trade data.

---

### 7. `Allocations`

**Purpose:** Stores the optimization results

**What it contains:**
- Dictionary mapping (from, to, commodity) → flow quantity
- Dictionary mapping (from, to, commodity) → total cost
- Methods to query flows and validate capacity constraints

**Usage:** After solving, this object holds the optimal material routing.

---

## Main TradeLPModel Workflow

### Phase 1: Model Construction

**Method:** `build_lp_model()`

**What it does:**
1. **Determine legal allocations** - Which flows are physically/technologically possible
2. **Create decision variables** - One per legal allocation, plus slack variables
3. **Add parameters** - Capacities, costs, ratios, etc.
4. **Build constraints** - Capacity, material balance, demand, ratios, tariffs
5. **Define objective** - Minimize sum of (flow × cost) + penalties

**Why this order matters:** Parameters must exist before constraints can reference them.

---

### Phase 2: Optimization

**Method:** `solve_lp_model()`

**What it does:**
1. Invokes HiGHS solver (fast open-source LP solver)
2. Uses interior point method with crossover for speed and integrality
3. Checks termination condition (optimal, infeasible, etc.)
4. Loads solution if optimal, logs diagnostics if infeasible

**Solver configuration:**
- Fixed random seed (1337) for reproducibility
- Presolve enabled to reduce problem size
- Scaling enabled for numerical stability
- Crossover enabled to avoid fractional solutions

**Returns:** Solver result object with termination condition and status

---

### Phase 3: Solution Extraction

**Method:** `extract_solution()`

**What it does:**
1. Reads optimal values from Pyomo variables
2. Filters out negligible flows (< lp_epsilon)
3. Creates Allocations object mapping flows to business objects
4. Sets optimal_production on each ProcessCenter

**Important:** Only extracts if solver status is "ok" (optimal solution found)

---

## Key Constraints Explained

### 1. Production Capacity Constraint

**Purpose:** No facility can exceed its maximum throughput

**Formulation:**
```
sum(outgoing flows from facility) ≤ capacity
```

**Applied to:** All PRODUCTION and SUPPLY process centers

---

### 2. Material Balance (BOM) Constraint

**Purpose:** Inputs must match outputs according to technology recipes, handling alternate inputs and multiple outputs

**The Challenge:**
Technologies can have flexible input-output combinations:
- **Multiple inputs** producing the same output (e.g., EAF can use scrap OR DRI to make steel)
- **Multiple alternate outputs** from the same input (e.g., blast furnace produces hot metal OR pig iron)
- **Quality consistency** needs to be maintained between inputs and their outputs. Inputs may have a quality which influences the output quality, creating a link between certain input commodities and their respective output commodities. (e.g. in a DRI furnace iron_ore_mid produces dri_mid but iron_ore_high produces dri_high.)

**Architecture:**
The constraint is grouped by **(process_center, output_group)** rather than individual BOM elements:
1. **Group by outputs**: BOMs producing the same set of outputs are treated together
2. **Sum alternate inputs**: Different inputs that produce the same outputs are summed (they're substitutes)
3. **Balance at output level**: Total production from all inputs = total sent out of all outputs in that group

**Formulation:**
```
For each (process_center, output_group):
    sum((incoming flow of input_i / input_ratio_i) for all inputs producing output_group)
    = sum(outgoing flow of output_j for all outputs in output_group)
```

**Example 1 - Multiple Inputs (EAF with alternate feedstocks):**
```
EAF can produce steel from:
  - BOM 1: Scrap → Steel (ratio 1.05)
  - BOM 2: DRI → Steel (ratio 1.15)

Both BOMs produce the same output_group {steel}

Constraint:
  (scrap_inflow / 1.05) + (dri_inflow / 1.15) = steel_outflow

If 80 tons scrap + 23 tons DRI flow in:
  (80/1.05) + (23/1.15) = 76.2 + 20 = 96.2 tons steel can flow out
```

**Example 2 - Multiple Outputs (Blast Furnace):**
```
BF produces multiple outputs from iron ore:
  - BOM: Iron ore → {hot metal, pig iron} (ratio 1.5)

Output_group is {hot_metal, pig iron}

Constraint:
  (iron_ore_inflow / 1.5) = hot_metal_outflow + pig_iron_outflow

If 150 tons iron ore flows in:
  (150/1.5) = 100 tons total can flow out (split between hot metal and pig iron)
```

**Implementation Details:**

The constraint building is optimized with pre-computed index sets:
1. **Pass 1**: Group incoming allocations by (process_center, output_group)
2. **Pass 2**: Pre-index outgoing allocations by (process_center, commodity)
3. **Constraint rule**: Use pre-computed sets for fast summation

**Why this complexity?**
- **Flexibility**: Models realistic technology choices (scrap vs DRI)
- **Co-products**: Handles by-products and multi-output processes
- **Realism**: Captures true production ratios and alternate feedstocks

**Note on Dependent Commodities:**

BOMs can also define dependent commodities (secondary materials like limestone that must flow with primary inputs like iron ore). A separate constraint type (`add_dependent_commodities_consistency_constraints_to_lp()`) handles these:

```
For each (process_center, dependent_commodity):
    incoming flow of dependent_commodity
    = sum(dependent_ratio_i × incoming flow of primary_input_i
          for all primary inputs requiring this dependent_commodity)
```

**Important:** This constraint is **automatically skipped** when no suppliers exist for the dependent commodity, preventing infeasibility. The model logs warnings for skipped constraints. This allows BOMs to specify ideal material requirements while handling cases where those materials aren't available in the model

---

### 3. Feedstock Ratio Constraints

**Purpose:** Technology-specific min/max shares for flexible feedstocks

**Formulation:**
```
min_ratio × total_output ≤ feedstock_usage ≤ max_ratio × total_output
```

**Example:** EAF must use 50-90% scrap, 10-50% DRI

---

### 4. Demand Fulfillment Constraint

**Purpose:** Regional demand must be satisfied (with slack if infeasible)

**Formulation:**
```
sum(incoming flows to demand center) + slack_variable = demand_quantity
```

**Slack penalty:** Very high cost (10M) to avoid unmet demand unless truly infeasible

---

### 5. Trade Quota Constraints

**Purpose:** Enforce volume limits on cross-border flows

**Formulation:**
```
sum(flows matching tariff pattern) ≤ quota_limit
```

**Example:** "China → EU steel flows ≤ 1M tons/year"

---

### 6. Secondary Feedstock Constraints

**Purpose:** Regional limits on secondary material availability

**Formulation:**
```
sum(material to region group) ≤ regional_availability
```

**Applied to:** Scrap and other secondary feedstocks with limited regional supply

---

### 7. Aggregated Commodity Constraints

**Purpose:** Technology-level min/max ratios across commodity groups

**Formulation:**
```
min_share × total_input ≤ sum(matching commodities) ≤ max_share × total_input
```

**Example:** "EAF must use 50-90% metallic charge from scrap-like materials"

---

## Objective Function

**Goal:** Minimize total system cost

**Components:**
1. **Allocation costs:** (flow × distance × transport_cost_per_km) OR (flow × location_specific_cost)
2. **Energy costs:** flow × bom_element.energy_cost
3. **Production costs:** flow × production_cost (carbon cost)
4. **Tariff taxes:** flow × tax_rate for cross-border flows
5. **Demand slack penalty:** unmet_demand × 10,000,000
6. **Capacity slack penalty:** underutilization × 100,000

**Why penalties?** They convert hard constraints (must meet demand) into soft constraints (strongly prefer to meet demand). This prevents infeasibility when demand exceeds supply.

---

## Special Features

### Distance-Based Filtering

**Problem:** Certain commodities can only travel short distances while in their "hot" form. Hot metal cools within a short radius (~100 km); DRI loses its heat premium over similar distances. Their cold counterparts (`pig_iron`, `hbi_*`, `electrolytic_iron`) ship globally.

**Hot/cold commodity pairs:**

| Hot (`closely_allocated_products`) | Cold (`distantly_allocated_products`) |
|------------------------------------|----------------------------------------|
| `hot_metal`                        | `pig_iron`                             |
| `dri_high` / `dri_mid` / `dri_low` | `hbi_high` / `hbi_mid` / `hbi_low`     |
| `liquid_iron`                      | `electrolytic_iron`                    |

**Implicit `pig_iron` output for all hot-metal producers.** `PrimaryFeedstock.get_primary_outputs()` mirrors any `hot_metal` output as `pig_iron` when the feedstock does not already declare one (regardless of technology — previously only BF had this mirror). This means smelting-reduction, charcoal-BF and any other hot-metal-producing tech can supply `pig_iron` flows to remote demand centres after the hot→cold relabeling described below, not just BF.

**Solution:** `fix_to_zero_allocations_where_distance_doesnt_match_commodity()` — behavior depends on whether furnace-group clustering is enabled and how clusters are keyed:

- **Clustering disabled (legacy):** The LP fixes hot-commodity flows to zero for all pairs beyond `hot_metal_radius`, and cold-commodity flows to zero for pairs inside the radius. Called before build to reduce problem size.
- **Clustering enabled, iso3 keying:** The LP blocks *international* hot-commodity flows (different ISO3). Intra-country hot flows are allowed at the LP stage because clusters aggregate furnace groups whose individual locations aren't visible to the LP. The actual per-FG radius check is deferred to **disaggregation** (see below).
- **Clustering enabled, plant-group keying** (`cluster_hot_metal_techs_by_plant_group=True`): The LP additionally blocks hot-commodity flows between meta-furnace-groups that belong to *different plant groups*, even within the same country. This matches the clustering key for hot-metal-affected technologies and limits LP-level hot flows to pairs that can plausibly stay within radius at disaggregation. Supplier / demand-centre edges (which don't have a `plant_group_id`) fall through to the iso3 rule.

A summary line `[LP HOT-METAL] Fixed to zero: X cross-country, Y cross-plant-group, Z missing-iso3 ...` is emitted per year so the operator can see how often each rule bound.

---

### Disaggregation: Hot-Metal Radius + Minimum-Ratio Enforcement

After the LP solves at the cluster level, `disaggregate_allocations()` in `furnace_group_clustering.py` splits cluster-to-cluster flows back to individual furnace groups. This is where per-FG distance constraints, BOM-minimum-share constraints, physical-capacity caps, and bill-of-materials consistency are all enforced.

The LP plans at cluster level and is not aware of the `hot_metal_radius` constraint; it can allocate cluster totals that are physically infeasible at the FG level (e.g. a source-cluster pocket that can't reach a destination-cluster pocket). Disaggregation has to project that infeasible LP solution onto something physically consistent — preserving physical capacity and per-FG BOM as hard invariants while allowing cluster-level flows to drift away from LP when necessary.

Nine mechanisms work together:

#### 1. BOF filtering and effective capacity at clustering time

`cluster_furnace_groups()` performs two corrections before building clusters:

- **Isolated BOF filtering.** BOF FGs with no active BF/ESF/SR within `hot_metal_radius` in their country are excluded from their cluster entirely. Without a local iron source they can never satisfy the hot-metal minimum-share constraint, so including them would guarantee a BOM violation.

- **Effective BOF cluster capacity.** For each BOF FG, effective capacity = `min(physical_capacity, Σ[reachable_BF_capacity within radius] / min_hot_metal_share)`. The cluster's `total_capacity`, `capacity_shares`, and weighted costs are all derived from these effective capacities. This prevents the LP from allocating more hot metal to a BOF cluster than its geographically reachable BF neighbours can physically supply.

**Cluster key.** The cluster key is `(technology_name, location_key, feedstock_signature)` where `location_key` is `plant.location.iso3` for most FGs, but switches to `plant.ultimate_plant_group` for FGs affected by the hot-metal radius when `cluster_hot_metal_techs_by_plant_group=True`. A FG is considered *affected* by looking at its `effective_primary_feedstocks`: any feedstock whose `metallic_charge` or `outputs` key is in `config.closely_allocated_products` (hot_metal, dri_*, liquid_iron) triggers plant-group keying. The resulting `MetaFurnaceGroup.plant_group_id` is consumed by the LP's cross-plant-group rule above. A log line `[CLUSTERING] Created N clusters (X FGs keyed by plant_group, Y by iso3)` confirms the split per year.

#### 2. Joint transportation problem for hot-metal disaggregation (pre-pass)

`disaggregate_allocations()` runs Case 4 joint-strict (hot_metal → BOF with ≥70% min-share) **first**, before Case 2 / Case 3, so we can measure the actual flows and adjust the other cases to stay BOM-consistent with the physical reality.

When multiple BF clusters contribute hot metal to the same BOF cluster, all contributing flows are solved in a **single joint transportation problem** rather than independently per BF→BOF pair. This guarantees that each BOF FG receives exactly `effective_share × total_LP_hot_metal` regardless of how many source clusters are involved.

The supply vector for the joint problem is computed by `_reach_based_joint_supplies()`: for each BOF FG demand, the volume is distributed to the BF FGs within radius weighted by their LP supply. This makes every geographic pocket self-balancing (`pocket_supply == pocket_demand`).

BOF FGs whose neighbouring BF uses a **different reductant** (and hence a different cluster key not contributing to this LP flow) are pre-filtered from the joint demand and their share redistributed to the remaining reachable BOF FGs, capped at each FG's effective capacity. When *all* BOF FGs in the cluster are unreachable from the contributing source cluster(s), the pre-pass falls back to proportional routing without radius so BOM stays consistent (the hot-metal flow is later relabelled as pig iron by `_substitute_commodity_by_distance`).

`_solve_strict_by_components()` decomposes the joint problem into geographically connected components (isolated pockets of BF/BOF pairs separated by more than `hot_metal_radius`). Each pocket is solved as an independent min-cost-flow problem.

Each min-cost-flow graph carries a small `SOURCE → SINK` **slack edge** (capacity = `2 kg × (sources + destinations)`, cost > `INFEASIBLE_COST`). Sub-pockets where supply equals demand at float level can flip infeasible after the ±0.5 kg per-node integer rounding the solver requires; the slack edge absorbs that few-kg noise without competing with any real bipartite route. If post-solve the slack edge is saturated, the infeasibility is structural rather than rounding-induced and `NetworkXUnfeasible` is re-raised so the existing diagnostic path runs.

#### 3. Per-FG physical-capacity cap

`_solve_strict_by_components()` accepts a per-FG `source_capacities` map. Each source FG is capped at `cap_share × LP_volume_for_this_joint_group` — this is the FG's LP share of the current group, and summing across all joint groups a single FG participates in is bounded by `cap_share × total_joint_LP ≤ cap_share × total_capacity = physical capacity`. When scaling pocket supply up to meet demand would push an FG above its cap, the cap holds and pocket **demand is scaled down** instead; the shortfall is recorded in `stats["dest_unmet_demand"]` and propagates downstream.

This is the safety net for the case where the LP over-committed a source cluster's pocket relative to what its reachable source FGs can physically produce.

#### 4. Strict radius for min-constrained flows

When a hot commodity is routed to a destination technology with a minimum-share constraint on that commodity — either via `PrimaryFeedstock.minimum_share_in_product > 0` or via an `AggregatedMetallicChargeConstraint` (e.g. BOF hot_metal ≥ 70%) — the min-cost-flow solver **omits** radius-violating edges entirely rather than penalising them. `_destination_has_min_constraint_for_commodity(to_meta_fg, commodity)` checks both individual feedstock constraints and aggregated wildcard constraints.

#### 5. Drift computation and Case 2 / Case 3 rebalancing

Once joint-strict has run, `disaggregate_allocations()` computes a **drift factor** per cluster that participated:

```
drift = actual_flow / LP_flow
```

* **Source clusters** (BF/SR/DRI): `drift = (actual_joint_output + non_joint_LP_output) / LP_total_output`. Drift < 1 means the cluster's joint-strict output was capped below LP.
* **Destination clusters** (BOF): `drift = (actual_joint_input + non_joint_LP_input) / LP_total_input`. Drift < 1 means the cluster received less hot metal / pig iron / DRI than the LP planned.

Drift then drives two rebalances:

* **Case 2 batches** (cluster → demand centre) are scaled by drift for every drifted cluster. A BOF cluster that couldn't take its full LP hot-metal input ships proportionally less crude steel; a source cluster whose joint-strict output was capped ships proportionally less pig iron / HBI to demand centres. Supplier-side demand centres receive less than the LP promised — the physical consequence of radius constraints the LP didn't model.

* **Case 3 batches** (supplier → cluster) are rebalanced by `_rebalance_case3_for_cluster_drift()`, a min-cost transportation problem that keeps per-supplier totals ≤ LP (mine / scrap capacity is a hard upper bound) and sets per-cluster totals to `LP_demand × drift` (matches actual production × BOM ratio). When Σ supply > Σ demand (common when BOF clusters drift down), supplier supplies scale uniformly down to match — mine capacity is under-utilised rather than exceeded.

#### 6. Effective-shares refresh (post-pre-pass)

For every cluster that participated in joint strict, effective shares are recomputed as

```
effective_i = (actual_joint_i + cap_share_i × non_joint_LP) / actual_total_cluster
```

(and the analogous input-side formula for BOF dest clusters). This makes Case 2 per-FG supplies and Case 3 per-FG demands track each FG's *total actual production* — so a FG that absorbed another's pocket demand in joint strict gets proportionally more iron ore, and a FG whose pocket was cut gets less. This is what makes BOM hold at the FG level, not just the cluster level.

**Important**: the refresh happens for every joint-strict-participating cluster, not just those whose cluster-level drift exceeds some threshold. A cluster can have near-unity overall drift (e.g. its joint output is only a small fraction of its total output) while still having individual FGs whose effective shares differ significantly from cap-share — without the universal refresh those FGs' iron-ore attribution would silently drift from their actual production.

#### 7. Hot → cold relabeling for non-constrained flows

When a hot commodity flows beyond the radius and the destination has no binding minimum constraint on it (Case 4 individual), `_substitute_commodity_by_distance()` relabels the allocation to the cold equivalent (e.g. `dri_high` → `hbi_high`, `hot_metal` → `pig_iron`). The flow volume is preserved; only the label changes.

#### 8. Per-FG BOM fix-up

`_bom_fix_up_per_fg()` runs as the final step before validation. For every FG in a cluster that participated in Case 4 joint strict, it:

1. Aggregates the FG's actual incoming volumes by BOM commodity.
2. Computes the BOM-ideal output: `ideal_j = Σ_X charge_in_j,X / expected_ratio_X`.
3. If `ideal_j > cap_share × total_capacity`, caps the output at physical capacity and scales the FG's incoming flows down by `physical_cap / ideal_j`. The scaled-down delta represents material the upstream source FG produced but this FG can't accept — it remains at the source side as stranded/unused.
4. Rescales the FG's outgoing flows (Case 2 / Case 4) so the total matches `ideal_j` (or the capped value).

This removes the systematic error from the linear (effective × drift) approximation: per-FG BOM holds exactly, regardless of how different the FG's commodity mix is from the cluster average.

#### 9. BOM consistency validation

`_validate_disaggregated_allocations()` checks every FG against:

* **Physical capacity** — total outgoing volume ≤ `cap_share × total_cluster_capacity`, with a 1% tolerance.
* **BOM consistency** — `Σ_X (charge_in_X / expected_ratio_X) / total_out ≈ 1.0` over every active metallic charge the FG's technology accepts, within `BOM_TOL = 0.03` (3%). The tolerance only has to absorb residual numerical noise (solver rounding, commodity-substitution deltas) now that the fix-up pass has removed the systematic ratio-approximation error.

A separate downstream check in `TMPAMConnector.validate_bom_consistency()` then acts as a safety net for min-share constraints, and `correct_utilization_for_supply_constraints()` scales a BOF FG's production down if its hot-metal share fell below the minimum.

---

#### Known limitations

* **LP infeasibility w.r.t. radius.** The LP doesn't model `hot_metal_radius`; cluster-level allocations can be physically infeasible at the pocket level. Cross-cluster borrowing (multi-source joint groups) or supply-capped demand reduction (single-source pockets) are post-hoc repairs — the resulting steel output and supplier-to-cluster routing can deviate from the LP's optimum.
* **Demand-centre shortfalls.** When a BOF cluster is capacity-constrained on the metallic-charge side, its Case 2 steel shipments drop by the drift factor (and are further adjusted by the per-FG BOM fix-up). Demand centres receive less than the LP promised; we do not redistribute that unmet demand to another cluster.
* **Mine-capacity soft preservation.** Case 3 Sinkhorn rebalance keeps each supplier's LP total as a hard *upper* bound but will under-utilise that capacity when aggregate drift pushes cluster demand below supplier LP.
* **Stranded material when a BOF is capacity-capped.** If the BOM-ideal output for a BOF FG exceeds its physical capacity, `_bom_fix_up_per_fg` caps the output and scales its inputs down proportionally. The "scaled-down delta" is material the upstream source FG produced but couldn't ship to this FG — it remains at the source side as stranded/unused, which may show up as a small per-source-FG BOM residual absorbed by `BOM_TOL`.
* **Downstream reductions are not re-routed.** If a cluster's drift cuts a flow to demand centre X, the demand is simply unfulfilled — no LP re-solve, no reallocation.

---

**Logging** — at the start of each disaggregation the log reports:
- Number of joint groups (strict hot) and individual flows.
- Per joint group: source cluster count, total LP volume, number of connected components.
- Any BOF FGs pre-filtered due to technology-cluster mismatch.
- Supply-constrained pockets (supply/demand mismatch + % demand reduction).
- Cluster drift detected: list of clusters with `|drift − 1| > 1%` and their actual/LP ratio.
- BOF clusters whose Case 2 shipments are being scaled down.
- BOM validation summary: counts by check type, and per-FG details for any violations.

At the end of disaggregation a **Hot Metal Radius Audit** block reports how many final allocations violate the radius:

```
[DISAGGREGATION] === Hot Metal Radius Audit ===
[DISAGGREGATION] Closely-allocated flows: N total (V t); violating radius=5.0km: X flows (Y t)
[DISAGGREGATION]   hot_metal: X1/N1 flows violate, Y1/V1 t violate
[DISAGGREGATION]   dri_high:  X2/N2 flows violate, Y2/V2 t violate
...
```

A violation is a final disaggregated allocation whose commodity is in `config.closely_allocated_products` and whose source→destination haversine distance exceeds `config.hot_metal_radius`. The counter is purely diagnostic — it doesn't block anything — and is useful for comparing clustering-key choices (iso3 vs plant_group) side-by-side.

**Allocation-cost back-fill onto disaggregated flows.** The clustered LP records per-edge `allocation_costs` keyed by `(from_cluster, to_cluster, commodity)`. After disaggregation, each per-FG flow inherits its cluster-pair's per-tonne cost — the LP objective coefficient is identical for every constituent FG within a cluster pair, so the lookup is unambiguous. Hot/cold relabeled flows match against the cluster's original commodity via `_commodity_equivalent_names()` (e.g. a disaggregated `pig_iron` flow falls back to the cluster's `hot_metal` cost). Suppliers and demand centres pass through unchanged. Match counts and the first ten unmatched examples are logged at info level. The result is that `Allocations.allocation_costs` is now populated under clustering (previously it was `None`, forcing `TM_PAM_connector` to recompute everything from scratch), making per-FG LP-level edge costs visible to downstream reporting.

---

### Transportation Cost System

**Two modes:**
1. **Legacy:** Global `cost_per_km` × haversine_distance
2. **Modern:** Location-specific `TransportationCost` per country-pair-commodity

**Performance:** O(1) lookup using pre-built dictionary

#### Process-centre distance caching

`Environment` carries a process-centre distance cache (`_distance_cache: dict[(from_pc_name, to_pc_name), float]`) that persists across simulation years. The first time any caller asks for the distance between two PC names — under the legacy `cost_per_km` mode, during `precompute_distances_for_hot_metal_check()`, or via the closure produced by `build_distance_function_for_trade_lp()` — it is computed via `ProcessCenter.distance_to_other_processcenter` (which itself uses the underlying ISO3 distance cache) and stored. Subsequent years reuse the result without recomputation.

LP setup uses `Environment.build_distance_function_for_trade_lp(process_centers)` to build a closure and assigns it to `lp_model._external_distance_function`, replacing the previous in-LP haversine pass. The hot-metal radius check is precomputed once per year via `precompute_distances_for_hot_metal_check(process_centers, hot_metal_radius)`, which returns the set of `(from_name, to_name)` pairs within radius — converting the in-loop check from O(N²) float comparisons to set-membership lookups.

`Environment.log_distance_cache_stats()` is invoked at the end of LP setup and reports `hits`, `misses`, `computations` and entry count at info level.

---

### Carbon Border Adjustments

**Applied outside TradeLPModel** (in setup workflow):
- Increases costs for high-carbon → low-carbon flows (export rebate)
- Increases costs for low-carbon → high-carbon flows (import adjustment)
- Prevents double-counting with adjusted_flows set

---

### Soft Minimum Capacity

**Purpose:** Encourage facilities to operate at reasonable utilization levels

**Implementation:**
- Slack variable for underutilization
- Moderate penalty (100k) to prefer operation without being rigid
- Prevents solutions where many facilities operate at 5% just to meet constraints

---

## Solver Details

### Why HiGHS?

**Advantages:**
- Open-source (no licensing issues)
- Very fast for large LP problems
- Actively maintained
- Good numerical stability

### Interior Point Method (IPM)

**Characteristics:**
- Faster than simplex for large problems
- Solves from "inside" feasible region
- May produce fractional solutions

**Crossover enabled:** Converts IPM solution to vertex solution (cleaner, more integer-friendly)

---

### Infeasibility Diagnostics

When model is infeasible (no solution exists):
1. Logs model statistics (variables, constraints, capacity vs demand)
2. Returns empty result instead of crashing

**Common causes:**
- Missing process connectors block necessary flows
- Conflicting constraints (e.g., quota too restrictive)

---

## Integration Points

### Inputs (from simulation):
- Process centers: Furnace groups, suppliers, demand centers
- Technology specs: Bill of materials from dynamic business cases
- Trade policy: Active tariffs and quotas for the year
- Constraints: Regional scrap limits, technology ratios

### Outputs (to simulation):
- Optimal commodity flows between all facilities
- Production levels for each facility
- Unmet demand (if any)
- Total system cost

---

## Design Decisions

### Why Slack Variables?

**Benefit:** Model solves even when constraints conflict
- Hard constraint: Demand MUST = supply → Infeasible if capacity too low
- Soft constraint: Demand = supply + slack → Always feasible, solver minimizes slack

### Why Pre-compute Legal Allocations?

**Benefit:** Massive performance improvement
- Without: 100² facilities × 5 commodities = 50,000 variables (most infeasible)
- With: Only ~5,000 variables for actually possible flows
- 10x reduction in problem size

---

## For Detailed Implementation

For implementation details, parameter types, exact formulations, and code examples, see the comprehensive docstrings in each class and method within `src/steelo/domain/trade_modelling/trade_lp_modelling.py`.

Key methods to review:
- `build_lp_model()` - Orchestrates model construction
- `add_bom_inflow_constraints_to_lp()` - Material balance logic
- `add_objective_function_to_lp()` - Cost minimization formulation
- `solve_lp_model()` - Solver invocation and diagnostics
- `extract_solution()` - Result extraction and validation
