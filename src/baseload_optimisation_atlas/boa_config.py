from pathlib import Path
from dataclasses import dataclass
from steelo.data import DataManager

# ===== Baseload power simulation parameters =====
# Lifetime of technologies in years (note: years must be a positive integer)
LIFETIMES = {
    "solar": 25,  # 25-30 years in IEA
    # https://www.iea.org/news/the-world-needs-more-diverse-solar-panel-supply-chains-to-ensure-a-secure-transition-to-net-zero-emissions?utm_source=chatgpt.com
    "wind": 25,  # IRENA https://www.irena.org/-/media/Files/IRENA/Agency/Publication/2017/Jun/IRENA_Leveraging_for_Onshore_Wind_Executive_Summary_2017.pdf
    # p.20; other sources say 20 years
    "battery": 13,  # IRENA https://www.irena.org/-/media/Files/IRENA/Agency/Publication/2017/Oct/IRENA_Electricity_Storage_Costs_2017.pdf
    # fig. 26; span 5-20 years, most 10-15 -> 13 instead of 12.5 because it must be an integer
    "steel": 20,
}

# Learning rates for solar and wind technologies
# From previous Systemiq project, TODO: Get source from Rafal
LEARNING_RATES = {
    "solar": 0.234,
    "wind": 0.146,
}

# Scaling factor for batteries: Used in the transformed capex scaling factor equation to account for modules with several units
# being installed at once being cheaper than many single units
BATTERY_UNIT_CAPEX_SCALING_FACTOR = -0.15

# Deterioration rate the energy systems over their lifetime
# TODO: Look for better sources; currently using a rough estimate by Rafal for solar and wind
YEARLY_DETERIORATION_RATES = {
    "solar": 0.005,  # 0.5% per year
    "wind": 0.01,  # 1% per year
    "battery": 0.015,  # 1.5% per year - batteries degrade faster
    # TODO: Modify LCOE calculation to include battery deterioration explicitly; 1.5% per year from
    # https://www2.nrel.gov/transportation/battery-lifespan
}

# Random seed for reproducibility
RANDOM_SEED = 42

# ===== Atlite simulation parameters =====
# ERA5 weather data constants
ERA5_DATA_RESOLUTION = 0.25  # degrees
ERA5_DATA_YEAR = 2024
# Coordinates; [max_lat, min_lon, min_lat, max_lon] = [north, west, south, east]
REGION_COORDS = {
    "INDO_AUS": [5.0, 93.0, -50.0, 180.0],
    "AFRICA": [3.0, 7.0, -37.0, 52.0],
    "ALASKA": [72.0, -170.0, 42.0, -50.0],
    "NORTH_AMERICA": [42.0, -128.0, 8.0, -50.0],
    "SOUTH_AMERICA": [14.0, -85.0, -58.0, -33.0],
    "MENA": [38.0, -20.0, 3.0, 62.0],
    "EU": [72.0, -25.0, 35.0, 62.0],
    "NORTH_ASIA": [72.0, 62.0, 50.0, 180.0],
    "SOUTH_ASIA": [50.0, 62.0, 5.0, 148.0],
}


# ===== Path Configuration =====
@dataclass
class BaseloadPowerConfig:
    """Configuration for baseload power simulation paths."""

    # Input data paths
    master_input_path: Path
    renewable_input_path: Path
    countries_shapefile_path: Path
    disputed_areas_shapefile_path: Path
    terrain_nc_path: Path

    # Directory paths
    atlite_output_dir: Path
    cav_dir: Path
    geo_output_dir: Path
    geo_plots_dir: Path

    @classmethod
    def from_project_root(cls, project_root: Path) -> "BaseloadPowerConfig":
        """
        Create configuration to run the baseload power simulation as a standalone from project root
        with default paths.
        """

        # Get paths to data shared with steelo
        manager = DataManager()
        manager.download_package("master-input", force=False)
        master_input_dir = manager.get_package_path("master-input")
        manager.download_package("geo-data", force=False)
        geo_data_dir = manager.get_package_path("geo-data")

        # Define paths for standalone data for the baseload power simulation
        data_dir = project_root / "data"
        atlite_dir = data_dir / "atlite"

        return cls(
            # Input data created when running the steelo pipeline
            master_input_path=master_input_dir / "master_input.xlsx",
            countries_shapefile_path=geo_data_dir / "ne_110m_admin_0_countries" / "ne_110m_admin_0_countries.shp",
            disputed_areas_shapefile_path=geo_data_dir
            / "ne_10m_admin_0_disputed_areas"
            / "ne_10m_admin_0_disputed_areas.shp",
            terrain_nc_path=geo_data_dir / "terrain_025_deg.nc",
            # Input data which must be added before running the baseload power simulation
            renewable_input_path=data_dir / "Renewable_Energy_Input_Data.xlsx",
            # Intermediate outputs from Atlite which must be added before running the baseload power simulation
            atlite_output_dir=atlite_dir / "output",
            cav_dir=atlite_dir / "cav",
            # Outputs of the baseload power simulation (which are inputs to steelo)
            geo_output_dir=project_root / "output" / "GEO",
            geo_plots_dir=project_root / "output" / "plots" / "GEO",
        )
