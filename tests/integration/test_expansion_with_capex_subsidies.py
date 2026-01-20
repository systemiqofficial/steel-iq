import pytest


from steelo.simulation_types import get_default_technology_settings

from steelo.devdata import get_furnace_group, get_plant
from steelo.domain import PointInTime, Year, TimeFrame, Volumes, Subsidy
from steelo.domain.models import PlantGroup, CountryMapping, CountryMappingService


@pytest.fixture
def plant_with_location():
    """Create a plant with location for testing subsidies."""
    fg = get_furnace_group(
        fg_id="fg_test",
        utilization_rate=0.7,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2023), end=Year(2043)),
            plant_lifetime=20,
        ),
        capacity=100,
        tech_name="EAF",
    )
    plant = get_plant(furnace_groups=[fg], plant_id="plant_test")
    plant.location.iso3 = "USA"
    return plant


@pytest.fixture
def country_mappings_for_test(bus):
    """Create mock country mappings for bus environment."""
    # Create mapping for USA
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

    bus.env.country_mappings = CountryMappingService(mappings)
    return bus.env.country_mappings


@pytest.fixture
def setup_environment(bus, country_mappings_for_test, tmp_path):
    """Set up the environment with required data for expansion evaluation."""
    from steelo.simulation import SimulationConfig

    # Create minimal SimulationConfig if not already present
    if bus.env.config is None:
        bus.env.config = SimulationConfig(
            start_year=Year(
                2025,
                technology_settings=get_default_technology_settings(),
            ),
            end_year=Year(2030),
            master_excel_path=tmp_path / "master.xlsx",
            output_dir=tmp_path / "output",
        )

    # Initialize capex data with all regions
    bus.env.name_to_capex = {
        "greenfield": {
            "Americas": {"EAF": 400.0, "BOF": 500.0, "DRI": 600.0, "BF": 550.0},
            "Europe": {"EAF": 400.0, "BOF": 500.0, "DRI": 600.0, "BF": 550.0},
            "Asia & Oceania": {"EAF": 400.0, "BOF": 500.0, "DRI": 600.0, "BF": 550.0},
            "Africa": {"EAF": 400.0, "BOF": 500.0, "DRI": 600.0, "BF": 550.0},
            "MENA": {"EAF": 400.0, "BOF": 500.0, "DRI": 600.0, "BF": 550.0},
            "CIS": {"EAF": 400.0, "BOF": 500.0, "DRI": 600.0, "BF": 550.0},
        }
    }

    # Initialize other required data
    bus.env.dynamic_feedstocks = {"EAF": [], "BOF": [], "DRI": [], "BF": []}
    bus.env.allowed_furnace_transitions = {"EAF": ["EAF", "BF", "BOF", "DRI"]}
    bus.env.industrial_cost_of_equity = {"USA": 0.08}
    bus.env.industrial_cost_of_debt = {"USA": 0.04}
    bus.env.global_risk_free_rate = 0.02
    bus.env.year = Year(2025)
    bus.env.fopex_by_country = {"USA": {"eaf": 50.0, "bof": 60.0, "dri": 70.0, "bf": 65.0}}

    # Initialize technology_emission_factors as empty list if not already initialized
    if not hasattr(bus.env, "technology_emission_factors"):
        bus.env.technology_emission_factors = []

    # Initialize technology_to_product mapping
    bus.env.technology_to_product = {
        "EAF": "steel",
        "BOF": "steel",
        "DRI": "iron",
        "BF": "iron",
    }

    return bus


def test_evaluate_expansion_without_subsidies(
    setup_environment, plant_with_location, mocker, country_mappings_for_test
):
    """Test expansion evaluation without any subsidies."""
    bus = setup_environment
    bus.uow.plants.add(plant_with_location)

    pg = PlantGroup(plant_group_id="pg_test", plants=[plant_with_location])
    bus.uow.plant_groups.add(pg)

    # Mock get_bom_from_avg_boms
    def mock_get_bom(energy_costs, tech, capacity, most_common_reductant=None):
        return (
            {
                "materials": {"scrap": {"unit_cost": 100.0, "demand": 1.0}},
                "energy": {"electricity": {"unit_cost": 50.0, "demand": 0.5}},
            },
            0.8,
            "scrap",
        )

    # Mock with side_effect to handle all tech types
    mocker.patch.object(bus.env, "get_bom_from_avg_boms", side_effect=mock_get_bom)

    # Set up pricing - need lists for price_series
    # Create price lists for the expected time horizon (construction_time + lifetime = 2 + 20 = 22 years)
    price = {"steel": [600.0] * 22, "iron": [400.0] * 22}

    # Create allowed_techs dictionary for all years in the simulation
    allowed_techs = {Year(year): ["EAF", "BOF", "DRI", "BF"] for year in range(2020, 2031)}

    # Evaluate expansion without subsidies
    options = pg.evaluate_expansion_options(
        price_series=price,
        capacity=Volumes(1000),
        region_capex=bus.env.name_to_capex["greenfield"],
        cost_of_debt_dict=bus.env.industrial_cost_of_debt,
        cost_of_equity_dict=bus.env.industrial_cost_of_equity,
        get_bom_from_avg_boms=bus.env.get_bom_from_avg_boms,
        dynamic_feedstocks=bus.env.dynamic_feedstocks,
        fopex_for_iso3=bus.env.fopex_by_country,
        iso3_to_region_map=country_mappings_for_test.iso3_to_region(),
        chosen_emissions_boundary_for_carbon_costs=bus.env.config.chosen_emissions_boundary_for_carbon_costs,
        tech_to_product=bus.env.technology_to_product,
        plant_lifetime=bus.env.config.plant_lifetime,
        construction_time=2,
        technology_emission_factors=bus.env.technology_emission_factors,  # Add missing parameter
        global_risk_free_rate=bus.env.global_risk_free_rate,
        equity_share=0.4,  # Add equity_share parameter (40% equity, 60% debt)
        current_year=bus.env.year,
        allowed_techs=allowed_techs,  # Add required allowed_techs parameter
        capex_subsidies={},  # Empty dict instead of None
        opex_subsidies={},  # Empty dict for consistency
        debt_subsidies={},  # Empty dict for consistency
    )

    # Should have one option per plant
    assert len(options) == 1
    plant_id = plant_with_location.plant_id
    assert plant_id in options

    # Get the NPV, best tech, and capex
    npv, best_tech, capex = options[plant_id]
    assert npv is not None
    assert best_tech in ["EAF", "BOF", "DRI", "BF"]
    assert capex == bus.env.name_to_capex["greenfield"]["Americas"][best_tech]


def test_evaluate_expansion_with_absolute_capex_subsidy(
    setup_environment, plant_with_location, mocker, country_mappings_for_test
):
    """Test expansion evaluation with absolute capex subsidy."""
    bus = setup_environment
    bus.uow.plants.add(plant_with_location)

    pg = PlantGroup(plant_group_id="pg_test", plants=[plant_with_location])
    bus.uow.plant_groups.add(pg)

    # Mock get_bom_from_avg_boms
    def mock_get_bom(energy_costs, tech, capacity, most_common_reductant=None):
        return (
            {
                "materials": {"scrap": {"unit_cost": 100.0, "demand": 1.0}},
                "energy": {"electricity": {"unit_cost": 50.0, "demand": 0.5}},
            },
            0.8,
            "scrap",
        )

    mocker.patch.object(bus.env, "get_bom_from_avg_boms", side_effect=mock_get_bom)

    # Create capex subsidies - $100 absolute subsidy for EAF in USA
    eaf_subsidy = Subsidy(
        scenario_name="test_scenario",
        technology_name="EAF",
        iso3="USA",
        cost_item="capex",
        absolute_subsidy=100.0,
        relative_subsidy=0.0,
        start_year=Year(2020),
        end_year=Year(2030),
    )

    capex_subsidies = {"USA": {"EAF": [eaf_subsidy]}}

    # Set up pricing - need lists for price_series
    # Create price lists for the expected time horizon (construction_time + lifetime = 2 + 20 = 22 years)
    price = {"steel": [600.0] * 22, "iron": [400.0] * 22}

    # Create allowed_techs dictionary for all years in the simulation
    allowed_techs = {Year(year): ["EAF", "BOF", "DRI", "BF"] for year in range(2020, 2031)}

    # Evaluate expansion with subsidies
    options = pg.evaluate_expansion_options(
        price_series=price,
        capacity=Volumes(1000),
        region_capex=bus.env.name_to_capex["greenfield"],
        cost_of_debt_dict=bus.env.industrial_cost_of_debt,
        cost_of_equity_dict=bus.env.industrial_cost_of_equity,
        get_bom_from_avg_boms=bus.env.get_bom_from_avg_boms,
        dynamic_feedstocks=bus.env.dynamic_feedstocks,
        fopex_for_iso3=bus.env.fopex_by_country,
        iso3_to_region_map=country_mappings_for_test.iso3_to_region(),
        chosen_emissions_boundary_for_carbon_costs=bus.env.config.chosen_emissions_boundary_for_carbon_costs,
        tech_to_product=bus.env.technology_to_product,
        plant_lifetime=bus.env.config.plant_lifetime,
        construction_time=2,
        technology_emission_factors=bus.env.technology_emission_factors,  # Add missing parameter
        global_risk_free_rate=bus.env.global_risk_free_rate,
        equity_share=0.4,  # Add equity_share parameter (40% equity, 60% debt)
        current_year=bus.env.year,
        allowed_techs=allowed_techs,  # Add required allowed_techs parameter
        capex_subsidies=capex_subsidies,
    )

    # Get the results
    plant_id = plant_with_location.plant_id
    # Debug: Check what options contains
    assert options, f"No expansion options returned. Plant id: {plant_id}"
    assert plant_id in options, f"Plant {plant_id} not in options. Available keys: {list(options.keys())}"
    npv, best_tech, capex_with_subsidy = options[plant_id]

    # If EAF is chosen, verify subsidy was applied
    if best_tech == "EAF":
        original_capex = bus.env.name_to_capex["greenfield"]["Americas"]["EAF"]
        assert capex_with_subsidy == original_capex - 100.0
    else:
        # For other technologies, capex should be unchanged
        assert capex_with_subsidy == bus.env.name_to_capex["greenfield"]["Americas"][best_tech]


def test_evaluate_expansion_with_relative_capex_subsidy(
    setup_environment, plant_with_location, mocker, country_mappings_for_test
):
    """Test expansion evaluation with relative capex subsidy."""
    bus = setup_environment
    bus.uow.plants.add(plant_with_location)

    pg = PlantGroup(plant_group_id="pg_test", plants=[plant_with_location])
    bus.uow.plant_groups.add(pg)

    # Mock get_bom_from_avg_boms
    def mock_get_bom(energy_costs, tech, capacity, most_common_reductant=None):
        return (
            {
                "materials": {"scrap": {"unit_cost": 100.0, "demand": 1.0}},
                "energy": {"electricity": {"unit_cost": 50.0, "demand": 0.5}},
            },
            0.8,
            "scrap",
        )

    mocker.patch.object(bus.env, "get_bom_from_avg_boms", side_effect=mock_get_bom)

    # Create capex subsidies - 20% relative subsidy for DRI in USA
    dri_subsidy = Subsidy(
        scenario_name="test_scenario",
        technology_name="DRI",
        iso3="USA",
        cost_item="capex",
        absolute_subsidy=0.0,
        relative_subsidy=0.2,  # 20% subsidy
        start_year=Year(2020),
        end_year=Year(2030),
    )

    capex_subsidies = {"USA": {"DRI": [dri_subsidy]}}

    # Set up pricing - need lists for price_series
    # Create price lists for the expected time horizon (construction_time + lifetime = 2 + 20 = 22 years)
    price = {"steel": [600.0] * 22, "iron": [400.0] * 22}

    # Create allowed_techs dictionary for all years in the simulation
    allowed_techs = {Year(year): ["EAF", "BOF", "DRI", "BF"] for year in range(2020, 2031)}

    # Evaluate expansion with subsidies
    options = pg.evaluate_expansion_options(
        price_series=price,
        capacity=Volumes(1000),
        region_capex=bus.env.name_to_capex["greenfield"],
        cost_of_debt_dict=bus.env.industrial_cost_of_debt,
        cost_of_equity_dict=bus.env.industrial_cost_of_equity,
        get_bom_from_avg_boms=bus.env.get_bom_from_avg_boms,
        dynamic_feedstocks=bus.env.dynamic_feedstocks,
        fopex_for_iso3=bus.env.fopex_by_country,
        iso3_to_region_map=country_mappings_for_test.iso3_to_region(),
        chosen_emissions_boundary_for_carbon_costs=bus.env.config.chosen_emissions_boundary_for_carbon_costs,
        tech_to_product=bus.env.technology_to_product,
        plant_lifetime=bus.env.config.plant_lifetime,
        construction_time=2,
        technology_emission_factors=bus.env.technology_emission_factors,  # Add missing parameter
        global_risk_free_rate=bus.env.global_risk_free_rate,
        equity_share=0.4,  # Add equity_share parameter (40% equity, 60% debt)
        current_year=bus.env.year,
        allowed_techs=allowed_techs,  # Add required allowed_techs parameter
        capex_subsidies=capex_subsidies,
    )

    # Get the results
    plant_id = plant_with_location.plant_id
    npv, best_tech, capex_with_subsidy = options[plant_id]

    # If DRI is chosen, verify subsidy was applied
    if best_tech == "DRI":
        original_capex = bus.env.name_to_capex["greenfield"]["Americas"]["DRI"]
        assert capex_with_subsidy == original_capex * 0.8  # 20% reduction
    else:
        # For other technologies, capex should be unchanged
        assert capex_with_subsidy == bus.env.name_to_capex["greenfield"]["Americas"][best_tech]


def test_evaluate_expansion_with_combined_subsidies(
    setup_environment, plant_with_location, mocker, country_mappings_for_test
):
    """Test expansion evaluation with both absolute and relative capex subsidies."""
    bus = setup_environment
    bus.uow.plants.add(plant_with_location)

    pg = PlantGroup(plant_group_id="pg_test", plants=[plant_with_location])
    bus.uow.plant_groups.add(pg)

    # Mock get_bom_from_avg_boms with higher NPV for BOF to ensure it's selected
    def mock_get_bom(energy_costs, tech, capacity, most_common_reductant=None):
        if tech == "BOF":
            # Make BOF more attractive
            return (
                {
                    "materials": {"hot metal": {"unit_cost": 50.0, "demand": 1.0}},
                    "energy": {"electricity": {"unit_cost": 30.0, "demand": 0.3}},
                },
                0.9,  # Higher utilization
                "hot metal",
            )
        else:
            return (
                {
                    "materials": {"scrap": {"unit_cost": 100.0, "demand": 1.0}},
                    "energy": {"electricity": {"unit_cost": 50.0, "demand": 0.5}},
                },
                0.8,
                "scrap",
            )

    mocker.patch.object(bus.env, "get_bom_from_avg_boms", side_effect=mock_get_bom)

    # Create multiple subsidies for BOF
    bof_subsidy1 = Subsidy(
        scenario_name="test_scenario_1",
        technology_name="BOF",
        iso3="USA",
        cost_item="capex",
        absolute_subsidy=50.0,
        relative_subsidy=0.0,
        start_year=Year(2020),
        end_year=Year(2030),
    )

    bof_subsidy2 = Subsidy(
        scenario_name="test_scenario_2",
        technology_name="BOF",
        iso3="USA",
        cost_item="capex",
        absolute_subsidy=0.0,
        relative_subsidy=0.1,  # 10% subsidy
        start_year=Year(2020),
        end_year=Year(2030),
    )

    capex_subsidies = {"USA": {"BOF": [bof_subsidy1, bof_subsidy2]}}

    # Set up pricing - need lists for price_series
    # Create price lists for the expected time horizon (construction_time + lifetime = 2 + 20 = 22 years)
    price = {"steel": [600.0] * 22, "iron": [400.0] * 22}

    # Create allowed_techs dictionary for all years in the simulation
    allowed_techs = {Year(year): ["EAF", "BOF", "DRI", "BF"] for year in range(2020, 2031)}

    # Evaluate expansion with subsidies
    options = pg.evaluate_expansion_options(
        price_series=price,
        capacity=Volumes(1000),
        region_capex=bus.env.name_to_capex["greenfield"],
        cost_of_debt_dict=bus.env.industrial_cost_of_debt,
        cost_of_equity_dict=bus.env.industrial_cost_of_equity,
        get_bom_from_avg_boms=bus.env.get_bom_from_avg_boms,
        dynamic_feedstocks=bus.env.dynamic_feedstocks,
        fopex_for_iso3=bus.env.fopex_by_country,
        iso3_to_region_map=country_mappings_for_test.iso3_to_region(),
        chosen_emissions_boundary_for_carbon_costs=bus.env.config.chosen_emissions_boundary_for_carbon_costs,
        tech_to_product=bus.env.technology_to_product,
        plant_lifetime=bus.env.config.plant_lifetime,
        construction_time=2,
        technology_emission_factors=bus.env.technology_emission_factors,  # Add missing parameter
        global_risk_free_rate=bus.env.global_risk_free_rate,
        equity_share=0.4,  # Add equity_share parameter (40% equity, 60% debt)
        current_year=bus.env.year,
        allowed_techs=allowed_techs,  # Add required allowed_techs parameter
        capex_subsidies=capex_subsidies,
    )

    # Get the results
    plant_id = plant_with_location.plant_id
    npv, best_tech, capex_with_subsidy = options[plant_id]

    # Verify BOF was chosen given the favorable BOM
    if best_tech == "BOF":
        original_capex = bus.env.name_to_capex["greenfield"]["Americas"]["BOF"]
        # First apply absolute subsidy, then relative
        expected_capex = (original_capex - 50.0) * 0.9
        assert abs(capex_with_subsidy - expected_capex) < 0.01


def test_evaluate_expansion_with_restricted_allowed_techs(
    setup_environment, plant_with_location, mocker, country_mappings_for_test
):
    """Test that expansion only considers technologies in allowed_techs."""
    bus = setup_environment
    bus.uow.plants.add(plant_with_location)

    pg = PlantGroup(plant_group_id="pg_test", plants=[plant_with_location])
    bus.uow.plant_groups.add(pg)

    # Mock get_bom_from_avg_boms to make DRI and BF more attractive
    def mock_get_bom(energy_costs, tech, capacity, most_common_reductant=None):
        if tech in ["DRI", "BF"]:
            # Make DRI and BF very attractive economically - very low costs
            return (
                {
                    "materials": {"iron_ore": {"unit_cost": 10.0, "demand": 1.0}},
                    "energy": {"electricity": {"unit_cost": 5.0, "demand": 0.1}},
                },
                0.98,  # Very high utilization
                "iron_ore",
            )
        else:
            # Make EAF and BOF less attractive - high costs
            return (
                {
                    "materials": {"scrap": {"unit_cost": 350.0, "demand": 1.0}},
                    "energy": {"electricity": {"unit_cost": 150.0, "demand": 1.0}},
                },
                0.5,  # Lower utilization
                "scrap",
            )

    mocker.patch.object(bus.env, "get_bom_from_avg_boms", side_effect=mock_get_bom)

    # Set up pricing - need lists for price_series
    # Make iron price much higher to ensure DRI/BF are more profitable
    price = {"steel": [600.0] * 22, "iron": [700.0] * 22}

    # Create allowed_techs that only allows EAF and BOF (excludes DRI and BF)
    # Even though DRI and BF would be more economical, they shouldn't be chosen
    allowed_techs_restricted = {Year(year): ["EAF", "BOF"] for year in range(2020, 2031)}

    # Evaluate expansion with restricted allowed technologies
    options = pg.evaluate_expansion_options(
        price_series=price,
        capacity=Volumes(1000),
        region_capex=bus.env.name_to_capex["greenfield"],
        cost_of_debt_dict=bus.env.industrial_cost_of_debt,
        cost_of_equity_dict=bus.env.industrial_cost_of_equity,
        get_bom_from_avg_boms=bus.env.get_bom_from_avg_boms,
        dynamic_feedstocks=bus.env.dynamic_feedstocks,
        fopex_for_iso3=bus.env.fopex_by_country,
        iso3_to_region_map=country_mappings_for_test.iso3_to_region(),
        chosen_emissions_boundary_for_carbon_costs=bus.env.config.chosen_emissions_boundary_for_carbon_costs,
        tech_to_product=bus.env.technology_to_product,
        plant_lifetime=bus.env.config.plant_lifetime,
        construction_time=2,
        technology_emission_factors=bus.env.technology_emission_factors,
        global_risk_free_rate=bus.env.global_risk_free_rate,
        equity_share=0.4,
        current_year=bus.env.year,
        allowed_techs=allowed_techs_restricted,  # Only EAF and BOF allowed
        capex_subsidies={},
        opex_subsidies={},
        debt_subsidies={},
    )

    # Get the results
    plant_id = plant_with_location.plant_id
    npv, best_tech, capex = options[plant_id]

    # Verify that only allowed technologies were considered
    assert best_tech in ["EAF", "BOF"], f"Technology {best_tech} was chosen but is not in allowed_techs"
    # Specifically verify that DRI and BF were not chosen despite being more economical
    assert best_tech not in ["DRI", "BF"], f"Technology {best_tech} should not have been available"

    # Now test with all technologies allowed to confirm DRI or BF would be chosen
    allowed_techs_all = {Year(year): ["EAF", "BOF", "DRI", "BF"] for year in range(2020, 2031)}

    options_all = pg.evaluate_expansion_options(
        price_series=price,
        capacity=Volumes(1000),
        region_capex=bus.env.name_to_capex["greenfield"],
        cost_of_debt_dict=bus.env.industrial_cost_of_debt,
        cost_of_equity_dict=bus.env.industrial_cost_of_equity,
        get_bom_from_avg_boms=bus.env.get_bom_from_avg_boms,
        dynamic_feedstocks=bus.env.dynamic_feedstocks,
        fopex_for_iso3=bus.env.fopex_by_country,
        iso3_to_region_map=country_mappings_for_test.iso3_to_region(),
        chosen_emissions_boundary_for_carbon_costs=bus.env.config.chosen_emissions_boundary_for_carbon_costs,
        tech_to_product=bus.env.technology_to_product,
        plant_lifetime=bus.env.config.plant_lifetime,
        construction_time=2,
        technology_emission_factors=bus.env.technology_emission_factors,
        global_risk_free_rate=bus.env.global_risk_free_rate,
        equity_share=0.4,
        current_year=bus.env.year,
        allowed_techs=allowed_techs_all,  # All technologies allowed
        capex_subsidies={},
        opex_subsidies={},
        debt_subsidies={},
    )

    npv_all, best_tech_all, capex_all = options_all[plant_id]

    # When all techs are allowed, the more economical DRI or BF should be chosen
    assert best_tech_all in ["DRI", "BF"], (
        f"With all techs allowed, {best_tech_all} was chosen but DRI or BF were expected"
    )

    # The NPV should be higher when all technologies are allowed
    # (since DRI/BF have better economics in our mock)
    assert npv_all > npv, "NPV should be higher when more economical technologies are allowed"
