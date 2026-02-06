import logging
from typing import TYPE_CHECKING, TypedDict, Any
import math

from steelo.domain.calculate_emissions import (
    calculate_emissions,
    calculate_emissions_cost_series,
    materiall_bill_business_case_match,
)

# Constants moved to domain.constants for clean architecture
if TYPE_CHECKING:
    from steelo.domain.models import PrimaryFeedstock, Subsidy, CountryMappingService, TechnologyEmissionFactors
from collections import Counter
from steelo.domain.constants import KG_TO_T, MWH_TO_KWH, Year

SECONDARY_FEEDSTOCKS_REQUIRING_KG_TO_T_CONVERSION = {
    "coking_coal",  # Stored in kg/t in dynamic business cases, priced in USD/t
    "bio_pci",
}

# Normalized keys that represent genuine energy carriers. Any secondary-feedstock entry whose normalized
# name is not in this set is treated as a material input and must not be double-counted as energy.
ENERGY_FEEDSTOCK_KEYS = {
    "electricity",
    "hydrogen",
    "natural_gas",
    "coking_coal",
    "coal",
    "coke",
    "pci",
    "bio_pci",
    "heat",
    "flexible",
    "bf_gas",
    "bof_gas",
    "cog",
    "steam",
    "burnt_dolomite",
    "burnt_lime",
    "lime",
    "olivine",
}


def _normalize_energy_key(name: str) -> str:
    return str(name).lower().replace(" ", "_").replace("-", "_")


def _coerce_to_float(value: Any) -> float | None:
    """
    Best-effort conversion of heterogeneous numeric payloads (floats, ints, stringified numbers, nested dicts)
    into a float. Returns None when conversion is not possible.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    if isinstance(value, dict):
        for candidate_key in ("Value", "value", "amount", "Amount"):
            if candidate_key in value:
                nested_value = _coerce_to_float(value[candidate_key])
                if nested_value is not None:
                    return nested_value
        return None
    return None


def filter_subsidies_for_year(subsidies: list["Subsidy"], year: "Year") -> list["Subsidy"]:
    """
    Filter subsidies to only include those active in the specified year.

    A subsidy is considered active if the year falls within its validity period (inclusive of both
    start_year and end_year).

    Args:
        subsidies: List of Subsidy objects with start_year and end_year attributes.
        year: The year to check against.

    Returns:
        List of subsidies that are active in the specified year. Returns empty list if no subsidies
        are provided or if none are active.

    Note:
        For collecting subsidies across multiple years, use `collect_active_subsidies_over_period` instead.
    """
    if not subsidies:
        return []
    return [subsidy for subsidy in subsidies if year >= subsidy.start_year and year <= subsidy.end_year]


def collect_active_subsidies_over_period(
    subsidies: list["Subsidy"], start_year: "Year", end_year: "Year"
) -> list["Subsidy"]:
    """
    Collect unique subsidies active during any year in [start_year, end_year).

    Args:
        subsidies: List of Subsidy objects with start_year and end_year attributes.
        start_year: First year of the period (inclusive).
        end_year: Last year of the period (exclusive, matching Python range convention).

    Returns:
        List of unique subsidies active during any year in the period.
    """
    active: set["Subsidy"] = set()
    for year in range(start_year, end_year):
        active.update(filter_subsidies_for_year(subsidies, Year(year)))
    return list(active)


def calculate_energy_price_with_subsidies(
    energy_price: float,
    energy_subsidies: list["Subsidy"],
) -> float:
    """
    Apply subsidies to an energy price.

    Args:
        energy_price: Base price before subsidy (USD/t for H2, USD/kWh for electricity)
        energy_subsidies: List of active subsidies for this energy type

    Returns:
        float: Subsidised price (floored at 0)
    """
    total_subsidy = 0.0
    for subsidy in energy_subsidies:
        if subsidy.subsidy_type == "absolute":
            total_subsidy += subsidy.subsidy_amount
        elif subsidy.subsidy_type == "relative":
            total_subsidy += energy_price * subsidy.subsidy_amount
    return max(0.0, energy_price - total_subsidy)


def get_subsidised_energy_costs(
    energy_costs: dict[str, float],
    hydrogen_subsidies: list["Subsidy"],
    electricity_subsidies: list["Subsidy"],
) -> tuple[dict[str, float], dict[str, float]]:
    """
    Create energy costs dict with subsidies applied.

    Args:
        energy_costs: Original energy costs dict {carrier: price}
        hydrogen_subsidies: Active H2 subsidies
        electricity_subsidies: Active electricity subsidies

    Returns:
        tuple: (subsidised_costs, no_subsidy_prices)
        - subsidised_costs: dict with subsidised prices (to use in calculations)
        - no_subsidy_prices: {"hydrogen": original_h2, "electricity": original_elec}
    Raises:
        KeyError: If hydrogen subsidies provided but "hydrogen" not in energy_costs,
                  or electricity subsidies provided but "electricity" not in energy_costs.
    """
    import copy

    subsidised = copy.copy(energy_costs)

    # Validate required keys exist when subsidies are provided
    if hydrogen_subsidies and "hydrogen" not in energy_costs:
        raise KeyError(
            f"Hydrogen subsidies provided but 'hydrogen' key not found in energy_costs. "
            f"Available keys: {list(energy_costs.keys())}"
        )
    if electricity_subsidies and "electricity" not in energy_costs:
        raise KeyError(
            f"Electricity subsidies provided but 'electricity' key not found in energy_costs. "
            f"Available keys: {list(energy_costs.keys())}"
        )

    h2_price = energy_costs.get("hydrogen", 0.0)
    elec_price = energy_costs.get("electricity", 0.0)

    no_subsidy_prices = {
        "hydrogen": h2_price,
        "electricity": elec_price,
    }

    if hydrogen_subsidies and h2_price > 0:
        subsidised["hydrogen"] = calculate_energy_price_with_subsidies(h2_price, hydrogen_subsidies)

    if electricity_subsidies and elec_price > 0:
        subsidised["electricity"] = calculate_energy_price_with_subsidies(elec_price, electricity_subsidies)

    return subsidised, no_subsidy_prices


def calculate_cost_breakdown_by_feedstock(
    bill_of_materials: dict[str, dict[str, dict[str, float]]],
    chosen_reductant: str,
    dynamic_business_cases: list["PrimaryFeedstock"],
    energy_costs: dict[str, float],
    energy_vopex_breakdown_by_input: dict[str, dict[str, float]] | None = None,
) -> dict:
    """
    Calculate detailed cost breakdown by feedstock type for a furnace group.

    Uses actual BOM energy and distributes it proportionally across feedstocks based on their
    energy intensity (from dynamic business cases) and usage share (demand_share_pct).

    Args:
        bill_of_materials (dict[str, dict[str, dict[str, float]]]): Nested dictionary containing
            materials and energy data.
        chosen_reductant (str): Selected reductant type (e.g., 'coke', 'natural_gas') to filter
            business cases.
        dynamic_business_cases (list[PrimaryFeedstock]): List of primary feedstock options with energy requirements.
        energy_costs (dict[str, float]): Energy costs by type (kept for backward compatibility).
        energy_vopex_breakdown_by_input (dict[str, dict[str, float]] | None): Unused parameter for compatibility.

    Returns:
        dict: Dictionary with cost breakdown by feedstock type. Each feedstock key maps to a dictionary containing:
            - Material cost (incl. transport and tariffs)
            - Individual energy carrier costs (weighted proportionally)
            Returns empty dict if no matching feedstocks are found.
    """
    breakdown: dict[str, dict[str, float]] = {}

    if not bill_of_materials or not bill_of_materials.get("materials"):
        return breakdown

    # Rename map to match STANDARD_COST_BREAKDOWN_COLUMNS
    rename_map = {
        "coking_coal": "coking coal",
        "burnt_dolomite": "burnt dolomite",
        "burnt_lime": "fluxes",
        "burnt lime": "fluxes",
        "lime": "fluxes",
        "bio_pci": "bio-pci",
        "natural_gas": "natural gas",
        "bf_gas": "bf gas",
        "bof_gas": "bof gas",
        "flexible": "heat",
    }

    # Get BOM energy (same source as calculate_variable_opex)
    bom_energy = bill_of_materials.get("energy", {})

    # Calculate energy intensity per carrier for each feedstock from dynamic business case
    # Structure: {feedstock: {carrier: amount}}
    feedstock_carrier_intensities: dict[str, dict[str, float]] = {}

    for dbc in dynamic_business_cases:
        if dbc.reductant != chosen_reductant:
            continue

        dbc_lower = dbc.metallic_charge.lower()
        if dbc_lower not in bill_of_materials["materials"]:
            continue

        carrier_amounts: dict[str, float] = {}

        # Get energy requirements by carrier
        for energy_key, amount in (dbc.energy_requirements or {}).items():
            normalized_key = _normalize_energy_key(energy_key)
            if normalized_key in ENERGY_FEEDSTOCK_KEYS:
                carrier_amounts[normalized_key] = carrier_amounts.get(normalized_key, 0.0) + (
                    _coerce_to_float(amount) or 0.0
                )

        # Add secondary feedstock by carrier
        for secondary_key, amount in (dbc.secondary_feedstock or {}).items():
            normalized_secondary = _normalize_energy_key(secondary_key)
            if normalized_secondary in ENERGY_FEEDSTOCK_KEYS:
                converted_amount = (
                    (amount * KG_TO_T)
                    if normalized_secondary in SECONDARY_FEEDSTOCKS_REQUIRING_KG_TO_T_CONVERSION
                    else amount
                )
                carrier_amounts[normalized_secondary] = (
                    carrier_amounts.get(normalized_secondary, 0.0) + converted_amount
                )

        feedstock_carrier_intensities[dbc_lower] = carrier_amounts

    # Build cost breakdown for each feedstock
    for dbc in dynamic_business_cases:
        metallic_charge_lower = dbc.metallic_charge.lower()

        if metallic_charge_lower not in bill_of_materials["materials"]:
            continue
        if dbc.reductant != chosen_reductant:
            continue

        material_entry = bill_of_materials["materials"][metallic_charge_lower].copy()
        feed_breakdown = material_entry.copy()

        # Add material cost (excludes current step's energy)
        material_unit_cost = _coerce_to_float(material_entry.get("unit_material_cost")) or 0.0
        feed_breakdown["material cost (incl. transport and tariffs)"] = material_unit_cost

        # Distribute BOM energy proportionally if available
        if bom_energy and metallic_charge_lower in feedstock_carrier_intensities:
            demand_share = _coerce_to_float(material_entry.get("demand_share_pct", 1.0)) or 1.0
            feedstock_carriers = feedstock_carrier_intensities[metallic_charge_lower]

            # Distribute BOM energy carrier-by-carrier
            for carrier, carrier_data in bom_energy.items():
                if not isinstance(carrier_data, dict):
                    continue

                carrier_unit_cost = _coerce_to_float(carrier_data.get("unit_cost")) or 0.0
                if carrier_unit_cost == 0:
                    continue

                normalized_carrier = _normalize_energy_key(carrier)

                # Check if this feedstock uses this carrier
                feedstock_carrier_intensity = feedstock_carriers.get(normalized_carrier, 0.0)
                if feedstock_carrier_intensity == 0:
                    # This feedstock doesn't use this carrier, skip it
                    continue

                # Calculate weighted intensity for THIS CARRIER across all feedstocks that use it
                total_weighted_intensity_for_carrier = 0.0
                for fs_lower, fs_carriers in feedstock_carrier_intensities.items():
                    if fs_lower not in bill_of_materials["materials"]:
                        continue

                    fs_carrier_intensity = fs_carriers.get(normalized_carrier, 0.0)
                    if fs_carrier_intensity > 0:
                        fs_demand_share = (
                            _coerce_to_float(bill_of_materials["materials"][fs_lower].get("demand_share_pct", 1.0))
                            or 1.0
                        )
                        total_weighted_intensity_for_carrier += fs_carrier_intensity * fs_demand_share

                if total_weighted_intensity_for_carrier > 0:
                    # This feedstock's share of THIS carrier's cost
                    carrier_weight = (demand_share * feedstock_carrier_intensity) / total_weighted_intensity_for_carrier

                    target_key = rename_map.get(normalized_carrier, normalized_carrier.replace("_", " "))

                    # Weighted energy cost for this feedstock and carrier
                    weighted_cost = carrier_unit_cost * carrier_weight
                    feed_breakdown[target_key] = feed_breakdown.get(target_key, 0.0) + weighted_cost

        breakdown[metallic_charge_lower] = feed_breakdown

    return breakdown


def calculate_cost_breakdown(
    bill_of_materials: dict[str, dict[str, dict[str, float]]],
    production: float,
    dynamic_business_cases: list["PrimaryFeedstock"],
    energy_costs: dict[str, float],
    chosen_reductant: str,
) -> dict:
    """
    Calculate the cost breakdown for a given furnace group.

    Iterates through dynamic business cases to find matching feedstocks and calculates total costs including
    material costs and energy costs. All costs are normalized per unit of production.

    Args:
        bill_of_materials (dict[str, dict[str, dict[str, float]]]): Nested dictionary containing the bill of materials
            with demand and total cost information.
        production (float): The total production quantity.
        dynamic_business_cases (list[PrimaryFeedstock]): List of primary feedstock options with energy requirements,
            secondary feedstock needs, and metallic charge specifications.
        energy_costs (dict[str, float]): Energy costs by type (e.g., {'electricity': 50.0, 'natural_gas': 30.0}).
        chosen_reductant (str): Selected reductant type (e.g., 'natural_gas') to filter business cases.

    Returns:
        dict: Dictionary containing the cost breakdown per unit of production. Returns empty dict if no matching
            feedstocks are found or if material demand is not available.
    """
    breakdown = {}
    if bill_of_materials and bill_of_materials["materials"]:
        # Iterate through each dynamic business case to find matching feedstocks
        for dbc in dynamic_business_cases:
            # Match business case to material bill by metallic charge and reductant
            if dbc.metallic_charge.lower() in bill_of_materials["materials"] and dbc.reductant == chosen_reductant:
                # Get the primary output product from this business case
                output = next(iter(dbc.get_primary_outputs()))

                # Extract material demand - return empty dict if not available
                if "demand" in bill_of_materials["materials"][dbc.metallic_charge.lower()]:
                    material_demand = bill_of_materials["materials"][dbc.metallic_charge.lower()]["demand"]
                else:
                    return {}

                # Get total material cost for this feedstock
                material_cost = bill_of_materials["materials"][dbc.metallic_charge.lower()]["total_cost"]

                # Combine energy requirements and secondary feedstock into single dict
                energy_requirements = dict(dbc.energy_requirements or {})
                secondary_feedstock = dict(dbc.secondary_feedstock or {})

                combined_energy: dict[str, float] = {}
                for energy_key, amount in energy_requirements.items():
                    normalized_key = _normalize_energy_key(energy_key)
                    if normalized_key not in ENERGY_FEEDSTOCK_KEYS:
                        continue
                    combined_energy[normalized_key] = combined_energy.get(normalized_key, 0.0) + amount

                for secondary_key, amount in secondary_feedstock.items():
                    normalized_secondary = _normalize_energy_key(secondary_key)
                    if normalized_secondary not in ENERGY_FEEDSTOCK_KEYS:
                        continue
                    converted_amount = (
                        amount * KG_TO_T
                        if normalized_secondary in SECONDARY_FEEDSTOCKS_REQUIRING_KG_TO_T_CONVERSION
                        else amount
                    )
                    combined_energy[normalized_secondary] = (
                        combined_energy.get(normalized_secondary, 0.0) + converted_amount
                    )

                total_dict = combined_energy

                # Calculate energy costs by scaling with material demand and normalizing per ton of product
                _nrg = {
                    key: (
                        value
                        * (energy_costs.get(key) or energy_costs.get(_normalize_energy_key(key)) or 0.0)
                        * material_demand
                        / dbc.required_quantity_per_ton_of_product
                        if dbc.required_quantity_per_ton_of_product
                        else 0.0
                    )
                    for key, value in total_dict.items()
                    if key in energy_costs or _normalize_energy_key(key) in energy_costs
                }

                # Initialize or accumulate costs for this output product
                if output not in breakdown:
                    # First occurrence: initialize with energy costs and material cost
                    breakdown[output] = _nrg.copy()
                    breakdown[output]["material cost"] = material_cost
                else:
                    # Subsequent occurrence: accumulate energy costs and material cost
                    for key, value in _nrg.items():
                        if key in breakdown[output]:
                            breakdown[output][key] += value
                        else:
                            breakdown[output][key] = value
                    breakdown[output]["material cost"] += material_cost

    # Normalize all costs by total production to get per-unit costs
    return {key: value / production for material, value_dict in breakdown.items() for key, value in value_dict.items()}


def calculate_variable_opex(materials_cost_data: dict, energy_cost_data: dict) -> float:
    """
    Calculate the total variable operating expenditure (OPEX) by combining material and energy costs.

    Material costs are calculated as total material costs divided by product volume.
    Energy costs are calculated as total energy costs divided by product volume.

    Args:
        materials_cost_data (dict): Dictionary of material cost data with keys:
            - 'total_material_cost': Total material cost (excludes current step's energy)
            - 'product_volume': Output volume (tons)
        energy_cost_data (dict): Dictionary of energy cost data with keys:
            - 'total_cost': Total energy cost
            - 'product_volume': Output volume (tons)

    Returns:
        float: The total variable OPEX per ton of output. Returns 0.0 if both costs are None.

    Note:
        - Material costs use the 'total_material_cost' field from TM_PAM connector which excludes
          current step's processing energy but includes all upstream costs (material + transport + upstream energy)
        - Energy costs are summed across all energy carriers and divided by product volume
        - This matches the cost_breakdown calculation which uses unit_material_cost + energy breakdown
    """
    logger = logging.getLogger(f"{__name__}.calculate_variable_opex")

    def calculate_material_total(materials_data: dict[str, dict[str, Any]]) -> float | None:
        """
        Calculate total material cost per unit of output.

        Sums all material costs and divides by total output (product volume).
        Uses the 'total_cost' field which excludes current step's energy but includes
        all upstream costs (material + transport + upstream energy).
        """
        if not materials_data:
            return None

        # Get product volume from first item (should be consistent across all items)
        product_volumes: set[float] = set()
        for item in materials_data.values():
            volume_value = _coerce_to_float(item.get("product_volume"))
            if volume_value is not None and volume_value > 0:
                product_volumes.add(volume_value)

        if len(product_volumes) > 1:
            logger.warning("Inconsistent product_volume values detected in material BOM data: %s", product_volumes)

        product_volume = next(iter(product_volumes), None)

        # Sum total material costs across all materials (excluding current step's energy)
        total_material_cost = 0.0
        for _, item in materials_data.items():
            # Use total_material_cost which excludes current step's energy
            # This matches what cost_breakdown uses (unit_material_cost)
            item_cost = _coerce_to_float(item.get("total_material_cost")) or 0.0
            total_material_cost += item_cost

        if product_volume and product_volume > 0 and total_material_cost > 0:
            unit_material_cost = total_material_cost / product_volume
            return unit_material_cost

        return None

    def calculate_energy_total(energy_data: dict[str, dict[str, Any]]) -> float | None:
        """
        Calculate total energy cost per unit of output.

        Sums all energy costs and divides by total demand (output volume).
        """
        if not energy_data:
            return None

        # Get product volume from first item (should be consistent across all items)
        product_volumes: set[float] = set()
        for item in energy_data.values():
            volume_value = _coerce_to_float(item.get("product_volume"))
            if volume_value is not None and volume_value > 0:
                product_volumes.add(volume_value)

        if len(product_volumes) > 1:
            logger.warning("Inconsistent product_volume values detected in energy BOM data: %s", product_volumes)

        product_volume = next(iter(product_volumes), None)

        # Sum total energy costs across all energy carriers
        total_energy_cost = 0.0
        for _, item in energy_data.items():
            item_cost = _coerce_to_float(item.get("total_cost")) or 0.0
            total_energy_cost += item_cost

        if product_volume and product_volume > 0 and total_energy_cost > 0:
            unit_energy_cost = total_energy_cost / product_volume
            return unit_energy_cost

        return None

        # weighted_entries: list[tuple[float, float]] = []
        # total_weight = 0.0
        # for item in energy_data.values():
        #     weight = _coerce_to_float(item.get("demand"))
        #     unit_cost_value = _coerce_to_float(item.get("unit_cost"))
        #     if weight is None or weight == 0.0 or unit_cost_value is None:
        #         continue
        #     weighted_entries.append((weight, unit_cost_value))
        #     total_weight += weight

        # if total_weight == 0.0:
        #     return None

        # total_value = sum(weight * cost for weight, cost in weighted_entries)
        # return total_value / total_weight

    # Calculate material cost as total cost / total output
    material_unit_cost = calculate_material_total(materials_cost_data)
    # Calculate energy cost as total cost / total output
    energy_unit_cost = calculate_energy_total(energy_cost_data)

    # Handle cases where one or both unit costs are None
    if material_unit_cost is None and energy_unit_cost is None:
        return 0.0  # No costs if both have zero weight
    if material_unit_cost is None:
        return energy_unit_cost if energy_unit_cost is not None else 0.0  # Only energy costs
    if energy_unit_cost is None:
        return material_unit_cost  # Only material costs

    return material_unit_cost + energy_unit_cost


class CostData(TypedDict):
    demand: float
    unit_cost: dict[str, float]  # or a more specific TypedDict if you know more


BillOfMaterialsDict = dict[str, dict[str, CostData]]


def calculate_unit_total_opex(unit_vopex: float, unit_fopex: float, utilization_rate: float) -> float:
    """
    Calculate the total operating expenditure (OPEX) per unit produced by summing variable and fixed OPEX.

    When utilization rate is zero, returns zero since no units are produced. Unit total OPEX does not include
    carbon costs.

    Args:
        unit_vopex (float): Variable OPEX per produced unit of goods ($/unit).
        unit_fopex (float): Fixed OPEX per unit of goods ($/unit).
        utilization_rate (float): Utilization rate of the production process (0.0 to 1.0).

    Returns:
        float: Total OPEX per unit of production ($/unit). Returns 0 if utilization rate is zero.
    """
    if utilization_rate > 0.0:
        # Sum variable and fixed OPEX when plant is operational
        return unit_fopex + unit_vopex
    else:
        # No production means no unit OPEX
        return 0.0


def calculate_debt_repayment(
    total_investment: float,
    equity_share: float,
    lifetime: int,
    cost_of_debt: float,
    lifetime_remaining: int | None = None,
) -> list[float]:
    """
    Calculate the yearly debt repayment schedule with constant principal and declining interest payments.

    Uses a straight-line amortization method where the principal is repaid equally each year, while interest
    is calculated on the average debt balance during each period (declining over time).

    Args:
        total_investment (float): The total investment cost ($).
        equity_share (float): The fraction of the investment financed by equity (0.0 to 1.0).
        lifetime (int): The full repayment lifetime in years.
        cost_of_debt (float): The annual interest rate on debt (e.g., 0.05 for 5%).
        lifetime_remaining (int | None): The remaining years for debt repayment. Defaults to lifetime.

    Returns:
        list[float]: List of yearly repayment amounts (principal + interest) for the remaining lifetime.
            Returns list of zeros if there is no debt (equity_share = 1.0).

    Note:
        - The repayment method uses constant principal payments with declining interest (not constant annuity payments).
        - Interest for each period is calculated on the average debt balance: (debt_start + debt_end) / 2.
        - Only returns the last `lifetime_remaining` years of the repayment schedule.
    """
    # Use full lifetime if remaining not specified
    if lifetime_remaining is None:
        lifetime_remaining = lifetime

    # Calculate total debt (investment minus equity portion)
    debt = total_investment * (1 - equity_share)

    # Handle case where there is no debt (100% equity financing)
    if debt == 0:
        total_repayment_list = [0.0] * lifetime
    else:
        # Create schedule with constant principal repayments
        capital_repayment_list = [debt / lifetime] * lifetime_remaining
        total_repayment_list = []

        # Calculate total repayment (principal + interest) for each year
        for capital_repayment in capital_repayment_list:
            # Calculate debt remaining after this principal payment
            remaining_debt = debt - capital_repayment
            # Calculate interest on average debt balance during the period
            interest_repayment = ((debt + remaining_debt) / 2) * cost_of_debt
            # Total repayment is principal plus interest
            total_repayment_list.append(interest_repayment + capital_repayment)
            # Update debt balance for next iteration
            debt = remaining_debt

    # Return only the last lifetime_remaining years
    return total_repayment_list[-lifetime_remaining:]


def calculate_current_debt_repayment(
    total_investment: float,
    lifetime_expired: bool,
    lifetime_years: int,
    years_elapsed: int,
    cost_of_debt: float,
    equity_share: float,
) -> float:
    """
    Calculate the total debt repayment (principal plus interest) for the current year only.

    Uses the same straight-line amortization method as calculate_debt_repayment, with constant principal
    and declining interest payments. This function calculates a single year's repayment given the current
    state, rather than generating a full schedule.

    Args:
        total_investment (float): The total investment cost ($).
        lifetime_expired (bool): Flag indicating if the repayment period is over.
        lifetime_years (int): The total repayment lifetime in years.
        years_elapsed (int): The number of years elapsed since operation start.
        cost_of_debt (float): The annual interest rate on debt (e.g., 0.05 for 5%).
        equity_share (float): The fraction of the investment financed by equity (0.0 to 1.0).

    Returns:
        float: The debt repayment amount (principal + interest) for the current year. Returns 0.0 if
            there is no debt, or if the repayment period has expired.

    Note:
        - Uses the same repayment method as calculate_debt_repayment: constant principal with declining interest.
        - Interest is calculated on the average debt balance during the current period.
    """
    # Calculate total debt (investment minus equity portion)
    debt = total_investment * (1 - equity_share)

    # Return zero if no debt or if repayment period has ended
    if debt == 0 or lifetime_expired:
        return 0.0

    # Calculate constant yearly principal payment
    yearly_principal = debt / lifetime_years
    # Calculate remaining debt at start of current year
    remaining_debt = debt - (yearly_principal * (years_elapsed - 1))
    # Calculate average debt balance during current year
    avg_debt = (remaining_debt + (remaining_debt - yearly_principal)) / 2

    # Return total repayment: principal plus interest on average balance
    return yearly_principal + (avg_debt * cost_of_debt)


def calculate_unit_production_cost(
    unit_total_opex: float,
    unit_carbon_cost: float,
    unit_current_debt_repayment: float,
    utilization_rate: float,
    cost_adjustments_from_secondary_outputs: float = 0.0,
) -> float:
    """
    Calculate the total unit production cost by summing all cost components and profit adjustments.

    Combines operating expenditure, carbon costs, debt repayment, and adjustments from secondary outputs (e.g.,
    by-product sales) into a single unit production cost. When utilization rate is zero, returns zero since no
    production occurs.

    Args:
        unit_total_opex (float): Unit total operating expenditure (FOPEX + VOPEX), with subsidies applied ($/unit).
        unit_carbon_cost (float): Carbon cost per unit of production ($/unit).
        unit_current_debt_repayment (float): Debt repayment per unit of production for the current period ($/unit).
        utilization_rate (float): Utilization rate of the furnace group (0.0 to 1.0).
        cost_adjustments_from_secondary_outputs (float): Cost adjustments from by-product sales ($/unit).
            Positive values increase costs, negative values decrease costs (revenue). Defaults to 0.0.

    Returns:
        float: The total cost per unit of production ($/unit). Returns 0 if utilization rate is zero.

    Note:
        - All input costs should already be per unit of production and have subsidies applied where applicable.
        - cost_adjustments_from_secondary_outputs can be negative to represent revenue from by-products.
        - When utilization_rate is 0, no production occurs, so unit production cost is meaningless and set to 0.
    """
    # Check if production is occurring
    if utilization_rate > 0:
        # Sum all cost components (OPEX, carbon, debt, secondary output adjustments)
        return (
            unit_total_opex + unit_carbon_cost + unit_current_debt_repayment + cost_adjustments_from_secondary_outputs
        )
    else:
        # No production means no unit production cost
        return 0.0


def calculate_gross_cash_flow(
    total_opex: list[float], price_series: list[float], expected_production: float
) -> list[float]:
    """
    Calculate the gross cash flow over multiple time steps.

    Computes cash flow as (revenue - operating costs) for each period.

    Args:
        total_opex (list[float]): List of unit total operating expenditures per time step ($/unit).
        price_series (list[float]): The market price per unit product per time step ($/unit).
        expected_production (float): The production volume per period (units/period).

    Returns:
        list[float]: A list of gross cash flows for each period ($/period).

    Note:
        - When OPEX is 0 (e.g., no production), cash flow is set to 0 rather than calculating revenue.
    """
    gross_cash_flow: list[float] = []
    # Calculate cash flow for each period
    for i, unit_total_opex_per_year in enumerate(total_opex):
        # If no operating costs, no production is occurring
        if unit_total_opex_per_year == 0:
            cash_flow = 0.0
        else:
            # Cash flow = (price - cost) * production volume
            cash_flow = (price_series[i] - unit_total_opex_per_year) * expected_production
        gross_cash_flow.append(cash_flow)

    return gross_cash_flow


def calculate_net_cash_flow(total_debt_repayment: list[float], gross_cash_flow: list[float]) -> list[float]:
    """
    Calculate the net cash flow by subtracting debt repayment from gross cash flow for each period.

    This function is used in NPV calculations where debt repayment reduces the available cash flow
    that can be used for equity returns.

    Args:
        total_debt_repayment (list[float]): List of debt repayment amounts per period.
        gross_cash_flow (list[float]): List of gross cash flows per period.

    Returns:
        list[float]: Net cash flow values for each period.

    Note: Debt is subtracted because it represents cash outflow that reduces available returns.
    """
    # Validate input lists have matching lengths
    if len(total_debt_repayment) != len(gross_cash_flow):
        raise ValueError("The lengths of total_debt_repayment and gross_cash_flow must be the same.")

    # Subtract debt repayment from gross cash flow for each period
    return [gross - debt for gross, debt in zip(gross_cash_flow, total_debt_repayment)]


def calculate_lost_cash_flow(
    total_debt_repayment: list[float] | list[int], gross_cash_flow: list[float] | list[int]
) -> list[float]:
    """
    Calculate the lost cash flow by adding debt repayment to gross cash flow for each period.

    This function is used in Cost of Stranded Asset (COSA) calculations where both the foregone
    operating cash flows and the remaining debt obligations represent total losses from stranding.

    Args:
        total_debt_repayment (list[float] | list[int]): List of debt repayment amounts per period.
        gross_cash_flow (list[float] | list[int]): List of gross cash flows per period.

    Returns:
        list[float]: Lost cash flow values for each period.

    Note: Debt is added because remaining debt obligations represent additional losses when an asset is stranded.
    """
    # Validate input lists have matching lengths
    if len(total_debt_repayment) != len(gross_cash_flow):
        raise ValueError("The lengths of total_debt_repayment and gross_cash_flow must be the same.")

    # Add debt repayment to gross cash flow for each period
    return [gross + debt for gross, debt in zip(gross_cash_flow, total_debt_repayment)]


def calculate_cost_of_stranded_asset(lost_cash_flow: list[float], cost_of_equity: float) -> float:
    """
    Calculate the net present value (NPV) of losses due to stranding an asset (COSA).

    Discounts future lost cash flows (including both foregone operating profits and remaining
    debt obligations) to present value using the cost of equity as the discount rate.

    Args:
        lost_cash_flow (list[float]): Future lost cash flows per period.
        cost_of_equity (float): The discount rate (cost of equity).

    Returns:
        float: The NPV of the stranded asset cost.
    """
    npv_loss = 0.0
    # Discount each period's lost cash flow to present value
    for t, cash in enumerate(lost_cash_flow, start=1):
        discount_factor = (1 + cost_of_equity) ** t
        npv_loss += cash / discount_factor
    return npv_loss


def calculate_npv_costs(
    net_cash_flow: list[float],
    cost_of_equity: float,
    equity_share: float,
    total_investment: float = 0,
) -> float:
    """
    Calculate the net present value (NPV) of a series of cash flows.

    Discounts future net cash flows to present value and subtracts the equity portion of the
    initial investment to determine project profitability from the equity investor's perspective.

    Args:
        net_cash_flow (list[float]): List of future net cash flows per period.
        cost_of_equity (float): The discount rate (cost of equity).
        equity_share (float): The fraction of the investment financed by equity.
        total_investment (float): The total investment cost. Defaults to 0.

    Returns:
        float: The calculated NPV.

    Note: Returns -1e9 for invalid inputs (cost_of_equity <= -1.0 or NaN values in cash flows).
    """
    # Use function-specific logger that respects the centralized configuration
    func_logger = logging.getLogger(f"{__name__}.calculate_npv_costs")

    # Handle edge cases for cost_of_equity
    if cost_of_equity <= -1.0:
        # Return large negative NPV to indicate unprofitability
        # when discount rate is invalid (would cause division by zero or negative base)
        return -1e9

    # Check for NaN values in cash flows
    if any(math.isnan(cash) for cash in net_cash_flow):
        return -1e9

    # Start with negative equity investment as initial cash outflow
    npv = -(total_investment * equity_share)
    func_logger.debug(f"[NPV COSTS]: Initial NPV: ${npv:,.2f}")

    # Add discounted cash flows for each period
    for t, cash in enumerate(net_cash_flow, start=1):
        npv += cash / ((1 + cost_of_equity) ** t)

    func_logger.debug(f"[NPV COSTS]: Final NPV: ${npv:,.2f}")
    return npv


def calculate_npv_full(
    capex: float,
    capacity: float,
    unit_total_opex_list: list[float],
    expected_utilisation_rate: float,
    price_series: list[float],
    cost_of_debt: float,
    cost_of_equity: float,
    equity_share: float,
    lifetime: int,
    construction_time: int,
    carbon_costs: list[float] | None = None,
    infrastructure_costs: float = 0.0,
) -> float:
    """
    Calculate the full net present value (NPV) for a technology investment.

    Computes NPV by calculating total investment, debt repayment schedule, applying construction
    time lags, adding carbon costs to OPEX, and discounting net cash flows to present value.

    Steps:
    1. Calculate total investment (CAPEX * capacity + infrastructure costs) and expected production
    2. Generate debt repayment schedule over the project lifetime
    3. Apply construction time lag to debt repayments and OPEX
    4. Add carbon costs to OPEX (if provided and production > 0)
    5. Calculate gross and net cash flows
    6. Discount to present value using cost of equity

    Args:
        capex (float): The capital expenditure per unit capacity ($/unit).
        capacity (float): The capacity of the plant (units).
        unit_total_opex_list (list[float]): List of unit total OPEX per period ($/unit).
        expected_utilisation_rate (float): Expected utilisation rate (0.0 to 1.0).
        price_series (list[float]): Market price per unit product per period ($/unit).
        cost_of_debt (float): The cost of debt as annual interest rate (e.g., 0.05 for 5%).
        cost_of_equity (float): The cost of equity as discount rate (e.g., 0.08 for 8%).
        equity_share (float): The fraction of the investment financed by equity (0.0 to 1.0).
        lifetime (int): The project lifetime in years.
        construction_time (int): Construction time in years (creates lag before production starts).
        carbon_costs (list[float] | None): List of total carbon costs per period ($). Defaults to None.
        infrastructure_costs (float): Additional infrastructure costs like rail (applies to new plants only).
            Defaults to 0.0.

    Returns:
        float: The calculated NPV for the technology investment.

    Note: Equity share must be passed explicitly from the config for new plants.
    """
    # Use function-specific logger that respects the centralized configuration
    func_logger = logging.getLogger(f"{__name__}.calculate_npv_full")

    # Calculate total investment and expected production
    total_investment = capacity * capex + infrastructure_costs
    expected_production = expected_utilisation_rate * capacity

    # Calculate debt repayment schedule over lifetime
    debt_repayment = calculate_debt_repayment(
        total_investment=total_investment, equity_share=equity_share, lifetime=lifetime, cost_of_debt=cost_of_debt
    )

    # Apply construction time lag to debt repayments and OPEX
    zeros = [0.0] * construction_time
    debt_repayment_lagged = zeros + debt_repayment
    unit_opex_lagged = zeros + unit_total_opex_list

    # Add carbon costs to OPEX if provided and production is positive
    if expected_production == 0:
        func_logger.warning("[NPV FULL] Expected production is zero. Not applying carbon costs to OPEX.")
    elif not carbon_costs:
        func_logger.warning("[NPV FULL] No carbon costs provided. Not applying carbon costs to OPEX.")
    else:
        # Convert total carbon costs to unit carbon costs and apply construction lag
        unit_carbon_costs = [carbon_cost / expected_production for carbon_cost in carbon_costs]
        unit_carbon_costs_lagged = zeros + unit_carbon_costs
        unit_opex_lagged = [x + y for x, y in zip(unit_opex_lagged, unit_carbon_costs_lagged)]

    # Calculate cash flows
    gross_cash_flow = calculate_gross_cash_flow(
        total_opex=unit_opex_lagged, price_series=price_series, expected_production=expected_production
    )
    net_cash_flow = calculate_net_cash_flow(total_debt_repayment=debt_repayment_lagged, gross_cash_flow=gross_cash_flow)

    # Log all intermediate calculations together for easier debugging
    func_logger.debug(f"[NPV FULL] Total investment: ${total_investment:,.2f}")
    func_logger.debug(f"[NPV FULL] Expected production: {expected_production:,.0f} kt")
    func_logger.debug(f"[NPV FULL] Debt repayment: {debt_repayment}")
    func_logger.debug(f"[NPV FULL] Debt repayment lagged: {debt_repayment_lagged}")
    func_logger.debug(f"[NPV FULL] OPEX list: {unit_opex_lagged}")
    func_logger.debug(f"[NPV FULL] Gross cash flow: {gross_cash_flow}")
    func_logger.debug(f"[NPV FULL] Net cash flow: {net_cash_flow}")

    # Calculate and return NPV
    return calculate_npv_costs(
        net_cash_flow=net_cash_flow,
        cost_of_equity=cost_of_equity,
        equity_share=equity_share,
        total_investment=total_investment,
    )


def stranding_asset_cost(
    debt_repayment_per_year: list[float],
    unit_total_opex_list: list[float],
    remaining_time: int,
    market_price_series: list[float],
    expected_production: float,
    cost_of_equity: float,
) -> float:
    """
    Calculate the Cost of Stranded Asset (COSA) when switching technologies.

    COSA represents the NPV of losses from abandoning the current technology before its planned
    end of life. This includes both remaining debt obligations (current + legacy debt) and the
    opportunity cost of foregone profitable operations.

    Steps:
    1. Extract remaining debt payments, OPEX, and prices for the remaining asset lifetime
    2. Calculate gross cash flow (revenue - OPEX) for remaining periods
    3. Calculate lost cash flow (gross cash flow + debt obligations)
    4. Discount lost cash flows to present value using cost of equity

    Args:
        debt_repayment_per_year (list[float]): Total yearly debt repayments (current + legacy debt combined).
        unit_total_opex_list (list[float]): Yearly unit total OPEX expenditures ($/unit).
        remaining_time (int): Remaining asset lifetime in years.
        market_price_series (list[float]): Market price per unit product per year ($/unit).
        expected_production (float): Production volume per period (units/period).
        cost_of_equity (float): Annual cost of equity as discount rate (e.g., 0.08 for 8%).

    Returns:
        float: The net present value of losses due to the stranded asset (COSA).

    Note: See FurnaceGroup.debt_repayment_per_year for full details on debt accumulation logic.
    """
    # Use function-specific logger that respects the centralized configuration
    func_logger = logging.getLogger(f"{__name__}.stranding_asset_cost")

    # Extract remaining time periods for all inputs (last N years of debt, first N years of OPEX/prices)
    remaining_debt = debt_repayment_per_year[-remaining_time:]
    remaining_opex = unit_total_opex_list[:remaining_time]
    remaining_price_series = market_price_series[:remaining_time]

    # Calculate gross cash flow from operations for remaining periods
    gross_cash_flow = calculate_gross_cash_flow(remaining_opex, remaining_price_series, expected_production)

    # Calculate lost cash flow (both foregone profits and debt obligations)
    lost_cash_flow = calculate_lost_cash_flow(remaining_debt, gross_cash_flow)

    # Log all intermediate calculations together for easier debugging
    func_logger.debug(
        f"[STRANDING ASSET COST] Remaining time: {remaining_time}, Expected production: {expected_production:,.0f} kt"
    )
    func_logger.debug(f"[STRANDING ASSET COST] Price per unit per year ($/t): {market_price_series}")
    func_logger.debug(f"[STRANDING ASSET COST] Remaining debt: {remaining_debt}")
    func_logger.debug(f"[STRANDING ASSET COST] Remaining unit total OPEX: {remaining_opex}")
    func_logger.debug(f"[STRANDING ASSET COST] Gross cash flow: {gross_cash_flow}")
    func_logger.debug(f"[STRANDING ASSET COST] Lost cash flow: {lost_cash_flow}")

    # Discount lost cash flows to present value
    return calculate_cost_of_stranded_asset(lost_cash_flow, cost_of_equity)


def calculate_business_opportunity_npvs(
    cost_data: dict[str, dict[tuple[float, float, str], dict[str, dict[str, Any]]]],
    target_year: int,
    market_price: dict[str, list[float]],
    steel_plant_capacity: float,
    plant_lifetime: int,
    construction_time: int,
    equity_share: float,
    technology_emission_factors: list["TechnologyEmissionFactors"],
    chosen_emissions_boundary_for_carbon_costs: str,
    dynamic_business_cases: dict[str, list["PrimaryFeedstock"]],
) -> dict[str, dict[tuple[float, float, str], dict[str, float]]]:
    """
    Calculates the NPV for a series of business opportunities. If the calculation fails, it returns a very
    negative NPV instead.

    Inputs include:
        - Electricity and hydrogen costs, CAPEX, market prices, cost of debt, cost of equity, equity share,
          and railway costs for the current year.
        - Subsidies (both for CAPEX and cost of debt) for the earliest possible construction start year.
        - OPEX and carbon costs (with subsidies) for the earliest possible operational years (taking into
          account consideration, construction, announcement, and plant lifetime).

    Args:
        cost_data: Nested dictionary: product -> site_id -> tech -> cost_type -> cost
        target_year: Target year for the business opportunity
        market_price: Dictionary mapping product to list of future market prices
        steel_plant_capacity: Capacity of the steel plant in tonnes
        plant_lifetime: Lifetime of the plant in years
        construction_time: Time required for plant construction in years
        equity_share: Share of investment financed by equity
        technology_emission_factors: List of technology-specific emission factors
        chosen_emissions_boundary_for_carbon_costs: Emission boundary for carbon cost calculation
        dynamic_business_cases: Dictionary mapping technology to list of primary feedstocks

    Returns:
        Dictionary mapping product -> site_id -> technology -> NPV.

    Notes:
        - This results in an adjusted NPV metric, which proved to be best to ensure the right plants are
          opened, because subsidized technologies would otherwise suffer a too long delay until the model
          picks them up. In real life, subsidies are often announced years in advance of actual plant
          construction. This metric only affects the decision to open a plant, not the actual costs once
          opened.
        - The plant capacity does not affect the NPV calculation.
        - cost_data has been validated by validate_and_clean_cost_data to ensure all required fields are
          present with correct types (floats for costs, dict for bom).
    """
    from steelo.domain.calculate_costs import calculate_npv_full, collect_active_subsidies_over_period

    logger = logging.getLogger(f"{__name__}.calculate_business_opportunity_npvs")
    npv_dict: dict[str, dict[tuple[float, float, str], dict[str, float]]] = {}  # product -> site_id -> tech -> NPV
    for prod, sites in cost_data.items():
        npv_dict[prod] = {}
        for site_id, business_ops in sites.items():
            npv_dict[prod][site_id] = {}
            for tech, bo_costs in business_ops.items():
                # Earliest possible year of operation
                start_year = Year(target_year + construction_time)
                end_year = Year(start_year + plant_lifetime)

                # Calculate unit total opex with subsidies applied for earliest possible operation years
                all_opex_subsidies: list["Subsidy"] = bo_costs.get("all_opex_subsidies", [])  # type: ignore[assignment]
                selected_opex_subsidies = collect_active_subsidies_over_period(
                    all_opex_subsidies, start_year=start_year, end_year=end_year
                )
                bom = bo_costs["bom"]
                assert isinstance(bom, dict), f"Expected bom to be dict, got {type(bom)}"
                unit_vopex = calculate_variable_opex(bom["materials"], bom["energy"])
                unit_fopex = bo_costs["fopex"]
                assert isinstance(unit_fopex, (int, float)), f"Expected fopex to be numeric, got {type(unit_fopex)}"
                unit_total_opex = unit_vopex + unit_fopex
                unit_total_opex_list = calculate_opex_list_with_subsidies(
                    opex=unit_total_opex,
                    opex_subsidies=selected_opex_subsidies,
                    start_year=start_year,
                    end_year=end_year,
                )

                # Calculate carbon costs for earliest possible operation years
                tech_business_cases = dynamic_business_cases.get(tech, dynamic_business_cases.get(tech.lower(), []))
                reductant_value = bo_costs["reductant"]
                assert reductant_value is None or isinstance(reductant_value, str), (
                    f"Expected reductant to be str or None, got {type(reductant_value)}"
                )
                matched_business_cases = materiall_bill_business_case_match(
                    dynamic_feedstocks=tech_business_cases,
                    material_bill=bom["materials"],
                    tech=tech,
                    reductant=reductant_value,
                )
                bom_emissions = calculate_emissions(
                    business_cases=matched_business_cases,
                    material_bill=bom["materials"],
                    technology_emission_factors=technology_emission_factors,
                )
                carbon_cost_series = bo_costs["carbon_cost_series"]
                assert isinstance(carbon_cost_series, dict), (
                    f"Expected carbon_cost_series to be dict, got {type(carbon_cost_series)}"
                )
                carbon_cost_list = calculate_emissions_cost_series(
                    emissions=bom_emissions,
                    carbon_price_dict=carbon_cost_series,
                    chosen_emission_boundary=chosen_emissions_boundary_for_carbon_costs,
                    start_year=start_year,
                    end_year=end_year,
                )

                # Calculate NPV
                npv_value = calculate_npv_full(
                    capex=bo_costs["capex"],  # type: ignore[arg-type]
                    capacity=steel_plant_capacity,
                    unit_total_opex_list=unit_total_opex_list,  # type: ignore[arg-type]
                    expected_utilisation_rate=bo_costs["utilization_rate"],  # type: ignore[arg-type]
                    price_series=market_price[prod],
                    lifetime=plant_lifetime,
                    construction_time=construction_time,
                    cost_of_debt=bo_costs["cost_of_debt"],  # type: ignore[arg-type]
                    cost_of_equity=bo_costs["cost_of_equity"],  # type: ignore[arg-type]
                    equity_share=equity_share,
                    infrastructure_costs=bo_costs["railway_cost"],  # type: ignore[arg-type]
                    carbon_costs=carbon_cost_list,
                )

                # Set to very negative NPV if calculation returned NaN
                if math.isnan(npv_value):
                    logger.warning(
                        f"NPV calculation returned NaN for product {prod} - site {site_id} - "
                        f"technology {tech}. Returning -inf."
                    )
                    npv_dict[prod][site_id][tech] = float("-inf")
                else:
                    npv_dict[prod][site_id][tech] = npv_value
    return npv_dict


def calculate_capex_reduction_rate(
    capacity_zero: float,
    capacity_current: float,
    learning_coeff: float = -0.043943347587597055,  # TODO: Remove hardcoded learning coefficient
) -> float:
    """
    Calculate CAPEX reduction multiplier based on learning-by-doing effects from capacity growth.

    Uses a power law relationship where CAPEX decreases as cumulative capacity increases,
    reflecting cost reductions from manufacturing experience, economies of scale, and technology maturation.
    The formula is: (capacity_current / capacity_zero)^learning_coeff

    Args:
        capacity_zero (float): Initial baseline capacity in tonnes (starting point for learning curve).
        capacity_current (float): Current cumulative capacity in tonnes (after technology deployment).
        learning_coeff (float): Learning coefficient representing cost reduction rate (negative value). Default: -0.044.

    Returns:
        reduction_multiplier (float): CAPEX reduction multiplier where 1.0 = no reduction, <1.0 = cost reduction, >1.0 = cost increase.

    Notes:
        - Returns 1.0 (no reduction) if capacity_zero is 0 to avoid division by zero
        - Negative learning coefficient means costs decrease as capacity increases
        - Example: If current capacity is 2x initial capacity with coeff=-0.044, multiplier ≈ 0.97 (3% cost reduction)
        - The default coefficient corresponds to approximately 7% cost reduction per doubling of capacity
    """
    # Calculate reduction using power law: (current/initial)^learning_coeff
    return (capacity_current / capacity_zero) ** learning_coeff if capacity_zero > 0 else 1.0


def calculate_debt_report(
    total_investment: float,
    lifetime_expired: bool,
    lifetime_years: int,
    years_elapsed: int,
    cost_of_debt: float,
    equity_share: float,
) -> tuple[float, float]:
    """
    Calculate the debt components (yearly principal and interest) for reporting purposes.

    Uses the same straight-line amortization method as calculate_debt_repayment to break down
    the current year's debt payment into its principal and interest components for reporting.

    Args:
        total_investment (float): The total investment cost ($).
        lifetime_expired (bool): Flag indicating if the debt repayment lifetime has expired.
        lifetime_years (int): The total number of repayment years.
        years_elapsed (int): The number of years that have elapsed since operation start.
        cost_of_debt (float): The annual interest rate on debt (e.g., 0.05 for 5%).
        equity_share (float): The fraction of the investment financed by equity (0.0 to 1.0).

    Returns:
        tuple[float, float]: A tuple of (yearly_principal, interest_repayment) for the current year.
            Returns (0.0, 0.0) if there is no debt or if the repayment period has expired.
    """
    # Calculate total debt (investment minus equity portion)
    debt = total_investment * (1 - equity_share)

    # Return zeros if no debt or if repayment period has ended
    if debt == 0 or lifetime_expired:
        return 0.0, 0.0

    # Calculate constant yearly principal payment
    yearly_principal = debt / lifetime_years
    # Calculate remaining debt at start of current year
    remaining_debt = debt - (yearly_principal * (years_elapsed - 1))
    # Calculate average debt balance during current year
    avg_debt = (remaining_debt + (remaining_debt - yearly_principal)) / 2

    # Return principal payment and interest on average balance
    return yearly_principal, (avg_debt * cost_of_debt)


def calculate_capex_with_subsidies(capex: float, capex_subsidies: list["Subsidy"]) -> float:
    """
    Calculate the CAPEX after applying subsidies.

    Applies both absolute and relative subsidies to the original CAPEX value. The final CAPEX
    cannot go below zero (fully subsidized).

    Args:
        capex (float): The original CAPEX value.
        capex_subsidies (list[Subsidy]): List of CAPEX subsidies for specific location and technology.

    Returns:
        float: The final CAPEX after applying subsidies (minimum value of 0).

    Note:
        Negative subsidy_amount values act as taxes/penalties (increase cost).
        Result is floored at 0 - subsidies cannot make costs negative.
    """
    # If no capex subsidies in the list, return original capex
    if capex_subsidies == []:
        return capex

    # Sum all applicable subsidies based on subsidy_type
    capex_total_subsidy = 0.0
    for subsidy in capex_subsidies:
        if subsidy.subsidy_type == "absolute":
            capex_total_subsidy += subsidy.subsidy_amount
        elif subsidy.subsidy_type == "relative":
            # subsidy_amount stored as decimal (e.g., 0.1 = 10%)
            capex_total_subsidy += capex * subsidy.subsidy_amount

    # Apply total subsidy, ensuring CAPEX doesn't go below zero
    return max(0.0, capex - capex_total_subsidy)


def calculate_opex_with_subsidies(opex: float, opex_subsidies: list["Subsidy"]) -> float:
    """
    Calculate the OPEX after applying subsidies.

    Applies both absolute and relative subsidies to the original OPEX value. The final OPEX
    cannot go below zero (fully subsidized).

    Args:
        opex (float): The original OPEX value.
        opex_subsidies (list[Subsidy]): List of OPEX subsidies for specific location and technology.

    Returns:
        float: The final OPEX after applying subsidies (minimum value of 0).

    Note:
        Negative subsidy_amount values act as taxes/penalties (increase cost).
        Result is floored at 0 - subsidies cannot make costs negative.
    """
    # If no opex subsidies in the list, return original opex
    if opex_subsidies == []:
        return opex

    # Sum all applicable subsidies based on subsidy_type
    opex_total_subsidy = 0.0
    for subsidy in opex_subsidies:
        if subsidy.subsidy_type == "absolute":
            opex_total_subsidy += subsidy.subsidy_amount
        elif subsidy.subsidy_type == "relative":
            # subsidy_amount stored as decimal (e.g., 0.1 = 10%)
            opex_total_subsidy += opex * subsidy.subsidy_amount

    # Apply total subsidy, ensuring OPEX doesn't go below zero
    return max(0.0, opex - opex_total_subsidy)


def calculate_opex_list_with_subsidies(
    opex: float,
    opex_subsidies: list["Subsidy"],
    start_year: "Year",
    end_year: "Year",
) -> list[float]:
    """
    Calculate OPEX values for each year of operation with time-varying subsidies.

    For each year in the operational period, filters subsidies that are active in that year
    and applies them to the base OPEX value.

    Args:
        opex (float): The base operating expenditure per unit of production.
        opex_subsidies (list[Subsidy]): List of OPEX subsidy objects with start/end years.
        start_year (Year): The first year of operation.
        end_year (Year): The last year of operation (exclusive).

    Returns:
        list[float]: OPEX values for each year of operation after applying active subsidies.
    """
    opex_list = []
    # Calculate subsidized OPEX for each year of operation
    for year in range(start_year, end_year):
        # Filter subsidies that are active in this specific year
        subsidies_in_year = []
        for subsidy in opex_subsidies:
            if subsidy.start_year <= year <= subsidy.end_year:
                subsidies_in_year.append(subsidy)

        # Apply subsidies active in this year
        opex_list.append(calculate_opex_with_subsidies(opex, subsidies_in_year))

    return opex_list


def calculate_energy_costs_and_most_common_reductant(
    dynamic_business_case: list["PrimaryFeedstock"], energy_costs: dict[str, float]
) -> tuple[str, dict[str, float]]:
    """
    Calculate energy costs for different production paths and identify the most cost-effective reductant.

    For each metallic input and reductant combination, calculates total energy VOPEX by summing
    energy requirements and secondary feedstock costs. Then identifies the most commonly used
    lowest-cost reductant across all metallic inputs.

    Args:
        dynamic_business_case (list[PrimaryFeedstock]): List of primary feedstock options with energy requirements.
        energy_costs (dict[str, float]): Energy costs by type (e.g., {'electricity': 50.0, 'natural_gas': 30.0}).

    Returns:
        tuple[str, dict[str, float]]: Tuple of (chosen_reductant, energy_vopex_by_metallic_charge).
            Returns ("", {}) if no business cases are provided or no reductants are found.
    """
    energy_vopex_by_input: dict[str, dict[str, float]] = {}

    # Early exit if no business cases provided
    if dynamic_business_case is None:
        return "", {}

    # Calculate energy VOPEX for each metallic input and reductant combination
    for dbc in dynamic_business_case:
        metallic_input = str(dbc.metallic_charge)
        energy_requirements_dict: dict[str, float] = {}
        for energy_name, amount in (dbc.energy_requirements or {}).items():
            normalized_energy = _normalize_energy_key(energy_name)
            if normalized_energy not in ENERGY_FEEDSTOCK_KEYS:
                continue
            energy_requirements_dict[normalized_energy] = energy_requirements_dict.get(normalized_energy, 0.0) + amount

        secondary_feedstock_dict = dict(dbc.secondary_feedstock or {})
        if not energy_requirements_dict and not secondary_feedstock_dict:
            continue

        # Combine energy requirements and secondary feedstock with proper units
        for secondary_key, amount in secondary_feedstock_dict.items():
            normalized_secondary = _normalize_energy_key(secondary_key)
            if normalized_secondary not in ENERGY_FEEDSTOCK_KEYS:
                continue
            converted_amount = (
                amount * KG_TO_T
                if normalized_secondary in SECONDARY_FEEDSTOCKS_REQUIRING_KG_TO_T_CONVERSION
                else amount
            )
            energy_requirements_dict[normalized_secondary] = (
                energy_requirements_dict.get(normalized_secondary, 0.0) + converted_amount
            )

        if not energy_requirements_dict:
            continue

        # Initialize nested dictionaries if not present
        if metallic_input not in energy_vopex_by_input:
            energy_vopex_by_input[metallic_input] = {}
        if dbc.reductant not in energy_vopex_by_input[metallic_input]:
            energy_vopex_by_input[metallic_input][dbc.reductant] = 0

        # Sum energy costs for all energy types
        for energy_type, volume in energy_requirements_dict.items():
            normalized_energy_type = _normalize_energy_key(energy_type)
            price = energy_costs.get(normalized_energy_type, energy_costs.get(energy_type, 0.0))
            energy_vopex_by_input[metallic_input][dbc.reductant] += volume * price

    # Find the lowest-cost reductant for each metallic input
    mins = [min(costs, key=lambda k: costs[k]) for costs in energy_vopex_by_input.values() if costs]
    counts = Counter(mins)

    # Return empty if no reductants found
    if not counts:
        return "", {}

    # Select the most commonly used lowest-cost reductant
    most_common_reductant, _ = counts.most_common(1)[0]
    chosen_reductant = str(most_common_reductant)

    # Return energy VOPEX by metallic charge for the chosen reductant only
    trimmed = {
        tech: mats[most_common_reductant]
        for tech, mats in energy_vopex_by_input.items()
        if most_common_reductant in mats
    }

    return chosen_reductant, trimmed


def calculate_debt_with_subsidies(cost_of_debt: float, debt_subsidies: list["Subsidy"], risk_free_rate: float) -> float:
    """
    Calculate the effective cost of debt after applying subsidies.

    Only absolute subsidies (percentage point reductions) are applied to cost of debt.
    Relative subsidies are ignored. The final cost of debt cannot go below the risk-free rate.

    Args:
        cost_of_debt (float): The initial cost of debt (as a decimal, e.g., 0.05 for 5%).
        debt_subsidies (list[Subsidy]): List of debt subsidy objects.
        risk_free_rate (float): The minimum allowable cost of debt (risk-free rate floor).

    Returns:
        float: The effective cost of debt after applying subsidies (minimum value of risk_free_rate).

    Note:
        Relative subsidies are ignored for cost of debt calculations; only absolute subsidies are applied.
        Negative subsidy_amount values act as taxes/penalties (increase cost of debt).
        Result is floored at risk_free_rate - subsidies cannot reduce cost below risk-free rate.
    """
    logger = logging.getLogger(f"{__name__}.calculate_debt_with_subsidies")
    # If no cost of debt subsidies in the list, return original cost of debt
    if debt_subsidies == []:
        return cost_of_debt

    # Sum all absolute subsidies (percentage point reductions only)
    debt_total_subsidy = 0.0
    for subsidy in debt_subsidies:
        if subsidy.subsidy_type == "absolute":
            debt_total_subsidy += subsidy.subsidy_amount
        else:
            logger.info("Ignoring relative subsidy for cost of debt calculation")

    # Apply subsidy, ensuring cost of debt doesn't go below risk-free rate
    return max(risk_free_rate, cost_of_debt - debt_total_subsidy)


# ----------------------------------- Hydrogen Costs -------------------------------------------------
# Note: A pixel-level version of the functions below  can be found in geospatial_calculations.py and is used by the
# GeospatialModel to follow the same logic to get hydrogen costs from electricity prices. For consistency,
# any changes to the logic below should be reflected in there as well.


def calculate_lcoh_from_electricity_country_level(
    electricity_by_country: dict[str, float],
    hydrogen_efficiency: dict["Year", float],
    hydrogen_capex_opex: dict[str, dict["Year", float]],
    year: "Year",
) -> dict[str, float]:
    """
    Calculate LCOH (Levelised Cost of Hydrogen) for each country based on electricity prices.

    Formula:
        LCOH (USD/kg) = energy_consumption (MWh/kg) × 1000 × electricity_price (USD/kWh) + capex_opex (USD/kg)
                      = (kWh/kg) × (USD/kWh) + (USD/kg)
                      = USD/kg

    The returned LCOH is in USD/kg. When applied to furnace groups via update_furnace_hydrogen_costs(),
    it is converted to USD/t (×1000) to match the BOM consumption units (t/t after normalisation).

    Args:
        electricity_by_country: Dictionary mapping ISO3 codes to electricity prices (USD/kWh).
            Already converted from USD/MWh by excel_reader.
        hydrogen_efficiency: Dictionary mapping years to electrolyser energy consumption (MWh/kg H2).
            From "Hydrogen efficiency" sheet in master Excel.
        hydrogen_capex_opex: Dictionary mapping ISO3 codes to year->CAPEX+OPEX values (USD/kg).
            From "Hydrogen CAPEX_OPEX component" sheet in master Excel.
        year: Current simulation year.

    Returns:
        Dictionary mapping ISO3 codes to LCOH values (USD/kg).

    Raises:
        ValueError: If required data is missing for calculation.
    """
    logger = logging.getLogger(f"{__name__}.calculate_lcoh_from_electricity_country_level")

    if year not in hydrogen_efficiency:
        raise ValueError(f"Hydrogen efficiency not found for year {year}")

    # Energy consumption of the electrolyser
    energy_consumption = hydrogen_efficiency[year]  # MWh/kg H2
    energy_consumption_kwh = energy_consumption * MWH_TO_KWH  # Convert MWh to kWh

    logger.debug(
        "[ENERGY UNITS] LCOH calc year %s: electrolyser efficiency %.4f MWh/kg = %.2f kWh/kg",
        year,
        energy_consumption,
        energy_consumption_kwh,
    )

    lcoh_by_country = {}
    for iso3, electricity_price in electricity_by_country.items():
        # Get CAPEX+OPEX component for this country and year
        if iso3 not in hydrogen_capex_opex:
            raise ValueError(f"Hydrogen CAPEX/OPEX not found for country {iso3}")
        if year not in hydrogen_capex_opex[iso3]:
            raise ValueError(f"Hydrogen CAPEX/OPEX not found for country {iso3} in year {year}")
        capex_opex = hydrogen_capex_opex[iso3][year]

        # Calculate LCOH: (kWh/kg) × (USD/kWh) + (USD/kg) = USD/kg
        lcoh_by_country[iso3] = energy_consumption_kwh * electricity_price + capex_opex

    # Log a sample of LCOH values for verification
    sample_countries = list(lcoh_by_country.keys())[:3]
    for iso3 in sample_countries:
        logger.debug(
            "[ENERGY UNITS] LCOH %s year %s: %.2f USD/kg",
            iso3,
            year,
            lcoh_by_country[iso3],
        )

    return lcoh_by_country


def calculate_regional_hydrogen_ceiling_country_level(
    lcoh_by_country: dict[str, float],
    country_mappings: "CountryMappingService",
    hydrogen_ceiling_percentile: float,
) -> tuple[dict[str, float], dict[str, str]]:
    """
    Calculate hydrogen ceiling for each region as the Xth percentile of LCOH values. If no LCOH data
    is available for a region, the ceiling is set to the global maximum LCOH (equivalent to no ceiling).

    Args:
        lcoh_by_country: Dictionary mapping ISO3 codes to LCOH values
        country_mappings: Service to map countries to regions
        hydrogen_ceiling_percentile: Percentile to use for ceiling (0-100)

    Returns:
        Tuple of:
        - Dictionary mapping region names to ceiling values
        - Dictionary mapping ISO3 codes to region names

    Raises:
        ValueError: If no LCOH values are provided
    """
    logger = logging.getLogger(f"{__name__}.calculate_regional_hydrogen_ceiling_country_level")

    import numpy as np
    from collections import defaultdict

    if not lcoh_by_country:
        raise ValueError("No LCOH values calculated - cannot determine regional ceilings")

    # Group countries by region
    region_to_countries = defaultdict(list)
    country_to_region = {}
    for mapping in country_mappings._mappings.values():
        region = mapping.tiam_ucl_region
        iso3 = mapping.iso3
        region_to_countries[region].append(iso3)
        country_to_region[iso3] = region

    # Calculate hydrogen ceiling for each region (percentile-based)
    regional_ceilings = {}
    global_max = max(lcoh_by_country.values())

    for region, countries in region_to_countries.items():
        # Get LCOH values for countries in this region
        region_lcoh_values = [lcoh_by_country[iso3] for iso3 in countries if iso3 in lcoh_by_country]

        if region_lcoh_values:
            # Calculate percentile
            regional_ceilings[region] = float(np.percentile(region_lcoh_values, hydrogen_ceiling_percentile))
        else:
            # No data for this region, use global max
            if region != "Rest of World":
                logger.warning(f"No LCOH data for region {region}, using global max")
            regional_ceilings[region] = float(global_max)

    return regional_ceilings, country_to_region


def apply_hydrogen_price_cap_country_level(
    lcoh_by_country: dict[str, float],
    regional_ceilings: dict[str, float],
    country_to_region: dict[str, str],
    intraregional_trade_allowed: bool,
    intraregional_trade_matrix: dict[str, list[str]],
    long_dist_pipeline_transport_cost: float,
) -> dict[str, float]:
    """
    Apply hydrogen price capping for all countries based on regional ceilings and trade options.

    Options:
        a) If intraregional trade is NOT allowed, choose the minimum of the LCOH and the regional ceiling.
        Interregional trade is always allowed (or indirectly ignored via a hydrogen ceiling of the 100th percentile).
        b) If intraregional trade is allowed and there are regions to trade with, set the capped LCOH to the
        minimum of the LCOH, the regional ceiling, and the minimum regional ceiling of ANY of the regions in
        the cluster plus long distance transport costs per kg of hydrogen.

    Args:
        lcoh_by_country: Dictionary mapping ISO3 codes to LCOH values
        regional_ceilings: Dictionary mapping regions to ceiling values
        country_to_region: Dictionary mapping ISO3 codes to region names
        intraregional_trade_allowed: Whether intraregional trade is allowed
        intraregional_trade_matrix: Dictionary mapping regions to list of tradeable regions
        long_dist_pipeline_transport_cost: Transport cost for long-distance pipeline (USD/kg)

    Returns:
        Dictionary mapping ISO3 codes to capped hydrogen prices

    Raises:
        ValueError: If required data is missing
    """
    capped_hydrogen_prices = {}

    for iso3, lcoh in lcoh_by_country.items():
        region = country_to_region.get(iso3)
        if region is None:
            raise ValueError(f"No region mapping found for country {iso3}")

        # Get the regional ceiling
        ceiling_value = regional_ceilings.get(region)
        if ceiling_value is None:
            raise ValueError(f"No regional ceiling calculated for region {region}")

        # Option a: No intraregional trade allowed -> apply the cap: minimum of LCOH and ceiling
        if not intraregional_trade_allowed:
            capped_hydrogen_prices[iso3] = min(lcoh, ceiling_value)

        # Option b: Intraregional trade allowed -> consider trade options and choose the minimum among
        # the country's LCOH, its regional ceiling, and its intraregional trade options
        else:
            trade_regions = intraregional_trade_matrix.get(region)
            if trade_regions:
                ## Find the best trade option
                valid_trade_ceilings = [regional_ceilings[r] for r in trade_regions if r in regional_ceilings]
                if not valid_trade_ceilings:
                    raise ValueError(f"No valid trade partner regions found for {region}")
                best_trade_ceiling = min(valid_trade_ceilings)
                ## Add transport cost
                best_trade_value = best_trade_ceiling + long_dist_pipeline_transport_cost
                ## Take minimum of regional ceiling and trade option
                ceiling_value = min(ceiling_value, best_trade_value)
            ## Apply the cap: minimum of LCOH and ceiling
            capped_hydrogen_prices[iso3] = min(lcoh, ceiling_value)

    return capped_hydrogen_prices


# ----------------------------------------------------------------------------------------------------------


def calculate_cost_adjustments_from_secondary_outputs(
    bill_of_materials,
    dynamic_business_cases,
    input_costs,
) -> float:
    """
    Calculate per-unit cost adjustments from secondary outputs (by-products).

    The adjustment is expressed in USD per tonne of product. Revenues from by-products will
    return negative values (reducing production cost) while additional costs will return positive
    values.
    """
    if "materials" not in bill_of_materials or not dynamic_business_cases:
        return 0.0

    materials = bill_of_materials["materials"]
    if not materials:
        return 0.0

    dbc_by_metallic_charge = {dbc.metallic_charge: dbc for dbc in dynamic_business_cases}
    adjustments_outputs = {output: price for output, price in input_costs.items()}
    # NOTE: By-product revenues must be provided as negative values in input_costs (e.g., slag: -10 USD/t).

    total_adjustments = 0.0
    total_product_volume = 0.0

    for material, material_data in materials.items():
        dbc = dbc_by_metallic_charge.get(material)
        if not dbc:
            continue

        material_volume = material_data.get("demand", 0.0)
        if material_volume <= 0:
            continue

        product_volume = material_data.get("product_volume")
        if (product_volume is None or product_volume <= 0) and getattr(dbc, "required_quantity_per_ton_of_product", 0):
            required_qty = dbc.required_quantity_per_ton_of_product
            if required_qty:
                product_volume = material_volume / required_qty
        if product_volume is None or product_volume <= 0:
            continue

        total_product_volume += product_volume

        material_adjustment = sum(
            product_volume * adjustments_outputs[output] * dbc.outputs[output]  # multiply by output amounts per unit
            for output in (dbc.outputs or {})
            if output in adjustments_outputs
        )
        total_adjustments += material_adjustment

    return total_adjustments / total_product_volume if total_product_volume > 0 else 0.0
