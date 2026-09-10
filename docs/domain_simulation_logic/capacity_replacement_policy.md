# China Capacity-Replacement Policy

Narrative companion to the reference sections in [Furnace Group Strategy](plant_agent_model/furnace_group_strategy.md) (the REPLACE hook), [Plant Expansions](plant_agent_model/plant_expansions.md) (the expansion gate), [New Plant Opening](geospatial_model/new_plant_opening.md) (the greenfield gate) and [Outputs and Post-Processing](outputs_and_postprocessing.md) (the artefacts a policy run leaves behind). The feature is built on the same pattern as the [CO2 Storage Capacity Gate](co2_storage_gate.md): a finite shared national stock, gated per decision at lifecycle transitions, injected as optional callables that default to `None`.

## Why we need it

Chinese new-build and replacement decisions are not a free-market response to economics. China's capacity-replacement ("swap") regime requires that new iron or steel capacity is built against capacity retired elsewhere, at a ratio and with an eligibility that depend on where the plant sits and on how clean the new route is; from 2028 capacity may no longer be swapped between companies. Without the policy, the model grows Chinese capacity wherever the NPV says so and the swap regime's main effect — dirty replacement surrendering capacity that cleaner projects can then claim — never appears.

The policy is **off by default** and China-only. Enabled, it routes every Chinese retirement, replacement and addition through a decision tree backed by a stateful national pool of retirement credits. Disabled, the module is dormant and a run is byte-identical to a build without it; plants outside China are never touched either way.

## The decision tree

Three branches, evaluated per plant action:

**① RETIRE.** A closure frees capacity into the national pool as a credit. A closure in one of the three **key regions** (Jing-Jin-Ji, Yangtze River Delta, Fenwei Plain) deposits a credit *tagged* with that region's name; a closure anywhere else deposits an *untagged* credit.

**② REPLACE.** A switch or renovation of an existing furnace group is a replacement, and the ratio of old to new capacity depends on the transition:

- old route not emission-intense, or new route not emission-intense ⇒ **1:1**;
- both emission-intense, in an **exempt province** (Qinghai, Tibet) ⇒ **1:1**;
- both emission-intense, anywhere else ⇒ **1.5:1** by default.

The ratio **shrinks the furnace group**: replacing capacity `C_old` yields `C_new = C_old / ratio`, and `C_old − C_new` is deposited into the pool as freed capacity, tagged by the key-region rule. Net national capacity is not created or destroyed, it is reallocated: a dirty replacement surrenders capacity that a cleaner project can then claim. Before any of this, a **utilisation gate** applies: a group that sat at or below the utilisation floor for the whole of the most recent window of recorded years is not eligible for replacement or renovation at all.

**③ INCREASE.** An expansion of an existing plant group, or a greenfield plant, must withdraw a matching amount of credit before it may build. The *applicable pool* is the region's own tagged credits for a build in a key region, and any credit (tagged or not) for a build elsewhere, so capacity bleeds *out* of key regions but never in. If the applicable pool cannot cover the build, it is not allowed. If it can, the credits are consumed oldest first; an emission-intense build still withdraws the full planned amount but may only build the planned amount **÷ 1.5**.

### Classifying routes

The tree needs one fact per route: whether it is emission-intense. Routes are keyed on `(technology, reductant)` because the policy's vocabulary does not map onto technology names alone — "non-coal DRI" is a reductant condition, not a technology. The classification is a single authored flag in the master Excel (`Capacity pool - technologies`); a route that is *not* emission-intense qualifies as deep abatement by definition, matching the policy's own threshold of at least a 60 % emission reduction against the BF-BOF route. Sparse override rows in the same sheet can pin the ratio of an individual transition, with `*` wildcards on either side. An unauthored flag is not `False`: the policy refuses to run rather than guess, because a silent 1:1 would exempt the pair.

Iron and steel are **separate stocks**: a credit carries its product, and an iron credit never funds a steel build.

## The pool

`CapacityPool` is an age-ordered queue of credits. Each credit carries an amount, a vintage year, a region tag (or none), an owner (the depositing plant group, by membership) and a product. Withdrawals see only the applicable pool — the credits passing every filter — and are served oldest first, all or nothing. A partially consumed credit splits and the remainder keeps its vintage.

| Filter | Rule |
|---|---|
| **Region** | A key-region build spends only credits carrying its own cluster tag; a build elsewhere spends any credit. Member provinces share the cluster tag, so a Hebei build can spend a Tianjin credit. |
| **Product** | Exact match, iron or steel. |
| **Owner** | Before the inter-company swap cutoff (2028 by default) any Chinese group may draw any credit. From the cutoff an expansion may spend only credits its own group deposited; credits banked before the cutoff follow the `banked_credit_rule` (see the levers below). Opening credits with no named owner are drawable before the cutoff and swept at it. |

**Greenfield builds draw from a single holder.** A new plant has no owner yet, so its withdrawal is served entirely from one plant group's credits — the holder whose oldest applicable credit comes first in the queue, among those holding enough — and the plant joins that company at construction start. The unowned opening pot counts as one such holder. A greenfield can therefore be refused with an ample pool when no single holder covers it, which the ledger and log distinguish from a short pool.

**Credits can expire.** With `credit_validity_years` set, a credit of vintage `V` is usable through `V + N − 1` and purged on entering `V + N`; purged credits are recorded so the expired-unused capacity is visible per region. Without it, credits live forever and the pool only grows.

**The opening pool is state, not history.** The `Capacity pool - opening credits` sheet seeds the pool at the simulation start with capacity retired in the past and *not yet spent*; region tags are derived from the province at load time, and an empty sheet is a legitimate zero-pool start (every INCREASE is then blocked until the first Chinese retirement banks a credit). Vintages after the start year are clamped down to it, and credits already past their shelf life at the start are purged.

## Where the policy acts

The policy is threaded into the plant-agent decision paths as optional callables; the adapters in `steelo.capacity_policy.handlers` decide applicability (China only) and translate the domain's plain values onto the tree, so the domain module never learns about the policy itself.

| Branch | Fires in | What happens | On block |
|---|---|---|---|
| **① RETIRE** | The `FurnaceGroupClosed` handler, and the end-of-life closure in `finalise_iteration` | The full freed capacity is deposited at the closure year's vintage, tagged by the key-region rule and owned by the closing group | — |
| **② REPLACE** | `Plant.evaluate_furnace_group_strategy`, before the NPV of every candidate — switches and the same-technology renovation alike | Utilisation gate, then the permitted capacity `C_old / ratio`; the candidate is valued at that capacity. When the switch or renovation executes, the group shrinks and the delta is deposited (`FurnaceGroupTechChanged`, `FurnaceGroupRenovated`) | The candidate is dropped from the menu; a group left with no candidate carries on unchanged |
| **③ INCREASE, expansion** | `PlantGroup.evaluate_expansion`: a non-consuming sizing query feeds every candidate NPV, and the consuming withdrawal runs at the point of commitment after every other expansion check | The planned capacity is withdrawn from the applicable pool; the build is the planned capacity, or ÷ 1.5 when the route is emission-intense | No expansion for that group this year |
| **③ INCREASE, greenfield** | `FurnaceGroup.track_business_opportunities`: the sizing query feeds opportunity identification and the yearly re-valuation, a non-consuming feasibility probe runs before the announcement draw, and the consuming single-holder withdrawal runs when the draw succeeds (considered → announced) | Credits are consumed at announcement and travel with the opportunity; at construction start the plant moves into the funding company (`FurnaceGroupAdded`); if the announced plant is later discarded, the consumed credits are refunded at their original vintages | The opportunity stays considered and retries next year; each blocked year counts towards `capacity_pool_max_retry_years`, at which the opportunity is discarded without ever having withdrawn |

**The permitted capacity is known before the NPV on every branch.** A penalty that only bites after the decision could block or shrink a build but never steer technology choice, so every valuation — switch, renovation, expansion and greenfield — runs at the capacity the policy permits. The sizing query and the consuming gate share one arithmetic, and the expansion path raises if they ever disagree.

## Configuration

The levers live on `SimulationConfig.capacity_policy` (`CapacityPolicyConfig`), read at run time as `bus.env.config.capacity_policy.<field>`. The data — which provinces, which routes, what opening pool — lives in three master-Excel sheets; the flag is scenario intent, the sheets are facts.

| Lever | Default | Meaning |
|---|---|---|
| `enabled` | `False` | The on/off switch. With it off the module is dormant. With it on, missing fixtures or empty province/technology sheets are a hard error at bootstrap, never silent dormancy. |
| `replacement_ratio` | `1.5` | Old-to-new ratio for a penalised replacement; the group shrinks to `C_old / ratio`. |
| `emission_intense_penalty_divisor` | `1.5` | Divisor on the buildable capacity of an emission-intense new build. A separate lever that happens to share the default. |
| `min_utilization_for_renovation` | `0.25` | Utilisation floor of the gate before branch ②. |
| `utilization_window_years` | `2` | Consecutive recorded years at or below the floor that make a group ineligible. Fewer recorded years than the window never block. |
| `inter_company_swap_cutoff_year` | `2028` | First year a company may only spend its own credits; `None` disables the owner partition. |
| `banked_credit_rule` | `"reassign"` | Treatment of pre-cutoff credits from the cutoff on: `reassign` keeps them with their depositor like any other credit; `persist` grandfathers them as freely spendable by anyone; `expire` makes them unusable by everyone. |
| `credit_validity_years` | `None` | Shelf life of a banked credit; `None` never expires. |
| `capacity_pool_max_retry_years` | `2` | Blocked years after which a considered greenfield is discarded. |
| `renovation_counts_as_replace` | `True` | A same-technology renovation is a full replacement (gate, ratio, shrink and deposit). `False` exempts renovations from the ratio; the utilisation gate applies either way. |

## Running with the policy

```bash
run_simulation --enable-capacity-policy
run_simulation --enable-capacity-policy --credit-validity-years 5
```

The run needs the three `Capacity pool - …` sheets in the master Excel (`CHN provinces`, `technologies`, `opening credits`), prepared into fixtures like every other sheet; their columns, allowed values and validation rules are in the [Master Input Reference](../user_guide/master_input_reference.md#china-capacity-replacement-policy-sheets). Data preparation validates them: authoring mistakes fail the preparation, unauthored classification flags only warn, and a policy-enabled run then refuses to bootstrap on any of those warnings. The policy also needs sub-national geo units for Chinese plants; when the geo-unit reference data is missing the run warns that it is region-blind, and every Chinese location that resolves to a bare `CHN` key is treated as non-key.

Every gate decision, deposit and withdrawal is logged as a `[CAPACITY POOL] …` line, and the run writes a ledger, a yearly pool state, the gate decisions and the Chinese fleet motions to `data/policy/`, drawn as charts in `plots/capacity_pool/` — see [Outputs and Post-Processing](outputs_and_postprocessing.md#fleet-motions-and-capacity-policy-artefacts).

## Key design choices

**Hard gates at the decision, not an annual budget.** There is no point in the model where a national capacity total could be rationed, so the policy fires per decision at the lifecycle transitions, like the CO2 storage gate.

**A replacing plant may not spend credits to avoid shrinking.** Branch ② deposits only; letting a switch buy its way out of the ratio would be a loophole the model should not exploit.

**Renovation is a replacement.** The tree's second branch asks whether a blast furnace or high-emission technology is being replaced and draws no same-technology exemption, so a BF renovation pays the ratio while an EAF renovation passes 1:1 by derivation. Treating renovations as neutral is the configurable deviation, not the default.

**Classification follows the candidate's own reductant pick.** The REPLACE gate classifies the new route by the reductant the candidate's NPV commits at operating start, the same series the CO2 gate uses. Routes with reductant-specific classification (the DRI family) re-optimise their reductant annually and may later run a different one from the one they were classified under; the divergence is bounded, because the NPV priced the later years, and measurable from any run by joining the gate decisions against the fleet motions.

**Every retirement earns a credit.** No eligibility test on idle or unregistered capacity; a modelling decision, not sourced policy, and one reason credit expiry matters.

**Credits are fungible within (region, product, owner).** The retired technology is provenance only; no fourth filter restricts an ironmaking credit to a blast furnace.

**Ownership is group membership, and greenfield capital is outside money.** Deposits and withdrawals name the plant group a plant belongs to, so a credit-funded greenfield that later retires banks its credit with the company that funded it. That company absorbs the plant's profit and debt but is not debited the equity, which is assumed to be an outside investment.

**Key regions are whole provinces.** The regulation names city clusters; the model resolves provinces, so any listed city promotes its whole province (eleven key provinces). Unlisted cities of a key province are over-applied by construction.

**Consume at commitment, refund on discard, cap the retries.** Credits are spent when an expansion is approved or a greenfield is announced, handed back if the announced plant is discarded, and a considered greenfield the pool keeps blocking dies at the retry cap rather than retrying forever.

**The utilisation gate is blind at first.** Utilisation history starts empty and is not back-filled, so the gate cannot fire during the first window of any run.

## What to expect in results

The policy's effect is largely what it *prevented*, which is why the ledger and the refused-capacity charts exist. Two behaviours are worth checking in any enabled run before reading its results as policy analysis. First, the owner partition after the cutoff fragments the pool: retirements refill it quickly, but the credit sits with many small holders, so a large share of what is minted may never be spendable and refusals concentrate on expansions of existing groups. Second, capacity refused in China does not vanish: the trade model can relocate iron and steel production abroad, so a Chinese emission reduction can coincide with a global increase. Neither is a defect, but both should be read off the run's own artefacts rather than assumed.

## Related documentation

- [Capacity-Replacement Policy: Technical Reference](capacity_replacement_policy_reference.md) — module layout, the input sheets column by column, pool arithmetic and the artefact contract
- [CO2 Storage Capacity Gate](co2_storage_gate.md) — the structurally identical gate this feature copies
- [Furnace Group Strategy](plant_agent_model/furnace_group_strategy.md), [Plant Expansions](plant_agent_model/plant_expansions.md), [New Plant Opening](geospatial_model/new_plant_opening.md) — the decision paths the hooks sit in
- [Outputs and Post-Processing](outputs_and_postprocessing.md) — the policy CSVs, charts and the decision-flow viewer
