"""Exceptions for data management module."""


class DataError(Exception):
    """Base exception for data-related errors."""

    pass


class DataValidationError(DataError):
    """Raised when data validation fails."""

    def __init__(self, message: str, errors: list[dict] | None = None):
        super().__init__(message)
        self.errors = errors or []


class DataDownloadError(DataError):
    """Raised when data download fails."""

    pass


class DataIntegrityError(DataError):
    """Raised when data integrity check fails."""

    pass
