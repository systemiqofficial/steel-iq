"""Tests for filter_subsidies_for_year function."""

from steelo.domain import calculate_costs
from steelo.domain.models import Subsidy, Year


def test_filter_subsidies_for_year_empty_list():
    """Test that empty subsidies list returns empty list."""
    result = calculate_costs.filter_subsidies_for_year([], Year(2025))
    assert result == []


def test_filter_subsidies_for_year_subsidy_within_range():
    """Test that subsidy within year range is returned."""
    subsidy = Subsidy(
        scenario_name="test",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="DRI+EAF",
        cost_item="opex",
        subsidy_type="absolute",
        subsidy_amount=50.0,
    )
    result = calculate_costs.filter_subsidies_for_year([subsidy], Year(2027))
    assert result == [subsidy]


def test_filter_subsidies_for_year_subsidy_outside_range():
    """Test that subsidy outside year range is not returned."""
    subsidy = Subsidy(
        scenario_name="test",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="DRI+EAF",
        cost_item="opex",
        subsidy_type="absolute",
        subsidy_amount=50.0,
    )
    result = calculate_costs.filter_subsidies_for_year([subsidy], Year(2035))
    assert result == []


def test_filter_subsidies_for_year_at_start_boundary():
    """Test that subsidy is active at start_year boundary (inclusive)."""
    subsidy = Subsidy(
        scenario_name="test",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="DRI+EAF",
        cost_item="opex",
        subsidy_type="absolute",
        subsidy_amount=50.0,
    )
    result = calculate_costs.filter_subsidies_for_year([subsidy], Year(2025))
    assert result == [subsidy]


def test_filter_subsidies_for_year_at_end_boundary():
    """Test that subsidy is active at end_year boundary (inclusive)."""
    subsidy = Subsidy(
        scenario_name="test",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="DRI+EAF",
        cost_item="opex",
        subsidy_type="absolute",
        subsidy_amount=50.0,
    )
    result = calculate_costs.filter_subsidies_for_year([subsidy], Year(2030))
    assert result == [subsidy]


def test_filter_subsidies_for_year_before_start():
    """Test that subsidy is not active before start_year."""
    subsidy = Subsidy(
        scenario_name="test",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="DRI+EAF",
        cost_item="opex",
        subsidy_type="absolute",
        subsidy_amount=50.0,
    )
    result = calculate_costs.filter_subsidies_for_year([subsidy], Year(2024))
    assert result == []


def test_filter_subsidies_for_year_after_end():
    """Test that subsidy is not active after end_year."""
    subsidy = Subsidy(
        scenario_name="test",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="DRI+EAF",
        cost_item="opex",
        subsidy_type="absolute",
        subsidy_amount=50.0,
    )
    result = calculate_costs.filter_subsidies_for_year([subsidy], Year(2031))
    assert result == []


def test_filter_subsidies_for_year_multiple_subsidies_mixed():
    """Test filtering with multiple subsidies, some active, some not."""
    active_subsidy = Subsidy(
        scenario_name="active",
        iso3="USA",
        start_year=Year(2020),
        end_year=Year(2030),
        technology_name="DRI+EAF",
        cost_item="opex",
        subsidy_type="absolute",
        subsidy_amount=50.0,
    )
    inactive_subsidy = Subsidy(
        scenario_name="inactive",
        iso3="USA",
        start_year=Year(2035),
        end_year=Year(2040),
        technology_name="DRI+EAF",
        cost_item="opex",
        subsidy_type="absolute",
        subsidy_amount=100.0,
    )
    result = calculate_costs.filter_subsidies_for_year([active_subsidy, inactive_subsidy], Year(2025))
    assert result == [active_subsidy]


def test_filter_subsidies_for_year_all_active():
    """Test filtering when all subsidies are active."""
    subsidy1 = Subsidy(
        scenario_name="sub1",
        iso3="USA",
        start_year=Year(2020),
        end_year=Year(2030),
        technology_name="DRI+EAF",
        cost_item="opex",
        subsidy_type="absolute",
        subsidy_amount=50.0,
    )
    subsidy2 = Subsidy(
        scenario_name="sub2",
        iso3="DEU",
        start_year=Year(2025),
        end_year=Year(2035),
        technology_name="BF-BOF",
        cost_item="capex",
        subsidy_type="relative",
        subsidy_amount=0.1,
    )
    result = calculate_costs.filter_subsidies_for_year([subsidy1, subsidy2], Year(2027))
    assert len(result) == 2
    assert subsidy1 in result
    assert subsidy2 in result


def test_filter_subsidies_for_year_none_active():
    """Test filtering when no subsidies are active."""
    subsidy1 = Subsidy(
        scenario_name="sub1",
        iso3="USA",
        start_year=Year(2020),
        end_year=Year(2025),
        technology_name="DRI+EAF",
        cost_item="opex",
        subsidy_type="absolute",
        subsidy_amount=50.0,
    )
    subsidy2 = Subsidy(
        scenario_name="sub2",
        iso3="DEU",
        start_year=Year(2035),
        end_year=Year(2040),
        technology_name="BF-BOF",
        cost_item="capex",
        subsidy_type="relative",
        subsidy_amount=0.1,
    )
    result = calculate_costs.filter_subsidies_for_year([subsidy1, subsidy2], Year(2030))
    assert result == []


def test_filter_subsidies_for_year_single_year_subsidy():
    """Test subsidy that is only active for a single year."""
    subsidy = Subsidy(
        scenario_name="single_year",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2025),
        technology_name="DRI+EAF",
        cost_item="opex",
        subsidy_type="absolute",
        subsidy_amount=50.0,
    )
    # Active in 2025
    assert calculate_costs.filter_subsidies_for_year([subsidy], Year(2025)) == [subsidy]
    # Not active in 2024
    assert calculate_costs.filter_subsidies_for_year([subsidy], Year(2024)) == []
    # Not active in 2026
    assert calculate_costs.filter_subsidies_for_year([subsidy], Year(2026)) == []
