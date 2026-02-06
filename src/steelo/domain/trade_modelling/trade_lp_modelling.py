from enum import Enum
from steelo.domain.models import Location
import pyomo.environ as pyo
from typing import Tuple, Any

# LP_EPSILON is now passed as a parameter to TradeLPModel
import logging
import time
import functools
from steelo.adapters.geospatial.geospatial_toolbox import haversine_distance
# import steelo.domain.trade_modelling.willingness_to_pay as willingness_to_pay


def time_function(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        logger = logging.getLogger(f"{__name__}.{func.__name__}")
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        logger.info(f"{func.__name__} took {end_time - start_time:.4f} seconds")
        return result

    return wrapper


class MaterialParameters(Enum):
    # The ratio of the material in the product, so for example the amount of hot metal needed to produce 1 ton of steel
    INPUT_RATIO = "input_ratio"
    # The maximum ratio of the material in the product, for when dynamic business cases are used
    MAXIMUM_RATIO = "maximum_ratio"
    # Same as above, but for the minimum ratio
    MINIMUM_RATIO = "minimum_ratio"


class ProcessType(Enum):
    PRODUCTION = "production"
    SUPPLY = "supply"
    DEMAND = "demand"


class Commodity:
    """Represents a tradeable material in the steel value chain.

    Commodities include finished products (steel), semi-finished products (hot metal, pig iron),
    and raw materials (iron ore, coal, scrap). Names are automatically normalized to lowercase
    for consistent comparison.

    Attributes:
        name: Lowercase commodity name (e.g., "steel", "iron_ore", "hot_metal")
    """

    def __init__(self, name: str):
        if not isinstance(name, str):
            raise ValueError(f"Commodity name must be a string, not {type(name)}: {name}")
        self.name = name.lower()

    def __eq__(self, value):
        if type(value) is not Commodity:
            return False
        return self.name == value.name

    def __hash__(self):
        return hash(self.name)


class TransportationCost:
    """Transportation cost class for location-dependent commodity transportation costs"""

    def __init__(self, from_iso3: str, to_iso3: str, commodity: str, cost_per_ton: float):
        """
        Initialize a transportation cost for a specific route and commodity.

        Args:
            from_iso3: Source country ISO3 code
            to_iso3: Destination country ISO3 code
            commodity: Commodity name (will be normalized to lowercase)
            cost_per_ton: Transportation cost per ton of product
        """
        self.from_iso3 = from_iso3
        self.to_iso3 = to_iso3
        self.commodity = commodity.lower()
        self.cost_per_ton = cost_per_ton

    def __eq__(self, other):
        if not isinstance(other, TransportationCost):
            return False
        return self.from_iso3 == other.from_iso3 and self.to_iso3 == other.to_iso3 and self.commodity == other.commodity

    def __hash__(self):
        return hash((self.from_iso3, self.to_iso3, self.commodity))

    def __repr__(self):
        return f"TransportationCost({self.from_iso3}->{self.to_iso3}, {self.commodity}, {self.cost_per_ton})"


class BOMElement:
    """Bill of Materials element defining input-output relationships in a production process.

    Each BOM element represents one input material (commodity) that can be used to produce
    one or more output commodities. Includes input ratios, min/max shares, dependent materials,
    and energy costs.

    Attributes:
        name: Unique identifier for this BOM element
        commodity: Input material consumed by this BOM element
        output_commodities: List of products that can be produced using this input
        parameters: Dict with MaterialParameters (INPUT_RATIO, MINIMUM_RATIO, MAXIMUM_RATIO)
        dependent_commodities: Secondary materials required when using this input (e.g., flux, energy)
        energy_cost: Energy cost per ton of input material
    """

    def __init__(
        self,
        name: str,
        commodity: Commodity,
        output_commodities: list[Commodity],
        parameters: dict[str, float | None],  # Changed to allow None values as used in practice
        dependent_commodities: dict[Commodity, float] | None = None,
        energy_cost: float = 0.0,
    ):
        self.name = name
        self.parameters = parameters
        self.commodity = commodity
        # dependent commodities or secondary feedstock are commodities that are needed when using the main commodity,
        # dict to express the quantity needed for each unit of the main commodity
        self.dependent_commodities = dependent_commodities
        self.output_commodities = output_commodities
        self.energy_cost = energy_cost


class Process:
    """Represents a technology or process type in the steel value chain.

    A Process defines how materials are transformed (PRODUCTION), supplied (SUPPLY),
    or consumed (DEMAND). Multiple ProcessCenters (facilities) can share the same Process
    definition.

    Attributes:
        name: Technology name (e.g., "BF-BOF", "EAF", "iron_ore_supply", "demand")
        type: ProcessType (PRODUCTION, SUPPLY, or DEMAND)
        bill_of_materials: List of BOMElement objects defining valid input-output combinations
        products: Property returning list of all commodities this process can produce
    """

    def __init__(self, name: str, type: ProcessType, bill_of_materials: list[BOMElement]):
        self.name = name
        self.type = type
        self.bill_of_materials = bill_of_materials

    @property
    def products(self):
        """Returns unique list of all commodities producible by this process."""
        return list({commodity for bom in self.bill_of_materials for commodity in bom.output_commodities})


class ProcessCenter:
    """Represents a specific facility or location in the steel trade network.

    A ProcessCenter is a node in the LP network with a specific capacity, location, and cost.
    It uses a Process definition to specify what it can produce/supply/demand. Multiple
    ProcessCenters can share the same Process type (e.g., multiple BF-BOF plants).

    Attributes:
        name: Unique identifier (e.g., furnace_group_id, supplier_id, demand_center_id)
        process: Process definition specifying technology and bill of materials
        capacity: Maximum throughput (tons/year)
        location: Geographic location (for distance calculations)
        production_cost: Cost per ton to operate this facility (e.g., carbon cost)
        soft_minimum_capacity: Optional target minimum utilization (fraction, e.g., 0.5 for 50%)
        optimal_production: Set after solving, the optimal production quantity
    """

    def __init__(
        self,
        name: str,
        process: Process,
        capacity: float,
        location: Location,
        production_cost: float = 0.0,
        soft_minimum_capacity: float | None = None,
    ):
        self.name = name
        self.process = process
        self.capacity = capacity
        self.location = location
        self.production_cost = production_cost
        self.soft_minimum_capacity = soft_minimum_capacity
        self.optimal_production: float | None = None

    def distance_to_other_processcenter(self, other_processcenter) -> float:
        if (
            self.location.distance_to_other_iso3 is not None
            and other_processcenter.location.iso3 is not None
            and other_processcenter.location.iso3 in self.location.distance_to_other_iso3
        ):
            return self.location.distance_to_other_iso3[other_processcenter.location.iso3]
        else:
            return haversine_distance(
                [
                    self.location.lat,
                    self.location.lon,
                    other_processcenter.location.lat,
                    other_processcenter.location.lon,
                ]
            )

    def set_optimal_production(self, optimal_production: float, lp_epsilon: float = 1e-3):
        if optimal_production > self.capacity + lp_epsilon:
            raise ValueError(
                f"Optimal production of {optimal_production} is higher than capacity {self.capacity} for ProcessCenter {self.name}"
            )
        self.optimal_production = optimal_production


class Allocations:
    """Stores optimal commodity flows and costs between process centers.

    After solving the LP model, this class holds the results: how much of each commodity
    flows from each source to each destination, and the associated costs.

    Attributes:
        allocations: Dict mapping (from, to, commodity) → flow quantity (tons/year)
        allocation_costs: Optional dict mapping (from, to, commodity) → total cost
    """

    def __init__(
        self,
        allocations: dict[Tuple[ProcessCenter, ProcessCenter, Commodity], float],
        allocation_costs: dict[Tuple[ProcessCenter, ProcessCenter, Commodity], float] | None = None,
    ):
        self.allocations = allocations
        self.allocation_costs = allocation_costs

    def get_allocation(
        self, from_processcenter: ProcessCenter, to_processcenter: ProcessCenter, commodity: Commodity
    ) -> float:
        return self.allocations[(from_processcenter, to_processcenter, commodity)]

    def set_allocation(
        self, from_processcenter: ProcessCenter, to_processcenter: ProcessCenter, commodity: Commodity, amount: float
    ):
        self.allocations[(from_processcenter, to_processcenter, commodity)] = amount

    def get_total_allocation_to_processcenter(self, processcenter: ProcessCenter) -> float:
        return sum(
            self.allocations[(from_processcenter, to_processcenter, commodity)]
            for (from_processcenter, to_processcenter, commodity) in self.allocations
            if to_processcenter == processcenter
        )

    def get_allocation_cost(
        self, from_processcenter: ProcessCenter, to_processcenter: ProcessCenter, commodity: Commodity
    ):
        if self.allocation_costs is None:
            return 0.0
        return self.allocation_costs[(from_processcenter, to_processcenter, commodity)]

    def get_total_allocation_from_processcenter(self, processcenter: ProcessCenter) -> float:
        return sum(
            self.allocations[(from_processcenter, to_processcenter, commodity)]
            for (from_processcenter, to_processcenter, commodity) in self.allocations
            if from_processcenter == processcenter
        )

    def validate_allocations(self, lp_epsilon: float = 1e-3):
        # Gather all the unique from_processcenters
        unique_from_processcenters = {from_pc for (from_pc, _, commodity) in self.allocations.keys()}
        # Perform validation once per from_processcenter
        for from_pc in unique_from_processcenters:
            if from_pc is None or from_pc.capacity is None:
                continue
            total_allocation_from_pc = self.get_total_allocation_from_processcenter(from_pc)
            if total_allocation_from_pc > from_pc.capacity + lp_epsilon:
                raise ValueError(
                    f"Total allocation from {from_pc.name} is higher than capacity: {total_allocation_from_pc} > {from_pc.capacity}"
                )

    def __repr__(self):
        return f"{len(self.allocations)} allocations of which {len([all for all in self.allocations.values() if all > 0])} are positive."


class ProcessConnector:
    """Defines a valid material flow path between two process types.

    Process connectors specify which technology-to-technology flows are allowed in the model.
    For example, "BF-BOF → EAF" would allow flows from blast furnace to electric arc furnace.

    Attributes:
        from_process: Source process type (or None for wildcards)
        to_process: Destination process type (or None for wildcards)
        name: Auto-generated identifier "from_to"
    """

    def __init__(self, from_process: Process | None, to_process: Process | None):
        self.from_process = from_process
        self.to_process = to_process
        if from_process is None or to_process is None:
            self.name = "None"
        else:
            self.name = f"{from_process.name}_{to_process.name}"

    def __repr__(self):
        from_name = self.from_process.name if self.from_process else "None"
        to_name = self.to_process.name if self.to_process else "None"
        return f"ProcessConnector({from_name} -> {to_name})"


class TradeLPModel:
    """Linear programming model for optimizing global steel trade flows.

    This class builds and solves a linear programming optimization problem using Pyomo.
    It models the global steel value chain as a network of process centers (suppliers,
    production facilities, demand centers) connected by valid material flows, with the
    objective of minimizing total cost while satisfying demand and respecting constraints.

    Key Attributes:
        process_centers: List of facilities/locations in the network (nodes)
        process_connectors: List of valid technology-to-technology flows (edge rules)
        commodities: List of materials being modeled
        processes: List of technology definitions
        bom_elements: List of bill of materials elements
        lp_model: Pyomo ConcreteModel (the actual LP formulation)
        allocations: Optimal commodity flows after solving
        lp_epsilon: Solver tolerance (1e-3) for constraint validation

    Constraint Support:
        tariff_quotas_by_iso3: Volume limits on cross-border flows
        tariff_taxes_by_iso3: Additional costs on cross-border flows
        secondary_feedstock_constraints: Regional scrap availability limits
        aggregated_commodity_constraints: Technology-level feedstock ratios
        transportation_costs: Location-specific transport costs

    Penalty Costs (for slack variables):
        demand_slack_cost: Very high cost for unmet demand (10M)
        soft_minimum_capacity_slack_cost: High cost for under-utilization (100k)
    """

    def __init__(self, lp_epsilon: float = 1e-3):
        self.process_centers: list[ProcessCenter] = []
        self.process_connectors: list[ProcessConnector] = []
        self.commodities: list[Commodity] = []
        self.processes: list[Process] = []
        self.bom_elements: list[BOMElement] = []
        self.lp_model = pyo.ConcreteModel()
        self.solution_status = None
        self.optimal_solution = None
        self.allocations: Allocations | None = None
        self.demand_slack_cost = 10000
        self.soft_minimum_capacity_slack_cost = 10000
        self.real_life_costs_normalization_factor = 1
        self.tariff_quotas_by_iso3: dict[Tuple[str, str, str], float] = {}
        self.tariff_taxes_by_iso3: dict[Tuple[str, str, str], float] = {}
        self.secondary_feedstock_constraints: dict[str, Any] = {}
        self.aggregated_commodity_constraints: dict[tuple[str, str], dict[str, float]] | None = None
        self.transportation_costs: list[TransportationCost] = []
        self._transportation_cost_lookup: dict[tuple[str, str, str], float] = {}
        self.lp_epsilon = lp_epsilon

        # Solver options for performance tuning (OPT-4)
        # Default to IPM - equivalent runtime to Simplex but uses ~5GB less memory
        self.solver_options: dict[str, Any] = {
            "solver": "ipm",
            "presolve": "on",
            "scaling": "on",
            "run_crossover": "on",
        }

        # Warm-start support (OPT-2) - previous year's solution for faster convergence
        self.previous_solution: dict[tuple[str, str, str], float] | None = None

    def add_transportation_costs(self, transportation_costs: list[TransportationCost]) -> None:
        """Add transportation costs to the model."""
        self.transportation_costs.extend(transportation_costs)
        # Build a lookup dictionary for O(1) performance
        self._build_transportation_cost_lookup()

    def _build_transportation_cost_lookup(self) -> None:
        """Build a lookup dictionary for fast transportation cost retrieval."""
        self._transportation_cost_lookup = {}
        for tc in self.transportation_costs:
            key = (tc.from_iso3, tc.to_iso3, tc.commodity)
            self._transportation_cost_lookup[key] = tc.cost_per_ton

    def get_transportation_cost(self, from_iso3: str, to_iso3: str, commodity: str) -> float:
        """Get transportation cost for a specific route and commodity."""
        commodity_lower = commodity.lower()
        key = (from_iso3, to_iso3, commodity_lower)
        return self._transportation_cost_lookup.get(key, 0.0)

    def get_distance(self, from_pc_name, to_pc_name, type="haversine"):
        from_pc = next(pc for pc in self.process_centers if pc.name == from_pc_name)
        to_pc = next(pc for pc in self.process_centers if pc.name == to_pc_name)
        if type == "haversine":
            return haversine_distance(
                [
                    from_pc.location.lat,
                    from_pc.location.lon,
                    to_pc.location.lat,
                    to_pc.location.lon,
                ]
            )
        elif type == "pref_economic":
            return from_pc.distance_to_other_processcenter(to_pc)
        else:
            raise ValueError(f"Unknown distance type: {type}")

    def add_tariff_information(
        self, quota_dict: dict[Tuple[str, str, str], float], tax_dict: dict[Tuple[str, str, str], float]
    ):
        self.tariff_quotas_by_iso3 = quota_dict
        self.tariff_taxes_by_iso3 = tax_dict

    def add_commodities(self, commodities: list[Commodity]):
        commodities = [commodity for commodity in commodities if commodity is not None]
        self.commodities = self.commodities + commodities

    def add_processes(self, processes: list[Process]):
        logger = logging.getLogger(f"{__name__}.add_processes")
        for proc in processes:
            if not proc.products == []:
                for product in proc.products:
                    if product not in self.commodities and product is not None:
                        logger.info(f"Commodity {product} implicitly added to commodities")
                        self.add_commodities([product])
        self.processes = self.processes + processes

    def add_process_centers(self, process_centers: list[ProcessCenter]):
        self.process_centers = self.process_centers + process_centers

    def add_process_connectors(self, process_connectors: list[ProcessConnector]):
        self.process_connectors = self.process_connectors + process_connectors

    def add_bom_elements(self, bom_elements: list[BOMElement]):
        logger = logging.getLogger(f"{__name__}.add_bom_elements")
        for element in bom_elements:
            if element.commodity not in self.commodities:
                logger.info(f"Commodity {element.commodity.name} implicitly added to commodities")
                self.add_commodities([element.commodity])
        self.bom_elements = self.bom_elements + bom_elements

    def get_commodity(self, commodity_name: str) -> Commodity:
        return next(commodity for commodity in self.commodities if commodity.name == commodity_name)

    def get_process(self, process_name: str) -> Process | None:
        try:
            return next(process for process in self.processes if process.name == process_name)
        except StopIteration:
            # logging.warning(f"Process {process_name} not found in processes")
            return None

    def get_bom_element(self, bom_element_name: str) -> BOMElement:
        return next(bom_element for bom_element in self.bom_elements if bom_element.name == bom_element_name)

    def set_legal_allocations(self):
        # Standard legal allocations for primary commodities
        legal_allocations = [
            (from_pc, to_pc, commodity)
            for from_pc in self.process_centers
            for to_pc in self.process_centers
            for commodity in from_pc.process.products
            if commodity is not None
            and commodity in [bom_element.commodity for bom_element in to_pc.process.bill_of_materials]
            and any(
                conn.from_process == from_pc.process and conn.to_process == to_pc.process
                for conn in self.process_connectors
            )
            and from_pc.process.type != ProcessType.DEMAND
        ]

        # Add legal allocations for dependent commodities that have suppliers
        dependent_commodity_allocations = []
        for from_pc in self.process_centers:
            if from_pc.process.type == ProcessType.SUPPLY:  # Only from suppliers
                for commodity in from_pc.process.products:
                    if commodity is None:
                        continue
                    # Check if this commodity is a dependent commodity in any destination process
                    for to_pc in self.process_centers:
                        if to_pc.process.type == ProcessType.PRODUCTION:
                            for bom_element in to_pc.process.bill_of_materials:
                                if bom_element.dependent_commodities:
                                    if commodity in bom_element.dependent_commodities.keys():
                                        # Check if there's a process connector allowing this flow
                                        if any(
                                            conn.from_process == from_pc.process and conn.to_process == to_pc.process
                                            for conn in self.process_connectors
                                        ):
                                            dependent_commodity_allocations.append((from_pc, to_pc, commodity))

        legal_allocations.extend(dependent_commodity_allocations)

        self.legal_allocations = legal_allocations

    @time_function
    def add_allocation_variables_to_lp(self):
        """Add the allocation variables to the LP model, the amount of a specific material allocated from one process center to another"""
        self.lp_model.allocation_variables = pyo.Var(
            [(from_pc.name, to_pc.name, commodity.name) for (from_pc, to_pc, commodity) in self.legal_allocations],
            domain=pyo.NonNegativeReals,
            initialize=0,
        )

    @time_function
    def add_production_constraints_to_lp(self):
        """Ensure that allocation from a production center is less than or equal to the capacity."""

        def production_rule(model, pc_name):
            idx_set = model.outbound_arcs[pc_name]
            # If it's empty, we skip the constraint so Pyomo won't see a trivial boolean
            if not idx_set:
                return pyo.Constraint.Skip
            # For each production center pc_name,
            # ensure the sum of flows *leaving* that center
            # does not exceed the capacity at that center
            return pyo.quicksum(model.allocation_variables[idx] for idx in idx_set) <= model.capacities[pc_name]

        self.lp_model.production_constraints = pyo.Constraint(
            [pc.name for pc in self.process_centers if pc.process.type in [ProcessType.PRODUCTION, ProcessType.SUPPLY]],
            rule=production_rule,
        )

    @time_function
    def add_soft_minimum_capacity_constraints_to_lp(self):
        """Add a soft constraint encouraging each process center to produce at least its soft minimum capacity."""

        def soft_min_rule(model, pc_name):
            idx_set = model.outbound_arcs[pc_name]
            # If it's empty, we skip the constraint so Pyomo won't see a trivial boolean
            if not idx_set:
                return pyo.Constraint.Skip
            # For each production center pc_name,
            # ensure the sum of flows *leaving* that center
            # is encouraged to be at least the soft minimum capacity, else we pay a penalty via the slack variable
            return (
                pyo.quicksum(model.allocation_variables[idx] for idx in idx_set)
                + model.minimum_capacity_slack_variable[pc_name]
                >= model.soft_minimum_capacities[pc_name] * model.capacities[pc_name]
            )

        self.lp_model.soft_minimum_capacity_constraints = pyo.Constraint(
            [pc_name for pc_name in self.lp_model.soft_minimum_capacities],
            rule=soft_min_rule,
        )

    @time_function
    def add_minimum_ratio_constraints_to_lp(self):
        """Ensure that the minimum ratio of a material in a product is met, if specified in the bill of materials"""

        def minimum_ratio_rule(model, pc, bom_c):
            if (
                not model.allocations_that_produce_same_outputs[pc, bom_c]
                or not model.allocations_of_bom_commodity[pc, bom_c]
            ):
                return pyo.Constraint.Skip
            # direct summation
            total_same_output = pyo.quicksum(
                model.allocation_variables[idx] for idx in model.allocations_that_produce_same_outputs[pc, bom_c]
            )
            total_bom_commodity = pyo.quicksum(
                model.allocation_variables[idx] for idx in model.allocations_of_bom_commodity[pc, bom_c]
            )
            return (
                total_bom_commodity
                - total_same_output * model.bom_parameters[pc, bom_c, MaterialParameters.MINIMUM_RATIO.value]
                >= 0
            )

        self.lp_model.minimum_ratio_constraints = pyo.Constraint(
            [
                (pc.name, bom.commodity.name)
                for pc in self.process_centers
                for bom in pc.process.bill_of_materials
                if MaterialParameters.MINIMUM_RATIO.value in bom.parameters
                and bom.parameters[MaterialParameters.MINIMUM_RATIO.value] is not None
                # and self.lp_model.process_center_has_incoming_allocations_for_bom[pc] # Ioana 20.05.: taking this out, having furnace groups that materialize stuff out of thin air
            ],
            rule=minimum_ratio_rule,
        )

    @time_function
    def add_maximum_ratio_constraints_to_lp(self):
        """Ensure that the maximum ratio of a material in a product is met, if specified in the bill of materials"""

        def maximum_ratio_rule(model, pc, bom_c):
            if (
                not model.allocations_that_produce_same_outputs[pc, bom_c]
                or not model.allocations_of_bom_commodity[pc, bom_c]
            ):
                return pyo.Constraint.Skip
            # direct summation
            total_same_output = pyo.quicksum(
                model.allocation_variables[idx] for idx in model.allocations_that_produce_same_outputs[pc, bom_c]
            )
            total_bom_commodity = pyo.quicksum(
                model.allocation_variables[idx] for idx in model.allocations_of_bom_commodity[pc, bom_c]
            )
            return (
                total_bom_commodity
                - total_same_output * model.bom_parameters[pc, bom_c, MaterialParameters.MAXIMUM_RATIO.value]
                <= 0
            )

        self.lp_model.maximum_ratio_constraints = pyo.Constraint(
            [
                (pc.name, bom.commodity.name)
                for pc in self.process_centers
                for bom in pc.process.bill_of_materials
                if MaterialParameters.MAXIMUM_RATIO.value in bom.parameters
                and bom.parameters[MaterialParameters.MAXIMUM_RATIO.value] is not None
                # and self.lp_model.process_center_has_incoming_allocations_for_bom[pc] # Ioana 20.05.: taking this out, having furnace groups that materialize stuff out of thin air
            ],
            rule=maximum_ratio_rule,
        )

    @time_function
    def add_allocation_maps_to_parameters(self):
        """
        Build dictionaries to quickly look up inbound arcs and feedstock outputs.
        """
        inbound_arcs = {}  # dict of t -> list of (f, t, c)
        from_allocation_index = self.lp_model.allocation_variables.index_set()

        # 1) Precompute inbound arcs to each 't' (process center)
        from collections import defaultdict

        inbound_arcs = defaultdict(list)
        outbound_arcs = defaultdict(list)
        for f, t, c in from_allocation_index:
            inbound_arcs[t].append((f, t, c))
            outbound_arcs[f].append((f, t, c))

        # 2) Precompute feedstock outputs in a dict: feedstock_outputs[t][c] = output_commodities
        feedstock_outputs = defaultdict(dict)
        for (t, c), out_commodities in self.lp_model.primary_outputs_of_feedstock.items():
            feedstock_outputs[t][c] = out_commodities

        self.lp_model.inbound_arcs = inbound_arcs
        self.lp_model.feedstock_outputs = feedstock_outputs
        self.lp_model.outbound_arcs = outbound_arcs

    def return_potential_tariff_keys(self, from_pc_iso3, to_pc_iso3, commodity_name):
        """Return the potential keys for tariff taxes and quotas"""
        return [
            (from_pc_iso3, to_pc_iso3, commodity_name),
            ("*", to_pc_iso3, commodity_name),
            (from_pc_iso3, "*", commodity_name),
            (from_pc_iso3, to_pc_iso3, "*"),
        ]

    @time_function
    def add_allocation_keys_subject_to_sf_constraints_as_parameters_to_lp(self):
        self.lp_model.secondary_feestock_constraints_allocations = {}
        for from_pc, to_pc, commodity in self.legal_allocations:
            # Check if the commodity is a secondary feedstock subject to a constraint
            if commodity.name in self.secondary_feedstock_constraints:
                for iso3_touple in self.secondary_feedstock_constraints[commodity.name]:
                    if to_pc.location.iso3 in iso3_touple:
                        # If the to_pc's iso3 is in the tuple, it means this allocation is subject to the constraint
                        # We need to add the allocation key to the parameters
                        if commodity.name not in self.lp_model.secondary_feestock_constraints_allocations:
                            self.lp_model.secondary_feestock_constraints_allocations[commodity.name] = {}

                        if iso3_touple not in self.lp_model.secondary_feestock_constraints_allocations[commodity.name]:
                            self.lp_model.secondary_feestock_constraints_allocations[commodity.name][iso3_touple] = []

                        self.lp_model.secondary_feestock_constraints_allocations[commodity.name][iso3_touple].append(
                            (from_pc.name, to_pc.name, commodity.name)
                        )

    @time_function
    def add_secondary_feedstock_constraints_to_lp(self):
        """Add constraints for certain secondary feedstocks for certain regions"""

        def secondary_feedstock_rule(model, commodity_name, iso3_key):
            allocations = model.secondary_feestock_constraints_allocations.get(commodity_name, {}).get(iso3_key, [])
            if not allocations:
                return pyo.Constraint.Skip
            total_allocation = pyo.quicksum(model.allocation_variables[allocation] for allocation in allocations)
            max_allocation = model.max_secondary_feedstock_allocation[commodity_name, iso3_key]
            return total_allocation <= max_allocation

        self.lp_model.secondary_feedstock_index_set = pyo.Set(
            initialize=[
                (commodity_name, iso3_touple)
                for commodity_name in self.secondary_feedstock_constraints
                for iso3_touple in self.secondary_feedstock_constraints[commodity_name]
            ],
            dimen=2,
        )

        self.lp_model.max_secondary_feedstock_allocation = pyo.Param(
            self.lp_model.secondary_feedstock_index_set,
            initialize={
                (commodity_name, iso3_key): self.secondary_feedstock_constraints[commodity_name][iso3_key]
                for commodity_name in self.secondary_feedstock_constraints
                for iso3_key in self.secondary_feedstock_constraints[commodity_name]
            },
            mutable=False,
        )

        self.lp_model.secondary_feedstock_constraints = pyo.Constraint(
            self.lp_model.secondary_feedstock_index_set, rule=secondary_feedstock_rule
        )

    @time_function
    def add_tariff_quotas_and_tax_as_parameters(self):
        """Add the tariff quotas as parameters to the LP model. Needed for the quota constraints."""
        self.lp_model.tariff_tax = {}
        self.lp_model.quota_allocations = {}
        for from_pc, to_pc, commodity in self.legal_allocations:
            ## TARIFF TAXES:
            # initiate at 0:
            self.lp_model.tariff_tax[from_pc.name, to_pc.name, commodity.name] = 0
            # Taxes - these will be extra allocation costs:
            potential_tariff_keys = self.return_potential_tariff_keys(
                from_pc.location.iso3, to_pc.location.iso3, commodity.name
            )
            for key in potential_tariff_keys:
                if key in self.tariff_taxes_by_iso3:  # if the potential key is in the tariff taxes
                    # Add the tariff tax to the allocation cost
                    self.lp_model.tariff_tax[from_pc.name, to_pc.name, commodity.name] += self.tariff_taxes_by_iso3[key]

            # QUOTAS - these will be constraints on a sum of allocations. We need to find all allocations subject to the quota:
            # TODO: We can't do quotas on regions yet
            for key in potential_tariff_keys:
                if key in self.tariff_quotas_by_iso3:
                    if key not in self.lp_model.quota_allocations:
                        self.lp_model.quota_allocations[key] = []
                    self.lp_model.quota_allocations[key].append((from_pc.name, to_pc.name, commodity.name))

    @time_function
    def add_minimum_and_maximum_ratio_constraints_to_lp(self):
        """
        Example re-implementation, showing how to speed up the sets:
        1) Build inbound_arcs and feedstock_outputs one time.
        2) Use them to quickly fill same_output_set and bom_commodity_set.
        """
        self.lp_model.allocations_that_produce_same_outputs = {}
        self.lp_model.allocations_of_bom_commodity = {}

        inbound_arcs = self.lp_model.inbound_arcs
        feedstock_outputs = self.lp_model.feedstock_outputs

        for pc in self.process_centers:
            # skip if not production
            if pc.process.type != ProcessType.PRODUCTION:
                continue

            pc_name = pc.name

            # For quick lookups, store a short variable
            # feedstock_outputs_for_pc is a dict: c -> primary_outputs_of_feedstock[pc_name, c]
            feedstock_outputs_for_pc = feedstock_outputs[pc_name]

            # arcs_into_pc is the list of (f, t, c) that come into pc_name
            arcs_into_pc = inbound_arcs[pc_name]

            for bom in pc.process.bill_of_materials:
                bom_c = bom.commodity.name
                same_output_set = set()
                bom_commodity_set = set()

                # Get the "target output commodity" for bom_c on this PC
                # (may be None if (pc_name, bom_c) not in feedstock_outputs_for_pc)
                reference_outputs = feedstock_outputs_for_pc.get(bom_c, None)

                # Loop only over arcs leading into pc_name
                for f, t, c in arcs_into_pc:
                    # same_output_set: feedstock that yields the same "primary output" as bom_c
                    if c in feedstock_outputs_for_pc and feedstock_outputs_for_pc[c] == reference_outputs:
                        same_output_set.add((f, t, c))

                    # bom_commodity_set: arcs for which c == bom_c
                    if c == bom_c:
                        bom_commodity_set.add((f, t, c))

                self.lp_model.allocations_that_produce_same_outputs[pc_name, bom_c] = same_output_set
                self.lp_model.allocations_of_bom_commodity[pc_name, bom_c] = bom_commodity_set

        # After building these sets, call max/min ratio constraints
        self.add_maximum_ratio_constraints_to_lp()
        self.add_minimum_ratio_constraints_to_lp()

    @time_function
    def add_bom_energy_costs_as_parameter_to_lp(self):
        """Add the energy costs as parameters to the LP model. Needed for the objective function."""
        self.lp_model.bom_energy_costs = {}
        for process_center in self.process_centers:
            process = process_center.process
            if process is not None and process.type == ProcessType.PRODUCTION:
                for bom_element in process_center.process.bill_of_materials:
                    if bom_element.energy_cost is not None:
                        self.lp_model.bom_energy_costs[process_center.name, bom_element.commodity.name] = (
                            bom_element.energy_cost
                        )

    @time_function
    def add_allocation_costs_as_parameters_to_lp(self):
        """Add the allocation costs as parameters to the LP model. Needed for the objective function."""
        logger = logging.getLogger(f"{__name__}.add_allocation_costs_as_parameters_to_lp")
        self.lp_model.allocation_costs = {}
        for from_pc, to_pc, commodity in self.legal_allocations:
            # Get location-specific transportation cost
            transportation_cost = self.get_transportation_cost(
                from_pc.location.iso3, to_pc.location.iso3, commodity.name
            )
            self.lp_model.allocation_costs[from_pc.name, to_pc.name, commodity.name] = (
                transportation_cost  # Location-specific transportation cost per ton
                + self.lp_model.bom_energy_costs.get((to_pc.name, commodity.name), 0)
                + self.lp_model.tariff_tax[from_pc.name, to_pc.name, commodity.name]
                + from_pc.production_cost  # Production cost (carbon cost) at the source process center
                # - self.lp_model.willingness_to_pay[to_pc.name]
            )
        # add a check to ensure allocation costs aren't insane:
        for key in self.lp_model.allocation_costs:
            if self.lp_model.allocation_costs[key] >= (
                self.demand_slack_cost * 0.2
            ):  # if higher than 20% of demand slack
                logger.warning(
                    f"High allocation cost {self.lp_model.allocation_costs[key]} for allocation {key}, "
                    f"higher than 20% of demand slack cost {self.demand_slack_cost}. "
                    "Automatically setting demand slack cost higher."
                )

    # @time_function
    # def add_willingness_to_pay_as_parameters_to_lp(self):
    #     """Add the willingness to pay as parameters to the LP model. Needed for the objective function."""
    #     self.lp_model.willingness_to_pay = {}
    #     for process_center in self.process_centers:
    #         if process_center.process.type == ProcessType.DEMAND and process_center.location.iso3 is not None:
    #             self.lp_model.willingness_to_pay[process_center.name] = willingness_to_pay.get_willingness_to_pay(
    #                 process_center.location.iso3
    #             )
    #         else:
    #             self.lp_model.willingness_to_pay[process_center.name] = 0

    @time_function
    def add_objective_function_to_lp(self):
        """Add the objective function to the LP model, i.e. the sum of production costs and transport costs"""
        allocation_cost_values = list(self.lp_model.allocation_costs.values())
        largest_allocation_cost = max(allocation_cost_values) if allocation_cost_values else 1.0
        demand_slack_cost_adjusted = max(self.demand_slack_cost, largest_allocation_cost * 5)
        self.lp_model.objective = pyo.Objective(
            expr=sum(
                self.lp_model.allocation_variables[from_pc, to_pc, commodity]
                * (
                    self.lp_model.allocation_costs[from_pc, to_pc, commodity]
                    / self.real_life_costs_normalization_factor
                )
                for (from_pc, to_pc, commodity) in self.lp_model.allocation_variables
            )
            + sum(
                self.lp_model.demand_slack_variable[dc.name] * demand_slack_cost_adjusted
                for dc in self.process_centers
                if dc.process.type == ProcessType.DEMAND
            )
            + sum(
                self.lp_model.minimum_capacity_slack_variable[pc_name] * self.soft_minimum_capacity_slack_cost
                for pc_name in self.lp_model.soft_minimum_capacities
            ),
            sense=pyo.minimize,
        )

    def add_bool_if_process_has_incoming_connections(self):
        """Check if a process has incoming connections through the process_connectors logic"""
        self.lp_model.process_has_incoming_connections = {}
        for process in self.processes:
            self.lp_model.process_has_incoming_connections[process] = any(
                conn.to_process == process for conn in self.process_connectors
            )

    def add_bool_if_process_center_has_incoming_allocations_for_bom_to_params(self):
        """Check if a process has incoming allocations for the bill of materials it needs to produce"""
        self.lp_model.process_center_has_incoming_allocations_for_bom = {}
        for process_center in self.process_centers:
            self.lp_model.process_center_has_incoming_allocations_for_bom[process_center] = any(
                (from_pc_name, to_pc_name, commodity_name) in self.lp_model.allocation_variables
                for from_pc_name, to_pc_name, commodity_name in self.lp_model.allocation_variables
                if to_pc_name == process_center.name
                and (
                    to_pc_name,
                    commodity_name,
                    MaterialParameters.INPUT_RATIO.value,
                )
                in self.lp_model.bom_parameters
            )

    @time_function
    def add_bom_inflow_constraints_to_lp(self):
        """
        Ensure that every process center gets the inflow of material needed to produce
        the output products, as specified by input ratios in the bill of materials.
        """
        import pyomo.environ as pyo
        from collections import defaultdict

        m = self.lp_model  # short reference

        # Common constants/aliases (fewer attribute lookups in hot paths)
        INPUT = MaterialParameters.INPUT_RATIO.value
        PROD = ProcessType.PRODUCTION.value

        allocation_index = m.allocation_variables.index_set()
        product_pc = m.product_of_process_center
        bom_params = m.bom_parameters
        primary_outputs = m.primary_outputs_of_feedstock
        outbound_arcs = m.outbound_arcs
        pc_type = m.process_center_type

        # ---------- Pass 0: pre-index outbound arcs by (pc, commodity) ----------
        # outbound_by_pc_and_commodity[pc][commodity] -> list[(from_pc, to_pc, commodity)]
        outbound_by_pc_and_commodity = {}
        for pc, arcs in outbound_arcs.items():
            d = defaultdict(list)
            # arcs are tuples (from_pc, to_pc, commodity)
            for arc in arcs:
                # arc[2] is commodity
                d[arc[2]].append(arc)
            outbound_by_pc_and_commodity[pc] = d

        # ---------- Pass 1: group inbound allocations by (pc, outputs_group) ----------
        # Use frozenset for outputs_group to avoid sorting/joining strings.
        incoming_by_key = defaultdict(list)  # (pc, outputs_group) -> list[(f, t, c)]
        outputs_group_cache = {}  # (t, c) -> frozenset(outputs)
        ratio_inv = {}  # (t, c) -> 1 / bom_params[t, c, INPUT]

        # Iterate once over all (from_pc, to_pc, commodity) in the allocation variable index
        for f, t, c in allocation_index:
            # Quick filters match your original logic
            if f not in product_pc:
                continue
            if (t, c, INPUT) not in bom_params:
                continue
            if (t, c) not in primary_outputs:
                continue

            # Cache outputs group per (t, c)
            outs = outputs_group_cache.get((t, c))
            if outs is None:
                outs = frozenset(primary_outputs[t, c])
                outputs_group_cache[(t, c)] = outs

            # Cache reciprocal coefficient per (t, c)
            if (t, c) not in ratio_inv:
                ratio_inv[(t, c)] = 1.0 / bom_params[t, c, INPUT]

            incoming_by_key[(t, outs)].append((f, t, c))

        # Expose for debugging parity with the original attribute names
        m.allocations_incoming_ingredients = incoming_by_key
        m._bom_ratio_inv = ratio_inv

        # ---------- Pass 2: build outgoing sets once per (pc, outputs_group) ----------
        outgoing_by_key = {}
        for pc, outs_group in incoming_by_key.keys():
            # Gather all outbound arcs of 'pc' whose commodity is in outs_group
            arcs_by_comm = outbound_by_pc_and_commodity.get(pc, {})
            if not arcs_by_comm:
                outgoing_by_key[(pc, outs_group)] = []
                continue

            # Union the per-commodity arc lists without scanning the entire arc list each time
            bucket = []
            for comm in outs_group:
                lst = arcs_by_comm.get(comm)
                if lst:
                    bucket.extend(lst)
            outgoing_by_key[(pc, outs_group)] = bucket

        m.allocations_outgoing_product = outgoing_by_key

        # ---------- Constraint rule ----------
        def bom_inflow_rule(model, pc, outs_group):
            incoming_set = model.allocations_incoming_ingredients.get((pc, outs_group), ())
            outgoing_set = model.allocations_outgoing_product.get((pc, outs_group), ())

            # Preserve original skip behavior
            if not incoming_set and not outgoing_set:
                return pyo.Constraint.Skip

            produced = pyo.quicksum(
                model.allocation_variables[f, t, c] * model._bom_ratio_inv[t, c] for (f, t, c) in incoming_set
            )
            sent_out = pyo.quicksum(model.allocation_variables[f, t, c] for (f, t, c) in outgoing_set)
            return produced - sent_out == 0

        # ---------- Index and build constraint ----------
        constraint_keys = [(pc, outs_group) for (pc, outs_group) in incoming_by_key.keys() if pc_type[pc] == PROD]

        m.bom_inflow_constraints = pyo.Constraint(constraint_keys, rule=bom_inflow_rule)

    @time_function
    def add_dependent_commodities_consistency_constraints_to_lp(self):
        """Add constraints ensuring dependent materials flow in correct ratios with primary inputs.

        Enforces that secondary materials (like limestone, flux, olivine) flow into process centers
        in the exact ratios required by their primary inputs (like iron ore). For example, if a BOM
        specifies that iron ore requires 0.2 tons of limestone per ton of iron ore, this constraint
        ensures exactly that amount of limestone flows in.

        Constraint formulation:
            For each (process_center, dependent_commodity):
                sum(incoming_dependent_commodity) - sum(ratio * incoming_primary_input) == 0

        Important behavior:
            - Skips constraints where the dependent commodity has NO suppliers/sources
            - This prevents infeasibility when dependent materials are defined in BOMs but unavailable
            - Logs warnings for each skipped constraint (no suppliers found)
            - Only creates constraints for process centers with incoming allocations

        Example:
            BF requires iron ore (primary) + 0.2 tons limestone (dependent) per ton of iron ore
            If 150 tons iron ore flows in, exactly 30 tons limestone must flow in
            If limestone has no suppliers, constraint is skipped with warning

        Notes:
            - Dependent commodities are defined in BOMElement.dependent_commodities dict
            - Constraint is equality (==), not inequality, requiring exact ratios
            - Multiple primary inputs can share the same dependent commodity
            - Process centers with no incoming connections are still checked (per Ioana's 20.05 note)
        """
        logger = logging.getLogger(f"{__name__}.add_dependent_commodities_consistency_constraints_to_lp")
        model = self.lp_model

        # 1) Create dictionaries of sets
        model.allocations_of_dependent_commodities_to_pc = {}
        model.allocations_of_boms_that_need_dependent_commodity_to_pc = {}
        # model.boms_dependent_on_commodity_at_pc = {}

        for pc in self.process_centers:
            for bom_e in pc.process.bill_of_materials:
                if bom_e.dependent_commodities is None:
                    continue
                for dep_com in bom_e.dependent_commodities:
                    dep_com_name = dep_com.name
                    bom_c_name = bom_e.commodity.name
                    pc_name = pc.name

                    # Only build sets if the constraints are relevant.
                    if (
                        pc.process.type == ProcessType.PRODUCTION
                    ):  # model.process_has_incoming_connections[pc.process] and #Ioana 20.05.: taking this out, having furnace groups that materialize stuff out of thin air
                        in_dep_set = set()
                        in_bom_set = set()

                        # Loop once over all possible (f, t, c) in the model
                        for f, t, c in model.inbound_arcs[pc_name]:
                            # Subset #1: allocations that send out 'dep_com_name' to 'pc_name'
                            #   to_pc_name == pc_name
                            #   commodity_name == dep_com_name
                            if c == dep_com_name:
                                in_dep_set.add((f, t, c))
                            # Subset #2: allocations that send out 'bom_c_name' to 'pc_name'
                            #   to_pc_name == pc_name
                            #   commodity_name == bom_c_name
                            if c == bom_c_name:
                                in_bom_set.add((f, t, c))

                        # Store them on the model
                        model.allocations_of_dependent_commodities_to_pc[pc_name, dep_com_name] = in_dep_set
                        if (
                            pc_name,
                            dep_com_name,
                        ) not in model.allocations_of_boms_that_need_dependent_commodity_to_pc:
                            model.allocations_of_boms_that_need_dependent_commodity_to_pc[pc_name, dep_com_name] = (
                                in_bom_set
                            )
                        else:
                            model.allocations_of_boms_that_need_dependent_commodity_to_pc[pc_name, dep_com_name] |= (
                                in_bom_set
                            )

        def dependent_commodities_rule(model, pc_name, dep_com_name):
            # Skip if there are no possible sources for the dependent commodity
            possible_sources = model.allocations_of_dependent_commodities_to_pc.get((pc_name, dep_com_name), [])
            if not possible_sources:
                return pyo.Constraint.Skip

            # Summation over all (f, t, c) in the produce-output set
            amount_of_needed_dependent_commodity = pyo.quicksum(
                [
                    model.dependent_commodities[pc_name, c, dep_com_name] * model.allocation_variables[f, t, c]
                    for (f, t, c) in model.allocations_of_boms_that_need_dependent_commodity_to_pc.get(
                        (pc_name, dep_com_name), []
                    )
                    if (pc_name, c, dep_com_name) in model.dependent_commodities
                    # if c in model.boms_dependent_on_commodity_at_pc.get((pc_name, dep_com_name), [])
                ]
            )
            amount_of_dependent_commodity_flowing_into_pc = pyo.quicksum(
                [model.allocation_variables[f, t, c] for (f, t, c) in possible_sources]
            )
            return amount_of_dependent_commodity_flowing_into_pc - amount_of_needed_dependent_commodity == 0

        # Build constraint index
        constraint_index = [
            (pc.name, dep_com.name)
            for pc in self.process_centers
            for bom in pc.process.bill_of_materials
            # Filter out BOMs that have None for dependent_commodities:
            if bom.dependent_commodities is not None
            for dep_com in bom.dependent_commodities
            if dep_com is not None
            and bom.commodity is not None
            # and self.lp_model.process_center_has_incoming_allocations_for_bom[pc] #Ioana 20.05.: taking this out, having furnace groups that materialize stuff out of thin air
            and (pc.name, bom.commodity.name, dep_com.name) in model.dependent_commodities
        ]

        logger.info(f"Creating {len(constraint_index)} dependent commodity constraints")

        # Count how many will actually be enforced (have sources)
        enforced_count = sum(
            1
            for pc_name, dep_com_name in constraint_index
            if model.allocations_of_dependent_commodities_to_pc.get((pc_name, dep_com_name), [])
        )
        skipped_count = len(constraint_index) - enforced_count

        logger.info(f"  {enforced_count} will be enforced (have suppliers)")
        if skipped_count > 0:
            logger.warning(f"  {skipped_count} will be skipped (no suppliers for dependent commodity)")

        model.dependent_commodities_constraints = pyo.Constraint(
            constraint_index,
            rule=dependent_commodities_rule,
        )

    @time_function
    def add_dependent_commodities_as_parameters_to_lp(self):
        """Add the dependent commodities as parameters to the LP model. Needed for the dependent commodities constraints."""
        self.lp_model.dependent_commodities = {}
        for pc in self.process_centers:
            for bom_e in pc.process.bill_of_materials:
                if bom_e.dependent_commodities is not None:
                    for dep_com in bom_e.dependent_commodities:
                        self.lp_model.dependent_commodities[pc.name, bom_e.commodity.name, dep_com.name] = (
                            bom_e.dependent_commodities[dep_com]
                        )

    @time_function
    def add_bom_parameters_as_parameters_to_lp(self):
        """Add the input ratios of the bill of materials as parameters to the LP model. Needed for add_bom_inflow_constraints_to_lp."""
        self.lp_model.bom_parameters = {}
        for process_center in self.process_centers:
            process = process_center.process
            for bom_element in process.bill_of_materials:
                for parameter_type in MaterialParameters:
                    if (
                        parameter_type.value in bom_element.parameters
                        and bom_element.parameters[parameter_type.value] is not None
                    ):
                        self.lp_model.bom_parameters[
                            process_center.name, bom_element.commodity.name, parameter_type.value
                        ] = bom_element.parameters[parameter_type.value]

    @time_function
    def add_product_of_process_center_as_parameter_to_lp(self):
        """Add the producta of the process center as a parameter to the LP model. Needed for the demand constraint."""
        self.lp_model.product_of_process_center = {}
        for pc in self.process_centers:
            if not pc.process.products == []:
                self.lp_model.product_of_process_center[pc.name] = [
                    prod.name for prod in pc.process.products if prod is not None
                ]

    @time_function
    def add_check_if_commodity_is_producable_as_parameter_to_lp(self):
        """Add a parameter to the LP model that checks if a commodity can be produced at a process center as one of its product"""
        self.lp_model.commodity_is_producable = {}
        for commodity in self.commodities:
            if commodity is not None:
                self.lp_model.commodity_is_producable[commodity.name] = any(
                    commodity in pc.process.products for pc in self.process_centers
                )

    @time_function
    def add_demand_slack_variables_to_lp(self):
        """Add the slack variables to the LP model, the amount of material that is not allocated to a demand center to make demand fulfillment
        a soft constraint and avoid infeasibilities."""
        self.lp_model.demand_slack_variable = pyo.Var(
            [
                dc.name
                for dc in self.process_centers
                if dc.process.type == ProcessType.DEMAND and dc.capacity is not None
            ],
            domain=pyo.NonNegativeReals,
            initialize=0,
        )

    @time_function
    def add_minimum_capacity_slack_variables_to_lp(self):
        """Add the slack variables to the LP model, the amount of material that is not allocated to a demand center to make demand fulfillment
        a soft constraint and avoid infeasibilities."""
        self.lp_model.minimum_capacity_slack_variable = pyo.Var(
            [
                pc.name
                for pc in self.process_centers
                if pc.process.type == ProcessType.PRODUCTION and pc.soft_minimum_capacity is not None
            ],
            domain=pyo.NonNegativeReals,
            initialize=0,
        )

    @time_function
    def add_demand_constraint_to_lp(self):
        """Add the demand constraint to the LP model, i.e. the sum of allocations to a demand center must equal the capacity (=demand) of the demand center"""

        def demand_rule(model, pc_name):
            # Only count allocations where the commodity has an INPUT_RATIO defined in the demand center's BOM
            # This ensures we only sum commodities that are actually demanded (not dependent/secondary commodities)
            return (
                sum(
                    model.allocation_variables[idx]
                    for idx in model.inbound_arcs[pc_name]
                    # if (pc_name, idx[2], MaterialParameters.INPUT_RATIO.value) in model.bom_parameters
                    if idx[2] == "steel"
                )
                + model.demand_slack_variable[pc_name]
                == model.capacities[pc_name]
            )

        self.lp_model.demand_constraints = pyo.Constraint(
            [
                pc.name
                for pc in self.process_centers
                if pc.process.type == ProcessType.DEMAND and pc.capacity is not None
            ],
            rule=demand_rule,
        )

    @time_function
    def add_trade_quota_constraints_to_lp(self):
        """Add the trade quota constraints to the LP model. The sum of allocations from one iso3 to another iso3 must not exceed the quota."""

        def trade_quota_rule(model, *tariff_key):
            return (
                sum(
                    model.allocation_variables[from_pc_name, to_pc_name, commodity]
                    for from_pc_name, to_pc_name, commodity in model.quota_allocations[tariff_key]
                )
                <= self.tariff_quotas_by_iso3[tariff_key]
            )

        self.lp_model.trade_quota_constraints = pyo.Constraint(
            self.lp_model.quota_allocations.keys(),
            rule=trade_quota_rule,
        )

    @time_function
    def add_primary_outputs_of_feedstock_as_parameter_to_lp(self):
        """Add the primary output of the feedstock as a parameter to the LP model. Needed for the bom flow constraints above."""
        self.lp_model.primary_outputs_of_feedstock = {}
        for pc in self.process_centers:
            for bom in pc.process.bill_of_materials:
                if (
                    bom.commodity in self.commodities
                    and bom.commodity is not None
                    and bom.output_commodities is not None
                ):
                    self.lp_model.primary_outputs_of_feedstock[pc.name, bom.commodity.name] = [
                        comm.name for comm in bom.output_commodities
                    ]

    @time_function
    def add_aggregate_commodity_constraint_parameters(self):
        """Add the aggregate commodity constraints parameters to the LP model. Needed for the aggregate commodity constraints."""
        self.tech_to_process_centers = {}
        for pc in self.process_centers:
            if pc.process.type == ProcessType.PRODUCTION:
                tech = pc.process.technology.name
                if tech not in self.tech_to_process_centers:
                    self.tech_to_process_centers[tech] = []
                self.tech_to_process_centers[tech].append(pc.name)
        self.lp_model.minimum_aggregate_commodity_constraints_params = {}
        self.lp_model.maximum_aggregate_commodity_constraints_params = {}
        for tech, commodity_mask in self.aggregated_commodity_constraints:
            if "minimum" in self.aggregated_commodity_constraints[(tech, commodity_mask)]:
                for pc_name in self.tech_to_process_centers[tech]:
                    self.lp_model.minimum_aggregate_commodity_constraints_params[pc_name, commodity_mask] = (
                        self.aggregated_commodity_constraints[(tech, commodity_mask)]["minimum"]
                    )
            if "maximum" in self.aggregated_commodity_constraints[(tech, commodity_mask)]:
                for pc_name in self.tech_to_process_centers[tech]:
                    self.lp_model.maximum_aggregate_commodity_constraints_params[pc_name, commodity_mask] = (
                        self.aggregated_commodity_constraints[(tech, commodity_mask)]["maximum"]
                    )

    @time_function
    def add_aggregate_commodity_constraints_to_lp(self):
        """Add the aggregate commodity constraints to the LP model. The sum of allocations for a commodity must equal the total demand for that commodity."""
        self.lp_model.allocations_that_produce_same_outputs_agg = {}
        self.lp_model.allocations_of_bom_commodity_agg = {}

        inbound_arcs = self.lp_model.inbound_arcs
        feedstock_outputs = self.lp_model.feedstock_outputs

        for pc in self.process_centers:
            # skip if not production
            if pc.process.type != ProcessType.PRODUCTION:
                continue

            pc_name = pc.name
            # feedstock_outputs_for_pc is a dict: c -> primary_outputs_of_feedstock[pc_name, c]
            feedstock_outputs_for_pc = feedstock_outputs[pc_name]

            # arcs_into_pc is the list of (f, t, c) that come into pc_name
            arcs_into_pc = inbound_arcs[pc_name]

            # ensure that all feedstocks that are part of the mask have the same output:
            reference_outputs = None

            for tech, commodity_mask in self.aggregated_commodity_constraints:
                for c in feedstock_outputs_for_pc:
                    if commodity_mask in c:
                        if reference_outputs is None:
                            reference_outputs = feedstock_outputs_for_pc[c]
                        elif feedstock_outputs_for_pc[c] != reference_outputs:
                            raise ValueError(
                                f"Feedstock {c} has different outputs than the reference outputs {reference_outputs} for process center {pc_name}."
                            )
                same_output_set = set()
                bom_commodity_set = set()

                # Loop only over arcs leading into pc_name
                for f, t, c in arcs_into_pc:
                    # same_output_set: feedstock that yields the same "primary output" as the commodity mask
                    if c in feedstock_outputs_for_pc and feedstock_outputs_for_pc[c] == reference_outputs:
                        same_output_set.add((f, t, c))

                    # bom_commodity_set: arcs for which c is part of the commodity mask
                    if commodity_mask in c:
                        bom_commodity_set.add((f, t, c))

                self.lp_model.allocations_that_produce_same_outputs_agg[pc_name, commodity_mask] = same_output_set
                self.lp_model.allocations_of_bom_commodity_agg[pc_name, commodity_mask] = bom_commodity_set

        def agg_maximum_ratio_rule(model, pc, comm_mask):
            if (
                not model.allocations_that_produce_same_outputs_agg[pc, comm_mask]
                or not model.allocations_of_bom_commodity_agg[pc, comm_mask]
            ):
                return pyo.Constraint.Skip
            # direct summation
            total_same_output = pyo.quicksum(
                model.allocation_variables[idx]
                for idx in model.allocations_that_produce_same_outputs_agg[pc, comm_mask]
            )
            total_bom_commodity = pyo.quicksum(
                model.allocation_variables[idx] for idx in model.allocations_of_bom_commodity_agg[pc, comm_mask]
            )
            return (
                total_bom_commodity
                - total_same_output * model.maximum_aggregate_commodity_constraints_params[pc, comm_mask]
                <= 0
            )

        def agg_minimum_ratio_rule(model, pc, comm_mask):
            if (
                not model.allocations_that_produce_same_outputs_agg[pc, comm_mask]
                or not model.allocations_of_bom_commodity_agg[pc, comm_mask]
            ):
                return pyo.Constraint.Skip
            # direct summation
            total_same_output = pyo.quicksum(
                model.allocation_variables[idx]
                for idx in model.allocations_that_produce_same_outputs_agg[pc, comm_mask]
            )
            total_bom_commodity = pyo.quicksum(
                model.allocation_variables[idx] for idx in model.allocations_of_bom_commodity_agg[pc, comm_mask]
            )
            return (
                total_bom_commodity
                - total_same_output * model.minimum_aggregate_commodity_constraints_params[pc, comm_mask]
                >= 0
            )

        self.lp_model.aggregate_commodity_maximum_ratio_constraints = pyo.Constraint(
            [
                (pc_name, comm_mask)
                for (pc_name, comm_mask) in self.lp_model.maximum_aggregate_commodity_constraints_params.keys()
            ],
            rule=agg_maximum_ratio_rule,
        )

        self.lp_model.aggregate_commodity_minimum_ratio_constraints = pyo.Constraint(
            [
                (pc_name, comm_mask)
                for (pc_name, comm_mask) in self.lp_model.minimum_aggregate_commodity_constraints_params.keys()
            ],
            rule=agg_minimum_ratio_rule,
        )

    @time_function
    def add_capacities_as_parameter_to_lp(self):
        """Add the capacities of the process centers as a parameter to the LP model. Needed for the production constraints above."""
        self.lp_model.capacities = {}
        for pc in self.process_centers:
            self.lp_model.capacities[pc.name] = pc.capacity

    @time_function
    def add_soft_minimum_capacities_as_parameter_to_lp(self):
        """Add the soft minimum capacity percentages of the process centers as a parameter to the LP model. Needed for the soft minimum capacity constraint."""
        self.lp_model.soft_minimum_capacities = {}
        for pc in self.process_centers:
            if pc.soft_minimum_capacity is not None:
                self.lp_model.soft_minimum_capacities[pc.name] = pc.soft_minimum_capacity

    @time_function
    def add_process_center_type_as_parameter_to_lp(self):
        """Add the type of the process centers as a parameter to the LP model. Needed for various constraints."""
        self.lp_model.process_center_type = {}
        for pc in self.process_centers:
            self.lp_model.process_center_type[pc.name] = pc.process.type.value

    def build_lp_model(self):
        """Build the complete Pyomo LP model with all variables, parameters, and constraints.

        This orchestrates the construction of the optimization problem by calling all the
        component-building methods in the correct order. Must be called after adding all
        process centers, processes, commodities, and connectors to the model.

        Steps:
            1. Determine legal allocations (valid flows based on process connectors)
            2. Add decision variables (allocation quantities, slack variables)
            3. Add parameters (capacities, costs, ratios, etc.)
            4. Add constraints (capacity, demand, BOM, ratios, tariffs, etc.)
            5. Add objective function (minimize total cost)

        After calling this method, the model is ready to solve with solve_lp_model().
        """
        self.set_legal_allocations()
        # Add variables:
        self.add_allocation_variables_to_lp()

        if self.aggregated_commodity_constraints is not None:
            self.add_aggregate_commodity_constraint_parameters()
            self.add_aggregate_commodity_constraints_to_lp()
        self.add_demand_slack_variables_to_lp()
        self.add_minimum_capacity_slack_variables_to_lp()
        # Add parameters:
        self.add_bom_parameters_as_parameters_to_lp()
        # self.add_willingness_to_pay_as_parameters_to_lp()
        self.add_primary_outputs_of_feedstock_as_parameter_to_lp()
        self.add_allocation_maps_to_parameters()
        self.add_allocation_keys_subject_to_sf_constraints_as_parameters_to_lp()
        # self.add_bool_if_process_center_has_incoming_allocations_for_bom_to_params()
        # self.add_bool_if_process_has_incoming_connections()
        self.add_capacities_as_parameter_to_lp()
        self.add_soft_minimum_capacities_as_parameter_to_lp()
        self.add_product_of_process_center_as_parameter_to_lp()
        self.add_check_if_commodity_is_producable_as_parameter_to_lp()
        self.add_dependent_commodities_as_parameters_to_lp()
        self.add_tariff_quotas_and_tax_as_parameters()
        self.add_bom_energy_costs_as_parameter_to_lp()
        self.add_allocation_costs_as_parameters_to_lp()
        self.add_process_center_type_as_parameter_to_lp()
        # Add constraints:
        self.add_production_constraints_to_lp()
        self.add_demand_constraint_to_lp()
        self.add_bom_inflow_constraints_to_lp()
        self.add_minimum_and_maximum_ratio_constraints_to_lp()
        self.add_trade_quota_constraints_to_lp()
        self.add_soft_minimum_capacity_constraints_to_lp()
        self.add_secondary_feedstock_constraints_to_lp()
        self.add_dependent_commodities_consistency_constraints_to_lp()
        # Add objective function:
        self.add_objective_function_to_lp()

    def solve_lp_model(self):
        """Solve the LP optimization problem using HiGHS solver.

        Solves the built LP model using configurable solver options with warm-start support.
        Returns solver results including termination condition and solution status.

        Returns:
            Pyomo solver result object with attributes:
                - solver.status: Solver status (ok, warning, error, etc.)
                - solver.termination_condition: Why solver stopped (optimal, infeasible, etc.)

        Notes:
            - Uses solver_options for configuration (default: IPM for memory efficiency)
            - Supports warm-starting from previous_solution (simplex only)
            - Random seed fixed (1337) for reproducibility
            - Does not automatically load solution (call extract_solution() after)
            - Logs detailed diagnostics if model is infeasible
        """
        logger = logging.getLogger(f"{__name__}.solve_lp_model")
        start_time = time.time()
        solver = pyo.SolverFactory("appsi_highs")
        solver.options["random_seed"] = 1337

        # Use configurable solver options for performance tuning (OPT-4)
        solver.options.update(self.solver_options)
        solver.config.load_solution = False  # Don't try to load infeasible solution

        # Warm-start from previous year's solution if available (OPT-2)
        # NOTE: HiGHS Appsi only supports warm starts for simplex solver, not IPM
        warm_start_enabled = False
        solver_type = self.solver_options.get("solver", "ipm")
        n_vars = self.lp_model.nvariables()

        if hasattr(self, "previous_solution") and self.previous_solution is not None:
            if solver_type == "simplex":
                warm_start_count = 0
                for (from_pc, to_pc, comm), value in self.previous_solution.items():
                    # Only set values for variables that exist in this year's model
                    if (from_pc, to_pc, comm) in self.lp_model.allocation_variables:
                        self.lp_model.allocation_variables[(from_pc, to_pc, comm)].set_value(value)
                        warm_start_count += 1
                if warm_start_count > 0:
                    warm_start_enabled = True
                    logger.info(
                        f"operation=warm_start variables_initialized={warm_start_count} "
                        f"coverage={(warm_start_count / n_vars) * 100:.1f}%"
                    )
            elif solver_type == "ipm":
                logger.info("operation=warm_start status=skipped reason='IPM solver does not support warm starts'")

        result = solver.solve(self.lp_model, load_solutions=False, warmstart=warm_start_enabled)
        elapsed = time.time() - start_time
        logger.info(f"operation=trade_optimization duration_s={elapsed:.3f}")
        self.solution_status = result.solver.status

        # Check if solution was found
        if result.solver.termination_condition == pyo.TerminationCondition.infeasible:
            logger.error("\n=== LP SOLVER DIAGNOSTICS ===")
            logger.error(f"Termination condition: {result.solver.termination_condition}")
            logger.error(f"Solver status: {result.solver.status}")
            logger.error("\nModel statistics:")
            logger.error(f"- Variables: {self.lp_model.nvariables()}")
            logger.error(f"- Constraints: {self.lp_model.nconstraints()}")
            logger.error(f"- Process centers: {len(self.process_centers)}")

            # Check for dependent commodities constraints
            if hasattr(self.lp_model, "dependent_commodities_constraints"):
                dep_constraints = len(self.lp_model.dependent_commodities_constraints)
                logger.error(f"- Dependent commodity constraints: {dep_constraints}")
                if dep_constraints > 0:
                    logger.error("  WARNING: Dependent commodities constraints are active!")
                    logger.error("  Check if all dependent commodities have suppliers defined")

            logger.error("\nThe model is infeasible - no solution exists that satisfies all constraints.")
            logger.error("Possible causes:")
            logger.error("  1. Demand exceeds available supply chain capacity")
            logger.error("  2. Missing process connectors prevent required flows")
            logger.error("  3. Dependent commodities (e.g., limestone) have no suppliers")
            logger.error("  4. Trade quotas/tariffs block necessary routes")
        elif result.solver.termination_condition == pyo.TerminationCondition.optimal:
            # Load the solution if optimal
            self.lp_model.solutions.load_from(result)

        return result

    def extract_solution(self):
        """Extract optimal allocation values from solved LP model.

        Reads the Pyomo variable values from the solved model and populates the
        allocations attribute with Allocations object containing flows and costs.
        Also sets optimal_production on each ProcessCenter.

        Raises:
            ValueError: If solver status is not 'ok' (no optimal solution)

        Notes:
            - Only extracts allocations >= lp_epsilon (filters negligible flows)
            - Creates Allocations object mapping (from, to, commodity) → volume and cost
            - Sets optimal_production attribute on production process centers
            - Must be called after solve_lp_model() with optimal solution
        """
        if self.solution_status != pyo.SolverStatus.ok:
            raise ValueError("No optimal solution found.")

        allocations = {}
        allocation_costs = {}
        for (from_pc_name, to_pc_name, commodity_name), var in self.lp_model.allocation_variables.items():
            volume = pyo.value(var)
            if volume >= self.lp_epsilon:
                from_pc = next(pc for pc in self.process_centers if pc.name == from_pc_name)
                to_pc = next(pc for pc in self.process_centers if pc.name == to_pc_name)
                comm = next(comm for comm in self.commodities if comm.name == commodity_name)
                allocations[(from_pc, to_pc, comm)] = volume
                allocation_costs[(from_pc, to_pc, comm)] = self.lp_model.allocation_costs[
                    from_pc_name, to_pc_name, commodity_name
                ]

        self.allocations = Allocations(allocations=allocations, allocation_costs=allocation_costs)
        # self.allocations.validate_allocations()

    def get_solution_for_warm_start(self) -> dict[tuple[str, str, str], float]:
        """Extract current solution values in a format suitable for warm-starting next year's LP.

        Returns dict mapping (from_pc_name, to_pc_name, commodity_name) to allocation volumes.
        Only stores non-zero allocations to minimize memory usage.

        Returns:
            Dictionary with allocation variable values for warm-starting future solves
        """
        solution = {}
        for (from_pc, to_pc, comm), var in self.lp_model.allocation_variables.items():
            value = pyo.value(var)
            if value >= self.lp_epsilon:
                solution[(from_pc, to_pc, comm)] = value
        return solution
