from network_optimisation.hitchcockproblem import HitchcockProblem
from steelo.adapters.repositories import Repository
from steelo.domain.models import SteelAllocations, Year
from ..events import SteelAllocationsCalculated
from ...service_layer.message_bus import MessageBus

# TODO: this should come from SimulationConfig
CAPACITY_LIMIT = 0.95


def set_up_HP_steel_trading(
    repository: Repository, year: Year, active_statuses: list[str], global_steel_price=None
) -> HitchcockProblem:
    """
    Set up the Hitchcock Problem for steel trading.

    Args:
        repository (Repository): Repository containing data about plants and demand centers.
        year (Year): The year for which the trading is being set up.
        global_steel_price (float, optional): Global steel price to override plant-specific costs.

    Returns:
        HitchcockProblem: Configured Hitchcock Problem instance.
    """
    sources = {}
    sinks = {}
    allocation_costs = {}
    source_costs = {}
    transport_cost_per_tonne_and_km = 0.036
    for demand_center in repository.demand_centers.list():
        sinks[demand_center.demand_center_id] = demand_center.demand_by_year[year]
    for plant in repository.plants.list():
        for furnace_group in plant.furnace_groups:
            if not furnace_group.technology.product.lower() == "steel" or furnace_group.status.lower() not in [
                s.lower() for s in active_statuses
            ]:
                continue
            sources[f"{plant.plant_id}-{furnace_group.furnace_group_id}"] = CAPACITY_LIMIT * furnace_group.capacity
            source_costs[f"{plant.plant_id}-{furnace_group.furnace_group_id}"] = furnace_group.unit_production_cost
            ac_p = {}
            for demand_center in repository.demand_centers.list():
                ac_p[demand_center.demand_center_id] = transport_cost_per_tonne_and_km * plant.distance_to(
                    demand_center.center_of_gravity
                )
            allocation_costs[f"{plant.plant_id}-{furnace_group.furnace_group_id}"] = ac_p
    hp = HitchcockProblem(sources=sources, sinks=sinks, allocation_costs=allocation_costs, source_costs=source_costs)
    return hp


def allocation_from_flows(repository: Repository, mincostFlow: dict) -> SteelAllocations:
    """
    Convert flow data from the Hitchcock Problem solution into steel allocations.

    Args:
        repository (Repository): Repository containing data about plants and demand centers.
        mincostFlow (dict): The flow data from the Hitchcock Problem solution.

    Returns:
        SteelAllocations: Allocated steel flows between plants and demand centers.
    """
    allocations = {}
    for s in mincostFlow:
        for t in mincostFlow[s]:
            if mincostFlow[s][t] > 0:
                try:
                    if "-" not in s:
                        continue
                    plant_id = s.split("-")[0]
                    furnace_group_id = s.split("-")[1]
                    demand_center = repository.demand_centers.get(t)
                    plant = repository.plants.get(plant_id)

                    # Get the actual furnace group object instead of using the ID string
                    furnace_group = next(
                        (fg for fg in plant.furnace_groups if str(fg.furnace_group_id) == furnace_group_id), None
                    )

                    if furnace_group:
                        allocations[(plant, furnace_group, demand_center)] = mincostFlow[s][t]
                except KeyError:
                    continue
    return SteelAllocations(allocations=allocations)


def steel_trade_HP(
    repository: Repository, year: Year, active_statuses: list[str], global_steel_price=None, formulation="mincost"
) -> SteelAllocations:
    """
    Solve the steel trade problem using the Hitchcock Problem formulation.

    Args:
        repository (Repository): Repository containing data about plants and demand centers.
        year (Year): The year for which the trading is being set up.
        global_steel_price (float, optional): Global steel price to override plant-specific costs.
        formulation (str, optional): The formulation to use for solving ('lp' or 'mincost'). Defaults to 'lp'.

    Returns:
        SteelAllocations: Allocated steel flows between plants and demand centers.
    """
    hp = set_up_HP_steel_trading(repository, year, active_statuses, global_steel_price)
    if formulation == "lp":
        total_cost, flow = hp.solve_as_lp()
        return allocation_from_flows(repository, flow)
    elif formulation == "mincost":
        min_cost, min_cost_flow = hp.solve_as_min_cost_flow()
        return allocation_from_flows(repository, min_cost_flow)
    raise ValueError("Invalid formulation type. Use 'lp' or 'mincost'.")


def send_allocation_to_bus(allocation: SteelAllocations, bus: MessageBus):
    """
    Send the optimized steel allocations to the message bus.

    Args:
        allocation (SteelAllocations): The optimized steel allocations from steel_trade_HP

    Returns:
        bus (MessageBus): The message bus to send the event to
    """

    if (event := SteelAllocationsCalculated(trade_allocations=allocation)) is not None:  # type: ignore[arg-type]
        bus.handle(event)
