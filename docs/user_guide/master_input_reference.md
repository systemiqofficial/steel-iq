# Master Input Reference

The master input workbook documents itself: its `Contents` sheet says what each sheet is and where the default data comes from, and sheet-level READMEs explain the authoring conventions. This page is the complement for the people who edit it. For each sheet it lists what every column may contain, what the model does with the value, the rules data preparation enforces on it, and the simulation parameters that change how the sheet is used.

The page is being built up sheet by sheet. The China capacity-replacement policy sheets are documented first; the remaining sheets follow.

## Conventions

- **Names are matched, never retyped.** Technology names must match the `Technology` column of `Techno-economic details`, reductants the `Reductant` column of `Bill of Materials` (matched up to name normalisation, so `Natural gas` and `natural_gas` are the same key), and geographic keys the model's own vocabulary: a bare ISO-3 country code, or `<iso3>:<ISO 3166-2 code>` for a sub-national unit, e.g. `CHN:CN-HE` for Hebei.
- **Blank rows are dropped, extra columns are ignored.** Free-text columns such as `notes` or `source` are for people; the model reads only the columns listed here. Blank cells carry meaning only where a row below says so.
- **Errors stop data preparation; warnings do not.** `steelo-data-prepare` lists every error it finds and writes no fixture for that sheet. Warnings are reported and the fixture is still written; whether a warning later blocks a run depends on the sheet.

## China capacity-replacement policy sheets

Three optional sheets, `Capacity pool - CHN provinces`, `Capacity pool - technologies` and `Capacity pool - opening credits`, feed [China's capacity-replacement policy](../domain_simulation_logic/capacity_replacement_policy.md). They are prepared into fixtures on every data preparation, so authoring errors fail every preparation, but the model reads the fixtures only when a run is started with `--enable-capacity-policy`; without the flag the sheets change nothing. A run with the flag needs all three sheets present and refuses to start on any validation warning left in them. The module internals are in the [technical reference](../domain_simulation_logic/capacity_replacement_policy_reference.md).

### Capacity pool - CHN provinces

One row per Chinese province-level unit. The sheet decides, for every Chinese plant and candidate site, how its closures, replacements and builds are treated by the pool.

| Column | Allowed values | What the model does with it |
|---|---|---|
| `geo_key` | `CHN:<ISO 3166-2 code>`; every Chinese unit the model resolves, each exactly once (31 rows) | The lookup key. A Chinese plant's own geo key is matched against this column to decide whether its closures deposit tagged credits, whether its builds are restricted to a cluster, and whether its replacements are exempt from the ratio. A Chinese location without a sub-national unit resolves to the bare `CHN` key, is treated as non-key, and is warned about. |
| `region_name` | Free text; required when `type` is `key`, read only then | The cluster name. Every credit deposited by a closure in the province carries it as its tag, and every build in the province may spend only credits carrying it. All provinces sharing the same name form one cluster and one pot, so a Hebei build may spend a Tianjin credit. |
| `type` | `key`, `exempt`, or blank | `key`: closures deposit tagged credits and builds are confined to the cluster's credits. `exempt`: every replacement in the province is 1:1 whatever the technologies; closures deposit untagged credits and builds may spend any credit. Blank: a non-key province; closures deposit untagged credits and builds may spend any credit, tagged or not. The asymmetry is the policy's mechanism: capacity bleeds out of the key regions and never back in. |
| `notes`, `province_name`, anything else | Free text | Ignored. |

**Rules enforced at preparation** (all errors): a `type` outside `key`/`exempt`/blank; a `key` row without a `region_name`; a `geo_key` appearing twice (a province cannot sit in two clusters); a Chinese unit missing from the sheet (non-key status must be a recorded decision, not an omission); a `geo_key` that is not a Chinese unit.

**Parameters that interact**: none of the configuration levers change how this sheet is read; the cluster partition is always on while the policy is enabled. The greenfield side additionally needs the geo-unit reference data (the admin-1 layer and the geo hierarchy) to place candidate sites in a province; when it is missing the run warns that it is region-blind.

### Capacity pool - technologies

Two kinds of row share the sheet, told apart by `switching_to`: **classification rows** (blank `switching_to`) carry the one fact per route the policy needs, and **override rows** (filled `switching_to`) pin the swap ratio of a single transition.

| Column | Allowed values | What the model does with it |
|---|---|---|
| `technology` | A roster name; `*` only on override rows | On a classification row, the route being classified. On an override row, the old side of the transition (`*` = any). Every technology China may build needs a classification: the policy sizes every Chinese candidate at valuation time, so a missing route stops the run the first time a Chinese plant considers it. |
| `product` | `iron` or `steel` on classification rows; blank on override rows | Validated for consistency; the model takes a route's product from the technology definition itself, so this column is descriptive here. |
| `reductant` | Blank, or a `Bill of Materials` reductant | Blank: the row classifies the technology for any reductant. Filled: the row classifies that variant alone, and the run-time lookup uses the reductant the candidate is evaluated with, its operating-start pick for a switch, the group's current reductant for the incumbent. A technology with reductant rows may keep a blank-reductant row with no flag; it points at the variants and classifies nothing. A reductant-split technology asked about with no reductant at all is classified by the worst case over its variants (emission-intense if any is), logged and flagged in the gate-decisions CSV. On an override row, an optional restriction to that old-side reductant. |
| `is_emission_intense` | `TRUE`/`FALSE` (also `1`/`0`); blank = not yet authored; must be blank on override rows | `TRUE` marks a route the policy penalises: as the old side of a replacement it pays the ratio when the new side is also `TRUE`; as the new side it pays the ratio when the old side is `TRUE`; as an expansion or greenfield it may build only the planned capacity ÷ `emission_intense_penalty_divisor` while withdrawing the full planned amount. `FALSE` marks a deep-abatement route (at least a 60 % emission reduction against BF-BOF): 1:1 on either side of a replacement and full capacity as a new build. Blank leaves every transition through the route undecided: a warning at preparation, and a refusal to start an enabled run. |
| `switching_to` | Blank on classification rows; a roster name or `*` on override rows | Marks the row as an override and names the new side of the transition. |
| `swap_ratio` | A positive number on override rows; blank otherwise | Replaces the derived ratio for that transition. Most specific override wins: technology + reductant + target, then technology + target, then one side `*`, then both sides `*`; with no override the ratio is derived from the two flags. `BF → *` and `* → EAF` are equally specific for BF → EAF, so both together are an error. No override rows is the normal state. |
| `notes` | Free text | Ignored. |

**Rules enforced at preparation.** Errors: `*` as the technology of a classification row; an unknown technology or reductant on either row type; a product outside `iron`/`steel` on a classification row; a `swap_ratio` on a classification row; a flag, a missing or non-positive `swap_ratio`, or a duplicate on an override row; duplicate classification keys; the equal-specificity collision above. Warnings: an unauthored flag on a classification row, and a roster technology with no classification row at all. Data preparation also writes `fixtures/capacity_pool_ratio_grid.csv`, the old-route × new-route ratios the sheet implies, for checking after an edit.

**Parameters that interact**

| Parameter | Default | Effect on this sheet |
|---|---|---|
| `replacement_ratio` | 1.5 | The derived ratio for a `TRUE` → `TRUE` transition; the group shrinks to `capacity / ratio` and deposits the difference. Every other flag combination derives 1:1. |
| `emission_intense_penalty_divisor` | 1.5 | The buildable share of a `TRUE` route's planned expansion or greenfield capacity. |
| `renovation_counts_as_replace` | `True` | Whether a same-technology renovation of a `TRUE` route pays the ratio like a switch. `False` exempts renovations from the ratio; the utilisation gate applies regardless. |
| `type = exempt` on the provinces sheet | — | Overrides the derived ratio to 1:1 for every replacement in that province. |

### Capacity pool - opening credits

The pool as it stands at the simulation start: capacity retired before the run and not yet spent. An empty sheet is valid and starts the pool at zero.

| Column | Allowed values | What the model does with it |
|---|---|---|
| `vintage_year` | Integer year; a year after the run's start year is clamped down to it with a warning | The credit's position in the queue (oldest spent first), whether it counts as banked before the company cutoff, and the start of its shelf life when one is configured; a credit already past its shelf life at the start is purged before the first year. |
| `capacity_mt` | Positive number, megatonnes | The credit amount, converted to tonnes at seeding. |
| `geo_key` | A Chinese unit key | Decides the credit's tag through the provinces sheet: a key province gives the cluster tag, anything else an untagged credit. |
| `product` | `iron` or `steel` | The stock the credit belongs to; an iron credit never funds a steel build or the reverse. |
| `technology` | Blank, or a roster name | Provenance only; the pool never restricts a credit to a technology. |
| `plant_group_id` | Blank, or a plant group id from the plants data | The owner. From the cutoff year on, only that group can spend the credit under the default banked-credit rule. Blank is an unowned credit: drawable by any Chinese group before the cutoff and swept at it, unless the `persist` rule keeps it. An id matching no plant group is reported at start-up, because nobody can ever spend such a credit after the cutoff. |
| `source` | Free text | Ignored. |

**Rules enforced at preparation** (all errors): a non-positive `capacity_mt`; a product outside `iron`/`steel`; a `technology` not on the roster; a `geo_key` that is not a Chinese unit.

**Parameters that interact**

| Parameter | Default | Effect on this sheet |
|---|---|---|
| `inter_company_swap_cutoff_year` | 2028 | First year a group may spend only its own credits. Before it, every opening credit is drawable by any Chinese group; `None` disables the partition and unowned credits stay drawable for the whole run. |
| `banked_credit_rule` | `reassign` | What happens to credits banked before the cutoff, the opening credits included: `reassign` keeps each with its owner and sweeps the unowned ones; `persist` keeps them all freely spendable; `expire` makes them unusable from the cutoff. |
| `credit_validity_years` (`--credit-validity-years`) | `None` | Shelf life. A vintage `V` credit is usable through `V + N − 1`; opening credits older than that at the start are purged immediately. |
