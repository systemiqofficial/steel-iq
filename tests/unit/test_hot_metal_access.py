"""update_hot_metal_access flags plants fed by any active hot-metal-producing technology the clustering scope lets reach them."""

import pytest

from steelo.domain.models import (
    FurnaceGroup,
    Location,
    Plant,
    PointInTime,
    PrimaryFeedstock,
    Technology,
    TimeFrame,
    Volumes,
    Year,
    update_hot_metal_access,
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


def make_furnace_group(
    fg_id: str, technology_name: str, outputs: dict[str, float], status: str = "operating"
) -> FurnaceGroup:
    """Build a minimal furnace group with ``status`` whose business case outputs ``outputs``."""
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
        status=status,
        chosen_reductant="coke",
        last_renovation_date=None,
        historical_production={},
        utilization_rate=0.8,
    )


def make_plant(
    plant_id: str,
    lat: float,
    lon: float,
    furnace_groups: list[FurnaceGroup],
    owner: str = "E100000000000",
    iso3: str = "CHN",
) -> Plant:
    """Build a minimal plant at (lat, lon) in ``iso3``, owned by ``owner``, holding ``furnace_groups``."""
    return Plant(
        plant_id=plant_id,
        location=Location(lat=lat, lon=lon, iso3=iso3, country=iso3, region="Asia", distance_to_other_iso3=None),
        furnace_groups=furnace_groups,
        technology_unit_fopex={},
        power_source="grid",
        soe_status="private",
        parent_gem_id=owner,
        workforce_size=100,
        certified=False,
        category_steel_product=set(),
    )


ACTIVE_STATUSES = ["operating", "operating pre-retirement", "operating switching technology"]


def update(plants: list[Plant], clustering_scope: str = "plant") -> None:
    update_hot_metal_access(
        plants, hot_metal_radius=5.0, active_statuses=ACTIVE_STATUSES, clustering_scope=clustering_scope
    )


def hot_metal_access_after_update(
    producer_tech: str, outputs: dict[str, float], distance_deg: float = 0.01, producer_status: str = "operating"
):
    """Return (fg flag, plant flag) for a BOF with one ``producer_tech`` plant ``distance_deg`` away."""
    bof = make_furnace_group("bof", "BOF", outputs={"steel": 1.0})
    bof_plant = make_plant("bof_plant", 35.0, 110.0, [bof])
    producer_plant = make_plant(
        "producer_plant",
        35.0 + distance_deg,
        110.0,
        [make_furnace_group("producer", producer_tech, outputs, status=producer_status)],
    )

    update([bof_plant, producer_plant])

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


@pytest.mark.parametrize("status", ["retired", "mothballed", "announced", "construction", "cancelled"])
def test_bof_gets_no_access_from_an_inactive_hot_metal_producer(status):
    """Only producers in an active status feed a BOF; a retired or not-yet-built BF does not."""
    assert hot_metal_access_after_update("BF", {"hot_metal": 1.0}, producer_status=status) == (False, False)


def test_bf_only_plant_has_access_for_a_bof_expansion():
    """A plant with a BF but no BOF is flagged, so the expansion check can offer a BOF next to its own BF."""
    bf = make_furnace_group("bf", "BF", {"hot_metal": 1.0})
    eaf = make_furnace_group("eaf", "EAF", {"steel": 1.0})
    bf_plant = make_plant("bf_plant", 35.0, 110.0, [bf, eaf])

    update([bf_plant])

    assert bf_plant.has_hot_metal_access
    assert bf.has_hot_metal_access and eaf.has_hot_metal_access


def test_non_bof_furnace_group_inherits_access_from_a_nearby_producer():
    """An EAF-only plant within radius of a BF is flagged, for any future switch to BOF."""
    eaf = make_furnace_group("eaf", "EAF", {"steel": 1.0})
    eaf_plant = make_plant("eaf_plant", 35.0, 110.0, [eaf])
    bf_plant = make_plant("bf_plant", 35.01, 110.0, [make_furnace_group("bf", "BF", {"hot_metal": 1.0})])

    update([eaf_plant, bf_plant])

    assert eaf_plant.has_hot_metal_access and eaf.has_hot_metal_access


def test_access_is_reset_when_the_producer_retires():
    """Flags are recomputed each call, so a plant loses access once its only BF leaves the active statuses."""
    bf = make_furnace_group("bf", "BF", {"hot_metal": 1.0})
    bof = make_furnace_group("bof", "BOF", {"steel": 1.0})
    plant = make_plant("plant", 35.0, 110.0, [bf, bof])

    update([plant])
    assert plant.has_hot_metal_access and bof.has_hot_metal_access

    bf.status = "retired"
    update([plant])
    assert not plant.has_hot_metal_access and not bof.has_hot_metal_access


def bof_access_next_to_bf(clustering_scope: str, bf_owner: str = "E100000000000", bf_iso3: str = "CHN") -> bool:
    """Return the access flag of a BOF owned by E100000000000 in CHN, with a BF ~1 km away."""
    bof = make_furnace_group("bof", "BOF", {"steel": 1.0})
    bof_plant = make_plant("bof_plant", 35.0, 110.0, [bof])
    bf_plant = make_plant(
        "bf_plant", 35.01, 110.0, [make_furnace_group("bf", "BF", {"hot_metal": 1.0})], owner=bf_owner, iso3=bf_iso3
    )

    update([bof_plant, bf_plant], clustering_scope=clustering_scope)

    return bof.has_hot_metal_access


def test_plant_scope_grants_access_from_another_owner_across_a_border():
    """Under plant scope the LP feeds a BOF from any BF within radius, so the flag must agree."""
    assert bof_access_next_to_bf("plant", bf_owner="E200000000000", bf_iso3="VNM")


@pytest.mark.parametrize("bf_owner, expected", [("E100000000000", True), ("E200000000000", False)])
def test_plant_group_scope_requires_the_same_owner(bf_owner, expected):
    """Under plant_group scope hot metal stays within one ultimate plant group."""
    assert bof_access_next_to_bf("plant_group", bf_owner=bf_owner) is expected


@pytest.mark.parametrize("bf_iso3, expected", [("CHN", True), ("VNM", False)])
def test_iso3_scope_requires_the_same_country(bf_iso3, expected):
    """Under iso3 scope hot metal stays within one country, whoever owns the BF."""
    assert bof_access_next_to_bf("iso3", bf_owner="E200000000000", bf_iso3=bf_iso3) is expected
