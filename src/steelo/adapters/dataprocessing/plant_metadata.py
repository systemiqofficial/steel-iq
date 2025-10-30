"""
Plant metadata schema and utilities for variable plant lifetime support.

This module provides utilities for capturing, validating, and storing canonical
facts about furnace groups that are independent of the plant_lifetime parameter.
"""

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class FurnaceGroupMetadata:
    """Canonical facts about a furnace group, independent of plant_lifetime."""

    commissioning_year: int | None
    age_at_reference_year: int | None
    last_renovation_year: int | None
    age_source: str  # "exact" | "imputed" | "estimated"
    source_sheet: str
    source_row: int
    validation_warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "commissioning_year": self.commissioning_year,
            "age_at_reference_year": self.age_at_reference_year,
            "last_renovation_year": self.last_renovation_year,
            "age_source": self.age_source,
            "source_sheet": self.source_sheet,
            "source_row": self.source_row,
            "validation_warnings": self.validation_warnings,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FurnaceGroupMetadata":
        """Create from dictionary."""
        return cls(
            commissioning_year=data.get("commissioning_year"),
            age_at_reference_year=data.get("age_at_reference_year"),
            last_renovation_year=data.get("last_renovation_year"),
            age_source=data["age_source"],
            source_sheet=data["source_sheet"],
            source_row=data["source_row"],
            validation_warnings=data.get("validation_warnings", []),
        )


def validate_commissioning_year(year: int | None, furnace_group_id: str) -> list[str]:
    """
    Validate commissioning year makes sense for steel industry.

    Args:
        year: Commissioning year to validate
        furnace_group_id: ID of furnace group for error reporting

    Returns:
        List of validation warnings
    """
    warnings = []

    if year is None:
        warnings.append("commissioning_year_missing")
        return warnings

    # Steel industry modern era starts ~1850s
    if year < 1850:
        warnings.append(f"commissioning_year_too_old: {year}")

    # Can't commission in the future
    current_year = datetime.now().year
    if year > current_year:
        warnings.append(f"commissioning_year_in_future: {year}")

    return warnings


def validate_age_at_reference(age: int | None, reference_year: int, furnace_group_id: str) -> list[str]:
    """
    Validate imputed age at reference year.

    Args:
        age: Age at reference year
        reference_year: The reference year
        furnace_group_id: ID of furnace group for error reporting

    Returns:
        List of validation warnings
    """
    warnings: list[str] = []

    if age is None:
        return warnings  # OK if commissioning year is known

    # Validate imputed age
    if age < 0:
        warnings.append(f"negative_age: {age}")

    if age > 150:
        warnings.append(f"implausibly_old: {age}")

    # Check if implied commissioning year is reasonable
    implied_commissioning = reference_year - age
    if implied_commissioning < 1850:
        warnings.append(f"implied_commissioning_too_old: {implied_commissioning}")

    return warnings


def create_metadata_dict(
    *,
    furnace_group_metadata: dict[str, FurnaceGroupMetadata],
    plant_lifetime_used: int,
    data_reference_year: int,
    master_excel_path: Path,
    master_excel_version: str,
) -> dict[str, Any]:
    """
    Create complete metadata dictionary for JSON serialization.

    Args:
        furnace_group_metadata: Dict mapping furnace_group_id to metadata
        plant_lifetime_used: Plant lifetime used during data preparation
        data_reference_year: Year when age calculations are anchored
        master_excel_path: Path to source Excel file
        master_excel_version: Version string of master Excel

    Returns:
        Complete metadata dictionary ready for JSON serialization
    """
    # Calculate hash of master Excel
    excel_hash = hashlib.sha256(master_excel_path.read_bytes()).hexdigest()

    return {
        "schema_version": "1.0",
        "metadata": {
            "plant_lifetime_used": plant_lifetime_used,
            "data_reference_year": data_reference_year,
            "generated_at": datetime.now().isoformat(),
            "master_excel_hash": f"sha256:{excel_hash}",
            "master_excel_version": master_excel_version,
            "source_file": master_excel_path.name,
        },
        "furnace_groups": {fg_id: fg_meta.to_dict() for fg_id, fg_meta in furnace_group_metadata.items()},
    }


def write_metadata_sidecar(metadata_dict: dict[str, Any], json_directory: Path) -> Path:
    """
    Write metadata sidecar file to JSON directory.

    Args:
        metadata_dict: Complete metadata dictionary
        json_directory: Directory where plants.json is located

    Returns:
        Path to written metadata file
    """
    metadata_path = json_directory / "plants_metadata.json"
    metadata_path.write_text(json.dumps(metadata_dict, indent=2))

    num_furnace_groups = len(metadata_dict.get("furnace_groups", {}))
    logger.info(f"Written metadata for {num_furnace_groups} furnace groups to {metadata_path}")

    return metadata_path


def validate_metadata_coverage(
    plant_fg_ids: set[str],
    metadata_fg_ids: set[str],
) -> None:
    """
    Validate that metadata covers all furnace groups.

    Args:
        plant_fg_ids: Set of furnace group IDs from plants
        metadata_fg_ids: Set of furnace group IDs in metadata

    Raises:
        ValueError: If there are missing or extra furnace groups
    """
    missing = plant_fg_ids - metadata_fg_ids
    extra = metadata_fg_ids - plant_fg_ids

    if missing:
        raise ValueError(
            f"Metadata missing for {len(missing)} furnace groups: "
            f"{list(sorted(missing))[:10]}{'...' if len(missing) > 10 else ''}"
        )

    if extra:
        logger.warning(
            f"Metadata contains {len(extra)} unused entries: "
            f"{list(sorted(extra))[:10]}{'...' if len(extra) > 10 else ''}"
        )
