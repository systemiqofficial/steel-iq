"""PlantGroup.update_hot_metal_access flags BOFs fed by any hot-metal-producing technology within radius."""

import pytest

from steelo.domain.models import (
    FurnaceGroup,
    Location,
    Plant,
    PlantGroup,
    PointInTime,
    PrimaryFeedstock,
    Technology,
    TimeFrame,
    Volumes,
    Year,
)

HOT_METAL_PRODUCERS = [
    "BF",
    "BF+CCS",
    "BF+CCU",
    "BF_CHARCOAL",
    "BF_CHARCOAL+CCS",
    "BF_CHARCOAL+CCU",
    "DRI+ESF",
    "DRI+ESF+CCS",
    "DRI+ESF+CCU",
    "SR",
    "SR+CCS",
    "SR+CCU",
]


def make_furnace_group(fg_id: str, technology_name: str, outputs: dict[str, float]) -> FurnaceGroup:
    """Build a minimal operating furnace group whose business case outputs ``outputs``."""
    feedstock = PrimaryFeedstock(metallic_charge="io_high", reductant="coke", technology=technology_name)
    feedstock.outputs = outputs
    return FurnaceGroup(
        furnace_group_id=fg_id,
        technology=Technology(
            name=technology_name,
            product="iron",
            bill_of_materials=None,
            capex_type="greenfield",
            dynamic_business_case=[feedstock],
        ),
        capacity=Volumes(1000.0),
        lifetime=PointInTime(
            plant_lifetime=20,
            current=2025,
            time_frame=TimeFrame(start=Year(2025), end=Year(2045)),
        ),
        status="operating",
        chosen_reductant="coke",
        last_renovation_date=None,
        historical_production={},
        utilization_rate=0.8,
    )


def make_plant(plant_id: str, lat: float, lon: float, furnace_groups: list[FurnaceGroup]) -> Plant:
    """Build a minimal plant at (lat, lon) holding ``furnace_groups``."""
    return Plant(
        plant_id=plant_id,
        location=Location(lat=lat, lon=lon, iso3="CHN", country="China", region="Asia", distance_to_other_iso3=None),
        furnace_groups=furnace_groups,
        technology_unit_fopex={},
        power_source="grid",
        soe_status="private",
        parent_gem_id="E100000000000",
        workforce_size=100,
        certified=False,
        category_steel_product=set(),
    )


def hot_metal_access_after_update(producer_tech: str, outputs: dict[str, float], distance_deg: float = 0.01):
    """Return (fg flag, plant flag) for a BOF whose group holds one ``producer_tech`` plant ``distance_deg`` away."""
    bof = make_furnace_group("bof", "BOF", outputs={"steel": 1.0})
    bof_plant = make_plant("bof_plant", 35.0, 110.0, [bof])
    producer_plant = make_plant(
        "producer_plant", 35.0 + distance_deg, 110.0, [make_furnace_group("producer", producer_tech, outputs)]
    )

    PlantGroup(plant_group_id="group", plants=[bof_plant, producer_plant]).update_hot_metal_access(hot_metal_radius=5.0)

    return bof.has_hot_metal_access, bof_plant.has_hot_metal_access


@pytest.mark.parametrize("producer_tech", HOT_METAL_PRODUCERS)
def test_bof_gets_access_from_any_hot_metal_producer_within_radius(producer_tech):
    """Every technology whose bill of materials outputs hot metal grants access, not only a hard-coded name list."""
    assert hot_metal_access_after_update(producer_tech, {"hot_metal": 1.0}) == (True, True)


def test_bof_gets_no_access_from_producers_of_other_iron_products():
    """DRI/HBI or liquid iron outputs do not satisfy a BOF's hot-metal minimum share."""
    assert hot_metal_access_after_update("DRI", {"dri_high": 1.0, "hbi_high": 1.0}) == (False, False)
    assert hot_metal_access_after_update("MOE", {"liquid_iron": 1.0, "electrolytic_iron": 1.0}) == (False, False)


def test_bof_gets_no_access_from_a_hot_metal_producer_outside_the_radius():
    """A blast furnace ~11 km away is beyond the 5 km hot-metal radius."""
    assert hot_metal_access_after_update("BF", {"hot_metal": 1.0}, distance_deg=0.1) == (False, False)
