# Sub-national Geography

## Overview

Some inputs can be authored for a province rather than a whole country, so that plants in the same country can face different electricity prices, hydrogen costs, subsidies and grid emissions. Today only China's 31 first-order units (provinces, municipalities and autonomous regions) are populated; every other country is modelled at country level.

This page covers how a location carries its province, which inputs are resolved at province level and which stay national, and the reference files that define the valid provinces.

## Geo Keys

Every `Location` carries an ISO-3 country code (`iso3`) and an optional sub-national unit (`geo_unit`), an ISO 3166-2 code such as `CN-HE`. Together they form the location's **geo key**:

- `CHN:CN-HE` for a plant in Hebei
- `CHN` for a plant with no sub-national unit

Geo-keyed inputs use the same form in the master workbook: a bare country code for a country row, or `iso3:code` for a province row.

## How Plants Get Their Province

- **Starting fleet**: from the master workbook. The `Furnace units` sheet carries `iso3` and `geo_unit_or_province` in separate columns; the older `Iron and steel plants` sheet carries a bare or combined code in `ISO3`, with an optional `geo_unit` column. See the [Master Input Reference](../user_guide/master_input_reference.md). The model no longer derives a plant's country or province from its coordinates.
- **New plants from the geospatial model**: from their coordinates, by a point-in-polygon lookup against the Natural Earth admin-1 layer (`derive_geo_unit_for_site`). The derived unit is kept only if its country matches the site's country and the unit appears in `geo_hierarchy.json`; otherwise the site is placed at country level. Without the admin-1 layer, every new site is placed at country level.

## Which Inputs Are Resolved at Province Level

| Input | Rule |
|---|---|
| Input costs (energy and material prices) | **Province first**, then country, through `Location.resolve()` |
| Hydrogen CAPEX/OPEX component | A province row overrides the country row for that province, so a province gets its own LCOH from its own electricity price and its own hydrogen CAPEX/OPEX |
| Grid emissivity | **Province first**, then country |
| Subsidies (CAPEX, OPEX, cost of debt, energy) | **Additive**: a country row applies to every plant in the country, province-tagged plants included; a province row applies only to plants inside that province and never replaces a country row |
| Carbon price | National only |
| Cost of capital | National only |
| Fixed OPEX | National only |
| Hydrogen price ceiling | Built from country-level LCOH only; a province inherits its country's regional ceiling |

`Location.resolve()` logs at INFO when a province has no value and the country value is used, and at DEBUG when the province value is used.

### Per-commodity fallback in input costs

A province row in `Input costs` may author only some commodities, for example only electricity. Data preparation fills each unauthored commodity from the country row for the same year. A commodity still unpriced after the fallback stops data preparation with an error.

Before this fallback, unauthored commodities on a province row were priced at 0.0. Province-tagged Chinese plants then booked about half their real variable OPEX and picked bio-PCI on zero-price ties.

### Grid emissivity years

A province row in `Power grid emissivity` may author only some years. Preparation fills the missing years from the country's rows for the same scenario and logs a warning. See the [Master Input Reference](../user_guide/master_input_reference.md#power-grid-emissivity).

## Reference Files

### geo-data package and the admin-1 layer

The `geo-data` package (version 2.0.0 and later) adds the Natural Earth admin-1 layer (`ne_10m_admin_1_states_provinces`), the first-order divisions of every country. Data preparation extracts it with the rest of the geo data.

### geo_hierarchy.json

Data preparation generates `geo_hierarchy.json` in the fixtures directory from the admin-1 layer. It holds one row per first-order unit of each declared country (currently China only), with:

| Field | Content |
|---|---|
| `iso3` | Country code |
| `geo_unit` | ISO 3166-2 code, for example `CN-HE` |
| `geo_key` | `iso3:geo_unit` |
| `display_name` | Unit name for outputs |
| `unit_type` | Province, Municipality, Autonomous Region and so on |
| `ne_region`, `ne_name` | Natural Earth region grouping and original name |

Hand-checked overrides in `geo_hierarchy_overrides.py` apply on top of Natural Earth: Taiwan, Hong Kong and Macau are never listed under China, because the model treats them as separate countries or as having no steel industry, and two display names are tidied ("Inner Mongolia", "Tibet").

Every sub-national check uses this file: the geography checks on `Furnace units`, `Iron and steel plants`, `Input costs` and `Subsidies`, the province lookup for new plants, and the province names in interactive outputs. With an older geo-data package that has no admin-1 layer, preparation skips the file, the sub-national checks are skipped with a warning, and new plants are placed at country level.

### geo_options.csv

`geo_options.csv` in the data root lists every province key that can be authored, for pasting into the master workbook. Columns: `Region`, `Country (iso3)`, `Country (name)`, `Subnational (geo_unit)`, `Subnational (display_name)`, `unit_type`, `geo_key`.

Data preparation writes it automatically. To regenerate it, or to write it elsewhere, run [`generate-geo-options`](../user_guide/cli_commands.md#generate-geo-options).

## Related Documentation

- [Master Input Reference](../user_guide/master_input_reference.md): sheet columns and checks
- [Environment](environment.md): where the geo-keyed inputs are loaded
- [Priority Location Selection](geospatial_model/priority_location_selection.md): hydrogen ceilings and intraregional trade
- [New Plant Opening](geospatial_model/new_plant_opening.md): how candidate sites are priced
