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

## Handling Demand Overshoot

When total demand exceeds the cumulative capacity of every producer in the cost curve, there is no intersection of demand and supply — every plant is already running flat out. In that case the market price is set to the marginal (most expensive) producer's cost **plus a configurable buffer**:

```python
# simplified
if demand > cost_curve[-1].cumulative_capacity:
    market_price = cost_curve[-1].production_cost + config.<product>_price_buffer
```

Two `SimulationConfig` parameters control this:

- `steel_price_buffer` — applied when steel demand exceeds steel capacity.
- `iron_price_buffer` — applied when iron demand exceeds iron capacity.

The buffer represents the extra willingness-to-pay required to incentivise new capacity when the market is supply-constrained. A `WARNING`-level log line is emitted whenever the buffer is triggered.

---

## Implementation in PAM

### Where It's Used

1. **Balance Sheet Updates** (`PlantGroup.sweep_fg_balances_to_group()`):
   - Uses market price to compute each FG's annual P&L: `(market_price - unit_cost) × production`
   - Aggregates every plant's FGs into the group treasury ``balance`` and resets ``fg.balance`` to 0

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

## Iron Price Pegging

### Overview

The simulation now supports **pegging iron prices to steel prices** to ensure iron maintains a minimum value relative to steel. This feature addresses market dynamics where iron's value is linked to steel as its primary downstream product.

### Configuration

Two parameters control iron price pegging in `SimulationConfig`:

```python
peg_iron_to_steel_price: bool = False  # Enable/disable pegging (default: disabled)
iron_to_steel_price_ratio: float = 0.8  # Minimum ratio of steel price (default: 80%)
```

### How It Works

When `peg_iron_to_steel_price = True`:

1. **Calculate Base Iron Price**: Extract iron price from the cost curve based on demand
2. **Calculate Steel Price**: Extract steel price from the cost curve based on steel demand
3. **Apply Pegging**: `iron_price = max(base_iron_price, steel_price × ratio)`

### Example

With pegging enabled at 80% ratio:
- Steel price from cost curve: $600/t
- Iron price from cost curve: $350/t
- Pegged floor price: $600 × 0.8 = $480/t
- **Final iron price: $480/t** (pegged floor is higher)

If iron's cost curve price was $500/t:
- Pegged floor: $480/t
- **Final iron price: $500/t** (cost curve is higher)

### Important Notes

- **Current Year Only**: Pegging applies only to current year prices, NOT to future price projections used in NPV calculations
- **Configurable Ratio**: The ratio can be adjusted based on market assumptions (e.g., 0.7 for 70%, 0.9 for 90%)
- **Optional Feature**: Disabled by default to maintain backward compatibility

### Rationale

Iron price pegging reflects real-world market dynamics where:
- Iron (especially DRI/HBI) trades at a premium relative to its production cost
- Steel prices set a floor for iron prices due to substitution economics
- Integrated steelmakers have pricing power in iron markets

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
