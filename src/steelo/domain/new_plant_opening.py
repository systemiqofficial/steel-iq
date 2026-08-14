import logging
import random

import numpy as np
from typing import Any, Callable, TypedDict

from steelo.domain.models import Location, Subsidy, compose_geo_key
from steelo.domain.constants import Year, T_TO_KG


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
    logger = logging.getLogger(f"{__name__}.select_location_subset")
    logger.info(f"[NEW PLANTS] Sampling {calculate_npv_pct * 100}% of top locations for NPV calculation.")
    best_locations_subset = {}
    for product in ["iron", "steel"]:
        n = int(len(locations.get(product, [])) * calculate_npv_pct)
        best_locations_subset[product] = random.sample(locations[product], n) if n > 0 else []
        logger.info(
            f"[NEW PLANTS] For {product}: Sampling n = {n} out of total locations = {len(locations.get(product, []))} for NPV calculation."
        )
        if best_locations_subset[product]:
            logger.info(f"[NEW PLANTS] Sample {product} location: {best_locations_subset[product][0]}")
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

        logger = logging.getLogger(f"{__name__}.get_list_of_allowed_techs_for_target_year")
        logger.info(
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
    cost_of_debt_all_locs: dict[str, dict[str, float]],
    cost_of_equity_all_locs: dict[str, dict[str, float]],
    fopex_all_locs_techs: dict[str, dict[str, float]],
    steel_plant_capacity: float,
    plant_lifetime: int,
    construction_time: int,
    get_bom_from_avg_boms: Callable[
        [dict[str, float], str, float, str | None],
        tuple[dict[str, dict[str, dict[str, float]]] | None, float, str, dict[str, float]],
    ],
    reductant_score_series: Callable[..., Any],
    iso3_to_region_map: dict[str, str],
    global_risk_free_rate: float,
    capex_subsidies: dict[str, dict[str, list[Subsidy]]],
    debt_subsidies: dict[str, dict[str, list[Subsidy]]],
    opex_subsidies: dict[str, dict[str, list[Subsidy]]],
    energy_subsidies: dict[str, dict[str, dict[str, list[Subsidy]]]],
    most_common_reductant: dict[str, str],
    environment_most_common_reductant: dict[str, str],
    derive_geo_unit: Callable[[float, float, str], str | None] | None = None,
) -> dict[str, dict[tuple[float, float, str], dict[str, dict[str, Any]]]]:
    """
    For each business opportunity (top location-technology pair), prepare all required inputs to calculate the NPV
    and create a new plant. Sites missing critical data (energy costs, cost of equity or debt) raise a ValueError,
    as do invalid data types; technologies with an incomplete field set are dropped in validation.

    Args:
        product_to_tech: Dictionary mapping products to their allowed technologies (product -> list of techs)
        best_locations_subset: Dictionary of best locations per product with necessary site data (product -> list of dicts with lat,
            lon, iso3, power price, hydrogen price, railway cost)
        current_year: The current simulation year.
        target_year: The year when the plant would start operation (current year + consideration time + 1 year announcement lag)
        energy_costs: Nested dictionary with energy costs per geography and year (geo_key -> year ->
            energy carrier -> cost); sub-national rows (``"ISO3:unit"``) win over country rows for
            sites whose derived geo_unit matches
        capex_dict_all_locs_techs: Nested dictionary with CAPEX values per region and technology (region -> tech -> capex)
        cost_of_debt_all_locs: Dictionary with cost of debt per country and technology (iso3 -> tech -> cost of debt)
        cost_of_equity_all_locs: Dictionary with cost of equity per country and technology (iso3 -> tech -> cost of equity)
        fopex_all_locs_techs: Nested dictionary with fixed OPEX values per country and technology (iso3 -> tech -> fopex)
        steel_plant_capacity: Capacity of the steel plant in tons per year
        get_bom_from_avg_boms: Function to retrieve the bill of materials and utilization rate for a given technology and energy costs
        iso3_to_region_map: Mapping from ISO3 country codes to regions for CAPEX lookup
        global_risk_free_rate: Global risk-free rate used in debt subsidy calculations
        capex_subsidies: Nested dictionary with CAPEX subsidies per geography and technology (geo_key -> tech -> list
            of subsidies; geo_key = "ISO3" or "ISO3:unit", country and sub-national rows merge additively)
        debt_subsidies: Nested dictionary with debt subsidies per geography and technology (geo_key -> tech -> list of subsidies)
        opex_subsidies: Nested dictionary with OPEX subsidies per geography and technology (geo_key -> tech -> list of subsidies)
        energy_subsidies: Nested dictionary with energy carrier subsidies (carrier -> geo_key -> tech -> list of subsidies)
        most_common_reductant: Dictionary mapping technology to most common reductant from plant group (tech -> reductant)
        environment_most_common_reductant: Fallback dict mapping technology to most common reductant from environment (tech -> reductant)
        derive_geo_unit: Optional ``(lat, lon, iso3) -> geo_unit | None`` derivation (injected from
            the geospatial adapter, admin-1 layer cached across the whole sites iteration) so
            candidate sites collect province-scoped subsidies; the same derivation runs at spawn,
            so evaluation and spawn always agree

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

    logger = logging.getLogger(f"{__name__}.prepare_cost_data_for_business_opportunity")
    logger.info("[NEW PLANTS] Preparing cost data for business opportunities for a subset of best locations.")
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
            # Candidate geography: province-scoped subsidies apply only at sites inside that
            # province. The same derivation runs again at spawn, so the two always agree.
            geo_unit = derive_geo_unit(site["Latitude"], site["Longitude"], site["iso3"]) if derive_geo_unit else None
            site_geo_key = compose_geo_key(site["iso3"], geo_unit)
            site_location = Location(
                lat=site["Latitude"],
                lon=site["Longitude"],
                country=site["iso3"],
                region="",
                iso3=site["iso3"],
                geo_unit=geo_unit,
            )
            if site_id not in cost_data[prod]:
                cost_data[prod][site_id] = {}

            # Track critical missing site-level data
            incomplete_site = False
            site_missing_fields = []

            # Set the energy costs to those of the site's geography (province row when the site
            # falls in one, else country — the same resolution the score series uses) and
            # overwrite electricity and hydrogen costs with custom values from the own power parc
            energy_costs_site = None
            year_costs_for_site = site_location.resolve(energy_costs, what="business-opportunity energy costs")
            if year_costs_for_site is None:
                site_missing_fields.append("energy_costs")
                incomplete_site = True
            elif current_year not in year_costs_for_site:
                site_missing_fields.append(f"energy_costs for year {current_year}")
                incomplete_site = True
            else:
                energy_costs_site = year_costs_for_site[current_year].copy()  # Copy to avoid modifying original
                elec_ratio = (
                    site["power_price"] / energy_costs_site["electricity"]
                    if energy_costs_site["electricity"] != 0
                    else float("inf")
                )
                if not (0.1 <= elec_ratio <= 10):
                    anomalous_power_prices_count += 1
                energy_costs_site["electricity"] = site["power_price"]
                energy_costs_site["hydrogen"] = site["capped_lcoh"] * T_TO_KG  # Convert USD/kg → USD/t

                # abs() negative by-product prices so subsidy arithmetic works correctly (mirrors set_energy_costs)
                for carrier in energy_costs_site:
                    if not carrier.startswith("co2"):
                        energy_costs_site[carrier] = abs(energy_costs_site[carrier])

            # Get per-technology cost of equity and debt for the country
            cost_of_equity_for_iso3 = cost_of_equity_all_locs.get(site["iso3"], None)
            if not cost_of_equity_for_iso3:
                site_missing_fields.append("cost_of_equity")
                incomplete_site = True

            cost_of_debt_for_iso3 = cost_of_debt_all_locs.get(site["iso3"], None)
            if not cost_of_debt_for_iso3:
                site_missing_fields.append("cost_of_debt")
                incomplete_site = True

            # If critical site-level data is missing, raise an error
            if incomplete_site:
                raise ValueError(
                    f"[NEW PLANTS] Missing critical site-level data for site {site_id} ({site['iso3']}): {', '.join(site_missing_fields)}. "
                    f"All cost data must be available for business opportunity evaluation."
                )
            assert cost_of_debt_for_iso3 is not None and cost_of_equity_for_iso3 is not None  # Help mypy

            for tech in product_to_tech[prod]:
                if tech not in cost_data[prod][site_id]:
                    cost_data[prod][site_id][tech] = {}

                # Financing rates are technology-specific
                cost_of_debt = cost_of_debt_for_iso3.get(tech)
                cost_of_equity = cost_of_equity_for_iso3.get(tech)
                if cost_of_debt is None or cost_of_equity is None:
                    raise ValueError(f"[NEW PLANTS] Missing cost of capital for {site['iso3']}/{tech}")

                # Track missing fields for logging
                missing_critical_fields = []

                # Always add railway cost and energy costs; equal for all technologies
                if site["rail_cost"] is None:
                    missing_critical_fields.append("railway_cost")
                else:
                    cost_data[prod][site_id][tech]["railway_cost"] = site["rail_cost"]

                # Apply energy carrier subsidies for this technology, filtered at the
                # operating start year (matching PAM and the score-series window)
                assert energy_costs_site is not None  # Help mypy understand the control flow
                operating_start_year = Year(target_year + construction_time)
                active_energy_subs: dict[str, list] = {}
                for carrier, carrier_subs in energy_subsidies.items():
                    all_subs = cc.collect_subsidies_for_geo(carrier_subs, site_geo_key).get(tech, [])
                    active = cc.filter_subsidies_for_year(all_subs, operating_start_year)
                    if active:
                        active_energy_subs[carrier] = active

                if active_energy_subs:
                    energy_costs_tech, output_costs_tech, no_subsidy_prices_tech = cc.get_subsidised_energy_costs(
                        energy_costs_site,
                        active_energy_subs,
                    )
                    sub_summary = ", ".join(f"{len(s)} {c}" for c, s in active_energy_subs.items())
                    logger.debug(
                        f"[NEW PLANTS] {site['iso3']}/{tech} year={operating_start_year} | Subs: {sub_summary}"
                    )
                else:
                    energy_costs_tech = energy_costs_site
                    output_costs_tech = energy_costs_site
                    no_subsidy_prices_tech = energy_costs_site.copy()

                cost_data[prod][site_id][tech]["energy_costs"] = energy_costs_tech  # type: ignore[assignment]
                cost_data[prod][site_id][tech]["output_costs"] = output_costs_tech  # type: ignore[assignment]
                cost_data[prod][site_id][tech]["no_subsidy_prices"] = no_subsidy_prices_tech  # type: ignore[assignment]
                cost_data[prod][site_id][tech]["cost_of_equity"] = cost_of_equity  # type: ignore[assignment]

                # Add average BOM and utilization rate per technology if available
                bom_result = get_bom_from_avg_boms(
                    energy_costs_tech,
                    tech,
                    int(steel_plant_capacity),
                    most_common_reductant.get(tech, environment_most_common_reductant.get(tech)),
                )
                bill_of_materials, util_rate, reductant, output_shares = bom_result
                if bill_of_materials is None:
                    missing_critical_fields.append("bom")
                if util_rate is None:
                    missing_critical_fields.append("utilization_rate")
                else:
                    cost_data[prod][site_id][tech]["utilization_rate"] = util_rate
                if reductant is None:
                    missing_critical_fields.append("reductant")

                if bill_of_materials is not None and reductant is not None:
                    # Year-wise reductant-optimised score at the site; the site's own pixel
                    # power/hydrogen prices are trajectory-scaled from the country series.
                    # TODO: temporary — extract the exact geo point's own year series from
                    # the rasters instead of ratio-scaling the current-year pixel prices.
                    site_overrides = {
                        "electricity": site["power_price"],
                        "hydrogen": site["capped_lcoh"] * T_TO_KG,
                    }
                    score_series = reductant_score_series(
                        site_location,
                        tech,
                        output_shares,
                        Year(target_year + construction_time),
                        Year(target_year + construction_time + plant_lifetime),
                        overrides=site_overrides,
                        override_reference_year=current_year,
                    )
                    committed_reductant = score_series.picks[0] if score_series.picks else ""
                    if committed_reductant != reductant:
                        # Commit the BOM the start-year pick implies (materials are
                        # reductant-invariant; only the energy rows follow the pick)
                        rebuilt_bom, rebuilt_util_rate, reductant, output_shares = get_bom_from_avg_boms(
                            energy_costs_tech,
                            tech,
                            int(steel_plant_capacity),
                            committed_reductant,
                        )
                        if rebuilt_bom is None:
                            raise ValueError(
                                f"BOM rebuild for {tech} with reductant '{committed_reductant}' returned no BOM"
                            )
                        bill_of_materials = rebuilt_bom
                        cost_data[prod][site_id][tech]["utilization_rate"] = rebuilt_util_rate
                    cost_data[prod][site_id][tech]["bom"] = bill_of_materials
                    cost_data[prod][site_id][tech]["reductant"] = committed_reductant  # type: ignore[assignment]
                    cost_data[prod][site_id][tech]["score_series"] = score_series.scores  # type: ignore[assignment]
                    cost_data[prod][site_id][tech]["output_shares"] = output_shares  # type: ignore[assignment]
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug(
                            "[REDUCTANT NPV] site %s: tech=%s committed=%r picks=%s",
                            site_id,
                            tech,
                            committed_reductant,
                            cc.summarise_reductant_picks(score_series.picks, Year(target_year + construction_time)),
                        )

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
                    all_capex_subsidies = cc.collect_subsidies_for_geo(capex_subsidies, site_geo_key).get(tech, [])
                    selected_capex_subsidies = cc.filter_subsidies_for_year(all_capex_subsidies, target_year)
                    capex_with_subsidies = cc.calculate_capex_with_subsidies(capex, selected_capex_subsidies)
                    cost_data[prod][site_id][tech]["capex"] = capex_with_subsidies
                    cost_data[prod][site_id][tech]["capex_no_subsidy"] = capex

                # Always add cost of debt with subsidies
                all_debt_subsidies = cc.collect_subsidies_for_geo(debt_subsidies, site_geo_key).get(tech, [])
                selected_debt_subsidies = cc.filter_subsidies_for_year(all_debt_subsidies, target_year)
                cost_of_debt_with_subsidies = cc.calculate_debt_with_subsidies(
                    cost_of_debt=cost_of_debt,
                    debt_subsidies=selected_debt_subsidies,
                    risk_free_rate=global_risk_free_rate,
                )
                cost_data[prod][site_id][tech]["cost_of_debt"] = cost_of_debt_with_subsidies  # type: ignore[assignment]
                cost_data[prod][site_id][tech]["cost_of_debt_no_subsidy"] = cost_of_debt

                # pass opex subsidies to be considered in npv calculation
                cost_data[prod][site_id][tech]["all_opex_subsidies"] = cc.collect_subsidies_for_geo(
                    opex_subsidies, site_geo_key
                ).get(tech, [])  # type: ignore[assignment]

                # Raise error if any critical fields are missing
                if missing_critical_fields:
                    raise ValueError(
                        f"[NEW PLANTS] Missing critical cost data for {tech} at site {site_id} ({site['iso3']}): {', '.join(missing_critical_fields)}. "
                        f"All cost data must be available for business opportunity evaluation."
                    )

    # Log error if more than 30% of the sampled locations have anomalous power prices
    total_sites = sum(len(product_sites) for product_sites in best_locations_subset.values())
    if anomalous_power_prices_count > total_sites * 0.3:
        logger.error(
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
    list_fields = ["all_opex_subsidies", "score_series"]
    required_fields = (
        float_fields
        + string_fields
        + list_fields
        + [
            "railway_cost",
            "energy_costs",
            "output_costs",
            "no_subsidy_prices",
            "bom",
            "output_shares",
        ]
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

                        # output_costs: dict of floats or ints (by-product output pricing)
                        if not isinstance(tech_data["output_costs"], dict):
                            raise ValueError(
                                f"output_costs must be dict, got {type(tech_data['output_costs']).__name__}: {tech_data['output_costs']}"
                            )
                        for output_type, output_cost in tech_data["output_costs"].items():
                            if not isinstance(output_cost, (float, int)):
                                raise ValueError(
                                    f"output_costs['{output_type}'] must be float or int, got {type(output_cost).__name__}: {output_cost}"
                                )

                        # no_subsidy_prices: dict of floats or ints (pre-subsidy energy costs)
                        if not isinstance(tech_data["no_subsidy_prices"], dict):
                            raise ValueError(
                                f"no_subsidy_prices must be dict, got {type(tech_data['no_subsidy_prices']).__name__}"
                            )
                        for price_key, price_val in tech_data["no_subsidy_prices"].items():
                            if not isinstance(price_val, (float, int)):
                                raise ValueError(
                                    f"no_subsidy_prices['{price_key}'] must be float or int, "
                                    f"got {type(price_val).__name__}: {price_val}"
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

                        # output_shares: dict of floats (metallic charge -> share of product)
                        if not isinstance(tech_data["output_shares"], dict):
                            raise ValueError(
                                f"output_shares must be dict, got {type(tech_data['output_shares']).__name__}: {tech_data['output_shares']}"
                            )
                        for charge_key, share_val in tech_data["output_shares"].items():
                            if not isinstance(share_val, (float, int)):
                                raise ValueError(
                                    f"output_shares['{charge_key}'] must be float or int, "
                                    f"got {type(share_val).__name__}: {share_val}"
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
    logger = logging.getLogger(f"{__name__}.validate_and_clean_cost_data")
    for product, sites in cost_data.items():
        for site_id, techs in sites.items():
            for tech, costs in techs.items():
                logger.debug(f"[NEW PLANTS] Sample costs data for {product} x {site_id[2]} x {tech}: {costs}")
                break
            break
        break

    return cost_data


def select_top_opportunities_by_npv(
    npv_dict: dict[str, dict[Any, dict[str, float]]],
    top_n_loctechs_as_business_op: int,
    probabilistic_agents: bool,
) -> dict[str, dict[tuple[float, float, str], dict[str, float]]]:
    """
    Select top location-technology combinations with high NPVs. When probabilistic_agents is True,
    uses weighted random sampling to ensure a mix of opportunities; when False, picks the top N
    by NPV deterministically.

    Args:
        npv_dict: Nested dictionary with NPV values (product -> site_id -> tech -> NPV)
        top_n_loctechs_as_business_op: Number of top location-technology combinations to select per product
        probabilistic_agents: If True, rank-weighted random draw over the top 3N candidates (deliberate
            realism - a mix of high and medium NPV options). If False, deterministic top-N by NPV.

    Returns:
        Dictionary mapping products to site IDs to technologies with their NPVs (product -> site_id (lat, lon, iso3) -> tech -> NPV)

    Raises:
        ValueError: If no valid NPVs found for a product

    Side Effects:
        - Logs NPV analysis statistics (valid, NaN, -inf combinations)
        - Logs selected top opportunities

    Notes:
        - Invalid NPV values (NaN, -inf) are removed before processing
        - When probabilistic_agents: candidates are ranked by NPV and only the best 3N enter a
          weighted draw with linearly decreasing rank weights - a mix of high and medium NPV
          options rather than only the highest, while implausible sites can never be selected
        - Rank weights are scale-free: the draw behaves identically for all-negative,
          mixed and all-positive pools
    """
    logger = logging.getLogger(f"{__name__}.select_top_opportunities_by_npv")
    if probabilistic_agents:
        logger.info(
            f"[NEW PLANTS] Drawing {top_n_loctechs_as_business_op} location-technology combinations per product from the "
            f"top {3 * top_n_loctechs_as_business_op} by NPV (rank-weighted, without replacement)."
        )
    else:
        logger.info(
            f"[NEW PLANTS] Selecting the top {top_n_loctechs_as_business_op} location-technology combinations "
            "per product by NPV (deterministic)."
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
                    logger.debug(f"  NaN: site={site_id}, tech={tech}, NPV={npv}")
                elif npv == float("-inf"):
                    neg_inf_count += 1
                    logger.debug(f"  -inf: site={site_id}, tech={tech}, NPV={npv}")
                else:
                    valid_pairs.append((site_id, tech))
                    valid_npvs.append(npv)
        total_combinations = len(valid_pairs) + nan_count + neg_inf_count
        logger.debug(f"[NEW PLANTS] NPV analysis for product {product}:")
        logger.debug(f"  Valid combinations: {len(valid_pairs)}/{total_combinations}")
        logger.debug(f"  NaN combinations: {nan_count}/{total_combinations}")
        logger.debug(f"  -inf combinations: {neg_inf_count}/{total_combinations}")
        if len(valid_pairs) == 0:
            raise ValueError(
                f"[NEW PLANTS] No valid NPVs found for product {product}. Skipping opportunity identification. "
                f"NPV dict for {product}: {npv_dict.get(product, {})}"
            )

        npvs_array = np.array(valid_npvs)
        ranked_indices = np.argsort(npvs_array)[::-1]

        if probabilistic_agents:
            # Trim to the plausible head of the pool, then draw with rank weights (best gets
            # weight `trim`, worst weight 1). The former shift-by-min weighting degenerated
            # to a near-uniform draw whenever the whole pool was negative.
            trim = min(len(ranked_indices), 3 * top_n_loctechs_as_business_op)
            pool_indices = ranked_indices[:trim]

            if trim > top_n_loctechs_as_business_op:
                rank_weights = np.arange(trim, 0, -1, dtype=float)
                probabilities = rank_weights / rank_weights.sum()
                drawn = np.random.choice(trim, size=top_n_loctechs_as_business_op, replace=False, p=probabilities)
                selected_pairs = [valid_pairs[pool_indices[i]] for i in drawn]
            else:
                selected_pairs = [valid_pairs[i] for i in pool_indices]
            logger.info(
                f"[NEW PLANTS] {product}: drew {len(selected_pairs)} of {len(valid_pairs)} valid candidates "
                f"(rank-weighted over the top {trim})."
            )
        else:
            top_indices = ranked_indices[:top_n_loctechs_as_business_op]
            selected_pairs = [valid_pairs[i] for i in top_indices]
            logger.info(
                f"[NEW PLANTS] {product}: selected top {len(selected_pairs)} of {len(valid_pairs)} valid candidates "
                "by NPV (deterministic)."
            )

        # Format selected (site, tech) pairs into business opportunities dict
        top_business_opportunities[product] = {}
        for site_id, tech in selected_pairs:
            if site_id not in top_business_opportunities[product]:
                top_business_opportunities[product][site_id] = {}
            top_business_opportunities[product][site_id][tech] = npv_dict[product][site_id][tech]
    # Log selected top opportunities in a more readable format
    for product, sites in top_business_opportunities.items():
        logger.info(f"[NEW PLANTS] Selected top opportunities for {product}:")
        for site_id, techs in sites.items():
            site_str = f"  Site (lat={site_id[0]}, lon={site_id[1]}, iso3={site_id[2]}):"
            tech_strs = [f"{tech} with NPV: {npv:.2f}" for tech, npv in techs.items()]
            logger.info(f"{site_str} {'; '.join(tech_strs)}")

    return top_business_opportunities
