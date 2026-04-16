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

**Solution:** `fix_to_zero_allocations_where_distance_doesnt_match_commodity()` — behavior depends on whether furnace-group clustering is enabled:

- **Clustering disabled (legacy):** The LP fixes hot-commodity flows to zero for all pairs beyond `hot_metal_radius`, and cold-commodity flows to zero for pairs inside the radius. Called before build to reduce problem size.
- **Clustering enabled:** The LP only blocks *international* hot-commodity flows (different ISO3). Intra-country hot flows are allowed at the LP stage because clusters aggregate furnace groups whose individual locations aren't visible to the LP. The actual per-FG radius check is deferred to **disaggregation** (see below).

---

### Disaggregation: Hot-Metal Radius + Minimum-Ratio Enforcement

After the LP solves at the cluster level, `disaggregate_allocations()` in `furnace_group_clustering.py` splits cluster-to-cluster flows back to individual furnace groups. This is where the per-FG distance and BOM-minimum constraints are enforced.

**Three mechanisms work together:**

1. **BOF filtering at clustering time.** `cluster_furnace_groups()` drops BOF furnace groups with `has_hot_metal_access == False` — BOFs that have no BF/ESF/SR producer within `hot_metal_radius`. These BOFs can't legitimately operate under the hot-metal minimum ratio, so excluding them prevents guaranteed radius violations downstream. The flag is set by `PlantGroup.update_hot_metal_access(hot_metal_radius)` at the start of each PAM year.

2. **Strict radius for min-constrained flows.** When a hot commodity (e.g. `hot_metal`, `dri_high`) is routed to a destination technology that has a `PrimaryFeedstock.minimum_share_in_product > 0` for that commodity, the min-cost-flow solver omits radius-violating edges entirely. If the resulting flow problem is infeasible, the disaggregation raises a `RuntimeError` listing the unreachable destination FGs — the LP has allocated hot material to a destination that physically can't receive it within radius, which would silently break the BOM minimum constraint.

3. **Hot → cold relabeling for non-constrained flows.** When a hot commodity flows beyond the radius *and* the destination has no minimum constraint on it (or the flow is surplus above the minimum), `_substitute_commodity_by_distance()` relabels the output allocation to the cold equivalent (e.g. `dri_high` → `hbi_high`). The flow volume is preserved; only the label changes. This reflects the physical reality that the LP's intra-country hot flow must actually travel as cold material over that distance. Logged at INFO level (not WARNING, since it's expected).

**Logging** — at the start of each disaggregation, the log reports:
- Which `(technology, commodity)` pairs have minimum-share constraints and whether each triggers strict radius.
- Per-commodity summary of flows that exceeded the radius, split into "relabeled to cold" (informational) vs "real violations" (warnings with top-3 destination countries).

**Helper:** `_destination_has_min_constraint_for_commodity(to_meta_fg, commodity)` checks `dynamic_business_case` for a feedstock with `minimum_share_in_product > 0` whose `metallic_charge` matches the commodity (or its hot/cold equivalent — feedstock data can use either form).

---

### Transportation Cost System

**Two modes:**
1. **Legacy:** Global `cost_per_km` × haversine_distance
2. **Modern:** Location-specific `TransportationCost` per country-pair-commodity

**Performance:** O(1) lookup using pre-built dictionary

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
