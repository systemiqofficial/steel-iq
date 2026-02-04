import math
from pathlib import Path
from typing import cast

import numpy as np
import pandas as pd
import pycountry
import pickle
from steelo.domain import (
    BiomassAvailability,
    Location,
    Volumes,
    Year,
    DemandCenter,
    PrimaryFeedstock,
    Supplier,
    TradeTariff,
    CarbonCostSeries,
    InputCosts,
    RegionEmissivity,
    Capex,
    CostOfCapital,
    CountryMapping,
    HydrogenEfficiency,
    HydrogenCapexOpex,
    Subsidy,
    AggregatedMetallicChargeConstraint,
    FOPEX,
    CarbonBorderMechanism,
)
from ...domain.models import TransportKPI, TechnologyEmissionFactors, FallbackMaterialCost
import logging

from ...domain.models import LegalProcessConnector
from ...utilities.data_processing import normalize_product_name

# Import only true constants from global_variables
from steelo.domain.constants import (
    Commodities,
    GJ_TO_KWH,
    MWH_TO_KWH,
    PERMWh_TO_PERkWh,
    PERGJ_TO_PERkWh,
    KG_TO_T,
    T_TO_KG,
    KT_TO_T,
    MT_TO_T,
)

# TODO: Remove overwriting and replace by simulation config
EXCEL_READER_START_YEAR = 2020
EXCEL_READER_END_YEAR = 2050
CHOSEN_DEMAND_SCENARIO = "BAU"

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

"""
A note on units:
- iron and steel volumes should be read in tonnes (t), and handled as tons throughout the code.
- Secondary feedstock should be read in as the unit that is defined in the BOMs, usually kilograms, costs need to be given as per that unit as well.
- Energy should be converted to kWh.
- currencies should be in USD.
"""

# Mapping of commodities whose consumption is expressed in t/t (tonnes per tonne) that require
# USD/kg to USD/t conversion when loading prices from master Excel.
# These materials have their usage in the BOM expressed as tonnes per tonne of product,
# so their prices must be converted from USD/kg to USD/t (multiply by 1000).
#
# IMPORTANT: Only include commodities whose BOM consumption ultimately uses tonnes per tonne.
# Hydrogen now falls into this bucket because BOM ingestion normalises its kg/t requirement to tonnes.
#
# NOTE: Keys must match the normalized commodity names after lowercasing and space-to-underscore
# conversion. "Bio-PCI" in Excel becomes "bio-pci" (hyphen preserved), not "bio_pci".
MATERIALS_REQUIRING_KG_TO_T_PRICE_CONVERSION = {
    "bio-pci",  # Bio-PCI: consumption in t/t, price needs conversion from USD/kg to USD/t
    "pci",  # PCI: consumption in t/t, price needs conversion from USD/kg to USD/t
    "coke",  # Coke: consumption in t/t, price needs conversion from USD/kg to USD/t
    "coking_coal",  # Coking coal: consumption in t/t, price needs conversion from USD/kg to USD/t
    "hydrogen",  # Hydrogen consumption stored in t/t after BOM processing; convert price to USD/t for consistency
}


def normalize_commodity_name(commodity: str) -> str:
    """Normalize metallic charge names to match expected format.

    Examples:
    - "Hot metal" -> "hot_metal"
    - "Pig iron" -> "pig_iron"
    - "DRI low" -> "dri_low"
    """
    if not commodity:
        return commodity
    # Convert to lowercase and replace spaces with underscores
    normalized = commodity.lower().replace(" ", "_")
    return normalized


translate_country_names = {
    "Dem. Rep. of the Congo": "Congo DRC",
    "Hong Kong, China": "Hong Kong",
    "Ivory Coast": "Côte d'Ivoire",
    "Korea, North": "North Korea",
    "Korea, Republic of": "South Korea",
    "Macedonia": "North Macedonia",
    "Russia": "Russian Federation",
}

translate_mine_regions_to_iso3 = {  # for tariffs - improve logic
    "North America": "USA",
    "South Africa": "ZAF",
    "Ukraine-Balkans Corridor": "UKR",
    "Canada": "CAN",
    "Other South America": "COL",
    "Brazil": "BRA",
    "Australia": "AUS",
    "Russia": "RUS",
    "Chile": "CHL",
    "China": "CHN",
    "India": "IND",
    "Kazakhstan": "KAZ",
    "Atlantic West Africa": "GHA",
    "Scandinavia": "SWE",
}

translate_country_names_to_iso3 = {
    "Democratic Republic of the Congo": "COD",
    "Niger": "NER",
    "Chile": "CHL",
    "Namibia": "NAM",
}


def read_regional_input_prices_from_master_excel(
    excel_path: Path,
    input_costs_sheet: str = "Input costs",
) -> list[InputCosts]:
    """
    Reader specifically designed for the master data excel file.

    Args:
        excel_path (Path): Path to the master data excel file.
        input_costs_sheet (str): Name of the sheet containing input costs.
    Returns:
        list[InputCosts]: A list of InputCosts objects containing the regional input prices.
    """

    # 1) Load raw data
    input_costs_df = pd.read_excel(excel_path, sheet_name=input_costs_sheet)

    # Store unit information before pivoting
    unit_mapping = dict(zip(input_costs_df["Commodity"], input_costs_df["Unit"]))

    # 2) Build input_cost_dict = { year: { ISO3: { commodity: cost_in_raw_units, … } } }
    input_costs_df.columns.name = "Year"
    input_cost_stacked = (
        input_costs_df.groupby(["ISO-3 code", "Commodity"])
        .sum(numeric_only=True)
        .unstack(level=1)
        .stack(level="Year")
        .reset_index()
    )
    ## Lowercase all columns and replace spaces with underscores ONLY for commodity columns (not structural columns)
    input_cost_stacked.columns = input_cost_stacked.columns.str.lower()
    rename_map = {}
    for col in input_cost_stacked.columns:
        if col not in ["iso-3 code", "year"]:
            rename_map[col] = col.replace(" ", "_")
    input_cost_stacked = input_cost_stacked.rename(columns=rename_map)
    ## Update unit mapping keys to match normalized commodity names (lowercase + spaces to underscores)
    unit_mapping_lower = {}
    for commodity, unit in unit_mapping.items():
        commodity_normalized = commodity.lower().replace(" ", "_")
        unit_mapping_lower[commodity_normalized] = unit

    # 3) Convert units to match BOM requirements based on Unit column

    # Apply conversions based on the Unit column
    for commodity in input_cost_stacked.columns:
        if commodity in ["iso-3 code", "year"]:
            continue

        unit = unit_mapping_lower.get(commodity, "")

        if unit == "USD/MWh":
            # Convert from USD/MWh to USD/kWh
            input_cost_stacked.loc[:, commodity] *= PERMWh_TO_PERkWh
        elif unit == "USD/GJ":
            # Convert from USD/GJ to USD/kWh
            input_cost_stacked.loc[:, commodity] *= PERGJ_TO_PERkWh
        elif unit == "USD/kg":
            # Selective conversion: materials with t/t consumption need USD/kg → USD/t conversion
            if commodity in MATERIALS_REQUIRING_KG_TO_T_PRICE_CONVERSION:
                # Convert from USD/kg to USD/t for materials consumed in tonnes per tonne
                # (multiply by 1000 kg/t to get USD/t from USD/kg)
                # Includes: coke, pci, bio-pci, coking_coal, hydrogen
                input_cost_stacked.loc[:, commodity] *= T_TO_KG
                logging.info(
                    f"Converting {commodity} price from USD/kg to USD/t (multiply by {T_TO_KG}) for t/t consumption"
                )
            else:
                # Keep in USD/kg for materials not in the conversion set
                logging.info(f"Keeping {commodity} price in USD/kg (not in t/t consumption set)")
        elif unit == "USD/t":
            pass
        else:
            raise ValueError(f"Unit '{unit}' not supported for commodity '{commodity}' in input costs.")

    grouped: dict[str, dict[str, dict[str, float]]] = (
        input_cost_stacked.groupby(["year", "iso-3 code"])
        .sum(numeric_only=True)
        .apply(lambda row: row.to_dict(), axis=1)
        .unstack(level="year")
        .to_dict()
    ).copy()

    # 4) set up input_cost_dict
    # Convert string years to int years
    input_cost_dict: dict[int, dict[str, dict[str, float]]] = {
        int(year): country_data for year, country_data in grouped.items()
    }

    # 6) Build a List[InputCosts], sorted by year then iso3
    result: list[InputCosts] = []
    for year in sorted(input_cost_dict):
        for iso3 in sorted(input_cost_dict[year]):
            costs = input_cost_dict[year][iso3]
            result.append(InputCosts(year=Year(year), iso3=iso3, costs=costs))

    return result


def read_aggregated_metallic_charge_constraints(
    dynamic_business_cases_excel_path: str, excel_sheet: str
) -> list[AggregatedMetallicChargeConstraint]:
    """
    Read and process wildcard constraints from Bill of Materials Excel.

    This function identifies rows with wildcard patterns (e.g., "DRI*", "HBI*") in the
    Metallic charge column where Metric type is "Constraint". These wildcards create
    constraints that apply to all feedstocks matching the pattern.

    Process:
    1. Find constraint rows with wildcards in Metallic charge column
    2. Extract the pattern (e.g., "DRI*" -> "DRI")
    3. Create AggregatedMetallicChargeConstraint objects for minimum/maximum shares
    4. These constraints will later be applied to all matching feedstocks

    Args:
        dynamic_business_cases_excel_path: Path to the Excel file containing Bill of Materials
        excel_sheet: Name of the sheet to read (typically "Bill of Materials")

    Returns:
        List of AggregatedMetallicChargeConstraint objects that define constraints
        for groups of feedstocks matching wildcard patterns.
    """
    df = pd.read_excel(dynamic_business_cases_excel_path, sheet_name=excel_sheet)
    aggregated_constraints: list[AggregatedMetallicChargeConstraint] = []

    # Look for rows with wildcards in Metallic charge and Metric type = Constraint
    constraint_rows = df[df["Metric type"] == "Constraint"]

    for _, row in constraint_rows.iterrows():
        mc_raw = row["Metallic charge"] if pd.notna(row["Metallic charge"]) else ""

        # Only process if it contains a wildcard
        if mc_raw and "*" in mc_raw:
            business_case_str = row["Business case"]

            # Handle special cases without underscore
            if "_" not in business_case_str:
                technology = business_case_str.upper()
            else:
                technology = business_case_str.split("_")[-1].upper()  # extract technology from business case

            # if technology is charcoal, rename to BF_CHARCOAL
            technology = technology.replace("CHARCOAL", "BF_CHARCOAL")

            # Extract the pattern (e.g., "DRI*" -> "DRI")
            pattern = mc_raw.replace("*", "")
            type_field = row["Type"] if pd.notna(row["Type"]) else ""
            value = row["Value"] if pd.notna(row["Value"]) else 0.0
            unit = row["Unit"] if pd.notna(row["Unit"]) else ""

            # Check if we already have this constraint
            existing_constraint = None
            for constraint in aggregated_constraints:
                if constraint.technology_name == technology and constraint.feedstock_pattern == pattern:
                    existing_constraint = constraint
                    break

            # Handle percentage values - when unit is %, value is already in fraction form
            if unit == "%":
                constraint_value = value
            else:
                constraint_value = value / 100.0

            if existing_constraint:
                # Update existing constraint
                if type_field == "Minimum" and constraint_value >= 0:
                    existing_constraint.minimum_share = constraint_value
                elif type_field == "Maximum" and constraint_value <= 1.0:
                    existing_constraint.maximum_share = constraint_value
            else:
                # Create new constraint
                min_share = None
                max_share = None

                if type_field == "Minimum" and constraint_value >= 0:
                    min_share = constraint_value
                elif type_field == "Maximum" and constraint_value <= 1.0:
                    max_share = constraint_value

                if min_share is not None or max_share is not None:
                    constraint = AggregatedMetallicChargeConstraint(
                        technology_name=technology,
                        feedstock_pattern=pattern,
                        minimum_share=min_share,
                        maximum_share=max_share,
                    )
                    aggregated_constraints.append(constraint)
                    logger.info(
                        f"Created aggregated constraint for {technology}: {pattern} min={min_share}, max={max_share}"
                    )

    return aggregated_constraints


def read_dynamic_business_cases(
    dynamic_business_cases_excel_path: str, excel_sheet: str
) -> dict[str, list[PrimaryFeedstock]]:
    """
    Read and process dynamic business cases from Excel to create PrimaryFeedstock objects.

    This function processes Bill of Materials data from an Excel sheet, creating feedstock
    objects for each unique combination of technology, metallic charge, and reductant.
    It handles various special cases including wildcard patterns, and aggregated constraints.

    Process:
    1. Create feedstocks for each technology + metallic_charge + reductant combination
    2. Data rows populate feedstock properties (materials, energy, outputs, constraints)
    3. Wildcard constraints (e.g., DRI*, HBI*) are read separately and applied to matching feedstocks

    Args:
        dynamic_business_cases_excel_path: Path to the Excel file containing Bill of Materials
        excel_sheet: Name of the sheet to read (typically "Bill of Materials")

    Returns:
        Dictionary mapping technology names (lowercase) to lists of PrimaryFeedstock objects.
        Each feedstock represents a unique technology-metallic_charge-reductant combination
        with associated materials, energy requirements, outputs, and constraints.
    """
    df = pd.read_excel(dynamic_business_cases_excel_path, sheet_name=excel_sheet)

    # Ensure string types for key columns
    df["Metallic charge"] = df["Metallic charge"].fillna("").astype(str)
    df["Reductant"] = df["Reductant"].fillna("").astype(str)
    df["Business case"] = df["Business case"].astype(str)

    # Dictionary to store all feedstocks
    feedstocks_dict: dict[str, PrimaryFeedstock] = {}

    # Group by business case to find all unique metallic charge/reductant combinations
    for business_case, group in df.groupby("Business case"):
        # Get technology from business case name (use existing mapping)
        business_case_str = str(business_case)
        # Handle special cases without underscore
        if "_" not in business_case_str:
            technology = business_case_str.upper()
        else:
            technology = business_case_str.split("_")[-1].upper()  # extract technology from business case

        # if technology is charcoal, rename to BF_CHARCOAL
        technology = technology.replace("CHARCOAL", "BF_CHARCOAL")

        # Find all unique metallic charges and reductants for this technology (excluding wildcards)
        metallic_charges: set[str] = set()
        reductants: set[str] = set()

        for _, row in group.iterrows():
            mc_raw = row["Metallic charge"] if row["Metallic charge"] and row["Metallic charge"] != "nan" else ""
            # Skip wildcards when collecting unique values
            if mc_raw and "*" not in mc_raw:
                mc = normalize_commodity_name(mc_raw)
                if mc:
                    metallic_charges.add(mc)

            red = row["Reductant"].lower() if row["Reductant"] and row["Reductant"] != "nan" else ""
            if red and "*" not in red:
                reductants.add(red)

        # Handle technologies without reductants (like steel_bof)
        if not reductants:
            reductants = {""}  # Add empty string for no reductant

        ordered_metallic_charges = sorted(metallic_charges)
        ordered_reductants = sorted(reductants)

        # Create feedstocks for all valid combinations
        for mc in ordered_metallic_charges:
            for red in ordered_reductants:
                if not mc and not red:
                    continue  # Skip completely empty combinations

                feedstock_key = f"{technology}_{mc}_{red}".lower()

                if feedstock_key not in feedstocks_dict:
                    feedstocks_dict[feedstock_key] = PrimaryFeedstock(
                        technology=technology, metallic_charge=mc, reductant=red
                    )

        # Now process all rows for this business case
        for _, row in group.iterrows():
            mc_raw = row["Metallic charge"] if row["Metallic charge"] and row["Metallic charge"] != "nan" else ""
            red = row["Reductant"].lower() if row["Reductant"] and row["Reductant"] != "nan" else ""

            # Check for wildcards in metallic charge
            if mc_raw and "*" in mc_raw:
                # Skip constraint rows with wildcards (handled by read_aggregated_metallic_charge_constraints)
                if row["Metric type"] == "Constraint":
                    continue
                else:
                    # For non-constraint rows, apply to matching feedstocks
                    pattern = normalize_commodity_name(mc_raw.replace("*", ""))
                    for key, feedstock in feedstocks_dict.items():
                        if (
                            feedstock.technology.lower() == technology.lower()
                            and feedstock.metallic_charge.lower().startswith(pattern)
                        ):
                            _process_row(row.to_dict(), feedstock, feedstocks_dict)
            elif mc_raw:
                mc = normalize_commodity_name(mc_raw)
                if not red:
                    # Apply to all feedstocks with this metallic charge
                    for key, feedstock in feedstocks_dict.items():
                        if feedstock.technology.lower() == technology.lower() and feedstock.metallic_charge == mc:
                            _process_row(row.to_dict(), feedstock, feedstocks_dict)
                else:
                    # Apply to specific combination
                    feedstock_key = f"{technology}_{mc}_{red}".lower()
                    if feedstock_key in feedstocks_dict:
                        _process_row(row.to_dict(), feedstocks_dict[feedstock_key], feedstocks_dict)
            elif red and not mc_raw:
                # Apply to all feedstocks with this reductant
                for key, feedstock in feedstocks_dict.items():
                    if feedstock.technology.lower() == technology.lower() and feedstock.reductant == red:
                        _process_row(row.to_dict(), feedstock, feedstocks_dict)
            else:
                # Apply to all feedstocks of this technology (if neither mc nor red specified)
                for key, feedstock in feedstocks_dict.items():
                    if feedstock.technology.lower() == technology.lower():
                        _process_row(row.to_dict(), feedstock, feedstocks_dict)

    # Organize by technology (use uppercase keys for backward compatibility)
    # Only include feedstocks that have a primary value
    technology_feedstocks: dict[str, list[PrimaryFeedstock]] = {}
    for feedstock in feedstocks_dict.values():
        # Skip feedstocks without primary value
        if feedstock.required_quantity_per_ton_of_product is None:
            logger.warning(
                f"Skipping feedstock {feedstock.technology}_{feedstock.metallic_charge}_{feedstock.reductant} "
                f"because it has no primary value"
            )
            continue

        tech_key = feedstock.technology.upper()
        feedstock.technology = feedstock.technology.upper()  # Update the feedstock's technology too

        if tech_key not in technology_feedstocks:
            technology_feedstocks[tech_key] = []
        technology_feedstocks[tech_key].append(feedstock)

    # Normalise feedstock ordering per technology for deterministic downstream behaviour
    for feedstock_list in technology_feedstocks.values():
        feedstock_list.sort(key=lambda fs: (fs.metallic_charge, fs.reductant))

    # Apply aggregated constraints from wildcards to matching feedstocks
    aggregated_constraints = read_aggregated_metallic_charge_constraints(dynamic_business_cases_excel_path, excel_sheet)

    for constraint in aggregated_constraints:
        # Find all feedstocks that match this constraint pattern
        # Ensure proper capitalization for technology names
        technology = constraint.technology_name.upper()
        pattern = constraint.feedstock_pattern.lower()

        if technology in technology_feedstocks:
            for feedstock in technology_feedstocks[technology]:
                # Check if the feedstock's metallic charge matches the pattern
                if feedstock.metallic_charge.lower().startswith(pattern):
                    # Apply the constraint to this feedstock
                    if constraint.minimum_share is not None:
                        feedstock.minimum_share_in_product = constraint.minimum_share
                    if constraint.maximum_share is not None:
                        feedstock.maximum_share_in_product = constraint.maximum_share
                    logger.debug(
                        f"Applied constraint to {feedstock.technology}_{feedstock.metallic_charge}: "
                        f"min={constraint.minimum_share}, max={constraint.maximum_share}"
                    )

    # Set default constraints (0.0 to 1.0) for any feedstock without constraints
    for tech_key, feedstock_list in technology_feedstocks.items():
        for feedstock in feedstock_list:
            if feedstock.minimum_share_in_product is None:
                feedstock.minimum_share_in_product = 0.0
                logger.debug(
                    f"Setting default minimum constraint (0.0) for {feedstock.technology}_{feedstock.metallic_charge}_{feedstock.reductant}"
                )
            if feedstock.maximum_share_in_product is None:
                feedstock.maximum_share_in_product = 1.0
                logger.debug(
                    f"Setting default maximum constraint (1.0) for {feedstock.technology}_{feedstock.metallic_charge}_{feedstock.reductant}"
                )

    return technology_feedstocks


def _process_row(row: dict, feedstock: PrimaryFeedstock, all_feedstocks: dict[str, PrimaryFeedstock]):
    """
    Process a single row and update the feedstock accordingly.
    Handles inputs, outputs, constraints, and proper unit conversions.

    Note: Wildcard constraints (e.g., DRI*, HBI*) are skipped here and handled
    by the separate read_aggregated_metallic_charge_constraints function.

    Args:
        row: The data row from Excel
        feedstock: The feedstock to update
        all_feedstocks: Dictionary of all feedstocks (currently unused but kept for compatibility)
    """
    side = row["Side"]
    metric_type = row["Metric type"]
    vector = row["Vector"] if pd.notna(row["Vector"]) else ""
    value = row["Value"] if pd.notna(row["Value"]) else 0.0
    unit = row["Unit"] if pd.notna(row["Unit"]) else ""
    type_field = row["Type"] if pd.notna(row["Type"]) else ""

    # Skip rows with no value
    if pd.isna(row["Value"]):
        return

    # Handle constraints (regular ones only - wildcards are handled separately)
    if metric_type == "Constraint":
        # Skip wildcard constraints (handled by read_aggregated_metallic_charge_constraints)
        if vector and "*" in vector:
            return

        # Regular constraint - apply to individual feedstock
        # When unit is %, value is already in fraction form (1.0 = 100%)
        if type_field == "Minimum":
            # Set minimum share even when value is 0
            feedstock.minimum_share_in_product = value if unit == "%" else value / 100.0
        elif type_field == "Maximum":
            # For %, check <= 1.0; for non-%, check <= 100
            if (unit == "%" and value <= 1.0) or (unit != "%" and value <= 100):
                feedstock.maximum_share_in_product = value if unit == "%" else value / 100.0
        return

    # Handle materials/feedstock
    if metric_type.lower() in ["materials", "feedstock", "reductant"]:
        if side == "Input":
            # Check if this is the primary feedstock (Vector matches metallic charge)
            if vector and normalize_commodity_name(vector) == feedstock.metallic_charge.lower():
                # For primary feedstock, keep the original unit logic
                if "t/t" in unit.lower():
                    feedstock.required_quantity_per_ton_of_product = float(value)
                elif "kg/t" in unit.lower():
                    # Primary feedstock in kg/t should also be stored as-is
                    feedstock.required_quantity_per_ton_of_product = float(value)
                else:
                    logger.warning(f"Unexpected unit '{unit}' for primary material requirement")
                    feedstock.required_quantity_per_ton_of_product = float(value)
            else:
                # Secondary feedstock - store with original units (usually kg/t)
                if vector:
                    feedstock.add_secondary_feedstock(normalize_commodity_name(vector), value)
        elif side == "Output":
            # Outputs - keep original values
            if vector:
                # Handle steel/liquid steel naming using Commodities
                output_name = normalize_commodity_name(vector)
                if output_name == Commodities.LIQUID_STEEL.value.lower():
                    output_name = Commodities.STEEL.value
                feedstock.add_output(name=output_name, amount=Volumes(float(value)))

    # Handle energy
    elif metric_type.lower() in ["energy", "heat", "machine drive", "machine_drive", "others"]:
        # For energy, we need to convert units
        converted_value = _convert_units(value, unit, metric_type)
        if side == "Input":
            if vector:
                feedstock.add_energy_requirement(normalize_commodity_name(vector), converted_value)
        elif side == "Output":
            if vector:
                feedstock.add_output(name=normalize_commodity_name(vector), amount=Volumes(converted_value))


def _convert_units(value: float, unit: str, metric_type: str) -> float:
    """
    Convert units to standard units used in the system.

    Conversions:
    - Energy units: GJ/t -> kWh/t (*277.78), MWh/t -> kWh/t (*1000)
    - Material units: kept as-is (t/t, kg/t)
    - Percentages: kept as-is

    Args:
        value: The numeric value to convert
        unit: The unit string (e.g., "GJ/t", "kWh/t")
        metric_type: The type of metric (e.g., "energy", "materials")

    Returns:
        The converted value in standard units
    """
    if not unit:
        return value

    unit_lower = unit.lower()

    # Material units
    if "t/t" in unit_lower:
        return value
    elif "kg/t" in unit_lower:
        return value * KG_TO_T
    elif "tco2/t" in unit_lower:
        return value

    # Energy units
    elif "kwh/t" in unit_lower:
        return value
    elif "gj/t" in unit_lower:
        return value * GJ_TO_KWH
    elif "mwh/t" in unit_lower:
        return value * MWH_TO_KWH
    elif "kg/t" in unit_lower and metric_type.lower() in ["energy", "heat", "machine drive", "reductant"]:
        # For things like hydrogen or PCI which are measured in kg/t
        return value

    # Percentage (for constraints)
    elif "%" in unit_lower:
        return value

    else:
        logger.warning(f"Unknown unit: {unit} for metric type {metric_type}, using raw value")
        return value


def read_mines_as_suppliers(mine_data_excel_path: str, mine_data_sheet_name: str, location_csv: str) -> list[Supplier]:
    """
    Read mine supply data from Excel and return a list of Supplier domain objects for mines.
    """
    import json
    import unicodedata
    import uuid

    # Project-wide constant namespace for iron ore suppliers
    # Using a valid UUID with last digits spelling "1205" (IRON in hex-like)
    IRON_ORE_NAMESPACE = uuid.UUID("00000000-0000-0000-0000-000000001205")

    def canonicalize(value: str | None) -> str:
        """Canonicalize string for consistent ID generation."""
        if value is None:
            return ""
        return unicodedata.normalize("NFC", str(value).strip().casefold())

    def generate_supplier_id(row: pd.Series, mine_location: Location, product: str, row_index: int) -> str:
        """Generate deterministic, collision-proof supplier ID.

        Includes row_index to ensure uniqueness even when all other data is identical.
        """
        payload = {
            "sheet": "Iron ore mines",
            "country": canonicalize(mine_location.region),
            "product": canonicalize(product),
            "mine": canonicalize(row.get("Mine", "")),
            "lat": round(mine_location.lat, 6),
            "lon": round(mine_location.lon, 6),
            "capacity": float(row.get("capacity", 0)),  # Include capacity for differentiation
            "costs": float(row.get("costs", 0)),  # Include costs for differentiation
            "row_index": row_index,  # Always include row index for absolute uniqueness
        }
        # Generate stable UUID and use first 20 hex chars for ID
        stable_uuid = uuid.uuid5(IRON_ORE_NAMESPACE, json.dumps(payload, sort_keys=True))
        return f"sup_{stable_uuid.hex[:20]}"

    def validate_suppliers(suppliers: list[Supplier], excel_df: pd.DataFrame) -> None:
        """Comprehensive validation of processed suppliers."""
        # 1. Uniqueness check
        supplier_ids = [s.supplier_id for s in suppliers]
        if len(supplier_ids) != len(set(supplier_ids)):
            duplicates = [id for id in supplier_ids if supplier_ids.count(id) > 1]
            raise ValueError(f"Duplicate supplier IDs generated: {set(duplicates)}")

        # 2. Total capacity preservation (check against first capacity column found)
        capacity_cols = [col for col in excel_df.columns if col.startswith("Capacity [Mt] ")]
        if capacity_cols:
            # Use the first capacity column for validation
            first_cap_col = capacity_cols[0]
            excel_total = excel_df[first_cap_col].sum()
            # Get capacity for the same year from suppliers
            validation_year = Year(int(first_cap_col.split()[-1]))
            supplier_total = sum(
                s.capacity_by_year.get(validation_year, Volumes(0)) / MT_TO_T  # Convert back to Mt
                for s in suppliers
            )
            tolerance = 0.01  # Allow small rounding differences

            if abs(excel_total - supplier_total) > tolerance:
                raise ValueError(
                    f"Total capacity mismatch for {validation_year}: Excel={excel_total:.2f} Mt, Suppliers={supplier_total:.2f} Mt"
                )

            # 3. Per-product capacity preservation
            for product in excel_df["Products"].unique():
                excel_product = excel_df[excel_df["Products"] == product][first_cap_col].sum()
                commodity = Commodities(normalize_product_name(product)).value
                supplier_product = sum(
                    s.capacity_by_year.get(validation_year, Volumes(0)) / MT_TO_T
                    for s in suppliers
                    if s.commodity == commodity
                )
                if abs(excel_product - supplier_product) > tolerance:
                    raise ValueError(
                        f"{product} capacity mismatch for {validation_year}: Excel={excel_product:.2f} Mt, Suppliers={supplier_product:.2f} Mt"
                    )

        # 4. Data type and range validation
        for supplier in suppliers:
            # Validate all capacity values
            for year, capacity in supplier.capacity_by_year.items():
                assert capacity >= 0, f"Negative capacity for {supplier.supplier_id} in {year}"
            assert -90 <= supplier.location.lat <= 90, f"Invalid latitude: {supplier.supplier_id}"
            assert -180 <= supplier.location.lon <= 180, f"Invalid longitude: {supplier.supplier_id}"

    mine_data_df = pd.read_excel(mine_data_excel_path, sheet_name=mine_data_sheet_name)

    # Strip whitespace from column names to handle Excel inconsistencies
    mine_data_df.columns = mine_data_df.columns.str.strip()

    # Extract year-specific columns dynamically - support both formats:
    # Old format: "Capacity [Mt] 2025", "Production Cost [$/t] 2025", "Mine Price [$/t] 2025"
    # New format: "capacity Mtpa 2025", "costs $/t 2025", "price $/t 2025"
    capacity_cols = [
        col for col in mine_data_df.columns if col.startswith("Capacity [Mt] ") or col.startswith("capacity Mtpa ")
    ]
    mine_price_cols = [
        col for col in mine_data_df.columns if col.startswith("Mine Price [$/t] ") or col.startswith("price $/t ")
    ]
    production_cost_cols = [
        col for col in mine_data_df.columns if col.startswith("Production Cost [$/t] ") or col.startswith("costs $/t ")
    ]

    # Add logging to help debug
    logging.info(f"Reading iron ore mines from '{mine_data_sheet_name}' sheet")
    logging.info(f"  Total rows in sheet: {len(mine_data_df)}")
    logging.info(f"  Capacity columns found: {len(capacity_cols)} columns")
    logging.info(f"  Production cost columns found: {len(production_cost_cols)} columns")
    logging.info(f"  Mine price columns found: {len(mine_price_cols)} columns")

    def extract_year_from_column(col_name: str) -> int:
        """Extract year from column name like 'Capacity [Mt] 2025' or 'capacity Mtpa 2025'"""
        return int(col_name.split()[-1])

    mines: list[Supplier] = []
    skipped_rows = 0
    for row_num, (idx, row) in enumerate(mine_data_df.iterrows()):
        # Check if any capacity column has non-zero value
        has_capacity = any(row.get(col, 0) > 0 for col in capacity_cols)
        if not has_capacity:
            skipped_rows += 1
            continue

        # Create a unique location for each mine (not reused)
        mine_location = Location(
            lat=row["lat"],
            lon=row["lon"],
            country=row["Region"],  # FIXME just to be able to create a valid Location 2025-05-22 Jochen
            region=row["Region"],
            iso3=translate_mine_regions_to_iso3.get(row["Region"], ""),
        )
        product = row["Products"]

        # Parse year-specific capacities
        cap_by_year = {}
        for col in capacity_cols:
            year = Year(extract_year_from_column(col))
            capacity_value = row.get(col, 0)
            if pd.notna(capacity_value):
                cap_by_year[year] = Volumes(int(capacity_value * MT_TO_T))
            else:
                cap_by_year[year] = Volumes(0)

        # Parse year-specific mine prices
        mine_price_by_year = {}
        for col in mine_price_cols:
            year = Year(extract_year_from_column(col))
            price_value = row.get(col)
            if pd.notna(price_value):
                mine_price_by_year[year] = float(price_value)

        # Parse year-specific production costs
        production_cost_by_year = {}
        for col in production_cost_cols:
            year = Year(extract_year_from_column(col))
            cost_value = row.get(col)
            if pd.notna(cost_value):
                production_cost_by_year[year] = float(cost_value)

        # If mine_price is not available for a year, use production_cost as fallback
        for year in cap_by_year.keys():
            if year not in mine_price_by_year and year in production_cost_by_year:
                mine_price_by_year[year] = production_cost_by_year[year]

        # Generate unique supplier ID - use row_num which is guaranteed to be an int
        # For backward compatibility with old ID generation, use first capacity value
        first_capacity_mt = list(cap_by_year.values())[0] / MT_TO_T if cap_by_year else 0
        first_cost = list(production_cost_by_year.values())[0] if production_cost_by_year else 0
        row_for_id = row.copy()
        row_for_id["capacity"] = first_capacity_mt
        row_for_id["costs"] = first_cost
        supplier_id = generate_supplier_id(row_for_id, mine_location, product, row_index=row_num)

        mine = Supplier(
            supplier_id=supplier_id,
            commodity=Commodities(normalize_product_name(product)).value,
            location=mine_location,
            capacity_by_year=cap_by_year,
            production_cost_by_year=production_cost_by_year,
            mine_cost_by_year=production_cost_by_year,  # Using production_cost as mine_cost
            mine_price_by_year=mine_price_by_year,
        )
        mines.append(mine)

    # Log summary
    logging.info(f"  Processed {len(mines)} iron ore mines")
    logging.info(f"  Skipped {skipped_rows} rows (zero capacity)")
    if mines:
        commodities = set(m.commodity for m in mines)
        logging.info(f"  Commodities: {commodities}")
    # Validate the suppliers before returning
    validate_suppliers(mines, mine_data_df)
    return mines


def refine_demand_centers_for_major_countries(old_centers):
    """
    Refine centers for major (steel consuming) countries by breaking down the absolute amount from
    single, geographic centers into several economic centers.

    Note: This affects both GEO and TM.
    """
    from steelo.domain.constants import MAJOR_DEMAND_AND_SUPPLY_CENTERS

    # Combine the major centers with the existing centers
    major_iso3 = set(center["iso3"] for center in MAJOR_DEMAND_AND_SUPPLY_CENTERS.values())
    new_centers = []
    for iso3 in major_iso3:
        centers = {name: center for name, center in MAJOR_DEMAND_AND_SUPPLY_CENTERS.items() if center["iso3"] == iso3}
        center_counter = 1
        for name, center in centers.items():
            # Set ID for each new center (e.g., Brazil, Brazil_2, Brazil_3)
            old_center = next((v for v in old_centers if v.center_of_gravity.iso3 == iso3), None)
            if old_center is None:
                logger.warning(
                    f"No demand center found for major country {iso3}, skipping refinement for {name}. Ignore for USA, CAN, AUS, and BRA when using the mini Excel."
                )
                continue
            new_id = (
                f"{old_center.demand_center_id}_{center_counter}" if center_counter > 1 else old_center.demand_center_id
            )

            # Create Location objects for each new center
            location = Location(
                lat=center["latitude"],
                lon=center["longitude"],
                country=name,  # Use the specific center name (e.g., "Brazil_South")
                iso3=iso3,
                distance_to_other_iso3=None,
                region="Unknown",
            )

            # Create amount by year for each new center by scaling the old country-level amount with the share
            years = old_center.demand_by_year.keys()
            amount_by_year = {}
            for year in years:
                amount_by_year[Year(year)] = old_center.demand_by_year[Year(year)] * center["share"]

            # Create new center with the new location and amount_by_year
            new_centers.append(
                DemandCenter(
                    demand_center_id=new_id,
                    center_of_gravity=location,
                    demand_by_year=amount_by_year,
                )
            )
            center_counter += 1

        # Remove the old country-level centers for major countries
        corrected_old_centers = [v for v in old_centers if v.center_of_gravity.iso3 not in major_iso3]

    # Combine old (country-level for most countries) and new (subcountry-level for major countries) centers
    return corrected_old_centers + new_centers


def read_demand_centers(
    *,
    gravity_distances_path: Path,
    demand_excel_path: Path,
    demand_sheet_name: str,
    location_csv: Path,
) -> list[DemandCenter]:
    with gravity_distances_path.open("rb") as f:
        gravity_dict = pickle.load(f)
    demand_df = pd.read_excel(demand_excel_path, sheet_name=demand_sheet_name)
    demand_df = demand_df[demand_df["Scenario"] == CHOSEN_DEMAND_SCENARIO]
    # Strip whitespace from metric names to handle Excel inconsistencies
    demand_df["Metric"] = demand_df["Metric"].str.strip()
    demand_df = demand_df[demand_df["Metric"] == "Crude steel consumption for forming [kt]"]

    location_df = pd.read_csv(location_csv)
    locations = []
    for _, row in location_df.iterrows():
        if row["COUNTRY"] in translate_country_names_to_iso3:
            iso3 = translate_country_names_to_iso3[row["COUNTRY"]]
        else:
            try:
                py_country = pycountry.countries.get(alpha_2=row["ISO"])
                # print(py_country, py_country.alpha_3)
                iso3 = py_country.alpha_3
                # iso3 = pycountry.countries.search_fuzzy(row["COUNTRY"])[0].alpha_3
            except LookupError:
                # If the country is not found in pycountry, use a fallback
                logging.warning(f"Country '{row['COUNTRY']}' not found in pycountry. Using 'Unknown'.")
                iso3 = "Unknown"
        location = Location(
            lat=row["latitude"],
            lon=row["longitude"],
            country=row["COUNTRY"],
            region="Unknown",
            iso3=iso3,
        )
        if location.iso3 in gravity_dict:
            location.distance_to_other_iso3 = gravity_dict[location.iso3]
        locations.append(location)

    has_detailed_iso3 = "ISO-3 code" in demand_df.columns

    demand_centers = []
    for _, row in demand_df.iterrows():
        if has_detailed_iso3:
            demand_location = next(loc for loc in locations if loc.iso3 == row["ISO-3 code"])
        else:
            try:
                demand_location = next(loc for loc in locations if loc.iso3 == row["ISO3166-1-Alpha-3"])
            except StopIteration:
                raise ValueError(f"Location not found for country: {row['ISO3166-1-Alpha-3']}")
        demand_by_year = {}
        for col in demand_df.columns:
            try:
                year = Year(int(col))
                demand_by_year[year] = Volumes(KT_TO_T * int(row[col]))
            except ValueError:
                continue
        demand_center = DemandCenter(
            demand_center_id=demand_location.country,
            center_of_gravity=demand_location,
            demand_by_year=demand_by_year,
        )
        demand_centers.append(demand_center)

    # Refine demand centers for major countries - split into multiple economic centers
    return refine_demand_centers_for_major_countries(demand_centers)


def refine_scrap_centers_for_major_countries(old_centers):
    """
    Refine centers for major (scrap exporting) countries by breaking down the absolute amount from
    single, geographic centers into several economic centers.

    Note: This affects both GEO and TM.
    """
    from steelo.domain.constants import MAJOR_DEMAND_AND_SUPPLY_CENTERS

    # Combine the major centers with the existing centers
    major_iso3 = set(center["iso3"] for center in MAJOR_DEMAND_AND_SUPPLY_CENTERS.values())
    new_centers = []
    for iso3 in major_iso3:
        centers = {name: center for name, center in MAJOR_DEMAND_AND_SUPPLY_CENTERS.items() if center["iso3"] == iso3}
        center_counter = 1
        for name, center in centers.items():
            # Set ID for each new center (e.g., Brazil_scrap, Brazil_scrap_2, Brazil_scrap_3)
            old_center = next((v for v in old_centers if v.location.iso3 == iso3), None)
            if old_center is None:
                logger.warning(
                    f"No scrap center found for major country {iso3}, skipping refinement for {name}. Ignore for USA, CAN, AUS, and BRA when using the mini Excel."
                )
                continue
            new_id = f"{old_center.supplier_id}_{center_counter}" if center_counter > 1 else old_center.supplier_id

            # Create Location objects for each new center
            location = Location(
                lat=center["latitude"],
                lon=center["longitude"],
                country=name,  # Use the specific center name (e.g., "Brazil_South")
                iso3=iso3,
                distance_to_other_iso3=None,
                region="Unknown",
            )

            # Create amount by year for each new center by scaling the old country-level amount with the share
            years = old_center.capacity_by_year.keys()
            amount_by_year = {}
            for year in years:
                amount_by_year[Year(year)] = old_center.capacity_by_year[Year(year)] * center["share"]

            # Create constant production cost dictionary for all years in simulation horizon
            # This initial value of 450 will be overwritten annually in handlers.py based on BOF hot_metal costs
            production_cost_by_year = {
                Year(year): 450.0 for year in range(EXCEL_READER_START_YEAR, EXCEL_READER_END_YEAR + 1)
            }

            # Create new center with the new location and amount_by_year
            new_centers.append(
                Supplier(
                    commodity=Commodities.SCRAP.value,
                    supplier_id=new_id,
                    location=location,
                    capacity_by_year=amount_by_year,
                    production_cost_by_year=production_cost_by_year,
                    mine_cost_by_year={},
                    mine_price_by_year={},
                )
            )
            center_counter += 1

        # Remove the old country-level centers for major countries
        corrected_old_centers = [v for v in old_centers if v.location.iso3 not in major_iso3]

    # Combine old (country-level for most countries) and new (subcountry-level for major countries) centers
    return corrected_old_centers + new_centers


def read_scrap_as_suppliers(
    scrap_excel_path: str,
    scrap_sheet_name: str,
    location_csv: str,
    gravity_distances_pkl_path: Path | None = None,
) -> list[Supplier]:
    """
    Read scrap supply data from Excel and return a list of Supplier domain objects for scrap.
    """
    if not gravity_distances_pkl_path:
        raise ValueError("gravity_distances_pkl_path must be provided")
    gravity_path = gravity_distances_pkl_path
    with gravity_path.open("rb") as f:
        gravity_dict = pickle.load(f)
    scrap_df = pd.read_excel(scrap_excel_path, sheet_name=scrap_sheet_name)
    scrap_df = scrap_df[scrap_df["Scenario"] == CHOSEN_DEMAND_SCENARIO]
    # Strip whitespace from metric names to handle Excel inconsistencies
    scrap_df["Metric"] = scrap_df["Metric"].str.strip()
    scrap_df = scrap_df[scrap_df["Metric"] == "Total available scrap"]

    location_df = pd.read_csv(location_csv)
    locations = []
    for _, row in location_df.iterrows():
        if row["COUNTRY"] in translate_country_names_to_iso3:
            iso3 = translate_country_names_to_iso3[row["COUNTRY"]]
        else:
            try:
                py_country = pycountry.countries.get(alpha_2=row["ISO"])
                iso3 = py_country.alpha_3
            except LookupError:
                iso3 = "Unknown"
        location = Location(
            lat=row["latitude"],
            lon=row["longitude"],
            country=row["COUNTRY"],
            region="Unknown",
            iso3=iso3,
        )
        if location.iso3 in gravity_dict:
            location.distance_to_other_iso3 = gravity_dict[location.iso3]
        locations.append(location)

    has_detailed_iso3 = "ISO-3 code" in scrap_df.columns

    supply_centers = []
    for _, row in scrap_df.iterrows():
        if has_detailed_iso3:
            scrap_location = next(loc for loc in locations if loc.iso3 == row["ISO-3 code"])
        else:
            try:
                scrap_location = next(loc for loc in locations if loc.iso3 == row["ISO3166-1-Alpha-3"])
            except StopIteration:
                raise ValueError(f"Location not found for country: {row['ISO3166-1-Alpha-3']}")
        scrap_by_year: dict[Year, Volumes] = {}
        for col in scrap_df.columns:
            try:
                year = int(col)
                scrap_by_year[Year(year)] = Volumes(KT_TO_T * int(row[col]))
            except ValueError:
                continue

        # Create constant production cost dictionary for all years in simulation horizon
        # This initial value of 450 will be overwritten annually in handlers.py based on BOF hot_metal costs
        production_cost_by_year = {
            Year(year): 450.0 for year in range(EXCEL_READER_START_YEAR, EXCEL_READER_END_YEAR + 1)
        }

        supply_center = Supplier(
            commodity=Commodities.SCRAP.value,
            supplier_id=f"{scrap_location.country}_scrap",
            location=scrap_location,
            capacity_by_year=scrap_by_year,
            production_cost_by_year=production_cost_by_year,
            mine_cost_by_year={},
            mine_price_by_year={},
        )
        supply_centers.append(supply_center)

    # Refine scrap centers for major countries - split into multiple economic centers
    return refine_scrap_centers_for_major_countries(supply_centers)


def find_iso3s_of_trade_bloc(country_mappings: list, bloc_name: str, negation: bool = False) -> list[str]:
    """
    Find the ISO3 codes of countries in a given trade bloc using CountryMapping objects.

    This function dynamically detects trade bloc memberships based on boolean attributes
    in the CountryMapping objects, allowing any trade bloc column in the Excel sheet to be used.

    Args:
        country_mappings: List of CountryMapping objects.
        bloc_name: The name of the trade bloc (e.g., EU, EFTA/EUCU, OECD, NAFTA, etc.).
                   Special characters like "/" are normalized to "_".
        negation: If True, return ISO3 codes not in the specified trade bloc.

    Returns:
        List of ISO3 codes for the specified trade bloc.

    Raises:
        ValueError: If the bloc_name is not found as an attribute in any CountryMapping object.
    """
    # Normalize bloc name to match CountryMapping attributes
    # Replace special characters that might be in Excel column names
    bloc_name_normalized = bloc_name.replace("/", "_").replace(" ", "_").replace("-", "_")

    # Special case: EUCU maps to EUCJ for backwards compatibility
    bloc_name_normalized = bloc_name_normalized.replace("EUCU", "EUCJ")

    # Verify that at least one country mapping has this attribute
    if country_mappings and not hasattr(country_mappings[0], bloc_name_normalized):
        # Try to find similar attributes to provide helpful error message
        available_blocs = [
            attr
            for attr in dir(country_mappings[0])
            if not attr.startswith("_") and isinstance(getattr(country_mappings[0], attr, None), bool)
        ]
        raise ValueError(
            f"Trade bloc '{bloc_name}' (normalized to '{bloc_name_normalized}') not found in country mappings. "
            f"Available trade blocs: {', '.join(available_blocs)}"
        )

    iso3_codes = []
    for country in country_mappings:
        # Get the boolean value for this trade bloc
        is_member = getattr(country, bloc_name_normalized, False)

        # Apply negation logic
        if negation:
            if not is_member:
                iso3_codes.append(country.iso3)
        else:
            if is_member:
                iso3_codes.append(country.iso3)

    return iso3_codes


def read_carbon_costs(carbon_cost_excel_path: Path, sheet_name="Carbon cost") -> list[CarbonCostSeries]:
    """
    Read carbon costs from an Excel file and return a list of CarbonCostSeries objects.

    The function handles two formats:
    1. Legacy format: columns for "ISO 3-letter code", "year", "carbon_cost"
    2. New format: "ISO 3-letter code_Bloc" column with year columns (2020, 2021, etc.)

    Args:
        carbon_cost_excel_path (str): Path to the Excel file containing carbon costs.
        sheet_name (str): Name of the sheet to read from.

    Returns:
        list[CarbonCostSeries]: A list of CarbonCostSeries objects with ISO3 codes and carbon costs by year.
    """
    carbon_cost_df = pd.read_excel(carbon_cost_excel_path, sheet_name=sheet_name)
    carbon_costs: dict[str, dict[Year, float]] = {}

    # Check for new format with year columns
    year_columns = [col for col in carbon_cost_df.columns if str(col).isdigit() and len(str(col)) == 4]
    has_new_format = "ISO 3-letter code_Bloc" in carbon_cost_df.columns and year_columns
    has_legacy_detailed_iso3 = "ISO 3-letter code" in carbon_cost_df.columns

    if has_new_format:
        # New format: ISO3 in "ISO 3-letter code_Bloc" column, years as column headers
        for _, row in carbon_cost_df.iterrows():
            iso3 = row["ISO 3-letter code_Bloc"]

            # Skip rows with invalid ISO3 codes
            if pd.isna(iso3) or not isinstance(iso3, str) or len(str(iso3).strip()) != 3:
                continue

            iso3 = str(iso3).strip()
            if iso3 not in carbon_costs:
                carbon_costs[iso3] = {}

            # Process each year column
            for year_col in year_columns:
                try:
                    year = Year(int(year_col))
                    cost_value = row[year_col]

                    # Handle invalid values: if cost is not numeric, default to 0
                    if pd.isna(cost_value):
                        carbon_costs[iso3][year] = 0.0
                    else:
                        try:
                            carbon_costs[iso3][year] = float(cost_value)
                        except (ValueError, TypeError):
                            logging.warning(
                                f"Invalid carbon cost value '{cost_value}' for {iso3} in year {year}, defaulting to 0"
                            )
                            carbon_costs[iso3][year] = 0.0
                except (ValueError, TypeError):
                    logging.warning(f"Invalid year column '{year_col}', skipping")
                    continue

    elif has_legacy_detailed_iso3:
        # Legacy detailed format: separate rows for each year
        for _, row in carbon_cost_df.iterrows():
            iso3 = row["ISO 3-letter code"]
            year = Year(int(row["year"]))
            cost = row["carbon_cost"]
            if iso3 not in carbon_costs:
                carbon_costs[iso3] = {}
            # Handle invalid values: if cost is not numeric, default to 0
            if pd.isna(cost):
                carbon_costs[iso3][year] = 0.0
            else:
                try:
                    carbon_costs[iso3][year] = float(cost)
                except (ValueError, TypeError):
                    logging.warning(f"Invalid carbon cost value '{cost}' for {iso3} in year {year}, defaulting to 0")
                    carbon_costs[iso3][year] = 0.0
    else:
        # Legacy simple format
        for _, row in carbon_cost_df.iterrows():
            iso3 = row["country_iso3"]
            year = Year(int(row["year"]))
            cost = row["carbon_cost"]
            if iso3 not in carbon_costs:
                carbon_costs[iso3] = {}
            # Handle invalid values: if cost is not numeric, default to 0
            if pd.isna(cost):
                carbon_costs[iso3][year] = 0.0
            else:
                try:
                    carbon_costs[iso3][year] = float(cost)
                except (ValueError, TypeError):
                    logging.warning(f"Invalid carbon cost value '{cost}' for {iso3} in year {year}, defaulting to 0")
                    carbon_costs[iso3][year] = 0.0

    carbon_costs_list = []
    for iso3, costs in carbon_costs.items():
        carbon_cost_series = CarbonCostSeries(iso3=iso3, carbon_cost=costs)
        carbon_costs_list.append(carbon_cost_series)
    return carbon_costs_list


def read_regional_emissivities(excel_path: Path, grid_sheet_name: str, gas_sheet_name: str) -> list[RegionEmissivity]:
    """
    Read grid_emissivity from from an Excel file and return a dictionary mapping ISO3 codes to Year and cost.

    Args:
        excel_path (Path): Path to the Excel file containing grid emissions data.
        grid_sheet_name (str): Name of the sheet containing grid emissions data.
        gas_sheet_name (str): Name of the sheet containing gas coke emissions data.
    Returns:
        list[RegionEmissivity]: A list of RegionEmissivity objects containing emissions data
        for each country and scenario.
    """
    grid_emission_df = pd.read_excel(excel_path, sheet_name=grid_sheet_name)
    gas_coke_emissions_df = pd.read_excel(excel_path, sheet_name=gas_sheet_name)

    carbon_intensity_columns = [col for col in grid_emission_df.columns if col.lower().startswith("ghg_factor_scope_")]

    # Check which ISO column name is used
    iso_column = None
    if "country_iso3" in grid_emission_df.columns:
        iso_column = "country_iso3"
    elif "ISO 3-letter code" in grid_emission_df.columns:
        iso_column = "ISO 3-letter code"
    elif "ISO-3 code" in grid_emission_df.columns:
        iso_column = "ISO-3 code"
    else:
        raise ValueError(
            f"Cannot find ISO column in grid emission sheet. Available columns: {list(grid_emission_df.columns)}"
        )

    # 1) Group grid emissivities
    # Structure: {year: {"Electricity": value}} using Vector column to name the emission factor
    # For grid emissions, Vector is typically "Electricity" and we use ghg_factor_scope_2 (grid electricity is scope 2)
    def process_grid_group(df):
        # Set year as index, take ghg_factor_scope_2 as the value, and use "Electricity" as the key
        result = {}
        for _, row in df.iterrows():
            year = row["year"]
            # Grid electricity emissions are in scope 2
            if "ghg_factor_scope_2" in row:
                result[year] = {"Electricity": row["ghg_factor_scope_2"]}
        return result

    grouped_data = grid_emission_df.groupby([iso_column, "projection_scenario"]).apply(
        lambda x: process_grid_group(x)  # type: ignore[arg-type]
    )

    # pull out the "meta" info once
    meta = grid_emission_df.drop_duplicates(subset=[iso_column, "projection_scenario"]).set_index(
        [iso_column, "projection_scenario"]
    )

    # 2) Group gas coke emissions by vector name (only one year data) and no projections
    carbon_intensity_columns = [
        col for col in gas_coke_emissions_df.columns if col.lower().startswith("ghg_factor_scope_")
    ]

    # Check which ISO column name is used in gas_coke_emissions_df
    gas_iso_column = None
    if "country_iso3" in gas_coke_emissions_df.columns:
        gas_iso_column = "country_iso3"
    elif "ISO 3-letter code" in gas_coke_emissions_df.columns:
        gas_iso_column = "ISO 3-letter code"
    elif "ISO-3 code" in gas_coke_emissions_df.columns:
        gas_iso_column = "ISO-3 code"
    else:
        raise ValueError(
            f"Cannot find ISO column in gas/coke emissions sheet. Available columns: {list(gas_coke_emissions_df.columns)}"
        )

    grouped_gas_coke = gas_coke_emissions_df.groupby([gas_iso_column, "Vector"])[carbon_intensity_columns].sum()

    grid_emissivity_list: list[RegionEmissivity] = []
    for key, metrics in grouped_data.items():
        iso3_raw, scenario_raw = key  # type: ignore[misc]
        iso3: str = str(iso3_raw)  # type: ignore[has-type]
        scenario: str = str(scenario_raw)  # type: ignore[has-type]
        # metrics is {'grid_carbon_intensity_value': {year: value}}
        emissivity = metrics  # type: ignore[index]

        # look up the country name & net‐zero year
        country_name: str = meta.at[(iso3_raw, scenario_raw), "country"]  # type: ignore[assignment]

        # Cast the results of to_dict() to the expected type
        coke_dict = cast(dict[str, float], grouped_gas_coke.loc[iso3].loc["Coking coal"].to_dict())
        gas_dict = cast(dict[str, float], grouped_gas_coke.loc[iso3].loc["Natural gas"].to_dict())

        grid_emissivity_list.append(
            RegionEmissivity(
                iso3=iso3,  # type: ignore[has-type]
                country_name=country_name,
                scenario=scenario.removeprefix("projection_").replace("_", " ").title(),  # type: ignore[has-type]
                grid_emissivity={key: value for key, value in emissivity.items()},
                coke_emissivity=coke_dict,
                gas_emissivity=gas_dict,
            )
        )
    return grid_emissivity_list


def read_tariffs(tariff_excel_path: str, tariff_sheet_name: str, country_mappings: list) -> list[TradeTariff]:
    """
    Read tariff data from an Excel file and return a list of TradeTariff objects.

    Trade bloc names in the "From ISO3" and "To ISO3" columns are automatically detected
    from the available boolean attributes in the CountryMapping objects.

    Args:
        tariff_excel_path: Path to the Excel file containing tariff data.
        tariff_sheet_name: Name of the sheet in the Excel file to read from.
        country_mappings: List of CountryMapping objects used to resolve trade bloc names.

    Returns:
        List of TradeTariff objects.
    """
    tariff_df = pd.read_excel(tariff_excel_path, sheet_name=tariff_sheet_name)
    tariffs = []

    # Dynamically detect available trade blocs from country_mappings
    # Get all boolean attributes from the first country mapping object
    supported_blocs = []
    if country_mappings:
        supported_blocs = [
            attr
            for attr in dir(country_mappings[0])
            if not attr.startswith("_") and isinstance(getattr(country_mappings[0], attr, None), bool)
        ]
        # Add common variants with "/" for Excel column names (e.g., EFTA/EUCU)
        # This allows the tariff sheet to use either EFTA_EUCJ or EFTA/EUCU
        if "EFTA_EUCJ" in supported_blocs:
            supported_blocs.append("EFTA/EUCU")

    logger.info(
        f"Detected {len(supported_blocs)} available trade blocs for tariff processing: {', '.join(supported_blocs)}"
    )

    # Drop rows with NaN values in the 'Tariff scenario name' column
    tariff_df = tariff_df.dropna(subset=["Tariff scenario name"])
    # tariff_df["Metric (Volume/Emissions)"] = tariff_df["Metric (Volume/Emissions)"].fillna("")
    for _, row in tariff_df.iterrows():
        # ingnore all 0/empty rows (0 quotas are let through):
        if (
            (row["Tax [$/t]"] == 0 or pd.isna(row["Tax [$/t]"]))
            and (row["Tax [%]"] == 0 or pd.isna(row["Tax [%]"]))
            and (pd.isna(row["Quota [t]"]))
        ):
            continue
        if math.isnan(row["Start year"]):
            start_year = 2000
        else:
            start_year = int(row["Start year"])
        if math.isnan(row["End year"]):
            end_year = 2100
        else:
            end_year = int(row["End year"])

        metric = row["Metric (Volume/Emissions)"]

        from_iso3_entry = row["From ISO3"]
        to_iso3_entry = row["To ISO3"]

        # Process "From ISO3" entry
        # if it starts with a NOT (then it's the negation of a trade bloc)
        if from_iso3_entry.startswith("NOT "):
            from_iso3_bloc = from_iso3_entry[4:]
            from_iso3_list = find_iso3s_of_trade_bloc(country_mappings, from_iso3_bloc, negation=True)
        elif from_iso3_entry in supported_blocs:
            from_iso3_list = find_iso3s_of_trade_bloc(country_mappings, from_iso3_entry)
        else:
            from_iso3_list = [from_iso3_entry]

        # Process "To ISO3" entry
        if to_iso3_entry.startswith("NOT "):
            to_iso3_bloc = to_iso3_entry[4:]
            to_iso3_list = find_iso3s_of_trade_bloc(country_mappings, to_iso3_bloc, negation=True)
        elif to_iso3_entry in supported_blocs:
            to_iso3_list = find_iso3s_of_trade_bloc(country_mappings, to_iso3_entry)
        else:
            to_iso3_list = [to_iso3_entry]

        for from_iso3 in from_iso3_list:
            for to_iso3 in to_iso3_list:
                if from_iso3 == to_iso3:
                    # Skip tariffs within the same country
                    continue
                # Create a TradeTariff object for each combination of from_iso3 and to_iso3
                tariff = TradeTariff(
                    tariff_name=row["Tariff scenario name"],
                    from_iso3=from_iso3,
                    to_iso3=to_iso3,
                    commodity=normalize_product_name(row["Commodity"]),
                    metric="" if pd.isna(metric) else metric,
                    tax_absolute=row["Tax [$/t]"],
                    tax_percentage=row["Tax [%]"],
                    quota=row["Quota [t]"],
                    start_date=Year(start_year) if start_year is not None else None,
                    end_date=Year(end_year) if end_year is not None else None,
                )
                tariffs.append(tariff)

    return tariffs


def read_capex_and_learning_rate_data(
    capex_excel_path: str, sheet_name: str = "Techno-economic details"
) -> list[Capex]:
    """
    Read capex and learning rate data from an Excel file and return a list of Capex objects.

    Now includes Product column from the data to properly categorize technologies.

    Args:
        capex_excel_path (str): Path to the Excel file containing capex and learning rate data.
        sheet_name (str): Name of the sheet in the Excel file to read from.
    """
    data = pd.read_excel(capex_excel_path, sheet_name=sheet_name)

    # Check if data has required columns
    if "Value" not in data.columns:
        raise ValueError(f"Capex data format from sheet: '{sheet_name}' not recognized. Expected 'Value' column.")

    # Check if Product column exists (new format)
    has_product_column = "Product" in data.columns

    if has_product_column:
        # New format with Product column - group by Technology, Product, and Metric
        # Fill NaN products with empty string to avoid dropping them during groupby
        data["Product"] = data["Product"].fillna("")
        grouped = data.groupby(["Technology", "Product", "Metric"])["Value"].sum().unstack("Metric")
        # Clean up column names by removing " capex" suffix
        grouped.columns = grouped.columns.str.replace(" capex", "")
        # Reset index to make Technology and Product accessible as columns
        grouped = grouped.reset_index()
    else:
        # Old format without Product column - use existing logic for backwards compatibility
        grouped = data.groupby(["Technology", "Metric"])["Value"].sum().unstack(0)
        grouped.index = grouped.index.str.replace(" capex", "").T
        # Convert to DataFrame format similar to new format
        grouped = grouped.T.reset_index()
        grouped["Product"] = ""  # Default empty product for old format

    capex_list = []
    for _, row in grouped.iterrows():
        technology_name = row["Technology"]

        # Extract product name, handle NaN values
        product = row.get("Product")
        if pd.isna(product):
            product = ""
        else:
            product = normalize_commodity_name(str(product))  # Normalize to lowercase

        # Handle empty or invalid values for greenfield and renovation capex
        greenfield = row.get("Greenfield", 0.0)
        renovation = row.get("Renovation", 0.0)

        # Convert empty strings, byte strings, or NaN to 0.0
        if pd.isna(greenfield) or greenfield == "" or greenfield == b"":
            greenfield = 0.0
        else:
            try:
                greenfield = float(greenfield)
            except (ValueError, TypeError):
                logger.warning(f"Invalid greenfield capex value for {technology_name}: {greenfield}, defaulting to 0.0")
                greenfield = 0.0

        if pd.isna(renovation) or renovation == "" or renovation == b"":
            renovation_share = 0.0
        else:
            try:
                # Always treat renovation as a share to be multiplied by greenfield
                renovation_share = float(renovation)
            except (ValueError, TypeError):
                logger.warning(f"Invalid renovation capex value for {technology_name}: {renovation}, defaulting to 0.0")
                renovation_share = 0.0

        # Read learning rate from Excel (already given as fraction, e.g., 0.05 for 5%)
        learning_rate = row.get("Learning rate")
        if learning_rate is None:
            raise ValueError(f"Learning rate missing for technology: {technology_name}")
        if pd.isna(learning_rate) or learning_rate == "" or learning_rate == b"":
            learning_rate = 0.0
        else:
            try:
                learning_rate = float(learning_rate)
            except (ValueError, TypeError):
                logger.warning(f"Invalid learning rate value for {technology_name}: {learning_rate}, defaulting to 0.0")
                learning_rate = 0.0

        capex_list.append(
            Capex(
                technology_name=str(technology_name),
                product=product,
                greenfield_capex=greenfield,
                capex_renovation_share=renovation_share,
                learning_rate=learning_rate,
            )
        )

    return capex_list


def read_cost_of_capital(coc_excel_path: str, sheet_name: str = "Cost of capital") -> list[CostOfCapital]:
    """
    Read cost of capital data from an Excel file and return a list of CostOfCapital objects.

    Args:
        coc_excel_path (str): Path to the Excel file containing cost of capital data.
        sheet_name (str): Name of the sheet in the Excel file to read from.

    Returns:
        list[CostOfCapital]: A list of CostOfCapital objects containing the cost of capital data.
    """

    data = pd.read_excel(coc_excel_path, sheet_name=sheet_name)

    # Handle the case where "More risky assets" column might be unnamed (just whitespace)
    # In the actual Excel file, this column has a single space as its header
    if "More risky assets" not in data.columns:
        # Check for columns that are just whitespace
        for col in data.columns:
            if col.strip() == "" and data.columns.get_loc(col) == 3:  # 4th column (index 3)
                data = data.rename(columns={col: "More risky assets"})
                break
        else:
            # If still not found, try to use the 4th column by position
            if len(data.columns) >= 4:
                data = data.rename(columns={data.columns[3]: "More risky assets"})

    cost_of_capital_list = []
    for _, row in data.iterrows():
        cost_of_capital_list.append(
            CostOfCapital(
                country=row["Country"],
                iso3=row["ISO-3 Code"],
                debt_res=row["Debt - Renewables"],
                equity_res=row["Equity - Renewables"],
                wacc_res=row["WACC - Renewables"],
                debt_other=row["Debt - Other assets"],
                equity_other=row["Equity - Other assets"],
                wacc_other=row["WACC - Other assets"],
            )
        )

    return cost_of_capital_list


def read_legal_process_connectors(
    excel_path: Path, sheet_name: str = "Legal Process connectors"
) -> list[LegalProcessConnector]:
    df = pd.read_excel(excel_path, sheet_name=sheet_name)

    legal_process_connectors = []
    for _, row in df.iterrows():
        try:
            from_str = str(row["from_process"])
            if from_str.split("_")[-1] in ["supply", "demand"] or "_" not in from_str:
                from_name = from_str
            else:
                from_name = from_str.split("_")[-1].upper()  # extract technology

            # if technology is charcoal, rename to BF_CHARCOAL
            from_name = from_name.replace("CHARCOAL", "BF_CHARCOAL")

            to_str = str(row["to_process"])
            if to_str.split("_")[-1] in ["supply", "demand"] or "_" not in to_str:
                to_name = to_str
            else:
                to_name = to_str.split("_")[-1].upper()  # extract technology

            # if technology is charcoal, rename to BF_CHARCOAL
            to_name = to_name.replace("CHARCOAL", "BF_CHARCOAL")

            lpc = LegalProcessConnector(from_technology_name=from_name, to_technology_name=to_name)
            legal_process_connectors.append(lpc)
        except KeyError as e:
            logger.warning(f"Error parsing row: {e}")
            continue

    logger.info(f"Read {len(legal_process_connectors)} legal process connectors")
    return legal_process_connectors


def read_country_mappings(excel_path: Path, sheet_name: str = "Country mapping") -> list[CountryMapping]:
    """
    Reads country mapping data from the specified Excel sheet and converts it
    into a list of CountryMapping domain objects.

    Trade bloc membership columns (columns containing True/False values) are
    automatically detected and added as boolean attributes to each CountryMapping object.

    Args:
        excel_path: Path to the master input Excel file.
        sheet_name: The name of the sheet to read.

    Returns:
        A list of CountryMapping objects.
    """
    try:
        df = pd.read_excel(excel_path, sheet_name=sheet_name)
    except ValueError:
        logger.error(f"Sheet '{sheet_name}' not found in {excel_path}")
        return []

    # Define the core columns that should not be treated as trade bloc memberships
    core_columns = {
        "Country",
        "ISO 2-letter code",
        "ISO 3-letter code",
        "irena_name",
        "irena_region",
        "region_for_outputs",
        "ssp_region",
        "gem_country",
        "eu_or_non_eu",
        "ws_region",
        "tiam-ucl_region",
    }

    # Detect boolean columns (trade bloc memberships)
    # These are columns with True/False values that aren't in the core set
    boolean_columns = []
    for col in df.columns:
        if col not in core_columns:
            # Check if the column contains boolean-like values
            # Sample the first few non-null values to determine if it's boolean
            non_null_values = df[col].dropna()
            if len(non_null_values) > 0:
                # Check if values are boolean, or numeric 0/1, or string true/false variants
                sample_values = non_null_values.head(10)
                is_boolean = all(
                    isinstance(val, (bool, np.bool_))
                    or (isinstance(val, (int, float, np.integer, np.floating)) and val in [0, 1, 0.0, 1.0])
                    or (isinstance(val, str) and val.lower() in ["true", "false", "yes", "no", "0", "1"])
                    for val in sample_values
                )
                if is_boolean:
                    boolean_columns.append(col)

    if boolean_columns:
        logger.info(f"Detected {len(boolean_columns)} trade bloc membership columns: {', '.join(boolean_columns)}")

    mappings = []
    for idx, row in df.iterrows():
        try:
            # Build the base mapping with core attributes
            # Type hint to allow str, None, and bool values
            mapping_kwargs: dict[str, str | None | bool] = {
                "country": str(row["Country"]),
                "iso2": str(row["ISO 2-letter code"]),
                "iso3": str(row["ISO 3-letter code"]),
                "irena_name": str(row["irena_name"]),
                "irena_region": str(row["irena_region"]) if pd.notna(row["irena_region"]) else None,
                "region_for_outputs": str(row["region_for_outputs"]),
                "ssp_region": str(row["ssp_region"]),
                "gem_country": str(row["gem_country"]) if pd.notna(row["gem_country"]) else None,
                "eu_region": (
                    str(row["eu_or_non_eu"]) if "eu_or_non_eu" in row and pd.notna(row["eu_or_non_eu"]) else None
                ),
                "ws_region": str(row["ws_region"]) if pd.notna(row["ws_region"]) else None,
                "tiam_ucl_region": str(row["tiam-ucl_region"]),
            }

            # Add boolean columns dynamically
            for col in boolean_columns:
                if col in row:
                    val = row[col]
                    # Convert to boolean, handling various input formats
                    if pd.isna(val):
                        boolean_val = False
                    elif isinstance(val, (bool, np.bool_)):
                        boolean_val = bool(val)
                    elif isinstance(val, (int, float, np.integer, np.floating)):
                        boolean_val = bool(val)
                    elif isinstance(val, str):
                        boolean_val = val.lower() in ["true", "yes", "1"]
                    else:
                        boolean_val = False

                    # Normalize column name for attribute (replace special chars with underscores)
                    attr_name = col.replace("/", "_").replace(" ", "_").replace("-", "_")
                    mapping_kwargs[attr_name] = boolean_val

            mapping = CountryMapping(**mapping_kwargs)  # type: ignore[arg-type]
            mappings.append(mapping)
        except Exception as e:
            # idx from iterrows is always an integer for standard DataFrames
            row_number = int(idx) + 2 if isinstance(idx, (int, float)) else 2
            logger.warning(f"Skipping row {row_number} in '{sheet_name}' due to validation error: {e}")
            continue

    logger.info(f"Successfully read {len(mappings)} country mappings from '{sheet_name}'.")
    return mappings


def read_carbon_border_mechanisms(excel_path: Path, sheet_name: str = "CBAM") -> list[CarbonBorderMechanism]:
    """
    Reads carbon border adjustment mechanism data from the CBAM sheet.

    Expected sheet structure:
    - Row 0: "CBAM active?" - 1 if active, 0 if inactive
    - Row 1: "Year CBAM begins" - start year
    - Row 2: "Year CBAM ends" - end year
    - Row 3: "Common carbon cost across the bloc?" - not used

    The function dynamically detects trade bloc columns (any column except the first
    descriptor column) and processes them as potential carbon border mechanisms.

    Args:
        excel_path: Path to the master input Excel file.
        sheet_name: The name of the sheet to read (default: "CBAM").

    Returns:
        A list of CarbonBorderMechanism objects.
    """
    try:
        df = pd.read_excel(excel_path, sheet_name=sheet_name)
    except ValueError:
        logger.error(f"Sheet '{sheet_name}' not found in {excel_path}")
        return []

    mechanisms = []

    # Check that we have at least 3 rows (active, start year, end year)
    if len(df) < 3:
        logger.warning(f"CBAM sheet has insufficient rows ({len(df)}) - expected at least 3")
        return []

    # Dynamically detect mechanism columns
    # Skip the first column (assumed to be the row descriptor/label column)
    # All other columns are potential mechanisms
    mechanism_configs = []
    for col in df.columns[1:]:  # Skip first column
        # Normalize the column name to match CountryMapping attributes
        region_column = col.replace("/", "_").replace(" ", "_").replace("-", "_")
        # Special case: EUCU maps to EUCJ
        region_column = region_column.replace("EUCU", "EUCJ")
        mechanism_configs.append((col, region_column))

    if mechanism_configs:
        logger.info(
            f"Detected {len(mechanism_configs)} potential CBAM mechanism columns: "
            f"{', '.join(col for col, _ in mechanism_configs)}"
        )

    # Process each mechanism
    for mechanism_name, region_column in mechanism_configs:
        if mechanism_name not in df.columns:
            logger.warning(f"Column '{mechanism_name}' not found in CBAM sheet")
            continue

        try:
            # Row 0: Check if CBAM is active (1 = active, 0 = inactive)
            is_active = df.iloc[0][mechanism_name]

            # Skip if not active
            if pd.isna(is_active) or is_active == 0 or is_active is False:
                logger.info(f"CBAM mechanism '{mechanism_name}' is not active (value: {is_active})")
                continue

            # Row 1: Start year
            start_year_val = df.iloc[1][mechanism_name]
            # Row 2: End year
            end_year_val = df.iloc[2][mechanism_name]

            # Convert to int, handling various formats
            start_year = None
            end_year = None

            if pd.notna(start_year_val):
                try:
                    start_year = int(start_year_val)
                except (ValueError, TypeError):
                    logger.warning(f"Invalid start year for {mechanism_name}: {start_year_val}")
                    continue

            if pd.notna(end_year_val):
                try:
                    end_year = int(end_year_val)
                except (ValueError, TypeError):
                    logger.warning(f"Invalid end year for {mechanism_name}: {end_year_val}")
                    # Continue with None end year (ongoing mechanism)

            if start_year:
                mechanism = CarbonBorderMechanism(
                    mechanism_name=mechanism_name,
                    applying_region_column=region_column,
                    start_year=start_year,
                    end_year=end_year,
                )
                mechanisms.append(mechanism)
                logger.info(f"Created {mechanism_name} mechanism: {start_year}-{end_year if end_year else 'ongoing'}")
            else:
                logger.warning(f"No valid start year for {mechanism_name} - skipping")

        except Exception as e:
            logger.error(f"Error processing mechanism {mechanism_name}: {e}")
            continue

    logger.info(f"Successfully read {len(mechanisms)} carbon border mechanisms from '{sheet_name}'.")
    return mechanisms


def read_hydrogen_efficiency(excel_path: Path, sheet_name: str = "Hydrogen efficiency") -> list[HydrogenEfficiency]:
    """
    Read hydrogen efficiency data from Excel sheet and return domain objects.

    Args:
        excel_path: Path to the Excel file
        sheet_name: Name of the sheet containing hydrogen efficiency data

    Returns:
        List of HydrogenEfficiency domain objects
    """
    df = pd.read_excel(excel_path, sheet_name=sheet_name)

    hydrogen_efficiency = []
    for _, row in df.iterrows():
        # Handle missing/invalid data
        if pd.isna(row["Year"]) or pd.isna(row["Value"]):
            logger.warning("Skipping row with missing Year or Value")
            continue

        try:
            data = HydrogenEfficiency(year=Year(int(row["Year"])), efficiency=float(row["Value"]))
            hydrogen_efficiency.append(data)
        except (ValueError, TypeError) as e:
            logger.warning(f"Error parsing hydrogen efficiency row: {e}")
            continue

    logger.info(f"Read {len(hydrogen_efficiency)} hydrogen efficiency entries from '{sheet_name}'")
    return hydrogen_efficiency


def read_hydrogen_capex_opex(
    excel_path: Path, sheet_name: str = "Hydrogen CAPEX_OPEX component"
) -> list[HydrogenCapexOpex]:
    """
    Read hydrogen CAPEX/OPEX component data from Excel sheet and return domain objects.

    Args:
        excel_path: Path to the Excel file
        sheet_name: Name of the sheet containing hydrogen CAPEX/OPEX data

    Returns:
        List of HydrogenCapexOpex domain objects
    """
    df = pd.read_excel(excel_path, sheet_name=sheet_name)

    hydrogen_capex_opex = []
    for _, row in df.iterrows():
        # Handle missing/invalid data
        if pd.isna(row["country_code"]):
            logger.warning("Skipping row with missing country_code")
            continue

        try:
            # Extract year columns (all columns except 'country_code')
            year_values = {}
            for col in df.columns:
                if col != "country_code" and str(col).isdigit():
                    year = Year(int(col))
                    value = row[col]
                    if not pd.isna(value):
                        year_values[year] = float(value)

            if year_values:  # Only create object if we have at least one year value
                data = HydrogenCapexOpex(country_code=str(row["country_code"]), values=year_values)
                hydrogen_capex_opex.append(data)
        except (ValueError, TypeError) as e:
            logger.warning(f"Error parsing hydrogen CAPEX/OPEX row: {e}")
            continue

    logger.info(f"Read {len(hydrogen_capex_opex)} hydrogen CAPEX/OPEX entries from '{sheet_name}'")
    return hydrogen_capex_opex


def _normalize_cost_item(cost_item: str | None, row_index: int) -> str | None:
    """
    Normalize cost item to standard values: 'opex', 'capex', 'cost of debt'.

    Args:
        cost_item: Raw cost item string from Excel
        row_index: Row index for logging purposes

    Returns:
        Normalized cost item string, or None if row should be skipped.
    """
    logger = logging.getLogger(__name__)

    if cost_item is None or (isinstance(cost_item, float) and math.isnan(cost_item)) or str(cost_item).strip() == "":
        return "opex"

    normalized = str(cost_item).strip().lower()

    if normalized in ("opex",):
        return "opex"
    elif normalized in ("capex",):
        return "capex"
    elif normalized in ("cost of debt", "debt"):
        return "cost of debt"
    elif normalized in ("hydrogen", "h2"):
        return "hydrogen"
    elif normalized in ("electricity",):
        return "electricity"
    else:
        logger.warning(f"Skipping row {row_index}: unknown cost item '{cost_item}'")
        return None


def _expand_technology_pattern(pattern: str | None, all_technologies: list[str]) -> list[str]:
    """
    Expand technology pattern to list of matching technologies.

    Args:
        pattern: Technology name, empty for all, or wildcard with '*' suffix
        all_technologies: List of all available technology names

    Returns:
        List of matching technology names. Returns all_technologies for empty pattern.
    """
    if pattern is None or (isinstance(pattern, float) and math.isnan(pattern)) or str(pattern).strip() == "":
        return all_technologies

    pattern_str = str(pattern).strip()

    if pattern_str.endswith("*"):
        prefix = pattern_str[:-1]
        matches = [tech for tech in all_technologies if prefix in tech]
        if not matches:
            logging.warning(f"Wildcard pattern '{pattern_str}' matched no technologies")
            return []
        return matches
    else:
        if pattern_str not in all_technologies:
            logging.warning(f"Technology '{pattern_str}' not found in available technologies")
            return []
        return [pattern_str]


def _parse_subsidy_type(subsidy_type: str | None, cost_item: str, row_index: int) -> str | None:
    """
    Parse subsidy type from Excel, defaulting based on cost item.

    Args:
        subsidy_type: Raw subsidy type from Excel ("Absolute", "Relative", or empty)
        cost_item: Normalized cost item
        row_index: Row index for logging purposes

    Returns:
        "absolute", "relative", or None if row should be skipped.
    """
    if (
        subsidy_type is None
        or (isinstance(subsidy_type, float) and math.isnan(subsidy_type))
        or str(subsidy_type).strip() == ""
    ):
        return "absolute"  # Default

    normalized = str(subsidy_type).strip().lower()

    if normalized == "relative":
        if cost_item == "cost of debt":
            logging.warning(f"Skipping row {row_index}: relative subsidies not supported for cost of debt")
            return None
        return "relative"

    return "absolute"


def read_subsidies(
    excel_path: Path,
    subsidies_sheet: str = "Subsidies",
    country_mapping_sheet: str = "Country mapping",
    techno_economic_sheet: str = "Techno-economic details",
) -> list[Subsidy]:
    """
    Read subsidies data from Excel sheet and return domain objects.

    Supports the new subsidies format with:
    - Single 'Subsidy amount' + 'Subsidy type' columns
    - Technology wildcard matching (e.g., 'CCS*' matches all CCS technologies)
    - Cost item normalization (OPEX, CAPEX, COST OF DEBT)
    - Percentage values as whole numbers (10 = 10%), converted to decimal internally

    Args:
        excel_path: Path to the Excel file
        subsidies_sheet: Name of the sheet containing subsidies data
        country_mapping_sheet: Name of the sheet containing country mappings with trade bloc columns
        techno_economic_sheet: Name of the sheet containing technology names

    Returns:
        List of Subsidy domain objects
    """
    logger = logging.getLogger(__name__)

    subsidies_df = pd.read_excel(excel_path, sheet_name=subsidies_sheet)
    country_df = pd.read_excel(excel_path, sheet_name=country_mapping_sheet)
    # Trade bloc columns are columns containing only True/False values
    trade_bloc_columns = [
        col
        for col in country_df.columns
        if country_df[col].dtype == bool or set(country_df[col].dropna().unique()).issubset({True, False})
    ]

    # Get all technology names from techno-economic details sheet
    techno_df = pd.read_excel(excel_path, sheet_name=techno_economic_sheet)
    all_technologies = techno_df["Technology"].dropna().unique().tolist()

    # Normalize column names to handle headers with newlines and descriptions
    def normalize_subsidy_column(col: str) -> str:
        """Normalize column names by taking first line and extracting key terms."""
        # Take first line before any newline
        col = col.split("\n")[0].strip()
        # Map common patterns to normalized names
        col_lower = col.lower()
        if "location" in col_lower:
            return "Location"
        elif "technology" in col_lower:
            return "Technology"
        elif "cost item" in col_lower:
            return "Cost item"
        elif "subsidy type" in col_lower:
            return "Subsidy type"
        elif "subsidy amount" in col_lower:
            return "Subsidy amount"
        elif "start year" in col_lower:
            return "Start year"
        elif "end year" in col_lower:
            return "End year"
        elif "scenario" in col_lower:
            return "Scenario name"
        return col

    # Apply normalization to column names
    subsidies_df.columns = [normalize_subsidy_column(col) for col in subsidies_df.columns]

    # Only keep required columns (ignore any after End year like notes)
    required_columns = [
        "Scenario name",
        "Location",
        "Technology",
        "Cost item",
        "Subsidy type",
        "Subsidy amount",
        "Start year",
        "End year",
    ]

    # Validate required columns exist
    missing_cols = [col for col in required_columns if col not in subsidies_df.columns]
    if missing_cols:
        raise ValueError(f"Missing required columns in subsidies sheet: {missing_cols}")

    subsidies_df = subsidies_df[required_columns]

    subsidies = []
    for _index, row in subsidies_df.iterrows():
        row_idx = _index if isinstance(_index, int) else 0  # type: int
        # Skip rows with empty subsidy amount
        subsidy_amount = row["Subsidy amount"]
        if pd.isna(subsidy_amount):
            logger.warning(f"Skipping row {_index}: empty subsidy amount")
            continue

        # Normalize cost item
        cost_item = _normalize_cost_item(row["Cost item"], row_idx)
        if cost_item is None:
            continue

        # Parse subsidy type
        subsidy_type = _parse_subsidy_type(row["Subsidy type"], cost_item, row_idx)
        if subsidy_type is None:
            continue

        # Convert percentage values to decimal
        # - Relative subsidies: percentage to decimal (e.g., 10% -> 0.1)
        # - Cost of debt absolute: percentage points to decimal (e.g., 5 -> 0.05)
        # - Other absolute subsidies: keep as-is (e.g., USD/t output)
        if subsidy_type == "relative":
            subsidy_amount = float(subsidy_amount) / 100
        elif cost_item == "cost_of_debt":
            # Absolute cost of debt subsidy is given as percentage point reduction
            subsidy_amount = float(subsidy_amount) / 100
        else:
            subsidy_amount = float(subsidy_amount)

        # Expand trade bloc to ISO3 list
        region = row["Location"]
        if region in trade_bloc_columns:
            iso3_list = country_df[country_df[region]]["ISO 3-letter code"].tolist()
        else:
            iso3_list = [region]

        # Expand technology pattern
        technology_list = _expand_technology_pattern(row["Technology"], all_technologies)

        # Create subsidies for each ISO3 and technology combination
        for iso3 in iso3_list:
            for technology in technology_list:
                subsidies.append(
                    Subsidy(
                        scenario_name=row["Scenario name"],
                        iso3=iso3,
                        start_year=Year(int(row["Start year"])),
                        end_year=Year(int(row["End year"])),
                        technology_name=technology,
                        cost_item=cost_item,
                        subsidy_type=subsidy_type,
                        subsidy_amount=subsidy_amount,
                    )
                )

    logger.info(f"Read {len(subsidies)} subsidies from '{subsidies_sheet}'")
    return subsidies


def read_transport_kpis_combined(
    excel_path: Path, emissions_sheet: str = "Transport emissions", costs_sheet: str = "Transportation costs"
) -> list[TransportKPI]:
    """Read and combine transport emissions and transportation costs into TransportKPI objects."""

    # Read emissions data
    emissions_df = pd.read_excel(excel_path, sheet_name=emissions_sheet)
    emissions_dict: dict[tuple[str, str, str], dict[str, float | str]] = {}

    for _, row in emissions_df.iterrows():
        if pd.isna(row["reporterISO"]) or pd.isna(row["partnerISO"]):
            continue

        key = (str(row["reporterISO"]), str(row["partnerISO"]), normalize_product_name(row["commodity"]))

        try:
            ghg_factor = row["ghg_factor_weighted"]
            if not pd.isna(ghg_factor):
                emissions_dict[key] = {"ghg_factor": float(ghg_factor), "updated_on": str(row["updated_on"])}
        except (ValueError, TypeError) as e:
            logger.warning(f"Error parsing emission row: {e}")
            continue

    # Read transportation costs data
    costs_df = pd.read_excel(excel_path, sheet_name=costs_sheet)
    costs_dict: dict[tuple[str, str, str], dict[str, float | str]] = {}

    for _, row in costs_df.iterrows():
        if pd.isna(row["reporterISO"]) or pd.isna(row["partnerISO"]):
            continue

        key = (str(row["reporterISO"]), str(row["partnerISO"]), normalize_product_name(row["commodity"]))

        try:
            transportation_cost = row["transportation_cost"]
            if not pd.isna(transportation_cost):
                costs_dict[key] = {
                    "transportation_cost": float(transportation_cost),
                    "updated_on": str(row["updated_on"]),
                }
        except (ValueError, TypeError) as e:
            logger.warning(f"Error parsing cost row: {e}")
            continue

    # Combine the data
    transport_kpis = []
    all_keys = set(emissions_dict.keys()) | set(costs_dict.keys())

    for key in all_keys:
        reporter_iso, partner_iso, commodity = key

        # Get data from both sources, with defaults
        emissions_data = emissions_dict.get(key, {})
        costs_data = costs_dict.get(key, {})

        # Use the most recent update date
        emissions_updated = emissions_data.get("updated_on", "")
        costs_updated = costs_data.get("updated_on", "")
        updated_on = str(emissions_updated if emissions_updated else costs_updated)

        # Get values with proper type handling
        ghg_value = emissions_data.get("ghg_factor", 0.0)
        cost_value = costs_data.get("transportation_cost", 0.0)

        transport_kpi = TransportKPI(
            reporter_iso=reporter_iso,
            partner_iso=partner_iso,
            commodity=commodity,
            ghg_factor=float(ghg_value) if ghg_value is not None else 0.0,
            transportation_cost=float(cost_value) if cost_value is not None else 0.0,
            updated_on=updated_on,
        )
        transport_kpis.append(transport_kpi)

    logger.info(f"Read {len(transport_kpis)} combined transport KPI entries")
    logger.info(f"  - {len(emissions_dict)} entries had emission factors")
    logger.info(f"  - {len(costs_dict)} entries had transportation costs")

    return transport_kpis


def read_biomass_availability(excel_path: Path, sheet_name: str = "Biomass availability") -> list[BiomassAvailability]:
    """Read biomass availability from Excel sheet."""
    df = pd.read_excel(excel_path, sheet_name=sheet_name)

    availabilities = []
    # Year columns are integers from 2024 to 2050
    year_columns = [col for col in df.columns if isinstance(col, int) and 2024 <= col <= 2050]

    for _, row in df.iterrows():
        # Skip rows with all NaN values in year columns
        if all(pd.isna(row[year]) for year in year_columns):
            continue

        region = str(row["tiam-ucl_region"]) if pd.notna(row["tiam-ucl_region"]) else ""
        country = str(row["Country"]) if pd.notna(row["Country"]) else None
        metric = str(row["Metric"]) if pd.notna(row["Metric"]) else ""
        scenario = str(row["Scenario"]) if pd.notna(row["Scenario"]) else ""
        unit = str(row["Unit"]) if pd.notna(row["Unit"]) else ""

        # Skip rows with no region
        if not region:
            continue

        for year in year_columns:
            if pd.notna(row[year]):
                try:
                    availability = BiomassAvailability(
                        region=region,
                        country=country,
                        metric=metric,
                        scenario=scenario,
                        unit=unit,
                        year=Year(int(year)),
                        availability=float(row[year]),
                    )
                    availabilities.append(availability)
                except (ValueError, TypeError) as e:
                    logger.warning(f"Error parsing biomass availability for year {year}: {e}")
                    continue

    logger.info(f"Read {len(availabilities)} biomass availability entries")
    return availabilities


def read_co2_storage_availability(excel_path: Path, sheet_name: str = "CO2 storage") -> list[BiomassAvailability]:
    """
    Read CO2 storage availability from Excel sheet as secondary feedstock constraints.

    Uses the same BiomassAvailability data structure as biomass constraints since
    CO2 storage functions similarly as a constraint on a secondary feedstock.

    Args:
        excel_path: Path to the master input Excel file.
        sheet_name: The name of the sheet to read (default: "CO2 storage").

    Returns:
        A list of BiomassAvailability objects representing CO2 storage constraints.
    """
    try:
        df = pd.read_excel(excel_path, sheet_name=sheet_name)
    except ValueError:
        logger.warning(f"Sheet '{sheet_name}' not found in {excel_path}, returning empty list")
        return []

    availabilities = []
    # Year columns are integers from 2024 to 2050
    year_columns = [col for col in df.columns if isinstance(col, int) and 2024 <= col <= 2050]

    for _, row in df.iterrows():
        # Skip rows with all NaN values in year columns
        if all(pd.isna(row[year]) for year in year_columns):
            continue

        # Get country ISO3 code
        country_iso3 = str(row["ISO-3 code"]) if pd.notna(row["ISO-3 code"]) else ""
        # country_name = str(row["Country"]) if pd.notna(row["Country"]) else ""  # Available if needed later
        # metric = str(row["Metric"]) if pd.notna(row["Metric"]) else "CO2 storage"  # Fixed value used below
        scenario = str(row["Scenario"]) if pd.notna(row["Scenario"]) else ""
        unit = str(row["Unit"]) if pd.notna(row["Unit"]) else "tCO2/y"
        # day_of_update = str(row["Day of update"]) if pd.notna(row["Day of update"]) else ""  # Available if needed

        # Skip rows with no country ISO3
        if not country_iso3:
            continue

        for year in year_columns:
            if pd.notna(row[year]):
                try:
                    # Use BiomassAvailability structure with country as ISO3
                    # and region left empty (will be handled by constraint conversion)
                    availability = BiomassAvailability(
                        region="",  # Empty region since we use ISO3 directly
                        country=country_iso3,  # Store ISO3 in country field
                        metric="co2 - stored",  # Use normalized lowercase name to match BOM normalization
                        scenario=scenario,
                        unit=unit,
                        year=Year(int(year)),
                        availability=float(row[year]),
                    )
                    availabilities.append(availability)
                except (ValueError, TypeError) as e:
                    logger.warning(f"Error creating CO2 storage availability for {country_iso3} year {year}: {e}")
                    continue

    logger.info(f"Read {len(availabilities)} CO2 storage availability entries from {sheet_name}")
    return availabilities


def read_fopex(excel_path: Path, sheet_name: str = "Fixed OPEX") -> list[FOPEX]:
    """
    Read Fixed Operating Expenditure (FOPEX) data from Excel sheet and return FOPEX domain objects.

    Expected sheet structure:
    - Country: Full country names
    - ISO 3-letter code: ISO3 country codes
    - Technology columns: BF, BOF, EAF, Other with USD/t values

    Args:
        excel_path: Path to the Excel file containing FOPEX data
        sheet_name: Name of the sheet containing FOPEX data (default: "Fixed OPEX")

    Returns:
        List of FOPEX domain objects
    """
    df = pd.read_excel(excel_path, sheet_name=sheet_name)

    # Check for required columns
    required_cols = ["ISO 3-letter code"]
    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        raise ValueError(f"Missing required columns in FOPEX sheet: {missing_cols}")

    fopex_list = []

    # Identify technology columns (exclude Country and ISO columns)
    exclude_cols = ["Country", "ISO 3-letter code", "Region"]
    technology_cols = [col for col in df.columns if col not in exclude_cols and not col.startswith("Unnamed")]

    for _, row in df.iterrows():
        iso3 = row["ISO 3-letter code"]

        # Skip rows with missing ISO3
        if pd.isna(iso3):
            logger.warning("FOPEX: Skipping row with missing ISO3 code")
            continue

        # Extract technology FOPEX values
        technology_fopex = {}
        for tech_col in technology_cols:
            if pd.notna(row[tech_col]):
                try:
                    # Store with lowercase technology names for consistency
                    technology_fopex[tech_col.lower()] = float(row[tech_col])
                except (ValueError, TypeError) as e:
                    logger.warning(f"FOPEX: Error parsing {tech_col} for {iso3}: {e}")
                    continue

        # Only create FOPEX object if we have at least one technology value
        if technology_fopex:
            fopex = FOPEX(iso3=str(iso3), technology_fopex=technology_fopex)
            fopex_list.append(fopex)
        else:
            logger.warning(f"FOPEX: No valid technology values found for {iso3}")

    logger.info(f"Read {len(fopex_list)} FOPEX entries")
    return fopex_list


def read_technology_emission_factors(
    excel_path: Path, sheet_name: str = "Technology emission factors"
) -> list[TechnologyEmissionFactors]:
    """Read technology emission factors from Excel sheet."""
    df = pd.read_excel(excel_path, sheet_name=sheet_name)

    emission_factors = []
    for _, row in df.iterrows():
        # Handle missing/invalid data
        if pd.isna(row["Business case"]):
            logger.warning("Technology Emission Factors: Skipping row with missing Business case")
            continue

        try:
            business_case_str = str(row["Business case"])

            # Handle special cases without underscore
            if "_" not in business_case_str:
                technology = business_case_str.upper()
            else:
                technology = business_case_str.split("_")[-1].upper()  # extract technology from business case

            # if technology is charcoal, rename to BF_CHARCOAL
            technology = technology.replace("CHARCOAL", "BF_CHARCOAL")

            boundary = str(row["Boundary"]) if pd.notna(row["Boundary"]) else ""
            metallic_charge = (
                normalize_commodity_name(row["Metallic charge"]) if pd.notna(row["Metallic charge"]) else ""
            )
            reductant = normalize_commodity_name(str(row["Reductant"])) if pd.notna(row["Reductant"]) else ""
            direct_ghg_factor = float(row["Direct"]) if pd.notna(row["Direct"]) else 0.0
            direct_with_biomass_ghg_factor = (
                float(row["Direct with biomass"]) if pd.notna(row["Direct with biomass"]) else 0.0
            )
            indirect_ghg_factor = float(row["Indirect"]) if pd.notna(row["Indirect"]) else 0.0

            factor = TechnologyEmissionFactors(
                business_case=str(row["Business case"]),
                technology=technology,
                boundary=boundary,
                metallic_charge=metallic_charge,
                reductant=reductant,
                direct_ghg_factor=direct_ghg_factor,
                direct_with_biomass_ghg_factor=direct_with_biomass_ghg_factor,
                indirect_ghg_factor=indirect_ghg_factor,
            )
            emission_factors.append(factor)
        except (ValueError, TypeError) as e:
            logger.warning(f"Error parsing technology emission factor row: {e}")
            continue

    logger.info(f"Read {len(emission_factors)} technology emission factor entries from '{sheet_name}'")
    return emission_factors


def read_fallback_material_costs(
    excel_path: Path, sheet_name: str = "Fallback material cost"
) -> list[FallbackMaterialCost]:
    """
    Read fallback material costs from an Excel file and return a list of FallbackMaterialCost objects.

    Expected format:
    - Column A: Country (ISO3 codes like ABW, AFG, etc.)
    - Column B: Business case/Technology (like iron_bf)
    - Column C: Metric (like "Unit material cost")
    - Column D: Unit (like "USD/tHM")
    - Columns E onwards: Years (2025, 2026, 2027, etc.) with cost values

    Args:
        excel_path (Path): Path to the Excel file containing fallback material costs.
        sheet_name (str): Name of the sheet to read from.

    Returns:
        list[FallbackMaterialCost]: A list of FallbackMaterialCost objects.
    """
    logger.info(f"Reading fallback material costs from '{excel_path}' sheet '{sheet_name}'")

    try:
        excel_file = pd.ExcelFile(excel_path)
    except Exception as e:
        logger.error("Failed to open Excel file '%s': %s", excel_path, e)
        raise

    if sheet_name not in excel_file.sheet_names:
        raise ValueError(
            f"'{excel_path}' is missing required sheet '{sheet_name}'. "
            "Ensure the latest master workbook (with the fallback BOM definition tab) is used during preparation."
        )

    try:
        df = excel_file.parse(sheet_name=sheet_name)
    except Exception as e:
        logger.error("Failed to read sheet '%s' from '%s': %s", sheet_name, excel_path, e)
        raise

    if df.empty:
        logger.warning(f"Sheet '{sheet_name}' is empty")
        return []

    # Log the columns for debugging
    logger.debug(f"Columns found: {list(df.columns)}")

    fallback_costs = []

    # Expected columns: Country, Business case, Metric, Unit, then year columns
    expected_static_columns = ["Country", "Business case", "Metric", "Unit"]

    # Check if we have the required columns
    missing_columns = [col for col in expected_static_columns if col not in df.columns]
    if missing_columns:
        raise ValueError(f"Missing required columns: {missing_columns}")

    # Find year columns (numeric columns after the static ones)
    year_columns = []
    for col in df.columns:
        if col not in expected_static_columns:
            try:
                # Try to parse as year
                year_int = int(col)
                if 2000 <= year_int <= 2100:  # Reasonable year range
                    year_columns.append(col)
            except (ValueError, TypeError):
                logger.warning(f"Skipping non-year column: {col}")
                continue

    year_columns.sort()  # Sort years in ascending order
    logger.info(f"Found {len(year_columns)} year columns: {year_columns}")

    for index, row in df.iterrows():
        try:
            # Skip rows with missing country data
            if pd.isna(row.get("Country")) or row.get("Country") == "":
                continue

            iso3 = str(row["Country"]).strip().upper()
            business_case_raw = str(row["Business case"]).strip() if pd.notna(row.get("Business case")) else ""
            metric = str(row["Metric"]).strip() if pd.notna(row.get("Metric")) else ""
            unit = str(row["Unit"]).strip() if pd.notna(row.get("Unit")) else ""

            # Extract and normalize technology name using same logic as BOM reading
            if not business_case_raw:
                continue  # Skip if no business case

            # Handle special cases without underscore
            if "_" not in business_case_raw:
                technology = business_case_raw.upper()
            else:
                technology = business_case_raw.split("_")[-1].upper()  # extract technology from business case

            # if technology is charcoal, rename to BF_CHARCOAL
            technology = technology.replace("CHARCOAL", "BF_CHARCOAL")

            # Validate required fields
            if not iso3 or not technology:
                logger.warning(f"Row {index}: Missing required data (ISO3: '{iso3}', Technology: '{technology}')")
                continue

            # Extract costs by year
            costs_by_year = {}
            for year_col in year_columns:
                cost_value = row.get(year_col)
                if pd.notna(cost_value):
                    try:
                        cost_float = float(cost_value)
                        year_obj = Year(int(year_col))
                        costs_by_year[year_obj] = cost_float
                    except (ValueError, TypeError) as e:
                        logger.warning(f"Row {index}, Year {year_col}: Invalid cost value '{cost_value}': {e}")
                        continue

            # Only create object if we have at least one valid cost entry
            if costs_by_year:
                fallback_cost = FallbackMaterialCost(
                    iso3=iso3, technology=technology, metric=metric, unit=unit, costs_by_year=costs_by_year
                )
                fallback_costs.append(fallback_cost)
            else:
                logger.warning(f"Row {index}: No valid cost data found for {iso3}-{technology}")

        except Exception as e:
            logger.warning(f"Error processing row {index}: {e}")
            continue

    logger.info(f"Read {len(fallback_costs)} fallback material cost entries from '{sheet_name}'")
    return fallback_costs


def read_fallback_bom_definitions(excel_path: Path, sheet_name: str = "Fallback BOM definition") -> dict[str, str]:
    """
    Read fallback BOM definitions from Excel sheet and return a dictionary mapping technologies to their default metallic charges.

    Expected format (first 20 rows):
    - Business case column: Technology names (e.g., iron_dri, iron_dri+eaf, etc.)
    - Metallic charge column: Default metallic charges (e.g., IO_high, IO_low, Pig iron, etc.)

    Args:
        excel_path (Path): Path to the Excel file containing fallback BOM definitions.
        sheet_name (str): Name of the sheet to read from.

    Returns:
        dict[str, str]: A dictionary mapping normalized technology names to metallic charges.
    """
    logger.info(f"Reading fallback BOM definitions from '{excel_path}' sheet '{sheet_name}'")

    try:
        df = pd.read_excel(excel_path, sheet_name=sheet_name)
    except Exception as e:
        logger.error(f"Failed to read Excel file: {e}")
        raise

    if df.empty:
        logger.warning(f"Sheet '{sheet_name}' is empty")
        return {}

    # Log the columns for debugging
    logger.debug(f"Columns found: {list(df.columns)}")

    # Check for required columns
    expected_columns = ["Business case", "Metallic charge"]
    missing_columns = [col for col in expected_columns if col not in df.columns]
    if missing_columns:
        raise ValueError(f"Missing required columns: {missing_columns}")

    default_metallic_charge_per_technology = {}

    # Read only the first 20 rows as specified
    rows_to_read = min(20, len(df))
    for index in range(rows_to_read):
        try:
            row = df.iloc[index]

            # Skip rows with missing data
            if pd.isna(row.get("Business case")) or pd.isna(row.get("Metallic charge")):
                continue

            business_case_raw = str(row["Business case"]).strip()
            metallic_charge_raw = str(row["Metallic charge"]).strip()

            if not business_case_raw or not metallic_charge_raw:
                continue

            # Extract and normalize technology name using same logic as BOM reading
            if "_" not in business_case_raw:
                technology = business_case_raw.upper()
            else:
                technology = business_case_raw.split("_")[-1].upper()  # extract technology from business case

            # if technology is charcoal, rename to BF_CHARCOAL
            if "BF_CHARCOAL" not in technology:
                technology = technology.replace("CHARCOAL", "BF_CHARCOAL")

            # Normalize metallic charge using the same normalization as in BOM reading
            metallic_charge = normalize_commodity_name(metallic_charge_raw)

            default_metallic_charge_per_technology[technology] = metallic_charge
            logger.debug(f"Mapped {business_case_raw} -> {technology} -> {metallic_charge}")

        except Exception as e:
            logger.warning(f"Error processing row {index}: {e}")
            continue

    logger.info(
        f"Read {len(default_metallic_charge_per_technology)} default metallic charge mappings from '{sheet_name}'"
    )
    return default_metallic_charge_per_technology
