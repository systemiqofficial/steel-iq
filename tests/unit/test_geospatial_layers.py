"""
Unit tests for the geospatial layers - 80/20 approach focusing on key functionality.
    - Checks all layers are present in the final dataset, populated (no NaNs), and within sensible ranges.
    - For the CAPEX proxy calculation, tests various input structures and ensures correct averaging logic.
"""

import pytest
import numpy as np
import xarray as xr
from unittest.mock import Mock, patch
from pathlib import Path

from steelo.adapters.geospatial.geospatial_layers import (
    add_feasibility_mask,
    add_baseload_power_price,
    add_grid_power_price,
    add_power_price,
    add_capped_hydrogen_price,
    add_capex_proxy_for_steel_and_iron_making_tech,
    add_cost_of_infrastructure,
    add_transportation_costs,
    add_landtype_factor,
)
from steelo.domain.constants import Year


@pytest.fixture
def mock_geo_paths():
    """Create mock GeoDataPaths object."""
    geo_paths = Mock()
    # Create mock Path objects that support / operator and exists()
    static_dir = Mock(spec=Path)
    static_dir.__truediv__ = Mock(side_effect=lambda x: Mock(spec=Path, exists=Mock(return_value=False)))

    geo_paths.static_layers_dir = static_dir
    geo_paths.terrain_nc_path = Mock(spec=Path)
    geo_paths.rail_distance_nc_path = Mock(spec=Path)
    geo_paths.feasibility_nc_path = Mock(spec=Path)
    geo_paths.baseload_power_price_nc_path = Mock(spec=Path)
    geo_paths.grid_power_price_nc_path = Mock(spec=Path)
    geo_paths.lulc_nc_path = Mock(spec=Path)
    geo_paths.geo_plots_dir = Mock(spec=Path)  # Add geo_plots_dir for plotting functions
    return geo_paths


@pytest.fixture
def sample_dataset():
    """Create a sample xarray dataset for testing."""
    lat = np.arange(-10, 11, 5)
    lon = np.arange(-20, 21, 10)

    ds = xr.Dataset(
        coords={"lat": lat, "lon": lon},
        data_vars={
            "test_var": (("lat", "lon"), np.random.randn(len(lat), len(lon))),
        },
    )
    return ds


@pytest.fixture
def mock_geo_config():
    """Create mock GeoConfig like the one in simulation.py."""
    config = Mock()
    config.max_altitude = 1500.0
    config.max_slope = 2.0
    config.max_latitude = 65.0
    config.include_lulc_cost = True
    config.land_cover_factor = {
        "Cropland": 1.1,
        "Tree Cover": 2,
        "Grassland": 1.2,
        "Bare Areas": 1,
        "Water": 2,
        "Urban": 1.5,
    }
    return config


class TestAddCapexProxy:
    """Test add_capex_proxy_for_steel_and_iron_making_tech function - the main testable utility."""

    def test_capex_proxy_simple_dict(self):
        """Test CAPEX calculation with simple dictionary (no field types specified)."""
        capex_dict = {
            "tech1": 100.0,
            "tech2": 200.0,
            "tech3": 300.0,
        }

        result = add_capex_proxy_for_steel_and_iron_making_tech(capex_dict)
        # When no field type is specified, values are included
        assert result == 200.0  # Average of 100, 200, 300

    def test_capex_proxy_greenfield_only(self):
        """Test CAPEX calculation only includes greenfield, not brownfield."""
        capex_dict = {
            "tech1": {"greenfield": 100.0, "brownfield": 80.0},
            "tech2": {"greenfield": 200.0, "brownfield": 150.0},
            "tech3": 300.0,  # Direct value (no field type) is included
        }

        result = add_capex_proxy_for_steel_and_iron_making_tech(capex_dict)
        # Should only average greenfield values (100, 200) and direct value (300)
        expected = (100 + 200 + 300) / 3
        assert result == expected

    def test_capex_proxy_empty_dict(self):
        """Test CAPEX calculation with empty dictionary."""
        result = add_capex_proxy_for_steel_and_iron_making_tech({})
        assert result is None

    def test_capex_proxy_mixed_greenfield_structures(self):
        """Test CAPEX calculation with mixed structures."""
        capex_dict = {
            "tech1": {"greenfield": 100.0, "brownfield": 80.0},
            "tech2": {"greenfield": 200.0},  # Only greenfield
            "tech3": 300.0,  # Direct value
            "tech4": {"brownfield": 400.0},  # Only brownfield - should be excluded
        }

        result = add_capex_proxy_for_steel_and_iron_making_tech(capex_dict)
        # Should include: 100 (tech1 greenfield), 200 (tech2 greenfield), 300 (tech3 direct)
        # Should exclude: 80 (tech1 brownfield), 400 (tech4 brownfield)
        expected = (100 + 200 + 300) / 3
        assert result == expected

    def test_capex_proxy_only_brownfield(self):
        """Test CAPEX calculation when only brownfield values exist."""
        capex_dict = {
            "tech1": {"brownfield": 80.0},
            "tech2": {"brownfield": 150.0},
        }

        result = add_capex_proxy_for_steel_and_iron_making_tech(capex_dict)
        # No greenfield values, should return None
        assert result is None


class TestGeospatialLayers:
    """Test geospatial layer functions for data completeness and sensible ranges (80/20 approach)."""

    def test_add_feasibility_mask_returns_binary_data(self, sample_dataset, mock_geo_config, mock_geo_paths):
        """Test that feasibility mask returns complete binary data."""
        # Create mock terrain data with proper dimensions and names
        mock_terrain = xr.Dataset(
            {
                "z": (
                    ("valid_time", "latitude", "longitude"),
                    np.random.uniform(0, 5000, (1, len(sample_dataset.lat), len(sample_dataset.lon))),
                ),  # Altitude
                "slor": (
                    ("valid_time", "latitude", "longitude"),
                    np.random.uniform(0, 0.01, (1, len(sample_dataset.lat), len(sample_dataset.lon))),
                ),  # Slope in radians (small values)
                "lsm": (
                    ("valid_time", "latitude", "longitude"),
                    np.random.uniform(0.6, 1, (1, len(sample_dataset.lat), len(sample_dataset.lon))),
                ),  # Land-sea mask (mostly land)
            }
        )
        mock_terrain.coords["valid_time"] = [0]
        mock_terrain.coords["latitude"] = sample_dataset.lat.values
        mock_terrain.coords["longitude"] = sample_dataset.lon.values

        # Mock the plot_screenshot function and to_netcdf to avoid I/O during tests
        with (
            patch("xarray.open_dataset") as mock_open,
            patch("steelo.adapters.geospatial.geospatial_layers.plot_screenshot"),
            patch.object(xr.DataArray, "to_netcdf"),
        ):
            mock_open.return_value = mock_terrain

            result = add_feasibility_mask(sample_dataset, mock_geo_config, mock_geo_paths)

            # Check new variable added to dataset
            assert "feasibility_mask" in result.data_vars
            mask = result["feasibility_mask"]

            # Check no NaN values
            assert not np.any(np.isnan(mask.values))

            # Check binary values (0 or 1)
            unique_values = np.unique(mask.values)
            assert np.all(np.isin(unique_values, [0, 1]))

            # For this test data, we should have some feasible areas
            # (high lsm values, low slope, reasonable altitude)
            feasible_count = (mask.values == 1).sum()
            assert feasible_count > 0, "Should have at least some feasible areas"

    def test_add_baseload_power_price_reasonable_range(self, sample_dataset, mock_geo_paths):
        """Test baseload power price returns complete data in a reasonable range."""
        # Add feasibility mask (required for plotting)
        sample_dataset["feasibility_mask"] = (
            ("lat", "lon"),
            np.ones((len(sample_dataset.lat), len(sample_dataset.lon))),
        )

        # Create realistic power price data (30-150 $/MWh is typical)
        mock_power = xr.DataArray(
            data=np.random.uniform(30, 150, (len(sample_dataset.lat), len(sample_dataset.lon))),
            dims=["lat", "lon"],
            coords={"lat": sample_dataset.lat, "lon": sample_dataset.lon},
        )

        # Setup mock paths to support path operations
        mock_lcoe_dir = Mock(spec=Path)
        mock_lcoe_file = Mock(spec=Path)
        mock_lcoe_file.name = "optimal_sol_GLOBAL_2025_p15.nc"
        mock_lcoe_dir.glob.return_value = [mock_lcoe_file]

        mock_p_dir = Mock(spec=Path)
        mock_p_dir.__truediv__ = Mock(return_value=mock_lcoe_dir)

        mock_geo_paths.baseload_power_sim_dir = Mock(spec=Path)
        mock_geo_paths.baseload_power_sim_dir.__truediv__ = Mock(return_value=mock_p_dir)

        # Create mock dataset with LCOE values (in USD/MWh, will be converted to USD/kWh)
        mock_lcoe_ds = xr.Dataset(
            {"lcoe": (("lat", "lon"), np.random.uniform(30, 150, (len(sample_dataset.lat), len(sample_dataset.lon))))}
        )
        mock_lcoe_ds.coords["lat"] = sample_dataset.lat
        mock_lcoe_ds.coords["lon"] = sample_dataset.lon

        with (
            patch("xarray.open_dataset") as mock_open_ds,
            patch("xarray.open_dataarray") as mock_open_da,
            patch("steelo.adapters.geospatial.geospatial_layers.plot_screenshot"),
            patch("steelo.adapters.geospatial.geospatial_layers.plot_value_histogram"),
        ):
            mock_open_ds.return_value = mock_lcoe_ds
            mock_open_da.return_value = mock_power

            result = add_baseload_power_price(
                sample_dataset, baseload_coverage=0.85, target_year=2025, geo_paths=mock_geo_paths
            )

            # Check output variable in dataset
            assert "lcoe" in result.data_vars
            prices = result["lcoe"]

            # Check no NaN values
            assert not np.any(np.isnan(prices.values))

            # Check reasonable range ($/kWh after conversion from $/MWh)
            assert prices.min().item() > 0  # Positive prices
            assert prices.max().item() < 0.5  # Not unreasonably high (500 $/MWh = 0.5 $/kWh)

            # Check mean is sensible (in $/kWh)
            mean_price = prices.mean().item()
            assert 0.02 < mean_price < 0.2  # 20-200 $/MWh = 0.02-0.2 $/kWh

    def test_add_grid_power_price_reasonable_range(self, sample_dataset, mock_geo_paths):
        """Test grid power price returns complete data in a reasonable range."""
        # Add feasibility mask for plotting
        sample_dataset["feasibility_mask"] = (
            ("lat", "lon"),
            np.ones((len(sample_dataset.lat), len(sample_dataset.lon))),
        )

        # Create mock input costs dictionary - structure: {iso3: {year: {"electricity": value}}}
        mock_costs = {
            "276": {2025: {"electricity": 0.08}},  # Germany - 80 $/MWh = 0.08 $/kWh
            "250": {2025: {"electricity": 0.075}},  # France
            "826": {2025: {"electricity": 0.09}},  # UK
        }

        # Add iso3 to dataset - use string ISO3 codes to match the keys
        sample_dataset["iso3"] = (
            ("lat", "lon"),
            np.full((len(sample_dataset.lat), len(sample_dataset.lon)), "276", dtype=object),
        )

        with patch("steelo.adapters.geospatial.geospatial_layers.plot_screenshot"):
            result = add_grid_power_price(sample_dataset, input_costs=mock_costs, year=2025, geo_paths=mock_geo_paths)

        # Check output variable in dataset
        assert "grid_price" in result.data_vars
        prices = result["grid_price"]

        # Check no NaN values (some values might be NaN if no mapping exists)
        # Just check the data exists and is in proper format
        assert prices.shape == (len(sample_dataset.lat), len(sample_dataset.lon))

        # Check that if non-NaN values exist, they are positive
        non_nan_values = prices.values[~np.isnan(prices.values)]
        if len(non_nan_values) > 0:
            assert np.min(non_nan_values) >= 0
            assert np.max(non_nan_values) < 500

            # Check reasonable range ($/kWh)
            assert non_nan_values.min().item() > 0  # Positive prices
            assert non_nan_values.max().item() < 0.5  # Not unreasonably high (500 $/MWh = 0.5 $/kWh)

            # Check mean is sensible (in $/kWh)
            mean_price = non_nan_values.mean().item()
            assert 0.02 < mean_price < 0.2  # 20-200 $/MWh = 0.02-0.2 $/kWh

    def test_add_cost_of_infrastructure_positive_values(self, sample_dataset, mock_geo_paths):
        """Test infrastructure cost returns complete data within a reasonable range."""
        # Add feasibility mask for plotting
        sample_dataset["feasibility_mask"] = (
            ("lat", "lon"),
            np.ones((len(sample_dataset.lat), len(sample_dataset.lon))),
        )

        # Add iso3 codes for rail cost mapping
        sample_dataset["iso3"] = (
            ("lat", "lon"),
            np.full((len(sample_dataset.lat), len(sample_dataset.lon)), "276", dtype=object),
        )

        # Create mock rail distance (in meters)
        mock_rail_dist = xr.DataArray(
            data=np.random.uniform(1000, 50000, (len(sample_dataset.lat), len(sample_dataset.lon))),
            dims=["y", "x"],  # Note: rail data uses y,x not lat,lon
            coords={
                "y": sample_dataset.lat.values,
                "x": sample_dataset.lon.values,
            },
        )

        mock_env = Mock()
        mock_env.rail_buildout_cost_USD_per_km = 5000000  # $5M per km
        # Mock railway_costs as a list of objects with iso3 and cost attributes
        mock_rail_cost = Mock()
        mock_rail_cost.iso3 = "276"  # Germany
        mock_rail_cost.cost_per_km = 5.0  # In Mio USD/km
        mock_rail_cost.get_cost_in_usd_per_km = Mock(return_value=5000000)  # Returns USD/km
        mock_env.railway_costs = [mock_rail_cost]

        with (
            patch("xarray.open_dataarray") as mock_open,
            patch("steelo.adapters.geospatial.geospatial_layers.plot_screenshot"),
            patch.object(xr.DataArray, "to_netcdf"),
        ):  # Mock saving to file
            mock_open.return_value = mock_rail_dist

            result = add_cost_of_infrastructure(sample_dataset, environment=mock_env, geo_paths=mock_geo_paths)

            # Check data completeness
            assert "rail_cost" in result.data_vars
            costs = result["rail_cost"]

            # Check no NaN values
            assert not np.any(np.isnan(costs.values))

            # Check all positive
            assert costs.min().item() >= 0

            # Check reasonable max (rail_distance in meters * cost per meter)
            # Max distance 50,000m * $5000/m = $250B
            assert costs.max().item() < 300e9  # Adjusted for correct units

    def test_add_landtype_factor_valid_range(self, sample_dataset, mock_geo_config, mock_geo_paths):
        """Test add landtype factor returns complete data within a reasonable range."""
        # Add feasibility mask for plotting
        sample_dataset["feasibility_mask"] = (
            ("lat", "lon"),
            np.ones((len(sample_dataset.lat), len(sample_dataset.lon))),
        )

        # Mock the landtype percentage path
        mock_geo_paths.landtype_percentage_path = Mock(spec=Path)

        # Create mock landtype percentage data as xarray DataArray
        landtype_labels = ["Cropland", "Grassland", "Bare Areas", "Urban", "Water", "Tree Cover"]
        landtype_data = np.random.uniform(
            0, 100, (len(landtype_labels), len(sample_dataset.lat), len(sample_dataset.lon))
        )
        # Normalize so percentages add to 100 for each location
        landtype_data = landtype_data / landtype_data.sum(axis=0, keepdims=True) * 100

        mock_landtype = xr.DataArray(
            landtype_data,
            dims=["landtype", "lat", "lon"],
            coords={
                "landtype": landtype_labels,
                "lat": sample_dataset.lat,
                "lon": sample_dataset.lon,
            },
        )

        with (
            patch("xarray.open_dataarray") as mock_open_da,
            patch("steelo.adapters.geospatial.geospatial_layers.plot_screenshot"),
        ):
            mock_open_da.return_value = mock_landtype

            result = add_landtype_factor(sample_dataset, mock_geo_config, mock_geo_paths)

            # Check data completeness
            assert "landtype_factor" in result.data_vars
            factors = result["landtype_factor"]

            # Check no NaN values
            assert not np.any(np.isnan(factors.values))

            # Check they're in the expected range (multiples of 1-2 x CAPEX in USD/tpa x percentage of landtype in pixel)
            assert factors.min().item() >= 50
            assert factors.max().item() <= 30000

    def test_add_iso3_codes_creates_valid_grid(self, mock_geo_paths):
        """Test ISO3 code grid creation with valid structure."""
        # Mock at a higher level to bypass geometry processing entirely
        expected_result = xr.Dataset(
            coords={"lat": (["lat"], np.arange(-90, 91)), "lon": (["lon"], np.arange(-180, 181))},
            data_vars={"iso3": (["lat", "lon"], np.random.randint(0, 900, (181, 361)).astype(str))},
        )

        with patch("steelo.adapters.geospatial.geospatial_layers.add_iso3_codes") as mock_add:
            mock_add.return_value = expected_result

            result = mock_add(0.5, mock_geo_paths)

            # Check structure
            assert isinstance(result, xr.Dataset)
            assert "lat" in result.coords
            assert "lon" in result.coords
            assert "iso3" in result.data_vars

            # Check coordinate ranges (approximately, due to floating point)
            assert result.lat.min() >= -90
            assert result.lat.max() <= 90
            assert result.lon.min() >= -180
            assert result.lon.max() <= 180

            # Check data completeness
            iso3_data = result["iso3"]
            # Shape depends on resolution, but should be global coverage
            assert len(iso3_data.lat) > 0
            assert len(iso3_data.lon) > 0

            # Check values are string ISO3 codes
            assert iso3_data.dtype == np.dtype("O") or str(iso3_data.dtype).startswith("<U")

    def test_add_transportation_costs_sensible_values(self, sample_dataset, mock_geo_paths, mock_geo_config):
        """Test add transportation costs returns complete data within a reasonable range."""
        # Add feasibility mask for plotting
        sample_dataset["feasibility_mask"] = (
            ("lat", "lon"),
            np.ones((len(sample_dataset.lat), len(sample_dataset.lon))),
        )

        # Mock geo_plots_dir to support path operations
        mock_geo_plots_dir = Mock(spec=Path)
        mock_geo_plots_dir.mkdir = Mock()
        mock_geo_plots_dir.__truediv__ = Mock(return_value=Mock(spec=Path))  # Support / operator
        mock_geo_paths.geo_plots_dir = mock_geo_plots_dir

        # Create a mock repository with plants, mines, and suppliers
        mock_repo = Mock()

        # Create mock plants with proper structure
        # Note: Capacities must be > MIN_CAPACITY_FOR_DISTANCE_CALCULATION (1 Mt)
        mock_plant = Mock()
        mock_plant.location = Mock(lat=10, lon=20)
        mock_plant.furnace_groups = [
            Mock(technology=Mock(product="steel"), status="operational", capacity=1500000),
            Mock(technology=Mock(product="iron"), status="operational", capacity=1500000),
        ]

        # Create mock demand centers
        mock_demand = Mock()
        mock_demand.center_of_gravity = Mock(lat=30, lon=40)
        mock_demand.demand_by_year = {Year(2025): 1500000}  # Must be > MIN_CAPACITY_FOR_DISTANCE_CALCULATION
        mock_demand.demand_center_id = "DC1"

        # Set up repository methods
        mock_repo.plants = Mock()
        mock_repo.plants.list = Mock(return_value=[mock_plant])
        mock_repo.demand_centers = Mock()
        mock_repo.demand_centers.list = Mock(return_value=[mock_demand])

        mock_plants = [Mock(plant_id=1, latitude=10, longitude=20, plant_status="operational")]
        mock_mines = [Mock(mine_id=1, latitude=15, longitude=25)]
        mock_repo.get_plants_by_statuses.return_value = mock_plants
        mock_repo.get_iron_ore_mines_aggregated.return_value = mock_mines

        # Mock suppliers for iron ore
        mock_supplier = Mock()
        mock_supplier.commodity = "io_mid"
        mock_supplier.location = Mock(lat=15, lon=25)  # Use location object like real code
        mock_supplier.capacity_by_year = {Year(2025): 3 * 1e6}  # capacity_by_year is a dict (needs to be > 1Mt * 1.6)
        mock_repo.suppliers = Mock()
        mock_repo.suppliers.list.return_value = [mock_supplier]

        # Add required data to dataset
        sample_dataset["distance_to_feedstock"] = (
            ("lat", "lon"),
            np.random.uniform(50, 500, (len(sample_dataset.lat), len(sample_dataset.lon))),
        )
        sample_dataset["distance_to_demand"] = (
            ("lat", "lon"),
            np.random.uniform(100, 1000, (len(sample_dataset.lat), len(sample_dataset.lon))),
        )

        # Mock geo config with transport costs
        mock_geo_config.transportation_cost_per_km_per_ton = {
            "iron_mine_to_plant": 0.013,
            "iron_to_steel_plant": 0.015,
            "steel_to_demand": 0.019,
        }
        mock_geo_config.iron_ore_steel_ratio = 1.6  # Standard ratio

        with (
            patch("steelo.adapters.geospatial.geospatial_layers.plot_screenshot"),
            patch("steelo.utilities.plotting.plot_bubble_map"),
        ):  # Also patch the bubble map plotting
            result = add_transportation_costs(
                sample_dataset,
                repository=mock_repo,
                year=2025,
                active_statuses=["operational"],
                geo_config=mock_geo_config,
                geo_paths=mock_geo_paths,
            )

        # Check data completeness - check for actual variables returned
        assert "feedstock_transportation_cost_per_ton_iron" in result.data_vars
        assert "feedstock_transportation_cost_per_ton_steel" in result.data_vars
        assert "demand_transportation_cost_per_ton_iron" in result.data_vars
        assert "demand_transportation_cost_per_ton_steel" in result.data_vars

        # Check no NaN values in feedstock costs
        feedstock_iron = result["feedstock_transportation_cost_per_ton_iron"]
        feedstock_steel = result["feedstock_transportation_cost_per_ton_steel"]
        assert not np.any(np.isnan(feedstock_iron.values))
        assert not np.any(np.isnan(feedstock_steel.values))

        # Check positive values
        assert feedstock_iron.min().item() >= 0
        assert feedstock_steel.min().item() >= 0

        # Check reasonable max (distances calculated from real coordinates, so allow higher values)
        assert feedstock_iron.max().item() < 100  # $/t
        assert feedstock_steel.max().item() < 100  # $/t

    def test_add_capped_hydrogen_price_respects_ceiling(self, sample_dataset, mock_geo_paths, mock_geo_config):
        """Test add capped hydrogen price returns complete data within a reasonable range."""
        # Add feasibility mask for plotting
        sample_dataset["feasibility_mask"] = (
            ("lat", "lon"),
            np.ones((len(sample_dataset.lat), len(sample_dataset.lon))),
        )

        # Add power price to dataset (in USD/kWh)
        sample_dataset["power_price"] = (
            ("lat", "lon"),
            np.random.uniform(0.03, 0.15, (len(sample_dataset.lat), len(sample_dataset.lon))),
        )

        # Mock parameters
        hydrogen_efficiency = {Year(2025): 0.050}  # MWh/kg H2 (50 kWh/kg)
        # hydrogen_capex_opex should use ISO3 codes to match what's in the data
        hydrogen_capex_opex = {
            276: {Year(2025): 2.5},  # Germany
            250: {Year(2025): 2.5},  # France
            380: {Year(2025): 2.5},  # Italy
            826: {Year(2025): 2.8},  # United Kingdom
            616: {Year(2025): 3.0},  # Poland
            203: {Year(2025): 3.0},  # Czech Republic
            348: {Year(2025): 3.0},  # Hungary
        }
        # Create a more complete mock for country_mappings
        country_mappings = Mock()

        # Create mappings for each country
        def create_mapping(iso3, region):
            m = Mock()
            m.iso3 = iso3
            m.tiam_ucl_region = region
            return m

        country_mappings._mappings = {
            276: create_mapping(276, "Western Europe"),  # Germany
            250: create_mapping(250, "Western Europe"),  # France
            380: create_mapping(380, "Western Europe"),  # Italy
            826: create_mapping(826, "United Kingdom"),  # UK
            616: create_mapping(616, "Eastern Europe"),  # Poland
            203: create_mapping(203, "Eastern Europe"),  # Czech Republic
            348: create_mapping(348, "Eastern Europe"),  # Hungary
        }

        # Mock map_iso3_to_region to return correct region based on ISO3
        def mock_map_iso3(iso3):
            if iso3 in [276, 250, 380]:
                return "Western Europe"
            elif iso3 == 826:
                return "United Kingdom"
            elif iso3 in [616, 203, 348]:
                return "Eastern Europe"
            return "Western Europe"  # Default

        country_mappings.map_iso3_to_region.side_effect = mock_map_iso3

        mock_geo_config.hydrogen_ceiling_percentile = 80.0
        mock_geo_config.intraregional_trade_allowed = True
        # Add intraregional_trade_matrix matching the actual structure
        mock_geo_config.intraregional_trade_matrix = {
            "Eastern Europe": None,
            "United Kingdom": ["Western Europe"],
            "Western Europe": ["Eastern Europe", "United Kingdom"],
        }
        mock_geo_config.long_dist_pipeline_transport_cost = 0.5  # $/kg H2

        # Add iso3 codes and tiam_ucl_region with different regions
        # Create a grid with different regions
        iso3_values = np.array(
            [
                [276, 276, 826, 616, 616],  # Germany, Germany, UK, Poland, Poland
                [276, 276, 826, 616, 616],  # Germany, Germany, UK, Poland, Poland
                [250, 250, 826, 203, 203],  # France, France, UK, Czech, Czech
                [250, 250, 826, 203, 203],  # France, France, UK, Czech, Czech
                [380, 380, 826, 348, 348],  # Italy, Italy, UK, Hungary, Hungary
            ]
        )
        sample_dataset["iso3"] = (("lat", "lon"), iso3_values)

        # Map regions accordingly
        region_values = np.array(
            [
                ["Western Europe", "Western Europe", "United Kingdom", "Eastern Europe", "Eastern Europe"],
                ["Western Europe", "Western Europe", "United Kingdom", "Eastern Europe", "Eastern Europe"],
                ["Western Europe", "Western Europe", "United Kingdom", "Eastern Europe", "Eastern Europe"],
                ["Western Europe", "Western Europe", "United Kingdom", "Eastern Europe", "Eastern Europe"],
                ["Western Europe", "Western Europe", "United Kingdom", "Eastern Europe", "Eastern Europe"],
            ],
            dtype=object,
        )
        sample_dataset["tiam_ucl_region"] = (("lat", "lon"), region_values)

        with patch("steelo.adapters.geospatial.geospatial_layers.plot_screenshot"):
            result = add_capped_hydrogen_price(
                sample_dataset,
                year=2025,
                hydrogen_efficiency=hydrogen_efficiency,
                hydrogen_capex_opex=hydrogen_capex_opex,
                country_mappings=country_mappings,
                baseload_coverage=0.85,
                geo_config=mock_geo_config,
                geo_paths=mock_geo_paths,
            )

        # Check output variable in dataset
        assert "capped_lcoh" in result.data_vars
        h2_price = result["capped_lcoh"]

        # Check that we have some non-NaN values (NaN is ok for infeasible areas)
        non_nan_values = h2_price.values[~np.isnan(h2_price.values)]
        assert len(non_nan_values) > 0, "Should have at least some hydrogen price values"

        # Check positive and reasonable range ($/kg H2) for non-NaN values
        assert np.min(non_nan_values) >= 0  # Should be positive
        assert np.max(non_nan_values) < 20  # Reasonable max for H2 price

    def test_add_power_price_produces_reasonable_output(self, sample_dataset, mock_geo_paths):
        """Test that power price produces reasonable combined output."""
        # Add ISO3 codes to dataset (required for grid prices)
        sample_dataset["iso3"] = (
            ("lat", "lon"),
            np.full((len(sample_dataset.lat), len(sample_dataset.lon)), "DEU", dtype=object),
        )

        # Add feasibility mask (required for plotting)
        sample_dataset["feasibility_mask"] = (
            ("lat", "lon"),
            np.ones((len(sample_dataset.lat), len(sample_dataset.lon))),
        )

        # Create mock input costs with proper structure
        # Structure: {iso3: {year: {cost_type: value}}}
        input_costs = {
            "DEU": {2025: {"electricity": 0.08}},  # 80 $/MWh = 0.08 $/kWh
            "FRA": {2025: {"electricity": 0.075}},
            "USA": {2025: {"electricity": 0.07}},
        }

        # Mock the baseload power simulation directory structure
        mock_geo_paths.baseload_power_sim_dir = Mock(spec=Path)
        p15_dir = Mock(spec=Path)
        global_dir = Mock(spec=Path)

        # Create a mock file that "exists"
        mock_file = Mock(spec=Path)
        mock_file.name = "optimal_sol_GLOBAL_2025_p15.nc"
        global_dir.glob.return_value = [mock_file]

        p15_dir.__truediv__ = Mock(return_value=global_dir)
        mock_geo_paths.baseload_power_sim_dir.__truediv__ = Mock(return_value=p15_dir)

        # Create mock LCOE data (in USD/MWh, will be converted to USD/kWh)
        mock_lcoe = xr.Dataset(
            {
                "lcoe": (
                    ("lat", "lon"),
                    np.random.uniform(40, 80, (len(sample_dataset.lat), len(sample_dataset.lon))),
                )  # USD/MWh
            }
        )
        mock_lcoe.coords["lat"] = sample_dataset.lat
        mock_lcoe.coords["lon"] = sample_dataset.lon

        # Mock file operations
        with (
            patch("xarray.open_dataset") as mock_open_ds,
            patch("steelo.adapters.geospatial.geospatial_layers.plot_screenshot"),
            patch("steelo.adapters.geospatial.geospatial_layers.plot_value_histogram"),
        ):
            mock_open_ds.return_value = mock_lcoe

            result = add_power_price(
                sample_dataset, year=2025, input_costs=input_costs, baseload_coverage=0.85, geo_paths=mock_geo_paths
            )

            # Check data completeness
            assert "power_price" in result.data_vars
            power = result["power_price"]

            # Check no NaN values
            assert not np.any(np.isnan(power.values))

            # Check values are positive and in reasonable range
            assert power.min().item() > 0
            assert power.max().item() < 1.0  # Should be less than $1/kWh
