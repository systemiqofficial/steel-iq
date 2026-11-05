# Tariffs, Quotas and Carbon Border Adjustments

The trade model supports three trade policy instruments. Each one changes which trade flows the linear programme (LP) chooses, and the resulting costs then reach the importing plants through the TM-PAM connector.

| Instrument | Master input sheet | Effect in the trade LP | Effect on plant costs |
|---|---|---|---|
| **Tariff tax** | `Tariffs` | Adds a cost per tonne to every matching cross-border arc | Added to the importer's material cost |
| **Trade quota** | `Tariffs` | Caps the tonnes that can flow on a country pair | None directly; it changes which suppliers an importer uses |
| **Carbon border adjustment** | `CBAM`, `Carbon cost`, `Country mapping` | Adds a charge per tonne on imports into a covered country, based on the embedded emissions of the product | Added to the importer's material cost |

All three apply only between different countries. Flows within one country are never taxed, capped or charged.

## Tariffs and Quotas

### The `Tariffs` sheet

Each row of the `Tariffs` master input sheet sets a fixed tax per tonne, a percentage tax, a yearly quota, or a combination, on a route between an exporting and an importing side, for one commodity and a range of years. The [master input reference](../../user_guide/master_input_reference.md#tariffs) lists the columns, their allowed values and what data preparation checks.

Each year, `Environment.get_active_trade_tariffs()` passes only the rows active in that year to the LP.

### Country and bloc entries

Data preparation expands each `From ISO3` and `To ISO3` entry into a list of countries with `_resolve_iso3_or_bloc_entry()`, then creates one tariff for every exporter and importer pair. Pairs where both sides are the same country are dropped.

| Entry | Resolves to |
|-------|-------------|
| `<ISO3>` (e.g. `CHN`) | The single country |
| `<bloc>` (e.g. `EU`) | All countries flagged for that bloc on the `Country mapping` sheet |
| `NOT <bloc>` (e.g. `NOT EU`) | All countries *not* flagged for that bloc |
| `NOT <ISO3>` (e.g. `NOT DEU`) | All known countries except that one |
| `*` | Kept as a literal wildcard and matched in the LP |

Trade blocs are the True/False columns on the `Country mapping` sheet, for example `EU`, `EFTA/EUCU`, `OECD`, `NAFTA`, `Mercosur`, `ASEAN` and `RCEP`. To add a bloc, add a True/False column to that sheet. No code change is needed. Bloc names are normalised for lookup, so `EFTA/EUCU` is stored as `EFTA_EUCU`. For `NOT <X>`, the resolver first looks up `<X>` as a bloc and then as a country code. An entry that is neither raises a `ValueError`.

### How the LP applies tariffs and quotas

`enforce_trade_tariffs_on_allocations()` turns the active tariffs into two lookups keyed by `(exporter, importer, commodity)`:

- **Taxes.** A fixed tax is used as is. A percentage tax is converted to USD per tonne using the average commodity price in the exporting country (`Environment.average_commodity_price_per_region`). Iron products (hot metal, pig iron, DRI, HBI, liquid and electrolytic iron) use the `iron` price. If no average price exists for the exporting country, the percentage tax is skipped with a warning.
- **Quotas.** Stored as tonnes per year.

The LP then checks four keys for each arc: the exact route, `(*, importer, commodity)`, `(exporter, *, commodity)` and `(exporter, importer, *)`. The taxes of every matching key are summed and added to the arc's allocation cost. Each matching quota becomes a constraint: the sum of all allocations under that key must stay at or below the quota.

When several rows produce the same key:

- **Quotas:** the lowest quota applies.
- **Fixed taxes:** they are added together.
- **Percentage taxes:** a percentage tax replaces any tax already set for that key instead of adding to it. A fixed tax on a later row is still added on top. To combine both on one route, put the percentage tax on the earlier row.

### Limitations

- **Quotas on a bloc are per country pair.** Bloc entries are expanded into country pairs before the LP sees them, so a quota of 1 Mt on `CHN` to `EU` caps each EU member separately at 1 Mt. It does not create one shared EU-wide quota.
- **Tariffs ignore the destination price.** Percentage taxes are always based on the price in the exporting country.

## Carbon Border Adjustments

A carbon border adjustment charges imports into a covered country for the carbon embedded in the product, at the destination's carbon price, minus carbon already paid where the product was made.

### The `CBAM` sheet

The `CBAM` sheet has one column per mechanism. The column header names a trade bloc column on the `Country mapping` sheet, which defines the member countries. Its rows switch the mechanism on, set the first and last year, and choose between national carbon prices and a common bloc price. The [master input reference](../../user_guide/master_input_reference.md#cbam) lists the rows and what is checked.

### The charge

`adapt_allocation_costs_for_carbon_border_mechanisms()` runs while the LP is built, before the objective is set. For every legal arc from a production centre into a covered country, the allocation cost rises by:

```
max(0, E × P_d − C)
```

- **`E`**: the direct emission intensity of the product in tCO2 per tonne, covering the exporter's own stage plus its upstream suppliers.
- **`P_d`**: the destination's carbon price in USD per tCO2, from `resolve_destination_carbon_prices()`.
- **`C`**: the carbon cost already paid at the exporter and upstream of it, in USD per tonne.

The own-stage parts of `E` and `C` use this year's emission factors and carbon price. The upstream parts come from last year's solved trade graph, so a change of suppliers reaches the charge one year later. Active furnace groups that did not run last year take the average of observed furnace groups of the same technology (`Environment.fill_missing_upstream_embedded_carbon()`). In the first simulated year nothing has been observed yet, so `E` and `C` cover the exporter's own stage only. See [Embedded emissions and carbon paid](../plant_agent_model/trade_model_connector.md#embedded-emissions-and-carbon-paid) for how the upstream values are built.

### Rules

- **No charge inside a mechanism.** Two countries in the same mechanism never charge each other, whatever other mechanisms cover either of them.
- **Mechanisms do not stack in order.** Every arc evaluates every active mechanism and sums the result, so the order of the mechanisms has no effect.
- **Suppliers are exempt.** Arcs from suppliers such as mines and scrap are never charged, because their production cost is a raw material price, not a carbon cost.
- **Destination price.** A country uses its national carbon price series, or its bloc's series when the mechanism has the common carbon cost flag. A country under several mechanisms takes the highest. Plant operating costs always use the national series.
- **Destinations without a price.** A covered country with no carbon price series is not charged, and the model logs a warning for it each year.

### Export rebates

Export rebates are off by default. Turn them on with `SimulationConfig.carbon_border_export_rebates = True` or the `--cbam-rebate` command line flag. Arcs leaving a covered country then also receive `min(0, E × P_d − C)`, so every cross-border arc carries exactly `E × P_d`.

## How the Costs Reach Plants

The LP records the tariff taxes on `Allocations.tariff_taxes` and the per-arc carbon border charges on `Allocations.carbon_border_charges`. The [TM-PAM connector](../plant_agent_model/trade_model_connector.md) adds both to each shipment's `material_tariff_transportation_cost`, alongside transport, so they flow into the importing furnace group's material cost and from there into prices, NPVs and plant decisions. The connector uses the same four-key wildcard lookup as the LP; see [Tariff propagation](../plant_agent_model/trade_model_connector.md#tariff-propagation) and [Carbon border charge propagation](../plant_agent_model/trade_model_connector.md#carbon-border-charge-propagation).

`log_carbon_border_outcomes()` logs a yearly summary of the border charges the solved flows actually carry.

## Code Reference

| Function | File | Role |
|---|---|---|
| `read_tariffs()`, `_resolve_iso3_or_bloc_entry()` | `adapters/dataprocessing/excel_reader.py` | Read the `Tariffs` sheet and expand blocs |
| `read_carbon_border_mechanisms()` | `adapters/dataprocessing/excel_reader.py` | Read the `CBAM` sheet |
| `log_carbon_border_outcomes()` | `domain/trade_modelling/set_up_steel_trade_lp.py` | Log the yearly border charge summary |
| `Environment.get_active_trade_tariffs()` | `domain/models.py` | Filter tariffs to the current year |
| `enforce_trade_tariffs_on_allocations()` | `domain/trade_modelling/set_up_steel_trade_lp.py` | Build the tax and quota lookups |
| `add_tariff_quotas_and_tax_as_parameters()`, `add_trade_quota_constraints_to_lp()` | `domain/trade_modelling/trade_lp_modelling.py` | Apply taxes to arc costs and add quota constraints |
| `resolve_destination_carbon_prices()`, `adapt_allocation_costs_for_carbon_border_mechanisms()` | `domain/trade_modelling/set_up_steel_trade_lp.py` | Price and apply the carbon border charge |
| `get_tariff_cost()` | `domain/trade_modelling/TM_PAM_connector.py` | Pass tariffs on to importers |
