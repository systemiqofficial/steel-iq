"""Tests for calculate_opex_with_subsidies and calculate_opex_list_with_subsidies functions."""

from unittest.mock import patch

import pytest
from steelo import devdata
from steelo.domain import calculate_costs
from steelo.domain.models import PointInTime, Subsidy, TimeFrame, Year


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
            subsidy_type="absolute",
            subsidy_amount=50.0,
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
            subsidy_type="relative",
            subsidy_amount=0.1,  # 10% (stored as decimal)
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
            scenario_name="test_scenario_abs",
            iso3="USA",
            technology_name="DRI+EAF",
            cost_item="OPEX",
            subsidy_type="absolute",
            subsidy_amount=50.0,
            start_year=Year(2025),
            end_year=Year(2030),
        ),
        Subsidy(
            scenario_name="test_scenario_rel",
            iso3="USA",
            technology_name="DRI+EAF",
            cost_item="OPEX",
            subsidy_type="relative",
            subsidy_amount=0.1,  # 10% (stored as decimal)
            start_year=Year(2025),
            end_year=Year(2030),
        ),
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
            scenario_name="test_scenario_1_abs",
            iso3="USA",
            technology_name="DRI+EAF",
            cost_item="OPEX",
            subsidy_type="absolute",
            subsidy_amount=25.0,
            start_year=Year(2025),
            end_year=Year(2030),
        ),
        Subsidy(
            scenario_name="test_scenario_1_rel",
            iso3="USA",
            technology_name="DRI+EAF",
            cost_item="OPEX",
            subsidy_type="relative",
            subsidy_amount=0.05,  # 5% (stored as decimal)
            start_year=Year(2025),
            end_year=Year(2030),
        ),
        Subsidy(
            scenario_name="test_scenario_2_abs",
            iso3="USA",
            technology_name="DRI+EAF",
            cost_item="OPEX",
            subsidy_type="absolute",
            subsidy_amount=30.0,
            start_year=Year(2025),
            end_year=Year(2030),
        ),
        Subsidy(
            scenario_name="test_scenario_2_rel",
            iso3="USA",
            technology_name="DRI+EAF",
            cost_item="OPEX",
            subsidy_type="relative",
            subsidy_amount=0.1,  # 10% (stored as decimal)
            start_year=Year(2025),
            end_year=Year(2030),
        ),
    ]

    # Act
    result = calculate_costs.calculate_opex_with_subsidies(opex, opex_subsidies)

    # Assert - opex minus sum of all subsidies
    # Absolute subsidies: 25 + 30 = 55
    # Relative subsidies: 350 * 0.05 + 350 * 0.1 = 17.5 + 35 = 52.5
    # Total subsidy: 55 + 52.5 = 107.5
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
            subsidy_type="absolute",
            subsidy_amount=200.0,  # More than opex
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
            scenario_name="test_scenario_abs",
            iso3="USA",
            technology_name="DRI+EAF",
            cost_item="OPEX",
            subsidy_type="absolute",
            subsidy_amount=50.0,
            start_year=Year(2026),  # Starts year 2
            end_year=Year(2028),  # Ends year 4
        ),
        Subsidy(
            scenario_name="test_scenario_rel",
            iso3="USA",
            technology_name="DRI+EAF",
            cost_item="OPEX",
            subsidy_type="relative",
            subsidy_amount=0.1,  # 10% (stored as decimal)
            start_year=Year(2026),  # Starts year 2
            end_year=Year(2028),  # Ends year 4
        ),
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
            scenario_name="test_scenario_1_abs",
            iso3="USA",
            technology_name="DRI+EAF",
            cost_item="OPEX",
            subsidy_type="absolute",
            subsidy_amount=20.0,
            start_year=Year(2025),
            end_year=Year(2027),
        ),
        Subsidy(
            scenario_name="test_scenario_1_rel",
            iso3="USA",
            technology_name="DRI+EAF",
            cost_item="OPEX",
            subsidy_type="relative",
            subsidy_amount=0.1,  # 10% (stored as decimal)
            start_year=Year(2025),
            end_year=Year(2027),
        ),
        Subsidy(
            scenario_name="test_scenario_2_abs",
            iso3="USA",
            technology_name="DRI-EAF",
            cost_item="OPEX",
            subsidy_type="absolute",
            subsidy_amount=30.0,
            start_year=Year(2026),
            end_year=Year(2028),
        ),
        Subsidy(
            scenario_name="test_scenario_2_rel",
            iso3="USA",
            technology_name="DRI-EAF",
            cost_item="OPEX",
            subsidy_type="relative",
            subsidy_amount=0.05,  # 5% (stored as decimal)
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
            scenario_name="test_scenario_abs",
            iso3="USA",
            technology_name="DRI+EAF",
            cost_item="OPEX",
            subsidy_type="absolute",
            subsidy_amount=100.0,
            start_year=Year(2020),  # Before start
            end_year=Year(2035),  # After end
        ),
        Subsidy(
            scenario_name="test_scenario_rel",
            iso3="USA",
            technology_name="DRI+EAF",
            cost_item="OPEX",
            subsidy_type="relative",
            subsidy_amount=0.2,  # 20% (stored as decimal)
            start_year=Year(2020),  # Before start
            end_year=Year(2035),  # After end
        ),
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


def test_furnace_group_opex_subsidy_window_ends_at_lifetime_end():
    """Test that the current technology's OPEX subsidies are collected over the furnace group's lifetime.

    The collection window must end at the furnace group's absolute end year. Adding that year to the
    current year yields a window thousands of years long, in which expired subsidies stay active.
    """
    # Arrange
    furnace_group = devdata.get_furnace_group(
        fg_id="fg_opex_window",
        tech_name="EAF",
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2015), end=Year(2035)),
            plant_lifetime=20,
        ),
    )
    expired_subsidy = Subsidy(
        scenario_name="expired_after_lifetime",
        iso3="USA",
        technology_name="EAF",
        cost_item="opex",
        subsidy_type="absolute",
        subsidy_amount=40.0,
        start_year=Year(2040),
        end_year=Year(2060),
    )
    collected_windows = []
    collected_subsidies = []
    real_collect = calculate_costs.collect_active_subsidies_over_period

    def spy(subsidies, start_year, end_year):
        collected_windows.append((start_year, end_year))
        collected = real_collect(subsidies, start_year=start_year, end_year=end_year)
        collected_subsidies.append(collected)
        return collected

    # Act - no allowed transitions, so the method returns straight after collecting OPEX subsidies
    with patch("steelo.domain.models.collect_active_subsidies_over_period", side_effect=spy):
        furnace_group.optimal_technology_name(
            market_price_series={"steel": [600.0] * 30, "iron": [400.0] * 30},
            cost_of_debt_by_tech={"EAF": 0.04},
            cost_of_equity_by_tech={"EAF": 0.08},
            get_bom_from_avg_boms=lambda *args: (None, 0.0, ""),
            capex_dict={"EAF": 400.0},
            capex_renovation_share={"EAF": 0.7},
            technology_fopex_dict={"eaf": 50.0},
            dynamic_business_cases={"EAF": []},
            chosen_emissions_boundary_for_carbon_costs="scope_1",
            technology_emission_factors=[],
            tech_to_product={"EAF": "steel"},
            plant_lifetime=20,
            construction_time=2,
            current_year=Year(2025),
            risk_free_rate=0.02,
            allowed_furnace_transitions={},
            tech_opex_subsidies={"EAF": [expired_subsidy]},
        )

    # Assert
    assert collected_windows == [(Year(2025), Year(2035))]
    assert collected_subsidies == [[]], "A subsidy starting after the end year must not be collected."
