"""Tests for collect_active_subsidies_over_period function."""

from steelo.domain import calculate_costs
from steelo.domain.models import Subsidy, Year


def test_collect_active_subsidies_over_period_empty_list():
    """Test that empty subsidies list returns empty list."""
    result = calculate_costs.collect_active_subsidies_over_period([], start_year=Year(2025), end_year=Year(2030))
    assert result == []


def test_collect_active_subsidies_over_period_single_subsidy_full_period():
    """Test subsidy spanning full period is returned once (deduplicated)."""
    subsidy = Subsidy(
        scenario_name="test",
        iso3="USA",
        start_year=Year(2020),
        end_year=Year(2040),
        technology_name="DRI+EAF",
        cost_item="opex",
        subsidy_type="absolute",
        subsidy_amount=50.0,
    )
    result = calculate_costs.collect_active_subsidies_over_period([subsidy], start_year=Year(2025), end_year=Year(2030))
    # Should return the subsidy only once, not multiple times
    assert len(result) == 1
    assert subsidy in result


def test_collect_active_subsidies_over_period_subsidy_partial_overlap():
    """Test subsidy active for only part of the period is still returned."""
    subsidy = Subsidy(
        scenario_name="partial",
        iso3="USA",
        start_year=Year(2027),
        end_year=Year(2028),
        technology_name="DRI+EAF",
        cost_item="opex",
        subsidy_type="absolute",
        subsidy_amount=50.0,
    )
    result = calculate_costs.collect_active_subsidies_over_period([subsidy], start_year=Year(2025), end_year=Year(2030))
    assert len(result) == 1
    assert subsidy in result


def test_collect_active_subsidies_over_period_subsidy_outside_period():
    """Test subsidy outside period entirely is not returned."""
    subsidy = Subsidy(
        scenario_name="outside",
        iso3="USA",
        start_year=Year(2035),
        end_year=Year(2040),
        technology_name="DRI+EAF",
        cost_item="opex",
        subsidy_type="absolute",
        subsidy_amount=50.0,
    )
    result = calculate_costs.collect_active_subsidies_over_period([subsidy], start_year=Year(2025), end_year=Year(2030))
    assert result == []


def test_collect_active_subsidies_over_period_multiple_unique_subsidies():
    """Test multiple different subsidies are all returned."""
    subsidy1 = Subsidy(
        scenario_name="sub1",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2027),
        technology_name="DRI+EAF",
        cost_item="opex",
        subsidy_type="absolute",
        subsidy_amount=50.0,
    )
    subsidy2 = Subsidy(
        scenario_name="sub2",
        iso3="DEU",
        start_year=Year(2028),
        end_year=Year(2030),
        technology_name="BF-BOF",
        cost_item="opex",
        subsidy_type="relative",
        subsidy_amount=0.1,
    )
    result = calculate_costs.collect_active_subsidies_over_period(
        [subsidy1, subsidy2], start_year=Year(2025), end_year=Year(2031)
    )
    assert len(result) == 2
    assert subsidy1 in result
    assert subsidy2 in result


def test_collect_active_subsidies_over_period_overlapping_subsidies():
    """Test multiple subsidies with overlapping periods are all returned uniquely."""
    subsidy1 = Subsidy(
        scenario_name="sub1",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="DRI+EAF",
        cost_item="opex",
        subsidy_type="absolute",
        subsidy_amount=50.0,
    )
    subsidy2 = Subsidy(
        scenario_name="sub2",
        iso3="USA",
        start_year=Year(2027),
        end_year=Year(2032),
        technology_name="DRI+EAF",
        cost_item="opex",
        subsidy_type="absolute",
        subsidy_amount=30.0,
    )
    result = calculate_costs.collect_active_subsidies_over_period(
        [subsidy1, subsidy2], start_year=Year(2025), end_year=Year(2035)
    )
    # Both subsidies should be in result, deduplicated
    assert len(result) == 2
    assert subsidy1 in result
    assert subsidy2 in result


def test_collect_active_subsidies_over_period_deduplication():
    """Test that same subsidy object is not duplicated when active across multiple years."""
    # This is the key behavior: a subsidy spanning 2025-2030 should only appear once
    # even when collecting over years 2025, 2026, 2027, 2028, 2029
    subsidy = Subsidy(
        scenario_name="multi_year",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="DRI+EAF",
        cost_item="opex",
        subsidy_type="absolute",
        subsidy_amount=50.0,
    )
    result = calculate_costs.collect_active_subsidies_over_period([subsidy], start_year=Year(2025), end_year=Year(2030))
    # Should only have one entry, not 5 (one per year)
    assert len(result) == 1
    assert result[0] == subsidy


def test_collect_active_subsidies_over_period_end_year_exclusive():
    """Test that end_year is exclusive (matches Python range convention)."""
    # Subsidy is only active in 2030
    subsidy = Subsidy(
        scenario_name="edge",
        iso3="USA",
        start_year=Year(2030),
        end_year=Year(2030),
        technology_name="DRI+EAF",
        cost_item="opex",
        subsidy_type="absolute",
        subsidy_amount=50.0,
    )
    # Period [2025, 2030) should NOT include 2030
    result = calculate_costs.collect_active_subsidies_over_period([subsidy], start_year=Year(2025), end_year=Year(2030))
    assert result == []

    # Period [2025, 2031) should include 2030
    result = calculate_costs.collect_active_subsidies_over_period([subsidy], start_year=Year(2025), end_year=Year(2031))
    assert len(result) == 1
    assert subsidy in result


def test_collect_active_subsidies_over_period_start_year_inclusive():
    """Test that start_year is inclusive."""
    subsidy = Subsidy(
        scenario_name="start_edge",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2025),
        technology_name="DRI+EAF",
        cost_item="opex",
        subsidy_type="absolute",
        subsidy_amount=50.0,
    )
    # Period [2025, 2030) should include 2025
    result = calculate_costs.collect_active_subsidies_over_period([subsidy], start_year=Year(2025), end_year=Year(2030))
    assert len(result) == 1
    assert subsidy in result


def test_collect_active_subsidies_over_period_mixed_active_inactive():
    """Test with mix of active and inactive subsidies."""
    active1 = Subsidy(
        scenario_name="active1",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="DRI+EAF",
        cost_item="opex",
        subsidy_type="absolute",
        subsidy_amount=50.0,
    )
    active2 = Subsidy(
        scenario_name="active2",
        iso3="DEU",
        start_year=Year(2028),
        end_year=Year(2035),
        technology_name="BF-BOF",
        cost_item="opex",
        subsidy_type="relative",
        subsidy_amount=0.1,
    )
    inactive = Subsidy(
        scenario_name="inactive",
        iso3="CHN",
        start_year=Year(2040),
        end_year=Year(2050),
        technology_name="H2-DRI",
        cost_item="opex",
        subsidy_type="absolute",
        subsidy_amount=100.0,
    )
    result = calculate_costs.collect_active_subsidies_over_period(
        [active1, active2, inactive], start_year=Year(2025), end_year=Year(2032)
    )
    assert len(result) == 2
    assert active1 in result
    assert active2 in result
    assert inactive not in result


def test_collect_active_subsidies_over_period_subsidy_equality():
    """Test that subsidies differing by any attribute remain distinct."""
    # Two subsidies that differ only by subsidy_amount
    subsidy1 = Subsidy(
        scenario_name="test",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="DRI+EAF",
        cost_item="opex",
        subsidy_type="absolute",
        subsidy_amount=50.0,
    )
    subsidy2 = Subsidy(
        scenario_name="test",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="DRI+EAF",
        cost_item="opex",
        subsidy_type="absolute",
        subsidy_amount=100.0,  # Different amount
    )
    result = calculate_costs.collect_active_subsidies_over_period(
        [subsidy1, subsidy2], start_year=Year(2025), end_year=Year(2030)
    )
    # Both should be in result since they differ by subsidy_amount
    assert len(result) == 2
