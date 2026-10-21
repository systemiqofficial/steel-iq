# Plant Agent Model (PAM)

## Introduction

The Plant Agent Model (PAM) is an economic decision-making framework that simulates the behavior of steel and iron facilities as autonomous agents making strategic and operational decisions. Each year, furnace groups decide whether to continue operating, renovate, switch technologies, or close based on Net Present Value (NPV) calculations that account for production costs, carbon pricing, debt obligations, and stranded asset costs. Plant groups evaluate whether to expand capacity by adding new furnace groups at existing plants or building greenfield facilities. These decisions emerge from the interaction of market prices (derived from supply-demand cost curves), balance sheet constraints, technology transition rules, capacity limits, and policy interventions such as subsidies. PAM captures realistic industry transformation dynamics through debt accumulation (which creates financial lock-in), probabilistic adoption (modeling real-world hesitation), and supply chain constraints (limiting how fast new technologies can scale).

## How the Agents Decide

### Furnace-Group Strategy Evaluation
At its core, the furnace-group strategy evaluation is how each individual production unit (i.e., each furnace group) decides whether to keep operating as is, upgrade its technology, renovate, or close entirely. The process begins by checking the plant-level balance: if it is negative, no action is taken. It also skips any units already in "operating pre-retirement" status.

Next, it checks whether historic losses exceed a critical threshold: the capital expenditure multiplied by the unit's capacity. If this write-off point is breached, it issues a closure command. Otherwise, it calculates the net present value (NPV) for each allowed technology transition, including staying with the current technology. This NPV calculation first determines the Cost of Stranded Assets (COSA): the discounted operating margin the current technology would forgo after the switch's construction period. For each allowed technology, it applies available subsidies (capex, opex and debt), determines whether the transition would be a brownfield renovation (same technology, no construction lag) or a greenfield installation (new technology), retrieves the bill of materials, then computes the NPV with energy, carbon and by-products priced year by year through the reductant score series. For technology switches, the NPV is adjusted downward by subtracting COSA, which represents the penalty for abandoning current equipment.

Once NPVs are computed, the agent selects a technology: either deterministically (highest NPV) or probabilistically (weighted random based on NPVs). If no technology option is profitable (all NPVs ≤ 0), no action is taken. If the selected technology is the current one and the unit's lifetime has expired, it may trigger a renovation; if a different technology is selected, it evaluates a technology switch. All decisions respect capacity limits and affordability constraints, yielding a single command (or none) per furnace group each year.

Full detail: [Furnace Group Strategy](furnace_group_strategy.md).

### Plant-Group Expansion Consideration
Once individual furnace groups have made their operating decisions, the model steps back to the level of the entire plant cluster (a plant group). Here the question is: "Should this group add new capacity?" To answer that, it evaluates NPV-optimal technology choices for each plant in the group, considering location-specific costs, allowed technologies, and active subsidies (capex, opex, debt). This evaluation accounts for technology-specific constraints, such as requiring a hot-metal furnace for BOF, and incorporates carbon costs based on technology emission factors and carbon price trajectories.

The plant-group then selects the expansion option with the highest NPV, verifies it is positive and profitable, and checks whether the group's balance can cover the equity requirement. Expansion decisions can be either deterministic (accept if profitable) or probabilistic (weighted by investment-to-NPV ratio). Before approving the expansion, the model enforces capacity limits that distinguish between new plants and expansions/switches, ensuring each product type (iron or steel) respects its allocation targets. If all checks pass (profitability, affordability, capacity limits, and probabilistic acceptance), the group issues an AddFurnaceGroup command. Otherwise, it holds its current footprint. This expansion loop ensures simulated industry growth is economically justified and respects both financial constraints and sector-level capacity policies.

Full detail: [Plant Expansions](plant_expansions.md).

## Documentation Structure

### Getting Started

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

### Investment Gates

**[CO2 Storage Capacity Gate](co2_storage_gate.md)**
How each country's annual CO2 storage budget limits commitments to carbon capture and storage (CCS) plants. The gate fires at five decision points between an opportunity being identified and a plant operating, so plants cannot commit to more storage than the country has.

**[China Capacity-Replacement Policy](capacity_replacement_policy.md)**
How China's capacity-replacement ("swap") rules gate Chinese expansions, new plants and replacements: new capacity must be built against capacity retired elsewhere. Enabled with `--enable-capacity-policy`.

**[Capacity-Replacement Policy: Technical Reference](capacity_replacement_policy_reference.md)**
Module layout, the input sheets column by column, pool arithmetic and the output artefacts of the capacity-replacement policy.

### Economic Framework

**[Market Price Calculation](market_price_calculation.md)**
Explains the "proxy profit" method used to derive market prices from the Trade Module's cost-optimization results. Covers cost curve construction, market-clearing price identification, and profit calculation.

**[Economic Considerations](economic_considerations.md)**
Comprehensive overview of the economic factors shaping PAM decisions: price signals (spot vs forecast), profitability checks, balance sheet constraints, capacity limits, financing structure, carbon pricing impacts, probabilistic adoption, and subsidies.

**[Reductant Choice and Investment OPEX](reductant_choice_and_opex.md)**
How the model picks a reductant and prices it into investment decisions. Covers the reductant score (energy, carbon and by-products), the annual reductant pick and fleet-wide vote, and the year-wise score series behind the switch, renovation, expansion and new-plant NPVs.

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
- **COSA**: Cost of Stranded Assets (NPV of the operating margin the current technology forgoes after the switch's construction period; remaining debt is excluded because it is carried forward as legacy debt)
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


```{toctree}
:hidden:

agent_definitions
plant_agents_model_orchestration
furnace_group_strategy
plant_expansions
introduction_of_new_technologies
co2_storage_gate
capacity_replacement_policy
capacity_replacement_policy_reference
market_price_calculation
economic_considerations
reductant_choice_and_opex
debt_accumulation_impact
calculate_costs
trade_model_connector
```
