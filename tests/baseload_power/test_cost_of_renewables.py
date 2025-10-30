import pandas as pd
import numpy as np
import pytest
from unittest.mock import patch

from baseload_optimisation_atlas.boa_cost_calculations import (
    calculate_generated_electricity_in_period,
    calculate_lcoe_of_re_installation,
    calculate_lcoe_of_single_re_tech,
)

from baseload_optimisation_atlas.boa_input_preprocessing import (
    correct_ssp_projections_with_current_capacity,
    update_capex_using_learning_curve,
    preprocess_renewable_energy_cost_data,
    preprocess_renewable_energy_capacity_data,
    project_capex,
)


# ---------------------------------------------- Test preprocess_renewable_energy_cost_data ------------------------------------------
def test_preprocess_renewable_energy_cost_data(tmp_path):
    # Create code_df
    code_df = pd.DataFrame(
        {
            "ISO-3 Code": ["USA", "DEU", "CHN"],
        }
    )

    # Create code_to_irena_region_map
    code_to_irena_region_map = {
        "USA": "North America",
        "DEU": "Europe",
        "CHN": "Asia",
    }

    # Create mock Excel files
    master_input_path = tmp_path / "master_input.xlsx"
    renewable_input_path = tmp_path / "renewable_input.xlsx"

    # Create cost of capital data for master_input.xlsx
    cost_of_capital_data = pd.DataFrame(
        {
            "ISO-3 Code": ["USA", "DEU", "CHN"],
            "WACC - Renewables": [3.5, np.nan, 4.5],
        }
    )

    # Create installation costs data
    installation_costs_data = pd.DataFrame(
        {
            "Region": ["North America", "Europe", "Asia"] * 2,
            "Technology": ["Solar"] * 3 + ["Wind"] * 3,
            "Capex": [1000, 1100, 1200, 1500, 1600, 1700],
            "Unit": ["USD/kW"] * 6,
        }
    )

    # Create operational costs data
    operational_costs_data = pd.DataFrame(
        {
            "Region": ["North America", "Europe", "Asia"] * 2,
            "Technology": ["Solar"] * 3 + ["Wind"] * 3,
            "Opex": [0.02, 0.025, 0.03, 0.035, 0.04, 0.045],
            "Unit": ["%"] * 6,
        }
    )

    # Write Excel files
    with pd.ExcelWriter(master_input_path) as writer:
        cost_of_capital_data.to_excel(writer, sheet_name="Cost of capital", index=False)

    with pd.ExcelWriter(renewable_input_path) as writer:
        installation_costs_data.to_excel(writer, sheet_name="Installation costs", index=False)
        operational_costs_data.to_excel(writer, sheet_name="Operational costs", index=False)

    # Call the function
    result = preprocess_renewable_energy_cost_data(
        code_df,
        code_to_irena_region_map,
        master_input_path,
        renewable_input_path,
    )

    # Assertions
    assert isinstance(result, pd.DataFrame)
    assert result.index.name == "ISO-3 Code"
    # Filter out any NaN indices (the function might include regions that don't map to our codes)
    valid_indices = [idx for idx in result.index if pd.notna(idx)]
    assert set(valid_indices) == {"USA", "DEU", "CHN"}

    # Check columns exist
    expected_columns = ["Capex Solar", "Capex Wind", "Opex Solar", "Opex Wind", "Cost of capital (%)"]
    for col in expected_columns:
        assert col in result.columns, f"Column {col} not found in result columns: {result.columns.tolist()}"

    # Check that missing cost of capital was filled with maximum
    assert result.loc["DEU", "Cost of capital (%)"] == 4.5

    # Check that capex values are mapped correctly
    assert result.loc["USA", "Capex Solar"] == 1000
    assert result.loc["DEU", "Capex Solar"] == 1100
    assert result.loc["CHN", "Capex Solar"] == 1200


# ---------------------------------------------- Test preprocess_renewable_energy_capacity_data ------------------------------------------
def test_preprocess_renewable_energy_capacity_data(tmp_path):
    # Create base inputs
    base_year = 2022
    code_df = pd.DataFrame(
        {
            "ISO-3 Code": ["USA", "DEU", "CHN"],
        }
    )

    # Create mappings
    code_to_irena_map = {
        "USA": "United States",
        "DEU": "Germany",
        "CHN": "China",
    }

    code_to_ssp_region_map = {
        "USA": "R5OECD90+EU",
        "DEU": "R5OECD90+EU",
        "CHN": "R5ASIA",
    }

    # Create renewable input path
    renewable_input_path = tmp_path / "renewable_input.xlsx"

    # Create historical capacity data
    historical_capacity_data = pd.DataFrame(
        {
            "Country": ["United States", "Germany", "China"],
            "Capacity Solar": [100000, 50000, 200000],  # MW
            "Capacity Wind": [150000, 60000, 300000],  # MW
            "Unit": ["MW", "MW", "MW"],
        }
    )

    # Create projected capacity data
    projected_capacity_data = pd.DataFrame(
        {
            "Region": ["R5OECD90+EU", "R5OECD90+EU", "R5ASIA", "R5ASIA"],
            "Technology": ["Solar", "Wind", "Solar", "Wind"],
            "Unit": ["GW", "GW", "GW", "GW"],
            2020: [500, 600, 1000, 1200],
            2030: [800, 900, 1500, 1800],
            2040: [1200, 1300, 2000, 2400],
            2050: [1600, 1700, 2500, 3000],
        }
    )

    # Write Excel file
    with pd.ExcelWriter(renewable_input_path) as writer:
        historical_capacity_data.to_excel(writer, sheet_name="Historical capacity", index=False)
        projected_capacity_data.to_excel(writer, sheet_name="Projected capacity", index=False)

    # Call the function
    result = preprocess_renewable_energy_capacity_data(
        base_year,
        code_df,
        code_to_irena_map,
        code_to_ssp_region_map,
        renewable_input_path,
    )

    # Assertions
    assert isinstance(result, pd.DataFrame)
    assert result.index.names == ["ISO-3 Code", "Technology"]

    # Check that all countries and technologies are present
    expected_index = pd.MultiIndex.from_product(
        [["CHN", "DEU", "USA"], ["Solar", "Wind"]], names=["ISO-3 Code", "Technology"]
    )
    assert set(result.index) == set(expected_index)

    # Check columns (years) - should include base_year and projection years
    assert base_year in result.columns  # 2022
    assert 2030 in result.columns
    assert 2040 in result.columns
    assert 2050 in result.columns

    # Verify some values are reasonable (capacity should increase over time)
    assert result.loc[("USA", "Solar"), 2030] > result.loc[("USA", "Solar"), base_year]
    assert result.loc[("CHN", "Wind"), 2050] > result.loc[("CHN", "Wind"), 2040]


# ---------------------------------------------- Test correct_ssp_projections_with_current_capacity ------------------------------------------
def test_correct_ssp_projections_with_current_capacity():
    # Create projected renewable generation capacity with MultiIndex
    proj_renewable_generation_capacity = pd.DataFrame(
        {
            2020: [500, 600, 1000, 1200],  # GW
            2030: [800, 900, 1500, 1800],
            2040: [1200, 1300, 2000, 2400],
            2050: [1600, 1700, 2500, 3000],
        },
        index=pd.MultiIndex.from_tuples(
            [
                ("USA", "Solar"),
                ("USA", "Wind"),
                ("CHN", "Solar"),
                ("CHN", "Wind"),
            ],
            names=["ISO-3 Code", "Technology"],
        ),
    )

    # Create historical capacity data
    hist_capacity_iso3 = pd.DataFrame(
        {
            "Capacity Solar": [100000, 200000],  # MW (will be converted to GW)
            "Capacity Wind": [150000, 300000],  # MW
        },
        index=pd.Index(["USA", "CHN"], name="ISO-3 Code"),
    )

    base_year = 2022

    # Call the function
    result = correct_ssp_projections_with_current_capacity(
        proj_renewable_generation_capacity,
        hist_capacity_iso3,
        base_year,
    )

    # Assertions
    assert isinstance(result, pd.DataFrame)
    assert not result.empty
    assert result.index.names == ["ISO-3 Code", "Technology"]

    # Check that the base year column exists and matches historical capacity
    assert base_year in result.columns
    assert np.isclose(result.loc[("USA", "Solar"), base_year], 100000)  # MW
    assert np.isclose(result.loc[("USA", "Wind"), base_year], 150000)  # MW
    assert np.isclose(result.loc[("CHN", "Solar"), base_year], 200000)  # MW
    assert np.isclose(result.loc[("CHN", "Wind"), base_year], 300000)  # MW

    # Check that projections are adjusted based on the base year correction
    # The ratio between 2022 and 2020 should be applied to future projections
    assert result.loc[("USA", "Solar"), 2030] != proj_renewable_generation_capacity.loc[("USA", "Solar"), 2030]

    # Verify projections increase over time
    assert result.loc[("USA", "Solar"), 2030] > result.loc[("USA", "Solar"), base_year]
    assert result.loc[("CHN", "Wind"), 2050] > result.loc[("CHN", "Wind"), 2040]


# ---------------------------------------------- Test update_capex_using_learning_curve ------------------------------------------
def test_update_capex_standard_input():
    capacity_0 = 100
    capacity_t = 200
    capex_0 = 1000
    lr = 0.2
    result = update_capex_using_learning_curve(capacity_0, capacity_t, capex_0, lr)
    # Expected result is 1000 * (200/100)^((np.log(0.8)/np.log(2)) = 800
    expected_result = 800
    assert isinstance(result, float)
    assert np.isclose(result, expected_result, rtol=1e-3)


def test_update_capex_zero_initial_capacity():
    capacity_0 = 0
    capacity_t = 200
    capex_0 = 1000
    lr = 0.2
    result = update_capex_using_learning_curve(capacity_0, capacity_t, capex_0, lr)
    assert isinstance(result, float)
    assert result < capex_0 / 5  # Capex should be significantly lower than initial value
    assert result > 0  # Capex should be positive


def test_update_capex_no_capacity_change():
    capacity_0 = 100
    capacity_t = 100
    capex_0 = 1000
    lr = 0.2
    result = update_capex_using_learning_curve(capacity_0, capacity_t, capex_0, lr)
    assert np.isclose(result, capex_0)  # Capex should remain unchanged


def test_update_capex_no_learning_rate():
    capacity_0 = 100
    capacity_t = 200
    capex_0 = 1000
    lr = 0.0
    result = update_capex_using_learning_curve(capacity_0, capacity_t, capex_0, lr)
    assert np.isclose(result, capex_0)  # Capex should remain unchanged


def test_update_capex_large_learning_rate():
    capacity_0 = 100
    capacity_t = 200
    capex_0 = 1000
    lr = 0.99
    result = update_capex_using_learning_curve(capacity_0, capacity_t, capex_0, lr)
    # Expected result is 1000 * (200/100)^((np.log(0.2)/np.log(2)) = 200; significant decrease in capex expected
    expected_result = 10
    assert np.isclose(result, expected_result, rtol=1e-3)


def test_update_capex_low_learning_rate():
    capacity_0 = 100
    capacity_t = 200
    capex_0 = 1000
    lr = 0.01
    result = update_capex_using_learning_curve(capacity_0, capacity_t, capex_0, lr)
    # Expected result is 1000 * (200/100)^((np.log(0.99)/np.log(2))
    expected_result = 1000 * (200 / 100) ** (np.log(0.99) / np.log(2))
    assert np.isclose(result, expected_result, rtol=1e-3)


def test_update_capex_capacity_decrease():
    capacity_0 = 200
    capacity_t = 100
    capex_0 = 1000
    lr = 0.2
    result = update_capex_using_learning_curve(capacity_0, capacity_t, capex_0, lr)
    # Capex should increase as capacity decreases
    assert result > capex_0


# ---------------------------------------------- Test calculate_generated_electricity_in_period ------------------------------------------
def test_calculate_generated_electricity_in_period_standard_inputs():
    time_period = 20
    re_potential = 0.3  # 30% capacity factor
    installed_capacity = 100  # MW
    deterioration_rate = 0.01  # 1% deterioration per year
    downtime = 10  # 10 days of downtime per year

    result = calculate_generated_electricity_in_period(
        time_period, re_potential, installed_capacity, deterioration_rate, downtime
    )

    assert isinstance(result, np.ndarray)
    assert len(result) == time_period
    assert np.isclose(result[0], 0.3 * 100 * 8760 * (1 - 10 / 365))  # First year generation
    assert result[1] < result[0]  # Generation decreases over time
    assert result[-1] < result[0]  # Last year generates less than the first year


def test_calculate_generated_electricity_in_period_no_deterioration():
    time_period = 10
    re_potential = 0.4
    installed_capacity = 50
    deterioration_rate = 0.0
    downtime = 0

    result = calculate_generated_electricity_in_period(
        time_period, re_potential, installed_capacity, deterioration_rate, downtime
    )

    assert all(np.isclose(gen, 0.4 * 50 * 8760) for gen in result)  # Constant generation after installation


def test_calculate_generated_electricity_in_period_full_capacity_factor():
    time_period = 5
    re_potential = 1.0
    installed_capacity = 10
    deterioration_rate = 0.05
    downtime = 0

    result = calculate_generated_electricity_in_period(
        time_period, re_potential, installed_capacity, deterioration_rate, downtime
    )

    assert np.isclose(result[0], 1.0 * 10 * 8760)  # First year at full capacity
    assert result[-1] < result[0]  # Last year generates less due to deterioration


def test_calculate_generated_electricity_in_period_zero_time_period():
    time_period = 0
    re_potential = 0.5
    installed_capacity = 100
    deterioration_rate = 0.02
    downtime = 5

    with pytest.raises(ValueError):  # Expect a ValueError for zero time period
        calculate_generated_electricity_in_period(
            time_period, re_potential, installed_capacity, deterioration_rate, downtime
        )


def test_calculate_generated_electricity_in_period_zero_capacity_factor():
    time_period = 10
    re_potential = 0.0
    installed_capacity = 100
    deterioration_rate = 0.02
    downtime = 0

    result = calculate_generated_electricity_in_period(
        time_period, re_potential, installed_capacity, deterioration_rate, downtime
    )

    assert all(np.isclose(gen, 0.0) for gen in result)  # No generation for all years


def test_calculate_generated_electricity_in_period_extreme_deterioration_rate():
    time_period = 10
    re_potential = 0.5
    installed_capacity = 100
    deterioration_rate = 1.0  # 100% deterioration per year
    downtime = 0

    result = calculate_generated_electricity_in_period(
        time_period, re_potential, installed_capacity, deterioration_rate, downtime
    )

    assert result[0] == 0.5 * 100 * 8760  # First year generates electricity
    assert all(np.isclose(gen, 0.0) for gen in result[1:])  # All subsequent years generate nothing


def test_calculate_generated_electricity_in_period_large_time_period():
    time_period = 100
    re_potential = 0.25
    installed_capacity = 50
    deterioration_rate = 0.01
    downtime = 0

    result = calculate_generated_electricity_in_period(
        time_period, re_potential, installed_capacity, deterioration_rate, downtime
    )

    assert len(result) == 100
    assert result[-1] < result[0]  # Last year generates less electricity


# ---------------------------------------------- Test calculate_lcoe_of_re_installation ------------------------------------------
@pytest.fixture
def mock_generated_electricity_in_period():
    # Set to half of the Excel values since per tech here and full in Excel (solar + wind)
    return [5500000] + [5000000] * 24


@pytest.fixture
def mock_lcoe_inputs():
    investment_horizon = 25  # Max lifetime
    installed_capacity = {"solar": 1000, "wind": 1000, "battery": 2000}
    capex = {
        "solar": [1000000] * (investment_horizon + 1),
        "wind": [1200000] * (investment_horizon + 1),
        "battery": [800000] * (investment_horizon + 1),
    }
    opex_pct = {"solar": 0.01, "wind": 0.02, "battery": 0.02}
    renewable_energy_profile = {  # Irrelevant since the generated electricity is mocked
        "solar": [1] * 8760,
        "wind": [1] * 8760,
    }
    cost_of_capital = 0.05
    return investment_horizon, installed_capacity, capex, opex_pct, renewable_energy_profile, cost_of_capital


@patch(
    "baseload_optimisation_atlas.boa_config.YEARLY_DETERIORATION_RATES",
    {"solar": 0.005, "wind": 0.01, "battery": 0.005},
)
@patch("baseload_optimisation_atlas.boa_config.LIFETIMES", {"solar": 25, "wind": 25, "battery": 13})
@patch("baseload_optimisation_atlas.boa_constants.HOURS_IN_YEAR", 8760)
def test_lcoe_without_curtailment_std_input(mock_generated_electricity_in_period, mock_lcoe_inputs):
    (
        investment_horizon,
        installed_capacity,
        capex,
        opex_pct,
        renewable_energy_profile,
        cost_of_capital,
    ) = mock_lcoe_inputs
    baseload_demand = 1000.0
    with patch(
        "baseload_optimisation_atlas.boa_cost_calculations.calculate_generated_electricity_in_period",
        return_value=mock_generated_electricity_in_period,
    ):
        lcoe = calculate_lcoe_of_re_installation(
            investment_horizon,
            installed_capacity,
            baseload_demand,
            capex,
            opex_pct,
            renewable_energy_profile,
            cost_of_capital,
            use_curtailment=False,
        )
    assert np.isclose(lcoe, 32.28, rtol=1e-2)  # Check if LCOE is close to expected value


@patch(
    "baseload_optimisation_atlas.boa_config.YEARLY_DETERIORATION_RATES",
    {"solar": 0.005, "wind": 0.01, "battery": 0.005},
)
@patch("baseload_optimisation_atlas.boa_config.LIFETIMES", {"solar": 25, "wind": 25, "battery": 13})
@patch("baseload_optimisation_atlas.boa_constants.HOURS_IN_YEAR", 8760)
def test_lcoe_with_curtailment_std_input(mock_generated_electricity_in_period, mock_lcoe_inputs):
    (
        investment_horizon,
        installed_capacity,
        capex,
        opex_pct,
        renewable_energy_profile,
        cost_of_capital,
    ) = mock_lcoe_inputs
    baseload_demand = 1000.0

    with patch(
        "baseload_optimisation_atlas.boa_cost_calculations.calculate_generated_electricity_in_period",
        return_value=mock_generated_electricity_in_period,
    ):
        lcoe = calculate_lcoe_of_re_installation(
            investment_horizon,
            installed_capacity,
            baseload_demand,
            capex,
            opex_pct,
            renewable_energy_profile,
            cost_of_capital,
            use_curtailment=True,
        )
    assert np.isclose(lcoe, 37.09, rtol=1e-2)  # Check if LCOE is close to expected value


@patch(
    "baseload_optimisation_atlas.boa_config.YEARLY_DETERIORATION_RATES",
    {"solar": 0.005, "wind": 0.01, "battery": 0.005},
)
@patch("baseload_optimisation_atlas.boa_config.LIFETIMES", {"solar": 30, "wind": 25, "battery": 15})
@patch("baseload_optimisation_atlas.boa_constants.HOURS_IN_YEAR", 8760)
def test_lcoe_high_cost_of_capital(mock_generated_electricity_in_period, mock_lcoe_inputs):
    (
        investment_horizon,
        installed_capacity,
        capex,
        opex_pct,
        renewable_energy_profile,
        cost_of_capital,
    ) = mock_lcoe_inputs
    baseload_demand = 1000.0

    cost_of_capital = 0.5  # High cost of capital (50%)

    with patch(
        "baseload_optimisation_atlas.boa_cost_calculations.calculate_generated_electricity_in_period",
        return_value=mock_generated_electricity_in_period,
    ):
        lcoe = calculate_lcoe_of_re_installation(
            investment_horizon,
            installed_capacity,
            baseload_demand,
            capex,
            opex_pct,
            renewable_energy_profile,
            cost_of_capital,
            use_curtailment=True,
        )
    assert lcoe > 100  # LCOE should be significantly higher with high cost of capital


@patch(
    "baseload_optimisation_atlas.boa_config.YEARLY_DETERIORATION_RATES",
    {"solar": 0.005, "wind": 0.01, "battery": 0.005},
)
@patch("baseload_optimisation_atlas.boa_config.LIFETIMES", {"solar": 30, "wind": 25, "battery": 15})
@patch("baseload_optimisation_atlas.boa_constants.HOURS_IN_YEAR", 8760)
def test_lcoe_zero_cost_of_capital(mock_generated_electricity_in_period, mock_lcoe_inputs):
    (
        investment_horizon,
        installed_capacity,
        capex,
        opex_pct,
        renewable_energy_profile,
        cost_of_capital,
    ) = mock_lcoe_inputs
    baseload_demand = 1000.0

    cost_of_capital = 0.0  # Zero cost of capital

    with patch(
        "baseload_optimisation_atlas.boa_cost_calculations.calculate_generated_electricity_in_period",
        return_value=mock_generated_electricity_in_period,
    ):
        lcoe = calculate_lcoe_of_re_installation(
            investment_horizon,
            installed_capacity,
            baseload_demand,
            capex,
            opex_pct,
            renewable_energy_profile,
            cost_of_capital,
            use_curtailment=True,
        )
    assert lcoe < 35  # LCOE should be lower with zero cost of capital


@patch(
    "baseload_optimisation_atlas.boa_config.YEARLY_DETERIORATION_RATES",
    {"solar": 0.0025, "wind": 0.005, "battery": 0.0025},
)
@patch("baseload_optimisation_atlas.boa_config.LIFETIMES", {"solar": 60, "wind": 50, "battery": 30})
@patch("baseload_optimisation_atlas.boa_constants.HOURS_IN_YEAR", 8760)
def test_lcoe_long_lasting_equipment(mock_generated_electricity_in_period, mock_lcoe_inputs):
    (
        investment_horizon,
        installed_capacity,
        capex,
        opex_pct,
        renewable_energy_profile,
        cost_of_capital,
    ) = mock_lcoe_inputs
    baseload_demand = 1000.0

    cost_of_capital = 0.0  # Zero cost of capital

    with patch(
        "baseload_optimisation_atlas.boa_cost_calculations.calculate_generated_electricity_in_period",
        return_value=mock_generated_electricity_in_period,
    ):
        lcoe = calculate_lcoe_of_re_installation(
            investment_horizon,
            installed_capacity,
            baseload_demand,
            capex,
            opex_pct,
            renewable_energy_profile,
            cost_of_capital,
            use_curtailment=True,
        )
    assert lcoe < 35  # LCOE should be lower if the equipment lasts x2 longer


# ---------------------------------------------- Test calculate_lcoe_of_single_re_tech ------------------------------------------
def test_calculate_lcoe_of_single_re_tech_standard_input():
    generated_electricity = [8760, 8700, 8600]  # Electricity in MWh
    fixed_opex_percentage = 0.01  # 1%
    cost_of_capital = 0.03  # 3%
    capex_0 = 1500  # USD/MW
    capex_t = [900, 850, 800]  # Declining CAPEX over years
    curtailment = [0.5, 0.6, 0.7]  # High curtailment (50%, 60%, 70%)

    lcoe = calculate_lcoe_of_single_re_tech(
        generated_electricity,
        fixed_opex_percentage,
        cost_of_capital,
        capex_0,
        capex_t,
        curtailment=curtailment,
    )

    expected_lcoe = 0.154044
    assert isinstance(lcoe, float)
    assert lcoe > 0  # LCOE must be positive
    assert np.isclose(lcoe, expected_lcoe, rtol=1e-2)  # Check if LCOE is close to expected value


def test_calculate_lcoe_of_single_re_tech_no_curtailment():
    generated_electricity = [8760, 8700, 8600]  # Yearly electricity in MWh (for 3 years)
    fixed_opex_percentage = 0.02  # 2%
    cost_of_capital = 0.05  # 5%
    capex_0 = 1000  # USD/MW
    capex_t = [900, 850, 800]  # Declining CAPEX over years
    curtailment = None  # No curtailment

    lcoe = calculate_lcoe_of_single_re_tech(
        generated_electricity,
        fixed_opex_percentage,
        cost_of_capital,
        capex_0,
        capex_t,
        curtailment,
    )

    expected_lcoe = 0.04422
    assert isinstance(lcoe, float)
    assert np.isclose(lcoe, expected_lcoe, rtol=1e-2)  # Check if LCOE is close to expected value


def test_calculate_lcoe_of_single_re_tech_fixed_capex():
    generated_electricity = [8760, 8760, 8760]  # Full yearly electricity generation
    fixed_opex_percentage = 0.01  # 1%
    cost_of_capital = 0.03  # 3%
    capex_0 = 1500  # USD/MW
    capex_t = None  # Use default CAPEX per year
    curtailment = None  # No curtailment

    lcoe = calculate_lcoe_of_single_re_tech(
        generated_electricity,
        fixed_opex_percentage,
        cost_of_capital,
        capex_0,
        capex_t,
        curtailment,
    )

    expected_lcoe = 0.062762
    assert isinstance(lcoe, float)
    assert np.isclose(lcoe, expected_lcoe, rtol=1e-2)  # Check if LCOE is close to expected value


def test_calculate_lcoe_of_single_re_tech_zero_generated_electricity():
    generated_electricity = [0, 0, 0]  # No electricity generated
    fixed_opex_percentage = 0.02  # 2%
    cost_of_capital = 0.05  # 5%
    capex_0 = 1000  # USD/MW

    with pytest.raises(ZeroDivisionError):  # Should raise a division by zero error
        calculate_lcoe_of_single_re_tech(
            generated_electricity,
            fixed_opex_percentage,
            cost_of_capital,
            capex_0,
        )


def test_calculate_lcoe_of_single_re_tech_zero_cost_of_capital():
    generated_electricity = [8760, 8700, 8600]  # Declining electricity generation
    fixed_opex_percentage = 0.01  # 1%
    cost_of_capital = 0.0  # No discount rate
    capex_0 = 1500  # USD/MW
    capex_t = None  # Default CAPEX
    curtailment = None  # No curtailment

    lcoe = calculate_lcoe_of_single_re_tech(
        generated_electricity,
        fixed_opex_percentage,
        cost_of_capital,
        capex_0,
        capex_t,
        curtailment,
    )

    expected_lcoe = 0.05928
    assert isinstance(lcoe, float)
    assert np.isclose(lcoe, expected_lcoe, rtol=1e-3)  # Check if LCOE is close to expected value


# ---------------------------------------------- Test project_capex ------------------------------------------
def test_project_capex():
    # Create mock costs DataFrame
    costs = pd.DataFrame(
        {
            "Capex solar": [1000, 1200],  # USD/kW
            "Capex wind": [1500, 1800],  # USD/kW
        },
        index=pd.Index(["USA", "CHN"], name="ISO-3 Code"),
    )

    # Create capacity projections with MultiIndex
    capacity_projections = pd.DataFrame(
        {
            2020: [100, 150, 200, 300],  # GW
            2025: [200, 250, 400, 500],
            2030: [400, 450, 800, 900],
            2035: [600, 650, 1200, 1300],
        },
        index=pd.MultiIndex.from_tuples(
            [
                ("USA", "solar"),
                ("USA", "wind"),
                ("CHN", "solar"),
                ("CHN", "wind"),
            ],
            names=["ISO-3 Code", "Technology"],
        ),
    )

    base_year = 2020

    # Call the function
    result = project_capex(
        costs=costs,
        capacity_projections=capacity_projections,
        base_year=base_year,
    )

    # Assertions
    assert isinstance(result, pd.DataFrame)
    assert not result.empty
    assert result.index.names == ["ISO-3 Code", "Technology"]

    # Check that all countries and technologies are present
    expected_index = capacity_projections.index
    assert set(result.index) == set(expected_index)

    # Check columns match capacity projection years
    assert list(result.columns) == list(capacity_projections.columns)

    # Verify base year CAPEX matches the input costs
    assert result.loc[("USA", "solar"), base_year] == 1000
    assert result.loc[("CHN", "solar"), base_year] == 1200
    assert result.loc[("USA", "wind"), base_year] == 1500
    assert result.loc[("CHN", "wind"), base_year] == 1800

    # Verify CAPEX decreases over time due to learning curve
    # (assuming capacity increases, which it does in our test data)
    assert result.loc[("USA", "solar"), 2025] < result.loc[("USA", "solar"), base_year]
    assert result.loc[("CHN", "wind"), 2035] < result.loc[("CHN", "wind"), 2030]


# ---------------------------------------------- Test get_lcoe_across_all_dims ------------------------------------------
@pytest.fixture
def mock_calculate_generated_electricity_in_period():
    with patch(
        "baseload_optimisation_atlas.boa_cost_calculations.calculate_generated_electricity_in_period",
        return_value=[8760, 8700, 8600],
    ) as mock_func:
        yield mock_func


@pytest.fixture
def mock_calculate_lcoe_of_single_re_tech():
    with patch(
        "baseload_optimisation_atlas.boa_cost_calculations.calculate_lcoe_of_single_re_tech", return_value=0.15
    ) as mock_func:
        yield mock_func


@pytest.fixture
def mock_inputs_lcoe_projection():
    capacity_fact = pd.DataFrame(
        {
            "Country1": [0.3],
            "Country2": [0.4],
        },
        index=["Capacity factor"],
    ).transpose()

    capex_projection = pd.DataFrame(
        {
            2020: [1000, 1200, 1100, 1300, 1000, 1200, 1000, 1200, 1100, 1300, 1000, 1200],
            2021: [900, 1100, 1000, 1200, 900, 1100, 900, 1100, 1000, 1200, 900, 1100],
            2022: [800, 1000, 900, 1100, 800, 1000, 800, 1000, 900, 1100, 800, 1000],
            2023: [700, 900, 800, 1000, 700, 900, 700, 900, 800, 1000, 700, 900],
            2024: [600, 800, 700, 900, 600, 800, 600, 800, 700, 900, 600, 800],
            2025: [500, 700, 600, 800, 500, 700, 500, 700, 600, 800, 500, 700],
        },
        index=pd.MultiIndex.from_product(
            [["Country1", "Country2"], ["SSP1", "SSP2"], ["low cost", "average cost", "high cost"]],
            names=["Country", "Scenario", "LR Scenario"],
        ),
    )

    return capacity_fact, capex_projection
