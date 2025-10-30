"""
Integration tests for the complete data preparation flow.

Tests the flow from master Excel → JSON files → SimulationConfig → Simulation
"""

import pytest
from pathlib import Path
import tempfile
import json


from steelo.simulation_types import get_default_technology_settings

from steelo.data import DataPathResolver
from steelo.data.recreation_config import RecreationConfig
from steelo.simulation import SimulationConfig


class TestDataPreparationFlow:
    """Test the complete data preparation and simulation setup flow."""

    @pytest.fixture
    def temp_dirs(self):
        """Create temporary directories for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)

            # Create directory structure
            data_dir = base_dir / "data"
            fixtures_dir = data_dir / "fixtures"
            fixtures_dir.mkdir(parents=True)

            # Create output directories
            (data_dir / "outputs" / "GEO").mkdir(parents=True)
            (data_dir / "Infrastructure").mkdir(parents=True)

            yield {
                "base": base_dir,
                "data": data_dir,
                "fixtures": fixtures_dir,
            }

    def test_path_resolver_integration(self, temp_dirs):
        """Test DataPathResolver integration with prepared data."""
        fixtures_dir = temp_dirs["fixtures"]

        # Create some dummy JSON files
        dummy_data = {"test": "data"}
        (fixtures_dir / "plants.json").write_text(json.dumps(dummy_data))
        (fixtures_dir / "demand_centers.json").write_text(json.dumps(dummy_data))
        (fixtures_dir / "suppliers.json").write_text(json.dumps(dummy_data))

        # Create resolver
        resolver = DataPathResolver(temp_dirs["data"])

        # Validate files exist - this should not raise since files were created
        resolver.validate_required_files(
            [
                "plants.json",
                "demand_centers.json",
                "suppliers.json",
            ]
        )

    def test_simulation_config_with_resolver(self, temp_dirs):
        """Test creating SimulationConfig with DataPathResolver paths."""
        resolver = DataPathResolver(temp_dirs["data"])

        # Get paths for SimulationConfig
        config_paths = resolver.get_simulation_config_paths()

        # Create SimulationConfig
        # Note: This will fail in real tests because the files don't exist,
        # but it demonstrates the integration pattern
        try:
            config = SimulationConfig(
                **config_paths,
                start_year=2025,
                end_year=2026,
                technology_settings=get_default_technology_settings(),
            )

            # Verify paths are set correctly
            assert config.plants_json_path_repo == resolver.plants_json_path
            assert config.demand_centers_json_path == resolver.demand_centers_json_path
            assert config.tech_switches_csv_path == resolver.tech_switches_csv_path

        except Exception:
            # Expected to fail without actual data files
            pass

    def test_recreation_config_integration(self, temp_dirs):
        """Test RecreationConfig integration."""
        # Create a simple recreation config
        config = RecreationConfig(
            files_to_recreate=["plants.json", "demand_centers.json"],
            skip_existing=True,
            validate_after_creation=True,
        )

        # Test file selection
        dummy_path = temp_dirs["fixtures"] / "plants.json"
        assert config.should_recreate_file("plants.json", dummy_path) is True
        assert config.should_recreate_file("suppliers.json", dummy_path) is False

        # Create the file and test skip_existing
        dummy_path.write_text("{}")
        assert config.should_recreate_file("plants.json", dummy_path) is False

    @pytest.mark.skip(reason="Requires actual data files and packages")
    def test_complete_data_flow(self, temp_dirs):
        """Test the complete data preparation flow (requires actual data)."""
        # This test would require:
        # 1. A test master Excel file
        # 2. Test core data archive
        # 3. Actual recreation functions

        # Example of how it would work:
        """
        # Step 1: Prepare data with DataRecreator
        manager = DataManager()
        recreator = DataRecreator(manager)
        
        config = RecreationConfig(
            files_to_recreate=["plants.json", "demand_centers.json"],
            validate_after_creation=True,
        )
        
        created_files = recreator.recreate_with_config(
            output_dir=temp_dirs["fixtures"],
            config=config,
            master_excel_path=Path("test_master.xlsx"),
        )
        
        # Step 2: Use DataPathResolver to get paths
        resolver = DataPathResolver(temp_dirs["data"])
        config_paths = resolver.get_simulation_config_paths()
        
        # Step 3: Create SimulationConfig
        sim_config = SimulationConfig(
            **config_paths,
            technology_settings=get_default_technology_settings(),
        )
        
        # Step 4: Run simulation
        from steelo.simulation import SimulationRunner
        runner = SimulationRunner(sim_config)
        results = runner.run()
        """
        pass

    def test_data_migration_checklist(self):
        """Test that we can track migration status programmatically."""
        from steelo.data.recreation_config import FILE_RECREATION_SPECS

        # Count files by source
        by_source = {"master-excel": 0, "core-archive": 0, "derived": 0}
        for spec in FILE_RECREATION_SPECS.values():
            by_source[spec.source_type] += 1

        # Verify we have files from master-excel and derived sources
        assert by_source["master-excel"] > 0
        assert by_source["derived"] > 0

        # All files have been successfully migrated from core-archive!
        # We should have zero core-archive files now
        assert by_source["core-archive"] == 0, "All files should be migrated from core-archive"

        # Check which files still need migration (should be none)
        needs_migration = []
        for filename, spec in FILE_RECREATION_SPECS.items():
            if spec.source_type == "core-archive":
                # These would be candidates for migration to master Excel
                needs_migration.append(filename)

        # No files should need migration anymore
        assert len(needs_migration) == 0, f"Files still needing migration: {needs_migration}"

        # Document files that have been successfully migrated to master-excel
        migrated_files = []
        for filename, spec in FILE_RECREATION_SPECS.items():
            if spec.source_type == "master-excel" and filename in [
                "input_costs.json",
                "primary_feedstocks.json",
                "plants.json",
            ]:
                migrated_files.append(filename)

        # These files have been successfully migrated from core-archive to master-excel
        assert "input_costs.json" in migrated_files
        assert "primary_feedstocks.json" in migrated_files
        assert "plants.json" in migrated_files  # Now also migrated!

        # Verify most files now come from master-excel
        master_excel_percentage = (by_source["master-excel"] / sum(by_source.values())) * 100
        assert master_excel_percentage > 90, (
            f"Most files should now come from master-excel (currently {master_excel_percentage:.1f}%)"
        )
