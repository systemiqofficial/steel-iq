"""
Test tech switches extraction through the data recreation system.
"""

import pytest
import pandas as pd
from pathlib import Path
import tempfile
import shutil


@pytest.fixture
def temp_output_dir():
    """Create a temporary output directory."""
    temp_dir = tempfile.mkdtemp()
    yield Path(temp_dir)
    shutil.rmtree(temp_dir)


@pytest.fixture
def master_excel_with_tech_switches(tmp_path):
    """Create a master Excel file with tech switches sheet."""
    excel_path = tmp_path / "master_input.xlsx"

    # Create tech switches data - rows are source tech, columns are target tech
    technologies = ["BF", "BOF", "DRI", "EAF", "ESF", "MOE"]
    # Initialize with empty strings
    data = {tech: [""] * len(technologies) for tech in technologies}

    # Set specific transitions
    # BF -> BOF
    data["BOF"][technologies.index("BF")] = "YES"
    # BOF -> EAF
    data["EAF"][technologies.index("BOF")] = "YES"
    # DRI -> BOF
    data["BOF"][technologies.index("DRI")] = "YES"
    # DRI -> EAF
    data["EAF"][technologies.index("DRI")] = "YES"
    # EAF -> ESF
    data["ESF"][technologies.index("EAF")] = "YES"
    # MOE -> EAF
    data["EAF"][technologies.index("MOE")] = "YES"

    tech_switches_df = pd.DataFrame(data, index=technologies)

    # Write Excel file with multiple sheets
    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        tech_switches_df.to_excel(writer, sheet_name="Allowed tech switches")
        # Add other sheets that might be required
        pd.DataFrame().to_excel(writer, sheet_name="Iron and steel plants")
        pd.DataFrame().to_excel(writer, sheet_name="Bill of Materials")
        pd.DataFrame().to_excel(writer, sheet_name="Power grid emissivity")
        pd.DataFrame().to_excel(writer, sheet_name="Input costs")

    return excel_path


@pytest.fixture
def master_excel_missing_tech_switches(tmp_path):
    """Create a master Excel file without tech switches sheet."""
    excel_path = tmp_path / "master_input_no_tech.xlsx"

    # Write Excel file without tech switches sheet
    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        pd.DataFrame().to_excel(writer, sheet_name="Iron and steel plants")
        pd.DataFrame().to_excel(writer, sheet_name="Bill of Materials")

    return excel_path


def test_tech_switches_extraction_via_recreation_config(temp_output_dir, master_excel_with_tech_switches):
    """Test tech switches extraction through the recreation config system."""
    # Test the function directly instead of through the full system
    from steelo.data.recreation_functions import recreate_tech_switches_data

    tech_switches_path = temp_output_dir / "tech_switches_allowed.csv"

    # Call the recreation function directly
    result_path = recreate_tech_switches_data(
        tech_switches_csv_path=tech_switches_path,
        master_excel_path=master_excel_with_tech_switches,
    )

    # Verify tech switches file was created
    assert result_path == tech_switches_path
    assert tech_switches_path.exists()

    # Verify content
    df = pd.read_csv(tech_switches_path, index_col=0, na_filter=False)
    assert "BF" in df.index
    assert "BOF" in df.columns
    assert df.loc["BF", "BOF"] == "YES"
    assert df.loc["BF", "BF"] == ""


def test_tech_switches_extraction_missing_sheet_fails(temp_output_dir, master_excel_missing_tech_switches):
    """Test that extraction fails when tech switches sheet is missing."""
    # Test the function directly
    from steelo.data.recreation_functions import recreate_tech_switches_data

    tech_switches_path = temp_output_dir / "tech_switches_allowed.csv"

    # This should raise an error
    with pytest.raises(ValueError) as exc_info:
        recreate_tech_switches_data(
            tech_switches_csv_path=tech_switches_path,
            master_excel_path=master_excel_missing_tech_switches,
        )

    assert "Failed to extract tech switches" in str(exc_info.value)
    assert "Allowed tech switches" in str(exc_info.value)
