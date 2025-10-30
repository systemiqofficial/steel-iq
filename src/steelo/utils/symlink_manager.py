"""Utility functions for managing backward-compatible symlinks."""

import os
import shutil
from pathlib import Path
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


def create_symlink_with_backup(
    target: Path, link_path: Path, backup_suffix: str = "backup", max_backups: int = 5
) -> None:
    """
    Create a symlink, backing up any existing file/directory/symlink.

    Args:
        target: The target path that the symlink should point to
        link_path: The path where the symlink should be created
        backup_suffix: Suffix to append to backup names
        max_backups: Maximum number of backups to keep
    """
    # Ensure target exists
    if not target.exists():
        raise ValueError(f"Target path does not exist: {target}")

    # Handle existing path at link location
    if link_path.exists() or link_path.is_symlink():
        # Create backup name with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{link_path.name}_{backup_suffix}_{timestamp}"
        backup_path = link_path.parent / backup_name

        # If it's a symlink, just remove it (don't backup symlinks)
        if link_path.is_symlink():
            logger.info(f"Removing existing symlink: {link_path}")
            link_path.unlink()
        else:
            # It's a real file/directory, back it up
            logger.info(f"Backing up existing path: {link_path} -> {backup_path}")
            shutil.move(str(link_path), str(backup_path))

            # Clean up old backups
            _cleanup_old_backups(link_path.parent, f"{link_path.name}_{backup_suffix}_", max_backups)

    # Create the symlink
    try:
        # Use relative path for the symlink if possible
        if link_path.parent == target.parent:
            # Same parent directory, use relative path
            os.symlink(target.name, str(link_path))
        else:
            # Different parents, use absolute path
            os.symlink(str(target.absolute()), str(link_path))
        logger.info(f"Created symlink: {link_path} -> {target}")
    except OSError as e:
        logger.error(f"Failed to create symlink: {e}")
        raise


def _cleanup_old_backups(directory: Path, prefix: str, max_backups: int) -> None:
    """Remove old backups, keeping only the most recent ones."""
    # Find all backups with the given prefix
    backups = []
    for item in directory.iterdir():
        if item.name.startswith(prefix):
            try:
                # Add to list with modification time for sorting
                backups.append((item.stat().st_mtime, item))
            except Exception:
                # If we can't parse it, skip it
                continue

    # Sort by modification time (oldest first)
    backups.sort(key=lambda x: x[0])

    # Remove oldest backups if we exceed the limit
    while len(backups) > max_backups:
        _, old_backup = backups.pop(0)
        logger.info(f"Removing old backup: {old_backup}")
        if old_backup.is_dir():
            shutil.rmtree(old_backup)
        else:
            old_backup.unlink()


def update_data_symlink(steelo_home: Path, data_prep_dir: Path) -> None:
    """
    Update the 'data' symlink to point to the latest data preparation.

    Args:
        steelo_home: The STEELO_HOME directory
        data_prep_dir: The directory containing the prepared data (usually has 'fixtures' subdirectory)
    """
    data_link = steelo_home / "data"

    # The data preparation directory should contain the actual data
    # Check if it has a fixtures subdirectory (common pattern)
    if (data_prep_dir / "fixtures").exists():
        target = data_prep_dir
    else:
        # Otherwise, assume the prep_dir itself contains the data
        target = data_prep_dir

    create_symlink_with_backup(target=target, link_path=data_link, backup_suffix="backup")


def update_output_symlink(steelo_home: Path, simulation_output_dir: Path) -> None:
    """
    Update the 'output' symlink to point to the latest simulation output.

    Args:
        steelo_home: The STEELO_HOME directory
        simulation_output_dir: The directory containing the simulation output
    """
    output_link = steelo_home / "output_latest"

    create_symlink_with_backup(target=simulation_output_dir, link_path=output_link, backup_suffix="backup")


def setup_legacy_symlinks(project_root: Path, steelo_home: Path) -> None:
    """
    Set up legacy symlinks in the project root for backward compatibility.

    This creates 'data' and 'output' symlinks in the project root that point
    to the corresponding directories in STEELO_HOME.

    Args:
        project_root: The project root directory
        steelo_home: The STEELO_HOME directory
    """
    # Create data symlink in project root
    if (steelo_home / "data").exists():
        create_symlink_with_backup(target=steelo_home / "data", link_path=project_root / "data", backup_suffix="backup")

    # Create output symlink in project root
    if (steelo_home / "output_latest").exists():
        create_symlink_with_backup(
            target=steelo_home / "output_latest", link_path=project_root / "output", backup_suffix="backup"
        )
