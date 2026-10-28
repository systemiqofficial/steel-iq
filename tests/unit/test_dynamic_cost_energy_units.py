import dataclasses
import math
from datetime import date

import pytest

from steelo.domain.commands import UpdateDynamicCosts
from steelo.domain.constants import T_TO_KG, Volumes, Year
from steelo.domain.models import (
    CaptiveEnergy,
    Environment,
    FurnaceGroup,
    Location,
    Plant,
    PlantGroup,
    PointInTime,
    ProductionThreshold,
    Technology,
    TimeFrame,
)


def _stack_at(location, year):
    """The stack the re-valuation locks: one power and electrolyser stack, at the year asked for."""
    return CaptiveEnergy(
        lock_year=year,
        coverage=0.85,
        lcoe=0.04,
        electrolyser_use=0.05,
        electrolyser_cost=1.0,
        hydrogen_contract=None,
    )


class FakeDataArray:
    """Minimal xarray.DataArray stand-in for unit tests."""

    def __init__(self, value: float):
        self._value = value

    def sel(self, *, lat: float, lon: float, method: str | None = None):  # noqa: ARG002
        return self

    @property
    def values(self) -> float:
        return self._value


def make_test_furnace_group() -> FurnaceGroup:
    technology = Technology(name="DRI", product="iron", dynamic_business_case=[])
    lifetime = PointInTime(
        plant_lifetime=20,
        current=Year(2025),
        time_frame=TimeFrame(start=Year(2025), end=Year(2045)),
    )
    return FurnaceGroup(
        furnace_group_id="FG1",
        capacity=Volumes(1_000_000.0),
        status="announced",
        last_renovation_date=date(2020, 1, 1),
        technology=technology,
        historical_production={},
        utilization_rate=0.0,
        lifetime=lifetime,
        production_threshold=ProductionThreshold(low=0.0, high=1.0),
        energy_cost_dict={"electricity": 0.08, "hydrogen": 4500.0},  # USD/kWh, USD/t
        tech_unit_fopex=5.0,
        energy_vopex_by_input={},
        historical_npv_business_opportunities={2024: 0.0},
    )


def make_test_plant(furnace_group: FurnaceGroup) -> Plant:
    location = Location(lat=24.0, lon=55.0, country="Testland", region="test_region", iso3="TST")
    return Plant(
        plant_id="PLANT1",
        location=location,
        furnace_groups=[furnace_group],
        power_source="grid",
        soe_status="private",
        parent_gem_id="parent",
        workforce_size=100,
        certified=False,
        category_steel_product=set(),
        technology_unit_fopex={"dri": 5.0},
    )


def test_update_dynamic_costs_uses_geospatial_power_price_without_scaling():
    """
    The re-valuation writes the site's power price unscaled and its hydrogen price in USD/t.

    The ``power_price`` raster is already in USD/kWh, as the energy cost dicts hold it; the
    ``capped_lcoh`` raster is in USD/kg and must be multiplied by ``T_TO_KG``.
    """
    fg = make_test_furnace_group()
    plant = make_test_plant(fg)
    plant_group = PlantGroup(plant_group_id="PG1", plants=[plant])

    raw_power_price = 0.055  # USD/kWh from geospatial dataset (power_price layer stored in USD/kWh)
    raw_hydrogen_price = 2.5  # USD/kg from geospatial dataset (capped_lcoh layer stored in USD/kg)

    custom_energy_costs = {
        "power_price": FakeDataArray(raw_power_price),
        "capped_lcoh": FakeDataArray(raw_hydrogen_price),
    }

    capex_dict_all_locs = {"test_region": {"DRI": 100.0}}
    cost_debt_all_locs = {
        "TST": {
            tech: 0.05
            for tech in (
                "EAF",
                "BF",
                "BOF",
                "DRI",
                "SR",
                "MOE",
                "E-WIN",
                "BF+CCS",
                "BF+CCU",
                "DRI+CCS",
                "DRI+CCU",
                "DRI+EAF",
                "DRI+ESF",
                "ESF",
                "ZZZ",
            )
        }
    }
    iso3_to_region_map = {"TST": "test_region"}

    commands_generated = plant_group.update_dynamic_costs_for_business_opportunities(
        current_year=Year(2025),
        consideration_time=1,
        construction_time=4,
        custom_energy_costs=custom_energy_costs,
        capex_dict_all_locs=capex_dict_all_locs,
        cost_debt_all_locs=cost_debt_all_locs,
        iso3_to_region_map=iso3_to_region_map,
        global_risk_free_rate=0.01,
        avg_utilization={},
        captive_energy_at=_stack_at,
        capex_subsidies={},
        debt_subsidies={},
    )

    assert commands_generated, "Expected one UpdateDynamicCosts command to be emitted."
    (cmd,) = commands_generated
    assert isinstance(cmd, UpdateDynamicCosts)
    assert math.isclose(cmd.new_energy_costs["electricity"], raw_power_price, rel_tol=1e-9)
    assert math.isclose(cmd.new_energy_costs["hydrogen"], raw_hydrogen_price * T_TO_KG, rel_tol=1e-9)
    # Output and no_subsidy should match input when no subsidies active
    assert math.isclose(cmd.new_output_energy_costs["electricity"], raw_power_price, rel_tol=1e-9)
    assert math.isclose(cmd.new_energy_costs_no_subsidy["electricity"], raw_power_price, rel_tol=1e-9)


def test_hydrogen_refresh_keeps_new_site_price_while_existing_plant_follows_region():
    """
    The year-start hydrogen refresh moves an existing plant to its region's price but leaves a new site alone.

    Notes:
        Both plants share one geo key, whose capped price moves from 4.0 to 3.6 USD/kg. The new site
        keeps the 3,000 USD/t it was given at construction start in all three cost dicts; the existing
        plant takes 3.6 USD/kg × ``T_TO_KG``.
    """
    new_site_fg = make_test_furnace_group()
    new_site_fg.set_energy_costs(electricity=0.05, hydrogen=3_000.0)
    new_site = make_test_plant(new_site_fg)
    new_site.parent_gem_id = "indi"

    existing_fg = make_test_furnace_group()
    existing_fg.set_energy_costs(electricity=0.08, hydrogen=4.0 * T_TO_KG)
    existing_plant = make_test_plant(existing_fg)

    for plant in (new_site, existing_plant):
        plant.update_furnace_hydrogen_costs({"TST": 3.6})

    for costs in (
        new_site_fg.energy_costs,
        new_site_fg.output_energy_costs,
        new_site_fg.energy_costs_no_subsidy,
    ):
        assert math.isclose(costs["hydrogen"], 3_000.0, rel_tol=1e-9)
    for costs in (
        existing_fg.energy_costs,
        existing_fg.output_energy_costs,
        existing_fg.energy_costs_no_subsidy,
    ):
        assert math.isclose(costs["hydrogen"], 3.6 * T_TO_KG, rel_tol=1e-9)


def make_new_site(*furnace_groups: FurnaceGroup) -> Plant:
    """A model-built plant in TST holding the given furnace groups."""
    plant = make_test_plant(furnace_groups[0])
    plant.parent_gem_id = "indi_TST"
    plant.furnace_groups = list(furnace_groups)
    return plant


def make_site_furnace_group(furnace_group_id: str, status: str, captive: CaptiveEnergy | None) -> FurnaceGroup:
    """A new site's furnace group with a status and a stack, storing 0.08 USD/kWh and 4,500 USD/t without subsidy."""
    fg = make_test_furnace_group()
    fg.furnace_group_id = furnace_group_id
    fg.status = status
    fg.captive_energy = captive
    return fg


def year_end_environment(grid_by_year: dict[int, float]) -> Environment:
    """
    An environment holding only what the year-end input-cost refresh reads.

    Args:
        grid_by_year: TST's grid price per year (USD/kWh).

    Returns:
        The environment, with natural gas at 0.03 and hydrogen at 4,000 USD/t in the input costs.
    """
    env = object.__new__(Environment)
    env.config = type("Config", (), {"disposal_cost_outputs": frozenset()})()
    env.input_costs = {
        "TST": {
            Year(year): {"electricity": grid, "hydrogen": 4000.0, "natural_gas": 0.03}
            for year, grid in grid_by_year.items()
        }
    }
    return env


def refresh(env: Environment, plant: Plant, year: int) -> None:
    """Run the year-end input-cost refresh for a year."""
    env.year = Year(year)
    env.set_input_cost_in_furnace_groups(world_plants=[plant])


LOCKED = CaptiveEnergy(
    lock_year=Year(2028),
    coverage=0.85,
    lcoe=0.048,
    electrolyser_use=0.05,
    electrolyser_cost=1.3,
    hydrogen_contract=None,
)


def test_year_end_refresh_moves_a_new_sites_prices_with_the_grid_share():
    """
    A site locked at 48 USD/MWh, 0.05 MWh/kg and 1.3 USD/kg pays 0.15 × the grid's rise of 20 USD/MWh more for
    electricity, and e × that more for hydrogen; a site on contract keeps the ceiling flat.

    Notes:
        The grid moves from 90 USD/MWh in 2032 to 110 in 2033.
    """
    own = make_site_furnace_group("FG_OWN", "operating", LOCKED)
    contract = make_site_furnace_group("FG_CONTRACT", "operating", dataclasses.replace(LOCKED, hydrogen_contract=3.0))
    plant = make_new_site(own, contract)
    env = year_end_environment({2032: 0.090, 2033: 0.110})

    refresh(env, plant, 2032)
    before = {fg.furnace_group_id: dict(fg.energy_costs_no_subsidy) for fg in plant.furnace_groups}
    refresh(env, plant, 2033)
    after = {fg.furnace_group_id: dict(fg.energy_costs_no_subsidy) for fg in plant.furnace_groups}

    assert before["FG_OWN"]["electricity"] == pytest.approx(0.85 * 0.048 + 0.15 * 0.090)
    assert after["FG_OWN"]["electricity"] - before["FG_OWN"]["electricity"] == pytest.approx(0.15 * 0.020)
    assert after["FG_OWN"]["hydrogen"] - before["FG_OWN"]["hydrogen"] == pytest.approx(
        0.05 * 1000 * 0.15 * 0.020 * 1000
    )
    assert before["FG_OWN"]["hydrogen"] == pytest.approx((0.05 * 1000 * (0.85 * 0.048 + 0.15 * 0.090) + 1.3) * 1000)
    assert before["FG_CONTRACT"]["hydrogen"] == after["FG_CONTRACT"]["hydrogen"] == pytest.approx(3000.0)
    assert after["FG_CONTRACT"]["electricity"] == pytest.approx(after["FG_OWN"]["electricity"])
    assert after["FG_OWN"]["natural_gas"] == 0.03


def test_year_end_refresh_keeps_an_opportunitys_stored_prices():
    """A considered opportunity keeps its cell prices, which the year's re-valuation replaces."""
    opportunity = make_site_furnace_group("FG_OPP", "considered", LOCKED)
    plant = make_new_site(opportunity)
    env = year_end_environment({2033: 0.110})

    refresh(env, plant, 2033)

    assert opportunity.energy_costs_no_subsidy["electricity"] == 0.08
    assert opportunity.energy_costs_no_subsidy["hydrogen"] == 4500.0
    assert opportunity.energy_costs_no_subsidy["natural_gas"] == 0.03


def test_year_end_refresh_raises_for_a_new_site_group_without_a_stack():
    """A new site's group past construction start without a stack has nothing to be priced from and stops the run."""
    plant = make_new_site(make_site_furnace_group("FG_NONE", "operating", None))

    with pytest.raises(ValueError, match="holds no captive energy stack"):
        refresh(year_end_environment({2033: 0.110}), plant, 2033)


def test_year_end_refresh_raises_for_an_opportunity_without_its_stored_hydrogen():
    """An opportunity missing its stored hydrogen without subsidy stops the run instead of taking another price."""
    locked = make_site_furnace_group("FG_LOCKED", "operating", LOCKED)
    opportunity = make_site_furnace_group("FG_OPP", "considered", LOCKED)
    opportunity.energy_costs = {"electricity": 0.06}
    opportunity.energy_costs_no_subsidy = {"electricity": 0.06}
    plant = make_new_site(locked, opportunity)

    with pytest.raises(KeyError, match="hydrogen"):
        refresh(year_end_environment({2033: 0.110}), plant, 2033)
