"""
Default technology availability settings.

This module provides configurable defaults for technology availability dates
that are used when the Excel file doesn't specify "From year" or "To year" columns.
"""

from typing import Optional

# Default year when technologies become available
DEFAULT_TECHNOLOGY_FROM_YEAR = 2030

# Default year when technologies stop being available (None = indefinite)
DEFAULT_TECHNOLOGY_TO_YEAR: Optional[int] = None

# Default allowed status for technologies
DEFAULT_TECHNOLOGY_ALLOWED = True

# Technology-specific overrides (optional)
# Format: {"TECHNOLOGY_CODE": {"from_year": 2030, "to_year": 2050, "allowed": True}}
# NOTE: Use NORMALIZED codes (no +, _, or special chars)
TECHNOLOGY_SPECIFIC_DEFAULTS = {
    # Core technologies - available from simulation start
    "BOF": {"allowed": True, "from_year": 2025},
    "BF": {"allowed": True, "from_year": 2025},
    "EAF": {"allowed": True, "from_year": 2025},
    "DRI": {"allowed": True, "from_year": 2025},
    # Special technologies
    "ESF": {"allowed": False, "from_year": 2030},  # Electro-Smelting Furnace disabled by default
    "MOE": {"allowed": False, "from_year": 2030},  # Molten Oxide Electrolysis disabled by default
    "SR": {"allowed": True, "from_year": 2030},  # Smelting Reduction
    # Advanced technologies (normalized codes)
    "EWIN": {"allowed": True, "from_year": 2030},  # Electrowinning (E-WIN → EWIN)
    "BFCCS": {"allowed": True, "from_year": 2030},  # Blast Furnace with CCS (BF+CCS → BFCCS)
    "BFCCU": {"allowed": True, "from_year": 2030},  # Blast Furnace with CCU (BF+CCU → BFCCU)
    "BFCHARCOAL": {"allowed": True, "from_year": 2030},  # Charcoal blast furnace (BF_CHARCOAL → BFCHARCOAL)
    "BFCHARCOALCCS": {"allowed": True, "from_year": 2030},  # Charcoal BF with CCS
    "BFCHARCOALCCU": {"allowed": True, "from_year": 2030},  # Charcoal BF with CCU
    "DRICCS": {"allowed": True, "from_year": 2030},  # DRI with CCS (DRI+CCS → DRICCS)
    "DRICCU": {"allowed": True, "from_year": 2030},  # DRI with CCU (DRI+CCU → DRICCU)
    "DRIESF": {"allowed": True, "from_year": 2030},  # DRI with ESF (DRI+ESF → DRIESF)
    "DRIESFCCS": {"allowed": True, "from_year": 2030},  # DRI with ESF and CCS
    "DRIESFCCU": {"allowed": True, "from_year": 2030},  # DRI with ESF and CCU
    "SRCCS": {"allowed": True, "from_year": 2030},  # Smelting Reduction with CCS
    "SRCCU": {"allowed": True, "from_year": 2030},  # Smelting Reduction with CCU
}


def get_technology_defaults(technology_code: str) -> dict:
    """
    Get default settings for a specific technology.

    Args:
        technology_code: Normalized technology code (e.g., "BF", "EAF", "DRI")

    Returns:
        Dictionary with "from_year", "to_year", and "allowed" keys
    """
    # Start with global defaults
    defaults = {
        "from_year": DEFAULT_TECHNOLOGY_FROM_YEAR,
        "to_year": DEFAULT_TECHNOLOGY_TO_YEAR,
        "allowed": DEFAULT_TECHNOLOGY_ALLOWED,
    }

    # Apply technology-specific overrides if available
    if technology_code in TECHNOLOGY_SPECIFIC_DEFAULTS:
        defaults.update(TECHNOLOGY_SPECIFIC_DEFAULTS[technology_code])

    return defaults
