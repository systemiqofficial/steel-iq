"""
Tests for the recreation configuration system.
"""

import pytest

from steelo.data.recreation_config import (
    RecreationConfig,
    FileRecreationSpec,
    RecreationManager,
    FILE_RECREATION_SPECS,
)


class TestRecreationConfig:
    """Test the RecreationConfig class."""

    def test_default_config(self):
        """Test default configuration values."""
        config = RecreationConfig()

        assert config.files_to_recreate is None
        assert config.skip_existing is False
        assert config.force_recreation is False
        assert config.validate_after_creation is True
        assert config.continue_on_error is False

    def test_should_recreate_file_default(self, tmp_path):
        """Test file recreation decision with default settings."""
        config = RecreationConfig()

        # Non-existent file should be recreated
        assert config.should_recreate_file("test.json", tmp_path / "test.json") is True

        # Existing file should also be recreated by default
        existing_file = tmp_path / "existing.json"
        existing_file.write_text("{}")
        assert config.should_recreate_file("existing.json", existing_file) is True

    def test_should_recreate_file_skip_existing(self, tmp_path):
        """Test skip_existing functionality."""
        config = RecreationConfig(skip_existing=True)

        # Non-existent file should be recreated
        assert config.should_recreate_file("test.json", tmp_path / "test.json") is True

        # Existing file should be skipped
        existing_file = tmp_path / "existing.json"
        existing_file.write_text("{}")
        assert config.should_recreate_file("existing.json", existing_file) is False

    def test_should_recreate_file_force(self, tmp_path):
        """Test force_recreation functionality."""
        config = RecreationConfig(force_recreation=True, skip_existing=True)

        # Force should override skip_existing
        existing_file = tmp_path / "existing.json"
        existing_file.write_text("{}")
        assert config.should_recreate_file("existing.json", existing_file) is True

    def test_should_recreate_file_selective(self, tmp_path):
        """Test selective file recreation."""
        config = RecreationConfig(files_to_recreate=["plants.json", "demand_centers.json"])

        # Files in list should be recreated
        assert config.should_recreate_file("plants.json", tmp_path / "plants.json") is True
        assert config.should_recreate_file("demand_centers.json", tmp_path / "demand_centers.json") is True

        # Files not in list should be skipped
        assert config.should_recreate_file("suppliers.json", tmp_path / "suppliers.json") is False

    def test_progress_callback(self):
        """Test progress callback functionality."""
        progress_messages = []

        def callback(message, percent):
            progress_messages.append((message, percent))

        config = RecreationConfig(progress_callback=callback, verbose=False)

        config.report_progress("Starting", 0)
        config.report_progress("Processing", 50)
        config.report_progress("Complete", 100)

        assert len(progress_messages) == 3
        assert progress_messages[0] == ("Starting", 0)
        assert progress_messages[1] == ("Processing", 50)
        assert progress_messages[2] == ("Complete", 100)


class TestFileRecreationSpec:
    """Test the FileRecreationSpec class."""

    def test_valid_spec(self):
        """Test creating a valid specification."""
        spec = FileRecreationSpec(
            filename="test.json",
            recreate_function="test_func",
            source_type="core-archive",
            description="Test spec",
        )

        assert spec.filename == "test.json"
        assert spec.source_type == "core-archive"
        assert spec.dependencies == []

    def test_master_excel_spec_requires_sheet(self):
        """Test that master-excel specs require a sheet name."""
        with pytest.raises(ValueError, match="master_excel_sheet required"):
            FileRecreationSpec(
                filename="test.json",
                recreate_function="test_func",
                source_type="master-excel",
            )

    def test_invalid_source_type(self):
        """Test that invalid source types raise an error."""
        with pytest.raises(ValueError, match="Invalid source_type"):
            FileRecreationSpec(
                filename="test.json",
                recreate_function="test_func",
                source_type="invalid",
            )


class TestRecreationManager:
    """Test the RecreationManager class."""

    def test_get_recreation_order(self):
        """Test dependency ordering."""
        config = RecreationConfig()
        manager = RecreationManager(config)

        # Get all files in order
        order = manager.get_recreation_order()

        # plants.json should come before plant_groups.json (dependency)
        plants_idx = order.index("plants.json")
        groups_idx = order.index("plant_groups.json")
        assert plants_idx < groups_idx

    def test_get_recreation_order_selective(self):
        """Test dependency ordering with selective files."""
        config = RecreationConfig(files_to_recreate=["plant_groups.json"])
        manager = RecreationManager(config)

        order = manager.get_recreation_order()

        # Should include plant_groups.json
        assert "plant_groups.json" in order

    def test_validate_dependencies(self, tmp_path):
        """Test dependency validation."""
        config = RecreationConfig()
        manager = RecreationManager(config)

        spec = FILE_RECREATION_SPECS["demand_centers.json"]

        # All dependencies missing
        missing = manager.validate_dependencies(spec, tmp_path)
        assert len(missing) > 0
        assert "gravity_distances_dict.pkl" in missing

        # Create dependency file
        (tmp_path / "gravity_distances_dict.pkl").write_bytes(b"dummy")
        (tmp_path / "countries.csv").write_text("dummy")

        # No dependencies missing
        missing = manager.validate_dependencies(spec, tmp_path)
        assert len(missing) == 0

    def test_get_recreation_summary(self):
        """Test recreation summary generation."""
        config = RecreationConfig()
        manager = RecreationManager(config)

        summary = manager.get_recreation_summary()

        assert summary["total_files"] == len(FILE_RECREATION_SPECS)
        assert "master-excel" in summary["by_source"]
        assert "core-archive" in summary["by_source"]
        assert "derived" in summary["by_source"]

        # Check counts
        master_excel_count = sum(1 for spec in FILE_RECREATION_SPECS.values() if spec.source_type == "master-excel")
        assert summary["by_source"]["master-excel"] == master_excel_count

    def test_get_recreation_summary_selective(self):
        """Test summary with selective files."""
        files = ["plants.json", "demand_centers.json"]
        config = RecreationConfig(files_to_recreate=files)
        manager = RecreationManager(config)

        summary = manager.get_recreation_summary()

        assert summary["total_files"] == 2
        assert len(summary["files"]) == 2
        assert "plants.json" in summary["files"]
        assert "demand_centers.json" in summary["files"]


class TestFileRecreationSpecs:
    """Test the predefined FILE_RECREATION_SPECS."""

    def test_all_specs_valid(self):
        """Test that all predefined specs are valid."""
        for filename, spec in FILE_RECREATION_SPECS.items():
            assert spec.filename == filename
            assert spec.source_type in ["master-excel", "core-archive", "derived"]

            if spec.source_type == "master-excel":
                assert spec.master_excel_sheet is not None

    def test_dependency_files_exist_in_specs(self):
        """Test that JSON dependencies are defined in specs."""
        for filename, spec in FILE_RECREATION_SPECS.items():
            for dep in spec.dependencies:
                if dep.endswith(".json"):
                    assert dep in FILE_RECREATION_SPECS, f"{dep} dependency of {filename} not in specs"
