"""Module for extracting technology configuration from master Excel."""

import json
import logging
from pathlib import Path
from datetime import datetime, timezone
from tempfile import NamedTemporaryFile
from typing import Optional, Annotated, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from steelo.core.parse import normalize_code_for_dedup, parse_bool, parse_int, resolve_column

logger = logging.getLogger("steelo.tech.extract")


class Technology(BaseModel):
    """Schema for a single technology."""

    code: str
    slug: Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]+$")]
    normalized_code: str  # Added for debugging/tracking
    display_name: str
    product_type: Literal["iron", "steel"]  # Required field - from Product column in Excel
    allowed: bool = True
    from_year: int = Field(2025, ge=2020, le=2100)
    to_year: Optional[int] = Field(None, ge=2020, le=2100)

    model_config = ConfigDict(extra="ignore")  # Ignore unknown fields for forward compatibility

    @model_validator(mode="after")
    def _check_years(self):
        if self.to_year is not None and self.to_year < self.from_year:
            raise ValueError(f"to_year ({self.to_year}) must be >= from_year ({self.from_year})")
        return self


class TechnologyConfig(BaseModel):
    """Schema for the complete technology configuration."""

    schema_version: int = 3  # Updated for product_type field (was 2)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: dict
    technologies: dict[str, Technology]

    model_config = ConfigDict(extra="ignore")  # Ignore unknown fields for forward compatibility


def _slug_for(code: str) -> str:
    """Generate slug from normalized code."""
    return normalize_code_for_dedup(code).lower()


def _sort_key(val: str) -> tuple:
    """
    Sort key that prefers simple codes over complex ones.

    Returns tuple of (normalized_code, non_alnum_count, original_lower)
    This ensures "BF" sorts before "B.F." when normalized codes match.
    """
    import re

    s = str(val).strip()
    norm = normalize_code_for_dedup(s)
    non_alnum = len(re.findall(r"[^A-Za-z0-9]", s))
    return (norm.lower(), non_alnum, s.lower())


def extract_technologies(df: pd.DataFrame, excel_path: Path) -> dict:
    """
    Extract technology configuration from Techno-economic details dataframe.

    Deduplicates by normalized technology code to prevent POST collision.
    Does NOT filter any technologies (CCS, CCU, etc. are included if in Excel).

    Args:
        df: DataFrame from "Techno-economic details" sheet
        excel_path: Path to source Excel file (for metadata)

    Returns:
        Dictionary with technology configuration ready for JSON serialization
    """
    # Column names and aliases
    TECH_CODE_COL = "Technology"
    DISPLAY_NAME_ALIASES = ["Name in the Dashboard", "Display Name", "Name in Dashboard", "Dashboard Name"]
    PRODUCT_ALIASES = ["Product", "Product (iron/steel)"]  # Support variants
    ALLOWED_COL = "Allowed"
    FROM_YEAR_COL = "From year"
    TO_YEAR_COL = "To year"

    # Resolve columns with aliases
    display_col = resolve_column(df, DISPLAY_NAME_ALIASES) or TECH_CODE_COL
    if display_col == TECH_CODE_COL:
        logger.warning("Display name column not found; using Technology codes as display names.")

    # Resolve product column - REQUIRED
    product_col = resolve_column(df, PRODUCT_ALIASES)
    if not product_col:
        raise ValueError(
            "Product column not found in Techno-economic details sheet. "
            "Expected 'Product' column with values 'iron' or 'steel'."
        )

    # Check for optional columns
    has_allowed = ALLOWED_COL in df.columns
    has_from_year = FROM_YEAR_COL in df.columns
    has_to_year = TO_YEAR_COL in df.columns

    if has_allowed:
        logger.info("Found 'Allowed' column in Excel - will use for technology availability")
    if has_from_year:
        logger.info("Found 'From year' column in Excel - will use for technology start year")
    if has_to_year:
        logger.info("Found 'To year' column in Excel - will use for technology end year")

    # Extract unique technologies with deterministic ordering
    technologies = {}
    warnings = []
    duplicates = []

    # Track seen normalized codes to prevent duplicates
    seen_codes = set()

    # Sort with preference for simple codes (BF over B.F.)
    tech_codes = df[TECH_CODE_COL].dropna().unique()
    sorted_codes = sorted(tech_codes, key=_sort_key)

    for tech_code in sorted_codes:
        # Clean and normalize data
        tech_code_str = str(tech_code).strip()
        norm_code = normalize_code_for_dedup(tech_code_str)

        # Skip empty/garbage codes after normalization
        if not norm_code:
            logger.warning("Skipping empty/invalid technology code: %r", tech_code_str)
            continue

        # Skip CCS and CCU as standalone entries - they're suffixes, not technologies
        if tech_code_str in ["CCS", "CCU"]:
            logger.info("Skipping %s - it's a carbon capture suffix, not a standalone technology", tech_code_str)
            continue

        # Check for duplicates by normalized code (prevents POST collision)
        if norm_code in seen_codes:
            duplicates.append(tech_code_str)
            logger.warning("Duplicate technology code after normalization: %s → %s", tech_code_str, norm_code)
            continue
        seen_codes.add(norm_code)

        tech_rows = df[df[TECH_CODE_COL] == tech_code]
        first_row = tech_rows.iloc[0]

        # Get display name with fallback
        display_name = str(first_row.get(display_col, tech_code_str)).strip()
        # Normalize whitespace in display name
        display_name = " ".join(display_name.split())

        # Extract product type - REQUIRED for every technology
        # This enforces the "Excel is the contract" principle
        product_raw = str(first_row.get(product_col, "")).strip().lower()

        # Fail fast if Product column is missing or invalid
        # This ensures Excel editors must provide Product for new technologies
        if not product_raw or product_raw == "nan":
            raise ValueError(
                f"Technology {tech_code_str} missing required Product value. "
                "Excel editors must fill Product column (iron/steel) for all technologies."
            )

        if product_raw not in ["iron", "steel"]:
            raise ValueError(
                f"Invalid product type '{product_raw}' for technology {tech_code_str}. Must be 'iron' or 'steel'."
            )

        product_type = product_raw

        # Parse optional columns with robust parsing
        allowed = parse_bool(first_row.get(ALLOWED_COL), True) if has_allowed else True
        from_year = parse_int(first_row.get(FROM_YEAR_COL), 2025) if has_from_year else 2025
        to_year = parse_int(first_row.get(TO_YEAR_COL), None) if has_to_year else None

        # Use normalized slug (guaranteed unique due to deduplication)
        slug = _slug_for(tech_code_str)

        try:
            # Create Technology instance with validation
            tech = Technology(
                code=tech_code_str,
                slug=slug,
                normalized_code=norm_code,  # Added for debugging/tracking
                display_name=display_name,
                product_type=product_type,  # REQUIRED field from Product column
                allowed=allowed,
                from_year=from_year,
                to_year=to_year,
            )

            technologies[slug] = tech

        except Exception as e:
            warnings.append(f"Technology {tech_code}: {str(e)}")
            logger.warning("Failed to process technology %s: %s", tech_code, e)

    # Log results
    logger.info(
        "Extracted %d technologies, %d duplicates, %d warnings", len(technologies), len(duplicates), len(warnings)
    )

    if duplicates:
        logger.warning("Found duplicate technology codes: %s", duplicates)

    if warnings:
        logger.warning("Extraction warnings: %s", "; ".join(warnings))

    # Create configuration with metadata
    now = datetime.now(timezone.utc)
    config = TechnologyConfig(
        schema_version=3,  # Updated for product_type field
        generated_at=now,
        source={
            "excel_path": Path(excel_path).name,  # Just filename, not full path (privacy)
            "sheet": "Techno-economic details",
            "extraction_date": now.isoformat(),
            "tech_count": len(technologies),
            "duplicates": duplicates if duplicates else None,
            "warnings_count": len(warnings),
            "has_optional_columns": {"allowed": has_allowed, "from_year": has_from_year, "to_year": has_to_year},
        },
        technologies=technologies,
    )

    # Return JSON-ready dict with ISO-8601 datetime formatting
    return config.model_dump(mode="json")


def write_json_atomic(data: dict, dest_path: Path) -> None:
    """Write JSON atomically to avoid partial reads."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    # Create temp file in same directory for atomic replace
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", delete=False, suffix=".json", prefix="tech_", dir=dest_path.parent
    ) as tmp:
        json.dump(data, tmp, indent=2, ensure_ascii=False)  # ensure_ascii=False for human-readable non-ASCII
        tmp_path = Path(tmp.name)

    # Atomic replace on same filesystem
    tmp_path.replace(dest_path)
