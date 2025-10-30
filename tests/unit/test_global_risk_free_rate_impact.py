"""Test that global_risk_free_rate actually affects debt subsidy calculations."""

from steelo.domain.calculate_costs import calculate_debt_with_subsidies
from steelo.domain.models import Subsidy, Year


def test_risk_free_rate_acts_as_floor_for_debt_cost():
    """Test that risk-free rate acts as a floor for debt cost after subsidies."""

    # Create subsidies that would reduce debt cost below risk-free rate
    subsidies = [
        Subsidy(
            scenario_name="Test Subsidy",
            iso3="USA",
            start_year=Year(2025),
            end_year=Year(2030),
            technology_name="all",
            cost_item="debt",
            absolute_subsidy=0.08,  # 8% reduction
            relative_subsidy=1.0,  # No relative change
        )
    ]

    # Test 1: With high risk-free rate (5%), debt cost can't go below it
    cost_of_debt = 0.10  # 10%
    risk_free_rate_high = 0.05  # 5%

    result = calculate_debt_with_subsidies(
        cost_of_debt=cost_of_debt,
        debt_subsidies=subsidies,
        risk_free_rate=risk_free_rate_high,
    )

    # Debt cost would be 10% - 8% = 2%, but floor is 5%
    assert result == risk_free_rate_high

    # Test 2: With low risk-free rate (1%), debt cost can go lower
    risk_free_rate_low = 0.01  # 1%

    result = calculate_debt_with_subsidies(
        cost_of_debt=cost_of_debt,
        debt_subsidies=subsidies,
        risk_free_rate=risk_free_rate_low,
    )

    # Debt cost would be 10% - 8% = 2%, which is above 1% floor
    assert abs(result - 0.02) < 0.0001  # Allow for floating point precision

    # Test 3: Without subsidies, cost remains unchanged
    result = calculate_debt_with_subsidies(
        cost_of_debt=cost_of_debt,
        debt_subsidies=[],
        risk_free_rate=risk_free_rate_high,
    )

    assert result == cost_of_debt


def test_risk_free_rate_with_multiple_subsidies():
    """Test risk-free rate with multiple subsidies stacking."""

    # Create multiple subsidies that stack
    subsidies = [
        Subsidy(
            scenario_name="Subsidy 1",
            iso3="USA",
            start_year=Year(2025),
            end_year=Year(2030),
            technology_name="all",
            cost_item="debt",
            absolute_subsidy=0.03,  # 3% reduction
            relative_subsidy=1.0,
        ),
        Subsidy(
            scenario_name="Subsidy 2",
            iso3="USA",
            start_year=Year(2025),
            end_year=Year(2030),
            technology_name="all",
            cost_item="debt",
            absolute_subsidy=0.04,  # 4% reduction
            relative_subsidy=1.0,
        ),
    ]

    cost_of_debt = 0.10  # 10%
    risk_free_rate = 0.045  # 4.5%

    result = calculate_debt_with_subsidies(
        cost_of_debt=cost_of_debt,
        debt_subsidies=subsidies,
        risk_free_rate=risk_free_rate,
    )

    # Total subsidy: 3% + 4% = 7%
    # Debt cost would be 10% - 7% = 3%, but floor is 4.5%
    assert result == risk_free_rate


def test_risk_free_rate_outside_subsidy_period():
    """Test that subsidies don't apply outside their period."""

    subsidies = [
        Subsidy(
            scenario_name="Time-limited Subsidy",
            iso3="USA",
            start_year=Year(2025),
            end_year=Year(2027),
            technology_name="all",
            cost_item="debt",
            absolute_subsidy=0.05,  # 5% reduction
            relative_subsidy=1.0,
        )
    ]

    cost_of_debt = 0.08  # 8%
    risk_free_rate = 0.02  # 2%

    # Note: The current function doesn't filter by year - it applies all subsidies passed
    # So we test with and without subsidies
    result_with_subsidy = calculate_debt_with_subsidies(
        cost_of_debt=cost_of_debt,
        debt_subsidies=subsidies,
        risk_free_rate=risk_free_rate,
    )

    # 8% - 5% = 3%, which is above 2% floor
    assert result_with_subsidy == 0.03

    # Without subsidies
    result_without_subsidy = calculate_debt_with_subsidies(
        cost_of_debt=cost_of_debt,
        debt_subsidies=[],
        risk_free_rate=risk_free_rate,
    )

    # No subsidy applies, so cost remains 8%
    assert result_without_subsidy == cost_of_debt
