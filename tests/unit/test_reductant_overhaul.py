"""Tests for the reductant overhaul (A1 status-filtered modal vote, A2 candidate
energy subsidies, B5 total-cost annual re-pick)."""

from datetime import date

from steelo.domain.models import (
    Environment,
    FurnaceGroup,
    Location,
    Plant,
    PointInTime,
    PrimaryFeedstock,
    Technology,
    Year,
)
from steelo.utilities import utils


def _make_fg(fg_id: str, tech_name: str, reductant: str, status: str = "operating") -> FurnaceGroup:
    """Build a minimal furnace group with a fixed chosen reductant."""
    return FurnaceGroup(
        furnace_group_id=fg_id,
        capacity=100_000,
        status=status,
        last_renovation_date=date(2020, 1, 1),
        technology=Technology(name=tech_name, product="iron"),
        historical_production={},
        utilization_rate=0.8,
        lifetime=PointInTime(plant_lifetime=20, current=Year(2025)),
        chosen_reductant=reductant,
        energy_cost_dict={},
    )


# ── A1: status-filtered fleet-modal aggregation ──────────────────────────────


def test_most_common_reductant_excludes_inactive_statuses():
    """Closed and discarded furnace groups do not vote in the modal aggregation.

    Two closed coal FGs must not outvote one operating natural_gas FG.
    """
    furnace_groups = [
        _make_fg("fg1", "DRI", "coal", status="closed"),
        _make_fg("fg2", "DRI", "coal", status="discarded"),
        _make_fg("fg3", "DRI", "natural_gas", status="operating"),
    ]
    result = utils.get_most_common_reductant_by_technology(furnace_groups, ["operating"])
    assert result == {"DRI": "natural_gas"}


def test_most_common_reductant_only_listed_statuses_count():
    """Only statuses named in active_statuses vote (case-insensitively)."""
    furnace_groups = [
        _make_fg("fg1", "DRI", "coal", status="Operating"),
        _make_fg("fg2", "DRI", "hydrogen", status="announced"),
        _make_fg("fg3", "DRI", "hydrogen", status="announced"),
    ]
    only_operating = utils.get_most_common_reductant_by_technology(furnace_groups, ["operating"])
    assert only_operating == {"DRI": "coal"}

    both = utils.get_most_common_reductant_by_technology(furnace_groups, ["operating", "announced"])
    assert both == {"DRI": "hydrogen"}

    none_active = utils.get_most_common_reductant_by_technology(furnace_groups, [])
    assert none_active == {}


def _make_env_for_feedstocks(dynamic_feedstocks: dict) -> Environment:
    """Build a minimal Environment stub for set_primary_feedstocks_in_furnace_groups."""
    env = object.__new__(Environment)
    env.dynamic_feedstocks = dynamic_feedstocks
    env.technology_to_product = {"DRI": "iron"}
    env.cost_breakdown_keys = []
    env.carbon_breakdown_keys = []
    env.config = type("Config", (), {"active_statuses": ["operating"]})()
    env.most_common_reductant_by_tech = {}
    return env


def _make_dri_plant(fg: FurnaceGroup) -> Plant:
    return Plant(
        plant_id="P000000000001",
        location=Location(lat=0.0, lon=0.0, country="New Zealand", region="oceania", iso3="NZL"),
        furnace_groups=[fg],
        power_source="grid",
        soe_status="private",
        parent_gem_id="G01",
        workforce_size=100,
        certified=False,
        category_steel_product=set(),
        technology_unit_fopex={"dri": 10.0},
    )


def _dri_feedstocks() -> list[PrimaryFeedstock]:
    """Two DRI business cases (coal vs natural_gas) over the same metallic charge."""
    coal = PrimaryFeedstock(metallic_charge="iron_ore", reductant="coal", technology="DRI")
    coal.add_energy_requirement("coal", 10.0)
    coal.required_quantity_per_ton_of_product = 1.5
    gas = PrimaryFeedstock(metallic_charge="iron_ore", reductant="natural_gas", technology="DRI")
    gas.add_energy_requirement("natural_gas", 10.0)
    gas.required_quantity_per_ton_of_product = 1.5
    return [coal, gas]


def test_set_primary_feedstocks_skips_closed_furnace_groups():
    """A closed FG keeps its previous chosen_reductant; an operating one re-picks."""
    env = _make_env_for_feedstocks({"DRI": _dri_feedstocks()})

    # Coal is cheaper, so a re-pick would choose coal.
    energy_costs = {"coal": 1.0, "natural_gas": 2.0}
    closed_fg = _make_fg("fg_closed", "DRI", "natural_gas", status="closed")
    closed_fg.set_energy_costs(**energy_costs)
    operating_fg = _make_fg("fg_open", "DRI", "natural_gas", status="operating")
    operating_fg.set_energy_costs(**energy_costs)

    env.set_primary_feedstocks_in_furnace_groups(
        world_plants=[_make_dri_plant(closed_fg), _make_dri_plant(operating_fg)]
    )

    assert closed_fg.chosen_reductant == "natural_gas"  # frozen
    assert operating_fg.chosen_reductant == "coal"  # re-picked
    # The dynamic business case is still attached to both FGs.
    assert closed_fg.technology.dynamic_business_case
    # The environment-level vote only counts the operating FG.
    assert env.most_common_reductant_by_tech == {"DRI": "coal"}
