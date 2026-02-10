from typing import Any, TYPE_CHECKING
import logging
import math
from steelo.adapters.repositories import InMemoryRepository, Repository
from steelo.domain.models import (
    Year,
    Location,
    FurnaceGroup,
    CommodityAllocations,
    TradeTariff,
    LegalProcessConnector,
    AggregatedMetallicChargeConstraint,
    CountryMapping,
    Volumes,
    TransportKPI,
    Environment,
    Supplier,
)
import steelo.domain.trade_modelling.trade_lp_modelling as tlp
from steelo.service_layer.message_bus import MessageBus

if TYPE_CHECKING:
    from steelo.simulation import SimulationConfig
from collections import defaultdict
import pyomo.environ as pyo
from steelo.domain.constants import LP_TOLERANCE, T_TO_KT

logger = logging.getLogger(__name__)


def _ensure_secondary_feedstock_supplier(
    repository: Repository,
    *,
    supplier_id: str,
    commodity: str,
    location: Location,
    capacity: float,
    year: Year,
) -> Supplier:
    """
    Guarantee that a synthetic supplier exists for secondary feedstock constraints.

    The LP injects virtual supply process centers (e.g. ``bio-pci_supply_process_center``) so the
    constraint can be enforced. Downstream, those nodes must map to Supplier objects when
    allocations are translated back into domain entities. We therefore create (or update) an
    in-memory Supplier with the matching identifier and per-year capacity.
    """
    capacity_as_volume = Volumes(capacity)
    try:
        supplier = repository.suppliers.get(supplier_id)
    except KeyError:
        # Create constant production cost dictionary for all years in simulation horizon
        # Synthetic source: no additional cost beyond LP allocation costs
        from steelo.adapters.dataprocessing.excel_reader import EXCEL_READER_START_YEAR, EXCEL_READER_END_YEAR

        production_cost_by_year = {Year(y): 0.0 for y in range(EXCEL_READER_START_YEAR, EXCEL_READER_END_YEAR + 1)}

        supplier = Supplier(
            supplier_id=supplier_id,
            location=location,
            commodity=commodity,
            capacity_by_year={year: capacity_as_volume},
            production_cost_by_year=production_cost_by_year,
            mine_cost_by_year={},
            mine_price_by_year={},
        )
        add_method = getattr(repository.suppliers, "add", None)
        if callable(add_method):
            add_method(supplier)
        else:
            if hasattr(repository.suppliers, "data"):
                repository.suppliers.data[supplier_id] = supplier
            if hasattr(repository.suppliers, "items"):
                repository.suppliers.items.append(supplier)
    else:
        supplier.capacity_by_year[year] = capacity_as_volume
    return supplier


def create_process_from_furnace_group(
    furnace_group: FurnaceGroup, lp_model: tlp.TradeLPModel, config: "SimulationConfig"
) -> tlp.Process:
    """Create a production process from a furnace group for the LP model.

    Converts a furnace group's technology and feedstock specifications into a Process object
    with a bill of materials (BOM). Each primary feedstock becomes a BOMElement with input ratios,
    minimum/maximum shares, secondary feedstocks, and energy costs.

    Args:
        furnace_group: The furnace group containing technology and feedstock specifications
        lp_model: The trade LP model to add BOM elements to (reuses existing BOMs if found)
        config: Simulation configuration containing primary products list

    Returns:
        Process object of type PRODUCTION with bill of materials for the technology

    Notes:
        - Returns empty process if furnace group has no dynamic business case
        - Skips feedstocks with NaN metallic charge values
        - Logs warnings for feedstocks with no primary outputs
        - Raises ValueError if required_quantity_per_ton_of_product is None
        - Adds new BOMElements to lp_model for reuse across furnace groups
    """
    boms = []
    if furnace_group.technology.dynamic_business_case is None:
        return tlp.Process(
            name=furnace_group.technology.name,
            type=tlp.ProcessType.PRODUCTION,
            bill_of_materials=[],
        )
    for primary_feedstock in furnace_group.effective_primary_feedstocks:
        try:
            bom = lp_model.get_bom_element(primary_feedstock.name)
        except StopIteration:
            # having issues with nan values in the dynamic business cases feedstock, have to filter them out
            if type(primary_feedstock.metallic_charge) is float:
                continue
            primary_commodities = list(primary_feedstock.get_primary_outputs(config.primary_products).keys())
            # Log warning and skip this feedstock if it has no primary outputs
            if len(primary_commodities) == 0:
                logger.warning(
                    f"WARNING: Feedstock {primary_feedstock.name} has no primary outputs. "
                    f"Outputs: {list(primary_feedstock.outputs.keys())}, "
                    f"Primary products considered: {config.primary_products}. "
                    f"Skipping this feedstock."
                )
                continue
            else:
                logger.info(
                    f"Feedstock {primary_feedstock.name} has primary outputs: {primary_commodities} "
                    f"(from total outputs: {list(primary_feedstock.outputs.keys())})"
                )
            dependent_commodities = {}
            for sec_feedstock in primary_feedstock.secondary_feedstock:
                dependent_commodities[tlp.Commodity(name=sec_feedstock)] = primary_feedstock.secondary_feedstock[
                    sec_feedstock
                ]
            if primary_feedstock.required_quantity_per_ton_of_product is None:
                raise ValueError(
                    f"Required quantity per ton of product is None for feedstock {primary_feedstock.name}. It's outputs are: {primary_feedstock.outputs.keys()}"
                )
            output_commodities = [tlp.Commodity(name=oc) for oc in primary_commodities]

            # energy_vopex_by_input is calculated as $ per ton of OUTPUT (from dynamic business case)
            # but we need $ per ton of INPUT for the trade model edges (which flow in input commodity units)
            # So we divide by required_quantity_per_ton_of_product to convert from per-ton-output to per-ton-input
            energy_cost_per_ton_output = (
                furnace_group.energy_vopex_by_input[primary_feedstock.metallic_charge]
                if primary_feedstock.metallic_charge in furnace_group.energy_vopex_by_input
                else 0
            )
            energy_cost_per_ton_input = (
                energy_cost_per_ton_output / primary_feedstock.required_quantity_per_ton_of_product
            )

            bom = tlp.BOMElement(
                name=primary_feedstock.name,
                commodity=tlp.Commodity(name=primary_feedstock.metallic_charge),
                output_commodities=output_commodities,
                parameters={
                    tlp.MaterialParameters.INPUT_RATIO.value: primary_feedstock.required_quantity_per_ton_of_product,
                    tlp.MaterialParameters.MAXIMUM_RATIO.value: primary_feedstock.maximum_share_in_product,
                    tlp.MaterialParameters.MINIMUM_RATIO.value: primary_feedstock.minimum_share_in_product,
                },
                dependent_commodities=dependent_commodities,
                energy_cost=energy_cost_per_ton_input,
            )
            lp_model.add_bom_elements([bom])
        boms.append(bom)
    process = tlp.Process(
        name=furnace_group.technology.name,
        type=tlp.ProcessType.PRODUCTION,
        bill_of_materials=boms,
    )
    return process


def add_furnace_groups_as_process_centers(repository, lp_model: tlp.TradeLPModel, config: "SimulationConfig"):
    """Convert steel furnace groups into production process centers for the LP model.

    Creates ProcessCenter objects for each active furnace group in the repository. Each process
    center represents a production facility with its technology, capacity, location, and costs.

    Args:
        repository: Repository containing plants with furnace groups
        lp_model: The trade LP model to add process centers to
        config: Simulation configuration with:
            - active_statuses: List of furnace statuses to include
            - capacity_limit: Safety factor to scale capacities (typically 0.95)
            - soft_minimum_capacity_percentage: Target minimum utilization
            - primary_products: List of primary products for BOM creation

    Notes:
        - Only includes furnace groups with status in config.active_statuses
        - Reuses Process objects across furnace groups with the same technology
        - Capacity is scaled by config.capacity_limit (e.g., 0.95 for 95% availability)
        - Production cost is set to furnace_group.carbon_cost_per_unit
        - Creates new processes on-the-fly using create_process_from_furnace_group()
    """
    process_centers = []
    for plant in repository.plants.list():
        for furnace_group in plant.furnace_groups:
            if furnace_group.status.lower() not in config.active_statuses:
                continue

            process = lp_model.get_process(furnace_group.technology.name)
            if process is None:
                process = create_process_from_furnace_group(
                    furnace_group=furnace_group, lp_model=lp_model, config=config
                )
                lp_model.add_processes([process])

            process_center = tlp.ProcessCenter(
                name=furnace_group.furnace_group_id,
                process=process,
                capacity=config.capacity_limit * furnace_group.capacity,
                location=plant.location,
                production_cost=furnace_group.carbon_cost_per_unit,  # setting production cost to carbon cost per unit
                soft_minimum_capacity=config.soft_minimum_capacity_percentage,
            )
            process_centers.append(process_center)

    lp_model.add_process_centers(process_centers)


def add_demand_centers_as_process_centers(repository, lp_model: tlp.TradeLPModel, year: Year):
    """Convert demand centers into demand process centers for the LP model.

    Creates a single shared demand Process and individual ProcessCenter objects for each
    demand center in the repository. The capacity of each process center equals the regional
    demand for the specified year.

    Args:
        repository: Repository containing demand centers
        lp_model: The trade LP model to add process centers to
        year: The year for which to retrieve demand quantities

    Notes:
        - Creates a single "demand" Process shared by all demand centers
        - Each demand center becomes a separate ProcessCenter
        - Capacity is set to demand_center.demand_by_year[year]
        - Location is set to demand_center.center_of_gravity
        - BOM contains steel demand commodity (steel → steel identity mapping)
    """
    demand_process_centers = []
    steel_demand_bom_element = tlp.BOMElement(
        name="steel_demand",
        commodity=tlp.Commodity(name="steel"),
        parameters={},
        output_commodities=[tlp.Commodity(name="steel")],
    )
    demand_process = tlp.Process(
        name="demand", type=tlp.ProcessType.DEMAND, bill_of_materials=[steel_demand_bom_element]
    )
    lp_model.add_processes([demand_process])
    for demand_center in repository.demand_centers.list():
        process_center = tlp.ProcessCenter(
            name=demand_center.demand_center_id,
            process=demand_process,
            capacity=demand_center.demand_by_year[year],
            location=demand_center.center_of_gravity,
        )
        demand_process_centers.append(process_center)

    lp_model.add_process_centers(demand_process_centers)


def add_suppliers_as_process_centers(repository, lp_model: tlp.TradeLPModel, year: Year, config: "SimulationConfig"):
    """Convert suppliers into supply process centers for the LP model.

    Creates one supply Process per commodity type and individual ProcessCenter objects for each
    supplier location. Handles both string and enum commodity types.

    Args:
        repository: Repository containing suppliers
        lp_model: The trade LP model to add process centers to
        year: The year for which to retrieve supply capacities
        config: Simulation configuration (currently unused but kept for consistency)

    Notes:
        - Creates one Process per commodity (e.g., "iron_ore_supply", "coal_supply")
        - Each supplier becomes a separate ProcessCenter
        - Capacity is set to supplier.capacity_by_year[year]
        - Production cost is set to supplier.production_cost_by_year[year]
        - Handles both string and enum commodity types via isinstance checks
        - Skips suppliers if their commodity's supply process is not found
    """
    supply_process_centers = []
    supply_processes = []
    supplied_commodities = set([sup.commodity for sup in repository.suppliers.list()])
    # logger.debug(f"Suppliers found for commodities: {supplied_commodities}")

    for commodity in supplied_commodities:
        # Handle both string and enum types
        if isinstance(commodity, str):
            commodity_name = commodity
        else:
            commodity_name = commodity.value

        supply_com_bom_element = tlp.BOMElement(
            name=f"{commodity_name}_supply",
            commodity=tlp.Commodity(name=commodity_name),
            parameters={},
            output_commodities=[tlp.Commodity(name=commodity_name)],
        )
        supply_process = tlp.Process(
            name=f"{commodity_name}_supply",
            type=tlp.ProcessType.SUPPLY,
            bill_of_materials=[supply_com_bom_element],
        )
        supply_processes.append(supply_process)

    lp_model.add_processes(supply_processes)
    for supplier in repository.suppliers.list():
        # Handle both string and enum types for commodity
        if isinstance(supplier.commodity, str):
            commodity_name = supplier.commodity
        else:
            commodity_name = supplier.commodity.value
        supplier_process: tlp.Process | None = lp_model.get_process(f"{commodity_name}_supply")
        if supplier_process is None:
            continue
        lp_model.add_processes([supplier_process])
        capacity = supplier.capacity_by_year.get(year)
        if capacity is None:
            logger.warning(
                "Skipping supplier %s for year %s because no capacity is defined",
                supplier.supplier_id,
                year,
            )
            continue
        # Get production cost for this year, fallback to 0 if not defined
        production_cost = supplier.production_cost_by_year.get(year, 0.0)
        process_center = tlp.ProcessCenter(
            name=supplier.supplier_id,
            process=supplier_process,
            capacity=capacity,
            location=supplier.location,
            production_cost=production_cost,
        )
        supply_process_centers.append(process_center)

    lp_model.add_process_centers(supply_process_centers)


def enforce_trade_tariffs_on_allocations(
    message_bus: MessageBus, active_trade_tariffs: list[TradeTariff], lp_model: tlp.TradeLPModel
):
    """Apply trade tariffs (quotas and taxes) to the LP model allocations.

    Processes TradeTariff objects and converts them into quota and tax dictionaries that
    are added to the LP model. Supports absolute taxes, percentage-based taxes, and volume quotas.
    Handles wildcard countries and iron product mappings.

    Args:
        message_bus: Message bus with access to environment data (average_commodity_price_per_region)
        active_trade_tariffs: List of TradeTariff objects containing:
            - from_iso3: Source country code (or "*" for all)
            - to_iso3: Destination country code (or "*" for all)
            - commodity: Commodity name
            - quota: Volume limit (tons/year) or NaN
            - tax_absolute: Absolute tax ($/ton) or NaN
            - tax_percentage: Percentage tax (fraction) or NaN
        lp_model: The trade LP model to apply tariffs to

    Notes:
        - Iron products (hot_metal, pig_iron, dri) are mapped to "iron" for tariff lookup
        - Wildcards "*" expand to all relevant countries
        - Multiple tariffs on same route: quotas use minimum, taxes are summed
        - Percentage taxes calculated from average_commodity_price_per_region
        - NaN values are skipped
        - Tariff data added via lp_model.add_tariff_information(quota_dict, tax_dict)
    """
    from steelo.domain.constants import IRON_PRODUCTS

    quota_dict: dict[tuple[str, str, str], float] = {}
    tax_dict: dict[tuple[str, str, str], float] = {}
    average_commodity_price_per_region = message_bus.env.average_commodity_price_per_region
    for trade_tariff in active_trade_tariffs:
        if trade_tariff.commodity is not None and trade_tariff.commodity.lower() in IRON_PRODUCTS:
            cost_commodity = "iron"
        else:
            cost_commodity = trade_tariff.commodity if trade_tariff.commodity is not None else ""
        if isinstance(trade_tariff.quota, float) and not (
            math.isnan(trade_tariff.quota) or trade_tariff.quota != trade_tariff.quota
        ):
            if (
                trade_tariff.from_iso3 or "unknown",
                trade_tariff.to_iso3 or "unknown",
                trade_tariff.commodity.lower() if trade_tariff.commodity is not None else "unknown",
            ) in quota_dict and quota_dict[
                (
                    trade_tariff.from_iso3 or "unknown",
                    trade_tariff.to_iso3 or "unknown",
                    trade_tariff.commodity.lower() if trade_tariff.commodity is not None else "unknown",
                )
            ] < trade_tariff.quota:
                continue  # Skip if the quota is already set and is less than the new one
            if trade_tariff.commodity is not None:
                quota_dict[
                    (
                        trade_tariff.from_iso3 or "unknown",
                        trade_tariff.to_iso3 or "unknown",
                        trade_tariff.commodity.lower(),
                    )
                ] = trade_tariff.quota
        if isinstance(trade_tariff.tax_absolute, float) and not (
            math.isnan(trade_tariff.tax_absolute) or trade_tariff.tax_absolute != trade_tariff.tax_absolute
        ):
            if (
                trade_tariff.from_iso3 or "unknown",
                trade_tariff.to_iso3 or "unknown",
                trade_tariff.commodity.lower() if trade_tariff.commodity is not None else "unknown",
            ) in tax_dict:
                # If the tax is already set, add the new tax to the existing one
                if trade_tariff.commodity is not None:
                    tax_dict[
                        (
                            trade_tariff.from_iso3 or "unknown",
                            trade_tariff.to_iso3 or "unknown",
                            trade_tariff.commodity.lower(),
                        )
                    ] += trade_tariff.tax_absolute
            else:
                # If the tax is not set, set it to the trade tariff tax
                if trade_tariff.commodity is not None:
                    tax_dict[
                        (
                            trade_tariff.from_iso3 or "unknown",
                            trade_tariff.to_iso3 or "unknown",
                            trade_tariff.commodity.lower(),
                        )
                    ] = trade_tariff.tax_absolute
        if isinstance(trade_tariff.tax_percentage, float) and not (
            math.isnan(trade_tariff.tax_percentage) or trade_tariff.tax_percentage != trade_tariff.tax_percentage
        ):
            if (cost_commodity, trade_tariff.from_iso3 or "unknown") in average_commodity_price_per_region:
                average_price = average_commodity_price_per_region[
                    (cost_commodity, trade_tariff.from_iso3 or "unknown")
                ]
                tax_dict[
                    (
                        trade_tariff.from_iso3 or "unknown",
                        trade_tariff.to_iso3 or "unknown",
                        trade_tariff.commodity.lower() if trade_tariff.commodity is not None else "unknown",
                    )
                ] = trade_tariff.tax_percentage * average_price
            elif trade_tariff.from_iso3 == "*":
                keys_of_region = [
                    (comm, iso3) for (comm, iso3) in average_commodity_price_per_region.keys() if comm == cost_commodity
                ]
                if len(keys_of_region) == 0:
                    logger.warning(f"cannot find average prices for {cost_commodity}")
                    continue
                else:
                    for comm, iso3 in keys_of_region:
                        tax_dict[(iso3 or "unknown", trade_tariff.to_iso3 or "unknown", comm)] = (
                            trade_tariff.tax_percentage * average_commodity_price_per_region[(comm, iso3)]
                        )
            elif trade_tariff.to_iso3 == "*":
                # Universal export tariffs from specific country to all destinations
                if (cost_commodity, trade_tariff.from_iso3 or "unknown") in average_commodity_price_per_region:
                    average_price = average_commodity_price_per_region[
                        (cost_commodity, trade_tariff.from_iso3 or "unknown")
                    ]
                    # Apply tariff to exports from from_iso3 to all other countries
                    all_destination_countries = set()
                    for comm, iso3 in average_commodity_price_per_region.keys():
                        if comm == cost_commodity:
                            all_destination_countries.add(iso3)

                    for dest_iso3 in all_destination_countries:
                        if dest_iso3 != (trade_tariff.from_iso3 or "unknown"):  # Don't apply to domestic trade
                            tax_dict[
                                (
                                    trade_tariff.from_iso3 or "unknown",
                                    dest_iso3,
                                    trade_tariff.commodity.lower() if trade_tariff.commodity is not None else "unknown",
                                )
                            ] = trade_tariff.tax_percentage * average_price
                else:
                    logger.warning(
                        f"cannot find average price for {cost_commodity} in {trade_tariff.from_iso3 or 'unknown'}"
                    )
            elif cost_commodity == "*":
                keys_of_region = [
                    (comm, iso3)
                    for (comm, iso3) in average_commodity_price_per_region.keys()
                    if iso3 == (trade_tariff.from_iso3 or "unknown")
                ]
                if len(keys_of_region) == 0:
                    logger.warning(f"cannot find average prices for {trade_tariff.from_iso3 or 'unknown'}")
                    continue
                else:
                    for comm, iso3 in keys_of_region:
                        tax_dict[(trade_tariff.from_iso3 or "unknown", trade_tariff.to_iso3 or "unknown", comm)] = (
                            trade_tariff.tax_percentage * average_commodity_price_per_region[(comm, iso3)]
                        )
    # Remove entries from tax_dict where the value is NaN or infinity
    tax_dict = {key: value for key, value in tax_dict.items() if not (math.isnan(value) or math.isinf(value))}
    lp_model.add_tariff_information(quota_dict=quota_dict, tax_dict=tax_dict)


def fix_to_zero_allocations_where_distance_doesnt_match_commodity(
    trade_lp: tlp.TradeLPModel, config: "SimulationConfig"
):
    """Fix allocation variables to zero based on commodity-specific distance constraints.

    Applies distance-based restrictions to prevent certain commodities from traveling
    inappropriate distances. Some processes produce hot and cold metal variations.
    Hot metal can only travel short distances, while other products like pig iron are
    made to be transported over longer distances.

    Args:
        trade_lp: The trade LP model with allocation variables to constrain
        config: Simulation configuration with:
            - hot_metal_radius: Maximum distance for hot metal transport (km, typically ~100)
            - closely_allocated_products: Products limited to short distances (e.g., ["hot_metal"])
            - distantly_allocated_products: Products requiring longer distances (e.g., ["pig_iron", "steel"])

    Notes:
        - For distances <= hot_metal_radius: fixes distantly_allocated_products to zero
        - For distances > hot_metal_radius: fixes closely_allocated_products to zero
        - Variables are fixed using pyomo's .fix(0) method
        - This must be called after allocation variables are created but before solving
    """
    for from_pc, to_pc, comm in trade_lp.lp_model.allocation_variables:
        distance = trade_lp.get_distance(from_pc, to_pc)
        # if the distance is within our hot metal radius
        if distance <= config.hot_metal_radius:
            # and if the commodity is one that is usually transported over long distances
            if comm in config.distantly_allocated_products:
                # Set the allocation to zero
                trade_lp.lp_model.allocation_variables[(from_pc, to_pc, comm)].fix(0)
        else:  # if the distance is outside our hot metal radius
            # and the commodity can only be transported over short distances
            if comm in config.closely_allocated_products:
                # Set the allocation to zero
                trade_lp.lp_model.allocation_variables[(from_pc, to_pc, comm)].fix(0)
    return trade_lp


def adapt_allocation_costs_for_carbon_border_mechanisms(
    trade_lp: tlp.TradeLPModel, carbon_border_mechanisms: list, country_mappings: dict[str, CountryMapping], year: int
):
    """Apply carbon border adjustment mechanisms to allocation costs.

    Adjusts allocation costs for cross-border flows based on carbon cost differentials
    between trading partners. Works with any carbon border mechanism (EU CBAM, OECD, etc.).
    Prevents double-counting when countries belong to multiple regions.

    Args:
        trade_lp: The trade LP model to modify
        carbon_border_mechanisms: List of CarbonBorderMechanism objects with:
            - applying_region: Region code where mechanism applies
            - is_active(year): Method to check if mechanism is active
            - get_applying_region_countries(mappings): Method to get country list
        country_mappings: Dictionary mapping ISO3 codes to CountryMapping objects
        year: Current simulation year

    Notes:
        - Export rebates: applying_region → other flows get cost increase if carbon cost is higher
        - Import adjustments: other → applying_region flows get cost increase if carbon cost is higher
        - Only first mechanism applied to each flow (tracked via adjusted_flows set)
        - Only applies to legal allocations (defined process connectors)
        - Skips flows involving None process centers or locations
    """
    # Track which trade flows have already been adjusted to prevent double-counting
    adjusted_flows = set()
    adjustments_made = 0
    skipped_duplicates = 0

    for mechanism in carbon_border_mechanisms:
        if not mechanism.is_active(year):
            continue

        # Get countries in the applying region
        applying_countries = mechanism.get_applying_region_countries(country_mappings)

        for from_pc, to_pc, comm in trade_lp.legal_allocations:
            from_iso3 = from_pc.location.iso3
            to_iso3 = to_pc.location.iso3

            # Create a unique identifier for this trade flow
            trade_flow_key = (from_iso3, to_iso3, comm.name)

            # Skip if we've already adjusted this trade flow
            if trade_flow_key in adjusted_flows:
                skipped_duplicates += 1
                continue

            from_carbon_cost = from_pc.production_cost
            to_carbon_cost = to_pc.production_cost
            differential = to_carbon_cost - from_carbon_cost

            # Case 1: Exporting from applying region to non-applying region (export rebates)
            if from_iso3 in applying_countries and to_iso3 not in applying_countries:
                # Apply the minimum of the carbon costs due to export rebates
                if from_carbon_cost > to_carbon_cost:
                    trade_lp.lp_model.allocation_costs[(from_pc.name, to_pc.name, comm.name)] += differential
                    adjusted_flows.add(trade_flow_key)
                    adjustments_made += 1

            # Case 2: Importing into applying region from non-applying region (border adjustment)
            elif from_iso3 not in applying_countries and to_iso3 in applying_countries:
                # Apply the maximum of the carbon costs (border adjustment)
                if from_carbon_cost < to_carbon_cost:
                    trade_lp.lp_model.allocation_costs[(from_pc.name, to_pc.name, comm.name)] += differential
                    adjusted_flows.add(trade_flow_key)
                    adjustments_made += 1


def set_up_steel_trade_lp(
    message_bus: MessageBus,
    year: Year,
    config: "SimulationConfig",
    legal_process_connectors: list[LegalProcessConnector],
    active_trade_tariffs: list[TradeTariff] | None = None,
    secondary_feedstock_constraints: dict[Any, Any] | None = None,
    aggregated_metallic_charge_constraints: list[AggregatedMetallicChargeConstraint] | None = None,
    transport_kpis: list[TransportKPI] | None = None,
) -> tlp.TradeLPModel:
    """Set up the linear programming model for steel trade optimization.

    Builds a complete trade network model including raw material suppliers, production facilities
    (furnace groups), and demand centers. Applies various constraints including tariffs, distance
    limits, and feedstock ratios. The model minimizes total cost while satisfying demand and
    respecting capacity constraints.

    Args:
        message_bus: Message bus providing access to:
            - repository: Contains plants, suppliers, demand_centers
            - env: Environment with average_commodity_price_per_region
        year: The simulation year for demand and supply capacities
        config: SimulationConfig with:
            - primary_products: List of commodities (e.g., ["steel", "iron"])
            - lp_epsilon: LP solver tolerance (e.g., 1e-3)
            - capacity_limit: Production capacity safety factor (typically 0.95)
            - soft_minimum_capacity_percentage: Target minimum utilization
            - active_statuses: Furnace statuses to include (e.g., ["operating"])
            - hot_metal_radius: Max distance for hot metal (km)
            - closely_allocated_products: Short-distance products
            - distantly_allocated_products: Long-distance products
        legal_process_connectors: List of LegalProcessConnector objects defining valid
            technology-to-technology material flows
        active_trade_tariffs: Optional list of TradeTariff objects with quotas and taxes
        secondary_feedstock_constraints: Optional dict of regional scrap availability limits
        aggregated_metallic_charge_constraints: Optional list of technology-level feedstock
            ratio constraints (e.g., minimum scrap share in EAF)
        transport_kpis: Optional list of TransportKPI objects with location-specific costs
            and emissions per country-commodity pair

    Returns:
        TradeLPModel: Configured LP model ready for solving with:
            - All process centers (supply, production, demand)
            - Process connectors (valid material flows)
            - Commodities and BOMs
            - All constraints applied (tariffs, distances, ratios)
            - Transportation costs added

    Notes:
        - Model is not solved by this function - call solve_steel_trade_lp_and_return_commodity_allocations()
        - Distance constraints and tariffs are applied before model building
        - Process centers reuse Process objects when multiple furnaces have same technology
    """
    repository = message_bus.uow.repository
    lp_model = tlp.TradeLPModel(lp_epsilon=config.lp_epsilon)
    modelled_products = config.primary_products

    logger.info(f"Setting up LP model with PRIMARY_PRODUCTS: {modelled_products}")
    for commodity in modelled_products:
        lp_model.add_commodities([tlp.Commodity(name=commodity)])

    add_furnace_groups_as_process_centers(repository=repository, lp_model=lp_model, config=config)
    add_demand_centers_as_process_centers(repository=repository, lp_model=lp_model, year=year)
    secondary_supply_locations: dict[str, tlp.Location] = {}
    if secondary_feedstock_constraints:
        for commodity in secondary_feedstock_constraints:
            total_capacity = sum(
                secondary_feedstock_constraints[commodity][iso_3_tuple]
                for iso_3_tuple in secondary_feedstock_constraints[commodity]
            )
            location = tlp.Location(
                lat=52.22,
                lon=-4.53,
                country="dummy country",
                iso3="XXX",
                region="dummy region",
            )
            secondary_supply_locations[commodity] = location
            _ensure_secondary_feedstock_supplier(
                repository,
                supplier_id=f"{commodity}_supply_process_center",
                commodity=commodity,
                location=location,
                capacity=total_capacity,
                year=year,
            )

    add_suppliers_as_process_centers(repository=repository, lp_model=lp_model, year=year, config=config)

    # Add location-specific transportation costs
    if transport_kpis is not None:
        transportation_costs = []
        for kpi in transport_kpis:
            # Create TransportationCost objects from TransportKPI data
            transport_cost = tlp.TransportationCost(
                from_iso3=kpi.reporter_iso,
                to_iso3=kpi.partner_iso,
                commodity=kpi.commodity,
                cost_per_ton=kpi.transportation_cost,
            )
            transportation_costs.append(transport_cost)

        lp_model.add_transportation_costs(transportation_costs)

    if active_trade_tariffs is not None:
        enforce_trade_tariffs_on_allocations(message_bus, active_trade_tariffs, lp_model=lp_model)

    # Convert new format aggregated constraints to old format for LP model
    if aggregated_metallic_charge_constraints and len(aggregated_metallic_charge_constraints) > 0:
        converted_constraints: dict[tuple[str, str], dict[str, float]] = {}
        for constraint in aggregated_metallic_charge_constraints:
            key = (constraint.technology_name, constraint.feedstock_pattern)
            converted_constraints[key] = {}
            if constraint.minimum_share is not None:
                converted_constraints[key]["minimum"] = constraint.minimum_share
            if constraint.maximum_share is not None:
                converted_constraints[key]["maximum"] = constraint.maximum_share
        lp_model.aggregated_commodity_constraints = converted_constraints

    new_sf_constraints: defaultdict[Any, dict[Any, Any]] = defaultdict(dict)
    if secondary_feedstock_constraints:
        for commodity in secondary_feedstock_constraints:
            for iso_3_tuple in secondary_feedstock_constraints[commodity]:
                new_iso3_key = "-".join(sorted(iso_3_tuple))
                new_sf_constraints[commodity][new_iso3_key] = secondary_feedstock_constraints[commodity][iso_3_tuple]
    # Add secondary feedstock constraints to the LP model
    if secondary_feedstock_constraints is not None:
        lp_model.secondary_feedstock_constraints = new_sf_constraints

    # Create a mapping of process names to processes for easy lookup
    process_map = {p.name: p for p in lp_model.processes}

    all_process_connectors = []

    # Create process connectors based on repository data
    for connector in legal_process_connectors:
        from_process = process_map.get(connector.from_technology_name)
        to_process = process_map.get(connector.to_technology_name)

        if from_process and to_process:
            process_connector = tlp.ProcessConnector(from_process=from_process, to_process=to_process)
            all_process_connectors.append(process_connector)
        else:
            if not from_process:
                logger.debug(f"Debug: Process '{connector.from_technology_name}' not found in LP model")
            if not to_process:
                logger.debug(f"Debug: Process '{connector.to_technology_name}' not found in LP model")

    # add dummy processes AND PROCESSCENTERS! for the secondary feedstock constraints:
    if secondary_feedstock_constraints:
        for commodity in secondary_feedstock_constraints:
            # Calculate total capacity across all regions for this commodity
            total_capacity = sum(
                secondary_feedstock_constraints[commodity][iso_3_tuple]
                for iso_3_tuple in secondary_feedstock_constraints[commodity]
            )

            commodity_supply_com_bom_element = tlp.BOMElement(
                name=f"{commodity}_supply",
                commodity=tlp.Commodity(name=commodity),
                parameters={},
                output_commodities=[tlp.Commodity(name=commodity)],
            )
            # Create a dummy process for the secondary feedstock
            commodity_supply_process = tlp.Process(
                name=f"{commodity}_supply",
                type=tlp.ProcessType.SUPPLY,
                bill_of_materials=[commodity_supply_com_bom_element],
            )
            lp_model.add_processes([commodity_supply_process])

            location = secondary_supply_locations.get(
                commodity,
                tlp.Location(
                    lat=52.22,
                    lon=-4.53,
                    country="dummy country",
                    iso3="XXX",
                    region="dummy region",
                ),
            )
            commodity_supply_process_center = tlp.ProcessCenter(
                name=f"{commodity}_supply_process_center",
                process=commodity_supply_process,
                capacity=total_capacity + 1,  # Set a non-limiting capacity limit
                location=location,
            )
            lp_model.add_process_centers([commodity_supply_process_center])

            # Create a process connector from the dummy process to all production processes
            for process in lp_model.processes:
                if process.type == tlp.ProcessType.PRODUCTION:
                    commodity_supply_process_to_process = tlp.ProcessConnector(
                        from_process=commodity_supply_process, to_process=process
                    )
                    all_process_connectors.append(commodity_supply_process_to_process)

    # Validate process network connectivity before building LP model
    logger.info("🔍 Starting process network validation...")
    try:
        from .process_network_validator import validate_process_network_connectivity

        logger.info("✅ Successfully imported process network validator")

        validation_results = validate_process_network_connectivity(
            repository=repository,  # type: ignore[arg-type]
            legal_process_connectors=legal_process_connectors,
            config=config,
            current_year=year,
            verbose=True,
        )
        logger.info("✅ Process network validation completed")

        # Log critical issues
        if validation_results["isolated_technologies"]:
            logger.debug(
                f"Found {len(validation_results['isolated_technologies'])} isolated technologies in trade network"
            )
        if validation_results["missing_inputs"]:
            logger.debug(
                f"Found {len(validation_results['missing_inputs'])} technologies with missing input connections: {validation_results['missing_inputs']}"
            )

        if not validation_results["isolated_technologies"] and not validation_results["missing_inputs"]:
            logger.info("✅ No critical connectivity issues found in process network")
    except Exception as e:
        logger.error(f"❌ Process network validation failed: {e}")
        import traceback

        logger.error(f"Traceback: {traceback.format_exc()}")

    valid_process_connectors = [
        pc
        for pc in all_process_connectors
        if pc is not None and pc.from_process is not None and pc.to_process is not None
    ]
    lp_model.add_process_connectors(valid_process_connectors)

    # Get willingness to pay from environment
    willingness_to_pay_list = getattr(message_bus.env, "willingness_to_pay", [])

    lp_model.build_lp_model(willingness_to_pay_list=willingness_to_pay_list)
    lp_model = fix_to_zero_allocations_where_distance_doesnt_match_commodity(trade_lp=lp_model, config=config)

    # Apply carbon border mechanisms if available
    if (
        hasattr(message_bus.env, "carbon_border_mechanisms")
        and message_bus.env.carbon_border_mechanisms
        and message_bus.env.country_mappings is not None
    ):
        active_mechanisms = [m for m in message_bus.env.carbon_border_mechanisms if m.is_active(year)]

        if active_mechanisms:
            country_mappings_dict = {
                mapping.iso3: mapping for mapping in message_bus.env.country_mappings._mappings.values()
            }
            adapt_allocation_costs_for_carbon_border_mechanisms(
                trade_lp=lp_model,
                carbon_border_mechanisms=message_bus.env.carbon_border_mechanisms,
                country_mappings=country_mappings_dict,
                year=year,
            )
            logger.info(
                f"Applied carbon border adjustments for {len(active_mechanisms)} active mechanisms in year {year}"
            )
        else:
            logger.info(f"No active carbon border mechanisms for year {year}, skipping adjustments")
    else:
        logger.info("No carbon border mechanisms defined in environment, skipping adjustments")

    # lp_model.lp_model.secondary_feedstock_constraints.pprint()
    # print(
    #     f"Number of secondary feedstock constraints from data: {len(secondary_feedstock_constraints) if secondary_feedstock_constraints else 0}"
    # )
    # print(f"Length max feedstock parameters: {len(lp_model.lp_model.max_secondary_feedstock_allocation)}")
    # print(f"length secondary_feedstock_index_set: {len(lp_model.lp_model.secondary_feedstock_index_set)}")
    # print(
    #     f"Number of created secondary feedstock constraints in LP model: {len(lp_model.lp_model.secondary_feedstock_constraints)}"
    # )
    # exit()

    return lp_model


def solve_steel_trade_lp_and_return_commodity_allocations(
    trade_lp: tlp.TradeLPModel, repository: InMemoryRepository
) -> dict[str, CommodityAllocations]:
    """Solve the steel trade LP model and extract commodity allocations.

    Solves the configured LP model using Pyomo/HiGHS, extracts the optimal allocation
    solution, and maps results back to domain objects (Plant, FurnaceGroup, Supplier,
    DemandCenter). Handles non-optimal solutions and writes debug output.

    Args:
        trade_lp: Configured TradeLPModel ready for solving (from set_up_steel_trade_lp)
        repository: InMemoryRepository for mapping process centers back to domain entities

    Returns:
        dict[str, CommodityAllocations]: Dictionary mapping commodity names to their
            allocations. Each CommodityAllocations contains:
            - allocations: Dict of (source, destination) → Volumes
            - costs: Dict of (source, destination) → cost

    Notes:
        - Returns empty allocations if solver doesn't reach optimal solution
        - Filters out allocations below LP_TOLERANCE (1e-4 tons)
        - Writes debug output to 'trade_lp_variables.csv' in working directory
        - Maps process centers to domain objects:
            - SUPPLY → Supplier
            - PRODUCTION → (Plant, FurnaceGroup)
            - DEMAND → DemandCenter
        - Logs detailed statistics about allocation counts per commodity
    """
    result = trade_lp.solve_lp_model()

    # Check if the solution is not optimal
    if result.solver.termination_condition != pyo.TerminationCondition.optimal:
        logger.error(f"\nLP solver terminated with: {result.solver.termination_condition}")
        logger.error("Returning empty allocations due to non-optimal solution.")
        # Return empty allocations instead of crashing
        commodity_allocations = {}
        for commodity in trade_lp.commodities:
            commodity_allocations[commodity.name] = CommodityAllocations(commodity=commodity.name, allocations={})
        return commodity_allocations

    trade_lp.extract_solution()

    commodity_allocations = {}
    for commodity in trade_lp.commodities:
        commodity_allocations[commodity.name] = CommodityAllocations(commodity=commodity.name, allocations={})

    if trade_lp.allocations is None:
        logger.error("No allocations found in trade LP model. Returning empty allocations.")
        return commodity_allocations

    logger.info(f"\nTotal allocations found: {len(trade_lp.allocations.allocations)}")
    variable_file = open("trade_lp_variables.csv", "w", newline="")
    variable_file.write("from_process_center,to_process_center,commodity,allocation_value,allocation_cost\n")
    commodity_counts: dict[str, int] = {}
    for (from_pc_name, to_pc_name, commodity_name), var in trade_lp.lp_model.allocation_variables.items():
        alloc_value = pyo.value(var)
        variable_file.write(
            f"{from_pc_name},{to_pc_name},{commodity_name},{alloc_value},{trade_lp.lp_model.allocation_costs[(from_pc_name, to_pc_name, commodity_name)]}\n"
        )
        if alloc_value > 0:
            commodity_counts[commodity_name] = commodity_counts.get(commodity_name, 0) + 1
    variable_file.close()
    logger.info(f"Non-zero allocations by commodity: {commodity_counts}")

    # Iterate over all allocations from the LP model
    for (from_pc, to_pc, comm), alloc_value in trade_lp.allocations.allocations.items():
        if alloc_value <= LP_TOLERANCE:
            continue  # Skip zero or negative allocations

        if from_pc.process.type == tlp.ProcessType.SUPPLY:
            supplier = repository.suppliers.get(from_pc.name)
            source = supplier
        else:
            furnace_group_id = from_pc.name
            # Find the plant that owns the furnace group
            plant_id = furnace_group_id.split("_")[0]
            plant = repository.plants.get(plant_id)
            furnace_group = plant.get_furnace_group(furnace_group_id)
            source = (plant, furnace_group)  # type: ignore[assignment]

        if to_pc.process.type == tlp.ProcessType.DEMAND:
            demand_id = to_pc.name
            demand_center = repository.demand_centers.get(demand_id)
            destination = demand_center
        else:
            to_plant_id = to_pc.name.split("_")[0]
            to_plant = repository.plants.get(to_plant_id)
            to_furnace_group = to_plant.get_furnace_group(to_pc.name)
            destination = (to_plant, to_furnace_group)  # type: ignore[assignment]

        volume = alloc_value
        cost = trade_lp.allocations.get_allocation_cost(from_pc, to_pc, comm)

        # Add allocation to the CommodityAllocations structure
        commodity_allocations[comm.name].add_allocation(source, destination, Volumes(volume))
        commodity_allocations[comm.name].add_cost(source, destination, cost)

    return commodity_allocations


def identify_bottlenecks(
    commodity_allocations: dict[str, CommodityAllocations],
    repository: Repository,
    environment: Environment,
    year: Year,
):
    """Identify production bottlenecks from trade allocation results.

    Analyzes commodity allocations to find furnace groups operating at or near capacity,
    which may constrain the system. Useful for to analyze trade model results if behaviour
    is unexpected.

    Args:
        commodity_allocations: Dict of commodity name → CommodityAllocations from solver
        repository: Repository with plants and furnace groups for capacity lookups
        environment: Environment (currently unused but kept for compatibility)
        year: The simulation year (currently unused but kept for compatibility)

    Notes:
        - Skips scrap commodity (no production bottlenecks)
        - Checks if total allocation from a furnace group approaches its capacity
        - Logs warnings for potential bottlenecks
        - Sets potential_bottleneck_found flag (logged but not returned)
        - Currently does not return the list of bottlenecks (void return)
    """
    potential_bottleneck_found = False

    active_furnace_groups = [
        (plant, fg)
        for plant in repository.plants.list()
        for fg in plant.furnace_groups
        if fg.status.lower() in environment.config.active_statuses
    ]
    # Check raw material suppliers
    for commodity, allocations in commodity_allocations.items():
        if commodity == "scrap":
            continue  # scrap is only an issue if we don't have enough iron supply

        # Check if any sources are not suppliers - if so, skip this analysis
        has_non_supplier_sources = False
        for source in allocations.allocations.keys():
            if not hasattr(source, "supplier_id"):
                has_non_supplier_sources = True
                break

        if has_non_supplier_sources:
            continue  # Skip analysis for this commodity if sources aren't all suppliers

        all_suppliers_utilized = True
        for supplier in repository.suppliers.list():
            if supplier.commodity != commodity:
                continue
            allocations_from_supplier = allocations.get_allocations_from(supplier)
            allocated_volume = sum(allocations_from_supplier.values())
            if allocated_volume < supplier.capacity_by_year[year] * 0.99999:
                all_suppliers_utilized = False
        if all_suppliers_utilized:
            potential_bottleneck_found = True
            logger.warning(
                f"[TM BOTTLENECK ANALYSIS] All suppliers for {commodity} are fully utilized. Potential bottleneck detected."
            )

    # Check iron making
    all_iron_makers_utilized = True
    for plant, fg in active_furnace_groups:
        fg_allocated_vols: float = 0
        for com, alloc in commodity_allocations.items():
            fg_allocations = alloc.get_allocations_from((plant, fg))
            fg_allocated_vols += sum(fg_allocations.values())
        if fg_allocated_vols < fg.capacity * environment.config.capacity_limit * 0.99999:
            logger.warning(
                f"[TM BOTTLENECK ANALYSIS] Iron maker {fg.furnace_group_id} of technology {fg.technology.name} and status {fg.status} is not fully utilized."
            )
            all_iron_makers_utilized = False
    if all_iron_makers_utilized:
        potential_bottleneck_found = True
        logger.warning("[TM BOTTLENECK ANALYSIS] All iron makers are fully utilized. Potential bottleneck detected.")

    # Check steel making
    steel_allocations = commodity_allocations.get("steel")
    all_steel_makers_utilized = True
    if steel_allocations:
        for plant, fg in active_furnace_groups:
            if fg.technology.product == "steel":
                fg_allocations = steel_allocations.get_allocations_from((plant, fg))
                allocated_volume = sum(fg_allocations.values())
                if allocated_volume < fg.capacity * environment.config.capacity_limit * 0.99999:
                    logger.warning(
                        f"[TM BOTTLENECK ANALYSIS] Steel maker {fg.furnace_group_id} of technology {fg.technology.name} and status {fg.status} is not fully utilized."
                    )
                    all_steel_makers_utilized = False
    if all_steel_makers_utilized:
        potential_bottleneck_found = True
        logger.warning("[TM BOTTLENECK ANALYSIS] All steel makers are fully utilized. Potential bottleneck detected.")

    if not potential_bottleneck_found:
        logger.warning("[TM BOTTLENECK ANALYSIS] No potential bottlenecks found in steel trade allocations.")
    # Summarise supplier headroom for key metallic charges to aid diagnostics
    supplier_list = list(repository.suppliers.list())
    capacity_by_commodity: dict[str, float] = {}
    for supplier in supplier_list:
        commodity_name = str(supplier.commodity).lower()
        capacity_value = float(supplier.capacity_by_year.get(year, 0.0))
        capacity_by_commodity[commodity_name] = capacity_by_commodity.get(commodity_name, 0.0) + capacity_value

    tracked_commodities = ("io_low", "io_mid", "io_high", "scrap")
    for tracked in tracked_commodities:
        total_capacity = capacity_by_commodity.get(tracked, 0.0)
        allocated_from_suppliers: float = 0.0
        allocations_obj = commodity_allocations.get(tracked)
        if allocations_obj:
            for supplier in supplier_list:
                if str(supplier.commodity).lower() != tracked:
                    continue
                supplier_allocations = allocations_obj.get_allocations_from(supplier)
                for volume in supplier_allocations.values():
                    allocated_from_suppliers += float(volume)
        total_capacity_float = float(total_capacity)
        headroom = total_capacity_float - allocated_from_suppliers
        logger.info(
            "operation=tm_feedstock_headroom year=%s commodity=%s supplier_capacity_kt=%.1f allocated_from_suppliers_kt=%.1f headroom_kt=%.1f",
            int(year),
            tracked,
            total_capacity_float * T_TO_KT,
            allocated_from_suppliers * T_TO_KT,
            headroom * T_TO_KT,
        )
