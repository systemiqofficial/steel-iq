import numpy as np
import pytest
from unittest.mock import patch

from baseload_optimisation_atlas.boa_logic import (
    estimate_battery_capacity,
    calculate_installation_cost,
    calculate_net_energy_production,
    state_of_charge,
    calculate_coverage,
    filter_designs_according_to_coverage_and_calculate_costs,
    show_optimal_design,
)


@pytest.fixture
def net_generation_distribution():
    """
    A proxy distribution for the net energy generation data equivalent to how it could look as a function of Cw*wind + Cpv*solar-demand
    """
    np.random.seed(42)
    return np.random.normal(0, 1, 8760)


def test_estimate_battery_capacity(net_generation_distribution):
    """
    Test the estimate_battery_capacity function.
    """
    battery_capacity = estimate_battery_capacity(net_generation_distribution, q_deficit=5, q_duration=95)
    assert (
        battery_capacity.round(3) == 1.655 * 5.0
    )  # for a normal distribution the 5th is -1.655 and for the duration of negative values the 95th percentile is 5

    battery_capacity = estimate_battery_capacity(net_generation_distribution, q_deficit=5, q_duration=99)

    assert battery_capacity == pytest.approx(
        11.585, 0.001
    )  # for a normal distribution the 5th is -1.655 and for the duration of negative values the 99th percentile is 7


def test_calculate_net_energy_production():
    # Generate synthetic wind and solar data
    wind_profile = np.array([0.6, 0.6, 0.55, 0.5, 0.4])
    solar_profile = np.array([0.12, 0.25, 0.5, 0.5, 0.5])

    # set energy demand
    demand = 1

    net_nrg = calculate_net_energy_production(1, solar_profile, 1, wind_profile)

    assert all(net_nrg == wind_profile + solar_profile - demand)


@pytest.fixture
def cost_dict():
    return dict(
        cost_wind=1160,  # per MW overscaling for wind
        cost_solar=758,  # per MW overscaling for solar
        cost_battery=273,  # per MWh of battery capacity
    )


def test_installation_cost(cost_dict):
    cost = calculate_installation_cost(1, 1, 1, **cost_dict)
    assert cost == 2191

    cost = calculate_installation_cost(1, 2, 1, **cost_dict)
    assert cost == 2191 + 1160

    cost = calculate_installation_cost(2, 1, 1, **cost_dict)
    assert cost == 2191 + 758

    cost = calculate_installation_cost(1, 1, 2, **cost_dict)
    assert cost == 2191 + 273

    cost = calculate_installation_cost(2, 2, 2, **cost_dict)
    assert cost == 2191 * 2


def test_state_of_charge():
    test_ge = np.array([1.5, 4, 0.5, 0, 0, 0, 0, 1, 2, 1])
    d = np.ones(10)
    test_es = test_ge - d
    soc = state_of_charge(test_es, 3)
    assert np.array_equal(soc, np.array([0.5, 3, 2.5, 1.5, 0.5, 0, 0, 0, 1, 1]))


def test_coverage():
    test_ge = np.array([1.5, 4, 0.5, 0, 0, 0, 0, 1, 2, 1])
    d = np.ones(10)
    test_es = test_ge - d
    soc = np.array([0.5, 3, 2.5, 1.5, 0.5, 0, 0, 0, 1, 1])
    coverage = calculate_coverage(soc, test_es)
    assert coverage == 0.8  # Demand covered for 8 out of 10 hours


# ---------------------------------------------- Test filter_designs_according_to_coverage_and_calculate_costs ------------------------------------------
@pytest.fixture
def sample_designs():
    return [
        {"solar": 1.0, "wind": 1.0, "battery": 0.5},
        {"solar": 0.5, "wind": 0.5, "battery": 0.1},
        {"solar": 0.0, "wind": 0.0, "battery": 0.0},
    ]


@pytest.fixture
def sample_capex():
    return {"solar": [1000] * 3, "wind": [1200] * 3, "battery": [800] * 3}


@pytest.fixture
def sample_opex():
    return {"solar": 0.02, "wind": 0.03, "battery": 0.01}


@pytest.fixture
def sample_profile():
    return {"solar": np.array([1] * 8760), "wind": np.array([1] * 8760)}


@patch("baseload_optimisation_atlas.boa_logic.state_of_charge")
@patch("baseload_optimisation_atlas.boa_logic.calculate_installation_cost")
@patch("baseload_optimisation_atlas.boa_logic.calculate_lcoe_of_re_installation")  # Patch where used, not where defined
def test_filter_and_costs(
    mock_lcoe,
    mock_installation_cost,
    mock_soc,
    sample_designs,
    sample_capex,
    sample_opex,
    sample_profile,
):
    # Setup mocks
    mock_soc.side_effect = [
        np.array([0.05] * 8760),  # accepted (soc > 0 100% of the time steps)
        np.concatenate([np.full(8497, 0.1), np.full(263, -0.1)]),  # accepted (soc > 0 97% of the time steps)
        np.concatenate([np.full(7884, 0.1), np.full(876, -0.1)]),  # rejected (soc > 0 90% of the time steps)
        np.array([-0.5] * 8760),  # rejected (soc > 0 0% of the time steps)
    ]
    mock_installation_cost.side_effect = [10000, 5000]
    mock_lcoe.side_effect = [50.0, 60.0]
    baseload_demand = 500.0
    cost_of_capital = 0.05
    investment_horizon = 3
    p = 5  # percentile - 5% means 95% coverage threshold

    # Create storage_costs with the expected structure
    storage_costs = {
        "battery_cost_per_installed_unit": np.array(sample_capex["battery"]),
        "average_implied_storage": np.array([1.0] * 3),  # Default value for test
    }
    capex_without_storage = {"solar": sample_capex["solar"], "wind": sample_capex["wind"]}

    accepted_designs, installation_costs, lcoes = filter_designs_according_to_coverage_and_calculate_costs(
        sample_designs,
        baseload_demand,
        capex_without_storage,
        storage_costs,
        sample_opex,  # This is already named correctly as opex_pct in the fixture
        sample_profile,
        cost_of_capital,
        investment_horizon,
        p,
    )

    # Validate accepted designs (2 out of 4; first ones)
    assert len(accepted_designs) == 2
    assert all(isinstance(d, dict) for d in accepted_designs)
    assert accepted_designs[0] == sample_designs[0]
    assert accepted_designs[1] == sample_designs[1]

    # Validate costs and LCOE copied correctly
    assert installation_costs == [10000, 5000]
    assert lcoes == [50.0, 60.0]

    # Validate that mocks were called with expected args
    assert mock_soc.call_count == 3
    assert mock_installation_cost.call_count == 2
    assert mock_lcoe.call_count == 2


# ---------------------------------------------- Test show_optimal_design ------------------------------------------
@pytest.fixture
def sample_data():
    designs = [
        {"solar": 1.0, "wind": 0.8, "battery": 0.5},
        {"solar": 1.2, "wind": 0.6, "battery": 0.4},
    ]
    installation_costs = [10000, 8000]
    lcoes = [55.0, 60.0]  # first one is optimal for LCOE
    profile = {"solar": np.array([1] * 8760), "wind": np.array([1] * 8760)}
    return designs, installation_costs, lcoes, profile


@patch("baseload_optimisation_atlas.boa_logic.plot_state_of_charge_and_cost")  # Patch where used, not where defined
def test_show_optimal_design_logs_correct_output(mock_plot, sample_data):
    designs, installation_costs, lcoes, profile = sample_data

    opt_design, opt_cost = show_optimal_design(designs, installation_costs, lcoes, profile)

    # Check that the plotting function was called once
    assert mock_plot.call_count == 1

    # Check that the correct tuple was returned
    assert opt_design == designs[0]
    assert opt_cost["installation cost"] == installation_costs[0]
    assert opt_cost["LCOE"] == lcoes[0]
