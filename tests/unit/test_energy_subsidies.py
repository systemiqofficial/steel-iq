"""Tests for energy carrier subsidy calculation functions."""

import pytest
from steelo.domain import calculate_costs
from steelo.domain.models import Subsidy, Year


def test_calculate_energy_price_with_subsidies_no_subsidies():
    """Test that no subsidies returns original price."""
    result = calculate_costs.calculate_energy_price_with_subsidies(5000.0, [])
    assert result == 5000.0


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
        subsidy_amount=1000.0,
    )
    result = calculate_costs.calculate_energy_price_with_subsidies(5000.0, [subsidy])
    assert result == 4000.0


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
    # 10% of $5000 = $500, so $5000 - $500 = $4500
    result = calculate_costs.calculate_energy_price_with_subsidies(5000.0, [subsidy])
    assert result == 4500.0


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
        subsidy_amount=1000.0,
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
    # $5000 - $1000 (absolute) - $500 (10% of $5000) = $3500
    result = calculate_costs.calculate_energy_price_with_subsidies(5000.0, [abs_subsidy, rel_subsidy])
    assert result == 3500.0


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
        subsidy_amount=10000.0,
    )
    result = calculate_costs.calculate_energy_price_with_subsidies(5000.0, [subsidy])
    assert result == 0.0


def test_get_subsidised_energy_costs_no_subsidies():
    """Test that no subsidies returns original energy costs unchanged."""
    energy_costs = {"hydrogen": 5000.0, "electricity": 0.10, "natural_gas": 0.03}
    input_costs, output_costs, no_sub = calculate_costs.get_subsidised_energy_costs(energy_costs, {})
    assert input_costs["hydrogen"] == 5000.0
    assert input_costs["electricity"] == 0.10
    assert output_costs["hydrogen"] == 5000.0
    assert output_costs["electricity"] == 0.10
    assert no_sub == energy_costs


def test_get_subsidised_energy_costs_hydrogen_only():
    """Test that hydrogen subsidy only affects hydrogen price."""
    energy_costs = {"hydrogen": 5000.0, "electricity": 0.10}
    h2_sub = Subsidy(
        scenario_name="test",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="DRI+EAF",
        cost_item="hydrogen",
        subsidy_type="absolute",
        subsidy_amount=1000.0,
    )
    input_costs, output_costs, no_sub = calculate_costs.get_subsidised_energy_costs(
        energy_costs,
        {"hydrogen": [h2_sub]},
    )
    assert input_costs["hydrogen"] == 4000.0
    assert input_costs["electricity"] == 0.10  # unchanged
    assert output_costs["hydrogen"] == 6000.0  # 5000 + 1000
    assert output_costs["electricity"] == 0.10  # unchanged
    assert no_sub["hydrogen"] == 5000.0


def test_get_subsidised_energy_costs_electricity_only():
    """Test that electricity subsidy only affects electricity price."""
    energy_costs = {"hydrogen": 5000.0, "electricity": 0.10}
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
    input_costs, output_costs, no_sub = calculate_costs.get_subsidised_energy_costs(
        energy_costs,
        {"electricity": [elec_sub]},
    )
    assert input_costs["hydrogen"] == 5000.0  # unchanged
    assert input_costs["electricity"] == 0.08  # 20% reduction
    assert output_costs["hydrogen"] == 5000.0  # unchanged
    assert output_costs["electricity"] == pytest.approx(0.12)  # 20% increase
    assert no_sub["electricity"] == 0.10


def test_get_subsidised_energy_costs_both_subsidies():
    """Test that both hydrogen and electricity subsidies apply to input and output."""
    energy_costs = {"hydrogen": 5000.0, "electricity": 0.10}
    h2_sub = Subsidy(
        scenario_name="test_h2",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="DRI+EAF",
        cost_item="hydrogen",
        subsidy_type="absolute",
        subsidy_amount=1000.0,
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
    input_costs, output_costs, no_sub = calculate_costs.get_subsidised_energy_costs(
        energy_costs,
        {"hydrogen": [h2_sub], "electricity": [elec_sub]},
    )
    assert input_costs["hydrogen"] == 4000.0
    assert input_costs["electricity"] == 0.08
    assert output_costs["hydrogen"] == 6000.0
    assert output_costs["electricity"] == pytest.approx(0.12)
    assert no_sub["hydrogen"] == 5000.0
    assert no_sub["electricity"] == 0.10


def test_get_subsidised_energy_costs_preserves_other_carriers():
    """Test that other energy carriers are preserved unchanged."""
    energy_costs = {"hydrogen": 5000.0, "electricity": 0.10, "natural_gas": 0.03, "coal": 0.02}
    h2_sub = Subsidy(
        scenario_name="test",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="DRI+EAF",
        cost_item="hydrogen",
        subsidy_type="absolute",
        subsidy_amount=1000.0,
    )
    input_costs, output_costs, no_sub = calculate_costs.get_subsidised_energy_costs(
        energy_costs,
        {"hydrogen": [h2_sub]},
    )
    assert input_costs["natural_gas"] == 0.03
    assert input_costs["coal"] == 0.02
    assert output_costs["natural_gas"] == 0.03
    assert output_costs["coal"] == 0.02


def test_get_subsidised_energy_costs_zero_price_still_subsidised():
    """Test that zero-priced carriers still receive subsidies on the output side."""
    energy_costs = {"hydrogen": 0.0, "electricity": 0.10}
    h2_sub = Subsidy(
        scenario_name="test",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="DRI+EAF",
        cost_item="hydrogen",
        subsidy_type="absolute",
        subsidy_amount=1000.0,
    )
    input_costs, output_costs, no_sub = calculate_costs.get_subsidised_energy_costs(
        energy_costs,
        {"hydrogen": [h2_sub]},
    )
    assert input_costs["hydrogen"] == 0.0  # max(0, 0 - 1000) = 0
    assert output_costs["hydrogen"] == 1000.0  # 0 + 1000 = 1000
    assert no_sub["hydrogen"] == 0.0


def test_get_subsidised_energy_costs_raises_if_carrier_key_missing():
    """Test that KeyError is raised if subsidies provided but carrier key missing."""
    energy_costs = {"electricity": 0.10, "natural_gas": 0.03}  # no hydrogen key
    h2_sub = Subsidy(
        scenario_name="test",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="DRI+EAF",
        cost_item="hydrogen",
        subsidy_type="absolute",
        subsidy_amount=1000.0,
    )
    with pytest.raises(KeyError, match="'hydrogen'.*not found"):
        calculate_costs.get_subsidised_energy_costs(energy_costs, {"hydrogen": [h2_sub]})


def test_get_subsidised_energy_costs_raises_if_electricity_key_missing():
    """Test that KeyError is raised if electricity subsidies provided but key missing."""
    energy_costs = {"hydrogen": 5000.0, "natural_gas": 0.03}  # no electricity key
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
    with pytest.raises(KeyError, match="'electricity'.*not found"):
        calculate_costs.get_subsidised_energy_costs(energy_costs, {"electricity": [elec_sub]})


def test_get_subsidised_energy_costs_natural_gas():
    """Test that natural gas subsidy applies correctly."""
    energy_costs = {"hydrogen": 5000.0, "electricity": 0.10, "natural_gas": 0.03}
    ng_sub = Subsidy(
        scenario_name="test_ng",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="BF",
        cost_item="natural_gas",
        subsidy_type="absolute",
        subsidy_amount=0.01,
    )
    input_costs, output_costs, no_sub = calculate_costs.get_subsidised_energy_costs(
        energy_costs,
        {"natural_gas": [ng_sub]},
    )
    assert input_costs["natural_gas"] == pytest.approx(0.02)
    assert output_costs["natural_gas"] == pytest.approx(0.04)  # 0.03 + 0.01
    assert no_sub["natural_gas"] == 0.03
    assert input_costs["hydrogen"] == 5000.0  # unchanged
    assert input_costs["electricity"] == 0.10  # unchanged


def test_get_subsidised_energy_costs_multiple_carriers():
    """Test that subsidies for multiple carriers apply simultaneously."""
    energy_costs = {"hydrogen": 5000.0, "electricity": 0.10, "natural_gas": 0.03}
    h2_sub = Subsidy(
        scenario_name="test_h2",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="DRI+EAF",
        cost_item="hydrogen",
        subsidy_type="absolute",
        subsidy_amount=1000.0,
    )
    ng_sub = Subsidy(
        scenario_name="test_ng",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="DRI+EAF",
        cost_item="natural_gas",
        subsidy_type="relative",
        subsidy_amount=0.5,
    )
    input_costs, output_costs, no_sub = calculate_costs.get_subsidised_energy_costs(
        energy_costs,
        {"hydrogen": [h2_sub], "natural_gas": [ng_sub]},
    )
    assert input_costs["hydrogen"] == 4000.0
    assert input_costs["natural_gas"] == pytest.approx(0.015)
    assert output_costs["hydrogen"] == 6000.0
    assert output_costs["natural_gas"] == pytest.approx(0.045)  # 0.03 + 50% of 0.03
    assert no_sub["hydrogen"] == 5000.0
    assert no_sub["natural_gas"] == 0.03


def test_furnace_group_set_subsidised_energy_costs():
    """Test that FurnaceGroup stores input, output, and original prices separately.

    A subsidy simultaneously reduces input cost and increases output profit.
    """
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

    # Set initial energy costs via set_energy_costs (populates output_energy_costs)
    furnace_group.set_energy_costs(hydrogen=5000.0, electricity=0.10, natural_gas=0.03)

    # Create subsidies
    h2_subsidy = Subsidy(
        scenario_name="test_h2",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="BF",
        cost_item="hydrogen",
        subsidy_type="absolute",
        subsidy_amount=1000.0,
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
    input_costs = {"hydrogen": 4000.0, "electricity": 0.08, "natural_gas": 0.03}
    output_costs = {"hydrogen": 6000.0, "electricity": 0.12, "natural_gas": 0.03}
    no_subsidy_prices = {"hydrogen": 5000.0, "electricity": 0.10}
    energy_subsidies = {"hydrogen": [h2_subsidy], "electricity": [elec_subsidy]}

    # Apply subsidised energy costs
    furnace_group.set_subsidised_energy_costs(
        input_costs=input_costs,
        output_costs=output_costs,
        no_subsidy_prices=no_subsidy_prices,
        energy_subsidies=energy_subsidies,
    )

    # Verify input costs (reduced by subsidy)
    assert furnace_group.energy_costs["hydrogen"] == 4000.0
    assert furnace_group.energy_costs["electricity"] == 0.08
    assert furnace_group.energy_costs["natural_gas"] == 0.03

    # Verify output costs (increased by subsidy)
    assert furnace_group.output_energy_costs["hydrogen"] == 6000.0
    assert furnace_group.output_energy_costs["electricity"] == pytest.approx(0.12)
    assert furnace_group.output_energy_costs["natural_gas"] == 0.03

    # Verify original prices stored
    assert furnace_group.energy_costs_no_subsidy["hydrogen"] == 5000.0
    assert furnace_group.energy_costs_no_subsidy["electricity"] == 0.10

    # Verify subsidies tracked in applied_subsidies
    assert len(furnace_group.applied_subsidies["hydrogen"]) == 1
    assert furnace_group.applied_subsidies["hydrogen"][0] == h2_subsidy
    assert len(furnace_group.applied_subsidies["electricity"]) == 1
    assert furnace_group.applied_subsidies["electricity"][0] == elec_subsidy


def _make_env_stub():
    """Create a lightweight stub with just the method under test."""
    from steelo.domain.models import Environment

    stub = object.__new__(Environment)
    stub.energy_subsidies = {}
    return stub


def test_environment_initiate_energy_subsidies_groups_by_carrier_iso3_tech():
    """Test that initiate_energy_subsidies groups subsidies by carrier -> iso3 -> tech."""
    env = _make_env_stub()
    h2_sub = Subsidy(
        scenario_name="h2_test",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="DRI",
        cost_item="hydrogen",
        subsidy_type="absolute",
        subsidy_amount=500.0,
    )
    ng_sub = Subsidy(
        scenario_name="ng_test",
        iso3="DEU",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="BF",
        cost_item="natural_gas",
        subsidy_type="absolute",
        subsidy_amount=0.01,
    )
    env.initiate_energy_subsidies([h2_sub, ng_sub])

    assert "hydrogen" in env.energy_subsidies
    assert "natural_gas" in env.energy_subsidies
    assert env.energy_subsidies["hydrogen"]["USA"]["DRI"] == [h2_sub]
    assert env.energy_subsidies["natural_gas"]["DEU"]["BF"] == [ng_sub]


def test_environment_initiate_energy_subsidies_excludes_financial():
    """Test that financial subsidies (opex, capex, cost of debt) are excluded."""
    env = _make_env_stub()
    opex_sub = Subsidy(
        scenario_name="opex_test",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="DRI",
        cost_item="opex",
        subsidy_type="relative",
        subsidy_amount=0.1,
    )
    capex_sub = Subsidy(
        scenario_name="capex_test",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="DRI",
        cost_item="capex",
        subsidy_type="absolute",
        subsidy_amount=100.0,
    )
    env.initiate_energy_subsidies([opex_sub, capex_sub])

    assert env.energy_subsidies == {}


def test_environment_initiate_energy_subsidies_empty_input():
    """Test that empty input produces empty dict."""
    env = _make_env_stub()
    env.initiate_energy_subsidies([])
    assert env.energy_subsidies == {}
