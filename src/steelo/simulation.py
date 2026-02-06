from dataclasses import dataclass, field, InitVar
from pathlib import Path
from typing import Optional, Callable, Any, TYPE_CHECKING
import logging
import json
from collections import Counter
from datetime import datetime
import tempfile
import shutil
import time
import psutil

try:
    import resource
except ImportError:  # pragma: no cover - Windows compatibility
    resource = None  # type: ignore[assignment]
import sys

from steelo.simulation_types import TechSettingsMap, get_default_technology_settings
from steelo.utilities.memory_profiling import MemoryTracker

from .domain import Year, PlantGroup
from .service_layer.message_bus import MessageBus
from .economic_models import EconomicModel, PlantAgentsModel, AllocationModel, GeospatialModel
from .domain.events import IterationOver
from .domain.datacollector import DataCollector
from .adapters.dataprocessing.postprocessing.post_process_datacollection import (
    extract_and_process_stored_dataCollection,
)
from .adapters.dataprocessing.postprocessing.generate_post_run_plots import generate_post_run_cap_prod_plots
from steelo.utilities.plotting import (
    plot_bar_chart_of_new_plants_by_status,
    plot_map_of_new_plants_operating,
)
from .logging_config import LoggingConfig
from steelo.domain.constants import T_TO_KT, MT_TO_T
from steelo.domain.calculate_costs import filter_subsidies_for_year, get_subsidised_energy_costs
from .furnace_breakdown_logging_minimal import FurnaceBreakdownLogger

if TYPE_CHECKING:
    from .adapters.repositories import Repository

# Initialize base logging configuration at module import
LoggingConfig.configure_base_loggers()

logger = logging.getLogger(__name__)


_MEMORY_BASELINE_RSS: float | None = None
_MEMORY_PEAK_RSS: float = 0.0


def _reset_memory_tracking() -> None:
    """Clear memory tracking globals so each simulation run starts fresh."""
    global _MEMORY_BASELINE_RSS, _MEMORY_PEAK_RSS
    _MEMORY_BASELINE_RSS = None
    _MEMORY_PEAK_RSS = 0.0


def _log_memory_usage(operation: str, **metadata: object) -> None:
    """Log detailed process memory metrics for the current worker."""

    def _safe_mb(value: float | None) -> float | None:
        if value is None:
            return None
        return value / (1024 * 1024)

    process = psutil.Process()

    # Collect memory metrics for the current process
    rss_bytes: float
    uss_bytes: float | None = None
    swap_bytes: float | None = None
    try:
        full_info = process.memory_full_info()
        rss_bytes = float(full_info.rss)
        uss_bytes = float(getattr(full_info, "uss", 0.0)) or None
        swap_bytes = float(getattr(full_info, "swap", 0.0)) or None
    except (psutil.AccessDenied, psutil.Error):
        mem_info = process.memory_info()
        rss_bytes = float(mem_info.rss)

    # Include memory used by child processes (e.g., solver workers)
    # Track baseline and peak usage for this process lifetime
    global _MEMORY_BASELINE_RSS, _MEMORY_PEAK_RSS
    if _MEMORY_BASELINE_RSS is None:
        _MEMORY_BASELINE_RSS = rss_bytes
    if rss_bytes > _MEMORY_PEAK_RSS:
        _MEMORY_PEAK_RSS = rss_bytes

    delta_from_start_bytes = rss_bytes - (_MEMORY_BASELINE_RSS or rss_bytes)

    # System-wide context
    virtual_memory = psutil.virtual_memory()
    system_total_mb = _safe_mb(float(virtual_memory.total))
    system_used_pct = virtual_memory.percent

    # High-water mark using resource module (platform dependent units)
    peak_rss_mb: float | None = None
    try:
        if resource is not None:
            ru = resource.getrusage(resource.RUSAGE_SELF)
            peak_raw = float(ru.ru_maxrss)
            if sys.platform == "darwin":  # ru_maxrss already bytes on macOS
                peak_rss_mb = peak_raw / (1024 * 1024)
            else:  # Linux returns kilobytes
                peak_rss_mb = peak_raw / 1024.0
    except Exception:
        peak_rss_mb = None

    parts = [f"operation={operation}"]
    for key in sorted(metadata):
        value = metadata[key]
        parts.append(f"{key}={value}")

    parts.extend(
        [
            f"rss_mb={_safe_mb(rss_bytes):.1f}",
            f"delta_from_start_mb={_safe_mb(delta_from_start_bytes):.1f}",
            f"peak_rss_mb={_safe_mb(_MEMORY_PEAK_RSS):.1f}",
            f"system_used_pct={system_used_pct:.1f}",
        ]
    )

    if system_total_mb is not None:
        parts.append(f"system_total_mb={system_total_mb:.0f}")
    if uss_bytes is not None:
        parts.append(f"uss_mb={_safe_mb(uss_bytes):.1f}")
    if swap_bytes is not None:
        parts.append(f"swap_mb={_safe_mb(swap_bytes):.1f}")
    if peak_rss_mb is not None:
        parts.append(f"ru_maxrss_mb={peak_rss_mb:.1f}")

    logger.info(" ".join(parts))


class Simulation:
    """
    A class to run a simulation using an economic model. The simulation
    uses a MessageBus to handle events and commands and uses the strategy
    pattern to run the economic model.
    """

    def __init__(self, *, bus: MessageBus, economic_model: EconomicModel) -> None:
        self.bus = bus
        self.economic_model = economic_model

    def run_simulation(self) -> None:
        # Use the LoggingConfig context manager with the model's class name
        model_name = self.economic_model.__class__.__name__
        with LoggingConfig.simulation_logging(model_name):
            self.economic_model.run(self.bus)


@dataclass
class GeoConfig:
    """
    Configuration for geospatial calculations and new plant candidate location selection.
    All geospatial parameters bundled together with sensible defaults.
    """

    # === Power and Hydrogen ===
    included_power_mix: str = "85% baseload + 15% grid"  # Options: "85% baseload + 15% grid", "95% baseload + 5% grid", "Not included", "Grid only"
    hydrogen_ceiling_percentile: float = 20.0  # Hydrogen price cap percentage to engage in interregional trade (e.g., within the EU). Set to 100 to inhibit interregional trade.
    intraregional_trade_allowed: bool = (
        True  # Whether trade among linked regions is allowed (e.g., Soviet Union and EU)
    )
    long_dist_pipeline_transport_cost: float = 1.0  # USD/kgH2
    # Which regions trade with which ones? (to[from])
    intraregional_trade_matrix: dict[str, Optional[list[str]]] = field(
        default_factory=lambda: {
            "Africa": ["Western Europe"],
            "Australia": None,
            "Canada": ["United States of America"],
            "China": ["South Korea", "Former Soviet Union", "Other Developing Asia"],
            "Central and South America": None,
            "Eastern Europe": ["Western Europe", "Former Soviet Union", "Middle East"],
            "Former Soviet Union": [
                "China",
                "Eastern Europe",
                "Middle East",
                "Other Developing Asia",
                "South Korea",
                "Western Europe",
            ],
            "Rest of World": None,
            "India": ["Middle East"],
            "Japan": None,
            "Middle East": ["Eastern Europe", "Former Soviet Union", "India", "Other Developing Asia"],
            "Mexico": ["United States of America"],
            "Other Developing Asia": ["China", "Former Soviet Union", "Middle East"],
            "South Korea": ["China", "Former Soviet Union"],
            "United Kingdom": ["Western Europe"],
            "United States of America": ["Canada", "Mexico"],
            "Western Europe": ["Eastern Europe", "United Kingdom", "Africa", "Former Soviet Union"],
        }
    )

    # === Infrastructure and Transportation ===
    include_infrastructure_cost: bool = True  # Build new infrastructure to connect new plants
    include_transport_cost: bool = True  # Move iron ore and steel from supplier to demand
    # Commodity-specific transportation costs (USD/tonne/km)
    # Note: Scrap neglected for simplicity
    transportation_cost_per_km_per_ton: dict[str, float] = field(
        default_factory=lambda: {
            "iron_mine_to_plant": 0.013,  # iron ore and pellets
            "iron_to_steel_plant": 0.015,  # hot metal, pig iron, DRI, HBI
            "steel_to_demand": 0.019,  # liquid steel and steel products
        }
    )

    # === Feasibility and Land Cover ===
    # Feasibility mask parameters
    max_altitude: float = 1500.0  # meters
    max_slope: float = 2.0  # degrees
    max_latitude: float = 70.0  # degrees
    # Land Use Land Cover cost on -> building on a spot with forests is more expensive than on bare land
    include_lulc_cost: bool = True
    # Factor of CAPEX within [1,2]; the lower the more suitable for steel and iron production
    land_cover_factor: dict[str, float] = field(
        default_factory=lambda: {
            "Cropland": 1.1,
            "Cropland Herbaceous": 1.1,
            "Cropland Tree/Shrub": 2,
            "Mosaic Cropland": 1.1,
            "Mosaic Natural Vegetation": 1.2,
            "Tree Cover": 2,
            "Mosaic Tree and Shrubland": 1.5,
            "Mosaic Herbaceous": 1.1,
            "Shrubland": 1.2,
            "Grassland": 1.2,
            "Lichens and Mosses": 2,
            "Sparse Vegetation": 1,
            "Shrub Cover": 1.5,
            "Urban": 1.5,
            "Bare Areas": 1,
            "Water": 2,
            "Snow and Ice": 2,
        }
    )

    # === Outgoing cashflow estimate (to build a new plant at a certain location) ===
    priority_pct: int = 5  # Percentage of global grid points selected as priority locations for business opportunities
    iron_ore_steel_ratio: float = 1.6  # Amount of iron ore needed to produce 1 unit of steel
    share_iron_vs_steel: dict[str, dict[str, float]] = field(
        default_factory=lambda: {
            "iron": {
                "capex_share": 0.7,  # %
                "energy_consumption_per_t": 3.0,  # MWh/t
            },
            "steel": {
                "capex_share": 0.3,  # %
                "energy_consumption_per_t": 1.0,  # MWh/t
            },
        }
    )

    # === Other ===
    random_seed: int = 42  # Seed for random number generation to ensure reproducibility


@dataclass
class SimulationConfig:
    """A pure, self-contained blueprint for a simulation run."""

    # === Core Parameters & Paths (NO DEFAULTS - must be explicitly provided) ===
    start_year: Year
    end_year: Year
    master_excel_path: Path
    output_dir: Path

    # Technology availability settings
    # Default to None for backward compatibility - will be auto-populated in __post_init__
    # After initialization, this will never be None
    technology_settings: Optional[TechSettingsMap] = None

    # === Trade Module Parameters ===
    # Solver configuration
    lp_epsilon: float = 1e-3  # LP solver epsilon
    capacity_limit: float = 0.95
    soft_minimum_capacity_percentage: float = 0.6
    minimum_active_utilisation_rate: float = 0.01
    minimum_margin: float = 0.5
    hot_metal_radius: float = 5.0  # km - radius for allocation model
    # Products
    primary_products: list[str] = field(
        default_factory=lambda: [
            "steel",
            "iron",
            "dri_mid",
            "dri_high",
            "dri_low",
            "hbi_low",
            "hot_metal",
            "hbi_mid",
            "hbi_high",
            "pig_iron",
            "liquid_steel",
            "io_low",
            "io_mid",
            "io_high",
            "catalytic_sinter",
            "liquid_iron",
            "electrolytic_iron",
        ]
    )
    closely_allocated_products: list[str] = field(
        default_factory=lambda: [
            "dri_high",
            "dri_mid",
            "dri_low",
            "hot_metal",
            "liquid_iron",
        ]
    )
    distantly_allocated_products: list[str] = field(
        default_factory=lambda: ["hbi_high", "hbi_mid", "hbi_low", "pig_iron", "electrolytic_iron"]
    )

    # === Plant Agent Module Parameters ===
    probabilistic_agents: bool = True  # Probabilitstic (mimick human decision-making) vs deterministic approach
    plant_lifetime: int = 20  # Years

    # Statuses of furnace groups
    active_statuses: list[str] = field(
        default_factory=lambda: [
            "operating",
            "operating pre-retirement",
            "operating switching technology",
        ]
    )
    announced_statuses: list[str] = field(
        default_factory=lambda: ["announced", "construction", "construction switching technology"]
    )

    # Financial parameters
    equity_share: float = 0.2  # 20%
    global_risk_free_rate: float = 0.0209  # 2.09% risk-free rate

    # Price increase when demand exceeds supply
    steel_price_buffer: float = 200.0  # USD/tonne - buffer above highest cost curve price when demand exceeds supply
    iron_price_buffer: float = 200.0  # USD/tonne - buffer above highest cost curve price when demand exceeds supply

    # Capacity
    ## Furnace group capacity expansion size and initial capacity of new plants (in tonnes)
    expanded_capacity: float = 2.5 * MT_TO_T
    ## Capacity limits for switching and expanding existing furnace groups (in tonnes)
    capacity_limit_iron: float = 100 * MT_TO_T
    capacity_limit_steel: float = 100 * MT_TO_T
    ## Proportion of new capacity from new plants vs expansions of existing plants
    new_capacity_share_from_new_plants: float = 0.4  # 40% of new capacity comes from new plants, 60% from expansions

    # === Geospatial Module Parameters ===
    # Best locations for new plants
    geo_config: GeoConfig = field(default_factory=GeoConfig)
    # New plant opening
    consideration_time: int = (
        3  # Minimum number of years a considered business opportunity needs to be NPV-positive before being announced
    )
    construction_time: int = 4  # Years it takes to construct a plant after it has been announced
    probability_of_construction: float = (
        0.9 if probabilistic_agents else 1
    )  # Probability of a plant being constructed after being announced
    probability_of_announcement: float = (
        0.7 if probabilistic_agents else 1
    )  # Probability of a plant being announced after being considered - given a history of positive NPVs of at least `consideration_time` years
    top_n_loctechs_as_business_op: int = 15  # Number of top location-technology combinations to consider as business
    # opportunities per product per year (e.g., 5 for steel and 5 for iron = 10 total)

    # === Scenario and Policy Settings ===
    chosen_demand_scenario: str = "BAU"
    chosen_grid_emissions_scenario: str = "Business As Usual"
    scrap_generation_scenario: str = "business_as_usual"
    chosen_emissions_boundary_for_carbon_costs: str = "responsible_steel"
    use_iron_ore_premiums: bool = True
    green_steel_emissions_limit: float = 0.4  # in tCO2/tsteel
    # Use InitVar to accept but not store deprecated parameter for backward compatibility
    global_bf_ban: InitVar[bool] = None
    include_tariffs: bool = True  # Whether to include tariffs in trade modeling

    # === Optional paths ===
    # Input data (for locating fixtures)
    data_dir: Optional[Path] = None  # If None, will use ./data as default
    # Outputs
    plots_dir: Optional[Path] = None  # If None, will be set to output_dir/plots
    geo_plots_dir: Optional[Path] = None  # If None, will be set to plots_dir/GEO
    pam_plots_dir: Optional[Path] = None  # If None, will be set to plots_dir/PAM
    tm_output_dir: Path | None = None  # Computed Trade Module attributes (set in __post_init__)
    # Geo Data Paths (for geospatial calculations) - only needed for specific geo calculations and provided by the
    # calling code when needed
    terrain_nc_path: Optional[Path] = None
    land_cover_tif_path: Optional[Path] = None
    rail_distance_nc_path: Optional[Path] = None
    countries_shapefile_dir: Optional[Path] = None
    disputed_areas_shapefile_dir: Optional[Path] = None
    landtype_percentage_nc_path: Optional[Path] = None
    baseload_power_sim_dir: Optional[Path] = None
    feasibility_mask_path: Optional[Path] = None

    # === Data Settings ===
    use_master_excel: bool = False
    steel_plant_gem_data_year: int = 2025
    production_gem_data_years: list[int] = field(default_factory=lambda: list(range(2019, 2023)))
    excel_reader_start_year: int = 2020
    excel_reader_end_year: int = 2050
    demand_sheet_name: str = "Steel_Demand_Chris Bataille"

    # === Other ===
    # Verbosity
    log_level: int = logging.DEBUG
    # Repository (lazy-loaded, not serialized)
    _repository: Optional["Repository"] = field(default=None, init=False, repr=False)
    _json_repository: Optional[Any] = field(default=None, init=False, repr=False)

    def __repr__(self):
        # Exclude _repository from repr to avoid circular references
        repr_dict = {k: v for k, v in self.__dict__.items() if not k.startswith("_")}
        return json.dumps(repr_dict, indent=4, default=str)

    @property
    def repository(self) -> "Repository":
        """Lazy-load repository based on data source."""
        if self._repository is None:
            self._repository = self._create_repository()
        return self._repository

    def _create_repository(self) -> "Repository":
        """Create repository based on available data sources."""
        from .adapters.repositories import JsonRepository, InMemoryRepository
        from .domain.models import PlantGroup

        if self.data_dir:
            # Load from prepared JSON data
            fixtures_dir = self.data_dir / "fixtures"
            if not fixtures_dir.exists():
                raise ValueError(f"Fixtures directory not found: {fixtures_dir}")

            logger.info(f"Loading repository from fixtures: {fixtures_dir}")

            json_repo = JsonRepository(
                plant_lifetime=self.plant_lifetime,
                plants_path=fixtures_dir / "plants.json",
                demand_centers_path=fixtures_dir / "demand_centers.json",
                suppliers_path=fixtures_dir / "suppliers.json",
                plant_groups_path=fixtures_dir / "plant_groups.json",
                trade_tariffs_path=fixtures_dir / "tariffs.json",
                subsidies_path=fixtures_dir / "subsidies.json",
                carbon_costs_path=fixtures_dir / "carbon_costs.json",
                primary_feedstocks_path=fixtures_dir / "primary_feedstocks.json",
                input_costs_path=fixtures_dir / "input_costs.json",
                region_emissivity_path=fixtures_dir / "region_emissivity.json",
                capex_path=fixtures_dir / "capex.json",
                cost_of_capital_path=fixtures_dir / "cost_of_capital.json",
                legal_process_connectors_path=fixtures_dir / "legal_process_connectors.json",
                country_mappings_path=fixtures_dir / "country_mappings.json",
                hydrogen_efficiency_path=fixtures_dir / "hydrogen_efficiency.json",
                hydrogen_capex_opex_path=fixtures_dir / "hydrogen_capex_opex.json",
                railway_costs_path=fixtures_dir / "railway_costs.json",
                transport_emissions_path=fixtures_dir / "transport_emissions.json",
                biomass_availability_path=fixtures_dir / "biomass_availability.json",
                technology_emission_factors_path=fixtures_dir / "technology_emission_factors.json",
                current_simulation_year=int(self.start_year),
            )

            # Create in-memory repository and populate from JSON
            repository = InMemoryRepository()
            repository.plants.add_list(json_repo.plants.list())
            repository.demand_centers.add_list(json_repo.demand_centers.list())
            repository.suppliers.add_list(json_repo.suppliers.list())

            # Plant groups setup
            plant_groups = []
            for pg in json_repo.plant_groups.list():
                plant_list = [repository.plants.get(p.plant_id) for p in pg.plants]
                plant_groups.append(PlantGroup(plant_group_id=pg.plant_group_id, plants=plant_list))
            repository.plant_groups.add_list(plant_groups)
            repository.plant_groups.add(PlantGroup(plant_group_id="indi", plants=[]))

            repository.trade_tariffs.add_list(json_repo.trade_tariffs.list())

            # Store the json_repo reference for later use
            self._json_repository = json_repo

            return repository

        elif self.master_excel_path:
            # TODO: Implement Excel-based repository creation
            # This would involve reading from the master Excel file
            # and creating an in-memory repository
            raise NotImplementedError("Excel-based repository creation not yet implemented")
        else:
            raise ValueError("Either data_dir or master_excel_path must be provided")

    def __post_init__(self, global_bf_ban: Optional[bool] = None):
        """Initialize derived paths and ensure directories exist."""
        # Ensure technology_settings is always available (required for the system to function)
        if self.technology_settings is None:
            self.technology_settings = get_default_technology_settings()

        # Handle deprecated parameter - preserve semantics by translating to technology_settings
        if global_bf_ban is not None:
            import warnings

            warnings.warn(
                "global_bf_ban is deprecated and will be removed in a future version. "
                "Use technology_settings to control technology availability instead.",
                DeprecationWarning,
                stacklevel=2,
            )

            # Preserve semantics: translate global_bf_ban=True to technology_settings
            if global_bf_ban:
                # Ensure BF is disabled when global_bf_ban=True
                from steelo.simulation_types import TechnologySettings

                start_year_int = self.start_year.value if hasattr(self.start_year, "value") else self.start_year
                self.technology_settings["BF"] = TechnologySettings(
                    allowed=False, from_year=start_year_int, to_year=None
                )
                # Also disable BFBOF since it requires BF
                self.technology_settings["BFBOF"] = TechnologySettings(
                    allowed=False, from_year=start_year_int, to_year=None
                )

        # Convert strings to Path objects if needed
        self.output_dir = Path(self.output_dir)
        self.master_excel_path = Path(self.master_excel_path)

        # Create output directory
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Set default plot directories if not provided
        if self.plots_dir is None:
            self.plots_dir = self.output_dir / "plots"
        else:
            self.plots_dir = Path(self.plots_dir)
        self.plots_dir.mkdir(parents=True, exist_ok=True)

        if self.geo_plots_dir is None:
            self.geo_plots_dir = self.plots_dir / "GEO"
        else:
            self.geo_plots_dir = Path(self.geo_plots_dir)
        self.geo_plots_dir.mkdir(parents=True, exist_ok=True)

        if self.pam_plots_dir is None:
            self.pam_plots_dir = self.plots_dir / "PAM"
        else:
            self.pam_plots_dir = Path(self.pam_plots_dir)
        self.pam_plots_dir.mkdir(parents=True, exist_ok=True)

        # Create TM output directory
        self.tm_output_dir = self.output_dir / "TM"
        self.tm_output_dir.mkdir(parents=True, exist_ok=True)

        # Convert optional geo paths to Path objects if provided
        if self.terrain_nc_path is not None:
            self.terrain_nc_path = Path(self.terrain_nc_path)
        if self.land_cover_tif_path is not None:
            self.land_cover_tif_path = Path(self.land_cover_tif_path)
        if self.rail_distance_nc_path is not None:
            self.rail_distance_nc_path = Path(self.rail_distance_nc_path)
        if self.countries_shapefile_dir is not None:
            self.countries_shapefile_dir = Path(self.countries_shapefile_dir)
        if self.disputed_areas_shapefile_dir is not None:
            self.disputed_areas_shapefile_dir = Path(self.disputed_areas_shapefile_dir)
        if self.landtype_percentage_nc_path is not None:
            self.landtype_percentage_nc_path = Path(self.landtype_percentage_nc_path)

    @classmethod
    def from_data_directory(
        cls,
        *,
        start_year: Year,
        end_year: Year,
        data_dir: Path,
        output_dir: Path,
        master_excel_path: Optional[Path] = None,
        **kwargs: Any,
    ) -> "SimulationConfig":
        """
        Factory method to create a SimulationConfig with all default paths configured
        based on a data directory structure.

        This method automatically detects and sets up all geo data paths if the files exist,
        making it easy to use from CLI, notebooks, and Django without having to specify
        all individual file paths.

        Args:
            start_year: First year of simulation
            end_year: Last year of simulation
            data_dir: Directory containing the data files (e.g., /path/to/steel-model/data)
            output_dir: Directory where simulation outputs should be written
            master_excel_path: Path to master Excel file. If None, defaults to data_dir/master_input.xlsx
            **kwargs: Additional SimulationConfig parameters to override defaults

        Returns:
            Fully configured SimulationConfig instance
        """
        from .data.path_resolver import DataPathResolver

        data_dir = Path(data_dir)
        output_dir = Path(output_dir)

        # Set default master excel path if not provided
        if master_excel_path is None:
            master_excel_path = data_dir / "master_input.xlsx"

        # Use DataPathResolver to get all the standard data paths
        path_resolver = DataPathResolver(data_directory=data_dir)

        # Validate that required files exist
        path_resolver.validate_required_files(["plants.json", "demand_centers.json"])

        # Build the config with detected paths
        config_data: dict[str, Any] = {
            "start_year": start_year,
            "end_year": end_year,
            "master_excel_path": master_excel_path,
            "output_dir": output_dir,
            "data_dir": data_dir,
        }

        # Add geo data paths if the files exist
        if path_resolver.terrain_nc_path.exists():
            config_data["terrain_nc_path"] = path_resolver.terrain_nc_path

        if path_resolver.rail_distance_nc_path.exists():
            config_data["rail_distance_nc_path"] = path_resolver.rail_distance_nc_path

        if path_resolver.landtype_percentage_nc_path.exists():
            config_data["landtype_percentage_nc_path"] = path_resolver.landtype_percentage_nc_path

        if path_resolver.countries_shapefile_dir.exists():
            config_data["countries_shapefile_dir"] = path_resolver.countries_shapefile_dir

        # Check for disputed areas - might be ne_10m instead of ne_110m
        disputed_areas_110m = data_dir / "ne_110m_admin_0_disputed_areas"
        disputed_areas_10m = data_dir / "ne_10m_admin_0_disputed_areas"
        if disputed_areas_10m.exists():
            config_data["disputed_areas_shapefile_dir"] = disputed_areas_10m
        elif disputed_areas_110m.exists():
            config_data["disputed_areas_shapefile_dir"] = disputed_areas_110m

        # Add baseload power and feasibility mask paths if they exist
        # Check if baseload_power_sim_dir is provided in kwargs (custom path from CLI)
        if "baseload_power_sim_dir" in kwargs:
            # Use the custom path provided via CLI
            config_data["baseload_power_sim_dir"] = kwargs["baseload_power_sim_dir"]
        elif path_resolver.baseload_power_sim_dir.exists():
            # Use the default path from data directory
            config_data["baseload_power_sim_dir"] = path_resolver.baseload_power_sim_dir

        if path_resolver.feasibility_mask_path.exists():
            config_data["feasibility_mask_path"] = path_resolver.feasibility_mask_path

        # Override with any additional kwargs (except baseload_power_sim_dir which we already handled)
        kwargs_without_baseload = {k: v for k, v in kwargs.items() if k != "baseload_power_sim_dir"}
        config_data.update(kwargs_without_baseload)

        # Create a properly typed config dictionary
        typed_config: dict[str, Any] = {
            "start_year": start_year,  # Already a Year object
            "end_year": end_year,  # Already a Year object
            "master_excel_path": config_data["master_excel_path"],  # Already a Path
            "output_dir": config_data["output_dir"],  # Already a Path
        }

        # Add technology_settings if not provided in kwargs
        if "technology_settings" not in config_data:
            # Load from technologies.json instead of hardcoded defaults
            try:
                from steelo.adapters.repositories.technology_repository import TechnologyRepository
                from steelo.simulation_types import TechnologySettings

                # Load technologies from the prepared data
                tech_repo = TechnologyRepository(data_dir)
                technologies = tech_repo.load_technologies()

                # Convert to TechnologySettings map using defaults from Excel
                tech_settings = {}
                for slug, tech_data in technologies.items():
                    normalized_code = tech_data["normalized_code"]
                    tech_settings[normalized_code] = TechnologySettings(
                        allowed=tech_data.get("allowed", True),  # Use Excel default
                        from_year=tech_data.get("from_year", 2025),  # Use Excel default
                        to_year=tech_data.get("to_year"),  # Use Excel default (None if not specified)
                    )

                typed_config["technology_settings"] = tech_settings

            except Exception as e:
                # Log the error and provide minimal fallback
                import logging

                logger = logging.getLogger(__name__)
                logger.error(f"Failed to load technologies.json from {data_dir}: {e}")
                logger.warning("Falling back to minimal hardcoded technology settings")

                from steelo.simulation_types import TechnologySettings

                # Minimal fallback - just the most basic technologies
                typed_config["technology_settings"] = {
                    "BF": TechnologySettings(allowed=True, from_year=2025, to_year=None),
                    "BFBOF": TechnologySettings(allowed=True, from_year=2025, to_year=None),
                    "BOF": TechnologySettings(allowed=True, from_year=2025, to_year=None),
                    "EAF": TechnologySettings(allowed=True, from_year=2025, to_year=None),
                }

        # Add optional paths if they exist (they're already Path objects)
        if "data_dir" in config_data:
            typed_config["data_dir"] = config_data["data_dir"]
        if "terrain_nc_path" in config_data:
            typed_config["terrain_nc_path"] = config_data["terrain_nc_path"]
        if "rail_distance_nc_path" in config_data:
            typed_config["rail_distance_nc_path"] = config_data["rail_distance_nc_path"]
        if "landtype_percentage_nc_path" in config_data:
            typed_config["landtype_percentage_nc_path"] = config_data["landtype_percentage_nc_path"]
        if "countries_shapefile_dir" in config_data:
            typed_config["countries_shapefile_dir"] = config_data["countries_shapefile_dir"]
        if "disputed_areas_shapefile_dir" in config_data:
            typed_config["disputed_areas_shapefile_dir"] = config_data["disputed_areas_shapefile_dir"]

        # Add any other kwargs that were passed in
        for key, value in config_data.items():
            if key not in typed_config:
                typed_config[key] = value

        return cls(**typed_config)

    @classmethod
    def for_testing(
        cls,
        repository: "Repository",
        start_year: Year = Year(2025),
        end_year: Year = Year(2027),
        output_dir: Optional[Path] = None,
        **kwargs: Any,
    ) -> "SimulationConfig":
        """
        Create config with injected repository for testing.

        This factory method allows tests to provide their own repository
        implementation with test data, bypassing the normal data loading.

        Args:
            repository: Pre-populated repository instance
            start_year: First year of simulation (default: 2025)
            end_year: Last year of simulation (default: 2027)
            output_dir: Output directory (default: ./test_output)
            **kwargs: Additional SimulationConfig parameters

        Returns:
            SimulationConfig instance with the injected repository
        """
        if output_dir is None:
            output_dir = Path("./test_output")

        # Create a minimal master excel path for compatibility
        master_excel_path = output_dir / "test_master.xlsx"

        # Provide default technology settings if not specified
        if "technology_settings" not in kwargs:
            kwargs["technology_settings"] = get_default_technology_settings()

        config = cls(
            start_year=start_year,
            end_year=end_year,
            output_dir=output_dir,
            master_excel_path=master_excel_path,
            **kwargs,
        )

        # Inject the test repository
        config._repository = repository

        return config

    @classmethod
    def from_master_excel(
        cls,
        master_excel_path: Path,
        output_dir: Path,
        start_year: Year,
        end_year: Year,
        **kwargs: Any,
    ) -> "SimulationConfig":
        """
        Create config from master Excel file.

        This factory method extracts data from the master Excel file into a temporary
        directory and then creates a configuration using from_data_directory.

        Args:
            master_excel_path: Path to master Excel file
            output_dir: Directory for simulation outputs
            start_year: First year of simulation
            end_year: Last year of simulation
            **kwargs: Additional SimulationConfig parameters

        Returns:
            SimulationConfig instance with data extracted from Excel
        """
        import tempfile
        from .adapters.dataprocessing.master_excel_reader import MasterExcelReader

        # Create temporary directory for prepared data
        temp_dir = Path(tempfile.mkdtemp(prefix="master_excel_data_"))

        try:
            # Extract data from master Excel
            with MasterExcelReader(master_excel_path, output_dir=temp_dir) as reader:
                output_paths = reader.get_output_paths()

            # Create fixtures directory for compatibility
            fixtures_dir = temp_dir / "fixtures"
            fixtures_dir.mkdir(exist_ok=True)

            # Create dummy required files to satisfy validation
            dummy_files = ["plants.json", "demand_centers.json"]
            for dummy_file in dummy_files:
                dummy_path = fixtures_dir / dummy_file
                if not dummy_path.exists():
                    # Create minimal valid JSON
                    import json

                    dummy_path.write_text(json.dumps([]))

            # Move extracted files to expected locations
            # This is a simplified approach - in production, you might want more sophisticated mapping
            import shutil

            for field_name, file_path in output_paths.items():
                # Map extraction results to expected file names
                if field_name == "steel_plants_csv_path":
                    # Keep the CSV for preprocessing
                    continue
                elif field_name == "tech_switches_csv_path":
                    target_path = fixtures_dir / "tech_switches_allowed.csv"
                    if file_path.exists():
                        shutil.copy2(file_path, target_path)
                elif field_name == "railway_costs_json_path":
                    target_path = fixtures_dir / "railway_costs.json"
                    if file_path.exists():
                        shutil.copy2(file_path, target_path)
                # Add more mappings as needed

            # Use from_data_directory to create the final config
            config = cls.from_data_directory(
                start_year=start_year,
                end_year=end_year,
                data_dir=temp_dir,
                output_dir=output_dir,
                master_excel_path=master_excel_path,
                **kwargs,
            )

            # Override any paths with extracted data
            for field_name, file_path in output_paths.items():
                if hasattr(config, field_name):
                    setattr(config, field_name, file_path)

            return config

        except Exception as e:
            # Cleanup temp directory on error
            import shutil

            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
            raise RuntimeError(f"Failed to create config from master Excel: {e}") from e


@dataclass
class Progress:
    start_year: Year | None = None
    end_year: Year | None = None
    current_year: Year | None = None


class SimulationRunner:
    """
    A class that orchestrates the full simulation process.
    This can be used from both CLI and Django interfaces.
    """

    temp_dir: Optional[Path] = None

    def __init__(
        self,
        config: SimulationConfig,
        bus: MessageBus,  # No longer optional
        progress_callback: Callable = lambda progress: None,
        modelrun_id: Optional[int] = None,
    ) -> None:
        self.config = config
        self.bus = bus  # Directly assign the provided bus
        self.progress_callback = progress_callback
        self.modelrun_id = modelrun_id
        self.temp_dir = Path(tempfile.mkdtemp(prefix="steel_sim_static_"))
        self._update_geo_paths_for_static_files()
        self.data_collector = DataCollector(
            world_plant_groups=self.bus.uow.plant_groups.list(), env=self.bus.env, output_dir=self.config.output_dir
        )
        # Initialize furnace breakdown logger for better debugging
        self.furnace_logger = FurnaceBreakdownLogger()

        # Note: Logging is now configured in bootstrap_simulation() before data loading

    def _update_geo_paths_for_static_files(self) -> None:
        """Update geo_paths to use temporary directory for static files."""
        if hasattr(self.bus.env, "geo_paths") and self.bus.env.geo_paths and self.temp_dir is not None:
            from dataclasses import replace

            self.bus.env.geo_paths = replace(self.bus.env.geo_paths, static_layers_dir=self.temp_dir)

    def _cleanup_temp_dir(self) -> None:
        """Clean up temporary directory and its contents."""
        if self.temp_dir and self.temp_dir.exists():
            try:
                shutil.rmtree(self.temp_dir)
                logger.info(f"Cleaned up temporary directory: {self.temp_dir}")
            except Exception as e:
                logger.warning(f"Failed to clean up temporary directory {self.temp_dir}: {e}")

    def run(self):
        bus = self.bus
        data_collector = self.data_collector
        commands = {}
        start_year = int(self.config.start_year)
        end_year = int(self.config.end_year)

        # Reset memory tracking for this simulation run
        _reset_memory_tracking()

        # Initialize memory profiler for leak detection
        memory_tracker = MemoryTracker()

        # Performance logging: simulation start
        simulation_start_time = time.time()
        if self.modelrun_id is not None:
            logger.info(f"operation=simulation_start model_run_id={self.modelrun_id}")
        else:
            logger.info("operation=simulation_start")
        _log_memory_usage("memory_snapshot", stage="simulation_start")
        memory_tracker.checkpoint("simulation_start")

        plant_groups = {}
        for plant in bus.uow.plants.list():
            plant_groups[plant.ultimate_plant_group] = plant_groups.get(plant.ultimate_plant_group, []) + [plant]
        for pg_id, plants in plant_groups.items():
            new_plant_group = PlantGroup(plant_group_id=pg_id, plants=plants)
            bus.uow.plant_groups.add(new_plant_group)

        # Report initial progress before processing any simulation year
        progress = Progress(start_year=start_year, end_year=end_year, current_year=start_year - 1)
        self.progress_callback(progress)

        for i in range(start_year, end_year + 1):
            # Performance logging: year start
            year_start_time = time.time()
            logger.info(f"operation=year_start year={i}")
            memory_tracker.checkpoint("year_start", year=i)

            # Check for cancellation if modelrun_id is provided
            if self.modelrun_id is not None:
                try:
                    # Import Django components only when needed
                    from steeloweb.models import ModelRun
                    from django.utils import timezone

                    modelrun = ModelRun.objects.get(pk=self.modelrun_id)
                    if modelrun.state in [ModelRun.RunState.CANCELLING, ModelRun.RunState.CANCELLED]:
                        # Simulation was cancelled - stop processing
                        if modelrun.state == ModelRun.RunState.CANCELLING:
                            # Update state to cancelled if still in cancelling
                            modelrun.state = ModelRun.RunState.CANCELLED
                            modelrun.finished_at = timezone.now()
                            modelrun.save()

                        # Return early with cancellation status
                        logger.info(f"operation=simulation_cancelled last_year={i - 1}")
                        return {"status": "cancelled", "last_year": i - 1}
                except Exception as e:
                    # If Django is not available or there's an error, continue without cancellation check
                    logging.info(f"Warning: Could not check for cancellation: {e}")

            # Report progress for this year
            progress = Progress(start_year=start_year, end_year=end_year, current_year=i)
            self.progress_callback(progress)

            # Set the environment year to match the loop iteration
            bus.env.year = Year(i)

            # Set demand and plant costs for the year
            bus.env.calculate_demand()
            bus.env.pass_fopex_for_iso3_to_plants(bus.uow.plants.list())
            # bus.env.set_capex_and_debt_in_furnace_groups(bus.uow.plants.list())  # no need for this because we are not using learning rate - capex and debt (with subsidy) set when fgs are renovated, changed or created
            bus.env.update_furnace_capex_renovation_share(bus.uow.plants.list())
            capped_hydrogen_cost_dict = (
                bus.env.calculate_capped_hydrogen_costs_per_country()
            )  # Calculate for all countries once per year (iso3 -> cost)
            logging.info(f"\n Steel demand in year {bus.env.year}: \t {bus.env.current_demand * T_TO_KT:,.0f} kt \n")

            # Initialise OPEX subsidies for Year 1 (subsequent years handled by finalise_iteration)
            if i == start_year:
                for plant in bus.uow.plants.list():
                    for fg in plant.furnace_groups:
                        all_opex_subs = bus.env.opex_subsidies.get(plant.location.iso3, {}).get(fg.technology.name, [])
                        active_opex_subs = filter_subsidies_for_year(all_opex_subs, bus.env.year)
                        fg.applied_subsidies["opex"] = active_opex_subs

            for plant in bus.uow.plants.list():
                plant.update_furnace_tech_unit_fopex()
                plant.update_furnace_hydrogen_costs(capped_hydrogen_cost_dict)

                # Apply H2/electricity subsidies to energy_costs (after H2 price update)
                for fg in plant.furnace_groups:
                    all_h2_subs = bus.env.hydrogen_subsidies.get(plant.location.iso3, {}).get(fg.technology.name, [])
                    all_elec_subs = bus.env.electricity_subsidies.get(plant.location.iso3, {}).get(
                        fg.technology.name, []
                    )
                    active_h2_subs = filter_subsidies_for_year(all_h2_subs, bus.env.year)
                    active_elec_subs = filter_subsidies_for_year(all_elec_subs, bus.env.year)

                    if active_h2_subs or active_elec_subs:
                        h2_before = fg.energy_costs.get("hydrogen", 0.0)
                        elec_before = fg.energy_costs.get("electricity", 0.0)
                        subsidised_costs, no_subsidy_prices = get_subsidised_energy_costs(
                            fg.energy_costs, active_h2_subs, active_elec_subs
                        )
                        fg.set_subsidised_energy_costs(
                            subsidised_costs, no_subsidy_prices, active_h2_subs, active_elec_subs
                        )
                        logging.debug(
                            f"[H2/ELEC SUBS] {plant.location.iso3}/{fg.technology.name} FG:{fg.furnace_group_id} "
                            f"Year={bus.env.year} | H2: ${h2_before:.2f} -> ${subsidised_costs.get('hydrogen', 0):.2f}/t | "
                            f"Elec: ${elec_before:.6f} -> ${subsidised_costs.get('electricity', 0):.6f}/kWh | "
                            f"Subs: {len(active_h2_subs)} H2, {len(active_elec_subs)} elec"
                        )

                # Set carbon costs for the plant based on its location
                if plant.location.iso3 in bus.env.carbon_costs:
                    plant.set_carbon_cost_series(carbon_cost_series=bus.env.carbon_costs[plant.location.iso3])
                else:
                    # Use fallback carbon costs for missing ISO3 codes
                    fallback_iso3 = None
                    if plant.location.iso3 == "PRI":  # Puerto Rico -> USA
                        fallback_iso3 = "USA"

                    if fallback_iso3 and fallback_iso3 in bus.env.carbon_costs:
                        logging.info(
                            f"Warning: No carbon cost data for ISO3 code {plant.location.iso3}, using {fallback_iso3} as fallback"
                        )
                        plant.set_carbon_cost_series(carbon_cost_series=bus.env.carbon_costs[fallback_iso3])
                    else:
                        logging.info(
                            f"Warning: No carbon cost data for ISO3 code {plant.location.iso3}, using zero carbon costs"
                        )
                        plant.set_carbon_cost_series(carbon_cost_series={})

            # Transition furnace groups from construction to operating status when their start year arrives
            # This must happen BEFORE AllocationModel runs so that newly operational plants get their BOMs populated
            for plant in bus.uow.plants.list():
                for fg in plant.furnace_groups:
                    if (
                        bus.env.year == fg.lifetime.time_frame.start
                        and fg.status.lower() in bus.env.config.announced_statuses
                    ):
                        if fg.status.lower() != "construction switching technology":
                            fg.status = "operating"
                            logging.info(
                                f"Transitioned furnace group {fg.furnace_group_id} from construction to operating"
                            )

            Simulation(bus=bus, economic_model=AllocationModel()).run_simulation()
            Simulation(bus=bus, economic_model=PlantAgentsModel()).run_simulation()
            Simulation(bus=bus, economic_model=GeospatialModel()).run_simulation()
            with LoggingConfig.simulation_logging("DebugLogging"):
                data_collector.collect(
                    world_plant_list=bus.uow.plants.list(),
                    world_plant_groups=bus.uow.plant_groups.list(),
                    year=bus.env.year,
                )

                # Collect market prices for iron and steel
                prices = data_collector.collect_market_iron_steel_price()
                data_collector.trace_price[bus.env.year] = prices
                logger.info(
                    f"Year {bus.env.year} prices - Steel: ${prices['steel']:.2f}/t, Iron: ${prices['iron']:.2f}/t"
                )

            commands[bus.env.year] = bus.collect_commands()

            with LoggingConfig.simulation_logging("DebugLogging"):
                if LoggingConfig.FURNACE_GROUP_BREAKDOWN:
                    logging.info(f"========== FURNACE GROUP DEBUG - YEAR {bus.env.year} ==========\n")

                    # Use the new FurnaceBreakdownLogger for all plants
                    self.furnace_logger.log_all_furnace_groups(bus=bus, commands=commands)

                    logging.info(f"========== END DEBUG - YEAR {bus.env.year} ==========\n")

                if (event := IterationOver) is not None:
                    logging.info(f"Iron demand in year {bus.env.year}: \t {bus.env.iron_demand * T_TO_KT:,.0f} kt")

                    price = {
                        "steel": bus.env.extract_price_from_costcurve(demand=bus.env.current_demand, product="steel"),
                        "iron": bus.env.extract_price_from_costcurve(demand=bus.env.iron_demand, product="iron"),
                    }
                    logging.info(f"Price: {price}")

                    capacities = {
                        "steel": bus.env.cost_curve["steel"][-1],
                        "iron": bus.env.cost_curve["iron"][-1],
                    }
                    logging.info(f"Capacities: {capacities}")

                    # Check for duplicate furnace group names across all plants in the repository
                    furnace_group_names = []
                    for plant in bus.uow.plants.list():
                        for fg in plant.furnace_groups:
                            furnace_group_names.append(fg.furnace_group_id)
                    name_counts = Counter(furnace_group_names)
                    duplicates = {name: count for name, count in name_counts.items() if count > 1}
                    if duplicates:
                        duplicate_details = "\n".join(f"{name}: {count} times" for name, count in duplicates.items())
                        raise ValueError(f"Duplicate furnace group names found:\n{duplicate_details}")

                    # Increment year during the simulation period, not at the end
                    if bus.env.year == Year(self.config.end_year):
                        bus.handle(event(time_step_increment=0, iron_price=price["iron"]))
                    else:
                        bus.handle(event(time_step_increment=1, iron_price=price["iron"]))

            # Performance logging: year complete
            year_elapsed = time.time() - year_start_time
            logger.info(f"operation=year_complete year={i} duration_s={year_elapsed:.3f}")
            _log_memory_usage("memory_snapshot", stage="year_complete", year=i)
            memory_tracker.checkpoint("year_end", year=i)

        # Report completion once all years have been processed
        progress = Progress(start_year=start_year, end_year=end_year, current_year=end_year + 1)
        self.progress_callback(progress)

        # Postprocessing
        output_path = extract_and_process_stored_dataCollection(
            commands=commands,
            data_dir=self.config.output_dir / "TM",
            output_path=self.config.output_dir
            / "TM"
            / f"post_processed_{datetime.now().strftime('%Y-%m-%d %H-%M')}.csv",
            store=True,
        )
        plot_bar_chart_of_new_plants_by_status(data_collector.status_counts, plot_paths=bus.env.plot_paths)
        plot_map_of_new_plants_operating(data_collector.new_plant_locations, plot_paths=bus.env.plot_paths)
        generate_post_run_cap_prod_plots(
            file_path=output_path,
            capacity_limit=bus.env.config.capacity_limit,
            steel_demand=bus.env.current_demand,
            iron_demand=bus.env.iron_demand,
            plot_paths=bus.env.plot_paths,
            iso3_to_region_map=bus.env.country_mappings.iso3_to_region(),
        )

        # Export market prices to CSV and plot
        if data_collector.trace_price:
            import pandas as pd
            import matplotlib.pyplot as plt

            price_data = []
            for year, prices in sorted(data_collector.trace_price.items()):
                price_data.append(
                    {
                        "year": year,
                        "steel_price_usd_per_t": prices.get("steel", 0.0),
                        "iron_price_usd_per_t": prices.get("iron", 0.0),
                    }
                )

            price_df = pd.DataFrame(price_data)

            # Save CSV to output/data directory
            data_dir = self.config.output_dir / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            price_csv_path = data_dir / f"market_prices_{start_year}_{end_year}.csv"
            price_df.to_csv(price_csv_path, index=False)
            logger.info(f"Saved market prices to {price_csv_path}")

            # Create line plot of prices over time
            fig, ax = plt.subplots(figsize=(10, 6))
            ax.plot(
                price_df["year"],
                price_df["steel_price_usd_per_t"],
                marker="o",
                linewidth=2,
                label="Steel",
                color="#1f77b4",
            )
            ax.plot(
                price_df["year"],
                price_df["iron_price_usd_per_t"],
                marker="s",
                linewidth=2,
                label="Iron",
                color="#ff7f0e",
            )

            ax.set_xlabel("Year", fontsize=12)
            ax.set_ylabel("Price (USD/t)", fontsize=12)
            ax.set_title("Market Prices - Steel and Iron", fontsize=14, fontweight="bold")
            ax.legend(fontsize=11)
            ax.grid(True, alpha=0.3)

            # Format y-axis with commas for thousands
            ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f"${x:,.0f}"))

            plt.tight_layout()

            price_plot_path = bus.env.plot_paths.pam_plots_dir / f"market_prices_{start_year}_{end_year}.png"
            plt.savefig(price_plot_path, dpi=300, bbox_inches="tight")
            plt.close()
            logger.info(f"Saved market prices plot to {price_plot_path}")

        # Clean up temporary directory
        self._cleanup_temp_dir()

        # Performance logging: simulation complete
        total_elapsed = time.time() - simulation_start_time
        if self.modelrun_id is not None:
            logger.info(
                f"operation=simulation_complete model_run_id={self.modelrun_id} total_duration_s={total_elapsed:.3f}"
            )
        else:
            logger.info(f"operation=simulation_complete total_duration_s={total_elapsed:.3f}")
        _log_memory_usage("memory_snapshot", stage="simulation_end")
        memory_tracker.checkpoint("simulation_end")

        # Return collected results
        return {
            "price": data_collector.trace_price,
            "cost": data_collector.cost_breakdown,
            "capacity": data_collector.trace_capacity,
            "production": data_collector.trace_production,
        }
