# SimulationConfig to Override Schema Mapping

This document maps parameters from `SimulationConfig` to the override schema categories.

## Technology Overrides

Maps to: `TechnologyOverrides`, `TechnologyOverride`

**SimulationConfig Parameters:**
- `technology_settings: Optional[TechSettingsMap]` → `TechnologyOverrides.overrides: Dict[str, TechnologyOverride]`

**Schema Fields:**
- `allowed: Optional[bool]` - Whether technology is allowed
- `from_year: Optional[int]` - Year technology becomes available (2020-2100)
- `to_year: Optional[int]` - Year technology is phased out (2020-2100)

---

## Economic Overrides

Maps to: `EconomicOverrides`

**SimulationConfig Parameters Covered:**
- `plant_lifetime: int` (default: 20) → `plant_lifetime: Optional[int]` (1-100)
- `global_risk_free_rate: float` (default: 0.0209) → `global_risk_free_rate: Optional[float]` (0-1)
- `steel_price_buffer: float` (default: 200.0) → `steel_price_buffer: Optional[float]` (≥0)
- `iron_price_buffer: float` (default: 200.0) → `iron_price_buffer: Optional[float]` (≥0)
- `expanded_capacity: float` (default: 2.5 MT) → `expanded_capacity: Optional[float]` (>0)
- `capacity_limit_iron: float` (default: 200 MT) → `capacity_limit_iron: Optional[float]` (>0)
- `capacity_limit_steel: float` (default: 200 MT) → `capacity_limit_steel: Optional[float]` (>0)

**Additional Schema Fields (not in SimulationConfig):**
- `capex_multiplier: Optional[float]` (0-10) - Useful for scenario sensitivity analysis
- `opex_multiplier: Optional[float]` (0-10) - Useful for scenario sensitivity analysis

**Not Covered:**
- `equity_share: float` (default: 0.2) - Could be added if needed
- `new_capacity_share_from_new_plants: float` (default: 0.2) - Could be added if needed

---

## Geospatial Overrides

Maps to: `GeospatialOverrides`

**SimulationConfig Parameters Covered (from GeoConfig):**
- `max_altitude: float` (default: 1500.0) → `max_altitude: Optional[float]` (≥0)
- `max_slope: float` (default: 2.0) → `max_slope: Optional[float]` (≥0)
- `max_latitude: float` (default: 70.0) → `max_latitude: Optional[float]` (-90 to 90)
- `include_infrastructure_cost: bool` (default: True) → `include_infrastructure_cost: Optional[bool]`
- `include_transport_cost: bool` (default: True) → `include_transport_cost: Optional[bool]`
- `include_lulc_cost: bool` (default: True) → `include_lulc_cost: Optional[bool]`
- `hydrogen_ceiling_percentile: float` (default: 20.0) → `hydrogen_ceiling_percentile: Optional[float]` (0-100)

**Not Covered:**
- `included_power_mix: str` - Could be added with enum validation
- `intraregional_trade_allowed: bool` - Could be added if needed
- `long_dist_pipeline_transport_cost: float` - Could be added if needed
- `intraregional_trade_matrix: dict` - Complex structure, likely not needed for simple overrides
- `transportation_cost_per_km_per_ton: dict` - Could be added if needed
- `land_cover_factor: dict` - Complex structure, likely not needed for simple overrides
- `priority_pct: int` - Could be added if needed
- `iron_ore_steel_ratio: float` - Could be added if needed
- `share_iron_vs_steel: dict` - Complex structure, likely not needed for simple overrides
- `random_seed: int` - Could be added if needed

---

## Policy Overrides

Maps to: `PolicyOverrides`

**SimulationConfig Parameters Covered:**
- `include_tariffs: bool` (default: True) → `include_tariffs: Optional[bool]`
- `use_iron_ore_premiums: bool` (default: True) → `use_iron_ore_premiums: Optional[bool]`
- `green_steel_emissions_limit: float` (default: 0.4) → `green_steel_emissions_limit: Optional[float]` (≥0)
- `chosen_demand_scenario: str` (default: "BAU") → `chosen_demand_scenario: Optional[str]`
  - Valid values: "BAU", "Conservative", "Aggressive", "IEA_NZE", "IEA_STEPS"
- `chosen_grid_emissions_scenario: str` (default: "Business As Usual") → `chosen_grid_emissions_scenario: Optional[str]`
  - Valid values: "Business As Usual", "Conservative", "Aggressive"

**Not Covered:**
- `scrap_generation_scenario: str` (default: "business_as_usual") - Could be added if needed
- `chosen_emissions_boundary_for_carbon_costs: str` (default: "responsible_steel") - Could be added if needed

---

## Agent Overrides

Maps to: `AgentOverrides`

**SimulationConfig Parameters Covered:**
- `probabilistic_agents: bool` (default: True) → `probabilistic_agents: Optional[bool]`
- `probability_of_announcement: float` (default: 0.7 or 1) → `probability_of_announcement: Optional[float]` (0-1)
- `probability_of_construction: float` (default: 0.9 or 1) → `probability_of_construction: Optional[float]` (0-1)
- `consideration_time: int` (default: 3) → `consideration_time: Optional[int]` (1-20)
- `construction_time: int` (default: 4) → `construction_time: Optional[int]` (1-20)

**Not Covered:**
- `top_n_loctechs_as_business_op: int` (default: 15) - Could be added if needed

---

## Parameters Not Suitable for Override Schemas

These parameters are either core configuration, paths, or too complex for simple overrides:

### Core Parameters (Required)
- `start_year: Year` - Core configuration
- `end_year: Year` - Core configuration
- `master_excel_path: Path` - Core configuration
- `output_dir: Path` - Core configuration
- `data_dir: Optional[Path]` - Core configuration

### Path Configuration (Optional)
- `plots_dir`, `geo_plots_dir`, `pam_plots_dir`, `tm_output_dir` - Derived from output_dir
- `terrain_nc_path`, `land_cover_tif_path`, `rail_distance_nc_path` - Geo data paths
- `countries_shapefile_dir`, `disputed_areas_shapefile_dir` - Geo data paths
- `landtype_percentage_nc_path`, `baseload_power_sim_dir` - Geo data paths
- `feasibility_mask_path` - Geo data path

### Trade Module Parameters (Have Defaults)
- `lp_epsilon: float` (default: 1e-3) - Solver parameter
- `capacity_limit: float` (default: 0.95) - Trade parameter
- `soft_minimum_capacity_percentage: float` (default: 0.6) - Trade parameter
- `minimum_active_utilisation_rate: float` (default: 0.01) - Trade parameter
- `minimum_margin: float` (default: 0.5) - Trade parameter
- `hot_metal_radius: float` (default: 5.0) - Allocation parameter

### Product Lists (Have Defaults)
- `primary_products: list[str]` - Product configuration
- `closely_allocated_products: list[str]` - Allocation configuration
- `distantly_allocated_products: list[str]` - Allocation configuration

### Status Lists (Have Defaults)
- `active_statuses: list[str]` - Status configuration
- `announced_statuses: list[str]` - Status configuration

### Data Settings (Have Defaults)
- `use_master_excel: bool` (default: False) - Data source flag
- `steel_plant_gem_data_year: int` (default: 2025) - Data configuration
- `production_gem_data_years: list[int]` - Data configuration
- `excel_reader_start_year: int` (default: 2020) - Data configuration
- `excel_reader_end_year: int` (default: 2050) - Data configuration
- `demand_sheet_name: str` - Data configuration

### Other
- `log_level: int` (default: logging.DEBUG) - Logging configuration
- `_repository: Optional[Repository]` - Internal, lazy-loaded
- `_json_repository: Optional[Any]` - Internal, lazy-loaded

---

## Summary

**Total SimulationConfig Parameters:** ~70+
**Parameters Covered by Override Schemas:** ~25
**Coverage:** ~36%

The override schemas focus on the most commonly adjusted parameters for scenario
analysis while keeping the API simple and practical. More parameters can be added
to the schemas as needed based on user requirements.

## Usage Example

```python
from steeloweb.schemas import ScenarioOverrides, PolicyOverrides, AgentOverrides

# Create a BF ban scenario
overrides = ScenarioOverrides(
    technology=TechnologyOverrides(
        overrides={
            "BF": TechnologyOverride(allowed=False),
            "BFBOF": TechnologyOverride(allowed=False),
        }
    ),
    policy=PolicyOverrides(
        green_steel_emissions_limit=0.4,
        chosen_demand_scenario="Aggressive",
    ),
    agent=AgentOverrides(
        probabilistic_agents=True,
        probability_of_announcement=0.6,
    ),
)

# Validate
errors = validate_scenario_overrides(overrides)
if errors:
    print(f"Validation errors: {errors}")
```
