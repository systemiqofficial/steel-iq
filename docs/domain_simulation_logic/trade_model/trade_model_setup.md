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

---

### 3. Constraint Functions

#### `enforce_trade_tariffs_on_allocations()`
**Purpose:** Applies trade policy restrictions to cross-border flows.

**Supports three tariff types:**
1. **Volume quotas**: Maximum tons per year on a route
2. **Absolute taxes**: Fixed cost per ton ($/ton)
3. **Percentage taxes**: Cost based on commodity price (% of market price)

**Features:**
- Wildcard support for country groups (e.g., "any country to EU")
- Handles iron product families (hot metal, pig iron, DRI → "iron")
- Accumulates multiple taxes on same route

---

#### `fix_to_zero_allocations_where_distance_doesnt_match_commodity()`
**Purpose:** Enforces physical distance constraints on commodity transport.

**Logic:**
- **Hot metal**: Can only travel short distances (~100km) due to cooling
- **Pig iron/steel**: Made for long-distance transport
- Fixes LP variables to zero for infeasible distance-commodity pairs

**Applied before solving** and reduces model size.

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
- `hot_metal_radius`: Maximum hot metal transport distance (~100km)
- `closely_allocated_products`: Commodities limited to short distances
- `distantly_allocated_products`: Commodities requiring long transport

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
