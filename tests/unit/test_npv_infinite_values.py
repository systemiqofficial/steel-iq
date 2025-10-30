"""Test for NPV calculation edge cases that can produce infinite values."""

import pytest
from steelo.domain.calculate_costs import calculate_npv_costs, calculate_npv_full


def test_npv_with_cost_of_debt_minus_one():
    """Test that NPV calculation with cost_of_debt = -1 returns large negative value."""
    net_cash_flow = [1000, 1000, 1000, 1000, 1000]
    cost_of_equity = -1.0  # This will cause (1 + cost_of_debt) = 0
    equity_share = 0.2
    total_investment = 10000

    result = calculate_npv_costs(
        net_cash_flow=net_cash_flow,
        cost_of_equity=cost_of_equity,
        equity_share=equity_share,
        total_investment=total_investment,
    )

    # Should return -1e9 to indicate unprofitability
    assert result == -1e9


def test_npv_with_cost_of_debt_less_than_minus_one():
    """Test that NPV calculation with cost_of_debt < -1 returns large negative value."""
    net_cash_flow = [1000, 1000, 1000, 1000, 1000]
    cost_equity = -1.5  # This will cause (1 + cost_of_debt) = -0.5
    equity_share = 0.2
    total_investment = 10000

    result = calculate_npv_costs(
        net_cash_flow=net_cash_flow,
        cost_of_equity=cost_equity,
        equity_share=equity_share,
        total_investment=total_investment,
    )

    # Should return -1e9 to indicate unprofitability
    assert result == -1e9


def test_random_choices_with_infinite_weights():
    """Test that random.choices fails with infinite weights."""
    import random

    # Simulate the scenario from the error
    tech_npv_dict = {
        "EAF": 1000.0,
        "BOF": float("inf"),  # Infinite NPV
        "DRI": 500.0,
    }

    weights = [max(v, 0) for v in tech_npv_dict.values()]

    # This should raise ValueError: Total of weights must be finite
    with pytest.raises(ValueError, match="Total of weights must be finite"):
        random.choices(population=list(tech_npv_dict.keys()), weights=weights, k=1)


def test_npv_full_with_extreme_values():
    """Test calculate_npv_full with edge case values."""
    # Calculate unit total opex from components
    unit_fopex = 50
    unit_vopex = 1.5 * 100 + 0.5 * 50  # materials + energy
    unit_total_opex = unit_fopex + unit_vopex
    unit_total_opex_list = [unit_total_opex] * 20  # lifetime only

    # Test with cost_of_debt = -1
    result = calculate_npv_full(
        capex=500,
        capacity=1000,
        unit_total_opex_list=unit_total_opex_list,
        expected_utilisation_rate=0.8,
        price_series=[600] * 22,  # Must be a list for lifetime + construction_time
        cost_of_debt=-1.0,  # This will cause (1 + cost_of_debt) = 0
        cost_of_equity=-1.0,
        lifetime=20,
        construction_time=2,
        equity_share=0.2,
        carbon_costs=None,
    )

    # Should return -1e9 due to invalid cost_of_debt
    assert result == -1e9
