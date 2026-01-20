import random
import numpy as np
from typing import Any, Callable, TypedDict

from steelo.domain.models import Subsidy
from steelo.domain.constants import Year
from steelo.logging_config import new_plant_logger


class NewPlantLocation(TypedDict):
    """Type definition for new plant location data."""

    Latitude: float
    Longitude: float
    iso3: str
    power_price: float
    capped_lcoh: float  # Hydrogen price (levelized cost of hydrogen)
    rail_cost: float


def select_location_subset(
    locations: dict,
    calculate_npv_pct: float,
) -> dict:
    """
    Randomly select a subset of top locations for detailed NPV assessment (potential business opportunities).

    Args:
        locations: Dictionary mapping products to lists of location dictionaries
        calculate_npv_pct: Percentage of locations to sample (0.0 to 1.0)

    Returns:
        Dictionary mapping products to sampled location lists

    Side Effects:
        Logs sampling information and sample locations
    """
    new_plant_logger.info(f"[NEW PLANTS] Sampling {calculate_npv_pct * 100}% of top locations for NPV calculation.")
    best_locations_subset = {}
    for product in ["iron", "steel"]:
        n = int(len(locations.get(product, [])) * calculate_npv_pct)
        best_locations_subset[product] = random.sample(locations[product], n) if n > 0 else []
        new_plant_logger.info(
            f"[NEW PLANTS] For {product}: Sampling n = {n} out of total locations = {len(locations.get(product, []))} for NPV calculation."
        )
        new_plant_logger.info(f"[NEW PLANTS] Sample {product} location: {locations[product][0]}")
    return best_locations_subset


def get_list_of_allowed_techs_for_target_year(
    allowed_techs: dict[Year, list[str]],
    tech_to_product: dict[str, str],
    target_year: Year,
) -> dict[str, list[str]]:
    """
    Get allowed technologies for steel and iron production at target year.

    Args:
        allowed_techs: Dictionary mapping years to lists of allowed technology names
        tech_to_product: Dictionary mapping technology names to product types
        target_year: Earliest possible construction start year (current year + consideration time + 1 year announcement lag)

    Returns:
        Dictionary mapping products (steel, iron) to lists of allowed technologies

    Raises:
        ValueError: If no allowed technologies defined for target year or no allowed technologies for a product

    Side Effects:
        Logs allowed technologies for target year

    Notes:
        - Business opportunities only allowed if technology is permitted in target year
        - Target year: earliest possible construction start year (current year + consideration time + 1 year announcement lag)
        - Technologies about to be banned are poor investments even if currently discussed
    """
    # No allowed techs are defined for the target year; raise error.
    if target_year not in allowed_techs:
        raise ValueError(
            f"[NEW PLANTS] No allowed technologies for year {target_year}. Check allowed_techs input: {allowed_techs}"
        )

    # Allowed techs are defined for the target year; filter technologies accordingly.
    else:
        allowed_techs_for_target_year = set(allowed_techs[target_year])

        # Invert tech_to_product to product_to_tech
        product_to_tech: dict[str, list[str]] = {}
        for tech, prod in tech_to_product.items():
            if prod not in product_to_tech:
                product_to_tech[prod] = []
            product_to_tech[prod].append(tech)
        product_to_tech = {k: v for k, v in product_to_tech.items() if k in ["steel", "iron"]}

        # Filter technologies based on allowed_techs for the relevant year
        for product in product_to_tech:
            original_techs = product_to_tech[product]
            product_to_tech[product] = [tech for tech in original_techs if tech in allowed_techs_for_target_year]
            if not product_to_tech[product]:
                raise ValueError(
                    f"[NEW PLANTS] No allowed technologies for product {product} in year {target_year}. "
                    f"All techs: {original_techs}, Allowed techs: {allowed_techs_for_target_year}"
                )

        new_plant_logger.info(
            f"[NEW PLANTS] Allowed technologies to consider as new business opportunities (based on allowed technologies in year {target_year}): {product_to_tech}"
        )
        return product_to_tech


def prepare_cost_data_for_business_opportunity(
    product_to_tech: dict[str, list[str]],
    best_locations_subset: dict[str, list[NewPlantLocation]],
    current_year: Year,
    target_year: Year,
    energy_costs: dict[str, dict[Year, dict[str, float]]],
    capex_dict_all_locs_techs: dict[str, dict[str, float]],
    cost_of_debt_all_locs: dict[str, float],
    cost_of_equity_all_locs: dict[str, float],
    fopex_all_locs_techs: dict[str, dict[str, float]],
    steel_plant_capacity: float,
    get_bom_from_avg_boms: Callable[
        [dict[str, float], str, float], tuple[dict[str, dict[str, dict[str, float]]] | None, float, str | None]
    ],
    iso3_to_region_map: dict[str, str],
    global_risk_free_rate: float,
    capex_subsidies: dict[str, dict[str, list[Subsidy]]],
    debt_subsidies: dict[str, dict[str, list[Subsidy]]],
    opex_subsidies: dict[str, dict[str, list[Subsidy]]],
    carbon_costs: dict[str, dict[Year, float]],
    most_common_reductant: dict[str, str],
) -> dict[str, dict[tuple[float, float, str], dict[str, dict[str, Any]]]]:
    """
    For each business opportunity (top location-technology pair), prepare all required inputs to calculate the NPV
    and create a new plant. If inputs are missing, skip the location-technology pair and log a warning.

    Args:
        product_to_tech: Dictionary mapping products to their allowed technologies (product -> list of techs)
        best_locations_subset: Dictionary of best locations per product with necessary site data (product -> list of dicts with lat,
            lon, iso3, power price, hydrogen price, railway cost)
        current_year: The current simulation year.
        target_year: The year when the plant would start operation (current year + consideration time + 1 year announcement lag)
        energy_costs: Nested dictionary with energy costs per country and year (iso3 -> year -> energy carrier -> cost)
        capex_dict_all_locs_techs: Nested dictionary with CAPEX values per region and technology (region -> tech -> capex)
        cost_of_debt_all_locs: Dictionary with cost of debt per country (iso3 -> cost of debt)
        cost_of_equity_all_locs: Dictionary with cost of equity per country (iso3 -> cost of equity)
        fopex_all_locs_techs: Nested dictionary with fixed OPEX values per country and technology (iso3 -> tech -> fopex)
        steel_plant_capacity: Capacity of the steel plant in tons per year
        get_bom_from_avg_boms: Function to retrieve the bill of materials and utilization rate for a given technology and energy costs
        iso3_to_region_map: Mapping from ISO3 country codes to regions for CAPEX lookup
        global_risk_free_rate: Global risk-free rate used in debt subsidy calculations
        capex_subsidies: Nested dictionary with CAPEX subsidies per country and technology (iso3 -> tech -> list of subsidies)
        debt_subsidies: Nested dictionary with debt subsidies per country and technology (iso3 -> tech -> list of subsidies)
        opex_subsidies: Nested dictionary with OPEX subsidies per country and technology (iso3 -> tech -> list of subsidies)
        carbon_costs: Dictionary with carbon cost series per country (iso3 -> year -> carbon cost)

    Returns:
        cost_data: Dictionary with all prepared cost data per product, site (lat, lon, iso3), and technology (product -> site_id ->
            tech -> cost_type -> cost) with:
            - railway_cost: railway cost for location
            - energy_costs: dict with energy costs for location. Electricity and hydrogen costs are taken from the own power parc.
            - cost_of_equity: cost of equity for location (with subsidies, if applicable)
            - cost_of_debt: cost of debt for location (with subsidies, if applicable)
            - capex: CAPEX for location and technology (with subsidies, if applicable)
            - fopex: fixed OPEX for location and technology
            - bom: dict with average BOMs for location and technology
            - utilization_rate: avg utilization rate for location and technology

    """
    from steelo.domain import calculate_costs as cc

    new_plant_logger.info("[NEW PLANTS] Preparing cost data for business opportunities for a subset of best locations.")
    cost_data: dict[
        str, dict[tuple[float, float, str], dict[str, dict[str, Any]]]
    ] = {}  # prod -> site_id (lat, lon, iso3) -> tech -> cost_type -> cost
    anomalous_power_prices_count = 0
    for prod, sites in best_locations_subset.items():
        if prod not in cost_data:
            cost_data[prod] = {}
        for site in sites:
            site_id = (site["Latitude"], site["Longitude"], site["iso3"])
            region = iso3_to_region_map.get(site["iso3"], "default")
            if site_id not in cost_data[prod]:
                cost_data[prod][site_id] = {}

            # Track critical missing site-level data
            incomplete_site = False
            site_missing_fields = []

            # Set the energy costs to those of the country and overwrite electricity and hydrogen costs with
            # custom values from the own power parc
            energy_costs_site = None
            if site["iso3"] not in energy_costs:
                site_missing_fields.append("energy_costs")
                incomplete_site = True
            else:
                energy_costs_site = energy_costs[site["iso3"]][current_year].copy()  # Copy to avoid modifying original
                elec_ratio = (
                    site["power_price"] / energy_costs_site["electricity"]
                    if energy_costs_site["electricity"] != 0
                    else float("inf")
                )
                if not (0.1 <= elec_ratio <= 10):
                    anomalous_power_prices_count += 1
                energy_costs_site["electricity"] = site["power_price"]
                energy_costs_site["hydrogen"] = site["capped_lcoh"]

            # Get cost of equity and debt for country
            cost_of_equity = cost_of_equity_all_locs.get(site["iso3"], None)
            if not cost_of_equity:
                site_missing_fields.append("cost_of_equity")
                incomplete_site = True

            cost_of_debt = cost_of_debt_all_locs.get(site["iso3"], None)
            if not cost_of_debt:
                site_missing_fields.append("cost_of_debt")
                incomplete_site = True

            # If critical site-level data is missing, raise an error
            if incomplete_site:
                raise ValueError(
                    f"[NEW PLANTS] Missing critical site-level data for site {site_id} ({site['iso3']}): {', '.join(site_missing_fields)}. "
                    f"All cost data must be available for business opportunity evaluation."
                )

            for tech in product_to_tech[prod]:
                if tech not in cost_data[prod][site_id]:
                    cost_data[prod][site_id][tech] = {}

                # Track missing fields for logging
                missing_critical_fields = []

                # Always add railway cost, energy costs, and cost of equity; equal for all technologies
                if site["rail_cost"] is None:
                    missing_critical_fields.append("railway_cost")
                else:
                    cost_data[prod][site_id][tech]["railway_cost"] = site["rail_cost"]
                ## Validity checked above (for entire site)
                cost_data[prod][site_id][tech]["energy_costs"] = energy_costs_site  # type: ignore[assignment]
                cost_data[prod][site_id][tech]["cost_of_equity"] = cost_of_equity  # type: ignore[assignment]

                # Add average BOM and utilization rate per technology if available
                # energy_costs_site is guaranteed to not be None here (checked above with incomplete_site)
                assert energy_costs_site is not None  # Help mypy understand the control flow
                bom_result = get_bom_from_avg_boms(
                    energy_costs_site, tech, int(steel_plant_capacity), most_common_reductant.get(tech, "")
                )
                bill_of_materials, util_rate, reductant = bom_result
                if bill_of_materials is None:
                    missing_critical_fields.append("bom")
                else:
                    cost_data[prod][site_id][tech]["bom"] = bill_of_materials
                if util_rate is None:
                    missing_critical_fields.append("utilization_rate")
                else:
                    cost_data[prod][site_id][tech]["utilization_rate"] = util_rate
                if reductant is None:
                    missing_critical_fields.append("reductant")
                else:
                    cost_data[prod][site_id][tech]["reductant"] = reductant  # type: ignore[assignment]

                # Add fixed OPEX per technology if available
                fopex_all_techs = fopex_all_locs_techs.get(site["iso3"])
                if not fopex_all_techs:
                    missing_critical_fields.append("fopex")
                else:
                    fopex = fopex_all_techs.get(tech.lower())
                    if fopex is not None:
                        cost_data[prod][site_id][tech]["fopex"] = fopex  # type: ignore[assignment]
                    else:
                        missing_critical_fields.append(f"fopex for technology {tech}")

                # Add CAPEX per technology if available (including subsidies if applicable)
                capex = capex_dict_all_locs_techs.get(region, {}).get(tech, None)
                if not capex:
                    missing_critical_fields.append("capex")
                else:
                    all_capex_subsidies = capex_subsidies.get(site["iso3"], {}).get(tech, [])
                    selected_capex_subsidies = cc.filter_active_subsidies(all_capex_subsidies, target_year)
                    capex_with_subsidies = cc.calculate_capex_with_subsidies(capex, selected_capex_subsidies)
                    cost_data[prod][site_id][tech]["capex"] = capex_with_subsidies
                    cost_data[prod][site_id][tech]["capex_no_subsidy"] = capex

                # Always add cost of debt with subsidies (since it's technology-agnostic but can have tech-specific subsidies)
                all_debt_subsidies = debt_subsidies.get(site["iso3"], {}).get(tech, [])
                selected_debt_subsidies = cc.filter_active_subsidies(all_debt_subsidies, target_year)
                cost_of_debt_with_subsidies = cc.calculate_debt_with_subsidies(
                    # cost_of_debt is guaranteed to not be None here due to incomplete_site check
                    cost_of_debt=cost_of_debt,  # type: ignore[arg-type]
                    debt_subsidies=selected_debt_subsidies,
                    risk_free_rate=global_risk_free_rate,
                )
                cost_data[prod][site_id][tech]["cost_of_debt"] = cost_of_debt_with_subsidies  # type: ignore[assignment]
                cost_data[prod][site_id][tech]["cost_of_debt_no_subsidy"] = cost_of_debt

                # pass opex subsidies to be considered in npv calculation
                cost_data[prod][site_id][tech]["all_opex_subsidies"] = opex_subsidies.get(site["iso3"], {}).get(
                    tech, []
                )  # type: ignore[assignment]
                cost_data[prod][site_id][tech]["carbon_cost_series"] = carbon_costs.get(site["iso3"])  # type: ignore[assignment]

                # Raise error if any critical fields are missing
                if missing_critical_fields:
                    raise ValueError(
                        f"[NEW PLANTS] Missing critical cost data for {tech} at site {site_id} ({site['iso3']}): {', '.join(missing_critical_fields)}. "
                        f"All cost data must be available for business opportunity evaluation."
                    )

    # Log error if more than 30% of the sampled locations have anomalous power prices
    if anomalous_power_prices_count > len(sites) * 0.3:
        new_plant_logger.error(
            """[NEW PLANTS] More than 30% of the sampled locations have power prices for the own power parc that differ from the local grid " \n
            power price by more than one OOM. Please check the units (expected in USD/kWh)."""
        )

    return validate_and_clean_cost_data(cost_data)


def validate_and_clean_cost_data(
    cost_data: dict[str, dict[tuple[float, float, str], dict[str, dict[str, Any]]]],
) -> dict[str, dict[tuple[float, float, str], dict[str, dict[str, Any]]]]:
    """
    Validate and clean cost data by removing incomplete or invalid entries.

    Args:
        cost_data: Nested dictionary with cost data (product -> site_id -> tech -> cost_type -> cost)

    Returns:
        Cleaned cost data with only complete and valid entries

    Raises:
        ValueError: If invalid data types detected or no valid cost data for any business opportunity

    Side Effects:
        Logs sample of prepared cost data
    """
    # Define required fields (and the expected types for some)
    float_fields = [
        "cost_of_equity",
        "cost_of_debt",
        "cost_of_debt_no_subsidy",
        "capex",
        "capex_no_subsidy",
        "fopex",
        "utilization_rate",
    ]
    string_fields = ["reductant"]
    list_fields = ["all_opex_subsidies"]
    required_fields = (
        float_fields + string_fields + list_fields + ["railway_cost", "energy_costs", "bom", "carbon_cost_series"]
    )

    # Run through all products, sites, and technologies
    for prod in list(cost_data.keys()):
        for site_id in list(cost_data[prod].keys()):
            complete_techs = {}
            for tech, tech_data in cost_data[prod][site_id].items():
                if set(tech_data.keys()) == set(required_fields):
                    # Validate data types for each field
                    try:
                        # railway_cost: float or int
                        if not isinstance(tech_data["railway_cost"], (float, int)):
                            raise ValueError(
                                f"railway_cost must be float or int, got {type(tech_data['railway_cost']).__name__}: {tech_data['railway_cost']}"
                            )

                        # energy_costs: dict of floats or ints
                        if not isinstance(tech_data["energy_costs"], dict):
                            raise ValueError(
                                f"energy_costs must be dict, got {type(tech_data['energy_costs']).__name__}: {tech_data['energy_costs']}"
                            )
                        for energy_type, energy_cost in tech_data["energy_costs"].items():
                            if not isinstance(energy_cost, (float, int)):
                                raise ValueError(
                                    f"energy_costs['{energy_type}'] must be float or int, got {type(energy_cost).__name__}: {energy_cost}"
                                )

                        # float-only fields
                        for field in float_fields:
                            if not isinstance(tech_data[field], float):
                                raise ValueError(
                                    f"{field} must be float, got {type(tech_data[field]).__name__}: {tech_data[field]}"
                                )

                        # string-only fields
                        for field in string_fields:
                            if tech_data[field] is not None and not isinstance(tech_data[field], str):
                                raise ValueError(
                                    f"{field} must be str or None, got {type(tech_data[field]).__name__}: {tech_data[field]}"
                                )

                        # list fields (e.g., all_opex_subsidies which is a list of Subsidy objects)
                        for field in list_fields:
                            if not isinstance(tech_data[field], list):
                                raise ValueError(
                                    f"{field} must be list, got {type(tech_data[field]).__name__}: {tech_data[field]}"
                                )

                        # carbon_cost_series: dict[Year, float] or None
                        if tech_data["carbon_cost_series"] is not None:
                            if not isinstance(tech_data["carbon_cost_series"], dict):
                                raise ValueError(
                                    f"carbon_cost_series must be dict or None, got {type(tech_data['carbon_cost_series']).__name__}"
                                )

                        # bom: dict of floats
                        if not isinstance(tech_data["bom"], dict):
                            raise ValueError(f"bom must be dict, got {type(tech_data['bom']).__name__}")
                        for bom_item, bom_value in tech_data["bom"].items():
                            if not isinstance(bom_value, (float, dict)):
                                raise ValueError(
                                    f"bom['{bom_item}'] must be float or dict, got {type(bom_value).__name__}: {bom_value}"
                                )
                            if isinstance(bom_value, dict):
                                # Handle nested dict in BOM (e.g., for different years)
                                for sub_key, sub_value in bom_value.items():
                                    if not isinstance(sub_value, (float, dict)):
                                        raise ValueError(
                                            f"bom['{bom_item}']['{sub_key}'] must be float or dict, got {type(sub_value).__name__}: {sub_value}"
                                        )
                        # Store complete and valid tech data
                        complete_techs[tech] = tech_data
                    # Raise ValueError for invalid data types
                    except ValueError as e:
                        raise ValueError(f"[NEW PLANTS] Invalid data type for {tech} in {site_id[2]}: {e}") from e
                # Skip incomplete techs
                else:
                    pass

            # Update site with only complete technologies
            if complete_techs:
                cost_data[prod][site_id] = complete_techs
            else:
                # Remove site if no complete technologies
                del cost_data[prod][site_id]

    # Check that the cost data has at least one non-empty entry; valid cost data was prepared for a single business opportunity
    if not any(
        cost_data[product][site_id][tech]
        for product in cost_data
        for site_id in cost_data[product]
        for tech in cost_data[product][site_id]
    ):
        raise ValueError(
            "[NEW PLANTS] No valid cost data for any business opportunity. Check dict structure and data types."
        )

    # Log sample of prepared cost data
    for product, sites in cost_data.items():
        for site_id, techs in sites.items():
            for tech, costs in techs.items():
                new_plant_logger.debug(f"[NEW PLANTS] Sample costs data for {product} x {site_id[2]} x {tech}: {costs}")
                break
            break
        break

    return cost_data


def select_top_opportunities_by_npv(
    npv_dict: dict[str, dict[Any, dict[str, float]]],
    top_n_loctechs_as_business_op: int,
) -> dict[str, dict[tuple[float, float, str], dict[str, float]]]:
    """
    Select top location-technology combinations with high NPVs using weighted random sampling to ensure mix of opportunities.

    Args:
        npv_dict: Nested dictionary with NPV values (product -> site_id -> tech -> NPV)
        top_n_loctechs_as_business_op: Number of top location-technology combinations to select per product

    Returns:
        Dictionary mapping products to site IDs to technologies with their NPVs (product -> site_id (lat, lon, iso3) -> tech -> NPV)

    Raises:
        ValueError: If no valid NPVs found for a product

    Side Effects:
        - Logs NPV analysis statistics (valid, NaN, -inf combinations)
        - Logs selected top opportunities

    Notes:
        - Invalid NPV values (NaN, -inf) are removed before processing
        - Random selection weighted by NPV ensures mix of high and medium NPV options rather than only highest
        - If NPVs contain negative values, distribution is shifted to create non-negative weights
    """
    new_plant_logger.info(
        f"[NEW PLANTS] Selecting top {top_n_loctechs_as_business_op} location-technology combinations with high NPVs as "
        "business opportunities (per product and year)."
    )
    top_business_opportunities: dict[str, dict[tuple[float, float, str], dict[str, float]]] = {}

    # Collect all valid (site_id, tech) pairs with their NPVs. Valid NPVs are those that are not NaN or -inf.
    for product in npv_dict:
        valid_pairs = []
        valid_npvs = []
        nan_count = 0
        neg_inf_count = 0
        for site_id, techs in npv_dict[product].items():
            for tech, npv in techs.items():
                if np.isnan(npv):
                    nan_count += 1
                    new_plant_logger.debug(f"  NaN: site={site_id}, tech={tech}, NPV={npv}")
                elif npv == float("-inf"):
                    neg_inf_count += 1
                    new_plant_logger.debug(f"  -inf: site={site_id}, tech={tech}, NPV={npv}")
                else:
                    valid_pairs.append((site_id, tech))
                    valid_npvs.append(npv)
        total_combinations = len(valid_pairs) + nan_count + neg_inf_count
        new_plant_logger.debug(f"[NEW PLANTS] NPV analysis for product {product}:")
        new_plant_logger.debug(f"  Valid combinations: {len(valid_pairs)}/{total_combinations}")
        new_plant_logger.debug(f"  NaN combinations: {nan_count}/{total_combinations}")
        new_plant_logger.debug(f"  -inf combinations: {neg_inf_count}/{total_combinations}")
        if len(valid_pairs) == 0:
            raise ValueError(
                f"[NEW PLANTS] No valid NPVs found for product {product}. Skipping opportunity identification. "
                f"NPV dict for {product}: {npv_dict.get(product, {})}"
            )

        # Create non-negative weights by shifting the NPV distribution if needed
        npvs_array = np.array(valid_npvs)
        min_npv = np.min(npvs_array)
        if min_npv < 0:
            weights = npvs_array - min_npv
        else:
            weights = npvs_array
        if weights.sum() == 0:
            continue
        probabilities = weights / weights.sum()

        # Randomly select top N indices (weighted by NPV - the higher the more likely to be selected)
        if len(valid_pairs) >= top_n_loctechs_as_business_op:
            selected_indices = np.random.choice(
                len(valid_pairs), size=top_n_loctechs_as_business_op, replace=False, p=probabilities
            )
            selected_pairs = [valid_pairs[i] for i in selected_indices]
        else:
            selected_pairs = valid_pairs

        # Format selected (site, tech) pairs into business opportunities dict
        top_business_opportunities[product] = {}
        for site_id, tech in selected_pairs:
            if site_id not in top_business_opportunities[product]:
                top_business_opportunities[product][site_id] = {}
            top_business_opportunities[product][site_id][tech] = npv_dict[product][site_id][tech]
    # Log selected top opportunities in a more readable format
    for product, sites in top_business_opportunities.items():
        new_plant_logger.info(f"[NEW PLANTS] Selected top opportunities for {product}:")
        for site_id, techs in sites.items():
            site_str = f"  Site (lat={site_id[0]}, lon={site_id[1]}, iso3={site_id[2]}):"
            tech_strs = [f"{tech} with NPV: {npv:.2f}" for tech, npv in techs.items()]
            new_plant_logger.info(f"{site_str} {'; '.join(tech_strs)}")

    return top_business_opportunities
