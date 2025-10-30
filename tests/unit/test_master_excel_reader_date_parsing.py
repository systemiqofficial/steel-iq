"""Test for MasterExcelReader date parsing fix."""

import pytest
from datetime import date, datetime
from pathlib import Path
import pandas as pd
import tempfile

from steelo.adapters.dataprocessing.master_excel_reader import MasterExcelReader


class TestDateParsing:
    """Test date parsing functionality in MasterExcelReader."""

    @pytest.fixture
    def reader(self):
        """Create a MasterExcelReader instance with a dummy file."""
        with tempfile.NamedTemporaryFile(suffix=".xlsx") as tf:
            # Create minimal Excel file
            df = pd.DataFrame({"dummy": [1, 2, 3]})
            df.to_excel(tf.name)
            yield MasterExcelReader(Path(tf.name))

    def test_parse_date_valid_formats(self, reader):
        """Test parsing of various valid date formats."""
        # Test date object
        d = date(2020, 6, 15)
        assert reader._parse_date(d) == date(2020, 6, 15)

        # Test datetime object
        dt = datetime(2020, 6, 15, 10, 30)
        assert reader._parse_date(dt) == date(2020, 6, 15)

        # Test string formats
        assert reader._parse_date("2020-06-15") == date(2020, 6, 15)
        assert reader._parse_date("15/06/2020") == date(2020, 6, 15)
        assert reader._parse_date("06/15/2020") == date(2020, 6, 15)
        assert reader._parse_date("2020/06/15") == date(2020, 6, 15)

        # Test year-only string
        assert reader._parse_date("2020") == date(2020, 1, 1)

    def test_parse_date_excel_serial_numbers(self, reader):
        """Test parsing of Excel serial date numbers."""
        # With our new logic, values >= 1900 are treated as years
        # So we test smaller Excel serial dates that would result in dates >= 1900

        # Excel serial date 366 = 1900-12-31 (1899-12-30 + 366 days)
        assert reader._parse_date(366) == date(1900, 12, 31)

        # Excel serial date 1462 = 1904-01-01 (1899-12-30 + 1462 days)
        assert reader._parse_date(1462) == date(1904, 1, 1)

        # Large numbers (> 2100) should return None
        assert reader._parse_date(43831) is None  # Would be 2020 in Excel
        assert reader._parse_date(44927) is None  # Would be 2023 in Excel

    def test_parse_date_unrealistic_values(self, reader):
        """Test that unrealistic date values are handled properly."""
        # Very old year like 1878 should be treated as invalid
        # These are likely Excel serial dates that were misinterpreted
        result = reader._parse_date(1878)
        # Should either return None or a reasonable default, not 1878-01-01
        assert result is None or result.year >= 1900, (
            f"Unrealistic date year {result.year} - likely misinterpreted Excel serial date"
        )

        # Similarly for other unrealistic years
        result = reader._parse_date(1850)
        assert result is None or result.year >= 1900

    def test_parse_date_invalid_values(self, reader):
        """Test handling of invalid date values."""
        assert reader._parse_date(None) is None
        assert reader._parse_date("") is None
        assert reader._parse_date("   ") is None
        assert reader._parse_date(pd.NaT) is None
        assert reader._parse_date(float("nan")) is None

        # Invalid string formats should return None
        assert reader._parse_date("not-a-date") is None
        assert reader._parse_date("2020-13-45") is None


def test_furnace_group_renovation_dates():
    """Test that furnace groups get reasonable renovation dates."""
    with tempfile.NamedTemporaryFile(suffix=".xlsx") as tf:
        # Create test data with various date formats
        plant_data = pd.DataFrame(
            {
                "Plant ID": ["P001", "P002", "P003"],
                "Coordinates": ["52.52, 13.40", "48.85, 2.35", "35.68, 139.76"],
                "Country": ["Germany", "France", "Japan"],
                "Main production equipment": ["BF", "EAF", "BOF"],
                "Nominal BF capacity (ttpa)": [1000, 0, 0],
                "Nominal EAF steel capacity (ttpa)": [0, 500, 0],
                "Nominal BOF steel capacity (ttpa)": [0, 0, 800],
                "Start date": [
                    "2010-01-01",  # Normal date string
                    1970,  # Year as integer
                    1878,  # Unrealistic year - should be handled
                ],
                "Capacity operating status": ["operating", "operating", "operating"],
            }
        )

        # Write to Excel
        with pd.ExcelWriter(tf.name) as writer:
            plant_data.to_excel(writer, sheet_name="Iron and steel plants", index=False)

        # Read plants
        reader = MasterExcelReader(Path(tf.name))
        with reader:
            plants, _ = reader.read_plants(dynamic_feedstocks_dict={})  # Unpack tuple

        assert len(plants) == 3

        # Check first plant - normal date
        p1_fg = plants[0].furnace_groups[0]
        assert p1_fg.last_renovation_date == date(2010, 1, 1)

        # Check second plant - Excel serial date
        p2_fg = plants[1].furnace_groups[0]
        assert p2_fg.last_renovation_date == date(1970, 1, 1)

        # Check third plant - unrealistic date should be None or reasonable default
        p3_fg = plants[2].furnace_groups[0]
        assert p3_fg.last_renovation_date is None or p3_fg.last_renovation_date.year >= 1900, (
            f"Unrealistic renovation date: {p3_fg.last_renovation_date}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
