"""Tests for FurnaceGroup.co2_slip_carbon_cost_contribution property."""

from unittest.mock import Mock

import pytest

from steelo.domain.carbon_cost import CarbonCost
from steelo.domain.models import FurnaceGroup


def _make_fg_mock(
    carbon_cost: CarbonCost | None = None,
    utilization_rate: float = 0.8,
    carbon_breakdown: dict[str, dict[str, float]] | None = None,
    fg_id: str = "P1_FG1",
) -> FurnaceGroup:
    """Build a minimal FurnaceGroup mock wired to the real property."""
    fg = Mock(spec=FurnaceGroup)
    fg.furnace_group_id = fg_id
    fg._carbon_cost = carbon_cost
    fg.utilization_rate = utilization_rate
    fg.carbon_breakdown_by_feedstock = carbon_breakdown or {}

    # Bind the real property so it executes against mock attributes
    type(fg).co2_slip_carbon_cost_contribution = FurnaceGroup.co2_slip_carbon_cost_contribution

    return fg


def test_co2_slip_basic_calculation():
    """co2_slip × carbon_price gives correct USD/t product."""
    cc = CarbonCost.calculate(emissions_per_unit=2.0, carbon_price=50.0, production=1000.0)
    fg = _make_fg_mock(
        carbon_cost=cc,
        carbon_breakdown={"io_high": {"co2_slip": 0.05, "co2_stored": 0.3}},
    )

    result = fg.co2_slip_carbon_cost_contribution
    assert result == pytest.approx(0.05 * 50.0)


def test_co2_slip_multiple_feedstocks():
    """CO2 slip is summed across feedstocks."""
    cc = CarbonCost.calculate(emissions_per_unit=2.0, carbon_price=100.0, production=1000.0)
    fg = _make_fg_mock(
        carbon_cost=cc,
        carbon_breakdown={
            "io_high": {"co2_slip": 0.03, "co2_stored": 0.2},
            "io_low": {"co2_slip": 0.02, "co2_stored": 0.1},
        },
    )

    result = fg.co2_slip_carbon_cost_contribution
    assert result == pytest.approx((0.03 + 0.02) * 100.0)


def test_co2_slip_no_carbon_cost_returns_zero():
    """Returns 0.0 when _carbon_cost is None."""
    fg = _make_fg_mock(
        carbon_cost=None,
        carbon_breakdown={"io_high": {"co2_slip": 0.05}},
    )

    assert fg.co2_slip_carbon_cost_contribution == 0.0


def test_co2_slip_zero_utilization_returns_zero():
    """Returns 0.0 when utilisation rate is zero."""
    cc = CarbonCost.calculate(emissions_per_unit=2.0, carbon_price=50.0, production=1000.0)
    fg = _make_fg_mock(
        carbon_cost=cc,
        utilization_rate=0,
        carbon_breakdown={"io_high": {"co2_slip": 0.05}},
    )

    assert fg.co2_slip_carbon_cost_contribution == 0.0


def test_co2_slip_zero_carbon_price_returns_zero():
    """Returns 0.0 when carbon price is zero."""
    cc = CarbonCost.zero(production=1000.0)
    fg = _make_fg_mock(
        carbon_cost=cc,
        carbon_breakdown={"io_high": {"co2_slip": 0.05}},
    )

    assert fg.co2_slip_carbon_cost_contribution == 0.0


def test_co2_slip_absent_from_breakdown():
    """Returns 0.0 when no feedstock has co2_slip."""
    cc = CarbonCost.calculate(emissions_per_unit=2.0, carbon_price=50.0, production=1000.0)
    fg = _make_fg_mock(
        carbon_cost=cc,
        carbon_breakdown={"io_high": {"co2_stored": 0.3}},
    )

    assert fg.co2_slip_carbon_cost_contribution == 0.0


def test_co2_slip_empty_breakdown():
    """Returns 0.0 when carbon breakdown is empty."""
    cc = CarbonCost.calculate(emissions_per_unit=2.0, carbon_price=50.0, production=1000.0)
    fg = _make_fg_mock(carbon_cost=cc, carbon_breakdown={})

    assert fg.co2_slip_carbon_cost_contribution == 0.0
