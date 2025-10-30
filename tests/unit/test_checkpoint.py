"""
Unit tests for checkpoint functionality.

Tests individual checkpoint operations in isolation.
"""

import pytest
import json
import pickle
import shutil
import tempfile

from pathlib import Path

from steelo.service_layer.checkpoint import (
    SimulationCheckpoint,
    CheckpointMetadata,
    CheckpointError,
)
from steelo.domain import Year
from steelo.domain.models import Environment
from steelo.service_layer.unit_of_work import UnitOfWork


class TestCheckpointMetadata:
    """Test checkpoint metadata functionality."""

    def test_metadata_creation(self):
        """Test creating checkpoint metadata."""
        metadata = CheckpointMetadata(year=2025, timestamp="2024-01-01T12:00:00", simulation_id="test_sim_001")

        assert metadata.year == 2025
        assert metadata.timestamp == "2024-01-01T12:00:00"
        assert metadata.simulation_id == "test_sim_001"
        assert metadata.checkpoint_version == "1.0"

    def test_metadata_to_dict(self):
        """Test converting metadata to dictionary."""
        metadata = CheckpointMetadata(year=2025, timestamp="2024-01-01T12:00:00", simulation_id="test_sim_001")

        data = metadata.to_dict()
        assert data["year"] == 2025
        assert data["timestamp"] == "2024-01-01T12:00:00"
        assert data["simulation_id"] == "test_sim_001"
        assert data["checkpoint_version"] == "1.0"


class TestSimulationCheckpoint:
    """Test SimulationCheckpoint class."""

    @pytest.fixture
    def temp_checkpoint_dir(self, tmp_path):
        """Create a temporary checkpoint directory."""
        checkpoint_dir = tmp_path / "test_checkpoints"
        checkpoint_dir.mkdir()
        yield checkpoint_dir
        if checkpoint_dir.exists():
            shutil.rmtree(checkpoint_dir)

    @pytest.fixture
    def checkpoint_system(self, temp_checkpoint_dir):
        """Create a checkpoint system instance."""
        return SimulationCheckpoint(str(temp_checkpoint_dir))

    @pytest.fixture
    def mock_cost_of_x_file(self):
        """Create a temporary cost_of_x.json file for testing."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            data = {"Country code": {"0": "USA"}, "Cost of equity - industrial assets": {"0": 0.25}}
            json.dump(data, f)
            temp_path = Path(f.name)
        yield temp_path
        temp_path.unlink()  # Clean up

    @pytest.fixture
    def mock_tech_switches_file(self):
        """Create a temporary tech_switches_allowed.csv file for testing."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("Origin,BF,BOF,DRI,EAF\n")
            f.write("BF,NO,NO,NO,NO\n")
            temp_path = Path(f.name)
        yield temp_path
        temp_path.unlink()  # Clean up

    @pytest.fixture
    def mock_env(self, mock_cost_of_x_file, mock_tech_switches_file):
        """Create a mock environment."""
        from steelo.simulation import SimulationConfig
        from steelo.simulation_types import get_default_technology_settings
        from pathlib import Path
        import tempfile

        config = SimulationConfig(
            start_year=Year(2025),
            end_year=Year(2050),
            master_excel_path=Path(tempfile.gettempdir()) / "master.xlsx",
            output_dir=Path(tempfile.gettempdir()),
            technology_settings=get_default_technology_settings(),
        )
        env = Environment(config=config, tech_switches_csv=mock_tech_switches_file)
        env.year = Year(2025)
        env.cost_curve = {"steel": [], "iron": []}
        env.avg_boms = {}
        env.dynamic_feedstocks = {}
        env.added_capacity = {}
        env.capacity_limit = {}
        env.cost_of_capital = {}
        env.name_to_capex = {"default": {}, "greenfield": {}}
        return env

    @pytest.fixture
    def mock_uow(self):
        """Create a mock unit of work."""
        return UnitOfWork()

    def test_checkpoint_directory_creation(self, tmp_path):
        """Test that checkpoint directory is created if it doesn't exist."""
        checkpoint_dir = tmp_path / "new_checkpoint_dir"
        assert not checkpoint_dir.exists()

        SimulationCheckpoint(str(checkpoint_dir))
        assert checkpoint_dir.exists()

    def test_get_checkpoint_path(self, checkpoint_system):
        """Test checkpoint path generation."""
        path = checkpoint_system.get_checkpoint_path(Year(2025))

        assert "checkpoint_year_2025" in str(path)
        assert f"sim_{checkpoint_system.simulation_id}" in str(path)
        assert path.suffix == ".pkl"

    def test_get_metadata_path(self, checkpoint_system):
        """Test metadata path generation."""
        path = checkpoint_system.get_metadata_path(Year(2025))

        assert "checkpoint_year_2025" in str(path)
        assert f"sim_{checkpoint_system.simulation_id}" in str(path)
        assert "metadata.json" in str(path)

    def test_save_checkpoint_creates_files(self, checkpoint_system, mock_env, mock_uow):
        """Test that save_checkpoint creates both checkpoint and metadata files."""
        # Save checkpoint
        checkpoint_system.save_checkpoint(Year(2025), mock_env, mock_uow)

        # Check files exist
        checkpoint_path = checkpoint_system.get_checkpoint_path(Year(2025))
        metadata_path = checkpoint_system.get_metadata_path(Year(2025))

        assert checkpoint_path.exists()
        assert metadata_path.exists()

        # Check metadata content
        with open(metadata_path, "r") as f:
            metadata = json.load(f)

        assert metadata["year"] == 2025
        assert metadata["simulation_id"] == checkpoint_system.simulation_id
        assert "timestamp" in metadata

    def test_save_checkpoint_data_structure(self, checkpoint_system, mock_env, mock_uow):
        """Test the structure of saved checkpoint data."""
        checkpoint_system.save_checkpoint(Year(2025), mock_env, mock_uow)

        # Load and check structure
        checkpoint_path = checkpoint_system.get_checkpoint_path(Year(2025))
        with open(checkpoint_path, "rb") as f:
            data = pickle.load(f)

        assert "year" in data
        assert data["year"] == Year(2025)
        assert "environment_state" in data
        assert "repository_state" in data
        assert "metadata" in data

        # Check environment state
        env_state = data["environment_state"]
        assert "year" in env_state
        assert "cost_curve" in env_state
        assert "name_to_capex" in env_state

    def test_load_checkpoint_not_found(self, checkpoint_system):
        """Test loading a checkpoint that doesn't exist."""
        result = checkpoint_system.load_checkpoint(Year(2099))
        assert result is None

    def test_load_checkpoint_success(self, checkpoint_system, mock_env, mock_uow):
        """Test successfully loading a checkpoint."""
        # Save checkpoint
        checkpoint_system.save_checkpoint(Year(2025), mock_env, mock_uow)

        # Load checkpoint
        data = checkpoint_system.load_checkpoint(Year(2025))

        assert data is not None
        assert data["year"] == Year(2025)
        assert "environment_state" in data
        assert "repository_state" in data

    def test_list_checkpoints_empty(self, checkpoint_system):
        """Test listing checkpoints when none exist."""
        checkpoints = checkpoint_system.list_checkpoints()
        assert len(checkpoints) == 0

    def test_list_checkpoints_multiple(self, checkpoint_system, mock_env, mock_uow):
        """Test listing multiple checkpoints."""
        # Save multiple checkpoints
        for year in [2025, 2030, 2035]:
            mock_env.year = Year(year)
            checkpoint_system.save_checkpoint(Year(year), mock_env, mock_uow)

        # List checkpoints
        checkpoints = checkpoint_system.list_checkpoints()

        assert len(checkpoints) == 3
        years = [c.year for c in checkpoints]
        assert years == [2025, 2030, 2035]  # Should be sorted

    def test_clean_old_checkpoints(self, checkpoint_system, mock_env, mock_uow):
        """Test cleaning old checkpoints."""
        # Save many checkpoints
        for year in range(2025, 2040):
            mock_env.year = Year(year)
            checkpoint_system.save_checkpoint(Year(year), mock_env, mock_uow)

        # Check all exist
        all_files = list(checkpoint_system.checkpoint_dir.glob("checkpoint_year_*.pkl"))
        assert len(all_files) == 15

        # Clean old checkpoints
        checkpoint_system.clean_old_checkpoints(keep_last_n=5)

        # Check only 5 remain
        remaining_files = list(checkpoint_system.checkpoint_dir.glob("checkpoint_year_*.pkl"))
        assert len(remaining_files) == 5

        # Check that the most recent ones are kept
        remaining_years = []
        for f in remaining_files:
            # Extract year from filename
            year_str = f.stem.split("_")[2]
            remaining_years.append(int(year_str))

        assert sorted(remaining_years) == [2035, 2036, 2037, 2038, 2039]

    def test_checkpoint_error_on_save_failure(self, checkpoint_system, mock_env, mock_uow, monkeypatch):
        """Test that CheckpointError is raised on save failure."""

        # Make pickle.dump fail
        def failing_dump(*args, **kwargs):
            raise Exception("Simulated pickle failure")

        monkeypatch.setattr(pickle, "dump", failing_dump)

        with pytest.raises(CheckpointError) as exc_info:
            checkpoint_system.save_checkpoint(Year(2025), mock_env, mock_uow)

        assert "Failed to save checkpoint" in str(exc_info.value)

    def test_checkpoint_error_on_load_failure(self, checkpoint_system, mock_env, mock_uow, monkeypatch):
        """Test that CheckpointError is raised on load failure."""
        # Save a checkpoint first
        checkpoint_system.save_checkpoint(Year(2025), mock_env, mock_uow)

        # Make pickle.load fail
        def failing_load(*args, **kwargs):
            raise Exception("Simulated pickle failure")

        monkeypatch.setattr(pickle, "load", failing_load)

        with pytest.raises(CheckpointError) as exc_info:
            checkpoint_system.load_checkpoint(Year(2025))

        assert "Failed to load checkpoint" in str(exc_info.value)

    def test_multiple_simulations_same_year(self, temp_checkpoint_dir, mock_env, mock_uow):
        """Test that multiple simulations can save checkpoints for the same year."""
        # Create two checkpoint systems (simulating two different runs)
        checkpoint1 = SimulationCheckpoint(str(temp_checkpoint_dir))
        checkpoint2 = SimulationCheckpoint(str(temp_checkpoint_dir))

        # Save checkpoints for the same year
        checkpoint1.save_checkpoint(Year(2025), mock_env, mock_uow)
        import time

        time.sleep(0.01)  # Small delay to ensure different file modification times
        checkpoint2.save_checkpoint(Year(2025), mock_env, mock_uow)

        # Both should exist
        all_2025_checkpoints = list(temp_checkpoint_dir.glob("checkpoint_year_2025_*.pkl"))
        assert len(all_2025_checkpoints) == 2

        # Loading should return the most recent checkpoint (checkpoint2's)
        data = checkpoint1.load_checkpoint(Year(2025))
        assert data is not None
        assert data["metadata"].simulation_id == checkpoint2.simulation_id

        # Also verify that both checkpoint files have different simulation IDs
        assert checkpoint1.simulation_id != checkpoint2.simulation_id
