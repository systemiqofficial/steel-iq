"""Tests for calculate_opex_with_subsidies and calculate_opex_list_with_subsidies functions."""

import pytest
from steelo.domain import calculate_costs
from steelo.domain.models import Subsidy, Year


def test_calculate_opex_with_subsidies_no_subsidies():
    """Test that calculate_opex_with_subsidies returns original opex when no subsidies."""
    # Arrange
    opex = 350.0
    opex_subsidies = []

    # Act
    result = calculate_costs.calculate_opex_with_subsidies(opex, opex_subsidies)

    # Assert
    assert result == 350.0


def test_calculate_opex_with_subsidies_single_absolute():
    """Test calculate_opex_with_subsidies with single absolute subsidy."""
    # Arrange
    opex = 350.0
    opex_subsidies = [
        Subsidy(
            scenario_name="test_scenario",
            iso3="USA",
            technology_name="DRI+EAF",
            cost_item="OPEX",
            absolute_subsidy=50.0,
            relative_subsidy=0.0,
            start_year=Year(2025),
            end_year=Year(2030),
        )
    ]

    # Act
    result = calculate_costs.calculate_opex_with_subsidies(opex, opex_subsidies)

    # Assert - opex minus absolute subsidy
    assert result == 300.0


def test_calculate_opex_with_subsidies_single_relative():
    """Test calculate_opex_with_subsidies with single relative subsidy."""
    # Arrange
    opex = 350.0
    opex_subsidies = [
        Subsidy(
            scenario_name="test_scenario",
            iso3="USA",
            technology_name="DRI+EAF",
            cost_item="OPEX",
            absolute_subsidy=0.0,
            relative_subsidy=0.1,  # 10%
            start_year=Year(2025),
            end_year=Year(2030),
        )
    ]

    # Act
    result = calculate_costs.calculate_opex_with_subsidies(opex, opex_subsidies)

    # Assert - opex minus 10% of opex
    assert result == 315.0  # 350 - (350 * 0.1) = 315


def test_calculate_opex_with_subsidies_combined():
    """Test calculate_opex_with_subsidies with both absolute and relative subsidies."""
    # Arrange
    opex = 350.0
    opex_subsidies = [
        Subsidy(
            scenario_name="test_scenario",
            iso3="USA",
            technology_name="DRI+EAF",
            cost_item="OPEX",
            absolute_subsidy=50.0,
            relative_subsidy=0.1,  # 10%
            start_year=Year(2025),
            end_year=Year(2030),
        )
    ]

    # Act
    result = calculate_costs.calculate_opex_with_subsidies(opex, opex_subsidies)

    # Assert - opex minus (absolute + relative * opex)
    # 350 - (50 + 350 * 0.1) = 350 - (50 + 35) = 265
    assert result == 265.0


def test_calculate_opex_with_subsidies_multiple():
    """Test calculate_opex_with_subsidies with multiple subsidies."""
    # Arrange
    opex = 350.0
    opex_subsidies = [
        Subsidy(
            scenario_name="test_scenario_1",
            iso3="USA",
            technology_name="DRI+EAF",
            cost_item="OPEX",
            absolute_subsidy=25.0,
            relative_subsidy=0.05,  # 5%
            start_year=Year(2025),
            end_year=Year(2030),
        ),
        Subsidy(
            scenario_name="test_scenario_2",
            iso3="USA",
            technology_name="DRI+EAF",
            cost_item="OPEX",
            absolute_subsidy=30.0,
            relative_subsidy=0.1,  # 10%
            start_year=Year(2025),
            end_year=Year(2030),
        ),
    ]

    # Act
    result = calculate_costs.calculate_opex_with_subsidies(opex, opex_subsidies)

    # Assert - opex minus sum of all subsidies
    # First subsidy: 25 + 350 * 0.05 = 25 + 17.5 = 42.5
    # Second subsidy: 30 + 350 * 0.1 = 30 + 35 = 65
    # Total subsidy: 42.5 + 65 = 107.5
    # Result: 350 - 107.5 = 242.5
    assert result == 242.5


def test_calculate_opex_with_subsidies_floor_zero():
    """Test that calculate_opex_with_subsidies floors at zero (no negative opex)."""
    # Arrange
    opex = 100.0
    opex_subsidies = [
        Subsidy(
            scenario_name="test_scenario",
            iso3="USA",
            technology_name="DRI+EAF",
            cost_item="OPEX",
            absolute_subsidy=200.0,  # More than opex
            relative_subsidy=0.0,
            start_year=Year(2025),
            end_year=Year(2030),
        )
    ]

    # Act
    result = calculate_costs.calculate_opex_with_subsidies(opex, opex_subsidies)

    # Assert - should floor at 0
    assert result == 0.0


def test_calculate_opex_list_with_subsidies_no_subsidies():
    """Test calculate_opex_list_with_subsidies with no subsidies."""
    # Arrange
    opex = 350.0
    opex_subsidies = []
    start_year = Year(2025)
    end_year = Year(2030)  # 5 years

    # Act
    result = calculate_costs.calculate_opex_list_with_subsidies(opex, opex_subsidies, start_year, end_year)

    # Assert - returns original opex for each year (no subsidies applied)
    assert len(result) == 5
    assert all(s == 350.0 for s in result)


def test_calculate_opex_list_with_subsidies_partial_period():
    """Test calculate_opex_list_with_subsidies with subsidy for partial period."""
    # Arrange
    opex = 350.0
    opex_subsidies = [
        Subsidy(
            scenario_name="test_scenario",
            iso3="USA",
            technology_name="DRI+EAF",
            cost_item="OPEX",
            absolute_subsidy=50.0,
            relative_subsidy=0.1,
            start_year=Year(2026),  # Starts year 2
            end_year=Year(2028),  # Ends year 4
        )
    ]
    start_year = Year(2025)
    end_year = Year(2030)  # 5 years total

    # Act
    result = calculate_costs.calculate_opex_list_with_subsidies(opex, opex_subsidies, start_year, end_year)

    # Assert
    # Year 2025: no subsidy (before start) - returns original opex
    # Years 2026-2028: subsidy = 50 + 350 * 0.1 = 85, so opex becomes 350 - 85 = 265
    # Year 2029: no subsidy (after end) - returns original opex
    expected = [350.0, 265.0, 265.0, 265.0, 350.0]
    assert len(result) == 5
    for i, (actual, exp) in enumerate(zip(result, expected)):
        assert actual == pytest.approx(exp), f"Year {i}: expected {exp}, got {actual}"


def test_calculate_opex_list_with_subsidies_overlapping():
    """Test calculate_opex_list_with_subsidies with overlapping subsidies."""
    # Arrange
    opex = 300.0
    opex_subsidies = [
        Subsidy(
            scenario_name="test_scenario_1",
            iso3="USA",
            technology_name="DRI+EAF",
            cost_item="OPEX",
            absolute_subsidy=20.0,
            relative_subsidy=0.1,
            start_year=Year(2025),
            end_year=Year(2027),
        ),
        Subsidy(
            scenario_name="test_scenario_2",
            iso3="USA",
            technology_name="DRI-EAF",
            cost_item="OPEX",
            absolute_subsidy=30.0,
            relative_subsidy=0.05,
            start_year=Year(2026),
            end_year=Year(2028),
        ),
    ]
    start_year = Year(2025)
    end_year = Year(2030)  # 5 years total

    # Act
    result = calculate_costs.calculate_opex_list_with_subsidies(opex, opex_subsidies, start_year, end_year)

    # Assert
    # Subsidy 1: 20 + 300 * 0.1 = 50
    # Subsidy 2: 30 + 300 * 0.05 = 45
    # Year 2025: only subsidy 1, opex = 300 - 50 = 250
    # Years 2026-2027: both subsidies, opex = 300 - 95 = 205
    # Year 2028: only subsidy 2, opex = 300 - 45 = 255
    # Year 2029: no subsidies, opex = 300
    expected = [250.0, 205.0, 205.0, 255.0, 300.0]
    assert len(result) == 5
    for i, (actual, exp) in enumerate(zip(result, expected)):
        assert actual == pytest.approx(exp), f"Year {i}: expected {exp}, got {actual}"


def test_calculate_opex_list_with_subsidies_full_period():
    """Test calculate_opex_list_with_subsidies with subsidy for full period."""
    # Arrange
    opex = 400.0
    opex_subsidies = [
        Subsidy(
            scenario_name="test_scenario",
            iso3="USA",
            technology_name="DRI+EAF",
            cost_item="OPEX",
            absolute_subsidy=100.0,
            relative_subsidy=0.2,
            start_year=Year(2020),  # Before start
            end_year=Year(2035),  # After end
        )
    ]
    start_year = Year(2025)
    end_year = Year(2030)  # 5 years total

    # Act
    result = calculate_costs.calculate_opex_list_with_subsidies(opex, opex_subsidies, start_year, end_year)

    # Assert - same subsidy for all years
    # Subsidy = 100 + 400 * 0.2 = 100 + 80 = 180
    # Opex with subsidy = 400 - 180 = 220
    expected = [220.0] * 5
    assert len(result) == 5
    for i, (actual, exp) in enumerate(zip(result, expected)):
        assert actual == pytest.approx(exp), f"Year {i}: expected {exp}, got {actual}"
