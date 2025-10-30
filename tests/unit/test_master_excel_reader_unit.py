"""Unit tests for MasterExcelReader methods."""

from pathlib import Path
from datetime import date
from steelo.adapters.dataprocessing.master_excel_reader import MasterExcelReader


class TestMasterExcelReaderUnits:
    """Unit tests for MasterExcelReader helper methods."""

    def test_get_iso3_from_country_standard_names(self):
        """Test ISO3 conversion for standard country names."""
        reader = MasterExcelReader(Path("dummy.xlsx"))

        assert reader._get_iso3_from_country("United States") == "USA"
        assert reader._get_iso3_from_country("Germany") == "DEU"
        assert reader._get_iso3_from_country("Japan") == "JPN"
        assert reader._get_iso3_from_country("France") == "FRA"
        assert reader._get_iso3_from_country("China") == "CHN"

    def test_get_iso3_from_country_special_cases(self):
        """Test ISO3 conversion for special case mappings."""
        reader = MasterExcelReader(Path("dummy.xlsx"))

        assert reader._get_iso3_from_country("USA") == "USA"
        assert reader._get_iso3_from_country("UK") == "GBR"
        assert reader._get_iso3_from_country("South Korea") == "KOR"
        assert reader._get_iso3_from_country("Türkiye") == "TUR"
        assert reader._get_iso3_from_country("Democratic Republic of the Congo") == "COD"

    def test_get_iso3_from_country_edge_cases(self):
        """Test ISO3 conversion for edge cases."""
        reader = MasterExcelReader(Path("dummy.xlsx"))

        assert reader._get_iso3_from_country("") == "XXX"
        assert reader._get_iso3_from_country(None) == "XXX"
        assert reader._get_iso3_from_country("   ") == "XXX"
        assert reader._get_iso3_from_country("NonExistentCountry") == "XXX"

    def test_parse_date_various_formats(self):
        """Test date parsing from various formats."""
        reader = MasterExcelReader(Path("dummy.xlsx"))

        # Year only as string
        assert reader._parse_date("2020") == date(2020, 1, 1)

        # Year as int
        assert reader._parse_date(2021) == date(2021, 1, 1)
        assert reader._parse_date(2021.0) == date(2021, 1, 1)

        # Empty values
        assert reader._parse_date(None) is None
        assert reader._parse_date("") is None
        assert reader._parse_date("   ") is None

        # Already a date object
        test_date = date(2022, 6, 15)
        assert reader._parse_date(test_date) == test_date

    def test_parse_date_pandas_compatibility(self):
        """Test date parsing with pandas NaT and other formats."""
        reader = MasterExcelReader(Path("dummy.xlsx"))
        import pandas as pd
        import numpy as np

        # Pandas NaT
        assert reader._parse_date(pd.NaT) is None

        # Numpy nan
        assert reader._parse_date(np.nan) is None

        # Invalid formats
        assert reader._parse_date("invalid") is None
        assert reader._parse_date("abc123") is None

    def test_validate_tech_switches_content(self):
        """Test validation of tech switches matrix content."""
        reader = MasterExcelReader(Path("dummy.xlsx"))
        import pandas as pd

        # Valid matrix
        valid_df = pd.DataFrame(
            {"BF": ["YES", None, "YES"], "EAF": [None, "YES", None], "DRI": ["YES", None, None]},
            index=["BF", "EAF", "DRI"],
        )

        errors = reader._validate_tech_switches_content(valid_df)
        # Should have 2 self-transition infos (BF and EAF)
        self_transition_errors = [e for e in errors if e.error_type == "SELF_TRANSITION"]
        assert len(self_transition_errors) == 2
        assert all(e.severity == "INFO" for e in self_transition_errors)

        # Invalid values
        invalid_df = pd.DataFrame(
            {
                "BF": ["YES", "NO"],
                "EAF": ["MAYBE", "YES"],
            },
            index=["BF", "EAF"],
        )

        errors = reader._validate_tech_switches_content(invalid_df)
        invalid_value_errors = [e for e in errors if e.error_type == "INVALID_VALUE"]
        assert len(invalid_value_errors) > 0
        assert any("MAYBE" in e.message for e in invalid_value_errors)
        assert any("NO" in e.message for e in invalid_value_errors)
