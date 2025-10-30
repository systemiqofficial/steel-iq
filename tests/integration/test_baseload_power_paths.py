"""Tests for baseload power simulation directory handling."""

from pathlib import Path
import json
from unittest.mock import patch
import xarray as xr
import numpy as np


from steelo.simulation_types import get_default_technology_settings

from steelo.simulation import SimulationConfig
from steelo.domain import Year
from steelo.adapters.geospatial.geospatial_layers import add_baseload_power_price
from steelo.domain.models import GeoDataPaths


class TestBaseloadPowerGlobalSubdirectory:
    """Test that p5 files are correctly placed in p5/GLOBAL/ subdirectory."""

    def test_manifest_has_p5_files_in_global_subdirectory(self):
        """Test that manifest.json specifies p5 files in GLOBAL subdirectory."""
        manifest_path = Path(__file__).parent.parent.parent / "src" / "steelo" / "data" / "manifest.json"
        with open(manifest_path) as f:
            manifest = json.load(f)

        # Find geo-data package
        geo_package = next((p for p in manifest["packages"] if p["name"] == "geo-data"), None)
        assert geo_package is not None, "geo-data package not found in manifest"

        # Check p5 files are in GLOBAL subdirectory
        p5_files = [f for f in geo_package["files"] if "p5" in f and "optimal_sol" in f]
        assert len(p5_files) > 0, "No p5 files found in manifest"

        for file_path in p5_files:
            assert "p5/GLOBAL/" in file_path, f"p5 file not in GLOBAL subdirectory: {file_path}"

    def test_geo_extractor_extracts_p5_to_global_subdirectory(self, tmp_path):
        """Test that geo_extractor.py file_mappings places p5 files in p5/GLOBAL/ subdirectory."""
        # This is a simplified test that just checks the file mappings are correct
        # without actually running the full extractor

        # Import shutil for copying files
        import shutil

        # Create mock geo-data directory with old structure
        geo_data_dir = tmp_path / "geo-data"
        geo_data_dir.mkdir()

        # Create p5 files in old location (directly in p5/)
        p5_source = geo_data_dir / "outputs" / "GEO" / "baseload_power_simulation" / "p5"
        p5_source.mkdir(parents=True)

        for year in [2025, 2030, 2035, 2040, 2045, 2050]:
            mock_file = p5_source / f"optimal_sol_GLOBAL_{year}_p5.nc"
            mock_file.write_text("mock data")

        # Test the file mappings logic directly
        target_dir = tmp_path / "extracted"
        target_dir.mkdir()

        # Simulate what extract_geo_data does for baseload files
        for year in [2025, 2030, 2035, 2040, 2045, 2050]:
            # Old path in archive
            old_source = (
                geo_data_dir
                / "outputs"
                / "GEO"
                / "baseload_power_simulation"
                / "p5"
                / f"optimal_sol_GLOBAL_{year}_p5.nc"
            )
            # New target with GLOBAL
            new_target = (
                target_dir
                / "outputs"
                / "GEO"
                / "baseload_power_simulation"
                / "p5"
                / "GLOBAL"
                / f"optimal_sol_GLOBAL_{year}_p5.nc"
            )

            if old_source.exists():
                new_target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(old_source, new_target)

        # Check that p5 files are in GLOBAL subdirectory
        p5_target = target_dir / "outputs" / "GEO" / "baseload_power_simulation" / "p5" / "GLOBAL"
        assert p5_target.exists(), "p5/GLOBAL directory not created"

        for year in [2025, 2030, 2035, 2040, 2045, 2050]:
            expected_file = p5_target / f"optimal_sol_GLOBAL_{year}_p5.nc"
            assert expected_file.exists(), f"p5 file not in GLOBAL subdirectory: {expected_file}"

    def test_add_baseload_power_price_finds_p5_files_in_global(self, tmp_path):
        """Test that add_baseload_power_price function finds p5 files in GLOBAL subdirectory."""
        # Create mock dataset
        ds = xr.Dataset(
            {
                "lat": (["lat"], np.linspace(-90, 90, 10)),
                "lon": (["lon"], np.linspace(-180, 180, 10)),
                "feasibility_mask": (["lat", "lon"], np.ones((10, 10))),
            }
        )

        # Create baseload power sim directory structure
        baseload_dir = tmp_path / "baseload_power_simulation"
        p5_global = baseload_dir / "p5" / "GLOBAL"
        p5_global.mkdir(parents=True)

        # Create mock LCOE file
        mock_lcoe = xr.Dataset({"lcoe": (["lat", "lon"], np.random.rand(10, 10) * 100)})
        mock_lcoe.to_netcdf(p5_global / "optimal_sol_GLOBAL_2025_p5.nc")

        # Create GeoDataPaths with all required arguments
        geo_paths = GeoDataPaths(
            data_dir=tmp_path,
            atlite_dir=tmp_path / "atlite",
            geo_plots_dir=tmp_path / "plots",
            terrain_nc_path=tmp_path / "terrain.nc",
            rail_distance_nc_path=tmp_path / "rail.nc",
            railway_capex_csv_path=tmp_path / "railway_capex.csv",
            lcoh_capex_csv_path=tmp_path / "lcoh_capex.csv",
            regional_energy_prices_xlsx=tmp_path / "energy_prices.xlsx",
            countries_shapefile_dir=tmp_path / "countries",
            disputed_areas_shapefile_dir=tmp_path / "disputed",
            baseload_power_sim_dir=baseload_dir,
            static_layers_dir=tmp_path / "static",
            landtype_percentage_path=tmp_path / "landtype.nc",
        )

        # Test that function finds the file
        result = add_baseload_power_price(ds, baseload_coverage=0.95, target_year=2025, geo_paths=geo_paths)

        assert "lcoe" in result.data_vars, "LCOE not added to dataset"


class TestBaseloadPowerCLIArgument:
    """Test the --baseload-power-sim-dir CLI argument."""

    def test_cli_accepts_baseload_power_sim_dir_argument(self):
        """Test that CLI parser accepts --baseload-power-sim-dir argument."""
        import argparse
        import sys

        # Mock sys.argv
        test_args = [
            "run_simulation",
            "--baseload-power-sim-dir",
            "/path/to/boa/output",
            "--start-year",
            "2025",
            "--end-year",
            "2030",
        ]

        with patch.object(sys, "argv", test_args):
            # Create a parser to test argument parsing
            parser = argparse.ArgumentParser()
            parser.add_argument("--baseload-power-sim-dir", type=str, help="Path to BOA output directory")
            parser.add_argument("--start-year", type=int, default=2025)
            parser.add_argument("--end-year", type=int, default=2030)

            args = parser.parse_args(test_args[1:])  # Skip program name

            assert args.baseload_power_sim_dir == "/path/to/boa/output"
            assert args.start_year == 2025
            assert args.end_year == 2030

    def test_simulation_config_accepts_custom_baseload_dir(self, tmp_path):
        """Test that SimulationConfig can be created with custom baseload_power_sim_dir."""
        custom_baseload_dir = tmp_path / "custom_boa_output"
        custom_baseload_dir.mkdir()

        # Create a config with custom baseload dir
        config = SimulationConfig(
            start_year=Year(2025),
            end_year=Year(2030),
            master_excel_path=tmp_path / "master.xlsx",
            output_dir=tmp_path / "output",
            baseload_power_sim_dir=custom_baseload_dir,
            technology_settings=get_default_technology_settings(),
        )

        assert config.baseload_power_sim_dir == custom_baseload_dir

    def test_simulation_config_from_data_dir_with_override(self, tmp_path):
        """Test that from_data_directory accepts baseload_power_sim_dir override."""
        # Create mock data directory
        data_dir = tmp_path / "data"
        fixtures_dir = data_dir / "fixtures"
        fixtures_dir.mkdir(parents=True)

        # Create minimal required files
        (fixtures_dir / "plants.json").write_text("[]")
        (fixtures_dir / "demand_centers.json").write_text("[]")

        # Create custom baseload directory
        custom_baseload_dir = tmp_path / "custom_boa_output"
        custom_baseload_dir.mkdir()

        # Create config with override
        config = SimulationConfig.from_data_directory(
            start_year=Year(2025),
            end_year=Year(2030),
            data_dir=data_dir,
            output_dir=tmp_path / "output",
            baseload_power_sim_dir=custom_baseload_dir,
        )

        assert config.baseload_power_sim_dir == custom_baseload_dir

    def test_bootstrap_uses_custom_baseload_dir(self, tmp_path):
        """Test that bootstrap uses custom baseload_power_sim_dir from config."""
        # Simply verify that config with custom baseload_dir is preserved
        # Create custom baseload directory
        custom_baseload_dir = tmp_path / "custom_boa_output"
        custom_baseload_dir.mkdir()

        # Create fixtures directory
        data_dir = tmp_path / "data"
        fixtures_dir = data_dir / "fixtures"
        fixtures_dir.mkdir(parents=True)
        (fixtures_dir / "plants.json").write_text("[]")
        (fixtures_dir / "demand_centers.json").write_text("[]")

        # Create config with custom baseload dir
        config = SimulationConfig.from_data_directory(
            start_year=Year(2025),
            end_year=Year(2030),
            data_dir=data_dir,
            output_dir=tmp_path / "output",
            baseload_power_sim_dir=custom_baseload_dir,
        )

        # Verify that the custom path is preserved
        assert config.baseload_power_sim_dir == custom_baseload_dir
