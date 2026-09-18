"""Tests that furnace-group lifecycle events carry plant and ownership context."""

from steelo.domain import events
from steelo.domain.models import (
    FurnaceGroup,
    Location,
    Plant,
    PointInTime,
    Technology,
    TimeFrame,
    Year,
)


def make_plant() -> Plant:
    """Build a Chinese plant with one operating BF furnace group."""
    plant = Plant(
        plant_id="test_plant",
        location=Location(lat=39.0, lon=116.0, country="China", region="Asia", iso3="CHN", geo_unit="CN-HE"),
        furnace_groups=[],
        power_source="grid",
        soe_status="private",
        parent_gem_id="E100000126525 [100.0%]",
        workforce_size=100,
        certified=False,
        category_steel_product=set(),
        steel_capacity=1000,
        technology_unit_fopex={"bf": 50, "eaf": 45},
    )
    plant.add_furnace_group(
        FurnaceGroup(
            furnace_group_id="fg1",
            capacity=1000,
            status="operating",
            last_renovation_date=None,
            technology=Technology(
                name="BF",
                bill_of_materials={},
                product="iron",
                capex_type="greenfield",
                capex=500,
            ),
            historical_production={},
            utilization_rate=0.8,
            lifetime=PointInTime(
                current=2024,
                time_frame=TimeFrame(start=Year(2014), end=Year(2034)),
                plant_lifetime=20,
            ),
        )
    )
    return plant


def test_furnace_group_closed_event_carries_plant_context():
    """close_furnace_group emits an event with capacity, location, owner, and product."""
    plant = make_plant()
    plant.close_furnace_group("fg1")

    event = plant.events[-1]
    assert isinstance(event, events.FurnaceGroupClosed)
    assert event.capacity == 1000
    assert event.iso3 == "CHN"
    assert event.geo_unit == "CN-HE"
    assert event.owner_id == "E100000126525"
    assert event.product == "iron"


def test_furnace_group_renovated_event_carries_plant_context():
    """renovate_furnace_group emits an event where old and new technology coincide."""
    plant = make_plant()
    plant.renovate_furnace_group(
        furnace_group_id="fg1",
        plant_lifetime=20,
        capacity=1000,
        capex=300.0,
        capex_no_subsidy=300.0,
        cost_of_debt=0.05,
        cost_of_debt_no_subsidy=0.05,
    )

    event = plant.events[-1]
    assert isinstance(event, events.FurnaceGroupRenovated)
    assert event.capacity == 1000
    assert event.old_capacity == 1000
    assert event.iso3 == "CHN"
    assert event.geo_unit == "CN-HE"
    assert event.old_technology_name == "BF"
    assert event.new_technology_name == "BF"
    assert event.owner_id == "E100000126525"
    assert event.product == "iron"


def test_furnace_group_tech_changed_event_records_old_technology():
    """change_furnace_group_technology captures the outgoing technology name."""
    plant = make_plant()
    plant.change_furnace_group_technology(
        furnace_group_id="fg1",
        technology_name="EAF",
        plant_lifetime=20,
        lag=0,
        capacity=1000,
        capex=400.0,
        capex_no_subsidy=400.0,
        cost_of_debt=0.05,
        cost_of_debt_no_subsidy=0.05,
        bom={"materials": {}, "energy": {}},
    )

    event = plant.events[-1]
    assert isinstance(event, events.FurnaceGroupTechChanged)
    assert event.technology_name == "EAF"
    assert event.old_technology_name == "BF"
    assert event.old_capacity == 1000
    assert event.iso3 == "CHN"
    assert event.geo_unit == "CN-HE"
    assert event.owner_id == "E100000126525"
    assert event.product == "iron"
