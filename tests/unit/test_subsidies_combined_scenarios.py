"""Tests for combined subsidy scenarios across CAPEX, OPEX, and debt."""

from steelo.domain import calculate_costs
from steelo.domain.models import Subsidy, Year


def test_multiple_absolute_subsidies_same_year():
    """Test that multiple absolute subsidies stack additively."""
    # Arrange
    opex = 400.0
    opex_subsidies = [
        Subsidy(
            scenario_name="federal_grant",
            iso3="USA",
            technology_name="DRI+EAF",
            cost_item="OPEX",
            subsidy_type="absolute",
            subsidy_amount=50.0,
            start_year=Year(2025),
            end_year=Year(2030),
        ),
        Subsidy(
            scenario_name="state_grant",
            iso3="USA",
            technology_name="DRI+EAF",
            cost_item="OPEX",
            subsidy_type="absolute",
            subsidy_amount=30.0,
            start_year=Year(2025),
            end_year=Year(2030),
        ),
        Subsidy(
            scenario_name="local_grant",
            iso3="USA",
            technology_name="DRI+EAF",
            cost_item="OPEX",
            subsidy_type="absolute",
            subsidy_amount=20.0,
            start_year=Year(2025),
            end_year=Year(2030),
        ),
    ]

    # Act
    result = calculate_costs.calculate_opex_with_subsidies(opex, opex_subsidies)

    # Assert - all absolute subsidies sum: 50 + 30 + 20 = 100
    # Result: 400 - 100 = 300
    assert result == 300.0


def test_multiple_relative_subsidies_same_year():
    """Test that multiple relative subsidies stack additively (based on original cost)."""
    # Arrange
    capex = 1000.0
    capex_subsidies = [
        Subsidy(
            scenario_name="federal_tax_credit",
            iso3="USA",
            technology_name="DRI+EAF",
            cost_item="CAPEX",
            subsidy_type="relative",
            subsidy_amount=0.10,  # 10%
            start_year=Year(2025),
            end_year=Year(2030),
        ),
        Subsidy(
            scenario_name="state_tax_credit",
            iso3="USA",
            technology_name="DRI+EAF",
            cost_item="CAPEX",
            subsidy_type="relative",
            subsidy_amount=0.05,  # 5%
            start_year=Year(2025),
            end_year=Year(2030),
        ),
        Subsidy(
            scenario_name="green_bond_discount",
            iso3="USA",
            technology_name="DRI+EAF",
            cost_item="CAPEX",
            subsidy_type="relative",
            subsidy_amount=0.08,  # 8%
            start_year=Year(2025),
            end_year=Year(2030),
        ),
    ]

    # Act
    result = calculate_costs.calculate_capex_with_subsidies(capex, capex_subsidies)

    # Assert - all relative subsidies sum: (10% + 5% + 8%) * 1000 = 230
    # Result: 1000 - 230 = 770
    assert result == 770.0


def test_mixed_subsidies_order_independence():
    """Test that subsidy order doesn't affect the final result."""
    # Arrange
    opex = 500.0

    # Order 1: absolute first, then relative
    subsidies_order1 = [
        Subsidy(
            scenario_name="absolute_first",
            iso3="USA",
            technology_name="EAF",
            cost_item="OPEX",
            subsidy_type="absolute",
            subsidy_amount=100.0,
            start_year=Year(2025),
            end_year=Year(2030),
        ),
        Subsidy(
            scenario_name="relative_second",
            iso3="USA",
            technology_name="EAF",
            cost_item="OPEX",
            subsidy_type="relative",
            subsidy_amount=0.2,  # 20%
            start_year=Year(2025),
            end_year=Year(2030),
        ),
    ]

    # Order 2: relative first, then absolute
    subsidies_order2 = [
        Subsidy(
            scenario_name="relative_first",
            iso3="USA",
            technology_name="EAF",
            cost_item="OPEX",
            subsidy_type="relative",
            subsidy_amount=0.2,  # 20%
            start_year=Year(2025),
            end_year=Year(2030),
        ),
        Subsidy(
            scenario_name="absolute_second",
            iso3="USA",
            technology_name="EAF",
            cost_item="OPEX",
            subsidy_type="absolute",
            subsidy_amount=100.0,
            start_year=Year(2025),
            end_year=Year(2030),
        ),
    ]

    # Act
    result_order1 = calculate_costs.calculate_opex_with_subsidies(opex, subsidies_order1)
    result_order2 = calculate_costs.calculate_opex_with_subsidies(opex, subsidies_order2)

    # Assert - both orders should produce same result
    # Total subsidy: 100 + (500 * 0.2) = 100 + 100 = 200
    # Result: 500 - 200 = 300
    assert result_order1 == result_order2 == 300.0


def test_subsidy_amount_zero():
    """Test that zero-amount subsidies have no effect."""
    # Arrange
    capex = 600.0
    capex_subsidies = [
        Subsidy(
            scenario_name="zero_absolute",
            iso3="USA",
            technology_name="BOF",
            cost_item="CAPEX",
            subsidy_type="absolute",
            subsidy_amount=0.0,
            start_year=Year(2025),
            end_year=Year(2030),
        ),
        Subsidy(
            scenario_name="zero_relative",
            iso3="USA",
            technology_name="BOF",
            cost_item="CAPEX",
            subsidy_type="relative",
            subsidy_amount=0.0,  # 0%
            start_year=Year(2025),
            end_year=Year(2030),
        ),
    ]

    # Act
    result = calculate_costs.calculate_capex_with_subsidies(capex, capex_subsidies)

    # Assert - zero subsidies should not change the capex
    assert result == 600.0


def test_large_relative_subsidy_floors_at_zero():
    """Test that a relative subsidy > 100% still floors at zero."""
    # Arrange
    opex = 200.0
    opex_subsidies = [
        Subsidy(
            scenario_name="very_large_relative",
            iso3="USA",
            technology_name="DRI",
            cost_item="OPEX",
            subsidy_type="relative",
            subsidy_amount=1.5,  # 150%
            start_year=Year(2025),
            end_year=Year(2030),
        )
    ]

    # Act
    result = calculate_costs.calculate_opex_with_subsidies(opex, opex_subsidies)

    # Assert - 150% of 200 = 300, so 200 - 300 = -100, but floors at 0
    assert result == 0.0


def test_negative_subsidy_amount_increases_cost():
    """Test that negative subsidy amounts increase the cost (acts as a tax/penalty)."""
    # Arrange
    capex = 500.0
    capex_subsidies = [
        Subsidy(
            scenario_name="carbon_penalty",
            iso3="USA",
            technology_name="BF-BOF",
            cost_item="CAPEX",
            subsidy_type="absolute",
            subsidy_amount=-100.0,  # Negative = cost increase
            start_year=Year(2025),
            end_year=Year(2030),
        )
    ]

    # Act
    result = calculate_costs.calculate_capex_with_subsidies(capex, capex_subsidies)

    # Assert - negative subsidy increases cost: 500 - (-100) = 600
    assert result == 600.0


def test_negative_relative_subsidy_increases_cost():
    """Test that negative relative subsidy amounts increase the cost proportionally."""
    # Arrange
    opex = 400.0
    opex_subsidies = [
        Subsidy(
            scenario_name="environmental_surcharge",
            iso3="USA",
            technology_name="BF-BOF",
            cost_item="OPEX",
            subsidy_type="relative",
            subsidy_amount=-0.25,  # -25% = 25% cost increase
            start_year=Year(2025),
            end_year=Year(2030),
        )
    ]

    # Act
    result = calculate_costs.calculate_opex_with_subsidies(opex, opex_subsidies)

    # Assert - negative relative: 400 - (400 * -0.25) = 400 - (-100) = 500
    assert result == 500.0
