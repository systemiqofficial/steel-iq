# Market Price Calculation and Proxy Profit

## Overview

The Plant Agent Model (PAM) relies on market prices to calculate plant profitability and balance sheets. However, the Trade Module doesn't simulate a true market price - it optimizes global allocation based on production costs. This document explains how "proxy profit" approximates realistic market dynamics.

---

## The Challenge

**Trade Module Optimization**:
- Uses levelized cost of steel/iron (LCOS) as the bid price for suppliers
- Minimizes global cost of allocation to meet demand
- Naturally captures competitive advantage based on production costs
- **Does NOT reflect**: Profit maximization or true market price/value of commodities

**Problem**: Without market prices, we can't calculate realistic profits or balance sheets for plant agents.

---

## Solution: Proxy Profit Method

### Step 1: Derive Cost Curve

Aggregate all plants' production costs to create a supply curve:

```
Cost ($/t)  ↑
           │     ╱──────
           │    ╱
           │   ╱
           │  ╱
           │ ╱
           │╱___________→ Cumulative Capacity (t)
```

- X-axis: Cumulative production capacity (sorted by cost, lowest to highest)
- Y-axis: Levelized cost of steel/iron (LCOS) for each plant
- Result: Upward-sloping supply curve

### Step 2: Find Market-Clearing Price

Identify where the supply curve intersects demand:

```
Cost ($/t)  ↑
Market price│-----╱──────
            │    ╱  │
            │   ╱   │ 
            │  ╱    │
            │ ╱     │
            │╱______│_____→ Cumulative Capacity (t)
```               Demand

- Vertical line at demand quantity
- Intersection with cost curve and y-axis = **market price**

### Step 3: Calculate Proxy Profit

For each plant:

```python
profit_i = (market_price - lcos_i) × sales_i
```

Where:
- `market_price`: Derived from cost curve intersection (Step 2)
- `lcos_i`: Plant i's levelized cost of steel/iron
- `sales_i`: Plant i's allocated production volume (from Trade Module)

---

## Example

### Scenario
- **Demand**: 100 Mt steel
- **Plants**:
  - Plant A: LCOS = $400/t, Capacity = 50 Mt
  - Plant B: LCOS = $500/t, Capacity = 40 Mt
  - Plant C: LCOS = $600/t, Capacity = 30 Mt

### Cost Curve
```
0-50 Mt:  $400/t (Plant A)
50-90 Mt: $500/t (Plant B)
90-120 Mt: $600/t (Plant C)
```

### Market Price Calculation
- Demand = 100 Mt
- Falls in Plant C's range (90-120 Mt)
- **Market Price = $600/t** (marginal plant's cost)

### Profit Calculation
```python
Plant A: profit = (600 - 400) × 50 = $10,000M
Plant B: profit = (600 - 500) × 40 = $4,000M
Plant C: profit = (600 - 600) × 10 = $0M  (marginal plant breaks even)
```

---

## Implementation in PAM

### Where It's Used

1. **Balance Sheet Updates** (`Plant.update_furnace_and_plant_balance()`):
   - Uses market price to calculate: `balance = (market_price - unit_cost) × production`
   - Aggregates to plant and plant group balances

2. **NPV Calculations** (`FurnaceGroup.optimal_technology_name()`):
   - Uses forecasted market prices for each year, by extracting future demands from current cost curves
   - Projects future revenues based on future demand and predicted prices

3. **Expansion Decisions** (`PlantGroup.evaluate_expansion()`):
   - Uses forecasted market prices for each year, by extracting future demands from current cost curves
   - Determines if new capacity will be profitable at projected prices

### Price Updates

Market prices are recalculated after every Trade Module run:
```python
# In simulation.py or handlers
market_price = extract_price_from_costcurve(
    demand=current_demand,
    cost_curve=sorted_plants_by_cost
)
```

---

## Limitations

1. **Assumes Perfect Competition**: All plants receive the same market price
   - Reality: Regional price differences, contracts, quality premiums

2. **No Price Dynamics**: Prices update annually based on current supply/demand
   - Reality: Intra-year volatility, speculation, inventory effects

3. **Marginal Cost Pricing**: Market price = marginal plant's cost
   - Reality: Market power, cartels, trade barriers affect pricing

4. **No Demand Elasticity**: Demand is fixed, doesn't respond to price
   - Reality: High prices → demand destruction, substitution

---

## Why This Approach Works

Despite limitations, proxy profit provides:

1. **Competitive Differentiation**: Low-cost plants earn higher profits
2. **Realistic Losses**: High-cost plants may operate at losses
3. **Investment Signals**: Profitable plants can finance expansions
4. **Technology Transition Incentives**: Cleaner/cheaper tech improves profitability

This approximation is **sufficient** for modeling long-term industry transformation where:
- Annual time steps smooth out short-term volatility
- Strategic decisions (technology switches, expansions) depend on multi-year trends
- Relative competitiveness matters more than absolute price levels

---

## Related Documentation

- **[Agent Definitions](agent_definitions.md)**: How balance sheets accumulate at Plant and PlantGroup levels
- **[PlantAgentsModel Orchestration](plant_agents_model_orchestration.md)**: When and how prices are calculated in the simulation loop
- **[Cost Calculation Functions](calculate_costs.md)**: Functions used for cost calculations that feed into the cost curve
