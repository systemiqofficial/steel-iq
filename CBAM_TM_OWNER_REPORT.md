# Carbon border mechanism — issues found, fixes, and open decisions

**For the Trade Module owner — 2026-08-13.**

This report accompanies the branch `experiment/cbam-fixes`
(= develop `f58ea81` + one commit, `29f0ed0`). It describes the defects we found in the
carbon border mechanism during an instrumented six-run sweep (2026–2060), where each sits
in the code, which ones the branch proposes a solution for (with the validation evidence),
and which need a decision from you. The branch is a proposal for your review, not a landed
fix. A supplementary document, `CBAM_TM_OWNER_EVIDENCE.md`, carries the underlying
run-data comparisons and the diagnosis trail for each issue.

Background: the border adjustments only started reaching the LP objective with `c20fe7d`
(before that, the mutated cost dict was discarded). Making them live exposed the defects
below. All of them predate `c20fe7d` — `git log -L` places the central one in the initial
public source import; they were harmless while the adjustments went nowhere.

---

## 1. Issue summary

| # | issue | severity | status |
|---|---|---|---|
| 1 | Supplier raw-material prices read as carbon costs | **Critical** | fixed — `d6b91f7`, already on develop |
| 2 | Steel import channel structurally dead | **Critical** | solution proposed — `29f0ed0` (this branch), for review |
| 3 | Commodity mismatch on production-destination arcs | **High** | solution proposed — `29f0ed0` (same root cause as #2) |
| 4 | Reference average excluded decarbonised plants, not idle ones | **Medium** | solution proposed — `29f0ed0` |
| 5 | Overlapping blocs: first mechanism in sheet-column order wins | **High** | **open — needs a design decision** |
| 6 | Duplicate arc keys in `legal_allocations` | **Low** | open — root cause undiagnosed |

The practical consequence of #1 and #2 together: **no CBAM impact assessment produced from
this model without these changes is meaningful.** With the mechanism as shipped, EU steel
imports show no detectable difference from a CBAM-off control in any year of a 36-year run.
With the branch, the mechanism produces large, robust production and capacity effects
(§3.2).

---

## 2. The issues

### Issue 1 — supplier raw-material prices read as carbon costs (Critical, fixed)

**Where:** `adapt_allocation_costs_for_carbon_border_mechanisms` in
`src/steelo/domain/trade_modelling/set_up_steel_trade_lp.py`. The function reads
`from_pc.production_cost` as the source's carbon cost, but that field is overloaded: it
holds `carbon_cost_per_unit` for furnace groups (`set_up_steel_trade_lp.py:451`) and
meta-groups (`:422`), but a **raw-material price** for suppliers (`:576`). Both meanings
are correct for the LP objective (`trade_lp_modelling.py:1014`); the CBAM function is the
only consumer that needs it to be specifically carbon, and it did not check the source's
process type.

**Consequence:** all 27 EU suppliers are scrap at 450 \$/t, so every EU-supplier → non-EU
arc tripped the export-rebate branch. Measured in 2026: 93,841 export arcs at a mean of
−73 \$/t against a maximum carbon-supported differential of ~3 \$/t (the EU carbon price
that year is 1.5325 \$/tCO2 — a −274 \$/t rebate is arithmetically impossible). At least
95% of the adjustment mass was supplier-driven.

**Fix and why we consider it fixed:** `d6b91f7` skips supply-sourced arcs (snippet A). We
hold no embedded-carbon figure for suppliers, so any value used there is invented, and real
CBAM does not cover scrap feedstock. After the fix, export rebates are carbon-bounded
(2026 mean −2.5 \$/t) and — the decisive test — both sides now scale with the carbon price
across scenario masters, which the export side previously did not (evidence doc §2).

**Residual recommendation:** split the overloaded field — rename `production_cost` to
`unit_cost_adder` and give `ProcessCenter` an explicit `carbon_cost` — so this class of bug
cannot recur. It is the shared root of issues 1, 2 and 3.

### Issue 2 — the steel import channel was structurally dead (Critical — solution proposed on this branch)

**Where:** `build_reference_producer_carbon_costs` (pre-branch
`set_up_steel_trade_lp.py:864-891`) and the destination side of
`adapt_allocation_costs_for_carbon_border_mechanisms` (`:956-964`).

**Mechanism of the bug:** carbon in this model is booked almost entirely on the **iron**
stage. A BF-BOF steel plant consumes hot metal whose emissions were already charged to the
upstream furnace group, so the steel stage's own `carbon_cost_per_unit` is zero by
construction — globally, 100% of iron furnace groups carry positive direct GHG but only
10.7% of steel furnace groups do (2033; those 111 are all EAF). The reference builder used
each stage's **own** cost, so `(country, "steel")` reference keys barely existed: the EU had
5–6 in 2029–2030, and **zero from 2032**, at which point every steel import arc into an EU
demand centre hit a silent `continue` and the import branch reported `n=0` for the rest of
the run. Meanwhile export rebates fired on ~110,000 EU **iron** arcs at a mean of −72 \$/t.
As shipped, the mechanism was an export subsidy on EU iron with no import protection on
steel — the primary real-world CBAM channel did not exist in the model. This affected the
integrated (BF-BOF) route everywhere, not just the EU.

**Proposed solution (`29f0ed0`, this branch):** make both sides of the comparison
**embedded**: the reference for steel
is the stage cost plus the realised iron input intensity × the domestic iron reference
(snippet B), and the source side is embedded symmetrically
(`_source_embedded_carbon_cost`, `set_up_steel_trade_lp.py:1041`). `ProcessCenter` gains
`last_production` and `input_intensities`, taken from the previous year's allocation — the
same vintage as `carbon_cost_per_unit` (`trade_lp_modelling.py:219-233`). Import arcs with
no destination reference are skipped **and counted** (`imports_skipped_no_reference`);
export arcs into markets with no domestic producers rebate in full (unpriced market). A
`[CBAM ADJUST]` summary log line (`:1174`) reports per-class counts and skip reasons —
the missing-versus-zero-reference distinction was silent for the entire life of this bug
and cost a full aborted run set to find.

**Why we believe the proposed solution resolves it.** Before → after, on identical inputs
(v2 = as shipped, v3 = this branch; details and full tables in the evidence doc, §3–4):

| | as shipped (v2) | with `29f0ed0` (v3) |
|---|---|---|
| `(EU country, steel)` reference keys from 2032 | **0 — channel dead** | 18–19 in every year |
| import adjustments, 2026 | n=5,101, mean 0.14 \$/t | n=144,107, mean 2.88 \$/t |
| — of which steel | **0 (structurally impossible)** | 12,801 |
| export rebates, 2026 | n=69,342 — **iron only** | n=72,731, of which 11,909 steel |
| steel import arcs adjusted, 2030s | 0 in every year | 13,000–16,000 per year |
| EU steel imports, on − off | no detectable effect in any of 35 years | +3.6…+8.6 Mt 2026–39, −16…−21 Mt 2042–46, both beyond noise |
| EU steel production, on − off | within noise in almost all years | +7…+35 Mt/yr from 2035, beyond noise 24/26 years |
| EU steel capacity, on − off | transient bump only (via the iron export subsidy) | +21…+49 Mt every year from 2040, all beyond noise |

Two further checks that the numbers are right for the right reasons:

1. *Bound check:* every maximum adjustment 2026–2044 equals the destination carbon price ×
   2.0622 tCO2/t to four decimal places, across both scenarios — magnitudes never exceed
   what carbon can justify (the exact failure mode of issues 1 and 2, which announced
   themselves at −274 \$/t), and price pass-through is linear. This is a bound, not a proof
   of the weights.
2. *Replay validation of the weights themselves:* feeding the patched functions with
   process centres reconstructed from the sweep's pickles yields the 18–19 EU steel
   reference keys above with a production-weighted EU steel reference of 2→27 \$/t over
   2026–2035. The arc counts are also consistent with the arc population census — the
   shipped 5,101 imports against 69,342 exports on a near-symmetric population was the
   anomaly.

The branch adds a dead-import-channel regression test plus four further tests covering
source-side embedding, the issue-3 benchmark, embedded export rebates with the
unpriced-market floor, and the intensity fallback (`tests/unit/test_setting_up_trade_lp.py`).
`pytest` and `mypy src/` were green at commit.

### Issue 3 — commodity mismatch on production destinations (High — solution proposed on this branch)

**Where:** pre-branch `set_up_steel_trade_lp.py:963-964`. For a demand-centre destination
the reference was keyed `(iso3, commodity)` — correct. For a **production** destination the
code used that plant's own `production_cost`, denominated per tonne of *its* product. An
iron → steel-plant arc therefore compared the source's cost per tonne of iron against the
destination's cost per tonne of steel — different denominators.

**Proposed solution:** same root cause as issue 2 and addressed by the same commit — on
the branch, the destination side always uses the destination *country's* reference for the
commodity **crossing the border**, never the destination plant's own cost (snippet C).
What each arc type is benchmarked against, before → after:

| arc | as shipped | with `29f0ed0` |
|---|---|---|
| iron → steel plant | that **plant's own** cost, \$ per tonne of **steel** — wrong denominator | destination country's **iron** reference, \$ per tonne of **iron** |
| steel → demand centre | `(iso3, steel)` stage-level reference — right key, but ~always missing (issue 2) | destination country's embedded steel reference |
| iron → demand centre | `(iso3, iron)` stage-level reference | destination country's iron reference (same semantics, now production-weighted) |

Covered by a dedicated regression test asserting an iron arc into a steel plant is
benchmarked against the iron reference.

### Issue 4 — the reference average excluded the wrong plants (Medium — solution proposed on this branch)

**Where:** pre-branch `set_up_steel_trade_lp.py:883` — the guard
`if pc.production_cost == 0.0: continue`, documented as excluding "idle producers", with
capacity weighting at `:888`.

**Consequence:** zero carbon cost overwhelmingly means *no priced emissions at this stage*,
not idleness. Measured in 2033: of 123 EU steel furnace groups only 5 were idle — 118 were
producing 121.9 Mt and still carried exactly zero carbon cost (21 of them BOF). The guard
dropped genuinely decarbonised plants, biasing the reference towards the dirtiest survivors
(the EU iron reference reaches 126.41 \$/t by 2040 precisely because only the worst plants
remain in the average). Capacity weighting also let a large idle plant outweigh a small
running one, and the commit's docstring said "production-weighted" while the code weighted
by capacity.

**Proposed solution:** `29f0ed0` deletes the guard and weights producers by last year's
production (snippet D): idle plants drop out via zero weight, and producing zero-carbon
plants dilute the reference honestly. Before → after:

| | as shipped | with `29f0ed0` |
|---|---|---|
| membership rule | drop every `production_cost == 0.0` plant as "idle" | weight by last year's production |
| EU steel plants entering the reference, 2033 | 0 of 123 — the 118 producing 121.9 Mt were excluded too | the 118 producing plants; the 5 idle ones drop out via zero weight |
| weighting | capacity — a large idle plant outweighs a small running one | realised production |
| resulting reference | dirtiest-survivor bias: EU iron reference climbs to 126.41 \$/t by 2040 | honest dilution: replayed EU embedded steel reference 2→27 \$/t over 2026–35 |

### Issue 5 — bloc overlap: first mechanism in column order wins (High, OPEN)

**Where:** the mechanism loop in `adapt_allocation_costs_for_carbon_border_mechanisms`
(branch `set_up_steel_trade_lp.py:1116`, with the `adjusted_arcs` lock at `:1135`).
Mechanisms iterate in CBAM-sheet column order and the first to touch an arc locks it.

**Consequence:** blocs overlap — 20 of 27 EU countries are also flagged OECD. From 2035 in
the multi-bloc scenario, a DEU→JPN flow is seen first by the EU mechanism as an export
(rebate); the OECD mechanism, which would correctly treat it as intra-bloc, never runs. So
a flow between two CBAM-applying blocs with a common carbon price still gets a rebate, and
which treatment applies depends on Excel column order. In the sweep this locks ~741,000
arcs per year from 2035 — **no multi-bloc-scenario number after 2034 should be quoted until
this is decided.** The branch deliberately does not touch it.

**Proposed solution:** decide the intended semantics before code. Our suggestion: "no
adjustment if source and destination are both inside *any* common applying bloc", evaluated
across all mechanisms before any is applied — i.e. compute each arc's set of applicable
mechanisms first, then apply at most one, rather than letting iteration order decide.

### Issue 6 — duplicate arc keys in `legal_allocations` (Low, open)

With a single active mechanism, the instrumentation reported `duplicates_skipped=26` in
2026 — 26 repeated `(from, to, commodity)` triples in `trade_lp.legal_allocations`, i.e.
genuinely duplicated entries or two process centres sharing a name. The count fell to 0
once supply-sourced arcs were skipped, so the duplicates are supplier-sourced. Harmless for
CBAM (the `adjusted_arcs` guard catches them) and invisible in `allocation_costs` (dict
keys collapse), but a data-consistency smell that was never explained.

A note on 2025 numbers, in case they look strange during review: in the current master
Excel the EU carbon price jumps between 2025 and 2026 (2024–25 sit at the full 61.3
\$/tCO2, 2026 starts the phased path at a fraction of that). That is simply how the data
currently is; the practical consequence for reading the runs is that 2025, the
initialisation year, is not a comparable baseline for any before/after statement, and none
of the evidence in the companion document uses it.

---

## 3. Design properties the proposed change deliberately preserves

These are properties of the mechanism's formulation, not defects. The branch changes none
of them; the sweep attached evidence to each, so the decisions are now informed ones.

1. **The differential design withdraws protection exactly as decarbonisation succeeds.**
   The comparison is domestic versus foreign carbon *cost*, so a fully clean EU receives
   nothing even against a maximally dirty importer. Under the base scenario the mechanism
   goes inert in 2046 — when EU ore-based ironmaking ends, the mean import adjustment
   collapses 850-fold in one year (85.43 → 0.10 \$/t) and stays nil to 2060. Production and
   capacity effects that persist to 2060 are inherited capital, not ongoing protection.
   Real CBAM exists to prevent leakage precisely *as* domestic industry decarbonises; the
   alternative formulation — charge the import's own embedded emissions at the destination
   carbon price, crediting carbon prices paid at origin — would not have this property, but
   needs per-plant embedded *emissions* plumbed into the LP. Your call.
2. **The mechanism includes export rebates; the EU's actual CBAM has none.** During the
   live years the rebate side is economically the stronger half: nearly every foreign
   market has no domestic reference, so EU exporters are rebated in full, EU steel is pushed
   into unpriced markets, and imports backfill domestic demand — gross trade rises in
   *both* directions while the mechanism is live. Anyone quoting these results should be
   told the modelled instrument is CBAM-plus-export-rebates.
3. **The differential charges clean foreign producers the full reference.** 91% of non-EU
   iron producers and 99% of non-EU steel producers carry zero carbon cost (2026), so the
   mechanism behaves like a near-uniform tariff rather than a differentiated one.
4. **References are per destination country, not EU-pooled.** An all-scrap EU country (e.g.
   ESP) has essentially no import protection because it has no domestic reference; real
   CBAM is one EU-wide benchmark. From ~2042 more EU import arcs are skipped for a missing
   reference than are adjusted — economically honest, and now at least visible via the
   `imports_skipped_no_reference` counter.

## 4. Suggested order of work

1. Review `29f0ed0` (would close issues 2, 3, 4 if you are happy with the approach).
   Nothing downstream of the CBAM path means anything without a solution to issue 2.
2. Decide the bloc-overlap semantics (issue 5) before anyone runs the multi-bloc scenario
   past 2034 again.
3. Split the overloaded `production_cost` field (root cause of issues 1–3).
4. Diagnose the duplicate arcs (issue 6) at leisure.

---

## Appendix — the landed fix and the proposed changes, grounded in code

### A. Issue 1 — skip supply-sourced arcs (`d6b91f7`, on develop)

```python
for from_pc, to_pc, comm in trade_lp.legal_allocations:
    ...
    # Skip supplier sources: their production_cost is raw-material price, not carbon cost.
    if from_pc.process.type == tlp.ProcessType.SUPPLY:
        continue
```

Mirrors the destination-side guard that already routed demand centres through the
reference builder; there was no equivalent on the source side.

### B. Issue 2 — embedded references replace stage-level ones (`29f0ed0`, proposed)

Before (`build_reference_producer_carbon_costs`): one `(iso3, commodity)` average of each
producer's **own** stage cost — zero for nearly every steel plant, so steel keys vanish:

```python
for commodity in pc.process.products:
    key = (iso3, commodity.name)
    weighted_cost_sum[key] += pc.production_cost * pc.capacity
    capacity_sum[key] += pc.capacity
```

After: per-country references pooled by commodity class; steel embeds its iron inputs
(`set_up_steel_trade_lp.py:1028-1036`):

```python
for pc, weight in steel_producers:
    iso3 = pc.location.iso3
    alpha = _iron_input_intensity(pc, references)          # t iron-class input / t steel
    embedded = pc.production_cost + alpha * iron_ref.get(iso3, iron_ref_global)
    steel_cost_sum[iso3] += embedded * weight
    steel_weight_sum[iso3] += weight
```

and the source side is embedded symmetrically (`:1041-1054`):

```python
def _source_embedded_carbon_cost(pc, commodity_name, references):
    if commodity_name not in STEEL_CLASS_COMMODITIES:
        return pc.production_cost
    alpha = _iron_input_intensity(pc, references)
    iron_ref = references.iron_ref.get(pc.location.iso3, references.iron_ref_global)
    return pc.production_cost + alpha * iron_ref
```

The realised intensities come from the previous year's allocation
(`build_input_intensities`, `:880-904`), the same vintage as `carbon_cost_per_unit`.

### C. Issue 3 — destination benchmarked on the crossing commodity (`29f0ed0`, proposed)

Before — a production destination used its own cost, denominated per tonne of a
*different* product:

```python
if to_pc.process.type == tlp.ProcessType.DEMAND:
    to_carbon_cost = reference_carbon_cost.get((to_iso3, comm.name))
    if to_carbon_cost is None:
        continue                       # silent — also issue 2's failure mode
else:
    to_carbon_cost = to_pc.production_cost   # per tonne of to_pc's product, not comm
```

After — always the destination country's reference for the commodity on the arc, with the
missing-reference cases split and counted (`:1139-1160`):

```python
from_carbon_cost = _source_embedded_carbon_cost(from_pc, comm.name, references)
to_carbon_cost = _destination_reference(to_iso3, comm.name, references)
...
# import side: no domestic producers -> nothing to protect, counted, skipped
# export side: no domestic producers -> unpriced market, rebate in full
```

### D. Issue 4 — production weighting replaces the zero-cost guard (`29f0ed0`, proposed)

Before:

```python
if pc.production_cost == 0.0:      # documented as "idle producers" — mostly decarbonised ones
    continue
...
weighted_cost_sum[key] += pc.production_cost * pc.capacity
```

After (`:926-933`):

```python
def _reference_weight(pc):
    """Weight of a producer in the reference average: last year's production when known.

    Falls back to capacity when no production history is attached (clustered meta-furnace
    groups). Idle plants weigh zero and drop out of the reference.
    """
    last_production = getattr(pc, "last_production", None)
    return pc.capacity if last_production is None else last_production
```
