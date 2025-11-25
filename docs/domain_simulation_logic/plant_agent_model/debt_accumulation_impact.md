# Debt Accumulation Impact on Model Behavior

## Overview

Debt accumulation occurs when a furnace group switches technologies before fully repaying its existing debt. This creates "legacy debt" that persists alongside the new technology's debt, fundamentally affecting investment timing and technology transition dynamics.

**Key Insight**: Mid-lifetime technology switches become expensive due to accumulated debt, creating strong incentives to wait for renovation boundaries (when debt is fully repaid).

---

## The Mechanism

### Without Debt Accumulation (Unrealistic)

**Scenario**: BF plant switches to DRI after 10 years

```
Year 0-10:  BF debt payments (original $800M debt)
Year 10:    Switch to DRI
Year 11-30: DRI debt payments (new $1,200M debt)

Total debt burden: $2,000M ($800M + $1,200M)
```

**Problem**: Old debt disappears → Switching mid-lifetime artificially attractive → Unrealistic rapid transitions

### With Debt Accumulation (Realistic)

**Same scenario with debt preservation:**

```
Year 0-10:  BF debt only ($800M ÷ 20 years = $40M/year)
Year 10:    Switch to DRI
            Remaining BF debt: $800M × (10 years / 20 years) = $400M

Year 11-20: BOTH debts:
            - BF legacy: $400M ÷ 10 years = $40M/year
            - DRI new: $1,200M ÷ 20 years = $60M/year
            - Combined: $100M/year

Year 21-30: DRI debt only: $60M/year

Total debt burden: $2,600M ($400M legacy + $2,200M)
```

**Effect**: Old debt persists → Switching mid-lifetime becomes expensive → More realistic transition dynamics

---

## Behavioral Impacts

### 1. Technology Switching Timing

**Observation**: Plants wait longer before switching technologies

**Why**: Cost of Stranded Assets (COSA) increases with remaining debt

**Example NPV Comparison**:

| Switch Year | Remaining Debt | COSA | NPV (new tech) | Net Benefit |
|-------------|----------------|------|----------------|-------------|
| Year 5      | $600M          | $650M| $800M          | $150M       |
| Year 10     | $400M          | $450M| $800M          | $350M       |
| Year 15     | $200M          | $250M| $800M          | $550M       |
| Year 20     | $0M            | $50M | $800M          | $750M       |

**Result**: Waiting until year 20 (debt paid off) yields $600M more benefit than switching at year 5.

### 2. Renovation Boundary Clustering

**Observation**: Most technology switches occur at 20-year boundaries

**Why**: Debt fully repaid → COSA minimized → Maximum net benefit

**Visualization**:
```
Technology Switches by Year in Lifetime:

Year 1-5:   ▓ (2%)  ← Very few (high COSA)
Year 6-10:  ▓▓ (5%)
Year 11-15: ▓▓▓ (8%)
Year 16-19: ▓▓▓▓ (12%)
Year 20:    ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓ (60%)  ← Majority at renovation boundary
Year 21+:   ▓▓▓▓ (13%) ← Some delay due to affordability/capacity limits
```

**Interpretation**:
- 60%+ of switches occur at renovation time (year 20, 40, 60...)
- Remaining 40% occur mid-lifetime only when:
  - NPV advantage is very large (e.g., carbon price spike makes current tech uneconomical)
  - Subsidies offset the high COSA
  - Plant has excess balance to absorb the loss

### 3. Cost of Stranded Assets (COSA) Elevation

**Observation**: COSA values are higher for mid-lifetime switches

**Formula**:
```python
COSA = NPV(remaining_debt_payments + foregone_operating_profits)
```

**Example Calculation** (switching from BF to DRI at year 10):

```
Remaining Debt Payments:
  Years 11-20: $400M legacy ÷ 10 = $40M/year

Foregone Operating Profits:
  BF margin: $50/t × 100 kt/year × 10 years = $50M/year

NPV at 8% discount rate:
  COSA = NPV([$40M + $50M] × 10 years, r=0.08)
  COSA = $90M × 6.71 (PV factor for 10 years)
  COSA ≈ $604M
```

**Without Debt Accumulation**:
```
Remaining Debt: $0 (assumed paid off or written off)
COSA = NPV($50M × 10 years, r=0.08) = $335M
```

**Impact**: Debt accumulation increases COSA by **80%** ($604M vs $335M), making switches much less attractive.

### 4. Capital Requirements

**Observation**: Higher upfront capital needed for technology transitions

**Components**:
1. **Equity for new technology**: 20% × new CAPEX × capacity
2. **Debt service**: Ongoing payments on both old and new debt
3. **Lower profitability**: Accumulated debt increases unit production cost

**Example**:
```
Switch from BF to DRI at year 10:

Upfront Cost:
  Equity (20% × $1,200/t × 100,000t): $24M

Annual Debt Burden (years 11-20):
  Legacy BF: $40M/year
  New DRI: $60M/year
  Combined: $100M/year (vs $60M if switching at year 20)

Unit Cost Impact:
  Extra debt: $40M ÷ 100kt production = $400/t

  BF unit cost: $600/t
  DRI unit cost: $550/t + $400/t legacy debt = $950/t

  Result: DRI MORE EXPENSIVE than BF despite lower base cost!
```

**Implication**: Mid-lifetime switches can be unprofitable even when new technology has lower base costs, due to debt burden.

---

## Model Realism Improvements

### Before Debt Accumulation

**Unrealistic behaviors observed**:
- Plants switch technologies every few years (technology "hopping")
- Entire industry transitions in 5-10 years
- No clustering at renovation boundaries
- Technology switches insensitive to remaining lifetime

### After Debt Accumulation

**Realistic behaviors observed**:
- Technology switches primarily at end-of-life (year 20, 40, 60...)
- Gradual industry transition over 30-50 years
- Strong preference to "wait it out" rather than switch early
- Mid-lifetime switches only for compelling reasons (high carbon costs, large subsidies)

---

## Strategic Implications

### For Plants

**Optimal timing**:
- **Wait until renovation**: Minimize COSA, maximize net benefit
- **Switch early only if**: NPV advantage > COSA + switching costs

**Lock-in effects**:
- High debt burden creates path dependency
- Early technology choices have long-lasting consequences
- "Stranded asset" risk becomes real financial burden

### For Policy

**Subsidy effectiveness**:
- **Most effective**: At renovation boundaries (low COSA to overcome)
- **Less effective**: Mid-lifetime (must overcome high COSA)
- **Optimal targeting**: Time subsidies to coincide with renovation cycles

**Transition speed**:
- High carbon prices alone may not accelerate transitions (COSA barrier)
- Need BOTH carbon price AND subsidies to trigger mid-lifetime switches
- Infrastructure support (H2, CCS) must align with renovation cycles

---

## Cascading Debt

### Multiple Technology Switches

**Scenario**: Plant switches BF → DRI in year 10, then DRI → SR in year 25

**Debt accumulation**:
```
Year 0-10:  BF debt ($800M ÷ 20 = $40M/year)
Year 10:    Switch to DRI
            BF legacy: $400M remaining

Year 11-20: BF legacy ($40M/year) + DRI debt ($60M/year) = $100M/year
Year 20:    BF legacy paid off

Year 21-25: DRI debt only ($60M/year)
Year 25:    Switch to SR
            DRI legacy: $1,200M × (5/20) = $300M remaining

Year 26-30: DRI legacy ($60M/year) + SR debt ($80M/year) = $140M/year
Year 31-45: SR debt only ($80M/year)
```

**Impact**:
- Cascading debt from multiple switches creates very high debt burdens
- Strongly disincentivizes "technology hopping"
- Plants that switch early face long-term competitive disadvantage

---

## Calibration Considerations

### Debt Parameters

**Lifetime affects burden**:
```python
plant_lifetime = 20  # Standard
# Shorter lifetime (15 years) → Higher annual payments → Larger COSA
# Longer lifetime (25 years) → Lower annual payments → Smaller COSA
```

**Cost of debt affects total burden**:
```python
cost_of_debt = 0.05  # 5%
# Higher rate (8%) → More interest paid → Larger COSA
# Lower rate (3%) → Less interest paid → Smaller COSA
```

### Balance Sheet Impact

**Aggressive debt accumulation**:
- Plants accumulate negative balances
- Cannot afford future investments
- More closures, slower transitions

**Generous debt forgiveness** (if debt accumulation disabled):
- Plants maintain positive balances
- Can afford rapid technology switching
- Unrealistic transition speeds

---

## Debugging Debt Accumulation

### Key Checks

**Verify legacy debt is being tracked**:
```python
# After technology switch
assert furnace_group.legacy_debt_schedule != []
assert len(furnace_group.legacy_debt_schedule) == remaining_years
```

**Verify debt is being combined**:
```python
total_debt = furnace_group.debt_repayment_per_year
current_tech_debt = calculate_debt_repayment(new_investment, ...)
legacy_debt = furnace_group.legacy_debt_schedule

assert total_debt[0] == current_tech_debt[0] + legacy_debt[0]
```

**Verify debt decreases annually**:
```python
# In update_balance_sheet()
old_legacy = furnace_group.legacy_debt_schedule
# ... payment made ...
new_legacy = furnace_group.legacy_debt_schedule

assert len(new_legacy) == len(old_legacy) - 1  # One year removed
```

### Logging

Enable debt tracking logs:
```python
import logging
logger = logging.getLogger('steelo.domain.models.debt_accumulation')
logger.setLevel(logging.DEBUG)

# In change_furnace_group_technology():
logger.debug(
    f"Technology switch {old_tech} → {new_tech}:\n"
    f"  Remaining years: {remaining_years}\n"
    f"  Old debt schedule length: {len(old_debt_schedule)}\n"
    f"  Captured legacy debt: {sum(legacy_debt):,.0f}\n"
    f"  New technology debt: {sum(new_debt):,.0f}\n"
    f"  Combined total: {sum(combined_debt):,.0f}"
)
```

---

## Summary

Debt accumulation creates realistic technology transition dynamics by:

1. **Increasing COSA for mid-lifetime switches**: Making early transitions expensive
2. **Clustering switches at renovation boundaries**: Most transitions occur when debt paid off
3. **Preventing technology hopping**: Multiple switches lead to unsustainable debt burdens
4. **Creating path dependency**: Early decisions have lasting financial consequences
5. **Requiring larger capital reserves**: Plants need strong balance sheets to afford transitions

**Result**: Model exhibits gradual, realistic technology transitions (30-50 years) rather than unrealistic overnight shifts (5-10 years).

---

## Related Documentation

- **[Agent Definitions](agent_definitions.md)**: Technical details of legacy_debt_schedule in FurnaceGroup
- **[Cost Calculation Functions](calculate_costs.md)**: COSA calculation details (stranding_asset_cost function)
- **[Economic Considerations](economic_considerations.md)**: How debt interacts with other economic factors
- **[Furnace Group Strategy](furnace_group_strategy.md)**: How COSA is computed in technology evaluation
