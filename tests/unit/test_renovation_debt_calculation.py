"""Test debt calculation for plants in renovation cycles."""

import pytest
from datetime import date
from steelo.domain.models import FurnaceGroup, PointInTime, TimeFrame, Year, Technology, Volumes


def create_test_furnace_group(start_year: int, current_year: int = 2025, capacity: float = 1000.0) -> FurnaceGroup:
    """Helper to create a test furnace group."""
    fg = FurnaceGroup(
        furnace_group_id="test_fg",
        capacity=Volumes(capacity),
        status="operating",
        last_renovation_date=date(start_year, 1, 1),
        technology=Technology(name="BF", product="iron"),
        historical_production={},
        utilization_rate=0.8,
        lifetime=PointInTime(
            current=Year(current_year),
            time_frame=TimeFrame(start=Year(start_year), end=Year(start_year + 50)),
            plant_lifetime=20,
        ),
        equity_share=0.2,  # 20% equity, 80% debt
        cost_of_debt=0.05,
    )
    # Set capex_renovation_share for BF technology (typically 0.4 for renovations)
    fg.capex_renovation_share = 0.4
    # Call the method to set is_first_renovation_cycle
    fg.set_is_first_renovation_cycle()
    return fg


def test_first_cycle_plant_uses_greenfield_capex():
    """Test that a plant in its first cycle uses greenfield CAPEX."""
    # Plant started in 2015, 10 years old
    fg = create_test_furnace_group(start_year=2015)

    # Check if the plant is in its first renovation cycle
    # The property should be set by the set_is_first_renovation_cycle method
    assert hasattr(fg, "is_first_renovation_cycle")
    assert fg.is_first_renovation_cycle is True
    # Should use greenfield CAPEX for BF technology
    # Note: actual CAPEX values depend on Capex.py constants


def test_old_plant_uses_brownfield_capex():
    """Test that an old plant uses brownfield (renovation) CAPEX."""
    # Plant started in 1984, 41 years old (in 3rd cycle)
    fg = create_test_furnace_group(start_year=1984)

    assert hasattr(fg, "is_first_renovation_cycle")
    assert fg.is_first_renovation_cycle is False
    # Should use brownfield CAPEX (lower than greenfield)


def test_outstanding_debt_scales_with_remaining_years():
    """Test that outstanding debt is scaled based on remaining years in cycle."""
    # Plant with 4 years remaining (like the German plant from 1909)
    fg = create_test_furnace_group(start_year=1909)

    # Should have 4 years remaining (116 % 20 = 16, so 20 - 16 = 4)
    assert fg.lifetime.remaining_number_of_years == 4

    # Outstanding debt should be scaled to 4/20 = 0.2 of total debt
    total_debt = fg.total_investment * (1 - fg.equity_share)
    expected_outstanding = total_debt * (4 / 20)
    assert abs(fg.outstanding_debt - expected_outstanding) < 0.01


def test_plant_at_renovation_boundary_has_no_debt():
    """Test that a plant at renovation boundary has no outstanding debt."""
    # Plant exactly 100 years old (at renovation boundary)
    fg = create_test_furnace_group(start_year=1925)

    assert fg.lifetime.remaining_number_of_years == 0
    assert fg.outstanding_debt == 0.0


def test_new_plant_full_debt():
    """Test that a new plant in first year has nearly full debt."""
    # Plant just started this year
    fg = create_test_furnace_group(start_year=2025)

    # Should have 20 years remaining
    assert fg.lifetime.remaining_number_of_years == 20

    # Outstanding debt should be full debt amount
    total_debt = fg.total_investment * (1 - fg.equity_share)
    assert abs(fg.outstanding_debt - total_debt) < 0.01


@pytest.mark.parametrize(
    "start_year,expected_remaining,expected_debt_ratio",
    [
        (2025, 20, 1.0),  # Brand new plant - full debt
        (2020, 15, 0.75),  # 5 years old - 75% debt remaining
        (2015, 10, 0.5),  # 10 years old - 50% debt remaining
        (2010, 5, 0.25),  # 15 years old - 25% debt remaining
        (2005, 0, 0.0),  # 20 years old - at renovation, no debt
        (2004, 19, 0.95),  # 21 years old - new cycle, 95% debt
        (1909, 4, 0.2),  # 116 years old - 20% debt remaining
    ],
)
def test_debt_scaling_various_ages(start_year, expected_remaining, expected_debt_ratio):
    """Test debt scaling for plants of various ages."""
    fg = create_test_furnace_group(start_year=start_year)

    assert fg.lifetime.remaining_number_of_years == expected_remaining

    # Calculate expected outstanding debt
    total_debt = fg.total_investment * (1 - fg.equity_share)
    expected_outstanding = total_debt * expected_debt_ratio

    # Allow small floating point differences
    assert abs(fg.outstanding_debt - expected_outstanding) < 1.0, (
        f"Plant from {start_year}: expected debt ratio {expected_debt_ratio}, "
        f"got {fg.outstanding_debt / total_debt if total_debt > 0 else 0}"
    )
