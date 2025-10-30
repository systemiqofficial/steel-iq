# PAM Classes Overview - Domain Models

## Purpose

This document provides a high-level overview of the core domain model classes in `models.py` that are used by the Plant Agent Model (PAM). It focuses on understanding what each class represents and how they relate to each other.

## Core Agent Hierarchy

PAM uses a three-tier hierarchy of decision-making agents:

```
PlantGroup (Company/Owner)
    └── Plant (Physical facility)
        └── FurnaceGroup (Production unit)
            └── Technology (Process definition)
```

### Decision-Making by Level

- **PlantGroup**: Decides where to expand capacity across all owned plants
- **Plant**: Coordinates strategy across all furnace groups at a location
- **FurnaceGroup**: Decides when to renovate, switch technology, or close

---

## Core Classes

### 1. FurnaceGroup (models.py:898)
**What it represents**: A single production unit (furnace or production line) with a specific technology.

**Core concept**: The primary decision-making agent in PAM. Each furnace group evaluates its own profitability and decides whether to continue operating, renovate, switch to a new technology, or close.

**Key relationships**:
- Belongs to one `Plant`
- Uses one `Technology`
- Tracks `PointInTime` for renovation cycles
- Has a `bill_of_materials` (materials and energy inputs)

**Economic attributes**:
- `capacity`, `utilization_rate`, `production`
- `balance` (annual profit/loss), `historic_balance` (cumulative)
- `equity_share`, `cost_of_debt`, `total_investment`
- `unit_production_cost` (total cost per tonne)

### 2. Plant (models.py:2692)
**What it represents**: A physical production facility at a specific geographic location.

**Core concept**: A collection of furnace groups that share a location and infrastructure. Coordinates strategy decisions across all its furnace groups and aggregates their financial performance.

**Key relationships**:
- Belongs to one `PlantGroup`
- Contains multiple `FurnaceGroup` objects
- Located at one `Location`

**Coordination role**:
- Evaluates strategy for each furnace group
- Aggregates balance sheets from all furnaces
- Handles technology switches, renovations, and closures
- Creates new furnace groups for expansions

### 3. PlantGroup (models.py:4121)
**What it represents**: A company or ownership entity that owns multiple plants.

**Core concept**: Makes strategic investment decisions about where to expand capacity across all owned facilities. Represents the highest level of decision-making in PAM.

**Key relationships**:
- Contains multiple `Plant` objects
- Identified by `plant_group_id` (typically from GEM database)
- Aggregates `total_balance` across all plants

**Strategic role**:
- Evaluates expansion options across all plants
- Selects best plant and technology for new capacity
- Checks affordability and capacity limits
- Generates new plants for greenfield investments

### 4. Technology (models.py:821)
**What it represents**: A production process (e.g., BF, BOF, EAF, DRI).

**Core concept**: Defines the production characteristics, costs, and feedstock options for a specific technology.

**Key relationships**:
- Used by `FurnaceGroup`
- Contains multiple `PrimaryFeedstock` options

**Key attributes**:
- `name` (e.g., "BF", "EAF", "EAF")
- `product` ("steel" or "iron")
- `capex`, `capex_no_subsidy`
- `capex_type` ("greenfield" or "brownfield")
- `dynamic_business_case` (list of feedstock options)

### 5. PrimaryFeedstock (models.py:747)
**What it represents**: A specific input combination for a technology (metallic charge + reductant).

**Core concept**: Defines one way to operate a technology. For example, an EAF can use scrap+electricity or DRI+electricity as different feedstock options.

**Key relationships**:
- Belongs to a `Technology` (via `dynamic_business_case`)
- Has `secondary_feedstock` (additional materials)
- Has `energy_requirements` (by energy type)

**Key attributes**:
- `metallic_charge` (e.g., "scrap", "dri", "hot_metal")
- `reductant` (e.g., "coke", "hydrogen", "natural_gas")
- `required_quantity_per_ton_of_product`
- `outputs` (products and by-products)

---

## Supporting Classes

### 6. PointInTime (models.py:131)
**What it represents**: Lifetime tracking for renovation cycles.

**Core concept**: Plants don't have absolute ages - they undergo renovation cycles every N years (default 20). This class tracks position within the current cycle.

**Key relationships**:
- Used by `FurnaceGroup` (as `lifetime` attribute)
- Contains a `TimeFrame` (start and end years)

**Key computation**:
- For a 116-year-old plant with 20-year lifetime: 116 mod 20 = 16 years elapsed → 4 years remaining until renovation

### 7. Location (models.py:254)
**What it represents**: Geographic coordinates and country identification.

**Key relationships**:
- Used by `Plant`
- Contains pre-calculated distances to other countries

**Key attributes**:
- `lat`, `lon` (coordinates)
- `iso3` (country code)
- `distance_to_other_iso3` (for transport costs)

### 8. Subsidy (models.py:5479)
**What it represents**: Financial incentive for technology adoption.

**Key relationships**:
- Applied to `FurnaceGroup` (stored in `applied_subsidies`)
- Filtered by year and technology

**Key attributes**:
- `iso3` (country), `technology_name`, `cost_item` (capex/opex/debt)
- `start_year`, `end_year` (time-bounded)
- `absolute_subsidy` (fixed amount)
- `relative_subsidy` (percentage reduction)

### 9. TechnologyEmissionFactors (models.py:383)
**What it represents**: Emission intensities for a specific technology-feedstock combination.

**Key relationships**:
- Used by `FurnaceGroup` to calculate emissions
- Matched with `bill_of_materials` entries

**Key attributes**:
- `technology`, `metallic_charge`, `reductant`
- `boundary` (emission scope)
- `direct_ghg_factor`, `indirect_ghg_factor`

---

## Class Relationship Diagram

```
PlantGroup
    │
    ├── plant_group_id: str
    ├── total_balance: float
    └── plants: list[Plant] ────────────────┐
                                            │
                                            ▼
                                        Plant
                                            │
                                            ├── plant_id: str
                                            ├── location: Location
                                            ├── balance: float
                                            ├── technology_unit_fopex: dict
                                            ├── carbon_cost_series: dict[Year, float]
                                            └── furnace_groups: list[FurnaceGroup] ─────┐
                                                                                        │
                                                                                        ▼
                                                                                FurnaceGroup
                                                                                        │
                                                                                        ├── furnace_group_id: str
                                                                                        ├── capacity: Volumes
                                                                                        ├── status: str
                                                                                        ├── utilization_rate: float
                                                                                        ├── balance: float
                                                                                        ├── historic_balance: float
                                                                                        ├── equity_share: float
                                                                                        ├── cost_of_debt: float
                                                                                        ├── legacy_debt_schedule: list[float]
                                                                                        ├── applied_subsidies: dict[str, list[Subsidy]]
                                                                                        │
                                                                                        ├── lifetime: PointInTime
                                                                                        │       └── time_frame: TimeFrame
                                                                                        │
                                                                                        ├── technology: Technology
                                                                                        │       ├── name: str
                                                                                        │       ├── product: str
                                                                                        │       ├── capex: float
                                                                                        │       ├── capex_type: str
                                                                                        │       ├── bill_of_materials: dict
                                                                                        │       └── dynamic_business_case: list[PrimaryFeedstock]
                                                                                        │                                           │
                                                                                        │                                           ├── metallic_charge: str
                                                                                        │                                           ├── reductant: str
                                                                                        │                                           ├── secondary_feedstock: dict
                                                                                        │                                           ├── energy_requirements: dict
                                                                                        │                                           └── outputs: dict
                                                                                        │
                                                                                        └── bill_of_materials: dict
                                                                                                ├── materials: dict
                                                                                                └── energy: dict
```
---

## Key Concepts

### 1. Debt Accumulation
When a furnace group switches technologies before its debt is fully repaid:
- Old technology's remaining debt is stored in `legacy_debt_schedule`
- New technology creates new debt based on its CAPEX
- Total debt = new debt + legacy debt (for overlapping years)
- This makes early switches expensive and affects COSA calculations

### 2. Renovation Cycles
Plants don't retire after N years - they renovate:
- **First cycle** (age < 20 years): Uses greenfield CAPEX (full cost)
- **Subsequent cycles** (age >= 20 years): Uses brownfield CAPEX (~30% of greenfield)
- Renovation resets the lifetime clock but keeps the same technology
- Position within cycle: `actual_age mod plant_lifetime`

### 3. Cost Normalization
All costs are normalized to **per-unit-of-production** basis (USD/tonne):
- CAPEX → divided by production over lifetime
- OPEX → inherently per tonne
- Debt repayment → annual payment divided by annual production
- Carbon costs → emissions per tonne × carbon price

### 4. Subsidies
Three types, each with absolute and relative components:
- **CAPEX**: Reduces upfront investment → affects NPV and affordability
- **OPEX**: Reduces operating costs → affects profitability
- **Debt**: Reduces interest rates → affects debt repayment costs

Time-bounded: Only applied if `start_year <= current_year <= end_year`

### 5. Probabilistic Decisions
When enabled, technology switches and expansions use probabilistic acceptance:
```
P(accept) = exp(-investment_cost / NPV)
```
- Higher cost relative to benefit → Lower acceptance probability
- Models real-world hesitation and risk aversion

---

## Integration with Other Modules

### Cost Calculation Module (`calculate_costs.py`)
Provides functions for NPV, OPEX, debt repayment, COSA, and subsidies.
Called by: `FurnaceGroup.optimal_technology_name()`, `Plant.evaluate_furnace_group_strategy()`, `PlantGroup.evaluate_expansion()`

### Emissions Module (`calculate_emissions.py`)
Calculates emissions from bill of materials and technology emission factors.
Called by: `FurnaceGroup.set_emissions_based_on_allocated_volumes()`, `FurnaceGroup.optimal_technology_name()`

### Trade Module (`trade_modelling/`)
Optimizes production and trade flows based on costs and demand.
Reads: `FurnaceGroup.capacity`, `unit_production_cost`
Updates: `FurnaceGroup.allocated_volumes`, `utilization_rate`

---

## Summary

The PAM domain model consists of:

1. **Three-tier agent hierarchy**: PlantGroup → Plant → FurnaceGroup
2. **Economic decision-making**: NPV-based with debt accumulation and subsidies
3. **Renovation cycles**: Plants renovate every N years rather than retiring
4. **Flexible feedstocks**: Each technology can use multiple input combinations
5. **Event sourcing**: All state changes recorded as domain events

---

## Related Documentation

For detailed cost calculation logic, see **[Cost Calculation Functions](calculate_cost.md)**.

For decision-making algorithms, see **[Furnace Group Strategy](furnace_group_strategy.md)** and **[Plant Expansions](plant_expansions.md)**.
