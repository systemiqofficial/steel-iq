import pytest
import numpy as np
import xarray as xr
from steelo.adapters.geospatial.geospatial_calculations import (
    get_baseload_coverage,
    calculate_lcoh_from_power_price,
    calculate_regional_hydrogen_ceiling,
    get_weighted_location_dict_from_plants,
    get_weighted_location_dict_from_demand_centers,
    calculate_distance_to_demand_and_feedstock,
)
from steelo.domain.constants import Year, MWH_TO_KWH, Volumes
from steelo.domain.models import (
    CountryMapping,
    CountryMappingService,
    Location,
    Plant,
    FurnaceGroup,
    Technology,
    DemandCenter,
    Supplier,
    GeoDataPaths,
)
from unittest.mock import Mock, patch


class TestGetBaseloadCoverage:
    """Test suite for get_baseload_coverage function."""

    def test_grid_only_returns_zero(self):
        """Test that 'Grid only' returns 0.0 coverage."""
        result = get_baseload_coverage("Grid only")
        assert result == 0.0

    def test_not_included_returns_zero(self):
        """Test that 'Not included' returns 0.0 coverage."""
        result = get_baseload_coverage("Not included")
        assert result == 0.0

    def test_85_percent_baseload_returns_correct_value(self):
        """Test that '85% baseload + 15% grid' returns 0.85."""
        result = get_baseload_coverage("85% baseload + 15% grid")
        assert result == 0.85

    def test_95_percent_baseload_returns_correct_value(self):
        """Test that '95% baseload + 5% grid' returns 0.95."""
        result = get_baseload_coverage("95% baseload + 5% grid")
        assert result == 0.95

    def test_invalid_power_mix_raises_value_error(self):
        """Test that invalid power mix raises ValueError with appropriate message."""
        invalid_mix = "Invalid Mix"
        with pytest.raises(ValueError) as exc_info:
            get_baseload_coverage(invalid_mix)

        error_message = str(exc_info.value)
        assert "Invalid value for included_power_mix" in error_message
        assert invalid_mix in error_message
        assert "Grid only" in error_message
        assert "85% baseload + 15% grid" in error_message
        assert "95% baseload + 5% grid" in error_message
        assert "Not included" in error_message

    def test_case_sensitive_power_mix(self):
        """Test that function is case-sensitive for power mix strings."""
        # This should raise an error because the function expects exact case
        with pytest.raises(ValueError):
            get_baseload_coverage("grid only")  # lowercase 'g'

    def test_all_valid_options_work(self):
        """Test that all documented valid options work without errors."""
        valid_options = [
            ("Grid only", 0.0),
            ("Not included", 0.0),
            ("85% baseload + 15% grid", 0.85),
            ("95% baseload + 5% grid", 0.95),
        ]

        for power_mix, expected in valid_options:
            result = get_baseload_coverage(power_mix)
            assert result == expected, f"Failed for {power_mix}: expected {expected}, got {result}"

    @pytest.mark.parametrize(
        "power_mix,expected",
        [
            ("Grid only", 0.0),
            ("Not included", 0.0),
            ("85% baseload + 15% grid", 0.85),
            ("95% baseload + 5% grid", 0.95),
        ],
    )
    def test_parametrized_valid_inputs(self, power_mix, expected):
        """Parametrized test for all valid power mix options."""
        result = get_baseload_coverage(power_mix)
        assert result == expected

    @pytest.mark.parametrize(
        "invalid_mix",
        [
            "",
            "100% baseload",
            "50% baseload + 50% grid",
            "grid only",  # wrong case
            "GRID ONLY",  # wrong case
            None,  # This will cause TypeError, not ValueError
            123,  # numeric input
            ["Grid only"],  # list input
        ],
    )
    def test_parametrized_invalid_inputs(self, invalid_mix):
        """Parametrized test for various invalid inputs."""
        if invalid_mix is None or not isinstance(invalid_mix, str):
            # These will cause TypeError rather than ValueError
            with pytest.raises((ValueError, TypeError)):
                get_baseload_coverage(invalid_mix)
        else:
            with pytest.raises(ValueError):
                get_baseload_coverage(invalid_mix)


class TestCalculateLcohFromPowerPrice:
    """Test suite for calculate_lcoh_from_power_price function."""

    @pytest.fixture
    def sample_dataset(self):
        """Create a sample xarray Dataset for testing."""
        # Create coordinate arrays
        lat = np.array([10.0, 20.0, 30.0])
        lon = np.array([100.0, 110.0, 120.0])

        # Create data arrays with shape (3, 3)
        power_price = np.array([[0.05, 0.10, 0.15], [0.08, np.nan, 0.12], [0.07, 0.09, 0.11]])

        iso3 = np.array([["USA", "USA", "CAN"], ["USA", "MEX", "CAN"], ["MEX", "MEX", "CAN"]])

        # Create dataset
        ds = xr.Dataset(
            {
                "power_price": (["lat", "lon"], power_price),
                "iso3": (["lat", "lon"], iso3),
            },
            coords={"lat": lat, "lon": lon},
        )
        return ds

    @pytest.fixture
    def hydrogen_efficiency(self):
        """Sample hydrogen efficiency data."""
        return {
            Year(2020): 50.0,  # MWh/kg
            Year(2025): 45.0,
            Year(2030): 40.0,
        }

    @pytest.fixture
    def hydrogen_capex_opex(self):
        """Sample hydrogen CAPEX/OPEX data."""
        return {
            "USA": {Year(2025): 1.5, Year(2030): 1.2},
            "CAN": {Year(2025): 1.6, Year(2030): 1.3},
            "MEX": {Year(2025): 1.4, Year(2030): 1.1},
        }

    def test_basic_lcoh_calculation(self, sample_dataset, hydrogen_efficiency, hydrogen_capex_opex):
        """Test basic LCOH calculation with valid inputs."""
        year = 2025
        result = calculate_lcoh_from_power_price(sample_dataset, year, hydrogen_efficiency, hydrogen_capex_opex)

        # Check that lcoh variable was added
        assert "lcoh" in result.data_vars

        # Check shape matches input
        assert result["lcoh"].shape == sample_dataset["power_price"].shape

        # Verify calculation for a specific point
        # For USA at (0,0): power_price=0.05, efficiency=45.0 MWh/kg
        # LCOH = 45.0 * 1000 * 0.05 + 1.5 = 2250 + 1.5 = 2251.5
        expected_lcoh = 45.0 * MWH_TO_KWH * 0.05 + 1.5
        assert np.isclose(result["lcoh"].values[0, 0], expected_lcoh)

    def test_nan_handling(self, sample_dataset, hydrogen_efficiency, hydrogen_capex_opex):
        """Test that NaN values in power_price are handled correctly."""
        year = 2025
        result = calculate_lcoh_from_power_price(sample_dataset, year, hydrogen_efficiency, hydrogen_capex_opex)

        # Check that NaN in power_price results in NaN in lcoh
        assert np.isnan(result["lcoh"].values[1, 1])

    def test_missing_year_raises_error(self, sample_dataset, hydrogen_efficiency, hydrogen_capex_opex):
        """Test that missing year in efficiency raises an error."""
        year = 2040  # Year not in hydrogen_efficiency

        with pytest.raises(ValueError, match="Hydrogen efficiency not found for year 2040"):
            calculate_lcoh_from_power_price(sample_dataset, year, hydrogen_efficiency, hydrogen_capex_opex)

    def test_missing_country_skipped(self):
        """Test that countries not in hydrogen_capex_opex are skipped."""
        # Create dataset with unknown country
        ds = xr.Dataset(
            {
                "power_price": (["lat", "lon"], [[0.1]]),
                "iso3": (["lat", "lon"], [["XYZ"]]),
            },
            coords={"lat": [10.0], "lon": [100.0]},
        )

        hydrogen_efficiency = {Year(2025): 45.0}
        hydrogen_capex_opex = {"USA": {Year(2025): 1.5}}  # XYZ not included

        result = calculate_lcoh_from_power_price(ds, 2025, hydrogen_efficiency, hydrogen_capex_opex)

        # LCOH should be NaN for unknown country
        assert np.isnan(result["lcoh"].values[0, 0])

    def test_all_nan_power_prices_raises_error(self):
        """Test that all NaN power prices raise ValueError."""
        # Create dataset with all NaN power prices
        ds = xr.Dataset(
            {
                "power_price": (["lat", "lon"], [[np.nan, np.nan]]),
                "iso3": (["lat", "lon"], [["USA", "CAN"]]),
            },
            coords={"lat": [10.0], "lon": [100.0, 110.0]},
        )

        hydrogen_efficiency = {Year(2025): 45.0}
        hydrogen_capex_opex = {"USA": {Year(2025): 1.5}}

        with pytest.raises(ValueError) as exc_info:
            calculate_lcoh_from_power_price(ds, 2025, hydrogen_efficiency, hydrogen_capex_opex)

        assert "No valid input points found" in str(exc_info.value)

    def test_nan_iso3_skipped(self, hydrogen_efficiency, hydrogen_capex_opex):
        """Test that 'nan' string or NaN iso3 values are skipped."""
        ds = xr.Dataset(
            {
                "power_price": (["lat", "lon"], [[0.1, 0.2]]),
                "iso3": (["lat", "lon"], [["nan", "USA"]]),
            },
            coords={"lat": [10.0], "lon": [100.0, 110.0]},
        )

        result = calculate_lcoh_from_power_price(ds, 2025, hydrogen_efficiency, hydrogen_capex_opex)

        # First point should be NaN (iso3="nan"), second should be calculated
        assert np.isnan(result["lcoh"].values[0, 0])
        assert not np.isnan(result["lcoh"].values[0, 1])

    def test_calculation_with_zero_efficiency(self, sample_dataset):
        """Test calculation when efficiency is 0."""
        hydrogen_efficiency = {Year(2025): 0.0}
        hydrogen_capex_opex = {"USA": {Year(2025): 1.5}, "CAN": {Year(2025): 1.6}, "MEX": {Year(2025): 1.4}}

        result = calculate_lcoh_from_power_price(sample_dataset, 2025, hydrogen_efficiency, hydrogen_capex_opex)

        # With 0 efficiency, LCOH should just be the capex/opex component
        assert result["lcoh"].values[0, 0] == 1.5  # USA
        assert result["lcoh"].values[0, 2] == 1.6  # CAN


class TestCalculateRegionalHydrogenCeiling:
    """Test suite for calculate_regional_hydrogen_ceiling function."""

    @pytest.fixture
    def sample_dataset_with_lcoh(self):
        """Create a sample dataset with LCOH and ISO3 values."""
        lat = np.array([10.0, 20.0, 30.0, 40.0])
        lon = np.array([100.0, 110.0, 120.0, 130.0])

        # Create a 4x4 grid with varying LCOH values
        lcoh = np.array(
            [
                [2.0, 3.0, 4.0, 5.0],
                [2.5, np.nan, 4.5, 5.5],
                [3.0, 4.0, 5.0, 6.0],
                [3.5, 4.5, 5.5, np.nan],
            ]
        )

        iso3 = np.array(
            [
                ["USA", "USA", "CAN", "CAN"],
                ["USA", "MEX", "CAN", "CAN"],
                ["MEX", "MEX", "BRA", "BRA"],
                ["MEX", "MEX", "BRA", "ARG"],
            ]
        )

        ds = xr.Dataset(
            {
                "lcoh": (["lat", "lon"], lcoh),
                "iso3": (["lat", "lon"], iso3),
            },
            coords={"lat": lat, "lon": lon},
        )
        return ds

    @pytest.fixture
    def country_mappings(self):
        """Create sample country mappings."""
        mappings = [
            CountryMapping(
                country="United States",
                iso2="US",
                iso3="USA",
                irena_name="United States",
                region_for_outputs="North America",
                ssp_region="USA",
                tiam_ucl_region="USA",
            ),
            CountryMapping(
                country="Canada",
                iso2="CA",
                iso3="CAN",
                irena_name="Canada",
                region_for_outputs="North America",
                ssp_region="Canada",
                tiam_ucl_region="CAN",
            ),
            CountryMapping(
                country="Mexico",
                iso2="MX",
                iso3="MEX",
                irena_name="Mexico",
                region_for_outputs="North America",
                ssp_region="Latin America",
                tiam_ucl_region="CSA",
            ),
            CountryMapping(
                country="Brazil",
                iso2="BR",
                iso3="BRA",
                irena_name="Brazil",
                region_for_outputs="South America",
                ssp_region="Latin America",
                tiam_ucl_region="CSA",
            ),
            CountryMapping(
                country="Argentina",
                iso2="AR",
                iso3="ARG",
                irena_name="Argentina",
                region_for_outputs="South America",
                ssp_region="Latin America",
                tiam_ucl_region="CSA",
            ),
        ]
        return CountryMappingService(mappings)

    def test_basic_ceiling_calculation(self, sample_dataset_with_lcoh, country_mappings):
        """Test basic regional ceiling calculation at 50th percentile."""
        percentile = 50
        result = calculate_regional_hydrogen_ceiling(sample_dataset_with_lcoh, country_mappings, percentile)

        # Check that all regions are in the result
        assert "USA" in result
        assert "CAN" in result
        assert "CSA" in result  # Central and South America

        # USA has values: 2.0, 3.0, 2.5 (nan excluded)
        # Median should be 2.5
        assert np.isclose(result["USA"], 2.5)

        # CAN has values: 4.0, 5.0, 4.5, 5.5 (nan excluded)
        # Median should be between 4.5 and 5.0
        assert 4.5 <= result["CAN"] <= 5.0

    def test_percentile_calculation(self, sample_dataset_with_lcoh, country_mappings):
        """Test ceiling calculation at different percentiles."""
        # Test 25th percentile
        result_25 = calculate_regional_hydrogen_ceiling(sample_dataset_with_lcoh, country_mappings, 25)

        # Test 75th percentile
        result_75 = calculate_regional_hydrogen_ceiling(sample_dataset_with_lcoh, country_mappings, 75)

        # 75th percentile should be higher than 25th percentile
        for region in result_25.keys():
            if not np.isnan(result_25[region]) and not np.isnan(result_75[region]):
                assert result_75[region] >= result_25[region]

    def test_region_with_no_data(self):
        """Test handling of regions with no LCOH data."""
        # Create dataset with only NaN values for a region
        ds = xr.Dataset(
            {
                "lcoh": (["lat", "lon"], [[np.nan, 2.0], [np.nan, 3.0]]),
                "iso3": (["lat", "lon"], [["USA", "CAN"], ["USA", "CAN"]]),
            },
            coords={"lat": [10.0, 20.0], "lon": [100.0, 110.0]},
        )

        mappings = [
            CountryMapping(
                country="United States",
                iso2="US",
                iso3="USA",
                irena_name="United States",
                region_for_outputs="North America",
                ssp_region="USA",
                tiam_ucl_region="USA",
            ),
            CountryMapping(
                country="Canada",
                iso2="CA",
                iso3="CAN",
                irena_name="Canada",
                region_for_outputs="North America",
                ssp_region="Canada",
                tiam_ucl_region="CAN",
            ),
        ]
        country_mappings = CountryMappingService(mappings)

        result = calculate_regional_hydrogen_ceiling(ds, country_mappings, 50)

        # USA has only NaN values, should get global max
        assert result["USA"] == np.nanmax(ds["lcoh"].values)  # Should be 3.0

        # CAN has valid values
        assert result["CAN"] == np.nanpercentile([2.0, 3.0], 50)

    def test_all_nan_dataset(self):
        """Test handling when all LCOH values are NaN."""
        ds = xr.Dataset(
            {
                "lcoh": (["lat", "lon"], [[np.nan, np.nan]]),
                "iso3": (["lat", "lon"], [["USA", "CAN"]]),
            },
            coords={"lat": [10.0], "lon": [100.0, 110.0]},
        )

        mappings = [
            CountryMapping(
                country="United States",
                iso2="US",
                iso3="USA",
                irena_name="United States",
                region_for_outputs="North America",
                ssp_region="USA",
                tiam_ucl_region="USA",
            ),
        ]
        country_mappings = CountryMappingService(mappings)

        # Suppress the expected RuntimeWarning about all-NaN slice
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            result = calculate_regional_hydrogen_ceiling(ds, country_mappings, 50)

        # With all NaN, result should also be NaN
        assert np.isnan(result["USA"])

    def test_single_value_region(self, country_mappings):
        """Test region with only one LCOH value."""
        ds = xr.Dataset(
            {
                "lcoh": (["lat", "lon"], [[5.0, np.nan]]),
                "iso3": (["lat", "lon"], [["USA", "CAN"]]),
            },
            coords={"lat": [10.0], "lon": [100.0, 110.0]},
        )

        # Use only USA mapping
        mappings = [country_mappings._mappings["United States"]]
        single_country_mappings = CountryMappingService(mappings)

        result = calculate_regional_hydrogen_ceiling(ds, single_country_mappings, 50)

        # Single value should be returned regardless of percentile
        assert result["USA"] == 5.0

    def test_tiam_ucl_region_assignment(self, sample_dataset_with_lcoh, country_mappings):
        """Test that tiam_ucl_region is correctly added to the dataset."""
        _ = calculate_regional_hydrogen_ceiling(sample_dataset_with_lcoh, country_mappings, 50)

        # Check that tiam_ucl_region was added to dataset
        assert "tiam_ucl_region" in sample_dataset_with_lcoh.data_vars

        # Check specific assignments
        # USA iso3 should map to USA tiam_ucl_region
        usa_mask = sample_dataset_with_lcoh["iso3"] == "USA"
        usa_regions = sample_dataset_with_lcoh["tiam_ucl_region"].where(usa_mask, drop=True)
        # Filter out NaN values and check
        usa_values = [r for r in usa_regions.values.flat if not (isinstance(r, float) and np.isnan(r))]
        assert all(r == "USA" for r in usa_values)

        # MEX and BRA should map to CSA
        csa_mask = (sample_dataset_with_lcoh["iso3"] == "MEX") | (sample_dataset_with_lcoh["iso3"] == "BRA")
        csa_regions = sample_dataset_with_lcoh["tiam_ucl_region"].where(csa_mask, drop=True)
        # Filter out NaN values and check
        csa_values = [r for r in csa_regions.values.flat if not (isinstance(r, float) and np.isnan(r))]
        assert all(r == "CSA" for r in csa_values)


class TestGetWeightedLocationDictFromPlants:
    """Test suite for get_weighted_location_dict_from_plants function."""

    @pytest.fixture
    def mock_repository(self):
        """Create a mock repository with plants."""
        repository = Mock()

        # Create locations
        location1 = Location(lat=40.7128, lon=-74.0060, country="USA", region="North America", iso3="USA")
        location2 = Location(lat=51.5074, lon=-0.1278, country="UK", region="Europe", iso3="GBR")
        location3 = Location(lat=35.6762, lon=139.6503, country="Japan", region="Asia", iso3="JPN")

        # Create technologies
        steel_tech = Mock(spec=Technology)
        steel_tech.product = "steel"
        steel_tech.name = "BOF"

        iron_tech = Mock(spec=Technology)
        iron_tech.product = "iron"
        iron_tech.name = "BF"

        # Create furnace groups
        fg1 = Mock(spec=FurnaceGroup)
        fg1.technology = steel_tech
        fg1.capacity = Volumes(1000.0)
        fg1.status = "operating"

        fg2 = Mock(spec=FurnaceGroup)
        fg2.technology = steel_tech
        fg2.capacity = Volumes(500.0)
        fg2.status = "operating"

        fg3 = Mock(spec=FurnaceGroup)
        fg3.technology = iron_tech
        fg3.capacity = Volumes(800.0)
        fg3.status = "operating"

        fg4 = Mock(spec=FurnaceGroup)
        fg4.technology = steel_tech
        fg4.capacity = Volumes(300.0)
        fg4.status = "announced"  # Not active

        fg5 = Mock(spec=FurnaceGroup)
        fg5.technology = steel_tech
        fg5.capacity = Volumes(700.0)
        fg5.status = "operating"

        # Create plants
        plant1 = Mock(spec=Plant)
        plant1.location = location1
        plant1.furnace_groups = [fg1, fg3]  # Steel and iron

        plant2 = Mock(spec=Plant)
        plant2.location = location2
        plant2.furnace_groups = [fg2, fg4]  # Active and inactive steel

        plant3 = Mock(spec=Plant)
        plant3.location = location3
        plant3.furnace_groups = [fg5]  # Only steel

        plant4 = Mock(spec=Plant)
        plant4.location = None  # Plant with no location
        plant4.furnace_groups = [Mock(technology=steel_tech, capacity=Volumes(1000.0), status="operating")]

        plant5 = Mock(spec=Plant)
        plant5.location = location1  # Same location as plant1
        plant5.furnace_groups = [Mock(technology=steel_tech, capacity=Volumes(200.0), status="operating")]

        repository.plants.list.return_value = [plant1, plant2, plant3, plant4, plant5]
        return repository

    def test_basic_steel_capacity_aggregation(self, mock_repository):
        """Test basic aggregation of steel capacity by location."""
        active_statuses = ["operating", "construction"]
        result = get_weighted_location_dict_from_plants(mock_repository, "steel", active_statuses)

        # Expected capacities:
        # location1: 1000.0 (plant1) + 200.0 (plant5) = 1200.0 - accumulated capacity from two plants at the same location
        # location2: 500.0 (plant2, fg4 is not active)
        # location3: 700.0 (plant3)
        # plant4 has no location, so excluded

        assert len(result) == 3

        # Check specific locations
        location1 = next(k for k in result.keys() if k.iso3 == "USA")
        assert result[location1] == 1200.0  # Now sums capacities at same location

        location2 = next(k for k in result.keys() if k.iso3 == "GBR")
        assert result[location2] == 500.0

        location3 = next(k for k in result.keys() if k.iso3 == "JPN")
        assert result[location3] == 700.0

    def test_iron_product_filter(self, mock_repository):
        """Test filtering for iron product type."""
        active_statuses = ["operating"]
        result = get_weighted_location_dict_from_plants(mock_repository, "iron", active_statuses)

        # Only plant1 has iron furnace group (800.0 capacity)
        assert len(result) == 1
        location1 = next(iter(result.keys()))
        assert location1.iso3 == "USA"
        assert result[location1] == 800.0

    def test_status_filtering(self, mock_repository):
        """Test that only active statuses are included."""
        # Include "announced" status
        active_statuses = ["operating", "announced"]
        result = get_weighted_location_dict_from_plants(mock_repository, "steel", active_statuses)

        # Now plant2 should have 500.0 + 300.0 = 800.0 capacity
        location2 = next(k for k in result.keys() if k.iso3 == "GBR")
        assert result[location2] == 800.0

    def test_empty_repository(self):
        """Test with empty repository."""
        repository = Mock()
        repository.plants.list.return_value = []

        result = get_weighted_location_dict_from_plants(repository, "steel", ["operating"])
        assert result == {}

    def test_no_matching_products(self, mock_repository):
        """Test when no plants have the requested product."""
        result = get_weighted_location_dict_from_plants(mock_repository, "aluminum", ["operating"])
        assert result == {}

    def test_no_active_plants(self, mock_repository):
        """Test when no plants match the active statuses."""
        result = get_weighted_location_dict_from_plants(mock_repository, "steel", ["closed"])
        assert result == {}

    def test_plants_with_no_location(self, mock_repository):
        """Test that plants with no location are excluded."""
        # Modify all plants to have no location
        for plant in mock_repository.plants.list.return_value:
            plant.location = None

        result = get_weighted_location_dict_from_plants(mock_repository, "steel", ["operating"])
        assert result == {}

    def test_plants_with_zero_capacity(self):
        """Test that plants with zero capacity are excluded."""
        repository = Mock()
        location = Location(lat=40.0, lon=-74.0, country="USA", region="NA", iso3="USA")

        # Plant with furnace groups but all have zero capacity
        fg = Mock(spec=FurnaceGroup)
        fg.technology = Mock(product="steel")
        fg.capacity = Volumes(0.0)
        fg.status = "operating"

        plant = Mock(spec=Plant)
        plant.location = location
        plant.furnace_groups = [fg]

        repository.plants.list.return_value = [plant]

        result = get_weighted_location_dict_from_plants(repository, "steel", ["operating"])
        assert result == {}

    def test_multiple_furnace_groups_same_plant(self):
        """Test aggregation of multiple furnace groups in the same plant."""
        repository = Mock()
        location = Location(lat=40.0, lon=-74.0, country="USA", region="NA", iso3="USA")

        # Create multiple steel furnace groups
        steel_tech = Mock(product="steel")
        fg1 = Mock(technology=steel_tech, capacity=Volumes(100.0), status="operating")
        fg2 = Mock(technology=steel_tech, capacity=Volumes(200.0), status="operating")
        fg3 = Mock(technology=steel_tech, capacity=Volumes(300.0), status="construction")

        plant = Mock(spec=Plant)
        plant.location = location
        plant.furnace_groups = [fg1, fg2, fg3]

        repository.plants.list.return_value = [plant]

        # Test with different status combinations
        result = get_weighted_location_dict_from_plants(repository, "steel", ["operating"])
        assert result[location] == 300.0  # 100 + 200

        result = get_weighted_location_dict_from_plants(repository, "steel", ["operating", "construction"])
        assert result[location] == 600.0  # 100 + 200 + 300

    def test_location_capacity_accumulation(self):
        """Test that plants at the same location accumulate capacities."""
        repository = Mock()
        location = Location(lat=40.0, lon=-74.0, country="USA", region="NA", iso3="USA")

        # Create two plants at the same location
        steel_tech = Mock(product="steel")

        plant1 = Mock(spec=Plant)
        plant1.location = location
        plant1.furnace_groups = [Mock(technology=steel_tech, capacity=Volumes(1000.0), status="operating")]

        plant2 = Mock(spec=Plant)
        plant2.location = location  # Same location as plant1
        plant2.furnace_groups = [Mock(technology=steel_tech, capacity=Volumes(500.0), status="operating")]

        repository.plants.list.return_value = [plant1, plant2]

        result = get_weighted_location_dict_from_plants(repository, "steel", ["operating"])

        # Should be 1500.0 (sum of both plants)
        assert result[location] == 1500.0

        # Test reverse order - should get same result
        repository.plants.list.return_value = [plant2, plant1]
        result = get_weighted_location_dict_from_plants(repository, "steel", ["operating"])

        # Should still be 1500.0 (sum is commutative)
        assert result[location] == 1500.0


class TestGetWeightedLocationDictFromDemandCenters:
    """Test suite for get_weighted_location_dict_from_demand_centers function."""

    @pytest.fixture
    def mock_repository(self):
        """Create a mock repository with demand centers."""
        repository = Mock()

        # Create locations
        location1 = Location(lat=40.7128, lon=-74.0060, country="USA", region="North America", iso3="USA")
        location2 = Location(lat=51.5074, lon=-0.1278, country="UK", region="Europe", iso3="GBR")
        location3 = Location(lat=35.6762, lon=139.6503, country="Japan", region="Asia", iso3="JPN")

        # Create demand centers
        dc1 = Mock(spec=DemandCenter)
        dc1.demand_center_id = "DC1"
        dc1.center_of_gravity = location1
        dc1.demand_by_year = {
            Year(2020): Volumes(1000.0),
            Year(2025): Volumes(1200.0),
            Year(2030): Volumes(1500.0),
        }

        dc2 = Mock(spec=DemandCenter)
        dc2.demand_center_id = "DC2"
        dc2.center_of_gravity = location2
        dc2.demand_by_year = {
            Year(2020): Volumes(800.0),
            Year(2025): Volumes(900.0),
        }

        dc3 = Mock(spec=DemandCenter)
        dc3.demand_center_id = "DC3"
        dc3.center_of_gravity = location3
        dc3.demand_by_year = {
            Year(2025): Volumes(600.0),
            Year(2030): Volumes(700.0),
        }

        # Demand center with no location
        dc4 = Mock(spec=DemandCenter)
        dc4.demand_center_id = "DC4"
        dc4.center_of_gravity = None
        dc4.demand_by_year = {Year(2025): Volumes(1000.0)}

        # Demand center at same location as dc1
        dc5 = Mock(spec=DemandCenter)
        dc5.demand_center_id = "DC5"
        dc5.center_of_gravity = location1  # Same location as dc1
        dc5.demand_by_year = {
            Year(2025): Volumes(300.0),
            Year(2030): Volumes(400.0),
        }

        repository.demand_centers.list.return_value = [dc1, dc2, dc3, dc4, dc5]
        return repository

    def test_basic_demand_aggregation(self, mock_repository):
        """Test basic aggregation of demand by location for a specific year."""
        year = 2025
        result = get_weighted_location_dict_from_demand_centers(mock_repository, year)

        # Expected demands for 2025:
        # location1: 1200.0 (dc1) + 300.0 (dc5) = 1500.0
        # location2: 900.0 (dc2)
        # location3: 600.0 (dc3)
        # dc4 has no location, so excluded

        assert len(result) == 3

        # Check specific locations
        location1 = next(k for k in result.keys() if k.iso3 == "USA")
        assert result[location1] == 1500.0  # Sum of dc1 and dc5

        location2 = next(k for k in result.keys() if k.iso3 == "GBR")
        assert result[location2] == 900.0

        location3 = next(k for k in result.keys() if k.iso3 == "JPN")
        assert result[location3] == 600.0

    def test_year_with_missing_data(self, mock_repository):
        """Test handling when some demand centers don't have data for the requested year."""
        year = 2020
        result = get_weighted_location_dict_from_demand_centers(mock_repository, year)

        # For 2020:
        # location1: 1000.0 (dc1 only, dc5 has no 2020 data)
        # location2: 800.0 (dc2)
        # location3: no data (dc3 starts from 2025)

        assert len(result) == 2

        location1 = next(k for k in result.keys() if k.iso3 == "USA")
        assert result[location1] == 1000.0

        location2 = next(k for k in result.keys() if k.iso3 == "GBR")
        assert result[location2] == 800.0

    def test_year_with_no_data(self, mock_repository):
        """Test when no demand centers have data for the requested year."""
        year = 2015
        result = get_weighted_location_dict_from_demand_centers(mock_repository, year)

        # No demand centers have data for 2015
        assert result == {}

    def test_empty_repository(self):
        """Test with empty repository."""
        repository = Mock()
        repository.demand_centers.list.return_value = []

        result = get_weighted_location_dict_from_demand_centers(repository, 2025)
        assert result == {}

    def test_all_centers_without_location(self):
        """Test when all demand centers have no location."""
        repository = Mock()

        dc1 = Mock(spec=DemandCenter)
        dc1.demand_center_id = "DC1"
        dc1.center_of_gravity = None
        dc1.demand_by_year = {Year(2025): Volumes(1000.0)}

        dc2 = Mock(spec=DemandCenter)
        dc2.demand_center_id = "DC2"
        dc2.center_of_gravity = None
        dc2.demand_by_year = {Year(2025): Volumes(500.0)}

        repository.demand_centers.list.return_value = [dc1, dc2]

        result = get_weighted_location_dict_from_demand_centers(repository, 2025)
        assert result == {}

    def test_location_demand_accumulation(self):
        """Test that demand centers at the same location accumulate demand."""
        repository = Mock()
        location = Location(lat=40.0, lon=-74.0, country="USA", region="NA", iso3="USA")

        # Create two demand centers at the same location
        dc1 = Mock(spec=DemandCenter)
        dc1.demand_center_id = "DC1"
        dc1.center_of_gravity = location
        dc1.demand_by_year = {Year(2025): Volumes(1000.0)}

        dc2 = Mock(spec=DemandCenter)
        dc2.demand_center_id = "DC2"
        dc2.center_of_gravity = location  # Same location
        dc2.demand_by_year = {Year(2025): Volumes(500.0)}

        repository.demand_centers.list.return_value = [dc1, dc2]

        result = get_weighted_location_dict_from_demand_centers(repository, 2025)

        # Should be 1500.0 (sum of both centers)
        assert result[location] == 1500.0

        # Test reverse order - should get same result
        repository.demand_centers.list.return_value = [dc2, dc1]
        result = get_weighted_location_dict_from_demand_centers(repository, 2025)

        # Should still be 1500.0 (sum is commutative)
        assert result[location] == 1500.0

    def test_mixed_years_and_locations(self):
        """Test complex scenario with multiple years and locations."""
        repository = Mock()

        location1 = Location(lat=40.0, lon=-74.0, country="USA", region="NA", iso3="USA")
        location2 = Location(lat=50.0, lon=-75.0, country="CAN", region="NA", iso3="CAN")

        # Multiple centers with overlapping years and locations
        dc1 = Mock(spec=DemandCenter)
        dc1.demand_center_id = "DC1"
        dc1.center_of_gravity = location1
        dc1.demand_by_year = {
            Year(2020): Volumes(100.0),
            Year(2025): Volumes(200.0),
            Year(2030): Volumes(300.0),
        }

        dc2 = Mock(spec=DemandCenter)
        dc2.demand_center_id = "DC2"
        dc2.center_of_gravity = location1  # Same location as dc1
        dc2.demand_by_year = {
            Year(2025): Volumes(150.0),
            Year(2030): Volumes(250.0),
        }

        dc3 = Mock(spec=DemandCenter)
        dc3.demand_center_id = "DC3"
        dc3.center_of_gravity = location2
        dc3.demand_by_year = {
            Year(2025): Volumes(400.0),
        }

        repository.demand_centers.list.return_value = [dc1, dc2, dc3]

        # Test year 2025
        result_2025 = get_weighted_location_dict_from_demand_centers(repository, 2025)
        assert result_2025[location1] == 350.0  # 200 + 150
        assert result_2025[location2] == 400.0

        # Test year 2030
        result_2030 = get_weighted_location_dict_from_demand_centers(repository, 2030)
        assert result_2030[location1] == 550.0  # 300 + 250
        assert location2 not in result_2030  # dc3 has no 2030 data

        # Test year 2020
        result_2020 = get_weighted_location_dict_from_demand_centers(repository, 2020)
        assert result_2020[location1] == 100.0  # Only dc1 has 2020 data
        assert location2 not in result_2020


class TestCalculateDistanceToDemandAndFeedstock:
    """Test suite for calculate_distance_to_demand_and_feedstock function."""

    @pytest.fixture
    def mock_repository(self):
        """Create a comprehensive mock repository with plants, suppliers, and demand centers."""
        repository = Mock()

        # Create locations
        loc_usa = Location(lat=40.0, lon=-100.0, country="USA", region="NA", iso3="USA")
        loc_brazil = Location(lat=-15.0, lon=-50.0, country="Brazil", region="SA", iso3="BRA")
        loc_china = Location(lat=35.0, lon=105.0, country="China", region="Asia", iso3="CHN")

        # Iron ore suppliers
        supplier1 = Mock(spec=Supplier)
        supplier1.commodity = "io_low"
        supplier1.location = loc_brazil
        supplier1.capacity_by_year = {Year(2025): Volumes(5000.0)}

        supplier2 = Mock(spec=Supplier)
        supplier2.commodity = "io_high"
        supplier2.location = loc_china
        supplier2.capacity_by_year = {Year(2025): Volumes(3000.0)}

        # Supplier with no location
        supplier3 = Mock(spec=Supplier)
        supplier3.commodity = "io_mid"
        supplier3.location = None
        supplier3.capacity_by_year = {Year(2025): Volumes(2000.0)}

        repository.suppliers.list.return_value = [supplier1, supplier2, supplier3]

        # Plants with iron and steel production
        # Iron plant
        iron_tech = Mock(spec=Technology)
        iron_tech.product = "iron"

        fg_iron = Mock(spec=FurnaceGroup)
        fg_iron.technology = iron_tech
        fg_iron.capacity = Volumes(2000.0)
        fg_iron.status = "operating"

        plant1 = Mock(spec=Plant)
        plant1.location = loc_usa
        plant1.furnace_groups = [fg_iron]

        # Steel plant
        steel_tech = Mock(spec=Technology)
        steel_tech.product = "steel"

        fg_steel = Mock(spec=FurnaceGroup)
        fg_steel.technology = steel_tech
        fg_steel.capacity = Volumes(1500.0)
        fg_steel.status = "operating"

        plant2 = Mock(spec=Plant)
        plant2.location = loc_china
        plant2.furnace_groups = [fg_steel]

        repository.plants.list.return_value = [plant1, plant2]

        # Demand centers
        dc1 = Mock(spec=DemandCenter)
        dc1.demand_center_id = "DC1"
        dc1.center_of_gravity = loc_usa
        dc1.demand_by_year = {Year(2025): Volumes(1000.0)}

        dc2 = Mock(spec=DemandCenter)
        dc2.demand_center_id = "DC2"
        dc2.center_of_gravity = loc_brazil
        dc2.demand_by_year = {Year(2025): Volumes(800.0)}

        repository.demand_centers.list.return_value = [dc1, dc2]

        return repository

    @pytest.fixture
    def mock_geo_paths(self, tmp_path):
        """Create mock GeoDataPaths."""
        geo_paths = Mock(spec=GeoDataPaths)
        geo_paths.geo_plots_dir = tmp_path / "geo_plots"
        geo_paths.geo_plots_dir.mkdir(parents=True, exist_ok=True)
        return geo_paths

    @patch("steelo.adapters.geospatial.geospatial_calculations.distance_to_closest_location")
    @patch("steelo.adapters.geospatial.geospatial_calculations.generate_grid")
    @patch("steelo.utilities.plotting.plot_bubble_map")
    def test_basic_distance_calculation(
        self, mock_plot, mock_generate_grid, mock_distance_func, mock_repository, mock_geo_paths
    ):
        """Test basic distance calculation to all location types."""
        # Setup mocks
        mock_grid = Mock()
        mock_grid.x = [100.0, 110.0]
        mock_grid.y = [10.0, 20.0]
        mock_generate_grid.return_value = mock_grid

        # Mock distance function returns
        mock_dist_ore = Mock(spec=xr.DataArray)
        mock_dist_iron = Mock(spec=xr.DataArray)
        mock_dist_steel = Mock(spec=xr.DataArray)
        mock_dist_demand = Mock(spec=xr.DataArray)

        mock_distance_func.side_effect = [mock_dist_ore, mock_dist_iron, mock_dist_steel, mock_dist_demand]

        # Call function
        result = calculate_distance_to_demand_and_feedstock(
            mock_repository, year=2025, active_statuses=["operating"], geo_paths=mock_geo_paths
        )

        # Verify results
        assert len(result) == 4
        assert result[0] is mock_dist_ore
        assert result[1] is mock_dist_iron
        assert result[2] is mock_dist_steel
        assert result[3] is mock_dist_demand

        # Verify distance_to_closest_location was called 4 times
        assert mock_distance_func.call_count == 4

        # Verify plot was called for iron ore mines
        assert mock_plot.called

    @patch("steelo.adapters.geospatial.geospatial_calculations.distance_to_closest_location")
    @patch("steelo.adapters.geospatial.geospatial_calculations.generate_grid")
    @patch("steelo.utilities.plotting.plot_bubble_map")
    def test_iron_ore_supplier_filtering(
        self, mock_plot, mock_generate_grid, mock_distance_func, mock_repository, mock_geo_paths
    ):
        """Test that only iron ore suppliers are included in feedstock calculation."""
        # Setup grid mock
        mock_grid = Mock()
        mock_grid.x = [100.0]
        mock_grid.y = [10.0]
        mock_generate_grid.return_value = mock_grid

        mock_distance_func.return_value = Mock(spec=xr.DataArray)

        # Add non-iron-ore supplier
        scrap_supplier = Mock(spec=Supplier)
        scrap_supplier.commodity = "scrap"
        scrap_supplier.location = Location(lat=50.0, lon=10.0, country="Germany", region="EU", iso3="DEU")
        scrap_supplier.capacity_by_year = {Year(2025): Volumes(1000.0)}

        mock_repository.suppliers.list.return_value.append(scrap_supplier)

        # Call function
        calculate_distance_to_demand_and_feedstock(
            mock_repository, year=2025, active_statuses=["operating"], geo_paths=mock_geo_paths
        )

        # Check the first call to distance_to_closest_location (iron ore)
        first_call = mock_distance_func.call_args_list[0]
        iron_locations = first_call[0][0]  # First positional argument

        # Should only have 2 iron ore locations (Brazil and China), not the scrap
        assert len(iron_locations) == 2

    @patch("steelo.adapters.geospatial.geospatial_calculations.distance_to_closest_location")
    @patch("steelo.adapters.geospatial.geospatial_calculations.generate_grid")
    @patch("steelo.utilities.plotting.plot_bubble_map")
    def test_active_status_filtering(
        self, mock_plot, mock_generate_grid, mock_distance_func, mock_repository, mock_geo_paths
    ):
        """Test that only plants with active statuses are included."""
        # Setup grid mock
        mock_grid = Mock()
        mock_grid.x = [100.0]
        mock_grid.y = [10.0]
        mock_generate_grid.return_value = mock_grid

        mock_distance_func.return_value = Mock(spec=xr.DataArray)

        # Add inactive plant
        steel_tech = Mock(spec=Technology)
        steel_tech.product = "steel"

        fg_inactive = Mock(spec=FurnaceGroup)
        fg_inactive.technology = steel_tech
        fg_inactive.capacity = Volumes(1000.0)
        fg_inactive.status = "closed"  # Not in active_statuses

        plant_inactive = Mock(spec=Plant)
        plant_inactive.location = Location(lat=30.0, lon=100.0, country="India", region="Asia", iso3="IND")
        plant_inactive.furnace_groups = [fg_inactive]

        mock_repository.plants.list.return_value.append(plant_inactive)

        # Call with active_statuses = ["operating"]
        calculate_distance_to_demand_and_feedstock(
            mock_repository, year=2025, active_statuses=["operating"], geo_paths=mock_geo_paths
        )

        # Check steel plants call (third call to distance_to_closest_location)
        steel_call = mock_distance_func.call_args_list[2]
        steel_locations = steel_call[0][0]

        # Should only have 1 steel plant (China), not the closed one
        assert len(steel_locations) == 1

    @patch("steelo.adapters.geospatial.geospatial_calculations.distance_to_closest_location")
    @patch("steelo.adapters.geospatial.geospatial_calculations.generate_grid")
    @patch("steelo.utilities.plotting.plot_bubble_map")
    def test_no_iron_ore_suppliers(self, mock_plot, mock_generate_grid, mock_distance_func, mock_geo_paths):
        """Test handling when no iron ore suppliers exist."""
        repository = Mock()
        repository.suppliers.list.return_value = []  # No suppliers
        repository.plants.list.return_value = []
        repository.demand_centers.list.return_value = []

        # Setup grid mock
        mock_grid = Mock()
        mock_grid.x = [100.0]
        mock_grid.y = [10.0]
        mock_generate_grid.return_value = mock_grid

        # Mock distance function to raise error when called with empty dict
        def distance_side_effect(locations, *args, **kwargs):
            if not locations:
                raise ValueError("No weighted locations provided for distance calculation.")
            return Mock(spec=xr.DataArray)

        mock_distance_func.side_effect = distance_side_effect

        # Should raise ValueError from distance_to_closest_location
        with pytest.raises(ValueError, match="No weighted locations provided"):
            calculate_distance_to_demand_and_feedstock(
                repository, year=2025, active_statuses=["operating"], geo_paths=mock_geo_paths
            )

    @patch("steelo.adapters.geospatial.geospatial_calculations.distance_to_closest_location")
    @patch("steelo.adapters.geospatial.geospatial_calculations.generate_grid")
    @patch("steelo.utilities.plotting.plot_bubble_map")
    def test_year_specific_data(
        self, mock_plot, mock_generate_grid, mock_distance_func, mock_repository, mock_geo_paths
    ):
        """Test that the correct year's data is used."""
        # Setup grid mock
        mock_grid = Mock()
        mock_grid.x = [100.0]
        mock_grid.y = [10.0]
        mock_generate_grid.return_value = mock_grid

        mock_distance_func.return_value = Mock(spec=xr.DataArray)

        # Modify all suppliers to have different capacities by year
        for supplier in mock_repository.suppliers.list.return_value:
            if supplier.location is not None:  # Skip supplier without location
                supplier.capacity_by_year = {
                    Year(2020): Volumes(1000.0),
                    Year(2025): Volumes(2000.0),
                    Year(2030): Volumes(3000.0),
                }

        # Call with year 2030
        calculate_distance_to_demand_and_feedstock(
            mock_repository, year=2030, active_statuses=["operating"], geo_paths=mock_geo_paths
        )

        # Check iron ore call
        iron_call = mock_distance_func.call_args_list[0]
        iron_locations = iron_call[0][0]

        # Check that the 2030 capacity was used
        for location, capacity in iron_locations.items():
            assert capacity == 3000.0  # Year 2030 capacity for all suppliers

    @patch("steelo.adapters.geospatial.geospatial_calculations.distance_to_closest_location")
    @patch("steelo.adapters.geospatial.geospatial_calculations.generate_grid")
    @patch("steelo.utilities.plotting.plot_bubble_map")
    def test_grid_generation(self, mock_plot, mock_generate_grid, mock_distance_func, mock_repository, mock_geo_paths):
        """Test that grid is generated with correct bounding box."""
        # Setup grid mock with specific values
        mock_grid = Mock()
        mock_grid.x = [-180, -90, 0, 90, 180]
        mock_grid.y = [-90, -45, 0, 45, 90]
        mock_generate_grid.return_value = mock_grid

        mock_distance_func.return_value = Mock(spec=xr.DataArray)

        # Call function
        calculate_distance_to_demand_and_feedstock(
            mock_repository, year=2025, active_statuses=["operating"], geo_paths=mock_geo_paths
        )

        # Verify generate_grid was called with global bounding box
        mock_generate_grid.assert_called_once()
        call_args = mock_generate_grid.call_args
        bbox = call_args[1]["bbox"]
        assert bbox["minx"] == -180
        assert bbox["miny"] == -90
        assert bbox["maxx"] == 180
        assert bbox["maxy"] == 90
