"""Tests for hydrogen and electricity subsidy calculation functions."""

import pytest
from steelo.domain import calculate_costs
from steelo.domain.models import Subsidy, Year


def test_calculate_energy_price_with_subsidies_no_subsidies():
    """Test that no subsidies returns original price."""
    result = calculate_costs.calculate_energy_price_with_subsidies(5.0, [])
    assert result == 5.0


def test_calculate_energy_price_with_subsidies_absolute():
    """Test that absolute subsidy reduces price by fixed amount."""
    subsidy = Subsidy(
        scenario_name="test",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="DRI+EAF",
        cost_item="hydrogen",
        subsidy_type="absolute",
        subsidy_amount=1.0,
    )
    result = calculate_costs.calculate_energy_price_with_subsidies(5.0, [subsidy])
    assert result == 4.0


def test_calculate_energy_price_with_subsidies_relative():
    """Test that relative subsidy reduces price by percentage."""
    subsidy = Subsidy(
        scenario_name="test",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="DRI+EAF",
        cost_item="hydrogen",
        subsidy_type="relative",
        subsidy_amount=0.1,
    )
    # 10% of $5 = $0.50, so $5 - $0.50 = $4.50
    result = calculate_costs.calculate_energy_price_with_subsidies(5.0, [subsidy])
    assert result == 4.5


def test_calculate_energy_price_with_subsidies_combined():
    """Test that absolute and relative subsidies stack."""
    abs_subsidy = Subsidy(
        scenario_name="test_abs",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="DRI+EAF",
        cost_item="hydrogen",
        subsidy_type="absolute",
        subsidy_amount=1.0,
    )
    rel_subsidy = Subsidy(
        scenario_name="test_rel",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="DRI+EAF",
        cost_item="hydrogen",
        subsidy_type="relative",
        subsidy_amount=0.1,
    )
    # $5 - $1 (absolute) - $0.50 (10% of $5) = $3.50
    result = calculate_costs.calculate_energy_price_with_subsidies(5.0, [abs_subsidy, rel_subsidy])
    assert result == 3.5


def test_calculate_energy_price_with_subsidies_floors_at_zero():
    """Test that subsidy exceeding price floors at zero."""
    subsidy = Subsidy(
        scenario_name="test",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="DRI+EAF",
        cost_item="hydrogen",
        subsidy_type="absolute",
        subsidy_amount=10.0,
    )
    result = calculate_costs.calculate_energy_price_with_subsidies(5.0, [subsidy])
    assert result == 0.0


def test_get_subsidised_energy_costs_no_subsidies():
    """Test that no subsidies returns original energy costs."""
    energy_costs = {"hydrogen": 5.0, "electricity": 0.10, "natural_gas": 3.0}
    subsidised, no_sub = calculate_costs.get_subsidised_energy_costs(energy_costs, [], [])
    assert subsidised["hydrogen"] == 5.0
    assert subsidised["electricity"] == 0.10
    assert no_sub["hydrogen"] == 5.0
    assert no_sub["electricity"] == 0.10


def test_get_subsidised_energy_costs_hydrogen_only():
    """Test that hydrogen subsidy only affects hydrogen price."""
    energy_costs = {"hydrogen": 5.0, "electricity": 0.10}
    h2_sub = Subsidy(
        scenario_name="test",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="DRI+EAF",
        cost_item="hydrogen",
        subsidy_type="absolute",
        subsidy_amount=1.0,
    )
    subsidised, no_sub = calculate_costs.get_subsidised_energy_costs(energy_costs, [h2_sub], [])
    assert subsidised["hydrogen"] == 4.0
    assert subsidised["electricity"] == 0.10  # unchanged
    assert no_sub["hydrogen"] == 5.0


def test_get_subsidised_energy_costs_electricity_only():
    """Test that electricity subsidy only affects electricity price."""
    energy_costs = {"hydrogen": 5.0, "electricity": 0.10}
    elec_sub = Subsidy(
        scenario_name="test",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="EAF",
        cost_item="electricity",
        subsidy_type="relative",
        subsidy_amount=0.2,
    )
    subsidised, no_sub = calculate_costs.get_subsidised_energy_costs(energy_costs, [], [elec_sub])
    assert subsidised["hydrogen"] == 5.0  # unchanged
    assert subsidised["electricity"] == 0.08  # 20% reduction
    assert no_sub["electricity"] == 0.10


def test_get_subsidised_energy_costs_both_subsidies():
    """Test that both hydrogen and electricity subsidies apply."""
    energy_costs = {"hydrogen": 5.0, "electricity": 0.10}
    h2_sub = Subsidy(
        scenario_name="test_h2",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="DRI+EAF",
        cost_item="hydrogen",
        subsidy_type="absolute",
        subsidy_amount=1.0,
    )
    elec_sub = Subsidy(
        scenario_name="test_elec",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="DRI+EAF",
        cost_item="electricity",
        subsidy_type="relative",
        subsidy_amount=0.2,
    )
    subsidised, no_sub = calculate_costs.get_subsidised_energy_costs(energy_costs, [h2_sub], [elec_sub])
    assert subsidised["hydrogen"] == 4.0
    assert subsidised["electricity"] == 0.08
    assert no_sub["hydrogen"] == 5.0
    assert no_sub["electricity"] == 0.10


def test_get_subsidised_energy_costs_preserves_other_carriers():
    """Test that other energy carriers are preserved unchanged."""
    energy_costs = {"hydrogen": 5.0, "electricity": 0.10, "natural_gas": 3.0, "coal": 2.0}
    h2_sub = Subsidy(
        scenario_name="test",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="DRI+EAF",
        cost_item="hydrogen",
        subsidy_type="absolute",
        subsidy_amount=1.0,
    )
    subsidised, no_sub = calculate_costs.get_subsidised_energy_costs(energy_costs, [h2_sub], [])
    assert subsidised["natural_gas"] == 3.0
    assert subsidised["coal"] == 2.0


def test_get_subsidised_energy_costs_zero_price_not_modified():
    """Test that zero price carriers are not modified even with subsidies."""
    energy_costs = {"hydrogen": 0.0, "electricity": 0.10}
    h2_sub = Subsidy(
        scenario_name="test",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="DRI+EAF",
        cost_item="hydrogen",
        subsidy_type="absolute",
        subsidy_amount=1.0,
    )
    subsidised, no_sub = calculate_costs.get_subsidised_energy_costs(energy_costs, [h2_sub], [])
    assert subsidised["hydrogen"] == 0.0  # zero price not modified
    assert no_sub["hydrogen"] == 0.0


def test_get_subsidised_energy_costs_raises_if_hydrogen_key_missing():
    """Test that KeyError is raised if hydrogen subsidies provided but key missing."""
    energy_costs = {"electricity": 0.10, "natural_gas": 3.0}  # no hydrogen key
    h2_sub = Subsidy(
        scenario_name="test",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="DRI+EAF",
        cost_item="hydrogen",
        subsidy_type="absolute",
        subsidy_amount=1.0,
    )
    with pytest.raises(KeyError, match="'hydrogen' key not found"):
        calculate_costs.get_subsidised_energy_costs(energy_costs, [h2_sub], [])


def test_get_subsidised_energy_costs_raises_if_electricity_key_missing():
    """Test that KeyError is raised if electricity subsidies provided but key missing."""
    energy_costs = {"hydrogen": 5.0, "natural_gas": 3.0}  # no electricity key
    elec_sub = Subsidy(
        scenario_name="test",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="EAF",
        cost_item="electricity",
        subsidy_type="absolute",
        subsidy_amount=0.05,
    )
    with pytest.raises(KeyError, match="'electricity' key not found"):
        calculate_costs.get_subsidised_energy_costs(energy_costs, [], [elec_sub])


def test_furnace_group_set_subsidised_energy_costs():
    """Test that FurnaceGroup tracks subsidised energy costs and original prices."""
    from datetime import date
    from steelo.domain.models import FurnaceGroup, PointInTime, Technology

    # Create minimal FurnaceGroup for testing
    technology = Technology(name="EAF", energy_consumption=1.0, bill_of_materials={}, product="steel")
    lifetime = PointInTime(plant_lifetime=20, current=Year(2025))
    furnace_group = FurnaceGroup(
        furnace_group_id="test_fg_1",
        capacity=100,
        status="operating",
        last_renovation_date=date(2020, 1, 1),
        technology=technology,
        historical_production={},
        utilization_rate=0.7,
        lifetime=lifetime,
    )

    # Set initial energy costs on the furnace group
    furnace_group.energy_costs = {"hydrogen": 5.0, "electricity": 0.10, "natural_gas": 3.0}

    # Create subsidies
    h2_subsidy = Subsidy(
        scenario_name="test_h2",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="BF",
        cost_item="hydrogen",
        subsidy_type="absolute",
        subsidy_amount=1.0,
    )
    elec_subsidy = Subsidy(
        scenario_name="test_elec",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="BF",
        cost_item="electricity",
        subsidy_type="relative",
        subsidy_amount=0.2,
    )

    # Prepare subsidised costs (simulating what get_subsidised_energy_costs returns)
    subsidised_costs = {"hydrogen": 4.0, "electricity": 0.08, "natural_gas": 3.0}
    no_subsidy_prices = {"hydrogen": 5.0, "electricity": 0.10}

    # Apply subsidised energy costs
    furnace_group.set_subsidised_energy_costs(
        subsidised_costs=subsidised_costs,
        no_subsidy_prices=no_subsidy_prices,
        h2_subsidies=[h2_subsidy],
        elec_subsidies=[elec_subsidy],
    )

    # Verify energy_costs updated
    assert furnace_group.energy_costs["hydrogen"] == 4.0
    assert furnace_group.energy_costs["electricity"] == 0.08
    assert furnace_group.energy_costs["natural_gas"] == 3.0

    # Verify original prices stored
    assert furnace_group.energy_costs_no_subsidy["hydrogen"] == 5.0
    assert furnace_group.energy_costs_no_subsidy["electricity"] == 0.10

    # Verify subsidies tracked in applied_subsidies
    assert len(furnace_group.applied_subsidies["hydrogen"]) == 1
    assert furnace_group.applied_subsidies["hydrogen"][0] == h2_subsidy
    assert len(furnace_group.applied_subsidies["electricity"]) == 1
    assert furnace_group.applied_subsidies["electricity"][0] == elec_subsidy
