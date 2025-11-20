"""
Tests for the DataPathResolver class.
"""

import pytest
from pathlib import Path
import tempfile

from steelo.data.path_resolver import DataPathResolver


class TestDataPathResolver:
    """Test the DataPathResolver class."""

    @pytest.fixture
    def temp_data_dir(self):
        """Create a temporary data directory structure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            fixtures_dir = data_dir / "fixtures"
            fixtures_dir.mkdir(parents=True)

            # Create some subdirectories
            (data_dir / "outputs" / "GEO").mkdir(parents=True)
            (data_dir / "Infrastructure").mkdir(parents=True)

            yield data_dir

    def test_init(self, temp_data_dir):
        """Test initialization."""
        resolver = DataPathResolver(temp_data_dir)

        assert resolver.data_directory == temp_data_dir
        assert resolver.fixtures_dir == temp_data_dir / "fixtures"

    def test_init_nonexistent_dir(self):
        """Test initialization with non-existent directory."""
        with pytest.raises(ValueError, match="Data directory does not exist"):
            DataPathResolver(Path("/nonexistent/path"))

    def test_get_fixtures_path(self, temp_data_dir):
        """Test getting fixtures path."""
        resolver = DataPathResolver(temp_data_dir)

        path = resolver.get_fixtures_path("test.json")
        assert path == temp_data_dir / "fixtures" / "test.json"

    def test_get_data_path(self, temp_data_dir):
        """Test getting data path."""
        resolver = DataPathResolver(temp_data_dir)

        # Without subdirs
        path = resolver.get_data_path("test.nc")
        assert path == temp_data_dir / "test.nc"

        # With subdirs
        path = resolver.get_data_path("test.nc", ["outputs", "GEO"])
        assert path == temp_data_dir / "outputs" / "GEO" / "test.nc"

    def test_json_repository_paths(self, temp_data_dir):
        """Test JSON repository path properties."""
        resolver = DataPathResolver(temp_data_dir)

        assert resolver.plants_json_path == temp_data_dir / "fixtures" / "plants.json"
        assert resolver.demand_centers_json_path == temp_data_dir / "fixtures" / "demand_centers.json"
        assert resolver.suppliers_json_path == temp_data_dir / "fixtures" / "suppliers.json"
        assert resolver.tariffs_json_path == temp_data_dir / "fixtures" / "tariffs.json"

    def test_raw_data_paths(self, temp_data_dir):
        """Test raw data file path properties."""
        resolver = DataPathResolver(temp_data_dir)

        assert resolver.steel_plants_csv_path == temp_data_dir / "fixtures" / "steel_plants_input_data_2025-03.csv"
        assert resolver.technology_lcop_csv_path == temp_data_dir / "fixtures" / "technology_lcop.csv"
        primary_path = temp_data_dir / "fixtures" / "master_input_vlive_1.1.xlsx"
        fallback_path = temp_data_dir / "fixtures" / "master_input_vlive.xlsx"

        # Without any files, resolver reports preferred candidate path
        assert resolver.master_excel_path == primary_path

        # When the fallback file exists first, it should be used
        fallback_path.write_text("")
        assert resolver.master_excel_path == fallback_path

        # Once the primary file exists, it should take priority again
        primary_path.write_text("")
        assert resolver.master_excel_path == primary_path

    def test_geo_data_paths(self, temp_data_dir):
        """Test geo data path properties."""
        resolver = DataPathResolver(temp_data_dir)

        assert resolver.terrain_nc_path == temp_data_dir / "terrain_025_deg.nc"
        assert resolver.rail_distance_nc_path == temp_data_dir / "Infrastructure" / "rail_distance1.nc"
        assert resolver.geo_plots_dir == temp_data_dir / "output" / "plots" / "GEO"

    def test_validate_required_files_none_exist(self, temp_data_dir):
        """Test validation when no files exist."""
        resolver = DataPathResolver(temp_data_dir)

        # Should raise FileNotFoundError when required files don't exist
        with pytest.raises(FileNotFoundError, match="Required data file not found: plants.json"):
            resolver.validate_required_files(["plants.json"])

    def test_validate_required_files_some_exist(self, temp_data_dir):
        """Test validation when some files exist."""
        resolver = DataPathResolver(temp_data_dir)

        # Create some files
        (temp_data_dir / "fixtures" / "plants.json").write_text("{}")
        (temp_data_dir / "fixtures" / "demand_centers.json").write_text("{}")

        # Should pass for existing files
        resolver.validate_required_files(["plants.json", "demand_centers.json"])

        # Should fail for missing files
        with pytest.raises(FileNotFoundError, match="Required data file not found: suppliers.json"):
            resolver.validate_required_files(["plants.json", "suppliers.json"])

    def test_validate_required_files_custom_list(self, temp_data_dir):
        """Test validation with custom file list."""
        resolver = DataPathResolver(temp_data_dir)

        # Create a file
        (temp_data_dir / "fixtures" / "test.txt").write_text("test")

        # Should pass for existing file
        resolver.validate_required_files(["test.txt"])

        # Should fail for missing file
        with pytest.raises(FileNotFoundError, match="Required data file not found: missing.txt"):
            resolver.validate_required_files(["test.txt", "missing.txt"])

    def test_get_simulation_config_paths(self, temp_data_dir):
        """Test getting all paths for SimulationConfig."""
        resolver = DataPathResolver(temp_data_dir)

        paths = resolver.get_simulation_config_paths()

        # Check that all required keys are present
        required_keys = [
            "plants_json_path_repo",
            "demand_centers_json_path",
            "suppliers_json_path",
            "tariffs_json_path",
            "tech_switches_csv_path",
            "terrain_nc_path",
            "geo_plots_dir",
        ]

        for key in required_keys:
            assert key in paths
            assert isinstance(paths[key], Path)

    def test_custom_fixtures_subdir(self, temp_data_dir):
        """Test using a custom fixtures subdirectory."""
        custom_fixtures = temp_data_dir / "custom_fixtures"
        custom_fixtures.mkdir()

        resolver = DataPathResolver(temp_data_dir, fixtures_subdir="custom_fixtures")

        assert resolver.fixtures_dir == custom_fixtures
        assert resolver.plants_json_path == custom_fixtures / "plants.json"
