"""Tests that the Stage 7 technology pick respects the probabilistic_agents flag."""

import pytest
from functools import partial

from steelo.devdata import get_furnace_group, get_plant
from steelo.domain import PointInTime, Year, TimeFrame, Volumes
from steelo.domain.models import CountryMapping, CountryMappingService, PlantGroup
from steelo.domain.commands import ChangeFurnaceGroupTechnology
from steelo.simulation_types import get_default_technology_settings

TECH_NPVS = {"EAF": 100_000, "BOF": 500_000, "DRI": 1_000_000, "BF": 50_000}


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
    plant.technology_unit_fopex = {"EAF": 50.0, "BOF": 60.0, "DRI": 70.0, "BF": 65.0}
    plant.carbon_cost_series = [0.0] * 22
    return plant


@pytest.fixture
def plant_group_with_eaf(plant_with_eaf):
    pg = PlantGroup(plant_group_id="gem_test_eaf", plants=[plant_with_eaf])
    pg.balance = 1_000_000_000  # Deep pockets so the affordability gate never blocks
    return pg


@pytest.fixture
def mock_environment(bus, tmp_path):
    """Set up the environment with required data."""
    from steelo.simulation import SimulationConfig

    if bus.env.config is None:
        bus.env.config = SimulationConfig(
            start_year=Year(2025, technology_settings=get_default_technology_settings()),
            end_year=Year(2030),
            master_excel_path=tmp_path / "master.xlsx",
            output_dir=tmp_path / "output",
        )

    bus.env.country_mappings = CountryMappingService(
        [
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
    )
    bus.env.name_to_capex = {
        "greenfield": {
            "Americas": {"EAF": 400.0, "BOF": 500.0, "DRI": 600.0, "BF": 550.0},
        }
    }
    bus.env.dynamic_feedstocks = {"EAF": [], "BOF": [], "DRI": [], "BF": []}
    bus.env.allowed_furnace_transitions = {
        "EAF": ["EAF", "BF", "BOF", "DRI"],
    }
    bus.env.technology_emission_factors = []
    bus.env.technology_to_product = {"EAF": "steel", "BOF": "steel", "DRI": "iron", "BF": "iron"}
    return bus


def mock_optimal_technology_name(self, *args, **kwargs):
    """Return fixed NPVs per allowed transition so Stage 7 has a known ranking."""
    allowed_transitions = kwargs.get("allowed_furnace_transitions", {})
    allowed = allowed_transitions.get(self.technology.name, [])
    tech_npv_dict = {tech: TECH_NPVS[tech] for tech in allowed}
    bom_dict = {
        tech: {
            "materials": {"scrap": {"unit_cost": 200.0, "demand": 1.0}},
            "energy": {"electricity": {"unit_cost": 80.0, "demand": 0.5}},
        }
        for tech in allowed
    }
    npv_capex_dict = {tech: 400.0 for tech in tech_npv_dict}
    return tech_npv_dict, npv_capex_dict, 10_000, bom_dict


def evaluate_strategy(bus, plant, plant_group, probabilistic_agents):
    """Run evaluate_furnace_group_strategy with fixed inputs for the given flag."""
    furnace_group = plant.furnace_groups[0]
    return plant.evaluate_furnace_group_strategy(
        furnace_group_id=furnace_group.furnace_group_id,
        plant_group=plant_group,
        market_price_series={"steel": [600.0] * 22, "iron": [400.0] * 22},
        region_capex=bus.env.name_to_capex["greenfield"]["Americas"],
        capex_renovation_share={"EAF": 0.7, "BOF": 0.7, "DRI": 0.7, "BF": 0.7},
        cost_of_debt=0.04,
        cost_of_equity=0.08,
        get_bom_from_avg_boms=lambda *a: (None, 0.0, ""),
        probabilistic_agents=probabilistic_agents,
        dynamic_business_cases=bus.env.dynamic_feedstocks,
        chosen_emissions_boundary_for_carbon_costs="scope_1",
        technology_emission_factors=bus.env.technology_emission_factors,
        tech_to_product=bus.env.technology_to_product,
        plant_lifetime=20,
        construction_time=2,
        current_year=Year(2025),
        allowed_techs={Year(year): ["EAF", "BOF", "DRI", "BF"] for year in range(2020, 2031)},
        risk_free_rate=0.02,
        allowed_furnace_transitions=bus.env.allowed_furnace_transitions,
        capacity_limit_steel=Volumes(10_000),
        capacity_limit_iron=Volumes(10_000),
        installed_capacity_in_year=lambda tech: Volumes(1_000),
        new_plant_capacity_in_year=lambda tech: Volumes(0),
    )


def test_deterministic_selection_picks_max_npv_without_random_draw(
    mock_environment, plant_with_eaf, plant_group_with_eaf, mocker
):
    """With probabilistic_agents=False the max-NPV tech is chosen and random.choices is never used."""
    bus = mock_environment
    bus.uow.plants.add(plant_with_eaf)
    furnace_group = plant_with_eaf.furnace_groups[0]
    mocker.patch.object(
        furnace_group, "optimal_technology_name", new=partial(mock_optimal_technology_name, furnace_group)
    )
    mock_choices = mocker.patch("steelo.domain.models.random.choices")

    command = evaluate_strategy(bus, plant_with_eaf, plant_group_with_eaf, probabilistic_agents=False)

    mock_choices.assert_not_called()
    assert isinstance(command, ChangeFurnaceGroupTechnology)
    assert command.technology_name == "DRI"


def test_probabilistic_selection_uses_weighted_random_draw(
    mock_environment, plant_with_eaf, plant_group_with_eaf, mocker
):
    """With probabilistic_agents=True the tech comes from the NPV-weighted random draw."""
    bus = mock_environment
    bus.uow.plants.add(plant_with_eaf)
    furnace_group = plant_with_eaf.furnace_groups[0]
    mocker.patch.object(
        furnace_group, "optimal_technology_name", new=partial(mock_optimal_technology_name, furnace_group)
    )
    mock_choices = mocker.patch("steelo.domain.models.random.choices", return_value=["BOF"])
    mocker.patch("steelo.domain.models.random.random", return_value=0.0)  # force adoption acceptance

    command = evaluate_strategy(bus, plant_with_eaf, plant_group_with_eaf, probabilistic_agents=True)

    mock_choices.assert_called_once()
    assert isinstance(command, ChangeFurnaceGroupTechnology)
    assert command.technology_name == "BOF"
