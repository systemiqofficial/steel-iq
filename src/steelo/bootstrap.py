import inspect
import logging

from pathlib import Path
from typing import Optional, TYPE_CHECKING
from .service_layer import handlers, UnitOfWork, MessageBus, SimulationCheckpoint
from .domain.models import Environment, PlantGroup
from .adapters.repositories import JsonRepository, InMemoryRepository, Repository
from .data.path_resolver import DataPathResolver

if TYPE_CHECKING:
    from .simulation import SimulationConfig, SimulationRunner

logger = logging.getLogger(__name__)
# Set matplotlib font manager to only show errors
logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)


def validate_plant_lifetime_compatibility(config: "SimulationConfig", repository_json: "JsonRepository") -> None:
    """
    Validate that simulation configuration is compatible with dataset metadata.

    Logs informational messages about metadata usage and warns about potential issues.
    Raises ValueError if trying to use non-default lifetime with legacy data.
    """
    if hasattr(repository_json.plants, "metadata") and repository_json.plants.metadata:
        # Metadata exists - reconstruction is available
        metadata = repository_json.plants.metadata
        prep_lifetime = metadata.get_plant_lifetime_used()
        data_ref_year = metadata.get_data_reference_year()

        # Always log what we're doing
        logger.info(f"Dataset prepared with plant_lifetime={prep_lifetime}, data_reference_year={data_ref_year}")
        logger.info(
            f"Simulation configured with plant_lifetime={config.plant_lifetime}, start_year={config.start_year}"
        )

        # Warn if using different lifetime (expected and allowed)
        if config.plant_lifetime != prep_lifetime:
            logger.warning(
                f"Using plant_lifetime={config.plant_lifetime} "
                f"(dataset prepared with {prep_lifetime}). "
                f"Cycles will be recalculated from canonical age data."
            )

        # Info: data_reference_year vs simulation start is OK
        if int(config.start_year) != data_ref_year:
            years_offset = int(config.start_year) - data_ref_year
            logger.info(
                f"Simulation starts at {config.start_year} "
                f"(dataset reference year: {data_ref_year}). "
                f"Ages will be offset by {years_offset} years."
            )

        logger.info("✓ Metadata-based lifecycle reconstruction enabled")

    else:
        # Legacy data without metadata
        if config.plant_lifetime != 20:
            raise ValueError(
                f"Legacy data (no metadata) requires plant_lifetime=20. "
                f"Requested: {config.plant_lifetime}. "
                f"To use different lifetimes, regenerate data with metadata support."
            )
        logger.warning(
            "Using legacy data without metadata (plant_lifetime=20 only). "
            "Regenerate data to enable variable plant lifetime."
        )


def inject_dependencies(handler, dependencies):
    params = inspect.signature(handler).parameters
    deps = {name: dependency for name, dependency in dependencies.items() if name in params}
    return lambda message: handler(message, **deps)


def bootstrap(
    uow: UnitOfWork | None = None,
    env: Environment | None = None,
    cost_of_x_csv=None,  # Deprecated, kept for backward compatibility
    tech_switches_csv=None,
    config=None,
    checkpoint_dir: str = "checkpoints",
) -> MessageBus:
    """Creates a new MessageBus instance with all dependencies injected."""
    if not uow:
        uow = UnitOfWork()
    if not env:
        # Always use config-based initialization
        env = Environment(config=config, tech_switches_csv=tech_switches_csv)

    # Create checkpoint system
    checkpoint_system = SimulationCheckpoint(checkpoint_dir)

    dependencies = {"uow": uow, "env": env, "checkpoint_system": checkpoint_system}
    injected_event_handlers = {
        event_type: [inject_dependencies(handler, dependencies) for handler in event_handlers]
        for event_type, event_handlers in handlers.EVENT_HANDLERS.items()
    }
    injected_command_handlers = {
        command_type: inject_dependencies(handler, dependencies)
        for command_type, handler in handlers.COMMAND_HANDLERS.items()
    }
    return MessageBus(
        uow=uow, env=env, event_handlers=injected_event_handlers, command_handlers=injected_command_handlers
    )


def _load_secondary_feedstock_constraints(env, repository_json):
    """Load secondary feedstock constraints from biomass availability data (includes CO2 storage)."""
    import logging
    from .domain.models import SecondaryFeedstockConstraint
    from .domain import Year

    logger = logging.getLogger(__name__)
    constraints = []

    # Check if biomass availability data is available (this now includes CO2 storage)
    if hasattr(repository_json, "biomass_availability") and repository_json.biomass_availability:
        # Get country mapping for region-to-ISO3 conversion
        country_mapping = None
        if hasattr(repository_json, "country_mappings") and repository_json.country_mappings:
            mappings = repository_json.country_mappings.get_all()
            if mappings:
                # Create CountryMappingService from the list of mappings
                from .domain.models import CountryMappingService

                country_mapping = CountryMappingService(mappings)

        # Get all unique years from biomass data
        all_years = set()
        for item in repository_json.biomass_availability.list():
            all_years.add(item.year)

        # Aggregate constraints across all years
        aggregated_constraints = {}
        for year in sorted(all_years):
            year_constraints = repository_json.biomass_availability.get_constraints_for_year(year, country_mapping)
            for commodity, region_data in year_constraints.items():
                if commodity not in aggregated_constraints:
                    aggregated_constraints[commodity] = {}
                for region_tuple, year_dict in region_data.items():
                    if region_tuple not in aggregated_constraints[commodity]:
                        aggregated_constraints[commodity][region_tuple] = {}
                    aggregated_constraints[commodity][region_tuple].update(year_dict)

        # Convert to SecondaryFeedstockConstraint objects
        for commodity, regions in aggregated_constraints.items():
            for region_tuple, year_constraints in regions.items():
                constraint = SecondaryFeedstockConstraint(
                    secondary_feedstock_name=commodity,
                    region_iso3s=list(region_tuple),
                    maximum_constraint_per_year={Year(y): v for y, v in year_constraints.items()},
                )
                constraints.append(constraint)

        # Log breakdown of constraint types
        biomass_constraints = [c for c in constraints if "bio-pci" == c.secondary_feedstock_name]
        co2_constraints = [c for c in constraints if "co2 - stored" == c.secondary_feedstock_name]

        logger.info(f"Loaded {len(biomass_constraints)} biomass secondary feedstock constraints")
        logger.info(f"Loaded {len(co2_constraints)} CO2 storage secondary feedstock constraints")

    # Initialize environment with constraints (empty list if none found)
    env.initiate_secondary_feedstock_constraints(constraints)
    if not constraints:
        logger.info("No secondary feedstock constraints available, using empty list")
    else:
        logger.info(f"Total {len(constraints)} secondary feedstock constraints loaded")


def bootstrap_simulation(
    config: "SimulationConfig",
    repository: Optional[Repository] = None,  # Allow injecting a repository for testing
) -> "SimulationRunner":
    """
    Sets up the entire simulation environment from a configuration object.

    This factory is the single entry point for creating a runnable simulation,
    encapsulating all data loading, repository population, and dependency injection.
    """
    from .simulation import SimulationRunner

    # If no repository is provided, create one from JSON files (production behavior)
    repository_json = None
    fixtures_dir = None  # Initialize to None

    if repository is None:
        # Use data_dir if provided, otherwise default to ./data
        data_dir = config.data_dir if config.data_dir else Path("./data")
        fixtures_dir = data_dir / "fixtures"

        if not fixtures_dir.exists():
            raise FileNotFoundError(f"Fixtures directory not found: {fixtures_dir}")

        logger.info(f"Using fixtures directory: {fixtures_dir}")

        repository_json = JsonRepository(
            plant_lifetime=config.plant_lifetime,
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
            railway_costs_path=data_dir / "railway_costs.json",
            transport_emissions_path=fixtures_dir / "transport_emissions.json",
            biomass_availability_path=fixtures_dir / "biomass_availability.json",
            technology_emission_factors_path=fixtures_dir / "technology_emission_factors.json",
            fopex_path=fixtures_dir / "fopex.json",
            carbon_border_mechanisms_path=fixtures_dir / "carbon_border_mechanisms.json",
            fallback_material_costs_path=fixtures_dir / "fallback_material_costs.json",
            current_simulation_year=int(config.start_year),
        )

        # Validate metadata compatibility and log configuration
        validate_plant_lifetime_compatibility(config, repository_json)

        repository = InMemoryRepository()
        repository.plants.add_list(repository_json.plants.list())
        repository.demand_centers.add_list(repository_json.demand_centers.list())
        repository.suppliers.add_list(repository_json.suppliers.list())

        # Apply use_iron_ore_premiums flag to iron ore supplier costs
        for supplier in repository.suppliers.list():
            if config.use_iron_ore_premiums and supplier.mine_price is not None:
                supplier.production_cost = supplier.mine_price
            elif not config.use_iron_ore_premiums and supplier.mine_cost is not None:
                supplier.production_cost = supplier.mine_cost
            # If mine_cost/mine_price are None (e.g., for scrap suppliers), keep existing production_cost

        # Plant groups setup from SimulationRunner.setup()
        plant_groups = []
        for pg in repository_json.plant_groups.list():
            plant_list = [repository.plants.get(p.plant_id) for p in pg.plants]
            plant_groups.append(PlantGroup(plant_group_id=pg.plant_group_id, plants=plant_list))
        repository.plant_groups.add_list(plant_groups)
        repository.plant_groups.add(PlantGroup(plant_group_id="indi", plants=[]))

        repository.trade_tariffs.add_list(repository_json.trade_tariffs.list())

    # Create UoW
    uow = UnitOfWork(repository=repository)

    # Create Environment with basic constructor (let it initialize itself)
    if fixtures_dir:
        tech_switches_path = fixtures_dir / "tech_switches_allowed.csv"
    elif config.data_dir:
        # When repository is provided but we still need tech_switches
        tech_switches_path = config.data_dir / "fixtures" / "tech_switches_allowed.csv"
    else:
        tech_switches_path = None
    env = Environment(config=config, tech_switches_csv=tech_switches_path)

    # Load all the data into the environment using the initiate methods
    if repository_json:
        # Initialize country mappings first as it's needed by other methods
        env.initiate_country_mappings(country_mappings=repository_json.country_mappings.get_all())

        env.initiate_fopex(repository_json.fopex.list())
        env.initiate_demand_dicts(repository.demand_centers.list())
        env.initiate_carbon_costs(carbon_costs=repository_json.carbon_costs.list())
        from .domain.constants import Year

        def _normalise_cost_series(series):
            data = series.carbon_cost if hasattr(series, "carbon_cost") else series
            normalised = {}
            for key, value in data.items():
                try:
                    year_key = Year(int(key))
                except (TypeError, ValueError):
                    year_key = key
                normalised[year_key] = float(value)
            return normalised

        env.carbon_costs = {iso3: _normalise_cost_series(series) for iso3, series in env.carbon_costs.items()}
        try:
            price_sample = list(env.carbon_costs.items())[:3]
            logger.warning(
                "Carbon-cost dict sample: %s",
                [
                    (
                        iso3,
                        [
                            (repr(year_key), type(year_key).__name__, value)
                            for year_key, value in list(series.items())[:3]
                        ],
                    )
                    for iso3, series in price_sample
                ],
            )
        except Exception:
            logger.exception("Failed to log carbon cost sample")
        env.initiate_input_costs(input_costs_list=repository_json.input_costs.list())
        env.initiate_dynamic_feedstocks(feedstocks=repository_json.primary_feedstocks.list())
        env.initiate_techno_economic_details(capex_list=repository_json.capex.list())
        env.initiate_capex_subsidies(subsidies=repository_json.subsidies.list())
        env.initiate_opex_subsidies(subsidies=repository_json.subsidies.list())
        env.initiate_debt_subsidies(subsidies=repository_json.subsidies.list())
        env.initiate_industrial_asset_cost_of_capital(repository_json.cost_of_capital.list())
        env.initiate_grid_emissivity(emissivities=repository_json.region_emissivity.list())
        env.initiate_gas_coke_emissivity(emissivities=repository_json.region_emissivity.list())
        env.set_trade_tariffs(trade_tariffs=repository_json.trade_tariffs.list())
        env.set_legal_process_connectors(legal_process_connectors=repository_json.legal_process_connectors.list())
        env.carbon_border_mechanisms = repository_json.carbon_border_mechanisms.list()
        env.railway_costs = repository_json.railway_costs.list()
        env.transport_emissions = repository_json.transport_emissions.list()
        env.transport_kpis = repository_json.transport_emissions.list()
        env.fallback_material_costs = repository_json.fallback_material_costs.list()
        env.propagate_grid_emissivity_to_furnace_groups(plants=repository.plants.list())

        # Validate fallback material costs were loaded - fail fast if missing
        if len(env.fallback_material_costs) == 0:
            raise ValueError(
                "No fallback material costs loaded from fixtures. "
                "Cannot run simulation - technology evaluations will be invalid. "
                "Ensure the master Excel file contains a 'Fallback material cost' sheet, "
                "or that fallback_material_costs.json is populated in the fixtures directory."
            )
        logger.info(f"Loaded {len(env.fallback_material_costs)} fallback material costs")

        # Load default metallic charge per technology mapping from master Excel
        if fixtures_dir:
            resolver = DataPathResolver(fixtures_dir.parent)
            master_excel_path = resolver.fallback_bom_excel_path
            if master_excel_path.exists():
                from .adapters.dataprocessing.excel_reader import read_fallback_bom_definitions

                try:
                    env.default_metallic_charge_per_technology = read_fallback_bom_definitions(master_excel_path)
                except Exception as e:
                    logger.warning(
                        "Could not load fallback BOM definitions from %s: %s. "
                        "New technologies such as MOE or E-WIN will rely on hardcoded averages (no energy costs).",
                        master_excel_path,
                        e,
                    )
                    env.default_metallic_charge_per_technology = {}
            else:
                logger.warning(
                    "Master workbook for fallback BOM definitions not found at %s. "
                    "Default metallic charges will remain empty.",
                    master_excel_path,
                )
                env.default_metallic_charge_per_technology = {}
        env.initiate_hydrogen_efficiency(repository_json.hydrogen_efficiency.list())
        env.initiate_hydrogen_capex_opex(repository_json.hydrogen_capex_opex.list())
        env.initiate_technology_emission_factors(repository_json.technology_emission_factors.list())

        # Load secondary feedstock constraints from biomass availability data
        _load_secondary_feedstock_constraints(env, repository_json)

        # Initialize virgin iron demand (required for PlantAgentsModel)
        env.initialize_virgin_iron_demand(
            world_suppliers_list=repository_json.suppliers.list(), steel_demand_dict=env.demand_dict
        )
    else:
        # For test repositories, initialize country mappings if provided
        if hasattr(repository, "country_mappings") and repository.country_mappings:
            env.initiate_country_mappings(country_mappings=repository.country_mappings)
        # Initialize cost of capital if provided
        if hasattr(repository, "cost_of_capital") and repository.cost_of_capital:
            env.initiate_industrial_asset_cost_of_capital(repository.cost_of_capital)
        # Initialize empty secondary feedstock constraints for test repositories
        env.initiate_secondary_feedstock_constraints([])

    # Set configuration attributes
    # Note: global_bf_ban removed - now handled via allowed_techs system
    env.output_dir = config.output_dir

    # Initialize plot paths
    if config.output_dir:
        from .domain.models import PlotPaths

        plots_dir = config.output_dir / "plots"
        pam_plots_dir = plots_dir / "PAM"
        geo_plots_dir = plots_dir / "GEO"
        tm_plots_dir = plots_dir / "TM"

        # Create the directories
        plots_dir.mkdir(parents=True, exist_ok=True)
        pam_plots_dir.mkdir(parents=True, exist_ok=True)
        geo_plots_dir.mkdir(parents=True, exist_ok=True)
        tm_plots_dir.mkdir(parents=True, exist_ok=True)

        env.plot_paths = PlotPaths(
            plots_dir=plots_dir,
            pam_plots_dir=pam_plots_dir,
            geo_plots_dir=geo_plots_dir,
            tm_plots_dir=tm_plots_dir,
        )

    # Initialize GeoDataPaths if data_dir is available
    if config.data_dir:
        from .domain.models import GeoDataPaths

        # Use DataPathResolver to get actual paths
        path_resolver = DataPathResolver(data_directory=config.data_dir)

        # Check what actually exists and set paths accordingly
        terrain_path = (
            path_resolver.terrain_nc_path if path_resolver.terrain_nc_path.exists() else config.data_dir / "terrain.nc"
        )
        rail_distance_path = (
            path_resolver.rail_distance_nc_path
            if path_resolver.rail_distance_nc_path.exists()
            else config.data_dir / "rail_distance.nc"
        )

        env.geo_paths = GeoDataPaths(
            data_dir=config.data_dir,
            atlite_dir=config.data_dir / "atlite",
            geo_plots_dir=config.output_dir / "plots" / "GEO"
            if config.output_dir
            else config.data_dir / "output" / "plots" / "GEO",
            terrain_nc_path=terrain_path,
            rail_distance_nc_path=rail_distance_path,
            railway_capex_csv_path=config.data_dir / "railway_capex.csv",
            lcoh_capex_csv_path=config.data_dir / "lcoh_capex.csv",
            regional_energy_prices_xlsx=config.data_dir / "regional_energy_prices.xlsx",
            countries_shapefile_dir=path_resolver.countries_shapefile_dir,
            disputed_areas_shapefile_dir=config.data_dir / "ne_10m_admin_0_disputed_areas",
            baseload_power_sim_dir=path_resolver.baseload_power_sim_dir
            if path_resolver.baseload_power_sim_dir.exists()
            else config.data_dir / "baseload_power_sim",
            static_layers_dir=config.data_dir / "outputs" / "GEO",  # Use outputs/GEO for static layers
            landtype_percentage_path=path_resolver.landtype_percentage_nc_path
            if path_resolver.landtype_percentage_nc_path.exists()
            else config.data_dir / "landtype_percentage.nc",
        )

    # Calculate initial state using Environment methods
    env.calculate_demand()
    env.update_regional_capacity(repository.plants.list())
    env.update_capex_reduction_ratios()
    # Only update capex if we have capex data initialized
    if env.name_to_capex:
        env.update_capex()

    # set cost of debt in furnace groups
    for plant in repository.plants.list():
        for furnace_group in plant.furnace_groups:
            cost_of_debt = env.industrial_cost_of_debt.get(plant.location.iso3)
            if cost_of_debt is None:
                # For test repositories only (InMemoryRepository), use a default cost of debt if not initialized
                if isinstance(repository, InMemoryRepository):
                    cost_of_debt = 0.05  # Default 5% cost of debt for tests
                else:
                    raise ValueError(f"Cost of debt not found for ISO3: {plant.location.iso3}")
            furnace_group.set_cost_of_debt(cost_of_debt=cost_of_debt, cost_of_debt_no_subsidy=cost_of_debt)

    # Set CAPEX in furnace groups (existing logic from current bootstrap)
    iso3_to_region_mapping = env.country_mappings.iso3_to_region() if env.country_mappings else {}
    # Only update capex in furnace groups if we have capex data
    if env.name_to_capex and "greenfield" in env.name_to_capex:
        for plant in repository.plants.list():
            for furnace_group in plant.furnace_groups:
                if furnace_group.technology.product.lower() in set(env.technology_to_product.values()):
                    if plant.location.iso3 not in iso3_to_region_mapping:
                        logger.warning(
                            f"ISO3 code {plant.location.iso3} not found in mapping "
                            f"for plant {plant.plant_id}, skipping capex update"
                        )
                        continue
                    region = iso3_to_region_mapping[plant.location.iso3]
                    tech_name = furnace_group.technology.name

                    if (
                        region in env.name_to_capex["greenfield"]
                        and tech_name in env.name_to_capex["greenfield"][region]
                    ):
                        capex = env.name_to_capex["greenfield"][region][tech_name]
                        if furnace_group.technology.capex_type == "brownfield":
                            capex *= env.capex_renovation_share[tech_name]
                        furnace_group.set_technology_capex(capex=capex, capex_no_subsidy=capex)

    # Set input costs and feedstocks in furnace groups
    env.set_input_cost_in_furnace_groups(world_plants=repository.plants.list())
    if env.dynamic_feedstocks:
        env.set_primary_feedstocks_in_furnace_groups(world_plants=repository.plants.list())

    # Create message bus
    bus = bootstrap(uow=uow, env=env, config=config)

    # Create simulation runner
    return SimulationRunner(bus=bus, config=config)
