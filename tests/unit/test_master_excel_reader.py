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
                    "ISO3": ["DEU", "FRA"],
                }
            )
            with pd.ExcelWriter(tf.name) as writer:
                df.to_excel(writer, sheet_name="Iron and steel plants", index=False)
                self._create_minimal_bom_sheet(writer)

            reader = MasterExcelReader(Path(tf.name))
            with reader:
                plants, _, _ = reader.read_plants()  # Unpack tuple (3 values)
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
                    "ISO3": ["DEU", "FRA", "JPN"],
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
                plants, _, _ = reader.read_plants()  # Unpack tuple (3 values)

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

    def test_read_plants_reads_authored_iso3_and_geo_unit(self):
        """read_plants sources the authored geography from the sheet, accepting both forms.

        The ISO3 column carries a bare country or a combined geo_key ("CHN:CN-AH"); a bare
        country may instead carry its province in the separate geo_unit column. On conflict
        the combined ISO3 value wins, a blank geo_unit yields None (intentional
        country-level), and a blank ISO3 skips the row.
        """
        with tempfile.NamedTemporaryFile(suffix=".xlsx") as tf:
            df = pd.DataFrame(
                {
                    "Plant ID": ["P001", "P002", "P003", "P004", "P005"],
                    "Coordinates": [
                        "31.60, 118.50",
                        "39.00, 114.50",
                        "26.00, 119.00",
                        "52.52, 13.40",
                        "48.85, 2.35",
                    ],
                    "Country": ["China", "China", "China", "Germany", "France"],
                    "ISO3": ["CHN", "CHN:CN-HE", "CHN:CN-FJ", "DEU", None],
                    "geo_unit": ["CN-AH", None, "CN-LN", None, None],
                    "Main production equipment": ["BF", "BF", "BF", "EAF", "EAF"],
                    "Nominal BF capacity (ttpa)": [1000, 1000, 1000, 0, 0],
                    "Nominal EAF steel capacity (ttpa)": [0, 0, 0, 800, 600],
                    "Start date": ["2010", "2012", "2014", "2015", "2020"],
                },
            )
            with pd.ExcelWriter(tf.name) as writer:
                df.to_excel(writer, sheet_name="Iron and steel plants", index=False)
                self._create_minimal_bom_sheet(writer)

            reader = MasterExcelReader(Path(tf.name))
            with reader:
                plants, _, _ = reader.read_plants()

                # Blank ISO3 (P005) is skipped
                assert {p.plant_id for p in plants} == {"P001", "P002", "P003", "P004"}
                by_id = {p.plant_id: p for p in plants}

                # Bare country + separate geo_unit column
                assert by_id["P001"].location.iso3 == "CHN"
                assert by_id["P001"].location.geo_key == "CHN:CN-AH"

                # Combined geo_key in the ISO3 column
                assert by_id["P002"].location.iso3 == "CHN"
                assert by_id["P002"].location.geo_key == "CHN:CN-HE"

                # Conflict: the combined ISO3 value wins over the geo_unit column
                assert by_id["P003"].location.geo_key == "CHN:CN-FJ"

                # Bare country, no geo_unit: country-level
                assert by_id["P004"].location.geo_unit is None
                assert by_id["P004"].location.geo_key == "DEU"

    def test_read_plants_missing_iso3_and_country_columns_raises(self):
        """A plant sheet with neither ISO3 nor Country has no way to derive geography."""
        with tempfile.NamedTemporaryFile(suffix=".xlsx") as tf:
            df = pd.DataFrame(
                {
                    "Plant ID": ["P001"],
                    "Coordinates": ["52.52, 13.40"],
                    "Main production equipment": ["EAF"],
                    "Nominal EAF steel capacity (ttpa)": [800],
                    "Start date": ["2015"],
                },
            )
            with pd.ExcelWriter(tf.name) as writer:
                df.to_excel(writer, sheet_name="Iron and steel plants", index=False)
                self._create_minimal_bom_sheet(writer)

            reader = MasterExcelReader(Path(tf.name))
            with reader:
                with pytest.raises(ValueError, match="ISO3"):
                    reader.read_plants()

    def test_read_plants_missing_iso3_column_falls_back_to_country(self):
        """A plant sheet without ISO3 but with Country derives ISO3 from the country name
        (master input < v2.2, pre-dating the authored-ISO3 contract)."""
        with tempfile.NamedTemporaryFile(suffix=".xlsx") as tf:
            df = pd.DataFrame(
                {
                    "Plant ID": ["P001"],
                    "Coordinates": ["52.52, 13.40"],
                    "Country": ["Germany"],
                    "Main production equipment": ["EAF"],
                    "Nominal EAF steel capacity (ttpa)": [800],
                    "Start date": ["2015"],
                },
            )
            with pd.ExcelWriter(tf.name) as writer:
                df.to_excel(writer, sheet_name="Iron and steel plants", index=False)
                self._create_minimal_bom_sheet(writer)

            reader = MasterExcelReader(Path(tf.name))
            with reader:
                plants, _, _ = reader.read_plants()
                assert len(plants) == 1
                assert plants[0].location.iso3 == "DEU"

    def test_read_plants_unresolvable_country_name_raises(self):
        """A named country the mapping cannot resolve fails loudly rather than
        silently producing a plant with an empty ISO3."""
        with tempfile.NamedTemporaryFile(suffix=".xlsx") as tf:
            df = pd.DataFrame(
                {
                    "Plant ID": ["P001"],
                    "Coordinates": ["52.52, 13.40"],
                    "Country": ["Atlantis"],
                    "Main production equipment": ["EAF"],
                    "Nominal EAF steel capacity (ttpa)": [800],
                    "Start date": ["2015"],
                },
            )
            with pd.ExcelWriter(tf.name) as writer:
                df.to_excel(writer, sheet_name="Iron and steel plants", index=False)
                self._create_minimal_bom_sheet(writer)

            reader = MasterExcelReader(Path(tf.name))
            with reader:
                with pytest.raises(ValueError, match="Atlantis"):
                    reader.read_plants()

    def test_read_plants_skip_invalid_rows(self):
        """Test that invalid rows are skipped gracefully."""
        with tempfile.NamedTemporaryFile(suffix=".xlsx") as tf:
            # Mix of valid and invalid data
            df = pd.DataFrame(
                {
                    "Plant ID": ["P001", None, "P003", "P004"],
                    "Coordinates": ["52.52, 13.40", "48.85, 2.35", None, "invalid, 10.0"],
                    "Country": ["Germany", "France", "Japan", "Italy"],
                    "ISO3": ["DEU", "FRA", "JPN", "ITA"],
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
                plants, _, _ = reader.read_plants()  # Unpack tuple (3 values)

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
                    "ISO3": ["DEU", "FRA", "JPN", "USA", "GBR"],
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
                plants, _, _ = reader.read_plants()  # Unpack tuple (3 values)

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
                    "ISO3": ["DEU", "FRA", "JPN"],
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
                plants, _, _ = reader.read_plants()  # Unpack tuple (3 values)

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
