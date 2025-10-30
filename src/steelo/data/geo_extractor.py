"""Extract geo data from downloaded packages to the correct locations."""

import shutil
from pathlib import Path
from typing import Optional

from .manager import DataManager
import logging


class GeoDataExtractor:
    """Extracts geo data files to their expected locations."""

    def __init__(self, data_manager: Optional[DataManager] = None):
        """Initialize the extractor.

        Args:
            data_manager: Data manager instance. If not provided, creates a new one.
        """
        self.data_manager = data_manager or DataManager()

    def extract_geo_data(self, target_dir: Path = Path("data"), version: Optional[str] = None) -> dict[str, Path]:
        """Extract geo data files from the cached package to their expected locations.

        Args:
            target_dir: Base directory for data files (default: "data")
            version: Specific version of geo-data to use (optional)

        Returns:
            Dictionary mapping file descriptions to their extracted paths
        """
        # Get the geo-data package path, downloading if necessary
        try:
            package_path = self.data_manager.get_package_path("geo-data", version)
        except ValueError:
            # Package not downloaded yet, download it
            self.data_manager.download_package("geo-data", version)
            package_path = self.data_manager.get_package_path("geo-data", version)

        # Find the actual data directory (might be nested)
        geo_data_dir = None

        # First check if files are directly in the package root (v1.0.4+)
        if (package_path / "terrain_025_deg.nc").exists():
            geo_data_dir = package_path
        else:
            # Look for nested directories (older versions)
            for item in package_path.iterdir():
                if item.is_dir() and not item.name.startswith("."):
                    # For v1.0.3, it might be nested deeper
                    if item.name == "temp-geo-final":
                        subdir = item / "geo-data-temp"
                        if subdir.exists():
                            geo_data_dir = subdir
                            break
                    geo_data_dir = item
                    break

        if not geo_data_dir:
            raise ValueError(f"No data directory found in {package_path}")

        extracted_files = {}

        # Define the mapping of files to their target locations
        file_mappings = {
            "terrain_025_deg.nc": target_dir / "terrain_025_deg.nc",
            "ESACCI-LC-L4-LCCS-Map-300m-P1Y-2015-v2.0.7.tif": target_dir
            / "ESACCI-LC-L4-LCCS-Map-300m-P1Y-2015-v2.0.7.tif",
            "railway_CAPEX.csv": target_dir / "railway_CAPEX.csv",
            "LCOH_CAPEX_and_O&M_component.csv": target_dir / "LCOH_CAPEX_and_O&M_component.csv",
            "Infrastructure/rail_distance1.nc": target_dir / "Infrastructure" / "rail_distance1.nc",
            # Regional energy prices Excel file
            "Regional_Energy_prices.xlsx": target_dir / "Regional_Energy_prices.xlsx",
            # Landtype percentage data
            "landtype_percentage.nc": target_dir / "landtype_percentage.nc",
            # Natural Earth shapefile components
            "ne_110m_admin_0_countries/ne_110m_admin_0_countries.shp": target_dir
            / "ne_110m_admin_0_countries"
            / "ne_110m_admin_0_countries.shp",
            "ne_110m_admin_0_countries/ne_110m_admin_0_countries.shx": target_dir
            / "ne_110m_admin_0_countries"
            / "ne_110m_admin_0_countries.shx",
            "ne_110m_admin_0_countries/ne_110m_admin_0_countries.dbf": target_dir
            / "ne_110m_admin_0_countries"
            / "ne_110m_admin_0_countries.dbf",
            "ne_110m_admin_0_countries/ne_110m_admin_0_countries.prj": target_dir
            / "ne_110m_admin_0_countries"
            / "ne_110m_admin_0_countries.prj",
            "ne_110m_admin_0_countries/ne_110m_admin_0_countries.cpg": target_dir
            / "ne_110m_admin_0_countries"
            / "ne_110m_admin_0_countries.cpg",
            # Output files
            "outputs/GEO/feasibility_mask.nc": target_dir / "outputs" / "GEO" / "feasibility_mask.nc",
            "outputs/GEO/global_grid_with_iso3.nc": target_dir / "outputs" / "GEO" / "global_grid_with_iso3.nc",
            "outputs/GEO/landtype_factor.nc": target_dir / "outputs" / "GEO" / "landtype_factor.nc",
            "outputs/GEO/rail_cost.nc": target_dir / "outputs" / "GEO" / "rail_cost.nc",
            # Economic/Cost Data
            "capex_batteries_2010to2100.csv": target_dir / "capex_batteries_2010to2100.csv",
            "capex_onshore_wind_2022.csv": target_dir / "capex_onshore_wind_2022.csv",
            "capex_solar_2022.csv": target_dir / "capex_solar_2022.csv",
            "geolocator_raster.csv": target_dir / "geolocator_raster.csv",
            "installed_renewables_capacity_2022.csv": target_dir / "installed_renewables_capacity_2022.csv",
            # Capacity Projections
            "ssp_rcp_onshore_wind_capacity_projections.xlsx": target_dir
            / "ssp_rcp_onshore_wind_capacity_projections.xlsx",
            "ssp_rcp_solar_capacity_projections.xlsx": target_dir / "ssp_rcp_solar_capacity_projections.xlsx",
        }

        # Add new shapefiles from development version
        # ne_10m_admin_0_disputed_areas
        for ext in ["shp", "shx", "dbf", "prj", "cpg"]:
            file_mappings[f"ne_10m_admin_0_disputed_areas/ne_10m_admin_0_disputed_areas.{ext}"] = (
                target_dir / "ne_10m_admin_0_disputed_areas" / f"ne_10m_admin_0_disputed_areas.{ext}"
            )

        # Also extract baseload power simulation files
        # Note: p5 files are in GLOBAL subdirectory to match BOA output structure
        for year in [2025, 2030, 2035, 2040, 2045, 2050]:
            # p5 files are now in p5/GLOBAL/ in the archive (fixed in v1.2.0)
            file_mappings[f"outputs/GEO/baseload_power_simulation/p5/GLOBAL/optimal_sol_GLOBAL_{year}_p5.nc"] = (
                target_dir
                / "outputs"
                / "GEO"
                / "baseload_power_simulation"
                / "p5"
                / "GLOBAL"
                / f"optimal_sol_GLOBAL_{year}_p5.nc"
            )
            file_mappings[f"outputs/GEO/baseload_power_simulation/p15/GLOBAL/optimal_sol_GLOBAL_{year}_p15.nc"] = (
                target_dir
                / "outputs"
                / "GEO"
                / "baseload_power_simulation"
                / "p15"
                / "GLOBAL"
                / f"optimal_sol_GLOBAL_{year}_p15.nc"
            )

        # Copy files to their expected locations
        for source_name, target_path in file_mappings.items():
            source_path = geo_data_dir / source_name

            if source_path.exists():
                # Create parent directory if needed
                target_path.parent.mkdir(parents=True, exist_ok=True)

                # Copy the file
                shutil.copy2(source_path, target_path)
                extracted_files[source_name] = target_path
                logging.info(f"Extracted {source_name} to {target_path}")
            else:
                logging.warning(f"Warning: {source_name} not found in geo-data package")

        return extracted_files
