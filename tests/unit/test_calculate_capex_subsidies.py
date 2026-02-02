"""Tests for calculate_capex_with_subsidies function."""

from steelo.domain import calculate_costs
from steelo.domain.models import Subsidy, Year


def test_calculate_capex_with_subsidies_no_subsidies():
    """Test that calculate_capex_with_subsidies returns original capex when no subsidies."""
    # Arrange
    capex = 500.0
    capex_subsidies = []

    # Act
    result = calculate_costs.calculate_capex_with_subsidies(capex, capex_subsidies)

    # Assert
    assert result == 500.0


def test_calculate_capex_with_subsidies_single_absolute():
    """Test calculate_capex_with_subsidies with single absolute subsidy."""
    # Arrange
    capex = 500.0
    capex_subsidies = [
        Subsidy(
            scenario_name="test_scenario",
            iso3="USA",
            technology_name="DRI+EAF",
            cost_item="CAPEX",
            subsidy_type="absolute",
            subsidy_amount=100.0,
            start_year=Year(2025),
            end_year=Year(2030),
        )
    ]

    # Act
    result = calculate_costs.calculate_capex_with_subsidies(capex, capex_subsidies)

    # Assert - capex minus absolute subsidy
    assert result == 400.0


def test_calculate_capex_with_subsidies_single_relative():
    """Test calculate_capex_with_subsidies with single relative subsidy."""
    # Arrange
    capex = 500.0
    capex_subsidies = [
        Subsidy(
            scenario_name="test_scenario",
            iso3="USA",
            technology_name="DRI+EAF",
            cost_item="CAPEX",
            subsidy_type="relative",
            subsidy_amount=0.15,  # 15% (stored as decimal)
            start_year=Year(2025),
            end_year=Year(2030),
        )
    ]

    # Act
    result = calculate_costs.calculate_capex_with_subsidies(capex, capex_subsidies)

    # Assert - capex minus 15% of capex
    assert result == 425.0  # 500 - (500 * 0.15) = 425


def test_calculate_capex_with_subsidies_combined():
    """Test calculate_capex_with_subsidies with both absolute and relative subsidies."""
    # Arrange
    capex = 500.0
    capex_subsidies = [
        Subsidy(
            scenario_name="test_scenario_abs",
            iso3="USA",
            technology_name="DRI+EAF",
            cost_item="CAPEX",
            subsidy_type="absolute",
            subsidy_amount=50.0,
            start_year=Year(2025),
            end_year=Year(2030),
        ),
        Subsidy(
            scenario_name="test_scenario_rel",
            iso3="USA",
            technology_name="DRI+EAF",
            cost_item="CAPEX",
            subsidy_type="relative",
            subsidy_amount=0.1,  # 10% (stored as decimal)
            start_year=Year(2025),
            end_year=Year(2030),
        ),
    ]

    # Act
    result = calculate_costs.calculate_capex_with_subsidies(capex, capex_subsidies)

    # Assert - capex minus (absolute + relative * capex)
    # 500 - (50 + 500 * 0.1) = 500 - (50 + 50) = 400
    assert result == 400.0


def test_calculate_capex_with_subsidies_multiple():
    """Test calculate_capex_with_subsidies with multiple subsidies of each type."""
    # Arrange
    capex = 500.0
    capex_subsidies = [
        Subsidy(
            scenario_name="test_scenario_1_abs",
            iso3="USA",
            technology_name="DRI+EAF",
            cost_item="CAPEX",
            subsidy_type="absolute",
            subsidy_amount=30.0,
            start_year=Year(2025),
            end_year=Year(2030),
        ),
        Subsidy(
            scenario_name="test_scenario_1_rel",
            iso3="USA",
            technology_name="DRI+EAF",
            cost_item="CAPEX",
            subsidy_type="relative",
            subsidy_amount=0.05,  # 5% (stored as decimal)
            start_year=Year(2025),
            end_year=Year(2030),
        ),
        Subsidy(
            scenario_name="test_scenario_2_abs",
            iso3="USA",
            technology_name="DRI+EAF",
            cost_item="CAPEX",
            subsidy_type="absolute",
            subsidy_amount=20.0,
            start_year=Year(2025),
            end_year=Year(2030),
        ),
        Subsidy(
            scenario_name="test_scenario_2_rel",
            iso3="USA",
            technology_name="DRI+EAF",
            cost_item="CAPEX",
            subsidy_type="relative",
            subsidy_amount=0.1,  # 10% (stored as decimal)
            start_year=Year(2025),
            end_year=Year(2030),
        ),
    ]

    # Act
    result = calculate_costs.calculate_capex_with_subsidies(capex, capex_subsidies)

    # Assert - capex minus sum of all subsidies
    # Absolute subsidies: 30 + 20 = 50
    # Relative subsidies: 500 * 0.05 + 500 * 0.1 = 25 + 50 = 75
    # Total subsidy: 50 + 75 = 125
    # Result: 500 - 125 = 375
    assert result == 375.0


def test_calculate_capex_with_subsidies_floor_zero():
    """Test that calculate_capex_with_subsidies floors at zero (no negative capex)."""
    # Arrange
    capex = 100.0
    capex_subsidies = [
        Subsidy(
            scenario_name="test_scenario",
            iso3="USA",
            technology_name="DRI+EAF",
            cost_item="CAPEX",
            subsidy_type="absolute",
            subsidy_amount=200.0,  # More than capex
            start_year=Year(2025),
            end_year=Year(2030),
        )
    ]

    # Act
    result = calculate_costs.calculate_capex_with_subsidies(capex, capex_subsidies)

    # Assert - should floor at 0
    assert result == 0.0
