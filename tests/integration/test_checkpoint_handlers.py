"""
Integration tests for checkpoint functionality.

Tests the complete checkpoint save/load flow through the message bus,
ensuring all state is properly persisted and restored.
"""

import pytest
from pathlib import Path
import shutil

from steelo.simulation_types import get_default_technology_settings

from steelo.domain import Year
from steelo.service_layer.checkpoint import SimulationCheckpoint
from steelo.domain.events import IterationOver, SaveCheckpoint
from steelo.devdata import get_plant
from steelo.bootstrap import bootstrap
from steelo.domain.models import CountryMappingService, CountryMapping


@pytest.fixture
def logged_events(bus):
    """Add a logging event handler for all events."""
    logged_events = []

    def log_events(evt):
        logged_events.append(evt)

    for event_type, handlers in bus.event_handlers.items():
        handlers.append(log_events)
    return logged_events


class TestCheckpointIntegration:
    """Test checkpoint functionality through the message bus."""

    @pytest.fixture
    def checkpoint_dir(self, tmp_path):
        """Create a temporary checkpoint directory."""
        checkpoint_dir = tmp_path / "test_checkpoints"
        checkpoint_dir.mkdir()
        yield checkpoint_dir
        # Cleanup
        if checkpoint_dir.exists():
            shutil.rmtree(checkpoint_dir)

    @pytest.fixture
    def bus(self, checkpoint_dir, mock_cost_of_x_file, mock_tech_switches_file, tmp_path):
        """Create a message bus with test checkpoint directory."""
        from steelo.simulation import SimulationConfig
        from steelo.domain.constants import Year
        from pathlib import Path

        config = SimulationConfig(
            start_year=Year(2025),
            end_year=Year(2050),
            master_excel_path=Path(tmp_path) / "master.xlsx",
            output_dir=Path(tmp_path),
            technology_settings=get_default_technology_settings(),
        )
        return bootstrap(
            config=config,
            tech_switches_csv=mock_tech_switches_file,
            checkpoint_dir=str(checkpoint_dir),
        )

    @pytest.fixture
    def checkpoint_system(self, checkpoint_dir):
        """Create a checkpoint system with test directory."""
        return SimulationCheckpoint(str(checkpoint_dir))

    def test_checkpoint_save_through_event(self, bus, logged_events, checkpoint_dir):
        """Test that SaveCheckpoint event triggers checkpoint saving."""
        # Given: A simulation at year 2025
        bus.env.year = Year(2025)

        # When: SaveCheckpoint event is raised
        save_event = SaveCheckpoint(year=Year(2025))
        bus.handle(save_event)

        # Then: Checkpoint file should exist
        checkpoint_files = list(Path(checkpoint_dir).glob("checkpoint_year_2025_*.pkl"))
        assert len(checkpoint_files) == 1

        # And: Metadata file should exist
        metadata_files = list(Path(checkpoint_dir).glob("checkpoint_year_2025_*_metadata.json"))
        assert len(metadata_files) == 1

    def test_automatic_checkpoint_on_iteration(self, bus, logged_events, checkpoint_dir):
        """Test that checkpoints are automatically saved every 5 years."""

        # Initialize demand_dict to avoid AttributeError
        bus.env.demand_dict = {}

        # Set up country mappings for test
        mappings = [
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
            ),
        ]
        bus.env.country_mappings = CountryMappingService(mappings)

        # And: Simulation at year 2024
        bus.env.year = Year(2024)
        ### --- These below are generated in initiate_capex
        bus.env.name_to_capex = {  # Generated in function init_using list of class Capex objects :
            "greenfield": {"Europe": {"BOF": 200, "EAF": 300}},
            "default": {"Europe": {"BOF": 200, "EAF": 300}},
        }

        # Initialize regional capacity attributes required by the handler
        bus.env.steel_init_capacity = {"Europe": 1000}
        bus.env.iron_init_capacity = {"Europe": 800}

        # When: Iteration completes to year 2025
        iteration_event = IterationOver(time_step_increment=1, iron_price=1000)
        bus.handle(iteration_event)

        # Then: Checkpoint should be saved (2025 is divisible by 5)
        checkpoint_files = list(Path(checkpoint_dir).glob("checkpoint_year_2025_*.pkl"))
        assert len(checkpoint_files) == 1

        # When: Another iteration to 2026
        bus.env.year = Year(2025)
        bus.handle(IterationOver(time_step_increment=1, iron_price=1000))

        # Then: No new checkpoint (2026 is not divisible by 5)
        checkpoint_files = list(Path(checkpoint_dir).glob("checkpoint_year_2026_*.pkl"))
        assert len(checkpoint_files) == 0

    def test_checkpoint_preserves_plant_state(self, bus, checkpoint_system):
        """Test that plant state is preserved in checkpoints."""
        # Given: A plant with specific state
        plant = get_plant()
        bus.uow.plants.add(plant)
        original_capacity = plant.furnace_groups[0].capacity
        _original_status = plant.furnace_groups[0].status

        # When: Checkpoint is saved
        checkpoint_system.save_checkpoint(Year(2025), bus.env, bus.uow)

        # And: State is modified
        plant.furnace_groups[0].capacity = original_capacity * 2
        plant.furnace_groups[0].status = "closed"

        # And: Checkpoint is loaded
        checkpoint_data = checkpoint_system.load_checkpoint(Year(2025))

        # Then: Checkpoint should contain original state
        assert checkpoint_data is not None
        repo_state = checkpoint_data["repository_state"]
        assert "plants" in repo_state
        # Note: Full restoration would require implementing deserialization

    def test_checkpoint_metadata(self, checkpoint_system, mock_cost_of_x_file, mock_tech_switches_file, tmp_path):
        """Test that checkpoint metadata is saved correctly."""
        # Given: A checkpoint system
        # When: Listing checkpoints before any saves
        checkpoints = checkpoint_system.list_checkpoints()
        assert len(checkpoints) == 0

        # When: Saving checkpoints for multiple years
        from steelo.domain.models import Environment
        from steelo.service_layer.unit_of_work import UnitOfWork
        from steelo.simulation import SimulationConfig
        from steelo.domain import Year

        config = SimulationConfig(
            start_year=Year(2025),
            end_year=Year(2040),
            master_excel_path=tmp_path / "master.xlsx",
            output_dir=tmp_path / "output",
            technology_settings=get_default_technology_settings(),
        )
        env = Environment(tech_switches_csv=mock_tech_switches_file, config=config)
        uow = UnitOfWork()

        for year in [2025, 2030, 2035]:
            checkpoint_system.save_checkpoint(Year(year), env, uow)

        # Then: All checkpoints should be listed
        checkpoints = checkpoint_system.list_checkpoints()
        assert len(checkpoints) == 3
        assert [c.year for c in checkpoints] == [2025, 2030, 2035]

    def test_checkpoint_cleanup(self, checkpoint_system, mock_cost_of_x_file, mock_tech_switches_file, tmp_path):
        """Test old checkpoint cleanup functionality."""
        # Given: Multiple checkpoints
        from steelo.domain.models import Environment
        from steelo.service_layer.unit_of_work import UnitOfWork
        from steelo.simulation import SimulationConfig
        from steelo.domain import Year

        config = SimulationConfig(
            start_year=Year(2025),
            end_year=Year(2040),
            master_excel_path=tmp_path / "master.xlsx",
            output_dir=tmp_path / "output",
            technology_settings=get_default_technology_settings(),
        )
        env = Environment(tech_switches_csv=mock_tech_switches_file, config=config)
        uow = UnitOfWork()

        for year in range(2025, 2040):
            checkpoint_system.save_checkpoint(Year(year), env, uow)

        # When: Cleaning old checkpoints (keep last 5)
        checkpoint_system.clean_old_checkpoints(keep_last_n=5)

        # Then: Only 5 checkpoints should remain
        remaining_files = list(checkpoint_system.checkpoint_dir.glob("checkpoint_year_*.pkl"))
        assert len(remaining_files) == 5

    def test_checkpoint_error_handling(self, bus, logged_events, checkpoint_dir, monkeypatch):
        """Test error handling during checkpoint operations."""
        # Set up country mappings and other required attributes
        mappings = [
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
            ),
        ]
        bus.env.country_mappings = CountryMappingService(mappings)
        bus.env.demand_dict = {}
        bus.env.name_to_capex = {
            "greenfield": {"Europe": {"BOF": 200, "EAF": 300}},
            "default": {"Europe": {"BOF": 200, "EAF": 300}},
        }
        bus.env.steel_init_capacity = {"Europe": 1000}
        bus.env.iron_init_capacity = {"Europe": 800}

        # Given: Get the checkpoint system from the bus dependencies
        checkpoint_system = None
        for handlers in bus.event_handlers.values():
            for handler in handlers:
                if hasattr(handler, "__closure__") and handler.__closure__:
                    for cell in handler.__closure__:
                        deps = cell.cell_contents
                        if isinstance(deps, dict) and "checkpoint_system" in deps:
                            checkpoint_system = deps["checkpoint_system"]
                            break

        # Mock the save_checkpoint method to fail
        def failing_save(*args, **kwargs):
            raise Exception("Simulated save failure")

        monkeypatch.setattr(checkpoint_system, "save_checkpoint", failing_save)

        # When: Trying to save checkpoint through iteration
        bus.env.year = Year(2024)
        bus.handle(IterationOver(time_step_increment=1, iron_price=1000))

        # Then: Simulation should continue (error is logged but not raised)
        assert bus.env.year == Year(2025)

    def test_checkpoint_with_complex_state(self, bus, repository_for_trade, checkpoint_system):
        """Test checkpoint with complex repository state including trade data."""
        # Given: Complex repository state
        for plant in repository_for_trade.plants.list():
            bus.uow.plants.add(plant)
        for supplier in repository_for_trade.suppliers.list():
            bus.uow.repository.suppliers.add(supplier)
        for demand_center in repository_for_trade.demand_centers.list():
            bus.uow.repository.demand_centers.add(demand_center)

        # When: Saving checkpoint
        checkpoint_system.save_checkpoint(Year(2025), bus.env, bus.uow)

        # Then: Checkpoint should be created successfully
        checkpoint_data = checkpoint_system.load_checkpoint(Year(2025))
        assert checkpoint_data is not None
        assert "repository_state" in checkpoint_data
        assert "plants" in checkpoint_data["repository_state"]
        assert "suppliers" in checkpoint_data["repository_state"]
        assert "demand_centers" in checkpoint_data["repository_state"]
