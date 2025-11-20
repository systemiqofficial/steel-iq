"""
Data Path Resolver

Resolves data file paths from prepared data directories instead of using
hardcoded settings paths. This enables the master Excel migration by
removing dependencies on config.py paths.
"""

import logging
import zipfile
from pathlib import Path
from typing import Iterable, Optional
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)


class DataPathResolver:
    """
    Resolves paths to data files based on the prepared data directory structure.

    This class replaces the use of hardcoded settings paths, allowing data to come
    from either the master Excel or core archive without code changes.
    """

    MASTER_WORKBOOK_CANDIDATES: tuple[str, ...] = (
        "master_input_vlive_1.1.xlsx",
        "master_input_vlive.xlsx",
        "master_input.xlsx",
        "Data_collection_ultimate_steel.xlsx",
    )

    def __init__(self, data_directory: Path, fixtures_subdir: str = "fixtures"):
        """
        Initialize the path resolver.

        Args:
            data_directory: Root directory containing prepared data
            fixtures_subdir: Subdirectory name for fixture files (default: "fixtures")
        """
        self.data_directory = Path(data_directory)
        self.fixtures_dir = self.data_directory / fixtures_subdir

        # Validate directory exists
        if not self.data_directory.exists():
            raise ValueError(f"Data directory does not exist: {self.data_directory}")

    def get_fixtures_path(self, filename: str) -> Path:
        """Get path to a file in the fixtures directory."""
        return self.fixtures_dir / filename

    def get_data_path(self, filename: str, subdirs: Optional[list[str]] = None) -> Path:
        """
        Get path to a file in the data directory.

        Args:
            filename: Name of the file
            subdirs: Optional list of subdirectories (e.g., ["outputs", "GEO"])
        """
        if subdirs:
            path = self.data_directory
            for subdir in subdirs:
                path = path / subdir
            return path / filename
        return self.data_directory / filename

    # JSON repository paths
    @property
    def plants_json_path(self) -> Path:
        return self.get_fixtures_path("plants.json")

    @property
    def demand_centers_json_path(self) -> Path:
        return self.get_fixtures_path("demand_centers.json")

    @property
    def suppliers_json_path(self) -> Path:
        return self.get_fixtures_path("suppliers.json")

    @property
    def plant_groups_json_path(self) -> Path:
        return self.get_fixtures_path("plant_groups.json")

    @property
    def tariffs_json_path(self) -> Path:
        return self.get_fixtures_path("tariffs.json")

    @property
    def carbon_costs_json_path(self) -> Path:
        return self.get_fixtures_path("carbon_costs.json")

    @property
    def primary_feedstocks_json_path(self) -> Path:
        return self.get_fixtures_path("primary_feedstocks.json")

    @property
    def input_costs_json_path(self) -> Path:
        return self.get_fixtures_path("input_costs.json")

    @property
    def region_emissivity_json_path(self) -> Path:
        return self.get_fixtures_path("region_emissivity.json")

    @property
    def capex_json_path(self) -> Path:
        return self.get_fixtures_path("capex.json")

    @property
    def cost_of_capital_json_path(self) -> Path:
        return self.get_fixtures_path("cost_of_capital.json")

    # Raw data file paths
    @property
    def steel_plants_csv_path(self) -> Path:
        return self.get_fixtures_path("steel_plants_input_data_2025-03.csv")

    @property
    def technology_lcop_csv_path(self) -> Path:
        return self.get_fixtures_path("technology_lcop.csv")

    @property
    def historical_production_csv_path(self) -> Path:
        return self.get_fixtures_path("historical_production_data.csv")

    @property
    def iron_production_csv_path(self) -> Path:
        return self.get_fixtures_path("iron_production_2019to2022.csv")

    @property
    def steel_production_csv_path(self) -> Path:
        return self.get_fixtures_path("steel_production_2019to2022.csv")

    @property
    def gravity_distances_pkl_path(self) -> Path:
        return self.get_fixtures_path("gravity_distances_dict.pkl")

    @property
    def geolocator_raster_csv_path(self) -> Path:
        return self.get_fixtures_path("geolocator_raster.csv")

    @property
    def countries_csv_path(self) -> Path:
        return self.get_fixtures_path("countries.csv")

    @property
    def cost_of_x_json_path(self) -> Path:
        return self.get_fixtures_path("cost_of_x.json")

    @property
    def tech_switches_csv_path(self) -> Path:
        return self.get_fixtures_path("tech_switches_allowed.csv")

    @property
    def regional_input_costs_json_path(self) -> Path:
        # This property is deprecated - use input_costs_json_path instead
        return self.get_fixtures_path("input_costs.json")

    @property
    def master_excel_path(self) -> Path:
        """Path to master input Excel file if it exists."""
        return self._resolve_master_workbook_path()

    # Excel file paths (legacy)
    @property
    def demand_excel_path(self) -> Path:
        return self.get_fixtures_path("2025_05_27 Demand outputs for trade module.xlsx")

    @property
    def mining_data_excel_path(self) -> Path:
        return self.get_fixtures_path("mining_data.xlsx")

    @property
    def business_cases_excel_path(self) -> Path:
        return self.get_fixtures_path("BOM_ghg_system_boundary_v6.xlsx")

    @property
    def carbon_costs_excel_path(self) -> Path:
        return self.get_fixtures_path("carbon_costs_iso3_year.xlsx")

    @property
    def tariff_excel_path(self) -> Path:
        required = ("Trade_tariffs for model", "Trade_bloc definitions")
        return self._resolve_master_workbook_path(required_sheets=required)

    @property
    def fallback_bom_excel_path(self) -> Path:
        """Returns the workbook to use for fallback BOM definitions."""
        return self._resolve_master_workbook_path(required_sheets=("Fallback BOM definition",))

    @property
    def input_costs_csv_path(self) -> Path:
        return self.get_fixtures_path("input_costs_for_python_model.csv")

    @property
    def regional_energy_prices_excel_path(self) -> Path:
        return self.get_fixtures_path("Regional_Energy_prices.xlsx")

    # Geo data paths
    @property
    def terrain_nc_path(self) -> Path:
        return self.get_data_path("terrain_025_deg.nc")

    @property
    def rail_distance_nc_path(self) -> Path:
        return self.get_data_path("rail_distance1.nc", ["Infrastructure"])

    @property
    def railway_capex_csv_path(self) -> Path:
        return self.get_data_path("railway_CAPEX.csv")

    @property
    def lcoh_capex_csv_path(self) -> Path:
        return self.get_data_path("LCOH_CAPEX_and_O&M_component.csv")

    @property
    def baseload_power_sim_dir(self) -> Path:
        return self.get_data_path("baseload_power_simulation", ["outputs", "GEO"])

    @property
    def feasibility_mask_path(self) -> Path:
        return self.get_data_path("feasibility_mask.nc", ["outputs", "GEO"])

    @property
    def geo_plots_dir(self) -> Path:
        return self.get_data_path("", ["output", "plots", "GEO"])

    def _resolve_master_workbook_path(self, required_sheets: Optional[Iterable[str]] = None) -> Path:
        """Resolve the path to the primary master workbook, with graceful fallback."""
        checked_paths: list[Path] = []
        required_tuple = tuple(required_sheets) if required_sheets else ()

        for candidate in self.MASTER_WORKBOOK_CANDIDATES:
            for base_path in (self.get_fixtures_path(candidate), self.data_directory / candidate):
                checked_paths.append(base_path)
                if not base_path.exists():
                    continue

                if required_tuple and not self._workbook_contains_sheets(base_path, required_tuple):
                    logger.warning(
                        "Workbook '%s' missing required sheets %s. Skipping.",
                        base_path,
                        required_tuple,
                    )
                    continue

                if candidate != self.MASTER_WORKBOOK_CANDIDATES[0]:
                    logger.warning(
                        "Using legacy master workbook '%s'. Please migrate to '%s' when possible.",
                        base_path.name,
                        self.MASTER_WORKBOOK_CANDIDATES[0],
                    )
                return base_path

        logger.warning(
            "No master workbook found with required sheets %s. Checked: %s. Defaulting to fixtures path for %s.",
            required_tuple or "<any>",
            ", ".join(str(path) for path in checked_paths),
            self.MASTER_WORKBOOK_CANDIDATES[0],
        )
        return self.get_fixtures_path(self.MASTER_WORKBOOK_CANDIDATES[0])

    @staticmethod
    def _workbook_contains_sheets(path: Path, required_sheets: tuple[str, ...]) -> bool:
        """Return True if the workbook contains all required sheets."""
        if not required_sheets:
            return True

        try:
            with zipfile.ZipFile(path) as archive:
                workbook_xml = archive.read("xl/workbook.xml")
        except (FileNotFoundError, zipfile.BadZipFile, KeyError):
            return False

        try:
            tree = ET.fromstring(workbook_xml)
        except ET.ParseError:
            return False

        ns = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        sheets = tree.findall("main:sheets/main:sheet", ns)
        sheet_names = {sheet.attrib.get("name") for sheet in sheets}

        return all(sheet in sheet_names for sheet in required_sheets if sheet)

    @property
    def countries_shapefile_dir(self) -> Path:
        return self.get_data_path("ne_110m_admin_0_countries")

    @property
    def landtype_percentage_nc_path(self) -> Path:
        return self.get_data_path("landtype_percentage.nc")

    def validate_required_files(self, required_files: list[str]) -> None:
        """Checks if all specified files exist, raising an error if any are missing."""
        for filename in required_files:
            # Find the corresponding property to get the full path
            prop_name = filename.replace(".json", "_json_path").replace(".csv", "_csv_path")

            # Special case for some files
            if filename == "cost_of_x.json":
                prop_name = "cost_of_x_json_path"
            elif filename == "feasibility_mask.nc":
                prop_name = "feasibility_mask_path"

            if not hasattr(self, prop_name):
                # If no specific property, assume it's in fixtures
                file_path = self.get_fixtures_path(filename)
            else:
                file_path = getattr(self, prop_name)

            if not file_path.exists():
                raise FileNotFoundError(f"Required data file not found: {filename} (expected at {file_path})")

    def get_simulation_config_paths(self) -> dict[str, Path]:
        """
        Get all paths needed for SimulationConfig initialization.

        Returns:
            Dictionary of path names to Path objects
        """
        return {
            # JSON repository paths
            "plants_json_path_repo": self.plants_json_path,
            "demand_centers_json_path": self.demand_centers_json_path,
            "suppliers_json_path": self.suppliers_json_path,
            "plant_groups_json_path": self.plant_groups_json_path,
            "tariffs_json_path": self.tariffs_json_path,
            "carbon_costs_json_path": self.carbon_costs_json_path,
            "primary_feedstocks_json_path": self.primary_feedstocks_json_path,
            "input_costs_json_path": self.input_costs_json_path,
            "region_emissivity_json_path": self.region_emissivity_json_path,
            "capex_json_path": self.capex_json_path,
            "cost_of_capital_json_path": self.cost_of_capital_json_path,
            # Raw data paths
            "tech_switches_csv_path": self.tech_switches_csv_path,
            "cost_of_x_csv": self.cost_of_x_json_path,  # Note: SimulationConfig expects this name
            "geolocator_coordinates": self.geolocator_raster_csv_path,
            # Geo data paths
            "terrain_nc_path": self.terrain_nc_path,
            "rail_distance_nc_path": self.rail_distance_nc_path,
            "railway_capex_csv_path": self.railway_capex_csv_path,
            "lcoh_capex_csv_path": self.lcoh_capex_csv_path,
            "regional_energy_prices_xlsx": self.regional_energy_prices_excel_path,
            "baseload_power_sim_dir": self.baseload_power_sim_dir,
            "geo_plots_dir": self.geo_plots_dir,
            "countries_shapefile_dir": self.countries_shapefile_dir,
            "landtype_percentage_nc_path": self.landtype_percentage_nc_path,
        }
