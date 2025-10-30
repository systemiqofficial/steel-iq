"""Test renovation cycle calculations for old plants."""

import pytest
from steelo.domain.models import PointInTime, TimeFrame, Year


def test_old_plant_renovation_cycle():
    """Test that a plant from 1909 (116 years old in 2025) is 16 years into current cycle."""
    old_plant = PointInTime(
        current=Year(2025),
        time_frame=TimeFrame(start=Year(1909), end=Year(1959)),
        plant_lifetime=20,
    )

    assert old_plant.elapsed_number_of_years == 16
    assert old_plant.remaining_number_of_years == 4
    assert not old_plant.expired


def test_plant_at_renovation_boundary():
    """Test that a plant exactly at renovation boundary (100 years old) needs renovation."""
    boundary_plant = PointInTime(
        current=Year(2025),
        time_frame=TimeFrame(start=Year(1925), end=Year(1975)),
        plant_lifetime=20,
    )

    assert boundary_plant.elapsed_number_of_years == 20
    assert boundary_plant.remaining_number_of_years == 0
    assert boundary_plant.expired


def test_plant_in_first_cycle():
    """Test that a plant in its first cycle (10 years old) works correctly."""
    new_plant = PointInTime(
        current=Year(2025),
        time_frame=TimeFrame(start=Year(2015), end=Year(2035)),
        plant_lifetime=20,
    )

    assert new_plant.elapsed_number_of_years == 10
    assert new_plant.remaining_number_of_years == 10
    assert not new_plant.expired


def test_plant_starting_new_cycle():
    """Test that a plant 41 years old (1 year into 3rd cycle) works correctly."""
    mid_aged_plant = PointInTime(
        current=Year(2025),
        time_frame=TimeFrame(start=Year(1984), end=Year(2034)),
        plant_lifetime=20,
    )

    assert mid_aged_plant.elapsed_number_of_years == 1
    assert mid_aged_plant.remaining_number_of_years == 19
    assert not mid_aged_plant.expired


@pytest.mark.parametrize(
    "start_year,expected_elapsed,expected_remaining,should_expire",
    [
        (1909, 16, 4, False),  # 116 years old
        (1925, 20, 0, True),  # 100 years old (exactly at boundary)
        (2015, 10, 10, False),  # 10 years old (first cycle)
        (1984, 1, 19, False),  # 41 years old (just started 3rd cycle)
        (2005, 20, 0, True),  # 20 years old (exactly at first renovation)
        (2004, 1, 19, False),  # 21 years old (1 year into 2nd cycle)
        (1945, 20, 0, True),  # 80 years old (exactly at 4th renovation)
        (1946, 19, 1, False),  # 79 years old (1 year before 4th renovation)
    ],
)
def test_renovation_cycles_various_ages(start_year, expected_elapsed, expected_remaining, should_expire):
    """Test renovation cycle calculations for plants of various ages."""
    plant = PointInTime(
        current=Year(2025),
        time_frame=TimeFrame(start=Year(start_year), end=Year(start_year + 50)),
        plant_lifetime=20,
    )

    assert plant.elapsed_number_of_years == expected_elapsed, (
        f"Plant from {start_year}: expected {expected_elapsed} elapsed, got {plant.elapsed_number_of_years}"
    )
    assert plant.remaining_number_of_years == expected_remaining, (
        f"Plant from {start_year}: expected {expected_remaining} remaining, got {plant.remaining_number_of_years}"
    )
    assert plant.expired == should_expire, (
        f"Plant from {start_year}: expected expired={should_expire}, got {plant.expired}"
    )


def test_negative_remaining_lifetime_fixed():
    """Verify that the bug with negative remaining lifetime is fixed."""
    # This was the problematic case: plant from 1909 showing -66 years remaining
    very_old_plant = PointInTime(
        current=Year(2025),
        time_frame=TimeFrame(start=Year(1909), end=Year(1959)),
        plant_lifetime=20,
    )

    # Should never be negative
    assert very_old_plant.remaining_number_of_years >= 0
    # Should be 4 years remaining in current cycle
    assert very_old_plant.remaining_number_of_years == 4


def test_future_plant():
    """Test that a plant that hasn't started yet works correctly."""
    future_plant = PointInTime(
        current=Year(2025),
        time_frame=TimeFrame(start=Year(2030), end=Year(2050)),
        plant_lifetime=20,
    )

    assert future_plant.elapsed_number_of_years == 0
    assert future_plant.remaining_number_of_years == 20  # Full cycle
    assert not future_plant.expired
