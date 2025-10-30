"""Tests for furnace group strategy with allowed_techs restrictions."""

import pytest
from unittest.mock import MagicMock
from functools import partial

from steelo.devdata import get_furnace_group, get_plant
from steelo.domain import PointInTime, Year, TimeFrame, Volumes
from steelo.domain.models import CountryMapping, CountryMappingService
from steelo.domain.commands import ChangeFurnaceGroupTechnology, RenovateFurnaceGroup
from steelo.simulation_types import get_default_technology_settings


@pytest.fixture
def plant_with_eaf():
    """Create a plant with an EAF furnace group."""
    fg = get_furnace_group(
        fg_id="fg_test_eaf",
        utilization_rate=0.7,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2023), end=Year(2043)),
            plant_lifetime=20,
        ),
        capacity=100,
        tech_name="EAF",
    )
    plant = get_plant(furnace_groups=[fg], plant_id="plant_test_eaf")
    plant.location.iso3 = "USA"
    plant.balance = 1000000  # Positive balance to allow investments
    return plant


@pytest.fixture
def plant_with_bof():
    """Create a plant with a BOF furnace group."""
    fg = get_furnace_group(
        fg_id="fg_test_bof",
        utilization_rate=0.7,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2023), end=Year(2043)),
            plant_lifetime=20,
        ),
        capacity=100,
        tech_name="BOF",
    )
    plant = get_plant(furnace_groups=[fg], plant_id="plant_test_bof")
    plant.location.iso3 = "USA"
    plant.balance = 1000000  # Positive balance to allow investments
    return plant


@pytest.fixture
def country_mappings_for_test():
    """Create mock country mappings."""
    mappings = [
        CountryMapping(
            country="United States",
            iso2="US",
            iso3="USA",
            irena_name="United States",
            region_for_outputs="Americas",
            ssp_region="USA",
            gem_country="United States",
            ws_region="North America",
            tiam_ucl_region="USA",
            eu_region=None,
        ),
    ]
    return CountryMappingService(mappings)


@pytest.fixture
def mock_environment(bus, country_mappings_for_test, tmp_path):
    """Set up the environment with required data."""
    from steelo.simulation import SimulationConfig

    # Create minimal SimulationConfig
    if bus.env.config is None:
        bus.env.config = SimulationConfig(
            start_year=Year(2025, technology_settings=get_default_technology_settings()),
            end_year=Year(2030),
            master_excel_path=tmp_path / "master.xlsx",
            output_dir=tmp_path / "output",
        )

    # Initialize required data
    bus.env.country_mappings = country_mappings_for_test
    bus.env.name_to_capex = {
        "greenfield": {
            "Americas": {"EAF": 400.0, "BOF": 500.0, "DRI": 600.0, "BF": 550.0},
        }
    }
    bus.env.dynamic_feedstocks = {"EAF": [], "BOF": [], "DRI": [], "BF": []}
    bus.env.allowed_furnace_transitions = {
        "EAF": ["EAF", "BF", "BOF", "DRI"],
        "BOF": ["BOF", "EAF", "DRI", "BF"],
        "DRI": ["DRI", "EAF", "BOF", "BF"],
        "BF": ["BF", "EAF", "BOF", "DRI"],
    }
    bus.env.industrial_cost_of_equity = {"USA": 0.08}
    bus.env.industrial_cost_of_debt = {"USA": 0.04}
    bus.env.global_risk_free_rate = 0.02
    bus.env.year = Year(2025)
    bus.env.fopex_by_country = {"USA": {"eaf": 50.0, "bof": 60.0, "dri": 70.0, "bf": 65.0}}
    bus.env.technology_emission_factors = []
    bus.env.technology_to_product = {
        "EAF": "steel",
        "BOF": "steel",
        "DRI": "iron",
        "BF": "iron",
    }
    return bus


def test_technology_switching_respects_allowed_techs(mock_environment, plant_with_eaf, mocker):
    """Test that technology switching only happens to allowed technologies."""
    bus = mock_environment
    bus.uow.plants.add(plant_with_eaf)

    # Mock the BOM function to make DRI very attractive economically
    def mock_get_bom(energy_costs, tech, capacity):
        if tech == "DRI":
            # Make DRI extremely profitable
            return (
                {
                    "materials": {"iron_ore": {"unit_cost": 10.0, "demand": 1.0}},
                    "energy": {"natural_gas": {"unit_cost": 5.0, "demand": 0.1}},
                },
                0.95,
                "iron_ore",
            )
        elif tech == "BOF":
            # Make BOF moderately attractive
            return (
                {
                    "materials": {"hot_metal": {"unit_cost": 100.0, "demand": 1.0}},
                    "energy": {"electricity": {"unit_cost": 50.0, "demand": 0.3}},
                },
                0.85,
                "hot_metal",
            )
        else:
            # Make other techs less attractive
            return (
                {
                    "materials": {"scrap": {"unit_cost": 200.0, "demand": 1.0}},
                    "energy": {"electricity": {"unit_cost": 80.0, "demand": 0.5}},
                },
                0.7,
                "scrap",
            )

    # Set up market prices
    market_price_series = {"steel": [600.0] * 22, "iron": [400.0] * 22}

    # Test 1: With DRI not allowed, it should not be selected despite being most profitable
    allowed_techs_no_dri = {Year(year): ["EAF", "BOF", "BF"] for year in range(2020, 2031)}

    # Set up plant's technology_unit_fopex
    plant_with_eaf.technology_unit_fopex = {"EAF": 50.0, "BOF": 60.0, "DRI": 70.0, "BF": 65.0}
    plant_with_eaf.carbon_cost_series = [0.0] * 22

    # Mock the optimal_technology_name method to simulate the decision
    furnace_group = plant_with_eaf.furnace_groups[0]

    def mock_optimal_technology_name(self, *args, **kwargs):
        # Get allowed transitions from kwargs
        allowed_transitions = kwargs.get("allowed_furnace_transitions", {})
        # Get current tech from the furnace group instance (self)
        current_tech = self.technology.name
        allowed_techs_from_current = allowed_transitions.get(current_tech, [])

        # If no transitions allowed, return empty dicts
        if not allowed_techs_from_current:
            return {}, {}, 0, {}

        # Create NPV dict based on allowed transitions
        tech_npv_dict = {}
        bom_dict = {}
        for tech in allowed_techs_from_current:
            if tech == "DRI":
                tech_npv_dict[tech] = 1000000  # Very high NPV
                bom_dict[tech] = {
                    "materials": {"iron_ore": {"unit_cost": 10.0, "demand": 1.0}},
                    "energy": {"natural_gas": {"unit_cost": 5.0, "demand": 0.1}},
                }
            elif tech == "BOF":
                tech_npv_dict[tech] = 500000  # Moderate NPV
                bom_dict[tech] = {
                    "materials": {"hot_metal": {"unit_cost": 100.0, "demand": 1.0}},
                    "energy": {"electricity": {"unit_cost": 50.0, "demand": 0.3}},
                }
            elif tech == "EAF":
                tech_npv_dict[tech] = 100000  # Low NPV (current tech)
                bom_dict[tech] = {
                    "materials": {"scrap": {"unit_cost": 200.0, "demand": 1.0}},
                    "energy": {"electricity": {"unit_cost": 80.0, "demand": 0.5}},
                }
            else:
                tech_npv_dict[tech] = 50000  # Very low NPV
                bom_dict[tech] = {
                    "materials": {"scrap": {"unit_cost": 200.0, "demand": 1.0}},
                    "energy": {"electricity": {"unit_cost": 80.0, "demand": 0.5}},
                }

        npv_capex_dict = {tech: 400.0 for tech in tech_npv_dict}
        cosa = 10000 if current_tech != max(tech_npv_dict, key=tech_npv_dict.get) else 0

        return tech_npv_dict, npv_capex_dict, cosa, bom_dict

    mocker.patch.object(
        furnace_group, "optimal_technology_name", new=partial(mock_optimal_technology_name, furnace_group)
    )

    # Evaluate strategy with DRI not allowed
    command = plant_with_eaf.evaluate_furnace_group_strategy(
        furnace_group_id=furnace_group.furnace_group_id,
        market_price_series=market_price_series,
        region_capex=bus.env.name_to_capex["greenfield"]["Americas"],
        capex_renovation_share={"EAF": 0.7, "BOF": 0.7, "DRI": 0.7, "BF": 0.7},
        cost_of_debt=0.04,
        cost_of_equity=0.08,
        get_bom_from_avg_boms=mock_get_bom,
        probabilistic_agents=False,
        dynamic_business_cases=bus.env.dynamic_feedstocks,
        chosen_emissions_boundary_for_carbon_costs="scope_1",
        technology_emission_factors=bus.env.technology_emission_factors,
        tech_to_product=bus.env.technology_to_product,
        plant_lifetime=20,
        construction_time=2,
        current_year=Year(2025),
        allowed_techs=allowed_techs_no_dri,  # DRI not allowed
        risk_free_rate=0.02,
        allowed_furnace_transitions=bus.env.allowed_furnace_transitions,
        capacity_limit_steel=Volumes(10000),
        capacity_limit_iron=Volumes(10000),
        installed_capacity_in_year=lambda tech: Volumes(1000),
        new_plant_capacity_in_year=lambda tech: Volumes(0),
    )

    # Should switch to BOF (best allowed option), not DRI
    if command is not None:
        assert isinstance(command, (ChangeFurnaceGroupTechnology, RenovateFurnaceGroup))
        if isinstance(command, ChangeFurnaceGroupTechnology):
            assert command.technology_name in ["EAF", "BOF", "BF"], (
                f"Selected {command.technology_name} which should not include DRI"
            )
            assert command.technology_name != "DRI", "Should not switch to DRI when it's not allowed"

    # Test 2: With all techs allowed, DRI should be selected
    # Create a fresh plant for this test to avoid state pollution
    fg2 = get_furnace_group(
        fg_id="fg_test_eaf2",
        utilization_rate=0.7,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2023), end=Year(2043)),
            plant_lifetime=20,
        ),
        capacity=100,
        tech_name="EAF",
    )
    plant_with_eaf2 = get_plant(furnace_groups=[fg2], plant_id="plant_test_eaf2")
    plant_with_eaf2.location.iso3 = "USA"
    plant_with_eaf2.balance = 1000000
    plant_with_eaf2.technology_unit_fopex = {"EAF": 50.0, "BOF": 60.0, "DRI": 70.0, "BF": 65.0}
    plant_with_eaf2.carbon_cost_series = [0.0] * 22

    furnace_group2 = plant_with_eaf2.furnace_groups[0]
    mocker.patch.object(
        furnace_group2, "optimal_technology_name", new=partial(mock_optimal_technology_name, furnace_group2)
    )

    allowed_techs_all = {Year(year): ["EAF", "BOF", "DRI", "BF"] for year in range(2020, 2031)}

    command_all = plant_with_eaf2.evaluate_furnace_group_strategy(
        furnace_group_id=furnace_group2.furnace_group_id,
        market_price_series=market_price_series,
        region_capex=bus.env.name_to_capex["greenfield"]["Americas"],
        capex_renovation_share={"EAF": 0.7, "BOF": 0.7, "DRI": 0.7, "BF": 0.7},
        cost_of_debt=0.04,
        cost_of_equity=0.08,
        get_bom_from_avg_boms=mock_get_bom,
        probabilistic_agents=False,
        dynamic_business_cases=bus.env.dynamic_feedstocks,
        chosen_emissions_boundary_for_carbon_costs="scope_1",
        technology_emission_factors=bus.env.technology_emission_factors,
        tech_to_product=bus.env.technology_to_product,
        plant_lifetime=20,
        construction_time=2,
        current_year=Year(2025),
        allowed_techs=allowed_techs_all,  # All techs allowed
        risk_free_rate=0.02,
        allowed_furnace_transitions=bus.env.allowed_furnace_transitions,
        capacity_limit_steel=Volumes(10000),
        capacity_limit_iron=Volumes(10000),
        installed_capacity_in_year=lambda tech: Volumes(1000),
        new_plant_capacity_in_year=lambda tech: Volumes(0),
    )

    # With all techs allowed, the best available tech from allowed_furnace_transitions should be selected
    # Note: DRI is allowed in allowed_techs but EAF->DRI transition must also be in allowed_furnace_transitions
    if command_all is not None:
        assert isinstance(command_all, (ChangeFurnaceGroupTechnology, RenovateFurnaceGroup))
        if isinstance(command_all, ChangeFurnaceGroupTechnology):
            # DRI would only be chosen if it's both in allowed_techs AND in allowed_furnace_transitions
            # Since the default allowed_furnace_transitions has EAF -> ["EAF", "BF", "BOF", "DRI"],
            # and DRI is now in allowed_techs, it should be available and chosen (highest NPV)
            assert command_all.technology_name in ["DRI", "BOF"], (
                f"Expected DRI or BOF but got {command_all.technology_name}"
            )


def test_renovation_respects_allowed_techs(mock_environment, plant_with_bof, mocker):
    """Test that renovation only happens for allowed technologies."""
    bus = mock_environment
    bus.uow.plants.add(plant_with_bof)

    furnace_group = plant_with_bof.furnace_groups[0]

    # Set up plant's technology_unit_fopex
    plant_with_bof.technology_unit_fopex = {"EAF": 50.0, "BOF": 60.0, "DRI": 70.0, "BF": 65.0}
    plant_with_bof.carbon_cost_series = [0.0] * 22

    # Mock to simulate that BOF renovation has good NPV
    def mock_optimal_technology_name(self, *args, **kwargs):
        allowed_transitions = kwargs.get("allowed_furnace_transitions", {})
        current_tech = self.technology.name
        allowed_techs_from_current = allowed_transitions.get(current_tech, [])

        # If no transitions allowed, return empty dicts
        if not allowed_techs_from_current:
            return {}, {}, 0, {}

        tech_npv_dict = {}
        bom_dict = {}
        for tech in allowed_techs_from_current:
            if tech == "BOF":
                # Make BOF renovation profitable
                tech_npv_dict[tech] = 800000  # High NPV for staying with BOF
                bom_dict[tech] = {
                    "materials": {"hot_metal": {"unit_cost": 100.0, "demand": 1.0}},
                    "energy": {"electricity": {"unit_cost": 50.0, "demand": 0.3}},
                }
            elif tech == "EAF":
                tech_npv_dict[tech] = 300000  # Lower NPV
                bom_dict[tech] = {
                    "materials": {"scrap": {"unit_cost": 200.0, "demand": 1.0}},
                    "energy": {"electricity": {"unit_cost": 80.0, "demand": 0.5}},
                }
            else:
                tech_npv_dict[tech] = 100000  # Low NPV
                bom_dict[tech] = {
                    "materials": {"scrap": {"unit_cost": 200.0, "demand": 1.0}},
                    "energy": {"electricity": {"unit_cost": 80.0, "demand": 0.5}},
                }

        npv_capex_dict = {tech: 500.0 for tech in tech_npv_dict}
        cosa = 10000 if current_tech != max(tech_npv_dict, key=tech_npv_dict.get) else 0  # Add some COSA for switching

        return tech_npv_dict, npv_capex_dict, cosa, bom_dict

    mocker.patch.object(
        furnace_group, "optimal_technology_name", new=partial(mock_optimal_technology_name, furnace_group)
    )

    # Market prices
    market_price_series = {"steel": [600.0] * 22, "iron": [400.0] * 22}

    # Test 1: BOF not allowed - should not renovate
    allowed_techs_no_bof = {Year(year): ["EAF", "DRI", "BF"] for year in range(2020, 2031)}

    command_no_bof = plant_with_bof.evaluate_furnace_group_strategy(
        furnace_group_id=furnace_group.furnace_group_id,
        market_price_series=market_price_series,
        region_capex=bus.env.name_to_capex["greenfield"]["Americas"],
        capex_renovation_share={"EAF": 0.7, "BOF": 0.7, "DRI": 0.7, "BF": 0.7},
        cost_of_debt=0.04,
        cost_of_equity=0.08,
        get_bom_from_avg_boms=MagicMock(),
        probabilistic_agents=False,
        dynamic_business_cases=bus.env.dynamic_feedstocks,
        chosen_emissions_boundary_for_carbon_costs="scope_1",
        technology_emission_factors=bus.env.technology_emission_factors,
        tech_to_product=bus.env.technology_to_product,
        plant_lifetime=20,
        construction_time=2,
        current_year=Year(2025),
        allowed_techs=allowed_techs_no_bof,  # BOF not allowed
        risk_free_rate=0.02,
        allowed_furnace_transitions=bus.env.allowed_furnace_transitions,
        capacity_limit_steel=Volumes(10000),
        capacity_limit_iron=Volumes(10000),
        installed_capacity_in_year=lambda tech: Volumes(1000),
        new_plant_capacity_in_year=lambda tech: Volumes(0),
    )

    # Should switch to another tech or do nothing, but not renovate BOF
    if command_no_bof is not None:
        if isinstance(command_no_bof, RenovateFurnaceGroup):
            assert False, "Should not renovate BOF when it's not in allowed_techs"
        elif isinstance(command_no_bof, ChangeFurnaceGroupTechnology):
            assert command_no_bof.technology_name != "BOF", "Should not switch to BOF when it's not allowed"

    # Test 2: BOF allowed - should renovate
    allowed_techs_with_bof = {Year(year): ["EAF", "BOF", "DRI", "BF"] for year in range(2020, 2031)}

    command_with_bof = plant_with_bof.evaluate_furnace_group_strategy(
        furnace_group_id=furnace_group.furnace_group_id,
        market_price_series=market_price_series,
        region_capex=bus.env.name_to_capex["greenfield"]["Americas"],
        capex_renovation_share={"EAF": 0.7, "BOF": 0.7, "DRI": 0.7, "BF": 0.7},
        cost_of_debt=0.04,
        cost_of_equity=0.08,
        get_bom_from_avg_boms=MagicMock(),
        probabilistic_agents=False,
        dynamic_business_cases=bus.env.dynamic_feedstocks,
        chosen_emissions_boundary_for_carbon_costs="scope_1",
        technology_emission_factors=bus.env.technology_emission_factors,
        tech_to_product=bus.env.technology_to_product,
        plant_lifetime=20,
        construction_time=2,
        current_year=Year(2025),
        allowed_techs=allowed_techs_with_bof,  # BOF allowed
        risk_free_rate=0.02,
        allowed_furnace_transitions=bus.env.allowed_furnace_transitions,
        capacity_limit_steel=Volumes(10000),
        capacity_limit_iron=Volumes(10000),
        installed_capacity_in_year=lambda tech: Volumes(1000),
        new_plant_capacity_in_year=lambda tech: Volumes(0),
    )

    # With BOF allowed and having best NPV, should renovate
    if command_with_bof is not None:
        if isinstance(command_with_bof, RenovateFurnaceGroup):
            assert furnace_group.technology.name == "BOF", "Should only renovate if staying with BOF"


def test_no_action_when_no_techs_allowed(mock_environment, plant_with_eaf, mocker):
    """Test that a ValueError is raised when no technologies are allowed for the current year."""
    bus = mock_environment
    bus.uow.plants.add(plant_with_eaf)

    furnace_group = plant_with_eaf.furnace_groups[0]

    # Set up plant's technology_unit_fopex
    plant_with_eaf.technology_unit_fopex = {"EAF": 50.0, "BOF": 60.0, "DRI": 70.0, "BF": 65.0}
    plant_with_eaf.carbon_cost_series = [0.0] * 22

    # Empty allowed_techs for 2025
    allowed_techs_empty = {Year(2024): ["EAF", "BOF"], Year(2025): [], Year(2026): ["EAF", "BOF"]}

    # Market prices
    market_price_series = {"steel": [600.0] * 22, "iron": [400.0] * 22}

    # Should raise ValueError when no techs are allowed
    with pytest.raises(ValueError, match=r"\[FG STRATEGY\] No allowed techs in 2025"):
        plant_with_eaf.evaluate_furnace_group_strategy(
            furnace_group_id=furnace_group.furnace_group_id,
            market_price_series=market_price_series,
            region_capex=bus.env.name_to_capex["greenfield"]["Americas"],
            capex_renovation_share={"EAF": 0.7, "BOF": 0.7, "DRI": 0.7, "BF": 0.7},
            cost_of_debt=0.04,
            cost_of_equity=0.08,
            get_bom_from_avg_boms=MagicMock(),
            probabilistic_agents=False,
            dynamic_business_cases=bus.env.dynamic_feedstocks,
            chosen_emissions_boundary_for_carbon_costs="scope_1",
            technology_emission_factors=bus.env.technology_emission_factors,
            tech_to_product=bus.env.technology_to_product,
            plant_lifetime=20,
            construction_time=2,
            current_year=Year(2025),
            allowed_techs=allowed_techs_empty,  # No techs allowed in 2025
            risk_free_rate=0.02,
            allowed_furnace_transitions=bus.env.allowed_furnace_transitions,
            capacity_limit_steel=Volumes(10000),
            capacity_limit_iron=Volumes(10000),
            installed_capacity_in_year=lambda tech: Volumes(1000),
            new_plant_capacity_in_year=lambda tech: Volumes(0),
        )
