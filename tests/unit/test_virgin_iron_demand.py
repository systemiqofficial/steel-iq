"""Tests for VirginIronDemand and the iron_required_per_tonne_steel config."""

from types import SimpleNamespace

import pytest

from steelo.domain.models import Environment, PrimaryFeedstock, VirginIronDemand, Year
from steelo.simulation import SimulationConfig


YEAR = Year(2030)


def _scrap_supplier(capacity: float) -> SimpleNamespace:
    return SimpleNamespace(commodity="scrap", capacity_by_year={YEAR: capacity})


def _scrap_feedstocks(required_scrap: float) -> dict[str, list[PrimaryFeedstock]]:
    pf = PrimaryFeedstock(metallic_charge="scrap", reductant="", technology="EAF")
    pf.required_quantity_per_ton_of_product = required_scrap
    return {"EAF": [pf]}


def _make_config(**overrides) -> SimulationConfig:
    return SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2050),
        master_excel_path="dummy.xlsx",
        output_dir="/tmp/test_output",
        **overrides,
    )


# ---------------------------------------------------------------------------
# VirginIronDemand
# ---------------------------------------------------------------------------


def test_default_ratio_is_1_2():
    """Without scrap, 100 t of steel needs 120 t of virgin iron by default."""
    demand = VirginIronDemand(world_suppliers=[], steel_demand_dict={"dc": {YEAR: 100.0}}, dynamic_feedstocks={})

    assert demand.get_demand(YEAR) == pytest.approx(120.0)


def test_custom_ratio_scales_demand():
    demand = VirginIronDemand(
        world_suppliers=[],
        steel_demand_dict={"dc": {YEAR: 100.0}},
        dynamic_feedstocks={},
        iron_required_per_tonne_steel=1.5,
    )

    assert demand.get_demand(YEAR) == pytest.approx(150.0)


def test_ratio_applies_only_to_steel_not_made_from_scrap():
    """Scrap covers 110 t / 1.1 = 100 t of steel; the remaining 300 t needs 300 * 1.3 t of iron."""
    demand = VirginIronDemand(
        world_suppliers=[_scrap_supplier(110.0)],
        steel_demand_dict={"dc_a": {YEAR: 250.0}, "dc_b": {YEAR: 150.0}},
        dynamic_feedstocks=_scrap_feedstocks(1.1),
        iron_required_per_tonne_steel=1.3,
    )

    assert demand.get_demand(YEAR) == pytest.approx(390.0)


def test_demand_series_uses_ratio():
    demand = VirginIronDemand(
        world_suppliers=[],
        steel_demand_dict={"dc": {Year(2030): 100.0, Year(2031): 200.0}},
        dynamic_feedstocks={},
        iron_required_per_tonne_steel=1.1,
    )

    assert demand.get_demand_series(Year(2030), 2) == pytest.approx([110.0, 220.0])


# ---------------------------------------------------------------------------
# Environment wiring
# ---------------------------------------------------------------------------


def test_environment_passes_configured_ratio():
    """initialize_virgin_iron_demand reads the ratio from the simulation config."""
    env = SimpleNamespace(
        config=SimpleNamespace(iron_required_per_tonne_steel=1.4),
        dynamic_feedstocks={},
        virgin_iron_demand=None,
    )

    Environment.initialize_virgin_iron_demand(env, [], {"dc": {YEAR: 100.0}})

    assert env.virgin_iron_demand.get_demand(YEAR) == pytest.approx(140.0)


# ---------------------------------------------------------------------------
# SimulationConfig
# ---------------------------------------------------------------------------


def test_config_default_ratio():
    assert _make_config().iron_required_per_tonne_steel == 1.2


def test_config_accepts_custom_ratio():
    assert _make_config(iron_required_per_tonne_steel=1.05).iron_required_per_tonne_steel == 1.05


@pytest.mark.parametrize("value", [0.0, -1.2])
def test_config_rejects_non_positive_ratio(value):
    with pytest.raises(ValueError, match="iron_required_per_tonne_steel must be > 0.0"):
        _make_config(iron_required_per_tonne_steel=value)
