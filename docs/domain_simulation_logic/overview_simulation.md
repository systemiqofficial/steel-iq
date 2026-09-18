# Steel Industry Simulation - Annual Cycle Overview

## Introduction

The steel industry simulation is an agent-based model that simulates the global transformation of steel and iron production over multiple decades. Each year, the simulation executes a sequence of modules that together determine:

- **What to produce**: Trade Module optimizes global material flows
- **How to produce**: Plant Agent Model decides on technology choices
- **Where to produce**: Geospatial Model identifies optimal locations for new plants
- **At what cost**: Environment tracks market dynamics and cost curves

The simulation captures realistic industry dynamics through:
- Economic decision-making based on Net Present Value (NPV)
- Technology transitions constrained by buildout limits and supply chains
- Financial lock-in through debt accumulation across technology switches
- Probabilistic adoption reflecting real-world investment hesitation
- Geospatial optimization for renewable energy and location costs

## Annual Simulation Cycle

Each simulation year executes the following workflow:

### 1. Start Simulation

**Purpose**: Initialize the simulation year and update all plants with current market conditions

**Process**:
The simulation year begins by updating the environment year counter and calculating annual steel demand forecasts. Fixed operating costs are assigned per country, CAPEX renovation shares are updated, and hydrogen costs are computed considering inter- and intraregional trade. Each plant receives technology-specific unit operating costs, location-based hydrogen costs, and carbon cost series based on country carbon pricing policy (with fallback handling for missing country codes). Finally, furnace groups that have completed construction transition from construction to operating status.

**Key Output**: Active operating plants with current-year costs, demand, and carbon pricing ready for production allocation

**For Details**: See [Environment](environment.md)

### 2. Trade (or Allocation) Model (TM)

**Purpose**: Determine optimal global material flows to meet demand at minimum cost

**Process**:
The Trade Module solves a linear programming optimization problem representing the global steel value chain as a network of suppliers, furnace groups, and demand centers. It minimizes total system cost (production, transport, tariffs, energy) subject to capacity limits, material balance constraints, demand fulfillment, and trade policy. The model handles flexible feedstocks (e.g., EAF can use scrap or DRI), enforces technology-specific input ratios and quality requirements, and applies trade tariffs, quotas, and carbon border adjustments using the HiGHS solver.

**Key Outputs**:
- Production volumes allocated to each furnace group
- Capacity utilization rates from optimization
- Optimal commodity flows between all facilities worldwide

**For Details**: See [Trade Model Overview](trade_model/overview_trade_model.md)

### 3. Plant Agent Model (PAM)

**Purpose**: Make economic decisions for all plants and plant groups

**Process**:
The Plant Agent Model uses data from the Trade Model obtained via the TM-PAM Connector (e.g., utilization rates, bills of materials, emissions) to evaluate economic viability through NPV calculations and issue strategic commands. After establishing market conditions (carbon costs, current and forecast prices), the model evaluates each furnace group for optimal action: renovate at end-of-life, switch technology mid-lifetime (accounting for stranded assets), close if unprofitable, or continue operations. Plant groups then evaluate expansion opportunities using 3-year price forecasts to identify the best NPV options across all plants and technologies. All capacity additions are subject to annual buildout limits, with separate constraints for steel and iron production to reflect supply chain realities.

**Key Outputs**:
- Strategic commands (renovate, switch, close, expand)
- Updated plant and plant group financial balances
- Capacity additions tracked against buildout limits

**For Details**: See [Plant Agent Model Overview](plant_agent_model/overview_plant_agent_model.md)

### 4. Geospatial Model (GEO)

**Purpose**: Identify optimal locations globally for new iron and steel plants

**Process**:
The Geospatial Model combines high-resolution renewable energy cost calculations (0.25-degree resolution from weather data and storage optimization) with multi-factor location analysis including energy costs, local CAPEX, transportation expenses, infrastructure requirements, and land suitability. It ranks global locations by lifetime cost and selects top candidates with country-level diversity, while calculating hydrogen costs with inter- and intraregional trade. Promising location-technology combinations are tracked through a multi-year lifecycle (CONSIDERED → ANNOUNCED → CONSTRUCTION), subject to NPV viability, probabilistic filters, and capacity constraints. The new plant opening process serves as the bridge between the Geospatial Model and the Plant Agent Model, evaluating business opportunities through multi-year NPV tracking and managing the full project lifecycle from identification through construction. Plants transition to OPERATING status after construction lag completes.

**Key Outputs**:
- Ranked candidate locations with site-specific costs
- New plants in CONSTRUCTION status
- Location-specific energy and hydrogen costs

**For Details**: See [Geospatial Model Overview](geospatial_model/overview_geospatial_model.md)

### 5. Handlers

**Purpose**: Execute commands issued by all economic models (Trade, Plant Agent, Geospatial)

**Process**:
Handlers translate strategic commands into state changes by updating furnace group attributes (technology, capacity, status, debt), creating new furnace groups for expansions, and recording domain events for checkpoint and replay capabilities.

**Key Output**: Updated simulation state reflecting all approved decisions

### 6. Data Collector

**Purpose**: Gather simulation outputs for analysis and visualization

**Process**:
The data collector captures annual snapshots of production volumes, capacities, emissions, and costs from all operating plants (construction plants excluded), aggregated by technology, region, country, and year for post-simulation analysis and visualization.

**Key Output**: Time series data for post-simulation analysis

### 7. End of Simulation Year

**Purpose**: Update annual aging and finalize state transitions in `finalise_iteration`

**Process**:
The year concludes by incrementing age counters for all furnace groups, executing pending technology switches with new technology attributes, closing furnace groups marked for shutdown, updating debt balances (principal and interest), and decrementing construction countdowns for plants under construction.

**Key Output**: Simulation state ready for next year

### 8. Checkpoint

**Purpose**: Save complete simulation state for restart and analysis

**Process**:
The checkpoint system serializes all plants, furnace groups, plant groups, issued commands, and environment state (prices, capacities, limits) to enable simulation restart from any year and facilitate detailed analysis of decision history.

**Key Output**: Checkpoint file enabling simulation restart from any year

### 9. Next Year

**Purpose**: Advance simulation to next year

**Process**:
The simulation advances to the next year, incrementing the year counter while carrying forward plant and plant group balances. Renovated furnaces restart debt amortization cycles, and the workflow returns to Step 1 where construction plants may transition to operating status.

**Loop**: Returns to Step 1 for next simulation year

## Module Integration

The simulation modules form an interconnected system:

```
Environment
  ├── Tracks cost curves and market prices
  ├── Provides demand forecasts
  └── Aggregates regional capacities

Trade Module
  ├── Reads: Operating furnace groups, demand centers
  ├── Solves: Optimal global material flows
  └── Outputs: Utilization rates, BOMs, costs

Plant Agent Model
  ├── Reads: Market prices, BOMs, utilization rates
  ├── Decides: Renovate, switch, close, expand
  └── Outputs: Commands for state changes

Geospatial Model
  ├── Reads: Market prices, existing locations
  ├── Optimizes: Location-specific costs
  └── Outputs: New plants for construction

Data Collector
  ├── Reads: All operating plants and furnace groups
  └── Outputs: Time series for analysis
```

**Annual Feedback Loop**:
1. Trade Module determines what each plant produces
2. Plant Agent Model decides which technologies to use
3. Geospatial Model determines where new capacity is built
4. Environment updates cost curves based on new capacity mix
5. Updated cost curves influence next year's Trade Module optimization

## Key Simulation Features

### Economic Realism
- **NPV-based decisions**: All investments evaluated on financial viability
- **Debt accumulation**: Legacy debt persists across technology switches, creating financial lock-in
- **COSA (Cost of Stranded Assets)**: Switching technologies early accounts for remaining debt and foregone profits
- **Market dynamics**: Prices derived from supply-demand cost curves

### Technology Transition Realism
- **Buildout limits**: Annual capacity addition caps prevent overnight industry transformation
- **Probabilistic adoption**: Not all economically viable projects are realized (reflects real-world hesitation)
- **Supply chain constraints**: Separate limits for steel and iron reflect different infrastructure requirements
- **Allowed transitions**: Technology switches constrained by technical feasibility matrix

### Geospatial Optimization
- **Custom renewable energy**: Site-specific LCOE from weather data and storage optimization
- **Multi-factor location scoring**: Energy, CAPEX, transport, infrastructure, land suitability
- **Hydrogen economics**: Regional price ceilings with inter-/intraregional trade modeling

### Trade Realism
- **Flexible feedstocks**: Technologies can use alternate inputs (e.g., scrap vs DRI in EAF)
- **Quality consistency**: Input quality determines output quality
- **Policy instruments**: Tariffs, quotas, carbon border adjustments
- **Regional constraints**: Scrap availability and other secondary feedstock limits

## Related Documentation
- [Plant Agent Model](plant_agent_model/overview_plant_agent_model.md)
- [Geospatial Model](geospatial_model/overview_geospatial_model.md)
- [Trade Model](trade_model/overview_trade_model.md)
- [Environment Module](environment.md)