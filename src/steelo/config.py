"""
The folders the application needs before any run is configured: STEELO_HOME and the project root.

All configurable parameters are passed through SimulationConfig.
"""

import os
import sys
from pathlib import Path


def get_steelo_home() -> Path:
    """Get the STEELO_HOME directory, defaulting to ~/.steelo if not set."""
    if (steelo_home := os.getenv("STEELO_HOME")) is not None:
        return Path(steelo_home)
    else:
        return Path.home() / ".steelo"


def get_project_root_dir() -> Path:
    """Get the project root directory."""
    cwd = Path.cwd()
    project_root = Path(__file__).resolve(strict=True).parent.parent.parent
    running_from_django = cwd.name == "django"
    if running_from_django:
        # When running from src/django, go up 2 levels to get to project root
        project_root = cwd.parent.parent
        # Verify this is the correct directory by checking for expected files
        if not (project_root / "pyproject.toml").exists():
            # Fall back to the original calculation if we can't find the project root
            project_root = Path(__file__).resolve(strict=True).parent.parent.parent

    # Handle Windows standalone case where project_root might be in a protected location
    if os.name == "nt" and (
        "steelo-electron-windows" in str(Path(__file__))
        or "STEEL-IQ" in str(Path(__file__))
        or ".exe" in str(sys.executable)
    ):
        # In Windows standalone, use a writable location in the user's home directory
        steelo_home = get_steelo_home()
        return steelo_home

    return project_root


# Only expose the essential paths
project_root = get_project_root_dir()
root = get_steelo_home()  # STEELO_HOME
