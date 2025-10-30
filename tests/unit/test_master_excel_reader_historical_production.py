"""Unit tests for MasterExcelReader historical production extraction."""

import pytest
import pandas as pd

from steelo.adapters.dataprocessing.master_excel_reader import MasterExcelReader


class TestMasterExcelReaderHistoricalProduction:
    """Test historical production extraction functionality."""

    def _create_minimal_bom_sheet(self, writer):
        """Create a minimal Bill of Materials sheet for testing."""
        bom_data = pd.DataFrame(
            {
                "Business case": ["iron_bf", "steel_bof", "steel_eaf", "iron_dri"],
                "Metallic charge": ["pellets_low", "hot_metal", "scrap", "pellets_mid"],
                "Reductant": [None, None, None, None],
                "Side": ["Input", "Input", "Input", "Input"],
                "Metric type": ["Feedstock", "Feedstock", "Feedstock", "Feedstock"],
                "Type": [None, None, None, None],
                "Vector": ["pellets_low", "hot_metal", "scrap", "pellets_mid"],
                "Value": [1.6, 1.0, 1.1, 1.5],
                "Unit": ["t/t", "t/t", "t/t", "t/t"],
                "System boundary": ["cradle-to-gate", "cradle-to-gate", "cradle-to-gate", "cradle-to-gate"],
                "ghg_factor_scope_1": [0.0, 0.0, 0.0, 0.0],
                "ghg_factor_scope_2": [0.0, 0.0, 0.0, 0.0],
                "ghg_factor_scope_3_rest": [0.0, 0.0, 0.0, 0.0],
            }
        )
        bom_data.to_excel(writer, sheet_name="Bill of Materials", index=False)

    @pytest.fixture
    def mock_excel_with_production(self, tmp_path):
        """Create a mock Excel file with plant and production data."""
        excel_path = tmp_path / "test_master.xlsx"

        with pd.ExcelWriter(excel_path) as writer:
            # Iron and steel plants sheet
            plants_df = pd.DataFrame(
                {
                    "Plant ID": ["P001", "P002", "P003"],
                    "Coordinates": ["52.52, 13.40", "48.85, 2.35", "35.68, 139.76"],
                    "Country": ["Germany", "France", "Japan"],
                    "Main production equipment": ["BF; BOF", "EAF", "BF; BOF; EAF"],
                    "Nominal BOF steel capacity (ttpa)": [1200, 0, 800],
                    "Nominal EAF steel capacity (ttpa)": [0, 800, 400],
                    "Nominal BF capacity (ttpa)": [1000, 0, 700],
                    "Start date": ["2010", "2015", "2012"],
                    "Capacity operating status": ["operating", "operating", "operating"],
                }
            )
            plants_df.to_excel(writer, sheet_name="Iron and steel plants", index=False)

            # Steel production by plant sheet with header at row 1
            production_df = pd.DataFrame(
                {
                    "Plant ID": ["P001", "P001", "P002", "P003", "P003", "P003"],
                    "Plant name (English)": ["Plant 1", "Plant 1", "Plant 2", "Plant 3", "Plant 3", "Plant 3"],
                    "BOF steel production 2019 (ttpa)": [1100, pd.NA, pd.NA, 750, pd.NA, pd.NA],
                    "BOF steel production 2020 (ttpa)": [1050, pd.NA, pd.NA, 700, pd.NA, pd.NA],
                    "BOF steel production 2021 (ttpa)": [1150, pd.NA, pd.NA, 780, pd.NA, pd.NA],
                    "BOF steel production 2022 (ttpa)": [1180, pd.NA, pd.NA, 790, pd.NA, pd.NA],
                    "EAF steel production 2019 (ttpa)": [pd.NA, pd.NA, 750, pd.NA, pd.NA, 380],
                    "EAF steel production 2020 (ttpa)": [pd.NA, pd.NA, 760, pd.NA, pd.NA, 390],
                    "EAF steel production 2021 (ttpa)": [pd.NA, pd.NA, "unknown", pd.NA, pd.NA, 395],
                    "EAF steel production 2022 (ttpa)": [pd.NA, pd.NA, 780, pd.NA, pd.NA, 400],
                    "BF production 2019 (ttpa)": [950, pd.NA, pd.NA, 680, pd.NA, pd.NA],
                    "BF production 2020 (ttpa)": [960, pd.NA, pd.NA, "N/A", pd.NA, pd.NA],
                    "BF production 2021 (ttpa)": [970, pd.NA, pd.NA, 690, pd.NA, pd.NA],
                    "BF production 2022 (ttpa)": [980, pd.NA, pd.NA, 695, pd.NA, pd.NA],
                }
            )
            # Write with a blank row first to simulate header at row 1
            empty_df = pd.DataFrame()
            empty_df.to_excel(writer, sheet_name="Steel production by plant", index=False)
            production_df.to_excel(writer, sheet_name="Steel production by plant", index=False, startrow=1)

            # Add minimal BOM sheet
            self._create_minimal_bom_sheet(writer)

        return excel_path

    def test_read_steel_production_sheet(self, mock_excel_with_production):
        """Test reading the 'Steel production by plant' sheet."""
        reader = MasterExcelReader(mock_excel_with_production)
        with reader:
            production_df = reader._read_steel_production_sheet()

            assert not production_df.empty
            assert "Plant ID" in production_df.columns
            assert "BOF steel production 2019 (ttpa)" in production_df.columns
            assert len(production_df) == 6

    def test_extract_historical_production_bof(self, mock_excel_with_production):
        """Test extracting BOF production data."""
        reader = MasterExcelReader(mock_excel_with_production)
        with reader:
            production_df = reader._read_steel_production_sheet()

            hist_prod = reader._extract_historical_production(production_df, plant_id="P001", technology="BOF")

            assert hist_prod == {"2019": 1100.0, "2020": 1050.0, "2021": 1150.0, "2022": 1180.0}

    def test_extract_historical_production_handles_unknown(self, mock_excel_with_production):
        """Test handling of 'unknown' and 'N/A' values."""
        reader = MasterExcelReader(mock_excel_with_production)
        with reader:
            production_df = reader._read_steel_production_sheet()

            # EAF for P002 has 'unknown' in 2021
            hist_prod = reader._extract_historical_production(production_df, plant_id="P002", technology="EAF")

            assert hist_prod == {
                "2019": 750.0,
                "2020": 760.0,
                # 2021 skipped due to 'unknown'
                "2022": 780.0,
            }

            # BF for P003 has 'N/A' in 2020
            hist_prod_bf = reader._extract_historical_production(production_df, plant_id="P003", technology="BF")

            assert hist_prod_bf == {
                "2019": 680.0,
                # 2020 skipped due to 'N/A'
                "2021": 690.0,
                "2022": 695.0,
            }

    def test_extract_historical_production_empty_plant(self, mock_excel_with_production):
        """Test extraction for a plant that doesn't exist."""
        reader = MasterExcelReader(mock_excel_with_production)
        with reader:
            production_df = reader._read_steel_production_sheet()

            hist_prod = reader._extract_historical_production(production_df, plant_id="P999", technology="BOF")

            assert hist_prod == {}

    def test_extract_historical_production_unsupported_technology(self, mock_excel_with_production):
        """Test extraction for unsupported technology."""
        reader = MasterExcelReader(mock_excel_with_production)
        with reader:
            production_df = reader._read_steel_production_sheet()

            hist_prod = reader._extract_historical_production(production_df, plant_id="P001", technology="INVALID")

            assert hist_prod == {}

    def test_plants_created_with_historical_production(self, mock_excel_with_production):
        """Test that plants are created with historical production data."""
        reader = MasterExcelReader(mock_excel_with_production)

        with reader:
            plants, _ = reader.read_plants()  # Unpack tuple

            # Check Plant P001
            p1 = next(p for p in plants if p.plant_id == "P001")

            # Check BOF furnace group has historical production
            bof_fg = next(fg for fg in p1.furnace_groups if fg.technology.name == "BOF")
            assert bof_fg.historical_production == {2019: 1100.0, 2020: 1050.0, 2021: 1150.0, 2022: 1180.0}

            # Check BF furnace group has historical production
            bf_fg = next(fg for fg in p1.furnace_groups if fg.technology.name == "BF")
            assert bf_fg.historical_production == {2019: 950.0, 2020: 960.0, 2021: 970.0, 2022: 980.0}

            # Check Plant P002
            p2 = next(p for p in plants if p.plant_id == "P002")

            # Check EAF furnace group has historical production (missing 2021)
            eaf_fg = next(fg for fg in p2.furnace_groups if fg.technology.name == "EAF")
            assert eaf_fg.historical_production == {
                2019: 750.0,
                2020: 760.0,
                # 2021 missing due to 'unknown'
                2022: 780.0,
            }

    @pytest.fixture
    def mock_excel_without_production_sheet(self, tmp_path):
        """Create a mock Excel file without the production sheet."""
        excel_path = tmp_path / "test_master_no_production.xlsx"

        with pd.ExcelWriter(excel_path) as writer:
            # Only Iron and steel plants sheet
            plants_df = pd.DataFrame(
                {
                    "Plant ID": ["P001"],
                    "Coordinates": ["52.52, 13.40"],
                    "Country": ["Germany"],
                    "Main production equipment": ["BF; BOF"],
                    "Nominal BOF steel capacity (ttpa)": [1200],
                    "Nominal BF capacity (ttpa)": [1000],
                    "Start date": ["2010"],
                    "Capacity operating status": ["operating"],
                }
            )
            plants_df.to_excel(writer, sheet_name="Iron and steel plants", index=False)

            # Add minimal BOM sheet
            self._create_minimal_bom_sheet(writer)

        return excel_path

    def test_read_steel_production_sheet_missing(self, mock_excel_without_production_sheet):
        """Test reading when 'Steel production by plant' sheet is missing."""
        reader = MasterExcelReader(mock_excel_without_production_sheet)
        with reader:
            production_df = reader._read_steel_production_sheet()

            assert production_df.empty

    def test_plants_created_without_production_sheet(self, mock_excel_without_production_sheet):
        """Test that plants still get created when production sheet is missing."""
        reader = MasterExcelReader(mock_excel_without_production_sheet)

        with reader:
            plants, _ = reader.read_plants()  # Unpack tuple

            # Check plants are created
            assert len(plants) == 1
            p1 = plants[0]

            # Check furnace groups have empty historical production
            for fg in p1.furnace_groups:
                if fg.technology.name != "Prep Sinter":  # Skip Prep Sinter which copies from template
                    assert fg.historical_production == {}
