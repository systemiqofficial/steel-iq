import copy
import logging
import networkx as nx
from collections import deque
from steelo.adapters.repositories.in_memory_repository import (
    PlantInMemoryRepository,
)
from steelo.domain.models import PrimaryFeedstock, FurnaceGroup, TransportKPI
from steelo.domain.trade_modelling.trade_lp_modelling import Allocations, ProcessType
from steelo.domain.constants import LP_TOLERANCE
from steelo.domain import diagnostics as diag
from steelo.utilities.utils import normalize_energy_key


# logging.getLogger().setLevel(logging.WARNING)  # Commented out to avoid setting root logger


class TM_PAM_connector:
    """
    class to connect the trade module with the PAM model - containing various methods to extract data from the trade module and update
    the unit production costs and utilisation rates of the furnace groups
    """

    def __init__(
        self,
        dynamic_feedstocks_classes: dict[str, list[PrimaryFeedstock]],
        plants: PlantInMemoryRepository,
        transport_kpis: list[TransportKPI] | None = None,
    ):
        """Initialize the TM-PAM connector for trade module and plant agent model integration.

        Sets up data structures to bridge the trade optimization results with furnace group
        operational parameters, including feedstock mappings, energy costs, and transport costs.

        Args:
            dynamic_feedstocks_classes: Dictionary mapping feedstock types to lists of
                PrimaryFeedstock objects, containing all available feedstock options.
            plants: Repository of all plants in the model, providing access to furnace groups
                and their operational characteristics.
            transport_kpis: Optional list of TransportKPI objects containing location-specific
                transportation costs between countries for different commodities.

        Attributes Created:
            flat_feedstocks_dict: Flattened dict for O(1) feedstock lookup by name.
            feedstock_energy_requirements: Energy requirements per feedstock type.
            processing_energy_cost: Energy costs (total + carrier breakdown) by furnace group and commodity.
            chosen_reductant: Reductant choice for each furnace group.
            transport_costs: Dict mapping (from_iso, to_iso, commodity) to cost.
            iron_furnaces: List of furnace group IDs producing iron.
            steel_furnaces: List of furnace group IDs producing steel.
            G: NetworkX MultiDiGraph representing the trade flow network (initialized to None).
        """

        self.dynamic_feedstocks = dynamic_feedstocks_classes
        self.chosen_reductant = {}
        self.processing_energy_cost = {}

        for p in plants.list():
            for fg in p.furnace_groups:
                per_feed_energy: dict[str, dict[str, dict[str, float] | float]] = {}
                feed_totals = getattr(fg, "energy_vopex_by_input", {}) or {}
                feed_breakdowns = getattr(fg, "energy_vopex_breakdown_by_input", {}) or {}
                for commodity, total_cost in feed_totals.items():
                    normalized_commodity = str(commodity).lower()
                    breakdown = feed_breakdowns.get(commodity) or feed_breakdowns.get(str(commodity).lower()) or {}
                    normalized_breakdown = {
                        normalize_energy_key(carrier): float(cost) for carrier, cost in breakdown.items()
                    }
                    per_feed_energy[normalized_commodity] = {
                        "total": float(total_cost),
                        "carriers": normalized_breakdown,
                    }
                self.processing_energy_cost[fg.furnace_group_id] = per_feed_energy
                self.chosen_reductant[fg.furnace_group_id] = fg.chosen_reductant
                # Debug: log H2-using furnace groups at connector init
                _h2_carrier_summary: dict[str, list[str]] = {}
                _has_h2 = False
                for _comm, _detail in per_feed_energy.items():
                    if not isinstance(_detail, dict):
                        continue
                    _carriers = _detail.get("carriers")
                    if isinstance(_carriers, dict):
                        _h2_carrier_summary[_comm] = list(_carriers.keys())
                        if "hydrogen" in _carriers:
                            _has_h2 = True
                if _has_h2:
                    _init_logger = logging.getLogger(f"{__name__}.TM_PAM_connector_init")
                    _init_logger.debug(
                        "[H2-DEBUG INIT] FG:%s Tech:%s | processing_energy carriers: %s",
                        fg.furnace_group_id,
                        fg.technology.name,
                        _h2_carrier_summary,
                    )
        self.flat_feedstocks_dict = {
            entry.name.lower(): entry for key, items in self.dynamic_feedstocks.items() for entry in items
        }
        self.feedstock_energy_requirements = {
            entry.name.lower(): entry.energy_requirements
            for key, items in self.dynamic_feedstocks.items()
            for entry in items
        }

        self.plants = [p.plant_id for p in plants.list()]
        # self.furnaces = [fg for p in plants.list() for fg in p.furnace_groups]

        self.plants_repo = plants

        # Store transport costs in a dictionary for quick lookup
        self.transport_costs: dict[tuple[str, str, str], float] = {}
        if transport_kpis:
            for kpi in transport_kpis:
                key = (kpi.reporter_iso, kpi.partner_iso, kpi.commodity.lower())
                self.transport_costs[key] = kpi.transportation_cost

        self.iron_furnaces = [
            fg.furnace_group_id
            for p in plants.list()
            for fg in p.furnace_groups
            if isinstance(fg.technology.product, str) and fg.technology.product.lower() == "iron"
        ]
        self.steel_furnaces = [
            fg.furnace_group_id
            for p in plants.list()
            for fg in p.furnace_groups
            if isinstance(fg.technology.product, str) and fg.technology.product.lower() == "steel"
        ]
        self.bof_furnaces = [
            fg.furnace_group_id for p in plants.list() for fg in p.furnace_groups if fg.technology.name.upper() == "BOF"
        ]

        self.G = None
        self.current_year: int | None = None
        self.diagnostics_active_bof_count: int | None = None

    def get_transport_cost(self, from_iso: str, to_iso: str, commodity: str) -> float:
        """Retrieve transportation cost between two countries for a specific commodity.

        Args:
            from_iso: Source country ISO3 code (e.g., "USA", "CHN").
            to_iso: Destination country ISO3 code.
            commodity: Commodity name (case-insensitive, will be normalized to lowercase).

        Returns:
            Transportation cost per ton in USD. Returns 0.0 if no cost data available
            for the specified route and commodity.
        """
        key = (from_iso, to_iso, commodity.lower())
        return self.transport_costs.get(key, 0.0)  # Default to 0 if not found

    def process_energy_cost(
        self,
        furnace: str,
        process: str,
    ):
        """Calculate processing energy cost for a furnace using a specific feedstock process.

        Computes the total energy cost by multiplying each energy carrier requirement
        (electricity, natural gas, hydrogen, etc.) by plant-specific or global energy prices.

        Args:
            furnace: Furnace group ID string (format: "plantid_furnacegroupid").
            process: Process/feedstock name (e.g., "iron_ore", "scrap_steel") used to
                lookup energy requirements from `self.flat_feedstocks_dict`.

        Returns:
            Total processing energy cost per ton of material processed (USD/ton).
            Returns 0.0 if furnace plant not found or process not in feedstock dict.

        Notes:
            - Uses plant-specific energy costs if available (from plant.energy_costs).
            - Falls back to global energy prices if plant-specific costs unavailable.
            - Energy requirements are defined per feedstock type in PrimaryFeedstock objects.
        """
        global_cost_dict = dict(
            natural_gas=1.05506 * 6, electricity=0.150, coke=0.05, pci=0, hydrogen=6.61, bio_pci=0.05, coal=98.6
        )

        process_energy_cost = 0.0
        plant_id = furnace.split("_")[0]
        if plant_id not in self.plants:
            return 0
        if process not in self.flat_feedstocks_dict:
            return 0

        p = self.plants_repo.get(plant_id)
        plant_energy_cost = p.energy_costs
        # p.get_energy_costs() # THis function doesn't exist but let's get it.
        for key, value in self.feedstock_energy_requirements[process].items():
            # if the plant has the attribute
            if hasattr(plant_energy_cost, key.lower().replace("-", "_")):
                process_energy_cost += getattr(plant_energy_cost, key.lower().replace("-", "_")) * value
            else:
                # logging.debug(f"Key {key} not found in global cost dict or plant")

                process_energy_cost += float(global_cost_dict[key.lower().replace("-", "_")]) * value
        return process_energy_cost

    def calculate_allocations_for_graph(
        self,
        allocation_attr="allocations",
        volume_attr="volume",
        effectiveness_attr="process_efficiency",
        commodity_attr="commodity",
    ):
        """Compute input allocations from output volumes using process efficiencies.

        For each edge in the graph, calculates the required input quantity by dividing
        the shipped volume by the process efficiency (yield). This converts output volumes
        to input requirements for cost accounting.

        Args:
            allocation_attr: Edge attribute name to store computed allocation values.
            volume_attr: Edge attribute name containing shipped/output volumes.
            effectiveness_attr: Edge attribute name for process efficiency/yield (output/input ratio).
            commodity_attr: Edge attribute name for commodity identifier.

        Side Effects:
            Updates `self.G` edges with `allocation_attr` values = volume / efficiency.

        Example:
            If 100 tons steel shipped with 0.95 efficiency → allocation = 100/0.95 = 105.3 tons input.
        """
        G = self.G.copy()
        for edge in G.edges(keys=True, data=True):
            from_node, to_node, commodity, data = edge
            if volume_attr in data:
                volume = data[volume_attr]
                effectiveness = data.get(effectiveness_attr, 1)
                if effectiveness is None:
                    effectiveness = 1
                # Calculate the allocation based on the volume and effectiveness
                allocation_value = volume / effectiveness if effectiveness > 0 else 0
                if commodity_attr in data:
                    commodity = data[commodity_attr]
                G[from_node][to_node][commodity][allocation_attr] = allocation_value
        self.G = G.copy()

    def create_graph(self, solved_trade_allocations):
        """
        Build a directed multigraph of all process centers, with parallel edges, by key
        encoding trade allocations and their economic and technical attributes.

        This populates `self.G` as a `networkx.MultiDiGraph` where:
        - Nodes represent process centers, keyed by their `.name`.
        - Edges represent shipments of a commodity from one center to another,
            allowing parallel edges (one per commodity).
        - Each edge carries attributes such as volume, transport cost,
            processing energy cost, process identifier, commodity, primary output,
            and process efficiency.

        Args
        ----------
        solved_trade_allocations : object
            An object with an `allocations` attribute, a dict mapping
            `(from_process_center, to_process_center, commodity)` tuples to
            allocation volumes (floats). Only positive allocations are represented.

        Returns
        -------
        None
        Side Effects
        ------------
        Sets `self.G` to the constructed `nx.MultiDiGraph`.

        Notes
        -----
        - Uses `self.chosen_reductant` to name reductant-specific processes.
        - Uses `self.processing_energy_cost` to fetch energy costs per process.
        - Uses `self.flat_feedstocks_dict` to lookup primary outputs and efficiencies.
        """
        # Initialize an empty directed multigraph
        self.G = nx.MultiDiGraph()

        # Iterate through each allocation entry (from, to, commodity) → volume
        for (from_pc, to_pc, comm), alloc_value in solved_trade_allocations.allocations.items():
            # Skip zero or negative allocations
            if alloc_value <= LP_TOLERANCE:
                continue

            # Normalize commodity name
            commodity = comm.name.lower()

            # Build a process-identifier string, including reductant if chosen
            if to_pc.name in self.chosen_reductant:
                reductant = str(self.chosen_reductant[to_pc.name]).lower()
                process = f"{to_pc.process.name.lower()}_{commodity}_{reductant}"
            else:
                process = f"{to_pc.process.name.lower()}_{commodity}"

            # Look up energy cost details for this process/feed
            energy_cost_detail = {}
            if to_pc.name in self.processing_energy_cost:
                energy_cost_detail = self.processing_energy_cost[to_pc.name].get(commodity, {})
            if isinstance(energy_cost_detail, dict):
                total_energy_cost = float(energy_cost_detail.get("total", 0.0))
                energy_breakdown = dict(energy_cost_detail.get("carriers", {}))
            else:
                total_energy_cost = float(energy_cost_detail or 0.0)
                energy_breakdown = {}

            # Prepare edge attributes dictionary
            edge_attrs = {
                # 1. Shipment volume
                "volume": alloc_value,
                # 2. Transport cost from TransportKPI data
                "transport_cost": self.get_transport_cost(from_pc.location.iso3, to_pc.location.iso3, commodity),
                # 3. Processing energy cost, if defined for this destination
                "processing_energy_cost": total_energy_cost,
                "processing_energy_breakdown": energy_breakdown,
                # 4. Process identifier and commodity tag
                "process": process,
                "commodity": commodity,
                # 5. Primary output of this process, if in the flat feedstocks map
                "output": (
                    next(iter(self.flat_feedstocks_dict[process].get_primary_outputs()), None)
                    if process in self.flat_feedstocks_dict
                    else None
                ),
                # 6. Process efficiency (required quantity per ton of product)
                "process_efficiency": (
                    self.flat_feedstocks_dict[process].required_quantity_per_ton_of_product
                    if process in self.flat_feedstocks_dict
                    else None
                ),
                # (Could also add: process_energy_per_ton, allocation_cost, etc.)
            }

            # Add the source node, distinguishing suppliers from furnaces by their process type
            from_name = from_pc.name
            # Suppliers have ProcessType.SUPPLY and should get material costs (production_cost)
            # Furnaces have ProcessType.PRODUCTION and get empty dict (carbon costs handled separately)
            if from_pc.process.type == ProcessType.SUPPLY:
                self.G.add_node(from_name, product_cost=from_pc.production_cost, unit_cost={})
            else:
                self.G.add_node(from_name, product_cost={}, unit_cost={})

            # Add the destination node, initializing its attrs with same cost logic
            to_name = to_pc.name
            if to_pc.process.type == ProcessType.SUPPLY:
                self.G.add_node(
                    to_name,
                    process=process,
                    allocations={},
                    export={},
                    unit_cost={},
                    product_cost=to_pc.production_cost,
                )
            else:
                self.G.add_node(to_name, process=process, allocations={}, export={}, unit_cost={}, product_cost={})

            # Finally, add the directed edge with all computed attributes
            self.G.add_edge(from_name, to_name, key=commodity, **edge_attrs)  # allow parallel edges keyed by commodity

    def propage_cost_forward_by_layers_and_normalize(
        self,
        source_attr="product_cost",
        transport_attr="transport_cost",
        process_attr="processing_energy_cost",
        allocation_attr="allocations",
        export_attr="export",
        volume_attr="volume",
        product_attr="output",
        unit_cost_attr="unit_cost",
    ):
        """
        Propagate per-unit costs forward through the process-center graph and normalize by outgoing volumes.

        Starting from "root" nodes (those with no incoming edges), this does a breadth-first pass
        to accumulate all cost components (source, processing energy, transport) along each edge,
        weighted by shipped volume.  Once accumulated in each target node under `source_attr`,
        it then computes `unit_cost_attr` by dividing total cost per commodity by that node's
        total outgoing volume for the same commodity.

        Args
        ----------
        source_attr : str
            Node attribute key where upstream cost is stored/accumulated (dict by commodity).
        transport_attr : str
            Edge attribute key for transport cost per unit.
        process_attr : str
            Edge attribute key for processing energy cost per unit.
        allocation_attr : str
            Node attribute key holding dicts of per-commodity allocations:
            `{commodity: {'Cost': x, 'Volume': y}}`.
        export_attr : str
            (Unused) placeholder for future export tracking.
        volume_attr : str
            Edge attribute key for shipment volume.
        product_attr : str
            Edge attribute key indicating the output commodity name.
        unit_cost_attr : str
            Node attribute key under which per-commodity unit cost will be stored.

        Returns
        -------
        None

        Side Effects
        ------------
        - Reads from and then replaces `self.G` with a new MultiDiGraph containing:
            1. Per-commodity cumulative costs in `node[source_attr]` (a dict).
            2. Per-commodity unit costs in `node[unit_cost_attr]`.
        - Prints the number of edges processed (for debugging).
        - Prints each node's computed unit cost (for debugging).

        Notes
        -----
        - Assumes `self.G` is a DAG (no cycles), so BFS/topological layers make sense.
        - Skips any sink node (no outgoing edges) in propagation phase.
        - Leaves zero-volume edges effectively ignored in normalization.
        """
        logger = logging.getLogger(f"{__name__}.propage_cost_forward_by_layers_and_normalize")
        # Make a copy so we don't mutate the original in the middle of traversal
        G = self.G.copy()

        # Identify “roots” = nodes with zero in-degree
        roots = [n for n in G.nodes if G.in_degree(n) == 0]

        # BFS queue seeded with roots; track visited to avoid repeats
        q = deque(roots)
        seen = set(roots)
        edge_count = 0

        # 1) Propagate costs forward layer by layer
        while q:
            u = q.popleft()
            node_cost = G.nodes[u].get(source_attr, {})  # may be dict by commodity
            unit_cost = {}

            if allocation_attr in G.nodes[u]:
                export = {}
                for src, v, comm, edata in self.G.out_edges(u, keys=True, data=True):
                    G.nodes[u][export_attr][comm] = G.nodes[u][export_attr].get(comm, 0) + edata.get(volume_attr, 0)
            # If G[u] is also a to-node
            # For each outgoing edge (u → v) carrying commodity `comm`
            for _, v, comm, edata in G.out_edges(u, keys=True, data=True):
                edge_count += 1

                # Determine cost contribution from the source node
                if isinstance(node_cost, dict):
                    # For furnaces/processors (nodes with inputs), sum ALL input costs
                    if G.in_degree(u) > 0:
                        # Sum all accumulated input costs across all commodities
                        allocations = G.nodes[u].get(allocation_attr, {})
                        base_cost = sum(alloc.get("Cost", 0.0) for alloc in allocations.values())
                    else:
                        # For suppliers (root nodes), use commodity-specific production cost
                        base_cost = node_cost.get(comm, 0.0)
                else:
                    base_cost = float(node_cost)

                # Normalize by total exported volume of that commodity at u, if available
                export = G.nodes[u].get(export_attr, {})
                export_volume_at_u = export.get(comm, 1.0)
                per_unit_base = base_cost / export_volume_at_u
                unit_cost.update({comm: per_unit_base})
                if G.out_degree(v) == 0:
                    # print(f"Skipping sink node {v} with no outgoing edges")
                    continue
                # Calculate cost components separately
                # Material cost includes upstream material + all upstream energy + all transport
                # We want to track the material cost EXCLUDING the current step's energy
                volume = edata.get(volume_attr, 0.0)
                material_and_transport_cost = (per_unit_base + edata.get(transport_attr, 0.0)) * volume
                current_step_energy_cost = edata.get(process_attr, 0.0) * volume
                edge_cost = material_and_transport_cost + current_step_energy_cost

                if (
                    diag.diagnostics_enabled()
                    and self.current_year is not None
                    and v in self.bof_furnaces
                    and comm == "hot_metal"
                    and volume > 0
                ):
                    transport_unit = edata.get(transport_attr, 0.0)
                    energy_unit = edata.get(process_attr, 0.0)
                    base_unit = per_unit_base
                    total_unit = base_unit + transport_unit + energy_unit
                    delta = total_unit - base_unit
                    delta_pct = (delta / base_unit * 100) if base_unit else None
                    if delta > 500 or (delta_pct is not None and delta_pct > 100):
                        delta_pct_str = f"{delta_pct:.1f}" if delta_pct is not None else "n/a"
                        diag.append_text(
                            f"cost_propagation/{self.current_year}.txt",
                            [
                                "node={node}, source={src}, commodity={comm}, base={base:.2f}, "
                                "transport={transport:.2f}, energy={energy:.2f}, total={total:.2f}, "
                                "delta={delta:.2f}, delta_pct={delta_pct}".format(
                                    node=v,
                                    src=u,
                                    comm=comm,
                                    base=base_unit,
                                    transport=transport_unit,
                                    energy=energy_unit,
                                    total=total_unit,
                                    delta=delta,
                                    delta_pct=delta_pct_str,
                                )
                            ],
                        )

                # Initialize the target node's cost dict if needed
                if source_attr not in G.nodes[v] or not isinstance(G.nodes[v][source_attr], dict):
                    G.nodes[v][source_attr] = {}
                    G.nodes[v][allocation_attr] = {}

                # Accumulate cost and volume
                # Store both total cost and material cost (excluding current step's energy)
                # MaterialCost includes upstream material + ALL upstream costs (including upstream energy) + current transport
                # EXCLUDES the current step's processing energy
                prev = G.nodes[v][allocation_attr].get(comm, {"Cost": 0.0, "MaterialCost": 0.0, "Volume": 0.0})
                prev["Cost"] += edge_cost  # Total cost including current step's energy
                prev["MaterialCost"] += material_and_transport_cost  # Excludes current step's energy only
                prev["Volume"] += volume
                G.nodes[v][allocation_attr][comm] = prev
                G.nodes[v][source_attr][comm] = prev["Cost"]

                # For multi-output processes (e.g., BF producing both hot_metal and pig_iron),
                # allocate the total input cost proportionally across all output commodities
                # based on their respective volumes, ensuring equal per-unit costs
                output_edges = list(G.out_edges(v, keys=True, data=True))
                if output_edges:
                    # Calculate total output volume across all commodities
                    total_output_volume = sum(edge_data.get(volume_attr, 0.0) for _, _, _, edge_data in output_edges)

                    if total_output_volume > 0:
                        # Allocate cost proportionally to each output commodity
                        for _, _, out_comm, edge_data in output_edges:
                            out_volume = edge_data.get(volume_attr, 0.0)
                            # Cost for this output = (total input cost) × (this output volume / total output volume)
                            allocated_cost = prev["Cost"] * (out_volume / total_output_volume)
                            G.nodes[v][source_attr][out_comm] = allocated_cost

                # Enqueue v if not yet visited
                if v not in seen:
                    seen.add(v)
                    q.append(v)

            G.nodes[u][unit_cost_attr].update(unit_cost)
            # print(f"Node {u} unit costs: {unit_cost}")

        logger.info(f"Processed {edge_count} edges")

    def validate_edge_attributes(
        self,
        source_attr="product_cost",
        transport_attr="transport_cost",
        process_attr="processing_energy_cost",
        allocation_attr="allocations",
        volume_attr="volume",
        product_attr="output",
        effeciency_attr="process_efficiency",
        unit_cost_attr="unit_cost",
    ):
        """Validate presence of required attributes on graph edges before cost propagation.

        Counts how many edges have each expected attribute (present, missing, or None)
        to ensure the graph is properly constructed before running cost calculations.

        Args:
            source_attr: Node attribute for upstream product cost.
            transport_attr: Edge attribute for transportation cost.
            process_attr: Edge attribute for processing energy cost.
            allocation_attr: Node attribute for allocations dict.
            volume_attr: Edge attribute for shipment volume.
            product_attr: Edge attribute for output commodity name.
            effeciency_attr: Edge attribute for process efficiency.
            unit_cost_attr: Node attribute for unit cost.

        Returns:
            None. Currently logs attribute presence counts internally (debug level).

        Notes:
            This is primarily for debugging/validation during development.
            No exceptions raised - missing attributes may cause issues in propagation.
        """
        necessary_attributes = [
            source_attr,
            transport_attr,
            process_attr,
            allocation_attr,
            volume_attr,
            product_attr,
            effeciency_attr,
            unit_cost_attr,
        ]
        attribute_counts = {attr: {"present": 0, "missing": 0, "none": 0} for attr in necessary_attributes}

        for _, _, comm, edge_data in self.G.edges(keys=True, data=True):
            for attr in necessary_attributes:
                if attr in edge_data:
                    if edge_data[attr] is None:
                        attribute_counts[attr]["none"] += 1
                    else:
                        attribute_counts[attr]["present"] += 1
                else:
                    attribute_counts[attr]["missing"] += 1

        for attr, counts in attribute_counts.items():
            # logging.debug(
            #     f"Attribute '{attr}': {counts['present']} edges have it, "
            #     f"{counts['missing']} edges don't have it, "
            #     f"{counts['none']} edges have it as None."
            # )
            pass

    def set_up_network_and_propagate_costs(
        self,
        solved_trade_allocations: Allocations,
    ):
        """Build trade network graph and compute propagated costs through supply chains.

        High-level orchestration method that: (1) creates a NetworkX graph from trade
        optimization results, (2) calculates input allocations from outputs, (3) validates
        the graph structure, and (4) propagates costs forward from source nodes through
        the entire network.

        Args:
            solved_trade_allocations: Allocations object from the solved trade LP model,
                containing dict mapping (from_pc, to_pc, commodity) → volume.

        Raises:
            ValueError: If allocations dict is empty (no valid trade flows found).

        Side Effects:
            - Creates and stores `self.G` NetworkX MultiDiGraph.
            - Populates node and edge attributes including volumes, costs, and allocations.
            - Computes and stores unit costs at each node.

        Notes:
            Calls in sequence: create_graph() → calculate_allocations_for_graph() →
            validate_edge_attributes() → propage_cost_forward_by_layers_and_normalize().
        """
        logger = logging.getLogger(f"{__name__}.set_up_network_and_propagate_costs")
        logger.debug(f"[NETWORK] Setting up network with {len(solved_trade_allocations.allocations)} total allocations")

        if len(solved_trade_allocations.allocations) == 0:
            raise ValueError("No allocations found in the solved trade allocations. Please check the input data.")
        # 1) Create the graph
        self.create_graph(solved_trade_allocations=solved_trade_allocations)

        # 1.0) Validate that the graph is acyclic (DAG required for BFS cost propagation)
        if not nx.is_directed_acyclic_graph(self.G):
            try:
                # Find an example cycle to include in the error message
                cycle = nx.find_cycle(self.G)
                cycle_str = " -> ".join(f"{u}[{k}]" for u, v, k in cycle[:5])  # Show first 5 edges
                if len(cycle) > 5:
                    cycle_str += "..."
                raise ValueError(
                    f"Trade network graph contains cycles, which violates the supply chain assumption. "
                    f"Cost propagation requires a directed acyclic graph (DAG). "
                    f"Example cycle found: {cycle_str}"
                )
            except nx.NetworkXNoCycle:
                # Shouldn't happen, but handle gracefully
                raise ValueError(
                    "Trade network graph contains cycles, which violates the supply chain assumption. "
                    "Cost propagation requires a directed acyclic graph (DAG)."
                )

        # 1.1) Calculate the allocations for the graph
        self.validate_edge_attributes()
        self.calculate_allocations_for_graph()
        # 1.5) Validate the edge attributes before propagation
        self.validate_edge_attributes()
        # 2) Propagate the costs forward
        self.propage_cost_forward_by_layers_and_normalize()

    def update_exported_volumes(self, furnace_groups: list[FurnaceGroup], volume_attribute="volume"):
        """Update allocated volumes for each furnace group based on outgoing graph edges.

        Sums all outgoing edge volumes from each furnace group node in the trade network
        to determine total production allocated/exported. Sets allocated_volumes attribute
        on each FurnaceGroup object.

        Args:
            furnace_groups: List of FurnaceGroup objects to update.
            volume_attribute: Edge attribute name containing shipment volumes (default: "volume").

        Side Effects:
            Calls fg.set_allocated_volumes() for each furnace group. Sets to 0.0 if
            furnace group not found in graph or has no outgoing edges.

        Notes:
            - Uses furnace_group_id as the node key in self.G.
            - Logs debug information about edge counts and volumes.
            - Must be called after set_up_network_and_propagate_costs().
        """
        logger = logging.getLogger(f"{__name__}.TM_PAM_connector.update_exported_volumes")
        for fg in furnace_groups:
            exported_volumes = 0.0
            if self.G is not None and fg.furnace_group_id in self.G.nodes:
                outgoing_edges = list(self.G.out_edges(fg.furnace_group_id, data=True))
                logger.debug(f"[ALLOCATION] FG {fg.furnace_group_id}: Found {len(outgoing_edges)} outgoing edges")
                for _, dest, edge_data in outgoing_edges:
                    volume = edge_data.get(volume_attribute, 0)
                    exported_volumes += volume
                    logger.debug(f"[ALLOCATION] FG {fg.furnace_group_id} -> {dest}: volume = {volume}")
                fg.set_allocated_volumes(exported_volumes)
                logger.debug(f"[ALLOCATION] FG {fg.furnace_group_id}: total allocated_volumes = {exported_volumes}")
            else:
                fg.set_allocated_volumes(0.0)
                logger.debug(f"[ALLOCATION] FG {fg.furnace_group_id}: allocated_volumes = 0.0 (no outgoing edges)")

    def extract_transportation_costs(
        self,
        furnace_groups: list[FurnaceGroup],
        transport_costs_attr="transport_cost",
        commodity_attr="commodity",
        allocations_attr="allocations",
    ) -> dict[str, list[dict[str, float]]]:
        """Extract detailed transportation cost data for each furnace group's incoming shipments.

        Collects all inbound edges to each furnace group and extracts their transport costs,
        allocations, and commodity information for detailed cost accounting.

        Args:
            furnace_groups: List of FurnaceGroup objects to extract data for.
            transport_costs_attr: Edge attribute name for transport cost (default: "transport_cost").
            commodity_attr: Edge attribute name for commodity identifier (default: "commodity").
            allocations_attr: Edge attribute name for allocation volume (default: "allocations").

        Returns:
            Dictionary mapping furnace_group_id to list of dicts, where each dict contains:
                - "source": Source node name
                - allocations_attr: Allocation volume value
                - commodity_attr: Commodity name
                - transport_costs_attr: Transport cost value

        Notes:
            Returns empty list for furnaces not found in graph.
        """
        _test_this: dict[str, list[dict[str, float]]] = {}
        for fg in furnace_groups:
            if self.G is not None and fg.furnace_group_id in self.G.nodes:
                _test_this[fg.furnace_group_id] = []
                ingoing_edges = list(self.G.in_edges(fg.furnace_group_id, data=True))
                for source, recipient, edge_data in ingoing_edges:
                    _test_this[recipient].append(
                        {
                            "source": source,
                            allocations_attr: edge_data[allocations_attr],
                            commodity_attr: edge_data[commodity_attr],
                            transport_costs_attr: edge_data[transport_costs_attr],
                        }
                    )
        return _test_this

    def update_furnace_group_utilisation(self, furnace_groups: list[FurnaceGroup], volume_attribute="volume"):
        """Calculate and set utilization rates for furnace groups based on allocated volumes.

        Computes utilization_rate = allocated_volumes / capacity for each furnace group.
        First updates allocated_volumes from graph edges, then calculates the ratio.

        Args:
            furnace_groups: List of FurnaceGroup objects to update.
            volume_attribute: Edge attribute name for volumes (default: "volume").

        Side Effects:
            - Calls update_exported_volumes() to set fg.allocated_volumes.
            - Sets fg.utilization_rate for each furnace group.
            - Logs debug warning if capacity is 0.

        Notes:
            - Utilization rate is capped between 0.0 and capacity (no explicit cap applied).
            - Zero-capacity furnaces get utilization_rate = 0.
        """
        logger = logging.getLogger(f"{__name__}.update_furnace_group_utilisation")
        self.update_exported_volumes(furnace_groups=furnace_groups, volume_attribute=volume_attribute)
        for fg in furnace_groups:
            fg.utilization_rate = fg.allocated_volumes / fg.capacity if fg.capacity > 0 else 0
            if fg.capacity <= 0:
                # raise Warning(f"Furnace group capacity is 0 for {fg.furnace_group_id}")
                logger.debug(
                    f"Furnace group capacity is 0 for {fg.furnace_group_id} \n and allocation is {fg.allocated_volumes}"
                )

    def update_bill_of_materials(self, furnace_groups: list[FurnaceGroup]):
        """Update bill of materials for furnace groups using trade network allocations.

        Extracts material and energy demands from incoming graph edges, aggregates them by
        commodity, and calculates total and unit costs. Sets the bill_of_materials attribute
        on each FurnaceGroup with consolidated procurement data.

        Args:
            furnace_groups: List of FurnaceGroup objects to update with BOM data.

        Side Effects:
            Sets fg.bill_of_materials for each furnace group to a dict with structure:
                {
                    "materials": {
                        commodity_name: {
                            "demand": float,       # Total volume required (tons)
                            "total_cost": float,   # Total cost (USD)
                            "unit_cost": float     # Cost per ton (USD/ton)
                        }
                    },
                    "energy": {
                        commodity_name: {
                            "demand": float,       # Total energy demand
                            "total_cost": float,   # Total energy cost (USD)
                            "unit_cost": float     # Energy cost per unit (USD/unit)
                        }
                    }
                }

        Notes:
            - Material data comes from node allocations in the graph (upstream costs propagated).
            - Energy data comes from processing_energy_cost on incoming edges.
            - Aggregates multiple shipments of the same commodity into single BOM entries.
            - Must be called after set_up_network_and_propagate_costs() completes.
            - Logs detailed debug information to "update_bill_of_materials" logger.
        """
        # Create a custom logger specifically for this function
        logger = logging.getLogger("steelo.domain.trade_modelling.TM_PAM_connector.update_bill_of_materials")

        logger.debug(f"[BOM] Starting update_bill_of_materials for {len(furnace_groups)} furnace groups")
        if self.G is not None:
            logger.debug(f"[BOM] Graph has {len(self.G.nodes)} nodes and {len(self.G.edges)} edges")
        else:
            logger.debug("[BOM] Graph is None!")

        bom_issue_count_materials = 0
        bom_issue_count_energy = 0
        for fg in furnace_groups:
            logger.debug(
                f"[BOM] Starting BOM update for FG {fg.furnace_group_id} - Tech: {fg.technology.name}, Status: {fg.status}"
            )
            _ = {"materials": [], "energy": []}
            product_volume = 0.0
            if self.G is not None:
                in_edges = list(self.G.in_edges(fg.furnace_group_id))
                logger.debug(f"[BOM] FG {fg.furnace_group_id}: Found {len(in_edges)} incoming edges")
                for edges in in_edges:
                    edge_data = self.G.get_edge_data(*edges)
                    for commodity, attr_dict in edge_data.items():
                        # costs = self.G.nodes[edges[0]]["unit_cost"]
                        # unit_costs = costs[commodity] if isinstance(costs, dict) and commodity in costs else costs
                        processing_energy_cost = attr_dict.get("processing_energy_cost", 0.0)
                        energy_breakdown = attr_dict.get("processing_energy_breakdown") or {}

                        if energy_breakdown:
                            for carrier, carrier_unit_cost in energy_breakdown.items():
                                _["energy"].append(
                                    {carrier: {"demand": attr_dict["volume"], "unit_cost": carrier_unit_cost}}
                                )
                        elif processing_energy_cost:
                            _["energy"].append(
                                {commodity: {"demand": attr_dict["volume"], "unit_cost": processing_energy_cost}}
                            )
            else:
                logger.debug(f"[BOM] FG {fg.furnace_group_id}: Graph is None, no edges to process")

            if self.G is not None and fg.furnace_group_id in self.G.nodes:
                export_dict = self.G.nodes[fg.furnace_group_id].get("export", {}) or {}
                product_volume = float(sum(value for value in export_dict.values() if value))
            if product_volume <= 0:
                product_volume = float(fg.production) if getattr(fg, "production", 0.0) else 0.0
            if product_volume <= 0:
                logger.warning(
                    "[BOM] FG %s: Unable to determine product volume; falling back to input-based costs",
                    fg.furnace_group_id,
                )

            collect: dict[str, dict[str, dict[str, float]]] = {"materials": {}, "energy": {}}

            # Log the raw procurement data
            logger.debug(f"[BOM] FG {fg.furnace_group_id}: Processing procurement data with keys: {list(_.keys())}")

            for key, procurement_dict in _.items():
                logger.debug(
                    f"[BOM] FG {fg.furnace_group_id}: Processing key '{key}' with {len(procurement_dict)} items"
                )
                for commodity_dict in procurement_dict:
                    for commodity, demand_cost in commodity_dict.items():
                        if commodity not in collect[key]:
                            collect[key][commodity] = {
                                "demand": demand_cost["demand"],
                                "total_cost": demand_cost["unit_cost"] * demand_cost["demand"],
                            }
                            if product_volume > 0:
                                collect[key][commodity]["product_volume"] = product_volume
                        else:
                            collect[key][commodity]["demand"] += demand_cost["demand"]
                            collect[key][commodity]["total_cost"] += demand_cost["unit_cost"] * demand_cost["demand"]
                            if product_volume > 0:
                                collect[key][commodity]["product_volume"] = product_volume
                        input_demand = collect[key][commodity]["demand"]
                        collect[key][commodity]["unit_cost"] = (
                            collect[key][commodity]["total_cost"] / product_volume
                            if product_volume > 0
                            else collect[key][commodity]["total_cost"] / input_demand
                            if input_demand
                            else 0.0
                        )

            logger.debug(f"[BOM] FG {fg.furnace_group_id}: energy items = {len(collect['energy'])}")
            energy_keys = list(collect["energy"].keys())
            has_h2 = "hydrogen" in energy_keys
            if has_h2 or fg.technology.name.upper() in ("DRI-H2", "DRI+CCS", "E-WIN", "DRI-EAF"):
                logger.debug(
                    "[H2-DEBUG BOM] FG:%s Tech:%s | edges:%d | energy carriers: %s | has_hydrogen: %s",
                    fg.furnace_group_id,
                    fg.technology.name,
                    len(in_edges) if self.G is not None else 0,
                    energy_keys,
                    has_h2,
                )

            if self.G is None:
                logger.debug(f"[BOM] FG {fg.furnace_group_id}: Graph is None - unable to populate materials allocation")
            elif fg.furnace_group_id not in self.G.nodes:
                logger.warning(
                    f"Furnace group {fg.furnace_group_id} not found in graph nodes during bill of materials update"
                )
                logger.debug(f"[BOM] FG {fg.furnace_group_id} NOT FOUND in graph nodes!")
            else:
                logger.debug(f"[BOM] FG {fg.furnace_group_id}: Checking graph for materials allocation")
                node_allocations = self.G.nodes[fg.furnace_group_id].get("allocations", {})
                logger.debug(f"[BOM] FG {fg.furnace_group_id}: found {len(node_allocations)} allocations in graph node")

                if not node_allocations:
                    logger.debug(f"[BOM] FG {fg.furnace_group_id}: No allocations available to populate materials BOM")

                for comm, attr_dict in node_allocations.items():
                    volume = attr_dict["Volume"]
                    cost = attr_dict["Cost"]
                    material_cost = attr_dict.get("MaterialCost", cost)  # Fallback to Cost if MaterialCost not present
                    unit_cost = cost / volume if volume > 0 else 0

                    collect["materials"][comm] = {
                        "demand": volume,  # Input volume (tons)
                        "total_cost": cost,  # Total cost including current step's energy
                        "unit_cost": cost / product_volume if product_volume > 0 else unit_cost,  # Per ton of output
                        "total_material_cost": material_cost,  # Total material cost excluding current step's energy
                        "unit_material_cost": material_cost / product_volume
                        if product_volume > 0
                        else 0,  # Per ton of output
                        "product_volume": product_volume,
                    }
                    if product_volume <= 0 and volume > 0:
                        collect["materials"][comm]["product_volume"] = volume

            logger.debug(f"[BOM] FG {fg.furnace_group_id}: Final BOM materials = {list(collect['materials'].keys())}")

            util_rate = getattr(fg, "utilization_rate", None)
            if util_rate is not None and util_rate <= 0:
                fg.bill_of_materials = collect
            else:
                # make sure BOM exists when we have allocations, otherwise use existing BOM
                existing_bom: dict[str, dict[str, dict[str, float]]] | None = (
                    copy.deepcopy(fg.bill_of_materials) if isinstance(fg.bill_of_materials, dict) else None
                )

                if not collect["materials"] and not collect["energy"]:
                    if existing_bom and (existing_bom.get("materials") or existing_bom.get("energy")):
                        logger.warning(
                            "[BOM] FG %s: Trade module returned no materials/energy "
                            "(production=%s). Preserving existing BOM with %d material entries.",
                            fg.furnace_group_id,
                            getattr(fg, "production", None),
                            len(existing_bom.get("materials", {})),
                        )
                        logger.debug(
                            "[H2-DEBUG BOM-PRESERVED] FG:%s Tech:%s | existing energy keys: %s",
                            fg.furnace_group_id,
                            fg.technology.name,
                            list(existing_bom.get("energy", {}).keys()),
                        )
                        continue
                    logger.warning(
                        "[BOM] FG %s: Trade module returned no materials/energy "
                        "(production=%s) and no existing BOM found.",
                        fg.furnace_group_id,
                        getattr(fg, "production", None),
                    )
                    # Fall through to initialize or keep the merged_bom structure

                merged_bom: dict[str, dict[str, dict[str, float]]] = existing_bom or {"materials": {}, "energy": {}}

                def _ensure_material_shares(materials: dict[str, dict[str, float]]) -> None:
                    if not materials:
                        return
                    product_volume = None
                    for values in materials.values():
                        pv = values.get("product_volume")
                        if isinstance(pv, (int, float)) and pv > 0:
                            product_volume = float(pv)
                            break
                    if product_volume is None or product_volume <= 0:
                        total_output = sum(float(v.get("product_volume") or 0.0) for v in materials.values())
                        if total_output > 0:
                            product_volume = total_output
                        else:
                            demand_sum = sum(float(v.get("demand") or 0.0) for v in materials.values())
                            product_volume = demand_sum if demand_sum > 0 else None
                    if not product_volume or product_volume <= 0:
                        return
                    for commodity, values in materials.items():
                        demand = float(values.get("demand") or 0.0)
                        values["demand_share_pct"] = demand / product_volume

                if collect["materials"]:
                    merged_bom["materials"] = collect["materials"]
                    _ensure_material_shares(merged_bom["materials"])
                elif merged_bom.get("materials"):
                    logger.error(
                        "[BOM] FG %s: Preserving %d existing material entries (no new allocations).",
                        fg.furnace_group_id,
                        len(merged_bom["materials"]),
                    )
                    bom_issue_count_materials += 1
                else:
                    logger.warning(
                        "[BOM] FG %s: No material allocations available; BOM materials remain empty.",
                        fg.furnace_group_id,
                    )

                if collect["energy"]:
                    merged_bom["energy"] = collect["energy"]
                elif merged_bom.get("energy"):
                    logger.error(
                        "[BOM] FG %s: Preserving %d existing energy entries (no new allocations).",
                        fg.furnace_group_id,
                        len(merged_bom["energy"]),
                    )
                    bom_issue_count_energy += 1
                else:
                    logger.debug(
                        "[BOM] FG %s: No energy allocations available; BOM energy remains empty.",
                        fg.furnace_group_id,
                    )

                fg.bill_of_materials = merged_bom

                if (
                    diag.diagnostics_enabled()
                    and self.current_year is not None
                    and fg.technology.name.upper() == "BOF"
                    and diag.allow_heavy_exports(self.current_year, self.diagnostics_active_bof_count)
                ):
                    materials = merged_bom.get("materials", {})
                    for material_name, values in materials.items():
                        diag.append_csv(
                            f"bom_summary_{self.current_year}.csv",
                            ["year", "furnace_group_id", "technology", "material", "demand", "total_cost", "unit_cost"],
                            [
                                self.current_year,
                                fg.furnace_group_id,
                                fg.technology.name,
                                material_name,
                                float(values.get("demand", 0.0)),
                                float(values.get("total_cost", values.get("total_material_cost", 0.0))),
                                float(values.get("unit_cost", 0.0)),
                            ],
                        )

        return bom_issue_count_materials, bom_issue_count_energy

    def update_furnace_group_emissions(self, furnace_groups: list[FurnaceGroup]):
        """Calculate and set emissions for furnace groups based on their bill of materials.

        Calls each furnace group's emission calculation method if it has a valid BOM with
        materials. Sets emissions to empty dict if BOM is missing or has no materials.

        Args:
            furnace_groups: List of FurnaceGroup objects to calculate emissions for.

        Side Effects:
            - Calls fg.set_emissions_based_on_allocated_volumes() for groups with valid BOMs.
            - Sets fg.emissions = {} for groups without valid BOMs.
            - Logs warnings for furnaces missing BOM data.

        Notes:
            - Must be called after update_bill_of_materials() has populated the BOMs.
            - Emissions calculation uses material volumes and emission factors from BOM.
            - Requires fg.bill_of_materials["materials"] to be non-empty.
        """
        logger = logging.getLogger(f"{__name__}.TM_PAM_connector.update_furnace_group_emissions")
        # self.update_exported_volumes(furnace_groups=furnace_groups)
        for fg in furnace_groups:
            if fg.bill_of_materials and fg.bill_of_materials["materials"]:
                fg.set_emissions_based_on_allocated_volumes()
            else:
                # Log why emissions are being set to empty
                if not fg.bill_of_materials:
                    logger.warning(
                        f"[EMISSIONS] FG {fg.furnace_group_id}: No bill_of_materials, setting emissions to empty dict"
                    )
                elif not fg.bill_of_materials.get("materials"):
                    logger.warning(
                        f"[EMISSIONS] FG {fg.furnace_group_id}: Empty materials in BOM, setting emissions to empty dict"
                    )
                fg.emissions = {}
