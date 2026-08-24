# TODO: Rename script to something more intuitive (e.g., economic_models.py)
import gc
import logging
import pickle
import random
import time
from typing import cast

from steelo.adapters.geospatial.geo_unit_lookup import derive_geo_unit_for_site
from steelo.adapters.geospatial.top_location_finder import get_candidate_locations_for_opening_new_plants
from steelo.adapters.geospatial.geospatial_statistics import (
    export_lcoe_lcoh_statistics_by_country,
    export_overbuild_factor_statistics_by_country,
)
from steelo.adapters.repositories.in_memory_repository import InMemoryRepository
from steelo.domain import Year
from steelo.domain.commands import (
    AddFurnaceGroup,
    ChangeFurnaceGroupStatusToSwitchingTechnology,
    ChangeFurnaceGroupTechnology,
)
from steelo.domain.calculate_costs import collect_subsidies_for_geo
from steelo.domain.constants import T_TO_KT, Volumes
from steelo.domain.events import SteelAllocationsCalculated
from steelo.domain.trade_modelling.set_up_steel_trade_lp import (
    set_up_steel_trade_lp,
    solve_steel_trade_lp_and_return_commodity_allocations,
)
from steelo.service_layer.message_bus import MessageBus
from steelo.utilities.file_output import export_commodity_allocations_to_csv
from steelo.utilities.memory_profiling import MemoryTracker
from steelo.utilities.plotting import (
    plot_detailed_trade_map,
    plot_trade_allocation_visualization,
    plot_process_graph,
)

# ============================================================================
# SOLVER CONFIGURATION - Edit this section to experiment with different solver settings
# ============================================================================
# Production default: IPM - equivalent runtime to Simplex but ~5GB less memory (17.7GB vs 22.6GB peak)
# NOTE: IPM does not support warm starts (HiGHS limitation), but memory savings outweigh warm start benefits
# Based on full 26-year validation runs (see specs/2025-10-05_solver_validation.md)
#
# Leave empty to use production defaults, or uncomment an experiment below

SOLVER_CONFIG: dict[str, str | int] = {}  # Uses TradeLPModel defaults (IPM)

# # Override to test Simplex (dual) - supports warm starts but higher memory usage
# SOLVER_CONFIG = {"solver": "simplex", "simplex_strategy": 2}

# # Override to test different IPM settings
# SOLVER_CONFIG = {"solver": "ipm", "presolve": "choose", "scaling": "on"}

# # Disable Presolve (not recommended - 17% slower)
# SOLVER_CONFIG = {"presolve": "off"}

# # Concurrent Methods (not recommended - 22% slower)
# SOLVER_CONFIG = {"simplex_strategy": 4}

# # Relaxed Tolerances (no meaningful impact observed)
# SOLVER_CONFIG = {
#     "primal_feasibility_tolerance": 1e-6,
#     "dual_feasibility_tolerance": 1e-6,
#     "ipm_optimality_tolerance": 1e-7,
# }

# # Thread Tuning (minor 3% improvement, adds complexity)
# SOLVER_CONFIG = {"threads": 4}

# ============================================================================


class GeospatialModel:
    """Model to open new plants"""

    @staticmethod
    def run(bus: MessageBus) -> None:
        """
        GeospatialModel.run orchestrates the identification and progression of new steel and iron plants based on geospatial and
        economic criteria.

        Steps:
            1. Produces all geospatial input layers (LCOE, transport costs, etc.) and calculates outgoing cashflow proxies
            to select the best locations globally and per country.
            2. Identifies new business opportunities in top locations for technologies that will be allowed at the earliest possible
            construction start (current year + consideration time + announcement time). These are marked as 'considered'.
            3. Updates dynamic costs (e.g., grid cell-specific power and hydrogen prices, CAPEX, cost of debt, and subsidies) for all
            business opportunities yearly (status: considered and announced).
            4. Updates the NPV of all potential business opportunities each year with new costs (status: considered).
            5. Business opportunities (status: considered) whose last X (=consideration_time, default=3y) yearly NPVs are all
            positive (a rolling window, not necessarily the first years) are announced with a certain probability (default=70%;
            uniformly sampled), changing their status to 'announced'. This reflects that not all opportunities are taken up by
            investors.
            6. Business opportunities (status: considered) that have a negative NPV for at least X years (default=3y) in a row are
            discarded (status: discarded).
            7. Announced plants, if their technology is still allowed, are constructed after 1 year with a certain probability
            (default=90%), changing their status to 'construction'. This models the risk that some plants are not constructed.
            8. Plant construction takes X years (default=4y); after this period, the plant becomes operational (status: operating).

        Args:
            bus (MessageBus): The message bus to send the event to.

        Returns:
            None

        Side effects: Opens new plants and furnace groups, updates their energy costs (power and hydrogen), and manages
        the status progression of business opportunities through consideration, announcement, and construction.
        """
        logger = logging.getLogger(f"{__name__}.GeospatialModel.run")
        logger.info(f"\n\n[GEO] ========== 🗺️  Starting GeospatialModel.run for Year {bus.env.year} ========== \n")
        geo_start = time.time()

        # Set up
        ## Check preconditions
        if bus.env.geo_paths is None:
            raise ValueError("geo_paths must be configured in Environment for geospatial analysis")
        if bus.env.country_mappings is None:
            raise ValueError("Country mappings must be configured in Environment for geospatial analysis")
        if bus.env.config is None:
            raise ValueError("config must be set in Environment for geospatial analysis")
        ## Prepare input costs
        geo_config = bus.env.config.geo_config
        indi_master_pg = bus.uow.plant_groups.get("indi")
        per_country_indi_groups = [pg for pg in bus.uow.plant_groups.list() if pg.plant_group_id.startswith("indi_")]
        input_costs_converted: dict[
            str, dict[Year, dict[str, float]]
        ] = {}  # country_code -> {year: {cost_type: cost_value}}
        for iso3, year_costs in bus.env.input_costs.items():
            if iso3 is not None:  # Filter out None keys
                input_costs_converted[iso3] = {Year(year): costs for year, costs in year_costs.items()}
        ## Make a price series for COSA and NPV calculations, anchored at the current year.
        ## Extended by consideration_time + 1 so the opportunity paths can re-anchor it at
        ## their construction start (up to target_year) and still cover the operating window.
        future_price_series: dict[str, list[float]] = {}
        start_year = bus.env.year
        end_year = (
            bus.env.year
            + bus.env.config.consideration_time
            + 1
            + bus.env.config.construction_time
            + bus.env.config.plant_lifetime
        )
        steel_demand = []
        iron_demand = []
        for product in ["steel", "iron"]:
            future_price_list: list[float] = []
            for year in range(start_year, end_year):
                if product == "steel":
                    demand_in_year = Volumes(sum(entry.get(Year(year), 0) for entry in bus.env.demand_dict.values()))
                    steel_demand.append(demand_in_year)
                elif product == "iron":
                    if bus.env.virgin_iron_demand is not None:
                        demand_in_year = Volumes(bus.env.virgin_iron_demand.get_demand(Year(year)))
                        iron_demand.append(demand_in_year)
                    else:
                        raise ValueError("virgin_iron_demand must be set in environment for iron price series")
                future_price = bus.env.extract_price_from_costcurve(
                    demand=demand_in_year, product=product, future=False
                )
                future_price_list.append(future_price)
            future_price_series[product] = future_price_list

        # Create geospatial layers and calculate outgoing cashflow proxy to find top locations
        step_start = time.time()
        top_locations, custom_energy_costs = get_candidate_locations_for_opening_new_plants(
            bus.uow, bus.env, geo_config, bus.env.geo_paths
        )
        step_time = time.time() - step_start
        logger.info(f"operation=geo_candidate_locations year={bus.env.year} duration_s={step_time:.3f}")
        logger.debug(f"[GEO] Number of top iron locations returned: {len(top_locations.get('iron', []))}")
        logger.debug(f"[GEO] Number of top steel locations returned: {len(top_locations.get('steel', []))}")
        for product in ["iron", "steel"]:
            if not top_locations.get(product):
                logger.warning(f"[GEO] No {product} locations found! This will cause NPV error.")

        # Export LCOE/LCOH statistics every 5 years (matching cost curve export frequency)
        years_since_start = bus.env.year - bus.env.config.start_year
        if years_since_start % 5 == 0:
            try:
                export_lcoe_lcoh_statistics_by_country(
                    energy_prices=custom_energy_costs,
                    year=bus.env.year,
                    output_dir=bus.env.config.output_dir,
                    hydrogen_ceiling_percentile=geo_config.hydrogen_ceiling_percentile,
                )
            except Exception as e:
                logger.warning(f"Failed to export LCOE/LCOH statistics for year {bus.env.year}: {e}")

            # Export overbuild factor statistics for solar, wind, and battery
            for factor_name in ["solar_factor", "wind_factor", "battery_factor"]:
                try:
                    export_overbuild_factor_statistics_by_country(
                        energy_prices=custom_energy_costs,
                        year=bus.env.year,
                        output_dir=bus.env.config.output_dir,
                        factor_name=factor_name,
                    )
                except Exception as e:
                    logger.warning(f"Failed to export {factor_name} statistics for year {bus.env.year}: {e}")

        # Update dynamic costs for all existing business opportunities yearly
        step_start = time.time()
        dynamic_cost_commands: list = []
        for pg in per_country_indi_groups:
            dynamic_cost_commands.extend(
                pg.update_dynamic_costs_for_business_opportunities(
                    current_year=bus.env.year,
                    consideration_time=bus.env.config.consideration_time,
                    construction_time=bus.env.config.construction_time,
                    custom_energy_costs=custom_energy_costs,  # type: ignore[arg-type]  # needed to avoid importing xarray into the domain
                    capex_dict_all_locs=bus.env.name_to_capex["greenfield"],
                    cost_debt_all_locs=bus.env.cost_of_debt_by_tech,
                    iso3_to_region_map=bus.env.country_mappings.iso3_to_region(),
                    global_risk_free_rate=bus.env.config.global_risk_free_rate,
                    avg_utilization=bus.env.avg_utilization,
                    capex_subsidies=bus.env.capex_subsidies,
                    debt_subsidies=bus.env.debt_subsidies,
                    energy_subsidies=bus.env.energy_subsidies,
                )
            )
        for command in dynamic_cost_commands:
            bus.handle(command)
        step_time = time.time() - step_start
        logger.info(
            f"operation=geo_update_costs year={bus.env.year} duration_s={step_time:.3f} fg_count={len(dynamic_cost_commands)}"
        )
        logger.debug(f"[GEO] Updated dynamic costs for {len(dynamic_cost_commands)} furnace groups")

        # Update the status of all existing business opportunities (to move from opportunity to new plant)
        step_start = time.time()
        status_commands: list = []
        for pg in per_country_indi_groups:
            status_commands.extend(
                pg.update_status_of_business_opportunities(
                    current_year=bus.env.year,
                    consideration_time=bus.env.config.consideration_time,
                    market_price=future_price_series,
                    cost_of_equity_all_locs=bus.env.cost_of_equity_by_tech,
                    probability_of_announcement=bus.env.config.probability_of_announcement,
                    probability_of_construction=bus.env.config.probability_of_construction,
                    plant_lifetime=bus.env.config.plant_lifetime,
                    construction_time=bus.env.config.construction_time,
                    allowed_techs=bus.env.allowed_techs,
                    new_plant_capacity_in_year=bus.env.new_plant_capacity_in_year,
                    expanded_capacity=bus.env.config.expanded_capacity,
                    capacity_limit_iron=bus.env.config.capacity_limit_iron,
                    capacity_limit_steel=bus.env.config.capacity_limit_steel,
                    new_capacity_share_from_new_plants=bus.env.config.new_capacity_share_from_new_plants,
                    reductant_score_series=bus.env.reductant_score_series,
                    opex_subsidies=bus.env.opex_subsidies,
                    get_co2_headroom=bus.env.get_co2_headroom,
                    get_co2_need=bus.env.get_co2_need,
                    co2_storage_diagnostics=bus.env.co2_storage_diagnostics,
                    reserved_discount_factor=bus.env.config.co2_storage_reserved_discount_factor,
                )
            )
        if status_commands:
            for command in status_commands:
                bus.handle(command)
            step_time = time.time() - step_start
            logger.info(
                f"operation=geo_update_status year={bus.env.year} duration_s={step_time:.3f} fg_count={len(status_commands)}"
            )
            logger.debug(f"[GEO] Updated status for {len(status_commands)} furnace groups")

        # Identify new business opportunities and prioritize them by NPV
        step_start = time.time()
        bus.handle(
            indi_master_pg.identify_new_business_opportunities_4indi(
                current_year=bus.env.year,
                consideration_time=bus.env.config.consideration_time,
                construction_time=bus.env.config.construction_time,
                plant_lifetime=bus.env.config.plant_lifetime,
                input_costs=input_costs_converted,
                locations=top_locations,  # Includes the power, hydrogen and infrastructure costs in those locations
                iso3_to_region_map=bus.env.country_mappings.iso3_to_region(),
                market_price=future_price_series,
                capex_dict_all_locs_techs=bus.env.name_to_capex["greenfield"],
                cost_of_debt_all_locs=bus.env.cost_of_debt_by_tech,
                cost_of_equity_all_locs=bus.env.cost_of_equity_by_tech,
                steel_plant_capacity=bus.env.config.expanded_capacity,
                all_plant_ids=[p.plant_id for p in bus.uow.plants.list()],
                fopex_all_locs_techs=bus.env.fopex_by_country,
                equity_share=bus.env.config.equity_share,
                dynamic_feedstocks=bus.env.dynamic_feedstocks,
                get_bom_from_avg_boms=bus.env.get_bom_from_avg_boms,
                reductant_score_series=bus.env.reductant_score_series,
                global_risk_free_rate=bus.env.config.global_risk_free_rate,
                tech_to_product={
                    tech: product
                    for tech, product in bus.env.technology_to_product.items()
                    if tech not in geo_config.excluded_greenfield_technologies
                },
                allowed_techs=bus.env.allowed_techs,
                top_n_loctechs_as_business_op=bus.env.config.top_n_loctechs_as_business_op,
                opportunity_pool_depth=bus.env.config.opportunity_pool_depth,
                calculate_npv_sites_share=bus.env.config.calculate_npv_sites_share,
                technology_emission_factors=bus.env.technology_emission_factors,
                chosen_emissions_boundary_for_carbon_costs=bus.env.config.chosen_emissions_boundary_for_carbon_costs,
                capex_subsidies=bus.env.capex_subsidies,
                debt_subsidies=bus.env.debt_subsidies,
                opex_subsidies=bus.env.opex_subsidies,
                energy_subsidies=bus.env.energy_subsidies,
                active_statuses=bus.env.config.active_statuses,
                environment_most_common_reductant=bus.env.most_common_reductant_by_tech,
                disposal_cost_outputs=bus.env.config.disposal_cost_outputs,
                get_co2_headroom=bus.env.get_co2_headroom,
                get_co2_need_by_name=bus.env.get_co2_need_by_name,
                co2_storage_diagnostics=bus.env.co2_storage_diagnostics,
                derive_geo_unit=derive_geo_unit_for_site,
                probabilistic_agents=bus.env.config.probabilistic_agents,
            )
        )
        step_time = time.time() - step_start
        logger.info(f"operation=geo_identify_opportunities year={bus.env.year} duration_s={step_time:.3f}")

        # End of geospatial model
        module_elapsed = time.time() - geo_start
        logger.info(f"operation=geospatial_model year={bus.env.year} duration_s={module_elapsed:.3f}")
        logger.info(
            f"[GEO] ========== Total GeospatialModel.run Time: {module_elapsed:.3f} seconds ({module_elapsed / 60:.2f} minutes) =========="
        )


class AllocationModel:
    """Model to allocate resource to demand center from supply center"""

    @staticmethod
    def run(bus: MessageBus) -> None:
        """
        Run the allocation lp model

        Args
        """

        logger = logging.getLogger(f"{__name__}.AllocationModel.run")
        module_start = time.time()
        logger.debug(f"\n\n[TM] ========== Starting AllocationModel.run for year {bus.env.year} ========== \n")

        # Initialize memory tracker for detailed LP profiling
        memory_tracker = MemoryTracker()

        # Setup phase: emission factors, prices, carbon costs, LP construction
        setup_start = time.time()
        memory_tracker.checkpoint("before_lp_setup", year=bus.env.year)

        bus.env.set_primary_feedstocks_in_furnace_groups(world_plants=bus.uow.repository.plants.list())

        # Update furnace group emission factors before calculating emissions
        # This must happen before any emissions calculations
        for plant in bus.uow.plants.list():
            plant.update_furnace_technology_emission_factors(
                technology_emission_factors=bus.env.technology_emission_factors
            )

        # For percentage tariffs:
        bus.env.calculate_average_commodity_price_per_region(
            world_plants=bus.uow.repository.plants.list(),
            world_suppliers=bus.uow.repository.suppliers.list(),
            year=bus.env.year,
        )
        assert bus.env.legal_process_connectors is not None, (
            "Legal process connectors must be set in the environment. Please ensure they are read in from user input."
        )
        assert bus.env.config is not None, "config is required for trade LP"

        # Update furnace group carbon costs for the current year
        # This ensures that carbon border adjustment mechanisms have current carbon cost data
        current_year = bus.env.year
        for plant in bus.uow.plants.list():
            plant.update_furnace_group_carbon_costs(
                current_year, bus.env.config.chosen_emissions_boundary_for_carbon_costs
            )

        # Clustering: Reduce LP complexity by aggregating furnace groups
        meta_furnace_groups = None
        if bus.env.config.enable_furnace_group_clustering:
            from steelo.domain.trade_modelling.furnace_group_clustering import cluster_furnace_groups

            logger.info(f"[CLUSTERING] Clustering enabled for year {bus.env.year}")
            meta_furnace_groups, cluster_mapping = cluster_furnace_groups(
                plants=bus.uow.plants.list(),
                config=bus.env.config,
                aggregated_constraints=bus.env.aggregated_metallic_charge_constraints or None,
            )
            logger.info(
                f"[CLUSTERING] Reduced furnace groups from "
                f"{sum(len(fgs) for fgs in cluster_mapping.values())} to {len(meta_furnace_groups)}"
            )
            # Store cluster_mapping in environment for disaggregation later
            bus.env.cluster_mapping = cluster_mapping
            bus.env.meta_furnace_groups = meta_furnace_groups
        else:
            logger.debug(f"[CLUSTERING] Clustering disabled for year {bus.env.year}")

        if bus.env.config.include_tariffs:
            active_tariffs = bus.env.get_active_trade_tariffs()
            assert bus.env.config is not None, "config must be set in the environment"
            trade_lp = set_up_steel_trade_lp(
                bus,
                bus.env.year,
                bus.env.config,
                legal_process_connectors=bus.env.legal_process_connectors,
                active_trade_tariffs=active_tariffs,
                secondary_feedstock_constraints=bus.env.relevant_secondary_feedstock_constraints(),
                aggregated_metallic_charge_constraints=bus.env.aggregated_metallic_charge_constraints,
                transport_kpis=bus.env.transport_kpis,
                furnace_groups_override=meta_furnace_groups,  # Pass clustered meta-FGs
            )
        else:
            assert bus.env.config is not None, "config must be set in the environment"
            trade_lp = set_up_steel_trade_lp(
                bus,
                bus.env.year,
                bus.env.config,
                legal_process_connectors=bus.env.legal_process_connectors,
                secondary_feedstock_constraints=bus.env.relevant_secondary_feedstock_constraints(),
                aggregated_metallic_charge_constraints=bus.env.aggregated_metallic_charge_constraints,
                transport_kpis=bus.env.transport_kpis,
                furnace_groups_override=meta_furnace_groups,  # Pass clustered meta-FGs
            )

        # Warm-start from previous year's solution if available (OPT-2)
        if bus.env.previous_lp_solution is not None:
            trade_lp.previous_solution = bus.env.previous_lp_solution

        if bus.env.output_dir is not None:
            trade_lp.failure_dump_path = bus.env.output_dir / "TM" / f"trade_lp_failed_{bus.env.year}.mps"

        setup_elapsed = time.time() - setup_start
        logger.info(f"operation=allocation_setup year={bus.env.year} duration_s={setup_elapsed:.3f}")
        memory_tracker.checkpoint("after_lp_setup", year=bus.env.year)

        # Trade LP solve (trade_optimization is logged inside solve_steel_trade_lp_and_return_commodity_allocations)
        # When clustering is enabled, we skip CommodityAllocations creation here and do it after disaggregation
        # Use explicit True check to avoid MagicMock truthy values in tests
        clustering_enabled = getattr(bus.env.config, "enable_furnace_group_clustering", None)
        if clustering_enabled is True:
            # Just solve the LP, don't create CommodityAllocations yet
            from steelo.domain.trade_modelling.set_up_steel_trade_lp import solve_lp_only

            commodity_allocations = solve_lp_only(trade_lp)
            logger.info("[CLUSTERING] LP solved, CommodityAllocations creation deferred until after disaggregation")
        else:
            commodity_allocations = solve_steel_trade_lp_and_return_commodity_allocations(
                trade_lp=trade_lp, repository=cast(InMemoryRepository, bus.uow.repository)
            )
        memory_tracker.checkpoint("after_lp_solve", year=bus.env.year)

        # Store current solution for warm-starting next year's LP (OPT-2)
        # Only store if solution was successful (allocations found)
        if commodity_allocations and trade_lp.solution_status is not None:
            import pyomo.environ as pyo

            if trade_lp.solution_status == pyo.SolverStatus.ok:
                bus.env.previous_lp_solution = trade_lp.get_solution_for_warm_start()

        memory_tracker.checkpoint("after_extract_solution", year=bus.env.year)

        # Extract allocations before cleanup (needed for event publishing later)
        trade_lp_allocations = trade_lp.allocations if hasattr(trade_lp, "allocations") else None

        # Disaggregation: Convert clustered allocations back to individual furnace groups
        # Use explicit True check to avoid MagicMock truthy values in tests
        enable_clustering = getattr(bus.env.config, "enable_furnace_group_clustering", None)
        if enable_clustering is True and trade_lp_allocations is not None:
            from steelo.domain.trade_modelling.furnace_group_clustering import disaggregate_allocations
            from steelo.domain.trade_modelling.set_up_steel_trade_lp import (
                create_commodity_allocations_from_allocations,
            )

            logger.info(f"[DISAGGREGATION] Starting allocation disaggregation for year {bus.env.year}")
            # Assertion: meta_furnace_groups must be set when clustering is enabled
            assert bus.env.meta_furnace_groups is not None, "meta_furnace_groups must be set when clustering is enabled"
            disaggregated_allocations = disaggregate_allocations(
                clustered_allocations=trade_lp_allocations,
                meta_furnace_groups=bus.env.meta_furnace_groups,
                plants_repo=bus.uow.plants,  # type: ignore[arg-type]
                config=bus.env.config,
                transport_kpis=bus.env.transport_kpis,
                willingness_to_pay=bus.env.willingness_to_pay,
                aggregated_constraints=bus.env.aggregated_metallic_charge_constraints,
            )
            # Use disaggregated allocations for TM-PAM connector
            trade_lp_allocations = disaggregated_allocations
            logger.info(f"[DISAGGREGATION] Disaggregation complete for year {bus.env.year}")

            # Now create CommodityAllocations from disaggregated allocations
            logger.info("[DISAGGREGATION] Creating CommodityAllocations from disaggregated allocations")
            commodity_allocations = create_commodity_allocations_from_allocations(
                allocations=disaggregated_allocations,
                repository=cast(InMemoryRepository, bus.uow.repository),
                commodities=trade_lp.commodities,
            )
            logger.info("[DISAGGREGATION] CommodityAllocations created")

        # Post-processing: plotting and CSV export
        postprocess_start = time.time()

        if bus.env.transport_kpis:  # Check if list is not empty
            for _, allocations in commodity_allocations.items():
                allocations.update_transport_emissions(transport_emissions=bus.env.transport_kpis)

        # Filter out commodities with no allocations before plotting
        non_empty_allocations = {
            commodity: allocations
            for commodity, allocations in commodity_allocations.items()
            if len(allocations.allocations) > 0
        }

        total_number_of_allocations = sum(
            len(non_empty_allocations[commodity].allocations) for commodity in non_empty_allocations
        )

        if total_number_of_allocations == 0:
            logger.error(
                f"[TM] No allocations found for year {bus.env.year}. "
                "This may indicate an issue with the trade LP model or input data."
            )

        output_dir = bus.env.output_dir
        if output_dir is None:
            raise ValueError("output_dir must be set on bus.env")

        if non_empty_allocations:
            # # pickle the allocations for debugging purposes TODO: remove
            # with open(output_dir / f"steel_trade_allocations_{bus.env.year}.pkl", "wb") as f:
            #     pickle.dump(non_empty_allocations, f)

            # Create detailed trade map (existing pydeck visualization)
            plot_detailed_trade_map(
                allocations_by_commodity=non_empty_allocations, chosen_year=bus.env.year, plot_paths=bus.env.plot_paths
            )

            # Create trade allocation visualization (new network plot)
            plot_trade_allocation_visualization(
                allocations_by_commodity=non_empty_allocations,
                chosen_year=bus.env.year,
                plot_paths=bus.env.plot_paths,
                country_mappings=bus.env.country_mappings,
                top_n=20,
            )
        export_commodity_allocations_to_csv(
            commodity_allocations_dict=commodity_allocations,
            year=bus.env.year,
            filename=str(output_dir / "TM" / f"steel_trade_allocations_{bus.env.year}.csv"),
        )
        all_dcs = bus.uow.repository.demand_centers.list()
        demand_met = False  # Default to False if no steel allocations
        if "steel" in non_empty_allocations:
            steel_allocations = non_empty_allocations["steel"]
            demand_met = steel_allocations.validate_demand_is_met(year=bus.env.year, demand_centers=all_dcs)
            logger.info("operation=demand_validation year=%s commodity=steel demand_met=%s", bus.env.year, demand_met)
        else:
            logger.info(
                "operation=demand_validation year=%s commodity=steel demand_met=%s reason=no_allocations",
                bus.env.year,
                demand_met,
            )
        if not demand_met:
            trade_lp.generate_process_graph_for_reporting()
            process_utilization = trade_lp.calculate_process_utilization()
            try:
                tm_plots_dir = output_dir / "TM"
                tm_plots_dir.mkdir(parents=True, exist_ok=True)
                plot_process_graph(
                    trade_lp=trade_lp,
                    save_path=str(tm_plots_dir / f"bottleneck_analysis_{bus.env.year}.png"),
                    utilization=process_utilization,
                )
            except OSError:
                logger.warning("Could not save bottleneck analysis plot (output directory not writable)")

        # Explicit LP model cleanup to free memory (Priority 1 memory optimization)
        del trade_lp
        gc.collect()

        # for commodity, allocations in commodity_allocations.items():
        #     if len(allocations.allocations) == 0:
        #         continue
        #     allocations.create_cost_curve(bus.env)
        #     # Handle both dict and list types for cost_curve
        #     cost_curve = allocations.cost_curve
        #     if isinstance(cost_curve, dict):
        #         # If it's a dict, try to extract the list for the commodity
        #         cost_curve_list = cost_curve.get(commodity, [])
        #     else:
        #         cost_curve_list = cost_curve
        #     output_dir = bus.env.output_dir
        #     if output_dir is None:
        #         raise ValueError("output_dir must be set on bus.env")
        #     plot_cost_curve_for_commodity(
        #         cost_curve=cost_curve_list,  # type: ignore[arg-type]
        #         total_demand=allocations.calculate_total_volumes(),
        #         image_path=str(output_dir / "TM" / f"{commodity}_cost_curve_{bus.env.year}.png"),
        #     )

        # Only save and publish event if we have allocations
        print(f"Finished allocation for year {bus.env.year}")
        if trade_lp_allocations is not None:
            output_dir = bus.env.output_dir
            if output_dir is None:
                raise ValueError("output_dir must be set on bus.env")
            tm_dir = output_dir / "TM"
            tm_dir.mkdir(parents=True, exist_ok=True)
            with open(tm_dir / f"steel_trade_allocations_{bus.env.year}.pkl", "wb") as f:
                pickle.dump(trade_lp_allocations, f)
            if (event := SteelAllocationsCalculated(trade_allocations=trade_lp_allocations)) is not None:
                bus.handle(event)

        postprocess_elapsed = time.time() - postprocess_start
        logger.info(f"operation=allocation_postprocess year={bus.env.year} duration_s={postprocess_elapsed:.3f}")

        module_elapsed = time.time() - module_start
        logger.info(f"operation=allocation_model year={bus.env.year} duration_s={module_elapsed:.3f}")
        memory_tracker.checkpoint("allocation_complete", year=bus.env.year)


# class NewAllocationModel:
#     """Model to allocate resource to demand center from supply center"""

#     @staticmethod
#     def run(bus: MessageBus) -> None:
#         """
#         Run the allocation lp model

#         Args
#         """
#         assert bus.env.legal_process_connectors is not None, (
#             "Legal process connectors must be set in the environment. Please ensure they are read in from user input."
#         )
#         assert bus.env.config is not None, "config is required for trade LP"

#         trade_lp = set_up_steel_trade_lp(
#             bus,
#             bus.env.year,
#             bus.env.config,
#             legal_process_connectors=bus.env.legal_process_connectors,
#             transport_kpis=bus.env.transport_kpis,
#         )
#         logger.debug("Bom flow constraints ", len(trade_lp.lp_model.bom_inflow_constraints))
#         logger.debug("Demand constraints ", len(trade_lp.lp_model.demand_constraints))
#         trade_lp.solve_lp_model()
#         trade_lp.extract_solution()

#         if trade_lp.allocations is not None:
#             if (event := SteelAllocationsCalculated(trade_allocations=trade_lp.allocations)) is not None:
#                 bus.handle(event)


class PlantAgentsModel:
    """
    Economic decision-making model for steel and iron plant agents.

    This model simulates how steel and iron plants make economic decisions about:
    1. Technology switching (e.g., switching from BF to DRI, or to a CCS/CCU variant like BF+CCS)
    2. Renovations of existing technologies
    3. Furnace closures at the end of lifetime due to unprofitability
    4. Plant expansions (new furnace groups within existing plants)

    The model evaluates each furnace group within each plant based on:
    - Current and future market prices for steel and iron
    - Capital costs (CAPEX) and operational costs (OPEX)
    - Available subsidies (capex, opex, debt subsidies)
    - Carbon costs and emissions boundaries
    - Technology-specific bills of materials (BOMs)
    - Regional cost variations

    All decisions are made to maximize the net present value (NPV) of the plant's cash flows over its lifetime,
    taking into account cost of stranded assets (COSA) in cases when existing technologies are replaced by new
    technologies before their lifetime runs out.
    """

    @staticmethod
    def run(bus: MessageBus) -> None:
        """
        Execute the plant agent economic decision-making model.

        This method orchestrates the entire decision-making process for all plants:
        1. Calculate market conditions (current and future prices of steel and iron)
        2. Evaluate each plant's furnace groups for potential actions
        3. Evaluate plant groups for their plant expansion opportunities
        4. Track and enforce capacity limits (for steel and iron buildout separately)

        Args:
            bus (MessageBus): The message bus containing:
                - uow: Unit of work with access to plants/plant groups repositories
                - env: Environment with market data, costs, subsidies, and configuration
        """
        # Validate required configuration
        if bus.env.config is None:
            raise ValueError("SimulationConfig is required for PlantAgentsModel")

        logger = logging.getLogger(f"{__name__}.PlantAgentsModel.run")
        module_start = time.time()

        logger.info(f"\n\n[PAM] ========== Starting PlantAgentsModel.run for year {bus.env.year} ========== \n")

        # Load all plants from the repository
        plants = bus.uow.plants.list()

        logger.info(f"[PAM] Processing {len(plants)} plants in the simulation")
        logger.debug(f"[PAM] Active statuses configured: {bus.env.config.active_statuses}")

        # Step 1: Calculate carbon costs for all furnace groups
        # This updates each furnace group's carbon cost based on its emissions and the current carbon price
        carbon_start = time.time()
        bus.env.calculate_carbon_costs_of_furnace_groups(world_plants=plants)
        carbon_elapsed = time.time() - carbon_start
        logger.info(f"operation=pam_carbon_costs year={bus.env.year} duration_s={carbon_elapsed:.3f}")

        # Step 2: Extract current market prices from cost curves
        # These prices are frozen for consistent evaluation across all plants in this year
        freeze_market_price = {
            "steel": bus.env.extract_price_from_costcurve(demand=bus.env.current_demand, product="steel"),
            "iron": bus.env.extract_price_from_costcurve(demand=bus.env.iron_demand, product="iron"),
        }

        logger.info("[PAM] Market prices and demand")
        logger.info(
            f"[PAM] Steel - Price: ${freeze_market_price['steel']:,.2f}/t, Demand: {bus.env.current_demand * T_TO_KT:,.0f} kt"
        )
        logger.info(
            f"[PAM] Iron  - Price: ${freeze_market_price['iron']:,.2f}/t, Demand: {bus.env.iron_demand * T_TO_KT:,.0f} kt"
        )

        # Step 3: Build future price series for NPV and COSA calculations
        # This creates a time series of expected prices for the duration of plant lifetime
        future_price_series: dict[str, list[float]] = {}
        start_year = bus.env.year
        end_year = bus.env.year + bus.env.config.construction_time + bus.env.config.plant_lifetime
        steel_demand = []
        iron_demand = []
        for product in ["steel", "iron"]:
            future_price_list: list[float] = []
            for year in range(start_year, end_year):
                if product == "steel":
                    # Sum steel demand across all demand centers for this year
                    demand_in_year = Volumes(sum(entry.get(Year(year), 0) for entry in bus.env.demand_dict.values()))
                    steel_demand.append(demand_in_year)
                elif product == "iron":
                    # Get virgin iron demand for this year
                    if bus.env.virgin_iron_demand is not None:
                        demand_in_year = Volumes(bus.env.virgin_iron_demand.get_demand(Year(year)))
                        iron_demand.append(demand_in_year)
                    else:
                        raise ValueError("virgin_iron_demand must be set in environment for iron price series")
                # Extract price from cost curve for this demand level
                future_price = bus.env.extract_price_from_costcurve(
                    demand=demand_in_year, product=product, future=False
                )
                future_price_list.append(future_price)
            future_price_series[product] = future_price_list

        # Calculate capacity limits for PAM (excluding capacity reserved for new plants)
        capacity_share = 1 - bus.env.config.new_capacity_share_from_new_plants
        capacity_limit_pam_steel = Volumes(bus.env.config.capacity_limit_steel * capacity_share)
        capacity_limit_pam_iron = Volumes(bus.env.config.capacity_limit_iron * capacity_share)

        counter = 0  # Track number of commands executed across all plants

        # Step 4: Process each plant group for furnace group decisions.
        # Group-first ordering: sweep every FG's annual P&L into the group treasury BEFORE any plant
        # in the group runs its strategy. This gives all plants in the group equal information about
        # the year's available wallet. Plant-iteration order within a group remains random.
        plant_eval_start = time.time()
        logger.info("[PAM] Step 4 - Evaluating furnace group strategies (group-first)")
        step4_plant_groups = bus.uow.plant_groups.list()
        for pg in random.sample(step4_plant_groups, len(step4_plant_groups)):
            balance_before_sweep = pg.balance
            pg.sweep_fg_balances_to_group(
                market_price=freeze_market_price,
                active_statuses=bus.env.config.active_statuses,
            )
            logger.info(
                "[PAM STEP 4] plant_group_id=%s num_plants=%d balance_before_sweep=%.2f balance_after_sweep=%.2f",
                pg.plant_group_id,
                len(pg.plants),
                balance_before_sweep,
                pg.balance,
            )

            for plant in random.sample(pg.plants, len(pg.plants)):
                logger.info(
                    f"\n\n[PAM] === Processing plant {plant.plant_id} in {plant.location.iso3} "
                    f"(year {bus.env.year}) === \n"
                )

                # Retrieve location-specific subsidies for this plant: country-wide rows plus
                # any rows scoped to the plant's province. Empty dicts if no subsidies exist.
                tech_capex_subsidies = collect_subsidies_for_geo(bus.env.capex_subsidies, plant.location.geo_key)
                tech_opex_subsidies = collect_subsidies_for_geo(bus.env.opex_subsidies, plant.location.geo_key)
                tech_debt_subsidies = collect_subsidies_for_geo(bus.env.debt_subsidies, plant.location.geo_key)
                tech_energy_subsidies = {
                    carrier: collect_subsidies_for_geo(carrier_subsidies, plant.location.geo_key)
                    for carrier, carrier_subsidies in bus.env.energy_subsidies.items()
                }

                logger.debug(f"[PAM] Plant group: {plant.parent_gem_id}")
                logger.debug(f"[PAM] Plant group balance: ${pg.balance:,.2f}")
                logger.debug(f"[PAM] Subsidies for {plant.location.iso3}:")
                logger.debug(f"[PAM]  - CAPEX: {list(tech_capex_subsidies.keys())}")
                logger.debug(f"[PAM]  - OPEX:  {list(tech_opex_subsidies.keys())}")
                logger.debug(f"[PAM]  - DEBT:  {list(tech_debt_subsidies.keys())}")

                # Evaluate each furnace group within the plant in random order
                for fg in random.sample(plant.furnace_groups, len(plant.furnace_groups)):
                    # Skip furnace groups that should not be evaluated:
                    # - Zero capacity furnace groups
                    # - Inactive statuses (e.g., closed, mothballed)
                    # - Groups currently switching technology
                    # - "Other" technology category (not modeled)
                    # - Products not in the market price dictionary
                    if (
                        fg.capacity == 0
                        or fg.status.lower() not in bus.env.config.active_statuses
                        or fg.status.lower() == "operating switching technology"
                        or fg.technology.name.lower() == "other"
                        or fg.technology.product.lower() not in freeze_market_price
                    ):
                        logger.info(
                            f"[PAM] == Skipping FG {fg.furnace_group_id} - Tech: {fg.technology.name}, Capacity: {fg.capacity * T_TO_KT:,.0f} kt, Status: {fg.status}, Product: {fg.technology.product} ==\n"
                        )
                        continue

                    logger.info(
                        f"\n\n[PAM] == Evaluating FG {fg.furnace_group_id} - Tech: {fg.technology.name}, Capacity: {fg.capacity * T_TO_KT:,.0f} kt, Status: {fg.status}, Product: {fg.technology.product} ==\n"
                    )
                    logger.debug(f"[PAM] FG balance: ${fg.balance:,.2f}, Historic balance: ${fg.historic_balance:,.2f}")

                    # Retrieve region-specific CAPEX data for technology switching/renovation
                    if "greenfield" in bus.env.name_to_capex:
                        # Map the plant's ISO3 code to its region
                        if bus.env.country_mappings is None:
                            raise ValueError(
                                "Country mapping required for furnace switching (not found in Environment)"
                            )
                        iso3_to_region_mapping = bus.env.country_mappings.iso3_to_region()
                        if plant.location.iso3 not in iso3_to_region_mapping:
                            raise ValueError(f"Region mapping not found for ISO3 code {plant.location.iso3}")
                        region = iso3_to_region_mapping[plant.location.iso3]
                        region_capex = bus.env.name_to_capex["greenfield"][region]
                        capex_renovation_share = bus.env.capex_renovation_share

                        logger.debug(f"[PAM] Region {region} CAPEX loaded for {plant.location.iso3}")
                        logger.debug(f"[PAM] Region CAPEX for technologies: {region_capex}")
                        logger.debug(f"[PAM] Renovation share: {capex_renovation_share}")
                    else:
                        raise KeyError("Region capex not found in bus.env.name_to_capex.")

                    # Get per-technology financing rates for the plant location (before subsidies)
                    cost_of_debt_by_tech = bus.env.cost_of_debt_by_tech.get(plant.location.iso3)
                    if cost_of_debt_by_tech is None:
                        raise ValueError(f"Cost of debt not found for ISO3 code {plant.location.iso3}.")

                    cost_of_equity_by_tech = bus.env.cost_of_equity_by_tech.get(plant.location.iso3)
                    if cost_of_equity_by_tech is None:
                        raise ValueError(f"Cost of equity not found for ISO3 code {plant.location.iso3}")

                    # Evaluate potential technology switch or renovation for this furnace group
                    # This considers: switching technology, renovating existing technology, or closing the furnace
                    if (
                        cmd := plant.evaluate_furnace_group_strategy(
                            fg.furnace_group_id,
                            plant_group=pg,
                            market_price_series=future_price_series,
                            region_capex=region_capex,
                            cost_of_debt_by_tech=cost_of_debt_by_tech,
                            cost_of_equity_by_tech=cost_of_equity_by_tech,
                            capex_renovation_share=capex_renovation_share,
                            get_bom_from_avg_boms=bus.env.get_bom_from_avg_boms,
                            reductant_score_series=bus.env.reductant_score_series,
                            allowed_furnace_transitions=bus.env.allowed_furnace_transitions,
                            dynamic_business_cases=bus.env.dynamic_feedstocks,
                            probabilistic_agents=bus.env.config.probabilistic_agents,
                            tech_capex_subsidies=tech_capex_subsidies,
                            tech_opex_subsidies=tech_opex_subsidies,
                            tech_debt_subsidies=tech_debt_subsidies,
                            tech_energy_subsidies=tech_energy_subsidies,
                            current_year=bus.env.year,
                            allowed_techs=bus.env.allowed_techs,
                            chosen_emissions_boundary_for_carbon_costs=bus.env.config.chosen_emissions_boundary_for_carbon_costs,
                            technology_emission_factors=bus.env.technology_emission_factors,
                            tech_to_product=bus.env.technology_to_product,
                            plant_lifetime=bus.env.config.plant_lifetime,
                            construction_time=bus.env.config.construction_time,
                            risk_free_rate=bus.env.config.global_risk_free_rate,
                            capacity_limit_steel=capacity_limit_pam_steel,
                            capacity_limit_iron=capacity_limit_pam_iron,
                            installed_capacity_in_year=bus.env.installed_capacity_in_year,
                            new_plant_capacity_in_year=bus.env.new_plant_capacity_in_year,
                            most_common_reductant_by_tech=bus.env.most_common_reductant_by_tech,
                            get_co2_headroom=bus.env.get_co2_headroom,
                            get_co2_need_by_name=bus.env.get_co2_need_by_name,
                            co2_storage_diagnostics=bus.env.co2_storage_diagnostics,
                        )
                    ) is not None:
                        logger.info(f"[PAM] FG {fg.furnace_group_id} strategy returned command: {type(cmd).__name__}")
                        if isinstance(cmd, ChangeFurnaceGroupTechnology):
                            # Technology switch: operate with old technology during construction period
                            # After construction_time years, switch to new technology
                            command_to_execute = ChangeFurnaceGroupStatusToSwitchingTechnology(
                                plant_id=cmd.plant_id,
                                furnace_group_id=cmd.furnace_group_id,
                                year_of_switch=bus.env.year + bus.env.config.construction_time,
                                cmd=cmd,
                            )
                            product_opt = bus.env.technology_to_product.get(cmd.technology_name)
                            if product_opt is None:
                                raise ValueError(f"Technology {cmd.technology_name} not found in technology_to_product")
                            tech_product: str = product_opt
                            logger.info(
                                f"[PAM] EXECUTING {type(command_to_execute).__name__} - Future command: {cmd}, Tech: {cmd.technology_name}, Product: {tech_product}, Capacity: {cmd.capacity * T_TO_KT:,.0f} kt"
                            )
                            counter += 1
                            bus.handle(command_to_execute)
                        else:
                            # Execute other commands (e.g., CloseFurnaceGroup, RenovateFurnaceGroup)
                            logger.info(f"[PAM] EXECUTING {type(cmd).__name__}")
                            counter += 1
                            bus.handle(cmd)

        plant_eval_elapsed = time.time() - plant_eval_start
        logger.info(
            f"operation=pam_evaluate_plants year={bus.env.year} duration_s={plant_eval_elapsed:.3f} plant_count={len(plants)}"
        )

        # Step 5: Evaluate plant groups for expansion opportunities
        # Plant expansions add new furnace groups to existing plants based on NPV analysis
        expansion_start = time.time()
        logger.info("[PAM] Step 5 - Evaluating plant group expansions")
        logger.debug(f"[PAM] Total plant groups in simulation: {len(bus.uow.plant_groups.list())}")
        logger.debug(
            f"[PAM] Plant groups: {[plant_group.plant_group_id for plant_group in bus.uow.plant_groups.list()]}"
        )
        # Evaluate plant groups in random order to avoid systematic biases
        for pg in random.sample(bus.uow.plant_groups.list(), len(bus.uow.plant_groups.list())):
            logger.info(f"[PAM] === Evaluating plant group {pg.plant_group_id} for expansion ===")
            logger.debug(f"[PAM] Plant group contains {len(pg.plants)} plants")

            # Aggregate financial balance across all plants in the group
            # This is used to determine if the plant group can afford expansion
            logger.debug(f"[PAM] Plant group balance: ${pg.balance:,.2f}")
            # Evaluate expansion by comparing NPV of adding a new furnace group to status quo
            if (
                cmd := pg.evaluate_expansion(
                    price_series=future_price_series,
                    capacity=Volumes(bus.env.config.expanded_capacity),
                    region_capex=bus.env.name_to_capex["greenfield"],
                    dynamic_feedstocks=bus.env.dynamic_feedstocks,
                    fopex_for_iso3=bus.env.fopex_by_country,
                    equity_share=bus.env.config.equity_share,
                    iso3_to_region_map=bus.env.country_mappings.iso3_to_region() if bus.env.country_mappings else {},
                    probabilistic_agents=bus.env.config.probabilistic_agents,
                    chosen_emissions_boundary_for_carbon_costs=bus.env.config.chosen_emissions_boundary_for_carbon_costs,
                    technology_emission_factors=bus.env.technology_emission_factors,
                    global_risk_free_rate=bus.env.config.global_risk_free_rate,
                    tech_to_product=bus.env.technology_to_product,
                    plant_lifetime=bus.env.config.plant_lifetime,
                    construction_time=bus.env.config.construction_time,
                    current_year=bus.env.year,
                    allowed_techs=bus.env.allowed_techs,
                    cost_of_debt_dict=bus.env.cost_of_debt_by_tech,
                    cost_of_equity_dict=bus.env.cost_of_equity_by_tech,
                    get_bom_from_avg_boms=bus.env.get_bom_from_avg_boms,
                    reductant_score_series=bus.env.reductant_score_series,
                    capex_subsidies=bus.env.capex_subsidies,
                    opex_subsidies=bus.env.opex_subsidies,
                    debt_subsidies=bus.env.debt_subsidies,
                    energy_subsidies=bus.env.energy_subsidies,
                    capacity_limit_steel=capacity_limit_pam_steel,
                    capacity_limit_iron=capacity_limit_pam_iron,
                    installed_capacity_in_year=bus.env.installed_capacity_in_year,
                    new_plant_capacity_in_year=bus.env.new_plant_capacity_in_year,
                    new_capacity_share_from_new_plants=bus.env.config.new_capacity_share_from_new_plants,
                    active_statuses=bus.env.config.active_statuses,
                    environment_most_common_reductant=bus.env.most_common_reductant_by_tech,
                    get_co2_headroom=bus.env.get_co2_headroom,
                    get_co2_need_by_name=bus.env.get_co2_need_by_name,
                    co2_storage_diagnostics=bus.env.co2_storage_diagnostics,
                )
            ) is not None:
                logger.info(f"[PAM] Plant group {pg.plant_group_id} expansion returned: {type(cmd).__name__}")
                if isinstance(cmd, AddFurnaceGroup):
                    # Execute expansion by adding a new furnace group to the plant
                    product_opt = bus.env.technology_to_product.get(cmd.technology_name)
                    if product_opt is None:
                        raise ValueError(f"Technology {cmd.technology_name} not found in technology_to_product")
                    expansion_product: str = product_opt
                    logger.info(
                        f"[PAM] EXECUTING EXPANSION {type(cmd).__name__} - Tech: {cmd.technology_name}, Product: {expansion_product}, Capacity: {cmd.capacity * T_TO_KT:,.0f} kt"
                    )
                    counter += 1
                    bus.handle(cmd)

        expansion_elapsed = time.time() - expansion_start
        group_count = len(bus.uow.plant_groups.list())
        logger.info(
            f"operation=pam_evaluate_expansions year={bus.env.year} duration_s={expansion_elapsed:.3f} group_count={group_count}"
        )

        # Step 6: Summary and final logging
        module_elapsed = time.time() - module_start
        logger.info(f"operation=plant_agents_model year={bus.env.year} duration_s={module_elapsed:.3f}")

        logger.info(f"[PAM] ========== PlantAgentsModel.run COMPLETED (year {bus.env.year}) ==========")
        logger.info(f"[PAM] Total commands executed: {counter}")
        logger.info(f"[PAM] Added capacities by technology: {bus.env.added_capacity}")
        logger.info(f"[PAM] Switched capacities by technology: {bus.env.switched_capacity}")
        # Log total capacity installed this year (includes both added and switched capacity)
        steel_capacity = bus.env.installed_capacity_in_year("steel")
        iron_capacity = bus.env.installed_capacity_in_year("iron")
        logger.info(
            f"[PAM] Total capacity installed this year (added + switched) - Steel: {steel_capacity * T_TO_KT:,.0f} kt, Iron: {iron_capacity * T_TO_KT:,.0f} kt"
        )

        def _capacity_by_product(capacity_map: dict[str, float], product: str) -> float:
            total = 0.0
            for tech_name, capacity in capacity_map.items():
                tech_product = bus.env.technology_to_product.get(tech_name)
                if tech_product == product:
                    total += float(capacity)
            return total

        def _log_capacity_usage(product: str, limit: float) -> None:
            new_capacity = float(bus.env.new_plant_capacity_in_year(product))
            installed_capacity = float(bus.env.installed_capacity_in_year(product))
            consumed_capacity = max(0.0, installed_capacity - new_capacity)
            switched_capacity = _capacity_by_product(bus.env.switched_capacity, product)
            expansion_capacity = max(0.0, consumed_capacity - switched_capacity)
            added_capacity = _capacity_by_product(bus.env.added_capacity, product)

            limit_kt = limit * T_TO_KT
            consumed_kt = consumed_capacity * T_TO_KT
            switched_kt = switched_capacity * T_TO_KT
            expansion_kt = expansion_capacity * T_TO_KT
            new_capacity_kt = new_capacity * T_TO_KT
            added_kt = added_capacity * T_TO_KT
            utilisation_pct = (consumed_capacity / limit * 100.0) if limit > 0 else 0.0
            switch_pct = (switched_capacity / limit * 100.0) if limit > 0 else 0.0
            expansion_pct = (expansion_capacity / limit * 100.0) if limit > 0 else 0.0
            remaining_capacity = max(0.0, limit - consumed_capacity)
            remaining_kt = remaining_capacity * T_TO_KT
            remaining_pct = (remaining_capacity / limit * 100.0) if limit > 0 else 0.0

            logger.info(
                "operation=pam_capacity_usage year=%s product=%s limit_kt=%.1f consumed_kt=%.1f utilisation_pct=%.1f "
                "expansion_kt=%.1f expansion_pct=%.1f switch_kt=%.1f switch_pct=%.1f remaining_kt=%.1f remaining_pct=%.1f "
                "new_plants_kt=%.1f added_total_kt=%.1f",
                bus.env.year,
                product,
                limit_kt,
                consumed_kt,
                utilisation_pct,
                expansion_kt,
                expansion_pct,
                switched_kt,
                switch_pct,
                remaining_kt,
                remaining_pct,
                new_capacity_kt,
                added_kt,
            )

        _log_capacity_usage("steel", float(capacity_limit_pam_steel))
        _log_capacity_usage("iron", float(capacity_limit_pam_iron))

        def _log_capacity_balance(product: str) -> None:
            previous_active = bus.env.capacity_snapshot_by_product.get(product, 0.0)
            active_capacity = 0.0
            transition_capacity = 0.0
            inactive_terminal_statuses = {
                "closed",
                "retired",
                "decommissioned",
                "discarded",
                "mothballed",
                "idled",
                "idle",
            }
            active_statuses = {status.lower() for status in bus.env.config.active_statuses}

            for plant in plants:
                for fg in plant.furnace_groups:
                    tech_product = bus.env.technology_to_product.get(fg.technology.name)
                    if tech_product != product:
                        continue
                    status = (fg.status or "").lower()
                    capacity_value = float(getattr(fg, "capacity", 0.0) or 0.0)
                    if status in active_statuses:
                        active_capacity += capacity_value
                    elif status not in inactive_terminal_statuses:
                        transition_capacity += capacity_value

            removed_capacity = sum(plant.removed_capacity_by_product.get(product, 0.0) for plant in plants)
            added_capacity = _capacity_by_product(bus.env.added_capacity, product)
            switched_capacity = _capacity_by_product(bus.env.switched_capacity, product)
            new_capacity = float(bus.env.new_plant_capacity_in_year(product))
            net_change = added_capacity - removed_capacity
            delta_active = active_capacity - previous_active

            logger.info(
                "operation=pam_capacity_balance year=%s product=%s active_kt=%.1f delta_active_kt=%.1f "
                "added_kt=%.1f removed_kt=%.1f net_change_kt=%.1f switch_kt=%.1f new_plants_kt=%.1f "
                "transition_kt=%.1f previous_active_kt=%.1f",
                bus.env.year,
                product,
                active_capacity * T_TO_KT,
                delta_active * T_TO_KT,
                added_capacity * T_TO_KT,
                removed_capacity * T_TO_KT,
                net_change * T_TO_KT,
                switched_capacity * T_TO_KT,
                new_capacity * T_TO_KT,
                transition_capacity * T_TO_KT,
                previous_active * T_TO_KT,
            )

            bus.env.capacity_snapshot_by_product[product] = active_capacity

        _log_capacity_balance("steel")
        _log_capacity_balance("iron")
