# Carbon border mechanism — supplementary evidence

Companion to `CBAM_TM_OWNER_REPORT.md` (issue numbering follows that report). This document
shows the run data behind each issue: what we measured, and why we concluded the defect is
where the report says it is. Every number was re-derived from archived run artefacts during
an adversarial review of the first draft; nothing here needs a simulation re-run.

## 1. What was run

Six full runs, all `--end-year 2060 --log-level DEBUG`, one isolated `STEELO_HOME` per run,
masters sha256-verified at launch. Two code variants × three arms:

| set | code | meaning |
|---|---|---|
| v2 | develop-equivalent + issue-1 fix (`d6b91f7`) + `[CBAM ADJUST]` logging | the mechanism **as shipped** |
| v3 | v2 code + `29f0ed0` | the mechanism **with the branch's proposed solution** |

| arm | master | mechanism |
|---|---|---|
| run1 base | base 2060 master | EU only, 2026–2100 |
| run2 cc_cbam | carbon-costs-CBAM variant | EU 2026 + EFTA/EUCU, OECD, NAFTA, ASEAN from 2035; higher carbon-price paths |
| run3 control | base master, CBAM flag off | no mechanism — purpose-built control (byte-identical to base except the one flag) |

The headline pairing is run1 − run3 **within v3**: identical code and input, mechanism on
versus off. "EU" = the 27 EU-flagged countries (verified EU-27). Production/capacity are
de-duplicated on `(year, furnace_group_id)`; trade is summed from the per-year steel trade
allocation CSVs. An earlier v1 set on plain develop was aborted at 2027 the moment issue 1
surfaced; it is retained as evidence only.

## 2. Issue 1 — how the data pointed at the suppliers

The `[CBAM ADJUST]` instrumentation, year 2026, EU the only active mechanism, on two
masters that differ (among other things) in the 2026 EU carbon price:

```
base master (EU price 1.5325 $/tCO2): imports n=5126 mean=0.11 max=3.16 | exports n=93841 mean=-72.63 min=-274.25
cc   master (EU price 2.0    $/tCO2): imports n=5127 mean=0.15 max=4.12 | exports n=93841 mean=-73.13 min=-273.99
```

Two independent tells:

- **The import side scales with the carbon price** (0.11→0.15, 3.16→4.12, ratio ≈ 1.3 ≈
  2.0/1.5325). The export side does not: same arc count, same magnitudes, two different
  carbon prices. Whatever drives the export side, it is not carbon.
- **The magnitudes are arithmetically impossible.** At 1.5325 \$/tCO2, no plausible
  emissions intensity supports a −274 \$/t differential; the carbon-supported maximum is
  ~3 \$/t — which is exactly where the import side sits.

The import side escapes because demand-centre destinations route through the reference
builder (genuinely carbon); the source side had no equivalent guard. The prepared fixtures
close the loop: all 27 EU suppliers are scrap at 450 \$/t (non-EU span 25–450 \$/t), so
every EU-supplier → non-EU arc satisfies `from > to` and collects a large spurious
"rebate" whose size is a raw-material price spread, not a carbon differential. At least
95% of the adjustment mass was supplier-driven. After `d6b91f7`, the 2026 export side
reads n=69,342 mean −2.50 \$/t: carbon-bounded, and scaling with the price.

## 3. Issue 2 — how the dead import channel was diagnosed

### 3a. The import side dies at 2033 (run log, v2, base arm)

```
2030  imports n=15016  exports n=92250
2031  imports n=13765  exports n=104579
2032  imports n=4925   exports n=103218
2033  imports n=0      exports n=106368
2034  imports n=0      exports n=110104
2035  imports n=0      exports n=110144
...
2041  imports n=0      exports n=33232
```

Export rebates continue at mean −70 to −77 \$/t throughout, minimum pinned at exactly
−126.41 in several years.

### 3b. The `(EU, steel)` reference keys vanish at 2032 (reconstructed from TM pickles)

| yr | steel keys | iron keys | iron ref max | EU steel FGs | of which zero-cost |
|---|---|---|---|---|---|
| 2029 | 5 | 11 | 28.44 | 127 | 117 |
| 2030 | 6 | 12 | 61.31 | 129 | 121 |
| 2031 | 2 | 12 | 77.11 | 125 | 122 |
| 2032 | **0** | 12 | 92.91 | 124 | **124** |
| 2035 | 0 | 12 | 126.41 | 118 | 118 |
| 2041 | 0 | 6 | 126.41 | 92 | 92 |

From 2032 every EU steel furnace group carries `unit_carbon_cost == 0.0`, so the builder
emits no `(EU_country, "steel")` key; every steel import arc into an EU demand centre then
hits the silent `continue` and `imports n=0` follows directly. Iron keys survive (which is
why the export side kept firing). Note the import channel was already vestigial before
2032 — at best 6 country keys out of a possible 27; the 2026–2031 adjustments were a
handful of surviving integrated plants, not the channel working.

### 3c. The zero is structural, not idleness

In 2033, of 123 EU steel furnace groups only 5 are idle; **118 are producing 121.9 Mt and
still carry exactly zero carbon cost**, including 21 BOF plants. Globally in the same year:

```
iron    producing=562   with positive direct GHG=562   (100.0%)   1166 Mt
steel   producing=1042  with positive direct GHG=111   ( 10.7%)   1908 Mt
```

A representative EU BOF furnace group (2033) shows why: it consumes `hot_metal` and
`scrap`, both entering with `co2_inlet: 0.0` — the emissions were already booked on the
upstream furnace group whose product is `iron`. The steel stage is correctly near-zero in
the model's own accounting; the carbon lives one stage up. Hence: any reference built from
the steel stage's own cost cannot fire on steel imports, anywhere, for the dominant global
route.

### 3d. The export floor identifies which arcs were firing

The export-rebate minimum (−126.41) matches the maximum EU **iron** reference (126.41) to
the cent — exactly what you get when EU iron producers ship to destinations with reference
≈ 0 and collect their whole carbon cost. EU steel producers had `from_carbon_cost == 0.0`
and could never satisfy `from > to` against a non-negative destination: the export side was
iron-only, completing the picture of a one-sided iron export subsidy.

## 4. Issue 2 — the proposed solution, measured

### 4a. Arc level: same year, only `29f0ed0` differs

Year 2026, base arm:

| | v2 (as shipped) | v3 (branch) |
|---|---|---|
| import adjustments | n=5,101, mean 0.14 \$/t | n=144,107, mean 2.88 \$/t |
| — of which steel | **0 (structurally impossible)** | 12,801 |
| export rebates | n=69,342, mean −2.50 \$/t | n=72,731, mean −2.47 \$/t |
| — of which steel | **0 — iron only** | 11,909 |
| imports skipped, no reference | not counted | 27,593 |

The export side barely moves — it was never the broken half. The import side goes from
inert to the larger half of the mechanism (by 2034 the import mean, 88.01 \$/t, exceeds the
export mean in magnitude). Steel import arcs hold at 13,000–16,000 through the 2030s, where
v2 could not produce one in any year. The counts fit the arc population (2026 census: 37 EU
iron FGs, 122 EU steel, 570 non-EU iron, 893 non-EU steel, ~150 demand centres;
blast-furnace groups emit two iron commodities) — v2's 5,101 imports against 69,342 exports
on a near-symmetric population was the anomaly.

### 4b. The noise floor that governs all outcome claims

The two control runs (run3, v2 vs v3) differ only by code that is guarded out when no
mechanism exists — every difference between them is pure run-to-run LP non-determinism.
Measured over all 35 years, EU steel:

| metric | max noise | mean | max 2026–39 | max 2040–60 |
|---|---|---|---|---|
| imports | 13.4 Mt | 2.8 | **1.3 Mt** | 13.4 Mt |
| production | 12.9 Mt | 3.6 | 11.3 | 12.9 |
| capacity | 16.1 Mt | 5.6 | 16.1 | 12.2 |
| exports | **34.6 Mt** | 3.8 | 34.6 | 9.9 |

Consequences: no single-year export claim is possible at all (one control arm exports
31.5–34.6 Mt in 2034–35 while the other exports exactly 0.0); import effects are resolvable
before 2040 at a few Mt but only above ~13 Mt after; noise is serially correlated (the
control pair itself produces 7–8-year same-sign runs), so the claims below rest on
magnitude (2–3× noise) and 20–35-year runs, not short streaks. The floor rests on one
replicate pair — a second replicate would tighten it cheaply and is recommended before any
number travels externally.

### 4c. Outcome level: branch mechanism vs CBAM-off control (v3, identical code)

Five-year sample of run1 − run3 within v3 (`*` = beyond the noise maxima above):

| year | Δproduction (Mt) | | Δcapacity (Mt) | | Δimports (Mt) | |
|---|---|---|---|---|---|---|
| 2030 | +10.7 | | −0.1 | | +5.40 | * |
| 2035 | +25.1 | * | +15.2 | | +5.38 | * |
| 2040 | +28.3 | * | +31.7 | * | −0.02 | |
| 2045 | +25.9 | * | +47.6 | * | −21.00 | * |
| 2050 | +35.3 | * | +37.1 | * | −8.27 | |
| 2055 | +27.2 | * | +28.6 | * | −0.91 | |
| 2060 | +17.9 | * | +23.6 | * | −6.87 | |

Production is higher with the mechanism in all 35 years (+7 to +35 Mt from 2035, beyond the
12.9 Mt ceiling in 24 of 26 years); capacity lags production as fleet decisions should,
building from 2033 to a +49.2 Mt peak in 2043–44, beyond the 16.1 Mt ceiling in every year
from 2040. The import series has three phases: **higher** imports 2026–2039 (+3.6 to
+8.6 Mt against ≤1.3 Mt era noise — the rebate side pushes EU steel into unpriced markets
and imports backfill domestic demand, so gross trade rises both ways while the mechanism is
live); a resolvable import *reduction* only in the 2042–2046 transition (−16 to −21 Mt);
then within-noise differences to 2060. The measurable effect of the corrected mechanism is
on what the EU builds and runs, not a sustained import reduction.

### 4d. Contrast: shipped mechanism vs the same control (v2)

EU steel imports, CBAM on vs off, as shipped:

| year | on | off | diff |
|---|---|---|---|
| 2030 | 0.70 | 0.70 | 0.00 |
| 2035 | 0.66 | 0.66 | 0.00 |
| 2040 | 4.50 | 4.81 | −0.31 |
| 2045 | 10.88 | 13.65 | −2.77 |
| 2050 | 16.68 | 13.47 | +3.21 |
| 2055 | 19.66 | 9.90 | +9.76 |
| 2060 | 21.02 | 16.18 | +4.84 |

Largest difference over all 35 years: +13.05 Mt (2053), inside the 13.4 Mt floor. **The
shipped mechanism had no detectable effect on EU steel imports in any year** — issue 2's
consequence measured on outcomes, not just counters. (It did have one real effect: a
transient capacity-retention bump of +15 to +25 Mt over 2038–2045 via the iron export
subsidy, which died with EU iron — wrong channel, wrong commodity, still no steel-import
protection.)

### 4e. Magnitude bound and replay validation

Every maximum adjustment in 2026–2044 equals the destination carbon price × 2.0622 tCO2/t
to four decimal places, across both scenarios and fifteen years (e.g. base: 3.16 at 1.5325
\$/tCO2, 126.41 at 61.3; cc_cbam: 309.33 at 150, 412.44 at 200). Because the adjustment
*is* a price×intensity differential this cannot prove the embedding weights — it
establishes that magnitudes never exceed what carbon can justify (the failure mode of
issues 1 and 2), that price pass-through is linear, and that the implied binding intensity
(2.0622, a plausible hot-metal figure) is stable. The recurring 126.41 \$/t, once suspected
to be a cap, is simply 61.3 × 2.0622. The weights themselves were validated by replaying
the patched reference builder against the sweep's pickles: 18–19 EU steel reference keys in
every year (0 from 2032 in shipped code), production-weighted EU steel reference 2→27 \$/t
over 2026–2035.

### 4f. Known behaviour of the branch mechanism: inertness from 2046 (base scenario)

From the `[CBAM ADJUST]` lines of the branch base run:

| year | EU iron production | import arcs adjusted | mean import adjustment \$/t |
|---|---|---|---|
| 2044 | 4.9 Mt | 35,625 | 91.31 |
| 2045 | 0 Mt | 31,517 | 85.43 |
| 2046 | 0 Mt | 8,500 | **0.10** |
| 2053 | 0 Mt | 9,361 | 0.10 |

EU iron production ceases → the embedded steel reference loses its domestic iron component
→ the differential ≈ 0. An 850-fold collapse in one year, by design of the differential
formulation (report §3.1), not a bug in the proposed change: run2's scenario sustains 16–27 Mt of EU
iron throughout and its mechanism stays live over the same period (81.70 \$/t in 2046). The
post-2046 production/capacity gaps in §4c are inherited capital — plant that survived
because it was protected in 2026–2045.

## 5. Issue 4 — why the guard was wrong

§3c is the direct measurement: in 2033, 118 of 123 EU steel furnace groups were producing
at zero carbon cost and only 5 were idle — the population the `production_cost == 0.0`
guard actually removes is overwhelmingly *decarbonised*, not idle. The bias it induces is
visible in the reference trajectory (§3b): the EU iron reference climbs to 126.41 \$/t
because each year the cleanest plants leave the average. Note the guard does not cause
issue 2 — before `d6b91f7` the `(EU, steel)` reference was 0.0 (an average of zeros), after
it the key is absent; both yield zero import adjustments from 2032:

| | reference for `(EU, steel)` | import test `from < to` | adjustments |
|---|---|---|---|
| before `d6b91f7` | 0.0 | `from < 0.0` — false for any non-negative cost | none |
| after `d6b91f7` | key absent → `None` → skip | never evaluated | none |

## 6. Issue 5 — bloc overlap, measured

20 of 27 EU countries are also OECD-flagged, and mechanisms iterate in CBAM-sheet column
order with a first-wins lock per arc. In the multi-bloc scenario the lock fires on
~741,000 arcs per year from 2035 (v3 code; ~465,000 in v2 — different code, same defect):
each of those is an arc that a later mechanism saw but was not allowed to touch. Worked
example: DEU→JPN is claimed by the EU mechanism as an export rebate; the OECD mechanism,
under which both are members of a common-carbon-price bloc (intra-bloc, no adjustment),
never runs. Which treatment wins is an artefact of Excel column order. This is why the
report advises against quoting any multi-bloc number after 2034.

## 7. Issue 6 — duplicate arcs

With one active mechanism each `(from, to, commodity)` triple should be visited exactly
once per year, so `duplicates_skipped=26` (2026) means 26 repeated triples in
`legal_allocations`. The count fell to 0 when supply-sourced arcs were skipped — the
duplicates are supplier-sourced, most plausibly two supplier process centres sharing a
name. Not diagnosed further.

## 8. Caveats on everything above

- The noise floor rests on **one** replicate pair; it is a measured range, not a
  distribution. The headline effects sit 2–3× above it, but a second replicate is cheap
  insurance.
- Trade allocations are LP-degenerate: per-furnace-group or single-year trade comparisons
  across runs are meaningless; only aggregates and multi-year patterns are quoted.
- The magnitude bound (§4e) is partially circular and is presented as a
  contamination/bound check, not a proof of the embedding weights — the replay is the
  weight validation.
- Multi-bloc-scenario (run2) numbers after 2034 are contaminated by issue 5 and are not
  used for any claim here.
- No 2025 number is used anywhere above. The master Excel currently carries an EU
  carbon-price jump between 2025 and 2026 (2024–25 at the full 61.3 \$/tCO2, 2026 starting
  the phased path well below it), so the initialisation year is not a comparable baseline —
  this is simply how the input data currently is.
