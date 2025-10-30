"""Test for handling duplicate mines in real-world data."""

import pandas as pd
import pytest
from unittest.mock import patch

from steelo.adapters.dataprocessing.excel_reader import read_mines_as_suppliers


class TestDuplicateMineHandling:
    """Test handling of mines with identical or near-identical data."""

    @pytest.fixture
    def mock_location_csv(self, tmp_path):
        """Create a mock location CSV file."""
        csv_path = tmp_path / "locations.csv"
        csv_path.write_text("COUNTRY,ISO,latitude,longitude\n")
        return str(csv_path)

    def test_mines_with_identical_data_get_unique_ids(self, mock_location_csv, tmp_path):
        """Test that mines with identical data still get unique IDs using row index."""
        # Create mock Excel data with two identical mines (no Mine column, as in real data)
        data = pd.DataFrame(
            {
                "Region": ["Australia", "Australia"],
                "Products": ["IO_low", "IO_low"],
                "capacity": [100, 100],  # Same capacity
                "costs": [50, 50],  # Same cost
                "lat": [-25.0, -25.0],  # Same coordinates
                "lon": [133.0, 133.0],
                "price": [0, 0],  # Same price (as in real data)
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

        # Assert: All supplier IDs are unique despite identical data
        supplier_ids = [s.supplier_id for s in suppliers]
        assert len(supplier_ids) == len(set(supplier_ids)), f"Duplicate IDs found: {supplier_ids}"
        assert len(suppliers) == 2, f"Expected 2 suppliers, got {len(suppliers)}"

    def test_real_world_china_mauritania_duplicates(self, mock_location_csv, tmp_path):
        """Test the exact duplicates found in real data: China and Mauritania mines."""
        # Recreate the exact duplicates found in the real master_input.xlsx
        data = pd.DataFrame(
            {
                "Region": ["China", "China", "Mauritania", "Mauritania"],
                "Products": ["IO_low", "IO_low", "IO_mid", "IO_mid"],
                "capacity": [1, 3, 28, 8],  # Different capacities
                "costs": [65, 65, 45, 45],  # Same costs within region
                "lat": [31.93, 31.93, 22.67, 22.67],  # Same coordinates
                "lon": [118.7, 118.7, -12.67, -12.67],
                "price": [0, 0, 0, 0],
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

        # Assert: All supplier IDs are unique
        supplier_ids = [s.supplier_id for s in suppliers]
        assert len(supplier_ids) == len(set(supplier_ids)), f"Duplicate IDs found: {supplier_ids}"
        assert len(suppliers) == 4, f"Expected 4 suppliers, got {len(suppliers)}"

        # Verify capacities are preserved
        china_suppliers = [s for s in suppliers if s.location.region == "China"]
        assert len(china_suppliers) == 2
        china_capacities = sorted([list(s.capacity_by_year.values())[0] / 1_000_000 for s in china_suppliers])
        assert abs(china_capacities[0] - 1.0) < 0.01, f"China capacity 1: {china_capacities[0]}"
        assert abs(china_capacities[1] - 3.0) < 0.01, f"China capacity 2: {china_capacities[1]}"

        mauritania_suppliers = [s for s in suppliers if s.location.region == "Mauritania"]
        assert len(mauritania_suppliers) == 2
        mauritania_capacities = sorted([list(s.capacity_by_year.values())[0] / 1_000_000 for s in mauritania_suppliers])
        assert abs(mauritania_capacities[0] - 8.0) < 0.01, f"Mauritania capacity 1: {mauritania_capacities[0]}"
        assert abs(mauritania_capacities[1] - 28.0) < 0.01, f"Mauritania capacity 2: {mauritania_capacities[1]}"

    def test_mines_with_same_coordinates_different_capacity(self, mock_location_csv, tmp_path):
        """Test mines at same location but different capacities get unique IDs."""
        # This scenario happens when multiple mines are at the same coordinates
        data = pd.DataFrame(
            {
                "Region": ["China", "China", "China"],
                "Products": ["IO_mid", "IO_mid", "IO_mid"],
                "capacity": [100, 150, 200],  # Different capacities
                "costs": [40, 42, 45],  # Different costs
                "lat": [35.0, 35.0, 35.0],  # Same coordinates
                "lon": [104.0, 104.0, 104.0],
                "price": [0, 0, 0],
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

        # Assert: All supplier IDs are unique
        supplier_ids = [s.supplier_id for s in suppliers]
        assert len(supplier_ids) == len(set(supplier_ids)), f"Duplicate IDs found: {supplier_ids}"
        assert len(suppliers) == 3, f"Expected 3 suppliers, got {len(suppliers)}"

        # Verify each supplier has correct capacity
        capacities = sorted([list(s.capacity_by_year.values())[0] / 1_000_000 for s in suppliers])
        expected_capacities = [100, 150, 200]
        for actual, expected in zip(capacities, expected_capacities):
            assert abs(actual - expected) < 0.01, f"Capacity mismatch: {actual} vs {expected}"

    def test_all_169_real_mines_get_unique_ids(self, mock_location_csv, tmp_path):
        """Test that all 169 mines from real data would get unique IDs."""
        # Create a large dataset similar to the real one
        regions = ["Australia"] * 50 + ["Brazil"] * 40 + ["China"] * 40 + ["India"] * 20 + ["Mauritania"] * 19
        products = (["IO_low"] * 20 + ["IO_mid"] * 20 + ["IO_high"] * 10) * 3 + ["IO_mid"] * 19

        data = pd.DataFrame(
            {
                "Region": regions,
                "Products": products,
                "capacity": [10 + i * 0.5 for i in range(169)],  # Varying capacities
                "costs": [40 + (i % 30) for i in range(169)],  # Varying costs
                "lat": [20.0 + (i % 50) * 0.1 for i in range(169)],  # Some duplicate coordinates
                "lon": [100.0 + (i % 50) * 0.1 for i in range(169)],
                "price": [0] * 169,
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

        # Assert: All 169 supplier IDs are unique
        supplier_ids = [s.supplier_id for s in suppliers]
        assert len(supplier_ids) == len(set(supplier_ids)), (
            f"Found {len(supplier_ids) - len(set(supplier_ids))} duplicate IDs"
        )
        assert len(suppliers) == 169, f"Expected 169 suppliers, got {len(suppliers)}"

        # Verify total capacity is preserved
        total_capacity = sum(list(s.capacity_by_year.values())[0] / 1_000_000 for s in suppliers)
        expected_total = sum(10 + i * 0.5 for i in range(169))
        assert abs(total_capacity - expected_total) < 0.1, (
            f"Total capacity mismatch: {total_capacity} vs {expected_total}"
        )
