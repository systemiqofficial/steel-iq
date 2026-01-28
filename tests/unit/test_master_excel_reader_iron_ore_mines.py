"""Unit tests for iron ore mine reading functionality."""

import json
import uuid
from unittest.mock import patch

import pandas as pd
import pytest

from steelo.adapters.dataprocessing.excel_reader import read_mines_as_suppliers


class TestReadMinesAsSuppliers:
    """Test the read_mines_as_suppliers function with focus on unique ID generation."""

    @pytest.fixture
    def mock_excel_data(self):
        """Create mock Excel data with duplicate country/quality combinations."""
        return pd.DataFrame(
            {
                "Region": ["Australia", "Australia", "Australia", "Brazil", "Brazil", "China", "China"],
                "Products": ["IO_low", "IO_low", "IO_mid", "IO_high", "IO_high", "IO_mid", "IO_mid"],
                "capacity Mtpa 2025": [100, 200, 150, 300, 250, 180, 220],
                "capacity Mtpa 2030": [100, 200, 150, 300, 250, 180, 220],
                "costs $/t 2025": [50, 45, 48, 60, 55, 40, 42],
                "costs $/t 2030": [50, 45, 48, 60, 55, 40, 42],
                "price $/t 2025": [60, 55, 58, 70, 65, 50, 52],  # price = costs + premium
                "price $/t 2030": [60, 55, 58, 70, 65, 50, 52],
                "lat": [-25.0, -26.0, -24.0, -15.0, -16.0, 35.0, 34.0],
                "lon": [133.0, 134.0, 132.0, -47.0, -48.0, 104.0, 105.0],
                "Mine": ["Mine A", "Mine B", "Mine C", "Mine D", "Mine E", "Mine F", "Mine G"],
            }
        )

    @pytest.fixture
    def mock_location_csv(self, tmp_path):
        """Create a mock location CSV file."""
        csv_path = tmp_path / "locations.csv"
        # Empty CSV since we're not using it in the test
        csv_path.write_text("COUNTRY,ISO,latitude,longitude\n")
        return str(csv_path)

    def test_read_mines_generates_unique_supplier_ids(self, mock_excel_data, mock_location_csv, tmp_path):
        """Test that mines in same country with same quality get unique IDs."""
        # Setup: Create mock Excel file
        excel_path = tmp_path / "test_mines.xlsx"
        mock_excel_data.to_excel(excel_path, sheet_name="Iron ore mines", index=False)

        # Act: Read mines
        with patch("steelo.adapters.dataprocessing.excel_reader.translate_mine_regions_to_iso3", return_value={}):
            suppliers = read_mines_as_suppliers(
                mine_data_excel_path=str(excel_path),
                mine_data_sheet_name="Iron ore mines",
                location_csv=mock_location_csv,
            )

        # Assert: All supplier IDs are unique
        supplier_ids = [s.supplier_id for s in suppliers]
        assert len(supplier_ids) == len(set(supplier_ids)), f"Duplicate IDs found: {supplier_ids}"

        # Assert: We have all 7 suppliers (not just 4 after deduplication)
        assert len(suppliers) == 7, f"Expected 7 suppliers, got {len(suppliers)}"

        # This test should FAIL with the current implementation
        # because Australia_IO_low will be duplicated

    def test_read_mines_preserves_all_capacity(self, mock_excel_data, mock_location_csv, tmp_path):
        """Test that total iron ore capacity from Excel is preserved."""
        # Setup: Create mock Excel file with known total capacity
        excel_path = tmp_path / "test_mines.xlsx"
        mock_excel_data.to_excel(excel_path, sheet_name="Iron ore mines", index=False)
        expected_total_capacity = mock_excel_data["capacity Mtpa 2025"].sum()

        # Act: Read mines
        with patch("steelo.adapters.dataprocessing.excel_reader.translate_mine_regions_to_iso3", return_value={}):
            suppliers = read_mines_as_suppliers(
                mine_data_excel_path=str(excel_path),
                mine_data_sheet_name="Iron ore mines",
                location_csv=mock_location_csv,
            )

        # Assert: Sum of all supplier capacities equals Excel total
        # Check capacity for 2025
        from steelo.domain import Year

        actual_total_capacity = sum(
            s.capacity_by_year.get(Year(2025), 0) / 1_000_000  # Convert back to Mt
            for s in suppliers
        )

        assert abs(expected_total_capacity - actual_total_capacity) < 0.01, (
            f"Total capacity mismatch: Excel={expected_total_capacity:.2f} Mt, Suppliers={actual_total_capacity:.2f} Mt"
        )

        # This test should FAIL with the current implementation
        # because duplicate IDs cause capacity loss

    def test_read_mines_handles_missing_mine_names(self, mock_location_csv, tmp_path):
        """Test handling when mine name is missing."""
        # Setup: Create mock Excel with some empty mine names
        data = pd.DataFrame(
            {
                "Region": ["Australia", "Australia"],
                "Products": ["IO_low", "IO_low"],
                "capacity Mtpa 2025": [100, 200],
                "capacity Mtpa 2030": [100, 200],
                "costs $/t 2025": [50, 45],
                "costs $/t 2030": [50, 45],
                "price $/t 2025": [60, 55],
                "price $/t 2030": [60, 55],
                "lat": [-25.0, -26.0],
                "lon": [133.0, 134.0],
                "Mine": ["Mine A", None],  # One missing mine name
            }
        )
        excel_path = tmp_path / "test_mines.xlsx"
        data.to_excel(excel_path, sheet_name="Iron ore mines", index=False)

        # Act: Read mines
        with patch("steelo.adapters.dataprocessing.excel_reader.translate_mine_regions_to_iso3", return_value={}):
            suppliers = read_mines_as_suppliers(
                mine_data_excel_path=str(excel_path),
                mine_data_sheet_name="Iron ore mines",
                location_csv=mock_location_csv,
            )

        # Assert: All suppliers get unique IDs even with missing mine names
        supplier_ids = [s.supplier_id for s in suppliers]
        assert len(supplier_ids) == len(set(supplier_ids)), "Duplicate IDs found even with missing mine names"
        assert len(suppliers) == 2, f"Expected 2 suppliers, got {len(suppliers)}"

    def test_read_mines_preserves_individual_mine_data(self, mock_excel_data, mock_location_csv, tmp_path):
        """Test that individual mine data (location, cost) is preserved."""
        # Setup
        excel_path = tmp_path / "test_mines.xlsx"
        mock_excel_data.to_excel(excel_path, sheet_name="Iron ore mines", index=False)

        # Act
        with patch("steelo.adapters.dataprocessing.excel_reader.translate_mine_regions_to_iso3", return_value={}):
            suppliers = read_mines_as_suppliers(
                mine_data_excel_path=str(excel_path),
                mine_data_sheet_name="Iron ore mines",
                location_csv=mock_location_csv,
            )

        # Assert: Check that we have suppliers with different coordinates for same country/product
        australia_low_suppliers = [s for s in suppliers if s.location.region == "Australia" and s.commodity == "io_low"]

        # Should have 2 Australia IO_low suppliers with different locations
        assert len(australia_low_suppliers) >= 2, (
            f"Expected at least 2 Australia IO_low suppliers, got {len(australia_low_suppliers)}"
        )

        # Check they have different locations
        locations = [(s.location.lat, s.location.lon) for s in australia_low_suppliers]
        assert len(locations) == len(set(locations)), "Duplicate locations found for different mines"

        # This test should FAIL with current implementation

    def test_capacity_preservation_by_product(self, mock_location_csv, tmp_path):
        """Test that capacity is preserved for each product type."""
        # Setup: Create data with known capacities per product
        data = pd.DataFrame(
            {
                "Region": ["Australia", "Australia", "Brazil", "China", "China"],
                "Products": ["IO_low", "IO_low", "IO_high", "IO_mid", "IO_mid"],
                "capacity Mtpa 2025": [100, 150, 200, 120, 180],  # IO_low: 250, IO_high: 200, IO_mid: 300
                "capacity Mtpa 2030": [100, 150, 200, 120, 180],
                "costs $/t 2025": [50, 45, 60, 40, 42],
                "costs $/t 2030": [50, 45, 60, 40, 42],
                "price $/t 2025": [60, 55, 70, 50, 52],
                "price $/t 2030": [60, 55, 70, 50, 52],
                "lat": [-25.0, -26.0, -15.0, 35.0, 34.0],
                "lon": [133.0, 134.0, -47.0, 104.0, 105.0],
                "Mine": ["A", "B", "C", "D", "E"],
            }
        )
        excel_path = tmp_path / "test_mines.xlsx"
        data.to_excel(excel_path, sheet_name="Iron ore mines", index=False)

        expected_by_product = {
            "IO_low": 250,
            "IO_high": 200,
            "IO_mid": 300,
        }

        # Act
        with patch("steelo.adapters.dataprocessing.excel_reader.translate_mine_regions_to_iso3", return_value={}):
            suppliers = read_mines_as_suppliers(
                mine_data_excel_path=str(excel_path),
                mine_data_sheet_name="Iron ore mines",
                location_csv=mock_location_csv,
            )

        # Assert: Check capacity by product
        from steelo.domain import Year

        actual_by_product = {}
        for product in ["IO_low", "IO_high", "IO_mid"]:
            commodity = product.lower()  # IO_low -> io_low
            product_suppliers = [s for s in suppliers if s.commodity == commodity]
            total = sum(
                s.capacity_by_year.get(Year(2025), 0) / 1_000_000  # Convert to Mt
                for s in product_suppliers
            )
            actual_by_product[product] = total

        for product, expected in expected_by_product.items():
            actual = actual_by_product[product]
            assert abs(expected - actual) < 0.01, (
                f"{product} capacity mismatch: Expected={expected:.2f} Mt, Actual={actual:.2f} Mt"
            )

        # This test should FAIL with current implementation


class TestUniqueSupplierIDGeneration:
    """Test the unique ID generation mechanism itself."""

    def test_uuid5_generation_is_deterministic(self):
        """Test that UUIDv5 generates the same ID for the same input."""
        # This will test our new ID generation function once implemented
        namespace = uuid.UUID("00000000-0000-0000-0000-000000001205")

        payload1 = {
            "country": "australia",
            "product": "io_low",
            "mine": "mine a",
            "lat": -25.0,
            "lon": 133.0,
        }

        payload2 = {
            "country": "australia",
            "product": "io_low",
            "mine": "mine a",
            "lat": -25.0,
            "lon": 133.0,
        }

        id1 = uuid.uuid5(namespace, json.dumps(payload1, sort_keys=True))
        id2 = uuid.uuid5(namespace, json.dumps(payload2, sort_keys=True))

        assert id1 == id2, "Same input should generate same UUID"

    def test_uuid5_generation_is_unique_for_different_inputs(self):
        """Test that UUIDv5 generates different IDs for different inputs."""
        namespace = uuid.UUID("00000000-0000-0000-0000-000000001205")

        payload1 = {
            "country": "australia",
            "product": "io_low",
            "mine": "mine a",
            "lat": -25.0,
            "lon": 133.0,
        }

        payload2 = {
            "country": "australia",
            "product": "io_low",
            "mine": "mine b",  # Different mine
            "lat": -26.0,  # Different location
            "lon": 134.0,
        }

        id1 = uuid.uuid5(namespace, json.dumps(payload1, sort_keys=True))
        id2 = uuid.uuid5(namespace, json.dumps(payload2, sort_keys=True))

        assert id1 != id2, "Different inputs should generate different UUIDs"


class TestIronOrePremiumsSupport:
    """Test that mine_cost and mine_price fields are properly populated from Excel."""

    @pytest.fixture
    def mock_location_csv(self, tmp_path):
        """Create a mock location CSV file."""
        csv_path = tmp_path / "locations.csv"
        csv_path.write_text("COUNTRY,ISO,latitude,longitude\n")
        return str(csv_path)

    def test_mine_cost_and_price_fields_populated(self, mock_location_csv, tmp_path):
        """Test that both mine_cost and mine_price are read from Excel."""
        # Setup: Create Excel with both costs and price columns
        data = pd.DataFrame(
            {
                "Region": ["Australia", "Brazil"],
                "Products": ["IO_low", "IO_high"],
                "capacity Mtpa 2025": [100, 200],
                "capacity Mtpa 2030": [100, 200],
                "costs $/t 2025": [50, 60],
                "costs $/t 2030": [50, 60],
                "price $/t 2025": [65, 75],  # price = costs + premium
                "price $/t 2030": [65, 75],
                "lat": [-25.0, -15.0],
                "lon": [133.0, -47.0],
                "Mine": ["Mine A", "Mine B"],
            }
        )
        excel_path = tmp_path / "test_mines.xlsx"
        data.to_excel(excel_path, sheet_name="Iron ore mines", index=False)

        # Act
        with patch("steelo.adapters.dataprocessing.excel_reader.translate_mine_regions_to_iso3", return_value={}):
            suppliers = read_mines_as_suppliers(
                mine_data_excel_path=str(excel_path),
                mine_data_sheet_name="Iron ore mines",
                location_csv=mock_location_csv,
            )

        # Assert: Both mine_cost and mine_price dictionaries are populated
        from steelo.domain import Year

        assert len(suppliers) == 2
        for supplier in suppliers:
            assert supplier.mine_cost_by_year is not None, (
                f"mine_cost_by_year should be populated for {supplier.supplier_id}"
            )
            assert supplier.mine_price_by_year is not None, (
                f"mine_price_by_year should be populated for {supplier.supplier_id}"
            )
            assert len(supplier.mine_cost_by_year) > 0, (
                f"mine_cost_by_year should have values for {supplier.supplier_id}"
            )
            assert len(supplier.mine_price_by_year) > 0, (
                f"mine_price_by_year should have values for {supplier.supplier_id}"
            )

        # Assert: Values match the Excel data for 2025
        supplier_0 = suppliers[0]
        mine_cost_2025 = supplier_0.mine_cost_by_year.get(Year(2025))
        assert mine_cost_2025 == 50 or mine_cost_2025 == 60
        if mine_cost_2025 == 50:
            assert supplier_0.mine_price_by_year.get(Year(2025)) == 65
            assert supplier_0.production_cost_by_year.get(Year(2025)) == 50  # Defaults to costs
        else:
            assert supplier_0.mine_price_by_year.get(Year(2025)) == 75
            assert supplier_0.production_cost_by_year.get(Year(2025)) == 60

    def test_missing_price_column_falls_back_to_costs(self, mock_location_csv, tmp_path):
        """Test that if price column is missing, mine_price falls back to costs."""
        # Setup: Create Excel without price column
        data = pd.DataFrame(
            {
                "Region": ["Australia"],
                "Products": ["IO_low"],
                "capacity Mtpa 2025": [100],
                "capacity Mtpa 2030": [100],
                "costs $/t 2025": [50],
                "costs $/t 2030": [50],
                # No "price" columns
                "lat": [-25.0],
                "lon": [133.0],
                "Mine": ["Mine A"],
            }
        )
        excel_path = tmp_path / "test_mines.xlsx"
        data.to_excel(excel_path, sheet_name="Iron ore mines", index=False)

        # Act
        with patch("steelo.adapters.dataprocessing.excel_reader.translate_mine_regions_to_iso3", return_value={}):
            suppliers = read_mines_as_suppliers(
                mine_data_excel_path=str(excel_path),
                mine_data_sheet_name="Iron ore mines",
                location_csv=mock_location_csv,
            )

        # Assert: mine_price falls back to costs
        from steelo.domain import Year

        assert len(suppliers) == 1
        supplier = suppliers[0]
        assert supplier.mine_cost_by_year.get(Year(2025)) == 50
        assert supplier.mine_price_by_year.get(Year(2025)) == 50  # Falls back to costs
        assert supplier.production_cost_by_year.get(Year(2025)) == 50

    def test_nan_price_values_fall_back_to_costs(self, mock_location_csv, tmp_path):
        """Test that NaN values in price column fall back to costs."""
        # Setup: Create Excel with price column but some NaN values
        data = pd.DataFrame(
            {
                "Region": ["Australia", "Brazil", "China"],
                "Products": ["IO_low", "IO_high", "IO_mid"],
                "capacity Mtpa 2025": [100, 200, 150],
                "capacity Mtpa 2030": [100, 200, 150],
                "costs $/t 2025": [50, 60, 55],
                "costs $/t 2030": [50, 60, 55],
                "price $/t 2025": [65, float("nan"), 70],  # Brazil has NaN price
                "price $/t 2030": [65, float("nan"), 70],
                "lat": [-25.0, -15.0, 35.0],
                "lon": [133.0, -47.0, 104.0],
                "Mine": ["Mine A", "Mine B", "Mine C"],
            }
        )
        excel_path = tmp_path / "test_mines.xlsx"
        data.to_excel(excel_path, sheet_name="Iron ore mines", index=False)

        # Act
        with patch("steelo.adapters.dataprocessing.excel_reader.translate_mine_regions_to_iso3", return_value={}):
            suppliers = read_mines_as_suppliers(
                mine_data_excel_path=str(excel_path),
                mine_data_sheet_name="Iron ore mines",
                location_csv=mock_location_csv,
            )

        # Assert: All suppliers have numeric mine_price (no NaN)
        from steelo.domain import Year

        assert len(suppliers) == 3
        for supplier in suppliers:
            mine_price = supplier.mine_price_by_year.get(Year(2025))
            assert pd.notna(mine_price), f"mine_price should not be NaN for {supplier.supplier_id}"
            assert isinstance(mine_price, (int, float)), f"mine_price should be numeric for {supplier.supplier_id}"

        # Assert: Brazil mine (with NaN price) falls back to costs
        brazil_supplier = [s for s in suppliers if s.location.region == "Brazil"][0]
        assert brazil_supplier.mine_cost_by_year.get(Year(2025)) == 60
        assert brazil_supplier.mine_price_by_year.get(Year(2025)) == 60  # Falls back to costs because original was NaN
        assert brazil_supplier.production_cost_by_year.get(Year(2025)) == 60

        # Assert: Other mines use their actual price values
        australia_supplier = [s for s in suppliers if s.location.region == "Australia"][0]
        assert australia_supplier.mine_cost_by_year.get(Year(2025)) == 50
        assert australia_supplier.mine_price_by_year.get(Year(2025)) == 65  # Uses actual price value
        assert australia_supplier.production_cost_by_year.get(Year(2025)) == 50
