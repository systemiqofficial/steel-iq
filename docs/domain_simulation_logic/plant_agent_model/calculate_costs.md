# Cost Calculation Module

## Overview

The Cost Calculation Module (`calculate_costs.py`) is the financial analysis engine of the Plant Agent Model (PAM). It bridges detailed cost data (materials, energy, labor) with high-level economic assessments (NPV, LCOH, COSA) to enable informed decision-making:

- **Operational decisions**: What to produce and at what cost, by computing unit production costs combining OPEX, carbon costs, and debt repayment for each furnace group.
- **Investment decisions**: Whether to build new capacity and where, by evaluating business opportunities for capacity expansion through comprehensive NPV analysis. 
- **Technology decisions**: When to switch to cleaner or cheaper technologies, by calculating NPV and stranded asset costs (COSA) for all technology options. 
- **Strategic decisions**: How subsidies and carbon costs affect competitiveness, by modeling time-varying CAPEX, OPEX, and debt subsidies alongside carbon pricing impacts. 

All calculations are normalized to per-unit-of-production basis, support time-varying subsidies, and account for debt financing with straight-line amortization, making this module central to realistic steel industry economic modeling.

## Functional Modules

### Subsidy Management
Handles time-varying subsidies for CAPEX, OPEX, and cost of debt. Subsidies are automatically filtered by year and applied to reduce costs. Bounds checking prevents costs from going negative or below floors (CAPEX/OPEX ≥ 0, Cost_of_Debt ≥ Risk_Free_Rate).

Three types of subsidies are supported:

1. **CAPEX subsidies**: Reduce upfront investment costs
   - Absolute: Fixed dollar amount per unit (e.g., $50/t)
   - Relative: Percentage reduction (e.g., 20% off)

2. **OPEX subsidies**: Reduce operating costs
   - Absolute: Fixed dollar amount per unit (e.g., $10/t)
   - Relative: Percentage reduction (e.g., 15% off)

3. **Cost of Debt subsidies**: Reduce interest rates
   - Absolute only: Percentage point reduction (e.g., -2% points)
   - Relative subsidies are ignored for debt

All subsidies are time-bound with `start_year` and `end_year`, automatically filtered each simulation year.

#### Negative Subsidies (Taxes/Penalties)

Subsidies can have negative `subsidy_amount` values, which act as taxes or penalties that **increase** costs instead of reducing them.

**Formula:** `cost_with_subsidy = cost - subsidy_amount`

| subsidy_amount | Calculation | Effect |
|----------------|-------------|--------|
| +100 (positive) | `500 - 100 = 400` | Cost decreases |
| -100 (negative) | `500 - (-100) = 600` | Cost increases |
| -25% relative | `400 - (400 × -0.25) = 500` | 25% cost increase |

**Use cases:**
- Carbon penalties on high-emission technologies
- Environmental surcharges
- Regulatory fees

**Floor behavior:** The final cost cannot go negative (no "money back"). CAPEX/OPEX floor at 0, COST OF DEBT floors at risk-free rate.

**Functions:**
- `filter_subsidies_for_year()` - Filters subsidies to only those active in a specific year
- `collect_active_subsidies_over_period()` - Collects unique subsidies active during any year in a period (with deduplication)
- `calculate_capex_with_subsidies()` - Applies absolute and relative subsidies to CAPEX
- `calculate_opex_with_subsidies()` - Applies absolute and relative subsidies to OPEX (floor at 0)
- `calculate_opex_list_with_subsidies()` - Generates time-varying OPEX with year-specific subsidies
- `calculate_debt_with_subsidies()` - Cost-of-debt subsidies; absolute point reductions only; floored at risk-free rate

#### Subsidy Filtering Functions

Two functions handle subsidy filtering for different use cases:

**`filter_subsidies_for_year(subsidies, year)`** - Use for single-year filtering:
- CAPEX subsidies (applied at construction start)
- Debt subsidies (applied at financing decision)
- Current-year OPEX tracking

**`collect_active_subsidies_over_period(subsidies, start_year, end_year)`** - Use for multi-year collection:
- OPEX subsidies over plant lifetime (for NPV calculations)
- Any scenario requiring subsidies across multiple years

The period function uses `set()` internally for deduplication - a subsidy spanning 2025-2030 appears once, not six times. The `end_year` is exclusive (matches Python `range()` convention).

### Cost Breakdown Analysis
Extracts and processes bills of materials (BOM) to accurately assess the material and energy costs associated with production. Returns nested dictionaries with cost breakdowns by output product or feedstock.

**Functions:**
- `calculate_cost_breakdown()` - Calculates normalized cost breakdown per unit of production
- `calculate_cost_breakdown_by_feedstock()` - Detailed cost breakdown for each feedstock option

### Operating Expenditure Calculation
Computes both variable and fixed operating expenditures based on input cost data and capital investment ratios. Returns 0 for unit costs when utilization is zero (no production means no unit cost).

**Functions:**
- `calculate_variable_opex()` - Weighted average of material and energy costs. Accepts flexible input formats: unit_cost as float or `{"Value": float, "Unit": str}`, and uses `demand` or `demand_share_pct` for weighting.
- `calculate_unit_total_opex()` - Sums variable and fixed OPEX per unit (returns 0 if utilization is 0)
- `calculate_unit_production_cost()` - Total unit cost including OPEX, carbon costs, debt repayment, and secondary output adjustments
- `calculate_cost_adjustments_from_secondary_outputs()` - Computes average per-unit cost adjustment from secondary outputs (by-products)

### Debt Repayment
Generates debt repayment schedules using straight-line amortization (constant principal, declining interest) and calculates debt payment breakdowns.

The module uses **straight-line amortization** with constant principal and declining interest:
- Principal is repaid equally each year: `Principal = Total Debt / Lifetime`
- Interest is calculated on average debt balance: `Interest = ((Debt_Start + Debt_End) / 2) × Cost_of_Debt`
- Total repayment = Principal + Interest (declines over time as debt decreases)

This method differs from annuity loans where payments are constant. Here, payments start higher and decrease over time.

**Functions:**
- `calculate_debt_repayment()` - Generates full yearly debt repayment schedule
- `calculate_current_debt_repayment()` - Calculates single year's debt payment
- `calculate_debt_report()` - Breaks down debt into principal and interest components for reporting

**Note:** `years_elapsed` is 1-indexed (first operational year = 1). If `lifetime_expired` is `True` or `debt == 0`, returns 0.

### Cash Flow Analysis
Calculates cash flows over time for profitability analysis and stranded asset cost calculations. Validates array lengths before operations and raises ValueError for mismatches (fail-fast for data consistency).

**Functions:**
- `calculate_gross_cash_flow()` - Cash flow as (Revenue - OPEX) per period
- `calculate_net_cash_flow()` - Subtracts debt from gross cash flow (for NPV)
- `calculate_lost_cash_flow()` - Adds debt to gross cash flow (for COSA)

**Note:** If unit OPEX == 0 for a period, the model assumes no production and sets cash flow to 0 (revenue is not realized).

### Investment Evaluation - NPV
Provides tools to calculate net present value (NPV) for technology investments, supporting both individual and batch calculations for business opportunities. Returns -1e9 for invalid inputs (NaN values in cash flows or cost_of_equity ≤ -1.0) to signal non-viability.

**Functions:**
- `calculate_npv_costs()` - Basic NPV from equity investor perspective
- `calculate_npv_full()` - Comprehensive NPV with construction lag, carbon costs, and infrastructure
- `calculate_business_opportunity_npvs()` - Batch NPV calculation for multiple sites/technologies

**Note:** `construction_time` years are prepended to OPEX and debt schedules. `price_series` must include the same lead periods so its length matches the lagged OPEX/debt.

### Stranded Asset Analysis
Calculates the Cost of Stranded Asset (COSA) when switching technologies before end-of-life, accounting for remaining debt obligations and foregone operating profits.

When switching technologies before end-of-life, COSA represents the NPV of:
1. **Remaining debt obligations** - Must still be repaid even if asset is abandoned
2. **Foregone operating profits** - Lost future revenue from current technology

Formula: `COSA = NPV(Gross_Cash_Flow + Remaining_Debt)`

COSA is subtracted from the NPV of the new technology to determine if a switch is economically viable.

**Functions:**
- `calculate_cost_of_stranded_asset()` - NPV of losses from stranding an asset
- `stranding_asset_cost()` - Full COSA calculation combining debt obligations and foregone profits

### CAPEX Calculations
Handles capital expenditure calculations including subsidies and learning-by-doing cost reductions. Returns 1.0 (no reduction) when capacity_zero is 0 to avoid division by zero.

**Functions:**
- `calculate_capex_with_subsidies()` - Applies absolute and relative subsidies to CAPEX
- `calculate_capex_reduction_rate()` - Learning-by-doing cost reductions based on the learning curve logic (the more mature a technology, the cheaper its unit cost). The learning coefficient for CAPEX reduction is set to ca. -0.04, which corresponds to approximately 7% cost reduction per doubling of capacity.

### Hydrogen Cost Calculations
Calculates levelized cost of hydrogen (LCOH) with regional ceilings and intraregional trade options.

For hydrogen-based technologies, LCOH represents the full cost of hydrogen production:

```
LCOH ($/kg) = Energy_Consumption (kWh/kg) × Electricity_Price ($/kWh) + CAPEX_OPEX ($/kg)
```

The module supports:
- Regional hydrogen price ceilings (percentile-based)
- Intraregional hydrogen trade with transport costs
- Country-level LCOH calculations with electrolyzer efficiency curves

**Note:** This LCOH calculation is analogous to the one used in the geospatial model, but based on grid power prices instead of custom renewable energy costs. Functions are duplicated in `geospatial_calculations.py` and changes must be manually synchronized between both locations. See [Priority Location Selection](../geospatial_model/priority_location_selection.md) for more details on ceilings and trade.

**Functions:**
- `calculate_lcoh_from_electricity_country_level()` - LCOH per country from electricity prices
- `calculate_regional_hydrogen_ceiling_country_level()` - Regional hydrogen price ceilings (percentile-based)
- `apply_hydrogen_price_cap_country_level()` - Applies price caps with intraregional trade options

**Note:** Functions are duplicated in `geospatial_calculations.py` and changes must be manually synchronized between both locations.

### Energy and Reductant Selection
Identifies the most cost-effective reductant across different production paths based on energy costs.

**Functions:**
- `calculate_energy_costs_and_most_common_reductant()` - Identifies most cost-effective reductant across all metallic inputs

## Cross-Cutting Solutions

### Cost Normalization
- All costs normalized to **per-unit-of-production** basis for comparability
- Handles both absolute ($/t) and relative (%) subsidies
- Ensures minimum values: CAPEX/OPEX ≥ 0, Cost_of_Debt ≥ Risk_Free_Rate

### Time Series Treatment
- Construction time lags: Prepends zeros to schedules for projects under construction
- Year-specific subsidies: Automatically filters subsidies active in each year
- Variable time horizons: Supports both full lifetime and remaining lifetime calculations

### Edge Case Handling
- Zero utilization → Returns 0 for unit costs (no production means no unit cost)
- Invalid NPV inputs → Returns -1e9 (large negative value signals non-viability)
- Missing data → Returns 0 or empty collections (graceful degradation)
- Array length mismatches → Raises ValueError (fail-fast for data consistency)


## Integration Points

This module is called by the following PAM components:

| Caller | Function Used | Purpose |
|--------|---------------|---------|
| `FurnaceGroup.optimal_technology_name()` | `stranding_asset_cost()`, `calculate_npv_full()` | Technology switching NPV analysis |
| `FurnaceGroup.unit_production_cost` | `calculate_unit_production_cost()`, `calculate_current_debt_repayment()` | Current operating cost |
| `FurnaceGroup.debt_repayment_per_year` | `calculate_debt_repayment()` | Debt schedule for COSA calculations |
| `Plant.evaluate_expansion()` | `calculate_business_opportunity_npvs()` | New plant investment analysis |
| `PlantAgentsModel.run()` | `calculate_lcoh_from_electricity_country_level()`, `calculate_regional_hydrogen_ceiling_country_level()`, `apply_hydrogen_price_cap_country_level()` | Hydrogen cost modeling |
