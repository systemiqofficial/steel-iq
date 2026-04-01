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
    These defaults are now configurable via steelo.config.technology_defaults.
    """
    try:
        from steelo.domain.constants import get_technology_defaults

        # Define all known technology codes (NORMALIZED format)
        all_tech_codes = [
            "BF",
            "BOF",
            "BF+CCS",
            "BF+CCU",
            "BF_CHARCOAL",
            "BF_CHARCOAL+CCS",
            "BF_CHARCOAL+CCU",
            "DRI+CCS",
            "DRI+CCU",
            "DRI+ESF",
            "DRI+ESF+CCS",
            "DRI+ESF+CCU",
            "EAF",
            "E-WIN",
            "MOE",
            "DRI",
            "SR",
            "SR+CCS",
            "SR+CCU",
        ]

        result = {}
        for tech_code in all_tech_codes:
            defaults = get_technology_defaults(tech_code)
            result[tech_code] = TechnologySettings(
                allowed=defaults["allowed"],
                from_year=defaults["from_year"],
                to_year=defaults["to_year"],
            )

        # Apply specific overrides for ESF and MOE if not already set in config
        if "ESF" in result and result["ESF"].allowed:
            result["ESF"] = TechnologySettings(
                allowed=False, from_year=defaults["from_year"], to_year=defaults["to_year"]
            )
        if "ESFEAF" in result and result["ESFEAF"].allowed:
            result["ESFEAF"] = TechnologySettings(
                allowed=False, from_year=defaults["from_year"], to_year=defaults["to_year"]
            )
        if "MOE" in result and result["MOE"].allowed:
            result["MOE"] = TechnologySettings(
                allowed=False, from_year=defaults["from_year"], to_year=defaults["to_year"]
            )

        return result

    except ImportError:
        # Fallback to hardcoded defaults if config file doesn't exist
        return {
            # Special technology overrides (all starting in 2030)
            "BOF": TechnologySettings(allowed=False, from_year=2025),
            "BF": TechnologySettings(allowed=False, from_year=2025),
            "EAF": TechnologySettings(allowed=False, from_year=2025),
            "DRI": TechnologySettings(allowed=False, from_year=2025),
            "ESF": TechnologySettings(allowed=False, from_year=2030),  # Electro-Smelting Furnace disabled by default
            "MOE": TechnologySettings(allowed=False, from_year=2030),  # Molten Oxide Electrolysis disabled by default
            "E-WIN": TechnologySettings(allowed=True, from_year=2030),  # Electrowinning starts in 2030
            "BF+CCS": TechnologySettings(allowed=True, from_year=2030),  # Blast Furnace with CCS
            "BF+CCU": TechnologySettings(allowed=True, from_year=2030),  # Blast Furnace with CCU
            "BF_CHARCOAL": TechnologySettings(allowed=True, from_year=2030),  # Charcoal blast furnace
            "BF_CHARCOAL+CCS": TechnologySettings(allowed=True, from_year=2030),  # Charcoal BF with CCS
            "BF_CHARCOAL+CCU": TechnologySettings(allowed=True, from_year=2030),  # Charcoal BF with CCU
            "DRI+CCS": TechnologySettings(allowed=True, from_year=2030),  # DRI with CCS
            "DRI+CCU": TechnologySettings(allowed=True, from_year=2030),  # DRI with CCU
            "DRI+ESF": TechnologySettings(allowed=True, from_year=2030),  # DRI with ESF
            "DRI+ESF+CCS": TechnologySettings(allowed=True, from_year=2030),  # DRI with ESF and CCS
            "DRI+ESF+CCU": TechnologySettings(allowed=True, from_year=2030),  # DRI with ESF and CCU
            "SR+CCS": TechnologySettings(allowed=True, from_year=2030),  # Smelting Reduction with CCS
            "SR+CCU": TechnologySettings(allowed=True, from_year=2030),  # Smelting Reduction with CCU
        }
