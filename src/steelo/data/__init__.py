"""Data management module for Steel Model.

This module handles downloading, caching, and managing data files
required by the steel model, including bundled data from S3 and
user-provided Excel files.
"""

from .manager import DataManager
from .manifest import DataManifest
from .exceptions import DataValidationError, DataDownloadError

# Skip DataRecreator import for now to avoid CLI dependency cycles
# from .recreate import DataRecreator
from .path_resolver import DataPathResolver
from .preparation import DataPreparationService, PreparationResult, PreparedFile, FileSource, PreparationStep

__all__ = [
    "DataManager",
    "DataManifest",
    "DataValidationError",
    "DataDownloadError",
    # "DataRecreator",  # Temporarily removed to avoid circular imports
    "DataPathResolver",
    "DataPreparationService",
    "PreparationResult",
    "PreparedFile",
    "FileSource",
    "PreparationStep",
]
