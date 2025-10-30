import math
from datetime import date

from steelo.domain.commands import UpdateDynamicCosts
from steelo.domain.constants import Volumes, Year
from steelo.domain.models import (
    FurnaceGroup,
    Location,
    Plant,
    PlantGroup,
    PointInTime,
    ProductionThreshold,
    Technology,
    TimeFrame,
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
        energy_cost_dict={"electricity": 0.15, "hydrogen": 8.5},
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
    fg = make_test_furnace_group()
    plant = make_test_plant(fg)
    plant_group = PlantGroup(plant_group_id="PG1", plants=[plant])

    raw_power_price = 55.0  # USD/MWh from geospatial dataset
    raw_hydrogen_price = 2.5  # USD/kg (already correct units)

    custom_energy_costs = {
        "power_price": FakeDataArray(raw_power_price),
        "capped_lcoh": FakeDataArray(raw_hydrogen_price),
    }

    capex_dict_all_locs = {"test_region": {"DRI": 100.0}}
    cost_debt_all_locs = {"TST": 0.05}
    iso3_to_region_map = {"TST": "test_region"}

    commands_generated = plant_group.update_dynamic_costs_for_business_opportunities(
        current_year=Year(2025),
        consideration_time=1,
        custom_energy_costs=custom_energy_costs,
        capex_dict_all_locs=capex_dict_all_locs,
        cost_debt_all_locs=cost_debt_all_locs,
        iso3_to_region_map=iso3_to_region_map,
        global_risk_free_rate=0.01,
        capex_subsidies={},
        debt_subsidies={},
    )

    assert commands_generated, "Expected one UpdateDynamicCosts command to be emitted."
    (cmd,) = commands_generated
    assert isinstance(cmd, UpdateDynamicCosts)
    assert math.isclose(cmd.new_electricity_cost, raw_power_price, rel_tol=1e-9)
    assert math.isclose(cmd.new_hydrogen_cost, raw_hydrogen_price, rel_tol=1e-9)
