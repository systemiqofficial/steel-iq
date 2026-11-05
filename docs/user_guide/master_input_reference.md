# Master Input Reference

The master input workbook documents itself: its `Contents` sheet says what each sheet is and where the default data comes from, and sheet-level READMEs explain the authoring conventions. This page is the complement for the people who edit it. For each sheet it lists what every column may contain, what the model does with the value, the rules data preparation enforces on it, and the simulation parameters that change how the sheet is used.

The page is being built up sheet by sheet. The China capacity-replacement policy sheets, `Cost of capital`, `Furnace units`, `Power grid emissivity`, `Biomass availability`, `Tariffs`, `CBAM`, the geography columns of `Iron and steel plants` and the `Region` column of `Iron ore mines` are documented so far; the remaining sheets follow.

## Conventions

- **Names are matched, never retyped.** Technology names must match the `Technology` column of `Techno-economic details`, reductants the `Reductant` column of `Bill of Materials` (matched up to name normalisation, so `Natural gas` and `natural_gas` are the same key), and geographic keys the model's own vocabulary: a bare ISO-3 country code, or `<iso3>:<ISO 3166-2 code>` for a sub-national unit, e.g. `CHN:CN-HE` for Hebei.
- **Blank rows are dropped, extra columns are ignored.** Free-text columns such as `notes` or `source` are for people; the model reads only the columns listed here. Blank cells carry meaning only where a row below says so.
- **Errors stop data preparation; warnings do not.** `steelo-data-prepare` lists every error it finds and writes no fixture for that sheet. Warnings are reported and the fixture is still written; whether a warning later blocks a run depends on the sheet.

## China capacity-replacement policy sheets

Three optional sheets, `Capacity pool - CHN provinces`, `Capacity pool - technologies` and `Capacity pool - opening credits`, feed [China's capacity-replacement policy](../domain_simulation_logic/plant_agent_model/capacity_replacement_policy.md). They are prepared into fixtures on every data preparation, so authoring errors fail every preparation, but the model reads the fixtures only when a run turns the policy on, with `--enable-capacity-policy` or the app's "Capacity-replacement policy (China)" switch; without it the sheets change nothing. The app's run form refuses the switch for a data preparation without the capacity pool fixtures. A run with the policy on needs all three sheets present and refuses to start on any validation warning left in them. The module internals are in the [technical reference](../domain_simulation_logic/plant_agent_model/capacity_replacement_policy_reference.md).

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
| `reductant` | Blank, or a `Bill of Materials` reductant | Blank: the row classifies the technology for any reductant. Filled: the row classifies that variant alone, and the run-time lookup uses the reductant the candidate is evaluated with, its operating-start pick for a switch, the group's current reductant for the incumbent. A technology with reductant rows may keep a blank-reductant row with no flag; it points at the variants and classifies nothing. A reductant-split technology asked about with no reductant at all is classified by the worst case over its variants (emission-intense if any is), logged and flagged in the gate-decisions CSV. On an override row, an optional restriction to that old-side reductant, valid only for a technology classified per reductant. |
| `is_emission_intense` | `TRUE`/`FALSE` (also `1`/`0`); blank = not yet authored; must be blank on override rows | `TRUE` marks a route the policy penalises: as the new side of a replacement it pays the ratio whatever the old side is (stricter than the source flowchart, which tests the old side first; a modelling decision); as the old side it has no effect on the ratio; as an expansion or greenfield it may build only the planned capacity ÷ `emission_intense_penalty_divisor` while withdrawing the full planned amount. `FALSE` marks a deep-abatement route (at least a 60 % emission reduction against BF-BOF): 1:1 as the new side of a replacement and full capacity as a new build. Blank leaves every transition through the route undecided: a warning at preparation, and a refusal to start an enabled run. |
| `switching_to` | Blank on classification rows; a roster name or `*` on override rows | Marks the row as an override and names the new side of the transition. |
| `switching_to_reductant` | Blank, or a `Bill of Materials` reductant on override rows; must be blank on classification rows. Optional column: a sheet without it reads as all blank | Blank: the override applies whatever reductant the new route is evaluated with. Filled: the override applies only when the new route carries that reductant, mirroring `reductant` on the old side; valid only where `switching_to` is `*` or a technology classified per reductant. A new route evaluated with no reductant at all (the worst-case classification above) never matches a filled cell, so it falls through to a less specific override or to the derived ratio. |
| `swap_ratio` | A positive number on override rows; blank otherwise | Replaces the derived ratio for that transition. Most specific override wins: technology + target, then one side `*`, then both sides `*`; within a level the row naming more reductants wins (both sides, then one, then none); with no override the ratio is derived from the two flags. Two rows at the same level naming the same number of reductants that can match one transition are equally specific, so both together are an error: `BF → *` with `* → EAF` (both match BF → EAF), or a `DRI → DRI` row with `reductant` `Coal` beside one with `switching_to_reductant` `Coal` (both match coal DRI → coal DRI). No override rows is the normal state. |
| `notes` | Free text | Ignored. |

**Rules enforced at preparation.** Errors: `*` as the technology of a classification row; an unknown technology or reductant on either row type; a product outside `iron`/`steel` on a classification row; a `swap_ratio` or a `switching_to_reductant` on a classification row; a flag, a missing or non-positive `swap_ratio`, a `reductant` or `switching_to_reductant` restriction on a technology not classified per reductant, or a duplicate (same technology, reductant, target and target reductant) on an override row; duplicate classification keys; the equal-specificity collision above. Warnings: an unauthored flag on a classification row, and a roster technology with no classification row at all. Data preparation also writes `fixtures/capacity_pool_ratio_grid.csv`, the old-route × new-route ratios the sheet implies, for checking after an edit.

**Worked example: penalising coal DRI only.** `DRI` is classified per reductant, so an override on `* → DRI` alone would also catch hydrogen and natural-gas DRI. Pinning the new side's reductant confines it to the coal route:

| `technology` | `reductant` | `switching_to` | `switching_to_reductant` | `swap_ratio` |
|---|---|---|---|---|
| `*` | | `DRI` | `Coal` | 2 |

Every replacement that builds coal DRI now surrenders capacity at 2:1, whatever it replaces. Replacements building `DRI` on hydrogen or natural gas keep their derived ratio, and in `capacity_pool_ratio_grid.csv` only the `DRI|Coal` column changes.

**Parameters that interact**

| Parameter | Default | Effect on this sheet |
|---|---|---|
| `replacement_ratio` | 1.5 | The derived ratio for a transition whose new side is `TRUE`, whatever the old side; the group shrinks to `capacity / ratio` and deposits the difference. A `FALSE` new side derives 1:1. |
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

## Cost of capital

One row per (country, technology). The sheet sets the interest rate on debt and the required return on equity for every investment the plant agents evaluate: renovations, technology switches, expansions and new plants. It is prepared into `cost_of_capital.json` by `steelo-data-prepare`; the model reads it on every run.

| Column | Allowed values | What the model does with it |
|---|---|---|
| `Country` | Free text | Stored with the country's rates for reference. |
| `ISO-3 Code` | A bare ISO-3 country code | Groups the rows. Plants and candidate sites take their rates from their own country code. Some codes appear under several territory names (for example `ESP`); identical duplicate rows are merged. |
| `Tech` | A technology name exactly as in the `Technology` column of `Techno-economic details`, or `Renewables` or `Hydrogen` | Matched verbatim, without name normalisation. Each candidate technology is financed at its own row's rates. |
| `Cost of debt` | A fraction (`0.05` = 5%) | Interest rate on the debt share of CAPEX (1 − `equity_share`), before any cost-of-debt subsidy. Sets the debt repayment schedule in every NPV and in the furnace group's realised P&L. |
| `Cost of equity` | A fraction | Discount rate for the investor's NPV of a candidate and for the incumbent's cost of stranded assets (COSA). |
| `Cost of capital` | A fraction (weighted average cost of capital) | Stored but **not used**. The model finances from `Cost of debt`, `Cost of equity` and `equity_share`, so editing this column changes nothing. |

**Rules enforced at preparation** (all errors):

- two rows for the same `ISO-3 Code` and `Tech` with different values
- a country that does not cover every technology in `Techno-economic details`, plus `Renewables` and `Hydrogen`

The `Renewables` and `Hydrogen` rows are required but not currently read by the model: the levelised costs of electricity and hydrogen do not take their financing rates from this sheet.

**At run time**: a plant in a country with no rows stops the run when its furnace groups are evaluated, and a new-plant site in such a country stops the run when its NPV is prepared. Blank rate cells are not caught at preparation: they are read as NaN and make the affected NPVs invalid, so those candidates never win. Fill every rate cell.

**Parameters that interact**: `equity_share` (default 0.2) sets the share of CAPEX paid from the plant group's balance; the rest is debt at `Cost of debt`. Cost-of-debt subsidies lower `Cost of debt` for the technologies they cover, floored at the risk-free rate.

## Furnace units

One row per furnace group (a furnace unit). The sheet is the model's starting fleet: rows sharing a `plant_id` become one plant, and each row becomes one furnace group of that plant. `steelo-data-prepare` reads it into `plants.json` by default; `--use-plants-sheet` switches back to the older `Iron and steel plants` sheet, and a workbook with no `Furnace units` sheet falls back to that sheet with a warning.

| Column | Allowed values | What the model does with it |
|---|---|---|
| `plant_id` | Any identifier; blank rows are skipped | Groups rows into plants. |
| `unit_id` | Any identifier | Used only in a temporary ID during preparation. The final furnace group ID is `<plant_id>_<n>`, numbered from 0 in sheet order, so `unit_id` does not reach `plants.json`. |
| `technology` | A technology name from `Techno-economic details` | The furnace group's technology. Its business cases come from `Bill of Materials`. |
| `capacity_ttpa` | Capacity in thousand tonnes per year | The furnace group's capacity. Rows with a blank, zero or negative capacity are skipped. |
| `status` | A furnace group status, for example `operating`, `construction`, `announced`; required in every row | The starting status. `operating` applies only when the whole column is missing; a blank cell is read as the status `nan`. `announced` and `construction` mark pipeline units (see the start-year rules below). |
| `start_year` | A year, or blank | Commissioning year; sets the renovation cycle. Blank values, and pipeline units dated before the reference year, are imputed (see below). |
| `last_renovation_year` | A year, or blank | Last renovation; defaults to the start year. |
| `iso3` | A real ISO-3 country code; required | The plant's country. Read from the plant's first row. |
| `geo_unit_or_province` | An ISO 3166-2 sub-national code (for example `CN-HE`), or blank | With `iso3` it forms the plant's geographic key (`CHN:CN-HE`), used for province-level prices and subsidies. Blank places the plant at country level. |
| `latitude`, `longitude` | Decimal degrees | Plant location, from the plant's first row. |
| `region`, `power_source`, `soe_status`, `parent_gem_id`, `workforce_size` | Free text or numbers; optional | Plant attributes, from the plant's first row. |
| `plant_name`, `source` | Free text | Read only by the interactive capacity map viewers, for plant labels and the source line. |

**Rules enforced at preparation** (all errors):

- a row with a `plant_id` but no `iso3`, or an `iso3` that is not a real ISO-3 code
- a filled `geo_unit_or_province` that does not form a key in the prepared geo hierarchy (`geo_hierarchy.json`)

When no geo hierarchy has been prepared yet, the sub-national check is skipped with a warning.

### Start-year imputation

The model sets every furnace group's renovation cycle from its start year, so undated units would otherwise all reach the end of their lifetime in the same year. Preparation fills the gaps:

- **Operating units with no `start_year`**: a year drawn uniformly from a vintage band, **2000 to 2013 for China** and **2005 to 2025 elsewhere** (both ends included).
- **Pipeline units** (`announced` or `construction`) with no `start_year`, or with one before the reference year: dated forward from the reference year, because the simulation switches a pipeline unit on only in the year equal to its start year, and a past year would strand it.
  - `construction`: reference year + 0 to 4 years (`CONSTRUCTION_TIME_DEFAULT`)
  - `announced`: reference year + 0 to 1 years (`ANNOUNCEMENT_TIME`) + 4 years of construction, so 4 to 5 years ahead

Imputed units carry `age_source = "imputed"` in their metadata; units with an authored year carry `"exact"`. The draws use a fixed seed (`RANDOM_SEED_DEFAULT` = 42) that the `--random-seed` option of `run_simulation` does not change, so the same sheet always gets the same years. The reference year is currently fixed at 2025 in preparation.

Preparation logs how many operating units and how many pipeline units it imputed, when either count is above zero.

## Iron ore mines

This section covers the `Region` column only; the capacity, cost and price columns are not yet documented here.

| Column | Allowed values | What the model does with it |
|---|---|---|
| `Region` | One of the 22 names below, spelled exactly | Mapped to the mine's ISO-3 code. The trade model keys transport costs and tariffs on that code. |

| Region | ISO-3 | Region | ISO-3 |
|---|---|---|---|
| Australia | AUS | Mauritania | MRT |
| Bosnia & Herzegovina | BIH | Mexico | MEX |
| Brazil | BRA | Norway | NOR |
| Canada | CAN | Peru | PER |
| Chile | CHL | Russia | RUS |
| China | CHN | Sierra Leone | SLE |
| Guinea | GIN | South Africa | ZAF |
| India | IND | Sweden | SWE |
| Iran | IRN | Ukraine | UKR |
| Kazakhstan | KAZ | USA | USA |
| Liberia | LBR | Venezuela | VEN |

**Rules enforced at preparation** (error): a mine with capacity in any year whose `Region` is not in the table. Mines with no capacity in any year are skipped before the check. The mapping lives in code (`translate_mine_regions_to_iso3` in `excel_reader.py`), so a new mine country needs a code change, not only a workbook edit.

Before this check, an unmapped region gave the mine an empty ISO-3 code and its ore travelled without transport costs or tariffs in the trade model; see [Fixes that affect comparability between runs](../domain_simulation_logic/overarching_elements/outputs_and_postprocessing.md#fixes-that-affect-comparability-between-runs).

## Iron and steel plants

The older plant sheet, read instead of `Furnace units` when data preparation runs with `--use-plants-sheet` or the workbook has no `Furnace units` sheet. This section covers its geography columns only.

| Column | Allowed values | What the model does with it |
|---|---|---|
| `ISO3` | A bare ISO-3 code (`CHN`) or a combined geo key (`CHN:CN-AH`) | The plant's country and, in the combined form, its province. A row with a blank `ISO3` is skipped. |
| `geo_unit` | An ISO 3166-2 code (`CN-AH`), or blank; optional column | The province for a row whose `ISO3` is a bare country. Blank places the plant at country level. When the row also uses the combined `ISO3` form with a different unit, the combined value wins. |

**Rules checked by `validate-master-input` and the web app upload** (not by `steelo-data-prepare`, so a bad value passes preparation unchecked):

- error: a country code that is not a real ISO-3 code, or a unit that does not form a key in `geo_hierarchy.json`
- warning: a combined `ISO3` value and a different `geo_unit` value on the same row

**At preparation**: a sheet with no `ISO3` column takes the country from its `Country` column (workbooks older than master input v2.2); a sheet with neither column is rejected.

The country and province come only from these columns. The model no longer derives them from the plant's coordinates. See [Sub-national Geography](../domain_simulation_logic/overarching_elements/sub_national_geography.md) for how province-level values are used.

## Power grid emissivity

One row per (geography, year, scenario). The sheet sets the grid electricity emission factor that gives each furnace group its indirect (Scope 2) electricity emissions. It does not change carbon cost, which charges direct emissions only.

| Column | Allowed values | What the model does with it |
|---|---|---|
| `country_iso3` (or `ISO 3-letter code`, or `ISO-3 code`) | A bare ISO-3 code (`CHN`) or a geo key (`CHN:CN-HE`) | The geography. Plants resolve the province row first, then the country row. |
| `country` | Free text | Stored as the country name. |
| `year` | A year | The year of the factor. |
| `projection_scenario` | `projection_<name>`, for example `projection_business_as_usual` | The scenario. The run uses the rows whose name, without the `projection_` prefix and in title case, equals `--grid-emissions-scenario` (default `Business As Usual`; the current data also carries `Net Zero`). |
| `ghg_factor_scope_2` | A number (tCO2e per unit of electricity) | The grid emission factor. |

Coke and natural gas factors for each geography come from the country rows of the `Met coal & gas emissions` sheet; a province row takes its country's factors.

**Rules**:

- at preparation (warning): a province row that does not author every year its country authors; the missing years are filled from the country's rows for the same scenario
- at run start (error): `--grid-emissions-scenario` matches no scenario in the sheet; the message lists the available scenarios
- during the run (error): a plant whose location has no emissivity at province or country level, or whose entry has no value for the current year

Before these errors existed, a plant with no matching data, or a run with a mistyped scenario, silently got a grid emissivity of 0.0.

## Biomass availability

The sheet sets annual bio-PCI availability limits for the trade model, per region. Each limit becomes a secondary feedstock constraint: the total flow of bio-PCI into furnace groups located in the region (or country) may not exceed the limit for the year. CO2 storage limits come from the separate [`CO2 storage`](#co2-storage) sheet.

| Column | Allowed values | What the model does with it |
|---|---|---|
| `tiam-ucl_region` | A TIAM-UCL region name, as in the `tiam-ucl_region` column of `Country mapping` | The region of the row: the limit covers every country mapped to that region. Rows with a blank region are skipped. |
| `Country` | A country name, or blank | A row with a country applies to that country only. |
| `Metric` | `Bio-PCI availability` | Any metric without "co2" in it becomes a `bio_pci` limit. A metric containing "co2" becomes a `co2_stored` limit instead; the current data has no such rows. |
| `Scenario` | Free text; blank in the current data | Stored; not used to filter rows. |
| `Unit` | `kg charcoal-equivalent/y` | A unit containing "kg" is divided by 1000, so every limit is in tonnes per year. |
| `2024` to the last year column | Numbers | The limit for that year. Blank cells set no limit for that year. |

**At run start** (error): a bio-PCI region that matches no `tiam-ucl_region` in `Country mapping`, no ISO-3 code and no country name.

**How the trade model applies the limits**: each constrained commodity (bio-PCI from this sheet, CO2 storage from the `CO2 storage` sheet) gets a synthetic supplier (`bio_pci_supply_process_center`, `co2_stored_supply_process_center`) with no production cost, connected to every production process; the per-region or per-country limits then cap the flows from it. Scrap is not limited this way: scrap availability comes from scrap suppliers.

Before the kg conversion, bio-PCI limits were read 1000 times too high and never bound; see [Fixes that affect comparability between runs](../domain_simulation_logic/outputs_and_postprocessing.md#fixes-that-affect-comparability-between-runs).

## CO2 storage

One row per country. The sheet sets each country's annual CO2 storage capacity. Preparation stores these limits alongside the bio-PCI limits as `co2_stored` constraints, so the trade model applies them the same way (see [Biomass availability](#biomass-availability)). They also set the budget of the [CO2 storage gate](../domain_simulation_logic/co2_storage_gate.md).

| Column | Allowed values | What the model does with it |
|---|---|---|
| `ISO-3 code` | An ISO-3 code | The country the limit applies to. Rows with a blank code are skipped. |
| `Scenario` | Free text | Stored; not used to filter rows. |
| `Unit` | `t CO2/y` | Stored only. Values are read as tonnes of CO2 per year, with no conversion. |
| `2024` to the last year column | Numbers | The limit for that year. Blank cells set no limit for that year; rows with every year blank are skipped. |

Other columns, such as `Country` and `Metric`, are not read. A workbook without the sheet is prepared with a warning and no CO2 storage limits.

## Tariffs

One row per tariff, quota, or both, on a trade route. The sheet is prepared into `tariffs.json` by `steelo-data-prepare`; the model reads it on every run. How the trade model applies the rows is described in [Tariffs, Quotas and Carbon Border Adjustments](../domain_simulation_logic/trade_model/trade_policy.md#tariffs-and-quotas).

| Column | Allowed values | What the model does with it |
|---|---|---|
| `Tariff scenario name` | Free text | Labels the row. Rows with a blank name are dropped. |
| `From ISO3` | An ISO-3 code, a trade bloc, `NOT <bloc>`, `NOT <ISO-3>` or `*` | The exporting side. A bloc is any True/False column of `Country mapping`, for example `EU` or `EFTA/EUCU`; see [Country and bloc entries](../domain_simulation_logic/trade_model/trade_policy.md#country-and-bloc-entries). Data preparation writes one tariff for every exporter and importer pair and drops pairs within one country. |
| `To ISO3` | As `From ISO3` | The importing side. |
| `Commodity` | A commodity name, for example `steel`, or `*` for every commodity | Matched after name normalisation. |
| `Tax [$/t]` | A number in USD per tonne, or blank | Added to the cost of every tonne shipped on the route. |
| `Tax [%]` | A fraction (`0.25` = 25%), or blank | Converted each year to USD per tonne at the average price of the commodity in the exporting country. |
| `Quota [t]` | Tonnes per year, or blank | Caps the flow on each exporter and importer pair. `0` bans the route. A quota on a bloc caps each member country separately, not the bloc as a whole. |
| `Start year` | A year, or blank | First year the row applies. Blank means 2000. |
| `End year` | A year, or blank | Last year the row applies. Blank means 2100. |
| `Metric (Volume/Emissions)` | Free text | Stored; not used by the model. |

**Rows skipped at preparation**: a row whose taxes are both blank or zero and whose quota is blank sets nothing and is dropped. A zero quota is kept.

**Rules enforced at preparation** (error): a `NOT <X>` entry where `<X>` is neither a trade bloc nor a known ISO-3 code.

**Not caught**: any other entry that is not a known bloc is taken as an ISO-3 code as written. A misspelt bloc, such as `EU ` with a trailing space, therefore becomes a country that matches no flows, and the row silently has no effect.

**At run time**: when several rows give the same exporter, importer and commodity, the lowest quota applies and fixed taxes add up. A percentage tax replaces any tax already set by an earlier row instead of adding to it. A percentage tax on an exporter with no average commodity price is skipped with a warning.

## CBAM

Defines the carbon border adjustment mechanisms. The sheet is prepared into `carbon_border_mechanisms.json` by `steelo-data-prepare`; the model reads it on every run. How the charge is calculated is described in [Carbon border adjustments](../domain_simulation_logic/trade_model/trade_policy.md#carbon-border-adjustments).

The first column holds the row labels. Every other column is one mechanism. Its header must name a trade bloc column of `Country mapping`, which defines the member countries. Headers are normalised the same way as bloc names, so `EFTA/EUCU` matches the `EFTA_EUCU` bloc. The rows are read **by position**, not by label, so keep them in this order:

| Row | Allowed values | What the model does with it |
|---|---|---|
| 1. `CBAM active?` | `1`, `0` or blank | Only columns with `1` become mechanisms. |
| 2. `Year CBAM begins` | A year | First year imports into the bloc are charged. A blank or invalid year skips the mechanism with a warning. |
| 3. `Year CBAM ends` | A year, or blank | Last year of the charge. Blank means it never ends. |
| 4. `Common carbon cost across the bloc?` | `1`, `0` or blank | `1` prices the border for every member at the bloc's own row of `Carbon cost`. Otherwise each member uses its national carbon price. If the whole row is missing, every mechanism uses national prices. |

**Rules at preparation**: none raise an error. A sheet with fewer than three rows produces no mechanisms, with a warning.

**At run time**: a mechanism whose header matches no countries in `Country mapping` charges nothing and is reported with a warning. A member with no carbon price is not charged, and the model logs a warning for it each year.

**Not caught**: with the common-price flag set, a bloc that has no row on the `Carbon cost` sheet is priced at zero, so its members charge nothing, without a warning.

**Parameters that interact**: `carbon_border_export_rebates` (CLI `--cbam-rebate`, default off) also rebates the carbon already paid on exports leaving a member country.
