"""Tests for energy carrier subsidy calculation functions."""

import pytest
from steelo.domain import calculate_costs
from steelo.domain.models import Subsidy, Year
from steelo.domain.calculate_costs import ReductantScoreSeries


def _stub_series(location, tech, output_shares, start, end, **kwargs):
    n = int(end) - int(start)
    return ReductantScoreSeries(scores=[0.0] * n, picks=["scrap"] * n)


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


# ── FurnaceGroup.__init__ energy cost propagation ───────────────────────────


def test_furnace_group_init_populates_output_energy_costs():
    """Test that FurnaceGroup.__init__ with energy_cost_dict populates output_energy_costs."""
    from datetime import date
    from steelo.domain.models import FurnaceGroup, PointInTime, Technology

    technology = Technology(name="EAF", product="steel")
    lifetime = PointInTime(plant_lifetime=20, current=Year(2025))
    fg = FurnaceGroup(
        furnace_group_id="test_init_1",
        capacity=100,
        status="operating",
        last_renovation_date=date(2020, 1, 1),
        technology=technology,
        historical_production={},
        utilization_rate=0.7,
        lifetime=lifetime,
        energy_cost_dict={"hydrogen": 5000.0, "electricity": 0.10, "bf_gas": 0.02},
    )

    assert fg.output_energy_costs != {}
    assert fg.output_energy_costs["hydrogen"] == 5000.0
    assert fg.output_energy_costs["electricity"] == 0.10
    assert fg.output_energy_costs["bf_gas"] == 0.02
    assert fg.energy_costs["hydrogen"] == 5000.0


def test_furnace_group_init_empty_energy_costs_leaves_empty_dicts():
    """Test that FurnaceGroup.__init__ without energy_cost_dict leaves dicts empty."""
    from datetime import date
    from steelo.domain.models import FurnaceGroup, PointInTime, Technology

    technology = Technology(name="EAF", product="steel")
    lifetime = PointInTime(plant_lifetime=20, current=Year(2025))
    fg = FurnaceGroup(
        furnace_group_id="test_init_2",
        capacity=100,
        status="operating",
        last_renovation_date=date(2020, 1, 1),
        technology=technology,
        historical_production={},
        utilization_rate=0.7,
        lifetime=lifetime,
    )

    assert fg.energy_costs == {}
    assert fg.output_energy_costs == {}
    assert fg.energy_costs_no_subsidy == {}


# ── generate_new_furnace energy cost propagation ────────────────────────────


def _make_plant_with_energy_costs():
    """Create a minimal Plant with one FG that has energy costs set."""
    from datetime import date
    from steelo.domain.models import (
        FurnaceGroup,
        Plant,
        PointInTime,
        Technology,
        Location,
    )

    technology = Technology(name="BF-BOF", product="steel")
    lifetime = PointInTime(plant_lifetime=20, current=Year(2025))
    fg = FurnaceGroup(
        furnace_group_id="P000000000001",
        capacity=500_000,
        status="operating",
        last_renovation_date=date(2020, 1, 1),
        technology=technology,
        historical_production={},
        utilization_rate=0.8,
        lifetime=lifetime,
        energy_cost_dict={
            "hydrogen": 5000.0,
            "electricity": 0.10,
            "bf_gas": -0.02,
            "natural_gas": 0.03,
        },
    )
    location = Location(
        lat=51.5,
        lon=-0.1,
        country="Test",
        region="test_region",
        iso3="TST",
    )
    plant = Plant(
        plant_id="P000000000001",
        location=location,
        furnace_groups=[fg],
        power_source="grid",
        soe_status="private",
        parent_gem_id="parent",
        workforce_size=100,
        certified=False,
        category_steel_product=set(),
        technology_unit_fopex={"bf-bof": 10.0, "dri+eaf": 8.0},
    )
    return plant


def test_generate_new_furnace_path_a_sets_all_three_dicts():
    """Test that explicit energy_costs + output + no_subsidy populates all three dicts."""
    plant = _make_plant_with_energy_costs()

    input_costs = {"hydrogen": 4000.0, "electricity": 0.08}
    output_costs = {"hydrogen": 6000.0, "electricity": 0.12}
    no_subsidy = {"hydrogen": 5000.0, "electricity": 0.10}

    new_fg = plant.generate_new_furnace(
        technology_name="DRI+EAF",
        product="steel",
        current_year=2025,
        capex=500.0,
        capex_no_subsidy=500.0,
        cost_of_debt=0.05,
        cost_of_debt_no_subsidy=0.05,
        capacity=100_000,
        lag=2,
        status="considered",
        util_rate=0.0,
        plant_lifetime=20,
        chosen_reductant="hydrogen",
        energy_costs=input_costs,
        output_energy_costs=output_costs,
        energy_costs_no_subsidy=no_subsidy,
    )

    assert new_fg.energy_costs["hydrogen"] == 4000.0
    assert new_fg.energy_costs["electricity"] == 0.08
    assert new_fg.output_energy_costs["hydrogen"] == 6000.0
    assert new_fg.output_energy_costs["electricity"] == 0.12
    assert new_fg.energy_costs_no_subsidy["hydrogen"] == 5000.0
    assert new_fg.energy_costs_no_subsidy["electricity"] == 0.10


def test_generate_new_furnace_path_b_copies_parent_costs():
    """Test that inherited energy_costs are copied, not referenced.

    Mutating the parent FG's energy_costs must not affect the child FG.
    """
    plant = _make_plant_with_energy_costs()

    new_fg = plant.generate_new_furnace(
        technology_name="DRI+EAF",
        product="steel",
        current_year=2025,
        capex=500.0,
        capex_no_subsidy=500.0,
        cost_of_debt=0.05,
        cost_of_debt_no_subsidy=0.05,
        capacity=100_000,
        lag=2,
        status="construction",
        util_rate=0.0,
        plant_lifetime=20,
        chosen_reductant="hydrogen",
    )

    assert new_fg.energy_costs["hydrogen"] == 5000.0
    assert new_fg.output_energy_costs["hydrogen"] == 5000.0

    # Mutation isolation
    parent_fg = plant.furnace_groups[0]
    parent_fg.energy_costs["hydrogen"] = 9999.0
    assert new_fg.energy_costs["hydrogen"] == 5000.0


def test_generate_new_furnace_normalises_negative_physical_carriers():
    """Test that set_energy_costs applies abs() to negative physical carrier prices."""
    plant = _make_plant_with_energy_costs()

    new_fg = plant.generate_new_furnace(
        technology_name="DRI+EAF",
        product="steel",
        current_year=2025,
        capex=500.0,
        capex_no_subsidy=500.0,
        cost_of_debt=0.05,
        cost_of_debt_no_subsidy=0.05,
        capacity=100_000,
        lag=2,
        status="considered",
        util_rate=0.0,
        plant_lifetime=20,
        chosen_reductant="hydrogen",
        energy_costs={"bf_gas": -0.02, "hydrogen": 5000.0},
    )

    # bf_gas should be abs-normalised (non-co2 carrier)
    assert new_fg.energy_costs["bf_gas"] == 0.02


# ── prepare_cost_data_for_business_opportunity: abs() on negative prices ─────


def test_prepare_cost_data_normalises_negative_prices_before_subsidy():
    """Test that negative by-product prices are abs-normalised before subsidy calc.

    Without abs(), a negative bf_gas price (-0.02) with a subsidy (0.005) gives:
    - Input: max(0, -0.02 - 0.005) = 0  (wrong — should be max(0, 0.02 - 0.005) = 0.015)
    - Output: -0.02 + 0.005 = -0.015    (wrong — should be 0.02 + 0.005 = 0.025)
    """
    from steelo.domain.new_plant_opening import (
        prepare_cost_data_for_business_opportunity,
        NewPlantLocation,
    )

    product_to_tech = {"steel": ["BF-BOF"]}
    best_locations_subset = {
        "steel": [
            NewPlantLocation(
                Latitude=40.0,
                Longitude=-100.0,
                iso3="USA",
                power_price=0.05,
                capped_lcoh=3.0,
                rail_cost=10.0,
            ),
        ],
    }
    energy_costs = {
        "USA": {
            Year(2025): {
                "electricity": 0.05,
                "hydrogen": 3500.0,
                "bf_gas": -0.02,  # Negative by-product price
            },
        },
    }
    capex_dict_all_locs_techs = {"Americas": {"BF-BOF": 1000.0}}
    cost_of_debt_all_locs = {
        "USA": {
            tech: 0.05
            for tech in (
                "EAF",
                "BF",
                "BOF",
                "DRI",
                "SR",
                "MOE",
                "E-WIN",
                "BF+CCS",
                "BF+CCU",
                "DRI+CCS",
                "DRI+CCU",
                "DRI+EAF",
                "DRI+ESF",
                "ESF",
                "ZZZ",
                "BF-BOF",
            )
        }
    }
    cost_of_equity_all_locs = {
        "USA": {
            tech: 0.08
            for tech in (
                "EAF",
                "BF",
                "BOF",
                "DRI",
                "SR",
                "MOE",
                "E-WIN",
                "BF+CCS",
                "BF+CCU",
                "DRI+CCS",
                "DRI+CCU",
                "DRI+EAF",
                "DRI+ESF",
                "ESF",
                "ZZZ",
                "BF-BOF",
            )
        }
    }
    fopex_all_locs_techs = {"USA": {"bf-bof": 50.0}}
    iso3_to_region_map = {"USA": "Americas"}

    # bf_gas subsidy: absolute 0.005 USD/kWh
    bf_gas_subsidy = Subsidy(
        scenario_name="test",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2035),
        technology_name="BF-BOF",
        cost_item="bf_gas",
        subsidy_type="absolute",
        subsidy_amount=0.005,
    )
    energy_subsidies = {
        "bf_gas": {"USA": {"BF-BOF": [bf_gas_subsidy]}},
    }

    def _get_bom(_energy_costs, tech, _capacity, _most_common_reductant=None):
        """Minimal BOM mock."""
        if tech == "BF-BOF":
            return (
                {"energy": {"electricity": {"unit_cost": 50.0, "demand": 0.5}}},
                0.85,
                "coal",
                {"scrap": 1.0},
            )
        return None, 0.0, None, {}

    cost_data = prepare_cost_data_for_business_opportunity(
        product_to_tech=product_to_tech,
        best_locations_subset=best_locations_subset,
        current_year=Year(2025),
        target_year=Year(2030),
        energy_costs=energy_costs,
        capex_dict_all_locs_techs=capex_dict_all_locs_techs,
        cost_of_debt_all_locs=cost_of_debt_all_locs,
        cost_of_equity_all_locs=cost_of_equity_all_locs,
        fopex_all_locs_techs=fopex_all_locs_techs,
        steel_plant_capacity=100.0,
        get_bom_from_avg_boms=_get_bom,
        plant_lifetime=20,
        construction_time=2,
        reductant_score_series=_stub_series,
        iso3_to_region_map=iso3_to_region_map,
        global_risk_free_rate=0.03,
        capex_subsidies={},
        debt_subsidies={},
        opex_subsidies={},
        energy_subsidies=energy_subsidies,
        most_common_reductant={},
        environment_most_common_reductant={},
    )

    site_id = (40.0, -100.0, "USA")
    tech_data = cost_data["steel"][site_id]["BF-BOF"]

    # bf_gas base price should be abs-normalised to 0.02 before subsidy calc
    # Input: max(0, 0.02 - 0.005) = 0.015
    assert tech_data["energy_costs"]["bf_gas"] == pytest.approx(0.015)
    # Output: 0.02 + 0.005 = 0.025
    assert tech_data["output_costs"]["bf_gas"] == pytest.approx(0.025)
    # No-subsidy: the abs-normalised base price
    assert tech_data["no_subsidy_prices"]["bf_gas"] == pytest.approx(0.02)


# ── set_input_cost_in_furnace_groups: indi compounding fix ──────────────────


def test_set_input_cost_indi_uses_no_subsidy_prices():
    """Test that indi plants preserve pre-subsidy electricity/hydrogen prices.

    Simulates the yearly cycle: subsidies applied → set_input_cost resets base.
    Without the fix, the subsidised price becomes next year's base (compounding).
    """
    from datetime import date
    from steelo.domain.models import (
        Environment,
        FurnaceGroup,
        Plant,
        PointInTime,
        Technology,
        Location,
    )

    # Create indi plant with energy costs
    technology = Technology(name="EAF", product="steel")
    lifetime = PointInTime(plant_lifetime=20, current=Year(2025))
    fg = FurnaceGroup(
        furnace_group_id="P000000000001",
        capacity=100_000,
        status="operating",
        last_renovation_date=date(2020, 1, 1),
        technology=technology,
        historical_production={},
        utilization_rate=0.8,
        lifetime=lifetime,
        energy_cost_dict={"electricity": 0.028, "hydrogen": 3500.0, "natural_gas": 0.025},
    )
    location = Location(lat=24.0, lon=44.0, country="SAU", region="mena", iso3="SAU")
    plant = Plant(
        plant_id="P000000000001",
        location=location,
        furnace_groups=[fg],
        power_source="grid",
        soe_status="private",
        parent_gem_id="indi",
        workforce_size=100,
        certified=False,
        category_steel_product=set(),
        technology_unit_fopex={"eaf": 10.0},
    )

    # Simulate subsidy application (electricity $0.028 → $0.023)
    fg.set_subsidised_energy_costs(
        input_costs={"electricity": 0.023, "hydrogen": 3400.0, "natural_gas": 0.025},
        output_costs={"electricity": 0.033, "hydrogen": 3600.0, "natural_gas": 0.025},
        no_subsidy_prices={"electricity": 0.028, "hydrogen": 3500.0, "natural_gas": 0.025},
        energy_subsidies={"electricity": [], "hydrogen": []},
    )
    assert fg.energy_costs["electricity"] == 0.023  # subsidised

    # Create env stub with input_costs
    env = object.__new__(Environment)
    env.year = Year(2025)
    env.config = type("Config", (), {"disposal_cost_outputs": frozenset({"steelmaking_slag"})})()
    env.input_costs = {
        "SAU": {
            Year(2025): {
                "electricity": 0.050,
                "hydrogen": 4000.0,
                "natural_gas": 0.025,
            },
        },
    }

    # Run set_input_cost_in_furnace_groups (end-of-year reset)
    env.set_input_cost_in_furnace_groups(world_plants=[plant])

    # Electricity should be reset to the no_subsidy price (0.028), NOT the
    # subsidised price (0.023) and NOT the country-level price (0.050)
    assert fg.energy_costs["electricity"] == 0.028
    # Hydrogen should also use no_subsidy price
    assert fg.energy_costs["hydrogen"] == 3500.0
    # Other carriers should come from country-level input_costs
    assert fg.energy_costs["natural_gas"] == 0.025


# ── prepare_cost_data_for_business_opportunity: subsidy filter year (B4) ─────


def test_prepare_cost_data_filters_energy_subsidies_at_operating_start_year():
    """Energy subsidies for the BOM choice are filtered at the operating start year.

    With target_year=2030 and construction_time=2 the plant first operates in 2032:
    a subsidy expiring in 2031 (during construction) must not price the BOM, while
    one starting in 2032 must — matching PAM's operating_start_year filter and the
    window the score series already uses.
    """
    from steelo.domain.new_plant_opening import (
        prepare_cost_data_for_business_opportunity,
        NewPlantLocation,
    )

    product_to_tech = {"steel": ["BF-BOF"]}
    best_locations_subset = {
        "steel": [
            NewPlantLocation(
                Latitude=40.0,
                Longitude=-100.0,
                iso3="USA",
                power_price=0.05,
                capped_lcoh=3.0,
                rail_cost=10.0,
            ),
        ],
    }
    energy_costs = {
        "USA": {
            Year(2025): {
                "electricity": 0.05,
                "hydrogen": 3500.0,
                "bf_gas": 0.02,
            },
        },
    }

    expired_during_construction = Subsidy(
        scenario_name="test",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2031),
        technology_name="BF-BOF",
        cost_item="bf_gas",
        subsidy_type="absolute",
        subsidy_amount=0.005,
    )
    starts_at_operation = Subsidy(
        scenario_name="test",
        iso3="USA",
        start_year=Year(2032),
        end_year=Year(2040),
        technology_name="BF-BOF",
        cost_item="bf_gas",
        subsidy_type="absolute",
        subsidy_amount=0.004,
    )
    energy_subsidies = {
        "bf_gas": {"USA": {"BF-BOF": [expired_during_construction, starts_at_operation]}},
    }

    def _get_bom(_energy_costs, tech, _capacity, _most_common_reductant=None):
        """Minimal BOM mock."""
        if tech == "BF-BOF":
            return (
                {"energy": {"electricity": {"unit_cost": 50.0, "demand": 0.5}}},
                0.85,
                "coal",
                {"scrap": 1.0},
            )
        return None, 0.0, None, {}

    cost_data = prepare_cost_data_for_business_opportunity(
        product_to_tech=product_to_tech,
        best_locations_subset=best_locations_subset,
        current_year=Year(2025),
        target_year=Year(2030),
        energy_costs=energy_costs,
        capex_dict_all_locs_techs={"Americas": {"BF-BOF": 1000.0}},
        cost_of_debt_all_locs={"USA": {"BF-BOF": 0.05}},
        cost_of_equity_all_locs={"USA": {"BF-BOF": 0.08}},
        fopex_all_locs_techs={"USA": {"bf-bof": 50.0}},
        steel_plant_capacity=100.0,
        get_bom_from_avg_boms=_get_bom,
        plant_lifetime=20,
        construction_time=2,
        reductant_score_series=_stub_series,
        iso3_to_region_map={"USA": "Americas"},
        global_risk_free_rate=0.03,
        capex_subsidies={},
        debt_subsidies={},
        opex_subsidies={},
        energy_subsidies=energy_subsidies,
        most_common_reductant={},
        environment_most_common_reductant={},
    )

    tech_data = cost_data["steel"][(40.0, -100.0, "USA")]["BF-BOF"]

    # Only the subsidy active at operating start (2032) applies: 0.02 - 0.004 = 0.016.
    # The one expiring in 2031 (active at target_year 2030) must not.
    assert tech_data["energy_costs"]["bf_gas"] == pytest.approx(0.016)
    assert tech_data["output_costs"]["bf_gas"] == pytest.approx(0.024)
    assert tech_data["no_subsidy_prices"]["bf_gas"] == pytest.approx(0.02)
