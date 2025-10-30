import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
from django.conf import settings
from django.test import TestCase

from steeloweb.models import ModelRun, DataPreparation, DataPackage


@pytest.mark.django_db
class TestModelRunOutputIsolation(TestCase):
    """Simple tests for ModelRun output isolation functionality."""

    def setUp(self):
        """Set up test data."""
        super().setUp()

        # Create mock data packages
        self.core_package = DataPackage.objects.create(
            name=DataPackage.PackageType.CORE_DATA,
            version="test",
            source_type=DataPackage.SourceType.LOCAL,
            is_active=True,
        )

        self.geo_package = DataPackage.objects.create(
            name=DataPackage.PackageType.GEO_DATA,
            version="test",
            source_type=DataPackage.SourceType.LOCAL,
            is_active=True,
        )

        # Create a ready data preparation
        self.data_prep = DataPreparation.objects.create(
            name="Test Preparation",
            status=DataPreparation.Status.READY,
            core_data_package=self.core_package,
            geo_data_package=self.geo_package,
            data_directory="/mock/data/dir",
        )

    def test_ensure_output_directories_creates_isolated_path(self):
        """Test that ensure_output_directories creates an isolated directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(settings, "MEDIA_ROOT", temp_dir):
                # Create a model run
                model_run = ModelRun.objects.create(
                    name="Test Run", config={"start_year": 2025, "end_year": 2026}, data_preparation=self.data_prep
                )

                # Ensure output directories
                model_run.ensure_output_directories()

                # Verify isolated directory was created
                assert model_run.output_directory
                assert f"run_{model_run.pk}" in model_run.output_directory
                assert temp_dir in model_run.output_directory

                # Check that the directory structure was created
                output_path = Path(model_run.output_directory)
                assert output_path.exists()
                assert (output_path / "TM").exists()
                assert (output_path / "plots" / "GEO").exists()
                assert (output_path / "plots" / "PAM").exists()

    def test_get_output_path_returns_correct_path(self):
        """Test that get_output_path returns the correct Path object."""
        model_run = ModelRun.objects.create(name="Test Run", config={}, output_directory="/test/output/dir")

        output_path = model_run.get_output_path()
        assert isinstance(output_path, Path)
        assert str(output_path) == "/test/output/dir"

    def test_cleanup_output_directory_removes_files(self):
        """Test that cleanup removes the output directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create a model run with output directory
            output_dir = Path(temp_dir) / "test_output"
            output_dir.mkdir(parents=True)

            model_run = ModelRun.objects.create(name="Test Run", config={}, output_directory=str(output_dir))

            # Create some test files
            (output_dir / "test.txt").write_text("test content")
            assert output_dir.exists()

            # Cleanup
            model_run.cleanup_output_directory()

            # Verify directory was removed
            assert not output_dir.exists()

    @patch("steelo.validation.validate_technology_settings")  # Skip validation
    @patch("steelo.bootstrap.bootstrap_simulation")
    def test_run_creates_simulation_config_with_output_paths(self, mock_bootstrap_simulation, mock_validate):
        """Test that run() creates SimulationConfig with isolated paths."""
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(settings, "MEDIA_ROOT", temp_dir):
                # Create a mock master Excel file for the data preparation
                from django.core.files.base import ContentFile

                # Update the data preparation to have a master Excel file (FileField)
                self.data_prep.master_excel_file.save("test_master.xlsx", ContentFile(b"fake excel content"), save=True)

                # Create model run with technology_settings
                model_run = ModelRun.objects.create(
                    name="Test Run",
                    config={
                        "start_year": 2025,
                        "end_year": 2026,
                        "technology_settings": {
                            "BF": {"allowed": True, "from_year": 2025, "to_year": None},
                            "BOF": {"allowed": True, "from_year": 2025, "to_year": None},
                            "EAF": {"allowed": True, "from_year": 2025, "to_year": None},
                            "DRING": {"allowed": True, "from_year": 2025, "to_year": None},
                        },
                    },
                    data_preparation=self.data_prep,
                )
                model_run.ensure_output_directories()

                # Mock SimulationRunner
                mock_runner = MagicMock()
                mock_runner.run.return_value = {"status": "success"}
                mock_runner.progress_callback = None
                mock_runner.modelrun_id = None
                mock_bootstrap_simulation.return_value = mock_runner

                # Run simulation
                model_run.run()

                # Verify bootstrap_simulation was called with a config
                mock_bootstrap_simulation.assert_called_once()

                # Verify the isolated directory was created and used
                assert model_run.output_directory
                output_path = Path(model_run.output_directory)
                assert output_path.exists()
                assert f"run_{model_run.pk}" in str(output_path)
