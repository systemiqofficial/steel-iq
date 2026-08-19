import copy
import logging
import math
from typing import Any
import networkx as nx
from steelo.adapters.repositories.in_memory_repository import (
    PlantInMemoryRepository,
)
from steelo.domain.calculate_costs import ENERGY_FEEDSTOCK_KEYS
from steelo.domain.models import PrimaryFeedstock, FurnaceGroup, TransportKPI
from steelo.domain.trade_modelling.trade_lp_modelling import Allocations, ProcessType
from steelo.domain.constants import LP_TOLERANCE
from steelo.domain import diagnostics as diag
from steelo.utilities.utils import normalize_name


# logging.getLogger().setLevel(logging.WARNING)  # Commented out to avoid setting root logger


def _required_quantity_by_charge(fg) -> dict[str, float]:
    """Map each metallic charge of the FG's chosen-reductant feedstock rows to its req_qty.

    Raises if two rows for the same charge disagree — the snapshot conversion would
    otherwise silently use whichever row happened to come last."""
    req_by_charge: dict[str, float] = {}
    for feedstock in fg.effective_primary_feedstocks:
        if not isinstance(feedstock.metallic_charge, str):
            continue
        charge = feedstock.metallic_charge.lower()
        req_qty = feedstock.required_quantity_per_ton_of_product
        if charge in req_by_charge and req_by_charge[charge] != req_qty:
            raise ValueError(
                f"Furnace group {fg.furnace_group_id} has conflicting required_quantity_per_ton_of_product "
                f"values for metallic charge '{charge}': {req_by_charge[charge]} vs {req_qty}"
            )
        req_by_charge[charge] = req_qty
    return req_by_charge


def _energy_intensity_by_charge(fg) -> dict[str, dict[str, float]]:
    """Per-charge carrier quantities per tonne of product (energy requirements plus
    energy-like secondary feedstocks), keyed by normalised carrier name."""
    intensities: dict[str, dict[str, float]] = {}
    for feedstock in fg.effective_primary_feedstocks:
        if not isinstance(feedstock.metallic_charge, str):
            continue
        carriers: dict[str, float] = {}
        for source in (feedstock.energy_requirements or {}, feedstock.secondary_feedstock or {}):
            for carrier, amount in source.items():
                normalized = normalize_name(carrier)
                if normalized in ENERGY_FEEDSTOCK_KEYS:
                    carriers[normalized] = carriers.get(normalized, 0.0) + amount
        intensities[feedstock.metallic_charge.lower()] = carriers
    return intensities


def _ensure_material_shares(materials: dict[str, dict[str, float]]) -> None:
    """Stamp each material entry with demand_share_pct = demand / product volume (input
    tonnes per tonne of product), the weight the cost-breakdown product-share maths needs."""
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
    for values in materials.values():
        demand = float(values.get("demand") or 0.0)
        values["demand_share_pct"] = demand / product_volume


class TM_PAM_connector:
    """
    Connects the trade module with the PAM model — extracts data from the
    trade module and updates unit production costs and utilisation rates.
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
            processing_energy_cost: Energy costs (total + carrier breakdown) by furnace group and
                commodity, in USD per tonne of metallic INPUT (converted from the FG's per-tonne-of-
                product energy_vopex dicts using each charge's required_quantity_per_ton_of_product).
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
                req_by_charge = _required_quantity_by_charge(fg) if feed_totals else {}
                for commodity, total_cost in feed_totals.items():
                    if not isinstance(commodity, str):
                        continue
                    normalized_commodity = commodity.lower()
                    # energy_vopex_* values are USD per tonne of product; edge volumes flow in
                    # metallic-input tonnes, so convert here — once — to USD per tonne of input.
                    req_qty = req_by_charge.get(normalized_commodity)
                    if not req_qty:
                        raise ValueError(
                            f"Furnace group {fg.furnace_group_id} carries energy cost for metallic charge "
                            f"'{commodity}' but has no effective primary feedstock row with a "
                            "required_quantity_per_ton_of_product to convert it with"
                        )
                    breakdown = feed_breakdowns.get(commodity) or feed_breakdowns.get(normalized_commodity) or {}
                    normalized_breakdown = {
                        normalize_name(carrier): float(cost) / req_qty for carrier, cost in breakdown.items()
                    }
                    if float(total_cost) and not normalized_breakdown:
                        raise ValueError(
                            f"Furnace group {fg.furnace_group_id} has energy cost for metallic charge "
                            f"'{commodity}' but no per-carrier breakdown to book it by"
                        )
                    per_feed_energy[normalized_commodity] = {
                        "total": float(total_cost) / req_qty,
                        "carriers": normalized_breakdown,
                    }
                self.processing_energy_cost[fg.furnace_group_id] = per_feed_energy
                self.chosen_reductant[fg.furnace_group_id] = fg.chosen_reductant
        self.flat_feedstocks_dict = {}
        self.feedstock_energy_requirements = {}
        for _key, items in self.dynamic_feedstocks.items():
            for entry in items:
                name_lower = entry.name.lower()
                self.flat_feedstocks_dict[name_lower] = entry
                self.feedstock_energy_requirements[name_lower] = entry.energy_requirements

        self.plants = [p.plant_id for p in plants.list()]
        # self.furnaces = [fg for p in plants.list() for fg in p.furnace_groups]

        self.plants_repo = plants

        # Store transport costs in a dictionary for quick lookup
        self.transport_costs: dict[tuple[str, str, str], float] = {}
        if transport_kpis:
            for kpi in transport_kpis:
                key = (kpi.reporter_iso, kpi.partner_iso, kpi.commodity.lower())
                self.transport_costs[key] = kpi.transportation_cost

        self.iron_furnaces = []
        self.steel_furnaces = []
        self.bof_furnaces = []
        for p in plants.list():
            for fg in p.furnace_groups:
                fg_id = fg.furnace_group_id
                if isinstance(fg.technology.product, str):
                    product_lower = fg.technology.product.lower()
                    if product_lower == "iron":
                        self.iron_furnaces.append(fg_id)
                    elif product_lower == "steel":
                        self.steel_furnaces.append(fg_id)
                if fg.technology.name.upper() == "BOF":
                    self.bof_furnaces.append(fg_id)

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

    def get_tariff_cost(self, from_iso: str, to_iso: str, commodity: str) -> float:
        """Retrieve tariff cost between two countries for a specific commodity.

        Supports wildcard keys: checks exact match first, then wildcard source,
        wildcard destination, and wildcard commodity. All matching tariffs are summed,
        mirroring the LP's ``return_potential_tariff_keys`` logic.

        Args:
            from_iso: Source country ISO3 code.
            to_iso: Destination country ISO3 code.
            commodity: Commodity name (case-insensitive).

        Returns:
            Tariff cost per ton in USD. Returns 0.0 if no tariffs apply.
        """
        comm = commodity.lower()
        total = 0.0
        for key in [
            (from_iso, to_iso, comm),
            ("*", to_iso, comm),
            (from_iso, "*", comm),
            (from_iso, to_iso, "*"),
        ]:
            total += self.tariff_taxes.get(key, 0.0)
        return total

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
            if hasattr(plant_energy_cost, normalize_name(key)):
                process_energy_cost += getattr(plant_energy_cost, normalize_name(key)) * value
            else:
                # logging.debug(f"Key {key} not found in global cost dict or plant")

                process_energy_cost += float(global_cost_dict[normalize_name(key)]) * value
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
        for _from_node, _to_node, _commodity, data in self.G.edges(keys=True, data=True):
            if volume_attr in data:
                volume = data[volume_attr]
                effectiveness = data.get(effectiveness_attr, 1)
                if effectiveness is None:
                    effectiveness = 1
                # Calculate the allocation based on the volume and effectiveness
                data[allocation_attr] = volume / effectiveness if effectiveness > 0 else 0

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
        - Uses `self.processing_energy_cost` to fetch energy costs per process (USD per
          tonne of metallic input, matching the input-tonne edge volumes).
        - Uses `self.flat_feedstocks_dict` to lookup primary outputs and efficiencies.
        """
        logger = logging.getLogger(f"{__name__}.create_graph")

        # Store tariff taxes for edge attribute lookup
        self.tariff_taxes: dict[tuple[str, str, str], float] = solved_trade_allocations.tariff_taxes or {}
        logger.debug("[TARIFF] Loaded %d tariff entries", len(self.tariff_taxes))

        # Initialize an empty directed multigraph
        self.G = nx.MultiDiGraph()

        # Iterate through each allocation entry (from, to, commodity) → volume
        for (from_pc, to_pc, comm), alloc_value in solved_trade_allocations.allocations.items():
            # Skip zero or negative allocations
            if alloc_value <= LP_TOLERANCE:
                continue

            # Normalize commodity name
            commodity = comm.name.lower()

            # Build a process-identifier string, including reductant if chosen. Reads
            # .technology (the shared technology name) rather than .name, since .name is
            # unique per reductant variant (e.g. "BF_coke") and would double-encode the
            # reductant into this string otherwise.
            if to_pc.name in self.chosen_reductant:
                reductant = str(self.chosen_reductant[to_pc.name]).lower()
                process = f"{to_pc.process.technology.lower()}_{commodity}_{reductant}"
            else:
                process = f"{to_pc.process.technology.lower()}_{commodity}"

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
                # 3. Tariff cost from LP tariff taxes (import/export duties)
                "tariff_cost": self.get_tariff_cost(from_pc.location.iso3, to_pc.location.iso3, commodity),
                # 4. Processing energy cost, if defined for this destination
                "processing_energy_cost": total_energy_cost,
                "processing_energy_breakdown": energy_breakdown,
                # 5. Process identifier and commodity tag
                "process": process,
                "commodity": commodity,
                # 6. Primary output of this process, if in the flat feedstocks map
                "output": (
                    next(iter(self.flat_feedstocks_dict[process].get_primary_outputs()), None)
                    if process in self.flat_feedstocks_dict
                    else None
                ),
                # 7. Process efficiency (required quantity per ton of product)
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
                # Producing furnace: stamp own per-unit cost (carbon) so propagation can embed
                # it into outgoing flows. Consumed via downstream BOMs; never appears in this
                # furnace's own BOM (built from incoming allocations).
                self.G.add_node(
                    from_name,
                    product_cost={},
                    unit_cost={},
                    own_unit_cost=float(from_pc.production_cost or 0.0),
                )

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

            if edge_attrs["tariff_cost"] > 0:
                logger.debug(
                    "[TARIFF] %s -> %s (%s): tariff=$%.2f/t",
                    from_name,
                    to_name,
                    commodity,
                    edge_attrs["tariff_cost"],
                )

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
        Mutates `self.G` in place:
            1. Per-commodity input costs accumulated in `node[allocation_attr]`.
            2. Per-commodity export volumes in `node[export_attr]`.
            3. Per-commodity unit costs in `node[unit_cost_attr]`.

        Notes
        -----
        - Requires `self.G` to be a DAG; `nx.topological_sort` raises on cycles.
        - Skips any sink node (no outgoing edges) in propagation phase.
        - Leaves zero-volume edges effectively ignored in normalization.
        """
        logger = logging.getLogger(f"{__name__}.propage_cost_forward_by_layers_and_normalize")
        G = self.G
        edge_count = 0

        # Topological order guarantees a node is processed only after ALL of its suppliers
        # have pushed costs into it — BFS discovery order does not, and made downstream
        # costs depend on the allocation dict's insertion order on multi-tier chains.
        for u in nx.topological_sort(G):
            node_cost = G.nodes[u].get(source_attr, {})  # may be dict by commodity
            unit_cost = {}

            if allocation_attr in G.nodes[u]:
                # Initialize export dict if it doesn't exist
                if export_attr not in G.nodes[u]:
                    G.nodes[u][export_attr] = {}
                # Accumulate export volumes by commodity
                for src, v, comm, edata in G.out_edges(u, keys=True, data=True):
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

                # Normalize by export volume
                export = G.nodes[u].get(export_attr, {})

                # For multi-output processes (e.g., BF producing hot_metal AND pig_iron),
                # divide total input cost by TOTAL output volume across all commodities
                # to avoid inflating per-unit costs
                if isinstance(node_cost, dict) and G.in_degree(u) > 0 and len(export) > 1:
                    # Multi-output process: use total export volume
                    export_volume_at_u = sum(export.values())
                else:
                    # Single-output process or supplier: use commodity-specific volume
                    export_volume_at_u = export.get(comm, 1.0)

                per_unit_base = base_cost / export_volume_at_u
                # Embed producing furnaces' own carbon onto outgoing flows. Suppliers
                # (root nodes) are skipped — their cost already enters via base_cost.
                if G.in_degree(u) > 0:
                    own = G.nodes[u].get("own_unit_cost")
                    if own:
                        per_unit_base += float(own)
                unit_cost.update({comm: per_unit_base})
                if G.out_degree(v) == 0:
                    # print(f"Skipping sink node {v} with no outgoing edges")
                    continue
                # Calculate cost components separately
                # Material cost includes upstream material + all upstream energy + transport + tariffs
                # We want to track the material cost EXCLUDING the current step's energy
                volume = edata.get(volume_attr, 0.0)
                material_tariff_transportation_cost = (
                    per_unit_base + edata.get(transport_attr, 0.0) + edata.get("tariff_cost", 0.0)
                ) * volume
                current_step_energy_cost = edata.get(process_attr, 0.0) * volume
                edge_cost = material_tariff_transportation_cost + current_step_energy_cost

                tariff_unit = edata.get("tariff_cost", 0.0)
                if tariff_unit > 0:
                    logger.debug(
                        "[COST PROP] %s -> %s (%s): base=$%.2f transport=$%.2f tariff=$%.2f energy=$%.2f",
                        u,
                        v,
                        comm,
                        per_unit_base,
                        edata.get(transport_attr, 0.0),
                        tariff_unit,
                        edata.get(process_attr, 0.0),
                    )

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
                    total_unit = base_unit + transport_unit + tariff_unit + energy_unit
                    delta = total_unit - base_unit
                    delta_pct = (delta / base_unit * 100) if base_unit else None
                    if delta > 500 or (delta_pct is not None and delta_pct > 100):
                        delta_pct_str = f"{delta_pct:.1f}" if delta_pct is not None else "n/a"
                        diag.append_text(
                            f"cost_propagation/{self.current_year}.txt",
                            [
                                "node={node}, source={src}, commodity={comm}, "
                                "base={base:.2f}, transport={transport:.2f}, "
                                "tariff={tariff:.2f}, energy={energy:.2f}, "
                                "total={total:.2f}, delta={delta:.2f}, "
                                "delta_pct={delta_pct}".format(
                                    node=v,
                                    src=u,
                                    comm=comm,
                                    base=base_unit,
                                    transport=transport_unit,
                                    tariff=tariff_unit,
                                    energy=energy_unit,
                                    total=total_unit,
                                    delta=delta,
                                    delta_pct=delta_pct_str,
                                )
                            ],
                        )

                # Accumulate cost and volume
                # Store both total cost and material cost (excluding current step's energy)
                # MaterialCost includes upstream material + ALL upstream costs
                # (including upstream energy) + current transport + tariffs
                # EXCLUDES the current step's processing energy
                prev = G.nodes[v][allocation_attr].get(comm, {"Cost": 0.0, "MaterialCost": 0.0, "Volume": 0.0})
                prev["Cost"] += edge_cost  # Total cost including current step's energy
                prev["MaterialCost"] += material_tariff_transportation_cost  # Excludes current step's energy only
                prev["Volume"] += volume
                G.nodes[v][allocation_attr][comm] = prev

            G.nodes[u][unit_cost_attr].update(unit_cost)

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
            - Utilization rate is clamped to at most 1.0; an allocation above capacity is a
              solver anomaly and is logged as a warning.
            - Zero-capacity furnaces get utilization_rate = 0.
        """
        logger = logging.getLogger(f"{__name__}.update_furnace_group_utilisation")
        self.update_exported_volumes(furnace_groups=furnace_groups, volume_attribute=volume_attribute)
        for fg in furnace_groups:
            raw_rate = fg.allocated_volumes / fg.capacity if fg.capacity > 0 else 0
            if raw_rate > 1.0:
                logger.warning(
                    f"LP allocated {fg.allocated_volumes:,.0f} t to furnace group {fg.furnace_group_id} "
                    f"with capacity {fg.capacity:,.0f} t (utilisation {raw_rate:.3f}); clamping to 1.0"
                )
            fg.utilization_rate = min(1.0, raw_rate)
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
                            "demand": float,       # Carrier quantity (t or kWh, post-conversion units)
                            "total_cost": float,   # Total energy cost (USD)
                            "unit_cost": float     # Energy cost per tonne of product (USD/t)
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

        logger.debug(
            "[BOM] Starting update_bill_of_materials for %d furnace groups",
            len(furnace_groups),
        )
        if self.G is not None:
            logger.debug(
                "[BOM] Graph has %d nodes and %d edges",
                len(self.G.nodes),
                len(self.G.edges),
            )
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
                # Iterate edge-wise: keyless in_edges repeats the (u, v) pair once per parallel
                # edge and get_edge_data(u, v) returns ALL parallel edges, double-booking every
                # commodity arriving from a multi-commodity source (e.g. a DRI plant shipping
                # dri_mid and hbi_mid to the same BOF).
                in_edges = list(self.G.in_edges(fg.furnace_group_id, data=True))
                logger.debug(f"[BOM] FG {fg.furnace_group_id}: Found {len(in_edges)} incoming edges")
                for _source, _target, attr_dict in in_edges:
                    # The __init__ snapshot guarantees a per-carrier breakdown wherever energy cost exists.
                    energy_breakdown = attr_dict.get("processing_energy_breakdown") or {}
                    for carrier, carrier_unit_cost in energy_breakdown.items():
                        _["energy"].append({carrier: {"demand": attr_dict["volume"], "unit_cost": carrier_unit_cost}})
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

                    if material_cost > 0 and material_cost != cost:
                        logger.debug(
                            "[BOM] FG %s: %s total_material_cost=$%.2f (total_cost=$%.2f, diff=$%.2f incl. tariffs)",
                            fg.furnace_group_id,
                            comm,
                            material_cost,
                            cost,
                            cost - material_cost,
                        )

            logger.debug(f"[BOM] FG {fg.furnace_group_id}: Final BOM materials = {list(collect['materials'].keys())}")

            # Two-leg check on freshly booked energy: the ledger total (built from edge costs
            # x volumes) must equal the FG's own per-product energy vopex x product tonnes.
            # Divergence means a unit or booking regression somewhere in the edge pipeline.
            if collect["energy"] and collect["materials"]:
                vopex_by_charge = {
                    str(charge).lower(): float(value)
                    for charge, value in (getattr(fg, "energy_vopex_by_input", {}) or {}).items()
                    if isinstance(charge, str)
                }
                req_by_charge = _required_quantity_by_charge(fg)
                expected_energy = sum(
                    material["demand"] / req * vopex_by_charge[charge]
                    for charge, material in collect["materials"].items()
                    if charge in vopex_by_charge and (req := req_by_charge.get(charge))
                )
                booked_energy = sum(entry["total_cost"] for entry in collect["energy"].values())
                if not math.isclose(booked_energy, expected_energy, rel_tol=0.0, abs_tol=0.01):
                    logger.error(
                        "Energy booking mismatch for furnace group %s: booked %.2f USD vs %.2f USD "
                        "expected from per-product energy vopex x product tonnes",
                        fg.furnace_group_id,
                        booked_energy,
                        expected_energy,
                    )

                # Restate energy demand as carrier quantities (t / kWh, matching the price
                # units) instead of the edge's metallic input tonnage.
                intensity_by_charge = _energy_intensity_by_charge(fg)
                for carrier, entry in collect["energy"].items():
                    entry["demand"] = sum(
                        material["demand"] / req * intensity_by_charge.get(charge, {}).get(carrier, 0.0)
                        for charge, material in collect["materials"].items()
                        if (req := req_by_charge.get(charge))
                    )

            util_rate = getattr(fg, "utilization_rate", None)
            if util_rate is not None and util_rate <= 0:
                _ensure_material_shares(collect["materials"])
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
                        continue
                    logger.warning(
                        "[BOM] FG %s: Trade module returned no materials/energy "
                        "(production=%s) and no existing BOM found.",
                        fg.furnace_group_id,
                        getattr(fg, "production", None),
                    )
                    # Fall through to initialize or keep the merged_bom structure

                merged_bom: dict[str, dict[str, dict[str, float]]] = existing_bom or {"materials": {}, "energy": {}}

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

    def validate_bom_consistency(
        self,
        furnace_groups: list[FurnaceGroup],
        aggregated_constraints: list | None = None,
        mass_balance_tolerance: float = 0.01,
        min_share_tolerance: float = 0.01,
    ) -> list[dict]:
        """Validate BOM balance and minimum-share constraints for all active furnace groups.

        Two checks are run for every FG with production > 0:

        1. **Mass balance**: the total metallic-charge input (hot_metal, pig_iron, scrap,
           DRI/HBI, etc.) should be within `mass_balance_tolerance` of the production
           volume.  A steel furnace using 1 t of metallic charge to produce 1 t of steel
           should have a ratio close to 1.  Ratios outside [1 - tol, 1 + tol] indicate a
           disaggregation error (e.g. material stranded at wrong FG).

        2. **Min-share constraints**: for each feedstock whose
           ``minimum_share_in_product > 0``, the material's actual share of the total
           metallic-charge input must be ≥ ``minimum_share_in_product - min_share_tolerance``.
           Aggregated constraints (e.g. BOF hot_metal ≥ 70%) are also checked; both the
           hot and cold form of a commodity are counted (pig_iron ≡ hot_metal).

        Args:
            furnace_groups: All furnace groups to check.
            aggregated_constraints: Optional list of ``AggregatedMetallicChargeConstraint``
                objects (same ones passed to the LP / disaggregation).
            mass_balance_tolerance: Relative tolerance for metallic input/output ratio.
                Default 10 % (0.10).
            min_share_tolerance: Absolute slack allowed on minimum-share constraints.
                Default 2 pp (0.02).

        Returns:
            List of issue dicts.  Each dict contains at least:
            ``fg_id``, ``technology``, ``check`` (``"empty_bom"``, ``"mass_balance"``,
            or ``"min_share"``), and ``message``.  All violations are also logged as
            WARNING.
        """
        logger = logging.getLogger(f"{__name__}.validate_bom_consistency")

        # Commodities that count as metallic charge (hot and cold forms).
        # Pig iron and hot metal are the same material in different transport states;
        # both count toward any hot_metal minimum.
        METALLIC_COMMODITIES: set[str] = {
            "hot_metal",
            "pig_iron",
            "dri_low",
            "dri_mid",
            "dri_high",
            "hbi_low",
            "hbi_mid",
            "hbi_high",
            "scrap",
            "scrap_steel",
            "electrolytic_iron",
            "liquid_iron",
        }
        # Hot ↔ cold equivalences for min-share matching
        HOT_COLD_EQUIV: dict[str, str] = {
            "pig_iron": "hot_metal",
            "hot_metal": "pig_iron",
            "hbi_low": "dri_low",
            "dri_low": "hbi_low",
            "hbi_mid": "dri_mid",
            "dri_mid": "hbi_mid",
            "hbi_high": "dri_high",
            "dri_high": "hbi_high",
            "liquid_iron": "electrolytic_iron",
            "electrolytic_iron": "liquid_iron",
        }

        def _equiv_names(name: str) -> set[str]:
            n = name.lower()
            return {n, HOT_COLD_EQUIV[n]} if n in HOT_COLD_EQUIV else {n}

        issues: list[dict[str, Any]] = []

        for fg in furnace_groups:
            production = getattr(fg, "allocated_volumes", 0.0) or 0.0
            if production <= 0:
                continue

            bom = fg.bill_of_materials
            if not bom or not bom.get("materials"):
                issue: dict[str, Any] = {
                    "fg_id": fg.furnace_group_id,
                    "technology": fg.technology.name,
                    "check": "empty_bom",
                    "message": (
                        f"FG {fg.furnace_group_id} ({fg.technology.name}): "
                        f"production={production:.0f}t but BOM materials are empty"
                    ),
                }
                issues.append(issue)
                logger.warning("[BOM-CHECK] %s", issue["message"])
                continue

            materials = bom["materials"]

            # --- Check 1: mass balance on metallic charge ---
            total_metallic_in = sum(
                float(info.get("demand", 0.0))
                for comm, info in materials.items()
                if comm.lower() in METALLIC_COMMODITIES
            )

            # If total metallic is zero but the FG has at least one metallic-charge
            # feedstock constraint, it should have received metallic inputs.  A
            # non-empty BOM that contains only non-metallic materials (e.g. iron_ore
            # routed to a BOF via a graph bug) would otherwise slip past all checks.
            if total_metallic_in == 0:
                feedstocks_needing_metallic = [
                    fs
                    for fs in (getattr(fg, "effective_primary_feedstocks", None) or [])
                    if normalize_name(getattr(fs, "metallic_charge", "")) in METALLIC_COMMODITIES
                    and getattr(fs, "minimum_share_in_product", 0) > 0
                ]
                if feedstocks_needing_metallic:
                    issue = {
                        "fg_id": fg.furnace_group_id,
                        "technology": fg.technology.name,
                        "check": "zero_metallic",
                        "message": (
                            f"FG {fg.furnace_group_id} ({fg.technology.name}): "
                            f"production={production:.0f}t but BOM contains no metallic-charge materials "
                            f"(expected: {[getattr(fs, 'metallic_charge', '?') for fs in feedstocks_needing_metallic]})"
                        ),
                    }
                    issues.append(issue)
                    logger.warning("[BOM-CHECK] zero_metallic: %s", issue["message"])

            if total_metallic_in > 0:
                ratio = total_metallic_in / production
                # Yield losses mean slightly more input than output is normal;
                # ratios well outside [0.9, 1.2] suggest a disaggregation error.
                lo, hi = 1.0 - mass_balance_tolerance, 1.2 + mass_balance_tolerance
                if not (lo <= ratio <= hi):
                    issue = {
                        "fg_id": fg.furnace_group_id,
                        "technology": fg.technology.name,
                        "check": "mass_balance",
                        "ratio": ratio,
                        "message": (
                            f"FG {fg.furnace_group_id} ({fg.technology.name}): "
                            f"metallic_input={total_metallic_in:.0f}t / production={production:.0f}t "
                            f"= {ratio:.3f} (expected [{lo:.2f}, {hi:.2f}])"
                        ),
                    }
                    issues.append(issue)
                    logger.warning("[BOM-CHECK] mass_balance: %s", issue["message"])

            # --- Check 2: per-feedstock minimum shares ---
            feedstocks = getattr(fg, "effective_primary_feedstocks", None) or []
            for fs in feedstocks:
                min_share = getattr(fs, "minimum_share_in_product", None)
                if not min_share or min_share <= 0:
                    continue

                equiv = _equiv_names(normalize_name(fs.metallic_charge))
                actual_demand = sum(
                    float(info.get("demand", 0.0)) for comm, info in materials.items() if comm.lower() in equiv
                )
                actual_share = actual_demand / total_metallic_in if total_metallic_in > 0 else 0.0

                if actual_share < min_share - min_share_tolerance:
                    issue = {
                        "fg_id": fg.furnace_group_id,
                        "technology": fg.technology.name,
                        "check": "min_share",
                        "commodity": fs.metallic_charge,
                        "actual_share": actual_share,
                        "required_share": min_share,
                        "message": (
                            f"FG {fg.furnace_group_id} ({fg.technology.name}): "
                            f"{fs.metallic_charge} share={actual_share:.1%} "
                            f"< minimum={min_share:.1%} "
                            f"(demand={actual_demand:.0f}t / metallic_total={total_metallic_in:.0f}t)"
                        ),
                    }
                    issues.append(issue)
                    logger.warning("[BOM-CHECK] min_share: %s", issue["message"])

            # --- Check 3: aggregated constraints (e.g. BOF hot_metal ≥ 70%) ---
            if aggregated_constraints:
                fg_tech = fg.technology.name.lower()
                for c in aggregated_constraints:
                    min_share = getattr(c, "minimum_share", None)
                    if not min_share or min_share <= 0:
                        continue
                    if str(getattr(c, "technology_name", "")).lower() != fg_tech:
                        continue
                    pattern = str(getattr(c, "feedstock_pattern", "")).lower()
                    if not pattern:
                        continue

                    # Sum demand for all materials whose name starts with pattern.
                    # Do NOT include hot/cold equivalents: constraints are defined separately for each
                    # (e.g. BOF has both "DRI" ≤ 15% and "HBI" ≤ 15%, not combined).
                    matching_demand = sum(
                        float(info.get("demand", 0.0))
                        for comm, info in materials.items()
                        if comm.lower().startswith(pattern)
                    )
                    actual_share = matching_demand / total_metallic_in if total_metallic_in > 0 else 0.0

                    if actual_share < min_share - min_share_tolerance:
                        issue = {
                            "fg_id": fg.furnace_group_id,
                            "technology": fg.technology.name,
                            "check": "aggregated_min_share",
                            "pattern": pattern,
                            "actual_share": actual_share,
                            "required_share": min_share,
                            "message": (
                                f"FG {fg.furnace_group_id} ({fg.technology.name}): "
                                f"aggregated constraint '{pattern}*' share={actual_share:.1%} "
                                f"< minimum={min_share:.1%} "
                                f"(matching={matching_demand:.0f}t / metallic_total={total_metallic_in:.0f}t)"
                            ),
                        }
                        issues.append(issue)
                        logger.warning("[BOM-CHECK] aggregated_min_share: %s", issue["message"])

                    # Check maximum share constraint (e.g. BOF DRI ≤ 15%, HBI ≤ 15%)
                    max_share = getattr(c, "maximum_share", None)
                    if max_share and max_share > 0:
                        if actual_share > max_share + min_share_tolerance:
                            issue = {
                                "fg_id": fg.furnace_group_id,
                                "technology": fg.technology.name,
                                "check": "aggregated_max_share",
                                "pattern": pattern,
                                "actual_share": actual_share,
                                "required_share": max_share,
                                "message": (
                                    f"FG {fg.furnace_group_id} ({fg.technology.name}): "
                                    f"aggregated constraint '{pattern}*' share={actual_share:.1%} "
                                    f"> maximum={max_share:.1%} "
                                    f"(matching={matching_demand:.0f}t / metallic_total={total_metallic_in:.0f}t)"
                                ),
                            }
                            issues.append(issue)
                            logger.warning("[BOM-CHECK] aggregated_max_share: %s", issue["message"])

        if issues:
            logger.warning(
                "[BOM-CHECK] Found %d BOM consistency issue(s) across %d furnace groups",
                len(issues),
                len(furnace_groups),
            )
        else:
            logger.info("[BOM-CHECK] All active furnace groups passed BOM consistency checks")

        return issues

    def correct_utilization_for_supply_constraints(
        self,
        furnace_groups: list[FurnaceGroup],
        bom_issues: list[dict[str, Any]],
    ) -> int:
        """Reduce utilization of FGs that cannot receive enough of a constrained material.

        When a FG has a min-share violation (e.g. a BOF receiving only 4.8 % hot_metal
        instead of the required ≥ 70 %) the LP-assigned production implicitly assumes a
        supply that is not physically available within ``hot_metal_radius``.  This method
        corrects by:

        1. Treating the constrained commodity's actual supply as fixed (geography-determined).
        2. Computing the maximum total metallic charge consistent with that supply and the
           minimum-share requirement: ``T_new = constrained_supply / required_share``.
        3. Scaling production and ``utilization_rate`` by ``T_new / T_old``.
        4. Keeping the constrained commodity's BOM demand unchanged; scaling all other
           metallic BOM entries down so ``T_new`` is satisfied.
        5. Scaling non-metallic BOM entries with production.
        6. Refreshing ``demand_share_pct`` and rebuilding the BOM energy entries from the
           corrected material demands (``demand / req x intensity`` and ``x vopex``), since
           the metallic mix changes non-uniformly.

        For each FG the most-binding violation (smallest ``actual_share / required_share``)
        is used.

        Args:
            furnace_groups: All furnace groups.
            bom_issues: List returned by ``validate_bom_consistency``.

        Returns:
            Number of FGs whose utilization was corrected.
        """
        logger = logging.getLogger(f"{__name__}.correct_utilization_for_supply_constraints")

        METALLIC_COMMODITIES: set[str] = {
            "hot_metal",
            "pig_iron",
            "dri_low",
            "dri_mid",
            "dri_high",
            "hbi_low",
            "hbi_mid",
            "hbi_high",
            "scrap",
            "scrap_steel",
            "electrolytic_iron",
            "liquid_iron",
        }
        HOT_COLD_EQUIV: dict[str, str] = {
            "pig_iron": "hot_metal",
            "hot_metal": "pig_iron",
            "hbi_low": "dri_low",
            "dri_low": "hbi_low",
            "hbi_mid": "dri_mid",
            "dri_mid": "hbi_mid",
            "hbi_high": "dri_high",
            "dri_high": "hbi_high",
            "liquid_iron": "electrolytic_iron",
            "electrolytic_iron": "liquid_iron",
        }

        def _equiv_names(name: str) -> set[str]:
            n = name.lower()
            return {n, HOT_COLD_EQUIV[n]} if n in HOT_COLD_EQUIV else {n}

        # Collect the most-binding min_share / aggregated_min_share issue per FG
        binding_by_fg: dict[str, dict[str, Any]] = {}
        for issue in bom_issues:
            if issue["check"] not in ("min_share", "aggregated_min_share"):
                continue
            fg_id = issue["fg_id"]
            actual_share = issue.get("actual_share", 0.0)
            required_share = issue.get("required_share", 1.0)
            scale = actual_share / required_share if required_share > 0 else 1.0
            if fg_id not in binding_by_fg or scale < binding_by_fg[fg_id]["_scale"]:
                binding_by_fg[fg_id] = {**issue, "_scale": scale}

        if not binding_by_fg:
            return 0

        fg_by_id = {fg.furnace_group_id: fg for fg in furnace_groups}
        corrected = 0

        for fg_id, issue in binding_by_fg.items():
            fg = fg_by_id.get(fg_id)
            if fg is None:
                continue

            bom = fg.bill_of_materials
            if not bom or not bom.get("materials"):
                continue

            materials = bom["materials"]
            total_metallic_in = sum(
                float(info.get("demand", 0.0))
                for comm, info in materials.items()
                if comm.lower() in METALLIC_COMMODITIES
            )
            if total_metallic_in <= 0:
                continue

            # Identify constrained commodity set (hot/cold equivalents count together)
            commodity_raw = issue.get("commodity") or issue.get("pattern") or ""
            constrained_equiv = _equiv_names(normalize_name(commodity_raw)) if commodity_raw else set()

            constrained_supply = (
                sum(
                    float(info.get("demand", 0.0))
                    for comm, info in materials.items()
                    if comm.lower() in constrained_equiv
                )
                if constrained_equiv
                else 0.0
            )

            required_share = issue["required_share"]
            if required_share <= 0 or constrained_supply <= 0:
                continue

            # Maximum total metallic consistent with fixed constrained supply + min-share
            new_total_metallic = constrained_supply / required_share
            if new_total_metallic >= total_metallic_in:
                continue  # No reduction needed (shouldn't happen, but guard)

            scale_production = new_total_metallic / total_metallic_in  # < 1

            # Scale factor for all OTHER metallic materials (excluding constrained commodity)
            other_metallic_old = total_metallic_in - constrained_supply
            other_metallic_new = new_total_metallic - constrained_supply
            scale_other = other_metallic_new / other_metallic_old if other_metallic_old > 0 else 0.0

            old_production = fg.allocated_volumes
            new_production = old_production * scale_production

            logger.warning(
                "[UTIL-CORRECT] FG %s (%s): supply-constrained by '%s' (%.1f%% < %.0f%% min). "
                "Reducing production %.0f → %.0f t, utilization %.1f%% → %.1f%%",
                fg_id,
                fg.technology.name,
                commodity_raw,
                issue["actual_share"] * 100,
                required_share * 100,
                old_production,
                new_production,
                (fg.utilization_rate or 0) * 100,
                (new_production / fg.capacity * 100) if fg.capacity > 0 else 0,
            )

            # Update production and utilization
            fg.set_allocated_volumes(new_production)
            if fg.capacity > 0:
                fg.utilization_rate = new_production / fg.capacity

            # Scale outgoing graph edges (steel to demand centers) so downstream
            # demand-satisfaction accounting reflects reduced supply.
            if self.G is not None and fg_id in self.G.nodes:
                for _, dest, edge_data in list(self.G.out_edges(fg_id, data=True)):
                    old_vol = edge_data.get("volume", 0.0)
                    edge_data["volume"] = old_vol * scale_production
                    old_alloc = edge_data.get("allocations", 0.0)
                    if old_alloc:
                        edge_data["allocations"] = old_alloc * scale_production

            # Update BOM demands and costs
            for comm, info in materials.items():
                comm_lower = comm.lower()
                if comm_lower in constrained_equiv:
                    # Constrained commodity: demand fixed, but unit_cost rises (less output)
                    scale = 1.0
                elif comm_lower in METALLIC_COMMODITIES:
                    # Other metallics: reduce to maintain valid share
                    scale = scale_other
                else:
                    # Non-metallic inputs (iron ore, flux, gases): scale with production
                    scale = scale_production

                info["demand"] = float(info.get("demand", 0.0)) * scale
                for total_key, unit_key in (
                    ("total_cost", "unit_cost"),
                    ("total_material_cost", "unit_material_cost"),
                ):
                    if total_key in info:
                        info[total_key] = float(info[total_key]) * scale
                        if new_production > 0:
                            info[unit_key] = info[total_key] / new_production

                info["product_volume"] = new_production

            # The metallic mix changed non-uniformly, so the share weights and the
            # per-pathway energy legs must be recomputed from the corrected demands —
            # a single scale factor would misstate both.
            _ensure_material_shares(materials)

            energy = bom.get("energy", {})
            if energy:
                req_by_charge = _required_quantity_by_charge(fg)
                intensity_by_charge = _energy_intensity_by_charge(fg)
                vopex_by_charge = {
                    str(charge).lower(): {normalize_name(c): float(v) for c, v in (carriers or {}).items()}
                    for charge, carriers in (getattr(fg, "energy_vopex_breakdown_by_input", {}) or {}).items()
                    if isinstance(charge, str)
                }
                for carrier, entry in energy.items():
                    carrier_demand = 0.0
                    carrier_cost = 0.0
                    for charge, info in materials.items():
                        req = req_by_charge.get(charge.lower())
                        if not req:
                            continue
                        product_tonnes = float(info.get("demand", 0.0)) / req
                        carrier_demand += product_tonnes * intensity_by_charge.get(charge.lower(), {}).get(carrier, 0.0)
                        carrier_cost += product_tonnes * vopex_by_charge.get(charge.lower(), {}).get(carrier, 0.0)
                    entry["demand"] = carrier_demand
                    entry["total_cost"] = carrier_cost
                    entry["unit_cost"] = carrier_cost / new_production if new_production > 0 else 0.0
                    entry["product_volume"] = new_production

            corrected += 1

        if corrected:
            logger.info(
                "[UTIL-CORRECT] Corrected utilization for %d FG(s) due to constrained supply",
                corrected,
            )
        return corrected

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
