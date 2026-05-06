"""
Utility functions for data processing and normalization.

This module contains helper functions for processing and normalizing data
from various sources (Excel, JSON, etc.) to ensure consistency throughout
the codebase.
"""

from steelo.utilities.utils import normalize_name


def normalize_product_name(name: str) -> str:
    """
    Normalize product names for consistency.

    Delegates to ``normalize_name()`` for canonical lowercase/underscore form,
    then applies the liquid_steel → steel mapping.

    Args:
        name: The product name to normalise.

    Returns:
        The normalised product name.

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
    normalized = normalize_name(name)

    # Apply the same liquid_steel -> steel conversion as in excel reader
    if normalized == "liquid_steel":
        normalized = "steel"

    return normalized
