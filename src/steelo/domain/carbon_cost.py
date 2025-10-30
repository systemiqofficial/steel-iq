"""
Carbon cost domain model with clear value objects and services.

This module provides a clear, type-safe way to handle carbon costs throughout
the system, preventing confusion between total and per-unit costs.
"""

from dataclasses import dataclass
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# When the configured emissions boundary is unavailable, fall back to the nearest
# boundary that exists in the emissions payload so that downstream consumers still
# receive non-zero carbon costs.
BOUNDARY_FALLBACKS: dict[str, tuple[str, ...]] = {
    "cbam": ("responsible_steel", "worldsteel_no_opt_credits", "worldsteel_opt_credits"),
}


@dataclass(frozen=True)
class CarbonCost:
    """
    Immutable value object representing carbon costs.

    This makes it impossible to confuse total costs with per-unit costs,
    as each is explicitly named and typed.
    """

    cost_per_unit: float  # $/t of product
    total_cost: float  # Total $ for production volume
    emissions_per_unit: float  # tCO2/t
    carbon_price: float  # $/tCO2
    production: float  # tonnes

    @classmethod
    def calculate(cls, emissions_per_unit: float, carbon_price: float, production: float) -> "CarbonCost":
        """
        Factory method to calculate carbon costs from emissions and price.

        Args:
            emissions_total: tCO2 in total
            carbon_price: $ per tCO2
            production: tonnes of product

        Returns:
            CarbonCost value object with all calculations done
        """
        cost_per_unit = emissions_per_unit * carbon_price
        total_cost = cost_per_unit * production

        return cls(
            cost_per_unit=cost_per_unit,
            total_cost=total_cost,
            emissions_per_unit=emissions_per_unit,
            carbon_price=carbon_price,
            production=production,
        )

    @classmethod
    def zero(cls, production: float = 0.0) -> "CarbonCost":
        """Create a zero carbon cost object."""
        return cls(
            cost_per_unit=0.0,
            total_cost=0.0,
            emissions_per_unit=0.0,
            carbon_price=0.0,
            production=production,
        )


class CarbonCostService:
    """
    Service for calculating carbon costs with proper validation and logging.

    This centralizes all carbon cost logic in one place, making it easier
    to maintain and test.
    """

    def __init__(self, emissions_boundary: str):
        """
        Initialize the service.

        Args:
            emissions_boundary: The emissions boundary to use (e.g., 'responsible_steel')
        """
        self.emissions_boundary = emissions_boundary

    def calculate_carbon_cost(
        self, emissions_total: Optional[dict], carbon_price: float, production: float
    ) -> CarbonCost:
        """
        Calculate carbon cost from emissions data.

        Args:
            emissions_total: Dictionary containing total emissions data by boundary
            carbon_price: Carbon price in $/tCO2
            production: Production volume in tonnes

        Returns:
            CarbonCost value object
        """
        emissions_per_unit = self._calculate_emissions_per_unit(emissions_total, production)

        if carbon_price == 0.0:
            return CarbonCost.zero(production)

        return CarbonCost.calculate(emissions_per_unit, carbon_price, production)

    def _calculate_emissions_per_unit(self, emissions_total: Optional[dict], production: float) -> float:
        """
        Extract emissions per unit from emissions dictionary.

        Args:
            emissions_total: Dictionary containing total emissions data by boundary
            production: Production volume in tonnes

        Returns:
            Emissions in tCO2/t product, or 0.0 if not found
        """
        if not emissions_total or production is None or production <= 0:
            return 0.0

        boundaries_to_try: list[str] = [self.emissions_boundary]
        fallback_boundaries = BOUNDARY_FALLBACKS.get(self.emissions_boundary, ())
        for boundary in fallback_boundaries:
            if boundary not in boundaries_to_try:
                boundaries_to_try.append(boundary)

        for boundary_name in boundaries_to_try:
            boundary_data = emissions_total.get(boundary_name)
            if not boundary_data:
                continue
            direct_emissions = boundary_data.get("direct_ghg")
            if direct_emissions is None:
                logger.debug(
                    "Emissions boundary '%s' is missing 'direct_ghg' key. Skipping for carbon-cost calculation.",
                    boundary_name,
                )
                continue
            return direct_emissions / production

        logger.debug(
            "No usable emissions boundary found for carbon-cost calculation. "
            "Configured boundary: '%s'. Available boundaries: %s",
            self.emissions_boundary,
            list(emissions_total.keys()),
        )
        return 0.0

    def calculate_carbon_cost_series(
        self,
        emissions_total: Optional[dict],
        carbon_prices: dict[int, float],
        start_year: int,
        end_year: int,
        production: float,
    ) -> list[CarbonCost]:
        """
        Calculate carbon costs for a series of years.

        Args:
            emissions_total: Total emissions data
            carbon_prices: Carbon prices by year
            start_year: First year
            end_year: Last year
            production: Annual production

        Returns:
            List of CarbonCost objects, one per year
        """
        costs = []
        for year in range(start_year, end_year + 1):
            carbon_price = carbon_prices.get(year, None)
            if carbon_price is None:
                raise ValueError(f"Carbon price for year {year} not found in provided series.")
            cost = self.calculate_carbon_cost(emissions_total, carbon_price, production)
            costs.append(cost)
        return costs
