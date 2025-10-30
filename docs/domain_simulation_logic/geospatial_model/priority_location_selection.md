# Priority Location Selection for New Plants

## Overview

The priority location selection system identifies the cheapest locations globally to build new iron and steel plants. It combines multiple geospatial layers (including energy and transportation costs, among others) into a single priority ranking metric. The best locations are selected so that they minimize total lifetime costs (CAPEX + OPEX). Some randomness is included to give all countries a choice of building.

### Geospatial layer types
Three types of layers flow into the calculation of the priority KPI:
1. Static Layers (calculated once per simulation)
   - ISO3 country codes
   - Feasibility mask (terrain, slope, latitude)
   - Cost of infrastructure (railway buildout)

2. Independent Dynamic Layers (recalculated per year)
   - Power prices (baseload renewable and/or grid)
   - Hydrogen costs (capped by regional ceiling)

3. Dependent Dynamic Layers (recalculated per year)
   - CAPEX proxy (technology-specific)
   - Transportation costs (to/from demand & feedstock)

The dependent layers are affected by changes in the environment (e.g., operating steel plants and their capacities), which are triggered by the other modules (plant agent model, trade model).

### Selection of top locations
The pipeline to select the best locations for building new steel and iron plants is:
1. Outgoing cashflow proxy: A metric to measure the priority of each location is calculated by combining all layers above. The cheaper, the better.
2. Priority location extraction: The cheapest X% grid cells are selected worldwide.
3. Country-level lottery system: The cheapest Y locations in each country are added to the previous ones to ensure all countries get a choice of building (even if small). How many locations are selected is proportional to the size of the country in question. 

## Geospatial Layer Details

### ISO3 Country Codes
Each grid point is assigned to a country using country boundary shapefiles. This enables country-specific constraints and ensures location data can be mapped to national policies and regulations.

### Feasibility Mask
Not all locations are suitable for building steel plants. The feasibility mask filters out locations based on physical constraints: sea points, high altitude locations, steep slopes, and extreme latitudes. The altitude is calculated from geopotential data, and the final mask combines all terrain constraints.

**Configuration:**
- `max_altitude`: Maximum elevation in meters (default: 1'500m)
- `max_slope`: Maximum terrain slope in degrees (default: 2°)
- `max_latitude`: Maximum absolute latitude in degrees (default: 65°)

### Power Prices
Electricity costs vary by location and depend on the user-configured power mix. Three options are available:
- **Baseload renewable** (85% or 95% coverage): Uses pre-calculated optimal LCOE values from own renewable energy simulations (see the documentation on baseload power optimization for more details). The remaining percentage comes from grid power:
`power_price = baseload_coverage × lcoe_baseload + (1 - baseload_coverage) × grid_price`
- **Grid only**: Uses country-level grid electricity prices from input data
- **Not included**: Sets power price to zero. This is only valid for the selection of best locations. New plant energy costs are set to the grid power price in this case since they cannot be zero.

### Hydrogen Costs
The levelized cost of hydrogen (LCOH) is calculated from the power prices set above based on country-specific electrolyzer efficiency and electricity consumption. To prevent unrealistically high costs in regions with poor renewable resources, a regional ceiling is applied.

**Intraregional Hydrogen Trade:**
Optionally, regions can import hydrogen from connected regions. When enabled, the capped LCOH accounts for the possibility of importing hydrogen via pipeline from neighboring regions at their regional ceiling price plus long-distance transport costs. The hydrogen trade network is the following (to ↔ from):
- **Africa** ↔ Western Europe
- **Canada** ↔ United States of America
- **China** ↔ South Korea, Former Soviet Union, Other Developing Asia
- **Eastern Europe** ↔ Western Europe, Former Soviet Union, Middle East
- **Former Soviet Union** ↔ China, Eastern Europe, Middle East, Other Developing Asia, South Korea, Western Europe
- **India** ↔ Middle East
- **Middle East** ↔ Eastern Europe, Former Soviet Union, India, Other Developing Asia
- **Mexico** ↔ United States of America
- **Other Developing Asia** ↔ China, Former Soviet Union, Middle East
- **South Korea** ↔ China, Former Soviet Union
- **United Kingdom** ↔ Western Europe
- **United States of America** ↔ Canada, Mexico
- **Western Europe** ↔ Eastern Europe, United Kingdom, Africa, Former Soviet Union
- Australia, Central and South America, Japan, Rest of World: No trade connections

**Process:**
1. Calculate base LCOH from power price and efficiency
2. Calculate regional ceiling within each TIAM-UCL region
3. If intraregional trade is disabled: `capped_lcoh = min(lcoh, regional_ceiling)`
4. If intraregional trade is enabled: `capped_lcoh = min(lcoh, regional_ceiling, min_connected_ceiling + pipeline_cost)`
   - Where `min_connected_ceiling` is the lowest ceiling among connected trade regions
   - And `pipeline_cost` is the long-distance hydrogen pipeline transport cost per kg

**Configuration:**
- `hydrogen_ceiling_percentile`: Percentile for regional cap (default: 20th percentile)
- `intraregional_trade_allowed`: Enable/disable hydrogen imports between regions (default: True)
- `long_dist_pipeline_transport_cost`: Pipeline transport cost in USD/kg (default: 1 USD/kg H2)

### CAPEX Proxy
Average capital expenditure estimates for greenfield steel and iron-making technologies are extracted from the simulation's cost curves. This provides a single representative CAPEX value (USD/tonne of capacity) for new plant construction. This simplification is used only to select the best locations - technology-specific CAPEX values are set later on, when considering a business opportunity to build a new plant in more detail. 

### Infrastructure Costs
New plants require railway connections to transport materials. The infrastructure cost layer calculates the expense of building rail links from each potential site to the existing rail network. Costs scale with distance and vary by country.

**Formula:** `rail_cost = distance_to_rail × railway_cost_per_km_in_country`

### Transportation Costs
Plants must ship products to demand centers and receive feedstock materials. This layer calculates per-tonne shipping costs based on haversine distances to the nearest demand (demand centers for steel and steel plants for iron) and feedstock suppliers (iron plants for steel and iron ore mines for iron).

**Formula:** `transport_cost_per_ton = distance × cost_per_km_per_ton`

**Commodity-specific shipping costs:**
- Iron ore/pellets (mine to plant): 0.013 USD/km/t
- Hot metal/pig iron/DRI/HBI (iron to steel plant): 0.015 USD/km/t
- Liquid steel/steel products (steel to demand): 0.019 USD/km/t

**Configuration:**
- `iron_ore_steel_ratio`: Amount of iron ore needed to produce 1 unit of steel (default: 1.6)
- Can be disabled via `include_transport_cost = False`

### Land Type Factor
Different land cover types and uses are more or less expensive to build on. This layer applies CAPEX multipliers between 1-2 based on land use/land cover (LULC) classifications.

**CAPEX multipliers by land cover type:**

| Land Cover Type | Multiplier |
|----------------|------------|
| Cropland | 1.1× |
| Cropland Herbaceous | 1.1× |
| Cropland Tree/Shrub | 2.0× |
| Mosaic Cropland | 1.1× |
| Mosaic Natural Vegetation | 1.2× |
| Tree Cover | 2.0× |
| Mosaic Tree and Shrubland | 1.5× |
| Mosaic Herbaceous | 1.1× |
| Shrubland | 1.2× |
| Grassland | 1.2× |
| Lichens and Mosses | 2.0× |
| Sparse Vegetation | 1.0× |
| Shrub Cover | 1.5× |
| Urban | 1.5× |
| Bare Areas | 1.0× |
| Water | 2.0× |
| Snow and Ice | 2.0× |

**Purpose:** Accounts for site clearing, grading, and environmental remediation costs.
**Configuration:** Can be disabled entirely via `include_lulc_cost = False`

## Steps to Select Priority Locations

The system uses a three-step process to identify candidate locations for new plant construction, combining global cost optimization with country-level representation.
 
### Step 1: Outgoing Cashflow Calculation

The priority metric combines all cost layers into a single "outgoing cashflow" value representing total lifetime costs of operating a plant at each location.

**Formula:**
```
Outgoing Cashflow = CAPEX components + OPEX components

Where:
  CAPEX = capex × capacity × landtype_factor + rail_cost
  OPEX (annual) = power_price × energy_consumption + feedstock_transport + demand_transport

Total = CAPEX + (OPEX × plant_lifetime)
```

**Cost Components:**

| Component | Formula | Enabled By |
|-----------|---------|------------|
| CAPEX | `capex × capex_share × capacity × landtype_factor` | Always (landtype_factor optional) |
| Rail buildout | `rail_cost` | `include_infrastructure_cost` |
| Power consumption | `power_price × energy_consumption × lifetime` | `included_power_mix ≠ "Not included"` |
| Feedstock transport | `feedstock_cost_per_ton × capacity × lifetime [× ore_ratio]` | `include_transport_cost` |
| Demand transport | `demand_cost_per_ton × capacity × lifetime` | `include_transport_cost` |

Iron and steel are calculated separately, each receiving a share of CAPEX and energy consumption based on `share_iron_vs_steel` (CAPEX shares: 70% for iron, 30% for steel; energy consumption per t steel: 3.0 MWh/t for iron, 1.0 MWh/t for steel).

### Step 2: Global Priority Location Extraction

After calculating outgoing cashflow for all feasible locations, the system extracts the top X% cheapest locations globally (where X is defined by `priority_pct`, default 5%) by flattening the 2D cost grid, removing infeasible locations, sorting by cost, and applying a distribution-adaptive selection method:

1. **Continuous Distribution** (≥ 20 unique values): Uses quantile threshold to select all locations below the Xth percentile (most common case)
2. **Low-Variance Distribution** (< 20 unique values): Samples from cost-ranked chunks with random selection within chunks to prevent systematic geographic bias
3. **Uniform Distribution** (1 unique value): Randomly selects X% of locations using `random_seed` to ensure geographic diversity when all costs are equal

### Step 3: Country-Level Lottery System

To ensure all countries have an opportunity to build new plants (even small countries or those with globally high costs), the system adds country-specific top locations to the global selection.

**Process:**
1. For each country (ISO3 code), extract the top X/10% cheapest locations within that country (e.g., if global priority is 5%, select top 0.5% per country)
2. Add these country-specific locations to the global top locations
3. Number of locations per country is proportional to the country's feasible land area
4. Uses the same distribution-adaptive selection method as Step 2

**Example:**
- Global priority: 5% → ~500-1000 locations worldwide
- Country priority: 0.5% → ~5-50 additional locations per country
- Small countries may only contribute 2-3 locations
- Large countries may contribute 50-100 locations

**Benefits:**
- Prevents global selection from excluding entire regions with high energy/transport costs
- Maintains geographic diversity in candidate locations
- Ensures all countries can participate in decarbonization scenarios
- Creates more realistic policy simulations by giving all nations choices

## Visualization Outputs

The pipeline generates the following plots (saved to `geo_plots_dir`):

### Geospatial Layer Plots
- `global_grid_with_iso3.png` - Country boundaries on grid
- `landsea_mask_bin.png` - Binary land-sea mask
- `altitude_bin.png` - Binary altitude feasibility
- `slope_bin.png` - Binary slope feasibility
- `feasibility_mask.png` - Combined binary feasibility mask (land-sea mask, altitude, and slope)
- `power_price_{year}.png` - Electricity costs by location (grid)
- `baseload_lcoe_{year}_p{X}.png` - Renewable LCOE (if baseload used)
- `capped_lcoh_{year}.png` - Hydrogen costs (after regional cap and intraregional trade, if allowed)
- `distance_to_rail.png` - Distance to nearest railway network
- `rail_infrastructure_cost.png` - Railway buildout costs
- `distance_to_demand_{product}_{year}.png` - Distance to nearest demand by product (demand centers for steel, steel plants for iron)
- `transport_cost_to_demand_{product}_{year}.png` - Per-tonne shipping cost to demand
- `distance_to_feedstock_{product}_{year}.png` - Distance to nearest feedstock sources by product (iron plants for steel, iron ore mines for iron)
- `transport_cost_to_feedstock_{product}_{year}.png` - Per-tonne shipping cost from feedstock

### Priority Calculation Plots (Per Year, Per Product)
- `outgoing_cashflow_proxy_{product}_{year}_p{X}.png` - Total lifetime costs
- `priority_heatmap_{product}_{year}_p{X}.png` - Inverted priority map (higher = better)
- `top{X}_priority_locations_{product}_{year}.png` - Selected candidate locations

Where `{X}` indicates the percentage of grid power vs. baseload (e.g., p5 = 95% baseload + 5% grid).

## Code implementation

### Main entry point
The wrapper function `get_candidate_locations_for_opening_new_plants()` in `src/steelo/adapters/geospatial/top_location_finder.py` calculates:
- `top_locations`: A list of candidate locations with coordinates and cost data for both steel and iron
- `energy_prices`: Power prices and hydrogen costs for all locations

**Process Steps:**

1. **Calculate baseload coverage** - Determines the percentage of power from renewable baseload sources based on user configuration
2. **Add ISO3 codes** - Assigns country codes to each grid point (cached for reuse)
3. **Add feasibility mask** - Filters out sea, mountains, steep slopes, and extreme latitudes (cached for reuse)
4. **Add power prices** - Calculates electricity costs from baseload renewable energy and/or grid
5. **Add capped hydrogen price** - Computes hydrogen costs with regional ceiling constraints (and intraregional trade, if allowed)
6. **Add CAPEX proxy** - Estimates capital costs for steel/iron-making technologies
7. **Add infrastructure costs** - Calculates railway buildout costs (cached for reuse)
8. **Add transportation costs** - Computes shipping costs to demand centers and from feedstock sources (optional)
9. **Add land type factor** - Applies CAPEX multipliers based on land cover type (optional)
10. **Calculate priority KPI** - Combines all layers into final priority rankings
11. **Extract energy prices** - Stores power and hydrogen costs for downstream NPV calculations

**Timing & Performance:**
Each step is timed and logged separately. The total pipeline typically takes 30 sec - 5 min depending on:
- Whether cached layers exist (ISO3, feasibility mask, infrastructure costs) - year 1 vs the rest
- Available cores for task parallelization

### Configuration

#### `GeoConfig` Parameters

Key parameters affecting priority location selection:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `priority_pct` | 5 | Percentage of global top locations to extract as priority locations |
| `included_power_mix` | 85% baseload + 15% grid | Power source for new plants |
| `include_transport_cost` | True | Whether to include shipping costs |
| `include_infrastructure_cost` | True | Whether to include railway buildout |
| `include_lulc_cost` | True | Whether to apply land cover-based CAPEX multipliers |
| `max_altitude` | 1500 | Maximum altitude (meters) for feasibility |
| `max_slope` | 2 | Maximum slope (degrees) for feasibility |
| `max_latitude` | 65 | Maximum latitude (degrees) for feasibility |
| `iron_ore_steel_ratio` | 1.6 | Amount of iron ore needed to produce one unit of steel |
| `hydrogen_ceiling_percentile` | 20 | Regional hydrogen cost cap (percentile) |
| `intraregional_trade_allowed` | True | Enable hydrogen imports between regions |
| `long_dist_pipeline_transport_cost` | 1.0 | Pipeline transport cost (USD/kg H2) |

#### `GeoDataPaths` Parameters

Required file paths:

| Path | Description |
|------|-------------|
| `static_layers_dir` | Directory for cached layers (ISO3, feasibility, rail costs) |
| `terrain_nc_path` | NetCDF file with land-sea mask, altitude, slope |
| `lulc_nc_path` | NetCDF file with land cover classifications |
| `shp_countries_path` | Shapefile with country boundaries (for ISO3 codes) |
| `baseload_power_sim_dir` | Directory with pre-calculated renewable LCOE files - see baseload power documentation to modify |
| `geo_plots_dir` | Output directory for plots |

### Output Format

Each selected location is represented as:

```python
{
    'Latitude': 48.75,           # Decimal degrees
    'Longitude': 12.25,          # Decimal degrees
    'iso3': 'DEU',               # ISO3 country code
    'rail_cost': 125000000.0,    # Railway buildout cost (USD)
    'power_price': 0.045,        # Electricity price (USD/kWh)
    'capped_lcoh': 2.8,          # Hydrogen cost (USD/kg)
}
```

A list of locations is returned for each product:

```python
{
    'iron': [
        {'Latitude': 48.75, 'Longitude': 12.25, 'iso3': 'DEU', ...},
        {'Latitude': -23.5, 'Longitude': -46.6, 'iso3': 'BRA', ...},
        # ... 500-1000 locations
    ],
    'steel': [
        {'Latitude': 51.5, 'Longitude': -0.1, 'iso3': 'GBR', ...},
        {'Latitude': 35.7, 'Longitude': 139.7, 'iso3': 'JPN', ...},
        # ... 500-1000 locations
    ]
}
```

### Code References

**Main files:**
- `src/steelo/adapters/geospatial/top_location_finder.py` - Pipeline orchestration
- `src/steelo/adapters/geospatial/priority_kpi.py` - Priority calculation and location extraction
- `src/steelo/adapters/geospatial/geospatial_layers.py` - Individual layer calculations
- `src/steelo/adapters/geospatial/geospatial_calculations.py` - Helper calculations (distances, LCOH, etc.)
- `src/steelo/adapters/geospatial/geospatial_toolbox.py` - Low-level utilities (haversine distance, grid creation)

**Tests:**
- `tests/unit/test_geospatial_layers.py` - Layer function tests
- `tests/unit/test_geospatial_calculations.py` - Calculation helper tests
- `tests/unit/test_priority_kpi.py` - Priority calculation tests
- `tests/unit/test_geospatial_toolbox.py` - Toolbox utility tests

## Limitations and Improvement Opportunities
**1. No explict grid connection:** 

The implicit assumption here is that the new plants will be (mostly, 85-95% of coverage) powered by “isolated islands” of renewable power: their own renewable energy installations. This is, of course, a strong assumption and likely not how it will play out in majority of cases. We introduced this assumption to avoid having to model the state of the expanding power grid which is a topic worthy of its own complex model. 

The baseload renewable power cost calculated by our model falls firmly in the grid power price range experienced today by industrial players, hence we assumed it is a good enough proxy for the new plants as it should not be too optimistic. Remember that we do not work with variable LCOE -which is typically reported in the renewable power literature- but with the LCOE of a system capable of providing near-constant supply of power (baseload) which is a different cost ballpark (considerably more expensive).

Moreover, we do not account for distance from the power grid, under the abovementioned assumption that the new plants are mostly powered by renewable power islands. All existing plants are assumed to be connected to the grid for the remaining 5-15% of coverage. 

**2. Missing geospatial indicators:**

The outgoing cashflow proxy is based on the geospatial indicators we consider the most influential ones. There are several other factors which play a role (e.g., protected areas or port infrastructure). However, the development of those additional layers had to be abandonned due to a lack of open-source data and/or time constraints. Some of these layers are: 
- **Indigenous lands and protected areas:** This is partially represented by the land use and land cover layer (e.g., forests and wetlands being more expensive to build on), but could be more explicit and binary.
- **More detailed infrastructure:** Currently, railway is the only considered infrastructure network. However, ships are a major mean of transport for the steel insdustry. It would be best to add a layer representing the distance to the nearest harbour capable of handling large shipment and the associated cost (e.g., a smaller port likely results in larger transportation costs). 
- **Availability of skilled labour:** Global limited availability of skilled workforce is currently represented via the new yearly capacity limits (see [Introduction of New Technologies](../plant_agent_model/introduction_of_new_technologies.md)). However, a spatially-explict representation of this indicator would be more accurate. 

## Related Documentation

- [New Plant Opening Logic](new_plant_opening.md) - How candidate locations are used to create new plants
- [Baseload Power Optimization](baseload_optimization_atlas.md) - Details on renewable LCOE calculations
- [Plant Agent Model](../plant_agent_model/overview_plant_agent_model.md) - Overall plant lifecycle management
