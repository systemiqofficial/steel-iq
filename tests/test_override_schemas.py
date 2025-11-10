"""
Tests for scenario parameter override schemas.

Tests validation logic for all override types to ensure user input
is properly validated before being applied to simulation configurations.
"""

import pytest
from pydantic import ValidationError

from steeloweb.schemas.override_schema import (
    TechnologyOverride,
    TechnologyOverrides,
    EconomicOverrides,
    GeospatialOverrides,
    PolicyOverrides,
    AgentOverrides,
    ScenarioOverrides,
    validate_scenario_overrides,
)


class TestTechnologyOverride:
    """Test TechnologyOverride validation."""

    def test_technology_override_valid(self):
        """Test creating a valid technology override."""
        override = TechnologyOverride(allowed=True, from_year=2025, to_year=2050)
        assert override.allowed is True
        assert override.from_year == 2025
        assert override.to_year == 2050

    def test_technology_override_all_optional(self):
        """Test that all fields are optional."""
        override = TechnologyOverride()
        assert override.allowed is None
        assert override.from_year is None
        assert override.to_year is None

    def test_technology_override_invalid_year_range(self):
        """Test that to_year must be >= from_year."""
        with pytest.raises(ValidationError) as exc_info:
            TechnologyOverride(from_year=2050, to_year=2025)
        assert "to_year must be >= from_year" in str(exc_info.value)

    def test_technology_override_year_bounds(self):
        """Test year boundary validation."""
        # Valid boundaries
        TechnologyOverride(from_year=2020, to_year=2100)

        # Below minimum
        with pytest.raises(ValidationError):
            TechnologyOverride(from_year=2019)

        # Above maximum
        with pytest.raises(ValidationError):
            TechnologyOverride(to_year=2101)

    def test_technology_override_equal_years(self):
        """Test that from_year and to_year can be equal."""
        override = TechnologyOverride(from_year=2030, to_year=2030)
        assert override.from_year == override.to_year


class TestTechnologyOverrides:
    """Test TechnologyOverrides collection."""

    def test_technology_overrides_empty(self):
        """Test creating an empty overrides collection."""
        overrides = TechnologyOverrides()
        assert overrides.overrides == {}

    def test_technology_overrides_with_data(self):
        """Test creating overrides collection with data."""
        data = {
            "overrides": {
                "BF": {"allowed": False},
                "EAF": {"allowed": True, "from_year": 2025},
            }
        }
        overrides = TechnologyOverrides.model_validate(data)
        assert "BF" in overrides.overrides
        assert "EAF" in overrides.overrides
        assert overrides.overrides["BF"].allowed is False

    def test_technology_overrides_get_set(self):
        """Test get and set methods."""
        overrides = TechnologyOverrides()
        override = TechnologyOverride(allowed=False)
        overrides.set("BF", override)
        assert overrides.get("BF") == override
        assert overrides.get("NONEXISTENT") is None


class TestEconomicOverrides:
    """Test EconomicOverrides validation."""

    def test_economic_overrides_valid(self):
        """Test creating valid economic overrides."""
        overrides = EconomicOverrides(
            plant_lifetime=25,
            global_risk_free_rate=0.025,
            steel_price_buffer=150.0,
            expanded_capacity=2500000.0,
        )
        assert overrides.plant_lifetime == 25
        assert overrides.global_risk_free_rate == 0.025
        assert overrides.steel_price_buffer == 150.0

    def test_economic_overrides_all_optional(self):
        """Test that all fields are optional."""
        overrides = EconomicOverrides()
        assert overrides.plant_lifetime is None
        assert overrides.capex_multiplier is None

    def test_economic_overrides_invalid_negative_values(self):
        """Test that negative values are rejected."""
        # Price buffer can't be negative
        with pytest.raises(ValidationError):
            EconomicOverrides(steel_price_buffer=-10.0)

        # Capacity can't be negative or zero
        with pytest.raises(ValidationError):
            EconomicOverrides(expanded_capacity=0)

        with pytest.raises(ValidationError):
            EconomicOverrides(capacity_limit_iron=-1000)

    def test_economic_overrides_multiplier_bounds(self):
        """Test multiplier bounds validation."""
        # Valid multipliers
        EconomicOverrides(capex_multiplier=0.1, opex_multiplier=2.5)

        # Zero not allowed
        with pytest.raises(ValidationError):
            EconomicOverrides(capex_multiplier=0)

        # Above maximum
        with pytest.raises(ValidationError):
            EconomicOverrides(opex_multiplier=11)

    def test_economic_overrides_risk_free_rate_validation(self):
        """Test that risk-free rate is validated as decimal."""
        # Valid decimal rate
        EconomicOverrides(global_risk_free_rate=0.0209)

        # Rate above 100% is invalid
        with pytest.raises(ValidationError) as exc_info:
            EconomicOverrides(global_risk_free_rate=2.09)
        assert "less than or equal to 1" in str(exc_info.value)

    def test_economic_overrides_plant_lifetime_bounds(self):
        """Test plant lifetime bounds."""
        # Valid lifetimes
        EconomicOverrides(plant_lifetime=1)
        EconomicOverrides(plant_lifetime=100)

        # Below minimum
        with pytest.raises(ValidationError):
            EconomicOverrides(plant_lifetime=0)

        # Above maximum
        with pytest.raises(ValidationError):
            EconomicOverrides(plant_lifetime=101)


class TestGeospatialOverrides:
    """Test GeospatialOverrides validation."""

    def test_geospatial_overrides_valid(self):
        """Test creating valid geospatial overrides."""
        overrides = GeospatialOverrides(
            max_altitude=1200.0,
            max_slope=1.5,
            max_latitude=65.0,
            include_infrastructure_cost=True,
            hydrogen_ceiling_percentile=25.0,
        )
        assert overrides.max_altitude == 1200.0
        assert overrides.max_slope == 1.5
        assert overrides.max_latitude == 65.0

    def test_geospatial_overrides_all_optional(self):
        """Test that all fields are optional."""
        overrides = GeospatialOverrides()
        assert overrides.max_altitude is None
        assert overrides.include_lulc_cost is None

    def test_geospatial_overrides_latitude_bounds(self):
        """Test latitude boundary validation."""
        # Valid latitudes
        GeospatialOverrides(max_latitude=-90)
        GeospatialOverrides(max_latitude=0)
        GeospatialOverrides(max_latitude=90)

        # Below minimum
        with pytest.raises(ValidationError):
            GeospatialOverrides(max_latitude=-91)

        # Above maximum
        with pytest.raises(ValidationError):
            GeospatialOverrides(max_latitude=91)

    def test_geospatial_overrides_negative_altitude_slope(self):
        """Test that negative altitude and slope are rejected."""
        with pytest.raises(ValidationError):
            GeospatialOverrides(max_altitude=-100)

        with pytest.raises(ValidationError):
            GeospatialOverrides(max_slope=-1)

    def test_geospatial_overrides_hydrogen_percentile_bounds(self):
        """Test hydrogen ceiling percentile bounds."""
        # Valid percentiles
        GeospatialOverrides(hydrogen_ceiling_percentile=0)
        GeospatialOverrides(hydrogen_ceiling_percentile=50)
        GeospatialOverrides(hydrogen_ceiling_percentile=100)

        # Below minimum
        with pytest.raises(ValidationError):
            GeospatialOverrides(hydrogen_ceiling_percentile=-1)

        # Above maximum
        with pytest.raises(ValidationError):
            GeospatialOverrides(hydrogen_ceiling_percentile=101)


class TestPolicyOverrides:
    """Test PolicyOverrides validation."""

    def test_policy_overrides_valid(self):
        """Test creating valid policy overrides."""
        overrides = PolicyOverrides(
            include_tariffs=True,
            use_iron_ore_premiums=False,
            green_steel_emissions_limit=0.5,
            chosen_demand_scenario="BAU",
            chosen_grid_emissions_scenario="Business As Usual",
        )
        assert overrides.include_tariffs is True
        assert overrides.green_steel_emissions_limit == 0.5

    def test_policy_overrides_all_optional(self):
        """Test that all fields are optional."""
        overrides = PolicyOverrides()
        assert overrides.include_tariffs is None
        assert overrides.chosen_demand_scenario is None

    def test_policy_overrides_negative_emissions_limit(self):
        """Test that negative emissions limit is rejected."""
        with pytest.raises(ValidationError):
            PolicyOverrides(green_steel_emissions_limit=-0.1)

    def test_policy_overrides_valid_demand_scenarios(self):
        """Test validation of demand scenario choices."""
        # Valid scenarios
        for scenario in ["BAU", "Conservative", "Aggressive", "IEA_NZE", "IEA_STEPS"]:
            overrides = PolicyOverrides(chosen_demand_scenario=scenario)
            assert overrides.chosen_demand_scenario == scenario

        # Invalid scenario
        with pytest.raises(ValidationError) as exc_info:
            PolicyOverrides(chosen_demand_scenario="InvalidScenario")
        assert "must be one of" in str(exc_info.value)

    def test_policy_overrides_valid_grid_scenarios(self):
        """Test validation of grid emissions scenario choices."""
        # Valid scenarios
        for scenario in ["Business As Usual", "Conservative", "Aggressive"]:
            overrides = PolicyOverrides(chosen_grid_emissions_scenario=scenario)
            assert overrides.chosen_grid_emissions_scenario == scenario

        # Invalid scenario
        with pytest.raises(ValidationError) as exc_info:
            PolicyOverrides(chosen_grid_emissions_scenario="InvalidScenario")
        assert "must be one of" in str(exc_info.value)


class TestAgentOverrides:
    """Test AgentOverrides validation."""

    def test_agent_overrides_valid(self):
        """Test creating valid agent overrides."""
        overrides = AgentOverrides(
            probabilistic_agents=True,
            probability_of_announcement=0.7,
            probability_of_construction=0.9,
            consideration_time=3,
            construction_time=4,
        )
        assert overrides.probabilistic_agents is True
        assert overrides.probability_of_announcement == 0.7
        assert overrides.consideration_time == 3

    def test_agent_overrides_all_optional(self):
        """Test that all fields are optional."""
        overrides = AgentOverrides()
        assert overrides.probabilistic_agents is None
        assert overrides.probability_of_announcement is None

    def test_agent_overrides_probability_bounds(self):
        """Test that probabilities are bounded to [0, 1]."""
        # Valid probabilities
        AgentOverrides(probability_of_announcement=0)
        AgentOverrides(probability_of_announcement=0.5)
        AgentOverrides(probability_of_announcement=1)

        # Below minimum
        with pytest.raises(ValidationError):
            AgentOverrides(probability_of_announcement=-0.1)

        # Above maximum
        with pytest.raises(ValidationError):
            AgentOverrides(probability_of_construction=1.1)

    def test_agent_overrides_time_bounds(self):
        """Test that time parameters have valid bounds."""
        # Valid times
        AgentOverrides(consideration_time=1, construction_time=20)

        # Below minimum
        with pytest.raises(ValidationError):
            AgentOverrides(consideration_time=0)

        # Above maximum
        with pytest.raises(ValidationError):
            AgentOverrides(construction_time=21)


class TestScenarioOverrides:
    """Test complete ScenarioOverrides bundle."""

    def test_scenario_overrides_empty(self):
        """Test creating an empty scenario overrides bundle."""
        overrides = ScenarioOverrides()
        assert overrides.technology is not None
        assert overrides.economic is not None
        assert overrides.geospatial is not None
        assert overrides.policy is not None
        assert overrides.agent is not None

    def test_scenario_overrides_with_all_categories(self):
        """Test creating scenario overrides with all categories."""
        data = {
            "technology": {"overrides": {"BF": {"allowed": False}}},
            "economic": {"plant_lifetime": 25},
            "geospatial": {"max_altitude": 1500.0},
            "policy": {"include_tariffs": True},
            "agent": {"probabilistic_agents": False},
        }
        overrides = ScenarioOverrides.model_validate(data)
        assert overrides.economic.plant_lifetime == 25
        assert overrides.policy.include_tariffs is True

    def test_scenario_overrides_nested_validation(self):
        """Test that nested validation errors are caught."""
        # Invalid nested data should raise validation error
        with pytest.raises(ValidationError):
            ScenarioOverrides.model_validate({
                "economic": {"plant_lifetime": -1}  # Invalid: negative lifetime
            })


class TestValidateScenarioOverridesFunction:
    """Test the validate_scenario_overrides helper function."""

    def test_validate_empty_scenario(self):
        """Test validating a scenario with no overrides."""
        class EmptyScenario:
            pass

        errors = validate_scenario_overrides(EmptyScenario())
        assert errors == []

    def test_validate_valid_scenario(self):
        """Test validating a scenario with valid overrides."""
        class ValidScenario:
            technology_overrides = {"overrides": {}}
            economic_overrides = {"plant_lifetime": 25}
            geospatial_overrides = {"max_altitude": 1500.0}
            policy_overrides = {"include_tariffs": True}
            agent_overrides = {"probabilistic_agents": False}

        errors = validate_scenario_overrides(ValidScenario())
        assert errors == []

    def test_validate_invalid_scenario(self):
        """Test validating a scenario with invalid overrides."""
        class InvalidScenario:
            economic_overrides = {"plant_lifetime": -1}  # Invalid

        errors = validate_scenario_overrides(InvalidScenario())
        assert len(errors) > 0
        assert "Economic overrides" in errors[0]

    def test_validate_multiple_errors(self):
        """Test that all validation errors are collected."""
        class MultiErrorScenario:
            economic_overrides = {"plant_lifetime": -1}  # Invalid
            geospatial_overrides = {"max_latitude": 100}  # Invalid

        errors = validate_scenario_overrides(MultiErrorScenario())
        assert len(errors) >= 2
        assert any("Economic" in e for e in errors)
        assert any("Geospatial" in e for e in errors)

    def test_validate_none_overrides(self):
        """Test that None overrides are handled gracefully."""
        class NoneScenario:
            economic_overrides = None
            policy_overrides = None

        errors = validate_scenario_overrides(NoneScenario())
        assert errors == []


class TestRealWorldScenarios:
    """Test realistic scenario override combinations."""

    def test_bf_ban_scenario(self):
        """Test a scenario that bans blast furnace technology."""
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
        )
        assert overrides.technology.overrides["BF"].allowed is False
        assert overrides.policy.green_steel_emissions_limit == 0.4

    def test_high_carbon_price_scenario(self):
        """Test a scenario with aggressive carbon pricing."""
        overrides = ScenarioOverrides(
            policy=PolicyOverrides(
                green_steel_emissions_limit=0.2,
                chosen_grid_emissions_scenario="Aggressive",
            ),
            economic=EconomicOverrides(
                steel_price_buffer=300.0,
                global_risk_free_rate=0.03,
            ),
        )
        assert overrides.policy.green_steel_emissions_limit == 0.2
        assert overrides.economic.steel_price_buffer == 300.0

    def test_conservative_expansion_scenario(self):
        """Test a scenario with conservative capacity expansion."""
        overrides = ScenarioOverrides(
            agent=AgentOverrides(
                probabilistic_agents=True,
                probability_of_announcement=0.5,
                probability_of_construction=0.7,
                consideration_time=5,
            ),
            economic=EconomicOverrides(
                expanded_capacity=1500000.0,
            ),
        )
        assert overrides.agent.probability_of_announcement == 0.5
        assert overrides.economic.expanded_capacity == 1500000.0

    def test_geographic_constraints_scenario(self):
        """Test a scenario with strict geographic constraints."""
        overrides = ScenarioOverrides(
            geospatial=GeospatialOverrides(
                max_altitude=1000.0,
                max_slope=1.0,
                max_latitude=60.0,
                include_infrastructure_cost=True,
                include_transport_cost=True,
                hydrogen_ceiling_percentile=15.0,
            ),
        )
        assert overrides.geospatial.max_altitude == 1000.0
        assert overrides.geospatial.hydrogen_ceiling_percentile == 15.0
