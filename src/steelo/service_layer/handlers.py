from typing import Callable, TYPE_CHECKING
from collections import defaultdict

from ..domain import events, commands, Volumes, Year, PointInTime, TimeFrame
from ..domain.models import Environment
from .unit_of_work import UnitOfWork
from datetime import datetime
import json

# Global variables moved to Environment/Config
from steelo.domain.constants import Commodities, T_TO_KT  # Keep enum as constant
from steelo.domain.trade_modelling.TM_PAM_connector import TM_PAM_connector
from steelo.domain.calculate_costs import filter_active_subsidies
from steelo.domain import diagnostics as diag
import logging

if TYPE_CHECKING:
    from .checkpoint import SimulationCheckpoint

logger = logging.getLogger(__name__)


def close_furnace_group(cmd: commands.CloseFurnaceGroup, uow: UnitOfWork):
    with uow:
        plant = uow.plants.get(cmd.plant_id)
        plant.close_furnace_group(cmd.furnace_group_id)
        uow.commit()


def renovate_furnace_group(cmd: commands.RenovateFurnaceGroup, uow: UnitOfWork, env: Environment):
    """
    Handle the RenovateFurnaceGroup command to renovate an existing furnace group.

    Retrieves the plant from the repository, delegates to the plant's renovate_furnace_group method to update
    the furnace group's lifetime and financial parameters, and commits the changes to the unit of work.

    Args:
        cmd (commands.RenovateFurnaceGroup): Command containing plant_id, furnace_group_id, financial parameters
            (capex, capex_no_subsidy, cost_of_debt, cost_of_debt_no_subsidy), and subsidy lists.
        uow (UnitOfWork): Unit of work for managing the transaction and accessing repositories.
        env (Environment): Environment containing the simulation configuration with plant_lifetime setting.

    Side Effects:
        - Updates the furnace group's lifetime, last_renovation_date, and financial parameters.
        - Changes the technology CAPEX type to "brownfield".
        - Logs a FurnaceGroupRenovated event.
        - Commits the changes to the repository.
    """
    with uow:
        plant = uow.plants.get(cmd.plant_id)
        plant.renovate_furnace_group(
            cmd.furnace_group_id,
            env.config.plant_lifetime,
            capex=cmd.capex,
            capex_no_subsidy=cmd.capex_no_subsidy,
            cost_of_debt=cmd.cost_of_debt,
            cost_of_debt_no_subsidy=cmd.cost_of_debt_no_subsidy,
            capex_subsidies=cmd.capex_subsidies,
            debt_subsidies=cmd.debt_subsidies,
        )
        uow.commit()


def change_furnace_group_status_to_switching_technology(
    cmd: commands.ChangeFurnaceGroupStatusToSwitchingTechnology, uow: UnitOfWork, env: Environment
):
    """
    Handle the ChangeFurnaceGroupStatusToSwitchingTechnology command to begin a technology transition.

    Changes the furnace group's status to 'operating switching technology', which allows the furnace to continue
    producing with its current technology while a new technology is under construction. The embedded
    ChangeFurnaceGroupTechnology command will be executed in the future at the specified year_of_switch.

    Args:
        cmd (commands.ChangeFurnaceGroupStatusToSwitchingTechnology): Command containing plant_id, furnace_group_id,
            year_of_switch (the year when the technology switch will occur), and cmd (the embedded
            ChangeFurnaceGroupTechnology command to be executed in the future).
        uow (UnitOfWork): Unit of work for managing the transaction and accessing repositories.
        env (Environment): Environment for tracking switched capacity.

    Side Effects:
        - Changes the furnace group's status to 'operating switching technology'.
        - Stores the year_of_switch and embedded command for future execution.
        - Tracks the switched capacity using the new technology name and capacity from the embedded command.
        - Commits the changes to the repository.

    Note:
        This command enables a critical transition pattern where the furnace continues production with the old
        technology during the construction period of the new technology, preventing capacity loss during the switch.
    """
    with uow:
        plant = uow.plants.get(cmd.plant_id)
        plant.change_furnace_group_status_to_switching_technology(cmd.furnace_group_id, cmd.year_of_switch, cmd.cmd)

        # Track the switched capacity - use the NEW technology name and capacity from the embedded command
        if cmd.cmd and hasattr(cmd.cmd, "technology_name") and hasattr(cmd.cmd, "capacity"):
            env.add_switched_capacity(cmd.cmd.technology_name, capacity=Volumes(cmd.cmd.capacity))

        uow.commit()


def change_furnace_group_technology(cmd: commands.ChangeFurnaceGroupTechnology, uow: UnitOfWork, env: Environment):
    """
    Handle the ChangeFurnaceGroupTechnology command to switch a furnace group to a different technology.

    Retrieves the plant from the repository, delegates to the plant's change_furnace_group_technology method to
    update the technology, bill of materials, financial parameters, and dynamic business case, then commits the changes.

    Args:
        cmd (commands.ChangeFurnaceGroupTechnology): Command containing plant_id, furnace_group_id, technology_name,
            old_technology_name, business case metrics (npv, cosa, utilisation), financial parameters (capex,
            capex_no_subsidy, cost_of_debt, cost_of_debt_no_subsidy), capacity, remaining_lifetime, bom, and subsidy lists.
        uow (UnitOfWork): Unit of work for managing the transaction and accessing repositories.
        env (Environment): Environment containing the simulation configuration with plant_lifetime setting and
            dynamic_feedstocks data.

    Side Effects:
        - Changes the furnace group's technology and associated parameters.
        - Updates the bill of materials and financial metrics.
        - Applies dynamic feedstock data if available for the new technology.
        - Logs a FurnaceGroupTechnologyChanged event.
        - Commits the changes to the repository.
    """
    with uow:
        plant = uow.plants.get(cmd.plant_id)
        plant.change_furnace_group_technology(
            furnace_group_id=cmd.furnace_group_id,
            technology_name=cmd.technology_name,
            plant_lifetime=env.config.plant_lifetime,
            dynamic_business_case=env.dynamic_feedstocks.get(
                cmd.technology_name,
                env.dynamic_feedstocks.get(cmd.technology_name.lower(), []),
            ),
            bom=cmd.bom,
            lag=0,
            capex=cmd.capex,
            capex_no_subsidy=cmd.capex_no_subsidy,
            cost_of_debt=cmd.cost_of_debt,
            cost_of_debt_no_subsidy=cmd.cost_of_debt_no_subsidy,
            capex_subsidies=cmd.capex_subsidies,
            debt_subsidies=cmd.debt_subsidies,
        )
        uow.commit()


def add_furnace_group_to_plant(cmd: commands.AddFurnaceGroup, uow: UnitOfWork, env: Environment):
    """
    Handle the AddFurnaceGroup command to add a new furnace group to an existing plant.

    Retrieves the plant from the repository, generates a new furnace with the specified technology and capacity,
    applies subsidies, and adds it to the plant as an expansion. The new furnace starts in construction status
    with zero utilization.

    Args:
        cmd (commands.AddFurnaceGroup): Command containing furnace_group_id, plant_id, technology_name, capacity,
            product, equity_needed, npv, financial parameters (capex, capex_no_subsidy, cost_of_debt,
            cost_of_debt_no_subsidy), and subsidy lists (capex_subsidies, debt_subsidies).
        uow (UnitOfWork): Unit of work for managing the transaction and accessing repositories.
        env (Environment): Environment containing the simulation configuration, current year, dynamic_feedstocks data,
            and bill of materials information.

    Side Effects:
        - Creates a new furnace group in construction status with zero utilization.
        - Sets applied_subsidies["capex"] to the capex_subsidies list from the command.
        - Sets applied_subsidies["debt"] to the debt_subsidies list from the command.
        - Adds the furnace group to the plant.
        - Increments the plant's added_capacity counter.
        - Logs a FurnaceGroupAdded event.
        - Commits the changes to the repository.

    Note:
        - Both subsidized (capex, cost_of_debt) and non-subsidized (capex_no_subsidy, cost_of_debt_no_subsidy)
          financial parameters are passed to enable tracking of subsidy impact.
        - The subsidy lists are stored in the furnace's applied_subsidies dictionary for later reference.
    """
    with uow:
        plant = uow.plants.get(cmd.plant_id)
        new_furnace = plant.generate_new_furnace(
            technology_name=cmd.technology_name,
            product=cmd.product,
            capacity=int(cmd.capacity),
            capex=cmd.capex,
            capex_no_subsidy=cmd.capex_no_subsidy,
            cost_of_debt=cmd.cost_of_debt,
            cost_of_debt_no_subsidy=cmd.cost_of_debt_no_subsidy,
            current_year=env.year,
            lag=env.config.construction_time,
            status="construction",
            util_rate=0.0,  # New capacity starts at 0% utilization, will be ramped up by trade module
            plant_lifetime=env.config.plant_lifetime,
            dynamic_business_case=env.dynamic_feedstocks.get(
                cmd.technology_name,
                env.dynamic_feedstocks.get(cmd.technology_name.lower(), []),
            ),
            equity_needed=cmd.equity_needed,
            bill_of_materials=env.get_bom_from_avg_boms(plant.energy_costs or {}, cmd.technology_name, cmd.capacity)[0],
            chosen_reductant=env.get_bom_from_avg_boms(plant.energy_costs or {}, cmd.technology_name, cmd.capacity)[2]
            or "",
        )
        # Set the subsidies on the new furnace group
        new_furnace.applied_subsidies["capex"] = cmd.capex_subsidies
        new_furnace.applied_subsidies["debt"] = cmd.debt_subsidies
        plant.add_furnace_group(new_furnace)
        plant.added_capacity = Volumes(plant.added_capacity + cmd.capacity)
        plant.furnace_group_added(
            new_furnace.furnace_group_id,
            cmd.plant_id,
            technology_name=cmd.technology_name,
            capacity=int(cmd.capacity),
            is_new_plant=False,  # This is an expansion to an existing plant
        )
        uow.commit()


def update_capacity_buildout(_event: events.FurnaceGroupAdded, uow: UnitOfWork, env: Environment):
    with uow:
        env.add_capacity(_event.technology_name, capacity=Volumes(_event.capacity))
        # Track new plant capacity separately from total capacity to monitor expansions vs new builds
        if _event.is_new_plant:
            env.add_new_plant_capacity(_event.technology_name, capacity=Volumes(_event.capacity))
        uow.commit()


def update_cost_curve(_event: events.Event, uow: UnitOfWork, env: Environment):
    with uow:
        env.update_cost_curve(
            world_furnace_groups=[
                fg for p in uow.plants.list() for fg in p.furnace_groups if (fg.status in env.config.active_statuses)
            ],
            lag=0,
        )
        uow.commit()


def update_future_cost_curve(_event: events.Event, uow: UnitOfWork, env: Environment):
    with uow:
        env.generate_cost_curve(
            world_furnace_groups=[fg for plant in uow.plants.list() for fg in plant.furnace_groups], lag=3
        )
        uow.commit()


def update_furnace_utilization_rates(event: events.SteelAllocationsCalculated, uow: UnitOfWork, env: Environment):
    trade_allocations = event.trade_allocations
    if env.config is None:
        raise ValueError("SimulationConfig is required for update_furnace_utilization_rates")

    with uow:
        fgs = [fg for p in uow.plants.list() for fg in p.furnace_groups if (fg.status in env.config.active_statuses)]
        active_bof_count = sum(1 for fg in fgs if fg.technology.name.upper() == "BOF")

        tmpc = TM_PAM_connector(
            dynamic_feedstocks_classes=env.dynamic_feedstocks,
            plants=uow.plants,  # type: ignore[arg-type]
            transport_kpis=env.transport_kpis,
        )
        tmpc.current_year = int(env.year)
        tmpc.diagnostics_active_bof_count = active_bof_count
        tmpc.set_up_network_and_propagate_costs(solved_trade_allocations=trade_allocations)
        tmpc.update_furnace_group_utilisation(fgs)
        bom_issue_count_materials, bom_issue_count_energy = tmpc.update_bill_of_materials(fgs)
        logger.info(
            f"BOM Update Summary (year {env.year}):\n"
            f"  - {bom_issue_count_materials} furnace groups retained existing material entries\n"
            f"  - {bom_issue_count_energy} furnace groups retained existing energy entries"
        )
        tmpc.update_furnace_group_emissions(fgs)
        env.allocation_and_transportation_costs = tmpc.extract_transportation_costs(fgs)

        steel_demand_dict = env.demand_dict

        # Initialize virgin iron demand if not already done (this is used for the FUTURE MARKET PRICE in cost curve)
        if env.virgin_iron_demand is None:
            env.initialize_virgin_iron_demand(
                world_suppliers_list=uow.repository.suppliers.list(), steel_demand_dict=steel_demand_dict
            )

        # Update iron_demand for current year (this is used for the CURRENT MARKET PRICE in cost curve)
        iron_demand_from_production = 0.0
        for plant in uow.plants.list():
            for fg in plant.furnace_groups:
                if fg.status in env.config.active_statuses and fg.technology.product.lower() == "iron":
                    iron_demand_from_production += fg.production

        print(f"Iron demand based on production in year {env.year} is {iron_demand_from_production * T_TO_KT:,.0f} kt")
        if env.virgin_iron_demand is not None:
            print(
                f"Iron demand for future market price is {env.virgin_iron_demand.get_demand(env.year) * T_TO_KT:,.0f} kt"
            )
        env.iron_demand = iron_demand_from_production

        # Recalculate carbon costs now that allocations/emissions are up to date
    if getattr(env, "carbon_costs", None):
        env.calculate_carbon_costs_of_furnace_groups(world_plants=uow.plants.list())
        env.calculate_average_material_costs(world_plants=uow.plants.list())
        env.generate_average_material_costs(uow.plants.list())
        env.generate_average_boms(uow.plants.list())
        uow.commit()


def finalise_iteration(
    event: events.IterationOver, env: Environment, uow: UnitOfWork, checkpoint_system: "SimulationCheckpoint"
):
    """
    Finalise the current simulation iteration and prepare for the next year.

    Steps:
    1. Increment the simulation year by the time_step_increment.
    2. Recalculate demand for the new year.
    3. Update all furnace groups:
       a. Update current year in lifetime tracking
       b. Execute scheduled technology switches if the future_switch_year matches current year
       c. Handle end-of-life transitions: close operating furnaces or switch to construction mode for technology switches
       d. Reset balance for the next iteration
       e. Update OPEX subsidies based on active subsidies for the current year

    Note: Construction → operating transition happens in simulation.py at the START of each year iteration,
    before AllocationModel runs. This ensures newly operational plants get their BOMs populated by the trade
    module before data collection occurs.
    4. Update supplier production costs (scrap pricing based on BOF hot_metal costs).
    5. Reset capacity tracking counters (added_capacity, switched_capacity, new_plant_capacity).
    6. Update environment-wide parameters:
       a. Regional capacity
       b. CAPEX reduction ratios
       c. CAPEX values
       d. Input costs for all furnace groups
       e. Technology availability
       f. Grid emissivity propagation
    7. Generate the cost curve for the new year.
    8. Commit all changes to the repository.

    Args:
        event (events.IterationOver): Event indicating the iteration has completed, containing time_step_increment.
        env (Environment): Environment containing simulation configuration, year, demand, subsidies, and cost data.
        uow (UnitOfWork): Unit of work for managing the transaction and accessing repositories.
        checkpoint_system (SimulationCheckpoint): Checkpoint system for saving simulation state.

    Side Effects:
        - Increments env.year by time_step_increment.
        - Updates furnace group statuses (construction → operating, operating → closed, etc.).
        - Executes scheduled technology switches when future_switch_year matches current year.
        - Updates furnace group applied OPEX subsidies for the current year.
        - Resets furnace group balances to zero.
        - Updates supplier production costs based on material costs.
        - Resets capacity tracking counters in env.
        - Updates CAPEX reduction ratios, CAPEX values, input costs, technology availability, and grid emissivity.
        - Regenerates the cost curve.
        - Commits all changes to the repository.

    Note:
        - Technology switches scheduled via 'operating switching technology' status are executed when
          the current year matches the future_switch_year.
        - Furnace groups in 'operating switching technology' status transition to 'construction switching technology'
          at end-of-life, allowing them to continue to the new technology construction phase.
    """
    print(f"🔄 finalise_iteration called for year {env.year} -> {env.year + event.time_step_increment}")
    logger.info(f"Finalising iteration: year {env.year} -> {env.year + event.time_step_increment}")

    # Step 1: Increment the simulation year
    env.year = Year(env.year + event.time_step_increment)

    # Step 2: Recalculate demand for the new year
    env.calculate_demand()

    # Step 3: Update all furnace groups
    with uow:
        tech_status_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        tech_bom_counts: dict[str, int] = defaultdict(int)
        active_counts: dict[str, int] = defaultdict(int)
        year_int = int(env.year)
        for plant in uow.plants.list():
            # plant.reset_capacity_changes()
            for fg in plant.furnace_groups:
                # Only process steel and iron producing furnace groups
                if fg.technology.product.lower() not in [Commodities.STEEL.value, Commodities.IRON.value]:
                    continue
                if not fg.lifetime:
                    print("No lifetime.current in furnace group", fg.furnace_group_id, fg.status, fg.capacity)
                    continue

                tech_name = fg.technology.name
                status_key = fg.status.lower()
                tech_status_counts[tech_name][status_key] += 1
                if status_key in env.config.active_statuses:
                    active_counts[tech_name] += 1
                    if fg.bill_of_materials and fg.bill_of_materials.get("materials"):
                        tech_bom_counts[tech_name] += 1

                # Step 3a: Update current year in lifetime tracking
                fg.lifetime.current = env.year

                # Step 3b: Execute scheduled technology switches if the future_switch_year matches current year
                if fg.future_switch_year == env.year and fg.future_switch_cmd is not None:
                    cmd = fg.future_switch_cmd
                    # Execute the technology change command directly during finalisation of the year
                    if isinstance(cmd, commands.ChangeFurnaceGroupTechnology):
                        plant = uow.plants.get(cmd.plant_id)
                        plant.change_furnace_group_technology(
                            furnace_group_id=cmd.furnace_group_id,
                            technology_name=cmd.technology_name,
                            plant_lifetime=env.config.plant_lifetime,
                            dynamic_business_case=env.dynamic_feedstocks.get(
                                cmd.technology_name,
                                env.dynamic_feedstocks.get(cmd.technology_name.lower(), []),
                            ),
                            bom=cmd.bom,
                            lag=0,
                            capex=cmd.capex,
                            capex_no_subsidy=cmd.capex_no_subsidy,
                            cost_of_debt=cmd.cost_of_debt,
                            cost_of_debt_no_subsidy=cmd.cost_of_debt_no_subsidy,
                            capex_subsidies=cmd.capex_subsidies,
                            debt_subsidies=cmd.debt_subsidies,
                        )

                # Step 3c: Handle end-of-life transitions
                # Close operating furnaces or switch to construction mode for technology switches
                if fg.lifetime.current > fg.lifetime.time_frame.end and fg.status.lower() in env.config.active_statuses:
                    if fg.status.lower() != "operating switching technology":
                        # Standard end-of-life: close the furnace group
                        fg.status = "closed"
                    else:
                        # Technology switch scenario: transition to construction phase of new technology
                        fg.status = "construction switching technology"
                        fg.utilization_rate = 0.0

                        # Type guard: future_switch_year should be set when status is "operating switching technology"
                        if fg.future_switch_year is None:
                            raise ValueError(
                                f"Furnace group {fg.furnace_group_id} has status 'operating switching technology' "
                                f"but future_switch_year is None"
                            )

                        # Set up the new lifetime based on when the new technology will start operating
                        year_start = Year(fg.future_switch_year)
                        year_end = Year(fg.future_switch_year + env.config.plant_lifetime)

                        fg.lifetime = PointInTime(
                            plant_lifetime=env.config.plant_lifetime,
                            current=env.year,
                            time_frame=TimeFrame(start=year_start, end=year_end),
                        )

                # Step 3e: Reset balance for the next iteration
                fg.balance = 0

                # Step 3f: Update OPEX subsidies based on active subsidies for the current year
                all_opex_subsidies = env.opex_subsidies.get(plant.location.iso3, {}).get(fg.technology.name, [])
                active_opex_subsidies = filter_active_subsidies(all_opex_subsidies, env.year)
                fg.applied_subsidies["opex"] = active_opex_subsidies

                logger.debug(
                    f"[OPEX SUBSIDIES] Updated FG {fg.furnace_group_id} "
                    f"({fg.technology.name}) in {plant.location.iso3}: "
                    f"{len(all_opex_subsidies)} total -> {len(active_opex_subsidies)} active for year {env.year}"
                )

        if diag.diagnostics_enabled() and tech_status_counts:
            for tech, statuses in tech_status_counts.items():
                total = sum(statuses.values())
                active = active_counts.get(tech, 0)
                with_bom = tech_bom_counts.get(tech, 0)
                diag.append_csv(
                    "furnace_counts.csv",
                    ["year", "technology", "total", "active", "with_bom", "status_breakdown"],
                    [year_int, tech, total, active, with_bom, json.dumps(statuses)],
                )

        # Step 4: Update supplier production costs (scrap pricing based on BOF hot_metal costs)
        for supplier in uow.repository.suppliers.list():
            if supplier.commodity == "scrap":
                pricing_source = "default"
                source_cost = 200.0  # Default scrap cost
                sample_size = getattr(env, "_diag_bof_sample_count", None)
                # Use BOF hot_metal cost if available, otherwise use hardcoded default
                if "BOF" in env.avg_boms and "hot_metal" in env.avg_boms["BOF"]:
                    source_cost = env.avg_boms["BOF"]["hot_metal"]["unit_cost"] * 0.95
                    pricing_source = "avg_bom"
                else:
                    # Fallback to hardcoded value from Excel
                    fallback_cost = env.get_fallback_material_cost(iso3=supplier.location.iso3, technology="BOF")
                    if fallback_cost is not None:
                        source_cost = fallback_cost * 0.95
                        pricing_source = "fallback"
                    else:
                        # Ultimate fallback if no cost data available
                        source_cost = 200.0
                        pricing_source = "default"
                supplier.production_cost = source_cost

                if diag.diagnostics_enabled():
                    diag.append_csv(
                        "scrap_pricing_log.csv",
                        ["year", "supplier_id", "iso3", "source", "sample_size", "production_cost"],
                        [
                            year_int,
                            getattr(supplier, "supplier_id", "unknown"),
                            supplier.location.iso3,
                            pricing_source,
                            sample_size if sample_size is not None else "",
                            source_cost,
                        ],
                    )

        env.calculate_demand()

        # Step 5: Reset capacity tracking counters
        env.update_regional_capacity(uow.plants.list())
        env.added_capacity = {}
        env.switched_capacity = {}
        env.new_plant_capacity = {}
        for plant in uow.plants.list():
            plant.reset_capacity_changes()

        # Step 6: Update environment-wide parameters
        env.update_capex_reduction_ratios()  # Step 6a
        env.update_capex()  # Step 6b
        env.set_input_cost_in_furnace_groups(world_plants=uow.plants.list())  # Step 6c
        env.update_technology_availability()  # Step 6d: Update technology availability based on the current year
        env.propagate_grid_emissivity_to_furnace_groups(plants=uow.plants.list())  # Step 6e

        # Step 7: Generate the cost curve for the new year
        # The furnace groups' economics have been updated by the `update_furnace_utilization_rates` handler.
        all_furnace_groups = [fg for p in uow.plants.list() for fg in p.furnace_groups]
        env.cost_curve = env.generate_cost_curve(all_furnace_groups, lag=0)

        # Step 8: Commit all changes to the repository
        uow.commit()

    # Save checkpoint every 5 years or based on configuration
    if env.year % 5 == 0:  # Save checkpoint every 5 years
        try:
            checkpoint_system.save_checkpoint(env.year, env, uow)
            logger.info(f"Checkpoint saved for year {env.year}")
        except Exception as e:
            logger.warning(f"Failed to save checkpoint for year {env.year}: {e}")

    print(f"Demand for year {env.year}: is {env.current_demand * T_TO_KT:,.0f} kt")
    print(
        f"Steel capacity for year {env.year}: is {sum([fg.capacity for p in uow.plants.list() for fg in p.furnace_groups if fg.status in env.config.active_statuses and fg.technology.product == 'steel']) * T_TO_KT:,.0f} kt"
    )
    logger.debug(f"finalising iteration. time: {datetime.now()}")


def add_new_business_opportunities_to_repository(cmd: commands.AddNewBusinessOpportunities, uow: UnitOfWork):
    """
    Adds business opportunities to the indi plant group with status "considered".
    """
    with uow:
        uow.plants.add_list(cmd.new_plants)
        uow.commit()


def update_status_of_furnace_group(cmd: commands.UpdateFurnaceGroupStatus, uow: UnitOfWork, env: Environment):
    """
    Updates the status of a furnace group. If the furnace group is moved into construction, it also sets the start year,
    resets the utilization rate to 0 (so that the trade module can ramp it up over time), subtracts the equity needed, and
    triggers FurnaceGroupAdded event to ensure proper (capacity) tracking.

    Note: Subsidies are updated each year while the plant is under consideration or announced - and locked in at
    construction start time.
    """
    year = env.year
    with uow:
        plant = uow.plants.get(cmd.plant_id)
        for fg in plant.furnace_groups:
            if fg.furnace_group_id == cmd.fg_id:
                old_status = fg.status
                fg.status = cmd.new_status
                if fg.status.lower() == "construction":
                    # Set the start year to become operational
                    fg.lifetime = PointInTime(
                        current=Year(year),
                        time_frame=TimeFrame(
                            start=Year(year + env.config.construction_time),
                            end=Year(year + env.config.construction_time + env.config.plant_lifetime),
                        ),
                        plant_lifetime=env.config.plant_lifetime,
                    )

                    # New plants start at 0% utilization, will be ramped up by trade module
                    fg.utilization_rate = 0.0

                    # Subtract equity needed from balance
                    capex = fg.technology.capex if fg.technology.capex is not None else 0.0
                    if capex == 0.0:
                        logger.warning(
                            f"FG {fg.furnace_group_id} in new plant {plant.plant_id} has no CAPEX set, cannot deduce equity from balance."
                        )
                    fg.balance -= env.config.equity_share * capex

                    # Trigger FurnaceGroupAdded event to update capacity tracking
                    plant.furnace_group_added(
                        furnace_group_id=fg.furnace_group_id,
                        plant_id=plant.plant_id,
                        technology_name=fg.technology.name,
                        capacity=int(fg.capacity),
                        is_new_plant=True,  # This is a new plant being constructed
                    )

                    logger.info(
                        f"[STATUS TRANSITION] {old_status} -> construction for {fg.technology.name} FG {fg.furnace_group_id} "
                    )
        uow.commit()


def update_dynamic_costs(cmd: commands.UpdateDynamicCosts, uow: UnitOfWork, env: Environment):
    """
    Updates the dynamic costs for a furnace group.

    This handler applies the yearly updated costs to a furnace group:
        - Cost of debt (with subsidies, if applicable)
        - CAPEX (with subsidies, if applicable)
        - Electricity costs from own renewable energy parc or grid
        - Hydrogen costs from own renewable energy parc or grid
        - Bill of materials with updated energy prices
    """
    with uow:
        plant = uow.plants.get(cmd.plant_id)
        for fg in plant.furnace_groups:
            if fg.furnace_group_id == cmd.furnace_group_id:
                fg.cost_of_debt = cmd.new_cost_of_debt
                fg.cost_of_debt_no_subsidy = cmd.new_cost_of_debt_no_subsidy
                fg.technology.capex = cmd.new_capex
                fg.technology.capex_no_subsidy = cmd.new_capex_no_subsidy
                fg.energy_costs["electricity"] = cmd.new_electricity_cost
                fg.energy_costs["hydrogen"] = cmd.new_hydrogen_cost
                if cmd.new_bill_of_materials is not None:
                    fg.bill_of_materials = cmd.new_bill_of_materials
        uow.commit()


def save_checkpoint_handler(
    event: events.SaveCheckpoint, env: Environment, uow: UnitOfWork, checkpoint_system: "SimulationCheckpoint"
):
    """Save a checkpoint of the current simulation state."""
    checkpoint_system.save_checkpoint(event.year, env, uow)
    # Note: CheckpointSaved event could be raised here if we had a system-level event mechanism
    # For now, the checkpoint file existence serves as confirmation


def load_checkpoint_handler(
    event: events.LoadCheckpoint, env: Environment, uow: UnitOfWork, checkpoint_system: "SimulationCheckpoint"
):
    """Load a checkpoint and restore simulation state."""
    checkpoint_data = checkpoint_system.load_checkpoint(event.year)
    if checkpoint_data:
        # TODO: Implement state restoration logic
        # This would involve deserializing the environment and repository states
        logger.info(f"Checkpoint loaded for year {event.year}")
        # Note: CheckpointLoaded event could be raised here if we had a system-level event mechanism
    else:
        logger.warning(f"No checkpoint found for year {event.year}")


EVENT_HANDLERS: dict[type[events.Event], list[Callable]] = {
    events.FurnaceGroupClosed: [update_cost_curve],
    events.FurnaceGroupTechChanged: [update_cost_curve, update_capacity_buildout],
    events.FurnaceGroupRenovated: [update_cost_curve],
    events.FurnaceGroupAdded: [update_future_cost_curve, update_capacity_buildout],
    events.SinteringCapacityAdded: [update_future_cost_curve, update_capacity_buildout],
    events.SteelAllocationsCalculated: [update_furnace_utilization_rates, update_cost_curve, update_future_cost_curve],
    events.IterationOver: [finalise_iteration, update_cost_curve],
    events.SaveCheckpoint: [save_checkpoint_handler],
    events.LoadCheckpoint: [load_checkpoint_handler],
}

COMMAND_HANDLERS: dict[type[commands.Command], Callable] = {
    commands.CloseFurnaceGroup: close_furnace_group,
    commands.RenovateFurnaceGroup: renovate_furnace_group,
    commands.ChangeFurnaceGroupStatusToSwitchingTechnology: change_furnace_group_status_to_switching_technology,
    commands.ChangeFurnaceGroupTechnology: change_furnace_group_technology,
    commands.AddFurnaceGroup: add_furnace_group_to_plant,
    commands.AddNewBusinessOpportunities: add_new_business_opportunities_to_repository,
    commands.UpdateDynamicCosts: update_dynamic_costs,
    commands.UpdateFurnaceGroupStatus: update_status_of_furnace_group,
    # commands.AddSinteringCapacityToPlant: add_sintering_capacity_to_plant,
}
