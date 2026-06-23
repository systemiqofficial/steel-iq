# Trade Model Setup - High-Level Overview

## Purpose

The trade model optimizes global steel and iron trade flows by solving a linear programming (LP) problem. It determines the most cost-effective way to route materials from suppliers through production facilities to demand centers, subject to capacity constraints, trade policies, and physical limitations.

## Core Concept

The model represents the global steel value chain as a network:
- **Nodes**: Suppliers (mines), production facilities (furnaces), and demand centers (regions)
- **Edges**: Valid material flows between compatible technologies
- **Objective**: Minimize total cost (production + transport + tariffs + carbon)
- **Constraints**: Capacity limits, trade policies, distance restrictions, feedstock ratios

## Main Functions

### 1. `set_up_steel_trade_lp()` - Build the Optimization Model

**What it does:** Constructs the complete LP model structure from simulation data.

**Process:**
1. Initializes an empty LP model with solver tolerance settings
2. Adds all commodities being modeled (steel, iron, etc.)
3. Creates process centers for suppliers, production facilities, and demand
4. Defines valid connections between process types
5. Applies optional constraints (tariffs, distance limits, feedstock ratios)
6. Adds location-specific transportation costs if available

**Key decisions:**
- Only includes active furnace groups (configurable status filter)
- Scales production capacity by safety factor (typically 95%)
- Reuses process definitions across multiple furnaces with same technology

**Outputs:** A fully configured `TradeLPModel` ready for optimization

---

### 2. Helper Functions - Building Model Components

#### `create_process_from_furnace_group()`
**Purpose:** Converts a furnace group's technology specifications into an LP process definition.

**What it captures:**
- Input-output relationships (bill of materials)
- Minimum/maximum feedstock ratios
- Secondary feedstock requirements
- Energy costs per input type

**Handles edge cases:**
- Skips feedstocks with missing or invalid data
- Warns about technologies with no primary outputs
- Reuses existing BOM elements when possible

---

#### `add_furnace_groups_as_process_centers()`
**Purpose:** Represents each steel production facility as an LP node.

**What it models:**
- Production capacity (scaled by availability factor)
- Geographic location for distance calculations
- Production cost (carbon cost)
- Soft minimum utilization target

**Filters:** Only includes furnaces with active operating status

---

#### `add_demand_centers_as_process_centers()`
**Purpose:** Represents regional steel demand as LP nodes.

**What it models:**
- Regional demand quantity for the simulation year
- Geographic center of demand region
- Single shared demand process (all regions demand "steel")

---

#### `add_suppliers_as_process_centers()`
**Purpose:** Represents raw material sources (mines, scrap yards) as LP nodes.

**What it models:**
- Supply capacity for the simulation year
- Geographic location
- Production/extraction cost
- One process type per commodity (e.g., all scrap sources "scrap_supply")

**Per-year supplier fields.** `Supplier` exposes time-varying capacity, production cost, mine cost and mine price as dicts keyed by `Year`:

| Field | Lookup |
|-------|--------|
| `capacity_by_year[year]` | Volumes (tonnes) available that year |
| `production_cost_by_year[year]` | Production / extraction cost ($/t) |
| `mine_cost_by_year[year]` | Mine-level cost ($/t), iron-ore mines only |
| `mine_price_by_year[year]` | Mine-level price ($/t), iron-ore mines only |

Trade-LP setup, the average-commodity-price calculation in `Environment.calculate_average_commodity_price_per_region()`, and downstream callers all read the value for the current simulation year. The same per-year structure applies to non-mine suppliers (scrap, etc.) — they simply populate a flat constant across years if their underlying input has no annual variation. Suppliers whose `production_cost_by_year` does not contain the current year are silently skipped from the average-price calculation.

---

### 3. Constraint Functions

#### `enforce_trqs_via_gateways()`
**Purpose:** Models Tariff Rate Quotas (TRQs) by injecting virtual gateway nodes into the LP before it is built.

**What a TRQ is:** A trade policy with a duty-free volume allowance (quota) and a higher out-of-quota (OOQ) tariff rate above that threshold. EU safeguard measures on steel imports are a typical example.

**Gateway node approach:** Each TRQ tier becomes a virtual pass-through node in the LP network:
```
plant → gateway_tier_0  (capacity = tariff-free quota,  arc cost = in-quota rate × commodity price)
plant → gateway_tier_1  (capacity = unlimited,           arc cost = OOQ rate × commodity price)
gateway_tier_k → demand center  (arc cost = avg transport from exporting countries)
```
The in-quota / OOQ rate applied to each plant→gateway arc is the **conventional** or **green** rate depending on the originating plant's green-steel eligibility (see *Conventional vs green rate split* below). Because the LP minimises cost, it fills the cheaper (in-quota) tier first with no explicit ordering constraints needed.

**Duty-free ordering nudge:** When several plants compete for the same scarce duty-free (tier-0) capacity at otherwise-equal cost, `_override_gateway_arc_costs()` adds a tiny tie-break of `1e-3 × (production_cost + BOM energy cost)` to each plant→tier-0 arc on the LP objective copy only, so the lowest-total-cost plants (carbon + feedstock energy) win the scarce quota first. The BOM term sums the plant's `bom_energy_costs` entries across commodities; `production_cost` is its carbon cost. This nudge is written only to `lp_model.allocation_costs` — `gateway_arc_costs` is left untouched, so `collapse_gateway_arcs()` and PAM still see the real tariff and transport costs.

**Conventional vs green rate split:** Every tier carries two ad-valorem rates, supplied on the `TRQs` sheet as four columns: `In-quota conventional tariff rate`, `Out-of-quota conventional tariff rate`, `In-quota green tariff rate`, `Out-of-quota green tariff rate` (each 0–100, accepts `"50%"` strings; in-quota blank → 0%). They are stored on `TariffRateQuota` as `in_quota_conventional_tariff_rate` / `out_of_quota_conventional_tariff_rate` / `in_quota_green_tariff_rate` / `out_of_quota_green_tariff_rate` and surface on each `TRQTier` as `conventional_tariff_rate` / `green_tariff_rate`. When a plant flows through a gateway, `compute_gateway_arc_costs()` charges it the green rate if `green_steel_eligible`, else the conventional rate. *Backward compatibility:* a legacy single `Out-of-quota tariff rate` / `In-quota tariff rate` column is read as the conventional value, and a missing green column defaults to the conventional rate (so old single-rate sheets behave as before).

**Steel-type scope (TRQs):** The `Commodity` column on the `TRQs` sheet still encodes *which steel draws on the quota volume*, parsed into `TariffRateQuota.steel_type`:
- `steel` / `steel (any)` → **any**: both conventional and green steel share the single quota volume; each pays its own rate (conventional plants the conventional rate, green plants the green rate).
- `conventional steel` → **conventional**: only non-green plants use the quota (green-steel-eligible plants are fully exempt); only the conventional rate columns are relevant — the green columns are ignored.
- `green steel` → **green**: only green-steel-eligible plants use the quota (conventional plants fully exempt); only the green rate columns are relevant.

Eligibility uses the same `green_steel_eligible` flag set on each plant ProcessCenter as for normal tariffs (`trq_steel_type_applies_to_plant()` is the single source of truth for *which plants enter the gateway*; the per-arc rate is then chosen by the same flag). "Fully exempt" means the plant gets **no** gateway arc and keeps its direct plant→DC arc, so it never counts against the quota and pays no OOQ tariff (it is still subject to any ordinary `TradeTariff`, a separate mechanism). The separate `TradeTariff.green_steel_exemption` discount is unaffected by this split.

**Shared quotas:** Multiple exporting countries that share one quota pool are merged into a single TRQ object (same `shared_quota_id`) with a combined `from_iso3s` list. They all feed through the same gateway node, so the capacity constraint automatically covers the combined volume. The merged TRQ takes its `steel_type` (and other metadata) from the first row of the group.

**Data pipeline:** TRQs are read from the `TRQs` sheet of the master Excel file via `read_trqs()` and stored on `Environment.active_trqs`, which is refreshed each simulation year based on `start_year` / `end_year`.

**Route guard:** TRQ-covered `(from_iso3, to_iso3, commodity)` triples are recorded in `lp_model.trq_covered_routes`. The `enforce_trade_tariffs_on_allocations()` function skips any `TradeTariff` on a covered route to avoid double-counting. Note: this guard currently skips *all* trade tariffs on TRQ routes, including additive instruments like CBAM, which therefore are not applied on top of the TRQ tariff on those routes — a known limitation.

**Gateway collapse (TM-PAM connector):** After solving, `trade_lp.allocations` contains raw `plant → gateway` and `gateway → DC` arcs, which the domain mapping loop and the TM-PAM connector cannot use directly — they expect direct `plant → DC` arcs and have no concept of gateway nodes (which carry `iso3 = "__GWY__"`). `collapse_gateway_arcs()` (in `set_up_steel_trade_lp.py`) runs between `extract_solution()` and domain mapping to bridge this. It is called from **both** solve paths — `solve_steel_trade_lp_and_return_commodity_allocations()` (non-clustering) and `solve_lp_only()` (clustering) — so TRQ tariffs reach PAM in either case. The step:
1. Dissolves each `plant → gateway → DC` chain into a direct `plant → DC` arc.
2. Attributes volume proportionally to gateway outflow shares: `vol(plant→DC) = vol(plant→gw) × (vol(gw→DC) / total_gw_outflow)`.
3. Reads the per-plant tariff from `gateway_arc_costs` and injects a volume-weighted average into `allocations.tariff_taxes`, keyed by `(from_iso3, to_iso3, commodity)`, so TM-PAM picks it up via `get_tariff_cost()`. Only plants subject to the TRQ (per its `steel_type`) have gateway arcs, so exempt steel never contributes to this average.
4. Removes all gateway arcs from `allocations`.

**Granularity caveat:** because the injected `tariff_taxes` are keyed by ISO3 route (not by plant), the per-plant tariff is flattened to a volume-weighted average across all (subject) plants of the same origin country shipping the same out-of-quota route. The LP objective and the per-arc `allocation_costs` remain correct per plant; only the ISO3-keyed value PAM consumes is blended. This matches the existing granularity of normal (non-TRQ) tariffs, which are inherently ISO3-keyed.

**TRQ reporting (`trq_report_<year>.csv`):** After the LP solves (and before `trade_lp` is freed), `write_trq_report_csv()` emits one CSV per simulation year to `output_dir/TM/trq_report_<year>.csv`. It is a no-op when no TRQs are active (no file written). Built by `build_trq_report_rows()`, which re-reads the solved gateway flows and attributes each plant's gateway inflow to destinations by the gateway's outflow shares (same attribution as `collapse_gateway_arcs`). There is **one row per `(gateway_node_id, from_iso3, to_iso3, commodity)`** — gateway node already encodes the tariff tier, so in-quota (tier 0) and out-of-quota (tier 1) appear as separate rows. Columns:

| Column | Meaning |
|--------|---------|
| `allocated_volume_t` | Sum of attributed flow (tonnes). Zero-volume groups are dropped. |
| `avg_allocation_cost_usd_per_t` | Volume-weighted (contributing plant's production cost [carbon] + energy opex + transport on gateway→DC). Energy opex is reconstructed from the plant's actual solved feedstock flows: `Σ bom_energy_costs[(plant, input)] × inflow / plant output`. The TRQ tariff is reported separately. |
| `pct_plants_green_steel_eligible` | Share of the *distinct* contributing plants with `green_steel_eligible=True`, 0–100. For conventional/green-scoped TRQs this is 0/100 by construction; it is informative mainly for `any`-scoped TRQs. |
| `applied_tariff_tax_usd_per_t` | Volume-weighted TRQ tariff component only (the value injected into `allocations.tariff_taxes`). 0 for the duty-free tier. |

---

#### `enforce_trade_tariffs_on_allocations()`
**Purpose:** Applies flat trade policy restrictions to cross-border flows.

**Supports three tariff types:**
1. **Volume quotas**: Maximum tons per year on a route
2. **Absolute taxes**: Fixed cost per ton ($/ton)
3. **Percentage taxes**: Cost based on commodity price (% of market price)

**Green Steel Tariff Exemptions:**
- Steel producers with green steel certification can receive tariff reductions
- Eligibility: Furnace group must achieve minimum green steel grade level (configurable via `GREEN_STEEL_ELIGIBILITY_MINIMUM_LEVEL` in constants.py)
- Exemption specified as a decimal fraction (0.0-1.0) in tariff data:
  - 0.0 = no tariff applied (full exemption)
  - 1.0 = full tariff applied (no exemption)
  - 0.5 = 50% of tariff applied
  - Note: the master-input column is labelled `Green Steel Exemption [% of original tax]`, but
    values must be entered as decimals (e.g. `0.5`, not `50`), consistent with how `Tax [%]` is
    stored. No `/100` conversion is applied on read.
- **Only applies to steel tariffs**, not iron or other commodities
- For meta furnace groups (clusters): uses production-weighted average green steel grade

**Features:**
- Wildcard support for country groups (e.g., "any country to EU")
- Handles iron product families (hot metal, pig iron, DRI → "iron")
- Accumulates multiple taxes on same route
- Green steel exemptions reduce effective tariff: `effective_tariff = base_tariff × exemption_fraction`
- Skips routes already covered by a TRQ gateway (see `enforce_trqs_via_gateways` above)

**`From ISO3` / `To ISO3` syntax in the `Tariffs` master-input sheet.** Each entry resolves to a list of ISO3 codes via `_resolve_iso3_or_bloc_entry()`:

| Entry | Resolves to |
|-------|-------------|
| `<ISO3>` (e.g. `CHN`) | The single country |
| `<bloc>` (e.g. `EU`) | All countries flagged for that bloc on the `Country mapping` sheet |
| `NOT <bloc>` (e.g. `NOT EU`) | All countries *not* flagged for that bloc |
| `NOT <ISO3>` (e.g. `NOT DEU`) | All known countries except that one |
| `*` | Kept as a literal wildcard for downstream matching |

Supported trade-bloc names: `EU`, `EFTA/EUCU`, `OECD`, `NAFTA`, `Mercosur`, `ASEAN`, `RCEP`. Membership comes from boolean columns on each `CountryMapping` record (the legacy `Trade bloc definitions` sheet with one column per region marked `X` is no longer read). For `NOT <X>`, the resolver first looks up `<X>` as a bloc and falls back to treating it as an ISO3 code; an unknown `<X>` raises `ValueError` rather than silently producing the universe. Adding a new bloc requires (a) adding a boolean column to the `Country mapping` sheet, (b) adding the field to the `CountryMapping` model, and (c) appending the bloc name to `supported_blocs` in `read_tariffs()` / `find_iso3s_of_trade_bloc()`.

---

#### `fix_to_zero_allocations_where_distance_doesnt_match_commodity()`
**Purpose:** Enforces physical locality constraints on commodity transport at the LP stage.

**Three modes, selected by config:**

1. **Clustering disabled (legacy):** Distance-based fixing against `hot_metal_radius` — hot commodities zeroed beyond the radius, cold commodities zeroed inside it.
2. **Clustering enabled, iso3 keying:** Hot commodities are fixed to zero across iso3 boundaries; cold commodities remain free. Per-FG radius enforcement is deferred to disaggregation.
3. **Clustering enabled, plant-group keying** (`cluster_hot_metal_techs_by_plant_group=True`): hot commodities are additionally zeroed between meta-furnace-groups with different `plant_group_id`. Same-plant-group pairs, and pairs involving non-meta-FG process centres (suppliers / demand), fall through to the iso3 rule.

**Applied before solving** and reduces model size. Emits a `[LP HOT-METAL] Fixed to zero: X cross-country, Y cross-plant-group, Z missing-iso3 ...` summary per year.

---

#### Secondary Feedstock & Aggregated Constraints
**Purpose:** Limits scrap availability and enforces technology-specific feedstock ratios.

**Secondary feedstock constraints:**
- Regional limits on scrap, recycled materials
- Example: "Europe can only source 50M tons scrap/year"

**Aggregated constraints:**
- Technology-level min/max ratios
- Example: "EAF must use 50-90% scrap, 10-50% DRI"

---

### 4. `solve_steel_trade_lp_and_return_commodity_allocations()` - Solve & Extract Results

**What it does:**
1. Solves the LP optimization problem using Pyomo/HiGHS solver
2. Extracts optimal allocation values from solver variables
3. Maps LP results back to domain objects (plants, suppliers, demand centers)
4. Filters out negligible allocations (< 0.0001 tons)

**Error handling:**
- Returns empty allocations if solver fails to find optimal solution
- Logs detailed error messages with termination condition
- Continues simulation rather than crashing

**Debug output:**
- Writes `trade_lp_variables.csv` with all allocation details
- Logs statistics on non-zero allocations per commodity

**Outputs:** Dictionary mapping each commodity to its optimal allocation flows

---

### 5. Post-Processing Functions

#### `identify_bottlenecks()`
**Purpose:** Analyzes results to find capacity-constrained facilities.

**What it detects:**
- Furnace groups operating at or near maximum capacity
- Potential supply chain chokepoints
- Useful for understanding why demand might not be fully met

**Note:** Currently logs warnings but doesn't return structured data.

---

#### `adapt_allocation_costs_for_carbon_border_mechanisms()`
**Purpose:** Applies carbon border adjustment mechanisms (CBAM) to trade costs.

**What it models:**
- Export rebates when high-carbon-price region exports to low-carbon-price region
- Import adjustments when low-carbon-price region exports to high-carbon-price region
- Prevents double-counting when countries belong to multiple policy regions

**Generalized design:** Works with any carbon border mechanism (EU CBAM, OECD, etc.), not just EU-specific.

**Note:** Called separately from main setup, typically in allocation workflow.

---

## Configuration

### Key SimulationConfig Parameters

**Model behavior:**
- `lp_epsilon`: Solver tolerance (1e-3) - how close to constraints is acceptable
- `capacity_limit`: Production safety factor (0.95) - models realistic availability
- `active_statuses`: Which furnace states to include (e.g., ["operating", "mothballed"])

**Physical constraints:**
- `hot_metal_radius`: Maximum transport distance for hot commodities (~5 km by default). Enforced in several layers:
  1. **LP-build time** — international hot-commodity flows (different ISO3) are fixed to zero via `fix_to_zero_allocations_where_distance_doesnt_match_commodity()`. When `cluster_hot_metal_techs_by_plant_group=True`, hot flows between meta-FGs with different `plant_group_id` are additionally zeroed.
  2. **Clustering** — BOF FGs with no active BF/ESF/SR within radius in the same country are excluded from their cluster. BOF cluster capacity is capped at `min(physical_cap, Σ[reachable_BF_cap] / min_hot_metal_share)` so the LP cannot over-allocate.
  3. **Disaggregation pre-pass (strict)** — hot flows to destinations with a BOM minimum-share constraint are solved under strict radius with per-FG physical-capacity caps: radius-violating edges are omitted from the min-cost-flow graph, and when a geographic pocket can't physically meet its demand, destination demand is scaled down (producing downstream shortfalls the drift mechanism rebalances).
  4. **Drift + rebalance** — clusters whose actual joint-strict flow differs from LP get their Case 2 (cluster → demand) batches scaled by the drift factor and their Case 3 (supplier → cluster) batches rebalanced so per-cluster demand matches actual production × BOM ratio while per-supplier totals stay below LP (mine capacity hard-bounded).
  5. **Disaggregation (relabeling)** — hot flows without a binding minimum constraint that exceed the radius are relabeled to their cold equivalent (e.g. `dri_high` → `hbi_high`, `hot_metal` → `pig_iron`).
  6. **Post-disaggregation** — physical capacity and BOM consistency are validated for every FG; any BOF FG that received insufficient hot metal has its utilisation corrected downward.
- `closely_allocated_products`: Hot commodities limited to short distances (`hot_metal`, `dri_high`/`dri_mid`/`dri_low`, `liquid_iron`).
- `distantly_allocated_products`: Cold equivalents that ship globally (`pig_iron`, `hbi_high`/`hbi_mid`/`hbi_low`, `electrolytic_iron`).
- `enable_furnace_group_clustering`: When enabled, the LP works with meta-furnace-groups (clusters of same-technology-reductant-country FGs) to reduce problem size; all radius and minimum-ratio enforcement runs at disaggregation time as described above.
- `cluster_hot_metal_techs_by_plant_group`: When clustering is enabled, FGs whose `effective_primary_feedstocks` touch a closely-allocated commodity (as `metallic_charge` or `outputs`) cluster by `plant.ultimate_plant_group` instead of `iso3`. Stored on each `MetaFurnaceGroup.plant_group_id` and consumed by the LP's cross-plant-group zero-fix rule (#1 above). Non-affected techs keep iso3 keying; the `[CLUSTERING]` log line reports the split per year. No effect when `enable_furnace_group_clustering` is off.

See the "Disaggregation: Hot-Metal Radius + Minimum-Ratio Enforcement" section in `overview_trade_model.md` for full details.

**Economic data:**
- `primary_products`: Which commodities to optimize (["steel", "iron"])
- `transport_kpis`: Location-specific transport costs and emissions

---

## Integration with Simulation

The trade model is called during each simulation time step:

1. **Allocation Model** prepares input data (plants, demand, suppliers for current year)
2. **Setup phase** builds LP model with `set_up_steel_trade_lp()`
3. **Optional adjustments** apply carbon border mechanisms
4. **Solve phase** optimizes with `solve_steel_trade_lp_and_return_commodity_allocations()`
5. **Analysis phase** identifies bottlenecks and validates results
6. **Allocations** are returned to simulation for plant-level profit calculations

---

## For Detailed Implementation

For implementation details, parameter types, and code examples, see the comprehensive docstrings in each function within `src/steelo/domain/trade_modelling/set_up_steel_trade_lp.py`.
