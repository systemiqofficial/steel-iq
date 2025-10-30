"""Shared parsing utilities for web and adapters."""

import re
import unicodedata
from typing import Optional, Any


def normalize_code_for_dedup(code: str) -> str:
    """
    Normalize technology code for deduplication.

    Removes all non-alphanumeric characters and converts to uppercase.
    This ensures "BF", "B.F.", "b-f" all map to "BF".

    Args:
        code: Raw technology code from Excel

    Returns:
        Normalized uppercase code with only alphanumeric characters
    """
    # Unicode normalize first (e.g., é → e)
    s = unicodedata.normalize("NFKD", str(code))
    # Remove all non-alphanumerics (punctuation, whitespace, etc.)
    s = re.sub(r"[^A-Za-z0-9]", "", s)
    return s.upper()


# Sets for robust boolean parsing
TRUE_SET = {"1", "true", "yes", "y", "on", "enabled", "active"}
FALSE_SET = {"0", "false", "no", "n", "off", "disabled", "inactive"}

# Sets for strict boolean parsing
TRUE = {"1", "true", "yes", "y", "on"}
FALSE = {"0", "false", "no", "n", "off"}


def parse_bool(val: Any, default: bool = True) -> bool:
    """
    Parse a boolean value from Excel/form data robustly.

    Args:
        val: Value to parse (can be bool, str, int, None, etc.)
        default: Default value if parsing fails

    Returns:
        Parsed boolean value
    """
    if val is None:
        return default
    if isinstance(val, bool):
        return val  # Fast path for actual bools

    # Convert to string and normalize
    s = str(val).strip().lower()
    if s in TRUE_SET:
        return True
    if s in FALSE_SET:
        return False

    return default


def parse_int(val: Any, default: Optional[int] = None, lo: int = 2020, hi: int = 2100) -> Optional[int]:
    """
    Parse an integer value from Excel/form data robustly.

    Handles floats (e.g., "2025.0"), strings with whitespace,
    and enforces reasonable bounds for year values.

    Args:
        val: Value to parse
        default: Default value if parsing fails
        lo: Minimum allowed value (inclusive)
        hi: Maximum allowed value (inclusive)

    Returns:
        Parsed integer value or default
    """
    if val is None or str(val).strip() == "":
        return default

    try:
        # Handle floats like "2025.0" from Excel
        i = int(float(str(val).strip()))
        if lo <= i <= hi:
            return i
    except (ValueError, TypeError):
        pass

    return default


def normalize_column_name(name: str) -> str:
    """
    Normalize column names for comparison.

    Handles case differences, non-breaking spaces, and extra whitespace.

    Args:
        name: Column name to normalize

    Returns:
        Normalized column name in lowercase with single spaces
    """
    # Replace non-breaking spaces with regular spaces
    s = str(name).replace("\u00a0", " ")
    # Normalize whitespace and convert to lowercase
    return " ".join(s.strip().casefold().split())


def resolve_column(df, aliases: list[str]) -> Optional[str]:
    """
    Find a column in a DataFrame by trying multiple aliases.

    Args:
        df: Pandas DataFrame
        aliases: List of possible column names to try

    Returns:
        Actual column name from DataFrame or None if not found
    """
    # Create normalized map of actual column names
    norm_map = {normalize_column_name(c): c for c in df.columns}

    # Try each alias
    for alias in aliases:
        norm_alias = normalize_column_name(alias)
        if norm_alias in norm_map:
            return norm_map[norm_alias]

    return None


def parse_bool_strict(val, default=None) -> bool:
    """Parse boolean strictly - no silent coercion."""
    if val is None:
        if default is None:
            raise ValueError("boolean required")
        return default
    if isinstance(val, bool):
        return val

    s = str(val).strip().lower()
    if s == "":  # Empty string with no default
        if default is None:
            raise ValueError("boolean required")
        return default
    if s in TRUE:
        return True
    if s in FALSE:
        return False
    raise ValueError(f"invalid boolean: {val!r}")


def parse_int_strict(val, required: bool, lo: int, hi: int) -> Optional[int]:
    """Parse integer strictly with range check."""
    if val is None or str(val).strip() == "":
        if required:
            raise ValueError("integer required")
        return None

    try:
        i = int(float(str(val).strip()))
    except (ValueError, TypeError):
        raise ValueError(f"invalid integer: {val!r}") from None

    if not (lo <= i <= hi):
        raise ValueError(f"out of range [{lo},{hi}]: {i}")
    return i


def normalize_code(code: str) -> str:
    """Normalize technology code for consistent keying.

    Matches the logic used in Excel extraction:
    - Convert to uppercase
    - Remove spaces, punctuation, diacritics
    - Keep only alphanumeric characters

    This is an alias for normalize_code_for_dedup for consistency
    with the implementation plan naming.
    """
    return normalize_code_for_dedup(code)
