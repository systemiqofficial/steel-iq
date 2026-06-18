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

> **Correction (2026-06)**: COSA and the legacy-debt schedule previously
> double-counted the remaining debt. The old code added remaining debt inside
> COSA *and* carried that same debt forward via the `legacy_debt_schedule` in the
> post-switch P&L. But the old debt is paid whether the plant stays or switches,
> so it cancels from the switch-vs-stay comparison. This has been fixed: **COSA is
> now the foregone operating margin only** — `COSA = NPV(foregone_operating_profits)`
> — and the persisting debt lives solely in the `legacy_debt_schedule`. The
> magnitude of the switch penalty therefore drops, so modelled transitions are
> somewhat faster than previously documented. The behavioural conclusions below
> (waiting, clustering at renovation boundaries, no technology "hopping", gradual
> 30–50 year transition) still hold, but the cause has been re-attributed: lock-in
> now comes from (a) the foregone operating margin being large when remaining life
> is long and small near end-of-life, and (b) the realised legacy-debt burden
> carried by the schedule post-switch — explicitly **not** from debt being charged
> inside COSA.

### 1. Technology Switching Timing

**Observation**: Plants wait longer before switching technologies

**Why**: Cost of Stranded Assets (COSA) — the foregone operating margin — is large
when many years of profitable operation remain, and shrinks towards zero near
end-of-life. Switching early therefore forfeits more margin.

**Example NPV Comparison** (BF margin $50M/year, r=0.08):

| Switch Year | Years Remaining | Foregone Margin (COSA) | NPV (new tech) | Net Benefit |
|-------------|-----------------|------------------------|----------------|-------------|
| Year 5      | 15              | $428M                  | $800M          | $372M       |
| Year 10     | 10              | $335M                  | $800M          | $465M       |
| Year 15     | 5               | $200M                  | $800M          | $600M       |
| Year 20     | 0               | $0M                    | $800M          | $800M       |

**Result**: Waiting until year 20 (no remaining margin to forgo) yields more net
benefit than switching at year 5, because COSA tracks the foregone operating
margin, which falls as the plant approaches end-of-life. The legacy-debt schedule
adds a further post-switch burden that bites if the plant switches before its debt
is repaid.

### 2. Renovation Boundary Clustering

**Observation**: Most technology switches occur at 20-year boundaries

**Why**: At end-of-life there is no remaining operating margin to forgo → COSA ≈ 0,
and the debt is fully repaid so no legacy burden is carried forward → Maximum net benefit

**Visualization**:
```
Technology Switches by Year in Lifetime:

Year 1-5:   ▓ (2%)  ← Very few (high COSA)
Year 6-10:  ▓▓ (5%)
Year 11-15: ▓▓▓ (8%)
Year 16-19: ▓▓▓▓ (12%)
Year 20:    ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓ (60%)  ← Majority at renovation boundary
Year 21+:   ▓▓▓▓ (13%) ← Switches at later renovation cycles
```

**Interpretation**:
- 60%+ of switches occur at renovation time (year 20, 40, 60...)
- Remaining 40% occur mid-lifetime only when:
  - NPV advantage is very large (e.g., carbon price spike makes current tech uneconomical)
  - Subsidies offset the high COSA
  - Plant has excess balance to absorb the loss
- A switch blocked in a given year by affordability or the annual capacity limit is **not** deferred to the next year: the call simply returns no action, and the furnace group is re-evaluated independently in later years (NPV and COSA recomputed for that year's economics). Any subsequent switch is therefore a fresh decision, not a queued one.

### 3. Cost of Stranded Assets (COSA)

**Observation**: COSA is larger when more years of profitable operation remain

**Formula**:
```python
COSA = NPV(foregone_operating_profits)
```

COSA is the **foregone operating margin only** — the discounted gross cash flow
(revenue − OPEX) over the remaining life. The remaining debt is *not* included:
the old debt is paid whether the plant stays or switches (it persists via the
`legacy_debt_schedule`), so it cancels from the switch-vs-stay comparison.

**Example Calculation** (switching from BF to DRI at year 10):

```
Foregone Operating Profits:
  BF margin: $50/t × 100 kt/year = $50M/year, for 10 remaining years

NPV at 8% discount rate:
  COSA = NPV($50M × 10 years, r=0.08)
  COSA = $50M × 6.71 (PV factor for 10 years)
  COSA ≈ $335M
```

**The legacy debt's real effect** is captured by the `legacy_debt_schedule` — the
realised post-switch P&L carries the $400M remaining BF debt forward as actual
debt-service payments. It is not double-counted by inflating COSA. For a
loss-making incumbent (gross cash flow < 0) COSA is negative, correctly rewarding
exit.

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

Enable debt tracking logs via `logging_config.yaml`:
```yaml
# Enable DEBUG for PlantAgentsModel (where debt calculations run)
modules:
  pam: DEBUG

# Or enable DEBUG for a specific function only
function_overrides:
  change_furnace_group_technology: DEBUG
```

Then run with:
```bash
run_simulation --log-level DEBUG ...
```

Look for log messages like:
```
DEBUG   | PAM  | change_furnace_group_technology: Technology switch BF-BOF → DRI-EAF:
  Remaining years: 15
  Old debt schedule length: 20
  Captured legacy debt: 45,000,000
  New technology debt: 120,000,000
  Combined total: 165,000,000
```

---

## Summary

Debt accumulation creates realistic technology transition dynamics by:

1. **Carrying a realised legacy-debt burden post-switch**: The `legacy_debt_schedule` keeps charging the old debt through realised P&L, making early transitions expensive (this is *not* COSA — COSA is the foregone operating margin only)
2. **Clustering switches at renovation boundaries**: Most transitions occur at end-of-life, when there is no remaining margin to forgo (COSA ≈ 0) and the debt is fully repaid (no legacy burden)
3. **Preventing technology hopping**: Multiple switches lead to unsustainable accumulated debt burdens via the schedule
4. **Creating path dependency**: Early decisions have lasting financial consequences
5. **Requiring larger capital reserves**: Plants need strong balance sheets to afford transitions

**Result**: Model exhibits gradual, realistic technology transitions (30-50 years) rather than unrealistic overnight shifts (5-10 years).

---

## Related Documentation

- **[Agent Definitions](agent_definitions.md)**: Technical details of legacy_debt_schedule in FurnaceGroup
- **[Cost Calculation Functions](calculate_costs.md)**: COSA calculation details (stranding_asset_cost function)
- **[Economic Considerations](economic_considerations.md)**: How debt interacts with other economic factors
- **[Furnace Group Strategy](furnace_group_strategy.md)**: How COSA is computed in technology evaluation
