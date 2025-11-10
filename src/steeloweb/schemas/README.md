# Steeloweb Schemas

Pydantic schemas for validating scenario parameter overrides in the steel model web interface.

## Overview

This package provides validation schemas for scenario configuration overrides. These schemas ensure
that user-provided parameters are within acceptable ranges before being applied to simulation
configurations.

## Design Philosophy

Following DHH's coding style:

- **Simple**: Each schema is focused on a single concern (technology, economic, geospatial, etc.)
- **Practical**: All fields are optional since these are overrides, not requirements
- **Clear validation**: Uses Pydantic's built-in validators with clear error messages
- **Type-safe**: Full type hints for better IDE support and type checking

## Schema Categories

### TechnologyOverride

Controls availability and time windows for specific technologies (e.g., BF, EAF, DRI).

```python
from steeloweb.schemas import TechnologyOverride, TechnologyOverrides

# Ban blast furnaces from 2030 onwards
tech_overrides = TechnologyOverrides(
    overrides={
        "BF": TechnologyOverride(allowed=False, from_year=2030),
        "BFBOF": TechnologyOverride(allowed=False, from_year=2030),
    }
)
```

### EconomicOverrides

Controls financial parameters and capacity limits.

```python
from steeloweb.schemas import EconomicOverrides

econ_overrides = EconomicOverrides(
    plant_lifetime=25,
    global_risk_free_rate=0.03,
    steel_price_buffer=250.0,
)
```

### GeospatialOverrides

Controls location feasibility and cost inclusions for new plant sites.

```python
from steeloweb.schemas import GeospatialOverrides

geo_overrides = GeospatialOverrides(
    max_altitude=1200.0,
    max_slope=1.5,
    include_infrastructure_cost=True,
    hydrogen_ceiling_percentile=15.0,
)
```

### PolicyOverrides

Controls trade policies, emissions scenarios, and regulatory constraints.

```python
from steeloweb.schemas import PolicyOverrides

policy_overrides = PolicyOverrides(
    include_tariffs=True,
    green_steel_emissions_limit=0.3,
    chosen_demand_scenario="Aggressive",
)
```

### AgentOverrides

Controls agent decision-making behavior and timing.

```python
from steeloweb.schemas import AgentOverrides

agent_overrides = AgentOverrides(
    probabilistic_agents=True,
    probability_of_announcement=0.6,
    consideration_time=5,
)
```

## Complete Example

```python
from steeloweb.schemas import ScenarioOverrides, validate_scenario_overrides

# Create a complete scenario override
scenario = ScenarioOverrides(
    technology=TechnologyOverrides(
        overrides={
            "BF": TechnologyOverride(allowed=False),
        }
    ),
    economic=EconomicOverrides(
        plant_lifetime=25,
        steel_price_buffer=300.0,
    ),
    geospatial=GeospatialOverrides(
        max_altitude=1500.0,
    ),
    policy=PolicyOverrides(
        green_steel_emissions_limit=0.4,
    ),
    agent=AgentOverrides(
        probability_of_construction=0.8,
    ),
)

# Validate the scenario
errors = validate_scenario_overrides(scenario)
if errors:
    raise ValueError(f"Validation failed: {', '.join(errors)}")
```

## Validation Rules

### Technology Overrides
- Year ranges: 2020-2100
- `to_year` must be >= `from_year`

### Economic Overrides
- `plant_lifetime`: 1-100 years
- `global_risk_free_rate`: 0-1 (decimal format, e.g., 0.02 for 2%)
- Price buffers: >= 0
- Capacity parameters: > 0
- Multipliers: 0-10

### Geospatial Overrides
- `max_altitude`: >= 0 meters
- `max_slope`: >= 0 degrees
- `max_latitude`: -90 to 90 degrees
- `hydrogen_ceiling_percentile`: 0-100

### Policy Overrides
- `green_steel_emissions_limit`: >= 0 tCO2/tsteel
- `chosen_demand_scenario`: One of ["BAU", "Conservative", "Aggressive", "IEA_NZE", "IEA_STEPS"]
- `chosen_grid_emissions_scenario`: One of ["Business As Usual", "Conservative", "Aggressive"]

### Agent Overrides
- Probabilities: 0-1
- Time parameters: 1-20 years

## Testing

Run the test suite:

```bash
# Run all override schema tests
PYTHONPATH=/home/user/steel-iq/src:$PYTHONPATH uv run python -m pytest tests/test_override_schemas.py -v -p no:django

# Run specific test class
PYTHONPATH=/home/user/steel-iq/src:$PYTHONPATH uv run python -m pytest tests/test_override_schemas.py::TestEconomicOverrides -v -p no:django
```

## Files

- `override_schema.py` - Main schema definitions
- `MAPPING.md` - Mapping of SimulationConfig parameters to override schemas
- `README.md` - This file
- `__init__.py` - Package exports

## Future Extensions

Additional parameters can be added to the schemas as needed:

- Solver parameters (lp_epsilon, capacity_limit, etc.)
- Product configurations
- Data source settings
- More geospatial parameters

See `MAPPING.md` for a complete list of SimulationConfig parameters and their current
coverage status.
