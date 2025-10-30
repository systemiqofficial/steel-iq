"""Integration test for recreate_from_package() method."""

import pytest
import pandas as pd
from unittest.mock import Mock, patch
from steelo.data.manager import DataManager
from steelo.data.recreate import DataRecreator


def test_recreate_from_package_shows_deprecation_warning(tmp_path, capsys):
    """Test that recreate_from_package shows deprecation warning for plants."""
    # Setup
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    # Create a minimal mock to speed up test - we only care about the warning
    # The warning is printed before any actual work is done

    manager = DataManager()
    recreator = DataRecreator(manager)

    # Mock the expensive package operations since we only care about the warning
    with patch.object(manager, "get_package_path") as mock_get_path:
        # Create a minimal package directory
        mock_package_dir = tmp_path / "mock_package"
        mock_package_dir.mkdir()
        mock_get_path.return_value = mock_package_dir

        with patch.object(manager, "_is_package_cached", return_value=True):
            with patch.object(manager.manifest, "get_package", return_value=None):
                # Execute - this should show a deprecation warning
                try:
                    recreator.recreate_from_package(
                        output_dir=output_dir, package_name="core-data", force_download=False
                    )
                except Exception:
                    # We don't care about errors after the warning is printed
                    pass

    # Verify warning is shown
    captured = capsys.readouterr()
    assert "WARNING: recreate_from_package() is deprecated for plants data" in captured.out
    assert "Plants should be created from master Excel" in captured.out


def test_recreate_with_config_creates_plants_from_master_excel(tmp_path, test_master_excel_path, monkeypatch):
    """Test that recreate_with_config properly creates plants from master Excel."""
    # Setup
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    # Create a minimal mock package directory structure
    # This avoids needing to download the actual package
    mock_cache_dir = tmp_path / "mock_cache"
    mock_package_dir = mock_cache_dir / "core-data" / "v1.0.3"
    mock_package_dir.mkdir(parents=True)

    # Create minimal files that might be needed
    (mock_package_dir / "gravity_distances_dict.pkl").write_bytes(b"mock pickle data")
    (mock_package_dir / "geolocator_raster.csv").write_text("lat,lon\n0,0")
    (mock_package_dir / "countries.csv").write_text("iso3,name\nUSA,United States")

    # Use the mock cache directory
    manager = DataManager(cache_dir=mock_cache_dir)

    # Mock the manifest to recognize our package
    mock_package = Mock()
    mock_package.name = "core-data"
    mock_package.version = "1.0.3"

    with patch.object(manager.manifest, "get_package", return_value=mock_package):
        # Also need to mock _get_package_dir to return our mock directory
        with patch.object(manager, "_get_package_dir", return_value=mock_package_dir):
            recreator = DataRecreator(manager)

            from steelo.data.recreation_config import RecreationConfig

            # Create only plants.json
            config = RecreationConfig(files_to_recreate=["plants.json"], skip_existing=False, verbose=False)

            # Execute
            created_paths = recreator.recreate_with_config(
                output_dir=output_dir,
                config=config,
                master_excel_path=test_master_excel_path,
                package_name="core-data",
            )

    # Verify
    assert "plants.json" in created_paths
    assert (output_dir / "plants.json").exists()

    # Load and verify content
    import json

    with open(output_dir / "plants.json") as f:
        plants_data = json.load(f)

    # Should have plants with dynamic business cases
    assert len(plants_data) > 0

    # Check that at least one plant has a technology with dynamic business cases
    has_business_cases = False
    # plants_data is a dict with "root" key containing list of plants
    plants_list = plants_data.get("root", [])
    for plant in plants_list:
        for fg in plant.get("furnace_groups", []):
            tech = fg.get("technology", {})
            if tech.get("dynamic_business_case"):
                has_business_cases = True
                break
        if has_business_cases:
            break

    assert has_business_cases, "No plant has technologies with dynamic business cases"


@pytest.fixture
def test_master_excel_path(tmp_path):
    """Create test master Excel file."""
    excel_path = tmp_path / "test_master_input.xlsx"

    # Create minimal plant data
    plant_data = {
        "Plant ID": ["P100000000001"],
        "Plant name (English)": ["Test Steel Plant 1"],
        "Country": ["USA"],
        "Coordinates": ["40.123,-82.456"],
        "Capacity operating status": ["operating"],
        "Start date": ["2009"],
        "Nominal crude steel capacity (ttpa)": [2000],
        "Nominal BOF steel capacity (ttpa)": [1200],
        "Nominal EAF steel capacity (ttpa)": [800],
        "Nominal BF capacity (ttpa)": [1800],
        "Main production equipment": ["BF;BOF;EAF"],
    }

    # Create minimal Bill of Materials data
    bom_data = {
        "Business case": ["iron_bf", "steel_bof", "steel_eaf"],
        "Metallic charge": ["pellets_low", "hot_metal", "scrap"],
        "Reductant": ["", "", ""],
        "Side": ["Input", "Input", "Input"],
        "Metric type": ["Feedstock", "Feedstock", "Feedstock"],
        "Type": [None, None, None],
        "Vector": ["pellets_low", "hot_metal", "scrap"],
        "Value": [1.6, 1.05, 1.1],
        "Unit": ["t/t", "t/t", "t/t"],
        "System boundary": ["cradle-to-gate", "cradle-to-gate", "cradle-to-gate"],
        "ghg_factor_scope_1": [0.0, 0.0, 0.0],
        "ghg_factor_scope_2": [0.0, 0.0, 0.0],
        "ghg_factor_scope_3_rest": [0.0, 0.0, 0.0],
    }

    # Create Excel file
    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        pd.DataFrame(plant_data).to_excel(writer, sheet_name="Iron and steel plants", index=False)
        pd.DataFrame(bom_data).to_excel(writer, sheet_name="Bill of Materials", index=False)

    return excel_path
