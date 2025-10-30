"""Strict validation logic for simulation configurations."""

from dataclasses import dataclass
from typing import Set

from steelo.simulation_types import TechSettingsMap


class SimulationConfigError(ValueError):
    """Central error type for all configuration issues.

    Used for:
    - Legacy fields present
    - Schema version mismatch
    - Normalized code collisions
    - Unknown/missing technologies
    - Horizon violations
    - Missing required config fields
    """

    pass


def validate_technology_settings(
    tech_settings: TechSettingsMap,
    available_codes: Set[str],  # normalized codes from technologies.json
    *,
    year_min: int,
    year_max: int,
) -> None:
    """Strict validation - fail fast on any mismatch."""
    # Empty config is invalid
    if not tech_settings:
        raise SimulationConfigError("technology_settings is empty")

    # Unknown technologies in config (using normalized codes)
    unknown = sorted(set(tech_settings) - available_codes)
    if unknown:
        raise SimulationConfigError(f"Unknown technologies in config: {', '.join(unknown)}")

    # Missing technologies from data
    missing = sorted(available_codes - set(tech_settings))
    if missing:
        raise SimulationConfigError(f"Missing settings for technologies: {', '.join(missing)}")

    # Validate individual settings
    for code, ts in tech_settings.items():
        if ts.from_year is None:
            raise SimulationConfigError(f"{code}: from_year required")

        # Check year range validity
        if ts.to_year and ts.to_year < ts.from_year:
            raise SimulationConfigError(f"{code}: invalid year range {ts.from_year}-{ts.to_year}")

    # NOTE: Individual technologies can be outside the scenario range - the simulation will ignore them.
    # However, we must ensure at least one technology is available for EVERY year in the scenario,
    # otherwise the simulation will crash with "No allowed technologies found for year X".

    # Check that every year in the scenario has at least one available technology
    years_without_tech = []
    for year in range(year_min, year_max + 1):
        has_tech_for_year = False
        for code, ts in tech_settings.items():
            if not ts.allowed:
                continue
            # Technology is available from from_year to to_year (or indefinitely if to_year is None)
            tech_start = ts.from_year
            tech_end = ts.to_year if ts.to_year is not None else float("inf")
            # Check if technology is available for this specific year
            if tech_start <= year <= tech_end:
                has_tech_for_year = True
                break

        if not has_tech_for_year:
            years_without_tech.append(year)

    if years_without_tech:
        # Report first gap to make error message actionable
        if len(years_without_tech) == 1:
            year_desc = f"year {years_without_tech[0]}"
        elif len(years_without_tech) <= 5:
            year_desc = f"years {', '.join(map(str, years_without_tech))}"
        else:
            # Show first and last year of gap if there are many
            year_desc = f"years {years_without_tech[0]}-{years_without_tech[-1]}"

        raise SimulationConfigError(
            f"No technologies are available for {year_desc} in the scenario period ({year_min}-{year_max}). "
            "At least one technology must be enabled and available for every year in the scenario."
        )


def check_product_coverage(
    technology_settings: TechSettingsMap,
    tech_to_product: dict[str, str],
    start_year: int,
    end_year: int,
) -> tuple[bool, list[str]]:
    """
    Check if technology configuration provides adequate coverage for iron and steel production.

    Args:
        technology_settings: Dict mapping tech codes to TechnologySettings
        tech_to_product: Dict mapping tech names to product types ('iron', 'steel')
        start_year: Simulation start year
        end_year: Simulation end year

    Returns:
        Tuple of (is_valid, error_messages)
        - is_valid: True if configuration is valid
        - error_messages: List of human-readable error descriptions
    """
    errors = []

    # Build allowed techs by year (mirror simulation logic)
    allowed_techs_by_year = _build_allowed_techs_by_year(technology_settings, start_year, end_year)

    # Check coverage for each year
    for year in range(start_year, end_year + 1):
        allowed_techs_this_year = allowed_techs_by_year.get(year, [])

        # Group allowed techs by product
        products_with_techs = _group_techs_by_product(allowed_techs_this_year, tech_to_product)

        # Check minimum requirements
        missing_products = []
        if "iron" not in products_with_techs:
            missing_products.append("iron")
        if "steel" not in products_with_techs:
            missing_products.append("steel")

        if missing_products:
            tech_suggestions = _get_technology_suggestions(missing_products, tech_to_product)
            errors.append(
                f"No allowed technologies for {' and '.join(missing_products)} "
                f"production in year {year}. Please enable at least one technology "
                f"for each product type. Suggestions: {tech_suggestions}"
            )
            break  # Stop after first problematic year

    return len(errors) == 0, errors


def _build_allowed_techs_by_year(
    technology_settings: TechSettingsMap, start_year: int, end_year: int
) -> dict[int, list[str]]:
    """Mirror the logic from is_technology_allowed to build year-based allowed techs."""
    from steelo.domain.models import is_technology_allowed

    allowed_by_year = {}

    # Get all raw technology names from the settings
    raw_tech_names = list(technology_settings.keys())

    for year in range(start_year, end_year + 1):
        allowed_techs = []

        for raw_tech_name in raw_tech_names:
            if is_technology_allowed(technology_settings, raw_tech_name, year):
                allowed_techs.append(raw_tech_name)

        allowed_by_year[year] = allowed_techs

    return allowed_by_year


def _group_techs_by_product(allowed_techs: list[str], tech_to_product: dict[str, str]) -> dict[str, list[str]]:
    """Group technologies by the products they produce."""
    products_with_techs: dict[str, list[str]] = {}

    for tech in allowed_techs:
        product = tech_to_product.get(tech)
        if product in ["iron", "steel"]:
            products_with_techs.setdefault(product, []).append(tech)

    return products_with_techs


def _get_technology_suggestions(missing_products: list[str], tech_to_product: dict[str, str]) -> str:
    """Generate helpful technology suggestions for missing products."""
    suggestions = []

    for product in missing_products:
        product_techs = [tech for tech, prod in tech_to_product.items() if prod == product]
        if product_techs:
            # Show a few example technologies for this product
            examples = ", ".join(product_techs[:3])  # Show first 3
            if len(product_techs) > 3:
                examples += f", ... ({len(product_techs)} total)"
            suggestions.append(f"{product}: {examples}")

    return "; ".join(suggestions)


@dataclass
class ValidationError:
    """Structured validation error for better UX."""

    title: str
    description: str
    suggestions: list[dict[str, str]]
    product_type: str

    def __str__(self):
        """String representation for debugging."""
        return f"ValidationError(title='{self.title}', product_type='{self.product_type}')"


@dataclass
class ValidationResult:
    """Result of enhanced validation with user-friendly error data."""

    is_valid: bool
    errors: list[ValidationError]


def check_product_coverage_enhanced(
    technology_settings: TechSettingsMap,
    tech_to_product: dict[str, str],
    technologies_data: dict,  # Technology data with display names
    start_year: int,
    end_year: int,
) -> ValidationResult:
    """
    Enhanced validation with user-friendly error data for contextual display.

    Args:
        technology_settings: Dict mapping tech codes to TechnologySettings
        tech_to_product: Dict mapping tech names to product types ('iron', 'steel')
        technologies_data: Technology data from preparation (includes display_name)
        start_year: Simulation start year
        end_year: Simulation end year

    Returns:
        ValidationResult with structured error data for template display
    """
    errors = []

    # Build allowed techs by year (mirror simulation logic)
    allowed_techs_by_year = _build_allowed_techs_by_year(technology_settings, start_year, end_year)

    # Check coverage for each year
    for year in range(start_year, end_year + 1):
        allowed_techs_this_year = allowed_techs_by_year.get(year, [])

        # Group allowed techs by product
        products_with_techs = _group_techs_by_product(allowed_techs_this_year, tech_to_product)

        # Check minimum requirements
        missing_products = []
        if "iron" not in products_with_techs:
            missing_products.append("iron")
        if "steel" not in products_with_techs:
            missing_products.append("steel")

        if missing_products:
            # Build user-friendly error messages for each missing product
            for missing_product in missing_products:
                suggestions = _build_user_friendly_suggestions(missing_product, tech_to_product, technologies_data)
                errors.append(
                    ValidationError(
                        title=f"Missing {missing_product.title()} Production",
                        description=f"No {missing_product} production technologies are enabled for year {year}.",
                        suggestions=suggestions,
                        product_type=missing_product,
                    )
                )
            break  # Stop after first problematic year

    return ValidationResult(is_valid=len(errors) == 0, errors=errors)


def _build_user_friendly_suggestions(
    product_type: str, tech_to_product: dict[str, str], technologies_data: dict
) -> list[dict[str, str]]:
    """Build suggestions using display names from technology data."""
    suggestions = []

    for slug, tech_info in technologies_data.items():
        normalized_code = tech_info.get("normalized_code") or tech_info.get("code", "")
        if tech_to_product.get(normalized_code) == product_type:
            suggestions.append(
                {"display_name": tech_info.get("display_name", normalized_code), "code": normalized_code, "slug": slug}
            )

    return suggestions[:5]  # Limit to top 5 suggestions
