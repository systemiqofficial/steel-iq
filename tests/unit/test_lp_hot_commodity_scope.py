"""Hot-commodity edges the clustered trade LP blocks, per geographical_clustering_scope."""

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from steelo.domain.models import Location
from steelo.domain.trade_modelling.set_up_steel_trade_lp import (
    fix_to_zero_allocations_where_distance_doesnt_match_commodity,
)


@dataclass
class Config:
    geographical_clustering_scope: str
    hot_metal_radius: float = 5.0
    enable_furnace_group_clustering: bool = True
    closely_allocated_products: list[str] = field(default_factory=lambda: ["hot_metal"])


class Variable:
    def __init__(self) -> None:
        self.fixed = False

    def fix(self, value: float) -> None:
        assert value == 0
        self.fixed = True


def location(lat: float, iso3: str) -> Location:
    return Location(lat=lat, lon=110.0, iso3=iso3, country="", region="", distance_to_other_iso3=None)


def blocked_after_fix(config: Config, to_lat: float, to_iso3: str, keys: tuple[str, str] = ("g1", "g1")) -> bool:
    """Whether the hot_metal edge bf → bof is fixed to zero; the BF sits at lat 35.0 in CHN."""
    variable = Variable()
    trade_lp = SimpleNamespace(
        process_centers=[
            SimpleNamespace(name="bf", location=location(35.0, "CHN")),
            SimpleNamespace(name="bof", location=location(to_lat, to_iso3)),
        ],
        lp_model=SimpleNamespace(allocation_variables={("bf", "bof", "hot_metal"): variable}),
    )
    env = SimpleNamespace(
        meta_furnace_groups=[
            SimpleNamespace(meta_furnace_group_id=name, cluster_key=SimpleNamespace(location_key=key))
            for name, key in zip(("bf", "bof"), keys)
        ]
    )
    fix_to_zero_allocations_where_distance_doesnt_match_commodity(trade_lp, config, env)
    return variable.fixed


@pytest.mark.parametrize(
    "to_lat, to_iso3, blocked",
    [
        (35.03, "CHN", False),  # neighbouring plant ~3.3 km away
        (35.03, "MNG", False),  # across a border, still within radius
        (35.1, "CHN", True),  # ~11 km away
    ],
)
def test_plant_scope_applies_the_radius_without_a_country_check(to_lat, to_iso3, blocked):
    assert blocked_after_fix(Config("plant"), to_lat, to_iso3) is blocked


@pytest.mark.parametrize(
    "keys, to_iso3, blocked",
    [
        (("g1", "g1"), "CHN", False),
        (("g1", "g1"), "MNG", False),  # a plant group spanning a border is one cluster scope
        (("g1", "g2"), "CHN", True),
        (("CHN", "MNG"), "MNG", True),  # owner-less clusters fall back to their iso3 key
    ],
)
def test_plant_group_scope_keeps_flows_inside_one_cluster_key(keys, to_iso3, blocked):
    """No radius is applied, even at 500 km; disaggregation enforces it per furnace group."""
    assert blocked_after_fix(Config("plant_group"), 40.0, to_iso3, keys) is blocked


@pytest.mark.parametrize("to_iso3, blocked", [("CHN", False), ("MNG", True)])
def test_iso3_scope_keeps_flows_inside_a_country(to_iso3, blocked):
    assert blocked_after_fix(Config("iso3"), 40.0, to_iso3) is blocked
