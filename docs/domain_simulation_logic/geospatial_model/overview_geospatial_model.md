# Geospatial Model Overview

The Geospatial Model determines where new iron and steel plants should be built globally by combining renewable energy optimization, location-based cost analysis, and multi-year business opportunity tracking.

## Process Flow

The geospatial decision-making process follows three sequential stages:

### 1. Renewable Energy Cost Calculation
**[Baseload Optimization Atlas](baseload_optimization_atlas.md)**

Calculates optimal renewable energy costs (LCOE) at high spatial resolution (0.25 degrees) worldwide. This standalone module simulates renewable energy supply from weather data and optimizes the mix of solar, wind, and battery storage to minimize electricity costs. Due to computational intensity, this runs separately and provides precomputed energy price data to the main model.

**Output:** Pixel-level electricity costs for custom renewable energy installations

### 2. Priority Location Identification
**[Priority Location Selection](priority_location_selection.md)**

Identifies the most economically attractive locations globally for new plant construction by combining multiple geospatial cost factors:
- Energy costs (from Baseload Optimization Atlas)
- Local CAPEX of the steel or iron-making equipment
- Transportation costs (iron ore, feedstock, finished products)
- Infrastructure costs (railway buildout)
- Land suitability (terrain, slope, latitude)

The system ranks all global locations by total lifetime cost (CAPEX + OPEX) and extracts the top X% as candidate sites, with additional country-level diversity to ensure all regions have building opportunities. The cost of hydrogen (including inter-/ and intraregional trade) is also calculated globally, at pixel level based on the energy costs.

**Output:** Ranked list of candidate locations for new plant construction, including their site-specific energy, hydrogen, and infrastructure costs.

### 3. Business Opportunity Evaluation and New Plant Opening
**[New Plant Opening](new_plant_opening.md)**

Transforms candidate locations into actual operating plants through a multi-year lifecycle. For each promising location-technology combination, the system:
- Calculates Net Present Value (NPV) with location-specific costs
- Tracks economic viability over multiple years (CONSIDERED status)
- Announces viable projects subject to NPV and probability filters (ANNOUNCED status)
- Initiates construction subject to capacity limits and probability filters (CONSTRUCTION status)

Projects can be discarded at any stage due to negative economics, technology bans, or capacity constraints. The plant agent model brings plants online (OPERATING status) which have been under construction long enough.

**Output:** New plants added to the simulation with specific locations, technologies, costs, and capacities

## Module Relationships

```
Baseload Optimization Atlas
         |
         | (provides energy costs)
         v
Priority Location Selection
         |
         | (provides candidate locations)
         v
New Plant Opening
         |
         | (creates new plants)
         v
Plant Agent Model
```

The geospatial model integrates with the broader simulation:
- **Input from Trade Model:** Market prices inform NPV calculations
- **Input from Plant Agent Model:** Existing plant locations affect transportation cost calculations
- **Output to Plant Agent Model:** New plants are added to the global plant repository
