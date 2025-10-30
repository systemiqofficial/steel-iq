# tests/unit/test_master_excel_reader_extended.py

import pytest
import pandas as pd
from pathlib import Path
from steelo.adapters.dataprocessing.master_excel_reader import MasterExcelReader, ExtractionResult


@pytest.fixture
def master_excel_with_plants(tmp_path):
    """Create a test Excel file with plant data."""
    excel_path = tmp_path / "test_master.xlsx"

    # Create test data matching the expected format
    plants_data = {
        "Plant ID": ["P100000001", "P100000002", "P100000003"],
        "Plant name (English)": ["Plant A", "Plant B", "Plant C"],
        "Country": ["USA", "Germany", "China"],
        "Coordinates": ["40.7128, -74.0060", "52.5200, 13.4050", "39.9042, 116.4074"],
        "Capacity operating status": ["operating", "construction", "operating"],
        "Main production equipment": ["BF;BOF", "DRI;EAF", "BF;BOF"],
        "Start date": ["2020", "2024", "2018"],
        "Nominal crude steel capacity (ttpa)": [1000, 500, 2000],
        "Nominal BOF steel capacity (ttpa)": [800, 0, 1600],
        "Nominal EAF steel capacity (ttpa)": [0, 500, 0],
        "Nominal iron capacity (ttpa)": [900, 450, 1800],
        "Nominal BF capacity (ttpa)": [900, 0, 1800],
        "Nominal DRI capacity (ttpa)": [0, 450, 0],
        "Power source": ["grid", "renewable", "grid"],
        "SOE Status": ["private", "private", "state"],
        "Parent GEM ID": ["", "", ""],
        "Workforce size": [1000, 500, 2000],
    }

    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        pd.DataFrame(plants_data).to_excel(writer, sheet_name="Iron and steel plants", index=False)

        # Add minimal Bill of Materials sheet
        bom_data = pd.DataFrame(
            {
                "Business case": ["iron_bf", "steel_bof", "steel_eaf"],
                "Metallic charge": ["pellets_low", "hot_metal", "scrap"],
                "Reductant": [None, None, None],
                "Side": ["Input", "Input", "Input"],
                "Metric type": ["Feedstock", "Feedstock", "Feedstock"],
                "Type": [None, None, None],
                "Vector": ["pellets_low", "hot_metal", "scrap"],
                "Value": [1.6, 1.0, 1.1],
                "Unit": ["t/t", "t/t", "t/t"],
                "System boundary": ["cradle-to-gate", "cradle-to-gate", "cradle-to-gate"],
                "ghg_factor_scope_1": [0.0, 0.0, 0.0],
                "ghg_factor_scope_2": [0.0, 0.0, 0.0],
                "ghg_factor_scope_3_rest": [0.0, 0.0, 0.0],
            }
        )
        bom_data.to_excel(writer, sheet_name="Bill of Materials", index=False)

    return excel_path


@pytest.fixture
def master_excel_with_bom(tmp_path):
    """Create a test Excel file with Bill of Materials data."""
    excel_path = tmp_path / "test_master_bom.xlsx"

    # Create test BOM data
    bom_data = {
        "Technology": ["BF", "BOF", "EAF", "DRI"],
        "Iron ore": [1600, 0, 0, 1400],
        "Scrap": [0, 200, 1100, 0],
        "Electricity": [100, 50, 450, 120],
        "Natural gas": [0, 0, 100, 1100],
        "Coal": [700, 0, 0, 0],
    }

    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        pd.DataFrame(bom_data).to_excel(writer, sheet_name="Bill of Materials", index=False)

    return excel_path


@pytest.fixture
def master_excel_with_all_sheets(tmp_path):
    """Create a test Excel file with all supported sheets."""
    excel_path = tmp_path / "test_master_complete.xlsx"

    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        # Plants
        plants_data = {
            "Plant ID": ["P001"],
            "Plant name": ["Test Plant"],
            "Country": ["USA"],
        }
        pd.DataFrame(plants_data).to_excel(writer, sheet_name="Iron and steel plants", index=False)

        # Tech switches
        tech_data = pd.DataFrame({"BF": ["", "YES"], "BOF": ["YES", ""]}, index=["BF", "BOF"])
        tech_data.to_excel(writer, sheet_name="Allowed tech switches")

        # Railway cost
        railway_data = {
            "ISO-3 Code": ["USA", "DEU"],
            "Railway capex": [50.0, 75.0],
            "Unit": ["USD/km", "USD/km"],
        }
        pd.DataFrame(railway_data).to_excel(writer, sheet_name="Railway cost", index=False)

        # Bill of Materials
        bom_data = {
            "Technology": ["BF", "BOF"],
            "Iron ore": [1600, 0],
        }
        pd.DataFrame(bom_data).to_excel(writer, sheet_name="Bill of Materials", index=False)

    return excel_path


def test_read_plants_success(master_excel_with_plants, tmp_path):
    """Test successful reading of plant data."""
    with MasterExcelReader(master_excel_with_plants, output_dir=tmp_path) as reader:
        plants, _ = reader.read_plants()  # Unpack tuple (plants, metadata)

        # read_plants now returns tuple of (list[Plant], dict)
        assert isinstance(plants, list)
        assert len(plants) == 3  # Should have 3 plants

        # Verify first plant
        plant = plants[0]
        assert plant.plant_id == "P100000001"
        assert plant.location.iso3 == "USA"  # location.country is iso3
        assert len(plant.furnace_groups) > 0


def test_read_plants_missing_sheet(tmp_path):
    """Test handling of missing plants sheet."""
    # Create Excel without plants sheet
    excel_path = tmp_path / "empty.xlsx"
    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        pd.DataFrame({"dummy": [1]}).to_excel(writer, sheet_name="Other", index=False)

    with MasterExcelReader(excel_path, output_dir=tmp_path) as reader:
        # read_plants now raises ValueError instead of returning ExtractionResult
        with pytest.raises(ValueError, match="Sheet 'Iron and steel plants' not found"):
            reader.read_plants()


def test_read_bom_success(master_excel_with_bom, tmp_path):
    """Test successful reading of Bill of Materials."""
    with MasterExcelReader(master_excel_with_bom, output_dir=tmp_path) as reader:
        result = reader.read_bom()

        assert result.success
        assert result.file_path.name == "BOM_ghg_system_boundary.xlsx"
        assert result.file_path.exists()


def test_extract_all_data(master_excel_with_all_sheets, tmp_path):
    """Test extracting all supported data types."""
    with MasterExcelReader(master_excel_with_all_sheets, output_dir=tmp_path) as reader:
        results = reader.extract_all_data()

        # Check that all expected keys are present
        # Note: steel_plants_csv_path is no longer included as read_plants() returns list[Plant]
        assert "tech_switches_csv_path" in results
        assert "railway_costs_json_path" in results
        assert "new_business_cases_excel_path" in results

        # Check that extractions were successful
        for field_name, result in results.items():
            assert isinstance(result, ExtractionResult)
            if result.success:
                assert result.file_path is not None
                assert result.file_path.exists()


def test_get_output_paths(master_excel_with_all_sheets, tmp_path):
    """Test getting output paths for SimulationConfig."""
    with MasterExcelReader(master_excel_with_all_sheets, output_dir=tmp_path) as reader:
        paths = reader.get_output_paths()

        # Should only contain successful extractions
        assert isinstance(paths, dict)
        for field_name, path in paths.items():
            assert isinstance(path, Path)
            assert path.exists()
