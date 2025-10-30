# Economic Considerations in PAM

## Overview

The Plant Agent Model (PAM) makes decisions based on economic signals and constraints that shape investment behavior. This document explains the key economic factors that drive technology transitions, capacity expansions, and closures.

---

## 1. Price Signals

### Spot Prices (Current Year)
Used for: Furnace group actual balance updates based on production volumes

**Source**: Derived from current demand intersecting the cost curve
```python
freeze_market_price = extract_price_from_costcurve(current_demand)
```

**Application**:
- Balance sheet calculations: `profit = (spot_price - unit_cost) × production`
- NPV calculations for technology switches
- Closure decisions (if losses exceed CAPEX threshold)

### Forecast Prices
Used for:
- Furnace group strategy evaluation (switches, renovations, closures)
- Plant group expansion decisions

**Method**: For each year from construction start through plant lifetime:
1. Calculate future steel demand (sum across all demand centers)
2. Calculate future iron demand (virgin iron demand, based on steel demand and scrap availability)
3. Apply each year's demand to the existing cost curve to extract price

```python
future_price_series = {"steel": [], "iron": []}  # Years: start_year to (year + construction_time + plant_lifetime)
```

**Rationale**: Technology switching and expansions require construction time (3-4 years). Investment decisions use forecasted prices based on projected demand at time of operation, not current prices.

### Price Dynamics
- Prices update annually based on supply/demand balance
- Higher demand → Higher market price → More expansions
- Lower demand → Lower prices → More closures
- Technology shifts change cost curve shape → Affect prices

---

## 2. Profitability Checks

### NPV-Based Decisions
All major decisions require positive Net Present Value:

**Formula**:
```
NPV = Σ [(Revenue_t - OPEX_t - Carbon_Cost_t - Debt_t) / (1 + r)^t] - Initial_Investment
```

**Thresholds**:
- **Technology switch**: `NPV_new - COSA > 0`
- **Renovation**: `NPV_renovation > 0`
- **Expansion**: `NPV_expansion > 0`
- **Closure**: If `historic_balance < -(CAPEX × capacity)`

### Balance Sheet Constraints
Decisions require sufficient accumulated profits:

**Investment Capacity**:
```python
equity_needed = capacity × capex × equity_share  # Default: 20% equity
can_afford = plant_group.total_balance >= equity_needed
```

**Example**:
- New furnace: 2.5 Mt capacity × $800/t CAPEX × 20% equity = $400M needed
- Plant group balance: $550M → Can afford
- Plant group balance: $300M → Cannot afford, expansion rejected

### Profitability vs Affordability
Two separate checks:
1. **Profitable?** NPV > 0 (Is the investment economically viable?)
2. **Affordable?** Balance ≥ equity_needed (Can we finance it?)

Both must be true for investment to proceed.

---

## 3. Capacity Constraints

### Annual Addition Limits
Prevents unrealistic industry growth:

**Separate Limits by Product**:
- `capacity_limit_steel`: Max steel capacity additions per year
- `capacity_limit_iron`: Max iron capacity additions per year

### Enforcement
```python
# Check if expansion would exceed limit
expansion_and_switch_capacity = installed_capacity - new_plant_capacity

if expansion_and_switch_capacity + new_expansion > capacity_limit:
    reject_expansion()  # Too much growth this year
```

### New Plant Allocation
Share of new capacity from greenfield vs brownfield:
```python
new_capacity_share_from_new_plants = 0.5  # 50% from new plants, 50% from expansions/switches
```

If demand < active capacity → Allow new plants regardless of share
(Prevents blocking greenfield when demand is growing)

---

## 4. Financing and Debt

### Capital Structure
Default financing mix:
```python
equity_share = 0.20  # 20% equity
debt_share = 0.80    # 80% debt
```

**Upfront Cost**:
- Equity portion must be paid from accumulated balance
- Debt portion financed at `cost_of_debt` interest rate

**Example**:
- Total CAPEX: $1,000M
- Equity (20%): $200M (deducted from balance)
- Debt (80%): $800M (repaid over plant lifetime)

### Debt Repayment
Uses straight-line amortization (constant principal, declining interest):

```python
annual_principal = total_debt / lifetime
annual_interest = average_debt_balance × cost_of_debt
annual_payment = annual_principal + annual_interest
```

**Impact on Profitability**:
- Debt repayment added to unit production cost
- Higher debt → Higher per-tonne cost → Lower competitiveness
- Debt fully repaid after `plant_lifetime` years (default 20)

### Cost of Debt by Country
Interest rates vary by location:
```python
cost_of_debt_dict = {
    "USA": 0.05,  # 5%
    "CHN": 0.04,  # 4%
    "IND": 0.08,  # 8%
    # ...
}
```

Lower rates → Lower financing costs → More competitive

---

## 5. Carbon Pricing

### Carbon Cost Integration
Carbon costs added to operating expenses:

```python
unit_carbon_cost = emissions_per_tonne × carbon_price
unit_production_cost = opex + carbon_cost + debt_repayment
```

**Sources of Emissions**:
- Direct (Scope 1): Process emissions, fossil fuel combustion
- Indirect (Scope 2): Purchased electricity
- Supply chain (Scope 3): Upstream material production

### Carbon Price Trajectory
Time-varying carbon prices affect technology competitiveness:

```python
carbon_cost_series = {
    2025: 50,   # $/tCO2
    2030: 100,
    2040: 200,
    2050: 300,
}
```

**Impact**:
- Rising prices → Favor low-carbon technologies (DRI+ESF, EAF with renewables)
- Falling prices → Favor cost-optimized technologies (BF with CCS)

### Technology-Specific Effects
Different emission intensities create technology tipping points:

| Technology | Emissions (tCO2/t steel) | Carbon Cost @ $100/tCO2 |
|------------|--------------------------|-------------------------|
| BF         | 2.0                      | $200/t                  |
| BF+CCS     | 0.4                      | $40/t                   |
| DRI+ESF    | 0.1                      | $10/t                   |
| EAF (scrap)| 0.3                      | $30/t                   |

Carbon price of $100/tCO2 → $190/t cost advantage for DRI+ESF over BF

---

## 6. Stochastic Elements

### Probabilistic Adoption
When enabled (`probabilistic_agents=True`), acceptance is probabilistic:

```python
acceptance_probability = exp(-investment_cost / NPV)
accept = random.random() < acceptance_probability
```

**Rationale**:
- Models real-world hesitation and uncertainty
- Higher cost/benefit ratio → Lower acceptance
- Prevents unrealistic instantaneous technology shifts

**Example**:
- Investment: $1,000M, NPV: $2,000M → P(accept) = exp(-0.5) = 60.7%
- Investment: $1,000M, NPV: $500M → P(accept) = exp(-2) = 13.5%
- Investment: $1,000M, NPV: $5,000M → P(accept) = exp(-0.2) = 81.9%

### Technology Selection
When multiple technologies have positive NPV:

**Deterministic Mode** (`probabilistic_agents=False`):
- Always choose highest NPV technology

**Probabilistic Mode** (`probabilistic_agents=True`):
- Weighted random selection: `P(tech_i) = NPV_i / Σ(NPV_j)`
- Allows suboptimal but profitable technologies to be chosen
- Models market diversity and imperfect information

---

## 7. Subsidies

### Subsidy Types
Three categories, each reducing costs:

1. **CAPEX Subsidies**: Reduce upfront investment
   ```python
   subsidized_capex = base_capex - absolute - (base_capex × relative)
   ```

2. **OPEX Subsidies**: Reduce operating costs
   ```python
   subsidized_opex = base_opex - absolute - (base_opex × relative)
   ```

3. **Debt Subsidies**: Reduce interest rates (absolute points only)
   ```python
   subsidized_rate = base_rate - absolute_points
   ```

### Time-Bounded Application
Subsidies active only within specified years:
```python
subsidy.start_year = 2025
subsidy.end_year = 2035

if current_year >= start_year and current_year <= end_year:
    apply_subsidy()
```

### Economic Impact
- Lower CAPEX → Higher NPV → More likely to switch/expand
- Lower OPEX → Higher profits → Faster balance sheet recovery
- Lower debt cost → Lower unit production cost → More competitive

**Strategic Use**:
- Target emerging technologies to accelerate adoption
- Temporary incentives bridge "valley of death" until learning-by-doing reduces costs
- Geographic targeting supports regional industrial policy

---

## Interaction Effects

### Price × Carbon Cost
High carbon prices shift competitive advantage:
- Low carbon price → Cheapest technology wins (usually BF/BOF)
- High carbon price → Cleanest technology wins (DRI/EAF)
- Moderate carbon price → Technology diversity (some switch, some don't)

### Balance Sheet × Investment
Strong balance sheets enable faster technology transition:
- Profitable plants → Accumulate balance → Can afford switches
- Unprofitable plants → Negative balance → Cannot switch, eventually close
- Creates path-dependency: Early adopters compound advantages

### Capacity Limits × Demand Growth
Interaction determines industry structure:
- High growth + tight limits → Favor expansions at existing plants
- High growth + loose limits → Favor new plant construction
- Low growth + any limits → Minimal investment, closures dominate

### Subsidies × Profitability
Subsidies most effective when:
- Base NPV slightly negative (subsidy tips to positive)
- Plant has balance to cover equity share
- Technology has long-term cost advantage after subsidy expires

---

## Summary

PAM decisions emerge from the interaction of:

1. **Market signals**: Spot and forecast prices determine revenues
2. **Profitability**: NPV calculations weigh costs vs benefits
3. **Affordability**: Balance sheet constrains investment capacity
4. **Capacity limits**: Prevent unrealistic industry growth
5. **Financing**: Debt structures affect per-unit costs
6. **Carbon pricing**: Emission costs shape technology competitiveness
7. **Uncertainty**: Probabilistic acceptance models real-world hesitation
8. **Subsidies**: Policy interventions accelerate transitions

These factors create emergent industry dynamics:
- **Technology transitions** accelerate when prices rise or carbon costs increase
- **Regional differences** emerge from varying financing costs and subsidies
- **Path dependency** creates winners and losers based on early performance
- **Market dynamics** balance growth (expansions) and consolidation (closures)

---

## Related Documentation

- **[Market Price Calculation](market_price_calculation.md)**: How market prices are derived from cost curves
- **[PlantAgentsModel Orchestration](plant_agents_model_orchestration.md)**: When these considerations are evaluated in the simulation
- **[Cost Calculation Functions](calculate_cost.md)**: Functions for NPV, COSA, subsidies, and carbon costs
