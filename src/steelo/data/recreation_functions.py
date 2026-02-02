"""Data recreation functions - pure business logic, no CLI concerns.

This module contains functions for recreating JSON repositories from various data sources.
These functions were moved from cli.py to follow clean architecture principles.
"""

import json
import shutil
from pathlib import Path
from typing import Any
from collections import defaultdict

from rich.console import Console

from ..domain.models import Plant, PlantGroup, InputCosts
from ..domain.constants import PLANT_LIFETIME, T_TO_KG, Year
from ..adapters.dataprocessing.excel_reader import (
    read_biomass_availability,
    read_co2_storage_availability,
    read_demand_centers,
    read_scrap_as_suppliers,
    read_mines_as_suppliers,
    read_dynamic_business_cases,
    read_tariffs,
    read_carbon_costs,
    read_regional_input_prices_from_master_excel,
    read_regional_emissivities,
    read_capex_and_learning_rate_data,
    read_cost_of_capital,
    read_legal_process_connectors,
    read_country_mappings,
    read_hydrogen_efficiency,
    read_hydrogen_capex_opex,
    read_subsidies,
    read_transport_kpis_combined,
    read_technology_emission_factors,
    read_fopex,
    read_carbon_border_mechanisms,
    read_fallback_material_costs,
)
from ..adapters.repositories.json_repository import (
    BiomassAvailabilityJsonRepository,
    PlantJsonRepository,
    DemandCenterJsonRepository,
    SupplierJsonRepository,
    PlantGroupJsonRepository,
    TariffJsonRepository,
    CarbonCostsJsonRepository,
    PrimaryFeedstockJsonRepository,
    InputCostsJsonRepository,
    RegionEmissivityJsonRepository,
    CapexJsonRepository,
    CostOfCapitalJsonRepository,
    LegalProcessConnectorJsonRepository,
    HydrogenEfficiencyJsonRepository,
    HydrogenCapexOpexJsonRepository,
    SubsidyJsonRepository,
    TransportKPIJsonRepository,
    TechnologyEmissionFactorsJsonRepository,
    FOPEXRepository,
    CarbonBorderMechanismJsonRepository,
    FallbackMaterialCostJsonRepository,
)
from ..domain.calculate_costs import calculate_lcoh_from_electricity_country_level

console = Console()


def recreate_country_mappings_data(
    *,
    json_path: Path,
    master_excel_path: Path | None = None,
) -> None:
    """Recreate country_mappings.json from master Excel file."""
    console.print(f"[blue]Creating country mappings[/blue]: {json_path}")

    if not master_excel_path or not master_excel_path.exists():
        raise ValueError("Master Excel file is required to create country_mappings.json")

    # Import here to avoid circular imports
    from ..adapters.repositories.json_repository import CountryMappingInDb

    try:
        # Read mappings from master Excel
        mappings = read_country_mappings(master_excel_path, "Country mapping")

        # Convert to JSON format
        mappings_data = []
        for mapping in mappings:
            mapping_dict = {
                "Country": mapping.country,
                "ISO 2-letter code": mapping.iso2,
                "ISO 3-letter code": mapping.iso3,
                "irena_name": mapping.irena_name,
                "region_for_outputs": mapping.region_for_outputs,
                "ssp_region": mapping.ssp_region,
                "gem_country": mapping.gem_country,
                "ws_region": mapping.ws_region,
                "tiam-ucl_region": mapping.tiam_ucl_region,
                "eu_or_non_eu": mapping.eu_region,
                # Add new regional boolean fields
                "EU": mapping.EU,
                "EFTA_EUCJ": mapping.EFTA_EUCJ,
                "OECD": mapping.OECD,
                "NAFTA": mapping.NAFTA,
                "Mercosur": mapping.Mercosur,
                "ASEAN": mapping.ASEAN,
                "RCEP": mapping.RCEP,
            }
            # Validate with Pydantic model
            CountryMappingInDb(**mapping_dict)
            mappings_data.append(mapping_dict)

        # Sort by ISO3 for consistent output
        mappings_data.sort(key=lambda x: x["ISO 3-letter code"] or "")

        # Write to JSON file
        json_path.parent.mkdir(parents=True, exist_ok=True)
        with open(json_path, "w") as f:
            json.dump(mappings_data, f, indent=2)

        console.print(f"[green]✓[/green] Created {len(mappings_data)} country mappings")

    except Exception as e:
        console.print(f"[red]Error creating country mappings: {e}[/red]")
        raise


def recreate_plants_data(
    *,
    plants: list[Plant],
    json_path: Path,
    canonical_metadata: dict[str, Any] | None = None,
    data_reference_year: int | None = None,
    master_excel_path: Path | None = None,
    master_excel_version: str | None = None,
) -> PlantJsonRepository:
    """
    Write a list of Plant objects to a JSON file, optionally with metadata sidecar.

    This function has been simplified to only handle writing pre-processed plants to JSON.
    All data enrichment (dynamic business cases, energy prices, etc.) should be done
    by MasterExcelReader.read_plants() before calling this function.

    CRITICAL: canonical_metadata must have keys matching the final furnace_group_ids
    in the plants data, AFTER aggregation has been applied.

    Args:
        plants: List of fully-enriched Plant domain objects
        json_path: Path where to write the plants.json file
        canonical_metadata: Optional dict mapping furnace_group_id to FurnaceGroupMetadata.
                          Keys must match final post-aggregation IDs.
        data_reference_year: Year when age calculations are anchored (required if metadata provided)
        master_excel_path: Path to source Excel file (required if metadata provided)
        master_excel_version: Version string of master Excel (required if metadata provided)

    Returns:
        PlantJsonRepository instance containing the written plants

    Raises:
        ValueError: If metadata/plants ID mismatch detected
    """
    console.print(f"[blue]Writing {len(plants)} plants to[/blue]: {json_path}")

    # Ensure output directory exists
    json_path.parent.mkdir(parents=True, exist_ok=True)

    # If metadata provided, validate and write sidecar
    if canonical_metadata is not None:
        if data_reference_year is None or master_excel_path is None or master_excel_version is None:
            raise ValueError(
                "data_reference_year, master_excel_path, and master_excel_version are required when metadata is provided"
            )

        # Import here to avoid circular dependency
        from ..adapters.dataprocessing.plant_metadata import (
            create_metadata_dict,
            write_metadata_sidecar,
            validate_metadata_coverage,
        )

        # Validate metadata matches plants before writing
        plant_fg_ids = {fg.furnace_group_id for plant in plants for fg in plant.furnace_groups}
        metadata_fg_ids = set(canonical_metadata.keys())

        try:
            validate_metadata_coverage(plant_fg_ids, metadata_fg_ids)
        except ValueError as e:
            # Re-raise with more context
            raise ValueError(f"Metadata validation failed before writing: {e}") from e

        # Create and write metadata sidecar
        metadata_dict = create_metadata_dict(
            furnace_group_metadata=canonical_metadata,
            plant_lifetime_used=PLANT_LIFETIME,
            data_reference_year=data_reference_year,
            master_excel_path=master_excel_path,
            master_excel_version=master_excel_version,
        )

        write_metadata_sidecar(metadata_dict, json_path.parent)

        console.print(f"[green]✓[/green] Written metadata for {len(canonical_metadata)} furnace groups")

    # Write plants to repository
    write_repository = PlantJsonRepository(json_path, PLANT_LIFETIME)
    write_repository.add_list(plants)

    console.print(f"[green]✓[/green] Successfully wrote {len(plants)} plants to {json_path}")

    return write_repository


def recreate_demand_center_data(
    json_path: Path,
    demand_excel_path: Path,
    demand_sheet_name: str,
    gravity_distances_path: Path | None = None,
    location_csv: Path | None = None,
) -> DemandCenterJsonRepository:
    """
    Recreate the JSON sample demand center data from the current CSV file.

    Note: gravity_distances_path and location_csv must be provided by the caller.
    """
    if not gravity_distances_path:
        raise ValueError("gravity_distances_path parameter is required")
    if not location_csv:
        raise ValueError("location_csv parameter is required")

    demand_centers = read_demand_centers(
        gravity_distances_path=gravity_distances_path,
        demand_excel_path=demand_excel_path,
        demand_sheet_name=demand_sheet_name,
        location_csv=location_csv,
    )

    write_repository = DemandCenterJsonRepository(json_path)
    write_repository.add_list(demand_centers)
    console.print(f"[green]Sample demand center data written to[/green]: {json_path}")

    return write_repository


def recreate_mines_and_scrap_as_suppliers_data(
    json_path: Path,
    master_excel_path: str,
    scrap_sheet_name: str,
    mines_sheet_name: str = "Iron ore mines",
    location_csv: Path | None = None,
    gravity_distances_pkl_path: Path | None = None,
) -> SupplierJsonRepository:
    """
    Recreate the JSON sample mines/scrap suppliers data from the current Excel file.

    Note: location_csv is now a required parameter.
    """
    if location_csv is None:
        raise ValueError("location_csv parameter is required")

    location_path = str(location_csv)

    # Use provided gravity_distances_pkl_path if available
    gravity_path = gravity_distances_pkl_path if gravity_distances_pkl_path is not None else None

    scrap_suppliers = read_scrap_as_suppliers(
        scrap_excel_path=str(master_excel_path),
        scrap_sheet_name=scrap_sheet_name,
        location_csv=location_path,
        gravity_distances_pkl_path=gravity_path,
    )
    console.print(f"[blue]  Read {len(scrap_suppliers)} scrap suppliers[/blue]")

    mine_suppliers = read_mines_as_suppliers(
        mine_data_excel_path=str(master_excel_path),
        mine_data_sheet_name=mines_sheet_name,
        location_csv=location_path,
    )
    console.print(f"[blue]  Read {len(mine_suppliers)} iron ore mine suppliers[/blue]")

    suppliers = scrap_suppliers + mine_suppliers
    write_repository = SupplierJsonRepository(json_path)
    write_repository.add_list(suppliers)
    console.print(f"[green]Sample mine/scrap suppliers data written to[/green]: {json_path}")
    console.print(
        f"[green]  Total suppliers: {len(suppliers)} ({len(scrap_suppliers)} scrap + {len(mine_suppliers)} mines)[/green]"
    )
    return write_repository


def recreate_tarrifs_data(
    json_path: Path,
    tariff_excel_path: str,
    tariff_sheet_name: str = "Tariffs",
    country_mapping_sheet_name: str = "Country mapping",
) -> TariffJsonRepository:
    """
    Recreate the JSON sample tariffs data from the current Excel file.

    Args:
        json_path: Path to output JSON file.
        tariff_excel_path: Path to Excel file containing tariff data.
        tariff_sheet_name: Name of the tariff sheet in the Excel file.
        country_mapping_sheet_name: Name of the country mapping sheet in the Excel file.

    Returns:
        TariffJsonRepository containing the loaded tariffs.
    """
    # Load country mappings from the Excel file
    country_mappings = read_country_mappings(
        excel_path=Path(tariff_excel_path),
        sheet_name=country_mapping_sheet_name,
    )

    tariffs = read_tariffs(
        tariff_excel_path=tariff_excel_path,
        tariff_sheet_name=tariff_sheet_name,
        country_mappings=country_mappings,
    )
    # Start by deleting the existing file if it exists
    if json_path.exists():
        console.print(f"[blue]Deleting existing tariffs JSON file[/blue]: {json_path}")
        json_path.unlink()
    write_repository = TariffJsonRepository(json_path)
    write_repository.add_list(tariffs)

    console.print(f"[green]Sample tariffs data written to[/green]: {json_path}")
    return write_repository


def recreate_subsidy_data(
    json_path: Path,
    excel_path: Path,
    subsidies_sheet_name: str = "Subsidies",
    country_mapping_sheet_name: str = "Country mapping",
) -> SubsidyJsonRepository:
    """
    Recreate the JSON sample subsidy data from the current Excel file.
    """
    console.print(f"[blue]Reading subsidy data from Excel[/blue]: {excel_path}")

    subsidy_data = read_subsidies(
        excel_path, subsidies_sheet=subsidies_sheet_name, country_mapping_sheet=country_mapping_sheet_name
    )

    repo = SubsidyJsonRepository(json_path)
    repo.add_list(subsidy_data)
    console.print(f"[green]Writing subsidy data to[/green]: {json_path}")

    return repo


def recreate_carbon_costs_data(carbon_cost_json_path: Path, excel_path: Path) -> CarbonCostsJsonRepository:
    """
    Recreate the JSON carbon‐costs data from the current Excel file.
    If a JSON file already exists at json_path, it is deleted first.
    Returns the repository instance pointing to the newly written JSON.
    """
    # 1) Read domain objects from Excel:
    series_list = read_carbon_costs(excel_path)

    console.print(f"[green]Writing carbon_costs to[/green]: {carbon_cost_json_path}")

    # 3) Instantiate repository and write the new list:
    repo = CarbonCostsJsonRepository(carbon_cost_json_path)
    repo.add_list(series_list)
    return repo


def recreate_region_emissivity_data(
    region_emissivity_json_path: Path,
    excel_path: Path,
    grid_emissivity_sheet: str = "Power grid emissivity",
    gas_coke_emissivity_sheet: str = "Met coal & gas emissions",
) -> RegionEmissivityJsonRepository:
    """
    Recreate the JSON grid emissivity data from the current Excel file.
    """
    console.print(f"[blue]Reading grid emissivity data from Excel[/blue]: {excel_path} sheet Power grid emissivity")

    grid_emissivity_data = read_regional_emissivities(
        excel_path=excel_path, grid_sheet_name=grid_emissivity_sheet, gas_sheet_name=gas_coke_emissivity_sheet
    )

    repo = RegionEmissivityJsonRepository(region_emissivity_json_path)
    repo.add_list(grid_emissivity_data)
    console.print(f"[green]Writing carbon_costs to[/green]: {region_emissivity_json_path}")

    return repo


def recreate_input_costs_data(
    input_costs_json_path: Path,
    excel_path: Path,
    input_costs_sheet: str = "Input costs",
) -> InputCostsJsonRepository:
    """
    Recreate the JSON input‐costs file from a list of InputCosts domain objects.
    If a JSON file already exists at json_path, it is deleted first.
    Returns the repository instance pointing to the newly written JSON.
    """
    ic_list = read_regional_input_prices_from_master_excel(
        excel_path=excel_path,
        input_costs_sheet=input_costs_sheet,
    )

    # Augment input costs with hydrogen prices derived from electricity + CAPEX/OPEX inputs.
    hydrogen_efficiency_entries = read_hydrogen_efficiency(excel_path)
    hydrogen_capex_entries = read_hydrogen_capex_opex(excel_path)

    if hydrogen_efficiency_entries and hydrogen_capex_entries:
        hydrogen_efficiency_by_year = {entry.year: entry.efficiency for entry in hydrogen_efficiency_entries}
        hydrogen_capex_by_country = {entry.country_code: entry.values for entry in hydrogen_capex_entries}

        # Index InputCosts for quick lookup and capture electricity price per year
        ic_index: dict[tuple[str, Year], InputCosts] = {}
        electricity_by_year: dict[Year, dict[str, float]] = defaultdict(dict)
        for ic in ic_list:
            year = Year(int(ic.year))
            ic_index[(ic.iso3, year)] = ic
            electricity_price = ic.costs.get("electricity")
            if electricity_price is not None:
                electricity_by_year[year][ic.iso3] = electricity_price

        for year, electricity_by_country in electricity_by_year.items():
            # Filter to countries where we have CAPEX/OPEX values for the current year
            applicable_electricity = {
                iso3: price
                for iso3, price in electricity_by_country.items()
                if iso3 in hydrogen_capex_by_country and year in hydrogen_capex_by_country[iso3]
            }
            if not applicable_electricity:
                continue

            try:
                lcoh_by_country = calculate_lcoh_from_electricity_country_level(
                    electricity_by_country=applicable_electricity,
                    hydrogen_efficiency=hydrogen_efficiency_by_year,
                    hydrogen_capex_opex=hydrogen_capex_by_country,
                    year=year,
                )
            except ValueError as exc:
                console.print(
                    f"[yellow]Skipping hydrogen price derivation for {int(year)} due to data gap: {exc}[/yellow]"
                )
                continue

            for iso3, usd_per_kg in lcoh_by_country.items():
                existing_ic: InputCosts | None = ic_index.get((iso3, year))
                if existing_ic is None:
                    continue
                # Convert USD/kg to USD/t to match other BOM-aligned commodities.
                existing_ic.costs["hydrogen"] = usd_per_kg * T_TO_KG

    repo = InputCostsJsonRepository(input_costs_json_path)
    repo.add_list(ic_list)
    console.print(f"[blue]Written input costs to[/blue]: {input_costs_json_path}")
    return repo


def recreate_plant_groups_data(*, plants_json_path: Path, plant_groups_json_path: Path) -> PlantGroupJsonRepository:
    """
    Recreate the JSON sample plant group data from the current plants JSON file.
    """
    console.print(f"[blue]Reading sample plants for grouping[/blue]: {plants_json_path}")
    plant_repo = PlantJsonRepository(plants_json_path, PLANT_LIFETIME)
    plants_list = plant_repo.list()

    console.print("[blue]Grouping plants into plant groups[/blue]")
    pgs: dict[str, list[Plant]] = {}
    for plant in plants_list:
        parent_id = plant.ultimate_plant_group
        pgs.setdefault(parent_id, []).append(plant)

    console.print(f"[blue]Writing plant groups to[/blue]: {plant_groups_json_path}")
    group_repo = PlantGroupJsonRepository(plant_groups_json_path, PLANT_LIFETIME)
    group_repo.add_list([PlantGroup(plant_group_id=gid, plants=pgs[gid]) for gid in sorted(pgs)])
    console.print(f"[green]Sample plant groups data written to[/green]: {plant_groups_json_path}")
    return group_repo


def recreate_primary_feedstock_data(
    primary_feedstock_json_path: Path, excel_path: Path, bom_excel_sheet: str = "Bill of Materials"
) -> PrimaryFeedstockJsonRepository:
    """
    Recreate the JSON PrimaryFeedstock data.
    If json_path already exists, it is deleted first.
    If excel_path is provided, read from that Excel to build domain objects; otherwise,
    you must have already constructed a list of PrimaryFeedstock elsewhere.
    """
    # 2) Instantiate repository:
    repo = PrimaryFeedstockJsonRepository(primary_feedstock_json_path)

    # 3) If an Excel path is provided, read from it and write those entries:
    pf_dict = read_dynamic_business_cases(str(excel_path), excel_sheet=bom_excel_sheet)
    # Flatten the dict to get a single list of PrimaryFeedstock objects
    pf_list = [pf for pf_list in pf_dict.values() for pf in pf_list]
    repo.add_list(pf_list)

    return repo


def recreate_capex_data(capex_json_path: Path, master_excel_path: Path | None = None) -> CapexJsonRepository:
    """Recreate capex data from master Excel file."""
    if not master_excel_path:
        raise ValueError("master_excel_path is required for recreate_capex_data")

    # 2) Instantiate repository:
    repo = CapexJsonRepository(capex_json_path)

    console.print(f"[green]Writing capex data to[/green]: {capex_json_path}")
    capex_data_list = read_capex_and_learning_rate_data(str(master_excel_path))
    repo.add_list(capex_data_list)

    return repo


def recreate_cost_of_capital_data(
    cost_of_capital_json_path: Path, master_excel_path: Path | None = None
) -> CostOfCapitalJsonRepository:
    """Recreate cost of capital data from master Excel file."""
    if not master_excel_path:
        raise ValueError("master_excel_path is required for recreate_cost_of_capital_data")

    # 2) Instantiate repository:
    repo = CostOfCapitalJsonRepository(cost_of_capital_json_path)

    # 3) If an Excel path is provided, read from it and write those entries:
    console.print(f"[green]Writing cost_of_capital data to[/green]: {cost_of_capital_json_path}")
    costs_of_capital_list = read_cost_of_capital(str(master_excel_path))
    repo.add_list(costs_of_capital_list)

    return repo


def recreate_tech_switches_data(tech_switches_csv_path: Path, master_excel_path: Path) -> Path:
    """
    Extract tech switches from Master Excel file and save as CSV.

    Args:
        tech_switches_csv_path: Path where the CSV should be saved
        master_excel_path: Path to master Excel file

    Returns:
        Path to the created CSV file
    """
    from steelo.adapters.dataprocessing.master_excel_reader import MasterExcelReader

    with MasterExcelReader(master_excel_path) as reader:
        result = reader.read_tech_switches()

        if result.success:
            # Copy the extracted CSV to the desired location
            shutil.copy2(result.file_path, tech_switches_csv_path)
            return tech_switches_csv_path
        else:
            # Raise error with details
            error_msgs = [e.message for e in result.errors] if result.errors else ["Unknown error"]
            raise ValueError(f"Failed to extract tech switches from master Excel: {'; '.join(error_msgs)}")


def recreate_legal_process_connectors_data(
    legal_process_connectors_json_path: Path, excel_path: Path, sheet_name: str = "Legal Process connectors"
) -> LegalProcessConnectorJsonRepository:
    """Recreate legal process connectors data from master Excel file."""
    console.print(f"[blue]Reading legal process connectors from Excel[/blue]: {excel_path}")

    # Read data from Excel
    legal_process_connectors = read_legal_process_connectors(excel_path, sheet_name)

    # Create repository and save
    repo = LegalProcessConnectorJsonRepository(legal_process_connectors_json_path)
    repo.add_list(legal_process_connectors)

    console.print(
        f"[green]Created {legal_process_connectors_json_path.name} with "
        f"{len(legal_process_connectors)} legal process connectors entries[/green]"
    )
    return repo


def recreate_hydrogen_efficiency_data(
    hydrogen_efficiency_json_path: Path, excel_path: Path, sheet_name: str = "Hydrogen efficiency"
) -> HydrogenEfficiencyJsonRepository:
    """
    Recreate hydrogen efficiency data JSON from master Excel.

    Args:
        hydrogen_efficiency_json_path: Output path for JSON file
        excel_path: Path to master Excel file
        sheet_name: Name of the sheet containing hydrogen efficiency data

    Returns:
        Repository instance with the loaded data
    """
    console.print(f"[blue]Reading hydrogen efficiency data from Excel[/blue]: {excel_path}")

    # Read data from Excel
    hydrogen_efficiency = read_hydrogen_efficiency(excel_path, sheet_name)

    # Create repository and save
    repo = HydrogenEfficiencyJsonRepository(hydrogen_efficiency_json_path)
    repo.add_list(hydrogen_efficiency)

    console.print(
        f"[green]Created {hydrogen_efficiency_json_path.name} with "
        f"{len(hydrogen_efficiency)} hydrogen efficiency entries[/green]"
    )
    return repo


def recreate_hydrogen_capex_opex_data(
    hydrogen_capex_opex_json_path: Path, excel_path: Path, sheet_name: str = "Hydrogen CAPEX_OPEX component"
) -> HydrogenCapexOpexJsonRepository:
    """
    Recreate hydrogen CAPEX/OPEX data JSON from master Excel.

    Args:
        hydrogen_capex_opex_json_path: Output path for JSON file
        excel_path: Path to master Excel file
        sheet_name: Name of the sheet containing hydrogen CAPEX/OPEX data

    Returns:
        Repository instance with the loaded data
    """
    console.print(f"[blue]Reading hydrogen CAPEX/OPEX data from Excel[/blue]: {excel_path}")

    # Read data from Excel
    hydrogen_capex_opex = read_hydrogen_capex_opex(excel_path, sheet_name)

    # Create repository and save
    repo = HydrogenCapexOpexJsonRepository(hydrogen_capex_opex_json_path)
    repo.add_list(hydrogen_capex_opex)

    console.print(
        f"[green]Created {hydrogen_capex_opex_json_path.name} with "
        f"{len(hydrogen_capex_opex)} hydrogen CAPEX/OPEX entries[/green]"
    )
    return repo


def recreate_transport_emissions_data(
    transport_emissions_json_path: Path, excel_path: Path, sheet_name: str = "Transport emissions"
) -> TransportKPIJsonRepository:
    """Recreate transport emissions JSON from master Excel, combining emissions and transportation costs."""
    console.print(f"[blue]Reading transport KPIs (emissions + costs) from Excel[/blue]: {excel_path}")

    # Read combined data from both emissions and transportation costs sheets
    transport_kpis = read_transport_kpis_combined(
        excel_path, emissions_sheet=sheet_name, costs_sheet="Transportation costs"
    )

    # Create repository and save
    repo = TransportKPIJsonRepository(transport_emissions_json_path)
    repo.add_list(transport_kpis)

    console.print(
        f"[green]Created {transport_emissions_json_path.name} with {len(transport_kpis)} combined transport KPI entries[/green]"
    )
    return repo


def recreate_biomass_availability_data(
    biomass_availability_json_path: Path, excel_path: Path, sheet_name: str = "Biomass availability"
) -> BiomassAvailabilityJsonRepository:
    """Recreate biomass availability JSON from master Excel (includes both biomass and CO2 storage)."""
    console.print(f"[blue]Reading biomass availability from Excel[/blue]: {excel_path}")

    # Read biomass data from Excel
    biomass_availabilities = read_biomass_availability(excel_path, sheet_name)

    # Read CO2 storage data from Excel
    console.print(f"[blue]Reading CO2 storage availability from Excel[/blue]: {excel_path}")
    co2_availabilities = read_co2_storage_availability(excel_path, "CO2 storage")

    # Combine both types of constraints
    all_availabilities = biomass_availabilities + co2_availabilities

    # Create repository and save
    repo = BiomassAvailabilityJsonRepository(biomass_availability_json_path)
    repo.add_list(all_availabilities)

    console.print(
        f"[green]Created {biomass_availability_json_path.name} with "
        f"{len(biomass_availabilities)} biomass and {len(co2_availabilities)} CO2 storage entries "
        f"(total: {len(all_availabilities)} entries)[/green]"
    )
    return repo


def recreate_technology_emission_factors_data(
    technology_emission_factors_json_path: Path, excel_path: Path, sheet_name: str = "Technology emission factors"
) -> TechnologyEmissionFactorsJsonRepository:
    """
    Recreate technology emission factors JSON from master Excel.

    Args:
        technology_emission_factors_json_path: Output path for JSON file
        excel_path: Path to master Excel file
        sheet_name: Name of the sheet containing technology emission factors data

    Returns:
        Repository instance with the loaded data
    """
    console.print(f"[blue]Reading technology emission factors data from Excel[/blue]: {excel_path}")

    # Read data from Excel
    technology_emission_factors = read_technology_emission_factors(excel_path, sheet_name)

    # Create repository and save
    repo = TechnologyEmissionFactorsJsonRepository(technology_emission_factors_json_path)
    repo.add_list(technology_emission_factors)

    console.print(
        f"[green]Created {technology_emission_factors_json_path.name} with "
        f"{len(technology_emission_factors)} technology emission factors entries[/green]"
    )
    return repo


def recreate_fopex_data(fopex_json_path: Path, excel_path: Path, sheet_name: str = "Fixed OPEX") -> FOPEXRepository:
    """
    Recreate FOPEX JSON from master Excel.

    Args:
        fopex_json_path: Output path for JSON file
        excel_path: Path to master Excel file
        sheet_name: Name of the sheet containing FOPEX data

    Returns:
        Repository instance with the loaded data
    """
    console.print(f"[blue]Reading FOPEX data from Excel[/blue]: {excel_path}")

    # Read data from Excel
    fopex_list = read_fopex(excel_path, sheet_name)

    # Create repository and save
    repo = FOPEXRepository(fopex_json_path)
    repo.add_list(fopex_list)

    console.print(f"[green]Created {fopex_json_path.name} with {len(fopex_list)} FOPEX entries[/green]")
    return repo


def recreate_carbon_border_mechanisms_data(
    *,
    json_path: Path,
    master_excel_path: Path,
    sheet_name: str = "CBAM",
) -> CarbonBorderMechanismJsonRepository:
    """Recreate carbon_border_mechanisms.json from master Excel file.

    Args:
        json_path: Path where the JSON file will be created
        master_excel_path: Path to the master Excel file
        sheet_name: Name of the sheet containing CBAM data (default: "CBAM")

    Returns:
        Repository instance with the loaded data
    """
    console.print(f"[blue]Creating carbon border mechanisms[/blue]: {json_path}")

    if not master_excel_path or not master_excel_path.exists():
        raise ValueError("Master Excel file is required to create carbon_border_mechanisms.json")

    # Read mechanisms from master Excel
    mechanisms = read_carbon_border_mechanisms(master_excel_path, sheet_name)

    # Create repository and save
    repo = CarbonBorderMechanismJsonRepository(json_path)
    repo.add_list(mechanisms)

    # Write to file - use the mechanisms that were added
    from ..adapters.repositories.json_repository import CarbonBorderMechanismInDb

    mechanisms_in_db = [CarbonBorderMechanismInDb.from_domain(m) for m in repo._mechanisms_to_write]
    repo._write_models(mechanisms_in_db)

    console.print(f"[green]Created {json_path.name} with {len(mechanisms)} carbon border mechanisms[/green]")
    return repo


def recreate_fallback_material_costs(
    fallback_material_costs_json_path: Path, excel_path: Path, sheet_name: str = "Fallback material cost"
) -> FallbackMaterialCostJsonRepository:
    """Recreate fallback material costs JSON from master Excel."""
    console.print(f"[blue]Reading fallback material costs from Excel[/blue]: {excel_path}")

    try:
        # Read data from Excel
        fallback_costs = read_fallback_material_costs(excel_path, sheet_name)
    except Exception as e:
        console.print(f"[yellow]Sheet '{sheet_name}' not found or error reading: {e}[/yellow]")
        console.print("[yellow]Creating empty fallback material costs file[/yellow]")
        fallback_costs = []

    # Create repository and save
    repo = FallbackMaterialCostJsonRepository(fallback_material_costs_json_path)
    repo.add_list(fallback_costs)

    console.print(
        f"[green]Created {fallback_material_costs_json_path.name} with "
        f"{len(fallback_costs)} fallback material cost entries[/green]"
    )
    return repo
