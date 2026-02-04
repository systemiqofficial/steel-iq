"""Recreate sample data from downloaded packages.

This module provides functions to recreate JSON repositories from
Excel/CSV files downloaded from S3, similar to recreate_sample_data
but using dynamic paths from downloaded packages.
"""

import logging
import shutil
from pathlib import Path
from typing import Any, Callable

from rich.console import Console

from .manager import DataManager
from .recreation_config import RecreationConfig, RecreationManager, FILE_RECREATION_SPECS


from .recreation_functions import (
    recreate_capex_data,
    recreate_carbon_border_mechanisms_data,
    recreate_carbon_costs_data,
    recreate_cost_of_capital_data,
    recreate_country_mappings_data,
    recreate_demand_center_data,
    recreate_region_emissivity_data,
    recreate_input_costs_data,
    recreate_legal_process_connectors_data,
    recreate_mines_and_scrap_as_suppliers_data,
    recreate_plant_groups_data,
    recreate_plants_data,
    recreate_primary_feedstock_data,
    recreate_tarrifs_data,
    recreate_tech_switches_data,
    recreate_hydrogen_efficiency_data,
    recreate_hydrogen_capex_opex_data,
    recreate_subsidy_data,
    recreate_transport_emissions_data,
    recreate_biomass_availability_data,
    recreate_technology_emission_factors_data,
    recreate_fopex_data,
    recreate_fallback_material_costs,
)


logger = logging.getLogger(__name__)
console = Console()


class DataRecreator:
    """Recreates JSON repositories from downloaded data packages."""

    def __init__(self, data_manager: DataManager | None = None):
        """Initialize the data recreator.

        Args:
            data_manager: Data manager instance. If not provided, creates a new one.
        """
        self.data_manager = data_manager or DataManager()
        self.recreation_manager: RecreationManager | None = None

    def recreate_from_package(
        self,
        package_name: str,
        output_dir: Path,
        force_download: bool = False,
        master_excel_path: Path | None = None,
        track_timing: bool = False,
    ) -> dict[str, Path]:
        """Recreate JSON repositories from a downloaded package.

        Args:
            package_name: Name of the data package to use
            output_dir: Directory to write JSON repositories
            force_download: Force re-download of package
            master_excel_path: Optional path to master Excel file for additional data
            track_timing: If True, track and display timing for each file creation

        Returns:
            Dictionary mapping repository types to their output paths
        """
        # Ensure package is downloaded
        package = self.data_manager.manifest.get_package(package_name)
        if package and (force_download or not self.data_manager._is_package_cached(package)):
            console.print(f"[blue]Downloading package: {package_name}[/blue]")
            self.data_manager.download_package(package_name, force=force_download)

        # Get package directory
        package_dir = self.data_manager.get_package_path(package_name)
        console.print(f"[blue]Using data from: {package_dir}[/blue]")

        # Create output directory
        output_dir.mkdir(parents=True, exist_ok=True)

        # Copy critical files that are needed before processing starts
        # These files are needed by the recreation functions themselves
        critical_files = {
            "gravity_distances_dict.pkl": package_dir / "gravity_distances_dict.pkl",
            "geolocator_raster.csv": package_dir / "geolocator_raster.csv",
        }

        for filename, source_path in critical_files.items():
            if source_path.exists():
                shutil.copy2(source_path, output_dir / filename)
                console.print(f"[blue]Pre-copied critical file: {filename}[/blue]")

        # Map of expected files in the package
        file_mapping = {
            "plants_csv": "steel_plants_input_data_2025-03.csv",
            "technology_lcop_csv": "technology_lcop.csv",
            "demand_excel": "2025_05_27 Demand outputs for trade module.xlsx",
            "mine_data_excel": "mining_data.xlsx",
            "scrap_excel": "2025_05_27 Demand outputs for trade module.xlsx",  # Same file used for scrap
            "tariff_excel": "Data_collection_ultimate_steel.xlsx",
            "carbon_costs_excel": "carbon_costs_iso3_year.xlsx",
            "business_cases_excel": "BOM_ghg_system_boundary_v6.xlsx",
            "location_csv": "countries.csv",
            "gravity_distances_pkl": "gravity_distances_dict.pkl",
            "historical_production_csv": "historical_production_data.csv",
            "iron_production_csv": "iron_production_2019to2022.csv",
            "steel_production_csv": "steel_production_2019to2022.csv",
            "input_costs_csv": "input_costs_for_python_model.csv",
            "energy_prices_excel": "Regional_Energy_prices.xlsx",
            "cost_of_x_json": "cost_of_x.json",
            "tech_switches_csv": "tech_switches_allowed.csv",  # Now extracted from Master Excel, not copied
            "regional_input_costs_json": "regional_input_costs.json",
            "geolocator_csv": "geolocator_raster.csv",
        }

        # Check which files exist in the package
        available_files = {}
        for key, filename in file_mapping.items():
            file_path = package_dir / filename
            if file_path.exists():
                available_files[key] = file_path
            else:
                logger.warning(f"File not found in package: {filename}")

        # Output paths
        output_paths = {
            "plants": output_dir / "plants.json",
            "demand_centers": output_dir / "demand_centers.json",
            "suppliers": output_dir / "suppliers.json",
            "tariffs": output_dir / "tariffs.json",
            "plant_groups": output_dir / "plant_groups.json",
            "carbon_costs": output_dir / "carbon_costs.json",
            "input_costs": output_dir / "input_costs.json",
            "primary_feedstocks": output_dir / "primary_feedstocks.json",
            "region_emissivity": output_dir / "region_emissivity.json",
            "capex": output_dir / "capex.json",
            "cost_of_capital": output_dir / "cost_of_capital.json",
            "subsidies": output_dir / "subsidies.json",
        }

        # Use explicit file paths instead of manipulating global settings
        try:
            # Recreate data using existing functions
            # NOTE: This method is deprecated. Plants should be created from master Excel, not CSV.
            # This code path is kept for backwards compatibility but will be removed.
            console.print("[yellow]WARNING: recreate_from_package() is deprecated for plants data.[/yellow]")
            console.print("[yellow]Plants should be created from master Excel using recreate_with_config().[/yellow]")

            # Skip plant creation here - it should be done via master Excel
            if output_paths["plants"].exists():
                console.print(f"  ℹ Using existing {output_paths['plants'].name}")

            console.print("[blue]Recreating plant groups data (derived from plants)...[/blue]")
            if output_paths["plants"].exists():
                recreate_plant_groups_data(
                    plants_json_path=output_paths["plants"],
                    plant_groups_json_path=output_paths["plant_groups"],
                )
                console.print(f"  ✓ Created {output_paths['plant_groups'].name} [SOURCE: derived from plants.json]")
            # console.print("[blue]Recreating tariffs data...[/blue]")
            # if "tariff_excel" in available_files:
            #     recreate_tarrifs_data(
            #         json_path=output_paths["tariffs"],
            #         tariff_excel_path=available_files["tariff_excel"],
            #         tariff_sheet_name="Trade_tariffs for model",
            #         country_mapping_sheet_name="Country mapping",
            #     )

            # console.print("[blue]Recreating carbon costs data...[/blue]")
            # if "carbon_costs_excel" in available_files:
            #     recreate_carbon_costs_data(
            #         carbon_cost_json_path=output_paths["carbon_costs"],
            #         excel_path=available_files["carbon_costs_excel"],
            #     )

            # Note: input_costs and primary_feedstocks now require master Excel
            # They will be created in the master Excel section below

            # Recreate additional data that requires master Excel
            if master_excel_path and master_excel_path.exists():
                # Commenting out grid emissivity due to column name mismatch in master Excel
                console.print("[blue]Recreating regional emissivity data with master Excel ...[/blue]")
                recreate_region_emissivity_data(
                    region_emissivity_json_path=output_paths["region_emissivity"],
                    excel_path=master_excel_path,
                    grid_emissivity_sheet="Power grid emissivity",
                    gas_coke_emissivity_sheet="Met coal & gas emissions",
                )
                console.print("[blue]Recreating primary feedstock data with master Excel...[/blue]")
                # if "business_cases_excel" in available_files:
                recreate_primary_feedstock_data(
                    primary_feedstock_json_path=output_paths["primary_feedstocks"],
                    excel_path=master_excel_path,
                    bom_excel_sheet="Bill of Materials",
                )

                console.print("[blue]Recreating input costs data from MASTER EXCEL...[/blue]")
                recreate_input_costs_data(
                    input_costs_json_path=output_paths["input_costs"],
                    excel_path=master_excel_path,
                    input_costs_sheet="Input costs",
                )
                console.print(
                    f"  ✓ Created {output_paths['input_costs'].name} [SOURCE: master-excel - Input costs sheet]"
                )

                console.print("[blue]Recreating tariff data from MASTER EXCEL...[/blue]")
                recreate_tarrifs_data(
                    json_path=output_paths["tariffs"],
                    tariff_excel_path=str(master_excel_path),
                    tariff_sheet_name="Tariffs",
                    country_mapping_sheet_name="Country mapping",
                )
                console.print(f"  ✓ Created {output_paths['tariffs'].name} [SOURCE: master-excel - Tariffs sheet]")

                console.print("[blue]Recreating subsidies data from MASTER EXCEL...[/blue]")
                recreate_subsidy_data(
                    json_path=output_paths["subsidies"],
                    excel_path=master_excel_path,
                    subsidies_sheet_name="Subsidies",
                    country_mapping_sheet_name="Country mapping",
                )
                console.print(f"  ✓ Created {output_paths['subsidies'].name} [SOURCE: master-excel - Subsidies sheet]")

                console.print("[blue]Recreating suppliers data from MASTER EXCEL...[/blue]")
                recreate_mines_and_scrap_as_suppliers_data(
                    json_path=output_paths["suppliers"],
                    master_excel_path=str(master_excel_path),
                    scrap_sheet_name="Demand and scrap availability",
                    mines_sheet_name="Iron ore mines",
                    location_csv=package_dir / "countries.csv",
                    gravity_distances_pkl_path=package_dir / "gravity_distances_dict.pkl",
                )
                console.print(
                    f"  ✓ Created {output_paths['suppliers'].name} "
                    "[SOURCE: master-excel - Iron ore mines & Demand sheets]"
                )

                console.print("[blue]Recreating demand centers data from MASTER EXCEL...[/blue]")
                # if "demand_excel" in available_files:
                recreate_demand_center_data(
                    json_path=output_paths["demand_centers"],
                    demand_excel_path=master_excel_path,
                    demand_sheet_name="Demand and scrap availability",
                    gravity_distances_path=package_dir / "gravity_distances_dict.pkl",
                    location_csv=package_dir / "countries.csv",
                )
                console.print(
                    f"  ✓ Created {output_paths['demand_centers'].name} "
                    "[SOURCE: master-excel - Demand and scrap availability sheet]"
                )
                console.print("[blue]Recreating carbon costs data from MASTER EXCEL...[/blue]")
                recreate_carbon_costs_data(
                    carbon_cost_json_path=output_paths["carbon_costs"],
                    excel_path=master_excel_path,
                )
                console.print(
                    f"  ✓ Created {output_paths['carbon_costs'].name} [SOURCE: master-excel - Carbon cost sheet]"
                )

                console.print("[blue]Recreating capex data from MASTER EXCEL...[/blue]")
                recreate_capex_data(capex_json_path=output_paths["capex"])
                console.print(
                    f"  ✓ Created {output_paths['capex'].name} [SOURCE: master-excel - Techno-economic details sheet]"
                )

                console.print("[blue]Recreating cost of capital data from MASTER EXCEL...[/blue]")
                recreate_cost_of_capital_data(
                    cost_of_capital_json_path=output_paths["cost_of_capital"], master_excel_path=master_excel_path
                )
                console.print(
                    f"  ✓ Created {output_paths['cost_of_capital'].name} [SOURCE: master-excel - Cost of capital sheet]"
                )

            # Copy additional files directly
            console.print("[blue]Copying additional configuration files from CORE-DATA...[/blue]")
            if "cost_of_x_json" in available_files:
                shutil.copy2(available_files["cost_of_x_json"], output_dir / "cost_of_x.json")
                console.print("  [OK] Copied cost_of_x.json [SOURCE: core-data]")

            # tech_switches_allowed.csv is now extracted from Master Excel via FILE_RECREATION_SPECS

            if "gravity_distances_pkl" in available_files:
                shutil.copy2(available_files["gravity_distances_pkl"], output_dir / "gravity_distances_dict.pkl")
                console.print("  [OK] Copied gravity_distances_dict.pkl [SOURCE: core-data]")

            if "regional_input_costs_json" in available_files:
                shutil.copy2(available_files["regional_input_costs_json"], output_dir / "regional_input_costs.json")
                console.print("  [OK] Copied regional_input_costs.json [SOURCE: core-data]")

            # Copy files that need to be in the data root directory
            if "energy_prices_excel" in available_files:
                data_root = output_dir.parent  # Go up one level from fixtures to data
                shutil.copy2(available_files["energy_prices_excel"], data_root / "Regional_Energy_prices.xlsx")
                console.print(f"  [OK] Copied Regional_Energy_prices.xlsx to {data_root} [SOURCE: core-data]")

        finally:
            # No longer need to restore settings since we don't modify them
            pass

        # Return paths to created files
        created_paths = {}
        for key, path in output_paths.items():
            if path.exists():
                created_paths[key] = path

        return created_paths

    def recreate_with_config(
        self,
        output_dir: Path,
        config: RecreationConfig,
        master_excel_path: Path | None = None,
        package_name: str = "core-data",
    ) -> dict[str, Path]:
        """
        Recreate files using a RecreationConfig for fine-grained control.

        Args:
            output_dir: Directory to write JSON repositories
            config: Recreation configuration
            master_excel_path: Optional path to master Excel file
            package_name: Data package to use for core archive files

        Returns:
            Dictionary mapping filenames to their output paths
        """
        # Initialize recreation manager
        self.recreation_manager = RecreationManager(config)

        # Get recreation summary
        summary = self.recreation_manager.get_recreation_summary()
        config.report_progress(f"Planning to recreate {summary['total_files']} files", 0)

        # Ensure output directory exists
        output_dir.mkdir(parents=True, exist_ok=True)

        # Get package directory for core archive files
        package_dir = self.data_manager.get_package_path(package_name)

        # Get files in dependency order
        recreation_order = self.recreation_manager.get_recreation_order()
        total_files = len(recreation_order)

        created_paths = {}
        failed_files = []

        for idx, filename in enumerate(recreation_order):
            progress = int((idx / total_files) * 100)

            # Check if we should recreate this file
            file_path = output_dir / filename
            if not config.should_recreate_file(filename, file_path):
                config.report_progress(f"Skipping {filename} (already exists)", progress)
                if file_path.exists():
                    created_paths[filename] = file_path
                continue

            # Get specification
            spec = FILE_RECREATION_SPECS.get(filename)
            if not spec:
                logger.warning(f"No recreation spec for {filename}")
                continue

            config.report_progress(f"Recreating {filename} from {spec.source_type}", progress)

            # Check dependencies
            missing_deps = self.recreation_manager.validate_dependencies(spec, output_dir)
            if missing_deps:
                error_msg = f"Missing dependencies for {filename}: {missing_deps}"
                logger.error(error_msg)
                if not config.continue_on_error:
                    raise ValueError(error_msg)
                failed_files.append(filename)
                continue

            # Recreate the file
            success = False
            for attempt in range(config.max_retries):
                try:
                    if attempt > 0:
                        config.report_progress(f"Retrying {filename} (attempt {attempt + 1})", progress)

                    # Call the appropriate recreation function
                    success = self._recreate_single_file(spec, output_dir, package_dir, master_excel_path)

                    if success and file_path.exists():
                        created_paths[filename] = file_path

                        # Validate if configured
                        if config.validate_after_creation and spec.validator_function:
                            if not spec.validator_function(file_path):
                                raise ValueError(f"Validation failed for {filename}")

                        # Report progress with source information
                        source_info = self._get_source_info(spec, master_excel_path)
                        config.report_progress(f"Successfully recreated {filename} {source_info}", progress)
                        break

                except Exception as e:
                    logger.error(f"Failed to recreate {filename}: {e}")
                    if attempt == config.max_retries - 1:
                        if not config.continue_on_error:
                            raise
                        failed_files.append(filename)

        # Final report
        config.report_progress(f"Recreation complete. Created: {len(created_paths)}, Failed: {len(failed_files)}", 100)

        if failed_files:
            logger.error(f"Failed to recreate: {failed_files}")

        return created_paths

    def _recreate_single_file(
        self,
        spec,
        output_dir: Path,
        package_dir: Path,
        master_excel_path: Path | None,
    ) -> bool:
        """
        Recreate a single file based on its specification.

        Returns:
            True if successful, False otherwise
        """
        # Map function names to actual functions
        function_map: dict[str, Callable[..., Any]] = {
            "recreate_country_mappings_data": recreate_country_mappings_data,
            "recreate_plants_data": recreate_plants_data,
            "recreate_plant_groups_data": recreate_plant_groups_data,
            "recreate_demand_center_data": recreate_demand_center_data,
            "recreate_mines_and_scrap_as_suppliers_data": recreate_mines_and_scrap_as_suppliers_data,
            "recreate_tariffs_data": recreate_tarrifs_data,
            "recreate_subsidy_data": recreate_subsidy_data,
            "recreate_carbon_costs_data": recreate_carbon_costs_data,
            "recreate_capex_data": recreate_capex_data,
            "recreate_cost_of_capital_data": recreate_cost_of_capital_data,
            "recreate_input_costs_data": recreate_input_costs_data,
            "recreate_primary_feedstock_data": recreate_primary_feedstock_data,
            "recreate_region_emissivity_data": recreate_region_emissivity_data,
            "recreate_tech_switches_data": recreate_tech_switches_data,
            "recreate_legal_process_connectors_data": recreate_legal_process_connectors_data,
            "recreate_hydrogen_efficiency_data": recreate_hydrogen_efficiency_data,
            "recreate_hydrogen_capex_opex_data": recreate_hydrogen_capex_opex_data,
            "recreate_transport_emissions_data": recreate_transport_emissions_data,
            "recreate_biomass_availability_data": recreate_biomass_availability_data,
            "recreate_technology_emission_factors_data": recreate_technology_emission_factors_data,
            "recreate_fopex_data": recreate_fopex_data,
            "recreate_carbon_border_mechanisms_data": recreate_carbon_border_mechanisms_data,
            "recreate_fallback_material_costs": recreate_fallback_material_costs,
        }

        if isinstance(spec.recreate_function, str):
            func = function_map.get(spec.recreate_function)
            if not func:
                raise ValueError(f"Unknown recreate function: {spec.recreate_function}")
        else:
            func = spec.recreate_function

        # Prepare arguments based on function requirements
        output_path = output_dir / spec.filename

        # Call the appropriate function with correct arguments
        # This is simplified - in reality we'd need to match each function's signature
        if spec.source_type == "master-excel" and master_excel_path:
            # Functions that use master Excel
            if spec.recreate_function == "recreate_country_mappings_data":
                func(
                    json_path=output_path,
                    master_excel_path=master_excel_path,
                )
            elif spec.recreate_function == "recreate_demand_center_data":
                func(
                    json_path=output_path,
                    demand_excel_path=master_excel_path,
                    demand_sheet_name=spec.master_excel_sheet,
                    gravity_distances_path=package_dir / "gravity_distances_dict.pkl",
                    location_csv=package_dir / "countries.csv",
                )
            elif spec.recreate_function == "recreate_mines_and_scrap_as_suppliers_data":
                func(
                    json_path=output_path,
                    master_excel_path=str(master_excel_path),
                    scrap_sheet_name="Demand and scrap availability",
                    mines_sheet_name=spec.master_excel_sheet,
                    location_csv=package_dir / "countries.csv",
                    gravity_distances_pkl_path=package_dir / "gravity_distances_dict.pkl",
                )
            elif spec.recreate_function == "recreate_tariffs_data":
                func(
                    json_path=output_path,
                    tariff_excel_path=str(master_excel_path),
                    tariff_sheet_name=spec.master_excel_sheet,
                    country_mapping_sheet_name="Country mapping",
                )
            elif spec.recreate_function == "recreate_subsidy_data":
                func(
                    json_path=output_path,
                    excel_path=master_excel_path,
                    subsidies_sheet_name=spec.master_excel_sheet,
                    country_mapping_sheet_name="Country mapping",
                )
            elif spec.recreate_function == "recreate_carbon_costs_data":
                func(
                    carbon_cost_json_path=output_path,
                    excel_path=master_excel_path,
                )
            elif spec.recreate_function == "recreate_cost_of_capital_data":
                func(
                    cost_of_capital_json_path=output_path,
                    master_excel_path=master_excel_path,
                )
            elif spec.recreate_function == "recreate_capex_data":
                func(
                    capex_json_path=output_path,
                    master_excel_path=master_excel_path,
                )
            elif spec.recreate_function == "recreate_region_emissivity_data":
                func(region_emissivity_json_path=output_path, excel_path=master_excel_path)
            elif spec.recreate_function == "recreate_input_costs_data":
                func(
                    input_costs_json_path=output_path,
                    excel_path=master_excel_path,
                    input_costs_sheet=spec.master_excel_sheet,
                )
            elif spec.recreate_function == "recreate_primary_feedstock_data":
                func(
                    primary_feedstock_json_path=output_path,
                    excel_path=master_excel_path,
                    bom_excel_sheet=spec.master_excel_sheet,
                )
            elif spec.recreate_function == "recreate_tech_switches_data":
                func(
                    tech_switches_csv_path=output_path,
                    master_excel_path=master_excel_path,
                )
            elif spec.recreate_function == "recreate_legal_process_connectors_data":
                func(
                    legal_process_connectors_json_path=output_path,
                    excel_path=master_excel_path,
                    sheet_name=spec.master_excel_sheet,
                )
            elif spec.recreate_function == "recreate_hydrogen_efficiency_data":
                func(
                    hydrogen_efficiency_json_path=output_path,
                    excel_path=master_excel_path,
                    sheet_name=spec.master_excel_sheet,
                )
            elif spec.recreate_function == "recreate_hydrogen_capex_opex_data":
                func(
                    hydrogen_capex_opex_json_path=output_path,
                    excel_path=master_excel_path,
                    sheet_name=spec.master_excel_sheet,
                )
            elif spec.recreate_function == "recreate_transport_emissions_data":
                func(
                    transport_emissions_json_path=output_path,
                    excel_path=master_excel_path,
                    sheet_name=spec.master_excel_sheet,
                )
            elif spec.recreate_function == "recreate_biomass_availability_data":
                func(
                    biomass_availability_json_path=output_path,
                    excel_path=master_excel_path,
                    sheet_name=spec.master_excel_sheet,
                )
            elif spec.recreate_function == "recreate_technology_emission_factors_data":
                func(
                    technology_emission_factors_json_path=output_path,
                    excel_path=master_excel_path,
                    sheet_name=spec.master_excel_sheet,
                )
            elif spec.recreate_function == "recreate_fopex_data":
                func(
                    fopex_json_path=output_path,
                    excel_path=master_excel_path,
                    sheet_name=spec.master_excel_sheet,
                )
            elif spec.recreate_function == "recreate_carbon_border_mechanisms_data":
                func(
                    json_path=output_path,
                    master_excel_path=master_excel_path,
                    sheet_name=spec.master_excel_sheet,
                )
            elif spec.recreate_function == "recreate_fallback_material_costs":
                func(
                    fallback_material_costs_json_path=output_path,
                    excel_path=master_excel_path,
                    sheet_name=spec.master_excel_sheet,
                )
            elif spec.recreate_function == "recreate_plants_data":
                # Special handling for plants - read directly from master Excel
                from ..adapters.dataprocessing.master_excel_reader import MasterExcelReader
                from ..adapters.dataprocessing.preprocessing.iso3_finder import Coordinate
                from ..adapters.dataprocessing.excel_reader import read_dynamic_business_cases
                import csv

                # Load geocoder coordinates if available
                geocoder_coordinates = None
                geolocator_path = output_dir / "geolocator_raster.csv"
                if geolocator_path.exists():
                    geocoder_coordinates = []
                    with open(geolocator_path, "r") as f:
                        reader = csv.DictReader(f)
                        for row in reader:
                            try:
                                lat = float(row["lat"])
                                lon = float(row["lon"])
                                cc = row.get("cc", "")
                                if cc and len(cc) == 2:
                                    # Convert 2-letter country code to ISO3
                                    import pycountry

                                    try:
                                        country = pycountry.countries.get(alpha_2=cc)
                                        if country:
                                            iso3 = country.alpha_3
                                            geocoder_coordinates.append(Coordinate(lat=lat, lon=lon, iso3=iso3))
                                    except Exception:
                                        continue
                            except (ValueError, KeyError):
                                continue
                    console.print(f"  [dim]Loaded {len(geocoder_coordinates)} geocoder coordinates[/dim]")

                # Read dynamic business cases from Bill of Materials sheet
                console.print("  [dim]Reading dynamic business cases from Bill of Materials sheet[/dim]")
                dynamic_feedstocks_dict = read_dynamic_business_cases(
                    str(master_excel_path), excel_sheet="Bill of Materials"
                )

                # Note: We're not loading gravity distances for now as they need proper JSON serialization
                with MasterExcelReader(master_excel_path) as reader:
                    plants, canonical_metadata = reader.read_plants(
                        dynamic_feedstocks_dict=dynamic_feedstocks_dict,
                        geocoder_coordinates=geocoder_coordinates,
                        simulation_start_year=2025,  # TODO: Make this configurable
                    )

                func(
                    plants=plants,
                    json_path=output_path,
                    canonical_metadata=canonical_metadata,
                    data_reference_year=2025,  # TODO: Make this configurable
                    master_excel_path=master_excel_path,
                    master_excel_version="v1.0",  # TODO: Extract from Excel or config
                )
            elif spec.recreate_function == "recreate_plant_groups_data":
                func(
                    plants_json_path=output_dir / "plants.json",
                    plant_groups_json_path=output_path,
                )

        return output_path.exists()

    def _get_source_info(self, spec, master_excel_path: Path | None) -> str:
        """Get source information string for logging."""
        if spec.source_type == "master-excel" and master_excel_path:
            sheet = spec.master_excel_sheet or "Unknown sheet"
            return f"[SOURCE: master-excel - {sheet}]"
        elif spec.source_type == "core-archive":
            return "[SOURCE: core-data]"
        elif spec.source_type == "derived":
            deps = ", ".join(spec.dependencies[:2])
            if len(spec.dependencies) > 2:
                deps += f" (+{len(spec.dependencies) - 2} more)"
            return f"[SOURCE: derived from {deps}]"
        else:
            return f"[SOURCE: {spec.source_type}]"

    def recreate_all_packages(self, output_dir: Path) -> dict[str, dict[str, Path]]:
        """Recreate JSON repositories from all required packages.

        Args:
            output_dir: Base directory for output

        Returns:
            Dictionary mapping package names to their created repository paths
        """
        results = {}

        # Download all required packages
        console.print("[blue]Downloading all required data packages...[/blue]")
        self.data_manager.download_required_data()

        # Process each required package
        for package in self.data_manager.manifest.get_required_packages():
            package_output_dir = output_dir / package.name
            console.print(f"\n[blue]Processing package: {package.name}[/blue]")

            try:
                created_paths = self.recreate_from_package(
                    package.name,
                    package_output_dir,
                )
                results[package.name] = created_paths
            except Exception as e:
                logger.error(f"Failed to process package {package.name}: {e}")
                console.print(f"[red][FAILED] Failed to process {package.name}: {e}[/red]")

        return results
