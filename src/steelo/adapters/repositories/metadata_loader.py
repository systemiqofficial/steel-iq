"""
Metadata loader for plant lifetime reconstruction.

This module provides classes for loading and accessing plants_metadata.json files
to enable variable plant_lifetime at runtime.
"""

import json
import logging
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


class MetadataProvider(Protocol):
    """Interface for metadata access - allows testing without coupling to JSON loading."""

    def get_furnace_group_metadata(self, fg_id: str) -> dict | None:
        """Get metadata for a specific furnace group.

        Args:
            fg_id: Furnace group ID

        Returns:
            Metadata dict or None if not found
        """
        ...

    def get_data_reference_year(self) -> int:
        """Get the reference year for age calculations.

        Returns:
            Data reference year
        """
        ...

    def get_schema_version(self) -> str:
        """Get the metadata schema version.

        Returns:
            Schema version string
        """
        ...

    def get_plant_lifetime_used(self) -> int:
        """Get the plant lifetime used during data preparation.

        Returns:
            Plant lifetime in years
        """
        ...


class JsonMetadata:
    """Loads and provides access to plants_metadata.json."""

    def __init__(self, metadata_path: Path):
        """
        Initialize metadata loader.

        Args:
            metadata_path: Path to plants_metadata.json file

        Raises:
            ValueError: If schema version is not supported
        """
        self.path = metadata_path
        self._data = self._load()

    def _load(self) -> dict | None:
        """
        Load and validate metadata file.

        Returns:
            Metadata dictionary or None if file doesn't exist

        Raises:
            ValueError: If schema version is unsupported
        """
        if not self.path.exists():
            logger.debug(f"Metadata file not found: {self.path}")
            return None

        try:
            data = json.loads(self.path.read_text())
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in metadata file {self.path}: {e}") from e

        # Validate schema version
        schema_version = data.get("schema_version")
        if schema_version != "1.0":
            raise ValueError(
                f"Unsupported metadata schema version: {schema_version}. Expected 1.0. Please regenerate data."
            )

        logger.info(f"Loaded metadata with schema version {schema_version} from {self.path}")
        return data

    def get_furnace_group_metadata(self, fg_id: str) -> dict | None:
        """Get metadata for a specific furnace group."""
        if not self._data:
            return None
        return self._data.get("furnace_groups", {}).get(fg_id)

    def get_data_reference_year(self) -> int:
        """Get the reference year for age calculations."""
        if not self._data:
            raise ValueError("No metadata loaded")
        return self._data["metadata"]["data_reference_year"]

    def get_schema_version(self) -> str:
        """Get the metadata schema version."""
        if not self._data:
            return "unknown"
        return self._data.get("schema_version", "unknown")

    def get_plant_lifetime_used(self) -> int:
        """Get the plant lifetime used during data preparation."""
        if not self._data:
            raise ValueError("No metadata loaded")
        return self._data["metadata"]["plant_lifetime_used"]

    @property
    def is_loaded(self) -> bool:
        """Check if metadata was successfully loaded."""
        return self._data is not None

    def __bool__(self) -> bool:
        """Allow using metadata in boolean context."""
        return self.is_loaded
