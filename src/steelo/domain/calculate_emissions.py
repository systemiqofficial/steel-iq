import logging
from typing import TYPE_CHECKING

from steelo.utilities.utils import normalize_energy_key

if TYPE_CHECKING:
    from .models import PrimaryFeedstock
    from .models import TechnologyEmissionFactors


def materiall_bill_business_case_match(
    dynamic_feedstocks: dict[str, list["PrimaryFeedstock"]] | list["PrimaryFeedstock"],
    material_bill: dict[str, dict[str, float]] | list[str],
    tech: str,
    reductant: str | None = None,
) -> dict[str, "PrimaryFeedstock"]:
    """Match materials in bill of materials to their corresponding business cases.

    Links actual material consumption (from BOM) to technology-specific process definitions
    (business cases) based on metallic charge type and reductant. Required for emissions
    and cost calculations.

    Args:
        dynamic_feedstocks: Either a dict mapping technology names to lists of PrimaryFeedstock
            objects, or a flat list of PrimaryFeedstock objects. Contains all available
            business case definitions.
        material_bill: Either a dict with material names as keys (from actual BOM), or a
            list of material name strings. Materials to match.
        tech: Technology name (e.g., "BF", "EAF", "DRI") to filter business cases.
        reductant: Optional reductant type (e.g., "coke", "natural_gas", "hydrogen") for
            additional filtering. If None, matches any reductant.

    Returns:
        Dict mapping material names (lowercase) to their matched PrimaryFeedstock objects.
        Only includes materials that found a matching business case.

    Example:
        >>> feedstocks = {"BF": [bc_iron_ore_coke, bc_pellets_coke]}
        >>> bom = {"iron_ore": {"demand": 1000}, "pellets": {"demand": 500}}
        >>> matches = materiall_bill_business_case_match(feedstocks, bom, "BF", "coke")
        >>> # Returns: {"iron_ore": bc_iron_ore_coke, "pellets": bc_pellets_coke}

    Notes:
        - Matching is case-insensitive for metallic charge names.
        - Reductant matching is exact (case-sensitive string comparison).
        - If multiple business cases match, only the first is returned.
        - Unmatched materials are silently excluded from the result.
    """

    if isinstance(dynamic_feedstocks, list):
        relevant_bcs = dynamic_feedstocks
    else:
        relevant_bcs = dynamic_feedstocks.get(tech, [])
    # print(
    #     f"Relevant business cases for {tech}: {relevant_bcs} and their metallic charges: {[bc.metallic_charge for bc in relevant_bcs]}"
    # )
    # print(f"Material bill: {material_bill}")
    # print(f"Reductant: {reductant}, {str(reductant)}")

    _bcs = {}
    for materials_in_bom in material_bill:
        for bc in relevant_bcs:
            if materials_in_bom.lower() == bc.metallic_charge.lower() and reductant == str(bc.reductant):
                _bcs[materials_in_bom] = bc  # store the bc's needed to evaluate the emissions for the material bill

    return _bcs


def calculate_emissions(
    material_bill: dict[str, dict[str, float]],
    business_cases: dict[str, "PrimaryFeedstock"],
    technology_emission_factors: list["TechnologyEmissionFactors"],
    installed_carbon_capture: float = 0.0,
    grid_emissions: float = 0.0,
) -> dict[str, dict[str, float]]:
    """Calculate total emissions for a furnace group accounting for CCS/CCU technology.

    Computes comprehensive emissions across multiple boundaries (plant_boundary, supply_chain, etc.)
    and scopes (direct, indirect, biomass) based on material consumption, technology emission factors,
    and grid electricity use. Applies carbon capture reductions to direct emissions.

    The calculation process:
        1. Match materials in bill to business cases
        2. Calculate production volume from material demands
        3. Look up emission factors by technology, reductant, and metallic charge
        4. Compute emissions per boundary/scope using emission factors × production
        5. Add grid emissions to indirect scope
        6. Apply CCS/CCU: reduce direct_ghg by installed_carbon_capture (floor at 0)

    Args:
        material_bill: Dict mapping material names to {"demand": float, "total_cost": float, "unit_cost": float}.
            Materials consumed by the furnace group (e.g., iron_ore, scrap_steel).
        business_cases: Dict mapping material names to PrimaryFeedstock objects.
            Matched business cases defining process requirements and emission characteristics.
        technology_emission_factors: List of TechnologyEmissionFactors objects containing
            emission intensities (tCO2e per tonne product) for each technology/reductant/material
            combination across different boundaries and scopes.
        installed_carbon_capture: Carbon capture capacity in tCO2e per year. Reduces direct
            emissions via CCS (Carbon Capture and Storage) or CCU (Carbon Capture and Utilization).
            Default 0.0 means no capture installed.
        grid_emissions: Total grid electricity emissions in tCO2e, calculated separately.
            Added to indirect_ghg scope. Default 0.0.

    Returns:
        Nested dict with structure:
            {
                boundary_name: {  # e.g., "plant_boundary", "supply_chain"
                    "direct_ghg": float,              # Direct emissions minus carbon capture
                    "direct_with_biomass_ghg": float, # Direct including biogenic carbon
                    "indirect_ghg": float             # Indirect emissions plus grid
                }
            }
        Returns empty dict {} if no valid business cases or emission factors found.

    Notes:
        - Carbon capture is subtracted from direct_ghg ONLY, not from other scopes.
        - Carbon capture cannot create negative emissions (max function ensures >= 0).
        - Production volume calculated as: material_demand / required_quantity_per_ton_of_product
        - Grid emissions added to indirect_ghg for all boundaries.
        - Skips materials with zero/None required_quantity_per_ton_of_product.
        - Aggregates emissions across multiple materials in the bill of materials.
    """
    total_emissions: dict[str, dict[str, float]] = {}
    for material, bc in business_cases.items():
        if bc.required_quantity_per_ton_of_product is None or bc.required_quantity_per_ton_of_product == 0:
            continue  # Skip if no valid quantity

        if material not in material_bill:
            continue

        if "demand" in material_bill[material]:
            amount_of_product = material_bill[material]["demand"] / bc.required_quantity_per_ton_of_product
        else:
            amount_of_product = material_bill[material]["demand_share_pct"]

        normalized_bc_reductant = normalize_energy_key(bc.reductant)
        selected_technology_emission_factors = [
            factor
            for factor in technology_emission_factors
            if factor.technology.lower() == bc.technology.lower()
            and normalize_energy_key(factor.reductant) == normalized_bc_reductant
            and factor.metallic_charge == bc.metallic_charge
        ]

        # emission boundaries
        conventions = [factor.boundary for factor in selected_technology_emission_factors]

        convention_emissions = {}

        for conv in conventions:
            direct_ghg_factor = [
                factor.direct_ghg_factor for factor in selected_technology_emission_factors if factor.boundary == conv
            ]
            direct_with_biomass_ghg_factor = [
                factor.direct_with_biomass_ghg_factor
                for factor in selected_technology_emission_factors
                if factor.boundary == conv
            ]
            indirect_ghg_factor = [
                factor.indirect_ghg_factor for factor in selected_technology_emission_factors if factor.boundary == conv
            ]

            convention_emissions[conv] = {
                "direct_ghg": direct_ghg_factor[0] * amount_of_product if direct_ghg_factor else 0.0,
                "direct_with_biomass_ghg": direct_with_biomass_ghg_factor[0] * amount_of_product
                if direct_with_biomass_ghg_factor
                else 0.0,
                "indirect_ghg": indirect_ghg_factor[0] * amount_of_product + grid_emissions
                if indirect_ghg_factor
                else 0.0,
            }

        if not total_emissions:
            total_emissions.update(convention_emissions)
        else:
            for convention, emissions in convention_emissions.items():
                if convention not in total_emissions:
                    total_emissions[convention] = emissions
                else:
                    for scope, value in emissions.items():
                        total_emissions[convention][scope] += value

        for convention in total_emissions:
            if "direct_ghg" in total_emissions[convention]:
                total_emissions[convention]["direct_ghg"] = max(
                    total_emissions[convention]["direct_ghg"] - installed_carbon_capture, 0
                )
            else:
                total_emissions[convention]["direct_ghg"] = 0.0

    return total_emissions


def calculate_emissions_cost_series(
    emissions: dict[str, dict[str, float]] | None,
    carbon_price_dict: dict,
    chosen_emission_boundary: str,
    start_year,
    end_year,
) -> list[float]:
    """Calculate annual carbon cost series over a time period.

    Computes carbon costs for each year by multiplying direct emissions by the year's
    carbon price. Used for NPV calculations and long-term economic analysis.

    Args:
        emissions: Emissions data by boundary and scope. Structure:
            {boundary: {"direct_ghg": float, "indirect_ghg": float, ...}}
            None or empty dict treated as zero emissions.
        carbon_price_dict: Mapping of years to carbon prices in USD/tCO2e.
            Missing years default to 0.0 price.
        chosen_emission_boundary: Emissions boundary to use for cost calculation
            (e.g., "plant_boundary", "supply_chain").
        start_year: First year of the series (inclusive).
        end_year: Last year of the series (inclusive).

    Returns:
        List of annual carbon costs in USD, one value per year from start_year to end_year.
        Returns all zeros if:
            - emissions is None/empty
            - chosen_emission_boundary not in emissions
            - "direct_ghg" not in the chosen boundary

    Example:
        >>> emissions = {"plant_boundary": {"direct_ghg": 100000}}
        >>> prices = {2025: 50, 2026: 60, 2027: 70}
        >>> series = calculate_emissions_cost_series(emissions, prices, "plant_boundary", 2025, 2027)
        >>> # Returns: [5000000.0, 6000000.0, 7000000.0]

    Notes:
        - Only direct_ghg emissions incur carbon costs in this calculation.
        - Carbon costs after CCS/CCU are already reflected in the emissions input.
        - Length of returned list = (end_year - start_year + 1).
    """
    logger = logging.getLogger(f"{__name__}.calculate_emissions_cost_series")
    logger.debug(
        f"[EMISSIONS COST SERIES]: Calculating emissions series for {chosen_emission_boundary} "
        f"from {start_year} to {end_year}"
    )
    logger.debug(f"[EMISSIONS COST SERIES]: Emissions data: {emissions}")
    logger.debug(f"[EMISSIONS COST SERIES]: Carbon price data: {carbon_price_dict}")
    logger.debug(f"[EMISSIONS COST SERIES]: Chosen emission boundary: {chosen_emission_boundary}")

    if not emissions or emissions is None:
        return [0.0] * (end_year - start_year + 1)
    elif chosen_emission_boundary not in emissions:
        logger.debug(
            f"Emissions boundary {chosen_emission_boundary} not found in emissions data. Returning zero series."
        )
        return [0.0] * (end_year - start_year + 1)
    elif chosen_emission_boundary in emissions and "direct_ghg" not in emissions[chosen_emission_boundary]:
        logger.debug(
            f"Emissions data for {chosen_emission_boundary} does not contain 'direct_ghg' emissions. "
            "Returning zero series."
        )
        return [0.0] * (end_year - start_year + 1)
    else:
        return [
            emissions[chosen_emission_boundary]["direct_ghg"] * carbon_price_dict.get(year, 0.0)
            for year in range(start_year, end_year + 1)
        ]


# def find_emission_key(material_name: str, emissions_dict: dict[str, dict[str, float]]) -> str | None:
#     """
#     Case-insensitive lookup: returns the exact key from emissions_dict
#     whose lowercase matches material_name.lower(), or None if no match.
#     """
#     mat_l = material_name.lower().strip()

#     for k in emissions_dict:
#         if k.lower().strip() == mat_l:
#             return k
#     return None


# def compute_emissions_for_convention(
#     total_dict: dict[str, float],
#     emissions_factors: dict[str, dict[str, float]],
#     optional_emission_factors: dict[str, dict[str, float]] = {},
# ) -> tuple[dict[str, float], float]:
#     """
#     Computes the emissions for a given material bill and emissions factors.

#     Args:
#         - total_dict: a dict mapping material names → quantities (tons)
#         - emissions_factors: a dict mapping material names → dict of scope names and their emission factors
#         - optional_emission_factors: a dict mapping carrier name to emission factors (e.g. regional_grid_emissions)
#     Returns:
#       - emissions_by_scope: a dict mapping each scope name → total tons CO₂e
#       - total_emissions: sum of all non‐NaN scope contributions
#     """
#     # Initialize accumulator:
#     for key in optional_emission_factors:
#         if key in emissions_factors:
#             emissions_factors[key].update(optional_emission_factors[key])
#     scopes = next(iter(emissions_factors.values())).keys()  # Get the scopes from the first factor
#     emissions_by_scope: dict[str, float] = {scope: 0.0 for scope in scopes}
#     # print(total_dict)
#     for material, qty in total_dict.items():
#         if qty == 0:
#             continue
#         # 4a) Find the matching key in emissions_factors (case‐insensitive)
#         match_key = find_emission_key(material, emissions_factors)
#         if match_key is None:
#             logging.debug(f"Couldn't find emission factor and material: {material}, {emissions_factors}")
#             # No factor available for this material—skip or warn
#             # e.g. you could log: print(f"No emission factor found for {material!r}")
#             continue
#         else:
#             factors = emissions_factors[match_key]

#         # 4b) For each scope, if factor is not NaN, multiply by quantity:
#         for scope in scopes:
#             factor = factors.get(scope, math.nan)
#             if factor is not None and not math.isnan(factor):
#                 emissions_by_scope[scope] += qty * factor

#     # 4c) Sum up all the non‐NaN scope contributions for a single “total”:
#     total_emissions = sum(emissions_by_scope.values())
#     return emissions_by_scope, total_emissions


# def calculate_emissions_old(
#     material_bill: dict[str, dict[str, float]],
#     business_cases: dict[str, "PrimaryFeedstock"],
#     optional_emissions_intensity: dict[str, dict[str, float]] | None = {},
#     installed_carbon_capture: float = 0.0,
# ) -> dict[str, dict[str, dict[str, float]]]:
#     """
#     Calculate the total emissions for a given material bill and business cases.

#     Args:
#         material_bill (dict): A dictionary containing the material bill with material names as keys and their
#                                 respective quantities as values.
#         business_cases (dict): A dictionary containing business cases with material names as keys and their
#                                 respective business case objects as values.
#         optional_emissions_intensity (dict): A dictionary containing optional emissions intensity values for a specific energy carrier (e.g. regional_grid_emissions).
#     """
#     total_emissions: dict[str, dict[str, dict[str, float]]] = {}
#     for material, bc in business_cases.items():
#         if bc.required_quantity_per_ton_of_product is None or bc.required_quantity_per_ton_of_product == 0:
#             continue  # Skip if no valid quantity

#         if material not in material_bill:
#             continue

#         if "demand" in material_bill[material]:
#             amount_of_product = material_bill[material]["demand"] / bc.required_quantity_per_ton_of_product
#         else:
#             amount_of_product = material_bill[material]["demand_share_pct"]

#         total_dict = {bc.metallic_charge: bc.required_quantity_per_ton_of_product or 0.0}
#         total_dict.update(bc.secondary_feedstock)
#         total_dict.update(bc.energy_requirements)

#         convention_emissions = {}
#         # Check if emissions data exists for this business case
#         if bc.name not in bc.emissions:
#             logger.warning(f"No emissions data found for business case: {bc.name}")
#             continue

#         # we create a copy of the emissions factors to avoid modifying the original data
#         iterating_emissions = {
#             conv: {
#                 material: scope_dict.copy()  # copies the innermost dict[str,float]
#                 for material, scope_dict in material_map.items()
#             }
#             for conv, material_map in bc.emissions[bc.name].items()
#         }

#         for convention, emissions_factors in iterating_emissions.items():
#             em_by_scope, _ = compute_emissions_for_convention(
#                 total_dict, emissions_factors.copy(), optional_emissions_intensity
#             )
#             convention_emissions[convention] = {key: value * amount_of_product for key, value in em_by_scope.items()}

#         if not total_emissions:
#             total_emissions.update(convention_emissions)
#         else:
#             for convention, emissions in convention_emissions.items():
#                 if convention not in total_emissions:
#                     total_emissions[convention] = emissions
#                 else:
#                     for scope, value in emissions.items():
#                         total_emissions[convention][scope] += value

#         for convention in total_emissions:
#             if "ghg_factor_scope_1" in total_emissions[convention]:
#                 total_emissions[convention]["ghg_factor_scope_1"] = max(
#                     total_emissions[convention]["ghg_factor_scope_1"] - installed_carbon_capture, 0
#                 )
#             else:
#                 total_emissions[convention]["ghg_factor_scope_1"] = 0.0

#     return total_emissions


def calculate_emissions_cost_in_year(
    emissions: dict[str, dict[str, dict[str, float]]] | None, carbon_price: float, chosen_emission_boundary: str
) -> float:
    """Calculate total carbon cost for a single year.

    Multiplies direct GHG emissions by the carbon price to determine annual carbon costs.
    Used for yearly operational cost calculations.

    Args:
        emissions: Emissions data structured as:
            {boundary: {"direct_ghg": float, "indirect_ghg": float, ...}}
            Can be None for zero emissions.
        carbon_price: Carbon price for the year in USD per tCO2e.
        chosen_emission_boundary: Emissions boundary to use for cost calculation
            (e.g., "plant_boundary", "supply_chain").

    Returns:
        Total carbon cost in USD for the year. Returns 0.0 if:
            - emissions is None or empty
            - chosen_emission_boundary not found in emissions
            - "direct_ghg" scope not present in the boundary

    Example:
        >>> emissions = {"plant_boundary": {"direct_ghg": 100000, "indirect_ghg": 50000}}
        >>> cost = calculate_emissions_cost_in_year(emissions, 75.0, "plant_boundary")
        >>> # Returns: 7500000.0 (only direct_ghg counted)

    Notes:
        - Only direct_ghg emissions are priced; indirect/biogenic scopes excluded.
        - Emissions should already reflect CCS/CCU reductions if applicable.
        - Logs warnings if boundary or scope keys are missing.
    """
    logger = logging.getLogger(f"{__name__}.calculate_emissions_cost_in_year")
    if not emissions or emissions is None:
        return 0.0
    elif chosen_emission_boundary not in emissions:
        logger.warning(
            f"Emissions boundary {chosen_emission_boundary} not found in emissions data. Returning 0.0 carbon costs."
        )
        return 0.0
    elif chosen_emission_boundary in emissions and "direct_ghg" not in emissions[chosen_emission_boundary]:
        logger.warning(
            f"Emissions data for {chosen_emission_boundary} does not contain 'direct_ghg' emissions. "
            "Returning 0.0 carbon costs."
        )
        return 0.0
    else:
        return emissions[chosen_emission_boundary]["direct_ghg"] * carbon_price  # type: ignore
