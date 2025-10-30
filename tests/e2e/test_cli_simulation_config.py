"""
Tests for CLI creating and passing SimulationConfig.
"""

import sys
from unittest.mock import patch, MagicMock, Mock
from pathlib import Path
import tempfile

from steelo.simulation import SimulationConfig
from steelo.entrypoints.cli import run_full_simulation
from steelo.domain import Year


@patch("steelo.entrypoints.cli.setup_legacy_symlinks")  # Mock to prevent symlink creation
@patch("steelo.entrypoints.cli.update_output_symlink")  # Mock to prevent symlink creation
@patch("steelo.entrypoints.cli.update_data_symlink")  # Mock to prevent symlink creation
@patch("steelo.entrypoints.cli.bootstrap_simulation")
@patch("steelo.data.DataPreparationService")
@patch("steelo.data.DataManager")  # Mock to prevent S3 download
def test_cli_creates_and_passes_simulation_config(
    mock_data_manager_class,
    mock_data_prep_service_class,
    mock_create_runner,
    mock_update_data_symlink,
    mock_update_output_symlink,
    mock_setup_legacy_symlinks,
):
    """
    Tests that the CLI entrypoint correctly creates a SimulationConfig
    object and passes it to the simulation runner factory.
    """
    # Create a real temporary directory for the test
    with tempfile.TemporaryDirectory() as tmpdir:
        # Setup the CLI's expected temp directory structure
        cli_temp_base = Path(tmpdir)

        # Setup the data directory where CLI will create it
        data_dir = cli_temp_base / "data"
        fixtures_dir = data_dir / "fixtures"

        # Mock DataManager to avoid S3 download
        mock_data_manager = MagicMock()
        mock_master_input_path = cli_temp_base / "master-input"
        mock_master_input_path.mkdir(parents=True, exist_ok=True)
        (mock_master_input_path / "master_input.xlsx").touch()
        mock_data_manager.get_package_path.return_value = mock_master_input_path
        mock_data_manager.download_package.return_value = None
        mock_data_manager_class.return_value = mock_data_manager

        # Mock data preparation service
        mock_prep_service = MagicMock()
        mock_prep_result = MagicMock()
        mock_prep_result.data_dir = data_dir
        mock_prep_result.output_directory = fixtures_dir

        # Add specific attributes that will be serialized
        mock_prep_result.master_excel_path = None
        mock_prep_result.master_excel_source = "S3"
        mock_prep_result.data_package_version = "1.0.0"
        mock_prep_result.cache_used = False
        mock_prep_result.preparation_time = 1.23
        mock_prep_result.total_duration = 2.5
        mock_prep_result.files = ["file1.json", "file2.json"]

        # Make prepare_data create the required files
        def prepare_data_side_effect(*args, **kwargs):
            # Get the actual output_dir from kwargs
            actual_output_dir = kwargs.get("output_dir", fixtures_dir)
            # Create the directories and files when prepare_data is called
            actual_output_dir.parent.mkdir(parents=True, exist_ok=True)
            actual_output_dir.mkdir(parents=True, exist_ok=True)

            # Create the fixtures subdirectory where files are expected
            fixtures_subdir = actual_output_dir / "fixtures"
            fixtures_subdir.mkdir(parents=True, exist_ok=True)

            # Create required JSON files in fixtures directory
            (fixtures_subdir / "plants.json").write_text("[]")
            (fixtures_subdir / "demand_centers.json").write_text("[]")
            (fixtures_subdir / "suppliers.json").write_text("[]")
            (fixtures_subdir / "country_mappings.json").write_text("[]")
            (fixtures_subdir / "railway_costs.json").write_text("[]")

            # Update the mock result to use the actual directory
            mock_prep_result.output_directory = fixtures_subdir
            mock_prep_result.data_dir = actual_output_dir
            return mock_prep_result

        mock_prep_service.prepare_data.side_effect = prepare_data_side_effect
        mock_data_prep_service_class.return_value = mock_prep_service

        # Mock the runner to avoid actual simulation
        mock_runner = MagicMock()
        mock_runner.run = Mock()
        mock_create_runner.return_value = mock_runner

        # Simulate running the CLI with specific arguments
        with patch.object(sys, "argv", ["run_simulation", "--start-year", "2026", "--end-year", "2035"]):
            # Capture the result to avoid system exit
            with patch("sys.exit"):
                run_full_simulation()

        # Assert that our factory was called
        mock_create_runner.assert_called_once()

        # Get the arguments it was called with
        args, kwargs = mock_create_runner.call_args

        # Check that the first argument is a SimulationConfig instance
        assert len(args) > 0
        assert isinstance(args[0], SimulationConfig)

        # Check that the config reflects the CLI arguments
        config = args[0]
        assert config.start_year == Year(2026)
        assert config.end_year == Year(2035)
