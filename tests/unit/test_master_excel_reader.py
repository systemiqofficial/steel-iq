"""Unit tests for MasterExcelReader components."""

import pytest
from datetime import date
from pathlib import Path
import pandas as pd
import tempfile

from steelo.adapters.dataprocessing.master_excel_reader import MasterExcelReader


class TestMasterExcelReaderHelpers:
    """Test helper methods in MasterExcelReader."""

    @pytest.fixture
    def reader(self):
        """Create a MasterExcelReader instance with a dummy file."""
        with tempfile.NamedTemporaryFile(suffix=".xlsx") as tf:
            # Create minimal Excel file
            df = pd.DataFrame({"dummy": [1, 2, 3]})
            df.to_excel(tf.name)
            yield MasterExcelReader(Path(tf.name))

    def test_get_iso3_from_country_common_names(self, reader):
        """Test ISO3 conversion for common country names."""
        # Test special mappings
        assert reader._get_iso3_from_country("USA") == "USA"
        assert reader._get_iso3_from_country("Germany") == "DEU"
        assert reader._get_iso3_from_country("Japan") == "JPN"
        assert reader._get_iso3_from_country("Türkiye") == "TUR"
        assert reader._get_iso3_from_country("Russia") == "RUS"
        assert reader._get_iso3_from_country("South Korea") == "KOR"
        assert reader._get_iso3_from_country("UK") == "GBR"
        assert reader._get_iso3_from_country("Ivory Coast") == "CIV"
        assert reader._get_iso3_from_country("Democratic Republic of the Congo") == "COD"

    def test_get_iso3_from_country_standard_names(self, reader):
        """Test ISO3 conversion for standard country names."""
        # Test pycountry lookup
        assert reader._get_iso3_from_country("France") == "FRA"
        assert reader._get_iso3_from_country("Canada") == "CAN"
        assert reader._get_iso3_from_country("Brazil") == "BRA"
        assert reader._get_iso3_from_country("Australia") == "AUS"
        assert reader._get_iso3_from_country("India") == "IND"
        assert reader._get_iso3_from_country("China") == "CHN"

    def test_get_iso3_from_country_fuzzy_matching(self, reader):
        """Test ISO3 conversion with fuzzy matching."""
        # Test variations that should still match
        assert reader._get_iso3_from_country("united states") == "USA"  # lowercase
        assert reader._get_iso3_from_country("GERMANY") == "DEU"  # uppercase
        assert reader._get_iso3_from_country("  Japan  ") == "JPN"  # with spaces

    def test_get_iso3_from_country_invalid(self, reader):
        """Test ISO3 conversion for invalid country names."""
        assert reader._get_iso3_from_country("InvalidCountryName") == "XXX"
        assert reader._get_iso3_from_country("") == "XXX"
        assert reader._get_iso3_from_country("123") == "XXX"

    def test_parse_date_various_formats(self, reader):
        """Test date parsing from various formats."""
        # String year only
        assert reader._parse_date("2020") == date(2020, 1, 1)
        assert reader._parse_date("2025") == date(2025, 1, 1)

        # Integer year
        assert reader._parse_date(2020) == date(2020, 1, 1)
        assert reader._parse_date(2025.0) == date(2025, 1, 1)

        # Full date strings
        assert reader._parse_date("2020-06-15") == date(2020, 6, 15)
        assert reader._parse_date("15/06/2020") == date(2020, 6, 15)

        # Date object
        test_date = date(2020, 6, 15)
        assert reader._parse_date(test_date) == test_date

    def test_parse_date_invalid(self, reader):
        """Test date parsing with invalid inputs."""
        assert reader._parse_date(None) is None
        assert reader._parse_date("") is None
        assert reader._parse_date("invalid") is None
        assert reader._parse_date("99999") is None  # Invalid year

        # pandas NA values
        assert reader._parse_date(pd.NA) is None
        assert reader._parse_date(pd.NaT) is None
        assert reader._parse_date(float("nan")) is None


class TestMasterExcelReaderValidation:
    """Test validation and error handling in MasterExcelReader."""

    def _create_minimal_bom_sheet(self, writer):
        """Create a minimal Bill of Materials sheet for testing."""
        bom_data = pd.DataFrame(
            {
                "Business case": ["iron_bf", "steel_bof", "steel_eaf", "iron_dri", "iron_esf"],
                "Metallic charge": ["pellets_low", "hot_metal", "scrap", "pellets_mid", "dri_low"],
                "Reductant": [None, None, None, None, None],
                "Side": ["Input", "Input", "Input", "Input", "Input"],
                "Metric type": ["Feedstock", "Feedstock", "Feedstock", "Feedstock", "Feedstock"],
                "Type": [None, None, None, None, None],
                "Vector": ["pellets_low", "hot_metal", "scrap", "pellets_mid", "dri_low"],
                "Value": [1.6, 1.0, 1.1, 1.5, 1.0],
                "Unit": ["t/t", "t/t", "t/t", "t/t", "t/t"],
                "System boundary": [
                    "cradle-to-gate",
                    "cradle-to-gate",
                    "cradle-to-gate",
                    "cradle-to-gate",
                    "cradle-to-gate",
                ],
                "ghg_factor_scope_1": [0.0, 0.0, 0.0, 0.0, 0.0],
                "ghg_factor_scope_2": [0.0, 0.0, 0.0, 0.0, 0.0],
                "ghg_factor_scope_3_rest": [0.0, 0.0, 0.0, 0.0, 0.0],
            }
        )
        bom_data.to_excel(writer, sheet_name="Bill of Materials", index=False)

    def test_read_plants_missing_sheet(self):
        """Test error handling when Iron and steel plants sheet is missing."""
        with tempfile.NamedTemporaryFile(suffix=".xlsx") as tf:
            # Create Excel without the required sheet
            df = pd.DataFrame({"dummy": [1, 2, 3]})
            with pd.ExcelWriter(tf.name) as writer:
                df.to_excel(writer, sheet_name="Wrong Sheet")

            reader = MasterExcelReader(Path(tf.name))
            with pytest.raises(ValueError, match="Sheet 'Iron and steel plants' not found"):
                with reader:
                    _ = reader.read_plants()  # Returns tuple

    def test_read_plants_missing_critical_columns(self):
        """Test handling of missing critical columns."""
        with tempfile.NamedTemporaryFile(suffix=".xlsx") as tf:
            # Create Excel with missing critical columns
            df = pd.DataFrame(
                {
                    "Plant ID": ["P001", "P002"],
                    # Missing Latitude and Longitude
                    "Country": ["Germany", "France"],
                }
            )
            with pd.ExcelWriter(tf.name) as writer:
                df.to_excel(writer, sheet_name="Iron and steel plants", index=False)
                self._create_minimal_bom_sheet(writer)

            reader = MasterExcelReader(Path(tf.name))
            with reader:
                plants, _ = reader.read_plants()  # Unpack tuple
                # Should return empty list as all plants are skipped
                assert len(plants) == 0

    def test_read_plants_valid_data(self):
        """Test successful plant reading with valid data."""
        with tempfile.NamedTemporaryFile(suffix=".xlsx") as tf:
            # Create valid Excel data
            df = pd.DataFrame(
                {
                    "Plant ID": ["P001", "P002", "P003"],
                    "Coordinates": ["52.52, 13.40", "48.85, 2.35", "35.68, 139.76"],
                    "Country": ["Germany", "France", "Japan"],
                    "Main production equipment": ["BF; BOF", "EAF", "BF; EAF"],
                    "Nominal BF capacity (ttpa)": [1000, 0, 500],
                    "Nominal BOF steel capacity (ttpa)": [1200, 0, 0],
                    "Nominal EAF steel capacity (ttpa)": [0, 800, 600],
                    "Start date": ["2010", "2015", "2020-06-15"],
                    "Capacity operating status": ["operating", "operating", "planned"],
                    "Power source": ["grid", "renewable", "grid"],
                    "SOE Status": ["private", "private", "state"],
                    "Parent GEM ID": ["GEM001", "GEM002", "GEM003"],
                    "Workforce size": [1000, 500, 750],
                }
            )
            with pd.ExcelWriter(tf.name) as writer:
                df.to_excel(writer, sheet_name="Iron and steel plants", index=False)
                self._create_minimal_bom_sheet(writer)

            reader = MasterExcelReader(Path(tf.name))
            with reader:
                plants, _ = reader.read_plants()  # Unpack tuple

                # Check we got all plants
                assert len(plants) == 3

                # Check first plant details
                p1 = plants[0]
                assert p1.plant_id == "P001"
                assert p1.location.iso3 == "DEU"
                assert p1.location.lat == 52.52
                assert p1.location.lon == 13.40
                assert len(p1.furnace_groups) == 2  # BF and BOF (Prep Sinter no longer auto-added)
                assert p1.power_source == "grid"
                assert p1.workforce_size == 1000

                # Check furnace groups (capacity now in tonnes, not kilotonnes)
                bf_group = next(fg for fg in p1.furnace_groups if fg.technology.name == "BF")
                assert bf_group.capacity == 1000000  # 1000 kt = 1,000,000 t
                assert bf_group.technology.product == "iron"

                bof_group = next(fg for fg in p1.furnace_groups if fg.technology.name == "BOF")
                assert bof_group.capacity == 1200000  # 1200 kt = 1,200,000 t
                assert bof_group.technology.product == "steel"

                # Prep Sinter is no longer automatically added
                # Check that there's no Prep Sinter group
                prep_sinter_groups = [fg for fg in p1.furnace_groups if fg.technology.name == "Prep Sinter"]
                assert len(prep_sinter_groups) == 0  # No Prep Sinter auto-added

                # Check second plant
                p2 = plants[1]
                assert p2.plant_id == "P002"
                assert p2.location.iso3 == "FRA"
                assert len(p2.furnace_groups) == 1  # Only EAF
                assert p2.furnace_groups[0].technology.name == "EAF"
                assert p2.furnace_groups[0].capacity == 800000  # 800 kt = 800,000 t

                # Check third plant with parsed date
                p3 = plants[2]
                assert p3.plant_id == "P003"
                assert p3.location.iso3 == "JPN"
                assert len(p3.furnace_groups) == 2  # BF and EAF (Prep Sinter no longer auto-added)
                assert p3.furnace_groups[0].status == "planned"
                # Check date was parsed correctly
                eaf_group = next(fg for fg in p3.furnace_groups if fg.technology.name == "EAF")
                assert eaf_group.last_renovation_date == date(2020, 6, 15)

    def test_read_plants_skip_invalid_rows(self):
        """Test that invalid rows are skipped gracefully."""
        with tempfile.NamedTemporaryFile(suffix=".xlsx") as tf:
            # Mix of valid and invalid data
            df = pd.DataFrame(
                {
                    "Plant ID": ["P001", None, "P003", "P004"],
                    "Coordinates": ["52.52, 13.40", "48.85, 2.35", None, "invalid, 10.0"],
                    "Country": ["Germany", "France", "Japan", "Italy"],
                    "Main production equipment": ["BF", "EAF", "BF", "EAF"],
                    "Nominal BF capacity (ttpa)": [1000, 0, 500, 0],
                    "Nominal EAF steel capacity (ttpa)": [0, 800, 0, 600],
                    "Start date": ["2010", "2015", "2020", "2022"],
                }
            )
            with pd.ExcelWriter(tf.name) as writer:
                df.to_excel(writer, sheet_name="Iron and steel plants", index=False)
                self._create_minimal_bom_sheet(writer)

            reader = MasterExcelReader(Path(tf.name))
            with reader:
                plants, _ = reader.read_plants()  # Unpack tuple

                # Should skip rows with None plant_id, None lat/lon, invalid lat
                assert len(plants) == 1
                assert plants[0].plant_id == "P001"

    def test_read_plants_equipment_parsing(self):
        """Test parsing of various equipment configurations."""
        with tempfile.NamedTemporaryFile(suffix=".xlsx") as tf:
            df = pd.DataFrame(
                {
                    "Plant ID": ["P001", "P002", "P003", "P004", "P005"],
                    "Coordinates": ["52.52, 13.40", "48.85, 2.35", "35.68, 139.76", "40.71, -74.01", "51.51, -0.13"],
                    "Country": ["Germany", "France", "Japan", "USA", "UK"],
                    "Main production equipment": [
                        "BF; BOF; EAF",  # Multiple technologies
                        "DRI; EAF",  # DRI + EAF
                        "ESF",  # Less common
                        "",  # Empty equipment
                        "Unknown; BF",  # Mix of valid and invalid
                    ],
                    "Nominal BF capacity (ttpa)": [1000, 0, 0, 0, 500],
                    "Nominal DRI capacity (ttpa)": [0, 800, 0, 0, 0],
                    "Nominal ESF capacity (ttpa)": [0, 0, 600, 0, 0],
                    "Nominal BOF steel capacity (ttpa)": [1200, 0, 0, 0, 0],
                    "Nominal EAF steel capacity (ttpa)": [300, 900, 0, 0, 0],
                    "Start date": ["2010", "2015", "2020", "2022", "2018"],
                }
            )
            with pd.ExcelWriter(tf.name) as writer:
                df.to_excel(writer, sheet_name="Iron and steel plants", index=False)
                self._create_minimal_bom_sheet(writer)

            reader = MasterExcelReader(Path(tf.name))
            with reader:
                plants, _ = reader.read_plants()  # Unpack tuple

                # P001: Should have 3 furnace groups (BF, BOF, EAF - no auto Prep Sinter)
                p1 = next(p for p in plants if p.plant_id == "P001")
                assert len(p1.furnace_groups) == 3  # BF + BOF + EAF (no Prep Sinter)

                # P002: DRI + EAF (no Prep Sinter)
                p2 = next(p for p in plants if p.plant_id == "P002")
                assert len(p2.furnace_groups) == 2  # DRI + EAF (no Prep Sinter)
                dri = next(fg for fg in p2.furnace_groups if fg.technology.name == "DRI")
                assert dri.technology.product == "iron"
                assert dri.capacity == 800000  # 800 kt = 800,000 t

                # P003: ESF only (no Prep Sinter)
                p3 = next(p for p in plants if p.plant_id == "P003")
                assert len(p3.furnace_groups) == 1  # ESF only (no Prep Sinter)
                esf = next(fg for fg in p3.furnace_groups if fg.technology.name == "ESF")
                assert esf.technology.name == "ESF"
                assert esf.technology.product == "iron"

                # P004: Empty equipment, should be skipped
                assert not any(p.plant_id == "P004" for p in plants)

                # P005: Only valid equipment should be included (no Prep Sinter auto-added)
                p5 = next(p for p in plants if p.plant_id == "P005")
                assert len(p5.furnace_groups) == 1  # BF only (no Prep Sinter)
                bf = next(fg for fg in p5.furnace_groups if fg.technology.name == "BF")
                assert bf.technology.name == "BF"

    def test_read_plants_ohf_to_eaf_conversion(self):
        """Test that OHF capacity is transferred to EAF capacity."""
        with tempfile.NamedTemporaryFile(suffix=".xlsx") as tf:
            df = pd.DataFrame(
                {
                    "Plant ID": ["P001", "P002", "P003"],
                    "Coordinates": ["52.52, 13.40", "48.85, 2.35", "35.68, 139.76"],
                    "Country": ["Germany", "France", "Japan"],
                    "Main production equipment": [
                        "OHF; EAF",  # Both OHF and EAF
                        "OHF",  # Only OHF (should become EAF)
                        "BF; OHF",  # BF + OHF (OHF should become EAF)
                    ],
                    "Nominal BF capacity (ttpa)": [0, 0, 1000],
                    "Nominal OHF steel capacity (ttpa)": [500, 700, 300],
                    "Nominal EAF steel capacity (ttpa)": [400, 0, 0],
                    "Start date": ["2010", "2015", "2020"],
                }
            )
            with pd.ExcelWriter(tf.name) as writer:
                df.to_excel(writer, sheet_name="Iron and steel plants", index=False)
                self._create_minimal_bom_sheet(writer)

            reader = MasterExcelReader(Path(tf.name))
            with reader:
                plants, _ = reader.read_plants()  # Unpack tuple

                # P001: OHF + EAF -> Should have only EAF with combined capacity
                p1 = next(p for p in plants if p.plant_id == "P001")
                assert len(p1.furnace_groups) == 1  # Only EAF
                eaf = p1.furnace_groups[0]
                assert eaf.technology.name == "EAF"
                assert eaf.capacity == 900000  # 500 (OHF) + 400 (EAF) = 900 kt = 900,000 t

                # P002: Only OHF -> Should become EAF
                p2 = next(p for p in plants if p.plant_id == "P002")
                assert len(p2.furnace_groups) == 1  # Only EAF
                eaf = p2.furnace_groups[0]
                assert eaf.technology.name == "EAF"
                assert eaf.capacity == 700000  # 700 kt = 700,000 t

                # P003: BF + OHF -> Should have BF + EAF
                p3 = next(p for p in plants if p.plant_id == "P003")
                assert len(p3.furnace_groups) == 2  # BF + EAF
                bf = next(fg for fg in p3.furnace_groups if fg.technology.name == "BF")
                assert bf.capacity == 1000000  # 1000 kt = 1,000,000 t
                eaf = next(fg for fg in p3.furnace_groups if fg.technology.name == "EAF")
                assert eaf.technology.name == "EAF"
                assert eaf.capacity == 300000  # 300 kt = 300,000 t
