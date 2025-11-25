# Plant Agent Model (PAM) Overview

## Introduction

The Plant Agent Model (PAM) is an economic decision-making framework that simulates the behavior of steel and iron facilities as autonomous agents making strategic and operational decisions. Each year, furnace groups decide whether to continue operating, renovate, switch technologies, or close based on Net Present Value (NPV) calculations that account for production costs, carbon pricing, debt obligations, and stranded asset costs. Plant groups evaluate whether to expand capacity by adding new furnace groups at existing plants or building greenfield facilities. These decisions emerge from the interaction of market prices (derived from supply-demand cost curves), balance sheet constraints, technology transition rules, capacity limits, and policy interventions such as subsidies. PAM captures realistic industry transformation dynamics through debt accumulation (which creates financial lock-in), probabilistic adoption (modeling real-world hesitation), and supply chain constraints (limiting how fast new technologies can scale).

## Documentation Structure

### Getting Started

**[Agent Logic Overview](plant_agent_model_logic.md)**
Start here for a narrative introduction to how PAM works. Explains in plain language how furnace groups evaluate their strategy (renovate, switch, or close) and how plant groups decide on capacity expansion.

**[Agent Definitions](agent_definitions.md)**
High-level introduction to the domain model. Explains the three-tier hierarchy (PlantGroup → Plant → FurnaceGroup), key attributes and methods, how classes relate to each other, and the annual decision cycle.

**[PlantAgentsModel Orchestration](plant_agents_model_orchestration.md)**
Overview of how PAM runs within the simulation. Covers data flow (inputs from environment, intermediate values, outputs), the workflow execution sequence (market conditions → furnace strategy → plant group expansion → capacity enforcement), and integration with the Trade Module.


### Core Decision-Making Algorithms

**[Furnace Group Strategy](furnace_group_strategy.md)**
Detailed breakdown of `Plant.evaluate_furnace_group_strategy()` - the core algorithm that evaluates whether to renovate, switch technologies, or close. Covers the workflow including COSA calculation, NPV evaluation for each technology option, brownfield vs greenfield comparison, and subsidy application.

**[Plant Expansions](plant_expansions.md)**
Step-by-step documentation of `PlantGroup.evaluate_expansion()` - the strategic capacity growth algorithm. Covers the evaluation process including NPV calculation across all plants, balance sheet checks, probabilistic acceptance, capacity limit enforcement, and command generation.

**[Introduction of New Technologies](introduction_of_new_technologies.md)**
Explains how new technologies are introduced (activation years, new plants, expansions, technology switches) and how their growth is constrained by buildout limits. Covers annual capacity addition constraints (separate for steel and iron), real-world calibration, and supply chain constraints.

### Economic Framework

**[Market Price Calculation](market_price_calculation.md)**
Explains the "proxy profit" method used to derive market prices from the Trade Module's cost-optimization results. Covers cost curve construction, market-clearing price identification, and profit calculation.

**[Economic Considerations](economic_considerations.md)**
Comprehensive overview of the economic factors shaping PAM decisions: price signals (spot vs forecast), profitability checks, balance sheet constraints, capacity limits, financing structure, carbon pricing impacts, probabilistic adoption, and subsidies.

**[Debt Accumulation Impact](debt_accumulation_impact.md)**
Analyzes how preserving debt across technology switches creates realistic transition dynamics. Covers behavioral impacts (clustering at renovation boundaries, higher COSA, capital requirements), cascading debt from multiple switches, and model realism improvements.

**[Cost Calculation Functions](calculate_costs.md)**
Comprehensive reference for the functions in `calculate_costs.py`. Organized into functional modules (subsidies, cost breakdown, OPEX, debt repayment, cash flow, NPV, stranded assets, CAPEX, hydrogen costs, reductants). Includes key concepts (COSA, debt method, subsidies, LCOH) and integration points.

### Integration with Other Modules

**[Trade Model Connector](trade_model_connector.md)**
Documents the bridge between the Trade Module and PAM. Covers graph construction from LP results, cost propagation through supply chains, and updates to furnace group attributes (utilization rates, bill of materials, emissions).

**[New Plant Opening](../geospatial_model/new_plant_opening.md)**
Documents the bridge between the Geospatial Model and PAM. Explains how new greenfield plants are opened through business opportunity lifecycle (identification, multi-year NPV tracking, announcement, construction), location-technology pair evaluation, capacity limits, and probability filters that model real-world project risks.


## Key Concepts Quick Reference

### Agent Hierarchy
- **FurnaceGroup**: Individual production unit (makes renovation/switch/closure decisions)
- **Plant**: Physical facility containing multiple furnace groups (aggregates finances)
- **PlantGroup**: Company/owner containing multiple plants (makes expansion decisions)

### Decision Types
- **Renovate**: Upgrade current technology at end-of-life (brownfield CAPEX)
- **Switch Technology**: Change to different technology mid-lifetime or at renovation (greenfield CAPEX + COSA)
- **Close**: Shut down unprofitable furnace group
- **Expand**: Add new furnace group at existing plant or new plant

### Economic Metrics
- **NPV**: Net Present Value of future cash flows minus initial investment
- **COSA**: Cost of Stranded Assets (NPV of remaining debt + foregone profits)
- **LCOS**: Levelized Cost of Steel/Iron (production cost per tonne)
- **Market Price**: Derived from cost curve intersection with demand
- **Balance**: Accumulated profit/loss (plant-level and plant group-level)

### Time Horizons
- **Spot Prices**: Current year prices (for balance sheet updates)
- **Forecast Prices**: Future price series based on future demand (for techonolgy switch/expansion decisions)
- **Plant Lifetime**: Default 20 years between renovations
- **Construction Lag**: Typically 3-4 years for technology switching or new capacity

### Constraints
- **Balance Sheet**: Must have positive balance to invest
- **Capacity Limits**: Annual addition caps (separate for steel and iron)
- **Technology Transitions**: Only allowed switches defined in `allowed_furnace_transitions`
- **Debt Accumulation**: Legacy debt persists across technology switches
