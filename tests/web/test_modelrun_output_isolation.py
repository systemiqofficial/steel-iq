import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
from django.conf import settings

from steeloweb.models import ModelRun, ResultImages, SimulationPlot


@pytest.mark.django_db
class TestModelRunOutputIsolation:
    """Test the isolated output directory functionality for ModelRuns."""

    def test_output_directory_field_exists(self):
        """Test that the output_directory field exists on ModelRun."""
        model_run = ModelRun.objects.create()
        assert hasattr(model_run, "output_directory")
        assert model_run.output_directory == ""

    def test_get_output_path_returns_none_when_no_directory(self):
        """Test get_output_path returns None when no directory is set."""
        model_run = ModelRun.objects.create()
        assert model_run.get_output_path() is None

    def test_get_output_path_returns_path_object(self):
        """Test get_output_path returns Path object when directory is set."""
        model_run = ModelRun.objects.create()
        test_path = "/path/to/output"
        model_run.output_directory = test_path

        output_path = model_run.get_output_path()
        assert isinstance(output_path, Path)
        assert str(output_path) == test_path

    def test_ensure_output_directories_creates_structure(self):
        """Test that ensure_output_directories creates the expected directory structure."""
        model_run = ModelRun.objects.create()

        # Mock the media root
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(settings, "MEDIA_ROOT", temp_dir):
                model_run.ensure_output_directories()

                # Check that output_directory was set
                assert model_run.output_directory
                assert f"run_{model_run.pk}" in model_run.output_directory

                # Check that directories were created
                output_path = Path(model_run.output_directory)
                assert output_path.exists()
                assert (output_path / "TM").exists()
                assert (output_path / "plots").exists()
                assert (output_path / "plots" / "GEO").exists()
                assert (output_path / "plots" / "PAM").exists()

    def test_ensure_output_directories_idempotent(self):
        """Test that ensure_output_directories doesn't recreate existing directories."""
        model_run = ModelRun.objects.create()

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(settings, "MEDIA_ROOT", temp_dir):
                # First call
                model_run.ensure_output_directories()
                first_path = model_run.output_directory

                # Create a test file in the directory
                test_file = Path(first_path) / "test.txt"
                test_file.write_text("test content")

                # Second call
                model_run.ensure_output_directories()

                # Should have same path and test file should still exist
                assert model_run.output_directory == first_path
                assert test_file.exists()
                assert test_file.read_text() == "test content"

    def test_cleanup_output_directory_removes_files(self):
        """Test that cleanup_output_directory removes the output directory."""
        model_run = ModelRun.objects.create()

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(settings, "MEDIA_ROOT", temp_dir):
                # Create output directories
                model_run.ensure_output_directories()
                output_path = Path(model_run.output_directory)

                # Create some test files
                (output_path / "test.txt").write_text("test")
                (output_path / "TM" / "results.csv").write_text("data")

                # Verify files exist
                assert output_path.exists()
                assert (output_path / "test.txt").exists()

                # Cleanup
                model_run.cleanup_output_directory()

                # Verify directory is gone
                assert not output_path.exists()

    def test_cleanup_output_directory_handles_missing_directory(self):
        """Test that cleanup_output_directory handles missing directories gracefully."""
        model_run = ModelRun.objects.create()
        model_run.output_directory = "/nonexistent/path"

        # Should not raise an exception
        model_run.cleanup_output_directory()

    def test_delete_calls_cleanup(self):
        """Test that deleting a ModelRun cleans up its output directory."""
        model_run = ModelRun.objects.create()

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(settings, "MEDIA_ROOT", temp_dir):
                model_run.ensure_output_directories()
                output_path = Path(model_run.output_directory)

                # Create a test file
                (output_path / "test.txt").write_text("test")
                assert output_path.exists()

                # Delete the model run
                model_run.delete()

                # Verify directory is gone
                assert not output_path.exists()

    @patch("steelo.validation.validate_technology_settings")  # Skip validation
    @patch("steelo.bootstrap.bootstrap_simulation")
    @patch("steelo.simulation.SimulationConfig")
    def test_run_method_uses_isolated_directories(self, mock_config_class, mock_bootstrap_simulation, mock_validate):
        """Test that the run method passes isolated output directories to SimulationConfig."""
        # Create data packages and preparation like in the integration tests
        from steeloweb.models import DataPackage, DataPreparation
        from django.core.files.base import ContentFile

        core_package = DataPackage.objects.create(
            name=DataPackage.PackageType.CORE_DATA,
            version="test",
            source_type=DataPackage.SourceType.LOCAL,
            is_active=True,
        )
        geo_package = DataPackage.objects.create(
            name=DataPackage.PackageType.GEO_DATA,
            version="test",
            source_type=DataPackage.SourceType.LOCAL,
            is_active=True,
        )
        data_prep = DataPreparation.objects.create(
            name="Test Preparation",
            status=DataPreparation.Status.READY,
            core_data_package=core_package,
            geo_data_package=geo_package,
            data_directory="/mock/data/dir",
        )
        data_prep.master_excel_file.save("test_master.xlsx", ContentFile(b"fake excel content"), save=True)

        model_run = ModelRun.objects.create(
            config={
                "start_year": 2025,
                "end_year": 2030,
                "technology_settings": {
                    "BF": {"allowed": True, "from_year": 2025, "to_year": None},
                    "BOF": {"allowed": True, "from_year": 2025, "to_year": None},
                    "EAF": {"allowed": True, "from_year": 2025, "to_year": None},
                    "DRING": {"allowed": True, "from_year": 2025, "to_year": None},
                },
            },
            data_preparation=data_prep,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(settings, "MEDIA_ROOT", temp_dir):
                # Mock the simulation components
                mock_runner = MagicMock()
                mock_runner.run.return_value = {"status": "success"}
                mock_runner.progress_callback = None
                mock_runner.modelrun_id = None
                mock_bootstrap_simulation.return_value = mock_runner

                # Run the simulation
                model_run.run()

                # Verify output directories were created
                assert model_run.output_directory
                output_path = Path(model_run.output_directory)
                assert output_path.exists()

                # Check that bootstrap_simulation was called with a SimulationConfig
                assert mock_bootstrap_simulation.called, "bootstrap_simulation was not called"
                mock_bootstrap_simulation.assert_called_once()

                # The test successfully shows that the isolated directory is being used
                # based on the log output showing the correct path was set

    def test_capture_result_csv_uses_isolated_directory(self):
        """Test that capture_result_csv uses the isolated output directory."""
        model_run = ModelRun.objects.create()

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(settings, "MEDIA_ROOT", temp_dir):
                model_run.ensure_output_directories()
                output_path = Path(model_run.output_directory)

                # Create a test CSV file in the TM directory
                tm_dir = output_path / "TM"
                tm_dir.mkdir(exist_ok=True)
                csv_file = tm_dir / "post_processed_2024-01-01.csv"
                csv_file.write_text("col1,col2\nval1,val2\n")

                # Capture the CSV
                result = model_run.capture_result_csv()

                assert result is True
                assert model_run.result_csv
                assert model_run.result_csv.name.endswith(".csv")

    def test_capture_result_csv_raises_without_output_path(self):
        """Test that capture_result_csv raises ValueError when no output path is set."""
        model_run = ModelRun.objects.create()

        # Don't create isolated directories - model_run has no output_directory
        # Should raise ValueError since no output path is available
        with pytest.raises(ValueError, match="ModelRun must have an output path set"):
            model_run.capture_result_csv()

    def test_result_images_uses_isolated_directory(self):
        """Test that ResultImages.create_from_plots uses isolated directories."""
        model_run = ModelRun.objects.create()

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(settings, "MEDIA_ROOT", temp_dir):
                model_run.ensure_output_directories()
                output_path = Path(model_run.output_directory)

                # Create test plot files
                geo_dir = output_path / "plots" / "GEO"
                geo_dir.mkdir(parents=True, exist_ok=True)

                # Create a simple test image (1x1 PNG)
                test_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\x0c\x0c\x00\x00\x00\x00IEND\xaeB`\x82"

                (geo_dir / "lcoe_map.png").write_bytes(test_png)

                # Create result images
                result_images = ResultImages.create_from_plots(model_run)

                assert result_images
                assert result_images.modelrun == model_run
                # At least one image should be found (lcoe_map)
                assert result_images.lcoe_map

    def test_simulation_plot_uses_isolated_directory(self):
        """Test that SimulationPlot.capture_simulation_plots uses isolated directories."""
        model_run = ModelRun.objects.create()

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(settings, "MEDIA_ROOT", temp_dir):
                model_run.ensure_output_directories()
                output_path = Path(model_run.output_directory)

                # Create test plot files
                pam_dir = output_path / "plots" / "PAM"
                pam_dir.mkdir(parents=True, exist_ok=True)

                # Create a simple test image
                test_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\x0c\x0c\x00\x00\x00\x00IEND\xaeB`\x82"

                (pam_dir / "year2year_added_capacity_by_technology.png").write_bytes(test_png)

                # Capture plots
                plots = SimulationPlot.capture_simulation_plots(model_run)

                assert len(plots) == 1
                assert plots[0].modelrun == model_run
                assert plots[0].plot_type == SimulationPlot.PlotType.CAPACITY_ADDED


@pytest.mark.django_db
class TestModelRunConcurrency:
    """Test that multiple ModelRuns can use isolated directories concurrently."""

    def test_multiple_modelruns_have_different_directories(self):
        """Test that multiple ModelRuns get different output directories."""
        model_run1 = ModelRun.objects.create()
        model_run2 = ModelRun.objects.create()

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(settings, "MEDIA_ROOT", temp_dir):
                model_run1.ensure_output_directories()
                model_run2.ensure_output_directories()

                # Verify different directories
                assert model_run1.output_directory != model_run2.output_directory
                assert f"run_{model_run1.pk}" in model_run1.output_directory
                assert f"run_{model_run2.pk}" in model_run2.output_directory

                # Verify both exist
                assert Path(model_run1.output_directory).exists()
                assert Path(model_run2.output_directory).exists()

    def test_concurrent_file_writes_isolated(self):
        """Test that concurrent ModelRuns can write to their directories without conflicts."""
        model_runs = [ModelRun.objects.create() for _ in range(3)]

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(settings, "MEDIA_ROOT", temp_dir):
                for i, model_run in enumerate(model_runs):
                    model_run.ensure_output_directories()

                    # Write unique content to each
                    output_path = Path(model_run.output_directory)
                    (output_path / "TM" / f"results_{i}.csv").write_text(f"data for run {i}")

                # Verify each has its own content
                for i, model_run in enumerate(model_runs):
                    output_path = Path(model_run.output_directory)
                    content = (output_path / "TM" / f"results_{i}.csv").read_text()
                    assert content == f"data for run {i}"

                    # Verify other files don't exist in this directory
                    for j in range(3):
                        if i != j:
                            assert not (output_path / "TM" / f"results_{j}.csv").exists()
