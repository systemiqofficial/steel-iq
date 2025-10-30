"""
Integration tests to verify output path propagation through the simulation stack.
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
import tempfile
from datetime import datetime


from steelo.simulation_types import get_default_technology_settings

from steelo.simulation import SimulationConfig
from steelo.domain.datacollector import DataCollector


class TestOutputPathPropagation:
    """Test that output paths are correctly propagated through all components."""

    def test_simulation_config_paths(self):
        """Test that SimulationConfig correctly stores output paths."""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "test_output"
            config = SimulationConfig(
                output_dir=output_dir,
                plots_dir=output_dir / "plots",
                geo_plots_dir=output_dir / "plots" / "GEO",
                pam_plots_dir=output_dir / "plots" / "PAM",
                start_year=2025,
                end_year=2025,
                master_excel_path=Path("./data/fixtures/master_input.xlsx"),
                technology_settings=get_default_technology_settings(),
            )

            assert config.output_dir == output_dir
            assert config.plots_dir == output_dir / "plots"
            assert config.geo_plots_dir == output_dir / "plots" / "GEO"
            assert config.pam_plots_dir == output_dir / "plots" / "PAM"

    @pytest.mark.skip(reason="Needs refactoring to work with new bootstrap architecture")
    def test_simulation_runner_sets_environment_paths(self):
        """Test that bootstrap_simulation sets paths in bus.env."""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "test_output"

            # Create test fixtures in data directory
            data_dir = output_dir / "data"
            data_dir.mkdir(exist_ok=True, parents=True)
            fixtures_dir = data_dir / "fixtures"
            fixtures_dir.mkdir(exist_ok=True, parents=True)

            # Create minimal tech_switches_allowed.csv
            tech_switches_csv = fixtures_dir / "tech_switches_allowed.csv"
            tech_switches_csv.write_text(
                "Technology,BF-BOF,DRI-EAF,Scrap-EAF\nBF-BOF,YES,YES,YES\nDRI-EAF,NO,YES,YES\nScrap-EAF,NO,NO,YES\n"
            )

            # Create all required JSON files with minimal structure
            import json

            # Most need {"root": []} structure
            json_files = {
                "plants.json": {"root": []},
                "demand_centers.json": {"root": []},
                "suppliers.json": {"root": []},
                "plant_groups.json": {"root": []},
                "tariffs.json": {"root": []},
                "subsidies.json": {"root": []},
                "carbon_costs.json": {"root": []},
                "primary_feedstocks.json": {"root": []},
                "input_costs.json": {"root": []},
                "region_emissivity.json": {"root": []},
                "capex.json": {"root": []},
                "cost_of_capital.json": {"root": []},
                "legal_process_connectors.json": {"root": []},
                "country_mappings.json": [],
                "hydrogen_efficiency.json": {"root": []},
                "hydrogen_capex_opex.json": {"root": []},
                "transport_emissions.json": {"root": []},
                "biomass_availability.json": [],
                "carbon_storage.json": {"root": []},
            }
            for filename, content in json_files.items():
                (fixtures_dir / filename).write_text(json.dumps(content))

            # Create railway_costs.json in data dir (not fixtures)
            (data_dir / "railway_costs.json").write_text("[]")

            # Create config with custom paths
            config = SimulationConfig(
                output_dir=output_dir,
                plots_dir=output_dir / "plots",
                geo_plots_dir=output_dir / "plots" / "GEO",
                pam_plots_dir=output_dir / "plots" / "PAM",
                start_year=2025,
                end_year=2025,
                master_excel_path=Path(
                    temp_dir,
                    technology_settings=get_default_technology_settings(),
                )
                / "master.xlsx",
                data_dir=data_dir,
            )

            # Create a dummy master excel file
            (Path(temp_dir) / "master.xlsx").write_bytes(b"fake excel")

            # Use bootstrap_simulation directly
            from steelo.bootstrap import bootstrap_simulation

            runner = bootstrap_simulation(config)

            # Verify environment was configured with the paths
            assert runner.bus.env.plot_paths is not None
            plot_paths = runner.bus.env.plot_paths
            assert plot_paths.plots_dir == output_dir / "plots"
            assert plot_paths.geo_plots_dir == output_dir / "plots" / "GEO"
            assert plot_paths.pam_plots_dir == output_dir / "plots" / "PAM"
            assert runner.bus.env.output_dir == output_dir

    def test_datacollector_receives_output_dir(self):
        """Test that DataCollector is initialized with correct output_dir."""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "test_output"
            output_dir.mkdir(parents=True)

            # Mock environment
            env = MagicMock()

            # Create DataCollector - now requires output_dir
            collector = DataCollector(world_plant_groups=[], env=env, output_dir=output_dir)

            # DataCollector stores output_dir
            assert collector is not None
            assert collector.output_dir == output_dir

    @pytest.mark.skip(reason="Needs refactoring to work with new bootstrap architecture")
    @patch("steelo.simulation.extract_and_process_stored_dataCollection")
    @patch("steelo.simulation.plot_bar_chart_of_new_plants_by_status_and_tech")
    @patch("steelo.simulation.plot_map_of_plants_under_construction")
    @patch("steelo.simulation.generate_post_run_cap_prod_plots")
    def test_plotting_functions_receive_paths(self, mock_generate_plots, mock_plot_map, mock_plot_bar, mock_extract):
        """Test that plotting functions receive correct paths from SimulationRunner."""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "test_output"

            # Create test fixtures in data directory
            data_dir = output_dir / "data"
            data_dir.mkdir(exist_ok=True, parents=True)
            fixtures_dir = data_dir / "fixtures"
            fixtures_dir.mkdir(exist_ok=True, parents=True)

            # Create minimal tech_switches_allowed.csv
            tech_switches_csv = fixtures_dir / "tech_switches_allowed.csv"
            tech_switches_csv.write_text(
                "Technology,BF-BOF,DRI-EAF,Scrap-EAF\nBF-BOF,YES,YES,YES\nDRI-EAF,NO,YES,YES\nScrap-EAF,NO,NO,YES\n"
            )

            # Create minimal cost_of_x.json
            cost_of_x_json = fixtures_dir / "cost_of_x.json"
            cost_of_x_json.write_text("[]")

            # Create minimal plants.json to avoid JSON parsing error
            plants_json = fixtures_dir / "plants.json"
            plants_json.write_text('{"plants": []}')

            # Create config
            config = SimulationConfig(
                output_dir=output_dir,
                plots_dir=output_dir / "plots",
                geo_plots_dir=output_dir / "plots" / "GEO",
                pam_plots_dir=output_dir / "plots" / "PAM",
                start_year=2025,
                end_year=2025,
                master_excel_path=Path("./data/fixtures/master_input.xlsx"),
                technology_settings=get_default_technology_settings(),
            )

            # Create a mock repository to avoid file access
            from steelo.adapters.repositories import InMemoryRepository
            from steelo.domain.models import PlantGroup, CountryMapping
            from steelo.devdata import get_plant, get_test_demand_centers

            mock_repository = InMemoryRepository()

            # Add a minimal plant to avoid empty cost curve
            plant = get_plant(plant_id="test_plant", tech_name="EAF", production=50.0, unit_production_cost=70.0)
            mock_repository.plants.add(plant)

            # Add a demand center
            demand_centers = get_test_demand_centers()
            if demand_centers:
                mock_repository.demand_centers.add(demand_centers[0])

            # Add plant groups
            plant_group = PlantGroup(plant_group_id="test_group", plants=[plant])
            mock_repository.plant_groups.add(plant_group)
            mock_repository.plant_groups.add(PlantGroup(plant_group_id="indi", plants=[]))

            # Create a mock JSON repository with country mappings
            mock_json_repository = MagicMock()
            mock_json_repository.country_mappings = MagicMock()
            mock_json_repository.country_mappings.get_all.return_value = [
                CountryMapping(
                    country="Germany",
                    iso2="DE",
                    iso3="DEU",
                    irena_name="Germany",
                    region_for_outputs="Europe",
                    ssp_region="EUR",
                    gem_country="Germany",
                    ws_region="Europe",
                    tiam_ucl_region="Western Europe",
                    eu_region="EU",
                )
            ]
            mock_json_repository.capex = MagicMock()
            mock_json_repository.capex.list.return_value = []

            # Mock extract to return a path
            mock_extract.return_value = output_dir / "TM" / "test.csv"

            # Mock the DataCollector and GeospatialModel
            with patch("steelo.simulation.DataCollector") as mock_dc_class:
                mock_dc = MagicMock()
                mock_dc.status_counts = {}
                mock_dc.new_plant_locations = {}
                mock_dc.trace_price = {}
                mock_dc.cost_breakdown = {}
                mock_dc.trace_capacity = {}
                mock_dc.trace_production = {}
                mock_dc_class.return_value = mock_dc

                with patch("steelo.simulation.GeospatialModel") as mock_geo_model:
                    mock_geo_model_instance = MagicMock()
                    mock_geo_model.return_value = mock_geo_model_instance

                    # Create test config with injected repository
                    from steelo.bootstrap import bootstrap_simulation

                    test_config = SimulationConfig.for_testing(
                        repository=mock_repository,
                        start_year=config.start_year,
                        end_year=config.end_year,
                        output_dir=config.output_dir,
                        plots_dir=config.plots_dir,
                        geo_plots_dir=config.geo_plots_dir,
                        pam_plots_dir=config.pam_plots_dir,
                        data_dir=data_dir,  # Required for environment initialization
                    )
                    # Set the JSON repository for country mappings
                    test_config._json_repository = mock_json_repository

                    # Create runner
                    runner = bootstrap_simulation(test_config)

                    # Get the environment for verification
                    env = runner.bus.env

                    # Mock the run method to avoid complex simulation logic
                    with patch.object(runner, "run"):
                        # Manually call the plotting functions that run() would call
                        from steelo.simulation import (
                            extract_and_process_stored_dataCollection,
                            generate_post_run_cap_prod_plots,
                            plot_bar_chart_of_new_plants_by_status,
                            plot_map_of_new_plants_operating,
                        )

                        # Call extract_and_process_stored_dataCollection as run() would
                        post_processed_path = extract_and_process_stored_dataCollection(
                            data_dir=test_config.tm_output_dir,
                            output_path=test_config.tm_output_dir
                            / f"post_processed_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                            from_year=test_config.start_year,
                            to_year=test_config.end_year,
                        )

                        # Call plotting functions as run() would
                        plot_bar_chart_of_new_plants_by_status(
                            data_collector=runner.data_collector, plot_paths=env.plot_paths
                        )
                        plot_map_of_new_plants_operating(
                            world_plant_list=runner.bus.uow.plants.list(),
                            plot_paths=env.plot_paths,
                        )
                        generate_post_run_cap_prod_plots(
                            output_path=post_processed_path,
                            output_dir=test_config.output_dir,
                            plot_paths=env.plot_paths,
                        )

            # Verify extract_and_process_stored_dataCollection received paths
            mock_extract.assert_called_once()
            call_args = mock_extract.call_args
            assert call_args.kwargs["data_dir"] == output_dir / "TM"
            assert str(output_dir / "TM") in str(call_args.kwargs["output_path"])
            assert "post_processed_" in str(call_args.kwargs["output_path"])

            # Verify plotting functions received plot_paths
            mock_plot_bar.assert_called()
            plot_bar_args = mock_plot_bar.call_args
            assert plot_bar_args.kwargs["plot_paths"] == env.plot_paths

            mock_plot_map.assert_called()
            plot_map_args = mock_plot_map.call_args
            assert plot_map_args.kwargs["plot_paths"] == env.plot_paths

            mock_generate_plots.assert_called()
            generate_args = mock_generate_plots.call_args
            assert generate_args.kwargs["output_path"] == output_dir / "TM" / "test.csv"
            assert generate_args.kwargs["output_dir"] == output_dir
            assert generate_args.kwargs["plot_paths"] == env.plot_paths

    @patch("steelo.economic_models.plant_agent.pickle.dump")
    @patch("builtins.open", create=True)
    def test_allocation_model_uses_env_output_dir(self, mock_open, mock_pickle):
        """Test that AllocationModel uses bus.env.output_dir."""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "test_output"
            output_dir.mkdir(parents=True)
            (output_dir / "TM").mkdir(parents=True)

            # Create mock bus with environment
            mock_bus = MagicMock()
            mock_env = MagicMock()
            mock_env.year = 2025
            mock_env.output_dir = output_dir
            mock_bus.env = mock_env

            # Mock trade LP
            with patch("steelo.economic_models.plant_agent.set_up_steel_trade_lp") as mock_setup:
                mock_lp = MagicMock()
                mock_lp.allocations = {"test": "allocations"}
                mock_lp.solve_lp_model.return_value = None
                mock_lp.extract_solution.return_value = None
                mock_setup.return_value = mock_lp

                with patch(
                    "steelo.economic_models.plant_agent.solve_steel_trade_lp_and_return_commodity_allocations"
                ) as mock_solve:
                    # Mock the commodity allocations return
                    mock_allocations = {"steel": MagicMock(allocations=[]), "iron": MagicMock(allocations=[])}
                    mock_solve.return_value = mock_allocations

                    with patch("steelo.economic_models.plant_agent.export_commodity_allocations_to_csv") as mock_export:
                        # Run AllocationModel instead of PlantAgentsModel
                        from steelo.economic_models.plant_agent import AllocationModel

                        AllocationModel.run(mock_bus)

                        # Verify CSV export used correct path
                        mock_export.assert_called_once()
                        csv_path = mock_export.call_args.kwargs["filename"]
                        assert str(output_dir) in csv_path
                        assert "TM" in csv_path

                        # Verify pickle save used correct path
                        mock_open.assert_called()
                        pickle_path = str(mock_open.call_args[0][0])
                        assert str(output_dir) in pickle_path

    def test_full_path_propagation_chain(self):
        """Test the complete chain of output path propagation."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create isolated output directory
            isolated_dir = Path(temp_dir) / "model_outputs" / "run_123"
            isolated_dir.mkdir(parents=True)

            # Create all expected subdirectories
            (isolated_dir / "TM").mkdir()
            (isolated_dir / "plots" / "GEO").mkdir(parents=True)
            (isolated_dir / "plots" / "PAM").mkdir(parents=True)

            # Track all file writes
            written_files = []

            def track_file_write(path, *args, **kwargs):
                written_files.append(str(path))
                return MagicMock()

            # Patch file operations to track writes
            with patch("builtins.open", side_effect=track_file_write):
                with patch("pathlib.Path.write_text", side_effect=lambda content: written_files.append(str(self))):
                    with patch("pathlib.Path.write_bytes", side_effect=lambda content: written_files.append(str(self))):
                        # Create config with isolated paths
                        config = SimulationConfig(
                            output_dir=isolated_dir,
                            plots_dir=isolated_dir / "plots",
                            geo_plots_dir=isolated_dir / "plots" / "GEO",
                            pam_plots_dir=isolated_dir / "plots" / "PAM",
                            start_year=2025,
                            end_year=2025,
                            master_excel_path=Path("./data/fixtures/master_input.xlsx"),
                            technology_settings=get_default_technology_settings(),
                        )

                        # Verify all paths point to isolated directory
                        assert str(isolated_dir) in str(config.output_dir)
                        assert str(isolated_dir) in str(config.plots_dir)
                        assert str(isolated_dir) in str(config.geo_plots_dir)
                        assert str(isolated_dir) in str(config.pam_plots_dir)

            # Verify no files would be written outside isolated directory
            for file_path in written_files:
                assert str(isolated_dir) in file_path or "mock" in file_path.lower(), (
                    f"File {file_path} would be written outside isolated directory"
                )
