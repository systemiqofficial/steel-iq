"""Tests for MasterExcelReader fixes for coordinate-based ISO3, Prep Sinter, and gravity distances."""

import pytest
from datetime import date
from pathlib import Path
import pandas as pd
import tempfile
import pickle
from unittest.mock import patch

from steelo.adapters.dataprocessing.master_excel_reader import MasterExcelReader


class TestMasterExcelReaderFixes:
    """Test fixes for MasterExcelReader including ISO3 derivation, Prep Sinter, and gravity distances."""

    @pytest.fixture
    def sample_excel_data(self):
        """Create sample Excel data for testing."""
        data = {
            "Plant name": ["Border Plant 1", "Border Plant 2", "Iron Plant"],
            "Plant ID": ["P001", "P002", "P003"],
            "Country": ["France", "Russia", "Germany"],
            "Coordinates": ["48.5951, 7.8216", "61.1495, 28.7988", "51.0, 9.0"],
            "Main production equipment": ["BF;BOF", "EAF", "BF;DRI;BOF;EAF"],
            "Capacity operating status": ["operating", "operating", "operating"],
            "Nominal BF capacity (ttpa)": [1000, None, 2000],
            "Nominal DRI capacity (ttpa)": [None, None, 1500],
            "Nominal BOF steel capacity (ttpa)": [900, None, 1800],
            "Nominal EAF steel capacity (ttpa)": [None, 500, 1200],
            "Nominal iron capacity (ttpa)": [1000, 0, 3500],
            "Nominal crude steel capacity (ttpa)": [900, 500, 3000],
            "Power source": ["grid", "grid", "grid"],
            "SOE Status": ["private", "private", "private"],
            "Parent GEM ID": ["", "", ""],
            "Workforce size": ["100-500", "100-500", "500-1000"],
        }
        return pd.DataFrame(data)

    @pytest.fixture
    def gravity_distances_data(self):
        """Create sample gravity distances data."""
        return {
            "DEU": {"FRA": 100.0, "POL": 150.0, "ITA": 200.0},
            "FRA": {"DEU": 100.0, "ESP": 120.0, "ITA": 140.0},
            "FIN": {"RUS": 80.0, "SWE": 90.0, "EST": 70.0},
        }

    @pytest.fixture
    def excel_file_with_data(self, sample_excel_data):
        """Create a temporary Excel file with sample data."""
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tf:
            with pd.ExcelWriter(tf.name) as writer:
                sample_excel_data.to_excel(writer, sheet_name="Iron and steel plants", index=False)
            yield Path(tf.name)
            Path(tf.name).unlink()

    @pytest.fixture
    def gravity_distances_file(self, gravity_distances_data):
        """Create a temporary pickle file with gravity distances."""
        with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as tf:
            with open(tf.name, "wb") as f:
                pickle.dump(gravity_distances_data, f)
            yield Path(tf.name)
            Path(tf.name).unlink()

    @pytest.fixture
    def dynamic_feedstocks_mock(self):
        """Mock dynamic feedstocks data."""
        return {
            "BF": [{"name": "bf_feedstock", "metallic_charge": "sinter"}],
            "BOF": [{"name": "bof_feedstock", "metallic_charge": "pig_iron"}],
            "EAF": [{"name": "eaf_feedstock", "metallic_charge": "scrap"}],
            "DRI": [{"name": "dri_feedstock", "metallic_charge": "pellets"}],
            "Prep Sinter": [{"name": "prep_sinter_feedstock", "metallic_charge": "iron_ore"}],
        }

    def test_iso3_derivation_uses_coordinates_not_country_name(self, excel_file_with_data, dynamic_feedstocks_mock):
        """Test that ISO3 codes are derived from coordinates, not country names."""
        reader = MasterExcelReader(excel_file_with_data)

        # Mock derive_iso3 to return coordinate-based ISO3 codes
        with patch("steelo.adapters.dataprocessing.master_excel_reader.derive_iso3") as mock_derive_iso3:
            # Set up mock returns based on coordinates
            mock_derive_iso3.side_effect = lambda lat, lon: {
                (48.5951, 7.8216): "DEU",  # Near France-Germany border, should be DEU not FRA
                (61.1495, 28.7988): "FIN",  # Near Russia-Finland border, should be FIN not RUS
                (51.0, 9.0): "DEU",  # Germany, should remain DEU
            }.get((lat, lon), "XXX")

            plants, _ = reader.read_plants(dynamic_feedstocks_mock, current_date=date(2025, 1, 1))  # Unpack tuple

            # Verify derive_iso3 was called with correct coordinates
            assert mock_derive_iso3.call_count == 3
            mock_derive_iso3.assert_any_call(48.5951, 7.8216)
            mock_derive_iso3.assert_any_call(61.1495, 28.7988)
            mock_derive_iso3.assert_any_call(51.0, 9.0)

            # Verify plants have coordinate-based ISO3 codes
            plant_dict = {p.plant_id: p for p in plants}
            assert plant_dict["P001"].location.iso3 == "DEU"  # Not FRA
            assert plant_dict["P002"].location.iso3 == "FIN"  # Not RUS
            assert plant_dict["P003"].location.iso3 == "DEU"

    def test_prep_sinter_furnace_groups_added_for_iron_plants(self, excel_file_with_data, dynamic_feedstocks_mock):
        """Test that Prep Sinter furnace groups are NO LONGER automatically added to plants."""
        reader = MasterExcelReader(excel_file_with_data)

        with patch("steelo.adapters.dataprocessing.master_excel_reader.derive_iso3", return_value="DEU"):
            plants, _ = reader.read_plants(dynamic_feedstocks_mock, current_date=date(2025, 1, 1))  # Unpack tuple

        plant_dict = {p.plant_id: p for p in plants}

        # Plant P001 has BF (iron-making) - Prep Sinter no longer auto-added
        p001_fgs = {fg.technology.name for fg in plant_dict["P001"].furnace_groups}
        assert "Prep Sinter" not in p001_fgs

        # Plant P002 has only EAF (no iron-making) - should NOT have Prep Sinter
        p002_fgs = {fg.technology.name for fg in plant_dict["P002"].furnace_groups}
        assert "Prep Sinter" not in p002_fgs

        # Plant P003 has BF and DRI (iron-making) - Prep Sinter no longer auto-added
        p003_fgs = {fg.technology.name for fg in plant_dict["P003"].furnace_groups}
        assert "Prep Sinter" not in p003_fgs

    @pytest.mark.skip(reason="Gravity distances feature was disabled in commit 2baf1fe68 on 2025-08-04")
    def test_gravity_distances_loaded_to_plant_locations(
        self, excel_file_with_data, gravity_distances_file, dynamic_feedstocks_mock
    ):
        """Test that gravity distances are loaded and assigned to plant locations."""
        reader = MasterExcelReader(excel_file_with_data)

        with patch("steelo.adapters.dataprocessing.master_excel_reader.derive_iso3") as mock_derive_iso3:
            # Set up mock to return appropriate ISO3 codes
            mock_derive_iso3.side_effect = lambda lat, lon: {
                (48.5951, 7.8216): "DEU",
                (61.1495, 28.7988): "FIN",
                (51.0, 9.0): "DEU",
            }.get((lat, lon), "XXX")

            plants, _ = reader.read_plants(
                dynamic_feedstocks_mock,
                current_date=date(2025, 1, 1),
                gravity_distances_pkl_path=gravity_distances_file,
            )  # Unpack tuple

        plant_dict = {p.plant_id: p for p in plants}

        # Plant P001 should have DEU gravity distances
        assert plant_dict["P001"].location.distance_to_other_iso3 is not None
        assert plant_dict["P001"].location.distance_to_other_iso3 == {"FRA": 100.0, "POL": 150.0, "ITA": 200.0}

        # Plant P002 should have FIN gravity distances
        assert plant_dict["P002"].location.distance_to_other_iso3 is not None
        assert plant_dict["P002"].location.distance_to_other_iso3 == {"RUS": 80.0, "SWE": 90.0, "EST": 70.0}

        # Plant P003 should also have DEU gravity distances
        assert plant_dict["P003"].location.distance_to_other_iso3 is not None
        assert plant_dict["P003"].location.distance_to_other_iso3 == {"FRA": 100.0, "POL": 150.0, "ITA": 200.0}

    @pytest.mark.skip(reason="Gravity distances feature was disabled in commit 2baf1fe68 on 2025-08-04")
    def test_all_fixes_integrated(self, excel_file_with_data, gravity_distances_file, dynamic_feedstocks_mock):
        """Test that all three fixes work together correctly."""
        reader = MasterExcelReader(excel_file_with_data)

        with patch("steelo.adapters.dataprocessing.master_excel_reader.derive_iso3") as mock_derive_iso3:
            mock_derive_iso3.side_effect = lambda lat, lon: {
                (48.5951, 7.8216): "DEU",
                (61.1495, 28.7988): "FIN",
                (51.0, 9.0): "DEU",
            }.get((lat, lon), "XXX")

            plants, _ = reader.read_plants(
                dynamic_feedstocks_mock,
                current_date=date(2025, 1, 1),
                gravity_distances_pkl_path=gravity_distances_file,
            )  # Unpack tuple

        # Verify we have the expected number of plants
        assert len(plants) == 3

        # Check plant P003 which should have all features
        p003 = next(p for p in plants if p.plant_id == "P003")

        # Check ISO3 from coordinates
        assert p003.location.iso3 == "DEU"

        # Check gravity distances
        assert p003.location.distance_to_other_iso3 == {"FRA": 100.0, "POL": 150.0, "ITA": 200.0}

        # Check Prep Sinter was added
        tech_names = {fg.technology.name for fg in p003.furnace_groups}
        assert tech_names == {"BF", "DRI", "BOF", "EAF", "Prep Sinter"}

        # Verify total furnace group count
        assert len(p003.furnace_groups) == 5  # BF, DRI, BOF, EAF, Prep Sinter
