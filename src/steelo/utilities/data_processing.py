"""
Utility functions for data processing and normalization.

This module contains helper functions for processing and normalizing data
from various sources (Excel, JSON, etc.) to ensure consistency throughout
the codebase.
"""


def normalize_product_name(name: str) -> str:
    """
    Normalize product names to use underscores instead of spaces for consistency.

    This ensures that product names from Excel data (e.g., "hot metal") match
    the programmatic constants (e.g., "hot_metal") used throughout the codebase.

    Also converts liquid_steel to steel for consistency.

    Args:
        name: The product name to normalize

    Returns:
        The normalized product name with spaces replaced by underscores and lowercased

    Examples:
        >>> normalize_product_name("hot metal")
        'hot_metal'
        >>> normalize_product_name("pig iron")
        'pig_iron'
        >>> normalize_product_name("steel")
        'steel'
        >>> normalize_product_name("liquid steel")
        'steel'
        >>> normalize_product_name("liquid_steel")
        'steel'
    """
    normalized = name.lower().replace(" ", "_")

    # Apply the same liquid_steel -> steel conversion as in excel reader
    if normalized == "liquid_steel":
        normalized = "steel"

    return normalized
