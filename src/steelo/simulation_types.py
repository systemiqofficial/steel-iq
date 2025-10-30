"""Core type definitions for the simulation system."""

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class TechnologySettings:
    """Settings for a single technology."""

    allowed: bool
    from_year: int
    to_year: Optional[int] = None

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict."""
        return {"allowed": self.allowed, "from_year": self.from_year, "to_year": self.to_year}

    def __repr__(self) -> str:
        """Readable representation for logs."""
        to_str = f"-{self.to_year}" if self.to_year else ""
        status = "✓" if self.allowed else "✗"
        return f"Tech({status} {self.from_year}{to_str})"


TechSettingsMap = Dict[str, TechnologySettings]


def get_default_technology_settings() -> TechSettingsMap:
    """Get default technology settings for testing and fallbacks.

    Returns a complete set of technology settings with sensible defaults.
    Most technologies are allowed, except ESF and MOE which are typically
    restricted to later years.
    """
    return {
        "BF": TechnologySettings(allowed=True, from_year=2025, to_year=None),
        "BFBOF": TechnologySettings(allowed=True, from_year=2025, to_year=None),
        "BOF": TechnologySettings(allowed=True, from_year=2025, to_year=None),
        "DRING": TechnologySettings(allowed=True, from_year=2025, to_year=None),
        "DRINGEAF": TechnologySettings(allowed=True, from_year=2025, to_year=None),
        "DRIH2": TechnologySettings(allowed=True, from_year=2025, to_year=None),
        "DRIH2EAF": TechnologySettings(allowed=True, from_year=2025, to_year=None),
        "EAF": TechnologySettings(allowed=True, from_year=2025, to_year=None),
        "ESF": TechnologySettings(allowed=False, from_year=2025, to_year=None),
        "ESFEAF": TechnologySettings(allowed=False, from_year=2025, to_year=None),
        "MOE": TechnologySettings(allowed=False, from_year=2025, to_year=None),
        "DRI": TechnologySettings(allowed=True, from_year=2025, to_year=None),
    }
