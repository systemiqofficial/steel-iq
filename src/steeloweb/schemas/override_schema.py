"""
Pydantic schemas for validating scenario parameter overrides.

These schemas validate user-provided overrides for simulation parameters,
ensuring values are within acceptable ranges before being applied to
SimulationConfig instances.
"""

from typing import Optional, Dict, Any
from pydantic import BaseModel, Field, field_validator, model_validator


class TechnologyOverride(BaseModel):
    """
    Override for a single technology's availability settings.

    Controls when a technology is available in the simulation and
    whether it's allowed at all.
    """
    allowed: Optional[bool] = None
    from_year: Optional[int] = Field(None, ge=2020, le=2100)
    to_year: Optional[int] = Field(None, ge=2020, le=2100)

    @field_validator('to_year')
    @classmethod
    def to_year_after_from_year(cls, v: Optional[int], info) -> Optional[int]:
        """Ensure to_year is not before from_year."""
        if v is not None and 'from_year' in info.data and info.data['from_year'] is not None:
            if v < info.data['from_year']:
                raise ValueError('to_year must be >= from_year')
        return v


class TechnologyOverrides(BaseModel):
    """
    All technology overrides - dict keyed by technology code.

    Example:
        {
            "BF": {"allowed": False},
            "EAF": {"allowed": True, "from_year": 2025, "to_year": 2050}
        }
    """
    overrides: Dict[str, TechnologyOverride] = Field(default_factory=dict)

    def get(self, tech_code: str) -> Optional[TechnologyOverride]:
        """Get override for a specific technology code."""
        return self.overrides.get(tech_code)

    def set(self, tech_code: str, override: TechnologyOverride) -> None:
        """Set override for a specific technology code."""
        self.overrides[tech_code] = override


class EconomicOverrides(BaseModel):
    """
    Economic parameter overrides.

    Controls financial parameters, capacity limits, and pricing buffers
    that affect plant economics and investment decisions.
    """
    # Economic multipliers - not in SimulationConfig but useful for scenarios
    capex_multiplier: Optional[float] = Field(None, gt=0, le=10)
    opex_multiplier: Optional[float] = Field(None, gt=0, le=10)

    # Core economic parameters from SimulationConfig
    plant_lifetime: Optional[int] = Field(None, ge=1, le=100)
    global_risk_free_rate: Optional[float] = Field(None, ge=0, le=1)

    # Price buffers
    steel_price_buffer: Optional[float] = Field(None, ge=0)
    iron_price_buffer: Optional[float] = Field(None, ge=0)

    # Capacity parameters
    expanded_capacity: Optional[float] = Field(None, gt=0)
    capacity_limit_iron: Optional[float] = Field(None, gt=0)
    capacity_limit_steel: Optional[float] = Field(None, gt=0)


class GeospatialOverrides(BaseModel):
    """
    Geospatial parameter overrides.

    Controls location feasibility constraints, cost inclusions,
    and hydrogen trade parameters for new plant site selection.
    """
    # Feasibility constraints
    max_altitude: Optional[float] = Field(None, ge=0)
    max_slope: Optional[float] = Field(None, ge=0)
    max_latitude: Optional[float] = Field(None, ge=-90, le=90)

    # Cost inclusions
    include_infrastructure_cost: Optional[bool] = None
    include_transport_cost: Optional[bool] = None
    include_lulc_cost: Optional[bool] = None

    # Hydrogen parameters
    hydrogen_ceiling_percentile: Optional[float] = Field(None, ge=0, le=100)


class PolicyOverrides(BaseModel):
    """
    Policy parameter overrides.

    Controls trade policies, emissions scenarios, and regulatory
    constraints that affect plant operations and economics.
    """
    # Trade and market policies
    include_tariffs: Optional[bool] = None
    use_iron_ore_premiums: Optional[bool] = None

    # Emissions and green steel
    green_steel_emissions_limit: Optional[float] = Field(None, ge=0)

    # Scenario selections
    chosen_demand_scenario: Optional[str] = None
    chosen_grid_emissions_scenario: Optional[str] = None

    @field_validator('chosen_demand_scenario')
    @classmethod
    def validate_demand_scenario(cls, v: Optional[str]) -> Optional[str]:
        """Validate demand scenario is one of the expected values."""
        if v is not None:
            valid_scenarios = ["BAU", "Conservative", "Aggressive", "IEA_NZE", "IEA_STEPS"]
            if v not in valid_scenarios:
                raise ValueError(f'chosen_demand_scenario must be one of {valid_scenarios}')
        return v

    @field_validator('chosen_grid_emissions_scenario')
    @classmethod
    def validate_grid_scenario(cls, v: Optional[str]) -> Optional[str]:
        """Validate grid emissions scenario is one of the expected values."""
        if v is not None:
            valid_scenarios = ["Business As Usual", "Conservative", "Aggressive"]
            if v not in valid_scenarios:
                raise ValueError(f'chosen_grid_emissions_scenario must be one of {valid_scenarios}')
        return v


class AgentOverrides(BaseModel):
    """
    Agent behavior parameter overrides.

    Controls how plant agents make decisions about announcements,
    construction, and timing of investments.
    """
    # Behavioral mode
    probabilistic_agents: Optional[bool] = None

    # Probabilities
    probability_of_announcement: Optional[float] = Field(None, ge=0, le=1)
    probability_of_construction: Optional[float] = Field(None, ge=0, le=1)

    # Timing parameters
    consideration_time: Optional[int] = Field(None, ge=1, le=20)
    construction_time: Optional[int] = Field(None, ge=1, le=20)


class ScenarioOverrides(BaseModel):
    """
    Complete set of parameter overrides for a scenario.

    Bundles all override categories together for easy validation
    and application to SimulationConfig.
    """
    technology: TechnologyOverrides = Field(default_factory=TechnologyOverrides)
    economic: EconomicOverrides = Field(default_factory=EconomicOverrides)
    geospatial: GeospatialOverrides = Field(default_factory=GeospatialOverrides)
    policy: PolicyOverrides = Field(default_factory=PolicyOverrides)
    agent: AgentOverrides = Field(default_factory=AgentOverrides)


def validate_scenario_overrides(scenario: Any) -> list[str]:
    """
    Validate all overrides in a scenario object.

    Args:
        scenario: Object with override attributes (technology_overrides,
                 economic_overrides, etc.)

    Returns:
        List of error messages. Empty list if all validations pass.

    Example:
        errors = validate_scenario_overrides(scenario)
        if errors:
            raise ValueError(f"Validation failed: {', '.join(errors)}")
    """
    errors = []

    # Validate technology overrides
    try:
        if hasattr(scenario, 'technology_overrides') and scenario.technology_overrides:
            TechnologyOverrides.model_validate(scenario.technology_overrides)
    except Exception as e:
        errors.append(f"Technology overrides: {e}")

    # Validate economic overrides
    try:
        if hasattr(scenario, 'economic_overrides') and scenario.economic_overrides:
            EconomicOverrides.model_validate(scenario.economic_overrides)
    except Exception as e:
        errors.append(f"Economic overrides: {e}")

    # Validate geospatial overrides
    try:
        if hasattr(scenario, 'geospatial_overrides') and scenario.geospatial_overrides:
            GeospatialOverrides.model_validate(scenario.geospatial_overrides)
    except Exception as e:
        errors.append(f"Geospatial overrides: {e}")

    # Validate policy overrides
    try:
        if hasattr(scenario, 'policy_overrides') and scenario.policy_overrides:
            PolicyOverrides.model_validate(scenario.policy_overrides)
    except Exception as e:
        errors.append(f"Policy overrides: {e}")

    # Validate agent overrides
    try:
        if hasattr(scenario, 'agent_overrides') and scenario.agent_overrides:
            AgentOverrides.model_validate(scenario.agent_overrides)
    except Exception as e:
        errors.append(f"Agent overrides: {e}")

    return errors
