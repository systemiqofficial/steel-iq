# TODO: Move FurnaceGroup, Plant, PlantGroup, and Environment classes to separate files
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import copy
import math
import logging
import random
import uuid
from geopy.distance import geodesic  # type: ignore
from typing import TYPE_CHECKING, TypeVar, ClassVar, FrozenSet, Dict, Tuple, Union, Any, Callable, Optional
from collections import defaultdict, Counter
from steelo.domain import events, commands
from steelo.domain.calculate_costs import (
    build_direct_ghg_lookup,
    calculate_capex_with_subsidies,
    calculate_debt_repayment,
    calculate_debt_with_subsidies,
    calculate_energy_costs_and_most_common_reductant,
    calculate_opex_list_with_subsidies,
    calculate_unit_total_opex,
    calculate_variable_opex,
    scale_fopex_to_production,
    ZeroUtilisationError,
    filter_subsidies_for_year,
    get_subsidised_energy_costs,
    collect_active_subsidies_over_period,
    collect_subsidies_for_geo,
    score_reductants_for_business_cases,
    summarise_reductant_picks,
    ENERGY_FEEDSTOCK_KEYS,
    ReductantScoreSeries,
)
from steelo.utilities.utils import normalize_name
from steelo.domain.carbon_cost import CarbonCost, CarbonCostService
from steelo.domain import diagnostics as diag
from steelo.utilities.utils import merge_two_dictionaries
from steelo.core.parse import normalize_code
from steelo.simulation_types import TechSettingsMap

# Import only true constants from constants file
from steelo.domain.constants import (
    Volumes,
    Year,
    Commodities,
    T_TO_KT,
    T_TO_KG,
    MioUSD_TO_USD,
    MINIMUM_UTILIZATION_RATE_FOR_COST_CURVE,
    MINIMUM_PRODUCTION_VOLUME_FOR_COST_CURVE,
)

if TYPE_CHECKING:
    from steelo.simulation import SimulationConfig


class UnknownTechnologyError(KeyError):
    """Raised when checking unknown technology."""

    pass


def tech_key(code: str) -> str:
    """Get normalized key for technology code."""
    return normalize_code(code)


def is_technology_allowed(config: TechSettingsMap, raw_code: str, year: int) -> bool:
    """Check if technology is allowed - NO SILENT DEFAULTS."""
    # Normalize the code for lookup
    normalized_code = normalize_code(raw_code)

    try:
        ts = config[normalized_code]
    except KeyError as e:
        raise UnknownTechnologyError(
            f"Technology '{raw_code}' (normalized: '{normalized_code}') not in configuration"
        ) from e

    if not ts.allowed:
        return False
    if year < ts.from_year:
        return False
    if ts.to_year is not None and year > ts.to_year:
        return False
    return True


BoundedStringT = TypeVar("BoundedStringT", bound="BoundedString")


class BoundedString(str):
    _valid_values: ClassVar[FrozenSet[str]] = frozenset()  # Make it immutable

    def __new__(cls: type[BoundedStringT], value: str) -> BoundedStringT:
        if value not in cls._valid_values:
            raise ValueError(f"Invalid value for {cls.__name__}: {value}. Valid values are: {cls._valid_values}")
        return str.__new__(cls, value)

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return value in cls._valid_values

    @classmethod
    def all_values(cls) -> FrozenSet[str]:
        return cls._valid_values


class ProductCategory(BoundedString):
    _valid_values = frozenset({"Flat", "Long", "Tubes", "Semi-finished"})


class TimeFrame:
    """
    Holds the start and end year for the model calculations.
    """

    def __init__(self, *, start: Year = Year(2025), end: Year = Year(2060)) -> None:
        self.start = start
        self.end = end

    @property
    def years(self) -> tuple[Year, ...]:
        return tuple(Year(y) for y in range(self.start, self.end + 1))


class PointInTime:
    """
    Holds the current year and the time frame for the model calculations.

    For example, the lifetime of a furnace group is a point in time. It
    started in a specific year and will end in a specific year and how
    much lifetime is left can be calculated based on the current year.
    """

    def __init__(
        self, *, plant_lifetime: int, current: Year | None = None, time_frame: TimeFrame | None = None
    ) -> None:
        if not time_frame:
            time_frame = TimeFrame()
        if not current:
            current = time_frame.start
        self.current = current
        self.time_frame = time_frame
        self.plant_lifetime = plant_lifetime

    @property
    def start(self) -> Year:
        return self.time_frame.start

    @property
    def end(self) -> Year:
        return self.time_frame.end

    @property
    def number_of_years(self) -> int:
        return self.end - self.start

    @property
    def remaining_number_of_years(self) -> int:
        """
        Calculate remaining years in the current renovation cycle.

        Plants undergo renovation every X years (X is set by env.config.plant_lifetime variable with
        default: 20 years). This property calculates how many years remain in the current renovation cycle,
        properly handling plants that have undergone multiple renovations over their operational history.

        Renovation Cycle Logic:
        ----------------------
        Plants are treated as having repeating renovation cycles rather than absolute ages.
        For a plant of age A years with lifetime L years:
        - Cycles completed = A ÷ L (integer division)
        - Years in current cycle = A mod L
        - Remaining years in cycle = L - (A mod L)

        Example (plant from 1909, evaluated in 2025, with L=20):
        - Actual age: 116 years
        - Cycles completed: 5 (1909-1929, 1929-1949, 1949-1969, 1969-1989, 1989-2009)
        - Years into current cycle: 16 years (2009-2025)
        - Remaining years: 4 years (until 2029 renovation)

        Three Scenarios:
        ---------------
        1. Plant hasn't started yet (actual_age <= 0):
           Returns years until first renovation, capped at plant_lifetime

        2. First renovation cycle (actual_age < plant_lifetime):
           Returns plant_lifetime - actual_age
           Example: 10-year-old plant with L=20 → 10 years remaining

        3. Subsequent renovation cycles (actual_age >= plant_lifetime):
           Uses modulo arithmetic to find position in current cycle
           Example: 116-year-old plant with L=20 → 116 % 20 = 16 years elapsed → 4 remaining
           Special case: If years_in_current_cycle == 0, plant is at renovation boundary

        Returns:
            int: Years remaining until next renovation (0 if at renovation boundary)
        """
        # Calculate actual age of the plant
        actual_age = self.current - self.start

        if actual_age <= 0:
            # Plant hasn't started yet
            return min(self.plant_lifetime, self.end - self.current)
        elif actual_age < self.plant_lifetime:
            # First renovation cycle
            return max(0, self.plant_lifetime - actual_age)
        else:
            # Subsequent renovation cycles
            years_in_current_cycle = actual_age % self.plant_lifetime
            if years_in_current_cycle == 0:
                # Exactly at renovation boundary
                return 0
            return self.plant_lifetime - years_in_current_cycle

    @property
    def elapsed_number_of_years(self) -> int:
        """
        Calculate years elapsed in the current renovation cycle.

        This is the complement of remaining_number_of_years. Together they sum to plant_lifetime.
        See the comprehensive renovation cycle explanation in the remaining_number_of_years property.

        Returns:
            int: Years elapsed since the start of the current renovation cycle.
                 For plants in their first cycle: actual_age (e.g., 10 years for a plant from 2015)
                 For plants in subsequent cycles: years_in_current_cycle (e.g., 16 years for 116-year-old plant with L=20)
        """
        actual_age = self.current - self.start
        if actual_age <= 0:
            return 0
        elif actual_age < self.plant_lifetime:
            # First cycle
            return actual_age
        else:
            # Subsequent cycles - return years in current cycle
            years_in_cycle = actual_age % self.plant_lifetime
            return years_in_cycle if years_in_cycle > 0 else self.plant_lifetime

    @property
    def expired(self) -> bool:
        """
        Check if current renovation cycle has expired.
        For plants older than plant_lifetime, checks if at renovation boundary.
        """
        return self.remaining_number_of_years == 0


# Value type carried through Location.resolve so the lookup's value type is preserved (e.g. float in → float out)
_GeoValueT = TypeVar("_GeoValueT")


def compose_geo_key(iso3: str, geo_unit: str | None) -> str:
    """Compose the finest-available geographic key: ``iso3:geo_unit`` (e.g. ``"CHN:CN-HE"``)
    when a sub-national unit is set, else the bare country ``iso3``. Single source of truth for
    the key form — the country ``iso3`` is always recoverable via ``key.split(":", 1)[0]``."""
    return f"{iso3}:{geo_unit}" if geo_unit else iso3


@dataclass
class Location:
    lat: float
    lon: float
    country: str
    region: str
    iso3: str
    distance_to_other_iso3: dict[str, float] | None = None
    geo_unit: str | None = None  # sub-national division, ISO 3166-2 code e.g. "CN-HE"; None ⇒ country-level

    def __hash__(self):
        return hash((self.lat, self.lon))

    @property
    def geo_key(self) -> str:
        """Finest-available geographic key: ``iso3:geo_unit`` (e.g. ``"CHN:CN-HE"``) when a
        sub-national unit is set, else the bare country ``iso3``.

        Returns:
            ``iso3:geo_unit`` if ``geo_unit`` is set, otherwise ``iso3``. The country
            ``iso3`` is always recoverable via ``geo_key.split(":", 1)[0]``.
        """
        return compose_geo_key(self.iso3, self.geo_unit)

    def resolve(
        self,
        lookup: dict[str, _GeoValueT],
        *,
        what: str,
        valid_geo_keys: set[str] | None = None,
    ) -> _GeoValueT | None:
        """Resolve a value from a geo-keyed lookup, finest-available first.

        Tries the sub-national key (``self.geo_key``, e.g. ``"CHN:CN-HE"``) and falls back
        up to the country ``iso3``.

        Args:
            lookup: Mapping keyed by ``geo_key`` strings — sub-national (``iso3:code``)
                and/or bare country (``iso3``) keys.
            what: Short label for the quantity, used in log and error messages.
            valid_geo_keys: Optional set of recognised sub-national ``geo_key``s (from the
                populated ``geo_hierarchy``). When given and this location's ``geo_key`` is
                absent from it, a ``KeyError`` is raised.

        Returns:
            The sub-national value if present, else the country value, else ``None``.

        Raises:
            KeyError: If ``geo_unit`` is set but its ``geo_key`` is not in
                ``valid_geo_keys`` (typo / wrong level / unsupported unit).
        """
        logger = logging.getLogger(f"{__name__}.Location.resolve")
        if self.geo_unit is not None:
            if valid_geo_keys is not None and self.geo_key not in valid_geo_keys:
                raise KeyError(
                    f"{what}: location declares sub-national unit {self.geo_key!r}, which is "
                    f"not a recognised unit in geo_hierarchy — check the ISO 3166-2 code and level."
                )
            if self.geo_key in lookup:
                logger.debug(
                    "%s: resolved to sub-national unit %s (not country %s).",
                    what,
                    self.geo_key,
                    self.iso3,
                )
                return lookup[self.geo_key]
            logger.info(
                "%s: no sub-national value for %s; falling back to country %s.",
                what,
                self.geo_key,
                self.iso3,
            )
        return lookup.get(self.iso3)


@dataclass
class GeoDataPaths:
    """Configuration for all geospatial data paths"""

    # Base directories
    data_dir: Path
    atlite_dir: Path
    geo_plots_dir: Path

    # Specific data files
    terrain_nc_path: Path
    rail_distance_nc_path: Path
    railway_capex_csv_path: Path
    lcoh_capex_csv_path: Path
    regional_energy_prices_xlsx: Path
    countries_shapefile_dir: Path
    disputed_areas_shapefile_dir: Path

    # Output paths for different analyses
    baseload_power_sim_dir: Path
    static_layers_dir: (
        Path  # Directory for static geospatial layers (feasibility_mask.nc, rail_cost.nc, global_grid_with_iso3.nc)
    )
    landtype_percentage_path: Path

    # Natural Earth admin-1 (first-order divisions), used by the geo_hierarchy calibration.
    admin1_shapefile_dir: Optional[Path] = None

    # Combined (year, lat, lon) LCOE file promoted from a BOA run; None falls back to the
    # per-year files under baseload_power_sim_dir.
    baseload_lcoe_file: Optional[Path] = None


@dataclass
class PlotPaths:
    """Configuration for all plot output paths"""

    plots_dir: Optional[Path] = None
    pam_plots_dir: Optional[Path] = None
    geo_plots_dir: Optional[Path] = None
    tm_plots_dir: Optional[Path] = None


class InputCosts:
    def __init__(self, year, iso3, costs: dict[str, float], geo_unit: str | None = None) -> None:
        self.year = year
        self.iso3 = iso3
        self.geo_unit = geo_unit  # ISO 3166-2 code, e.g. "CN-HE"; None ⇒ country-level
        self.costs = costs
        self.name = f"{self.geo_key}_{self.year}"

    @property
    def geo_key(self) -> str:
        """Finest-available geographic key (mirrors ``Location.geo_key``)."""
        return compose_geo_key(self.iso3, self.geo_unit)

    def __repr__(self) -> str:
        return f"Country={self.iso3}, Year={self.year})"

    def __eq__(self, other):
        # Equal if they share the same name (geo_key_year) — so a country row and a province
        # row for the same iso3+year are distinct.
        return self.name == other.name


class WillingnessToPay:
    """
    Domain object representing willingness to pay for a specific commodity in a region or country.

    Willingness to pay can be specified either at the country level (using ISO3 codes)
    or at the region/trade bloc level (e.g., EU, NAFTA).
    """

    def __init__(self, region_or_iso3: str, commodity: str, value: float) -> None:
        """
        Initialize a WillingnessToPay entry.

        Args:
            region_or_iso3: Either an ISO3 country code (e.g., "USA") or a region/trade bloc name (e.g., "EU")
            commodity: The commodity name (e.g., "steel")
            value: The willingness to pay value (in USD/tonne or appropriate unit)
        """
        self.region_or_iso3 = region_or_iso3
        self.commodity = commodity
        self.value = value
        self.name = f"{self.region_or_iso3}_{self.commodity}"

    def __repr__(self) -> str:
        return f"WillingnessToPay(region_or_iso3={self.region_or_iso3}, commodity={self.commodity}, value={self.value})"

    def __eq__(self, other):
        return self.name == other.name


class CountryMapping:
    """
    Domain object representing a single country's mapping data across multiple data sources and regional classification systems.

    This class harmonizes country identifiers and regional groupings from different datasets used in the steel simulation,
    enabling seamless integration of renewable energy data (IRENA), plant data (GEM), demand scenarios (SSP),
    and trade modeling (World Steel, trade blocs).

    Attributes:
        country (str): Full country name.
        iso2 (str): 2-letter ISO country code (e.g., "US").
        iso3 (str): 3-letter ISO country code (e.g., "USA") - used as unique identifier.
        irena_name (str): Country name as used in IRENA renewable energy datasets.
        irena_region (str | None): IRENA regional classification.
        region_for_outputs (str): Primary regional grouping used for simulation outputs.
        ssp_region (str): Shared Socioeconomic Pathways region for demand scenarios.
        gem_country (str | None): Country name in Global Energy Monitor plant database.
        ws_region (str | None): World Steel regional classification.
        eu_region (str | None): European Union regional grouping.
        tiam_ucl_region (str): TIAM-UCL energy model region.
        **trade_bloc_memberships: Any additional boolean attributes representing trade bloc memberships
                                   are dynamically detected from the Excel sheet and added as attributes.

    Notes:
        - The iso3 code serves as the unique identifier and is used for all lookups throughout the simulation
        - Trade bloc memberships are dynamically detected from columns containing True/False values in the Excel sheet
        - These memberships are used to determine tariff applicability in the trade model
        - Different regional classifications allow data from multiple sources to be correctly mapped to countries
        - All country mapping data originates from the master Excel file
    """

    def __init__(
        self,
        *,
        country: str,
        iso2: str,
        iso3: str,
        irena_name: str,
        irena_region: str | None = None,
        region_for_outputs: str,
        ssp_region: str,
        gem_country: str | None = None,
        ws_region: str | None = None,
        eu_region: str | None = None,
        tiam_ucl_region: str,
        # Deprecated explicit parameters (kept for backwards compatibility)
        EU: bool = False,
        EFTA_EUCJ: bool = False,
        OECD: bool = False,
        NAFTA: bool = False,
        Mercosur: bool = False,
        ASEAN: bool = False,
        RCEP: bool = False,
        # Accept any additional boolean attributes dynamically
        **trade_bloc_memberships: bool,
    ) -> None:
        self.country = country
        self.iso2 = iso2
        self.iso3 = iso3
        self.irena_name = irena_name
        self.irena_region = irena_region
        self.region_for_outputs = region_for_outputs
        self.ssp_region = ssp_region
        self.gem_country = gem_country
        self.ws_region = ws_region
        self.eu_region = eu_region
        self.tiam_ucl_region = tiam_ucl_region

        # Set explicit parameters (for backwards compatibility)
        # These will be overridden by kwargs if provided
        self.EU = EU
        self.EFTA_EUCJ = EFTA_EUCJ
        self.OECD = OECD
        self.NAFTA = NAFTA
        self.Mercosur = Mercosur
        self.ASEAN = ASEAN
        self.RCEP = RCEP

        # Set any additional trade bloc memberships dynamically
        for attr_name, attr_value in trade_bloc_memberships.items():
            setattr(self, attr_name, attr_value)

    @property
    def id(self) -> str:
        """A unique identifier for the object, used for repository keys."""
        return self.iso3

    def __repr__(self) -> str:
        return f"CountryMapping(country={self.country}, iso3={self.iso3})"

    def __eq__(self, other) -> bool:
        if not isinstance(other, CountryMapping):
            return False
        return self.iso3 == other.iso3

    def __hash__(self) -> int:
        return hash(self.iso3)


@dataclass
class TechnologyEmissionFactors:
    """Represents the emissions associated with a specific technology in a specific country and year."""

    business_case: str
    technology: str
    boundary: str
    metallic_charge: str
    reductant: str
    direct_ghg_factor: float
    direct_with_biomass_ghg_factor: float
    indirect_ghg_factor: float


@dataclass
class CarbonBorderMechanism:
    """Represents a carbon border adjustment mechanism applied by a region to all non-members."""

    mechanism_name: str  # e.g., "CBAM", "EFTA/EUCJ", "OECD", "NAFTA", etc.
    applying_region_column: str  # Column name in CountryMapping (e.g., "EU", "EFTA_EUCJ", "OECD")
    start_year: int
    end_year: int | None = None  # None if it doesn't end

    def is_active(self, year: int) -> bool:
        """Check if this mechanism is active in a given year."""
        if year < self.start_year:
            return False
        if self.end_year is not None and year > self.end_year:
            return False
        return True

    def get_applying_region_countries(self, country_mappings: dict[str, CountryMapping]) -> set[str]:
        """Return all ISO3 codes of countries in the applying region."""
        countries = set()
        for iso3, mapping in country_mappings.items():
            if hasattr(mapping, self.applying_region_column):
                attr_value = getattr(mapping, self.applying_region_column, False)
                if attr_value:
                    countries.add(iso3)

        return countries


class RegionEmissivity:
    def __init__(
        self,
        iso3: str,
        country_name: str,
        scenario: str,
        grid_emissivity: dict[Year, dict[str, float]],
        coke_emissivity: dict[str, float],
        gas_emissivity: dict[str, float],
        geo_unit: str | None = None,
    ) -> None:
        self.iso3 = iso3
        self.geo_unit = geo_unit  # ISO 3166-2 code, e.g. "CN-HE"; None ⇒ country-level
        self.country_name = country_name
        self.scenario = scenario
        self.grid_emissivity = grid_emissivity
        self.coke_emissivity = coke_emissivity
        self.gas_emissivity = gas_emissivity
        self.id = f"{self.geo_key}_{self.scenario.lower().replace(' ', '_')}"

    @property
    def geo_key(self) -> str:
        """Finest-available geographic key (mirrors ``Location.geo_key``)."""
        return compose_geo_key(self.iso3, self.geo_unit)

    def __repr__(self) -> str:
        return f"RegionEmissivity: <{self.id}>"

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other) -> bool:
        return self.id == other.id


class SecondaryFeedstockConstraint:
    """
    Constraint for secondary feedstock allocations by region and year.

    Defines maximum allocation limits for specific secondary feedstocks (like bio-pci)
    in particular regions over time, used in trade optimization to limit total allocations.
    """

    def __init__(
        self, secondary_feedstock_name: str, region_iso3s: list[str], maximum_constraint_per_year: dict[Year, float]
    ):
        """
        Initialize a secondary feedstock constraint.

        Args:
            secondary_feedstock_name: Name of the secondary feedstock (e.g., "bio_pci")
            region_iso3s: List of ISO-3 country codes that define the region
            maximum_constraint_per_year: Dictionary mapping years to maximum allocation limits (in tonnes)

        Raises:
            ValueError: If feedstock name is empty, region list is empty, or constraints contain negative values
        """
        if not secondary_feedstock_name or not secondary_feedstock_name.strip():
            raise ValueError("Secondary feedstock name cannot be empty")

        if not region_iso3s:
            raise ValueError("Region ISO3 list cannot be empty")

        if not maximum_constraint_per_year:
            raise ValueError("Maximum constraint per year cannot be empty")

        # Validate all constraint values are non-negative
        for year, constraint in maximum_constraint_per_year.items():
            if constraint < 0:
                raise ValueError(f"Constraint value for year {year} cannot be negative: {constraint}")

        self.secondary_feedstock_name = secondary_feedstock_name.strip()
        self.region_iso3s = sorted(region_iso3s)  # Sort for consistent ordering
        self.maximum_constraint_per_year = maximum_constraint_per_year

    def get_constraint_for_year(self, year: Year) -> float | None:
        """Get the maximum constraint for a specific year, or None if not defined."""
        return self.maximum_constraint_per_year.get(year)

    def has_constraint_for_year(self, year: Year) -> bool:
        """Check if a constraint is defined for the given year."""
        return year in self.maximum_constraint_per_year

    def get_region_tuple(self) -> tuple[str, ...]:
        """Get the region as a sorted tuple of ISO3 codes (for use as dict key)."""
        return tuple(self.region_iso3s)

    def __repr__(self) -> str:
        years = sorted(self.maximum_constraint_per_year.keys())
        year_range = f"{years[0]}-{years[-1]}" if len(years) > 1 else str(years[0]) if years else "no years"
        return (
            f"SecondaryFeedstockConstraint("
            f"feedstock='{self.secondary_feedstock_name}', "
            f"region={self.region_iso3s}, "
            f"years={year_range})"
        )

    def __hash__(self) -> int:
        return hash((self.secondary_feedstock_name, self.get_region_tuple()))

    def __eq__(self, other) -> bool:
        if not isinstance(other, SecondaryFeedstockConstraint):
            return False
        return (
            self.secondary_feedstock_name == other.secondary_feedstock_name
            and self.region_iso3s == other.region_iso3s
            and self.maximum_constraint_per_year == other.maximum_constraint_per_year
        )


class AggregatedMetallicChargeConstraint:
    """
    Constraint for aggregated metallic charge materials using wildcard patterns.

    For example, a constraint on "scrap*" would aggregate all scrap types (scrap_low, scrap_mid, scrap_high)
    and apply minimum/maximum share constraints on their total contribution to a technology's product.
    """

    def __init__(
        self,
        technology_name: str,
        feedstock_pattern: str,
        minimum_share: float | None = None,
        maximum_share: float | None = None,
    ):
        """
        Initialize an aggregated metallic charge constraint.

        Args:
            technology_name: The technology this constraint applies to (e.g., "BF", "EAF")
            feedstock_pattern: The feedstock pattern without wildcard (e.g., "scrap" for "scrap*")
            minimum_share: Minimum share of this feedstock group in the product (0.0 to 1.0)
            maximum_share: Maximum share of this feedstock group in the product (0.0 to 1.0)
        """
        self.technology_name = technology_name
        self.feedstock_pattern = feedstock_pattern
        self.minimum_share = minimum_share
        self.maximum_share = maximum_share

        if minimum_share is not None and maximum_share is not None:
            if minimum_share > maximum_share:
                raise ValueError(
                    f"Minimum share ({minimum_share}) cannot be greater than maximum share ({maximum_share})"
                )

    def __repr__(self) -> str:
        constraints = []
        if self.minimum_share is not None:
            constraints.append(f"min={self.minimum_share:.2%}")
        if self.maximum_share is not None:
            constraints.append(f"max={self.maximum_share:.2%}")
        constraint_str = f" ({', '.join(constraints)})" if constraints else ""

        return f"AggregatedMetallicChargeConstraint({self.technology_name}, {self.feedstock_pattern}*{constraint_str})"

    def __hash__(self) -> int:
        return hash((self.technology_name, self.feedstock_pattern))

    def __eq__(self, other) -> bool:
        if not isinstance(other, AggregatedMetallicChargeConstraint):
            return False
        return self.technology_name == other.technology_name and self.feedstock_pattern == other.feedstock_pattern

    def matches_feedstock(self, feedstock_name: str) -> bool:
        """Check if a feedstock name matches this constraint's pattern."""
        return feedstock_name.startswith(self.feedstock_pattern)

    def has_minimum_constraint(self) -> bool:
        """Check if this constraint has a minimum share requirement."""
        return self.minimum_share is not None

    def has_maximum_constraint(self) -> bool:
        """Check if this constraint has a maximum share requirement."""
        return self.maximum_share is not None


# class TransportData:
#     def __init__(self, to_iso3: str, from_iso3: str, commodity_name: str, transport_emissions: float):
#         self.to_iso3 = to_iso3
#         self.from_iso3 = from_iso3
#         self.commodity_name = commodity_name
#         self.transport_emissions = transport_emissions


@dataclass
class TransportKPI:
    """Transport emission factors and costs between countries."""

    reporter_iso: str
    partner_iso: str
    commodity: str
    ghg_factor: float  # tCO2e/tonne.km
    transportation_cost: float  # USD/tonne
    updated_on: str

    def __hash__(self):
        return hash((self.reporter_iso, self.partner_iso, self.commodity))

    def __eq__(self, other):
        if not isinstance(other, TransportKPI):
            return False
        return (
            self.reporter_iso == other.reporter_iso
            and self.partner_iso == other.partner_iso
            and self.commodity == other.commodity
        )


@dataclass
class FallbackMaterialCost:
    """Domain object representing fallback material costs by country, technology, and year."""

    iso3: str
    technology: str
    metric: str
    unit: str
    costs_by_year: dict[Year, float]  # Year -> cost value

    def get_cost_for_year(self, year: Year) -> float | None:
        """Get cost for a specific year, return None if not available."""
        return self.costs_by_year.get(year)

    def get_available_years(self) -> list[Year]:
        """Get all years for which cost data is available."""
        return list(self.costs_by_year.keys())


@dataclass(eq=False)  # Disable automatic __eq__ generation
class BiomassAvailability:
    """Biomass availability by region and year."""

    region: str  # tiam-ucl_region from Excel
    country: str | None  # Can be None for regional data
    metric: str
    scenario: str
    unit: str
    year: Year
    availability: float  # Amount available

    def __hash__(self):
        return hash((self.region, self.country, self.year))

    def __eq__(self, other):
        if not isinstance(other, BiomassAvailability):
            return False
        return self.region == other.region and self.country == other.country and self.year == other.year


class HydrogenEfficiency:
    """Domain object for hydrogen efficiency data from master Excel."""

    def __init__(self, year: Year, efficiency: float):
        """
        Initialize hydrogen efficiency data.

        Args:
            year: Year for the efficiency value
            efficiency: Energy consumption in MWh/kg
        """
        self.year = year
        self.efficiency = efficiency

    def __repr__(self) -> str:
        return f"HydrogenEfficiency(year={self.year}, efficiency={self.efficiency})"

    def __hash__(self) -> int:
        return hash(self.year)

    def __eq__(self, other) -> bool:
        if not isinstance(other, HydrogenEfficiency):
            return False
        return self.year == other.year


class HydrogenCapexOpex:
    """Domain object for hydrogen CAPEX/OPEX component data from master Excel."""

    def __init__(self, country_code: str, values: dict[Year, float]):
        """
        Initialize hydrogen CAPEX/OPEX component data.

        Args:
            country_code: ISO-3 country code
            values: Dictionary mapping years to CAPEX/OPEX component values in USD/kg
        """
        self.country_code = country_code
        self.values = values

    def __repr__(self) -> str:
        return f"HydrogenCapexOpex(country_code={self.country_code}, years={list(self.values.keys())})"

    def __hash__(self) -> int:
        return hash(self.country_code)

    def __eq__(self, other) -> bool:
        if not isinstance(other, HydrogenCapexOpex):
            return False
        return self.country_code == other.country_code


@dataclass
class FOPEX:
    """
    Fixed Operating Expenditure (FOPEX) data for a specific country.

    Country name can be derived from CountryMappingService using the iso3 code.
    """

    iso3: str
    technology_fopex: dict[str, float]  # Technology -> USD/t values

    def __hash__(self):
        return hash(self.iso3)

    def __eq__(self, other):
        if not isinstance(other, FOPEX):
            return False
        return self.iso3 == other.iso3

    @property
    def id(self) -> str:
        """Unique identifier for repository storage."""
        return self.iso3

    def get_fopex_for_technology(self, technology: str) -> float | None:
        """Get FOPEX value for a specific technology."""
        return self.technology_fopex.get(technology.lower())


class PrimaryFeedstock:
    def __init__(self, metallic_charge: str, reductant: str, technology: str, **_ignored):
        self.name = f"{technology}_{metallic_charge}_{reductant}".lower()
        self.metallic_charge = metallic_charge
        self.reductant = reductant
        self.technology = technology.lower()
        self.required_quantity_per_ton_of_product: float | None = None
        self.secondary_feedstock: dict[str, float] = {}
        self.energy_requirements: dict[str, float] = {}
        self.carbon_inputs: dict[str, float] = {}
        self.carbon_outputs: dict[str, float] = {}
        self.maximum_share_in_product: float | None = None
        self.minimum_share_in_product: float | None = None
        self.outputs: dict = {}

    def __repr__(self) -> str:
        return self.name

    def add_secondary_feedstock(self, name: str, share: float) -> None:
        self.secondary_feedstock[name] = share

    def add_maximum_share_in_product(self, limit: float) -> None:
        self.maximum_share_in_product = limit

    def add_minimum_share_in_product(self, limit: float) -> None:
        self.minimum_share_in_product = limit

    def add_energy_requirement(self, vector_name: str, amount: float) -> None:
        if amount is None:
            raise ValueError(f"Energy requirement amount cannot be None for {vector_name}")
        self.energy_requirements[vector_name] = amount

    def add_carbon_input(self, vector_name: str, amount: float) -> None:
        """Add a carbon input vector (e.g. co2_inlet) to this feedstock.

        Args:
            vector_name: Normalised carbon input key.
            amount: Amount per tonne of product (original units preserved).
        """
        if amount is None:
            raise ValueError(f"Carbon input amount cannot be None for {vector_name}")
        self.carbon_inputs[vector_name] = amount

    def add_carbon_output(self, vector_name: str, amount: float) -> None:
        """Add a carbon output vector (e.g. co2_stored, co2_slip) to this feedstock.

        Args:
            vector_name: Normalised carbon output key.
            amount: Amount per tonne of product (original units preserved).
        """
        if amount is None:
            raise ValueError(f"Carbon output amount cannot be None for {vector_name}")
        self.carbon_outputs[vector_name] = amount

    def add_share_constraint(self, constraint_type: str, value: float) -> None:
        assert constraint_type in ["Maximum", "Minimum", "Minium"], f"Invalid constraint type {constraint_type}"
        if constraint_type == "Maximum":
            self.add_maximum_share_in_product(value)
        else:
            self.add_minimum_share_in_product(value)

    def add_output(self, name: str, amount: Volumes) -> None:
        self.outputs[name] = amount

    def get_primary_outputs(self, primary_products: list[str] | None = None) -> dict[str, Volumes]:
        from steelo.utilities.data_processing import normalize_product_name

        # Use provided primary_products or fall back to default for backward compatibility
        if primary_products is None:
            primary_products = [
                "steel",
                "iron",
                "hot_metal",
                "pig_iron",
                "liquid_steel",
                "dri_low",
                "dri_mid",
                "dri_high",
                "hbi_low",
                "hbi_mid",
                "hbi_high",
                "pig_iron",
                "liquid_steel",
                "io_low",
                "io_mid",
                "io_high",
                "scrap",
                "liquid_iron",
            ]

        # Normalize both the output keys and the primary_products for comparison
        normalized_primary_products = [normalize_product_name(p) for p in primary_products]
        return_dict = {
            normalize_product_name(k): v
            for k, v in self.outputs.items()
            if normalize_product_name(k) in normalized_primary_products
        }

        if "hot_metal" in return_dict and "pig_iron" not in return_dict:
            return_dict["pig_iron"] = return_dict["hot_metal"]
        return return_dict


def co2_stored_per_tonne_from_feedstocks(dynamic_business_case: list[PrimaryFeedstock] | None, reductant: str) -> float:
    """tCO2 stored per tonne of product for a technology's BOM.

    Filters feedstocks to the given reductant and returns the max co2_stored across the
    remaining feedstocks (metallic-charge alternatives). Falls back to max across all
    feedstocks when the reductant is absent — a conservative upper bound. Returns 0.0
    when the BOM is empty/None or carries no co2_stored vector.

    Reductant matching is case- and separator-sensitive by design (BOM-raw casing, e.g.
    ``Coke+PCI``). Any normalisation on either side would silently fall through to the
    all-reductants fallback and over-block.
    """
    if not dynamic_business_case:
        return 0.0
    matching = [pf for pf in dynamic_business_case if pf.reductant == reductant]
    if not matching:
        matching = dynamic_business_case
    return max((pf.carbon_outputs.get("co2_stored", 0.0) for pf in matching), default=0.0)


@dataclass
class Technology:
    name: str
    product: str
    technology_readiness_level: int | None = None
    process_emissions: float | None = None
    dynamic_business_case: list[PrimaryFeedstock] | None = None
    energy_consumption: float | None = None
    bill_of_materials: dict[str, dict[str, dict[str, Any]]] | None = None
    lcop: float | None = None
    capex_type: str | None = None
    capex: float | None = None
    capex_no_subsidy: float | None = None

    def __post_init__(self):
        if self.capex is None:
            self.capex = 0.0
        if self.capex_type is None:
            self.capex_type = "brownfield"

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        # Technologies are equal if they have the same name
        return self.name == other.name

    def __repr__(self) -> str:
        return f"Technology: <{self.name}>"

    def add_feedstock_to_dynamic_business_case(self, primary_feedstock: PrimaryFeedstock) -> None:
        if self.dynamic_business_case is None:
            self.dynamic_business_case = []
        self.dynamic_business_case.append(primary_feedstock)

    def co2_stored_per_tonne_product(self, reductant: str) -> float:
        return co2_stored_per_tonne_from_feedstocks(self.dynamic_business_case, reductant)

    def set_product(self, technology_to_product_map: dict[str, str], primary_products: list[str] | None = None) -> None:
        """
        Set the product for the technology based on the provided mappings (technology_to_product_map from Environment)
        """
        if not self.dynamic_business_case:
            raise TypeError(f"Dynamic_business_case cannot be None when setting product. Technology name: {self.name}")

        try:
            primary_products_found = [
                product.lower()
                for feedstock in self.dynamic_business_case
                for product in feedstock.outputs.keys()
                if primary_products and product.lower() in primary_products
            ]
        except TypeError:
            primary_products_found = None
        if not primary_products_found:
            try:
                primary_products_found = [technology_to_product_map[self.name]]
            except KeyError:
                raise ValueError(f"No primary products found for technology {self.name} in technology_to_product_map")
        if primary_products_found is None:
            raise ValueError(
                f"No primary products found for technology {self.name} after checking dynamic_business_case and technology_to_product_map."
            )

        self.primary_products = primary_products_found

        if (
            Commodities.STEEL.value in primary_products_found
            or Commodities.LIQUID_STEEL.value in primary_products_found
        ):
            self.product = Commodities.STEEL.value


@dataclass
class ProductionThreshold:
    """Production thresholds for the utilization rate of a furnace group."""

    low: float = 0.1
    high: float = 0.95


class FurnaceGroup:
    def __init__(
        self,
        *,
        furnace_group_id: str,
        capacity: Volumes,
        status: str,
        last_renovation_date: date | None,
        technology: Technology,
        historical_production: dict[Year, Volumes],
        utilization_rate: float,
        lifetime: PointInTime,
        production_threshold: ProductionThreshold = ProductionThreshold(),
        equity_share: float = 0.2,  # Default ONLY for data loading/existing plants - new business opportunities MUST pass from config
        cost_of_debt: float = 0.05,
        cost_of_debt_no_subsidy: float = 0.05,
        emissions: dict[str, dict[str, float]] | None = {},
        emissions_factor: dict[str, dict[str, float]] | None = None,
        historical_npv_business_opportunities: Optional[dict[int, float]] = None,
        bill_of_materials: dict[str, dict[str, dict[str, float]]] | None = None,
        energy_cost_dict: dict = {},
        chosen_reductant: str = "",
        energy_vopex_by_input: dict[str, float] = {},
        energy_vopex_breakdown_by_input: dict[str, dict[str, float]] = {},
        energy_vopex_by_carrier: dict[str, float] = {},
        tech_unit_fopex: float = 0.0,
        balance: float = 0.0,
        historic_balance: float = 0.0,
        allocated_volumes: float = 0.0,
        carbon_costs_for_emissions: float = 0.0,
        railway_cost: float = 0.0,
        created_by_PAM: bool = False,
        legacy_debt_schedule: list[float] | None = None,
        cost_breakdown_keys: list[str] | None = None,
        carbon_breakdown_keys: list[str] | None = None,
        disposal_cost_outputs: frozenset[str] | None = None,
        output_shares: dict[str, float] | None = None,
    ) -> None:
        self.furnace_group_id = furnace_group_id
        self.capacity = capacity
        self.status = status
        self.last_renovation_date = last_renovation_date
        self.technology = technology
        self.historical_production = historical_production
        self.utilization_rate = utilization_rate
        self.lifetime = lifetime
        self.production_threshold = production_threshold
        self.created_by_PAM = created_by_PAM
        self.bill_of_materials = bill_of_materials
        self.chosen_reductant = chosen_reductant
        self.cost_breakdown_keys = cost_breakdown_keys
        self.carbon_breakdown_keys = carbon_breakdown_keys
        self.allocated_volumes = allocated_volumes
        self.disposal_cost_outputs = disposal_cost_outputs
        # Metallic charge -> share of product for the candidate BOM; set only for
        # PAM-created business opportunities (feeds the re-check's score series)
        self.output_shares = output_shares

        # Future technology switch (accounts for construction time while operating with old technology)
        self.future_switch_cmd: Optional[commands.ChangeFurnaceGroupTechnology] = None
        self.future_switch_year: Optional[int] = None

        # Economic variables
        self.equity_share = equity_share
        self.cost_of_debt = cost_of_debt
        self.cost_of_debt_no_subsidy = cost_of_debt_no_subsidy  # Initially the same, can be updated later
        self.balance = balance  # furnaces are initiated with a balance of 0
        self.historic_balance = historic_balance
        self.historical_npv_business_opportunities = historical_npv_business_opportunities
        self.railway_cost = railway_cost
        self.legacy_debt_schedule = legacy_debt_schedule or []  # Track debt from previous tech when switching
        self.has_hot_metal_access = False

        self.applied_subsidies: dict[str, list[Subsidy]] = {
            "capex": [],
            "opex": [],
            "debt": [],
        }
        # Original energy prices before subsidies (for verification)
        self.energy_costs_no_subsidy: dict[str, float] = {}
        # Energy costs used for output revenue (by-product sales)
        self.output_energy_costs: dict[str, float] = {}
        self.set_energy_costs(**energy_cost_dict)  # must follow output_energy_costs init

        self.energy_vopex_by_input = energy_vopex_by_input
        self.energy_vopex_breakdown_by_input = energy_vopex_breakdown_by_input
        self.energy_vopex_by_carrier = energy_vopex_by_carrier
        self.tech_unit_fopex = tech_unit_fopex

        self.grid_emissivity: float | None = None

        # Emissions and carbon
        self.emissions_factor = emissions_factor
        self.technology_emission_factors: list[TechnologyEmissionFactors] = []
        self.emissions = emissions
        self.transport_emissions = 0.0

        # Initialize _carbon_cost from carbon_costs_for_emissions if provided
        if carbon_costs_for_emissions is not None and carbon_costs_for_emissions > 0:
            # Create initial CarbonCost object from legacy parameter
            self._carbon_cost = CarbonCost(
                cost_per_unit=carbon_costs_for_emissions,
                total_cost=carbon_costs_for_emissions * allocated_volumes if allocated_volumes else 0,
                emissions_per_unit=0.0,  # Will be set properly by set_carbon_costs_for_emissions
                carbon_price=0.0,  # Will be set properly by set_carbon_costs_for_emissions
                production=allocated_volumes or 0,
            )
        else:
            self._carbon_cost = CarbonCost.zero(production=allocated_volumes or 0)

        self.set_is_first_renovation_cycle()

    def set_energy_costs(self, **costs: float) -> None:
        """
        Set energy costs dictionary for the furnace group.

        All costs come from input costs data (master Excel "Input costs" tab) after unit
        conversion by excel_reader.read_regional_input_prices_from_master_excel().
        Keys are normalised to lowercase with underscores.

        Expected costs (post-conversion units):
            Energy carriers (USD/kWh):
                - electricity (converted from USD/MWh in Excel)
                - natural_gas, coal, bf_gas, bof_gas, cog, steam (converted from USD/GJ in Excel)

            Material carriers (USD/t):
                - coke, pci, bio_pci, coking_coal (converted from USD/kg in Excel)
                - hydrogen (converted from USD/kg; set via update_furnace_hydrogen_costs for capped prices)
                - ccu_products, co2_stored (unchanged from USD/t in Excel)

        Args:
            **costs: Energy costs as keyword arguments. Keys normalised to lowercase + underscores.

        Note:
            Hydrogen costs are typically set separately via Plant.update_furnace_hydrogen_costs()
            to use capped country-level prices calculated from LCOH.
            The incoming prices are unsubsidised, so energy_costs_no_subsidy is refreshed too.
        """
        logger = logging.getLogger(f"{__name__}.set_energy_costs")
        # Store costs with normalized keys to ensure downstream lookups succeed.
        # Physical carriers: abs() ensures consumption is always a positive cost.
        # Carbon carriers (co2_*): sign preserved (positive = cost, negative = credit).
        energy_costs: dict[str, float] = {}
        for raw_key, price in costs.items():
            normalized_key = normalize_name(raw_key)
            if not normalized_key.startswith("co2"):
                price = abs(price)
            energy_costs[normalized_key] = price
            # Preserve original key for compatibility where callers still expect legacy naming
            if normalized_key != raw_key:
                energy_costs[raw_key] = price

        self.energy_costs = energy_costs
        self.output_energy_costs = energy_costs.copy()
        # Full copy (not {}): update_furnace_hydrogen_costs writes a lone "hydrogen" key into
        # this dict, so it must never hold a partial snapshot
        self.energy_costs_no_subsidy = energy_costs.copy()
        logger.debug(
            "[ENERGY COSTS] FG %s: set %d carriers, sample: %s",
            getattr(self, "furnace_group_id", "?"),
            len({normalize_name(k) for k in energy_costs}),
            {k: f"${v:.6f}" for k, v in list(energy_costs.items())[:5]},
        )

    def set_subsidised_energy_costs(
        self,
        input_costs: dict[str, float],
        output_costs: dict[str, float],
        no_subsidy_prices: dict[str, float],
        energy_subsidies: dict[str, list["Subsidy"]],
    ) -> None:
        """Set energy costs with subsidies applied and track original prices.

        A subsidy simultaneously reduces input cost and increases output profit:
        - ``input_costs[carrier] = max(0, price - subsidy)``
        - ``output_costs[carrier] = price + subsidy``

        Args:
            input_costs: Subsidised prices for consumption (BOM energy, VOPEX).
            output_costs: Subsidised prices for by-product revenue.
            no_subsidy_prices: Original prices for all subsidised carriers.
            energy_subsidies: Applied subsidies per carrier {carrier: [Subsidy, ...]}.
        """
        logger = logging.getLogger(f"{__name__}.set_subsidised_energy_costs")
        self.energy_costs = input_costs
        self.output_energy_costs = output_costs
        self.energy_costs_no_subsidy = no_subsidy_prices
        for carrier, subs in energy_subsidies.items():
            self.applied_subsidies[carrier] = subs
        diffs = [
            f"{c} in=${input_costs.get(c, no_subsidy_prices[c]):.4f}"
            f" out=${output_costs.get(c, no_subsidy_prices[c]):.4f}"
            f" (base=${no_subsidy_prices[c]:.4f})"
            for c in sorted(no_subsidy_prices)
        ]
        if diffs:
            logger.debug("[ENERGY SUBS] FG set: %s", ", ".join(diffs))

    def __repr__(self) -> str:
        return f"FurnaceGroup: <{self.furnace_group_id}>"

    def __eq__(self, other) -> bool:
        return self.furnace_group_id == other.furnace_group_id

    def __hash__(self):
        return hash(self.furnace_group_id)

    def set_cost_of_debt(self, cost_of_debt: float, cost_of_debt_no_subsidy: float) -> None:
        """
        Set the subsidized and unsubsidized cost of debt for the furnace group.

        Args:
            cost_of_debt (float): Interest rate after subsidies are applied (e.g., 0.05 for 5%).
            cost_of_debt_no_subsidy (float): Baseline interest rate before subsidies (e.g., 0.07 for 7%).

        Side Effects:
            - Updates self.cost_of_debt with the subsidized rate.
            - Updates self.cost_of_debt_no_subsidy with the baseline rate.
        """
        # Set subsidized interest rate
        self.cost_of_debt = cost_of_debt
        # Set baseline interest rate without subsidies
        self.cost_of_debt_no_subsidy = cost_of_debt_no_subsidy

    def report_bill_of_materials(self):
        return {
            item_name: item_dict["demand"] * item_dict["unit_cost"]["Value"] * self.production
            for bom_group, items in self.technology.bill_of_materials.items()
            for item_name, item_dict in items.items()
        }

    def get_furnace_plant_id(self) -> str:
        """
        Sets the plant id of the furnace group.

        Returns:
            Plant ID extracted from furnace group ID.

        Note: The first furnace group in the plant has the same id as the plant. Afterwards, furnace groups are
        numbered sequentially with an underscore. Example: P000000000001, P000000000001_2, etc. Sintering furnace
        groups use the format P000000000001_Sintering.
        """
        return self.furnace_group_id.split("_")[0]

    def set_technology_capex(self, capex: float, capex_no_subsidy: float) -> None:
        """
        Set the capital expenditure for the furnace group's technology.

        Args:
            capex (float): Capital expenditure after subsidies are applied (USD/tonne).
            capex_no_subsidy (float): Baseline capital expenditure before subsidies (USD/tonne).

        Side Effects:
            - Updates self.technology.capex with the subsidized CAPEX.
            - Updates self.technology.capex_no_subsidy with the baseline CAPEX.
        """
        # Set subsidized capital expenditure
        self.technology.capex = capex
        # Set baseline capital expenditure without subsidies
        self.technology.capex_no_subsidy = capex_no_subsidy

    @property
    def is_ccs_or_ccu(self) -> bool:
        name = self.technology.name.lower()
        return "ccs" in name or "ccu" in name

    @property
    def produces_hot_metal(self) -> bool:
        """Whether the technology's bill of materials outputs hot metal, the feedstock behind a BOF's minimum share."""
        return any(
            normalize_name(output) == Commodities.HOT_METAL.value
            for feedstock in self.technology.dynamic_business_case or []
            for output in feedstock.outputs
        )

    @property
    def effective_primary_feedstocks(self) -> list[PrimaryFeedstock]:
        """
        Returns the effective primary feedstock for the furnace group.
        This is a list of PrimaryFeedstock objects that are used in the dynamic business case.
        """
        if self.technology.dynamic_business_case is None:
            return []

        if (
            not (hasattr(self, "chosen_reductant") and self.chosen_reductant)
            or self.chosen_reductant is None
            or (isinstance(self.chosen_reductant, float) and math.isnan(self.chosen_reductant))
            or (isinstance(self.chosen_reductant, str) and self.chosen_reductant == "None")
        ):
            return self.technology.dynamic_business_case
        return [
            feedstock
            for feedstock in self.technology.dynamic_business_case
            if feedstock.reductant == self.chosen_reductant
        ]

    def set_carbon_costs_for_emissions(
        self, carbon_price: float, chosen_emissions_boundary_for_carbon_costs: str
    ) -> None:
        """
        Calculate and set carbon costs for furnace group emissions.

        Uses the CarbonCostService to compute total and per-unit carbon costs based on
        the furnace group's emissions, production volume, and the specified carbon price.

        Args:
            carbon_price (float): Carbon price in USD per tonne CO2e.
            chosen_emissions_boundary_for_carbon_costs (str): Emissions boundary to use for
                cost calculation (e.g., "responsible_steel").

        Side Effects:
            Sets self._carbon_cost with a CarbonCost object containing:
                - cost_per_unit: USD per tonne of product
                - total_cost: Total USD for production volume
                - emissions_per_unit: tCO2e per tonne of product
                - carbon_price: USD per tCO2e
                - production: Tonnes produced (allocated_volumes)
        """
        # Use CarbonCostService to calculate carbon costs
        service = CarbonCostService(chosen_emissions_boundary_for_carbon_costs)
        self._carbon_cost = service.calculate_carbon_cost(
            emissions_total=self.emissions, carbon_price=carbon_price, production=self.allocated_volumes
        )

    # def set_regional_emsissivity(self, grid_emissivity: dict[str, float], fossil_emissivity: dict[str, float]) -> None:
    #     """
    #     Sets the grid emissivity for the furnace group. by updating the emissions factors for given energy vectors

    #     Args:

    #     Returns

    #     Side effects:
    #     """
    #     # Initialize emissions_factor if None
    #     if self.emissions_factor is None:
    #         self.emissions_factor = {}

    #     # Merge grid and fossil emissivities into a single dict
    #     combined_emissivities = {}
    #     combined_emissivities.update(fossil_emissivity)
    #     combined_emissivities.update(grid_emissivity)

    #     # Store as nested dict to match the type: dict[str, dict[str, float]]
    #     # Using "emissivity" as the inner key
    #     for energy_type, emissivity_value in combined_emissivities.items():
    #         self.emissions_factor[energy_type] = {"emissivity": emissivity_value}

    # def set_grid_emsissivity(self, grid_emission: dict[str, float]) -> None:
    #     """
    #     Sets the grid emissivity for the furnace group.
    #     """
    #     self.grid_emissivity = grid_emission

    @property
    def emissions_per_unit(self) -> dict[str, dict[str, float]]:
        """
        Calculate emissions per unit of production from total emissions.

        Returns:
            dict[str, dict[str, float]]: Dictionary with emissions per tonne of product for each boundary and scope.
        """
        if not self.emissions or not self.allocated_volumes or self.allocated_volumes <= 0:
            return {}

        per_unit_emissions: dict[str, dict[str, float]] = {}
        for boundary, scope_data in self.emissions.items():
            per_unit_emissions[boundary] = {}
            for scope, value in scope_data.items():
                per_unit_emissions[boundary][scope] = value / self.allocated_volumes

        return per_unit_emissions

    @property
    def carbon_cost_per_unit(self) -> float:
        """
        Return the carbon cost per unit of production.

        Returns:
            float: Carbon cost in USD per tonne of product, or 0.0 if no carbon cost is set or utilization rate is 0.
        """
        if self._carbon_cost is None:
            return 0.0
        if self.utilization_rate == 0:
            return 0.0
        return self._carbon_cost.cost_per_unit

    @property
    def carbon_cost(self) -> Optional[CarbonCost]:
        """
        Return the CarbonCost value object with detailed carbon cost information.

        Returns:
            Optional[CarbonCost]: CarbonCost object containing:
                - cost_per_unit ($ per tonne of product)
                - total_cost (total $ for production volume)
                - emissions_per_unit (tCO2/t)
                - carbon_price ($/tCO2)
                - production (tonnes)
        """
        return self._carbon_cost

    @property
    def co2_slip_carbon_cost_contribution(self) -> float:
        """Carbon cost contribution from CO2 slip emissions (USD/t product).

        Returns:
            float: ``co2_slip (tCO2/t) * carbon_price ($/tCO2)``, or 0.0 when
                no carbon cost is set, utilisation is zero, or no CO2 slip exists.
        """
        if self._carbon_cost is None or self.utilization_rate == 0:
            return 0.0
        carbon_price = self._carbon_cost.carbon_price
        if carbon_price == 0.0:
            return 0.0
        total_co2_slip = sum(feed.get("co2_slip", 0.0) for feed in self.carbon_breakdown_by_feedstock.values())
        return total_co2_slip * carbon_price

    # def is_suitable_for_green_steel(
    #     self, chosen_emissions_boundary_for_carbon_costs: str, green_steel_emissions_limit: float
    # ) -> bool:
    #     """
    #     Returns True if the furnace group is suitable for green steel production.
    #     This is determined by the emissions and the green steel emissions limit.
    #     """
    #     if self.technology.product != Commodities.STEEL.value:
    #         return False
    #     if (
    #         self.emissions is None
    #         or chosen_emissions_boundary_for_carbon_costs not in self.emissions
    #         or "indirect_ghg" not in self.emissions[chosen_emissions_boundary_for_carbon_costs]
    #     ):
    #         return False
    #     fg_emissions = (
    #         self.emissions.get(chosen_emissions_boundary_for_carbon_costs, {})["indirect_ghg"]
    #         + self.transport_emissions
    #     )

    #     return fg_emissions <= green_steel_emissions_limit

    @property
    def unit_fopex(self) -> float:
        """
        Calculate fixed operating expenditure per unit of production.

        Converts technology-level fixed OPEX (per unit capacity) to production-level fixed OPEX
        by adjusting for utilization rate. At 0% utilization, returns capacity-based FOPEX directly.

        Returns:
            float: Fixed OPEX per unit of production (USD/t).

        Note: Lower utilization increases unit FOPEX since fixed costs are spread over less production.
        """
        # If idle, return capacity-based fixed OPEX
        if self.utilization_rate <= 0:
            return self.tech_unit_fopex

        threshold_low = self.production_threshold.low
        if threshold_low is not None and threshold_low > 0:
            effective_utilisation = max(self.utilization_rate, threshold_low)
        else:
            effective_utilisation = self.utilization_rate

        # Scale fixed OPEX by the effective utilisation (capped by the minimum threshold)
        return self.tech_unit_fopex / effective_utilisation

    @property
    def unit_vopex(self) -> float:
        """
        Calculate unit variable operating expenditure based on bill of materials.

        Computes variable OPEX from materials and energy consumption defined in the bill of materials.
        Performs validation to ensure BOM structure is valid, initializing to empty if missing.

        Returns:
            float: Variable OPEX per unit of production (USD/t).

        Side Effects:
            - May initialize or repair self.bill_of_materials if invalid or missing.

        Note: Excludes carbon costs and debt repayment (those are calculated separately).
        """
        logger = logging.getLogger(f"{__name__}.FurnaceGroup.unit_vopex")
        from .calculate_costs import calculate_variable_opex

        # Validate and repair BOM structure if needed
        if self.bill_of_materials:
            # Check if BOM is a dict
            if not isinstance(self.bill_of_materials, dict):
                logger.error(
                    f"[UNIT VOPEX]: BOM is not a dict! Type: {type(self.bill_of_materials)}, Value: {self.bill_of_materials}"
                )
                if self.utilization_rate > 0:
                    raise ValueError("BOM must exist for FG with utilization rate > 0")
                else:
                    # Initialize empty BOM to prevent crash
                    self.bill_of_materials = {"materials": {}, "energy": {}}
            # Check if BOM has required keys
            elif "materials" not in self.bill_of_materials or "energy" not in self.bill_of_materials:
                logger.error(
                    f"[UNIT VOPEX]: BOM missing required keys! Keys: {list(self.bill_of_materials.keys())}, Value: {self.bill_of_materials}"
                )
                # Add missing keys to prevent crash
                if "materials" not in self.bill_of_materials:
                    self.bill_of_materials["materials"] = {}
                if "energy" not in self.bill_of_materials:
                    self.bill_of_materials["energy"] = {}
        else:
            # Initialize empty BOM if None
            self.bill_of_materials = {"materials": {}, "energy": {}}

        materials = self.bill_of_materials.get("materials", {})
        energy = self.bill_of_materials.get("energy", {})
        vopex = calculate_variable_opex(materials, energy)
        logger.debug(
            "[VOPEX] FG %s (%s): vopex=$%.4f/t, materials=%d, energy=%d",
            self.furnace_group_id,
            getattr(self.technology, "name", "?"),
            vopex,
            len(materials),
            len(energy),
        )

        # # Record a simple per-tonne breakdown for diagnostics
        # product_volume: float | None = None
        # for container in (materials, energy):
        #     for entry in container.values():
        #         pv = entry.get("product_volume")
        #         if pv and float(pv) > 0:
        #             product_volume = float(pv)
        #             break
        #     if product_volume:
        #         break
        # if product_volume is None or product_volume <= 0:
        #     total_demand = sum(float(entry.get("demand") or 0.0) for entry in materials.values())
        #     product_volume = total_demand if total_demand > 0 else None

        # def _per_t(total_cost: float) -> float:
        #     if not product_volume or product_volume <= 0:
        #         return 0.0
        #     return total_cost / product_volume

        # material_total_cost = sum(float(entry.get("total_cost") or 0.0) for entry in materials.values())
        # energy_total_cost = sum(float(entry.get("total_cost") or 0.0) for entry in energy.values())
        # material_per_t = _per_t(material_total_cost)
        # energy_per_t = _per_t(energy_total_cost)
        # other_per_t = vopex - (material_per_t + energy_per_t)

        # self.vopex_breakdown = {
        #     "materials_per_t": material_per_t,
        #     "energy_per_t": energy_per_t,
        #     "other_per_t": other_per_t,
        #     "product_volume_basis": product_volume or 0.0,
        # }

        return vopex

    @property
    def unit_total_opex(self) -> float:
        """
        Calculate unit total operating expenditure with subsidies applied.

        Returns:
            float: Total OPEX per unit of production after applying OPEX subsidies (USD/t).

        Note: Excludes carbon costs and debt repayment (those are separate cost components).
        """
        from .calculate_costs import calculate_opex_with_subsidies

        # Apply OPEX subsidies to baseline total OPEX
        return calculate_opex_with_subsidies(
            opex=self.unit_total_opex_no_subsidy,
            opex_subsidies=self.applied_subsidies["opex"],
        )

    @property
    def unit_total_opex_no_subsidy(self) -> float:
        """
        Calculate unit total operating expenditure without subsidies.

        Combines fixed OPEX and variable OPEX into total baseline OPEX before any subsidies are applied.
        Validates that bill of materials exists and has required materials data for producing furnaces.

        Returns:
            float: Total OPEX per unit of production before subsidies (USD/t). Returns 0.0 if idle.

        Raises:
            ValueError: If bill of materials is missing or lacks materials for a producing furnace.

        Note: Excludes carbon costs and debt repayment (those are separate cost components).
        """
        logger = logging.getLogger(f"{__name__}.FurnaceGroup.unit_total_opex_no_subsidy")
        from .calculate_costs import calculate_unit_total_opex

        # Idle furnaces have no per-unit OPEX
        if self.utilization_rate <= 0:
            return 0.0

        # Validate BOM exists for producing furnaces
        if not self.bill_of_materials:
            raise ValueError(
                f"Bill of materials must exist for FG with utilization rate > 0. FurnaceGroup ID: {self.furnace_group_id}, technology: {getattr(self.technology, 'name', '?')}"
            )
        # Validate materials exist in BOM
        elif not ("materials" in self.bill_of_materials and self.bill_of_materials["materials"]):
            # Get diagnostic info
            has_feedstocks = hasattr(self, "effective_primary_feedstocks")
            feedstock_count = (
                len(self.effective_primary_feedstocks) if has_feedstocks and self.effective_primary_feedstocks else 0
            )

            logger.error(
                f"FurnaceGroup {self.furnace_group_id} of technology {self.technology.name} has no materials defined in its bill of materials. "
                f"Production: {self.production}, "
                f"Utilization: {self.utilization_rate}, "
                f"Capacity: {self.capacity}, "
                f"Has effective_primary_feedstocks: {has_feedstocks}, "
                f"Feedstock count: {feedstock_count}, "
                f"BOM: {self.bill_of_materials}"
            )
            raise ValueError("Materials must exist in BOM for FG with utilization rate > 0")
        else:
            # Combine fixed and variable OPEX
            return calculate_unit_total_opex(
                unit_vopex=self.unit_vopex,
                unit_fopex=self.unit_fopex,
                utilization_rate=self.utilization_rate,
            )

    def set_is_first_renovation_cycle(self) -> None:
        """
        Determine if furnace group is in its first renovation cycle and set CAPEX type accordingly.

        First Cycle Definition:
        ----------------------
        A furnace group is in its first renovation cycle if:
            plant_age < plant_lifetime

        Where plant_age is calculated from:
        - last_renovation_date.year (if available): The original plant commissioning year
        - OR lifetime.start (if no last_renovation_date): The cycle start year

        Economic Impact:
        ---------------
        - First cycle (plant_age < plant_lifetime): Uses GREENFIELD CAPEX
            Example: 10-year-old plant with L=20 → greenfield, higher capital costs
        - Subsequent cycles (plant_age >= plant_lifetime): Uses BROWNFIELD CAPEX
            Example: 116-year-old plant with L=20 → brownfield, ~30% of greenfield costs

        The brownfield CAPEX reflects that renovations can reuse existing infrastructure
        (land, some equipment, permits, etc.), making them cheaper than building from scratch.

        See renovation cycle logic details in PointInTime.remaining_number_of_years property.

        Side Effects:
            Sets self.is_first_renovation_cycle (bool)
            Sets self.technology.capex_type to "greenfield" or "brownfield"
        """
        # If we have a last_renovation_date, use it to determine the original start
        if self.last_renovation_date:
            original_start_year = self.last_renovation_date.year
            plant_age = self.lifetime.current - original_start_year
            self.is_first_renovation_cycle = plant_age < self.lifetime.plant_lifetime
        else:
            # Otherwise, check if the lifetime start is close to current
            # (indicates this is the first cycle)
            years_since_cycle_start = self.lifetime.current - self.lifetime.start
            self.is_first_renovation_cycle = years_since_cycle_start < self.lifetime.plant_lifetime

        if self.is_first_renovation_cycle:
            self.technology.capex_type = "greenfield"
        else:
            self.technology.capex_type = "brownfield"

    def set_capex_renovation_share_for_current_tech(self, capex_renovation_share: dict[str, float]) -> None:
        """
        Set the CAPEX renovation share for the current technology to reduce costs during renovation.

        Renovating an existing technology is cheaper than building new (greenfield), so this share
        is applied to adjust CAPEX calculations accordingly.

        Args:
            capex_renovation_share (dict[str, float]): Mapping of technology names to renovation cost shares.

        Raises:
            ValueError: If no CAPEX renovation share is defined for the current technology.

        Side Effects:
            Sets the capex_renovation_share attribute of this furnace group.
        """
        # Look up renovation share for current technology
        tech_name = self.technology.name
        renovation_share = capex_renovation_share.get(tech_name)

        # Ensure renovation share is defined for this technology
        if renovation_share is None:
            raise ValueError(f"No CAPEX renovation share defined for technology {tech_name}")

        self.capex_renovation_share = renovation_share

    @property
    def total_investment(self) -> float:
        """
        Calculate total investment (CAPEX) for this furnace group.

        Formula: capacity (tonnes/year) × CAPEX ($/tonne)

        The CAPEX per tonne varies based on:
        - Technology type (BF, EAF, DRI+EAF, etc.)
        - Renovation cycle: greenfield vs brownfield (see set_is_first_renovation_cycle)
        - Region (capex_reduction_ratio adjustments)

        This total investment is used to calculate:
        - outstanding_debt (portion financed by debt, scaled by remaining years)
        - debt_repayment_per_year (annual debt service payments)
        - NPV calculations for technology investment decisions

        Returns:
            float: Total investment in $ for this furnace group
        """
        capex = self.technology.capex if self.technology.capex is not None else 0.0
        return self.capacity * capex

    @property
    def outstanding_debt(self) -> float:
        """
        Calculate outstanding debt at simulation start, properly accounting for renovation cycles.

        Debt Calculation Logic:
        ----------------------
        1. Total debt = total_investment × (1 - equity_share)
            - equity_share: portion financed by equity (typically 20-30%)
            - Remaining portion is financed by debt

        2. Debt scaling for renovation cycles:
            - Outstanding debt is scaled linearly by: (remaining_years / plant_lifetime)
            - This reflects that old plants have paid down more of their original debt

        Example (plant_lifetime=20, equity_share=0.2, total_investment=$1B):
        - Total debt = $1B × 0.8 = $800M
        - Plant with 4 years remaining: $800M × (4/20) = $160M outstanding
        - Plant with 15 years remaining: $800M × (15/20) = $600M outstanding
        - Plant at renovation boundary (0 years): $0 outstanding

        This linear scaling assumes debt is amortized evenly over the renovation cycle.
        See remaining_number_of_years for renovation cycle logic details.

        Returns:
            float: Outstanding debt in $ at simulation start
        """
        # Total debt for the investment
        total_debt = self.total_investment * (1 - self.equity_share)

        # Scale based on remaining years in renovation cycle
        remaining_years = self.lifetime.remaining_number_of_years
        if remaining_years <= 0:
            return 0.0  # No debt if at renovation boundary

        # Linear scaling: debt decreases linearly over the renovation cycle
        debt_scaling = remaining_years / self.lifetime.plant_lifetime
        return total_debt * debt_scaling

    @property
    def debt_repayment_per_year(self) -> list[float]:
        """
        Returns the total debt repayment schedule for all remaining years.

        This property combines debt from the current technology with any legacy debt from
        previous technology switches. When a furnace group switches technologies before
        completing its debt repayment (e.g., switching from BF to DRI+EAF after 10 years
        of a 20-year loan), it must continue servicing the old debt while taking on new debt.

        Debt Accumulation Logic:
        - Current technology debt: Calculated based on the new technology's CAPEX and loan terms
        - Legacy debt: Remaining payments from previous technologies stored in legacy_debt_schedule
        - Combined debt: Sum of current + legacy payments for overlapping years

        Example Scenario (simplified):
        - Year 0: Install BF with $400M debt, $32M/year for 20 years
        - Year 10: Switch to DRI+EAF with $320M debt, $26M/year for 20 years
        - Years 11-20: Total debt = $32M (old) + $26M (new) = $58M/year
        - Years 21-30: Total debt = $0 (old paid) + $26M (new) = $26M/year

        This accumulated debt burden affects:
        - Cost of Stranded Assets (COSA) calculations when evaluating future switches
        - NPV calculations for technology investment decisions
        - Plant financial viability and closure risk

        Returns:
            List of annual debt payments for remaining lifetime, including both current
            technology debt and any legacy debt from previous technology switches.
        """
        from .calculate_costs import calculate_debt_repayment

        # For renovation cycles, use plant_lifetime as the loan period
        # This ensures debt is properly amortized over the renovation cycle
        lifetime_for_debt = self.lifetime.plant_lifetime

        # Calculate current technology's debt repayment based on its CAPEX
        # This represents the NEW debt taken on for the current technology
        current_debt_payments = calculate_debt_repayment(
            total_investment=self.total_investment,
            equity_share=self.equity_share,
            lifetime=lifetime_for_debt,
            cost_of_debt=self.cost_of_debt,
            lifetime_remaining=self.lifetime.remaining_number_of_years,
        )

        # Add legacy debt from previous technologies if it exists
        # Legacy debt accumulates when switching technologies before debt is fully repaid
        if self.legacy_debt_schedule:
            # Combine legacy debt with new debt for overlapping years
            # This creates the "double debt" burden that makes mid-lifetime switches expensive
            combined_payments = []
            for i in range(len(current_debt_payments)):
                current_payment = current_debt_payments[i]
                # Add legacy payment if it exists for this year, otherwise 0
                legacy_payment = self.legacy_debt_schedule[i] if i < len(self.legacy_debt_schedule) else 0.0
                combined_payments.append(current_payment + legacy_payment)
            return combined_payments

        # No legacy debt - return only current technology's debt schedule
        return current_debt_payments

    @property
    def debt_repayment_for_current_year(self) -> float:
        """
        Returns the total debt repayment amount for the current year only.

        This is the current-year equivalent of debt_repayment_per_year. It combines the current
        technology's debt payment with any legacy debt from previous technology switches.
        See debt_repayment_per_year for full details on debt accumulation logic.

        Returns:
            Float representing the total debt payment due for the current year.
        """
        from .calculate_costs import calculate_current_debt_repayment

        logger = logging.getLogger(f"{__name__}.debt_repayment_for_current_year")

        if self.cost_of_debt is None:
            raise ValueError("Cost of debt must be set to calculate debt repayment")

        # Calculate current technology's debt payment for this year
        # This represents the NEW debt payment from the current technology
        current_tech_payment = calculate_current_debt_repayment(
            total_investment=self.total_investment,
            lifetime_expired=self.lifetime.expired,
            lifetime_years=self.lifetime.plant_lifetime,
            years_elapsed=self.lifetime.elapsed_number_of_years,
            cost_of_debt=self.cost_of_debt,
            equity_share=self.equity_share,
        )

        # logger.debug(f"[FG DEBT REPAYMENT]: Current debt payment: ${current_tech_payment:,.0f}")

        # Add legacy debt for the current year if it exists
        # Legacy debt accumulates when switching technologies before debt is fully repaid
        # The first element of legacy_debt_schedule is the payment due this year
        if self.legacy_debt_schedule and len(self.legacy_debt_schedule) > 0:
            logger.debug(f"[FG DEBT REPAYMENT]: Adding legacy debt: ${self.legacy_debt_schedule[0]:,.0f}")
            return current_tech_payment + self.legacy_debt_schedule[0]

        # No legacy debt - return only current technology's payment
        return current_tech_payment

    @property
    def production(self) -> float:
        """
        Total production for the current year (tonnes).

        Returns:
            float: Production calculated as utilization_rate × capacity.
        """
        return self.utilization_rate * self.capacity

    @property
    def cost_adjustments_from_secondary_outputs(self) -> float:
        """
        Cost adjustments from secondary outputs such as by-product sales (USD/t).

        Calculates revenue or costs from secondary outputs (e.g., slag) based on material volumes
        and input costs. Positive values represent costs, negative values represent revenue.

        Returns:
            float: Cost adjustment per unit of production. Returns 0.0 if no bill of materials is available.
        """
        from .calculate_costs import calculate_cost_adjustments_from_secondary_outputs

        if self.bill_of_materials is None:
            return 0.0
        return calculate_cost_adjustments_from_secondary_outputs(
            bill_of_materials=self.bill_of_materials,
            dynamic_business_cases=self.effective_primary_feedstocks,
            output_costs=self.output_energy_costs,
            disposal_cost_outputs=self.disposal_cost_outputs,
        )

    @property
    def unit_production_cost(self) -> float:
        """
        Calculate the total cost per unit of production.

        Aggregates all cost components including operating expenses, carbon costs, debt repayment,
        utilization adjustments, and credits from secondary outputs into a single unit cost metric.

        Returns:
            float: Total cost per unit of production ($/tonne or equivalent unit).

        Note: This is the comprehensive cost used for profitability calculations and plant competitiveness analysis.
        """
        from .calculate_costs import calculate_unit_production_cost

        # Aggregate all cost components into total unit production cost
        return calculate_unit_production_cost(
            unit_total_opex=self.unit_total_opex,
            unit_carbon_cost=self.carbon_cost_per_unit,
            unit_current_debt_repayment=self.unit_current_debt_repayment,
            utilization_rate=self.utilization_rate,
            cost_adjustments_from_secondary_outputs=self.cost_adjustments_from_secondary_outputs,
        )

    @property
    def is_underutilized(self) -> bool:
        return self.utilization_rate < self.production_threshold.low

    def calculate_metallic_charge_ratio(self) -> dict[str, float]:
        metallic_charge_ratio: dict[str, float] = {}
        if self.technology.dynamic_business_case is None:
            raise TypeError(f"dynamic_business_case cannot be None. Technology name: {self.technology.name}")
        if self.bill_of_materials is None:
            raise TypeError("bill_of_materials cannot be None for metallic charge ratio calculation")
        for feedstock in self.technology.dynamic_business_case:
            if feedstock.name.split("_")[1] in self.bill_of_materials["materials"]:
                if (
                    feedstock.required_quantity_per_ton_of_product is not None
                    and feedstock.required_quantity_per_ton_of_product != 0
                ):
                    metallic_charge_ratio[feedstock.name] = (
                        self.bill_of_materials["materials"][feedstock.name.split("_")[1]]["demand"]
                        / feedstock.required_quantity_per_ton_of_product
                    )
        total_metallic_charge_ratio = sum(metallic_charge_ratio.values())
        if total_metallic_charge_ratio > 1.01 or total_metallic_charge_ratio < 0.99:
            raise ValueError(f"Metallic charge ratio is not 1: {total_metallic_charge_ratio}")
        return metallic_charge_ratio

    # def energy_requirements_per_unit(self) -> dict[str, float]:
    #     energy_requirements_per_unit: dict[str, float] = {}
    #     if self.technology.dynamic_business_case is None:
    #         return energy_requirements_per_unit
    #     if self.bill_of_materials is None:
    #         return energy_requirements_per_unit
    #     metallic_charge_ratio = self.calculate_metallic_charge_ratio()
    #     for feedstock in self.technology.dynamic_business_case:
    #         if feedstock.name.split("_")[1] in self.bill_of_materials["materials"]:
    #             for en_req in feedstock.energy_requirements:
    #                 if en_req not in energy_requirements_per_unit:
    #                     energy_requirements_per_unit[en_req] = 0.0
    #                 energy_requirements_per_unit[en_req] += (
    #                     metallic_charge_ratio[feedstock.name] * feedstock.energy_requirements[en_req]
    #                 )
    #     return energy_requirements_per_unit

    def set_allocated_volumes(self, allocated_volume: float) -> None:
        """
        Set the allocated volumes *FROM* the furnace group.
        """
        self.allocated_volumes = allocated_volume

    @property
    def grid_emissions(self) -> float:
        """Calculate total grid emissions from electricity consumption.

        Takes electricity requirements from the bill of materials and multiplies by
        grid emissivity to compute total emissions from grid electricity use.

        Returns:
            Total grid emissions in tCO2e. Returns 0.0 if grid_emissivity, bill_of_materials,
            or required data is missing.

        Notes:
            - Matches BOM materials with technology dynamic business cases by metallic_charge and reductant.
            - Uses electricity requirements from business cases scaled by material demand.
            - Formula: electricity × grid_emissivity × material_demand / process_efficiency
        """
        if self.grid_emissivity is None:
            return 0.0
        if self.bill_of_materials is None:
            return 0.0

        total_grid_emissions = 0.0
        if self.bill_of_materials["materials"]:
            # Check if dynamic_business_case exists
            if self.technology.dynamic_business_case is None:
                return 0.0

            for dbc in self.technology.dynamic_business_case:
                if (
                    dbc.metallic_charge.lower() in self.bill_of_materials["materials"]
                    and dbc.reductant == self.chosen_reductant
                ):
                    if "demand" in self.bill_of_materials["materials"][dbc.metallic_charge.lower()]:
                        material_demand = self.bill_of_materials["materials"][dbc.metallic_charge.lower()]["demand"]
                    else:
                        return 0.0

                    if "electricity" in dbc.energy_requirements:
                        total_grid_emissions += (
                            dbc.energy_requirements["electricity"]
                            * self.grid_emissivity
                            * material_demand
                            / dbc.required_quantity_per_ton_of_product
                            if dbc.required_quantity_per_ton_of_product
                            else 0.0
                        )

        return total_grid_emissions

    def set_emissions_based_on_allocated_volumes(self) -> None:
        """Calculate and set total emissions for the furnace group based on allocated production.

        Matches bill of materials with technology business cases to identify emission sources,
        then calculates comprehensive emissions accounting for:
            - Direct process emissions from feedstocks
            - Energy-related emissions (electricity, natural gas, etc.)
            - Carbon capture adjustments
            - Grid electricity emissions

        Side Effects:
            Sets self.emissions to a nested dict with structure:
                {
                    boundary_name: {  # e.g., "plant_boundary", "supply_chain"
                        scope: float  # e.g., "scope_1", "scope_2", "scope_3"
                    }
                }

        Notes:
            - Returns early if bill_of_materials is None.
            - Uses calculate_emissions module functions for emission calculations.
            - Incorporates technology_emission_factors for material emission intensities.
            - Includes grid_emissions in the total calculation.
        """

        from steelo.domain.calculate_emissions import materiall_bill_business_case_match, calculate_emissions

        if self.bill_of_materials is None:
            return

        matched_business_cases = materiall_bill_business_case_match(
            dynamic_feedstocks=self.effective_primary_feedstocks,
            material_bill=self.bill_of_materials["materials"],
            tech=self.technology.name,
            reductant=self.chosen_reductant,
        )

        total_emissions = calculate_emissions(
            business_cases=matched_business_cases,
            material_bill=self.bill_of_materials["materials"],
            technology_emission_factors=self.technology_emission_factors,
            grid_emissions=self.grid_emissions,
        )

        self.emissions = total_emissions

    @property
    def cost_breakdown(self):
        """
        Calculate comprehensive cost breakdown including materials, energy, operational, and financial costs.

        Steps:
        1. Calculate material and energy costs from bill of materials using calculate_cost_breakdown.
        2. Add fixed operational expenses (FOPEX) per unit.
        3. Add carbon costs per unit.
        4. Add debt repayment costs per unit if bill of materials has materials defined.

        Returns:
            dict: Cost breakdown with keys including material costs, energy costs, unit fopex, carbon cost,
                and debt share.

        Note: This is a reporting property that aggregates costs from multiple sources for comprehensive cost analysis.
        """
        from .calculate_costs import calculate_cost_breakdown

        # Calculate base material and energy costs
        breakdown = calculate_cost_breakdown(
            bill_of_materials=self.bill_of_materials,
            production=self.production,
            dynamic_business_cases=self.technology.dynamic_business_case,
            energy_costs=self.energy_costs,
            chosen_reductant=self.chosen_reductant,
        )

        # Add operational and financial costs
        breakdown.update({"unit fopex": self.unit_fopex})
        breakdown.update({"carbon cost": self.carbon_cost_per_unit})
        if self.bill_of_materials and self.bill_of_materials["materials"]:
            breakdown.update({"debt share": self.unit_current_debt_repayment})
        return breakdown

    @property
    def unit_current_debt_repayment(self) -> float:
        """
        Debt repayment per unit of production (USD/t) or per unit of capacity if not producing.

        Mirrors the unit_fopex floor: utilisation is floored at production_threshold.low so a
        barely-producing furnace group does not spread its debt over a near-zero tonnage.

        Returns:
            float: Debt repayment divided by floored production when utilization > 0, otherwise
            divided by capacity.
        """
        if self.utilization_rate <= 0.0:
            return self.debt_repayment_for_current_year / self.capacity

        threshold_low = self.production_threshold.low
        if threshold_low is not None and threshold_low > 0:
            effective_utilisation = max(self.utilization_rate, threshold_low)
        else:
            effective_utilisation = self.utilization_rate

        return self.debt_repayment_for_current_year / (effective_utilisation * self.capacity)

    @property
    def cost_breakdown_by_feedstock(self) -> dict[str, dict[str, float]]:
        """
        Reporting the cost breakdown related to the bill of materials by feedstock
        """
        from .calculate_costs import calculate_cost_breakdown_by_feedstock

        if self.bill_of_materials is None:
            return {}

        return calculate_cost_breakdown_by_feedstock(
            bill_of_materials=self.bill_of_materials,
            dynamic_business_cases=self.technology.dynamic_business_case or [],
            chosen_reductant=self.chosen_reductant,
            energy_vopex_breakdown_by_input=self.energy_vopex_breakdown_by_input,
            cost_breakdown_keys=self.cost_breakdown_keys,
            output_costs=self.output_energy_costs,
            disposal_cost_outputs=self.disposal_cost_outputs,
        )

    @property
    def carbon_breakdown_by_feedstock(self) -> dict[str, dict[str, float]]:
        """Physical carbon intensity breakdown by feedstock (tCO2/t-product)."""
        from .calculate_costs import calculate_carbon_breakdown_by_feedstock

        if self.bill_of_materials is None:
            return {}

        return calculate_carbon_breakdown_by_feedstock(
            bill_of_materials=self.bill_of_materials,
            chosen_reductant=self.chosen_reductant,
            dynamic_business_cases=self.technology.dynamic_business_case or [],
            carbon_breakdown_keys=self.carbon_breakdown_keys,
        )

    def optimal_technology_name(
        self,
        market_price_series: dict[str, list[float]],
        cost_of_debt_by_tech: dict[str, float],
        cost_of_equity_by_tech: dict[str, float],
        get_bom_from_avg_boms: Callable[
            [dict[str, float], str, float, str | None],
            tuple[dict[str, dict[str, dict[str, float]]] | None, float, str, dict[str, float]],
        ],
        score_series_for_tech: Callable[[str, dict[str, float], Year, Year], ReductantScoreSeries],
        capex_dict: dict[str, float],
        capex_renovation_share: dict[str, float],
        technology_fopex_dict: dict[str, float],
        dynamic_business_cases: dict[str, list[PrimaryFeedstock]],
        chosen_emissions_boundary_for_carbon_costs: str,
        technology_emission_factors: list[TechnologyEmissionFactors],
        tech_to_product: dict[str, str],
        plant_lifetime: int,
        construction_time: int,
        current_year: Year,
        risk_free_rate: float,
        allowed_furnace_transitions: dict[str, list[str]] = {},
        carbon_cost_series: dict[Year, float] = {},
        tech_capex_subsidies: dict[str, list[Subsidy]] = {},
        tech_opex_subsidies: dict[str, list[Subsidy]] = {},
        tech_debt_subsidies: dict[str, list[Subsidy]] = {},
        tech_energy_subsidies: dict[str, dict[str, list[Subsidy]]] = {},
        most_common_reductant_by_tech: dict[str, str] = {},
    ) -> tuple[
        dict[str, float],
        dict[str, float],
        float | None,
        dict[str, dict[str, dict[str, dict[str, float]]]],
        dict[str, str],
    ]:
        """
        Identify the optimal technology transition for this furnace group by comparing NPVs of allowed technology
        options.

        Steps:
        1. Calculate Cost of Stranded Assets (COSA) for abandoning current technology (max of remaining debt and
            economic COSA).
        2. For each allowed technology transition:
           a. Apply subsidies to capex and cost of debt.
           b. Determine if brownfield renovation (same tech) or greenfield installation (new tech).
           c. Get or calculate BOM, utilization rate, and emissions.
           d. Calculate NPV including carbon costs and operating subsidies.
           e. Adjust NPV by subtracting COSA (for technology switches only).
        3. Return NPV comparison results with BOMs for each viable technology.

        Args:
            market_price_series (dict[str, list[float]]): Future market prices for steel and iron in $/tonne.
            cost_of_debt_by_tech (dict[str, float]): Interest rate for debt financing per technology (decimal),
                for this plant's country; each candidate is financed at its own rate.
            cost_of_equity_by_tech (dict[str, float]): Expected return rate for equity financing per technology
                (decimal), for this plant's country.
            get_bom_from_avg_boms (Callable): Function that retrieves average BOM for a technology.
            score_series_for_tech (Callable): (tech, output_shares, start, end) -> ReductantScoreSeries;
                the year-wise reductant-optimised score (energy VOPEX + carbon + secondary-output
                adjustment at each year's exogenous prices) that replaces the flat energy/carbon/
                by-product terms in the NPV.
            capex_dict (dict[str, float]): Capital expenditure per tonne of capacity for each technology ($/tonne).
            capex_renovation_share (dict[str, float]): Share of full capex required for renovating existing technology
                (decimal, e.g., 0.7 for 70%).
            technology_fopex_dict (dict[str, float]): Fixed operating expenses per tonne for each technology ($/tonne).
            dynamic_business_cases (dict[str, list[PrimaryFeedstock]]): Primary feedstock configurations by technology
                for emissions calculations.
            chosen_emissions_boundary_for_carbon_costs (str): Emission boundary to use for carbon cost calculation.
            technology_emission_factors (list[TechnologyEmissionFactors]): Emission factors for different technologies
                and feedstocks.
            tech_to_product (dict[str, str]): Maps technology names to product types (e.g., "BF" -> "iron").
            plant_lifetime (int): Expected operational lifetime for new technology investments (years).
            construction_time (int): Time required to construct new technology before it becomes operational (years).
            current_year (Year): Current simulation year.
            risk_free_rate (float): Risk-free interest rate used as floor for subsidized debt costs (decimal).
            allowed_furnace_transitions (dict[str, list[str]]): Dict mapping current technology names to list of
                technologies it can transition to.
            carbon_cost_series (dict[Year, float]): Carbon prices over time by year ($/tonne CO2).
            tech_capex_subsidies (dict[str, list[Subsidy]]): Available capital subsidies by technology.
            tech_opex_subsidies (dict[str, list[Subsidy]]): Available operating subsidies by technology.
            tech_debt_subsidies (dict[str, list[Subsidy]]): Available debt interest rate subsidies by technology.
            tech_energy_subsidies (dict[str, dict[str, list[Subsidy]]]): Energy carrier subsidies
                (carrier -> technology -> subsidies), already collected for the plant's geography.
                Candidate technologies are priced from unsubsidised carrier prices with these
                applied for the candidate's operating start year, so the incumbent's energy
                subsidies do not leak into candidate costings.

        Returns:
            tuple: (npv_dict, npv_capex_dict, cosa, bom_dict) where:
                - npv_dict (dict[str, float]): NPV in $ for each evaluated technology (after COSA adjustment for
                    switches).
                - npv_capex_dict (dict[str, float]): Effective capex per tonne in $ after subsidies for each technology.
                - cosa (float | None): Cost of Stranded Assets in $ for abandoning current technology (None if no
                    transitions allowed).
                - bom_dict (dict[str, dict[str, dict[str, dict[str, float]]]]): Bill of Materials for each evaluated
                    technology.
                - reductant_dict (dict[str, str]): Operating-start-year reductant pick per evaluated
                    technology (what a commitment should install).

        Notes:
        - Returns empty dicts and None for COSA if no transitions are allowed for current technology.
        - Technologies without capex data are skipped.
        - COSA is subtracted from NPV only for technology switches, not for renovating current technology.
        """
        from steelo.domain.calculate_costs import calculate_debt_with_subsidies

        # ========== STAGE 1: Initialize and Import Dependencies ==========
        from .calculate_costs import (
            calculate_npv_full,
            stranding_asset_cost,
            calculate_capex_with_subsidies,
            calculate_opex_list_with_subsidies,
            calculate_variable_opex,
        )
        from .calculate_emissions import calculate_emissions_cost_series

        logger = logging.getLogger(f"{__name__}.optimal_technology_name")

        # Log initial furnace group state
        logger.info(f"[OPTIMAL TECH] Starting technology evaluation for FurnaceGroup {self.furnace_group_id}")
        logger.info(
            f"[OPTIMAL TECH] Current technology: {self.technology.name}, "
            f"Capacity: {self.capacity * T_TO_KT:,.0f} kt, Utilization: {self.utilization_rate:.1%}"
        )
        logger.debug(
            f"[OPTIMAL TECH] Current year: {current_year}, "
            f"Lifetime remaining: {self.lifetime.remaining_number_of_years} years"
        )
        logger.debug(f"[OPTIMAL TECH] Market price series for steel ($/t): ${market_price_series['steel']}")
        logger.debug(f"[OPTIMAL TECH] Market price series for iron ($/t): ${market_price_series['iron']}")
        logger.debug(
            f"[OPTIMAL TECH] Allowed transitions from {self.technology.name}: "
            f"{allowed_furnace_transitions.get(self.technology.name, [])}"
        )

        # Log current operating costs
        logger.debug(f"[OPTIMAL TECH] Unit fixed OPEX: ${self.unit_fopex:,.2f}/t")
        logger.debug(f"[OPTIMAL TECH] Unit variable OPEX: ${self.unit_vopex:,.2f}/t")
        logger.debug(f"[OPTIMAL TECH] Unit total OPEX: ${self.unit_total_opex:,.2f}/t")
        logger.debug(f"[OPTIMAL TECH] Debt repayment number of years: {len(self.debt_repayment_per_year)}")

        # ========== STAGE 2: Calculate Current Technology OPEX with Subsidies ==========
        # Collect all active OPEX subsidies across the remaining lifetime
        applied_opex_subsidies = collect_active_subsidies_over_period(
            tech_opex_subsidies.get(self.technology.name, []),
            start_year=Year(current_year),
            end_year=self.lifetime.end,
        )

        # Calculate unit OPEX with subsidies (without carbon costs yet)
        unit_opex_list = calculate_opex_list_with_subsidies(
            opex=self.unit_total_opex_no_subsidy,
            opex_subsidies=applied_opex_subsidies,
            start_year=self.lifetime.current,
            end_year=self.lifetime.end,
        )

        logger.debug(
            f"[OPTIMAL TECH] Applied OPEX subsidies for current technology {self.technology.name}: {applied_opex_subsidies}"
        )
        logger.debug(f"[OPTIMAL TECH] Unit opex list with subsidies (without carbon costs): {unit_opex_list}")

        # ========== STAGE 3: Calculate Carbon Costs for Current Technology ==========
        # Calculate total carbon costs over remaining lifetime
        carbon_costs_list = calculate_emissions_cost_series(
            emissions=self.emissions,
            carbon_price_dict=carbon_cost_series,
            chosen_emission_boundary=chosen_emissions_boundary_for_carbon_costs,
            start_year=self.lifetime.current,
            end_year=self.lifetime.end,
        )

        # Convert to unit carbon costs (per tonne of production)
        if self.production <= 0:
            unit_carbon_cost_list = [0.0 for _ in carbon_costs_list]
        else:
            unit_carbon_cost_list = [carbon_cost / self.production for carbon_cost in carbon_costs_list]

        # Combine OPEX with carbon costs and secondary output adjustment for COSA calculation
        cosa_secondary_adj = self.cost_adjustments_from_secondary_outputs
        unit_opex_carbon_costs = [x + y + cosa_secondary_adj for x, y in zip(unit_opex_list, unit_carbon_cost_list)]
        logger.debug(
            f"[OPTIMAL TECH] COSA secondary output adjustment for {self.technology.name}: ${cosa_secondary_adj:,.4f}/t"
        )

        logger.debug(f"[OPTIMAL TECH] Calculating carbon costs for current technology {self.technology.name}")
        logger.debug(f"[OPTIMAL TECH] Emission boundary: {chosen_emissions_boundary_for_carbon_costs}")
        logger.debug(
            f"[OPTIMAL TECH] Carbon cost series length: {len(unit_carbon_cost_list)}, "
            f"First 5 values: {unit_carbon_cost_list[:5] if unit_carbon_cost_list else 'None'}"
        )
        logger.debug(f"[OPTIMAL TECH] Unit opex list with subsidies (after carbon costs): {unit_opex_carbon_costs}")

        # ========== STAGE 4: Calculate Cost of Stranded Assets (COSA) ==========
        # COSA represents the economic penalty for abandoning current technology before end of life
        logger.debug(f"[OPTIMAL TECH] Calculating COSA for current technology {self.technology.name}")
        logger.debug(
            f"[OPTIMAL TECH] Debt repayment years: {len(self.debt_repayment_per_year)}, "
            f"Remaining lifetime: {self.lifetime.remaining_number_of_years} years"
        )
        logger.debug(f"[OPTIMAL TECH] Debt repayment per year: {self.debt_repayment_per_year}")
        logger.debug(
            f"[OPTIMAL TECH] Product: {self.technology.product}, "
            f"Expected production: {self.production * T_TO_KT:,.0f} kt"
        )
        logger.debug(f"[OPTIMAL TECH] Price series ($/t): {market_price_series.get(self.technology.product)}")

        # COSA = foregone operating margin only; remaining debt is excluded (carried by the legacy-debt schedule).
        # Discounted at the incumbent technology's cost of equity.
        incumbent_cost_of_equity = cost_of_equity_by_tech.get(self.technology.name)
        if incumbent_cost_of_equity is None:
            raise ValueError(f"No cost of equity data for technology {self.technology.name}")
        cosa = stranding_asset_cost(
            unit_total_opex_list=unit_opex_carbon_costs,
            remaining_time=self.lifetime.remaining_number_of_years,
            market_price_series=market_price_series[self.technology.product],
            expected_production=self.production,
            cost_of_equity=incumbent_cost_of_equity,
            construction_time=construction_time,
        )

        logger.debug(f"[OPTIMAL TECH] COSA (foregone operating margin): ${cosa:,.0f}")

        # ========== STAGE 5: Check for Allowed Technology Transitions ==========
        npv_dict = {}
        npv_capex_dict: dict[str, float] = {}
        bom_dict: dict[str, Any] = {}
        reductant_dict: dict[str, str] = {}

        # Check if current technology has any allowed transitions defined
        if self.technology.name not in allowed_furnace_transitions:
            logger.info(f"[OPTIMAL TECH] NO TRANSITIONS ALLOWED - {self.technology.name} has no defined transitions")
            logger.info("[OPTIMAL TECH] Returning empty results - no technology switch possible")
            logger.info("[OPTIMAL TECH] NPV dict: {}")
            logger.info(f"[OPTIMAL TECH] NPV capex dict: {npv_capex_dict}")
            logger.info("[OPTIMAL TECH] COSA: None")
            logger.info(f"[OPTIMAL TECH] BOM dict: {bom_dict}")
            return {}, npv_capex_dict, None, bom_dict, reductant_dict

        # ========== STAGE 6: Evaluate Each Allowed Technology Transition ==========
        logger.debug(
            f"[OPTIMAL TECH] Beginning evaluation of {len(allowed_furnace_transitions[self.technology.name])} "
            f"possible transitions: {allowed_furnace_transitions[self.technology.name]}"
        )

        for tech in allowed_furnace_transitions[self.technology.name]:
            logger.info(f"[OPTIMAL TECH] ===== Evaluating transition to {tech} =====")

            # Skip if technology lacks capex data
            if tech not in capex_dict:
                logger.info(f"[OPTIMAL TECH] SKIPPING {tech} - No capex data available")
                continue

            # BOF requires smelter furnace (for pig iron production)
            if tech == "BOF" and not self.has_hot_metal_access:
                logger.info("[OPTIMAL TECH] SKIPPING BOF - Plant has no smelter furnace (required for BOF)")
                continue

            # current tech renovates in place and keeps producing, so its NPV carries no construction lag
            tech_construction_time = 0 if tech == self.technology.name else construction_time

            # Collect all active subsidies for this technology
            capex_subsidies = filter_subsidies_for_year(tech_capex_subsidies.get(tech, []), current_year)
            debt_subsidies = filter_subsidies_for_year(tech_debt_subsidies.get(tech, []), current_year)
            opex_subsidies = collect_active_subsidies_over_period(
                tech_opex_subsidies.get(tech, []),
                start_year=Year(current_year + tech_construction_time),
                end_year=Year(current_year + tech_construction_time + plant_lifetime),
            )

            # Apply subsidies to capex
            original_capex = capex_dict[tech]
            capex = calculate_capex_with_subsidies(original_capex, capex_subsidies)

            logger.debug(f"[OPTIMAL TECH] Base capex for {tech}: ${original_capex:,.2f}")
            logger.debug(
                f"[OPTIMAL TECH] Capex after subsidies: ${capex:.2f}, Reduction: ${original_capex - capex:.2f}"
            )
            logger.debug(f"[OPTIMAL TECH] Capex subsidies: {capex_subsidies}")
            logger.debug(f"[OPTIMAL TECH] Debt subsidies: {debt_subsidies}")
            logger.debug(f"[OPTIMAL TECH] Opex subsidies: {opex_subsidies}")

            # === STAGE 7: Branch Based on Current vs New Technology ==========
            if tech == self.technology.name:  # Renovate current technology (brownfield)
                # ========== BRANCH A: Brownfield Renovation (Same Technology) ==========
                # Apply renovation share to reduce capex (leveraging existing infrastructure)
                capex_renovation_share_for_tech = capex_renovation_share.get(tech)
                if capex_renovation_share_for_tech is None:
                    raise ValueError(f"No capex renovation share defined for technology {tech}")
                capex *= capex_renovation_share_for_tech

                # Reuse existing BOM and utilization rate (no technology change)
                bill_of_materials = self.bill_of_materials
                util_rate = self.utilization_rate
                reductant = self.chosen_reductant

                # Validate BOM structure before proceeding
                if not bill_of_materials or "materials" not in bill_of_materials or "energy" not in bill_of_materials:
                    logger.warning(f"[OPTIMAL TECH] SKIPPING {tech} - Invalid or missing BOM structure")
                    logger.warning(f"Invalid or missing BOM for current technology {tech}, skipping")
                    continue

                # Incumbent charge mix from the FG's own BOM; input-demand weights proxy
                # output shares — only relative charge weights enter the score series
                material_demands = {
                    normalize_name(material): float(data["demand"])
                    for material, data in bill_of_materials["materials"].items()
                }
                total_demand = sum(material_demands.values())
                output_shares = (
                    {charge: demand / total_demand for charge, demand in material_demands.items()}
                    if total_demand > 0
                    else {}
                )

                logger.debug(f"[OPTIMAL TECH] Evaluating CURRENT technology {tech} as brownfield renovation")
                logger.debug(
                    f"[OPTIMAL TECH] Capex renovation share adjustment - Share: {capex_renovation_share_for_tech:.2%}, Adjusted: ${capex:,.2f}"
                )
                logger.debug(f"[OPTIMAL TECH] Using existing BOM and utilization rate: {util_rate:.2%}")

            else:  # Switch to a new technology (greenfield)
                # ========== BRANCH B: Greenfield Installation (New Technology) ==========
                # Price the candidate from unsubsidised prices plus its own energy subsidies —
                # the incumbent's subsidies must not leak into candidate costings
                base_energy_costs = self.energy_costs_no_subsidy
                operating_start_year = Year(current_year + construction_time)
                active_energy_subs: dict[str, list[Subsidy]] = {}
                for carrier, subsidies_by_tech in tech_energy_subsidies.items():
                    active = filter_subsidies_for_year(subsidies_by_tech.get(tech, []), operating_start_year)
                    if active:
                        active_energy_subs[carrier] = active

                if active_energy_subs:
                    candidate_energy_costs, _, _ = get_subsidised_energy_costs(
                        base_energy_costs,
                        active_energy_subs,
                    )
                    sub_summary = ", ".join(f"{len(subs)} {carrier}" for carrier, subs in active_energy_subs.items())
                    logger.debug(
                        f"[OPTIMAL TECH] {tech} candidate energy subsidies for year {operating_start_year}: "
                        f"{sub_summary}"
                    )
                else:
                    candidate_energy_costs = base_energy_costs

                # Fetch average BOM for the new technology from historical data
                chosen_reductant = most_common_reductant_by_tech.get(tech)
                bom_result = get_bom_from_avg_boms(candidate_energy_costs, tech, self.capacity, chosen_reductant)
                bill_of_materials_opt, util_rate, reductant, output_shares = bom_result

                # Skip if BOM retrieval failed
                if bill_of_materials_opt is None:
                    logger.warning(f"[OPTIMAL TECH] SKIPPING {tech} - Could not retrieve BOM from averages")
                    logger.warning(f"Could not get bill of materials for technology {tech}, skipping")
                    continue

                bill_of_materials = bill_of_materials_opt

                logger.debug(f"[OPTIMAL TECH] Evaluating NEW technology {tech} as greenfield installation")
                logger.debug(f"[OPTIMAL TECH] Full greenfield capex: ${capex:,.2f}")
                logger.debug(
                    f"[OPTIMAL TECH] Retrieved BOM successfully - Utilization: {util_rate:.2%}, Reductant: {reductant}"
                )

            # ========== STAGE 8: Calculate NPV for Technology ==========
            # Only proceed if we have valid BOM data
            if bill_of_materials is not None and bill_of_materials["materials"]:
                # Validate and retrieve product price series for this technology
                product_type = tech_to_product[tech]
                if not product_type or product_type not in market_price_series:
                    logger.debug(f"[OPTIMAL TECH] SKIPPING {tech} - Invalid or missing product type: {product_type}")
                    continue
                product_price_series = market_price_series[product_type]

                # Year-wise reductant-optimised score over the operating window; the NPV's
                # energy, carbon and by-product terms all live inside this series
                operating_start = Year(current_year + tech_construction_time)
                operating_end = Year(current_year + tech_construction_time + plant_lifetime)
                score_series = score_series_for_tech(tech, output_shares, operating_start, operating_end)
                committed_reductant = score_series.picks[0] if score_series.picks else ""
                if tech != self.technology.name and committed_reductant != reductant:
                    # Commit the BOM the start-year pick implies (materials are reductant-
                    # invariant; only the energy rows follow the pick)
                    rebuilt_bom, util_rate, reductant, output_shares = get_bom_from_avg_boms(
                        candidate_energy_costs, tech, self.capacity, committed_reductant
                    )
                    if rebuilt_bom is None:
                        raise ValueError(
                            f"BOM rebuild for {tech} with reductant '{committed_reductant}' returned no BOM"
                        )
                    bill_of_materials = rebuilt_bom

                # Materials-only variable OPEX plus fixed OPEX; energy, carbon and
                # by-products enter through the per-year score
                unit_fopex = technology_fopex_dict.get(tech.lower())
                if unit_fopex is None:
                    raise ValueError(f"Unit FOPEX for technology {tech} not found")

                # A furnace group (incumbent or candidate) the market allocated no
                # fleet-wide production for cannot price its fixed OPEX; skip it before
                # committing it to bom_dict/reductant_dict
                try:
                    unit_fopex_scaled = scale_fopex_to_production(unit_fopex, util_rate)
                except ZeroUtilisationError:
                    logger.warning(
                        f"[OPTIMAL TECH] SKIPPING {tech} - zero utilisation for furnace group "
                        f"{self.furnace_group_id}, cannot price fixed OPEX"
                    )
                    continue

                bom_dict[tech] = bill_of_materials
                reductant_dict[tech] = committed_reductant

                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(
                        "[REDUCTANT NPV] FG %s: tech=%s committed=%r picks=%s",
                        self.furnace_group_id,
                        tech,
                        committed_reductant,
                        summarise_reductant_picks(score_series.picks, operating_start),
                    )

                unit_base_opex = calculate_unit_total_opex(
                    unit_fopex=unit_fopex_scaled,
                    unit_vopex=calculate_variable_opex(bill_of_materials["materials"], {}),
                    utilization_rate=util_rate,
                )

                # Apply operating subsidies over plant lifetime
                unit_total_opex_list = calculate_opex_list_with_subsidies(
                    opex=[unit_base_opex + score for score in score_series.scores],
                    opex_subsidies=opex_subsidies,
                    start_year=operating_start,
                    end_year=operating_end,
                )

                # Apply debt subsidies to this technology's own cost of debt
                npv_capex_dict[tech] = capex
                original_cost_of_debt = cost_of_debt_by_tech.get(tech)
                if original_cost_of_debt is None:
                    raise ValueError(f"No cost of debt data for technology {tech}")
                cost_of_equity = cost_of_equity_by_tech.get(tech)
                if cost_of_equity is None:
                    raise ValueError(f"No cost of equity data for technology {tech}")
                cost_of_debt = calculate_debt_with_subsidies(
                    cost_of_debt=original_cost_of_debt,
                    debt_subsidies=debt_subsidies,
                    risk_free_rate=risk_free_rate,
                )

                # Calculate NPV using all cost and revenue components (carbon and
                # by-product terms are already inside the per-year opex list)
                npv_dict[tech] = calculate_npv_full(
                    capex=capex,
                    capacity=self.capacity,
                    unit_total_opex_list=unit_total_opex_list,
                    expected_utilisation_rate=util_rate,
                    price_series=product_price_series,
                    lifetime=plant_lifetime,
                    construction_time=tech_construction_time,
                    cost_of_debt=cost_of_debt,
                    cost_of_equity=cost_of_equity,
                    equity_share=self.equity_share,
                )

                logger.debug(f"[OPTIMAL TECH] Proceeding with NPV calculation for {tech}")
                logger.debug(
                    f"[OPTIMAL TECH] Product type: {product_type}, Market price series ($/t): {product_price_series}"
                )
                logger.debug(f"[OPTIMAL TECH] Unit FOPEX for {tech}: ${unit_fopex:,.2f}")
                logger.debug(f"[OPTIMAL TECH] Operating subsidies calculated for {self.lifetime.plant_lifetime} years")
                logger.debug(
                    f"[OPTIMAL TECH] Cost of debt for {tech}: {original_cost_of_debt:.1%} -> {cost_of_debt:.1%} (after subsidies)"
                )
                logger.debug("[OPTIMAL TECH] Calculating NPV with parameters:")
                logger.debug(f"[OPTIMAL TECH]   - Technology: {tech}")
                logger.debug(
                    f"[OPTIMAL TECH]   - Capex per tonne: ${capex:,.2f} (before subsidy: ${original_capex:,.2f})"
                )
                logger.debug(
                    f"[OPTIMAL TECH]   - Total opex per tonne: {unit_total_opex_list} (base before score: ${unit_base_opex:,.2f})"
                )
                logger.debug(f"[OPTIMAL TECH]   - Capacity: {self.capacity:.2f} t")
                logger.debug(f"[OPTIMAL TECH]   - Utilization rate: {util_rate:.2%}")
                logger.debug(f"[OPTIMAL TECH]   - Price series ($/t): {product_price_series}")
                logger.debug(f"[OPTIMAL TECH]   - Lifetime: {self.lifetime.plant_lifetime} years")
                logger.debug(
                    f"[OPTIMAL TECH]   - Cost of debt: {cost_of_debt:.2%} (before subsidy: {original_cost_of_debt:.2%})"
                )
                logger.debug(f"[OPTIMAL TECH]   - Cost of equity: {cost_of_equity:.2%}")
                logger.debug(f"[OPTIMAL TECH]   - Equity share: {self.equity_share:.2%}")
                logger.debug(f"[OPTIMAL TECH] Raw NPV for {tech}: ${npv_dict[tech]:,.2f}")

                # ========== STAGE 9: Adjust NPV for COSA (Technology Switches Only) ==========
                # Subtract COSA penalty only when switching to a different technology
                if tech != self.technology.name and cosa is not None:
                    original_npv = npv_dict[tech]
                    npv_dict[tech] -= cosa
                    logger.debug(
                        f"[OPTIMAL TECH] COSA adjustment for {tech} - "
                        f"Original NPV: ${original_npv:,.2f}, COSA: ${cosa:,.2f}, Adjusted NPV: ${npv_dict[tech]:,.2f}"
                    )
                    logger.debug(
                        f"[OPTIMAL TECH] Technology switch {self.technology.name} -> {tech} "
                        f"is {'PROFITABLE' if npv_dict[tech] > 0 else 'UNPROFITABLE'} after COSA"
                    )
                else:
                    logger.debug(f"[OPTIMAL TECH] No COSA adjustment for current technology {tech}")
            else:
                # Skip NPV calculation - log reasons
                reasons = []
                if bill_of_materials is None:
                    reasons.append("BOM is None")
                elif not bill_of_materials.get("materials"):
                    reasons.append("BOM has no materials")
                if current_year is None:
                    reasons.append("current_year is None")

                logger.debug(f"[OPTIMAL TECH] SKIPPING NPV calculation for {tech} - Reasons: {', '.join(reasons)}")

        # ========== STAGE 10: Return Results ==========
        # Log final evaluation summary
        if npv_dict:
            best_tech = max(npv_dict, key=lambda k: npv_dict[k])
            logger.debug("[OPTIMAL TECH] ===== Evaluation Complete =====")
            logger.debug(f"[OPTIMAL TECH] Technologies evaluated: {list(npv_dict.keys())}")
            logger.debug(f"[OPTIMAL TECH] Best technology by NPV: {best_tech} with NPV: ${npv_dict[best_tech]:,.2f}")
            if cosa:
                logger.debug(f"[OPTIMAL TECH] COSA calculated: ${cosa:,.2f}")
            else:
                logger.debug("[OPTIMAL TECH] COSA is None")
        else:
            logger.debug("[OPTIMAL TECH] ===== Evaluation Complete =====")
            logger.debug("[OPTIMAL TECH] No viable technology transitions found")
            if cosa:
                logger.debug(f"[OPTIMAL TECH] COSA calculated: ${cosa:,.2f}")
            else:
                logger.debug("[OPTIMAL TECH] COSA is None")

        return npv_dict, npv_capex_dict, cosa, bom_dict, reductant_dict

    def update_balance_sheet(self, market_price: float) -> float:
        """
        Calculates the annual profit/loss and updates the legacy debt schedule.

        Steps:
        1. Compute current year balance: If producing, balance = (market_price - unit_cost) * production.
           If not producing, balance = -(debt_repayment + fixed_opex).
        2. Add current year balance to cumulative historic_balance.
        3. Advance the legacy debt schedule by removing the current year's payment (first element).

        Args:
            market_price (float): Market price per unit of production.

        Returns:
            float: The annual balance (profit if positive, loss if negative).

        Side Effects:
            - Updates self.balance to the calculated annual profit/loss.
            - Updates self.historic_balance by adding the current year's balance.
            - Removes the first element from self.legacy_debt_schedule.

        Note: See debt_repayment_per_year for full details on debt accumulation logic.
        """
        # Calculate balance: profit from production or loss from fixed costs when idle
        self.balance = (
            (market_price - self.unit_production_cost) * self.production
            if self.production > 0
            else -1 * (self.debt_repayment_for_current_year + self.unit_fopex * self.capacity)
        )
        # Accumulate into historic balance
        self.historic_balance += self.balance

        # Advance the legacy debt schedule by consuming the current year's payment
        # Example: [32M, 32M, 32M] becomes [32M, 32M] after popping the first element
        if self.legacy_debt_schedule and len(self.legacy_debt_schedule) > 0:
            self.legacy_debt_schedule = self.legacy_debt_schedule[1:]

        return self.balance

    def generate_energy_vopex_by_reductant(
        self,
        carbon_price: float = 0.0,
        technology_emission_factors: list[TechnologyEmissionFactors] | None = None,
        chosen_emissions_boundary: str = "",
    ) -> dict[str, float]:
        """
        Calculate energy VOPEX per (metallic charge, reductant) and pick the furnace
        group's reductant.

        With the default arguments the per-charge choice minimises energy VOPEX only
        (legacy behaviour, used by the commit-time callers). When
        ``technology_emission_factors`` is provided, the choice instead minimises the
        total score: energy VOPEX + ``carbon_price`` x direct GHG factor (for
        ``chosen_emissions_boundary``) + the business case's secondary-output
        adjustment (by-product revenue / disposal and carbon storage costs).

        Args:
            carbon_price: Country carbon price for the current year (USD/tCO2e).
            technology_emission_factors: Emission factors used to build the per-
                (reductant, metallic charge) direct GHG lookup. None keeps the
                energy-only choice. When provided, every business case of this
                furnace group's technology must have a factor for the chosen
                boundary (missing rows raise KeyError rather than scoring as 0).
            chosen_emissions_boundary: Emissions boundary whose direct GHG factor
                enters the score (e.g. "plant_boundary").

        Returns:
            dict[str, float]: Energy VOPEX per metallic charge for the chosen
            reductant (pure energy VOPEX regardless of scoring mode).

        Side Effects:
            Sets self.chosen_reductant and the reporting dicts
            (energy_vopex_by_input, energy_vopex_breakdown_by_input,
            energy_vopex_by_carrier) — reporting stays pure energy VOPEX; only the
            min-choice uses the total score.

        Notes:
            Logs a data-drift warning if a metallic charge's material requirements
            (required quantity per tonne or non-energy secondary feedstocks) differ
            across reductants — the score carries no material term because current
            masters author materials identically across reductants.
        """
        logger = logging.getLogger(f"{__name__}.generate_energy_vopex_by_reductant")
        if self.technology.dynamic_business_case is None:
            return {}

        ef_by_key = (
            build_direct_ghg_lookup(technology_emission_factors, chosen_emissions_boundary)
            if technology_emission_factors is not None
            else None
        )
        scores = score_reductants_for_business_cases(
            self.technology.dynamic_business_case,
            self.energy_costs,
            self.output_energy_costs,
            carbon_price,
            ef_by_key,
            self.disposal_cost_outputs,
        )
        energy_vopex_by_input = scores.energy_vopex_by_input
        energy_breakdown_by_input = scores.energy_breakdown_by_input
        score_by_input = scores.score_by_input

        for metallic_input, profiles in scores.material_profile_by_input.items():
            if len(set(profiles.values())) > 1:
                logger.warning(
                    "[REDUCTANT SCORE] FG %s: material requirements for charge '%s' differ across "
                    "reductants (%s) — the reductant score carries no material term, so this "
                    "difference is ignored. Check the master's Bill of Materials authoring.",
                    self.furnace_group_id,
                    metallic_input,
                    ", ".join(sorted(profiles)),
                )

        mins = [min(costs, key=lambda k: costs[k]) for costs in score_by_input.values() if costs]
        counts = Counter(mins)
        if not counts:
            # logger.warning(f"No reductants found for furnace group {self.furnace_group_id}")
            self.chosen_reductant = ""
            self.energy_vopex_by_input = {}
            self.energy_vopex_breakdown_by_input = {}
            self.energy_vopex_by_carrier = {}
            return {}
        most_common_reductant, _ = counts.most_common(1)[0]

        previous_reductant = self.chosen_reductant
        self.chosen_reductant = str(most_common_reductant)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "[REDUCTANT PICK] FG %s (%s): chosen=%r (was %r), scores=%s",
                self.furnace_group_id,
                self.technology.name,
                self.chosen_reductant,
                previous_reductant,
                {
                    charge: {reductant: round(score, 2) for reductant, score in costs.items()}
                    for charge, costs in score_by_input.items()
                },
            )
        # Reporting only the energy_vopex by metallic charge, using vopex for the chosen reductatnt
        trimmed: dict[str, float] = {}
        trimmed_breakdown: dict[str, dict[str, float]] = {}
        carrier_totals: dict[str, float] = defaultdict(float)

        for tech, reductant_costs in energy_vopex_by_input.items():
            if most_common_reductant not in reductant_costs:
                continue
            trimmed[tech] = reductant_costs[most_common_reductant]
            carriers = dict(energy_breakdown_by_input.get(tech, {}).get(most_common_reductant, {}))
            if carriers:
                trimmed_breakdown[tech] = carriers
                for carrier, value in carriers.items():
                    carrier_totals[carrier] += value

        self.energy_vopex_by_input = trimmed
        self.energy_vopex_breakdown_by_input = trimmed_breakdown
        self.energy_vopex_by_carrier = dict(carrier_totals)
        return trimmed

    def track_business_opportunities(
        self,
        year: Year,
        location: Location,
        market_price: dict[str, list[float]],  # product -> list of future market prices
        cost_of_equity: float,
        plant_lifetime: int,
        construction_time: int,
        consideration_time: int,
        probability_of_announcement: float,
        all_opex_subsidies: list[Subsidy],
        reductant_score_series: Callable[..., ReductantScoreSeries],
        status_stats: Counter | None = None,
        get_co2_headroom: Callable[[str, int, float], float] | None = None,
        get_co2_need: Callable[["Technology", float, str], float] | None = None,
        co2_storage_diagnostics: Callable[[str, int], tuple[float, float, float]] | None = None,
    ) -> commands.Command | None:
        """
        Tracks whether an identified business opportunity remains interesting over time to avoid making
        major decisions based on outlier years.

        Steps:
            1. Re-value the opportunity (status: considered) each year with the same metric used at
               creation: materials-only variable OPEX plus fixed OPEX (scaled to production terms)
               plus the year-wise reductant score over the earliest possible operating window.
               Energy, carbon and by-products enter through the score; the series re-optimises the
               reductant per year by design, while the committed ``chosen_reductant`` stays what
               conversion uses. The site's own pixel electricity/hydrogen prices (refreshed each
               year by ``update_dynamic_costs_for_business_opportunities``) enter as overrides on
               the country trajectory.
            2. Business opportunities whose last X (=consideration_time) NPVs are all positive
               (a rolling window, not necessarily the first years) are announced with a given
               probability (uniformly sampled), reflecting that not all opportunities are taken
               up by investors.
            3. Business opportunities whose last X (=consideration_time) NPVs are all negative are
               discarded. This is deliberate: the yearly site re-scan recreates opportunities at
               sites whose conditions improve later, so discarding does not exclude the site.

        Args:
            year: Current simulation year
            location: Location of the plant (including latitude, longitude, and country ISO3)
            market_price: Dictionary mapping product to list of future market prices,
                anchored at the current year; re-anchored to the opportunity's earliest
                construction start before the NPV
            cost_of_equity: Cost of equity for NPV calculation
            plant_lifetime: Lifetime of the plant in years
            construction_time: Time required for plant construction in years
            consideration_time: Number of years to track NPV before decision
            probability_of_announcement: Probability that a viable opportunity will be announced
            all_opex_subsidies: List of available OPEX subsidies
            reductant_score_series: ``(location, tech, output_shares, start, end, *, overrides,
                override_reference_year) -> ReductantScoreSeries`` (``Environment.reductant_score_series``)

        Returns:
            Command to update the status of the FurnaceGroup, or None if no status change.

        Note: This results in an adjusted NPV metric, which proved to be best to ensure the right plants
        are opened, because subsidized technologies would otherwise suffer a too long delay until the
        model picks them up. In real life, subsidies are often announced years in advance of actual plant
        construction. This metric only affects the decision to open a plant, not the actual costs once
        opened.
        """
        logger = logging.getLogger(f"{__name__}.FurnaceGroup.track_business_opportunities")
        from steelo.domain.calculate_costs import calculate_npv_full, scale_fopex_to_production

        # Verify prerequisites
        if self.historical_npv_business_opportunities is None:
            self.historical_npv_business_opportunities = {}
        if self.technology.capex is None:
            logger.warning(
                f"[NEW PLANTS] Technology capex is None for {self.technology.name}. Skipping NPV calculation and returning -inf."
            )
            npv_value = float("-inf")
            if status_stats is not None:
                status_stats["npv_inputs_missing"] += 1
        elif self.bill_of_materials is None:
            logger.warning(
                f"[NEW PLANTS] Bill of materials is None for {self.technology.name}. Skipping NPV calculation and returning -inf."
            )
            npv_value = float("-inf")
            if status_stats is not None:
                status_stats["npv_inputs_missing"] += 1
        elif self.output_shares is None:
            logger.warning(
                f"[NEW PLANTS] Output shares are None for {self.technology.name}. Skipping NPV calculation and returning -inf."
            )
            npv_value = float("-inf")
            if status_stats is not None:
                status_stats["npv_inputs_missing"] += 1
        elif self.utilization_rate <= 0:
            logger.warning(
                f"[NEW PLANTS] Zero utilisation for {self.technology.name} business opportunity at "
                f"({location.lat}, {location.lon}) in {location.iso3}. Skipping NPV calculation and returning -inf."
            )
            npv_value = float("-inf")
            if status_stats is not None:
                status_stats["npv_inputs_missing"] += 1
        else:
            # Earliest possible operating window, floored at the soonest path still open to a
            # considered opportunity: announce now, construct from next year
            years_already_considered = len(self.historical_npv_business_opportunities)
            earliest_operation_start_year = Year(
                max(
                    year + consideration_time + construction_time + 1 - years_already_considered,
                    year + 1 + construction_time,
                )
            )  # 1 year of announcement time
            earliest_operation_end_year = Year(earliest_operation_start_year + plant_lifetime)

            # Get OPEX (with subsidies) for the years the plant would be operational
            selected_opex_subsidies = collect_active_subsidies_over_period(
                all_opex_subsidies,
                start_year=earliest_operation_start_year,
                end_year=earliest_operation_end_year,
            )
            # Materials-only variable OPEX plus fixed OPEX; energy, carbon and
            # by-products enter through the per-year reductant score
            unit_vopex = calculate_variable_opex(self.bill_of_materials["materials"], {})
            unit_fopex = scale_fopex_to_production(self.tech_unit_fopex, self.utilization_rate)
            # Opportunity FGs always carry their own pixel prices; a missing carrier is a bug
            site_prices = self.energy_costs_no_subsidy or self.energy_costs
            site_overrides = {carrier: site_prices[carrier] for carrier in ("electricity", "hydrogen")}
            score_series = reductant_score_series(
                location,
                self.technology.name,
                self.output_shares,
                earliest_operation_start_year,
                earliest_operation_end_year,
                overrides=site_overrides,
                override_reference_year=year,
            )
            unit_total_opex_list = calculate_opex_list_with_subsidies(
                opex=[unit_vopex + unit_fopex + score for score in score_series.scores],
                opex_subsidies=selected_opex_subsidies,
                start_year=earliest_operation_start_year,
                end_year=earliest_operation_end_year,
            )

            # Calculate updated NPV (carbon and by-products live inside the score)
            years_to_construction_start = int(earliest_operation_start_year) - construction_time - int(year)
            npv_value = calculate_npv_full(
                capex=self.technology.capex,
                capacity=self.capacity,
                unit_total_opex_list=unit_total_opex_list,
                expected_utilisation_rate=self.utilization_rate,
                price_series=market_price[self.technology.product][years_to_construction_start:],
                lifetime=plant_lifetime,
                construction_time=construction_time,
                cost_of_debt=self.cost_of_debt,
                cost_of_equity=cost_of_equity,
                equity_share=self.equity_share,
                infrastructure_costs=self.railway_cost,
            )

        # Set to very negative NPV if calculation returned NaN
        if math.isnan(npv_value):
            logger.warning(
                f"[NEW PLANTS] NPV calculation returned NaN for {self.status} business opportunity for {self.technology} at ({location.lat}, {location.lon}) in {location.iso3}. Returning -inf."
            )
            self.historical_npv_business_opportunities[year] = float("-inf")
            if status_stats is not None:
                status_stats["npv_nan"] += 1
        else:
            self.historical_npv_business_opportunities[year] = npv_value

        # Log the NPV update
        if int(year) - 1 in self.historical_npv_business_opportunities:
            previous_npv = self.historical_npv_business_opportunities[int(year) - 1]
            logger.debug(
                f"[NEW PLANTS] Updated NPV for {self.status} business opportunity for {self.technology} at ({location.lat}, {location.lon}) in {location.iso3}: Year {int(year) - 1}: {previous_npv} -> Year {year}: {npv_value}"
            )
        else:
            # Raise error if no NPV was saved for the previous year; this should never happen since the indi furnace groups which are tracked
            # in here have been initialized in identify_new_business_opportunities previously
            raise ValueError(
                f"[NEW PLANTS] No historical NPV found for business opportunity {self.technology} at ({location.lat}, {location.lon}) in {location.iso3} when updating NPV for year {year}. Historical NPVs: {self.historical_npv_business_opportunities}"
            )

        # Check the latest NPVs and decide whether to announce or discard
        latest_npvs = list(self.historical_npv_business_opportunities.values())[-consideration_time:]
        if len(self.historical_npv_business_opportunities.keys()) >= consideration_time:
            if all(npv > 0 for npv in latest_npvs):
                if status_stats is not None:
                    status_stats["npv_positive_window"] += 1

                # G2 CO2 storage gate: block considered->announced if country headroom below need.
                # Blocked opportunity stays considered (no TTL, no discard) - retries next year.
                if get_co2_headroom is not None and get_co2_need is not None:
                    own_need = get_co2_need(self.technology, float(self.capacity), self.chosen_reductant or "")
                    if own_need > 0.0:
                        lookup_year = int(year) + 1 + construction_time
                        headroom = get_co2_headroom(location.iso3, lookup_year, 0.0)
                        firm, reserved, limit = (
                            co2_storage_diagnostics(location.iso3, lookup_year)
                            if co2_storage_diagnostics is not None
                            else (0.0, 0.0, 0.0)
                        )
                        if headroom < own_need:
                            logger.info(
                                f"[CO2 GATE] gate=G2 decision=blocked iso3={location.iso3} "
                                f"year={int(year)} lookup_year={lookup_year} "
                                f"tech={self.technology.name} reductant={self.chosen_reductant} "
                                f"fg_id={self.furnace_group_id} plant_id={self.get_furnace_plant_id()} "
                                f"need={own_need:.0f} firm={firm:.0f} reserved={reserved:.0f} "
                                f"own_contribution=0 limit={limit:.0f} "
                                f"headroom={headroom:.0f} discarded=false"
                            )
                            if status_stats is not None:
                                status_stats["co2_storage_blocked"] += 1
                            return None  # stay considered

                announcement_draw = random.random()
                if announcement_draw < probability_of_announcement:
                    if status_stats is not None:
                        status_stats["announced"] += 1
                    logger.debug(
                        f"[NEW PLANTS] Announcing the business opportunity for {self.technology} at ({location.lat}, {location.lon}) in {location.iso3} - NPV-positive for {consideration_time} years in a row."
                    )
                    return commands.UpdateFurnaceGroupStatus(
                        fg_id=self.furnace_group_id, plant_id=self.get_furnace_plant_id(), new_status="announced"
                    )
                if status_stats is not None:
                    status_stats["announcement_probability_failed"] += 1
                return None
            elif all(npv < 0 for npv in latest_npvs):
                if status_stats is not None:
                    status_stats["npv_negative_window"] += 1
                    status_stats["discarded"] += 1
                logger.debug(
                    f"[NEW PLANTS] Discarding the business opportunity for {self.technology} at ({location.lat}, {location.lon}) in {location.iso3} - NPV-negative for {consideration_time} years in a row."
                )
                return commands.UpdateFurnaceGroupStatus(
                    fg_id=self.furnace_group_id, plant_id=self.get_furnace_plant_id(), new_status="discarded"
                )
            else:
                if status_stats is not None:
                    status_stats["npv_mixed_window"] += 1
                return None  # No change in status
        else:
            # Not enough data to make a decision yet
            if status_stats is not None:
                status_stats["npv_insufficient_history"] += 1
            return None

    def convert_business_opportunity_into_actual_project(
        self,
        probability_of_construction: float,
        allowed_techs_current_year: list[str],
        new_plant_capacity_in_year: Callable[[str], float],
        expanded_capacity: float,
        capacity_limit_iron: float,
        capacity_limit_steel: float,
        new_capacity_share_from_new_plants: float,
        location: Location,
        status_stats: Counter | None = None,
        get_co2_headroom: Callable[[str, int, float], float] | None = None,
        get_co2_need: Callable[["Technology", float, str], float] | None = None,
        co2_storage_diagnostics: Callable[[str, int], tuple[float, float, float]] | None = None,
        construction_time: int = 4,
        reserved_discount_factor: float = 0.9,
    ) -> commands.Command | None:
        """
        Convert an announced business opportunity into an actual project (new plant and furnace) with a
        certain probability if the capacity limit allows for it, accounting for the risk that the plant
        may not be constructed due to unexpected events.

        Steps:
            1. Announced plants with allowed technologies are constructed with a certain probability
               (status: construction) if the new capacity limit for that year has not been reached yet.
            2. If the capacity limit has been reached, the business opportunity is kept in announced
               state and reconsidered in the next year (status: announced).
            3. Announced plants with technologies no longer allowed are discarded (status: discarded).
            4. The plant construction takes X years (default=4); after that, the plant is made
               operational by PAM (status: operating). This is controlled via the start date.

        Args:
            probability_of_construction: Probability that an announced plant starts construction
            allowed_techs_current_year: List of technologies allowed in the current year
            new_plant_capacity_in_year: Callable to get capacity from new plants only (not expansions)
            expanded_capacity: Capacity (in tonnes) added if the plant is constructed (furnace size)
            capacity_limit_iron: Capacity limit for iron (in tonnes) added this year in total
                (new plants and expansions)
            capacity_limit_steel: Capacity limit for steel (in tonnes) added this year in total
                (new plants and expansions)
            new_capacity_share_from_new_plants: Share of new capacity from new plants (vs expansions)
            location: Location of the plant (including latitude, longitude, and country ISO3)

        Returns:
            Command to update the status of the FurnaceGroup, or None if no status change.
        """
        logger = logging.getLogger(f"{__name__}.FurnaceGroup.convert_business_opportunity_into_actual_project")

        # Check if technology is still allowed; discard if not. This should rarely happen since such a business opportunity should have not
        # been even considered.
        if self.technology.name not in allowed_techs_current_year:
            logger.info(
                f"[NEW PLANTS] Discarding the business opportunity for {self.technology.name} at ({location.lat}, {location.lon}) in {location.iso3} - Technology no longer allowed."
            )
            if status_stats is not None:
                status_stats["tech_not_allowed"] += 1
            return commands.UpdateFurnaceGroupStatus(
                fg_id=self.furnace_group_id, plant_id=self.get_furnace_plant_id(), new_status="discarded"
            )

        # Get capacity from NEW PLANTS only (not expansions) and calculate limits
        new_plant_capacity_so_far = new_plant_capacity_in_year(self.technology.product)
        if self.technology.product == "iron":
            capacity_limit = capacity_limit_iron * new_capacity_share_from_new_plants
        elif self.technology.product == "steel":
            capacity_limit = capacity_limit_steel * new_capacity_share_from_new_plants
        else:
            raise ValueError(
                f"Unknown product type: '{self.technology.product}' for technology: '{self.technology.name}'"
            )

        # Stay in announced state if capacity limit reached
        if new_plant_capacity_so_far + expanded_capacity > capacity_limit:
            logger.info(
                f"[NEW PLANTS] BLOCKED - New plant capacity limit reached for {self.technology.product}: {new_plant_capacity_so_far * T_TO_KT:,.0f} kt + {expanded_capacity * T_TO_KT:,.0f} kt > {capacity_limit * T_TO_KT:,.0f} kt"
            )
            if status_stats is not None:
                status_stats["capacity_limit_blocked"] += 1
            return None  # Stay announced, try again next year

        # P1 CO2 storage gate: block + immediate-discard if announced CCS exceeds country headroom.
        # Exempts own d*need from headroom (FG already contributes to reserved bucket).
        if get_co2_headroom is not None and get_co2_need is not None:
            own_need = get_co2_need(self.technology, float(self.capacity), self.chosen_reductant or "")
            if own_need > 0.0:
                lookup_year = int(self.lifetime.current) + construction_time
                own_contribution = reserved_discount_factor * own_need
                headroom = get_co2_headroom(location.iso3, lookup_year, own_contribution)
                firm, reserved, limit = (
                    co2_storage_diagnostics(location.iso3, lookup_year)
                    if co2_storage_diagnostics is not None
                    else (0.0, 0.0, 0.0)
                )
                if headroom < own_need:
                    logger.info(
                        f"[CO2 GATE] gate=P1 decision=blocked iso3={location.iso3} "
                        f"year={int(self.lifetime.current)} lookup_year={lookup_year} "
                        f"tech={self.technology.name} reductant={self.chosen_reductant} "
                        f"fg_id={self.furnace_group_id} plant_id={self.get_furnace_plant_id()} "
                        f"need={own_need:.0f} firm={firm:.0f} reserved={reserved:.0f} "
                        f"own_contribution={own_contribution:.0f} limit={limit:.0f} "
                        f"headroom={headroom:.0f} discarded=true"
                    )
                    if status_stats is not None:
                        status_stats["co2_storage_blocked"] += 1
                    return commands.UpdateFurnaceGroupStatus(
                        fg_id=self.furnace_group_id,
                        plant_id=self.get_furnace_plant_id(),
                        new_status="discarded",
                    )

        # Stay in announced state if probability of construction not met
        construction_draw = random.random()
        if construction_draw > probability_of_construction:
            if status_stats is not None:
                status_stats["construction_probability_failed"] += 1
            return None  # Stay announced, try again next year

        # Otherwise, start construction
        else:
            logger.debug(f"[NEW PLANTS] Constructing new {self.technology} plant.")
            if status_stats is not None:
                status_stats["construction_started"] += 1
            return commands.UpdateFurnaceGroupStatus(
                fg_id=self.furnace_group_id,
                plant_id=self.get_furnace_plant_id(),
                new_status="construction",
            )


class Plant:
    def __init__(
        self,
        *,
        plant_id: str,
        location: Location,
        furnace_groups: list[FurnaceGroup],
        power_source: str,
        soe_status: str,
        parent_gem_id: str,
        workforce_size: int,
        certified: bool,
        category_steel_product: set[ProductCategory],
        technology_unit_fopex: dict[str, float],
        average_steel_cost: float | None = None,
        steel_capacity: Volumes | None = None,
    ) -> None:
        # ID:
        self.plant_id = plant_id

        # Location
        self.location = location

        # Plant operations
        self.furnace_groups = furnace_groups
        self.power_source = power_source

        # Company management info
        self.soe_status = soe_status  # State-owned plants expected to make different decisions than private companies (e.g., no country shift)
        self.parent_gem_id = parent_gem_id
        self.workforce_size = workforce_size  # Keep for OPEX calc and impacts

        # Product quality
        self.certified = certified  # Merge 'ISO 14001', 'ISO 50001', and 'ResponsibleSteel Certification' into one single col, True (one enough) / False
        self.category_steel_product = (
            category_steel_product  # Discuss with Hannah if demand is dependent on steel quality
        )

        # collect domain events
        self.events: list[events.Event] = []
        self.added_capacity = Volumes(0)
        self.removed_capacity = Volumes(0)
        self.removed_capacity_by_product: dict[str, float] = {}

        # cost and capacity
        self.technology_unit_fopex = technology_unit_fopex
        self.average_steel_cost = average_steel_cost
        self.steel_capacity = steel_capacity
        self.carbon_cost_series: dict[Year, float] = {}
        self.has_hot_metal_access: bool = False

    def add_furnace_group(self, new_furnace_group: FurnaceGroup) -> None:
        self.furnace_groups.append(new_furnace_group)

    @property
    def historic_balance(self) -> float:
        """Lifetime operational P&L summed across all FGs (including closed). Pure derivation."""
        return sum(fg.historic_balance for fg in self.furnace_groups)

    @property
    def ultimate_plant_group(self) -> str:
        """
        Extract the ultimate plant group ID from parent_gem_id string.

        Handles complex parent_gem_id formats like:
        - 'E100000126525 [100.0%]' -> 'E100000126525'
        - 'E100001016239' -> 'E100001016239'
        - 'E100001010315; E100000004212; E100001010324' -> 'E100001010315' (first one)
        - 'E100001016122 [60.0%]; E100000132388 [40.0%]' -> 'E100001016122' (highest weight)

        Returns:
            str: The most appropriate plant group ID without weights
        """
        if not self.parent_gem_id or self.parent_gem_id.strip() == "unknown":
            return self.location.iso3 or "unknown"

        # Split by semicolon for multiple entries
        entries = [entry.strip() for entry in self.parent_gem_id.split(";")]

        if len(entries) == 1:
            # Single entry - just extract ID and remove weight
            return self._extract_id_from_entry(entries[0])

        # Multiple entries - find highest weighted or take first
        best_entry = None
        best_weight = -1.0
        has_weights = False

        for entry in entries:
            entry_id = self._extract_id_from_entry(entry)
            entry_weight = self._extract_weight_from_entry(entry)

            if entry_weight is not None:
                has_weights = True
                if entry_weight > best_weight:
                    best_weight = entry_weight
                    best_entry = entry_id

        # If no weights found, take first entry
        if not has_weights:
            return self._extract_id_from_entry(entries[0])

        return best_entry or self._extract_id_from_entry(entries[0])

    def _extract_id_from_entry(self, entry: str) -> str:
        """Extract the ID part from an entry, removing weight brackets."""
        # Remove weight brackets like [100.0%] or [60.0%]
        if "[" in entry:
            return entry.split("[")[0].strip()
        return entry.strip()

    def _extract_weight_from_entry(self, entry: str) -> float | None:
        """Extract weight percentage from an entry like 'E100001016122 [60.0%]'."""
        if "[" not in entry or "]" not in entry:
            return None

        try:
            # Extract content between brackets
            weight_str = entry.split("[")[1].split("]")[0]
            # Remove % sign if present
            weight_str = weight_str.replace("%", "").strip()
            return float(weight_str)
        except (IndexError, ValueError):
            return None

    @property
    def end_year(self) -> Year | None:
        """
        Get the latest end year among all furnace groups.
        This represents when the plant's last furnace group needs renovation.
        """
        if not self.furnace_groups:
            return None

        # Find the furnace group with the latest end year
        latest_end = max(fg.lifetime.end for fg in self.furnace_groups if fg.lifetime)
        return latest_end

    def get_furnace_technologies(self, active_statuses: list[str]) -> dict[str, dict[str, dict[str, float]]]:
        """Get the technologies of the furnace groups in the plant"""

        _ = {}
        for furnace_group in self.furnace_groups:
            if furnace_group.status in active_statuses and furnace_group.capacity > 0:
                tech = furnace_group.technology.name
                _[tech] = {"bom": furnace_group.bill_of_materials, "utilization_rate": furnace_group.utilization_rate}
        return _

    def has_DRI_furnace(self) -> bool:
        """
        Check if the plant has a DRI furnace
        """
        for furnace_group in self.furnace_groups:
            if furnace_group.technology.name == "DRI":
                return True
        return False

    def calculate_average_steel_cost_and_capacity(self, active_statuses: list[str]):
        """
        Calculates average steel cost and capacity for the plant for active steel furnaces
        """
        total_cost = 0.0
        total_capacity = 0.0
        for furnace_group in self.furnace_groups:
            if (
                furnace_group.status in active_statuses
                and furnace_group.technology.product == Commodities.STEEL.value
                and furnace_group.technology.lcop is not None
            ):
                total_cost += furnace_group.technology.lcop * furnace_group.capacity
                total_capacity += furnace_group.capacity
        if total_capacity == 0:
            return
        self.average_steel_cost = total_cost / total_capacity
        self.steel_capacity = Volumes(total_capacity)

    def set_technology_lops(self, technology_lops: dict[str, float]) -> None:
        has_DRI = self.has_DRI_furnace()
        for furnace_group in self.furnace_groups:
            if furnace_group.technology.name == "EAF":
                if has_DRI:
                    furnace_group.technology.lcop = technology_lops["DRI-EAF"]
                else:
                    furnace_group.technology.lcop = technology_lops["EAF"]
            elif furnace_group.technology.name == "BOF":
                furnace_group.technology.lcop = technology_lops["Avg BF-BOF"]

    def distance_to(self, location: Location) -> float:
        if self.location.distance_to_other_iso3 is not None and location.iso3 is not None:
            return self.location.distance_to_other_iso3[location.iso3]
        else:
            return geodesic((self.location.lat, self.location.lon), (location.lat, location.lon)).kilometers

    def __eq__(self, other) -> bool:
        return self.plant_id == other.plant_id

    def __hash__(self):
        return hash(self.plant_id)

    def __repr__(self) -> str:
        return f"Plant: <{self.plant_id}>"

    def get_furnace_group(self, furnace_group_id: str) -> FurnaceGroup:
        try:
            return next(fg for fg in self.furnace_groups if fg.furnace_group_id == furnace_group_id)
        except StopIteration:
            raise ValueError(f"Furnace group {furnace_group_id} not found in plant {self.plant_id}")

    def reset_capacity_changes(self):
        self.added_capacity = Volumes(0)
        self.removed_capacity = Volumes(0)
        self.removed_capacity_by_product = {}

    def close_furnace_group(self, furnace_group_id: str) -> None:
        """
        Close a furnace group by setting its status to "closed" and tracking the removed capacity.

        Steps:
            1. Retrieve the furnace group by ID
            2. Add the furnace group's capacity to the plant's removed_capacity tracking
            3. Update the furnace group's status to "closed"
            4. Log a FurnaceGroupClosed event to the event stream

        Args:
            furnace_group_id (str): Unique identifier of the furnace group to close.

        Side Effects:
            - Increases self.removed_capacity by the furnace group's capacity
            - Changes the furnace group's status to "closed"
            - Appends a FurnaceGroupClosed event to self.events
        """
        furnace_group = self.get_furnace_group(furnace_group_id)
        self.removed_capacity = Volumes(self.removed_capacity + furnace_group.capacity)
        product_name = (furnace_group.technology.product or "").lower()
        if product_name:
            existing_removed = self.removed_capacity_by_product.get(product_name, 0.0)
            self.removed_capacity_by_product[product_name] = existing_removed + float(furnace_group.capacity)
        furnace_group.status = "closed"
        self.events.append(events.FurnaceGroupClosed(furnace_group_id=furnace_group_id))

    def renovate_furnace_group(
        self,
        furnace_group_id: str,
        plant_lifetime: int,
        capex: float,
        capex_no_subsidy: float,
        cost_of_debt: float,
        cost_of_debt_no_subsidy: float,
        capex_subsidies: list[Subsidy] = [],
        debt_subsidies: list[Subsidy] = [],
    ) -> None:
        """
        Renovate a furnace group by resetting its lifetime and updating financial parameters.

        Steps:
        1. Retrieve the furnace group by ID.
        2. Set the last renovation date to January 1st of the current year.
        3. Reset the furnace group's lifetime to start from the current year with the new plant_lifetime duration.
        4. Change the technology CAPEX type to "brownfield" to reflect renovation economics.
        5. Update the CAPEX and cost of debt values (both subsidized and unsubsidized versions).
        6. Apply any provided CAPEX and debt subsidies to the furnace group's subsidy tracking.
        7. Log a FurnaceGroupRenovated event to the event stream.

        Args:
            furnace_group_id (str): Unique identifier of the furnace group to renovate.
            plant_lifetime (int): New lifetime in years for the renovated furnace group.
            capex (float): Subsidized capital expenditure for the renovation.
            capex_no_subsidy (float): Unsubsidized capital expenditure for the renovation.
            cost_of_debt (float): Subsidized cost of debt financing for the renovation.
            cost_of_debt_no_subsidy (float): Unsubsidized cost of debt financing for the renovation.
            capex_subsidies (list[Subsidy], optional): List of subsidies applied to CAPEX. Defaults to empty list.
            debt_subsidies (list[Subsidy], optional): List of subsidies applied to debt financing. Defaults to empty
                list.

        Side Effects:
            - Modifies the furnace group's last_renovation_date, lifetime, technology.capex_type, technology.capex,
              technology.capex_no_subsidy, cost_of_debt, cost_of_debt_no_subsidy, and applied_subsidies.
            - Appends a FurnaceGroupRenovated event to the plant's event list.

        Note:
            - The subsidized values (capex, cost_of_debt) represent the effective costs after applying subsidies,
              while the unsubsidized values preserve the baseline costs for comparison and reporting purposes.
            - The applied_subsidies dictionary tracks which specific subsidies were applied, enabling transparency
              in subsidy accounting and allowing for policy impact analysis.
            - Brownfield CAPEX is typically lower than greenfield CAPEX as it represents upgrading existing
              infrastructure rather than building new capacity from scratch.
        """
        furnace_group = self.get_furnace_group(furnace_group_id)
        current_year = furnace_group.lifetime.current

        # Mark the renovation date
        furnace_group.last_renovation_date = date(current_year, 1, 1)

        # Reset lifetime to start from current year with new duration
        furnace_group.lifetime = PointInTime(
            plant_lifetime=plant_lifetime,
            current=current_year,
            time_frame=TimeFrame(start=current_year, end=Year(current_year + plant_lifetime)),
        )

        # Switch to brownfield CAPEX (renovation costs are different from greenfield construction)
        furnace_group.technology.capex_type = "brownfield"

        # Update financial parameters with both subsidized and unsubsidized values
        furnace_group.technology.capex = capex
        furnace_group.technology.capex_no_subsidy = capex_no_subsidy
        furnace_group.cost_of_debt = cost_of_debt
        furnace_group.cost_of_debt_no_subsidy = cost_of_debt_no_subsidy

        # Track which subsidies were applied to this renovation for transparency and reporting
        furnace_group.applied_subsidies["capex"] = capex_subsidies
        furnace_group.applied_subsidies["debt"] = debt_subsidies

        # Log the renovation event for audit trail and event sourcing
        self.events.append(events.FurnaceGroupRenovated(furnace_group_id=furnace_group_id))

    def change_furnace_group_status_to_switching_technology(
        self,
        furnace_group_id: str,
        year_of_switch: int,
        cmd: commands.ChangeFurnaceGroupTechnology,
    ) -> None:
        """
        Mark a furnace group as scheduled for technology switch in a future year.

        Updates the furnace group's status to indicate it will undergo a technology change,
        and stores the switch command and target year for later execution.

        Args:
            furnace_group_id (str): Unique identifier of the furnace group to update.
            year_of_switch (int): The year when the technology switch will occur.
            cmd (commands.ChangeFurnaceGroupTechnology): Command object containing the details
                of the technology switch to be executed in the future.

        Side Effects:
            Updates the furnace group's status, future_switch_cmd, and future_switch_year attributes.

        Note: This method schedules the switch but does not execute it. The actual technology
        change occurs when change_furnace_group_technology is called in the target year.
        """
        # Retrieve the furnace group to update
        furnace_group = self.get_furnace_group(furnace_group_id)

        # Mark status as switching and store the future switch details
        furnace_group.status = "operating switching technology"
        furnace_group.future_switch_cmd = cmd
        furnace_group.future_switch_year = year_of_switch

    def change_furnace_group_technology(
        self,
        furnace_group_id: str,
        technology_name: str,
        plant_lifetime: int,
        lag: int,
        capex: float,
        capex_no_subsidy: float,
        cost_of_debt: float,
        cost_of_debt_no_subsidy: float,
        capex_subsidies: list[Subsidy] = [],
        debt_subsidies: list[Subsidy] = [],
        dynamic_business_case: list[PrimaryFeedstock] | None = None,
        bom: dict | None = None,
        chosen_reductant: str | None = None,
        legacy_years: int | None = None,
    ) -> None:
        """
        Change the technology of a specified furnace group with debt preservation.

        Updates the technology of the furnace group, preserving remaining debt from the old technology
        and cascading any legacy debt from previous technology switches. Resets the furnace group's
        lifetime based on the new plant lifetime and construction lag, and updates all economic parameters.

        Steps:
        1. Capture remaining debt from the current technology before the switch.
        2. Combine any existing legacy debt with the newly captured debt to handle cascading debt from
           multiple technology switches.
        3. Create new technology instance and update furnace group properties.
        4. Reset lifetime with construction lag and new plant lifetime.
        5. Store combined legacy debt and applied subsidies.
        6. Update operational parameters (energy VOPEX, fixed OPEX).
        7. Record technology change event.

        Args:
            furnace_group_id (str): Unique identifier of the furnace group to change.
            technology_name (str): Name of the new technology to switch to.
            plant_lifetime (int): Lifetime of the new plant in years.
            lag (int): Construction lag in years before the plant becomes operational.
            capex (float): Capital expenditure for the new technology (after subsidies).
            capex_no_subsidy (float): Capital expenditure without subsidies applied.
            cost_of_debt (float): Cost of debt percentage for the new technology (after subsidies).
            cost_of_debt_no_subsidy (float): Cost of debt percentage without subsidies applied.
            capex_subsidies (list[Subsidy]): List of subsidies applied to capital expenditure. Defaults to [].
            debt_subsidies (list[Subsidy]): List of subsidies applied to debt costs. Defaults to [].
            dynamic_business_case (list[PrimaryFeedstock] | None): Dynamic business case feedstock options.
                Defaults to None.
            bom (dict | None): Bill of materials for the new technology. If None, keeps existing BOM.
                Defaults to None.
            legacy_years (int | None): Years of the old technology's debt still outstanding, anchored
                to the decision-time lifetime. A deferred switch executes after the furnace group's
                lifetime has been replaced for the new cycle, so the caller must supply this; None
                falls back to the current lifetime's remaining years (same-tick execution).

        Side Effects:
            - Updates furnace group's technology, lifetime, status, utilization rate, and debt schedules.
            - Appends FurnaceGroupTechChanged event to the plant's event list.
            - Marks furnace group as created_by_PAM.

        Raises:
            ValueError: If fixed OPEX for the new technology is not found in technology_unit_fopex.

        Notes:
            - Preserves remaining debt from the old technology and adds it to the new technology's debt
              schedule, ensuring proper debt accumulation when switching technologies mid-repayment.
            - Handles cascading legacy debt from multiple consecutive technology switches.
            - Status is set to "construction" if lag > 0, otherwise "operating".
            - Utilization rate is reset to 0.0 during the technology switch.
            - Subsidies are applied separately to capex and debt costs. Both subsidized and unsubsidized
              values are stored for comparison and reporting purposes.
            - See debt_repayment_per_year property for full details on debt accumulation logic.
        """
        furnace_group = self.get_furnace_group(furnace_group_id)

        # Capture the current technology's remaining debt tail before switching.
        years_remaining = legacy_years if legacy_years is not None else furnace_group.lifetime.remaining_number_of_years
        old_remaining_debt = []
        if years_remaining > 0:
            old_remaining_debt = calculate_debt_repayment(
                total_investment=furnace_group.total_investment,
                equity_share=furnace_group.equity_share,
                lifetime=furnace_group.lifetime.plant_lifetime,
                cost_of_debt=furnace_group.cost_of_debt,
                lifetime_remaining=years_remaining,
            )

        # Combine existing legacy debt with newly captured debt for cascading debt handling
        if furnace_group.legacy_debt_schedule:
            combined_legacy = []
            max_years = max(len(furnace_group.legacy_debt_schedule), len(old_remaining_debt))
            for i in range(max_years):
                legacy_payment = (
                    furnace_group.legacy_debt_schedule[i] if i < len(furnace_group.legacy_debt_schedule) else 0.0
                )
                old_payment = old_remaining_debt[i] if i < len(old_remaining_debt) else 0.0
                combined_legacy.append(legacy_payment + old_payment)
            old_remaining_debt = combined_legacy

        # Create new technology instance
        technology = Technology(
            name=technology_name,
            bill_of_materials=None,
            product=furnace_group.technology.product,
            capex_type="greenfield",
            dynamic_business_case=dynamic_business_case,
            capex=capex,
            capex_no_subsidy=capex_no_subsidy,
        )

        # Update economic parameters
        furnace_group.cost_of_debt = cost_of_debt
        furnace_group.cost_of_debt_no_subsidy = cost_of_debt_no_subsidy

        # Apply new technology and reset operational state
        furnace_group.technology = technology
        if bom is not None:
            furnace_group.bill_of_materials = bom
        furnace_group.utilization_rate = 0.0
        current_year = furnace_group.lifetime.current
        furnace_group.lifetime = PointInTime(
            plant_lifetime=plant_lifetime,
            current=current_year,
            time_frame=TimeFrame(start=Year(current_year + lag), end=Year(current_year + lag + plant_lifetime)),
        )
        furnace_group.status = "construction" if lag > 0 else "operating"

        # Store combined legacy debt and applied subsidies
        furnace_group.legacy_debt_schedule = old_remaining_debt
        furnace_group.applied_subsidies["capex"] = capex_subsidies
        furnace_group.applied_subsidies["debt"] = debt_subsidies

        # Update operational parameters. The bare call rebuilds the reporting dicts with an
        # energy-only pick; the evaluated reductant from the NPV wins when provided (C7)
        furnace_group.generate_energy_vopex_by_reductant()
        if chosen_reductant is not None:
            furnace_group.chosen_reductant = chosen_reductant
        fopex = self.technology_unit_fopex.get(furnace_group.technology.name.lower())
        if fopex is None:
            raise ValueError(f"Fixed OPEX for technology {furnace_group.technology.name} not found")
        furnace_group.tech_unit_fopex = fopex

        # Mark as created by PAM and record the change event
        furnace_group.created_by_PAM = True
        self.events.append(
            events.FurnaceGroupTechChanged(
                furnace_group_id=furnace_group_id,
                technology_name=technology_name,
                capacity=furnace_group.capacity,
            )
        )

    def evaluate_furnace_group_strategy(
        self,
        furnace_group_id: str,
        plant_group: PlantGroup,
        market_price_series: dict,
        region_capex: dict[str, float],
        capex_renovation_share: dict[str, float],
        cost_of_debt_by_tech: dict[str, float],
        cost_of_equity_by_tech: dict[str, float],
        get_bom_from_avg_boms: Callable[
            [dict[str, float], str, float, str | None],
            tuple[dict[str, dict[str, dict[str, float]]] | None, float, str, dict[str, float]],
        ],
        reductant_score_series: Callable[..., ReductantScoreSeries],
        probabilistic_agents: bool,
        dynamic_business_cases: dict[str, list[PrimaryFeedstock]],
        chosen_emissions_boundary_for_carbon_costs: str,
        technology_emission_factors: list[TechnologyEmissionFactors],
        tech_to_product: dict[str, str],
        plant_lifetime: int,
        construction_time: int,
        current_year: Year,
        allowed_techs: dict[Year, list[str]],
        risk_free_rate: float,
        allowed_furnace_transitions: dict[str, list[str]],
        capacity_limit_steel: Volumes,
        capacity_limit_iron: Volumes,
        installed_capacity_in_year: Callable[[str], Volumes],
        new_plant_capacity_in_year: Callable[[str], Volumes],
        tech_capex_subsidies: dict[str, list[Subsidy]] = {},
        tech_opex_subsidies: dict[str, list[Subsidy]] = {},
        tech_debt_subsidies: dict[str, list[Subsidy]] = {},
        tech_energy_subsidies: dict[str, dict[str, list[Subsidy]]] = {},
        most_common_reductant_by_tech: dict[str, str] = {},
        get_co2_headroom: Callable[[str, int, float], float] | None = None,
        get_co2_need_by_name: Callable[[str, float, str], float] | None = None,
        co2_storage_diagnostics: Callable[[str, int], tuple[float, float, float]] | None = None,
    ) -> commands.Command | None:
        """
        Evaluate the economic strategy for a furnace group using NPV-based decision making.

        Steps:
        1. Check furnace group status (skip if pre-retirement)
        2. Check for forced closure (historic losses exceed threshold = CAPEX × capacity)
        3. Filter allowed technology transitions based on current year
        4. Calculate NPV for all technology options (adjusted for COSA when switching)
        5. Check if any technology option is profitable (NPV > 0)
        6. Identify optimal technology (maximum NPV)
        7. Select technology: NPV-weighted random draw over all valid options, incumbent
           included (probabilistic_agents=True), otherwise the max-NPV option
        8. If the incumbent is selected and lifetime expired: evaluate renovation (gated on plant_group.balance)
        9. If a different tech is selected: evaluate technology switch (gated on plant_group.balance)
        10. Check capacity limits for expansions/switches
        11. Apply probabilistic adoption decision (if enabled)
        12. Return appropriate command (ChangeFurnaceGroupTechnology, RenovateFurnaceGroup, CloseFurnaceGroup, or None)

        An expired lifetime always resolves to an action: any no-action exit (all NPVs
        non-positive, blocked/unaffordable/rejected switch) falls back to renovating the
        incumbent when it is value-creating and affordable, otherwise the group closes.
        ``lifetime.expired`` is true only in the renovation-boundary year, so returning
        None would roll the group into a fresh cycle without paying renovation capex.

        Args:
            furnace_group_id: Unique identifier for the furnace group
            plant_group: PlantGroup that owns this plant. Its ``balance`` is the treasury
                used to gate and debit renovation / switch equity.
            market_price_series: Time series of market prices for relevant commodities
            region_capex: Capital costs by technology for the region ($/tonne capacity), without subsidies
            capex_renovation_share: Fraction of greenfield CAPEX needed for renovation by technology (0-1)
            cost_of_debt_by_tech: Interest rate for debt financing by technology (before subsidies), for this plant's country
            cost_of_equity_by_tech: Required return on equity by technology, for this plant's country
            get_bom_from_avg_boms: Function to retrieve bill of materials for technologies
            probabilistic_agents: Whether to use probabilistic (weighted random) decision making
            dynamic_business_cases: Technology-specific feedstock options mapping technology names to primary feedstocks
            chosen_emissions_boundary_for_carbon_costs: Emissions scope for carbon pricing (e.g., "Scope 1", "Scope 2")
            technology_emission_factors: List of emission factors for each technology
            tech_to_product: Mapping from technology name to product type ("iron" or "steel")
            plant_lifetime: Expected lifetime of plant in years
            construction_time: Years required to construct new plant
            current_year: Current simulation year
            allowed_techs: Technologies allowed per year {year: [tech_names]}
            risk_free_rate: Risk-free interest rate for financial calculations
            allowed_furnace_transitions: Valid technology switches {from_tech: [to_techs]}
            capacity_limit_steel: Maximum steel capacity (tonnes) that can be added via expansions/switches this year
                (excludes new plants)
            capacity_limit_iron: Maximum iron capacity (tonnes) that can be added via expansions/switches this year
                (excludes new plants)
            installed_capacity_in_year: Function returning total installed capacity for a product type (including new
                plants and expansions)
            new_plant_capacity_in_year: Function returning new plant capacity for a product type (new plants only, excludes expansions)
            new_capacity_share_from_new_plants: Share of new capacity that comes from new plants vs expansions
            tech_capex_subsidies: Capital subsidies by technology {tech_name: [Subsidy]}
            tech_opex_subsidies: Operating subsidies by technology {tech_name: [Subsidy]}
            tech_debt_subsidies: Debt subsidies by technology {tech_name: [Subsidy]}
            tech_energy_subsidies: Energy carrier subsidies {carrier: {tech_name: [Subsidy]}},
                already collected for the plant's geography; forwarded to
                optimal_technology_name for candidate-technology energy pricing

        Returns:
            Command object (ChangeFurnaceGroupTechnology for switches, RenovateFurnaceGroup for renovations,
            CloseFurnaceGroup for closures) or None if no action is profitable/feasible

        Side Effects:
            Debits ``plant_group.balance`` via ``plant_group.deduct_equity`` when a
            renovation or technology switch is approved. No mutation of plant-level
            state.
        """
        logger = logging.getLogger(f"{__name__}.evaluate_furnace_group_strategy")
        furnace_group = self.get_furnace_group(furnace_group_id)

        active_energy_subs = {
            k: v
            for k, v in furnace_group.applied_subsidies.items()
            if k not in ("capex", "opex", "debt", "cost of debt") and v
        }
        if active_energy_subs:
            diffs = []
            for carrier in sorted(active_energy_subs):
                no_sub = furnace_group.energy_costs_no_subsidy.get(carrier, 0)
                with_sub = furnace_group.energy_costs.get(carrier, 0)
                diffs.append(f"{carrier} ${no_sub:.4f}->${with_sub:.4f}")
            logger.debug(f"[FG STRATEGY] FG:{furnace_group_id} {self.location.iso3} | {', '.join(diffs)}")

        # Log initial state for debugging
        logger.debug(f"[FG STRATEGY] ========== Starting evaluation for FG {furnace_group_id} ==========")
        logger.debug(f"[FG STRATEGY]   - Current year: {current_year}")
        logger.debug(f"[FG STRATEGY]   - Current tech: {furnace_group.technology.name}")
        logger.debug(f"[FG STRATEGY]   - Capacity: {furnace_group.capacity * T_TO_KT:,.0f} kt")
        logger.debug(f"[FG STRATEGY]   - Status: {furnace_group.status}")
        logger.debug(f"[FG STRATEGY]   - FG balance: ${furnace_group.balance:,.2f}")
        logger.debug(f"[FG STRATEGY]   - Historic balance: ${furnace_group.historic_balance:,.2f}")
        logger.debug(f"[FG STRATEGY]   - Plant group balance: ${plant_group.balance:,.2f}")
        logger.debug(f"[FG STRATEGY]   - Location: {self.location.iso3}")

        # ===== STAGE 1: Check furnace group status =====
        # Skip if already scheduled for retirement
        if furnace_group.status.lower() == "operating pre-retirement":
            logger.debug(f"[FG STRATEGY] DECISION - No action (FG status: {furnace_group.status})")
            return None

        # ===== STAGE 2: Check for forced closure =====
        # Close if historic losses exceed the CAPEX value (write-off point)
        current_capex_per_tonne = region_capex.get(furnace_group.technology.name)
        if current_capex_per_tonne is None:
            raise ValueError(f"CAPEX for technology {furnace_group.technology.name} not found in region_capex")

        closure_threshold = current_capex_per_tonne * furnace_group.capacity
        logger.debug("[FG STRATEGY] Closure threshold check:")
        logger.debug(f"[FG STRATEGY]   - CAPEX: ${current_capex_per_tonne:,.2f}/t")
        logger.debug(f"[FG STRATEGY]   - FG capacity: {furnace_group.capacity * T_TO_KT:,.2f} kt")
        logger.debug(f"[FG STRATEGY]   - Closure threshold (CAPEX × capacity): ${-closure_threshold:,.2f}")
        logger.debug(f"[FG STRATEGY]   - Historic balance: ${furnace_group.historic_balance:,.2f}")

        if furnace_group.historic_balance < -closure_threshold:
            logger.info(
                f"[FG STRATEGY] DECISION - CLOSE FG:{furnace_group.furnace_group_id} plant:{self.plant_id} "
                f"tech:{furnace_group.technology.name} (historic losses ${furnace_group.historic_balance:,.2f} "
                f"exceed threshold ${-closure_threshold:,.2f})"
            )
            return commands.CloseFurnaceGroup(plant_id=self.plant_id, furnace_group_id=furnace_group.furnace_group_id)

        # ===== STAGE 3: Filter allowed technology transitions =====
        # Intersect allowed techs for current year with valid furnace transitions
        allowed_techs_in_year = allowed_techs.get(current_year, None)
        if not allowed_techs_in_year:
            raise ValueError(f"[FG STRATEGY] No allowed techs in {current_year}")

        # Bind the score-series provider to this plant's location; GEO ("indi") sites keep
        # their own power/hydrogen prices, trajectory-scaled from the current year
        site_overrides: dict[str, float] | None = None
        if self.parent_gem_id.lower().startswith("indi"):
            site_prices = furnace_group.energy_costs_no_subsidy or furnace_group.energy_costs
            site_overrides = {
                carrier: site_prices[carrier] for carrier in ("electricity", "hydrogen") if carrier in site_prices
            }

        def score_series_for_tech(
            tech_name: str, output_shares: dict[str, float], start: Year, end: Year
        ) -> ReductantScoreSeries:
            return reductant_score_series(
                self.location,
                tech_name,
                output_shares,
                start,
                end,
                overrides=site_overrides,
                override_reference_year=current_year if site_overrides else None,
            )

        # P2 CO2 storage gate: drop CCS techs whose annual need exceeds country headroom,
        # sized with the reductant that would actually be built (operating-start-year pick)
        lookup_year = int(current_year) + construction_time
        headroom = (
            get_co2_headroom(self.location.iso3, lookup_year, 0.0) if get_co2_headroom is not None else float("inf")
        )
        dropped_ccs_techs: set[str] = set()
        gate_pick_by_tech: dict[str, str] = {}
        filtered_allowed_furnace_transitions: dict[str, list[str]] = {}
        for from_tech, candidates in allowed_furnace_transitions.items():
            kept: list[str] = []
            for tech in candidates:
                if tech not in allowed_techs_in_year:
                    continue
                if get_co2_need_by_name is not None:
                    # Cheap pre-check: the empty-reductant lookup falls back to the
                    # all-reductant maximum, so 0 means no CCS potential at all
                    if get_co2_need_by_name(tech, float(furnace_group.capacity), "") > 0.0:
                        if tech not in gate_pick_by_tech:
                            gate_series = score_series_for_tech(tech, {}, Year(lookup_year), Year(lookup_year + 1))
                            gate_pick_by_tech[tech] = gate_series.picks[0] if gate_series.picks else ""
                        need = get_co2_need_by_name(tech, float(furnace_group.capacity), gate_pick_by_tech[tech])
                        if need > 0.0 and need > headroom:
                            dropped_ccs_techs.add(tech)
                            continue
                kept.append(tech)
            filtered_allowed_furnace_transitions[from_tech] = kept

        if dropped_ccs_techs and co2_storage_diagnostics is not None:
            _firm, _reserved, limit = co2_storage_diagnostics(self.location.iso3, lookup_year)
            logger.info(
                f"[CO2 GATE] gate=P2 iso3={self.location.iso3} year={int(current_year)} "
                f"lookup_year={lookup_year} fg_id={furnace_group_id} plant_id={self.plant_id} "
                f"headroom={headroom:.0f} limit={limit:.0f} "
                f"dropped_ccs_techs={','.join(sorted(dropped_ccs_techs))} "
                f"dropped_count={len(dropped_ccs_techs)}"
            )

        logger.debug(
            f"[FG STRATEGY] Allowed transitions from {furnace_group.technology.name}: "
            f"{filtered_allowed_furnace_transitions.get(furnace_group.technology.name)}"
        )

        # ===== STAGE 4: Calculate NPV for all technology options =====
        logger.debug("[FG STRATEGY] === Calculating NPV for all technology options ===")
        logger.debug(f"[FG STRATEGY] Fixed opex for technologies: {self.technology_unit_fopex}")

        tech_npv_dict, npv_capex_dict, cosa, bom_dict, reductant_dict = furnace_group.optimal_technology_name(
            market_price_series=market_price_series,
            cost_of_debt_by_tech=cost_of_debt_by_tech,
            cost_of_equity_by_tech=cost_of_equity_by_tech,
            get_bom_from_avg_boms=get_bom_from_avg_boms,
            score_series_for_tech=score_series_for_tech,
            allowed_furnace_transitions=filtered_allowed_furnace_transitions,
            capex_dict=region_capex,
            capex_renovation_share=capex_renovation_share,
            technology_fopex_dict=self.technology_unit_fopex,
            carbon_cost_series=self.carbon_cost_series,
            dynamic_business_cases=dynamic_business_cases,
            tech_capex_subsidies=tech_capex_subsidies,
            tech_opex_subsidies=tech_opex_subsidies,
            current_year=current_year,
            chosen_emissions_boundary_for_carbon_costs=chosen_emissions_boundary_for_carbon_costs,
            technology_emission_factors=technology_emission_factors,
            tech_to_product=tech_to_product,
            plant_lifetime=plant_lifetime,
            construction_time=construction_time,
            tech_debt_subsidies=tech_debt_subsidies,
            tech_energy_subsidies=tech_energy_subsidies,
            risk_free_rate=risk_free_rate,
            most_common_reductant_by_tech=most_common_reductant_by_tech,
        )

        # Log NPV calculation results
        cosa_msg = f"${cosa:,.2f}" if cosa else "None"
        logger.debug("[FG STRATEGY] NPV results by tech (COSA adjusted):")
        for tech, npv in tech_npv_dict.items():
            logger.debug(f"[FG STRATEGY]   {tech}: NPV = ${npv:,.2f}")
        logger.debug(f"[FG STRATEGY] CAPEX by tech ($/t): {npv_capex_dict}")
        logger.debug(f"[FG STRATEGY] COSA: {cosa_msg}")

        def renovate_or_close_expired() -> commands.Command:
            """Resolve an expired lifetime when no switch happens: renovate or close.

            The incumbent is renovated when its NPV is positive and the group can fund
            the equity portion; otherwise the furnace group closes. The incumbent being
            absent from ``tech_npv_dict`` means renovation is no longer an allowed
            transition (phased out or CO2-gated), so the group closes too.
            """
            incumbent = furnace_group.technology.name
            incumbent_npv = tech_npv_dict.get(incumbent)
            if incumbent_npv is None or not math.isfinite(incumbent_npv) or incumbent_npv <= 0:
                logger.info(
                    f"[FG STRATEGY] DECISION - CLOSE FG plant_id={self.plant_id} "
                    f"plant_group_id={plant_group.plant_group_id} "
                    f"(lifetime expired, incumbent {incumbent} NPV not positive: {incumbent_npv})"
                )
                return commands.CloseFurnaceGroup(
                    plant_id=self.plant_id, furnace_group_id=furnace_group.furnace_group_id
                )

            renovation_capex_per_tonne = npv_capex_dict.get(incumbent)
            if renovation_capex_per_tonne is None:
                raise ValueError(f"CAPEX (renovation) for technology {incumbent} not found in NPV CAPEX dict")
            renovation_share = capex_renovation_share.get(incumbent)
            if renovation_share is None:
                raise ValueError(f"CAPEX renovation share for technology {incumbent} not found")
            incumbent_capex_no_subsidy = region_capex.get(incumbent)
            if incumbent_capex_no_subsidy is None:
                raise ValueError(f"CAPEX without subsidies for technology {incumbent} not found in region CAPEX dict")

            incumbent_capex_subs = filter_subsidies_for_year(tech_capex_subsidies.get(incumbent, []), current_year)
            incumbent_debt_subs = filter_subsidies_for_year(tech_debt_subsidies.get(incumbent, []), current_year)
            incumbent_cost_of_debt = cost_of_debt_by_tech.get(incumbent)
            if incumbent_cost_of_debt is None:
                raise ValueError(f"No cost of debt data for technology {incumbent} at plant {self.plant_id}")
            incumbent_cost_of_debt_with_subs = calculate_debt_with_subsidies(
                cost_of_debt=incumbent_cost_of_debt,
                debt_subsidies=incumbent_debt_subs,
                risk_free_rate=risk_free_rate,
            )

            renovate_cost = renovation_capex_per_tonne * furnace_group.capacity * furnace_group.equity_share
            logger.info(
                f"[FG STRATEGY] plant_id={self.plant_id} plant_group_id={plant_group.plant_group_id} "
                f"renovate_cost=${renovate_cost:,.2f} balance=${plant_group.balance:,.2f} "
                f"headroom=${plant_group.balance - renovate_cost:,.2f}"
            )
            if renovate_cost > plant_group.balance:
                logger.info(
                    f"[FG STRATEGY] DECISION - CLOSE FG plant_id={self.plant_id} "
                    f"plant_group_id={plant_group.plant_group_id} gate_pass=False "
                    f"(cannot afford renovation: ${renovate_cost:,.2f} > "
                    f"group balance ${plant_group.balance:,.2f})"
                )
                return commands.CloseFurnaceGroup(
                    plant_id=self.plant_id, furnace_group_id=furnace_group.furnace_group_id
                )

            # Debit the group treasury via the single-site capex mutation
            plant_group.deduct_equity(renovate_cost, reason="renovation")
            logger.info(
                f"[FG STRATEGY] DECISION - RENOVATE {incumbent} plant_id={self.plant_id} "
                f"plant_group_id={plant_group.plant_group_id} gate_pass=True "
                f"(cost: ${renovate_cost:,.2f}, new group balance: ${plant_group.balance:,.2f})"
            )
            return commands.RenovateFurnaceGroup(
                plant_id=self.plant_id,
                furnace_group_id=furnace_group.furnace_group_id,
                capex=renovation_capex_per_tonne,
                capex_no_subsidy=incumbent_capex_no_subsidy * renovation_share,
                cost_of_debt=incumbent_cost_of_debt_with_subs,
                cost_of_debt_no_subsidy=incumbent_cost_of_debt,
                capex_subsidies=incumbent_capex_subs,
                debt_subsidies=incumbent_debt_subs,
            )

        # ===== STAGE 5: Check if any technology option is profitable =====
        best_npv = max(tech_npv_dict.values(), default=0)
        logger.debug(f"[FG STRATEGY] Best NPV across all options: ${best_npv:,.2f}")

        if best_npv <= 0:
            if furnace_group.lifetime.expired:
                logger.debug("[FG STRATEGY] All NPVs negative or zero at end of lifetime")
                return renovate_or_close_expired()
            logger.debug("[FG STRATEGY] DECISION - No action (all NPVs negative or zero)")
            return None

        # ===== STAGE 6: Identify optimal technology =====
        current_tech = furnace_group.technology.name
        optimal_tech = max(tech_npv_dict, key=lambda k: tech_npv_dict[k])

        logger.debug(f"[FG STRATEGY] Current tech: {current_tech}, Optimal tech: {optimal_tech}")

        # ===== STAGE 7: Technology selection =====
        # The draw always runs: the incumbent competes on its own NPV alongside the
        # challengers — weighted random when probabilistic_agents, otherwise max NPV

        # Filter out invalid NPV values (infinite, NaN)
        valid_techs = {
            k: v for k, v in tech_npv_dict.items() if v is not None and not math.isinf(v) and not math.isnan(v)
        }
        logger.debug(f"[FG STRATEGY] Valid technology options: {list(valid_techs.keys())}")

        if not valid_techs:
            logger.warning(f"[FG STRATEGY] No valid NPV values found for plant {self.plant_id}")
            if furnace_group.lifetime.expired:
                return renovate_or_close_expired()
            return None

        weights = [max(v, 0) for v in valid_techs.values()]
        total_weight = sum(weights)
        if total_weight < 0.0001:
            if furnace_group.lifetime.expired:
                return renovate_or_close_expired()
            return None

        if probabilistic_agents:
            # Weighted random selection based on NPV (negative NPVs get zero weight)
            best_tech = random.choices(population=list(valid_techs.keys()), weights=weights, k=1)[0]
            shares = ", ".join(f"{k}={w / total_weight:.3f}" for k, w in zip(valid_techs.keys(), weights))
            logger.info(
                f"[FG STRATEGY] DRAW plant_id={self.plant_id} "
                f"furnace_group_id={furnace_group.furnace_group_id} incumbent={current_tech} "
                f"selected={best_tech} p({shares})"
            )
        else:
            best_tech = max(valid_techs, key=lambda k: valid_techs[k])
            logger.debug(f"[FG STRATEGY] Selected technology: {best_tech} (deterministic, max NPV)")

        # Final profitability check for selected technology
        if tech_npv_dict[best_tech] <= 0:
            logger.debug(
                f"[FG STRATEGY] DECISION - No action "
                f"(selected tech {best_tech} has NPV ${tech_npv_dict[best_tech]:,.2f} <= 0)"
            )
            if furnace_group.lifetime.expired:
                return renovate_or_close_expired()
            return None

        # ===== Filter and apply subsidies for selected technology =====
        all_capex_subs = tech_capex_subsidies.get(best_tech, [])
        all_debt_subs = tech_debt_subsidies.get(best_tech, [])
        capex_subs = filter_subsidies_for_year(all_capex_subs, current_year)
        debt_subs = filter_subsidies_for_year(all_debt_subs, current_year)

        cost_of_debt = cost_of_debt_by_tech.get(best_tech)
        if cost_of_debt is None:
            raise ValueError(f"No cost of debt data for technology {best_tech} at plant {self.plant_id}")

        cost_of_debt_with_subsidies = calculate_debt_with_subsidies(
            cost_of_debt=cost_of_debt,
            debt_subsidies=debt_subs,
            risk_free_rate=risk_free_rate,
        )

        # Log subsidy filtering results
        subsidy_details = []
        for subsidy in capex_subs:
            subsidy_details.append(
                f"[FG STRATEGY]     • {subsidy.subsidy_name}: "
                f"type={subsidy.subsidy_type}, amount={subsidy.subsidy_amount:.4f}, "
                f"years {subsidy.start_year}-{subsidy.end_year}"
            )
        for subsidy in debt_subs:
            subsidy_details.append(
                f"[FG STRATEGY]     • {subsidy.subsidy_name}: "
                f"type={subsidy.subsidy_type}, amount={subsidy.subsidy_amount:.4f}, "
                f"years {subsidy.start_year}-{subsidy.end_year}"
            )

        logger.debug(f"[FG STRATEGY] Filtering subsidies for year {current_year}:")
        logger.debug(f"[FG STRATEGY]   - Total CAPEX subsidies available: {len(all_capex_subs)}")
        logger.debug(f"[FG STRATEGY]   - Active CAPEX subsidies: {len(capex_subs)}")
        logger.debug(f"[FG STRATEGY]   - Total debt subsidies available: {len(all_debt_subs)}")
        logger.debug(f"[FG STRATEGY]   - Active debt subsidies: {len(debt_subs)}")
        logger.debug(f"[FG STRATEGY]   - Technology: {best_tech}")
        if subsidy_details:
            for detail in subsidy_details:
                logger.debug(detail)
        else:
            logger.debug("[FG STRATEGY]     (none)")

        # Get unsubsidized CAPEX for comparison
        original_capex_per_tonne = region_capex.get(best_tech)
        if original_capex_per_tonne is None:
            raise ValueError(f"CAPEX without subsidies for technology {best_tech} not found in region CAPEX dict")

        # ===== STAGE 8: Handle renovation scenario (best tech = current tech) =====
        if best_tech == current_tech:
            logger.debug("[FG STRATEGY] Best technology is current technology")

            if furnace_group.lifetime.expired:
                logger.debug("[FG STRATEGY] Furnace group lifetime expired, evaluating renovation")
                return renovate_or_close_expired()
            else:
                logger.debug(
                    f"[FG STRATEGY] DECISION - No action "
                    f"(current tech optimal, lifetime not expired: {furnace_group.lifetime.remaining_number_of_years} years left)"
                )
                return None

        # ===== STAGE 9: Handle technology switch scenario =====
        logger.debug(f"[FG STRATEGY] Evaluating technology switch from {current_tech} to {best_tech}")

        # Get subsidized greenfield CAPEX per tonne
        capex_per_tonne_opt = npv_capex_dict.get(best_tech)
        if capex_per_tonne_opt is None:
            raise ValueError(f"CAPEX (greenfield) for technology {best_tech} not found in NPV CAPEX dict")
        capex_per_tonne: float = capex_per_tonne_opt

        # Calculate switching cost (equity portion only)
        switch_cost = capex_per_tonne * furnace_group.capacity * furnace_group.equity_share

        logger.debug("[FG STRATEGY] Switch cost calculation:")
        logger.debug(f"[FG STRATEGY]   - Subsidized CAPEX: ${capex_per_tonne:,.2f}/t")
        logger.debug(f"[FG STRATEGY]   - Capacity: {furnace_group.capacity * T_TO_KT:,.0f} kt")
        logger.debug(f"[FG STRATEGY]   - Equity share: {furnace_group.equity_share:.1%}")
        logger.debug(f"[FG STRATEGY]   - Total cost: ${switch_cost:,.2f}")
        logger.info(
            f"[FG STRATEGY] plant_id={self.plant_id} plant_group_id={plant_group.plant_group_id} "
            f"switch_cost=${switch_cost:,.2f} balance=${plant_group.balance:,.2f} "
            f"headroom=${plant_group.balance - switch_cost:,.2f}"
        )

        # Check affordability against group treasury
        if switch_cost > plant_group.balance:
            logger.debug(
                f"[FG STRATEGY] Cannot afford switch plant_id={self.plant_id} "
                f"plant_group_id={plant_group.plant_group_id} gate_pass=False "
                f"(${switch_cost:,.2f} > group balance ${plant_group.balance:,.2f})"
            )
            if furnace_group.lifetime.expired:
                return renovate_or_close_expired()
            logger.debug("[FG STRATEGY] DECISION - No action (switch unaffordable)")
            return None

        # ===== STAGE 10: Probabilistic adoption decision =====
        # Simulates real-world hesitation/uncertainty in technology adoption
        # Acceptance probability = exp(-switch_cost / NPV)
        # Higher cost relative to benefit → lower probability
        if probabilistic_agents:
            accept_prob = math.exp(-switch_cost / tech_npv_dict[best_tech])
            logger.debug("[FG STRATEGY] Probabilistic decision mode:")
            logger.debug(f"[FG STRATEGY]   - Acceptance probability: {accept_prob:.2%}")
            logger.debug(f"[FG STRATEGY]   - Cost/NPV ratio: {switch_cost / tech_npv_dict[best_tech]:.2f}")
        else:
            accept_prob = 1.0
            logger.debug("[FG STRATEGY] Deterministic decision mode (100% acceptance)")

        # Make final decision with random draw
        random_draw = random.random()
        fg_is_ccs_or_ccu = furnace_group.is_ccs_or_ccu

        if not fg_is_ccs_or_ccu and random_draw < accept_prob:
            # ===== STAGE 11: Check capacity limits =====
            # Ensure switch doesn't exceed annual capacity expansion limits
            if (
                capacity_limit_steel is not None
                and capacity_limit_iron is not None
                and installed_capacity_in_year is not None
            ):
                product_opt = tech_to_product.get(best_tech)
                if product_opt is None:
                    raise ValueError(f"Technology {best_tech} not found in technology_to_product")
                tech_product: str = product_opt

                total_installed_capacity = installed_capacity_in_year(tech_product)
                new_plant_capacity = new_plant_capacity_in_year(tech_product)
                expansion_and_switch_capacity = total_installed_capacity - new_plant_capacity

                if tech_product == "iron":
                    expansion_limit = capacity_limit_iron
                elif tech_product == "steel":
                    expansion_limit = capacity_limit_steel
                else:
                    raise ValueError(f"Unknown product type: '{tech_product}' for technology: '{best_tech}'")

                logger.debug("[FG STRATEGY] CAPACITY LIMIT check:")
                logger.debug(f"[FG STRATEGY]   - Product: {tech_product}")
                logger.debug(f"[FG STRATEGY]   - New plants: {new_plant_capacity * T_TO_KT:,.0f} kt")
                logger.debug(
                    f"[FG STRATEGY]   - Expansions/switches so far: {expansion_and_switch_capacity * T_TO_KT:,.0f} kt"
                )
                logger.debug(f"[FG STRATEGY]   - To add (switch): {furnace_group.capacity * T_TO_KT:,.0f} kt")
                logger.debug(
                    f"[FG STRATEGY]   - Total after: {(expansion_and_switch_capacity + furnace_group.capacity) * T_TO_KT:,.0f} kt"
                )
                logger.debug(f"[FG STRATEGY]   - Expansion/switch limit: {expansion_limit * T_TO_KT:,.0f} kt")

                if expansion_and_switch_capacity + furnace_group.capacity > expansion_limit:
                    logger.warning(
                        f"[FG STRATEGY] BLOCKED - Expansion/switch capacity limit reached for {tech_product}: "
                        f"{expansion_and_switch_capacity * T_TO_KT:,.0f} kt + {furnace_group.capacity * T_TO_KT:,.0f} kt > "
                        f"{expansion_limit * T_TO_KT:,.0f} kt"
                    )
                    if furnace_group.lifetime.expired:
                        return renovate_or_close_expired()
                    return None

            # ===== STAGE 12: Execute technology switch =====
            # Validate the command inputs before debiting: an abort must leave the wallet intact
            npv_value = tech_npv_dict.get(best_tech)
            if npv_value is None:
                raise ValueError(f"NPV for technology {best_tech} not found in NPV dict")

            if cosa is None:
                raise ValueError(
                    f"COSA value is None when attempting to switch technology: {current_tech} to {best_tech}"
                )

            bom = bom_dict.get(best_tech)
            if bom is None:
                logger.error(
                    "[FG STRATEGY] Missing BOM for %s. Available BOM keys: %s",
                    best_tech,
                    list(bom_dict.keys()),
                )
                raise ValueError(f"BOM for technology {best_tech} not found in BOM dict")
            materials = bom.get("materials", {})
            energy = bom.get("energy", {})
            if not materials:
                logger.error(
                    "[FG STRATEGY] Empty BOM for %s at switch time (materials=%d, energy=%d) - BOM keys: %s",
                    best_tech,
                    len(materials),
                    len(energy),
                    list(bom.keys()),
                )
                raise ValueError(f"Empty BOM for technology {best_tech} at switch time")

            plant_group.deduct_equity(switch_cost, reason="switch")
            logger.info(
                f"[FG STRATEGY] DECISION - SWITCH TECHNOLOGY {current_tech} → {best_tech} "
                f"plant_id={self.plant_id} plant_group_id={plant_group.plant_group_id} gate_pass=True "
                f"(NPV: ${tech_npv_dict.get(best_tech, 0):,.2f}, cost: ${switch_cost:,.2f}, "
                f"new group balance: ${plant_group.balance:,.2f})"
            )

            return commands.ChangeFurnaceGroupTechnology(
                plant_id=self.plant_id,
                furnace_group_id=furnace_group.furnace_group_id,
                technology_name=best_tech,
                old_technology_name=current_tech,
                npv=npv_value,
                cosa=cosa,
                utilisation=furnace_group.utilization_rate,
                capex=capex_per_tonne,
                capex_no_subsidy=original_capex_per_tonne,
                capacity=furnace_group.capacity,
                bom=bom,
                chosen_reductant=reductant_dict[best_tech],
                remaining_lifetime=furnace_group.lifetime.remaining_number_of_years,
                cost_of_debt=cost_of_debt_with_subsidies,
                cost_of_debt_no_subsidy=cost_of_debt,
                capex_subsidies=capex_subs,
                debt_subsidies=debt_subs,
            )
        else:
            # Probabilistic rejection or CCS/CCU equipped furnace
            rejection_reason = (
                "furnace has CCS/CCU"
                if fg_is_ccs_or_ccu
                else f"probabilistic rejection ({random_draw:.2%} >= {accept_prob:.2%})"
            )
            if furnace_group.lifetime.expired:
                logger.debug(f"[FG STRATEGY] Switch not taken ({rejection_reason}), lifetime expired")
                return renovate_or_close_expired()
            logger.debug(f"[FG STRATEGY] DECISION - No action ({rejection_reason})")
            return None

    def generate_new_furnace(
        self,
        technology_name: str,
        product: str,
        current_year: int,
        capex: float,
        capex_no_subsidy: float,
        cost_of_debt: float,
        cost_of_debt_no_subsidy: float,
        capacity: float,
        lag: int,
        status: str,
        util_rate: float,
        plant_lifetime: int,
        chosen_reductant: str,
        dynamic_business_case: list[PrimaryFeedstock] | None = None,
        bill_of_materials: dict[str, dict[str, dict[str, float]]] | None = None,
        energy_costs: dict[str, float] | None = None,
        output_energy_costs: dict[str, float] | None = None,
        energy_costs_no_subsidy: dict[str, float] | None = None,
        **kwargs,
    ) -> FurnaceGroup:
        """
        Generate a new furnace group for the specified technology in the plant executing the function.

        Steps:
        1. Create a Technology object with the specified parameters and greenfield capex type.
        2. Generate a unique furnace ID and calculate the active year based on construction lag.
        3. Initialize a FurnaceGroup with the technology, capacity, status, and financial parameters.
        4. Set energy costs (either inherited from plant or explicitly provided for new plants).
        5. Generate energy VOPEX by reductant and set fixed OPEX based on technology.
        6. Mark the furnace group as created by PAM (Plant Asset Model).

        Pure factory; does not mutate any balance. Equity debit for expansion is
        handled in ``add_furnace_group_to_plant`` via ``plant_group.deduct_equity``.
        New-plant construction does not debit any treasury.

        Args:
            technology_name (str): The name of the technology for the new furnace group.
            product (str): The product type for the new furnace group.
            current_year (int): The current year in the simulation.
            capex (float): The capital expenditure for the new furnace group (technology and region-specific).
            capex_no_subsidy (float): The capital expenditure without subsidies.
            cost_of_debt (float): The cost of debt for financing the new furnace group.
            cost_of_debt_no_subsidy (float): The cost of debt without subsidies.
            capacity (int): The capacity of the new furnace group in tonnes.
            lag (int): The lag time in years before the new furnace group becomes operational.
            status (str): The initial status of the new furnace group; "construction" if coming from capacity
                expansion, "considered" if coming from new plant opening.
            util_rate (float): The utilization rate for the new furnace group; set to 0.0 if coming from capacity
                expansion, since it will be ramped up over time by the trade module.
            plant_lifetime (int): The expected lifetime of the plant in years.
            chosen_reductant (str): The reductant type chosen for the furnace group (e.g., "hydrogen").
            dynamic_business_case (list[PrimaryFeedstock] | None): Optional list of primary feedstocks for dynamic
                business case modeling.
            bill_of_materials (dict[str, dict[str, dict[str, float]]] | None): Optional bill of materials for the
                furnace group.
            energy_costs (dict[str, float] | None): Optional energy costs; must be passed explicitly for
                new plants, otherwise inherited from the plant.
            output_energy_costs (dict[str, float] | None): Optional output-side energy costs for
                by-product revenue pricing. If None, defaults to a copy of energy_costs.
            energy_costs_no_subsidy (dict[str, float] | None): Optional pre-subsidy energy costs
                for all carriers.
            **kwargs: Additional keyword arguments for FurnaceGroup initialization.

        Returns:
            FurnaceGroup: A new FurnaceGroup object with the specified technology and parameters.

        Side Effects:
            Increments the plant's furnace ID counter.
        """

        technology = Technology(
            name=technology_name,
            bill_of_materials=None,
            product=product,
            dynamic_business_case=dynamic_business_case,
            capex_type="greenfield",
            capex=capex,
            capex_no_subsidy=capex_no_subsidy,
        )

        new_furnace_id = self.get_new_furnance_id_number()
        active_year = current_year + lag

        furnace_group = FurnaceGroup(
            furnace_group_id=new_furnace_id,
            capacity=Volumes(capacity),
            status=status,
            last_renovation_date=date(current_year, 1, 1),
            technology=technology,
            historical_production={},
            utilization_rate=util_rate,
            chosen_reductant=chosen_reductant,
            cost_of_debt=cost_of_debt,
            cost_of_debt_no_subsidy=cost_of_debt_no_subsidy,
            lifetime=PointInTime(
                current=Year(current_year),
                time_frame=TimeFrame(start=Year(active_year), end=Year(active_year + plant_lifetime)),
                plant_lifetime=plant_lifetime,
            ),
            **kwargs,
        )
        furnace_group.bill_of_materials = bill_of_materials

        # Set energy costs via set_energy_costs for normalisation (abs, normalize_name)
        gnf_logger = logging.getLogger(f"{__name__}.generate_new_furnace")
        if energy_costs:
            furnace_group.set_energy_costs(**energy_costs)
            if output_energy_costs:
                furnace_group.output_energy_costs = output_energy_costs
            if energy_costs_no_subsidy:
                furnace_group.energy_costs_no_subsidy = energy_costs_no_subsidy
            gnf_logger.debug(
                "[NEW FG] %s tech=%s | input=%d output=%d no_sub=%d carriers",
                furnace_group.furnace_group_id,
                technology_name,
                len(furnace_group.energy_costs),
                len(furnace_group.output_energy_costs),
                len(furnace_group.energy_costs_no_subsidy),
            )
        else:
            furnace_group.set_energy_costs(**self.energy_costs.copy())
            gnf_logger.debug(
                "[NEW FG] %s tech=%s | inherited %d carriers from plant",
                furnace_group.furnace_group_id,
                technology_name,
                len(furnace_group.energy_costs),
            )
        # The bare call rebuilds the reporting dicts with an energy-only pick; the
        # evaluated reductant passed by the caller wins (C7)
        furnace_group.generate_energy_vopex_by_reductant()
        furnace_group.chosen_reductant = chosen_reductant

        # Set fixed OPEX from technology lookup table
        fopex_value = self.technology_unit_fopex.get(technology_name.lower())
        if fopex_value:
            furnace_group.tech_unit_fopex = float(fopex_value)
        else:
            raise ValueError(f"Fixed OPEX for technology {technology_name} not found")

        # Mark as model-generated (not from historical data)
        furnace_group.created_by_PAM = True

        return furnace_group

    def furnace_group_added(
        self, furnace_group_id: str, plant_id: str, technology_name: str, capacity: float, is_new_plant: bool = False
    ) -> None:
        """
        Record a FurnaceGroupAdded event to the plant's event list.

        Args:
            furnace_group_id (str): Unique identifier for the furnace group.
            plant_id (str): Unique identifier for the plant.
            technology_name (str): Name of the technology used in the furnace group.
            capacity (int): Capacity of the furnace group in tonnes.
            is_new_plant (bool): Whether this is part of a new plant opening. Defaults to False.

        Side Effects:
            - Appends a FurnaceGroupAdded event to the plant's events list.
        """
        self.events.append(
            events.FurnaceGroupAdded(
                furnace_group_id=furnace_group_id,
                plant_id=plant_id,
                technology_name=technology_name,
                capacity=capacity,
                is_new_plant=is_new_plant,
            )
        )

    def report_cost_breakdown(self):
        """
        Generate a cost breakdown report for each technology in the plant.

        Steps:
        1. Initialize cost and bill of materials breakdowns by technology.
        2. Iterate over all furnace groups with non-zero capacity.
        3. For each furnace group, calculate principal debt, interest, and O&M costs.
        4. Accumulate production and merge bill of materials by technology.
        5. Integrate bill of materials into final cost breakdown structure.

        Returns:
            dict: A dictionary mapping technology names to their cost breakdown details, with keys
                "Principal_debt", "Interest", "O&M", "Production", and "Bill of Materials".
        """
        from .calculate_costs import calculate_debt_report

        cost_breakdown_by_tech = defaultdict(lambda: defaultdict(float))
        bom_breakdown_by_tech = defaultdict(lambda: defaultdict(float))
        final_breakdown = {}

        # Accumulate costs and bill of materials by technology
        for fg in self.furnace_groups:
            if fg.capacity == 0:
                continue
            tech = fg.technology.name

            # Calculate financial cost components
            principal_debt, interest = calculate_debt_report(
                total_investment=fg.total_investment,
                lifetime_expired=fg.lifetime.expired,
                equity_share=fg.equity_share,
                lifetime_years=fg.lifetime.number_of_years,
                years_elapsed=fg.lifetime.elapsed_number_of_years,
                cost_of_debt=fg.cost_of_debt,
            )
            cost_breakdown_by_tech[tech]["Principal_debt"] += principal_debt
            cost_breakdown_by_tech[tech]["Interest"] += interest
            cost_breakdown_by_tech[tech]["O&M"] += fg.unit_fopex * fg.production
            cost_breakdown_by_tech[tech]["Production"] += fg.production

            # Merge bill of materials for this technology
            bill_of_materials = fg.report_bill_of_materials()
            if bill_of_materials:
                merge_two_dictionaries(bom_breakdown_by_tech[tech], bill_of_materials)

        # Integrate bill of materials into final cost breakdown structure
        for tech, cost_data in cost_breakdown_by_tech.items():
            cost_data["Bill of Materials"] = dict(bom_breakdown_by_tech[tech])
            final_breakdown[tech] = dict(cost_data)

        return final_breakdown

    def get_new_furnance_id_number(self) -> str:
        """
        Generate the next available furnace group ID for a new furnace group in this plant.

        Steps:
            1. If no furnace groups exist yet, return the plant ID as the first furnace group ID
            2. Otherwise, collect all furnace group numbers (ignoring sintering groups)
            3. Sort the numbers and increment the highest by 1
            4. Verify the new ID is not already in use

        Returns:
            new_id (str): Next available furnace group ID in format "P000000000001_N" where N is the sequence number.

        Side Effects:
            Raises ValueError if the generated ID already exists in self.furnace_groups.

        Notes:
            - First furnace group uses the plant ID directly (e.g., "P000000000001")
            - Subsequent furnace groups append "_N" where N starts at 2 (e.g., "P000000000001_2", "P000000000001_3")
            - Sintering furnace groups are excluded from numbering sequence
        """

        # If there are no furnace groups yet, return the plant id as the first furnace group id
        if len(self.furnace_groups) == 0:
            new_id = self.plant_id

        # If there are more then one furnace groups, collect all furnace group numbers and sort them to find
        # the next available number, ignoring sintering furnace groups
        else:
            num_indices = []
            for fg_id in self.furnace_groups:
                if "_" in fg_id.furnace_group_id:
                    if "sintering" in fg_id.furnace_group_id.lower():
                        continue
                    num_indices.append(int(fg_id.furnace_group_id.split("_")[-1]))
                if len(num_indices) > 0:
                    sorted_num_indices = sorted(num_indices)
                    new_index = int(sorted_num_indices[-1]) + 1
                    new_id = self.plant_id + f"_{str(new_index)}"
                # Treat the case where there is only a single furnace group in the plant with the plant id as its index
                else:
                    new_id = self.plant_id + "_2"

        # Verify that the new id is not already in use
        for fg in self.furnace_groups:
            if fg.furnace_group_id == new_id:
                raise ValueError(f"Duplicate furnace group id: {new_id} in plant {self.plant_id}")

        return new_id

    def update_furnace_tech_unit_fopex(self) -> None:
        """
        Update the unit fixed OPEX for all furnace groups in the plant based on their technology.
        -----------
        Side Effects
            Updates the unit fixed OPEX attribute of each furnace group in the plant.

        """
        for fg in self.furnace_groups:
            tech_unit_fopex = self.technology_unit_fopex.get(fg.technology.name.lower())
            if tech_unit_fopex is None:
                raise ValueError(
                    f"Unit FOPEX for technology {fg.technology.name} not found (for plant {self.plant_id} country {self.location.iso3})"
                )
            fg.tech_unit_fopex = float(tech_unit_fopex)

    def update_furnace_hydrogen_costs(self, capped_hydrogen_cost_dict: dict[str, float]) -> None:
        """
        Update hydrogen costs for all furnace groups in the plant using country-specific capped prices.

        Args:
            capped_hydrogen_cost_dict (dict[str, float]): Dictionary mapping ISO3 country codes to capped hydrogen prices in USD/kg.
                Prices are converted internally to USD/t before being written to `energy_costs`.

        Side Effects:
            Updates the "hydrogen" entry in energy_costs dictionary for each furnace group in the plant.

        Notes:
            Raises ValueError if the plant's ISO3 code is not found in the capped_hydrogen_cost_dict.
        """
        logger = logging.getLogger(f"{__name__}.Plant.update_furnace_hydrogen_costs")
        capped_lcoh_usd_per_kg = self.location.resolve(capped_hydrogen_cost_dict, what="hydrogen price")
        if capped_lcoh_usd_per_kg is None:
            raise ValueError(f"No hydrogen price calculated for {self.location.geo_key} (plant {self.plant_id})")
        hydrogen_price_usd_per_t = capped_lcoh_usd_per_kg * T_TO_KG

        logger.debug(
            "[ENERGY UNITS] Plant %s (%s): H2 LCOH %.2f USD/kg -> %.2f USD/t",
            self.plant_id,
            self.location.iso3,
            capped_lcoh_usd_per_kg,
            hydrogen_price_usd_per_t,
        )

        for fg in self.furnace_groups:
            fg.energy_costs["hydrogen"] = hydrogen_price_usd_per_t
            fg.output_energy_costs["hydrogen"] = hydrogen_price_usd_per_t
            fg.energy_costs_no_subsidy["hydrogen"] = hydrogen_price_usd_per_t

    def aggregate_average_utilisation_rate(self):
        """
        Calculate the capacity-weighted average utilization rate for each product type across all furnace groups.

        Steps:
            1. Loop through all furnace groups and group them by product type (steel, iron, etc.)
            2. For each product, accumulate total capacity and total production (capacity × utilization rate)
            3. Calculate the average utilization rate as total production divided by total capacity
            4. Handle zero-capacity edge case by returning 0 utilization rate

        Returns:
            agg_util_rate (dict[str, dict[str, float]]): Dictionary mapping product names to dictionaries containing:
                - "total_capacity": Sum of all furnace group capacities for this product
                - "total_production": Sum of all production (capacity × utilization_rate) for this product
                - "utilisation_rate": Weighted average utilization rate (total_production / total_capacity)

        Notes:
            - Product names are normalized to lowercase
            - Returns 0 utilization rate if total capacity is 0 to avoid division by zero
        """

        agg_util_rate = {}
        for fg in self.furnace_groups:
            # if fg.technology.product == product:
            product = fg.technology.product.lower()
            if product not in agg_util_rate:
                agg_util_rate[product] = {"total_capacity": 0, "total_production": 0}
            agg_util_rate[product]["total_capacity"] += fg.capacity
            agg_util_rate[product]["total_production"] += fg.utilization_rate * fg.capacity
        for product in agg_util_rate:
            agg_util_rate[product]["utilisation_rate"] = (
                agg_util_rate[product]["total_production"] / agg_util_rate[product]["total_capacity"]
                if agg_util_rate[product]["total_capacity"] > 0
                else 0
            )

        return agg_util_rate

    def update_furnace_technology_emission_factors(
        self, technology_emission_factors: list[TechnologyEmissionFactors]
    ) -> None:
        """
        Update emission factors for all furnace groups in this plant.

        Propagates technology-specific emission factors to each furnace group, enabling
        them to calculate emissions based on their feedstock consumption and technology type.

        Args:
            technology_emission_factors (list[TechnologyEmissionFactors]): List of TechnologyEmissionFactors objects
                containing emission intensities for different materials, technologies, and emission boundaries.

        Side Effects:
            Sets technology_emission_factors for each furnace group in the plant.

        Notes:
            - Called during environment initialization or when emission factors are updated.
            - Furnace groups use these factors in set_emissions_based_on_allocated_volumes().
        """
        for fg in self.furnace_groups:
            fg.technology_emission_factors = technology_emission_factors

    def set_carbon_cost_series(self, carbon_cost_series: dict[Year, float]) -> None:
        """
        Set the carbon cost series for the plant.

        Updates the plant's carbon cost series with the provided year-to-cost mapping.
        This series is used to apply carbon costs to furnace groups during simulation.

        Args:
            carbon_cost_series (dict[Year, float]): Dictionary mapping Year objects to carbon costs ($/tCO2).

        Side Effects:
            Updates self.carbon_cost_series with the provided values.
        """
        # Convert string keys to Year objects if needed, then update the series
        if hasattr(carbon_cost_series, "carbon_cost"):
            # Accept CarbonCostSeries instances directly
            year_dict = carbon_cost_series.carbon_cost
        else:
            year_dict = {Year(int(k)): v for k, v in carbon_cost_series.items()}
        self.carbon_cost_series.update(year_dict)

    def update_furnace_group_carbon_costs(self, year: Year, chosen_emissions_boundary_for_carbon_costs: str) -> None:
        """
        Update carbon costs for all furnace groups in this plant for the specified year.

        Uses the plant's carbon cost series to set the carbon costs on each furnace group.
        This ensures that furnace groups have current carbon costs for trade model calculations.

        Args:
            year (Year): The year to set carbon costs for.
            chosen_emissions_boundary_for_carbon_costs (str): The emissions boundary to use for carbon cost calculations

        Side Effects:
            Updates carbon costs for all furnace groups via set_carbon_costs_for_emissions().

        Notes:
            - If the year is not in the carbon cost series, defaults to $0/tCO2.
            - Carbon costs affect furnace group variable costs in the trade optimization model.
        """
        # Retrieve carbon price for the year, defaulting to 0 if not found
        carbon_price = self.carbon_cost_series.get(year, 0.0)

        # Apply carbon price to all furnace groups
        for furnace_group in self.furnace_groups:
            furnace_group.set_carbon_costs_for_emissions(carbon_price, chosen_emissions_boundary_for_carbon_costs)

    @property
    def emissions(self) -> dict[str, dict[str, float]]:
        """Calculate total plant emissions by aggregating all furnace group emissions.

        Sums emissions across all furnace groups, organized by boundary and scope.
        Handles missing or None emissions gracefully, and converts NaN values to 0.

        Returns:
            Dictionary with structure:
                {
                    boundary_name: {  # e.g., "plant_boundary", "supply_chain"
                        scope: float  # Total tCO2e for this scope, e.g., "scope_1"
                    }
                }

        Notes:
            - Skips furnace groups with None emissions.
            - Initializes boundary/scope entries to 0 before aggregation.
            - Converts math.isnan() values to 0.0 to prevent calculation errors.
            - Returns empty dict if no furnace groups have emissions data.
        """
        emissions: dict[str, dict[str, float]] = {}
        for furnace_group in self.furnace_groups:
            if furnace_group.emissions is None:
                continue
            for boundary, scope_dict in furnace_group.emissions.items():
                if boundary not in emissions:
                    emissions[boundary] = {}
                for scope, value in scope_dict.items():
                    if scope not in emissions[boundary]:
                        emissions[boundary][scope] = 0
                    if math.isnan(value):
                        value = 0
                    emissions[boundary][scope] += value
        return emissions

    @property
    def energy_costs(self) -> dict[str, float]:
        """
        Calculate the energy cost for the plant based on the furnace groups.
        """
        return self.furnace_groups[-1].energy_costs


def get_new_plant_id(existent_plant_ids: list[str] = []) -> str:
    """
    Generate a new plant id for a potential new plant in the plant group. Adds 1 to the number of the last plant
    id in the repository. Plant ids are formatted as P000000000001, P000000000002, etc.
    """
    p_plant_ids = [pid for pid in existent_plant_ids if pid.startswith("P")]
    if len(p_plant_ids) > 0:
        sorted_plant_ids = sorted(p_plant_ids, key=lambda x: int(x[1:]))
        new_id = f"P{str(int(sorted_plant_ids[-1][1:]) + 1).zfill(12)}"
        return new_id
    else:
        return "P000000000001"


class PlantGroup:
    def __init__(self, *, plant_group_id: str, plants: list[Plant]) -> None:
        """
        A plant group is the treasury owner for its constituent plants.

        Attributes:
            plant_group_id: authoritative group identifier (e.g. a corporate GEM id
                or ``indi_<iso3>`` for per-country indi groups).
            plants: plants currently owned by the group.
            balance: stored ledger (not derived). Credited by ``sweep_fg_balances_to_group``
                from operational P&L; debited by ``deduct_equity`` for renovation,
                switch, and expansion. Greenfield construction does not debit.
        """
        self.plant_group_id = plant_group_id
        self.plants = plants
        self.balance = 0.0
        self.events: list[events.Event] = []

    def update_hot_metal_access(self, hot_metal_radius: float) -> None:
        """Flag BOF furnace groups (and their plants) that a hot-metal producer in the group can feed.

        Args:
            hot_metal_radius: Maximum distance (km) over which hot metal can be transported.
        """
        for plant in self.plants:
            plant.has_hot_metal_access = False  # Initialize hot metal access for the plant
            for fg in plant.furnace_groups:
                if fg.technology.name.lower() == "bof":
                    fg.has_hot_metal_access = False  # Initialize access flag
                    for other_plant in self.plants:
                        for other_fg in other_plant.furnace_groups:
                            if (
                                other_fg.produces_hot_metal
                                and plant.distance_to(other_plant.location) <= hot_metal_radius
                            ):
                                fg.has_hot_metal_access = True
                                plant.has_hot_metal_access = True

    def deduct_equity(self, amount: float, reason: str) -> None:
        """
        Debit equity against the group treasury.

        Sole mutation site for capex debits. Called by renovation and switch from
        ``evaluate_furnace_group_strategy`` and by expansion from the
        ``add_furnace_group_to_plant`` handler. Does not check affordability — gates
        live at the call sites.

        Args:
            amount: equity amount in USD (``capex_per_tonne × capacity × equity_share``).
            reason: one of ``"renovation"``, ``"switch"``, ``"expansion"``. Used only
                for the structured log line today; forward-compatible for per-reason
                tracing or aggregation.

        Notes:
            New-plant construction does not debit any treasury — the 20% equity is
            external investor money outside the simulated ledger.
        """
        deduct_logger = logging.getLogger(f"{__name__}.deduct_equity")
        balance_before = self.balance
        self.balance -= amount
        deduct_logger.info(
            "[DEDUCT EQUITY] reason=%s amount=%.2f plant_group_id=%s balance_before=%.2f balance_after=%.2f",
            reason,
            amount,
            self.plant_group_id,
            balance_before,
            self.balance,
        )

    def sweep_fg_balances_to_group(self, market_price: dict[str, float], active_statuses: list[str]) -> None:
        """
        Sweep annual operational P&L from all furnace groups into the group treasury.

        For each plant in the group, iterates the plant's furnace groups. Skips FGs
        that are inactive, have zero capacity, are classified as "other" technology,
        or produce a product not in the market. For each eligible FG, runs
        ``fg.update_balance_sheet`` to set its annual P&L, adds the result to the
        group's ``balance``, then zeroes ``fg.balance`` so the sweep is idempotent.

        Args:
            market_price: Dictionary mapping product names (lowercase) to market prices.
            active_statuses: Status strings considered active for balance updates.

        Side Effects:
            - Sets ``fg.balance`` to the annual P&L (via ``update_balance_sheet``).
            - Increments ``self.balance`` by each FG's annual P&L.
            - Resets ``fg.balance`` to 0.0 after aggregation.
        """
        sweep_logger = logging.getLogger(f"{__name__}.sweep_fg_balances_to_group")
        active_lc = [s.lower() for s in active_statuses]

        for plant in self.plants:
            for fg in plant.furnace_groups:
                if (
                    fg.capacity == 0
                    or fg.status.lower() not in active_lc
                    or fg.technology.name.lower() == "other"
                    or fg.technology.product.lower() not in market_price
                ):
                    continue

                fg.update_balance_sheet(market_price[fg.technology.product.lower()])
                annual_pnl = fg.balance
                self.balance += annual_pnl
                sweep_logger.debug(
                    "[SWEEP] fg_id=%s annual_pnl=%.2f plant_group_id=%s balance=%.2f",
                    fg.furnace_group_id,
                    annual_pnl,
                    self.plant_group_id,
                    self.balance,
                )
                fg.balance = 0.0

    def most_common_reductant(self, active_statuses: list[str]) -> dict[str, str]:
        """
        Get the most common reductant for each technology across all plants in the plant group.

        Aggregates reductant data from active-status furnace groups across all plants in this
        plant group to determine the most frequently used reductant for each technology type.

        Args:
            active_statuses: Status strings whose furnace groups count in the vote.

        Returns:
            dict[str, str]: Dictionary mapping technology name to most common reductant.
                           If no reductant is set for a technology, it will be omitted from the result.

        Example:
            {
                "BOF": "coke",
                "EAF": "electricity",
                "DRI": "natural_gas"
            }
        """
        from steelo.utilities.utils import get_most_common_reductant_by_technology

        # Collect all furnace groups from all plants
        all_furnace_groups = [fg for plant in self.plants for fg in plant.furnace_groups]

        return get_most_common_reductant_by_technology(all_furnace_groups, active_statuses)

    def generate_new_plant(
        self,
        site_id: tuple[float, float, str],  # (lat, lon, iso3)
        technology_name: str,
        product: str,
        npv: float,
        current_year: int,
        existent_plant_ids: list[str],
        cost_data: dict[
            str, dict[tuple[float, float, str], dict[str, dict[str, Any]]]
        ],  # product -> site_id -> tech -> cost_type -> cost
        equity_share: float,
        steel_plant_capacity: float,
        dynamic_feedstocks: list[PrimaryFeedstock],
        plant_lifetime: int,
        disposal_cost_outputs: frozenset[str] | None = None,
        derive_geo_unit: Callable[[float, float, str], str | None] | None = None,
    ) -> Plant:
        """
        Generate a new plant (and furnace group) in the plant group for a given location and technology.

        Args:
            site_id: Tuple containing (latitude, longitude, iso3 country code)
            technology_name: Name of the technology for the plant
            product: Product type (e.g., "steel", "iron")
            npv: Initial NPV value for the business opportunity
            current_year: Current simulation year
            existent_plant_ids: List of existing plant IDs to avoid duplicates
            cost_data: Nested dictionary: product -> site_id -> tech -> cost_type -> cost
            equity_share: Share of investment financed by equity
            steel_plant_capacity: Capacity of the steel plant in tonnes
            dynamic_feedstocks: List of primary feedstock options for the technology
            plant_lifetime: Lifetime of the plant in years
            derive_geo_unit: Optional ``(lat, lon, iso3) -> geo_unit | None`` derivation (injected
                from the geospatial adapter) tagging the spawn location's sub-national unit so
                province-level costs and subsidies resolve for the new plant.

        Returns:
            New Plant object with a single furnace group.

        Note: The status is set to considered and the plant id is set to the next available id in the
        plant group. The utilization rate is set to the average utilization rate for the technology to
        calculate realistic NPVs for business opportunities, refreshed yearly from the fleet average
        while considered (update_dynamic_costs_for_business_opportunities), and reset to 0 when the
        plant is made operational by PAM.
        """
        # Create new plant
        location = Location(
            lat=site_id[0],
            lon=site_id[1],
            country=site_id[2],
            region="unknown",
            iso3=site_id[2],
            geo_unit=derive_geo_unit(site_id[0], site_id[1], site_id[2]) if derive_geo_unit else None,
        )
        new_plant = Plant(
            plant_id=get_new_plant_id(existent_plant_ids),
            location=location,
            furnace_groups=[],
            power_source="grid",  # TODO: @Marcus, how to call it here if I use combi of baseload and grid?
            category_steel_product=set(
                [
                    ProductCategory("Flat"),
                    ProductCategory("Long"),
                    ProductCategory("Tubes"),
                    ProductCategory("Semi-finished"),
                ]
            ),
            soe_status="private",  # Indi is considered a private company
            parent_gem_id=self.plant_group_id,  # Set to plant group id
            workforce_size=500,
            certified=False,  # Assuming new plants are not certified
            # cost_data has been validated by validate_and_clean_cost_data to ensure fopex is a float
            technology_unit_fopex={technology_name.lower(): cost_data[product][site_id][technology_name]["fopex"]},  # type: ignore[dict-item]
        )

        # Create new furnace group in plant. New-plant construction does NOT debit any
        # treasury (20% equity is external investor money, 80% debt amortises via P&L).
        new_furnace = new_plant.generate_new_furnace(
            technology_name=technology_name,
            product=product,
            current_year=current_year,
            # cost_data has been validated by validate_and_clean_cost_data to ensure all required fields are present
            capex=cost_data[product][site_id][technology_name]["capex"],  # type: ignore[arg-type]
            capex_no_subsidy=cost_data[product][site_id][technology_name]["capex_no_subsidy"],  # type: ignore[arg-type]
            cost_of_debt=cost_data[product][site_id][technology_name]["cost_of_debt"],  # type: ignore[arg-type]
            cost_of_debt_no_subsidy=cost_data[product][site_id][technology_name]["cost_of_debt_no_subsidy"],  # type: ignore[arg-type]
            capacity=int(steel_plant_capacity),
            historical_npv_business_opportunities={current_year: npv},
            status="considered",
            # cost_data has been validated by prepare_cost_data to ensure utilization_rate exists and is numeric
            util_rate=float(
                cost_data[product][site_id][technology_name]["utilization_rate"]  # type: ignore[arg-type]
            ),  # average utilization rate for the technology
            chosen_reductant=cost_data[product][site_id][technology_name]["reductant"],  # type: ignore[arg-type]
            lag=int(1e6),  # Set lag to a very high number so that the plant needs to be made operational explicitly
            plant_lifetime=plant_lifetime,
            dynamic_business_case=dynamic_feedstocks,
            bill_of_materials=cost_data[product][site_id][technology_name].get("bom"),  # type: ignore[arg-type]
            energy_costs=cost_data[product][site_id][technology_name].get("energy_costs"),  # type: ignore[arg-type]
            output_energy_costs=cost_data[product][site_id][technology_name].get("output_costs"),  # type: ignore[arg-type]
            energy_costs_no_subsidy=cost_data[product][site_id][technology_name].get("no_subsidy_prices"),  # type: ignore[arg-type]
            disposal_cost_outputs=disposal_cost_outputs,
            equity_share=equity_share,
            output_shares=cost_data[product][site_id][technology_name]["output_shares"],
        )
        new_furnace.created_by_PAM = True
        # cost_data has been validated by validate_and_clean_cost_data to ensure these are floats/dicts
        new_furnace.railway_cost = cost_data[product][site_id][technology_name]["railway_cost"]  # type: ignore[assignment]
        new_plant.add_furnace_group(new_furnace)
        return new_plant

    def evaluate_expansion_options(
        self,
        price_series: dict[str, list[float]],
        capacity: Volumes,
        region_capex: dict[str, dict[str, float]],
        cost_of_debt_dict: dict[str, dict[str, float]] | None,
        cost_of_equity_dict: dict[str, dict[str, float]] | None,
        get_bom_from_avg_boms: (
            Callable[
                [dict[str, float], str, float, str | None],
                tuple[dict[str, dict[str, dict[str, float]]] | None, float, str, dict[str, float]],
            ]
            | None
        ),
        dynamic_feedstocks: dict[str, list[PrimaryFeedstock]],
        fopex_for_iso3: dict[str, dict[str, float]],
        iso3_to_region_map: dict[str, str],
        chosen_emissions_boundary_for_carbon_costs: str,
        technology_emission_factors: list[TechnologyEmissionFactors],
        global_risk_free_rate: float,
        equity_share: float,
        tech_to_product: dict[str, str],
        plant_lifetime: int,
        construction_time: int,
        current_year: Year,
        allowed_techs: dict[Year, list[str]],
        active_statuses: list[str],
        reductant_score_series: Callable[..., ReductantScoreSeries],
        capex_subsidies: dict[str, dict[str, list[Subsidy]]] = {},
        opex_subsidies: dict[str, dict[str, list[Subsidy]]] = {},
        debt_subsidies: dict[str, dict[str, list[Subsidy]]] = {},
        energy_subsidies: dict[str, dict[str, dict[str, list[Subsidy]]]] = {},
        environment_most_common_reductant: dict[str, str] = {},
        get_co2_headroom: Callable[[str, int, float], float] | None = None,
        get_co2_need_by_name: Callable[[str, float, str], float] | None = None,
        co2_storage_diagnostics: Callable[[str, int], tuple[float, float, float]] | None = None,
    ) -> dict[str, tuple[float | None, str, float, str]]:
        """
        Calculate NPV and optimal technology choice for all plants in the group considering allowed technologies and
        subsidies.

        Steps:
        1. Filter allowed technologies for the current year
        2. For each plant, retrieve location-specific financial parameters (CAPEX, FOPEX, cost of debt/equity)
        3. For each allowed technology:
           a. Get technology-specific CAPEX and check plant compatibility
           b. Calculate bill of materials and match with business cases for emissions
           c. Apply active subsidies to CAPEX, debt, and OPEX
           d. Calculate emissions costs based on carbon price series
           e. Compute NPV using full financial model
        4. Select technology with highest NPV for each plant

        Args:
            price_series (dict[str, list[float]]): Product price forecasts by product type
            capacity (Volumes): Capacity of new furnace group to evaluate (in tonnes)
            region_capex (dict[str, dict[str, float]]): CAPEX by region and technology (USD/tonne)
            cost_of_debt_dict (dict[str, dict[str, float]] | None): Cost of debt by ISO3 country code and technology
            cost_of_equity_dict (dict[str, dict[str, float]] | None): Cost of equity by ISO3 country code and technology
            get_bom_from_avg_boms (Callable | None): Function to retrieve bill of materials for a technology given
                energy costs
            dynamic_feedstocks (dict[str, list[PrimaryFeedstock]]): Primary feedstocks by technology for emissions
                calculations
            fopex_for_iso3 (dict[str, dict[str, float]]): Fixed OPEX by ISO3 country and technology (USD/tonne/year)
            iso3_to_region_map (dict[str, str]): Mapping from ISO3 country codes to regions
            chosen_emissions_boundary_for_carbon_costs (str): Emissions scope for carbon cost calculations
            technology_emission_factors (list[TechnologyEmissionFactors]): Emission factors for technologies
            global_risk_free_rate (float): Risk-free rate for debt subsidy calculations
            equity_share (float): Share of investment financed by equity (vs debt)
            tech_to_product (dict[str, str]): Mapping from technology names to product types
            plant_lifetime (int): Expected operational lifetime of new furnace (years)
            construction_time (int): Time to construct new furnace (years)
            current_year (Year): Current simulation year
            allowed_techs (dict[Year, list[str]]): Technologies allowed by year
            active_statuses (list[str]): Status strings whose furnace groups vote in the
                group's most-common-reductant aggregation
            capex_subsidies (dict[str, dict[str, list[Subsidy]]]): CAPEX subsidies by ISO3, technology, and subsidy
            opex_subsidies (dict[str, dict[str, list[Subsidy]]]): OPEX subsidies by ISO3, technology, and subsidy
            debt_subsidies (dict[str, dict[str, list[Subsidy]]]): Debt subsidies by ISO3, technology, and subsidy
            energy_subsidies (dict[str, dict[str, dict[str, list[Subsidy]]]]): Energy carrier
                subsidies (carrier -> geo_key -> technology -> subsidies). Collected per plant
                geography inside the loop; candidate technologies are priced from unsubsidised
                carrier prices with these applied for the candidate's operating start year

        Returns:
            dict[str, tuple[float | None, str, float]]: Dictionary mapping plant IDs to tuples of (NPV, best_technology,
                subsidized_capex) for the optimal expansion option. Returns empty dict if no viable options exist.
        """
        from steelo.domain import calculate_costs as cc

        # Function-level logger
        logger = logging.getLogger(f"{__name__}.evaluate_expansion_options")

        # Filter allowed technologies for the current year
        allowed_techs_in_year = allowed_techs.get(current_year, [])
        if not allowed_techs_in_year:
            raise ValueError(f"No allowed technologies found for year {current_year}")
        logger.info(f"[PG EXPANSION] Allowed technologies in {current_year}: {allowed_techs_in_year}")

        # Pre-filter counters for site #13 log
        num_pairs_evaluated = 0
        num_pairs_passed_prefilter = 0
        num_pairs_dropped_prefilter = 0

        # Dictionary to store best NPV and technology choice for each plant
        NPV_p = {}

        group_most_common_reductant = self.most_common_reductant(active_statuses)

        # Evaluate expansion options for each plant in the group
        for plant in self.plants:
            NPV = {}
            dropped_ccs_techs: set[str] = set()
            excpected_utilisation = plant.aggregate_average_utilisation_rate()

            # Retrieve location-specific financial parameters for this plant
            greenfield_capex = region_capex.get(iso3_to_region_map[plant.location.iso3])
            if greenfield_capex is None:
                raise ValueError(f"No capex data for region: {iso3_to_region_map[plant.location.iso3]}")

            technology_unit_fopex = fopex_for_iso3.get(plant.location.iso3)
            if technology_unit_fopex is None:
                raise ValueError(f"No unit FOPEX data for country: {plant.location.iso3}")

            if cost_of_debt_dict is None:
                raise ValueError("Cost of debt dictionary is not provided")
            cost_of_debt_for_iso3 = cost_of_debt_dict.get(plant.location.iso3)
            if cost_of_debt_for_iso3 is None:
                raise ValueError(f"No cost of debt data for country: {plant.location.iso3}")

            if cost_of_equity_dict is None:
                raise ValueError("Cost of equity dictionary is not provided")
            cost_of_equity_for_iso3 = cost_of_equity_dict.get(plant.location.iso3)
            if cost_of_equity_for_iso3 is None:
                raise ValueError(f"No cost of equity data for country: {plant.location.iso3}")

            # Candidates are priced from unsubsidised prices plus their own energy subsidies,
            # geo-collected per plant (group plants sit in different geographies)
            plant_energy_subsidies = {
                carrier: collect_subsidies_for_geo(carrier_subsidies, plant.location.geo_key)
                for carrier, carrier_subsidies in energy_subsidies.items()
            }
            base_energy_costs = plant.furnace_groups[-1].energy_costs_no_subsidy
            operating_start_year = Year(current_year + construction_time)
            operating_end_year = Year(current_year + construction_time + plant_lifetime)
            # GEO ("indi") sites keep their own power/hydrogen prices, trajectory-scaled
            site_overrides: dict[str, float] | None = None
            if plant.parent_gem_id.lower().startswith("indi"):
                site_overrides = {
                    carrier: base_energy_costs[carrier]
                    for carrier in ("electricity", "hydrogen")
                    if carrier in base_energy_costs
                }
            reductant_by_tech: dict[str, str] = {}

            # Evaluate each allowed technology for this plant
            for tech in allowed_techs_in_year:
                # Technology-specific financing rates
                cost_of_debt_original = cost_of_debt_for_iso3.get(tech)
                if cost_of_debt_original is None:
                    raise ValueError(f"No cost of debt data for {plant.location.iso3}/{tech}")
                cost_of_equity = cost_of_equity_for_iso3.get(tech)
                if cost_of_equity is None:
                    raise ValueError(f"No cost of equity data for {plant.location.iso3}/{tech}")

                # Get technology-specific CAPEX
                capex = greenfield_capex.get(tech)
                if capex is None:
                    raise ValueError(
                        f"No greenfield capex data for {tech} in region {iso3_to_region_map[plant.location.iso3]}"
                    )

                # Apply CAPEX subsidies before the affordability pre-filter
                all_capex_subsidies = collect_subsidies_for_geo(capex_subsidies, plant.location.geo_key).get(tech, [])
                selected_capex_subsidies = cc.filter_subsidies_for_year(all_capex_subsidies, current_year)
                capex = cc.calculate_capex_with_subsidies(capex, selected_capex_subsidies)

                # Skip if the group treasury cannot cover equity for this (plant, tech)
                num_pairs_evaluated += 1
                equity_needed_for_tech = capex * capacity * equity_share
                if self.balance < equity_needed_for_tech:
                    num_pairs_dropped_prefilter += 1
                    logger.debug(
                        f"[PG EXPANSION] Skipping {tech} for plant {plant.plant_id}: "
                        f"group balance ${self.balance:,.2f} < equity needed ${equity_needed_for_tech:,.2f}"
                    )
                    continue
                num_pairs_passed_prefilter += 1

                # Skip BOF technology if plant lacks hot metal furnace (prerequisite)
                if tech == "BOF" and not plant.has_hot_metal_access:
                    continue

                # P3 CO2 storage gate: skip CCS techs whose annual need exceeds country
                # headroom, sized with the reductant that would actually be built
                if get_co2_need_by_name is not None and get_co2_headroom is not None:
                    # Cheap pre-check: the empty-reductant lookup falls back to the
                    # all-reductant maximum, so 0 means no CCS potential at all
                    need = 0.0
                    if get_co2_need_by_name(tech, float(capacity), "") > 0.0:
                        gate_series = reductant_score_series(
                            plant.location,
                            tech,
                            {},
                            operating_start_year,
                            Year(operating_start_year + 1),
                            overrides=site_overrides,
                            override_reference_year=current_year if site_overrides else None,
                        )
                        gate_pick = gate_series.picks[0] if gate_series.picks else ""
                        need = get_co2_need_by_name(tech, float(capacity), gate_pick)
                    if need > 0.0:
                        headroom = get_co2_headroom(plant.location.iso3, int(current_year) + construction_time, 0.0)
                        if headroom < need:
                            dropped_ccs_techs.add(tech)
                            continue

                # Get bill of materials for this technology
                if get_bom_from_avg_boms is None:
                    continue

                active_energy_subs: dict[str, list[Subsidy]] = {}
                for carrier, subsidies_by_tech in plant_energy_subsidies.items():
                    active = cc.filter_subsidies_for_year(subsidies_by_tech.get(tech, []), operating_start_year)
                    if active:
                        active_energy_subs[carrier] = active
                if active_energy_subs:
                    candidate_energy_costs, _, _ = cc.get_subsidised_energy_costs(
                        base_energy_costs,
                        active_energy_subs,
                    )
                    sub_summary = ", ".join(f"{len(subs)} {carrier}" for carrier, subs in active_energy_subs.items())
                    logger.debug(
                        f"[PG EXPANSION] {plant.plant_id}/{tech} candidate energy subsidies for "
                        f"year {operating_start_year}: {sub_summary}"
                    )
                else:
                    candidate_energy_costs = base_energy_costs

                bom_result = get_bom_from_avg_boms(
                    candidate_energy_costs,
                    tech,
                    capacity,
                    group_most_common_reductant.get(tech, environment_most_common_reductant.get(tech)),
                )
                bill_of_materials_opt, util_rate, reductant, output_shares = bom_result
                if bill_of_materials_opt is None:
                    continue
                bill_of_materials: dict[str, dict[str, dict[str, float]]] = bill_of_materials_opt

                # Year-wise reductant-optimised score over the operating window; the NPV's
                # energy, carbon and by-product terms all live inside this series
                score_series = reductant_score_series(
                    plant.location,
                    tech,
                    output_shares,
                    operating_start_year,
                    operating_end_year,
                    overrides=site_overrides,
                    override_reference_year=current_year if site_overrides else None,
                )
                committed_reductant = score_series.picks[0] if score_series.picks else ""
                if committed_reductant != reductant:
                    # Commit the BOM the start-year pick implies (materials are reductant-
                    # invariant; only the energy rows follow the pick)
                    rebuilt_bom, util_rate, reductant, output_shares = get_bom_from_avg_boms(
                        candidate_energy_costs, tech, capacity, committed_reductant
                    )
                    if rebuilt_bom is None:
                        raise ValueError(
                            f"BOM rebuild for {tech} with reductant '{committed_reductant}' returned no BOM"
                        )
                    bill_of_materials = rebuilt_bom
                reductant_by_tech[tech] = committed_reductant
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(
                        "[REDUCTANT NPV] plant %s: tech=%s committed=%r picks=%s",
                        plant.plant_id,
                        tech,
                        committed_reductant,
                        cc.summarise_reductant_picks(score_series.picks, operating_start_year),
                    )

                # Apply subsidies (filter to only active ones in current year)
                from steelo.domain.calculate_costs import (
                    filter_subsidies_for_year,
                    collect_active_subsidies_over_period,
                )

                # Debt subsidies (CAPEX subsidies were already applied above the affordability pre-filter)
                all_debt_subsidies = collect_subsidies_for_geo(debt_subsidies, plant.location.geo_key).get(tech, [])
                selected_debt_subsidies = filter_subsidies_for_year(all_debt_subsidies, current_year)
                cost_of_debt = cc.calculate_debt_with_subsidies(
                    cost_of_debt=cost_of_debt_original,
                    debt_subsidies=selected_debt_subsidies,
                    risk_free_rate=global_risk_free_rate,
                )

                product = tech_to_product[tech]
                if not product or product not in price_series:
                    continue

                # Use maximum of technology utilization and plant's historical average
                expected_utilisation_rate: float = (
                    max(util_rate, excpected_utilisation[product]["utilisation_rate"])
                    if product in excpected_utilisation
                    else util_rate
                )

                # Calculate OPEX (fixed + variable + subsidies)
                tech_unit_fopex_value = technology_unit_fopex.get(tech.lower())
                if tech_unit_fopex_value is None:
                    raise ValueError(f"No fixed OPEX data for technology: {tech} in country: {plant.location.iso3}")
                # Per-capacity fixed OPEX spread over the production the NPV multiplies by; a
                # candidate the market allocated no production for cannot be priced this way
                try:
                    unit_fopex = cc.scale_fopex_to_production(float(tech_unit_fopex_value), expected_utilisation_rate)
                except cc.ZeroUtilisationError:
                    logger.warning(
                        f"[PG EXPANSION] SKIPPING {tech} for plant {plant.plant_id} - zero expected "
                        "utilisation, cannot price fixed OPEX"
                    )
                    continue

                # Materials-only variable OPEX plus fixed OPEX; energy, carbon and
                # by-products enter through the per-year score
                unit_base_opex = calculate_unit_total_opex(
                    unit_fopex=unit_fopex,
                    unit_vopex=cc.calculate_variable_opex(bill_of_materials["materials"], {}),
                    utilization_rate=util_rate,
                )

                # OPEX subsidies (collect active subsidies across plant lifetime)
                selected_opex_subsidies = collect_active_subsidies_over_period(
                    collect_subsidies_for_geo(opex_subsidies, plant.location.geo_key).get(tech, []),
                    start_year=Year(current_year + construction_time),
                    end_year=Year(current_year + construction_time + plant_lifetime),
                )

                unit_total_opex_list = cc.calculate_opex_list_with_subsidies(
                    opex=[unit_base_opex + score for score in score_series.scores],
                    opex_subsidies=selected_opex_subsidies,
                    start_year=Year(current_year + construction_time),
                    end_year=Year(current_year + construction_time + plant_lifetime),
                )

                # Carbon and by-product terms are already inside the per-year opex list
                NPV[tech] = cc.calculate_npv_full(
                    capex=capex,
                    capacity=capacity,
                    unit_total_opex_list=unit_total_opex_list,
                    cost_of_debt=cost_of_debt,
                    cost_of_equity=cost_of_equity,
                    equity_share=equity_share,
                    lifetime=plant_lifetime,
                    construction_time=construction_time,
                    expected_utilisation_rate=expected_utilisation_rate,
                    price_series=price_series[product],
                )

            if dropped_ccs_techs and co2_storage_diagnostics is not None:
                lookup_year = int(current_year) + construction_time
                _firm, _reserved, limit = co2_storage_diagnostics(plant.location.iso3, lookup_year)
                headroom = (get_co2_headroom or (lambda *_: 0.0))(plant.location.iso3, lookup_year, 0.0)
                logger.info(
                    f"[CO2 GATE] gate=P3 iso3={plant.location.iso3} year={int(current_year)} "
                    f"lookup_year={lookup_year} plant_id={plant.plant_id} "
                    f"headroom={headroom:.0f} limit={limit:.0f} "
                    f"dropped_ccs_techs={','.join(sorted(dropped_ccs_techs))} "
                    f"dropped_count={len(dropped_ccs_techs)}"
                )

            # Select technology with highest NPV for this plant
            if NPV:
                best_tech = max(NPV, key=lambda k: NPV[k])

                # Calculate subsidized CAPEX for best technology
                all_best_capex_subsidies = collect_subsidies_for_geo(capex_subsidies, plant.location.geo_key).get(
                    best_tech, []
                )
                best_capex_subsidies = filter_subsidies_for_year(all_best_capex_subsidies, current_year)
                best_capex = cc.calculate_capex_with_subsidies(greenfield_capex[best_tech], best_capex_subsidies)

                NPV_p[plant.plant_id] = NPV.get(best_tech), best_tech, best_capex, reductant_by_tech[best_tech]

        logger.info(
            "[PG EXPANSION OPTIONS] plant_group_id=%s num_pairs_evaluated=%d "
            "num_pairs_passed_prefilter=%d num_pairs_dropped_prefilter=%d",
            self.plant_group_id,
            num_pairs_evaluated,
            num_pairs_passed_prefilter,
            num_pairs_dropped_prefilter,
        )

        return NPV_p

    def evaluate_expansion(
        self,
        price_series: dict[str, list[float]],
        region_capex: dict[str, dict[str, float]],
        dynamic_feedstocks: dict[str, list[PrimaryFeedstock]],
        fopex_for_iso3: dict[str, dict[str, float]],
        iso3_to_region_map: dict[str, str],
        probabilistic_agents: bool,
        chosen_emissions_boundary_for_carbon_costs: str,
        technology_emission_factors: list[TechnologyEmissionFactors],
        global_risk_free_rate: float,
        capacity: Volumes,
        equity_share: float,
        tech_to_product: dict[str, str],
        plant_lifetime: int,
        construction_time: int,
        current_year: Year,
        allowed_techs: dict[Year, list[str]],
        cost_of_debt_dict: dict[str, dict[str, float]],
        cost_of_equity_dict: dict[str, dict[str, float]],
        get_bom_from_avg_boms: Callable,
        reductant_score_series: Callable[..., ReductantScoreSeries],
        capacity_limit_steel: Volumes,
        capacity_limit_iron: Volumes,
        installed_capacity_in_year: Callable[[str], Volumes],
        new_plant_capacity_in_year: Callable[[str], Volumes],
        new_capacity_share_from_new_plants: float,
        active_statuses: list[str],
        capex_subsidies: dict[str, dict[str, list[Subsidy]]] = {},
        opex_subsidies: dict[str, dict[str, list[Subsidy]]] = {},
        debt_subsidies: dict[str, dict[str, list[Subsidy]]] = {},
        energy_subsidies: dict[str, dict[str, dict[str, list[Subsidy]]]] = {},
        environment_most_common_reductant: dict[str, str] = {},
        get_co2_headroom: Callable[[str, int, float], float] | None = None,
        get_co2_need_by_name: Callable[[str, float, str], float] | None = None,
        co2_storage_diagnostics: Callable[[str, int], tuple[float, float, float]] | None = None,
    ) -> commands.Command | None:
        """
        Evaluate and execute the most profitable furnace expansion across all plants in the plant group.

        This multi-stage decision process evaluates all expansion options, selects the best one based on NPV,
        validates financing and capacity constraints, and creates an expansion command if all conditions are met.

        Steps:
        1. Initialize and log current state (plant group balance, capacity, parameters)
        2. Evaluate all expansion options to get NPV and tech choice for each plant
        3. Check if any viable expansion options exist (empty results → no expansion)
        4. Select the plant and technology with highest NPV
        5. Verify NPV is positive and profitable
        6. Calculate equity requirements and check plant group has sufficient balance
        7. Calculate acceptance probability and make expansion decision via random draw
        8. Check capacity limits (separate limits for iron vs steel, PAM share vs new plants)
        9. Validate plant exists and has location data
        10. Apply subsidies (CAPEX, debt) and calculate subsidized costs
        11. Create and return AddFurnaceGroup command with all parameters

        Args:
            price_series (dict[str, list[float]]): Product price forecasts by product type
            region_capex (dict[str, dict[str, float]]): CAPEX by region and technology (USD/tonne)
            dynamic_feedstocks (dict[str, list[PrimaryFeedstock]]): Primary feedstocks by technology
            fopex_for_iso3 (dict[str, dict[str, float]]): Fixed OPEX by ISO3 country and technology
            iso3_to_region_map (dict[str, str]): Mapping from ISO3 country codes to regions
            probabilistic_agents (bool): If True, apply probabilistic acceptance based on investment risk
            chosen_emissions_boundary_for_carbon_costs (str): Emissions scope for carbon cost calculations
            technology_emission_factors (list[TechnologyEmissionFactors]): Emission factors for technologies
            global_risk_free_rate (float): Risk-free rate for debt subsidy calculations
            capacity (Volumes): Capacity of new furnace group to evaluate (in tonnes)
            equity_share (float): Share of investment financed by equity (vs debt)
            tech_to_product (dict[str, str]): Mapping from technology names to product types
            plant_lifetime (int): Expected operational lifetime of new furnace (years)
            construction_time (int): Time to construct new furnace (years)
            current_year (Year): Current simulation year
            allowed_techs (dict[Year, list[str]]): Technologies allowed by year
            cost_of_debt_dict (dict[str, dict[str, float]]): Cost of debt by ISO3 country code and technology
            cost_of_equity_dict (dict[str, dict[str, float]]): Cost of equity by ISO3 country code and technology
            get_bom_from_avg_boms (Callable): Function to retrieve bill of materials for a technology
            capacity_limit_steel (Volumes): Maximum allowed steel capacity from expansions/switches (PAM share)
            capacity_limit_iron (Volumes): Maximum allowed iron capacity from expansions/switches (PAM share)
            installed_capacity_in_year (Callable[[str], Volumes]): Function to get total installed capacity for product
            new_plant_capacity_in_year (Callable[[str], Volumes]): Function to get capacity from new plants for product
            new_capacity_share_from_new_plants (float): Target share of new capacity from greenfield plants
            capex_subsidies (dict[str, dict[str, list[Subsidy]]]): CAPEX subsidies by ISO3, technology, and subsidy
            opex_subsidies (dict[str, dict[str, list[Subsidy]]]): OPEX subsidies by ISO3, technology, and subsidy
            debt_subsidies (dict[str, dict[str, list[Subsidy]]]): Debt subsidies by ISO3, technology, and subsidy

        Returns:
            commands.Command | None: AddFurnaceGroup command if expansion is approved, None otherwise.

        Side Effects:
            - Logs detailed decision-making process at debug level

        Note:
        - Balance deduction does NOT happen here - the ``add_furnace_group_to_plant``
          handler debits ``plant_group.balance`` via ``plant_group.deduct_equity`` after
          the factory has attached the new furnace to the plant.
        - Probabilistic acceptance uses formula: ``exp(-equity_needed / NPV)`` — the
          equity-basis equity outlay is dimensionally consistent with the equity-basis NPV.
        - Capacity limits distinguish between new plants and expansions/switches
        - All subsidies are filtered to only include those active in the current year
        """
        logger = logging.getLogger(f"{__name__}.PlantGroup.evaluate_expansion")

        # ========== STAGE 1: INITIALIZATION ==========
        logger.debug(
            f"[PG EXPANSION] {self.plant_group_id}: "
            f"balance=${self.balance:,.2f}, plants={len(self.plants)}, year={current_year}"
        )

        # ========== STAGE 2: EVALUATE ALL EXPANSION OPTIONS ==========

        expansion_options = self.evaluate_expansion_options(
            price_series=price_series,
            capacity=capacity,
            cost_of_debt_dict=cost_of_debt_dict,
            cost_of_equity_dict=cost_of_equity_dict,
            get_bom_from_avg_boms=get_bom_from_avg_boms,
            tech_to_product=tech_to_product,
            region_capex=region_capex,
            dynamic_feedstocks=dynamic_feedstocks,
            fopex_for_iso3=fopex_for_iso3,
            current_year=current_year,
            allowed_techs=allowed_techs,
            active_statuses=active_statuses,
            reductant_score_series=reductant_score_series,
            capex_subsidies=capex_subsidies,
            opex_subsidies=opex_subsidies,
            debt_subsidies=debt_subsidies,
            energy_subsidies=energy_subsidies,
            iso3_to_region_map=iso3_to_region_map,
            chosen_emissions_boundary_for_carbon_costs=chosen_emissions_boundary_for_carbon_costs,
            global_risk_free_rate=global_risk_free_rate,
            equity_share=equity_share,
            technology_emission_factors=technology_emission_factors,
            plant_lifetime=plant_lifetime,
            construction_time=construction_time,
            environment_most_common_reductant=environment_most_common_reductant,
            get_co2_headroom=get_co2_headroom,
            get_co2_need_by_name=get_co2_need_by_name,
            co2_storage_diagnostics=co2_storage_diagnostics,
        )

        # ========== STAGE 3: CHECK IF ANY EXPANSION OPTIONS EXIST ==========
        if not expansion_options:
            logger.debug("[PG EXPANSION] DECISION - No expansion (no viable options from evaluate_expansion_options)")
            return None

        # Log all expansion options found
        logger.debug(f"[PG EXPANSION] Found {len(expansion_options)} options:")
        for pid, (npv, tech, capex, _reductant) in expansion_options.items():
            npv_str = "None" if npv is None else f"${npv:,.0f}"
            logger.debug(f"[PG EXPANSION]   {pid}: {tech} NPV={npv_str} CAPEX=${capex:.2f}/t")

        # ========== STAGE 4: SELECT HIGHEST NPV OPTION ==========
        highest_plant_and_tech = max(expansion_options.items(), key=lambda item: item[1][0] or float("-inf"))
        plant_id, (npv, tech, capex, chosen_reductant) = highest_plant_and_tech

        npv_str = "None" if npv is None else f"{npv:,.0f}"
        logger.debug(f"[PG EXPANSION] Best: {plant_id} {tech} NPV=${npv_str} CAPEX=${capex:,.2f}/t")

        # ========== STAGE 5: CHECK NPV PROFITABILITY ==========
        if npv is None or npv <= 0:
            logger.debug(
                f"[PG EXPANSION] DECISION - No expansion (NPV {'is None' if npv is None else f'= ${npv:,.0f} ≤ 0'})"
            )
            return None

        # ========== STAGE 6: CHECK BALANCE SUFFICIENCY ==========
        # Equity = capex × capacity × equity_share
        investment = capacity * capex
        equity_needed = investment * equity_share

        if self.balance < equity_needed:
            logger.debug(
                f"[PG EXPANSION] DECISION - No expansion (insufficient funds: "
                f"${self.balance:,.2f} < ${equity_needed:,.2f})"
            )
            return None

        # ========== STAGE 7: PROBABILISTIC ACCEPTANCE ==========
        # Acceptance: exp(-equity_needed / NPV). Dimensionally consistent with NPV, which is equity-basis
        if probabilistic_agents:
            acceptance_probability = math.exp(-equity_needed / npv)
        else:
            acceptance_probability = 1

        random_draw = random.random()
        accepted = random_draw < acceptance_probability

        logger.info(
            "[PG EXPANSION] plant_group_id=%s plant_id=%s tech=%s investment=%.2f "
            "equity_needed=%.2f npv=%.2f acceptance_probability=%.4f random_draw=%.4f accepted=%s",
            self.plant_group_id,
            plant_id,
            tech,
            investment,
            equity_needed,
            npv,
            acceptance_probability,
            random_draw,
            accepted,
        )

        if not accepted:
            logger.debug(
                f"[PG EXPANSION] DECISION - No expansion (probabilistic rejection: "
                f"draw={random_draw:.4f} >= prob={acceptance_probability:.2%})"
            )
            return None

        # ========== STAGE 8: CHECK CAPACITY LIMITS ==========
        if (
            capacity_limit_steel is not None
            and capacity_limit_iron is not None
            and installed_capacity_in_year is not None
        ):
            product_opt = tech_to_product.get(tech)
            if product_opt is None:
                raise ValueError(f"Technology {tech} not found in technology_to_product")
            expansion_product: str = product_opt

            # Calculate current capacity usage
            total_installed_capacity = installed_capacity_in_year(expansion_product)
            new_plant_capacity = new_plant_capacity_in_year(expansion_product)
            expansion_and_switch_capacity = total_installed_capacity - new_plant_capacity

            # Get limit based on product type
            expansion_limit = capacity_limit_iron if expansion_product == "iron" else capacity_limit_steel
            if expansion_product not in ["iron", "steel"]:
                raise ValueError(f"Unknown product type: '{expansion_product}' for technology: '{tech}'")

            # Check if expansion would exceed limit
            if expansion_and_switch_capacity + capacity > expansion_limit:
                logger.warning("[PG EXPANSION] === Stage 8: Capacity limit EXCEEDED ===")
                logger.warning(f"[PG EXPANSION]   - Product: {expansion_product}")
                logger.warning(
                    f"[PG EXPANSION]   - Current expansion/switch capacity: {expansion_and_switch_capacity * T_TO_KT:,.0f} kt"
                )
                logger.warning(f"[PG EXPANSION]   - New expansion capacity: {capacity * T_TO_KT:,.0f} kt")
                logger.warning(
                    f"[PG EXPANSION]   - Total after expansion: {(expansion_and_switch_capacity + capacity) * T_TO_KT:,.0f} kt"
                )
                logger.warning(f"[PG EXPANSION]   - Limit: {expansion_limit * T_TO_KT:,.0f} kt")
                logger.warning("[PG EXPANSION]   - DECISION - No expansion (capacity limit reached)")
                return None

            # logger.debug("[PG EXPANSION] === Stage 8: Capacity limit check PASSED ===")
            # logger.debug(f"[PG EXPANSION]   - Product: {expansion_product}")
            # logger.debug(
            #     f"[PG EXPANSION]   - After expansion: {(expansion_and_switch_capacity + capacity) * T_TO_KT:,.0f} kt / "
            #     f"{expansion_limit * T_TO_KT:,.0f} kt limit"
            # )

        # ========== STAGE 9: VALIDATE PLANT AND LOCATION ==========
        # NOTE: Balance deduction does NOT happen here - the add_furnace_group_to_plant handler
        # calls plant_group.deduct_equity(cmd.equity_needed, reason="expansion") after the factory
        # has attached the new furnace to the plant.

        # Find the plant and validate location
        plant = next((p for p in self.plants if p.plant_id == plant_id), None)
        if plant is None:
            logger.warning(f"[PG EXPANSION] ERROR - Plant {plant_id} not found in plant group")
            return None

        if plant.location.iso3 is None:
            logger.warning(f"[PG EXPANSION] ERROR - Plant {plant_id} has no ISO3 location")
            return None

        region = iso3_to_region_map.get(plant.location.iso3)
        if region is None:
            logger.warning(f"[PG EXPANSION] ERROR - No region mapping for ISO3: {plant.location.iso3}")
            return None

        # Get base cost of debt for the winning technology
        cost_of_debt_original = cost_of_debt_dict.get(plant.location.iso3, {}).get(tech)
        if cost_of_debt_original is None:
            raise ValueError(f"No cost of debt data for {plant.location.iso3}/{tech} when expanding plant")

        # logger.debug("[PG EXPANSION] === Stage 9: Plant validation ===")
        # logger.debug(f"[PG EXPANSION]   - Plant: {plant_id}, Location: {plant.location.iso3}, Region: {region}")
        # logger.debug(f"[PG EXPANSION]   - Base cost of debt: {cost_of_debt_original:.2%}")

        # ========== STAGE 10: APPLY SUBSIDIES ==========
        from steelo.domain import calculate_costs as cc

        # Get all subsidies for this location and technology, then filter to active ones
        all_debt_subsidies = collect_subsidies_for_geo(debt_subsidies, plant.location.geo_key).get(tech, [])
        all_capex_subsidies = collect_subsidies_for_geo(capex_subsidies, plant.location.geo_key).get(tech, [])
        selected_debt_subsidies = filter_subsidies_for_year(all_debt_subsidies, current_year)
        selected_capex_subsidies = filter_subsidies_for_year(all_capex_subsidies, current_year)

        # Apply subsidies to debt and CAPEX
        cost_of_debt = cc.calculate_debt_with_subsidies(
            cost_of_debt=cost_of_debt_original,
            debt_subsidies=selected_debt_subsidies,
            risk_free_rate=global_risk_free_rate,
        )

        base_capex = region_capex[region][tech]
        capex = cc.calculate_capex_with_subsidies(base_capex, selected_capex_subsidies)

        # logger.debug("[PG EXPANSION] === Stage 10: Subsidies applied ===")
        # logger.debug(
        #     f"[PG EXPANSION]   - Debt subsidies: {len(selected_debt_subsidies)} active (of {len(all_debt_subsidies)} total)"
        # )
        # logger.debug(
        #     f"[PG EXPANSION]   - CAPEX subsidies: {len(selected_capex_subsidies)} active (of {len(all_capex_subsidies)} total)"
        # )
        # logger.debug(
        #     f"[PG EXPANSION]   - Cost of debt: {cost_of_debt_original:.2%} → {cost_of_debt:.2%} "
        #     f"({(cost_of_debt - cost_of_debt_original) * 100:+.2f} pp)"
        # )
        # logger.debug(
        #     f"[PG EXPANSION]   - CAPEX: ${base_capex:.2f}/t → ${capex:.2f}/t (${base_capex - capex:.2f}/t reduction)"
        # )

        # ========== STAGE 11: CREATE EXPANSION COMMAND ==========
        furnace_group_id = f"{plant_id}_new_furnace"

        # Safety check: ensure technology has product mapping
        if tech not in tech_to_product:
            logger.warning(f"[PG EXPANSION] ERROR - No product mapping for technology: {tech}")
            return None
        product = tech_to_product[tech]

        # Log subsidy details being passed to command
        subsidy_details = []
        if selected_capex_subsidies:
            subsidy_details.append(
                f"  CAPEX subsidies ({len(selected_capex_subsidies)}):\n"
                + "\n".join(
                    f"    • {s.subsidy_name}: type={s.subsidy_type}, amount={s.subsidy_amount:.4f}, "
                    f"years {s.start_year}-{s.end_year}"
                    for s in selected_capex_subsidies
                )
            )
        if selected_debt_subsidies:
            subsidy_details.append(
                f"  Debt subsidies ({len(selected_debt_subsidies)}):\n"
                + "\n".join(
                    f"    • {s.subsidy_name}: type={s.subsidy_type}, amount={s.subsidy_amount:.4f}, "
                    f"years {s.start_year}-{s.end_year}"
                    for s in selected_debt_subsidies
                )
            )

        logger.info("[PG EXPANSION] ✓ SUCCESS - Expansion approved")
        logger.info(f"[PG EXPANSION]   - Plant: {plant_id}, Technology: {tech}, Product: {product}")
        logger.info(f"[PG EXPANSION]   - Capacity: {capacity * T_TO_KT:,.0f} kt, NPV: ${npv:,.0f}")
        logger.info(f"[PG EXPANSION]   - Investment: ${investment:,.0f} (equity to debit: ${equity_needed:,.0f})")
        logger.info(f"[PG EXPANSION]   - CAPEX: ${base_capex:.2f}/t → ${capex:.2f}/t (with subsidies)")
        logger.info(
            f"[PG EXPANSION]   - Cost of debt: {cost_of_debt_original:.2%} → {cost_of_debt:.2%} (with subsidies)"
        )

        # if subsidy_details:
        #     logger.debug("[PG EXPANSION] Subsidies included:")
        #     for detail in subsidy_details:
        #         logger.debug(f"[PG EXPANSION]   {detail}")

        return commands.AddFurnaceGroup(
            furnace_group_id=furnace_group_id,
            plant_id=plant_id,
            technology_name=tech,
            capacity=capacity,
            product=product,
            chosen_reductant=chosen_reductant,
            equity_share=equity_share,
            equity_needed=equity_needed,
            npv=npv,  # type: ignore # npv is not None due to check above
            capex=capex,
            capex_no_subsidy=base_capex,
            cost_of_debt=cost_of_debt,
            cost_of_debt_no_subsidy=cost_of_debt_original,
            capex_subsidies=selected_capex_subsidies,
            debt_subsidies=selected_debt_subsidies,
        )

    # ------------------------------------------------------------- New plant opening logic ------------------------------------------------------------- #
    def identify_new_business_opportunities_4indi(
        self,
        current_year: Year,
        consideration_time: int,
        construction_time: int,
        plant_lifetime: int,
        input_costs: dict[str, dict[Year, dict[str, float]]],  # iso3 -> year -> energy carrier -> cost
        locations: dict,
        iso3_to_region_map: dict[str, str],
        market_price: dict[str, list[float]],  # product -> list of future prices
        capex_dict_all_locs_techs: dict[str, dict[str, float]],  # region -> tech -> capex
        cost_of_debt_all_locs: dict[str, dict[str, float]],  # iso3 -> tech -> cost of debt
        cost_of_equity_all_locs: dict[str, dict[str, float]],  # iso3 -> tech -> cost of equity
        steel_plant_capacity: float,
        all_plant_ids: list[str],
        fopex_all_locs_techs: dict[str, dict[str, float]],  # iso3 -> tech -> fopex
        equity_share: float,
        dynamic_feedstocks: dict[str, list[PrimaryFeedstock]],
        get_bom_from_avg_boms: Callable[
            [dict[str, float], str, float, str | None],
            tuple[dict[str, dict[str, dict[str, float]]] | None, float, str, dict[str, float]],
        ],
        reductant_score_series: Callable[..., ReductantScoreSeries],
        global_risk_free_rate: float,
        tech_to_product: dict[str, str],
        allowed_techs: dict[Year, list[str]],
        technology_emission_factors: list[TechnologyEmissionFactors],
        chosen_emissions_boundary_for_carbon_costs: str,
        active_statuses: list[str],
        top_n_loctechs_as_business_op: int,
        opportunity_pool_depth: int,
        calculate_npv_sites_share: float,
        capex_subsidies: dict[str, dict[str, list[Subsidy]]] = {},  # iso3 -> tech -> list of subsidies
        debt_subsidies: dict[str, dict[str, list[Subsidy]]] = {},  # iso3 -> tech -> list of subsidies
        opex_subsidies: dict[str, dict[str, list[Subsidy]]] = {},  # iso3 -> tech -> list of subsidies
        energy_subsidies: dict[str, dict[str, dict[str, list[Subsidy]]]] = {},  # carrier -> iso3 -> tech -> [Subsidy]
        environment_most_common_reductant: dict[str, str] = {},
        disposal_cost_outputs: frozenset[str] | None = None,
        get_co2_headroom: Callable[[str, int, float], float] | None = None,
        get_co2_need_by_name: Callable[[str, float, str], float] | None = None,
        co2_storage_diagnostics: Callable[[str, int], tuple[float, float, float]] | None = None,
        derive_geo_unit: Callable[[float, float, str], str | None] | None = None,
        probabilistic_agents: bool = True,
    ) -> commands.Command:
        """
        Identifies new business opportunities for plants at given locations with specific technologies.

        Steps:
            1. Find allowed technologies for the target year.
            2. Randomly select a subset of top locations for a more in-depth assessment to reduce runtime.
            3. Prepare all required inputs (input costs with electricity and hydrogen costs from own parc,
               capex and cost of debt with subsidies, cost of equity, fixed OPEX, market price of product,
               railway costs, average BOM, and utilization rate). Sites missing critical data
               (energy costs, cost of equity or debt) raise a ValueError, as do invalid data types;
               technologies with an incomplete field set are dropped in validation.
            4. Calculate the NPV for all business opportunities (location-technology pairs) for each product.
            5. Choose top N location-technology combinations with high NPVs and list them as potential
               business opportunities. This step includes some randomness, but is mainly driven by NPV
               values, reflecting that few opportunities are actually considered and tracked by a single
               parent company.
            6. For each business opportunity, initialize a new plant at a given location and a single
               furnace group with the same id as the plant with optimum technology (status: considered).

        Args:
            current_year: Current simulation year
            consideration_time: Number of years to consider before announcement
            construction_time: Time required for plant construction in years
            plant_lifetime: Lifetime of the plant in years
            input_costs: Dictionary mapping iso3 -> year -> energy carrier -> cost
            locations: Dictionary of potential plant locations
            iso3_to_region_map: Dictionary mapping ISO3 country codes to regions
            market_price: Dictionary mapping product to list of future prices, anchored
                at the current year; re-anchored to target_year before the NPV
            capex_dict_all_locs_techs: Dictionary mapping region -> tech -> capex
            cost_of_debt_all_locs: Dictionary mapping iso3 -> tech -> cost of debt
            cost_of_equity_all_locs: Dictionary mapping iso3 -> tech -> cost of equity
            steel_plant_capacity: Capacity of the steel plant in tonnes
            all_plant_ids: List of all existing plant IDs
            fopex_all_locs_techs: Dictionary mapping iso3 -> tech -> fopex
            equity_share: Share of investment financed by equity
            dynamic_feedstocks: Dictionary mapping technology to list of primary feedstocks
            get_bom_from_avg_boms: Callable to get bill of materials from average BOMs
            global_risk_free_rate: Global risk-free interest rate
            tech_to_product: Dictionary mapping technology to product type
            allowed_techs: Dictionary mapping year to list of allowed technologies
            technology_emission_factors: List of technology-specific emission factors
            chosen_emissions_boundary_for_carbon_costs: Emission boundary for carbon costs
            active_statuses: Status strings whose furnace groups vote in the group's
                most-common-reductant aggregation
            top_n_loctechs_as_business_op: Number of top opportunities to select per product
                (single source of truth: SimulationConfig, default 15)
            opportunity_pool_depth: Depth of the probabilistic draw's eligible pool: global
                head of (depth * top_n) candidates by NPV unioned with each allowed
                technology's best `depth` sites (see select_top_opportunities_by_npv)
            calculate_npv_sites_share: Fraction (0.0-1.0) of the priority-location subset that is
                randomly sampled for full NPV evaluation each year. Forced to 1.0 when
                probabilistic_agents is False (see SimulationConfig.__post_init__)
            capex_subsidies: Dictionary mapping geo_key -> tech -> list of capex subsidies
                (geo_key = "ISO3" or "ISO3:unit"; country and sub-national rows merge additively)
            debt_subsidies: Dictionary mapping geo_key -> tech -> list of debt subsidies
            opex_subsidies: Dictionary mapping geo_key -> tech -> list of opex subsidies
            energy_subsidies: Dictionary mapping carrier -> geo_key -> tech -> list of energy subsidies
            derive_geo_unit: Optional ``(lat, lon, iso3) -> geo_unit | None`` derivation (injected
                from the geospatial adapter) tagging each spawned plant's sub-national unit
            probabilistic_agents: If True (default), step 5 draws a rank-weighted mix of top
                opportunities. If False, step 5 deterministically picks the top N by NPV, and
                calculate_npv_sites_share is forced to 1.0 by SimulationConfig.__post_init__ so
                step 2 evaluates every candidate location instead of a random sample — see
                docs/domain_simulation_logic/geospatial_model/new_plant_opening.md.

        Returns:
            Command to add new Plant and FurnaceGroup objects for the identified business opportunities
            in the PlantGroup "indi".
        """
        logger = logging.getLogger(f"{__name__}.PlantGroup.identify_new_business_opportunities_4indi")
        from steelo.domain.new_plant_opening import (
            select_location_subset,
            prepare_cost_data_for_business_opportunity,
            select_top_opportunities_by_npv,
            get_list_of_allowed_techs_for_target_year,
        )
        from steelo.domain.calculate_costs import calculate_business_opportunity_npvs

        logger.info("[NEW PLANTS] Creating new business opportunities for PlantGroup 'indi' to open new plants.")

        def _count_entries(mapping: dict) -> tuple[dict[str, int], int]:
            per_product: dict[str, int] = {}
            total = 0
            for product, entries in mapping.items():
                count = 0
                if isinstance(entries, list):
                    count = len(entries)
                elif isinstance(entries, dict):
                    for value in entries.values():
                        if isinstance(value, dict):
                            count += len(value)
                        elif isinstance(value, list):
                            count += len(value)
                        else:
                            count += 1
                else:
                    count = 1
                per_product[product] = count
                total += count
            return per_product, total

        candidate_stats: dict[str, Any] = {"year": int(current_year)}
        initial_counts, initial_total = _count_entries(locations)
        candidate_stats["input_sites_total"] = initial_total
        for product, value in initial_counts.items():
            candidate_stats[f"input_sites_{product}"] = value

        # Set target year for technology filtering
        target_year = Year(current_year + consideration_time + 1)

        # Step 1: Find allowed technologies for the target year
        product_to_tech = get_list_of_allowed_techs_for_target_year(
            allowed_techs=allowed_techs,
            tech_to_product=tech_to_product,
            target_year=target_year,
        )
        candidate_stats["allowed_products"] = ",".join(
            f"{product}:{len(techs)}" for product, techs in product_to_tech.items()
        )

        # Step 2: Select a subset of locations
        best_locations_subset = select_location_subset(
            locations=locations,
            calculate_npv_sites_share=calculate_npv_sites_share,
        )
        subset_counts, subset_total = _count_entries(best_locations_subset)
        candidate_stats["subset_sites_total"] = subset_total
        for product, value in subset_counts.items():
            candidate_stats[f"subset_sites_{product}"] = value

        # Step 3: Prepare cost data for all allowed top location-technology combinations
        cost_data = prepare_cost_data_for_business_opportunity(
            product_to_tech=product_to_tech,
            best_locations_subset=best_locations_subset,
            current_year=current_year,
            target_year=target_year,
            energy_costs=input_costs,
            capex_dict_all_locs_techs=capex_dict_all_locs_techs,
            cost_of_debt_all_locs=cost_of_debt_all_locs,
            cost_of_equity_all_locs=cost_of_equity_all_locs,
            fopex_all_locs_techs=fopex_all_locs_techs,
            steel_plant_capacity=steel_plant_capacity,
            plant_lifetime=plant_lifetime,
            construction_time=construction_time,
            get_bom_from_avg_boms=get_bom_from_avg_boms,
            reductant_score_series=reductant_score_series,
            iso3_to_region_map=iso3_to_region_map,
            global_risk_free_rate=global_risk_free_rate,
            capex_subsidies=capex_subsidies,
            debt_subsidies=debt_subsidies,
            opex_subsidies=opex_subsidies,
            energy_subsidies=energy_subsidies,
            most_common_reductant=self.most_common_reductant(active_statuses),
            environment_most_common_reductant=environment_most_common_reductant,
            derive_geo_unit=derive_geo_unit,
        )
        cost_counts, cost_total = _count_entries(cost_data)
        candidate_stats["costed_pairs_total"] = cost_total
        for product, value in cost_counts.items():
            candidate_stats[f"costed_pairs_{product}"] = value

        # Step 4: Calculate NPVs for all allowed top location-technology combinations with enough data
        # npv_dict: product -> site_id (lat, lon, iso3) -> tech -> NPV
        # Prices from construction start, so price year i is cost year i in calculate_npv_full
        years_to_construction_start = int(target_year) - int(current_year)
        market_price_from_construction_start = {
            product: series[years_to_construction_start:] for product, series in market_price.items()
        }
        npv_dict = calculate_business_opportunity_npvs(
            cost_data=cost_data,
            target_year=target_year,
            market_price=market_price_from_construction_start,
            steel_plant_capacity=steel_plant_capacity,
            plant_lifetime=plant_lifetime,
            construction_time=construction_time,
            equity_share=equity_share,
        )
        # G1 CO2 storage gate: drop CCS techs per (iso3, tech) when annual need exceeds
        # country headroom at the opportunity's operating-start lookup year.
        if get_co2_headroom is not None and get_co2_need_by_name is not None:
            lookup_year = int(target_year) + construction_time
            dropped_by_iso3: dict[str, set[str]] = {}
            for product, sites in npv_dict.items():
                for site_id in list(sites.keys()):
                    iso3 = site_id[2]
                    kept_techs: dict[str, float] = {}
                    for tech, npv in sites[site_id].items():
                        # cost_data carries the evaluated operating-start-year pick (C8)
                        reductant = str(cost_data[product][site_id][tech]["reductant"])
                        need = get_co2_need_by_name(tech, float(steel_plant_capacity), reductant)
                        if need > 0.0:
                            headroom = get_co2_headroom(iso3, lookup_year, 0.0)
                            if headroom < need:
                                dropped_by_iso3.setdefault(iso3, set()).add(tech)
                                continue
                        kept_techs[tech] = npv
                    sites[site_id] = kept_techs

            if dropped_by_iso3 and co2_storage_diagnostics is not None:
                for iso3, dropped_techs in sorted(dropped_by_iso3.items()):
                    _firm, _reserved, limit = co2_storage_diagnostics(iso3, lookup_year)
                    headroom = get_co2_headroom(iso3, lookup_year, 0.0)
                    logger.info(
                        f"[CO2 GATE] gate=G1 iso3={iso3} year={int(current_year)} "
                        f"lookup_year={lookup_year} headroom={headroom:.0f} limit={limit:.0f} "
                        f"dropped_ccs_techs={','.join(sorted(dropped_techs))} "
                        f"dropped_count={len(dropped_techs)}"
                    )

        npv_counts, npv_total = _count_entries(npv_dict)
        candidate_stats["npv_pairs_total"] = npv_total
        for product, value in npv_counts.items():
            candidate_stats[f"npv_pairs_{product}"] = value

        # Step 5: Select top N opportunities based on NPV
        top_business_opportunities = select_top_opportunities_by_npv(
            npv_dict=npv_dict,
            top_n_loctechs_as_business_op=top_n_loctechs_as_business_op,
            probabilistic_agents=probabilistic_agents,
            opportunity_pool_depth=opportunity_pool_depth,
        )
        selected_counts, selected_total = _count_entries(top_business_opportunities)
        candidate_stats["selected_pairs_total"] = selected_total
        for product, value in selected_counts.items():
            candidate_stats[f"selected_pairs_{product}"] = value

        # Step 6: Generate new plant and furnace group for selected opportunities
        new_plants = []
        for product, sites in top_business_opportunities.items():
            for site_id, techs in sites.items():
                for tech, npv in techs.items():
                    new_plant = self.generate_new_plant(
                        site_id=site_id,
                        technology_name=tech,
                        product=product,
                        npv=npv,
                        current_year=current_year,
                        existent_plant_ids=all_plant_ids,
                        cost_data=cost_data,
                        equity_share=equity_share,
                        steel_plant_capacity=steel_plant_capacity,
                        dynamic_feedstocks=dynamic_feedstocks.get(tech.lower(), []),
                        plant_lifetime=plant_lifetime,
                        disposal_cost_outputs=disposal_cost_outputs,
                        derive_geo_unit=derive_geo_unit,
                    )
                    all_plant_ids.append(new_plant.plant_id)
                    new_plants.append(new_plant)
        candidate_stats["new_plants_created"] = len(new_plants)
        stats_payload = " ".join(f"{key}={value}" for key, value in candidate_stats.items())
        logger.info(f"operation=new_plant_candidate_summary {stats_payload}")
        return commands.AddNewBusinessOpportunities(new_plants=new_plants)

    def update_dynamic_costs_for_business_opportunities(
        self,
        current_year: Year,
        consideration_time: int,
        construction_time: int,
        custom_energy_costs: dict,
        capex_dict_all_locs: dict[str, dict[str, float]],
        cost_debt_all_locs: dict[str, dict[str, float]],
        iso3_to_region_map: dict[str, str],
        global_risk_free_rate: float,
        avg_utilization: dict[str, dict[str, float]],
        capex_subsidies: dict[str, dict[str, list[Subsidy]]] = {},
        debt_subsidies: dict[str, dict[str, list[Subsidy]]] = {},
        energy_subsidies: dict[str, dict[str, dict[str, list[Subsidy]]]] = {},
    ) -> list[commands.Command]:
        """
        Update dynamic cost data for all furnace groups in all "indi" plants which have not been
        constructed yet (aka, business opportunities; status: considered, announced).

        Dynamic costs include:
            - CAPEX with subsidies
            - Cost of debt with subsidies
            - Energy costs from custom energy model (with subsidies)
            - Expected utilisation, refreshed from the current fleet average for the
              technology (considered opportunities only — announced ones keep their value,
              which is reset to 0 at construction anyway)

        Dynamic costs are updated based on the following logic:
            - Base costs: CAPEX, cost of debt, electricity costs, and hydrogen costs are set to the
              current year.
            - CAPEX and debt subsidies: set to the construction start year, which is the [current year
              + consideration time + announcement time - years considered] for considered BOs and
              [current year + 1] for announced BOs, floored at [current year + 1].
            - Energy subsidies: set to the operating start year [construction start + construction
              time], since subsidised carrier prices are what the plant pays once running. This matches
              opportunity creation, PAM and the window the score series prices.

        Args:
            current_year: Current simulation year
            consideration_time: Number of years to consider before announcement
            construction_time: Years to build the plant, offsetting construction start to operating start
            custom_energy_costs: Dictionary containing power_price and capped_lcoh data arrays
            capex_dict_all_locs: Dictionary mapping region -> tech -> capex
            cost_debt_all_locs: Dictionary mapping iso3 -> tech -> cost of debt
            iso3_to_region_map: Dictionary mapping ISO3 country codes to regions
            global_risk_free_rate: Global risk-free interest rate
            avg_utilization: Fleet-average utilisation per technology
                (``Environment.avg_utilization``: tech -> {"utilization_rate": rate})
            capex_subsidies: Dictionary mapping geo_key -> tech -> list of capex subsidies
                (geo_key = "ISO3" or "ISO3:unit"; country and sub-national rows merge additively)
            debt_subsidies: Dictionary mapping geo_key -> tech -> list of debt subsidies
            energy_subsidies: Dictionary mapping carrier -> geo_key -> tech -> list of energy subsidies

        Returns:
            List of UpdateDynamicCosts commands for each furnace group that was updated.

        Note: This results in an adjusted NPV metric, which proved to be best to ensure the right
        plants are opened, because subsidized technologies would otherwise suffer a too long delay
        until the model picks them up. In real life, subsidies are often announced years in advance
        of actual plant construction. This metric only affects the decision to open a plant, not the
        actual costs once opened.
        """
        logger = logging.getLogger(f"{__name__}.PlantGroup.update_dynamic_costs_for_business_opportunities")
        from steelo.domain import calculate_costs as cc

        logger.info(
            f"[NEW PLANTS] PlantGroup.update_dynamic_costs_for_business_opportunities: Processing {len(self.plants)} plants"
        )

        update_commands: list[commands.Command] = []
        for plant in self.plants:
            iso3 = plant.location.iso3
            region = iso3_to_region_map.get(iso3)

            # Get per-technology cost of debt (without subsidies)
            cost_of_debt_for_iso3 = cost_debt_all_locs.get(iso3, None)
            if not cost_of_debt_for_iso3:
                logger.error(
                    f"[NEW PLANTS] Cost of debt not found for {iso3}. "
                    f"Cannot update costs for plant at ({plant.location.lat}, {plant.location.lon})."
                )
                continue

            for fg in plant.furnace_groups:
                if fg.status in ["considered", "announced"]:
                    cost_of_debt = cost_of_debt_for_iso3.get(fg.technology.name, None)
                    if not cost_of_debt:
                        logger.error(
                            f"[NEW PLANTS] Cost of debt not found for {iso3}/{fg.technology.name}. "
                            f"Cannot update costs at ({plant.location.lat}, {plant.location.lon})."
                        )
                        continue

                    # Get CAPEX (without subsidies)
                    capex = capex_dict_all_locs.get(region, {}).get(fg.technology.name, None) if region else None
                    if not capex:
                        logger.error(
                            f"[NEW PLANTS] CAPEX not found for {fg.technology.name} in {region}. "
                            f"Cannot update costs for {fg.technology.name} at ({plant.location.lat}, {plant.location.lon})."
                        )
                        continue

                    # Get subsidies for this location and technology - filter to only active ones
                    all_debt_subsidies = collect_subsidies_for_geo(debt_subsidies, plant.location.geo_key).get(
                        fg.technology.name, []
                    )
                    all_capex_subsidies = collect_subsidies_for_geo(capex_subsidies, plant.location.geo_key).get(
                        fg.technology.name, []
                    )

                    if fg.status == "announced":
                        year = Year(current_year + 1)  # announcement_time = 1
                    elif fg.status == "considered":
                        if fg.historical_npv_business_opportunities:
                            years_already_considered = len(fg.historical_npv_business_opportunities)
                        else:
                            years_already_considered = 0
                        # Floored at the soonest path still open to a considered opportunity:
                        # announce now, construct from next year (mirrors track_business_opportunities)
                        year = Year(
                            max(current_year + consideration_time + 1 - years_already_considered, current_year + 1)
                        )  # announcement_time = 1
                    operating_start_year = Year(year + construction_time)

                    selected_debt_subsidies = filter_subsidies_for_year(all_debt_subsidies, year)
                    selected_capex_subsidies = filter_subsidies_for_year(all_capex_subsidies, year)

                    logger.debug(
                        f"[NEW PLANTS]: Subsidies for {fg.technology.name} in {iso3} for year {current_year}: "
                        f"{len(all_debt_subsidies)} total debt -> {len(selected_debt_subsidies)} active, "
                        f"{len(all_capex_subsidies)} total capex -> {len(selected_capex_subsidies)} active"
                    )

                    # Calculate updated costs
                    new_costs: dict[str, Any] = {}
                    new_costs["cost_of_debt"] = cc.calculate_debt_with_subsidies(
                        cost_of_debt=cost_of_debt,
                        debt_subsidies=selected_debt_subsidies,
                        risk_free_rate=global_risk_free_rate,
                    )
                    new_costs["capex"] = cc.calculate_capex_with_subsidies(
                        capex=capex,
                        capex_subsidies=selected_capex_subsidies,
                    )
                    raw_power_price = (
                        custom_energy_costs["power_price"].sel(lat=plant.location.lat, lon=plant.location.lon).values
                    )
                    power_price = float(raw_power_price) if raw_power_price is not None else None
                    no_sub = fg.energy_costs_no_subsidy or fg.energy_costs
                    if power_price is None or math.isnan(power_price):
                        power_price = no_sub.get("electricity")
                    raw_hydrogen_price = (
                        custom_energy_costs["capped_lcoh"].sel(lat=plant.location.lat, lon=plant.location.lon).values
                    )
                    hydrogen_price = float(raw_hydrogen_price) if raw_hydrogen_price is not None else None
                    if hydrogen_price is None or (hydrogen_price is not None and math.isnan(hydrogen_price)):
                        hydrogen_price = no_sub.get("hydrogen")

                    if power_price is not None:
                        new_costs["electricity"] = power_price
                    if hydrogen_price is not None:
                        new_costs["hydrogen"] = hydrogen_price

                    # Apply energy carrier subsidies
                    active_energy_subs: dict[str, list] = {}
                    for carrier, carrier_subs in energy_subsidies.items():
                        all_subs = collect_subsidies_for_geo(carrier_subs, plant.location.geo_key).get(
                            fg.technology.name, []
                        )
                        active = filter_subsidies_for_year(all_subs, operating_start_year)
                        if active:
                            active_energy_subs[carrier] = active

                    # Build full energy cost dicts with updated electricity/hydrogen
                    base_costs = dict(fg.energy_costs_no_subsidy or fg.energy_costs)
                    if power_price is not None:
                        base_costs["electricity"] = power_price
                    if hydrogen_price is not None:
                        base_costs["hydrogen"] = hydrogen_price

                    if active_energy_subs:
                        new_energy_costs, new_output_energy_costs, new_energy_costs_no_subsidy = (
                            cc.get_subsidised_energy_costs(base_costs, active_energy_subs)
                        )
                        sub_summary = ", ".join(f"{len(s)} {c}" for c, s in active_energy_subs.items())
                        logger.debug(
                            f"[NEW PLANTS] {iso3}/{fg.technology.name} year={operating_start_year} Subs: {sub_summary}",
                        )
                    else:
                        new_energy_costs = dict(base_costs)
                        new_output_energy_costs = dict(base_costs)
                        new_energy_costs_no_subsidy = dict(base_costs)

                    # Refresh the expected utilisation from the current fleet average
                    if fg.status == "considered":
                        new_utilization_rate = avg_utilization.get(fg.technology.name, {}).get("utilization_rate", 0.6)
                    else:
                        new_utilization_rate = fg.utilization_rate

                    # Check if costs have actually changed
                    old_costs_cmp = {
                        "cost_of_debt": fg.cost_of_debt,
                        "capex": fg.technology.capex,
                        "energy_costs": fg.energy_costs,
                        "output_energy_costs": fg.output_energy_costs,
                        "utilization_rate": fg.utilization_rate,
                    }
                    new_costs_cmp = {
                        "cost_of_debt": new_costs["cost_of_debt"],
                        "capex": new_costs["capex"],
                        "energy_costs": new_energy_costs,
                        "output_energy_costs": new_output_energy_costs,
                        "utilization_rate": new_utilization_rate,
                    }
                    if old_costs_cmp == new_costs_cmp:
                        continue  # Skip if no changes
                    logger.debug(
                        "[NEW PLANTS] Updating dynamic costs for FG %s",
                        fg.furnace_group_id,
                    )
                    update_commands.append(
                        commands.UpdateDynamicCosts(
                            plant_id=plant.plant_id,
                            furnace_group_id=fg.furnace_group_id,
                            new_capex=new_costs["capex"],
                            new_capex_no_subsidy=capex,
                            new_cost_of_debt=new_costs["cost_of_debt"],
                            new_cost_of_debt_no_subsidy=cost_of_debt,
                            new_energy_costs=new_energy_costs,
                            new_output_energy_costs=new_output_energy_costs,
                            new_energy_costs_no_subsidy=new_energy_costs_no_subsidy,
                            new_utilization_rate=new_utilization_rate,
                        )
                    )
        return update_commands

    def update_status_of_business_opportunities(
        self,
        current_year: Year,
        consideration_time: int,
        market_price: dict[str, list[float]],
        cost_of_equity_all_locs: dict[str, dict[str, float]],
        probability_of_announcement: float,
        probability_of_construction: float,
        plant_lifetime: int,
        construction_time: int,
        allowed_techs: dict[Year, list[str]],
        new_plant_capacity_in_year: Callable[[str], float],
        expanded_capacity: float,
        capacity_limit_iron: float,
        capacity_limit_steel: float,
        new_capacity_share_from_new_plants: float,
        reductant_score_series: Callable[..., ReductantScoreSeries],
        opex_subsidies: dict[str, dict[str, list[Subsidy]]] = {},  # iso3 -> tech -> list
        get_co2_headroom: Callable[[str, int, float], float] | None = None,
        get_co2_need: Callable[["Technology", float, str], float] | None = None,
        co2_storage_diagnostics: Callable[[str, int], tuple[float, float, float]] | None = None,
        reserved_discount_factor: float = 0.9,
    ) -> list[commands.Command]:
        """
        Recalculate the NPV and update the status of all considered and announced business opportunities.

        Args:
            current_year: Current simulation year
            consideration_time: Number of years to consider before announcement
            market_price: Dictionary mapping product to list of future market prices,
                anchored at the current year; re-anchored to the opportunity's earliest
                construction start before the NPV
            cost_of_equity_all_locs: Dictionary mapping iso3 -> tech -> cost of equity
            probability_of_announcement: Probability that a viable opportunity will be announced
            probability_of_construction: Probability that an announced plant starts construction
            plant_lifetime: Lifetime of the plant in years
            construction_time: Time required for plant construction in years
            allowed_techs: Dictionary mapping year to list of allowed technologies
            new_plant_capacity_in_year: Callable to get capacity from new plants only (not expansions)
            expanded_capacity: Capacity (in tonnes) added if the plant is constructed
            capacity_limit_iron: Capacity limit for iron (in tonnes) added this year in total
                (new plants and expansions)
            capacity_limit_steel: Capacity limit for steel (in tonnes) added this year in total
                (new plants and expansions)
            new_capacity_share_from_new_plants: Share of new capacity from new plants (vs expansions)
            reductant_score_series: ``Environment.reductant_score_series``, threaded to the
                per-opportunity re-check
            opex_subsidies: Dictionary mapping iso3 -> tech -> list of opex subsidies

        Returns:
            List of commands to update the status of furnace groups.

        Note: Order matters, convert_business_opportunity_into_actual_project must be called before
        track_business_opportunities to ensure proper status transitions.
        """
        logger = logging.getLogger(f"{__name__}.PlantGroup.update_status_of_business_opportunities")
        logger.info(
            f"[NEW PLANTS] PlantGroup.update_status_of_business_opportunities: Processing {len(self.plants)} plants"
        )
        status_change_cmds = []
        status_stats: Counter = Counter()
        status_stats["plants_seen"] = len(self.plants)

        # Get allowed technologies for current year
        allowed_techs_current_year = allowed_techs.get(current_year, [])
        if not allowed_techs_current_year:
            raise ValueError(
                f"[NEW PLANTS] No allowed technologies found for year {current_year}. "
                f"Check the allowed techs object in the environment: {allowed_techs}"
            )

        # Shuffle to avoid selection bias in case of limited capacity
        for plant in random.sample(self.plants, len(self.plants)):
            iso3 = plant.location.iso3

            # Extract cost of equity (not dynamic, but not stored in the furnace group and needed for the NPV calculation)
            cost_of_equity_for_iso3 = cost_of_equity_all_locs.get(iso3, None)
            if not cost_of_equity_for_iso3:
                logger.error(
                    f"[NEW PLANTS] Cost of equity not found for {iso3}. "
                    f"Cannot update status for business opportunity at ({plant.location.lat}, {plant.location.lon}) in {plant.location.iso3}."
                )
                status_stats["missing_cost_of_equity"] += 1
                continue

            for fg in plant.furnace_groups:
                status_stats["furnace_groups_seen"] += 1
                cost_of_equity = cost_of_equity_for_iso3.get(fg.technology.name, None)
                if not cost_of_equity:
                    logger.error(f"[NEW PLANTS] Cost of equity not found for {iso3}/{fg.technology.name}.")
                    status_stats["missing_cost_of_equity"] += 1
                    continue
                # Planned and announced business opportunities are converted into actual projects with a certain probability
                if fg.status == "announced":
                    status_stats["announced_opportunities"] += 1
                    update_status_cmd = fg.convert_business_opportunity_into_actual_project(
                        probability_of_construction=probability_of_construction,
                        allowed_techs_current_year=allowed_techs_current_year,
                        new_plant_capacity_in_year=new_plant_capacity_in_year,
                        expanded_capacity=expanded_capacity,
                        capacity_limit_iron=capacity_limit_iron,
                        capacity_limit_steel=capacity_limit_steel,
                        new_capacity_share_from_new_plants=new_capacity_share_from_new_plants,
                        location=plant.location,
                        status_stats=status_stats,
                        get_co2_headroom=get_co2_headroom,
                        get_co2_need=get_co2_need,
                        co2_storage_diagnostics=co2_storage_diagnostics,
                        construction_time=construction_time,
                        reserved_discount_factor=reserved_discount_factor,
                    )
                    if update_status_cmd:
                        status_change_cmds.append(update_status_cmd)
                        status_stats["status_updates_emitted"] += 1
                        # Move to the next furnace group if the status has already been updated to avoid overwriting
                        continue

                # Considered business opportunities are updated each year to see if they remain profitable
                if fg.status == "considered":
                    status_stats["considered_opportunities"] += 1
                    update_status_cmd = fg.track_business_opportunities(
                        year=current_year,
                        location=plant.location,
                        market_price=market_price,
                        cost_of_equity=cost_of_equity,
                        plant_lifetime=plant_lifetime,
                        construction_time=construction_time,
                        consideration_time=consideration_time,
                        probability_of_announcement=probability_of_announcement,
                        all_opex_subsidies=collect_subsidies_for_geo(opex_subsidies, plant.location.geo_key).get(
                            fg.technology.name, []
                        ),
                        reductant_score_series=reductant_score_series,
                        status_stats=status_stats,
                        get_co2_headroom=get_co2_headroom,
                        get_co2_need=get_co2_need,
                        co2_storage_diagnostics=co2_storage_diagnostics,
                    )
                    if update_status_cmd:
                        status_change_cmds.append(update_status_cmd)
                        status_stats["status_updates_emitted"] += 1
        if status_stats:
            stats_snapshot = " ".join(f"{key}={value}" for key, value in sorted(status_stats.items()))
            logger.info(f"operation=new_plant_status_summary year={current_year} {stats_snapshot}")
        return status_change_cmds


# -------------------------------------------------------------------------------------------------------------------------------------------- #
class DemandCenter:
    def __init__(self, demand_center_id: str, center_of_gravity: Location, demand_by_year: dict[Year, Volumes]) -> None:
        self.demand_center_id = demand_center_id
        self.center_of_gravity = center_of_gravity
        self.demand_by_year = demand_by_year
        self.demand_type = "unknown"

    def __repr__(self) -> str:
        return f"DemandCenter: <{self.demand_center_id}>"

    def __hash__(self):
        return hash(self.demand_center_id)

    def __eq__(self, other) -> bool:
        return self.demand_center_id == other.demand_center_id


class SteelAllocations:
    def __init__(self, allocations: dict[tuple[Plant, FurnaceGroup, DemandCenter], Volumes]) -> None:
        self.allocations = allocations

    def __repr__(self) -> str:
        return f"{len(self.allocations)} SteelAllocations"

    def get_total(self) -> Volumes:
        return sum(self.allocations.values(), Volumes(0))


class Supplier:
    def __init__(
        self,
        supplier_id: str,
        location: Location,
        commodity: str,
        capacity_by_year: dict[Year, Volumes],
        production_cost_by_year: dict[Year, float],
        mine_cost_by_year: dict[Year, float] | None = None,
        mine_price_by_year: dict[Year, float] | None = None,
    ) -> None:
        self.supplier_id = supplier_id
        self.location = location
        self.capacity_by_year = capacity_by_year
        self.commodity = commodity
        self.production_cost_by_year = production_cost_by_year
        self.mine_cost_by_year = mine_cost_by_year if mine_cost_by_year is not None else {}
        self.mine_price_by_year = mine_price_by_year if mine_price_by_year is not None else {}

    def __repr__(self) -> str:
        return f"Supplier: <{self.supplier_id}>"

    def __hash__(self):
        return hash(self.supplier_id)

    def __eq__(self, other) -> bool:
        return self.supplier_id == other.supplier_id


class RawMaterialSource:
    def __init__(
        self,
        *,
        source_id: str,
        location: Location,
        capacity_by_material: dict[str, Volumes],
        cost_by_material: dict[str, float],
    ) -> None:
        self.source_id = source_id
        self.location = location
        self.capacity_by_material = capacity_by_material
        self.cost_by_material = cost_by_material


class TradeTariff:
    """Trade tariff applied to commodity flows between countries.

    Represents bilateral trade policy instruments including import duties, export taxes,
    and volume quotas. Used in trade optimization to model trade costs and constraints
    between origin and destination countries.

    Tariffs can be specified as:
        - Absolute tax (fixed USD per tonne)
        - Percentage tax (% of commodity value)
        - Volume quotas (maximum tonnes allowed)

    Args:
        tariff_name: Descriptive name for the tariff.
        from_iso3: ISO3 code of exporting country (origin).
        to_iso3: ISO3 code of importing country (destination).
        tariff_id: Unique identifier. Auto-generated UUID if not provided.
        tax_absolute: Fixed tax amount in USD per tonne. None if not applicable.
        tax_percentage: Tax as percentage of commodity value (0-100). None if not applicable.
        quota: Maximum volume allowed (Volumes object with yearly limits). None if unlimited.
        start_date: Year when tariff becomes active (inclusive). None for always active.
        end_date: Year when tariff expires (inclusive). None for permanent.
        metric: Measurement unit or basis for the tariff (e.g., "USD/tonne", "percent").
        commodity: Specific commodity name this tariff applies to (e.g., "steel", "iron_ore").
            None means applies to all commodities on this route.

    Attributes:
        tariff_id: Unique identifier string (UUID format).
        tariff_name: Name of the tariff.
        from_iso3: Origin country ISO3 code.
        to_iso3: Destination country ISO3 code.
        tax_absolute: Absolute tax in USD/tonne.
        tax_percentage: Percentage tax rate.
        quota: Volume quota limits.
        start_date: Start year.
        end_date: End year.
        metric: Measurement unit.
        commodity: Applicable commodity.

    Example:
        >>> tariff = TradeTariff(
        ...     tariff_name="EU_Steel_Import_25pct",
        ...     from_iso3="CHN",
        ...     to_iso3="DEU",
        ...     tax_percentage=25.0,
        ...     start_date=Year(2025),
        ...     commodity="steel"
        ... )

    Notes:
        - Quotas are enforced in trade optimization as volume constraints.
        - Tariffs are directional: from_iso3 → to_iso3 (reverse direction needs separate tariff).
        - Temporal validity checked via start_date and end_date (both inclusive).
    """

    def __init__(
        self,
        tariff_name: str,
        from_iso3: str,
        to_iso3: str,
        tariff_id: str | None = None,
        tax_absolute: float | None = None,
        tax_percentage: float | None = None,
        quota: Volumes | None = None,
        start_date: Year | None = None,
        end_date: Year | None = None,
        metric: str | None = None,
        commodity: str | None = None,
    ) -> None:
        self.tariff_name = tariff_name
        self.from_iso3 = from_iso3
        self.to_iso3 = to_iso3
        self.tax_absolute = tax_absolute
        self.tax_percentage = tax_percentage
        self.quota = quota
        self.start_date = start_date
        self.end_date = end_date
        self.metric = metric
        self.commodity = commodity
        if tariff_id is None:
            self.tariff_id = str(uuid.uuid4())
        else:
            self.tariff_id = tariff_id

    def __repr__(self) -> str:
        return f"TradeTariff: <{self.tariff_name}>"

    def __hash__(self):
        return hash(self.tariff_name)

    def __eq__(self, other) -> bool:
        return self.tariff_name == other.tariff_name


class Subsidy:
    """Financial subsidy reducing costs for specific technologies and regions.

    Represents a policy instrument that reduces costs for steel production technologies
    within a country — or a sub-national unit of it — during a specified time period.

    Attributes:
        scenario_name: Name of the subsidy scenario/policy.
        iso3: ISO3 country code where the subsidy applies.
        geo_unit: Optional sub-national division (ISO 3166-2 code, e.g. "CN-HE"); None means
            the subsidy applies country-wide.
        start_year: First year the subsidy is active (inclusive).
        end_year: Last year the subsidy is active (inclusive).
        technology_name: Technology the subsidy applies to, or "all" for all technologies.
        cost_item: Type of cost subsidised. Financial: "opex", "capex", "cost of debt".
            Energy carriers: any normalised carrier name (e.g. "hydrogen", "electricity",
            "natural_gas", "coal", etc.) matching energy_costs dict keys.
        subsidy_type: Either "absolute" (fixed amount) or "relative" (percentage).
        subsidy_amount: The subsidy value with units depending on cost_item:
            - energy carriers: USD per carrier unit (absolute) or decimal fraction (relative)
            - opex/capex: USD/t steel (absolute) or decimal fraction (relative)
            - cost of debt: percentage points (absolute only)
        subsidy_name: Auto-generated unique identifier string (geo-keyed, so a country row
            and a province row for the same iso3/tech/item stay distinct).
    """

    def __init__(
        self,
        scenario_name: str,
        iso3: str,
        start_year: Year,
        end_year: Year,
        technology_name: str,
        cost_item: str,
        subsidy_type: str,
        subsidy_amount: float,
        geo_unit: str | None = None,
    ) -> None:
        self.scenario_name = scenario_name
        self.iso3 = iso3
        self.geo_unit = geo_unit
        self.start_year = start_year
        self.end_year = end_year
        self.technology_name = technology_name
        self.cost_item = cost_item
        self.subsidy_type = subsidy_type
        self.subsidy_amount = subsidy_amount
        self.subsidy_name = (
            f"{self.geo_key}_{self.scenario_name}_{self.technology_name}_{self.cost_item}_{self.subsidy_type}"
        )

    @property
    def geo_key(self) -> str:
        """Finest-available geographic key (mirrors ``Location.geo_key``)."""
        return compose_geo_key(self.iso3, self.geo_unit)

    def __repr__(self) -> str:
        return f"Subsidy: <{self.subsidy_name}>"

    def __hash__(self):
        return hash(
            (
                self.scenario_name,
                self.iso3,
                self.geo_unit,
                self.start_year,
                self.end_year,
                self.technology_name,
                self.cost_item,
                self.subsidy_type,
                self.subsidy_amount,
            )
        )

    def __eq__(self, other) -> bool:
        if not isinstance(other, Subsidy):
            return False
        return (
            self.scenario_name == other.scenario_name
            and self.iso3 == other.iso3
            and self.geo_unit == other.geo_unit
            and self.start_year == other.start_year
            and self.end_year == other.end_year
            and self.technology_name == other.technology_name
            and self.cost_item == other.cost_item
            and self.subsidy_type == other.subsidy_type
            and self.subsidy_amount == other.subsidy_amount
        )


class CarbonCostSeries:
    def __init__(
        self,
        iso3: str,
        carbon_cost: dict[Year, float],
    ) -> None:
        self.iso3 = iso3
        self.carbon_cost = carbon_cost
        # Internal storage for CarbonCost objects (lazy initialization)
        self._carbon_cost_objects: dict[Year, CarbonCost] | None = None

    def __repr__(self) -> str:
        return f"carbon_cost: <{self.iso3}>"

    def __hash__(self):
        return hash(self.iso3)


class RailwayCost:
    """Domain object representing railway infrastructure costs by country."""

    def __init__(self, iso3: str, cost_per_km: float) -> None:
        """
        Initialize railway cost data.

        Args:
            iso3: ISO-3 country code
            cost_per_km: Cost in Million USD per km
        """
        self.iso3 = iso3
        self.cost_per_km = cost_per_km
        self.id = self.iso3  # For repository compatibility

    def __repr__(self) -> str:
        return f"RailwayCost(iso3={self.iso3}, cost_per_km={self.cost_per_km} MUSD/km)"

    def __eq__(self, other) -> bool:
        if not isinstance(other, RailwayCost):
            return False
        return self.iso3 == other.iso3

    def __hash__(self) -> int:
        return hash(self.iso3)

    def get_cost_in_usd_per_km(self) -> float:
        """Get cost in USD per km (converts from Million USD)."""
        return self.cost_per_km * MioUSD_TO_USD


@dataclass
class Capex:
    """
    Class to handle the capital expenditure (CAPEX) for different technologies and regions.
    It allows for dynamic updates based on capex reduction ratios and learning rates.
    """

    technology_name: str
    product: str
    greenfield_capex: float
    capex_renovation_share: float
    learning_rate: float


# Non-technology financing categories in the "Cost of capital" sheet
RENEWABLES_KEY = "Renewables"
HYDROGEN_KEY = "Hydrogen"


@dataclass(frozen=True)
class TechFinancingRates:
    """Financing rates for one (country, technology) pair, as fractions (0.05 = 5%)."""

    cost_of_debt: float
    cost_of_equity: float
    cost_of_capital: float


class CostOfCapital:
    """
    Per-country financing rates, differentiated by technology.

    `techs` maps a technology name (verbatim from the master Excel, matching
    Technology.name) to its rates; the special keys RENEWABLES_KEY and
    HYDROGEN_KEY carry the non-steelmaking categories.
    """

    def __init__(
        self,
        country: str,
        iso3: str,
        techs: dict[str, TechFinancingRates],
    ) -> None:
        self.country = country
        self.iso3 = iso3
        self.techs = techs

    def __repr__(self) -> str:
        return f"Cost of Capital: <{self.iso3}>"

    def __hash__(self):
        return hash(self.iso3)

    def __eq__(self, other) -> bool:
        return self.iso3 == other.iso3


class CountryMappingService:
    """Service to provide country mapping lookups from dynamic data."""

    def __init__(self, mappings: list[CountryMapping]):
        self._mappings = {m.country: m for m in mappings}

        # Pre-compute lookups for performance
        self._code_to_country = {v.iso3: k for k, v in self._mappings.items()}
        self._code_to_irena = {v.iso3: v.irena_name for v in self._mappings.values()}
        self._code_to_irena_region = {v.iso3: v.irena_region for v in self._mappings.values()}
        self._code_to_region = {v.iso3: v.region_for_outputs for v in self._mappings.values()}
        self._code_to_ssp_region = {v.iso3: v.ssp_region for v in self._mappings.values()}
        self._gem_to_ws_region = {
            v.gem_country: v.ws_region for v in self._mappings.values() if v.gem_country and v.ws_region
        }
        self._code_to_eu = {v.iso3: v.eu_region for v in self._mappings.values() if v.eu_region is not None}

    # Lookup methods
    def get_code(self, country: str) -> str | None:
        return self._code_to_country.get(country)

    def get_irena_name(self, country: str) -> str | None:
        return self._code_to_irena.get(country)

    def get_irena_region(self, country: str) -> str | None:
        return self._code_to_irena_region.get(country)

    def get_region(self, country: str) -> str | None:
        return self._code_to_region.get(country)

    def get_ssp_region(self, country: str) -> str | None:
        return self._code_to_ssp_region.get(country)

    def get_ws_region_for_gem(self, gem_country: str) -> str | None:
        return self._gem_to_ws_region.get(gem_country)

    def get_eu_region(self, country: str) -> str | None:
        return self._code_to_eu.get(country)

    # Direct map access (for code that needs the full dict)
    @property
    def code_to_country_map(self) -> dict[str, str]:
        return self._code_to_country.copy()

    @property
    def code_to_irena_map(self) -> dict[str, str]:
        return self._code_to_irena.copy()

    @property
    def code_to_irena_region_map(self) -> dict[str, str | None]:
        return self._code_to_irena_region.copy()

    @property
    def code_to_region_map(self) -> dict[str, str]:
        return self._code_to_region.copy()

    @property
    def code_to_ssp_region_map(self) -> dict[str, str]:
        return self._code_to_ssp_region.copy()

    @property
    def gem_country_ws_region_map(self) -> dict[str, str]:
        return self._gem_to_ws_region.copy()

    @property
    def code_to_eu_region_map(self) -> dict[str, str]:
        return self._code_to_eu.copy()

    def iso3_to_region(self) -> dict[str, str]:
        """Return a mapping from ISO3 codes to regions (region_for_outputs)."""
        return {mapping.iso3: mapping.region_for_outputs for mapping in self._mappings.values()}

    @property
    def mappings(self) -> list[CountryMapping]:
        """All country mappings, in sheet order."""
        return list(self._mappings.values())


class VirginIronDemand:
    """
    Class to store precalculated virgin iron demand for all available years.
    Calculates demand once at initialization and provides efficient access methods.
    """

    def __init__(
        self,
        world_suppliers: list["Supplier"],
        steel_demand_dict: dict[str, dict[Year, Volumes]],
        dynamic_feedstocks: dict[str, list[PrimaryFeedstock]],
    ):
        """
        Initialize and precalculate virgin iron demand for all available years.

        Args:
            world_suppliers: List of all suppliers in the world
            steel_demand_dict: Steel demand by demand center and year
            dynamic_feedstocks: Feedstock requirements by technology
        """
        logger = logging.getLogger(f"{__name__}.VirginIronDemand.__init__")
        # Store the precalculated demands by year
        self._demand_by_year: dict[Year, float] = {}

        # Determine the range of years from available data
        all_years: set[Year] = set()

        # Get years from suppliers
        for supplier in world_suppliers:
            if supplier.commodity == "scrap":
                all_years.update(supplier.capacity_by_year.keys())

        # Get years from steel demand
        for demand_centre_data in steel_demand_dict.values():
            all_years.update(demand_centre_data.keys())

        if not all_years:
            logger.warning("[VIRGIN IRON DEMAND]: No years found in data")
            return

        self.min_year = min(all_years)
        self.max_year = max(all_years)

        # Calculate scrap requirements once
        scrap_reqs_list: list[float] = []
        for _, process_dict in dynamic_feedstocks.items():
            for process in process_dict:
                if process.metallic_charge.lower() == "scrap" and process.required_quantity_per_ton_of_product:
                    scrap_reqs_list.append(process.required_quantity_per_ton_of_product)

        scrap_required_quantity = sum(scrap_reqs_list) / len(scrap_reqs_list) if scrap_reqs_list else 0.0

        if scrap_required_quantity == 0.0:
            logger.warning("[VIRGIN IRON DEMAND]: No scrap processes found - no steel can be produced from scrap")

        # Precalculate for all years
        for year in sorted(all_years):
            # Get scrap available for this year
            scrap_available = 0.0
            for supplier in world_suppliers:
                if supplier.commodity == "scrap":
                    scrap_available += supplier.capacity_by_year.get(year, 0.0)

            # Get steel demand for this year
            steel_demand = 0.0
            for demand_centre in steel_demand_dict.keys():
                steel_demand += steel_demand_dict[demand_centre].get(year, 0.0)

            # Calculate steel from scrap and iron
            if scrap_required_quantity == 0.0:
                steel_from_scrap = 0.0
                steel_from_iron = steel_demand
            else:
                steel_from_scrap = scrap_available / scrap_required_quantity
                steel_from_iron = steel_demand - steel_from_scrap

            # Calculate virgin iron demand (assuming 1.1 t iron per t steel)
            iron_required_quantity = 1.1
            virgin_iron_demand = steel_from_iron * iron_required_quantity

            # Store result
            self._demand_by_year[year] = virgin_iron_demand

        logger.info(
            f"[VIRGIN IRON DEMAND]: Precalculated demand for {len(self._demand_by_year)} years "
            f"({self.min_year} to {self.max_year})"
        )
        logger.debug(
            f"[VIRGIN IRON DEMAND]: Year {self.min_year}: {self._demand_by_year.get(self.min_year, 0) * T_TO_KT:,.0f} kt, "
            f"Year {self.max_year}: {self._demand_by_year.get(self.max_year, 0) * T_TO_KT:,.0f} kt"
        )

    def get_demand(self, year: Year) -> float:
        """Get virgin iron demand for a specific year."""
        logger = logging.getLogger(f"{__name__}.VirginIronDemand.get_demand")
        if year not in self._demand_by_year:
            logger.warning(f"Year {year} not in precalculated data, returning 0")
            return 0.0
        return self._demand_by_year[year]

    def get_demand_series(self, start_year: Year, num_years: int) -> list[float]:
        """
        Get virgin iron demand series for a specified number of years.

        Args:
            start_year: Starting year
            num_years: Number of years to include in the series

        Returns:
            List of virgin iron demands for num_years starting from start_year
        """
        logger = logging.getLogger(f"{__name__}.VirginIronDemand.get_demand_series")
        series = []
        latest_value = 0.0
        for i in range(num_years):
            year = Year(start_year + i)
            demand_value = self._demand_by_year.get(year)
            if demand_value is None:
                logger.warning(f"Year {year} not in precalculated iron demand data, returning previous value")
                series.append(latest_value)
            else:
                series.append(demand_value)
                latest_value = demand_value
        return series

    def get_demand_range(self, start_year: Year, end_year: Year) -> list[float]:
        """
        Get virgin iron demand for a range of years.

        Args:
            start_year: First year of the range
            end_year: Last year of the range (inclusive)

        Returns:
            List of virgin iron demands for each year in the range
        """
        return [self._demand_by_year.get(Year(y), 0.0) for y in range(start_year, end_year + 1)]


class Environment:
    """
    Class to track system environment, e.g. the collective macro-scale of the system
    such as total capacity of technologies to produce commodity X, cost curve of steel and
    iron per unit of production, prediction of market prices, emissions, capex, and optimal plant technology
    contains yearly demand dicts and the iteration year
    """

    # TODO: Re-write all sub-fuctions because they now receive list[FurnaceGroup], list[Plant]. When GEO logic updates the uow with new plants, this needs to be updated.
    def __init__(
        self,
        # cost_of_x_csv: Path,
        default_coc: float = 0.25,  # TODO: Remove hardcoded cost of capital
        tech_switches_csv: Path | None = None,
        config: Optional["SimulationConfig"] = None,
    ) -> None:
        """
        Initialise the environment with the capex values and switching capex
        (i.e. the capex needed switching from tech A --> B)
        """
        if config is None:
            raise ValueError("SimulationConfig must be provided to Environment")
        self.config = config
        self.year = self.config.start_year

        # Allowed technologies and transitions
        self._cached_allowed_techs: Optional[dict[Year, list[str]]] = None
        self.load_allowed_transitions(tech_switches_csv=tech_switches_csv)

        # Initialize costs - will be populated during simulation setup
        self.name_to_capex: dict[str, dict[str, dict[str, float]]] = {}  # capex_dict
        # iso3 -> technology name -> rate (fraction); includes RENEWABLES_KEY/HYDROGEN_KEY entries
        self.cost_of_debt_by_tech: dict[str, dict[str, float]] = {}
        self.cost_of_equity_by_tech: dict[str, dict[str, float]] = {}
        self.railway_costs: list[RailwayCost] = []
        self.dynamic_feedstock_cost: dict[str, float] = {}

        # Initialize CarbonCostService for centralized carbon cost calculations
        self.carbon_cost_service = CarbonCostService(self.config.chosen_emissions_boundary_for_carbon_costs)

        # Initialize capacities - will be populated during simulation setup
        self.steel_init_capacity: defaultdict[str, defaultdict[str, float]] = defaultdict(lambda: defaultdict(float))
        self.iron_init_capacity: defaultdict[str, defaultdict[str, float]] = defaultdict(lambda: defaultdict(float))
        self.added_capacity: dict[str, float] = {}
        self.switched_capacity: dict[str, float] = {}  # Track technology switches per year
        self.new_plant_capacity: dict[str, float] = {}  # Track capacity from new plants only (separate from expansions)
        self.co2_storage_firm: dict[str, float] = {}  # iso3 -> tCO2/yr (operating + construction CCS)
        self.co2_storage_reserved: dict[str, float] = {}  # iso3 -> tCO2/yr (announced CCS, discounted)
        self.capacity_snapshot_by_product: dict[str, float] = {}
        self._diag_bof_baseline_2059: dict[str, dict[str, float]] | None = None
        self._diag_bof_sample_count: int = 0

        # Initialize demand, BOMs, and utilization - will be populated during simulation setup
        self.demand_dict: dict[str, dict[Year, Volumes]] = {}
        self.avg_boms: dict[str, dict[str, dict[str, float]]] = {}

        # Initialize most common reductant tracking - updated each year
        self.most_common_reductant_by_tech: dict[str, str] = {}
        self.avg_utilization: dict[str, dict[str, float]] = {}

        # Initialize trade tariffs as empty list
        self.trade_tariffs: list[TradeTariff] = []
        # Initialize legal process connectors as empty list
        self.legal_process_connectors: list[LegalProcessConnector] = []
        # Initialize dynamic feedstocks as empty dict
        self.dynamic_feedstocks: dict[str, list[PrimaryFeedstock]] = {}
        # Initialize carbon costs as empty dict
        self.carbon_costs: dict[str, dict[Year, float]] = {}
        # Initialize technology emission factors as empty list
        self.technology_emission_factors: list[TechnologyEmissionFactors] = []
        # Initialize input costs as empty dict, keyed by geo_key (iso3 or iso3:code)
        self.input_costs: dict[str, dict[Year, dict[str, float]]] = {}
        # Precomputed capped-LCOH series (year -> geo_key -> USD/kg), filled at bootstrap
        self.capped_hydrogen_costs_by_year: dict[Year, dict[str, float]] = {}
        # Initialize cost curves as empty dicts
        self.cost_curve: dict[str, list[dict[str, float]]] = {"steel": [], "iron": []}
        self.future_cost_curve: dict[str, list[dict[str, float]]] = {"steel": [], "iron": []}

        # Performance optimization: Distance cache for trade LP
        # Persists across years to avoid recomputation
        self._distance_cache: dict[tuple[str, str], float] = {}
        self._distance_cache_stats = {"hits": 0, "misses": 0, "computations": 0}

        # Global technology restrictions - now handled via allowed_techs system
        self.aggregated_metallic_charge_constraints: list[AggregatedMetallicChargeConstraint] = []

        # Secondary feedstock constraints - to be set during data loading
        self.secondary_feedstock_constraints: list[SecondaryFeedstockConstraint] = []

        # Geospatial data paths - to be set during simulation setup

        # LP warm-starting support (OPT-2) - store previous year's solution for faster convergence
        self.previous_lp_solution: dict[tuple[str, str, str], float] | None = None
        self.geo_paths: Optional[GeoDataPaths] = None

        self.transport_emissions: list[TransportKPI] = []
        # Initialize fallback material costs as empty list
        self.fallback_material_costs: list[FallbackMaterialCost] = []
        # Initialize default metallic charge mapping as empty dict
        self.default_metallic_charge_per_technology: dict[str, str] = {}
        self.transport_kpis: list[TransportKPI] = []  # Alias for transport_emissions for compatibility
        self.trade_allocations: Any = None  # For storing trade allocations from LP solution
        # Plot paths
        self.plot_paths: Optional[PlotPaths] = None

        # fixed operating costs (FOPEX) by ISO3 and technology
        self.fopex_by_country: dict[str, dict[str, float]] = {}

        # Output directory
        self.output_dir: Optional[Path] = None

        # Iron demand attributes
        self.iron_demand: float = 0.0
        self.virgin_iron_demand: Optional[VirginIronDemand] = None  # Will be initialized later

        # Country mappings - initialized via initiate_country_mappings
        self.country_mappings: CountryMappingService | None = None

        # Carbon border mechanisms:
        self.carbon_border_mechanisms: list[CarbonBorderMechanism] = []

        # Willingness to pay:
        self.willingness_to_pay: list[WillingnessToPay] = []

        # Furnace group clustering - initialized during AllocationModel.run when clustering is enabled
        self.cluster_mapping: dict[str, list] | None = None  # Maps cluster keys to list of FurnaceGroups
        self.meta_furnace_groups: list | None = None  # List of MetaFurnaceGroup objects

        # Geospatial data from master Excel - initialized via initiate methods
        self.hydrogen_efficiency: dict[Year, float] = {}
        self.hydrogen_capex_opex: dict[str, dict[Year, float]] = {}

        # Subsidies:
        self.opex_subsidies: dict[str, dict[str, list[Subsidy]]] = {}
        self.capex_subsidies: dict[str, dict[str, list[Subsidy]]] = {}
        self.debt_subsidies: dict[str, dict[str, list[Subsidy]]] = {}
        self.energy_subsidies: dict[
            str, dict[str, dict[str, list[Subsidy]]]
        ] = {}  # carrier -> iso3 -> tech -> [Subsidy]

    def get_transport_emissions_as_dict(self) -> dict[tuple[str, str, str], float]:
        """
        Convert transport_emissions list to dictionary format for backward compatibility.
        Returns: Dict mapping (from_iso3, to_iso3, commodity) to emission factor.
        """
        return {(te.reporter_iso, te.partner_iso, te.commodity): te.ghg_factor for te in self.transport_emissions}

    def pass_fopex_for_iso3_to_plants(self, plants: list[Plant]) -> None:
        """
        Pass the fixed operating costs (FOPEX) for each ISO3 to the plants.
        Args:
            plants (list[Plant]): List of Plant objects to update with FOPEX.
        """
        for plant in plants:
            fopex = self.fopex_by_country.get(plant.location.iso3)
            if fopex is None:
                raise ValueError(f"FOPEX not found for country {plant.location.iso3}")
            plant.technology_unit_fopex = fopex

    def initiate_technology_emission_factors(
        self, technology_emission_factors: list[TechnologyEmissionFactors]
    ) -> None:
        """
        Initialize the technology emission factors for the environment.

        Args:
            technology_emission_factors (list[TechnologyEmissionFactors]): A list of TechnologyEmissionFactors objects to be added to the environment.

        Notes:
            When dynamic feedstocks are already loaded, coverage is validated: every
            (technology, reductant, metallic charge) business-case key needs an EF row in
            the configured boundary and in every boundary present in the table. The
            reductant score does a hard EF lookup, so a gap would crash mid-run — or
            silently price carbon as zero in the legacy lenient paths — fail at load
            time instead.
        """
        self.technology_emission_factors = technology_emission_factors

        required_keys = {
            (dbc.technology.lower(), normalize_name(dbc.reductant), dbc.metallic_charge)
            for feedstocks in self.dynamic_feedstocks.values()
            for dbc in feedstocks
        }
        if not required_keys:
            return
        if not technology_emission_factors:
            raise ValueError(
                "No technology emission factors loaded but dynamic business cases exist — "
                "carbon-aware reductant scoring would fail on every lookup. Check the "
                "'Technology emission factors' sheet / technology_emission_factors.json."
            )
        boundaries = {factor.boundary for factor in technology_emission_factors}
        chosen_boundary = self.config.chosen_emissions_boundary_for_carbon_costs
        if chosen_boundary not in boundaries:
            raise ValueError(
                f"Configured emissions boundary '{chosen_boundary}' has no rows in the "
                f"technology emission factors (available: {sorted(boundaries)})."
            )
        for boundary in sorted(boundaries):
            ef_keys = {
                (factor.technology.lower(), normalize_name(factor.reductant), factor.metallic_charge)
                for factor in technology_emission_factors
                if factor.boundary == boundary
            }
            missing = required_keys - ef_keys
            if missing:
                sample = ", ".join(str(key) for key in sorted(missing)[:5])
                raise ValueError(
                    f"Technology emission factors incomplete for boundary '{boundary}': "
                    f"{len(missing)} business-case key(s) missing (e.g. {sample})."
                )

    def validate_reductant_material_invariance(self) -> None:
        """
        Warn when a technology's material side differs across reductants.

        The reductant score (annual re-pick and investment NPVs) carries no material
        term: materials and utilisation are assumed reductant-invariant, which holds
        in all current masters. Two kinds of authoring drift would silently break
        that factorisation: a metallic charge present for only a subset of a
        technology's reductants, and required quantities / non-energy secondary
        feedstocks that differ per reductant.

        Returns:
            None. Logs one warning per affected (technology, metallic charge).
        """
        logger = logging.getLogger(f"{__name__}.Environment.validate_reductant_material_invariance")
        checked: set[str] = set()
        for tech_name, feedstocks in self.dynamic_feedstocks.items():
            canonical = tech_name.lower()
            if canonical in checked:
                continue
            checked.add(canonical)
            profiles: dict[str, dict[str, tuple]] = {}
            reductants: set[str] = set()
            for dbc in feedstocks:
                non_energy_secondary = tuple(
                    sorted(
                        (normalize_name(name), volume)
                        for name, volume in (dbc.secondary_feedstock or {}).items()
                        if normalize_name(name) not in ENERGY_FEEDSTOCK_KEYS
                    )
                )
                profiles.setdefault(dbc.metallic_charge, {})[dbc.reductant] = (
                    dbc.required_quantity_per_ton_of_product,
                    non_energy_secondary,
                )
                reductants.add(dbc.reductant)
            for metallic_charge, by_reductant in profiles.items():
                if set(by_reductant) != reductants:
                    logger.warning(
                        "[REDUCTANT DATA] %s: metallic charge '%s' is authored only for reductant(s) %s "
                        "(missing: %s) — the reductant score assumes identical charge coverage across "
                        "reductants. Check the master's Bill of Materials authoring.",
                        tech_name,
                        metallic_charge,
                        ", ".join(sorted(by_reductant)),
                        ", ".join(sorted(reductants - set(by_reductant))),
                    )
                if len(set(by_reductant.values())) > 1:
                    logger.warning(
                        "[REDUCTANT DATA] %s: material requirements for charge '%s' differ across "
                        "reductants (%s) — the reductant score carries no material term, so this "
                        "difference is ignored. Check the master's Bill of Materials authoring.",
                        tech_name,
                        metallic_charge,
                        ", ".join(sorted(by_reductant)),
                    )

    def initiate_fopex(self, fopex_list: list[FOPEX]) -> None:
        """
        Initialize the FOPEX data per country for the environment.
        Args:
            fopex_list (list[FOPEX]): A list of FOPEX objects to be added to the environment.
        """
        # Convert list of FOPEX objects to dictionary format expected by the rest of the code
        self.fopex_by_country = {}
        for fopex in fopex_list:
            # Ensure technology names are lowercase for consistency
            self.fopex_by_country[fopex.iso3] = {tech.lower(): value for tech, value in fopex.technology_fopex.items()}

    def get_fallback_material_cost(self, iso3: str, technology: str) -> float | None:
        """
        Get the fallback material cost for a given ISO3 and technology for the current year.

        Args:
            iso3: Country ISO3 code
            technology: Technology name (e.g., 'BF', 'EAF', etc.)

        Returns:
            The fallback material cost for the current year, or None if not found
        """
        for fallback_cost in self.fallback_material_costs:
            if fallback_cost.iso3.upper() == iso3.upper() and fallback_cost.technology.upper() == technology.upper():
                return fallback_cost.get_cost_for_year(self.year)
        return None

    def get_available_fallback_technologies(self) -> set[str]:
        """
        Get a set of all unique technologies available in fallback material costs.

        Returns:
            Set of unique technology names from fallback material costs
        """
        technologies = set()
        for fallback_cost in self.fallback_material_costs:
            technologies.add(fallback_cost.technology)
        return technologies

    def get_average_fallback_material_cost(self, technology: str, year: Year | None = None) -> float | None:
        """
        Get the average material cost for a given technology across all regions for a specific year.

        Args:
            technology: Technology name (e.g., 'BF', 'EAF', etc.)
            year: The year to get costs for. If None, uses the current environment year.

        Returns:
            The average cost across all regions that have data for this technology and year,
            or None if no data is available.
        """
        if year is None:
            year = self.year

        costs = []
        for fallback_cost in self.fallback_material_costs:
            if fallback_cost.technology.upper() == technology.upper():
                cost = fallback_cost.get_cost_for_year(year)
                if cost is not None:
                    costs.append(cost)

        if costs:
            return sum(costs) / len(costs)
        return None

    def initiate_techno_economic_details(self, capex_list: list[Capex]) -> None:
        """
        Initialize the techno-economic details in the environment:
        - capital expenditure (CAPEX) greenfield and renovation
        - technology to product mapping
        - activation years for technologies
        Args:
            capex_list: A list of Capex objects containing greenfield/renovation capex data and product info.
        """
        if self.country_mappings is None:
            raise ValueError("country_mappings must be set before calling initiate_capex_and_activation_years")
        region_dict = self.country_mappings.iso3_to_region()
        regions = set(region_dict.values())

        # Create all the required mappings
        self.technology_to_product: dict[str, str] = {capex.technology_name: capex.product for capex in capex_list}
        # Update the existing capex_renovation_share dict instead of reassigning
        self.capex_renovation_share: dict[str, float] = {
            capex.technology_name: capex.capex_renovation_share for capex in capex_list
        }

        # initialize empty nested dict
        capex_dict: dict[str, dict[str, dict[str, float]]] = {
            "greenfield": {region: {} for region in regions},
            "default": {region: {} for region in regions},
        }
        for region in regions:
            for capex in capex_list:
                capex_dict["greenfield"][region][capex.technology_name] = capex.greenfield_capex
                capex_dict["default"][region][capex.technology_name] = capex.greenfield_capex

        self.name_to_capex = capex_dict

    def initiate_opex_subsidies(self, subsidies: list[Subsidy]) -> None:
        """
        Initialize the opex subsidies for the environment.

        Args:
            subsidies (list[Subsidy]): A list of Subsidy objects to be added to the environment.
        """
        opex_subsidies: dict[str, dict[str, list[Subsidy]]] = {}
        for subsidy in subsidies:
            if subsidy.cost_item.lower() == "opex":
                if subsidy.geo_key not in opex_subsidies:
                    opex_subsidies[subsidy.geo_key] = {}
                if subsidy.technology_name not in opex_subsidies[subsidy.geo_key]:
                    opex_subsidies[subsidy.geo_key][subsidy.technology_name] = []
                opex_subsidies[subsidy.geo_key][subsidy.technology_name].append(subsidy)
        self.opex_subsidies = opex_subsidies

    def initiate_capex_subsidies(self, subsidies: list[Subsidy]) -> None:
        """
        Initialize the capex subsidies for the environment.

        Args:
            subsidies (list[Subsidy]): A list of Subsidy objects to be added to the environment.
        """
        capex_subsidies: dict[str, dict[str, list[Subsidy]]] = {}
        for subsidy in subsidies:
            if subsidy.cost_item.lower() == "capex":
                if subsidy.geo_key not in capex_subsidies:
                    capex_subsidies[subsidy.geo_key] = {}
                if subsidy.technology_name not in capex_subsidies[subsidy.geo_key]:
                    capex_subsidies[subsidy.geo_key][subsidy.technology_name] = []
                capex_subsidies[subsidy.geo_key][subsidy.technology_name].append(subsidy)
        self.capex_subsidies = capex_subsidies

    def initiate_debt_subsidies(self, subsidies: list[Subsidy]) -> None:
        """
        Initialize the debt subsidies for the environment.

        Args:
            subsidies (list[Subsidy]): A list of Subsidy objects to be added to the environment.
        """
        debt_subsidies: dict[str, dict[str, list[Subsidy]]] = {}
        for subsidy in subsidies:
            if subsidy.cost_item.lower() == "cost of debt":
                if subsidy.geo_key not in debt_subsidies:
                    debt_subsidies[subsidy.geo_key] = {}
                if subsidy.technology_name not in debt_subsidies[subsidy.geo_key]:
                    debt_subsidies[subsidy.geo_key][subsidy.technology_name] = []
                debt_subsidies[subsidy.geo_key][subsidy.technology_name].append(subsidy)
        self.debt_subsidies = debt_subsidies

    def initiate_energy_subsidies(self, subsidies: list[Subsidy]) -> None:
        """
        Initialise energy carrier subsidies for the environment.

        Groups subsidies by carrier name (cost_item), then by geo_key (country or
        ``iso3:province``), then by technology. Financial subsidies (opex, capex, cost of
        debt) are excluded — they are handled by their own initiate methods.

        Args:
            subsidies: List of Subsidy objects to filter and group.
        """
        financial_cost_items = {"opex", "capex", "cost of debt"}
        energy_subsidies: dict[str, dict[str, dict[str, list[Subsidy]]]] = {}
        for subsidy in subsidies:
            carrier = subsidy.cost_item.lower()
            if carrier in financial_cost_items:
                continue
            if carrier not in energy_subsidies:
                energy_subsidies[carrier] = {}
            if subsidy.geo_key not in energy_subsidies[carrier]:
                energy_subsidies[carrier][subsidy.geo_key] = {}
            if subsidy.technology_name not in energy_subsidies[carrier][subsidy.geo_key]:
                energy_subsidies[carrier][subsidy.geo_key][subsidy.technology_name] = []
            energy_subsidies[carrier][subsidy.geo_key][subsidy.technology_name].append(subsidy)
        self.energy_subsidies = energy_subsidies

    def initiate_dynamic_feedstocks(self, feedstocks: list[PrimaryFeedstock]) -> None:
        """
        Initialize the dynamic feedstocks dict, grouped by technology in the environment.

        Args:
            feedstocks (list[PrimaryFeedstock]): A list of PrimaryFeedstock objects to be added to the environment.
        """
        self.dynamic_feedstocks = {}
        for feedstock in feedstocks:
            # Store with both original case and lowercase for compatibility
            tech_name = feedstock.technology
            if tech_name not in self.dynamic_feedstocks:
                self.dynamic_feedstocks[tech_name] = []
            self.dynamic_feedstocks[tech_name].append(feedstock)

            # Also store with lowercase key for compatibility
            tech_name_lower = tech_name.lower()
            if tech_name_lower != tech_name and tech_name_lower not in self.dynamic_feedstocks:
                self.dynamic_feedstocks[tech_name_lower] = []
            if tech_name_lower != tech_name:
                self.dynamic_feedstocks[tech_name_lower].append(feedstock)

        # Collect all priced commodity keys from input_costs (across all countries/years)
        priced_commodities: set[str] = set()
        for yearly_costs in self.input_costs.values():
            for costs in yearly_costs.values():
                priced_commodities.update(costs.keys())

        # Compute canonical cost breakdown keys from all feedstocks
        all_keys: set[str] = set()
        for feedstock in feedstocks:
            for key in feedstock.energy_requirements or {}:
                all_keys.add(normalize_name(key))
            for key in feedstock.secondary_feedstock or {}:
                all_keys.add(normalize_name(key))
            # Include secondary output keys (by-products, not primary products)
            primary_output_keys = set(feedstock.get_primary_outputs().keys())
            for key in feedstock.outputs or {}:
                normalized = normalize_name(key)
                if normalized not in primary_output_keys:
                    all_keys.add(normalized)
            # Only carbon outputs with a price belong in cost_breakdown (e.g. co2_stored);
            # costless vectors (co2_slip, co2_utilised) are tracked in carbon_breakdown instead
            for key in feedstock.carbon_outputs or {}:
                normalized = normalize_name(key)
                if normalized in priced_commodities:
                    all_keys.add(normalized)
        self.cost_breakdown_keys: list[str] = sorted(all_keys)

        # Compute canonical carbon breakdown keys (physical tCO2/t-product for all CO2 vectors)
        ci_keys: set[str] = set()
        co_keys: set[str] = set()
        for feedstock in feedstocks:
            for key in feedstock.carbon_inputs or {}:
                ci_keys.add(normalize_name(key))
            for key in feedstock.carbon_outputs or {}:
                co_keys.add(normalize_name(key))
        self.carbon_input_keys: list[str] = sorted(ci_keys)
        self.carbon_output_keys: list[str] = sorted(co_keys)
        self.carbon_breakdown_keys: list[str] = sorted(ci_keys | co_keys)

    def initiate_aggregated_metallic_charge_constraints(
        self, constraints: list[AggregatedMetallicChargeConstraint]
    ) -> None:
        """
        Initialize the aggregated metallic charge constraints for the environment.

        Args:
            constraints (list[AggregatedMetallicChargeConstraint]): A list of AggregatedMetallicChargeConstraint objects to be added to the environment.
        """
        self.aggregated_metallic_charge_constraints = constraints

    def initiate_secondary_feedstock_constraints(self, constraints: list[SecondaryFeedstockConstraint]) -> None:
        """
        Initialize the secondary feedstock constraints for the environment.

        Args:
            constraints (list[SecondaryFeedstockConstraint]): A list of SecondaryFeedstockConstraint objects to be added to the environment.
        """
        self.secondary_feedstock_constraints = constraints

    def initiate_carbon_costs(self, carbon_costs: list[CarbonCostSeries]) -> None:
        """
        Initialize the carbon costs for the environment.

        Args:
            carbon_costs (list[CarbonCostSeries]): A list of CarbonCostSeries objects to be added to the environment.
        """
        self.carbon_costs = {cc.iso3: cc.carbon_cost for cc in carbon_costs}

    def initiate_grid_emissivity(self, emissivities: list[RegionEmissivity]) -> None:
        """
        Initialize the grid emissivities for the environment, based on passed scenario

        Args:
            emissivities (list[RegionEmissivity]): A list of RegionEmissivity objects to be added to the environment.

        Side Effects:
            Updates the `grid_emissivities` dictionary to map geo_keys (bare ISO3 or
            sub-national ``iso3:code``) to their respective emissivities.

        Raises:
            ValueError: If emissivities are supplied but none match the chosen scenario
                (a scenario-name typo would otherwise zero all grid emissions silently).
        """
        self.grid_emissivities = {
            ge.geo_key: ge.grid_emissivity
            for ge in emissivities
            if ge.scenario == self.config.chosen_grid_emissions_scenario
        }
        if emissivities and not self.grid_emissivities:
            raise ValueError(
                f"No grid emissivities match scenario {self.config.chosen_grid_emissions_scenario!r}; "
                f"available: {sorted({ge.scenario for ge in emissivities})}"
            )

    def propagate_grid_emissivity_to_furnace_groups(self, plants: list[Plant]) -> None:
        """
        Propagate the grid emissivities to all plants and furnace groups, resolving each
        plant's location finest-available first (sub-national geo_key, then country ISO3).

        Args:
            plants (list[Plant]): List of Plant objects to update with grid emissivities.

        Side Effects:
            Updates the `grid_emissivity` attribute of each FurnaceGroup object for the current year.

        Raises:
            ValueError: If a plant's location resolves to no grid emissivity entry, or the
                entry has no Electricity value for the current year.
        """
        logger = logging.getLogger(f"{__name__}.Environment.propagate_grid_emissivity_to_furnace_groups")
        # If grid_emissivities hasn't been initialized or no data was supplied, skip propagation
        if not getattr(self, "grid_emissivities", None):
            logger.warning("Grid emissivities not initialized, skipping propagation to furnace groups")
            return

        for plant in plants:
            emissivity_dict = plant.location.resolve(self.grid_emissivities, what="grid emissivity")
            if emissivity_dict is None:
                raise ValueError(f"Grid emissivity not found for {plant.location.geo_key}")
            year_data = emissivity_dict.get(self.year)
            if year_data is None or "Electricity" not in year_data:
                raise ValueError(f"Grid emissivity not found for {plant.location.geo_key} in year {self.year}")
            emissivity_value = year_data["Electricity"]
            for furnace_group in plant.furnace_groups:
                furnace_group.grid_emissivity = emissivity_value

    def initiate_gas_coke_emissivity(self, emissivities: list[RegionEmissivity]) -> None:
        """
        Initialize the fossil fuel emissivities for the environment, based on passed scenario

        Args:
            emissivities (list[RegionEmissivity]): A list of RegionEmissivity objects to be added to the environment.

        Side Effects:
            Updates the `fossil_emissivity` dictionary to map geo_keys (bare ISO3 or
            sub-national ``iso3:code``) to their respective emissivities.
        """
        # TODO FOR BACKLOG: fossil_emissivity is never consumed — scaffolding for a future
        # coke/gas emissions feature; see the data bugs noted in read_regional_emissivities.
        self.fossil_emissivity = {
            ge.geo_key: {"Coke": ge.coke_emissivity, "Natural gas": ge.gas_emissivity}
            for ge in emissivities
            if ge.scenario == self.config.chosen_grid_emissions_scenario
        }

    def initiate_input_costs(self, input_costs_list: list[InputCosts]) -> None:
        """
        Initialize the input costs for the environment.

        Args:
            input_costs (list[InputCosts]): A list of InputCosts objects to be added to the environment.
        """
        for ic in input_costs_list:
            if ic.geo_key not in self.input_costs:
                self.input_costs[ic.geo_key] = {}
            self.input_costs[ic.geo_key][ic.year] = ic.costs

    def set_capex_and_debt_in_furnace_groups(self, world_plants_list: list[Plant]) -> None:
        """
        Set capital expenditure (CAPEX) and cost of debt for all furnace groups in plants.

        Applies region-specific CAPEX values and country-specific cost of debt to each
        furnace group. For brownfield technologies, applies a renovation share discount
        to the CAPEX.

        Apply subsidies if available for CAPEX and cost of debt.

        Args:
            world_plants_list: List of Plant objects to update with financial parameters.

        Raises:
            ValueError: If country_mappings is not set.
            ValueError: If region cannot be determined for a plant's ISO3 code.
            ValueError: If industrial cost of debt is not available for a country.
            ValueError: If CAPEX data is not available for a region or technology.

        Side Effects:
            Modifies furnace groups in-place by setting their CAPEX and cost of debt values.
        """
        from steelo.domain.calculate_costs import calculate_debt_with_subsidies

        if self.country_mappings is None:
            raise ValueError("country_mappings must be set")

        for plant in world_plants_list:
            # get plant location iso3 and region
            plant_iso3 = plant.location.iso3
            plant_region = self.country_mappings.iso3_to_region().get(plant_iso3)
            if plant_region is None:
                raise ValueError(f"Region cannot be set for ISO3 code {plant_iso3}, skipping plant {plant.plant_id}")

            for furnace_group in plant.furnace_groups:
                # Set the cost of debt in the furnace groups
                cost_of_debt = self.cost_of_debt_by_tech.get(plant_iso3, {}).get(furnace_group.technology.name)
                if cost_of_debt is None:
                    raise ValueError(f"Cost of debt cannot be set for {plant_iso3}/{furnace_group.technology.name}")

                cost_of_debt_no_subsidy = cost_of_debt  # Store original cost of debt before subsidy
                cost_of_debt = calculate_debt_with_subsidies(
                    cost_of_debt=cost_of_debt,
                    debt_subsidies=furnace_group.applied_subsidies["debt"],
                    risk_free_rate=self.config.global_risk_free_rate,
                )

                furnace_group.set_cost_of_debt(cost_of_debt, cost_of_debt_no_subsidy)

                if furnace_group.technology.name in self.technology_to_product.keys():
                    # Set the capex in the furnace groups
                    capex_region = self.name_to_capex["greenfield"].get(plant_region)
                    if capex_region is None:
                        raise ValueError(f"Capex cannot be set for region {plant_region}")
                    capex = capex_region.get(furnace_group.technology.name)
                    if capex is None:
                        raise ValueError(
                            f"Capex cannot be set for technology {furnace_group.technology.name} in region {plant_region}"
                        )
                    if furnace_group.technology.capex_type == "brownfield":
                        capex *= self.capex_renovation_share[furnace_group.technology.name]  # Apply renovation share

                    # apply any capex subsidies
                    capex_no_subsidy = capex  # Store original capex before subsidy
                    capex = calculate_capex_with_subsidies(capex, furnace_group.applied_subsidies["capex"])

                    furnace_group.set_technology_capex(capex, capex_no_subsidy)

    def update_furnace_capex_renovation_share(self, world_plants_list: list[Plant]) -> None:
        """
        Update the capex renovation share for all furnace groups in the plant based on their technology.
        """
        for plant in world_plants_list:
            for furnace_group in plant.furnace_groups:
                if furnace_group.technology.name in self.technology_to_product.keys():
                    renovation_share = self.capex_renovation_share.get(furnace_group.technology.name)
                    if renovation_share is None:
                        raise ValueError(
                            f"Capex renovation share not found for technology {furnace_group.technology.name}"
                        )
                    furnace_group.capex_renovation_share = renovation_share

    def add_capacity(self, technology_name: str, capacity: Volumes) -> None:
        """
        This function tracks the capacity added to the system in a given year. This will be counted for any action that adds or actively maintains capacity and thus requires "material

        Args
            technolgoy_name (str):
                The name of the technology for which capacity is being added.
            capacity (Volumes):
                The amount of capacity being added, represented as a Volumes object.
        Returns
            None

        Side Effects
            Updates the `added_capacity` dictionary to track the total capacity added for each technology.
        """
        # if not self.added_capacity.get(self.year, {}):
        #     self.added_capacity[self.year] = {technology_name: capacity}
        if technology_name not in self.added_capacity:
            self.added_capacity[technology_name] = Volumes(0)
        self.added_capacity[technology_name] += capacity

    def add_switched_capacity(self, technology_name: str, capacity: Volumes) -> None:
        """
        Track capacity that is switched to a different technology in a given year.

        Args:
            technology_name (str): The name of the NEW technology being switched TO.
            capacity (Volumes): The amount of capacity being switched.

        Side Effects:
            Updates the `switched_capacity` dictionary to track the total capacity switched for each technology.
        """
        if technology_name not in self.switched_capacity:
            self.switched_capacity[technology_name] = Volumes(0)
        self.switched_capacity[technology_name] += capacity

    def add_new_plant_capacity(self, technology_name: str, capacity: Volumes) -> None:
        """
        Track capacity from newly built plants by indi (separate from expansions to existing plants).

        Args:
            technology_name (str): The name of the technology for the new plant.
            capacity (Volumes): The amount of capacity being added by the new plant.

        Side Effects:
            Updates the `new_plant_capacity` dictionary to track the total capacity from new plants for each technology.
        """
        if technology_name not in self.new_plant_capacity:
            self.new_plant_capacity[technology_name] = Volumes(0)
        self.new_plant_capacity[technology_name] += capacity

    def new_plant_capacity_in_year(self, product: str) -> Volumes:
        """
        Return the total capacity from new plants (indi) in the current year for technologies that produce the specified product.
        This tracks only NEW PLANTS, not expansions to existing plants.

        Args:
            product: The product type to filter by ("steel" or "iron")

        Returns:
            Volumes: The total capacity from new plants in the current year for technologies producing the specified product.
        """
        new_plant_volumes = []
        for tech_name, capacity in self.new_plant_capacity.items():
            # Only include technologies that produce the specified product according to technology_to_product
            if tech_name in self.technology_to_product:
                tech_product = self.technology_to_product[tech_name]
                if product == tech_product:
                    new_plant_volumes.append(Volumes(capacity))
            else:
                raise ValueError(f"Technology {tech_name} not found in technology to product map.")

        return sum(new_plant_volumes, Volumes(0))

    def installed_capacity_in_year(self, product: str) -> Volumes:
        """
        Return the total installed capacity in the current year for technologies that produce the specified product.
        This includes both newly added capacity AND switched capacity.

        Args:
            product: The product type to filter by ("steel" or "iron")

        Returns:
            Volumes: The total installed capacity (added + switched) in the current year for technologies producing the specified product.
        """
        # Calculate newly added capacity (both expansions and new builds)
        added_volumes = []
        for tech_name, capacity in self.added_capacity.items():
            # Only include technologies that produce the specified product according to technology_to_product
            if tech_name in self.technology_to_product:
                tech_product = self.technology_to_product[tech_name]
                if product == tech_product:
                    added_volumes.append(Volumes(capacity))
            else:
                raise ValueError(f"Technology {tech_name} not found in technology to product map.")

        # Calculate switched capacity (technology switches)
        switched_volumes = []
        for tech_name, capacity in self.switched_capacity.items():
            # Only include technologies that produce the specified product according to technology_to_product
            if tech_name in self.technology_to_product:
                tech_product = self.technology_to_product[tech_name]
                if product == tech_product:
                    switched_volumes.append(Volumes(capacity))
            else:
                raise ValueError(f"Technology {tech_name} not found in technology to product map.")

        # Return the sum of both added and switched capacity
        total_added = sum(added_volumes, Volumes(0))
        total_switched = sum(switched_volumes, Volumes(0))
        return Volumes(total_added + total_switched)

    def initiate_cost_of_capital(self, cost_of_capital_list: list[CostOfCapital]) -> None:
        """
        Initialise the per-country, per-technology financing-rate dicts.

        Args:
            cost_of_capital_list (list[CostOfCapital]): One entry per country with per-tech rates.
        Returns:
            None
        Side Effects:
            Populates `cost_of_debt_by_tech` and `cost_of_equity_by_tech` (iso3 -> tech -> rate).
        """
        self.cost_of_debt_by_tech = {
            c.iso3: {tech: rates.cost_of_debt for tech, rates in c.techs.items()} for c in cost_of_capital_list
        }
        self.cost_of_equity_by_tech = {
            c.iso3: {tech: rates.cost_of_equity for tech, rates in c.techs.items()} for c in cost_of_capital_list
        }
        tech_counts = {len(techs) for techs in self.cost_of_debt_by_tech.values()}
        logging.getLogger(f"{__name__}.initiate_cost_of_capital").info(
            f"Initialised cost of capital for {len(self.cost_of_debt_by_tech)} countries "
            f"with {sorted(tech_counts)} techs each"
        )

    def set_input_cost_in_furnace_groups(self, world_plants: list[Plant]) -> None:
        """
        Set or update feedstock and energy costs in all furnace groups based on plant location and type.

        This method is called twice in the simulation lifecycle:
        1. **At Bootstrap** (before simulation years start): Initializes all furnace groups with input costs
           from the Excel "Input costs" tab. At this point, furnace groups have empty energy_costs dictionaries.
        2. **At Each Year Start** (during simulation loop): Updates input costs for the current year, while
           preserving previously calculated hydrogen costs that account for interregional trade.

        Process:
            1. Retrieve country-specific input costs (electricity, hydrogen, coke, pci, natural_gas, coal, bio_pci)
               for each plant's location
            2. Handle special cases based on plant type:

               **New GEO Plants (parent_gem_id starts with "indi"):**
               - These plants have their own power infrastructure (renewable energy park)
               - Preserve electricity and hydrogen costs from their own power generation if previously set

               **Existing Plants (all others):**
               - Use grid electricity costs from Excel input costs
               - At bootstrap: Use hydrogen from Excel for initialization (temporary, will be recalculated before the simulation starts)
               - During simulation: Use capped hydrogen prices that account for regional ceilings and interregional trade

        Hydrogen Cost Flow:
            - Bootstrap: Uses hydrogen from Excel input costs
            - Year Start: calculate_capped_hydrogen_costs_per_country() runs, then update_furnace_hydrogen_costs()
              sets fg.energy_costs["hydrogen"] with capped prices
            - This method: Preserves those capped prices instead of overwriting with Excel's static values

        Args:
            world_plants (list[Plant]): All plants in the simulation to update.

        Side Effects:
            - Updates fg.energy_costs dictionary via set_energy_costs() for each furnace group

        Notes:
            - Plants with missing ISO3 codes in input_costs (e.g., Kosovo/XKX) are skipped with a warning
            - Hydrogen costs for existing plants must be calculated globally (all countries at once) to enable
              proper modeling of regional hydrogen price ceilings and interregional hydrogen trade
        """
        logger = logging.getLogger(f"{__name__}.Environment.set_input_cost_in_furnace_groups")
        logged_countries: set[str] = set()
        for plant in world_plants:
            # Resolve finest-available: the plant's sub-national geo_key if authored, else its country iso3
            year_costs = plant.location.resolve(self.input_costs, what="input costs")
            if year_costs is None:
                logger.warning(
                    f"No input_costs found for {plant.location.geo_key} for plant {plant.plant_id}, skipping energy cost update"
                )
                continue

            # Get input costs from Excel for this geo_key and year
            input_costs = year_costs[self.year].copy()

            # Log once per country to avoid flooding
            if plant.location.iso3 not in logged_countries:
                logger.debug(
                    "[INPUT COSTS] %s (year=%d): %s",
                    plant.location.iso3,
                    self.year,
                    {k: f"${v:.6f}" for k, v in input_costs.items()},
                )
                logged_countries.add(plant.location.iso3)

            # New GEO plants: preserve own power costs; use no_subsidy to avoid compounding
            if plant.parent_gem_id.lower().startswith("indi"):
                for fg in plant.furnace_groups:
                    if fg.disposal_cost_outputs is None:
                        fg.disposal_cost_outputs = self.config.disposal_cost_outputs
                    no_sub = fg.energy_costs_no_subsidy or fg.energy_costs
                    if "electricity" in no_sub:
                        input_costs["electricity"] = no_sub["electricity"]
                    if "hydrogen" in no_sub:
                        input_costs["hydrogen"] = no_sub["hydrogen"]
                    if fg.energy_costs_no_subsidy and "electricity" in no_sub:
                        sub_elec = fg.energy_costs.get("electricity", 0)
                        if sub_elec != no_sub["electricity"]:
                            logger.debug(
                                "[INPUT COSTS] indi %s FG:%s elec no_sub=$%.6f (was sub=$%.6f)",
                                plant.location.iso3,
                                fg.furnace_group_id,
                                no_sub["electricity"],
                                sub_elec,
                            )
                    fg.set_energy_costs(**input_costs)

            # Existing plants: Preserve calculated hydrogen costs if set (only not at bootstrap)
            else:
                for fg in plant.furnace_groups:
                    if fg.disposal_cost_outputs is None:
                        fg.disposal_cost_outputs = self.config.disposal_cost_outputs
                    if "hydrogen" in fg.energy_costs:
                        input_costs["hydrogen"] = fg.energy_costs["hydrogen"]
                    fg.set_energy_costs(**input_costs)

    def calculate_capped_hydrogen_costs_per_country(self, year: Year | None = None) -> dict[str, float]:
        """
        Calculate country-level hydrogen prices with regional ceilings and trade adjustments.

        Steps:
            1. Extract electricity prices for all countries in the given year and calculate Levelized Cost of Hydrogen
            (LCOH) for each country using electricity prices, CAPEX, and OPEX.
            2. Determine regional hydrogen price ceilings based on percentile thresholds
            3. Apply price caps considering regional ceilings, interregional trade options, and pipeline transport costs. Pipeline
            transport costs are added when importing hydrogen from trading partners.

        Args:
            year: Year to price; defaults to the current simulation year. Every input is
                exogenous, so any covered year can be computed at any time.

        Returns:
            capped_hydrogen_prices (dict[str, float]): Dictionary mapping ISO3 country codes to capped hydrogen prices in USD/kg.

        Side Effects:
            Logs info messages about calculation progress.

        Notes:
            - Requires self.config with geo_config settings for hydrogen ceiling percentile and trade parameters
            - Requires self.country_mappings for regional groupings
            - Requires self.hydrogen_efficiency and self.hydrogen_capex_opex data for LCOH calculation
        """
        logger = logging.getLogger(f"{__name__}.Environment.calculate_capped_hydrogen_costs_per_country")
        from steelo.domain.calculate_costs import (
            calculate_lcoh_from_electricity_country_level,
            calculate_regional_hydrogen_ceiling_country_level,
            apply_hydrogen_price_cap_country_level,
        )

        logger.info("Calculating capped hydrogen prices for all countries")

        # Validate prerequisites
        if self.config is None:
            raise ValueError("SimulationConfig is required for hydrogen price calculation")
        if self.country_mappings is None:
            raise ValueError("Country mappings are required for hydrogen price calculation")
        if not hasattr(self.config, "geo_config"):
            raise ValueError("GeoConfig is required for hydrogen price calculation")
        geo_config = self.config.geo_config
        target_year = self.year if year is None else year

        # Step 1: LCOH per geo_key (country rows + any authored sub-national rows)
        electricity_by_geo_key = {}
        for geo_key, year_costs in self.input_costs.items():
            if geo_key is None:
                continue
            if target_year in year_costs:
                if "electricity" not in year_costs[target_year]:
                    raise ValueError(f"Electricity price not found for {geo_key} in year {target_year}")
                electricity_by_geo_key[geo_key] = year_costs[target_year]["electricity"]
        lcoh_by_geo_key = calculate_lcoh_from_electricity_country_level(
            electricity_by_country=electricity_by_geo_key,
            hydrogen_efficiency=self.hydrogen_efficiency,
            hydrogen_capex_opex=self.hydrogen_capex_opex,
            year=target_year,
        )

        # Step 2: ceilings from country-level LCOH only; a province inherits its country's ceiling
        lcoh_by_country = {k: v for k, v in lcoh_by_geo_key.items() if ":" not in k}
        regional_ceilings, country_to_region = calculate_regional_hydrogen_ceiling_country_level(
            lcoh_by_country=lcoh_by_country,
            country_mappings=self.country_mappings,
            hydrogen_ceiling_percentile=geo_config.hydrogen_ceiling_percentile,
        )

        # Step 3: Apply hydrogen price caps
        # Filter out None values from intraregional_trade_matrix
        intraregional_trade_matrix_clean = {
            k: v for k, v in geo_config.intraregional_trade_matrix.items() if v is not None
        }
        capped_hydrogen_prices = apply_hydrogen_price_cap_country_level(
            lcoh_by_country=lcoh_by_geo_key,
            regional_ceilings=regional_ceilings,
            country_to_region=country_to_region,
            intraregional_trade_allowed=geo_config.intraregional_trade_allowed,
            intraregional_trade_matrix=intraregional_trade_matrix_clean,
            long_dist_pipeline_transport_cost=geo_config.long_dist_pipeline_transport_cost,
        )

        logger.info(f"Calculated capped hydrogen prices for {len(capped_hydrogen_prices)} countries")
        return capped_hydrogen_prices

    def initiate_capped_hydrogen_costs_by_year(self) -> None:
        """
        Precompute the capped-LCOH series for the full data horizon.

        Every input to the capped hydrogen price is exogenous (Excel trajectories,
        static geo config), so the whole series is knowable at bootstrap. Covers
        config.start_year through the last year present in input_costs.

        Returns:
            None. Fills self.capped_hydrogen_costs_by_year.

        Notes:
            Requires input_costs, hydrogen_efficiency, hydrogen_capex_opex and
            country_mappings to be initiated first; raises if input costs are empty.
        """
        if self.config is None:
            raise ValueError("SimulationConfig is required to precompute hydrogen prices")
        data_years = {y for year_costs in self.input_costs.values() for y in year_costs}
        if not data_years:
            raise ValueError("Input costs must be initiated before precomputing hydrogen prices")
        self.capped_hydrogen_costs_by_year = {
            Year(y): self.calculate_capped_hydrogen_costs_per_country(year=Year(y))
            for y in range(int(self.config.start_year), int(max(data_years)) + 1)
        }

    def capped_hydrogen_costs_for_year(self, year: Year) -> dict[str, float]:
        """
        Look up the precomputed capped LCOH dict (geo_key -> USD/kg) for a year.

        Args:
            year: Any simulation or forecast year.

        Returns:
            The precomputed dict for that year; beyond the data horizon the last
            covered year's prices apply (trajectories end in 2050, NPVs look past it).

        Notes:
            Raises if the series was not precomputed; years inside the covered range
            but absent from it raise KeyError rather than silently degrading.
        """
        if not self.capped_hydrogen_costs_by_year:
            raise ValueError("Capped hydrogen costs not precomputed; call initiate_capped_hydrogen_costs_by_year first")
        last_year = max(self.capped_hydrogen_costs_by_year)
        if year > last_year:
            return self.capped_hydrogen_costs_by_year[last_year]
        return self.capped_hydrogen_costs_by_year[year]

    def carbon_price_for_year(self, iso3: str, year: Year) -> float:
        """
        Country carbon price for a year, clamped to the last covered year.

        Args:
            iso3: Country code.
            year: Any simulation or forecast year.

        Returns:
            The exogenous carbon price; 0.0 for countries without a carbon-price
            series (no policy). Years beyond the series reuse its last value; years
            missing inside the covered range raise KeyError rather than pricing
            carbon as zero.
        """
        series = self.carbon_costs.get(iso3)
        if not series:
            return 0.0
        last_year = max(series)
        return series[min(year, last_year)]

    def _unsubsidised_energy_costs_for_year(self, location: "Location", year: Year) -> dict[str, float]:
        """
        Exogenous per-carrier prices at a location for a year, before any subsidy.

        Args:
            location: Plant/site location (geo_key resolution, finest available).
            year: The year to price; clamped to the input-cost data horizon.

        Returns:
            Normalised carrier -> price (sign handling as in set_energy_costs),
            hydrogen from the precomputed capped-LCOH series.
        """
        year_costs = location.resolve(self.input_costs, what="input costs")
        if year_costs is None:
            raise ValueError(f"No input costs found for {location.geo_key}")
        clamped_year = min(year, max(year_costs))
        base: dict[str, float] = {}
        for raw_key, price in year_costs[clamped_year].items():
            normalized_key = normalize_name(raw_key)
            base[normalized_key] = price if normalized_key.startswith("co2") else abs(price)
        capped_lcoh = location.resolve(self.capped_hydrogen_costs_for_year(year), what="hydrogen price")
        if capped_lcoh is None:
            raise ValueError(f"No hydrogen price for {location.geo_key}")
        base["hydrogen"] = capped_lcoh * T_TO_KG
        return base

    def candidate_energy_costs_for_year(
        self,
        location: "Location",
        technology_name: str,
        year: Year,
        overrides: dict[str, float] | None = None,
        override_reference_year: Year | None = None,
    ) -> tuple[dict[str, float], dict[str, float]]:
        """
        Candidate energy prices (input and output side) at a location for any year.

        Mirrors the year-start price refresh generalised to a future year: the
        exogenous input-cost trajectory at ``year`` (clamped to its last data year,
        sign handling as in set_energy_costs), hydrogen from the precomputed
        capped-LCOH series, and energy-carrier subsidies scoped to the candidate
        technology filtered for ``year`` (the A2 treatment, per year).

        Args:
            location: Plant/site location (geo_key resolution, finest available).
            technology_name: Candidate technology whose energy subsidies apply.
            year: The year to price.
            overrides: Site-specific current prices per carrier (e.g. a GEO site's
                own power price). Each override replaces the country level but keeps
                the country trajectory: price_t = override x country_t / country_ref,
                both sides unsubsidised; year-t subsidies apply after scaling.
            override_reference_year: Year the override prices belong to; required
                when overrides are given.

        Returns:
            (input_costs, output_costs) per normalised carrier for that year.

        Notes:
            Years missing inside the input-cost trajectory raise (no silent gaps).
        """
        base = self._unsubsidised_energy_costs_for_year(location, year)

        if overrides:
            if override_reference_year is None:
                raise ValueError("override_reference_year is required when overrides are given")
            reference = self._unsubsidised_energy_costs_for_year(location, override_reference_year)
            for carrier, site_price in overrides.items():
                normalized_carrier = normalize_name(carrier)
                reference_price = reference[normalized_carrier]
                if reference_price == 0.0:
                    raise ValueError(
                        f"Cannot trajectory-scale '{normalized_carrier}' for {location.geo_key}: "
                        f"reference price in {override_reference_year} is zero"
                    )
                base[normalized_carrier] = site_price * base[normalized_carrier] / reference_price

        active_subsidies: dict[str, list[Subsidy]] = {}
        for carrier, subsidies_by_geo in self.energy_subsidies.items():
            subsidies = collect_subsidies_for_geo(subsidies_by_geo, location.geo_key).get(technology_name, [])
            active = filter_subsidies_for_year(subsidies, year)
            if active:
                active_subsidies[carrier] = active
        if not active_subsidies:
            return base, base.copy()
        input_costs, output_costs, _ = get_subsidised_energy_costs(base, active_subsidies)
        return input_costs, output_costs

    def reductant_score_series(
        self,
        location: "Location",
        technology_name: str,
        output_shares: dict[str, float],
        start_year: Year,
        end_year: Year,
        overrides: dict[str, float] | None = None,
        override_reference_year: Year | None = None,
    ) -> "ReductantScoreSeries":
        """
        Year-wise reductant-optimised score series for a candidate technology.

        For each year in [start_year, end_year) the per-(charge, reductant) score
        (energy VOPEX + carbon x direct GHG + secondary-output adjustment) is
        evaluated at that year's exogenous prices; the reductant is picked with the
        same rule the annual re-pick uses (per-charge argmin, mode across charges),
        and the year's value is the output-share-weighted score of that pick. The
        NPV built on this series therefore forecasts exactly what the committed
        asset will do under the annual re-pick.

        Args:
            location: Plant/site location.
            technology_name: Candidate technology.
            output_shares: Metallic charge -> share of product (from the candidate
                BOM build; reductant-invariant).
            start_year: First operating year (inclusive).
            end_year: Last operating year (exclusive).
            overrides / override_reference_year: Site-price overrides, see
                candidate_energy_costs_for_year.

        Returns:
            ReductantScoreSeries with one score and one pick per operating year.

        Notes:
            Missing EF rows raise (hard lookup); bootstrap validation guarantees
            coverage for real data.
        """
        dynamic_business_case = self.dynamic_feedstocks.get(technology_name) or self.dynamic_feedstocks.get(
            technology_name.lower()
        )
        if not dynamic_business_case:
            raise ValueError(f"No dynamic business case for technology '{technology_name}'")
        if self.config is None:
            raise ValueError("SimulationConfig is required for reductant score series")
        ef_by_key = build_direct_ghg_lookup(
            self.technology_emission_factors,
            self.config.chosen_emissions_boundary_for_carbon_costs,
        )
        scores: list[float] = []
        picks: list[str] = []
        for year in range(int(start_year), int(end_year)):
            input_costs, output_costs = self.candidate_energy_costs_for_year(
                location,
                technology_name,
                Year(year),
                overrides=overrides,
                override_reference_year=override_reference_year,
            )
            carbon_price = self.carbon_price_for_year(location.iso3, Year(year))
            year_scores = score_reductants_for_business_cases(
                dynamic_business_case,
                input_costs,
                output_costs,
                carbon_price,
                ef_by_key,
                self.config.disposal_cost_outputs,
            )
            mins = [min(costs, key=lambda k: costs[k]) for costs in year_scores.score_by_input.values() if costs]
            counts = Counter(mins)
            if not counts:
                scores.append(0.0)
                picks.append("")
                continue
            pick, _ = counts.most_common(1)[0]
            value = sum(
                share * year_scores.score_by_input[charge][pick]
                for charge, share in output_shares.items()
                if pick in year_scores.score_by_input.get(charge, {})
            )
            scores.append(value)
            picks.append(str(pick))
        return ReductantScoreSeries(scores=scores, picks=picks)

    def get_eu_countries(self) -> list[str]:
        logger = logging.getLogger(f"{__name__}.Environment.get_eu_countries")
        if self.country_mappings is None:
            logger.warning("Country mapping not set. Returning empty list.")
            return []
        if self.country_mappings.code_to_eu_region_map is None:
            logger.warning("Country mapping not set or does not contain EU countries. Returning empty list.")
            return []
        eu_countries = [
            country
            for country in self.country_mappings.code_to_eu_region_map
            if self.country_mappings.code_to_eu_region_map[country] == "EU"
        ]
        return eu_countries

    def set_primary_feedstocks_in_furnace_groups(self, world_plants: list[Plant]):
        """
        Set the effective primary feedstocks available to a furnacegroup and update
        environment-level most common reductant tracking.

        This method:
        1. Updates each furnace group's dynamic business case and chosen reductant
        2. Calculates and stores the most common reductant per technology across all plants

        Side Effects:
            - Updates fg.chosen_reductant for each furnace group via generate_energy_vopex_by_reductant()
              (closed and discarded furnace groups keep their previous chosen_reductant)
            - Updates self.most_common_reductant_by_tech with aggregated reductant data
        """

        for p in world_plants:
            carbon_price = self.carbon_costs.get(p.location.iso3, {}).get(self.year, 0.0)
            for fg in p.furnace_groups:
                fg.technology.dynamic_business_case = self.dynamic_feedstocks.get(
                    fg.technology.name, self.dynamic_feedstocks.get(fg.technology.name.lower(), [])
                )
                # print(fg.technology.dynamic_business_case[-1].emissions)
                fg.technology.set_product(self.technology_to_product)
                # Closed/discarded FGs keep their frozen reductant choice; the dynamic
                # business case and breakdown keys are still attached for other readers.
                if fg.status.lower() not in ("closed", "discarded"):
                    fg.generate_energy_vopex_by_reductant(
                        carbon_price=carbon_price,
                        technology_emission_factors=self.technology_emission_factors,
                        chosen_emissions_boundary=self.config.chosen_emissions_boundary_for_carbon_costs,
                    )
                fg.cost_breakdown_keys = self.cost_breakdown_keys
                fg.carbon_breakdown_keys = self.carbon_breakdown_keys

        # Update environment-level most common reductant tracking after all furnace groups are updated
        self.most_common_reductant_by_tech = self.most_common_reductant(world_plants)
        logger = logging.getLogger(f"{__name__}.Environment.set_primary_feedstocks_in_furnace_groups")
        logger.debug(
            "[REDUCTANT FLEET] year=%s most_common_reductant_by_tech=%s",
            self.year,
            self.most_common_reductant_by_tech,
        )

    def _generate_cost_dict(
        self,
        world_furnace_groups: list[FurnaceGroup],
        lag: int = 0,
        environment_most_common_reductant: dict[str, str] = {},
    ) -> dict[str, dict[str, dict[str, float]]]:
        """
        Generate a dict representation of the costs for all furnace_groups
        """
        logger = logging.getLogger(f"{__name__}.Environment._generate_cost_dict")
        from .calculate_costs import calculate_variable_opex

        assets: dict[str, dict[str, dict[str, float]]] = {"steel": {}, "iron": {}}
        future_assets: dict[str, dict[str, dict[str, float]]] = {"steel": {}, "iron": {}}
        if lag == 0:
            for fg in world_furnace_groups:
                if (
                    fg.technology.product not in assets
                    or fg.status.lower() not in self.config.active_statuses
                    or fg.capacity == 0
                    or fg.production <= MINIMUM_PRODUCTION_VOLUME_FOR_COST_CURVE
                    or fg.utilization_rate <= MINIMUM_UTILIZATION_RATE_FOR_COST_CURVE
                ):
                    continue

                unit_cost = fg.unit_production_cost
                assets[fg.technology.product.lower()][fg.furnace_group_id] = {
                    "capacity": self.config.capacity_limit * fg.capacity,
                    "unit_cost_of_production": unit_cost,
                }
            # Sort by unit cost of production
            self.cost_dict = assets
            return assets
        elif lag > 0:
            for fg in world_furnace_groups:
                if fg.technology.product not in assets or fg.utilization_rate <= 0 or fg.capacity == 0:
                    continue
                if fg.lifetime.start <= self.year + lag:
                    if not fg.bill_of_materials:
                        bom, util_rate, reductant, _output_shares = self.get_bom_from_avg_boms(
                            fg.energy_costs,
                            tech=fg.technology.name,
                            capacity=1000,
                            most_common_reductant=self.most_common_reductant_by_tech.get(
                                fg.technology.name, environment_most_common_reductant.get(fg.technology.name)
                            ),
                        )
                        if bom is not None:
                            unit_cost = calculate_variable_opex(bom["materials"], bom["energy"])
                        else:
                            unit_cost = 0  # fallback if no BOM available
                    else:
                        unit_cost = fg.unit_vopex

                    if not unit_cost:
                        logger.warning(f"Unit cost not found for {fg.technology.name} with id {fg.furnace_group_id}")
                        break
                    future_assets[fg.technology.product.lower()][fg.furnace_group_id] = {
                        "capacity": self.config.capacity_limit * fg.capacity,
                        "unit_cost_of_production": unit_cost,
                    }
            self.future_cost_dict = future_assets
            return future_assets
        else:
            # Default case for invalid lag values
            return assets

    def generate_cost_curve(
        self, world_furnace_groups: list[FurnaceGroup], lag: int
    ) -> dict[str, list[dict[str, float]]]:
        """
        Generate a cost curve based on the unit cost of production for furnace groups.

        1) Generates and sorts the internal cost dictionary by the unit cost of production,
        2) Computes a cumulative production capacity and associates it with the corresponding production cost.


        Returns:
            list[dict]: A list of dictionaries representing the cost curve. Each dictionary has keys:
                - "cumulative_capacity" (float): The cumulative production capacity.
                - "production_cost" (float): The unit cost of production at that cumulative capacity.

        TODO: Add separation of steel and iron price.
        """
        cost_curve = {}
        self._generate_cost_dict(
            world_furnace_groups, lag=lag, environment_most_common_reductant=self.most_common_reductant_by_tech
        )
        cost_dict = self.cost_dict if lag == 0 else self.future_cost_dict
        for product, product_cost_dict in cost_dict.items():
            sorted_product_costs = dict(
                sorted(product_cost_dict.items(), key=lambda item: item[1]["unit_cost_of_production"])
            )

            # Sort by unit cost of production
            # Generate cost curve
            cumulative_production: float = 0
            curve = []
            for fg_id, values in sorted_product_costs.items():
                cap = values["capacity"]
                cost = values["unit_cost_of_production"]

                cumulative_production += cap

                curve.append({"cumulative_capacity": cumulative_production, "production_cost": cost})
            cost_curve[product] = curve

        if lag > 0:
            self.future_cost_curve = cost_curve
        else:
            self.cost_curve = cost_curve
        return cost_curve

    def update_cost_curve(
        self,
        world_furnace_groups: list[FurnaceGroup],
        lag: int,
        product_type=["steel", "iron"],
    ) -> None:
        """
        Update the cost curve for a given product type.
        Filters the furnace groups by product type and generates the cost curve.

        Parameters:
            world_furnace_groups (list[FurnaceGroup]): List of furnace groups.
            product_type (str, optional): Product type to filter by (default "steel").

        """
        self.generate_cost_curve(
            [
                fg
                for fg in world_furnace_groups
                if isinstance(fg.technology.product, str)
                and fg.technology.product.lower() in product_type
                and fg.status in self.config.active_statuses
            ],
            lag=lag,
        )

    @staticmethod
    def _truncate_curve(
        curve_entries: list[dict[str, float]], share: float
    ) -> tuple[list[dict[str, float]], dict[str, float] | None]:
        """
        Apply the market-clearing-share cutoff to an ascending-cost curve.

        Strict-inequality (Option A): keep entries with ``cumulative_capacity <= share * total``.
        The boundary furnace (first to strictly exceed the threshold) is excluded along with everything
        above it. At ``share = 1.0`` the last entry's cumulative equals total, so all entries are kept
        and behaviour is byte-identical to no truncation.

        Args:
            curve_entries: Cost-curve entries sorted ascending by production_cost (each with
                ``cumulative_capacity`` and ``production_cost`` keys).
            share: Fraction of total cumulative capacity that participates in market clearing.

        Returns:
            ``(truncated_prefix, last_full_entry)``. ``last_full_entry`` is ``None`` iff
            ``curve_entries`` is empty; otherwise it is the highest-cost entry of the full curve and
            is used as the reference for the empty-truncated fallback.
        """
        if not curve_entries:
            return [], None
        last_full = curve_entries[-1]
        threshold = share * last_full["cumulative_capacity"]
        truncated = [e for e in curve_entries if e["cumulative_capacity"] <= threshold]
        return truncated, last_full

    def extract_price_from_costcurve(self, demand: float, product: str, future: bool = False) -> float:
        """
        Return the market-clearing price for ``demand`` on the cost curve for ``product``.

        The shortage gate is ``demand > share * total`` (where ``share`` is
        ``steel_market_clearing_share`` or ``iron_market_clearing_share``). When demand fits within
        the dispatchable cap, walks the **full** cost curve and returns the production cost of the
        first entry whose cumulative capacity meets demand — so the boundary furnace (whose
        end-cumulative crosses the threshold but whose start fits within it) is reachable at its
        own cost rather than triggering the premium. When demand exceeds the threshold, returns
        ``last_truncated.production_cost + price_buffer``, where ``last_truncated`` is the highest
        furnace whose end-cumulative still fits under the cap.

        For iron with ``peg_iron_to_steel_price`` enabled, the pegging branch's steel reference price
        is computed against the **truncated** steel slice using ``steel_market_clearing_share``, so
        the engine returns one consistent steel price to all callers in a given year. Pegging is a
        floor (``iron_price = max(base_price, steel_price * ratio)``) and applies uniformly — both
        when iron clears in merit order on its truncated slice **and** when iron is in the shortage
        branch. This deliberately fixes the pre-truncation engine, which skipped pegging entirely
        when iron demand exceeded total iron capacity (the case where steel-driven pricing power
        most clearly should matter).

        Args:
            demand: Tonnes of the product demanded this year.
            product: ``"steel"`` or ``"iron"``.
            future: When True, walk ``self.future_cost_curve`` instead of ``self.cost_curve``.

        Returns:
            Market price in USD/tonne.

        Raises:
            KeyError: If ``product`` is empty/None.

        Notes:
            Empty/missing cost curve returns the default price ``100.0`` with a warning, matching the
            pre-truncation contract. Empty truncated slice (pathological config — should not happen
            with the form's ``min_value=0.5`` validator) falls back to ``last_full + buffer`` with a
            warning. Setting either share to ``1.0`` reproduces legacy behaviour byte-identically.
        """
        logger = logging.getLogger(f"{__name__}.Environment.extract_price_from_costcurve")

        if future:
            cost_curve = self.future_cost_curve
            year = "future year"
        else:
            cost_curve = self.cost_curve
            year = f"current year {self.year}"

        if not product:
            raise KeyError(
                "A product name - lower case - needs to be specified haven't sorted out how to yield both yet"
            )

        if product not in cost_curve or not cost_curve[product]:
            logger.warning(f"Empty cost curve for {product}. Returning default price.")
            return 100.0

        if product == "steel":
            share = self.config.steel_market_clearing_share
            buffer = self.config.steel_price_buffer
        elif product == "iron":
            share = self.config.iron_market_clearing_share
            buffer = self.config.iron_price_buffer
        else:
            raise KeyError(f"Unsupported product {product!r}; expected 'steel' or 'iron'.")

        truncated, last_full = self._truncate_curve(cost_curve[product], share)
        # `last_full` is non-None here because the empty-curve case returned 100.0 above.
        assert last_full is not None
        total = last_full["cumulative_capacity"]
        threshold = share * total

        if not truncated:
            logger.warning(
                f"Empty truncated {product} curve at share={share} in the {year}; degrading to full-curve merit-order."
            )
            # Degenerate share — fall back to legacy full-curve merit-order with no premium.
            truncated = list(cost_curve[product])
            threshold = total

        last_truncated = truncated[-1]

        if last_truncated["production_cost"] == float("inf"):
            logger.error(f"[COST CURVE]: Infinite production cost for {product}.")

        if demand > threshold:
            label = product.capitalize()
            regime = "exceeds total" if demand > total else f"in shortage band (above {share:.0%})"
            logger.warning(
                f"{label} demand {regime} in {year}: "
                f"demand={demand * T_TO_KT:,.0f}kt, "
                f"dispatchable={last_truncated['cumulative_capacity'] * T_TO_KT:,.0f}kt, "
                f"total={total * T_TO_KT:,.0f}kt → "
                f"boundary {last_truncated['production_cost']:.1f} +${buffer:.0f}"
            )
            base_price = last_truncated["production_cost"] + buffer
        else:
            # Walk full curve so the boundary furnace is reachable when demand lands inside it.
            for entry in cost_curve[product]:
                if entry["cumulative_capacity"] >= demand:
                    base_price = entry["production_cost"]
                    break
            else:
                # Unreachable: demand <= threshold <= total, and the full curve ends at total.
                raise ValueError(
                    f"Unreachable: walked the {product} curve without finding an entry meeting demand={demand}."
                )

        if product == "iron" and not future and self.config.peg_iron_to_steel_price:
            steel_demand = self.current_demand
            steel_share = self.config.steel_market_clearing_share
            steel_buffer = self.config.steel_price_buffer
            steel_truncated, steel_last_full = self._truncate_curve(cost_curve.get("steel", []), steel_share)

            if steel_last_full is None:
                # No steel curve — preserve legacy behaviour (no pegging applied).
                return base_price

            steel_total = steel_last_full["cumulative_capacity"]
            steel_threshold = steel_share * steel_total

            if not steel_truncated:
                logger.warning(
                    f"Empty truncated steel curve for iron pegging at share={steel_share} in {year}; "
                    f"degrading to full-curve merit-order for pegging reference."
                )
                steel_truncated = list(cost_curve["steel"])
                steel_threshold = steel_total

            last_steel_truncated = steel_truncated[-1]
            if steel_demand > steel_threshold:
                regime = (
                    "exceeds total"
                    if steel_demand > steel_total
                    else f"in shortage band (above dispatchable {steel_share:.0%})"
                )
                logger.warning(
                    f"[PEGGING] Steel reference {regime} in {year}: "
                    f"steel_demand={steel_demand * T_TO_KT:,.0f}kt → boundary "
                    f"{last_steel_truncated['production_cost']:.1f} +${steel_buffer:.0f}"
                )
                steel_price = last_steel_truncated["production_cost"] + steel_buffer
            else:
                # Walk full steel curve so the boundary furnace is reachable when steel_demand lands inside it.
                for entry in cost_curve["steel"]:
                    if entry["cumulative_capacity"] >= steel_demand:
                        steel_price = entry["production_cost"]
                        break
                else:
                    # Unreachable: steel_demand <= steel_threshold <= steel_total.
                    raise ValueError(
                        f"Unreachable: walked the steel curve without finding an entry "
                        f"meeting steel_demand={steel_demand}."
                    )

            ratio = self.config.iron_to_steel_price_ratio
            pegged_price = steel_price * ratio
            final_price = max(base_price, pegged_price)

            if final_price != base_price:
                logger.info(
                    f"[PEGGING] Applied: base iron ${base_price:.2f}/t, steel ${steel_price:.2f}/t, "
                    f"pegged@{ratio:.0%}=${pegged_price:.2f}/t, final ${final_price:.2f}/t"
                )

            return final_price

        return base_price

    def extract_global_average_feedstock_cost(self, world_furances: list[FurnaceGroup]) -> dict[str, float]:
        """
        Extract the average cost of feedstocks across all furnaces, using a weighted average cost
        TODO: implement a regional aspect. probably gross regions or something similar
        This can subsequently be used when exploring other technologies etc.
        """

        materials_dicts = []
        for fg in world_furances:
            if fg.bill_of_materials and fg.bill_of_materials["materials"]:
                materials_dicts.append(fg.bill_of_materials["materials"])
            # energy_costs.append(fg.bill_of_materials['energy_costs'])
            # Accumulators for each feedstock
        materials = []
        for mat in materials_dicts:
            for key, value_dict in mat.items():
                if isinstance(value_dict, dict):
                    materials.append(
                        {
                            "feedstock": key,
                            "demand": value_dict.get("demand"),
                            "total_cost": value_dict.get("total_cost"),
                        }
                    )

        sums: dict[str, dict[str, float]] = defaultdict(lambda: {"total_cost": 0.0, "demand": 0.0})

        # Accumulate
        for item in materials:
            key = str(item["feedstock"])
            # Safely convert to float, handling dict/None values
            total_cost = item["total_cost"]
            demand = item["demand"]

            if isinstance(total_cost, dict):
                total_cost_value = sum(total_cost.values()) if total_cost else 0.0
            elif total_cost is None:
                total_cost_value = 0.0
            else:
                total_cost_value = float(total_cost) if isinstance(total_cost, (int, float, str)) else 0.0

            if isinstance(demand, dict):
                demand_value = sum(demand.values()) if demand else 0.0
            elif demand is None:
                demand_value = 0.0
            else:
                demand_value = float(demand) if isinstance(demand, (int, float, str)) else 0.0

            sums[key]["total_cost"] += total_cost_value
            sums[key]["demand"] += demand_value

        # Compute ratios
        return {key: sums[key]["total_cost"] / sums[key]["demand"] for key in sums}

    def _predict_new_market_price(self, new_furnace_group: FurnaceGroup, demand: float) -> float:
        """
        Takes in a list of furnace groups and predicts the new market price based on the cost curve, but doesn't store it.
        Returns:  The production cost at or above the demand level.
        """
        # Handle both nested and flattened cost_dict structures
        if isinstance(self.cost_dict, dict) and "steel" in self.cost_dict and isinstance(self.cost_dict["steel"], dict):
            # Nested structure: use steel portion
            extended_list_of_furnace_groups: dict[str, dict[str, float]] = self.cost_dict["steel"].copy()
        else:
            # Flattened structure: use directly (assume it's the right structure)
            extended_list_of_furnace_groups: dict[str, dict[str, float]] = self.cost_dict.copy()  # type: ignore

        extended_list_of_furnace_groups[new_furnace_group.furnace_group_id] = {
            "capacity": float(new_furnace_group.capacity),
            "unit_cost_of_production": new_furnace_group.unit_production_cost,
        }
        sorted_assets = dict(
            sorted(
                extended_list_of_furnace_groups.items(),
                key=lambda item: (
                    float(item[1]["unit_cost_of_production"]) if item[1]["unit_cost_of_production"] is not None else 0.0
                ),
            )
        )

        # Sort by unit cost of production

        # Generate cost curve
        cumulative_capacity: float = 0
        for fg_id, values in sorted_assets.items():
            cap = values["capacity"]
            cost = values["unit_cost_of_production"]

            cumulative_capacity += float(cap) if cap is not None else 0.0

            if cumulative_capacity >= demand:
                return float(cost) if cost is not None else 0.0
        raise ValueError(f"Demand of {demand} exceeds maximum cumulative capacity of {cumulative_capacity}")

    def update_regional_capacity(self, world_plants: list[Plant]) -> None:
        """
        Aggregate total capacity by region and technology from all active furnace groups worldwide.

        Steps:
            1. Map plant locations to regions using country_mappings (ISO3 → region)
            2. Initialize empty nested dictionaries for steel and iron regional capacities
            3. Loop through all plants and their furnace groups
            4. Filter out inactive, zero-capacity, "other" technology, or non-allowed furnace groups
            5. Classify furnace groups by product type (steel vs iron)
            6. Accumulate capacity by region and technology for both products
            7. Store initial capacities if not already set (for learning curve baseline)

        Args:
            world_plants (list[Plant]): All plants in the simulation to aggregate capacity from.

        Side Effects:
            - Updates self.regional_steel_capacity with aggregated steel capacity by region and technology
            - Updates self.regional_iron_capacity with aggregated iron capacity by region and technology
            - Initializes self.steel_init_capacity (first call only) as baseline for CAPEX reduction calculations
            - Initializes self.iron_init_capacity (first call only) as baseline for CAPEX reduction calculations
        """
        if self.country_mappings is None:
            raise ValueError("country_mappings must be set before updating regional capacity")
        region_dict = self.country_mappings.iso3_to_region()
        self.regional_steel_capacity: defaultdict[str, defaultdict[str, float]] = defaultdict(
            lambda: defaultdict(float)
        )
        self.regional_iron_capacity: defaultdict[str, defaultdict[str, float]] = defaultdict(lambda: defaultdict(float))

        for pa in world_plants:
            for fg in pa.furnace_groups:
                if (
                    fg.status.lower() not in self.config.active_statuses
                    or fg.capacity == 0
                    or fg.technology.name.lower() == "other"
                    or fg.technology.name not in self.allowed_furnace_transitions
                ):
                    continue

                target_capacity = (
                    self.regional_steel_capacity
                    if fg.technology.product == Commodities.STEEL.value
                    else self.regional_iron_capacity
                    if fg.technology.product == Commodities.IRON.value
                    else None
                )

                if target_capacity is not None and region_dict.get(pa.location.iso3 or "") is not None:
                    region = region_dict[pa.location.iso3 or ""]
                    if region is not None:
                        target_capacity[region][fg.technology.name] += float(fg.capacity)

        if not self.steel_init_capacity:
            self.steel_init_capacity = self.regional_steel_capacity.copy()

        if not self.iron_init_capacity:
            self.iron_init_capacity = self.regional_iron_capacity.copy()

    def most_common_reductant(self, world_plants: list[Plant]) -> dict[str, str]:
        """
        Get the most common reductant for each technology across all plants in the simulation.

        Aggregates reductant data from active-status furnace groups across all plants in the
        simulation to determine the most frequently used reductant for each technology type.
        Closed and discarded furnace groups do not vote.

        Args:
            world_plants (list[Plant]): All plants in the simulation to aggregate reductant data from.

        Returns:
            dict[str, str]: Dictionary mapping technology name to most common reductant.
                           If no reductant is set for a technology, it will be omitted from the result.

        Example:
            {
                "BOF": "coke",
                "EAF": "electricity",
                "DRI": "natural_gas"
            }
        """
        from steelo.utilities.utils import get_most_common_reductant_by_technology

        # Collect all furnace groups from all plants
        all_furnace_groups = [fg for plant in world_plants for fg in plant.furnace_groups]

        return get_most_common_reductant_by_technology(all_furnace_groups, self.config.active_statuses)

    def update_steel_capex_reduction_ratio(self) -> None:
        """
        Calculate CAPEX reduction ratios for all steel technologies across all regions based on capacity growth.

        Steps:
            1. Loop through all regions in regional_steel_capacity
            2. For each technology in each region, calculate reduction ratio using initial vs current capacity
            3. Store results in self.steel_capex_reduction_ratio[region][technology]

        Side Effects:
            Updates self.steel_capex_reduction_ratio with nested dictionary mapping regions to technology-specific reduction multipliers.

        Notes:
            - Uses calculate_capex_reduction_rate() which applies learning-by-doing power law
            - Compares current capacity (self.regional_steel_capacity) to initial capacity (self.steel_init_capacity)
            - Reduction ratios <1.0 indicate cost reductions due to increased deployment
        """
        from .calculate_costs import calculate_capex_reduction_rate

        self.steel_capex_reduction_ratio: dict[str, dict[str, float]] = {}
        for region, capacity in self.regional_steel_capacity.items():
            self.steel_capex_reduction_ratio[region] = {}
            for tech, cap in capacity.items():
                self.steel_capex_reduction_ratio[region][tech] = calculate_capex_reduction_rate(
                    self.steel_init_capacity[region][tech], cap
                )

    def update_iron_capex_reduction_ratio(self) -> None:
        """
        Calculate CAPEX reduction ratios for all iron technologies across all regions based on capacity growth.

        Steps:
            1. Loop through all regions in regional_iron_capacity
            2. For each technology in each region, calculate reduction ratio using initial vs current capacity
            3. Store results in self.iron_capex_reduction_ratio[region][technology]

        Side Effects:
            Updates self.iron_capex_reduction_ratio with nested dictionary mapping regions to technology-specific reduction multipliers.

        Notes:
            - Uses calculate_capex_reduction_rate() which applies learning-by-doing power law
            - Compares current capacity (self.regional_iron_capacity) to initial capacity (self.iron_init_capacity)
            - Reduction ratios <1.0 indicate cost reductions due to increased deployment
        """
        from .calculate_costs import calculate_capex_reduction_rate

        self.iron_capex_reduction_ratio: dict[str, dict[str, float]] = {}
        for region, capacity in self.regional_iron_capacity.items():
            self.iron_capex_reduction_ratio[region] = {}

            for tech, cap in capacity.items():
                self.iron_capex_reduction_ratio[region][tech] = calculate_capex_reduction_rate(
                    capacity_current=cap, capacity_zero=self.iron_init_capacity[region][tech]
                )
        return None

    def update_capex_reduction_ratios(self) -> None:
        """
        Calculate and merge CAPEX reduction ratios for both steel and iron technologies across all regions.

        Steps:
            1. Calculate steel CAPEX reduction ratios by region and technology (via update_steel_capex_reduction_ratio)
            2. Calculate iron CAPEX reduction ratios by region and technology (via update_iron_capex_reduction_ratio)
            3. Merge steel and iron reduction ratios into a single unified dictionary

        Side Effects:
            Updates self.capex_reduction_ratio with merged dictionary mapping regions to technology-specific reduction ratios.

        Notes:
            - CAPEX reduction ratios represent learning-by-doing effects as cumulative capacity increases
            - Ratios are calculated based on current capacity relative to initial capacity for each technology and region
            - The merged ratio dictionary is used by update_capex() to adjust base CAPEX values
        """

        def merge_iron_steel_capex_ratios(
            target: dict[str, dict[str, float]], source: dict[str, dict[str, float]]
        ) -> dict[str, dict[str, float]]:
            """
            Merge two nested dictionaries with structure region → {technology: ratio}.

            Args:
                target (dict[str, dict[str, float]]): First dictionary (typically steel ratios).
                source (dict[str, dict[str, float]]): Second dictionary to merge in (typically iron ratios).

            Returns:
                merged (dict[str, dict[str, float]]): Combined dictionary with all region-technology combinations.

            Notes:
                If a region exists in both dictionaries, the inner technology dictionaries are merged (source updates target).
            """
            out = target.copy()
            for key, inner_dict in source.items():
                if key in out:
                    # If the key exists, update the inner dictionary
                    out[key].update(inner_dict)
                else:
                    # If the key does not exist, add a copy of the source's inner dictionary
                    out[key] = inner_dict.copy()
            return out

        self.update_steel_capex_reduction_ratio()
        self.update_iron_capex_reduction_ratio()

        self.capex_reduction_ratio = merge_iron_steel_capex_ratios(
            self.steel_capex_reduction_ratio, self.iron_capex_reduction_ratio
        )
        return None

    def update_capex(self) -> None:
        """
        Transform base CAPEX values into region-specific values by applying CAPEX reduction ratios.

        Steps:
            1. Check the structure of greenfield CAPEX data (flat dict[tech: capex] vs nested dict[region: {tech: capex}])
            2. If flat structure: Create region-specific CAPEX by multiplying base values with reduction ratios for each region
            3. If nested structure already exists: Update each region-technology combination using default values × reduction ratios
            4. Store original flat structure as "default" for reference

        Side Effects:
            - Updates self.name_to_capex["greenfield"] to nested dict[region][technology] structure with adjusted CAPEX values
            - Creates self.name_to_capex["default"] with original flat CAPEX values if not already present
            - Ensures all regions in capex_reduction_ratio have corresponding CAPEX entries

        Notes:
            - CAPEX reduction ratios account for learning-by-doing effects (more cumulative capacity = lower costs)
            - If a technology-region combination has no reduction ratio, uses ratio of 1.0 (no reduction)
            - This transformation happens during initialization to project global CAPEX values to regional variations
        """

        # check if structure of capex is dict[str, float]
        greenfield_capex = self.name_to_capex["greenfield"]
        if isinstance(greenfield_capex, dict) and all(
            isinstance(k, str) and isinstance(v, (int, float)) for k, v in greenfield_capex.items()
        ):
            # Store the original flat structure as default
            self.name_to_capex["default"] = greenfield_capex.copy()
            # if so - create a new dict[str, dict[str, float]] by multiplying with the region tech combo from capex_reduction_ratio

            greenfield_dict: dict[str, dict[str, float]] = {}
            for region, tech_ratios in self.capex_reduction_ratio.items():
                greenfield_dict[region] = {}
                for tech, capex in greenfield_capex.items():
                    if isinstance(capex, (int, float)):
                        greenfield_dict[region][tech] = float(capex) * tech_ratios.get(tech, 1)
            # Explicitly assign the new structure - cast to Any to handle mixed types
            self.name_to_capex["greenfield"] = greenfield_dict  # type: ignore

        else:
            # if not - we assume that the capex is already in the dict[str, dict[str, float]] format
            # and we just multiply the capex with the capex_reduction_ratio
            greenfield_structure = self.name_to_capex.get("greenfield", {})
            default_structure = self.name_to_capex.get("default", {})

            if isinstance(greenfield_structure, dict) and isinstance(default_structure, dict):
                for region, tech_ratios in self.capex_reduction_ratio.items():
                    # Ensure the region exists in greenfield
                    if region not in greenfield_structure:
                        greenfield_structure[region] = {}  # type: ignore

                    for tech, ratio in tech_ratios.items():
                        if tech in default_structure:
                            default_tech_value = default_structure[tech]
                            if isinstance(default_tech_value, (int, float)) and isinstance(
                                greenfield_structure[region], dict
                            ):
                                greenfield_structure[region][tech] = float(default_tech_value) * ratio  # type: ignore

        return None

    def update_technology_availability(self) -> None:
        """
        Updates the capexes and allowed furnace transitions based on the current environment year.
        """

        # NOTE: After removing activation year restrictions in initiate_techno_economic_details,
        # all technologies are now pre-loaded into name_to_capex regardless of activation year.
        # The dynamic loading below is no longer needed and has been commented out.
        # Keeping the code for reference in case activation year logic needs to be restored.

        # # Update greenfield capex for each region
        # for region, technology_capex_dict in self.name_to_capex["greenfield"].items():
        #     for technology, activation_year in self.technology_activation_year.items():
        #         if (
        #             isinstance(technology_capex_dict, dict)
        #             and technology not in technology_capex_dict
        #             and activation_year <= self.year
        #         ):
        #             if isinstance(self.name_to_capex["greenfield"][region], dict):
        #                 # Note: self.capex doesn't exist - would need to get capex from somewhere
        #                 # self.name_to_capex["greenfield"][region].update({technology: self.capex[technology]})
        #                 # self.name_to_capex["default"][region].update({technology: self.capex[technology]})
        #                 pass
        # # Update brownfield capex for each region

        # # Also update the default capex dictionary if it exists
        # if "default" in self.name_to_capex and isinstance(self.name_to_capex["default"], dict):
        #     for technology, activation_year in self.technology_activation_year.items():
        #         if technology not in self.name_to_capex["default"] and activation_year <= self.year:
        #             self.name_to_capex["default"][technology] = self.capex[technology]

    def initiate_demand_dicts(self, world_demand_centres: list[DemandCenter]) -> None:
        """
        Initiates the demand dictionary in the environment

        This allows us to have a proxy for the demand centres, as values, but not locations, change in time
        """
        self.demand_dict = defaultdict(lambda: defaultdict(lambda: Volumes(0)))
        for dc in world_demand_centres:
            self.demand_dict[dc.demand_center_id] = dc.demand_by_year

    def initiate_country_mappings(self, country_mappings: list[CountryMapping]) -> None:
        """
        Initialize the country mappings for the environment.

        Args:
            country_mappings (list[CountryMapping]): A list of CountryMapping objects
                containing country-specific data like ISO codes, IRENA names, regions, etc.

        Side Effects:
            Creates a CountryMappingService instance that provides efficient lookups
            for country-related data throughout the simulation.
        """
        self.country_mappings = CountryMappingService(country_mappings)

    def initiate_hydrogen_efficiency(self, hydrogen_efficiency: list[HydrogenEfficiency]) -> None:
        """
        Initialize hydrogen efficiency data for the environment.

        Args:
            hydrogen_efficiency: List of HydrogenEfficiency objects containing years and efficiency values
        """
        self.hydrogen_efficiency = {data.year: data.efficiency for data in hydrogen_efficiency}

    def initiate_hydrogen_capex_opex(self, hydrogen_capex_opex: list[HydrogenCapexOpex]) -> None:
        """
        Initialize hydrogen CAPEX/OPEX component data for the environment.

        Args:
            hydrogen_capex_opex: List of HydrogenCapexOpex objects containing country codes and yearly values
        """
        self.hydrogen_capex_opex = {data.country_code: data.values for data in hydrogen_capex_opex}

    def calculate_demand(self) -> None:
        """
        Calaculates the total demand for the current year
        """
        self.current_demand = Volumes(
            sum(float(entry.get(self.year, Volumes(0))) for entry in self.demand_dict.values())
        )

    def future_demand(self, year: Year) -> Volumes:
        """
        Returns the future demand for the given year.
        """
        return Volumes(sum(float(entry.get(year, Volumes(0))) for entry in self.demand_dict.values()))

    def set_trade_tariffs(self, trade_tariffs: list[TradeTariff]) -> None:
        self.trade_tariffs = trade_tariffs

    def set_legal_process_connectors(self, legal_process_connectors: list[LegalProcessConnector]) -> None:
        self.legal_process_connectors = legal_process_connectors

    def get_active_trade_tariffs(self) -> list[TradeTariff]:
        """
        Returns a list of active trade tariffs based on the current year.
        """
        active_tariffs = []
        for tariff in self.trade_tariffs:
            # treat empty values as non-restrictive
            if tariff.start_date is None and tariff.end_date is None:
                active_tariffs.append(tariff)
            elif tariff.start_date is None and tariff.end_date is not None and tariff.end_date >= self.year:
                active_tariffs.append(tariff)
            elif tariff.start_date is not None and tariff.start_date <= self.year and tariff.end_date is None:
                active_tariffs.append(tariff)
            elif (
                tariff.start_date is not None
                and tariff.end_date is not None
                and tariff.start_date <= self.year <= tariff.end_date
            ):
                active_tariffs.append(tariff)
        return active_tariffs

    def generate_average_boms(
        self, plant_list: list[Plant], iso3: str | None = None
    ) -> dict[str, dict[str, dict[str, float]]]:
        """Generate technology-specific average bill of materials across all active furnace groups.

        Aggregates material demands and costs from all active furnaces of each technology type
        to compute representative average material mixes (demand shares) and unit costs. Used
        as fallback data for new plants or technologies without specific BOM information.

        Args:
            plant_list: List of Plant objects to aggregate BOMs from.
            iso3: Optional ISO3 country code for region-specific fallbacks. If None,
                uses global averages.

        Returns:
            Dictionary with structure:
                {
                    technology_name: {
                        material_name: {
                            "unit_cost": float,        # Average cost per ton
                            "input_share_pct": float   # Fraction of total input demand (sums to 1.0)
                        }
                    }
                }

        Side Effects:
            - Sets self.avg_boms with the computed average BOMs.
            - Sets self.avg_utilization with average utilization rates per technology.
            - Adds hardcoded fallback BOMs for technologies in
              get_available_fallback_technologies() that have no data.

        Raises:
            ValueError: If no average BOMs can be generated (no active furnaces found).

        Notes:
            - Only includes furnaces with active status (from config.active_statuses).
            - Skips zero-capacity furnaces and "prep sinter"/"other" technologies.
            - Demand share percentages sum to 1.0 for each technology.
            - Falls back to default_metallic_charge_per_technology mapping if needed.
            - Uses region-specific or global average costs for missing technologies.
        """
        logger = logging.getLogger(f"{__name__}.Environment.generate_average_boms")
        logger.debug("Generating average BOMs")
        # 1) Accumulate per‐tech, per‐material sums
        # acc[tech][mat] = {'demand_sum': ..., 'cost_sum': ...}
        acc: dict[str, dict[str, dict[str, float]]] = defaultdict(
            lambda: defaultdict(lambda: {"demand_sum": 0.0, "cost_sum": 0.0})
        )  # noqa
        avg_util = {}
        tech_contributor_counts: dict[str, int] = defaultdict(int)
        bof_raw_inputs: list[dict[str, object]] = []

        for p in plant_list:
            for fg in p.furnace_groups:
                if (
                    fg.capacity == 0
                    or fg.status.lower() not in self.config.active_statuses
                    or fg.technology.name.lower() in ["prep sinter", "other"]
                ):
                    continue
                tech = fg.technology.name
                if tech not in avg_util:
                    avg_util[tech] = {"production": 0.0, "capacity": 0.0}
                avg_util[fg.technology.name]["production"] += float(fg.production)
                avg_util[fg.technology.name]["capacity"] += float(fg.capacity)

                # Check if bill_of_materials exists and has materials key
                if not fg.bill_of_materials or "materials" not in fg.bill_of_materials:
                    continue
                fg_contributed = False
                for mat_name, info in fg.bill_of_materials["materials"].items():
                    acc[tech][mat_name]["demand_sum"] += info["demand"]
                    acc[tech][mat_name]["cost_sum"] += info["total_material_cost"]
                    if (
                        diag.diagnostics_enabled()
                        and tech.upper() == "BOF"
                        and mat_name.lower() == "hot_metal"
                        and info["demand"] > 0
                    ):
                        bof_raw_inputs.append(
                            {
                                "year": int(self.year),
                                "plant_id": getattr(p, "plant_id", "unknown"),
                                "furnace_group_id": fg.furnace_group_id,
                                "production": float(fg.production),
                                "demand": float(info["demand"]),
                                "total_material_cost": float(info.get("total_material_cost", 0.0)),
                            }
                        )
                    fg_contributed = True

                if fg_contributed:
                    tech_contributor_counts[tech] += 1

        # 2) Build the stats structure
        stats: dict[str, dict[str, dict[str, float]]] = {}

        for tech, mats in acc.items():
            # compute the tech's total demand across all materials
            total_demand_all_mc_for_tech = sum(sums["demand_sum"] for sums in mats.values())

            stats[tech] = {}
            for mat_name, sums in mats.items():
                demand_of_metallic_charge = sums["demand_sum"]
                cost_associated_with_individual_metallic_charge = sums["cost_sum"]

                # weighted unit cost (cost per unit)
                unit_cost_for_metallic_charge = (
                    cost_associated_with_individual_metallic_charge / demand_of_metallic_charge
                    if demand_of_metallic_charge > 0
                    else None
                )

                # input share (fraction of total demand for this technology)
                input_share_pct_of_mc = (
                    (demand_of_metallic_charge / total_demand_all_mc_for_tech)
                    if total_demand_all_mc_for_tech > 0
                    else None
                )

                stats[tech][mat_name] = {
                    "unit_cost": unit_cost_for_metallic_charge if unit_cost_for_metallic_charge is not None else 0.0,
                    "input_share_pct": input_share_pct_of_mc if input_share_pct_of_mc is not None else 0.0,
                }
        self._diag_bof_sample_count = tech_contributor_counts.get("BOF", 0)
        if diag.diagnostics_enabled():
            year_int = int(self.year)
            bof_stats = stats.get("BOF")
            if bof_stats:
                diag.write_json(["avg_boms", f"{year_int}.json"], {"BOF": bof_stats})
                if year_int == 2059:
                    self._diag_bof_baseline_2059 = copy.deepcopy(bof_stats)
                elif year_int == 2060 and self._diag_bof_baseline_2059:
                    base_cost = self._diag_bof_baseline_2059.get("hot_metal", {}).get("unit_cost")
                    new_cost = bof_stats.get("hot_metal", {}).get("unit_cost")
                    if base_cost and new_cost:
                        delta = new_cost - base_cost
                        pct = (delta / base_cost * 100) if base_cost else None
                        logger.warning(
                            "[DIAGNOSTICS][AVG_BOM] BOF hot_metal unit_cost jump year %s -> %s: %.2f USD/t (%+.1f%%)",
                            2059,
                            2060,
                            delta,
                            pct if pct is not None else 0.0,
                        )
            bof_count = self._diag_bof_sample_count
            if bof_raw_inputs and diag.should_log_raw_bof_inputs(year_int, bof_count):
                for entry in bof_raw_inputs:
                    diag.append_csv(
                        f"avg_boms/bof_inputs_{year_int}.csv",
                        ["year", "plant_id", "furnace_group_id", "production", "demand", "total_material_cost"],
                        [
                            entry["year"],
                            entry["plant_id"],
                            entry["furnace_group_id"],
                            entry["production"],
                            entry["demand"],
                            entry["total_material_cost"],
                        ],
                    )

        for tech, util_dict in avg_util.items():
            avg_util[tech].update(
                {
                    "utilization_rate": (
                        float(util_dict["production"] / util_dict["capacity"]) if util_dict["capacity"] > 0 else 0
                    )
                }
            )
        if stats == {}:
            raise ValueError("No average BOMs available for the given plants.")

        self.avg_boms = stats
        self.avg_utilization = avg_util

        for tech in self.get_available_fallback_technologies():
            if tech not in self.avg_boms and tech.lower() not in self.avg_boms:
                logger.warning(
                    f"Using hardcoded average BOM for {tech} as no average BOM is available in the environment."
                )
                bom: dict = defaultdict(dict)
                # Check if the technology has a default metallic charge defined
                if tech in self.default_metallic_charge_per_technology:
                    metallic_charge = self.default_metallic_charge_per_technology[tech]
                    if iso3 is None:
                        # Use average across all regions
                        avg_cost = self.get_average_fallback_material_cost(technology=tech)
                        if avg_cost is not None:
                            bom[metallic_charge]["unit_cost"] = avg_cost
                            bom[metallic_charge]["input_share_pct"] = 1.0
                    else:
                        # Use region-specific cost (Note: get_available_fallback_technologies doesn't take iso3/tech params)
                        specific_cost = self.get_fallback_material_cost(iso3, tech)
                        if specific_cost is not None:
                            bom[metallic_charge]["unit_cost"] = specific_cost
                            bom[metallic_charge]["input_share_pct"] = 1.0
                else:
                    logger.warning(f"Technology {tech} not found in default_metallic_charge_per_technology mapping")
                self.avg_boms[tech] = bom
            if tech not in self.avg_utilization:
                logger.warning(
                    f"Using hardcoded average utilization for {tech} as no average utilization is available."
                )
                self.avg_utilization[tech] = {"utilization_rate": 0.6}

        return self.avg_boms

    def generate_average_material_costs(self, world_plants: list[Plant]) -> None:
        """Calculate technology-specific average material costs per ton of production.

        Aggregates total material costs and production volumes across all active furnace groups
        of each technology type to compute average unit material costs (USD/ton). Complementary
        to generate_average_boms() but focuses on total cost per ton rather than material mix.

        Args:
            world_plants: List of all Plant objects in the simulation to aggregate costs from.

        Side Effects:
            Sets self.avg_material_costs with structure:
                {
                    technology_name: {
                        "total_cost": float,          # Aggregated total material costs
                        "production": float,          # Aggregated total production
                        "unit_material_cost": float   # Cost per ton (total_cost/production)
                    }
                }

        Notes:
            - Only includes furnaces with active status (from config.active_statuses).
            - Sums all material costs from bill_of_materials["materials"] for each furnace.
            - Unit cost = 0 if production volume is zero (avoids division by zero).
            - Does not raise exceptions if some technologies have no data.
        """
        self.avg_material_costs: dict[str, dict[str, float]] = {}
        fgs = [fg for plant in world_plants for fg in plant.furnace_groups if fg.status in self.config.active_statuses]
        for fg in fgs:
            bom = fg.bill_of_materials
            if bom:
                materials = bom["materials"]
                if fg.technology.name not in self.avg_material_costs:
                    self.avg_material_costs[fg.technology.name] = {"total_cost": 0.0, "production": 0.0}
                for key, items in materials.items():
                    self.avg_material_costs[fg.technology.name]["total_cost"] += float(items["total_material_cost"])
                self.avg_material_costs[fg.technology.name]["production"] += float(fg.production)

        for tech in self.avg_material_costs:
            self.avg_material_costs[tech]["unit_material_cost"] = (
                self.avg_material_costs[tech]["total_cost"] / self.avg_material_costs[tech]["production"]
                if self.avg_material_costs[tech]["production"] > 0
                else 0
            )

    def generate_input_effectiveness_mapping_from_feedstocks(
        self, feedstocks_for_tech: list[PrimaryFeedstock], most_common_reductant: str | None = None
    ) -> dict[str, float]:
        logger = logging.getLogger(f"{__name__}.generate_input_effectiveness_mapping_from_feedstocks")

        input_effectiveness: dict[str, float] = {}
        for feed in feedstocks_for_tech:
            logger.debug(
                f"[BOM] Checking feed: {feed.metallic_charge}, reductant: '{feed.reductant}' (as reference, the mcr is '{most_common_reductant}'), qty: {feed.required_quantity_per_ton_of_product}"
            )
            if (
                isinstance(feed.metallic_charge, str)
                and (
                    most_common_reductant is None  # Accept any reductant when None
                    or feed.reductant == most_common_reductant
                    or (isinstance(feed.reductant, str) and feed.reductant.lower() == most_common_reductant)
                    or (not most_common_reductant and not feed.reductant)  # Both are blank/empty
                )
                and feed.required_quantity_per_ton_of_product is not None
            ):
                input_effectiveness[feed.metallic_charge.lower()] = feed.required_quantity_per_ton_of_product
                logger.debug(
                    f"[BOM] Added to input_effectiveness: {feed.metallic_charge.lower()} = {feed.required_quantity_per_ton_of_product}"
                )
        # If no inputs matched the most common reductant
        if not input_effectiveness:
            logger.error(
                f"[BOM] No inputs matched most common reductant: {most_common_reductant}, feedstocks: {feedstocks_for_tech}"
            )

        return input_effectiveness

    def get_bom_from_avg_boms(
        self, energy_costs: dict[str, float], tech: str, capacity: float, most_common_reductant: str | None = None
    ) -> tuple[dict[str, dict[str, dict[str, float]]] | None, float, str, dict[str, float]]:
        """Construct a complete bill of materials for a furnace from technology averages.

        Generates a detailed BOM by combining: (1) average material mix from avg_boms,
        (2) technology-specific process efficiencies from dynamic_feedstocks, and
        (3) plant-specific energy costs. Used primarily for new/planned furnaces or
        fallback when actual BOM data unavailable.

        Algorithm:
            1. Calculate total energy costs for each (material, reductant) combination
            2. Identify the most cost-effective reductant
            3. Map materials to process efficiencies for the chosen reductant
            4. Scale material demands by: output_share × capacity × process_efficiency
            5. Calculate energy demands and costs using the chosen reductant's energy profile

        Args:
            energy_costs: Dictionary mapping energy type (e.g., "electricity", "natural_gas")
                to cost per unit (USD/unit). Keys should use underscores, not hyphens.
            tech: Technology name (must exist in both avg_boms and dynamic_feedstocks).
                Examples: "EAF", "BF-BOF", "DRI-EAF".
            capacity: Annual production capacity in tons. Used to scale material/energy demands.
            most_common_reductant: Optional reductant to use for BOM generation. If None,
                accepts all feedstocks and extracts a reductant from available data.

        Returns:
            Tuple of (bom_dict, utilization_rate, chosen_reductant, output_shares) where
            output_shares maps normalised metallic charge -> share of product (reductant-
            invariant; weights the year-wise reductant score series), and:
                - bom_dict: BOM structure with materials and energy:
                    {
                        "materials": {
                            material_name: {
                                "demand": float,              # Tons required (input)
                                "total_cost": float,          # Total USD
                                "unit_cost": float,           # USD per ton
                                "unit_material_cost": float,  # Material cost per ton of output (excludes energy)
                                "product_volume": float       # Output volume (tons)
                            }
                        },
                        "energy": {
                            material_name: {
                                "demand": float,       # Energy units required
                                "total_cost": float,   # Total USD
                                "unit_cost": float     # USD per unit
                            }
                        }
                    }
                - utilization_rate: Expected utilization (from avg_utilization, default 0.6)
                - chosen_reductant: Name of the selected reductant (e.g., "coke", "natural_gas"),
                    or empty string if no reductant data available

        Raises:
            KeyError: If technology not found in avg_boms or if material in avg_boms
                not found in dynamic_feedstocks (indicates data inconsistency).

        Notes:
            - Returns (None, 0.6, "") if no energy cost data found for technology.
            - Logs extensive debug information via logger for troubleshooting.
            - Assumes avg_boms already populated (call generate_average_boms() first).
            - Material demand shares from avg_boms should sum to 1.0 per technology.
            - Process efficiencies are tons_input/ton_output (>1 for losses, <1 for enrichment).
        """
        logger = logging.getLogger(f"{__name__}.get_bom_from_avg_boms")

        feedstocks_for_tech = self.dynamic_feedstocks.get(tech, self.dynamic_feedstocks.get(tech.lower(), []))
        bom_dict: dict[str, dict[str, dict[str, float]]] = {"materials": {}, "energy": {}}
        output_shares: dict[str, float] = {}

        # Step 3a: Resolve reductant
        can_reconstruct_energy = bool(feedstocks_for_tech)
        if not feedstocks_for_tech:
            logger.warning(
                "[AVG_BOM_DIAG] reductant: tech=%s no PrimaryFeedstock data, energy reconstruction impossible",
                tech,
            )

        if most_common_reductant is None and feedstocks_for_tech:
            has_reductants = any(f.reductant and str(f.reductant).strip() for f in feedstocks_for_tech)
            if not has_reductants:
                # Reductant-free tech (e.g. EAF, BOF) — skip cheapest-reductant calculation
                most_common_reductant = ""
                logger.debug(
                    "[AVG_BOM_DIAG] reductant: tech=%s no reductants in feedstocks, using empty string",
                    tech,
                )
            else:
                cheapest_reductant, _ = calculate_energy_costs_and_most_common_reductant(
                    feedstocks_for_tech,
                    energy_costs,
                )
                if cheapest_reductant:
                    most_common_reductant = cheapest_reductant
                    logger.info(
                        "[AVG_BOM_DIAG] reductant: tech=%s source=cheapest_reductant_fallback chosen=%s",
                        tech,
                        most_common_reductant,
                    )
                else:
                    # Cheapest-reductant returned empty — fall back to first available
                    for feed in feedstocks_for_tech:
                        if feed.reductant and str(feed.reductant).strip():
                            most_common_reductant = str(feed.reductant)
                            break
                    if most_common_reductant is None:
                        most_common_reductant = ""
                    logger.warning(
                        "[AVG_BOM_DIAG] reductant: tech=%s cheapest_reductant_failed, using first_available=%s",
                        tech,
                        most_common_reductant,
                    )
        elif most_common_reductant is not None:
            logger.debug(
                "[AVG_BOM_DIAG] reductant: tech=%s source=fleet chosen=%s",
                tech,
                most_common_reductant,
            )

        # Step 3b: Build input effectiveness mapping for resolved reductant
        logger.debug("[BOM] Step 3b: Building input effectiveness")
        input_effectiveness = self.generate_input_effectiveness_mapping_from_feedstocks(
            feedstocks_for_tech,
            most_common_reductant,
        )

        # Step 4: Fallback if no average BOM available
        logger.debug("[BOM] Step 4: Checking avg_boms")
        if not hasattr(self, "avg_boms") or not self.avg_boms:
            logger.debug("[BOM] avg_boms not initialized or empty")
            self.avg_boms = {}

        # Check if the specific technology exists in avg_boms - fail fast if missing
        if tech not in self.avg_boms:
            available_techs = list(self.avg_boms.keys())
            logger.error(
                "[BOM] avg_boms missing tech '%s'. feedstocks=%d, avg_boms_keys=%s",
                tech,
                len(feedstocks_for_tech),
                available_techs,
            )
            raise KeyError(
                f"Technology '{tech}' not found in avg_boms. "
                f"This indicates missing or incomplete fallback material cost data. "
                f"Available technologies: {available_techs}. "
                f"Ensure the master Excel file contains complete 'Fallback material cost' data for all technologies."
            )

        logger.debug(f"[BOM] Found avg_boms for {tech}: {self.avg_boms[tech]}")

        # ── Step 5: Two-pass BOM construction ──────────────────────────────
        # Pass 1 — pre-scan: validate each MC and collect surviving entries
        surviving: dict[str, dict] = {}
        skipped_mcs: list[str] = []

        for feedstock, share_data in self.avg_boms[tech].items():
            normalized_feedstock = normalize_name(feedstock)

            # Skip carbon outputs
            if hasattr(self, "carbon_output_keys") and normalized_feedstock in self.carbon_output_keys:
                continue

            if not isinstance(share_data, dict):
                raise TypeError(
                    f"Invalid avg_boms schema for technology {tech!r}, feedstock {feedstock!r}: "
                    f"expected dict, got {type(share_data).__name__}"
                )

            # (a) Check input_effectiveness. Energy-like commodities (e.g. traded bio_pci
            # booked into materials by the TM write-back) are expected here — their cost
            # comes through the energy reconstruction instead.
            if normalized_feedstock not in input_effectiveness:
                skip_log = logger.debug if normalized_feedstock in ENERGY_FEEDSTOCK_KEYS else logger.warning
                skip_log(
                    "[AVG_BOM_DIAG] pf_match: tech=%s mc=%s skipped, not in input_effectiveness (available: %s)",
                    tech,
                    feedstock,
                    list(input_effectiveness.keys()),
                )
                skipped_mcs.append(feedstock)
                continue

            # (b) Find matching PrimaryFeedstock for (mc, reductant)
            matched_pf: PrimaryFeedstock | None = None
            if can_reconstruct_energy:
                for feed in feedstocks_for_tech:
                    if normalize_name(feed.metallic_charge) == normalized_feedstock and (
                        feed.reductant == most_common_reductant
                        or (
                            isinstance(feed.reductant, str)
                            and feed.reductant.lower() == (most_common_reductant or "").lower()
                        )
                        or (not most_common_reductant and not feed.reductant)
                    ):
                        matched_pf = feed
                        break
                if matched_pf is None:
                    logger.warning(
                        "[AVG_BOM_DIAG] pf_match: tech=%s mc=%s reductant=%s no PrimaryFeedstock match, skipping MC",
                        tech,
                        feedstock,
                        most_common_reductant,
                    )
                    skipped_mcs.append(feedstock)
                    continue

            # Extract input_share_pct and unit_cost
            input_share_pct = share_data.get("input_share_pct")
            if input_share_pct is None:
                if len(self.avg_boms[tech]) == 1 and "unit_cost" in share_data:
                    input_share_pct = 1.0
                    logger.warning(
                        "[BOM] avg_boms[%s][%s] missing input_share_pct; defaulting to 1.0 for single-input BOM",
                        tech,
                        feedstock,
                    )
                else:
                    raise KeyError(
                        f"avg_boms missing input_share_pct for technology {tech!r}, feedstock {feedstock!r}. "
                        f"share_data keys: {list(share_data.keys())}"
                    )

            unit_cost = share_data.get("unit_cost")
            if unit_cost is None:
                raise KeyError(
                    f"avg_boms missing unit_cost for technology {tech!r}, feedstock {feedstock!r}. "
                    f"share_data keys: {list(share_data.keys())}"
                )

            eff = input_effectiveness[normalized_feedstock]
            surviving[normalized_feedstock] = {
                "feedstock_key": feedstock,
                "pf": matched_pf,
                "input_share_pct": float(input_share_pct),
                "eff": eff,
                "unit_cost": float(unit_cost),
            }
            logger.debug(
                "[AVG_BOM_DIAG] pf_match: tech=%s mc=%s pf=%s eff=%.4f input_share=%.4f",
                tech,
                feedstock,
                matched_pf,
                eff,
                input_share_pct,
            )

        if skipped_mcs:
            logger.warning(
                "[AVG_BOM_DIAG] pf_match: tech=%s skipped_mcs=%s (%d of %d)",
                tech,
                skipped_mcs,
                len(skipped_mcs),
                len(skipped_mcs) + len(surviving),
            )

        if not surviving:
            logger.warning(
                "[AVG_BOM_DIAG] pf_match: tech=%s all metallic charges skipped, BOM will be empty",
                tech,
            )
        else:
            # Renormalise surviving input shares — skipped entries (e.g. bio_pci) must not
            # deflate the metallic-charge demands they were counted against.
            total_input_share = sum(data["input_share_pct"] for data in surviving.values())
            if total_input_share > 0:
                for data in surviving.values():
                    data["input_share_pct"] /= total_input_share

            # Compute output shares from input shares and effectiveness
            raw_output: dict[str, float] = {}
            for norm_mc, data in surviving.items():
                if data["eff"] and data["eff"] > 0:
                    raw_output[norm_mc] = data["input_share_pct"] / data["eff"]
                else:
                    raw_output[norm_mc] = 0.0

            total_raw = sum(raw_output.values())
            if total_raw == 0:
                logger.warning(
                    "[AVG_BOM_DIAG] output_shares: tech=%s total_raw=0, using equal shares",
                    tech,
                )
                equal_share = 1.0 / len(surviving)
                output_shares = {mc: equal_share for mc in surviving}
            else:
                output_shares = {mc: raw / total_raw for mc, raw in raw_output.items()}

            logger.debug(
                "[AVG_BOM_DIAG] output_shares: tech=%s shares=%s",
                tech,
                {mc: f"{s:.4f}" for mc, s in output_shares.items()},
            )

            # Pass 2 — build materials + accumulate energy
            energy_accum: dict[str, float] = {}

            for norm_mc, data in surviving.items():
                feedstock = data["feedstock_key"]
                eff = data["eff"]
                unit_cost_val = data["unit_cost"]
                matched_pf = data["pf"]
                o_share = output_shares[norm_mc]

                # Materials — input_share_pct is a share of fleet *input* tonnes, which
                # already embed eff; scale by output share so eff is applied exactly once
                material_demand = o_share * capacity * eff
                material_cost = unit_cost_val * material_demand

                bom_dict["materials"][feedstock] = {
                    "demand": material_demand,
                    "total_cost": material_cost,
                    "total_material_cost": material_cost,
                    "unit_cost": material_cost / capacity,
                    "unit_material_cost": material_cost / capacity,
                    "product_volume": capacity,
                    "demand_share_pct": o_share * eff,
                }
                logger.debug(
                    "[AVG_BOM_DIAG] material: tech=%s mc=%s o_share=%.4f eff=%.4f "
                    "demand=%.2f unit_cost_input=%.2f unit_cost_output=%.2f demand_share=%.4f",
                    tech,
                    feedstock,
                    o_share,
                    eff,
                    material_demand,
                    unit_cost_val,
                    material_cost / capacity,
                    o_share * eff,
                )

                # Energy — accumulate from PrimaryFeedstock
                if matched_pf is not None:
                    for carrier, intensity in (matched_pf.energy_requirements or {}).items():
                        norm_carrier = normalize_name(carrier)
                        if norm_carrier not in ENERGY_FEEDSTOCK_KEYS:
                            continue
                        contrib = o_share * float(intensity) * capacity
                        energy_accum[norm_carrier] = energy_accum.get(norm_carrier, 0.0) + contrib
                        logger.debug(
                            "[AVG_BOM_DIAG] energy_contrib: tech=%s mc=%s carrier=%s "
                            "o_share=%.4f intensity=%.4f contrib=%.2f",
                            tech,
                            feedstock,
                            norm_carrier,
                            o_share,
                            intensity,
                            contrib,
                        )

                    for sec_name, intensity in (matched_pf.secondary_feedstock or {}).items():
                        norm_sec = normalize_name(sec_name)
                        if norm_sec not in ENERGY_FEEDSTOCK_KEYS:
                            continue
                        contrib = o_share * float(intensity) * capacity
                        energy_accum[norm_sec] = energy_accum.get(norm_sec, 0.0) + contrib
                        logger.debug(
                            "[AVG_BOM_DIAG] energy_contrib_sec: tech=%s mc=%s carrier=%s "
                            "o_share=%.4f intensity=%.4f contrib=%.2f",
                            tech,
                            feedstock,
                            norm_sec,
                            o_share,
                            intensity,
                            contrib,
                        )

            # Build bom_dict["energy"] from accumulated demands
            for carrier, demand in energy_accum.items():
                price = energy_costs.get(carrier, 0.0)
                total_cost = price * demand
                if demand > 0 and price == 0:
                    logger.warning(
                        "[AVG_BOM_DIAG] energy: tech=%s carrier=%s has demand=%.2f but zero price in energy_costs",
                        tech,
                        carrier,
                        demand,
                    )
                bom_dict["energy"][carrier] = {
                    "demand": demand,
                    "total_cost": total_cost,
                    "unit_cost": price,
                    "product_volume": capacity,
                }

            total_material_cost = sum(m["total_cost"] for m in bom_dict["materials"].values())
            total_energy_cost = sum(e["total_cost"] for e in bom_dict["energy"].values())
            logger.info(
                "[AVG_BOM_DIAG] summary: tech=%s capacity=%.0f reductant=%s "
                "materials=%d (cost=%.0f) energy=%d (cost=%.0f) carriers=%s",
                tech,
                capacity,
                most_common_reductant,
                len(bom_dict["materials"]),
                total_material_cost,
                len(bom_dict["energy"]),
                total_energy_cost,
                list(bom_dict["energy"].keys()),
            )

        utilization = (
            self.avg_utilization.get(tech, {}).get("utilization_rate", 0.6)
            if hasattr(self, "avg_utilization") and self.avg_utilization
            else 0.6
        )
        logger.debug(f"[BOM] Final utilization: {utilization}")
        logger.debug(f"[BOM] Final BOM: {bom_dict}")
        logger.debug("[BOM] === End get_bom_from_avg_boms ===")

        # Ensure reductant is never None for return type consistency
        if most_common_reductant is None:
            most_common_reductant = ""
            logger.debug("[BOM] Reductant was still None at return, using empty string")

        return bom_dict, utilization, most_common_reductant, output_shares

    def calculate_average_commodity_price_per_region(
        self, world_plants: list[Plant], world_suppliers: list[Supplier], year: Year
    ) -> dict[Tuple, float]:
        """
        Calculate the average commodity price per iso3 based on the per unit costs.

        Args:
            world_plants: List of plants to include in calculation
            world_suppliers: List of suppliers to include in calculation
            year: The year for which to retrieve supplier production costs
        """
        logger = logging.getLogger(f"{__name__}.Environment.calculate_average_commodity_price_per_region")
        # Initialize the output dict: (commodity, region) -> average price
        average_commodity_price_per_region = {}
        # Temporary dicts to accumulate total cost and count per (commodity, region)
        sum_per_commodity_and_region: dict[Tuple[str, str], float] = {}
        count_per_commodity_and_region: dict[Tuple[str, str | None], int] = {}

        # 1) Process plant-level data
        for pl in world_plants:
            for fg in pl.furnace_groups:
                # Skip inactive or zero-capacity/utilization groups
                if fg.status.lower() not in self.config.active_statuses or fg.capacity == 0 or fg.utilization_rate == 0:
                    continue
                try:
                    unit_cost = fg.unit_production_cost  # cost per unit from the furnace group
                except (AttributeError, ValueError) as e:
                    # Skip if missing cost attribute or if BOM is missing for active furnace group
                    logger.warning(f"Skipping furnace group {fg.furnace_group_id} in price calculation: {e}")
                    continue
                if unit_cost is None:
                    continue  # skip if cost data is None

                # Determine the commodity and region key
                product = fg.technology.product  # e.g. 'steel', 'iron'
                region = pl.location.iso3  # ISO3 country code
                key = (product, region)

                # Initialize sums and counts if first encounter
                if key not in sum_per_commodity_and_region:
                    sum_per_commodity_and_region[key] = 0.0
                    count_per_commodity_and_region[key] = 0

                # Accumulate cost and count
                sum_per_commodity_and_region[key] += unit_cost
                count_per_commodity_and_region[key] += 1

        # 2) Process supplier-level data
        for supplier in world_suppliers:
            # Get production cost for the specified year
            production_cost = supplier.production_cost_by_year.get(year)
            if production_cost is None:
                continue

            # Normalize supplier commodity to string
            if isinstance(supplier.commodity, Commodities):
                product = supplier.commodity.value
            else:
                product = str(supplier.commodity)
            region = supplier.location.iso3
            key = (product, region)

            # Initialize sums and counts for new (commodity, region)
            if key not in sum_per_commodity_and_region:
                sum_per_commodity_and_region[key] = 0.0
                count_per_commodity_and_region[key] = 0

            # Accumulate cost and count
            sum_per_commodity_and_region[key] += production_cost
            count_per_commodity_and_region[key] += 1

        # 3) Compute averages
        for key, total_cost in sum_per_commodity_and_region.items():
            count = count_per_commodity_and_region[key]
            # Avoid division by zero (count should never be zero here)
            average_commodity_price_per_region[key] = total_cost / count

        # Store the result on self for later use and return
        self.average_commodity_price_per_region = average_commodity_price_per_region
        return average_commodity_price_per_region

    def load_allowed_transitions(
        self,
        tech_switches_csv: Path | None = None,
    ) -> None:
        """
        Populate self.allowed_furnace_transitions from a CSV file.

        The CSV is expected to have its first column as the row index,
        and subsequent columns as technology names with values 'YES' or 'NO'.
        Any technology ending in 'CCUS' will be excluded even if marked 'YES'.

        Important: this loader must NOT filter technologies by the current simulation year.
        Technology availability is year-dependent (via `technology_settings`) and is applied
        later when decisions are evaluated for a given year. If we filter at load time, any
        technology with `from_year > start_year` would be permanently removed from the
        transition graph, making future switches impossible (seen in binary/web runs).

        Args:

        Returns:
            None

        Side Effects:
            Populates self.allowed_furnace_transitions with a dictionary where keys are
            the first column values (origin) and values are lists of allowed technologies.
        """
        logger = logging.getLogger(f"{__name__}.Environment.load_allowed_transitions")
        from ..config import project_root

        self.allowed_furnace_transitions = {}
        if tech_switches_csv is None:
            # Use the default fixtures path
            path = project_root / "data" / "fixtures" / "tech_switches_allowed.csv"
        else:
            path = tech_switches_csv
        # Read all non-blank lines
        with open(path, "r") as f:
            lines = [line.strip() for line in f.readlines() if line.strip()]

        # Header row: drop the first cell (index name), keep tech names
        tech_names = lines[0].split(",")[1:]

        # Process each data row
        for row in lines[1:]:
            parts = row.split(",")
            origin = parts[0]
            flags = parts[1:]

            allowed = []
            for tech, flag in zip(tech_names, flags):
                if flag == "YES" and not tech.endswith("CCUS"):
                    # Keep original technology names with special characters.
                    # Only filter out technologies that are truly unknown to the configuration.
                    # Year-based availability is applied later (per-year) via `allowed_techs`.
                    normalized = normalize_code(tech)
                    assert self.config.technology_settings is not None
                    if normalized not in self.config.technology_settings:
                        logger.warning(f"Unknown technology in transitions CSV: {tech}")
                        continue
                    allowed.append(tech)

            # Store on the instance
            self.allowed_furnace_transitions[origin] = allowed

    def get_cached_distance(self, from_pc_name: str, to_pc_name: str, process_centers: list | None = None) -> float:
        """
        Get cached distance between two process centers.

        Args:
            from_pc_name: Source process center name
            to_pc_name: Destination process center name
            process_centers: Optional list of ProcessCenter objects for fallback computation

        Returns:
            Distance in km, or float('inf') if not computable
        """
        key = (from_pc_name, to_pc_name)

        # Check cache first
        if key in self._distance_cache:
            self._distance_cache_stats["hits"] += 1
            return self._distance_cache[key]

        # Cache miss - need to compute
        self._distance_cache_stats["misses"] += 1

        if process_centers is None:
            return float("inf")  # Can't compute without data

        # Find process centers (still O(n) but only on cache miss)
        from_pc = next((pc for pc in process_centers if pc.name == from_pc_name), None)
        to_pc = next((pc for pc in process_centers if pc.name == to_pc_name), None)

        if from_pc is None or to_pc is None:
            distance = float("inf")
        else:
            # Use existing method that checks ISO3 cache first
            distance = from_pc.distance_to_other_processcenter(to_pc)
            self._distance_cache_stats["computations"] += 1

        # Cache for future use
        self._distance_cache[key] = distance
        return distance

    def build_distance_function_for_trade_lp(self, process_centers: list):
        """
        Build a distance lookup function for TradeLPModel.

        This creates a closure that captures the process_centers list
        and can compute/cache distances as needed.

        Args:
            process_centers: List of ProcessCenter objects for this year

        Returns:
            Callable[[str, str], float] that returns distances
        """

        def distance_lookup(from_pc_name: str, to_pc_name: str) -> float:
            return self.get_cached_distance(from_pc_name, to_pc_name, process_centers)

        return distance_lookup

    def log_distance_cache_stats(self):
        """Log cache performance statistics."""
        import logging

        logger = logging.getLogger(f"{__name__}.Environment")

        stats = self._distance_cache_stats
        total = stats["hits"] + stats["misses"]
        if total > 0:
            hit_rate = (stats["hits"] / total) * 100
            logger.info(
                f"Distance cache stats: {stats['hits']} hits, {stats['misses']} misses "
                f"({hit_rate:.1f}% hit rate), {stats['computations']} distances computed, "
                f"{len(self._distance_cache)} entries cached"
            )

    def precompute_distances_for_hot_metal_check(self, process_centers: list, hot_metal_radius: float) -> set:
        """
        Precompute which PC pairs are within hot metal radius.
        Returns set of (from_name, to_name) tuples within radius.

        This converts the distance check from 100,000 float comparisons
        to 100,000 set membership checks (much faster).
        """
        import logging

        logger = logging.getLogger(f"{__name__}.Environment")

        within_radius = set()

        for from_pc in process_centers:
            for to_pc in process_centers:
                distance = self.get_cached_distance(from_pc.name, to_pc.name, process_centers=process_centers)
                if distance <= hot_metal_radius:
                    within_radius.add((from_pc.name, to_pc.name))

        logger.info(
            f"Pre-computed hot metal radius check: {len(within_radius)} pairs "
            f"within {hot_metal_radius}km radius out of {len(process_centers) ** 2} total pairs"
        )
        return within_radius

    def calculate_carbon_costs_of_furnace_groups(self, world_plants: list[Plant]) -> None:
        """
        Calculate the carbon costs for each furnace group based on the carbon costs per tonne of CO2.
        """

        if self.carbon_costs is None:
            raise ValueError("Carbon costs are not set in the environment.")

        for pl in world_plants:
            if pl.location.iso3 not in self.carbon_costs:
                continue
            year_key = Year(self.year)
            if year_key not in self.carbon_costs[pl.location.iso3]:
                continue
            for fg in pl.furnace_groups:
                fg.set_carbon_costs_for_emissions(
                    carbon_price=self.carbon_costs[pl.location.iso3][year_key],
                    chosen_emissions_boundary_for_carbon_costs=self.config.chosen_emissions_boundary_for_carbon_costs,
                )

    def pass_carbon_cost_series_to_plants(self, world_plants: list[Plant]) -> None:
        """
        Pass the carbon cost series to each plant in the world.
        This is used to set the carbon costs for each furnace group in the plant.
        """
        for pl in world_plants:
            if pl.location.iso3 not in self.carbon_costs:
                continue
            pl.set_carbon_cost_series(self.carbon_costs[pl.location.iso3])

    def calculate_average_material_costs(self, world_plants: list[Plant]) -> dict[str, dict[str, float]]:
        """
        Calculate the average material costs for each technology based on the furnace groups in the world plants.

        Args:
            world_plants (list[Plant]): List of all plants in the world.

        Returns:
            dict[str, float]: A dictionary with technology names as keys and their average material costs as values.
        """
        fgs = [fg for plant in world_plants for fg in plant.furnace_groups if fg.status in self.config.active_statuses]
        feedstocks = {}
        for fg in fgs:
            if fg.bill_of_materials and fg.bill_of_materials["materials"]:
                for material, cost_dict in fg.bill_of_materials["materials"].items():
                    if material not in feedstocks:
                        feedstocks[material] = {"total_cost": 0.0, "demand": 0.0}
                    feedstocks[material]["total_cost"] += (
                        float(cost_dict["total_cost"]) if cost_dict["total_cost"] is not None else 0.0
                    )
                    feedstocks[material]["demand"] += (
                        float(cost_dict["demand"]) if cost_dict["demand"] is not None else 0.0
                    )

        for material, data in feedstocks.items():
            feedstocks[material]["average_cost"] = data["total_cost"] / data["demand"] if data["demand"] > 0 else 0

        # TODO - @Marcus Fix - maybe in TMPAM
        if "hbi_low" not in feedstocks and "pellets_mid" in feedstocks:
            feedstocks["hbi_low"] = feedstocks["pellets_mid"].copy()
            feedstocks["hbi_low"]["average_cost"] *= 1.2
        if "hbi_mid" not in feedstocks and "dri_high" in feedstocks:
            feedstocks["hbi_mid"] = feedstocks["dri_high"].copy()

        self.average_material_cost = feedstocks
        return feedstocks

    def get_co2_need(self, tech: "Technology", capacity: float, reductant: str) -> float:
        """Annual tCO2 that a CCS furnace group of the given tech would add to storage.

        The single source of truth for need computation. All gates and counter writers
        must use this helper; inline ``co2_per_t * capacity * capacity_limit`` risks
        drift between the year-start scan and intra-year handler updates.

        Args:
            tech: Technology object carrying the ``dynamic_business_case`` BOM.
            capacity: Nameplate annual tonnes of product (``fg.capacity``).
            reductant: BOM-raw reductant string (case-preserved). See docstring on
                ``co2_stored_per_tonne_from_feedstocks`` for matching semantics.

        Returns:
            tCO2/yr. Multiplied by ``config.capacity_limit`` so the gate matches the
            LP's effective upper bound (availability factor), avoiding ~5% over-blocking.
        """
        per_t = tech.co2_stored_per_tonne_product(reductant)
        return per_t * capacity * self.config.capacity_limit

    def get_co2_need_by_name(self, tech_name: str, capacity: float, reductant: str) -> float:
        """Variant of get_co2_need that resolves the BOM by tech name via dynamic_feedstocks.

        Needed for call sites that have only a tech name (e.g. the P2 handler, which runs
        before a Technology object exists for the new tech). Falls back to a lowercased
        name lookup — mirrors the pattern used in ``finalise_iteration`` at [handlers.py].
        """
        dbc = self.dynamic_feedstocks.get(tech_name) or self.dynamic_feedstocks.get(tech_name.lower(), [])
        per_t = co2_stored_per_tonne_from_feedstocks(dbc, reductant)
        return per_t * capacity * self.config.capacity_limit

    def _co2_storage_limit_for_year(self, iso3: str, year: int) -> float:
        """CO2 storage limit (tCO2/yr) for a country at a given lookup year.

        Gate-specific year-lookup rule: returns the value at ``max(y in defined_years
        where y <= Y)``. Strict 0 if ``Y`` precedes the earliest defined year; carries
        the latest value forward beyond the last defined year. Returns 0 if no
        ``co2_stored`` constraint is defined for the country — reflects the physical
        reality that CCS cannot be built where storage is not assessed.

        Does NOT modify ``SecondaryFeedstockConstraint.get_constraint_for_year``: the
        LP depends on its exact-match-or-None semantics for trade allocation.
        """
        latest_year: int | None = None
        latest_value: float = 0.0
        earliest_year: int | None = None
        for constraint in self.secondary_feedstock_constraints:
            if constraint.secondary_feedstock_name != "co2_stored":
                continue
            if iso3 not in constraint.region_iso3s:
                continue
            for y, v in constraint.maximum_constraint_per_year.items():
                y_int = int(y)
                if earliest_year is None or y_int < earliest_year:
                    earliest_year = y_int
                if y_int <= year and (latest_year is None or y_int > latest_year):
                    latest_year = y_int
                    latest_value = v
        if latest_year is None:
            return 0.0
        return latest_value

    def scan_co2_storage_counters(self, uow) -> None:
        """Rebuild co2_storage_firm / co2_storage_reserved from current FurnaceGroup state.

        Invoked at year-start (after ``env.year`` is set, before Allocation/PAM/GEO run).
        Rebuilding from scratch is self-healing: FGs discarded, closed, or dropped in
        prior years automatically free up storage without requiring explicit unreserve
        bookkeeping.

        Status-keyed rule:

        - ``operating`` / ``operating pre-retirement`` / ``construction`` → firm, need
          computed from ``fg.technology`` + ``fg.chosen_reductant``.
        - ``operating switching technology`` / ``construction switching technology``
          (committed tech switch in flight) → firm, need computed from
          ``fg.future_switch_cmd.technology_name`` + ``fg.chosen_reductant`` (old
          reductant; the new FG hasn't run its reductant-pick economics yet).
        - ``announced`` → reserved × ``config.co2_storage_reserved_discount_factor``.
        - ``considered`` / ``discarded`` / ``closed`` → excluded.

        All counter writers (this scan + intra-year handlers) use ``fg.chosen_reductant``
        so the rebuild is bit-identical to the sum of intra-year increments — handler
        hooks and the scan never disagree.

        Iterates in deterministic order (sorted by iso3, plant_id, fg_id) so log traces
        are reproducible independent of the per-iteration random seed.
        """
        logger = logging.getLogger(f"{__name__}.Environment.scan_co2_storage_counters")

        self.co2_storage_firm = {}
        self.co2_storage_reserved = {}
        d = self.config.co2_storage_reserved_discount_factor

        entries = []
        for plant in uow.plants.list():
            for fg in plant.furnace_groups:
                entries.append((plant.location.iso3, plant.plant_id, fg.furnace_group_id, plant, fg))
        entries.sort(key=lambda e: (e[0], e[1], e[2]))

        for iso3, _plant_id, _fg_id, _plant, fg in entries:
            status = fg.status.lower()
            if status in ("considered", "discarded", "closed"):
                continue

            if status in ("operating switching technology", "construction switching technology"):
                if fg.future_switch_cmd is None:
                    continue
                need = self.get_co2_need_by_name(fg.future_switch_cmd.technology_name, fg.capacity, fg.chosen_reductant)
                if need == 0.0:
                    continue
                self.co2_storage_firm[iso3] = self.co2_storage_firm.get(iso3, 0.0) + need
                continue

            if status in ("operating", "operating pre-retirement", "construction"):
                need = self.get_co2_need(fg.technology, fg.capacity, fg.chosen_reductant)
                if need == 0.0:
                    if fg.is_ccs_or_ccu and not fg.technology.dynamic_business_case:
                        logger.warning(
                            f"CCS furnace group {fg.furnace_group_id} (tech={fg.technology.name}, "
                            f"status={fg.status}) has empty dynamic_business_case — contributing 0 to firm. "
                            f"Check env.dynamic_feedstocks population for this tech name."
                        )
                    continue
                self.co2_storage_firm[iso3] = self.co2_storage_firm.get(iso3, 0.0) + need
                continue

            if status == "announced":
                need = self.get_co2_need(fg.technology, fg.capacity, fg.chosen_reductant)
                if need == 0.0:
                    if fg.is_ccs_or_ccu and not fg.technology.dynamic_business_case:
                        logger.warning(
                            f"Announced CCS furnace group {fg.furnace_group_id} (tech={fg.technology.name}) "
                            f"has empty dynamic_business_case — contributing 0 to reserved. "
                            f"Check env.dynamic_feedstocks population for this tech name."
                        )
                    continue
                self.co2_storage_reserved[iso3] = self.co2_storage_reserved.get(iso3, 0.0) + d * need
                continue

        countries_with_state = set(self.co2_storage_firm) | set(self.co2_storage_reserved)
        for c in sorted(countries_with_state):
            firm = self.co2_storage_firm.get(c, 0.0)
            reserved = self.co2_storage_reserved.get(c, 0.0)
            limit = self._co2_storage_limit_for_year(c, int(self.year))
            utilisation_pct = 100.0 * (firm + reserved) / limit if limit > 0 else float("nan")
            logger.info(
                f"[CO2 SCAN] year={self.year} iso3={c} "
                f"firm={firm:,.0f} reserved={reserved:,.0f} limit={limit:,.0f} "
                f"utilisation_pct={utilisation_pct:.1f}"
            )

    def get_co2_headroom(self, iso3: str, year: int, own_reserved_contribution: float = 0.0) -> float:
        """Unified CO2 storage headroom read for all five gates.

        ``headroom = limit - firm - reserved + own_reserved_contribution``.

        ``own_reserved_contribution`` is ``d * own_need`` when the FG being evaluated is
        itself already in the reserved bucket (P1, status == announced). Every other
        gate defaults to 0. Exempting the FG's own reserved slot prevents P1 from
        double-counting its own discounted contribution against itself.
        """
        limit = self._co2_storage_limit_for_year(iso3, year)
        if limit == 0.0:
            return 0.0
        firm = self.co2_storage_firm.get(iso3, 0.0)
        reserved = self.co2_storage_reserved.get(iso3, 0.0)
        return limit - firm - reserved + own_reserved_contribution

    def co2_storage_diagnostics(self, iso3: str, year: int) -> tuple[float, float, float]:
        """Return (firm, reserved, limit) snapshot for [CO2 GATE] log lines.

        Pure read helper that surfaces the three counter components used in gate log
        lines without passing the full Environment into gate methods. Threaded as a
        narrow callable alongside ``get_co2_headroom`` / ``get_co2_need`` to preserve
        the parameter convention used elsewhere in the PAM pipeline.

        Args:
            iso3: Country ISO3.
            year: Lookup year for the limit (the operating-start year for the gate
                being logged — ``current_year + construction_time`` for P1/P2/P3).

        Returns:
            ``(firm, reserved, limit)`` in tCO2/yr. Firm/reserved default to 0.0 when
            the country has no entry in the counters; limit follows the same year-lookup
            rule as ``_co2_storage_limit_for_year`` (strict 0 pre-earliest,
            carry-forward past-latest, 0 when no ``co2_stored`` constraint defined).
        """
        firm = self.co2_storage_firm.get(iso3, 0.0)
        reserved = self.co2_storage_reserved.get(iso3, 0.0)
        limit = self._co2_storage_limit_for_year(iso3, year)
        return firm, reserved, limit

    def relevant_secondary_feedstock_constraints(self):
        """Returns the relevant secondary feedstock constraints for the current year."""
        if not self.secondary_feedstock_constraints:
            return None

        relevant_constraints = defaultdict(dict)
        for constraint in self.secondary_feedstock_constraints:
            if constraint.has_constraint_for_year(self.year):
                commodity = constraint.secondary_feedstock_name
                region_tuple = constraint.get_region_tuple()
                constraint_value = constraint.get_constraint_for_year(self.year)

                if commodity not in relevant_constraints:
                    relevant_constraints[commodity] = {}
                relevant_constraints[commodity][region_tuple] = constraint_value

        return dict(relevant_constraints)

    def initialize_virgin_iron_demand(
        self, world_suppliers_list: list[Supplier], steel_demand_dict: dict[str, dict[Year, Volumes]]
    ):
        """
        Initialize the VirginIronDemand object with precalculated demand for all years.
        This should be called once at the beginning of the simulation.
        """
        logger = logging.getLogger(f"{__name__}.Environment.initialize_virgin_iron_demand")
        self.virgin_iron_demand = VirginIronDemand(
            world_suppliers=world_suppliers_list,
            steel_demand_dict=steel_demand_dict,
            dynamic_feedstocks=self.dynamic_feedstocks,
        )
        logger.info("[VIRGIN IRON DEMAND]: Precalculation complete for all years")

    @property
    def allowed_techs(self) -> dict[Year, list[str]]:
        """
        Get allowed technologies by year based on technology settings. Returns RAW technology names (e.g., "DRI-H2-EAF")
        not normalized keys, to match what tech_to_product contains and what get_list_of_allowed_techs_for_target_year expects.
        Computation only happens once per Environment instance.

        Returns:
            Dict mapping years to lists of RAW technology names
        """
        # Simple memoization - cache the result after first computation
        if self._cached_allowed_techs is not None:
            return self._cached_allowed_techs

        from steelo.core.parse import normalize_code

        allowed_by_year: dict[Year, list[str]] = {}

        # Generate for FULL simulation range (not from current year!)
        # Note: add extra [+ consideration_time + 2] to be able to consider new plants until the end of the simulation
        # 1st +1 beacuse of fixed 1y announcement time and 2nd +1 because range is exclusive at the end
        for year in range(self.config.start_year, self.config.end_year + self.config.consideration_time + 2):
            allowed_techs = []

            # APPROACH: Check if tech_to_product is loaded, use it; otherwise derive from settings
            if hasattr(self, "technology_to_product") and self.technology_to_product:
                # ✅ tech_to_product is loaded - use the reverse mapping approach
                for raw_tech_name in self.technology_to_product.keys():
                    # Normalize raw name and check if allowed
                    normalized_key = normalize_code(raw_tech_name)
                    # technology_settings is guaranteed to be populated by __post_init__
                    assert self.config.technology_settings is not None
                    if normalized_key in self.config.technology_settings:
                        if is_technology_allowed(self.config.technology_settings, normalized_key, year):
                            allowed_techs.append(raw_tech_name)
            else:
                # ⚠️ tech_to_product not loaded yet - need to reconstruct raw names from normalized keys
                # This is a fallback that assumes we can reverse-engineer the raw names
                # For now, we'll defer to when tech_to_product is loaded or raise an error
                raise RuntimeError(
                    "allowed_techs accessed before technology_to_product is loaded. "
                    "Ensure CAPEX data loading happens before accessing allowed_techs."
                )

            allowed_by_year[Year(year)] = allowed_techs

        # Cache the result for future calls
        self._cached_allowed_techs = allowed_by_year
        return allowed_by_year


class CommodityAllocations:
    """Container for commodity trade flows between sources and destinations.

    Represents the complete allocation solution for a single commodity (e.g., steel, iron,
    scrap) showing which sources ship to which destinations, the volumes traded, and the
    associated costs. Used to store and query results from trade optimization models.

    The allocation structure is a nested dictionary mapping:
        Source → Destination → Volume

    Where:
        - Source can be a (Plant, FurnaceGroup) tuple or a Supplier
        - Destination can be a DemandCenter or (Plant, FurnaceGroup) tuple
        - Volume is a Volumes object representing tonnes shipped

    This class provides:
        - Allocation management (add, query by source/destination)
        - Cost tracking per allocation route
        - Total volume calculations
        - Demand validation against DemandCenters
        - Transport emissions calculation
        - Cost curve generation from allocations

    Type Aliases:
        Source: Union[Tuple[Plant, FurnaceGroup], Supplier]
            - (Plant, FurnaceGroup): Production from a specific furnace group
            - Supplier: External commodity supplier (e.g., iron ore mines, scrap dealers)

        Destination: Union[DemandCenter, Tuple[Plant, FurnaceGroup]]
            - DemandCenter: Final demand location (e.g., regional steel demand)
            - (Plant, FurnaceGroup): Intermediate demand (feedstock for another furnace)

    Args:
        commodity: Commodity name (e.g., "steel", "iron", "scrap_steel", "iron_ore").
        allocations: Nested dict of {Source: {Destination: Volumes}} representing trade flows.
        allocation_costs: Optional nested dict of {Source: {Destination: cost}} for route costs
            in USD per tonne. Defaults to empty dict.

    Attributes:
        commodity: Name of the commodity being allocated.
        allocations: Complete allocation mapping (nested dictionary).
        allocation_costs: Cost per allocation route.
        cost_curve: Supply cost curve data structure, populated by create_cost_curve().
            Format depends on source type (furnace groups vs suppliers).
        price: Market clearing price for this commodity in USD/tonne, set by create_cost_curve().

    Example:
        >>> # Create allocations for steel
        >>> allocations = CommodityAllocations(
        ...     commodity="steel",
        ...     allocations={
        ...         (plant1, fg1): {demand_center1: Volumes(1000)},
        ...         supplier1: {(plant2, fg2): Volumes(500)}
        ...     }
        ... )
        >>> # Query allocations from a source
        >>> destinations = allocations.get_allocations_from((plant1, fg1))
        >>> # Calculate total volumes
        >>> total = allocations.calculate_total_volumes()
        >>> # Validate demand is satisfied
        >>> is_valid = allocations.validate_demand_is_met(year, demand_centers)

    Notes:
        - Volumes are summed across all routes for aggregate calculations.
        - Demand validation checks if all DemandCenters receive required volumes.
        - Transport emissions calculated separately via update_transport_emissions().
        - Cost curves used for market price determination in economic equilibrium.
        - Supports both production-based sources (furnace groups) and external suppliers.
    """

    Source = Union[Tuple["Plant", "FurnaceGroup"], "Supplier"]
    Destination = Union["DemandCenter", Tuple["Plant", "FurnaceGroup"]]

    def __init__(
        self,
        commodity: str,
        allocations: Dict[Source, Dict[Destination, "Volumes"]],
        allocation_costs: Dict[Source, Dict[Destination, float]] = {},
    ) -> None:
        self.commodity = commodity
        self.allocations = allocations  # Nested dictionary: {Source: {Destination: Volumes}}
        self.allocation_costs = allocation_costs
        self.cost_curve: Union[dict[str, list[dict[str, float]]], list[dict[str, Any]]] = []
        self.price: float = 0.0

    def add_allocation(self, source: Source, destination: Destination, volume: "Volumes") -> None:
        """Adds or updates an allocation."""
        if source not in self.allocations:
            self.allocations[source] = {}
        self.allocations[source][destination] = volume

    def add_cost(self, source: Source, destination: Destination, cost: float) -> None:
        """Adds or updates an allocation cost."""
        if source not in self.allocation_costs:
            self.allocation_costs[source] = {}
        self.allocation_costs[source][destination] = cost

    def get_cost(self, source: Source, destination: Destination) -> float:
        """Returns the cost of an allocation."""
        return self.allocation_costs.get(source, {}).get(destination, 0)

    def get_allocations_from(self, source: Source) -> Dict[Destination, "Volumes"]:
        """Returns all allocations from a given source."""
        return self.allocations.get(source, {})

    def get_allocations_to(self, destination: Destination) -> Dict[Source, "Volumes"]:
        """Returns all allocations to a given destination."""
        to_allocations = {}
        for source in self.allocations:
            if destination in self.allocations[source]:
                volume = self.allocations[source][destination]
                to_allocations[source] = volume
        return to_allocations

    def get_all_allocations(self) -> Dict[Source, Dict[Destination, "Volumes"]]:
        """Returns the entire allocation mapping."""
        return self.allocations

    def calculate_total_volumes(self) -> Volumes:
        total = sum(sum(alloc.values(), Volumes(0)) for alloc in self.allocations.values())
        return Volumes(total)

    def validate_demand_is_met(self, year: Year, demand_centers: list[DemandCenter]) -> bool:
        logger = logging.getLogger(f"{__name__}.CommodityAllocations.validate_demand_is_met")
        demand_met = True
        for dc in demand_centers:
            supplied_demand = sum(self.get_allocations_to(dc).values())
            needed_demand = dc.demand_by_year.get(year, Volumes(0))
            if (
                (supplied_demand + Volumes(100)) < needed_demand
            ):  # tolerance to avoid floating point issues and issues with transport problem integer constraint
                demand_met = False
                logger.warning(
                    f"Demand not met for {dc} in {year}: {needed_demand:,.2f} needed, {supplied_demand:,.2f} supplied."
                )
        return demand_met

    def __repr__(self) -> str:
        return f"{len(self.allocations)} CommodityAllocations"

    def update_transport_emissions(self, transport_emissions: list[TransportKPI]) -> None:
        """Calculate and set transport emissions for all commodity allocations.

        For each allocation from source to destination, looks up the transport emission
        factor based on origin/destination ISO3 codes and commodity type, then calculates
        total transport emissions as: emission_factor × volume.

        Args:
            transport_emissions: List of TransportKPI objects containing GHG emission factors
                (tCO2e per tonne-km or per tonne) for different routes and commodities.

        Side Effects:
            - Resets all furnace group transport_emissions to 0.0.
            - Sets fg.transport_emissions for each destination furnace group to the sum of
              transport emissions from all incoming allocations.

        Notes:
            - Uses nested lookup function to find emission factors by (from_iso3, to_iso3, commodity).
            - Returns 0.0 emission factor if no matching TransportKPI found.
            - Only updates destinations that are (Plant, FurnaceGroup) tuples.
            - Accumulates emissions across multiple sources to same destination.
        """
        # First, set all to 0:
        for source in self.allocations:
            for destination in self.allocations[source]:
                if isinstance(destination, tuple):
                    plant, fg = destination
                    fg.transport_emissions = 0.0

        # Create a lookup function for transport emissions
        def get_transport_emission_factor(from_iso3: str, to_iso3: str, commodity: str) -> float:
            for te in transport_emissions:
                if te.reporter_iso == from_iso3 and te.partner_iso == to_iso3 and te.commodity == commodity:
                    return te.ghg_factor
            return 0.0

        # now update the transport emissions:
        for source in self.allocations:
            for destination in self.allocations[source]:
                from_iso3 = None
                to_iso3 = None

                # Determine source ISO3
                if isinstance(source, tuple):
                    from_plant, _ = source
                    from_iso3 = from_plant.location.iso3
                elif hasattr(source, "location"):  # Supplier case
                    from_iso3 = source.location.iso3

                # Determine destination ISO3 and apply emissions
                if isinstance(destination, tuple):
                    to_plant, to_fg = destination
                    to_iso3 = to_plant.location.iso3

                    if from_iso3 and to_iso3:
                        emission_factor = get_transport_emission_factor(from_iso3, to_iso3, self.commodity)
                        to_fg.transport_emissions += emission_factor * self.allocations[source][destination]

    def create_cost_curve(self, environment: Environment):
        total_demand = self.calculate_total_volumes()
        sources = self.allocations.keys()
        furnace_groups = []
        for source in sources:
            if isinstance(source, tuple):  # if the source is a plant, fg tuple then this is the case for all sources
                _, fg = source
                furnace_groups.append(fg)

        # if the commodity comes from furnace groups then we already have a function for that:
        if len(furnace_groups) >= 1:
            fg_cost_curve = environment.generate_cost_curve(world_furnace_groups=furnace_groups, lag=0)
            price = environment.extract_price_from_costcurve(demand=total_demand, product=self.commodity)
            self.price = price
            self.cost_curve = fg_cost_curve
            return

        # if not, so if the sources are suppliers then we need to make one:
        # sort the suppliers by sourcing costs:
        # Filter sources that are instances of Supplier
        supplier_sources = [s for s in sources if isinstance(s, Supplier)]
        # Sort them by production_cost_by_year
        sorted_suppliers = sorted(
            supplier_sources, key=lambda sup: sup.production_cost_by_year.get(environment.year, 0.0)
        )
        cummultative_capacity = Volumes(0)
        supplier_cost_curve: list[dict[str, Any]] = []
        supplier_price = 0.0
        price_is_set = False
        for supplier in sorted_suppliers:
            cummultative_capacity = Volumes(cummultative_capacity + supplier.capacity_by_year[environment.year])
            production_cost = supplier.production_cost_by_year.get(environment.year, 0.0)
            supplier_cost_curve.append(
                {
                    "cumulative_capacity": cummultative_capacity,
                    "production_cost": production_cost,
                }
            )
            if total_demand <= cummultative_capacity and not price_is_set:
                supplier_price = production_cost
        self.price = supplier_price
        self.cost_curve = supplier_cost_curve


@dataclass
class LegalProcessConnector:
    """Represents a legal connection between two technologies in the steel production flow."""

    from_technology_name: str
    to_technology_name: str
