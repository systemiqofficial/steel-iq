import pytest
import numpy as np
import pandas as pd
import xarray as xr
from unittest.mock import Mock, patch
from steelo.adapters.geospatial.priority_kpi import (
    calculate_outgoing_cashflow_proxy,
    extract_priority_locations,
    find_top_locations_per_country,
    calculate_priority_location_kpi,
)


class TestCalculateOutgoingCashflowProxy:
    """Test suite for calculate_outgoing_cashflow_proxy function."""

    @pytest.fixture
    def mock_dataset(self):
        """Create a minimal mock xarray Dataset."""
        lat = np.array([10.0, 20.0])
        lon = np.array([100.0, 110.0])

        return xr.Dataset(
            {
                "power_price": xr.DataArray(
                    np.array([[0.1, 0.12], [0.11, 0.13]]), coords={"lat": lat, "lon": lon}, dims=["lat", "lon"]
                ),
                "rail_cost": xr.DataArray(
                    np.array([[1000, 2000], [1200, 1800]]), coords={"lat": lat, "lon": lon}, dims=["lat", "lon"]
                ),
                "feedstock_transportation_cost_per_ton_steel": xr.DataArray(
                    np.array([[10, 15], [12, 18]]), coords={"lat": lat, "lon": lon}, dims=["lat", "lon"]
                ),
                "demand_transportation_cost_per_ton_steel": xr.DataArray(
                    np.array([[5, 8], [6, 9]]), coords={"lat": lat, "lon": lon}, dims=["lat", "lon"]
                ),
                "feedstock_transportation_cost_per_ton_iron": xr.DataArray(
                    np.array([[8.0, 12.0], [10.0, 14.0]]), coords={"lat": lat, "lon": lon}, dims=["lat", "lon"]
                ),
                "demand_transportation_cost_per_ton_iron": xr.DataArray(
                    np.array([[4.0, 6.0], [5.0, 7.0]]), coords={"lat": lat, "lon": lon}, dims=["lat", "lon"]
                ),
                "landtype_factor": xr.DataArray(
                    np.array([[1.0, 1.2], [1.1, 1.3]]), coords={"lat": lat, "lon": lon}, dims=["lat", "lon"]
                ),
                "feasibility_mask": xr.DataArray(np.ones((2, 2)), coords={"lat": lat, "lon": lon}, dims=["lat", "lon"]),
            }
        )

    @pytest.fixture
    def mock_geo_config(self):
        """Create a mock GeoConfig object."""
        config = Mock()
        config.included_power_mix = "85% baseload + 15% grid"
        config.include_infrastructure_cost = True
        config.include_transport_cost = True
        config.include_lulc_cost = True
        config.iron_ore_steel_ratio = 1.6
        return config

    @pytest.fixture
    def mock_geo_paths(self):
        """Create a mock GeoDataPaths object."""
        paths = Mock()
        paths.geo_plots_dir = "/mock/plots"
        return paths

    @patch("steelo.adapters.geospatial.priority_kpi.plot_screenshot")
    def test_calculate_outgoing_cashflow_basic_steel(self, mock_plot, mock_dataset, mock_geo_config, mock_geo_paths):
        """Test basic functionality for steel production."""
        capex = 500.0
        capex_share = 0.7
        energy_consumption_per_t = 1.0  # MWh/t for steel
        steel_plant_capacity = 1000000  # tonnes
        plant_lifetime = 25  # years

        result = calculate_outgoing_cashflow_proxy(
            data=mock_dataset,
            year=2030,
            capex=capex,
            product="steel",
            capex_share=capex_share,
            energy_consumption_per_t=energy_consumption_per_t,
            baseload_coverage=0.85,
            steel_plant_capacity=steel_plant_capacity,
            plant_lifetime=plant_lifetime,
            geo_config=mock_geo_config,
            geo_paths=mock_geo_paths,
        )

        # Calculate expected values
        MWH_TO_KWH = 1e3  # 1 MWh = 1000 kWh
        expected_capex = capex * capex_share * steel_plant_capacity * mock_dataset["landtype_factor"]
        # Total energy: MWh/t * t = MWh, then MWh * 1000 = kWh
        total_energy_mwh = energy_consumption_per_t * steel_plant_capacity
        expected_power = mock_dataset["power_price"] * total_energy_mwh * MWH_TO_KWH * plant_lifetime
        expected_rail = mock_dataset["rail_cost"]
        expected_feedstock = (
            mock_dataset["feedstock_transportation_cost_per_ton_steel"] * steel_plant_capacity * plant_lifetime
        )
        expected_demand = (
            mock_dataset["demand_transportation_cost_per_ton_steel"] * steel_plant_capacity * plant_lifetime
        )
        expected_total = expected_capex + expected_power + expected_rail + expected_feedstock + expected_demand

        assert isinstance(result, xr.DataArray)
        assert result.shape == (2, 2)
        assert (result > 0).all()
        assert np.allclose(result.values, expected_total.values, rtol=1e-5)
        assert mock_plot.call_count == 2

    @patch("steelo.adapters.geospatial.priority_kpi.plot_screenshot")
    def test_calculate_outgoing_cashflow_basic_iron(self, mock_plot, mock_dataset, mock_geo_config, mock_geo_paths):
        """Test basic functionality for iron production."""
        capex = 400.0
        capex_share = 0.3
        energy_consumption_per_t = 3.0  # MWh/t for iron
        steel_plant_capacity = 1000000  # tonnes
        plant_lifetime = 25  # years

        result = calculate_outgoing_cashflow_proxy(
            data=mock_dataset,
            year=2030,
            capex=capex,
            product="iron",
            capex_share=capex_share,
            energy_consumption_per_t=energy_consumption_per_t,
            baseload_coverage=0.85,
            steel_plant_capacity=steel_plant_capacity,
            plant_lifetime=plant_lifetime,
            geo_config=mock_geo_config,
            geo_paths=mock_geo_paths,
        )

        # Calculate expected values
        MWH_TO_KWH = 1e3  # 1 MWh = 1000 kWh
        expected_capex = capex * capex_share * steel_plant_capacity * mock_dataset["landtype_factor"]
        # Total energy: MWh/t * t = MWh, then MWh * 1000 = kWh
        total_energy_mwh = energy_consumption_per_t * steel_plant_capacity
        expected_power = mock_dataset["power_price"] * total_energy_mwh * MWH_TO_KWH * plant_lifetime
        expected_rail = mock_dataset["rail_cost"]
        # For iron, feedstock transport is multiplied by iron_ore_steel_ratio
        expected_feedstock = (
            mock_dataset["feedstock_transportation_cost_per_ton_iron"]
            * steel_plant_capacity
            * plant_lifetime
            * mock_geo_config.iron_ore_steel_ratio
        )
        expected_demand = (
            mock_dataset["demand_transportation_cost_per_ton_iron"] * steel_plant_capacity * plant_lifetime
        )
        expected_total = expected_capex + expected_power + expected_rail + expected_feedstock + expected_demand

        assert isinstance(result, xr.DataArray)
        assert result.shape == (2, 2)
        assert (result > 0).all()
        assert np.allclose(result.values, expected_total.values, rtol=1e-5)

    @patch("steelo.adapters.geospatial.priority_kpi.plot_screenshot")
    def test_calculate_outgoing_cashflow_no_power(self, mock_plot, mock_dataset, mock_geo_config, mock_geo_paths):
        """Test when power is not included."""
        mock_geo_config.included_power_mix = "Not included"

        capex = 500.0
        capex_share = 0.7
        energy_consumption_per_t = 1.0  # MWh/t for steel
        steel_plant_capacity = 1000000  # tonnes
        plant_lifetime = 25  # years

        result = calculate_outgoing_cashflow_proxy(
            data=mock_dataset,
            year=2030,
            capex=capex,
            product="steel",
            capex_share=capex_share,
            energy_consumption_per_t=energy_consumption_per_t,
            baseload_coverage=0.85,
            steel_plant_capacity=steel_plant_capacity,
            plant_lifetime=plant_lifetime,
            geo_config=mock_geo_config,
            geo_paths=mock_geo_paths,
        )

        # Calculate expected values (no power component)
        expected_capex = capex * capex_share * steel_plant_capacity * mock_dataset["landtype_factor"]
        expected_rail = mock_dataset["rail_cost"]
        expected_feedstock = (
            mock_dataset["feedstock_transportation_cost_per_ton_steel"] * steel_plant_capacity * plant_lifetime
        )
        expected_demand = (
            mock_dataset["demand_transportation_cost_per_ton_steel"] * steel_plant_capacity * plant_lifetime
        )
        expected_total = expected_capex + expected_rail + expected_feedstock + expected_demand

        assert isinstance(result, xr.DataArray)
        assert (result > 0).all()
        assert np.allclose(result.values, expected_total.values, rtol=1e-5)

    @patch("steelo.adapters.geospatial.priority_kpi.plot_screenshot")
    def test_calculate_outgoing_cashflow_minimal_config(self, mock_plot, mock_dataset, mock_geo_config, mock_geo_paths):
        """Test with minimal configuration (only CAPEX)."""
        mock_geo_config.included_power_mix = "Not included"
        mock_geo_config.include_infrastructure_cost = False
        mock_geo_config.include_transport_cost = False
        mock_geo_config.include_lulc_cost = False

        result = calculate_outgoing_cashflow_proxy(
            data=mock_dataset,
            year=2030,
            capex=500.0,
            product="steel",
            capex_share=1.0,
            energy_consumption_per_t=0.0,
            baseload_coverage=0.0,
            steel_plant_capacity=1000000,
            plant_lifetime=25,
            geo_config=mock_geo_config,
            geo_paths=mock_geo_paths,
        )

        assert isinstance(result, xr.DataArray)
        assert (result > 0).all()
        # All values should be the same (uniform CAPEX)
        expected = 500.0 * 1.0 * 1000000
        assert np.allclose(result.values, expected)

    @patch("steelo.adapters.geospatial.priority_kpi.plot_screenshot")
    def test_calculate_outgoing_cashflow_zero_error(self, mock_plot, mock_geo_config, mock_geo_paths):
        """Test that all-zero cashflow raises an error."""
        lat = np.array([10.0])
        lon = np.array([100.0])

        zero_dataset = xr.Dataset(
            {
                "landtype_factor": xr.DataArray(np.zeros((1, 1)), coords={"lat": lat, "lon": lon}, dims=["lat", "lon"]),
                "feasibility_mask": xr.DataArray(np.ones((1, 1)), coords={"lat": lat, "lon": lon}, dims=["lat", "lon"]),
            }
        )

        mock_geo_config.included_power_mix = "Not included"
        mock_geo_config.include_infrastructure_cost = False
        mock_geo_config.include_transport_cost = False
        mock_geo_config.include_lulc_cost = True

        with pytest.raises(ValueError, match="All locations have zero outgoing cashflow"):
            calculate_outgoing_cashflow_proxy(
                data=zero_dataset,
                year=2030,
                capex=0.0,  # Zero CAPEX causes error
                product="steel",
                capex_share=0.7,
                energy_consumption_per_t=0.0,
                baseload_coverage=0.0,
                steel_plant_capacity=1000000,
                plant_lifetime=25,
                geo_config=mock_geo_config,
                geo_paths=mock_geo_paths,
            )


class TestExtractPriorityLocations:
    """Test suite for extract_priority_locations function."""

    @pytest.fixture
    def mock_dataset_priority(self):
        """Create a mock dataset for priority location extraction."""
        lat = np.array([10.0, 20.0, 30.0])
        lon = np.array([100.0, 110.0, 120.0])

        # Create varied data for testing
        test_var = np.array([[1.0, 5.0, 3.0], [2.0, 8.0, 4.0], [6.0, 9.0, 7.0]])

        return xr.Dataset(
            {
                "test_variable": xr.DataArray(test_var, coords={"lat": lat, "lon": lon}, dims=["lat", "lon"]),
                "feasibility_mask": xr.DataArray(np.ones((3, 3)), coords={"lat": lat, "lon": lon}, dims=["lat", "lon"]),
            }
        )

    def test_extract_priority_locations_top_percent(self, mock_dataset_priority):
        """Test extraction of top percentage of locations (high values = high priority)."""
        top_values, locations_df = extract_priority_locations(
            ds=mock_dataset_priority,
            var_name="test_variable",
            top_pct=33,  # Top 33% = 3 out of 9 values
            random_seed=42,
            invert=False,
        )

        # Should select the top 3 values: 9, 8, 7
        assert isinstance(top_values, xr.DataArray)
        assert isinstance(locations_df, pd.DataFrame)
        assert top_values.shape == (3, 3)

        # Check that exactly 3 locations are selected
        assert top_values.sum().values == 3
        assert len(locations_df) == 3

        # Check that selected values are exactly 9, 8, 7
        selected_values = mock_dataset_priority["test_variable"].where(top_values == 1).values
        selected_values_flat = selected_values[~np.isnan(selected_values)]
        selected_values_sorted = np.sort(selected_values_flat)
        expected_values = np.array([7.0, 8.0, 9.0])
        assert np.array_equal(selected_values_sorted, expected_values)

    def test_extract_priority_locations_inverted(self, mock_dataset_priority):
        """Test extraction with inversion (low values = high priority)."""
        top_values, locations_df = extract_priority_locations(
            ds=mock_dataset_priority,
            var_name="test_variable",
            top_pct=33,  # Top 33% = 3 out of 9 values
            random_seed=42,
            invert=True,
        )

        # Should select the bottom 3 values: 1, 2, 3
        assert isinstance(top_values, xr.DataArray)
        assert isinstance(locations_df, pd.DataFrame)
        assert top_values.sum().values == 3
        assert len(locations_df) == 3

        # Check that selected values are exactly 1, 2, 3
        selected_values = mock_dataset_priority["test_variable"].where(top_values == 1).values
        selected_values_flat = selected_values[~np.isnan(selected_values)]
        selected_values_sorted = np.sort(selected_values_flat)
        expected_values = np.array([1.0, 2.0, 3.0])
        assert np.array_equal(selected_values_sorted, expected_values)

    def test_extract_priority_locations_uniform_distribution(self):
        """Test with uniform distribution (all values the same)."""
        lat = np.array([10.0, 20.0])
        lon = np.array([100.0, 110.0])

        uniform_dataset = xr.Dataset(
            {
                "uniform_var": xr.DataArray(
                    np.ones((2, 2)) * 5.0, coords={"lat": lat, "lon": lon}, dims=["lat", "lon"]
                ),
                "feasibility_mask": xr.DataArray(np.ones((2, 2)), coords={"lat": lat, "lon": lon}, dims=["lat", "lon"]),
            }
        )

        top_values, locations_df = extract_priority_locations(
            ds=uniform_dataset,
            var_name="uniform_var",
            top_pct=50,  # Should randomly select 2 out of 4
            random_seed=42,
            invert=False,
        )

        assert top_values.sum().values == 2
        assert len(locations_df) == 2

        # All selected values should be 5.0 (since all are uniform)
        selected_values = uniform_dataset["uniform_var"].where(top_values == 1).values
        selected_values_flat = selected_values[~np.isnan(selected_values)]
        assert np.all(selected_values_flat == 5.0)

    def test_extract_priority_locations_with_zeros_and_nans(self):
        """Test handling of zeros and NaNs in the data."""
        lat = np.array([10.0, 20.0, 30.0])
        lon = np.array([100.0, 110.0])

        mixed_dataset = xr.Dataset(
            {
                "mixed_var": xr.DataArray(
                    np.array([[1.0, np.nan], [0.0, 3.0], [2.0, 4.0]]),
                    coords={"lat": lat, "lon": lon},
                    dims=["lat", "lon"],
                ),
                "feasibility_mask": xr.DataArray(
                    np.array([[1.0, 0.0], [1.0, 1.0], [1.0, 1.0]]), coords={"lat": lat, "lon": lon}, dims=["lat", "lon"]
                ),
            }
        )

        top_values, locations_df = extract_priority_locations(
            ds=mixed_dataset,
            var_name="mixed_var",
            top_pct=50,  # Should select top 50% of valid values
            random_seed=42,
            invert=False,
        )

        # Only non-zero, non-NaN values within feasibility mask should be considered
        # Valid values: 1.0, 3.0, 2.0, 4.0 (excluding 0 and NaN)
        assert isinstance(top_values, xr.DataArray)
        assert isinstance(locations_df, pd.DataFrame)

        # Should select 2 out of 4 valid values (the top 50%: 3.0 and 4.0)
        assert top_values.sum().values == 2
        assert len(locations_df) == 2

        # Check that selected values are 3.0 and 4.0
        selected_values = mixed_dataset["mixed_var"].where(top_values == 1).values
        selected_values_flat = selected_values[~np.isnan(selected_values)]
        selected_values_sorted = np.sort(selected_values_flat)
        expected_values = np.array([3.0, 4.0])
        assert np.array_equal(selected_values_sorted, expected_values)


class TestFindTopLocationsPerCountry:
    """Test suite for find_top_locations_per_country function."""

    @pytest.fixture
    def mock_dataset_with_countries(self):
        """Create a mock dataset with ISO3 codes and values to test country-specific extraction."""
        lat = np.array([10.0, 20.0, 30.0, 40.0])
        lon = np.array([100.0, 110.0, 120.0, 130.0])

        # Create a grid with different ISO3 codes
        iso3_grid = np.array(
            [
                ["USA", "USA", "CAN", "CAN"],
                ["USA", "USA", "CAN", "MEX"],
                ["BRA", "BRA", "MEX", "MEX"],
                ["BRA", "ARG", "ARG", "ARG"],
            ]
        )

        # Create outgoing cashflow values for testing
        # Lower values are better (costs)
        cashflow_steel = np.array(
            [
                [100.0, 200.0, 300.0, 400.0],
                [150.0, 250.0, 350.0, 50.0],  # MEX has best value at 50
                [500.0, 600.0, 75.0, 125.0],  # MEX has values 75 and 125
                [700.0, 800.0, 900.0, 1000.0],
            ]
        )

        cashflow_iron = np.array(
            [
                [110.0, 210.0, 310.0, 410.0],
                [160.0, 260.0, 360.0, 60.0],
                [510.0, 610.0, 85.0, 135.0],
                [710.0, 810.0, 910.0, 1010.0],
            ]
        )

        return xr.Dataset(
            {
                "iso3": xr.DataArray(iso3_grid, coords={"lat": lat, "lon": lon}, dims=["lat", "lon"]),
                "outgoing_cashflow_steel": xr.DataArray(
                    cashflow_steel, coords={"lat": lat, "lon": lon}, dims=["lat", "lon"]
                ),
                "outgoing_cashflow_iron": xr.DataArray(
                    cashflow_iron, coords={"lat": lat, "lon": lon}, dims=["lat", "lon"]
                ),
                "top10_steel": xr.DataArray(np.zeros((4, 4)), coords={"lat": lat, "lon": lon}, dims=["lat", "lon"]),
                "top10_iron": xr.DataArray(np.zeros((4, 4)), coords={"lat": lat, "lon": lon}, dims=["lat", "lon"]),
                "feasibility_mask": xr.DataArray(np.ones((4, 4)), coords={"lat": lat, "lon": lon}, dims=["lat", "lon"]),
            }
        )

    def test_find_top_locations_per_country_steel(self, mock_dataset_with_countries):
        """Test finding top locations per country for steel production."""
        # Create initial top locations (empty for this test)
        top_locations = {
            "steel": pd.DataFrame(columns=["Latitude", "Longitude"]),
            "iron": pd.DataFrame(columns=["Latitude", "Longitude"]),
        }

        # Find top locations per country for steel
        # priority_pct=10 means top 1% per country (10/10)
        top_values, locations_df = find_top_locations_per_country(
            ds=mock_dataset_with_countries,
            top_locations=top_locations,
            product="steel",
            priority_pct=10,
            random_seed=42,
        )

        # Check return types
        assert isinstance(top_values, xr.DataArray)
        assert isinstance(locations_df, pd.DataFrame)

        # Check that locations were selected
        assert top_values.sum().values > 0
        assert len(locations_df) > 0

        # Verify that the best value for each country is selected
        # USA's best: 100.0 at (10, 100)
        assert top_values.values[0, 0] == 1  # USA best location

        # MEX's best: 50.0 at (20, 130)
        assert top_values.values[1, 3] == 1  # MEX best location

        # Check that selected values are the minimum for each country
        selected_values = mock_dataset_with_countries["outgoing_cashflow_steel"].where(top_values == 1).values
        selected_values_flat = selected_values[~np.isnan(selected_values)]
        # Should include at least: 100 (USA), 300 (CAN), 50 (MEX), 500 (BRA), 800 (ARG)
        assert 50.0 in selected_values_flat  # MEX best
        assert 100.0 in selected_values_flat  # USA best

    def test_find_top_locations_per_country_with_existing_global(self, mock_dataset_with_countries):
        """Test that country-specific selections are added to existing global top locations."""
        # Create some existing global top locations
        existing_locations = pd.DataFrame(
            {
                "Latitude": [10.0, 20.0],
                "Longitude": [100.0, 110.0],
            }
        )

        top_locations = {
            "steel": existing_locations.copy(),
        }

        # Mark the existing global top locations in the dataset
        mock_dataset_with_countries["top10_steel"].values[0, 0] = 1
        mock_dataset_with_countries["top10_steel"].values[1, 1] = 1

        # Find additional top locations per country
        top_values, locations_df = find_top_locations_per_country(
            ds=mock_dataset_with_countries,
            top_locations=top_locations,
            product="steel",
            priority_pct=10,
            random_seed=42,
        )

        # The result should include both existing and new locations
        assert len(locations_df) >= len(existing_locations)

        # Check that existing locations are preserved
        assert top_values.values[0, 0] >= 1  # Could be 2 if selected by both
        assert top_values.values[1, 1] >= 1

    def test_find_top_locations_per_country_empty_countries(self, mock_dataset_with_countries):
        """Test handling of countries with no valid data (all NaN)."""
        # Set Argentina's data to NaN
        arg_mask = mock_dataset_with_countries["iso3"] == "ARG"
        mock_dataset_with_countries["outgoing_cashflow_steel"] = mock_dataset_with_countries[
            "outgoing_cashflow_steel"
        ].where(~arg_mask, np.nan)

        top_locations = {
            "steel": pd.DataFrame(columns=["Latitude", "Longitude"]),
        }

        # Should handle gracefully without errors
        top_values, locations_df = find_top_locations_per_country(
            ds=mock_dataset_with_countries,
            top_locations=top_locations,
            product="steel",
            priority_pct=10,
            random_seed=42,
        )


class TestCalculatePriorityLocationKpi:
    """Test suite for calculate_priority_location_kpi function."""

    @pytest.fixture
    def mock_full_dataset(self):
        """Create a comprehensive mock dataset for testing the full KPI calculation."""
        lat = np.array([10.0, 20.0, 30.0, 40.0])
        lon = np.array([100.0, 110.0, 120.0, 130.0])

        ds = xr.Dataset(
            {
                "feasibility_mask": (["lat", "lon"], np.ones((4, 4))),
                "iso3": (
                    ["lat", "lon"],
                    np.array(
                        [
                            ["ARG", "ARG", "BRA", "BRA"],
                            ["ARG", "ARG", "BRA", "BRA"],
                            ["CHL", "CHL", "USA", "USA"],
                            ["CHL", "CHL", "USA", "USA"],
                        ]
                    ),
                ),
                "rail_cost": (["lat", "lon"], np.random.uniform(10, 50, (4, 4))),
                "power_price": (["lat", "lon"], np.random.uniform(20, 80, (4, 4))),
                "capped_lcoh": (["lat", "lon"], np.random.uniform(2, 5, (4, 4))),
                "OPEX": (["lat", "lon"], np.random.uniform(100, 300, (4, 4))),
                "POWER_20MW": (["lat", "lon"], np.random.uniform(50, 150, (4, 4))),
                "POWER_5MW": (["lat", "lon"], np.random.uniform(10, 30, (4, 4))),
                "distance_to_secondary_ports": (["lat", "lon"], np.random.uniform(50, 500, (4, 4))),
                "distance_to_primary_ports": (["lat", "lon"], np.random.uniform(100, 1000, (4, 4))),
                "feedstock_transportation_cost_per_ton_iron": (["lat", "lon"], np.random.uniform(5, 20, (4, 4))),
                "feedstock_transportation_cost_per_ton_steel": (["lat", "lon"], np.random.uniform(5, 20, (4, 4))),
                "demand_transportation_cost_per_ton_iron": (["lat", "lon"], np.random.uniform(5, 20, (4, 4))),
                "demand_transportation_cost_per_ton_steel": (["lat", "lon"], np.random.uniform(5, 20, (4, 4))),
                "landtype_factor": (["lat", "lon"], np.random.uniform(0.9, 1.1, (4, 4))),
            },
            coords={"lat": lat, "lon": lon},
        )
        return ds

    @pytest.fixture
    def mock_geo_config(self):
        """Create a mock GeoConfig object."""
        config = Mock()
        config.priority_pct = 25
        config.random_seed = 42
        config.share_iron_vs_steel = {
            "iron": {"capex_share": 0.4, "energy_consumption_per_t": 3.0},  # MWh/t for iron
            "steel": {"capex_share": 0.6, "energy_consumption_per_t": 1.0},  # MWh/t for steel
        }
        config.rail_cost = 0.05
        config.road_cost = 0.15
        config.transport_source = "primary"
        config.iron_ore_steel_ratio = 1.6
        return config

    @pytest.fixture
    def mock_geo_paths(self):
        """Create a mock GeoDataPaths object."""
        paths = Mock()
        paths.geo_plots_dir = "/mock/plots"
        return paths

    @patch("steelo.adapters.geospatial.priority_kpi.plot_screenshot")
    def test_calculate_priority_location_kpi_basic(self, mock_plot, mock_full_dataset, mock_geo_config, mock_geo_paths):
        """Test basic functionality of calculate_priority_location_kpi."""
        result = calculate_priority_location_kpi(
            ds=mock_full_dataset,
            capex=1000000,
            year=2025,
            baseload_coverage=0.8,
            steel_plant_capacity=2.5,
            plant_lifetime=25,
            geo_config=mock_geo_config,
            geo_paths=mock_geo_paths,
        )

        # Check that result is a dict with iron and steel keys
        assert isinstance(result, dict)
        assert "iron" in result
        assert "steel" in result

        # Check that each product has a list of location records
        for product in ["iron", "steel"]:
            assert isinstance(result[product], list)
            # Should have some locations selected
            if result[product]:  # If there are results
                # Check structure of first location
                location = result[product][0]
                assert "Latitude" in location
                assert "Longitude" in location
                assert "iso3" in location
                assert "rail_cost" in location
                assert "power_price" in location
                assert "capped_lcoh" in location

    @patch("steelo.adapters.geospatial.priority_kpi.plot_screenshot")
    def test_calculate_priority_location_kpi_with_masked_areas(
        self, mock_plot, mock_full_dataset, mock_geo_config, mock_geo_paths
    ):
        """Test that feasibility mask is properly applied."""
        # Mask out some areas
        mock_full_dataset["feasibility_mask"].values[0:2, 0:2] = 0

        result = calculate_priority_location_kpi(
            ds=mock_full_dataset,
            capex=1000000,
            year=2025,
            baseload_coverage=0.8,
            steel_plant_capacity=2.5,
            plant_lifetime=25,
            geo_config=mock_geo_config,
            geo_paths=mock_geo_paths,
        )

        # Check that results exist but masked areas are excluded
        for product in ["iron", "steel"]:
            if result[product]:
                for location in result[product]:
                    # No location should be from the masked area (lat 10-20, lon 100-110)
                    assert not (location["Latitude"] in [10.0, 20.0] and location["Longitude"] in [100.0, 110.0])

    @patch("steelo.adapters.geospatial.priority_kpi.plot_screenshot")
    def test_calculate_priority_location_kpi_different_percentages(
        self, mock_plot, mock_full_dataset, mock_geo_config, mock_geo_paths
    ):
        """Test with different priority percentages."""
        # Test with 50% selection
        mock_geo_config.priority_pct = 50

        result = calculate_priority_location_kpi(
            ds=mock_full_dataset,
            capex=1000000,
            year=2025,
            baseload_coverage=0.8,
            steel_plant_capacity=2.5,
            plant_lifetime=25,
            geo_config=mock_geo_config,
            geo_paths=mock_geo_paths,
        )

        # Should have results for both products
        assert "iron" in result
        assert "steel" in result

        # With 50% selection, should have more locations than with 25%
        mock_geo_config.priority_pct = 10
        result_small = calculate_priority_location_kpi(
            ds=mock_full_dataset,
            capex=1000000,
            year=2025,
            baseload_coverage=0.8,
            steel_plant_capacity=2.5,
            plant_lifetime=25,
            geo_config=mock_geo_config,
            geo_paths=mock_geo_paths,
        )

        # The 50% selection should have at least as many locations as 10%
        assert len(result["iron"]) >= len(result_small["iron"])
        assert len(result["steel"]) >= len(result_small["steel"])
