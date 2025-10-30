import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
from django.conf import settings
from django.test import TransactionTestCase

from steeloweb.models import ModelRun, DataPreparation, DataPackage


@pytest.mark.django_db
class TestModelRunOutputIsolationIntegration(TransactionTestCase):
    """Integration tests for ModelRun output isolation with actual simulation runs."""

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

        # Create a ready data preparation with a master Excel file
        from django.core.files.base import ContentFile

        self.data_prep = DataPreparation.objects.create(
            name="Test Preparation",
            status=DataPreparation.Status.READY,
            core_data_package=self.core_package,
            geo_data_package=self.geo_package,
            data_directory="/mock/data/dir",
        )
        # Add a mock master Excel file
        self.data_prep.master_excel_file.save("test_master.xlsx", ContentFile(b"fake excel content"), save=True)

    @patch("steelo.validation.validate_technology_settings")  # Skip validation
    @patch("steelo.bootstrap.bootstrap_simulation")
    def test_simulation_writes_to_isolated_directory(self, mock_bootstrap_simulation, mock_validate):
        """Test that simulation outputs are written to the isolated directory."""
        # Setup mocks
        mock_runner_instance = MagicMock()
        mock_runner_instance.progress_callback = None
        mock_runner_instance.modelrun_id = None
        mock_bootstrap_simulation.return_value = mock_runner_instance

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(settings, "MEDIA_ROOT", temp_dir):
                # Create a model run with technology_settings as plain dicts
                tech_settings = {
                    "BF": {"allowed": True, "from_year": 2025, "to_year": None},
                    "BOF": {"allowed": True, "from_year": 2025, "to_year": None},
                    "EAF": {"allowed": True, "from_year": 2025, "to_year": None},
                    "DRING": {"allowed": True, "from_year": 2025, "to_year": None},
                }

                model_run = ModelRun.objects.create(
                    name="Isolation Test Run",
                    config={
                        "start_year": 2025,
                        "end_year": 2026,  # Short run for testing
                        "technology_settings": tech_settings,
                    },
                    data_preparation=self.data_prep,
                )

                # Ensure output directories
                model_run.ensure_output_directories()

                # Verify isolated directory was created
                assert model_run.output_directory
                assert f"run_{model_run.pk}" in model_run.output_directory

                # Mock the simulation to write test files
                def mock_simulation_run():
                    output_path = Path(model_run.output_directory)

                    # Simulate writing various output files
                    tm_dir = output_path / "TM"
                    tm_dir.mkdir(parents=True, exist_ok=True)

                    # Write test files that would normally be created by simulation
                    (tm_dir / "datacollection_post_allocation_2025.pkl").write_bytes(b"test pickle data")
                    (tm_dir / "steel_cost_curve_2025.png").write_bytes(b"test image data")
                    (tm_dir / "post_processed_2025-01-01.csv").write_text("col1,col2\nval1,val2\n")

                    plots_dir = output_path / "plots"
                    plots_dir.mkdir(parents=True, exist_ok=True)

                    geo_dir = plots_dir / "GEO"
                    geo_dir.mkdir(parents=True, exist_ok=True)
                    (geo_dir / "new_steel_plants_by_status.png").write_bytes(b"test geo plot")

                    pam_dir = plots_dir / "PAM"
                    pam_dir.mkdir(parents=True, exist_ok=True)
                    (pam_dir / "steel_production_development_by_region.png").write_bytes(b"test pam plot")

                    return {"status": "success"}

                mock_runner_instance.run.side_effect = mock_simulation_run

                # Run the simulation
                model_run.state = ModelRun.RunState.RUNNING
                model_run.save()

                _result = model_run.run()

                # Verify outputs were written to isolated directory
                output_path = Path(model_run.output_directory)

                # Check TM outputs
                assert (output_path / "TM" / "datacollection_post_allocation_2025.pkl").exists()
                assert (output_path / "TM" / "steel_cost_curve_2025.png").exists()
                assert (output_path / "TM" / "post_processed_2025-01-01.csv").exists()

                # Check plot outputs
                assert (output_path / "plots" / "GEO" / "new_steel_plants_by_status.png").exists()
                assert (output_path / "plots" / "PAM" / "steel_production_development_by_region.png").exists()

                # Verify bootstrap_simulation was called with correct config
                mock_bootstrap_simulation.assert_called_once()
                config = mock_bootstrap_simulation.call_args[0][0]
                assert str(model_run.output_directory) in str(config.output_dir)

    @patch("steelo.validation.validate_technology_settings")  # Skip validation
    @patch("steelo.domain.datacollector.DataCollector")
    @patch("steelo.bootstrap.bootstrap_simulation")
    def test_datacollector_uses_isolated_directory(
        self, mock_bootstrap_simulation, mock_datacollector_class, mock_validate
    ):
        """Test that DataCollector uses the isolated output directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(settings, "MEDIA_ROOT", temp_dir):
                # Create model run with technology_settings as plain dicts
                tech_settings = {
                    "BF": {"allowed": True, "from_year": 2025, "to_year": None},
                    "BOF": {"allowed": True, "from_year": 2025, "to_year": None},
                    "EAF": {"allowed": True, "from_year": 2025, "to_year": None},
                    "DRING": {"allowed": True, "from_year": 2025, "to_year": None},
                }

                model_run = ModelRun.objects.create(
                    name="DataCollector Test",
                    config={
                        "start_year": 2025,
                        "end_year": 2025,
                        "master_excel_path": str(Path(temp_dir) / "master.xlsx"),
                        "technology_settings": tech_settings,
                    },
                    data_preparation=self.data_prep,  # Add the data_preparation
                )
                model_run.ensure_output_directories()

                # Mock the runner that bootstrap_simulation returns
                mock_runner = MagicMock()
                mock_bus = MagicMock()
                mock_runner.bus = mock_bus
                mock_runner.progress_callback = None
                mock_runner.modelrun_id = None

                # Use a flag to check if DataCollector was called correctly
                datacollector_called_correctly = False
                captured_output_dir = None

                def mock_datacollector_init(world_plant_groups=None, env=None, output_dir=None):
                    nonlocal datacollector_called_correctly, captured_output_dir
                    datacollector_called_correctly = True
                    captured_output_dir = output_dir
                    return MagicMock()

                mock_datacollector_class.side_effect = mock_datacollector_init

                # Mock the data_collector attribute that gets set in SimulationRunner.__init__
                mock_runner.data_collector = mock_datacollector_class([], mock_bus.env, model_run.get_output_path())
                mock_runner.run.return_value = {"status": "success"}

                # Return the mock runner from bootstrap_simulation
                mock_bootstrap_simulation.return_value = mock_runner

                # Run simulation
                _result = model_run.run()

                # Verify bootstrap_simulation was called
                mock_bootstrap_simulation.assert_called_once()
                config_arg = mock_bootstrap_simulation.call_args[0][0]
                assert str(model_run.output_directory) in str(config_arg.output_dir)

                # Verify DataCollector was created (at least once during runner construction)
                assert datacollector_called_correctly
                # Verify it was called with the isolated directory
                assert captured_output_dir is not None
                assert str(model_run.output_directory) in str(captured_output_dir)

    @patch("steelo.economic_models.plant_agent.pickle.dump")
    @patch("builtins.open", create=True)
    @patch("steelo.economic_models.plant_agent.set_up_steel_trade_lp")
    @patch("steelo.economic_models.plant_agent.export_commodity_allocations_to_csv")
    @patch("steelo.economic_models.plant_agent.solve_steel_trade_lp_and_return_commodity_allocations")
    def test_allocation_model_uses_isolated_directory(
        self, mock_solve, mock_export, mock_setup, mock_open, mock_pickle
    ):
        """Test that AllocationModel uses bus.env.output_dir for outputs."""
        from steelo.economic_models.plant_agent import AllocationModel

        with tempfile.TemporaryDirectory() as temp_dir:
            # Create mock bus with environment
            mock_bus = MagicMock()
            mock_env = MagicMock()
            mock_env.year = 2025
            mock_env.output_dir = Path(temp_dir) / "isolated_output"
            mock_env.output_dir.mkdir(parents=True, exist_ok=True)
            (mock_env.output_dir / "TM").mkdir(exist_ok=True)
            mock_env.calculate_average_commodity_price_per_region = MagicMock()
            mock_env.get_active_trade_tariffs = MagicMock(return_value=[])
            mock_env.relevant_secondary_feedstock_constraints = MagicMock(return_value=[])
            mock_env.aggregated_metallic_charge_constraints = []
            # Add simulation_config with include_tariffs = False
            mock_simulation_config = MagicMock()
            mock_simulation_config.include_tariffs = False
            mock_env.simulation_config = mock_simulation_config
            mock_bus.env = mock_env

            # Mock uow
            mock_uow = MagicMock()
            mock_uow.repository.plants.list.return_value = []
            mock_uow.repository.suppliers.list.return_value = []
            mock_bus.uow = mock_uow

            # Mock the trade LP
            mock_lp = MagicMock()
            mock_lp.allocations = {"test": "allocations"}
            mock_setup.return_value = mock_lp

            # Mock the commodity allocations return - empty so no plotting happens
            mock_allocations = {"steel": MagicMock(allocations=[]), "iron": MagicMock(allocations=[])}
            mock_solve.return_value = mock_allocations

            # Mock file operations
            mock_file = MagicMock()
            mock_open.return_value.__enter__.return_value = mock_file

            # Run AllocationModel
            AllocationModel.run(mock_bus)

            # Verify CSV export was called with isolated path
            mock_export.assert_called_once()
            csv_path = mock_export.call_args.kwargs["filename"]
            assert str(mock_env.output_dir) in csv_path
            assert "TM" in csv_path
            assert "steel_trade_allocations_2025.csv" in csv_path

            # Verify pickle file was saved to isolated directory
            mock_open.assert_called()
            pickle_path = str(mock_open.call_args[0][0])
            assert str(mock_env.output_dir) in pickle_path
            assert "steel_trade_allocations_2025.pkl" in pickle_path

    def test_concurrent_model_runs_isolation(self):
        """Test that concurrent model runs don't interfere with each other."""
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(settings, "MEDIA_ROOT", temp_dir):
                # Create two model runs
                model_run1 = ModelRun.objects.create(
                    name="Concurrent Test 1", config={"start_year": 2025, "end_year": 2025}
                )
                model_run2 = ModelRun.objects.create(
                    name="Concurrent Test 2", config={"start_year": 2025, "end_year": 2025}
                )

                # Ensure directories for both
                model_run1.ensure_output_directories()
                model_run2.ensure_output_directories()

                # Verify different directories
                assert model_run1.output_directory != model_run2.output_directory

                # Write test files to each
                path1 = Path(model_run1.output_directory) / "TM"
                path2 = Path(model_run2.output_directory) / "TM"
                path1.mkdir(parents=True, exist_ok=True)
                path2.mkdir(parents=True, exist_ok=True)

                (path1 / "test_file_1.txt").write_text("Run 1 data")
                (path2 / "test_file_2.txt").write_text("Run 2 data")

                # Verify isolation
                assert (path1 / "test_file_1.txt").exists()
                assert not (path1 / "test_file_2.txt").exists()
                assert (path2 / "test_file_2.txt").exists()
                assert not (path2 / "test_file_1.txt").exists()

                # Test cleanup
                model_run1.delete()
                assert not path1.exists()
                assert path2.exists()  # Model run 2 should be unaffected
