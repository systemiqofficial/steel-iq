"""
Base settings.

This module now only provides the minimal settings needed to bootstrap the application.
All configurable parameters should be passed through SimulationConfig.
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

# Data year range for production data (used by preprocessing modules)
PRODUCTION_GEM_DATA_YEARS = range(2019, 2023)


class Settings:
    """Minimal settings object for allowed exception modules only."""

    def __init__(self):
        self.project_root = project_root
        # Required for baseload_power_simulation.py (allowed exception module)
        self.gravity_distances_pkl_path = self.project_root / "data" / "gravity_distances.pkl"
        self.regional_energy_prices_excel = self.project_root / "data" / "regional_energy_prices.xlsx"


# Only create settings object for backward compatibility with allowed exception modules
settings = Settings()
