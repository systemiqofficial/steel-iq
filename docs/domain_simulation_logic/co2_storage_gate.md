# CO2 Storage Capacity Gate

Narrative companion to the reference sections in [Environment](environment.md#co2-storage-capacity-tracking), [Furnace Group Strategy](plant_agent_model/furnace_group_strategy.md) (P2), and [Plant Expansions](plant_agent_model/plant_expansions.md) (P3).

## Why we need a gate

Every country has an annual CO2 storage budget, loaded per-country per-year from the master Excel input. The trade model already enforces storage limits when allocating production within a year, but that happens after plants are built. Without an upstream gate, the simulation can commit a dozen CCS plants in a country whose storage only supports three, then operate them all at reduced utilisation forever — stranded CCS capital and a misleading decarbonisation story. The gate fires at five decision points in the pipeline between "opportunity identified" and "plant operating", blocking commitments that the country cannot physically absorb.

## The tracker

The Environment owns two per-country counters:

- **Firm** — tCO2/yr locked in by operating plants, under-construction plants, and plants committed via an in-flight tech switch.
- **Reserved** — tCO2/yr pledged by announced plants that haven't started building, scaled by a discount factor (default 0.9) reflecting that not all announcements become construction starts.

Counters are rebuilt from scratch at the start of each year by walking every FurnaceGroup, so cancelled or closed plants automatically free their slot. Intra-year handler hooks keep counters up-to-date as plants transition between statuses during the year, so gates fired later in the year see earlier commitments.

To estimate a prospective CCS plant's need, the gate uses `Environment.get_co2_need(tech, capacity, reductant)`. The computation uses the most conservative (highest-emitting) feedstock for the plant's expected reductant; this over-estimates by up to ~6% and never under-estimates.

## How a CCS plant reaches operation

A greenfield plant progresses: considered → announced → under construction → operating. Existing plants can also add CCS via a **tech switch** (swap a furnace group's tech) or an **expansion** (add a new furnace group alongside existing ones).

The five gates sit at the transition points:

| Gate | Fires when | On block |
|---|---|---|
| **G1** | Geospatial model generates new opportunities | CCS tech dropped from candidate set |
| **G2** | Considered → announced transition | Stays considered, retries next year |
| **P1** | Announced → under-construction transition | Plant is **discarded** (no retry) |
| **P2** | Plant-agent ranks tech-switch candidates | CCS tech dropped from candidate set |
| **P3** | Plant-agent ranks expansion candidates | CCS tech dropped from candidate set |

## Unified gate formula

All five gates use the same underlying comparison:

```
headroom = limit − firm − reserved + own_reserved_contribution
pass iff headroom ≥ need
```

The only variation is `own_reserved_contribution`, which is `d × own_need` for P1 (the announced plant already contributes `d × need` to the reserved bucket, so exempting it prevents double-counting against itself) and `0` for every other gate. The invariant `firm + reserved ≤ limit` holds after every decision.

## Looking at the right year

A plant committing today consumes storage in the future. A plant announcing in 2026 with a 4-year construction time needs storage in 2030, not 2026. Each gate looks up the budget at the year the plant will actually start operating, not the current year. If the requested year precedes the earliest year defined in the data (or the country has no `co2_stored` constraint at all), headroom is **zero** — CCS cannot be committed against storage that doesn't exist on paper. Past the latest defined year, the last value is carried forward.

## Key design choices

**Hard gate, not a price penalty.** When storage is unavailable, CCS is refused. Predictable, no tuning parameters.

**Per-country, not regional.** A country cannot use its neighbour's storage.

**Gate and trade-model enforcement coexist.** The gate prevents over-*investment*; the trade model manages over-*operation* within a year.

**Approximate need is acceptable.** A conservative reductant-filtered maximum is used upfront; if the plant ends up running a cleaner feedstock, the trade model handles it via under-utilisation.

**Considered opportunities stay alive when blocked at G2.** They cost nothing (not in counters); keeping them open lets them succeed later if other plants are discarded or new storage comes online.

**Announced opportunities blocked at P1 are discarded.** They *do* occupy a reserved slot; leaving them indefinitely would freeze the country's pipeline.

**CCS and CCU are distinct.** CCU reuses CO2 as a feedstock rather than storing it — zero storage draw, but the capital is still locked against re-switching. Two different checks: `is_ccs_or_ccu` (name-based re-switch lock) vs `get_co2_need > 0` (BOM-based storage draw).

## What this gives us

- CCS buildout per country is capped at the assessed storage budget.
- Stranded CCS assets drop close to zero — plants only get approved if the country can absorb their output.
- Operators can inspect firm and reserved counters per country at any year to see how much of the budget is committed vs. provisional.
- As future Excel storage curves grow, the gate automatically opens up — no code change needed.