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

The market price is set against a **dispatchable slice** of the cost curve, not the full curve. The slice is capped at a configurable fraction of total cumulative capacity (default 95% per product). Any demand falling above the dispatchable slice — whether the slice is short of total capacity or above it — triggers a shortage premium on top of the slice's boundary cost. This avoids the unrealistic outcome where a thin sliver of high-cost outlier capacity at the top of the curve sets the price whenever the market gets tight.

### Dispatchable Share Cutoff

Two `SimulationConfig` parameters control where the dispatchable slice ends:

- `steel_market_clearing_share` — fraction of steel total cumulative capacity that participates in market clearing (default `0.95`, range `0.5–1.0`).
- `iron_market_clearing_share` — same for iron.

Setting either to `1.0` reproduces today's behaviour for that product (no truncation; the full curve clears the market).

**Strict-inequality boundary rule.** The dispatchable slice keeps every furnace whose cumulative capacity is *at most* `share × total`. The first furnace to *strictly* exceed that threshold is the "boundary" furnace; it is excluded from the truncated slice (so it cannot be the buffer reference), but it is still reachable through the in-band merit-order walk when demand lands inside it.

```python
threshold = config.<product>_market_clearing_share * total_capacity
truncated = [e for e in cost_curve if e.cumulative_capacity <= threshold]

if demand <= threshold:
    # Merit-order dispatch on the full curve — boundary furnace included at its own cost.
    market_price = first(e.production_cost for e in cost_curve if e.cumulative_capacity >= demand)
else:
    # Shortage band: demand exceeds the dispatchable cap.
    market_price = truncated[-1].production_cost + config.<product>_price_buffer
```

### Shortage Premium

Two `SimulationConfig` parameters control the shortage premium added when demand falls past the dispatchable slice:

- `steel_price_buffer` — applied when steel demand exceeds the dispatchable steel slice.
- `iron_price_buffer` — applied when iron demand exceeds the dispatchable iron slice.

The buffer represents the extra willingness-to-pay required to incentivise new capacity when the market is supply-constrained. It fires when `demand > share × total` and emits a `WARNING`-level log line in two distinct regimes:

1. **Shortage band** — `share × total < demand ≤ total`. There is unused capacity past the dispatchable cap, but the engine has chosen not to dispatch it (the long tail).
2. **Demand exceeds total** — `demand > total`. No unused capacity remains.

When `demand ≤ share × total` the boundary furnace (whose end-cumulative crosses the threshold but whose start fits within it) is reachable at its own production cost via the in-band merit-order walk on the full curve — no buffer is added, even though the furnace itself is excluded from the truncated slice.

### Worked Example

Building on the 3-plant scenario above (Plants A/B/C with capacities 50/40/30 Mt and LCOS 400/500/600 $/t, `total = 120` Mt):

- `steel_market_clearing_share = 0.95` ⇒ `threshold = 0.95 × 120 = 114` Mt.
- Plant A (cum=50) and Plant B (cum=90) are below threshold ⇒ kept.
- Plant C (cum=120) strictly exceeds 114 ⇒ Plant C is the boundary, excluded.
- The dispatchable slice is `[A, B]`; `last_truncated.production_cost = $500`.

| Demand   | Branch                                | Market price                       |
|----------|---------------------------------------|------------------------------------|
| 80 Mt    | Merit-order on full curve             | $500 (Plant B clears the demand)   |
| 100 Mt   | Merit-order on full curve (100 ≤ 114) | $600 (Plant C clears the demand)   |
| 116 Mt   | Shortage band (114 < 116 ≤ 120)       | $500 + $200 = $700                 |
| 130 Mt   | Demand exceeds total (130 > 120)      | $500 + $200 = $700                 |

At `share = 1.0` the threshold equals total capacity, so the shortage gate only fires when demand strictly exceeds total: 100 Mt clears at $600 (Plant C), 130 Mt triggers $600 + $200.

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

1. **Calculate Base Iron Price**: Extract iron price from the dispatchable iron slice (truncated at `iron_market_clearing_share`).
2. **Calculate Steel Price**: Extract steel price from the dispatchable **steel** slice (truncated at `steel_market_clearing_share`). This is the same value the engine returns to other callers in the same year, so the pegging reference and the headline steel price never disagree.
3. **Apply Pegging**: `iron_price = max(base_iron_price, steel_price × ratio)`

If the truncated steel slice is empty (a pathological share that drops every steel furnace), the pegging branch falls back to `last_full_steel.production_cost + steel_price_buffer` for the steel reference, mirroring the main empty-truncated semantics, and emits a `WARNING` log line.

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

## Plot Visualisation

The per-year cost-curve PNGs in `pam_plots_dir` reflect the dispatchable share cutoff:

- **Vertical dashed marker** at `x = share × total_capacity`, labelled `Market clearing share (95%)` (or whatever percentage is configured), drawn whenever `share < 1.0`.
- **Annotation** alongside the clearing-price line. When demand falls past the dispatchable slice, the annotation extends with one of:
  - `(Demand exceeds dispatchable 95%: boundary cost + $200 shortage premium)` — shortage band.
  - `(Demand exceeds total supply: boundary cost + $200 shortage premium)` — demand strictly above total capacity.

The bars and the per-plot CSV export still cover the **full** cost curve — every furnace contributes a bar, the x-axis spans full cumulative capacity, and capacity inventory remains intact. Truncation only changes where the engine sets the price (the red dashed clearing-price line + annotation) and adds the new vertical marker. This keeps the visualisation honest about installed capacity while making the dispatchable slice immediately legible.

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
