"""
Checkpoint system for saving and loading simulation state.

This module provides functionality to save simulation state at regular intervals,
allowing for recovery from crashes and debugging of long-running simulations.
"""

import pickle
import json
import logging
import time
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any
from dataclasses import dataclass, asdict
from collections import defaultdict

from ..domain import Environment, Year
from ..service_layer.unit_of_work import UnitOfWork

logger = logging.getLogger(__name__)


@dataclass
class CheckpointMetadata:
    """Metadata about a checkpoint."""

    year: int
    timestamp: str
    simulation_id: str
    checkpoint_version: str = "1.0"

    def to_dict(self) -> dict:
        return asdict(self)


class CheckpointError(Exception):
    """Raised when checkpoint operations fail."""

    pass


class SimulationCheckpoint:
    """
    Manages saving and loading of simulation checkpoints.

    Checkpoints include:
    - Complete repository state (all domain objects)
    - Environment state
    - Metadata about the checkpoint
    """

    def __init__(self, checkpoint_dir: str = "checkpoints"):
        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(exist_ok=True)
        # Include microseconds to ensure unique IDs even when created in quick succession
        self.simulation_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")

    def get_checkpoint_path(self, year: Year) -> Path:
        """Get the path for a checkpoint file."""
        return self.checkpoint_dir / f"checkpoint_year_{year}_sim_{self.simulation_id}.pkl"

    def get_metadata_path(self, year: Year) -> Path:
        """Get the path for checkpoint metadata."""
        return self.checkpoint_dir / f"checkpoint_year_{year}_sim_{self.simulation_id}_metadata.json"

    def save_checkpoint(self, year: Year, env: Environment, uow: UnitOfWork) -> None:
        """
        Save a checkpoint of the current simulation state.

        Args:
            year: The current simulation year
            env: The environment containing simulation state
            uow: Unit of work containing the repository

        Raises:
            CheckpointError: If checkpoint saving fails
        """
        start_time = time.time()
        checkpoint_path = self.get_checkpoint_path(year)
        metadata_path = self.get_metadata_path(year)

        try:
            # Create metadata
            metadata = CheckpointMetadata(
                year=int(year), timestamp=datetime.now().isoformat(), simulation_id=self.simulation_id
            )

            # Save metadata as JSON for easy inspection
            with open(metadata_path, "w") as f:
                json.dump(metadata.to_dict(), f, indent=2)

            # Prepare checkpoint data
            checkpoint_data = {
                "year": year,
                "environment_state": self._serialize_environment(env),
                "repository_state": self._serialize_repository(uow),
                "metadata": metadata,
            }

            # Save checkpoint
            with open(checkpoint_path, "wb") as f:
                pickle.dump(checkpoint_data, f, protocol=pickle.HIGHEST_PROTOCOL)

            elapsed = time.time() - start_time
            logger.info(f"operation=checkpoint_save year={int(year)} duration_s={elapsed:.3f}")

        except Exception as e:
            logger.error(f"Failed to save checkpoint for year {year}: {e}")
            raise CheckpointError(f"Failed to save checkpoint: {e}") from e

    def load_checkpoint(self, year: Year) -> Optional[Dict[str, Any]]:
        """
        Load a checkpoint for a specific year.

        Args:
            year: The year to load the checkpoint for

        Returns:
            Dictionary containing checkpoint data or None if not found

        Raises:
            CheckpointError: If checkpoint loading fails
        """
        # Look for any checkpoint file for the requested year
        pattern = f"checkpoint_year_{year}_sim_*.pkl"
        checkpoint_files = list(self.checkpoint_dir.glob(pattern))

        if not checkpoint_files:
            logger.warning(f"No checkpoint found for year {year}")
            return None

        # Use the most recent checkpoint if multiple exist
        checkpoint_path = max(checkpoint_files, key=lambda p: p.stat().st_mtime)

        try:
            with open(checkpoint_path, "rb") as f:
                checkpoint_data = pickle.load(f)

            logger.info(f"✓ Checkpoint loaded for year {year} from {checkpoint_path}")
            return checkpoint_data

        except Exception as e:
            logger.error(f"Failed to load checkpoint from {checkpoint_path}: {e}")
            raise CheckpointError(f"Failed to load checkpoint: {e}") from e

    def list_checkpoints(self) -> list[CheckpointMetadata]:
        """List all available checkpoints."""
        checkpoints = []

        for metadata_file in self.checkpoint_dir.glob("*_metadata.json"):
            try:
                with open(metadata_file, "r") as f:
                    data = json.load(f)
                    checkpoints.append(CheckpointMetadata(**data))
            except Exception as e:
                logger.warning(f"Could not read metadata from {metadata_file}: {e}")

        return sorted(checkpoints, key=lambda c: c.year)

    def clean_old_checkpoints(self, keep_last_n: int = 10) -> None:
        """Remove old checkpoints, keeping only the most recent N."""
        checkpoint_files = sorted(
            self.checkpoint_dir.glob("checkpoint_year_*.pkl"), key=lambda p: p.stat().st_mtime, reverse=True
        )

        for checkpoint_file in checkpoint_files[keep_last_n:]:
            # Remove checkpoint and its metadata
            checkpoint_file.unlink()
            metadata_file = checkpoint_file.with_suffix(".json")
            if metadata_file.exists():
                metadata_file.unlink()
            logger.info(f"Removed old checkpoint: {checkpoint_file}")

    def _serialize_environment(self, env: Environment) -> dict:
        """
        Serialize environment state.

        Note: This is a simplified version. In practice, you may need to handle
        complex objects within the environment more carefully.
        """

        # Extract key environment state
        # Convert defaultdicts with lambda to regular dicts to avoid pickle issues
        def convert_defaultdict(d):
            """Convert defaultdict to regular dict recursively."""
            if isinstance(d, defaultdict):
                # Convert to regular dict
                result = {}
                for k, v in d.items():
                    result[k] = convert_defaultdict(v)
                return result
            elif isinstance(d, dict):
                # Regular dict - process recursively
                return {k: convert_defaultdict(v) for k, v in d.items()}
            else:
                return d

        return {
            "year": env.year,
            "cost_curve": convert_defaultdict(getattr(env, "cost_curve", {})),
            "future_cost_curve": convert_defaultdict(getattr(env, "future_cost_curve", {})),
            "avg_boms": convert_defaultdict(getattr(env, "avg_boms", {})),
            "avg_utilization": convert_defaultdict(getattr(env, "avg_utilization", {})),
            "dynamic_feedstocks": convert_defaultdict(getattr(env, "dynamic_feedstocks", {})),
            "added_capacity": convert_defaultdict(getattr(env, "added_capacity", {})),
            "capacity_limit": convert_defaultdict(getattr(env, "capacity_limit", {})),
            "cost_of_capital": convert_defaultdict(getattr(env, "cost_of_capital", {})),
            "name_to_capex": convert_defaultdict(getattr(env, "name_to_capex", {})),
            "carbon_costs": convert_defaultdict(getattr(env, "carbon_costs", {})),
            "steel_init_capacity": convert_defaultdict(getattr(env, "steel_init_capacity", {})),
            "iron_init_capacity": convert_defaultdict(getattr(env, "iron_init_capacity", {})),
            "allowed_furnace_transitions": convert_defaultdict(getattr(env, "allowed_furnace_transitions", {})),
            "capex_reduction_ratio": convert_defaultdict(getattr(env, "capex_reduction_ratio", {})),
            "regional_steel_capacity": convert_defaultdict(getattr(env, "regional_steel_capacity", {})),
            "regional_iron_capacity": convert_defaultdict(getattr(env, "regional_iron_capacity", {})),
            "demand_dict": convert_defaultdict(getattr(env, "demand_dict", {})),
            "current_demand": getattr(env, "current_demand", 0),
            # Add more attributes as needed
        }

    def _serialize_repository(self, uow: UnitOfWork) -> dict:
        """
        Serialize repository state using JsonRepository export functionality.
        """
        # Use the JsonRepository's export functionality if available
        # Otherwise, manually serialize domain objects
        repo_data = {
            "plants": [
                plant.to_dict() if hasattr(plant, "to_dict") else plant.__dict__
                for plant in uow.repository.plants.list()
            ],
            "demand_centers": [
                dc.to_dict() if hasattr(dc, "to_dict") else dc.__dict__ for dc in uow.repository.demand_centers.list()
            ],
            "suppliers": [
                s.to_dict() if hasattr(s, "to_dict") else s.__dict__ for s in uow.repository.suppliers.list()
            ],
            # Add other repository collections as needed
        }
        return repo_data
