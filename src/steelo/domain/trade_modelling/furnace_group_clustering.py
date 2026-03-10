"""Furnace group clustering logic for trade LP optimization.

This module provides functionality to cluster furnace groups into meta-furnace groups
based on technology, reductant choice, and country location. This reduces LP complexity
while preserving essential spatial and technological characteristics.

Key Features:
- Clusters furnace groups by technology, chosen reductant, and country (iso3)
- Calculates capacity-weighted center of gravity for each cluster
- Preserves original locations for accurate disaggregation
- Supports smart disaggregation with distance and technology constraints
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
import logging
import networkx as nx

from steelo.domain.models import Location, Plant, FurnaceGroup, PrimaryFeedstock, Volumes
from steelo.domain.constants import T_TO_KT
from steelo.utilities.utils import normalize_name
from steelo.adapters.geospatial.geospatial_toolbox import haversine_distance

if TYPE_CHECKING:
    from steelo.simulation import SimulationConfig

logger = logging.getLogger(__name__)


def _create_feedstock_signature(fg: FurnaceGroup) -> str:
    """Create a hashable signature from a furnace group's effective feedstocks.

    Args:
        fg: FurnaceGroup to extract feedstocks from

    Returns:
        A string signature representing the feedstock configuration
    """
    if not hasattr(fg, "effective_primary_feedstocks"):
        return "NONE"

    feedstocks = fg.effective_primary_feedstocks
    if not feedstocks or len(feedstocks) == 0:
        return "NONE"

    # Create a sorted, deterministic signature from feedstock attributes
    # Using reductant and metallic_charge as the key differentiators
    feedstock_keys = []
    for fs in feedstocks:
        # Create a key from reductant and metallic_charge (the main differentiators)
        key = f"{fs.reductant}:{fs.metallic_charge}"
        feedstock_keys.append(key)

    # Sort for deterministic ordering
    feedstock_keys.sort()

    # Join into single signature
    return "|".join(feedstock_keys)


@dataclass(frozen=True)
class ClusterKey:
    """Key for grouping furnace groups into clusters.

    Furnace groups with identical keys will be aggregated into a single meta-furnace group.

    Attributes:
        technology_name: Technology type (e.g., "BF", "DRI", "EAF")
        iso3: Country ISO3 code (e.g., "CHN", "USA", "DEU")
        feedstock_signature: Hash of effective_primary_feedstocks to ensure compatibility
            (includes reductant information, making chosen_reductant redundant)
    """

    technology_name: str
    iso3: str
    feedstock_signature: str  # Hashable representation of feedstocks

    def __str__(self) -> str:
        # Include feedstock_signature prefix for readability
        fs_prefix = (
            self.feedstock_signature.split(":")[0] if ":" in self.feedstock_signature else self.feedstock_signature
        )
        return f"{self.technology_name}_{fs_prefix}_{self.iso3}"


@dataclass
class MetaFurnaceGroup:
    """Aggregated furnace group representing a cluster of similar furnace groups.

    Meta-furnace groups are used in the trade LP to reduce problem complexity by
    combining furnace groups with identical technology, reductant, and country.
    After LP solving, allocations are disaggregated back to individual furnace groups.

    Attributes:
        cluster_key: The key used to group furnace groups into this cluster
        meta_furnace_group_id: Unique identifier for this meta-furnace group
        constituent_fg_ids: List of original furnace group IDs in this cluster
        technology_name: Technology type of all constituent furnace groups
        chosen_reductant: Reductant choice of all constituent furnace groups
        location: Capacity-weighted center of gravity of all constituent FGs
        total_capacity: Sum of capacities of all constituent furnace groups
        weighted_avg_carbon_cost: Capacity-weighted average carbon cost per unit
        dynamic_business_case: List of primary feedstock options (should be identical across cluster)
        capacity_shares: Mapping from fg_id to its share of total cluster capacity (0-1)
        constituent_locations: Original locations of constituent FGs (for disaggregation)
        weighted_avg_energy_costs: Capacity-weighted average energy costs by metallic charge input
            (e.g., {"hot_metal": 25.5, "pig_iron": 30.2} in USD per tonne of output)

    Example:
        >>> # Three BF furnaces in China using coke, with total capacity 10,000 t
        >>> meta_fg = MetaFurnaceGroup(
        ...     cluster_key=ClusterKey("BF", "coke", "CHN"),
        ...     meta_furnace_group_id="cluster_BF_coke_CHN",
        ...     constituent_fg_ids=["plant1_fg0", "plant2_fg0", "plant3_fg0"],
        ...     technology_name="BF",
        ...     chosen_reductant="coke",
        ...     location=Location(lat=35.0, lon=110.0, iso3="CHN", ...),
        ...     total_capacity=Volumes(10000.0),
        ...     weighted_avg_carbon_cost=85.5,
        ...     capacity_shares={
        ...         "plant1_fg0": 0.3,  # 3000 t capacity
        ...         "plant2_fg0": 0.5,  # 5000 t capacity
        ...         "plant3_fg0": 0.2,  # 2000 t capacity
        ...     },
        ...     weighted_avg_energy_costs={"hot_metal": 28.3, "pig_iron": 31.5},
        ...     ...
        ... )
    """

    cluster_key: ClusterKey
    meta_furnace_group_id: str
    constituent_fg_ids: list[str]
    technology_name: str
    chosen_reductant: str
    location: Location
    total_capacity: Volumes
    weighted_avg_carbon_cost: float
    dynamic_business_case: list[PrimaryFeedstock] | None
    capacity_shares: dict[str, float] = field(default_factory=dict)
    constituent_locations: dict[str, Location] = field(default_factory=dict)
    weighted_avg_energy_costs: dict[str, float] = field(default_factory=dict)

    def __str__(self) -> str:
        return (
            f"MetaFurnaceGroup({self.meta_furnace_group_id}, "
            f"n_constituents={len(self.constituent_fg_ids)}, "
            f"capacity={float(self.total_capacity) * T_TO_KT:.1f} kt)"
        )


def calculate_center_of_gravity(furnace_groups_with_plants: list[tuple[FurnaceGroup, Plant]]) -> Location:
    """Calculate capacity-weighted center of gravity for a cluster of furnace groups.

    Uses weighted average of latitude and longitude, where weights are furnace capacities.
    This provides a representative location for the cluster that reflects the geographic
    distribution of production capacity.

    Args:
        furnace_groups_with_plants: List of (FurnaceGroup, Plant) tuples in the cluster

    Returns:
        Location: New location at the weighted centroid with metadata from first FG

    Notes:
        - For small geographic areas (within same country), simple lat/lon averaging is acceptable
        - For large areas spanning multiple time zones, could use proper spherical geometry
        - Preserves iso3, country, and region from the cluster (should be identical for all FGs)
        - If total capacity is zero, falls back to unweighted average

    Example:
        >>> fg1 = FurnaceGroup(capacity=Volumes(1000.0), ...)
        >>> plant1 = Plant(location=Location(lat=40.0, lon=100.0, ...), ...)
        >>> fg2 = FurnaceGroup(capacity=Volumes(3000.0), ...)
        >>> plant2 = Plant(location=Location(lat=42.0, lon=102.0, ...), ...)
        >>> # Centroid should be closer to plant2 (3x capacity of plant1)
        >>> centroid = calculate_center_of_gravity([(fg1, plant1), (fg2, plant2)])
        >>> # Expected: lat ≈ 41.5, lon ≈ 101.5 (weighted toward plant2)
    """
    if not furnace_groups_with_plants:
        raise ValueError("Cannot calculate center of gravity for empty list of furnace groups")

    total_capacity = sum(float(fg.capacity) for fg, _ in furnace_groups_with_plants)

    if total_capacity == 0:
        # Fall back to simple average if all capacities are zero
        logger.warning(f"Total capacity is zero for cluster, using unweighted average for center of gravity")
        avg_lat = sum(plant.location.lat for _, plant in furnace_groups_with_plants) / len(furnace_groups_with_plants)
        avg_lon = sum(plant.location.lon for _, plant in furnace_groups_with_plants) / len(furnace_groups_with_plants)
    else:
        # Capacity-weighted average
        avg_lat = (
            sum(plant.location.lat * float(fg.capacity) for fg, plant in furnace_groups_with_plants) / total_capacity
        )

        avg_lon = (
            sum(plant.location.lon * float(fg.capacity) for fg, plant in furnace_groups_with_plants) / total_capacity
        )

    # Use the first furnace group's location metadata (iso3, country, region)
    # These should be identical for all FGs in the cluster since we cluster by iso3
    reference_location = furnace_groups_with_plants[0][1].location

    return Location(
        lat=avg_lat,
        lon=avg_lon,
        iso3=reference_location.iso3,
        country=reference_location.country,
        region=reference_location.region,
        distance_to_other_iso3=None,  # Will be recalculated if needed by LP
    )


def cluster_furnace_groups(
    plants: list[Plant], config: "SimulationConfig"
) -> tuple[list[MetaFurnaceGroup], dict[str, list[str]]]:
    """Cluster furnace groups by technology, chosen reductant, and country.

    Groups furnace groups with identical technology, reductant, and country into
    meta-furnace groups. Each meta-furnace group represents the aggregated capacity
    and characteristics of all constituent furnace groups.

    Args:
        plants: List of all plants in the simulation
        config: Simulation configuration containing active_statuses

    Returns:
        Tuple of:
            - List of MetaFurnaceGroup objects (one per unique cluster)
            - Mapping from meta_furnace_group_id to list of constituent fg_ids

    Notes:
        - Only includes furnace groups with status in config.active_statuses
        - Furnace groups with missing chosen_reductant are assigned "unknown"
        - All FGs in a cluster should have identical dynamic_business_case structure
        - Capacity shares sum to 1.0 for each cluster
        - Single-FG clusters are still created for consistency

    Example:
        >>> plants = [plant1, plant2, plant3]
        >>> config = SimulationConfig(active_statuses=["operating"])
        >>> meta_fgs, mapping = cluster_furnace_groups(plants, config)
        >>> # If plant1 and plant2 have identical BF-coke-CHN, plant3 has DRI-gas-CHN:
        >>> len(meta_fgs)  # 2 meta-furnace groups
        >>> mapping["cluster_BF_coke_CHN"]  # ["plant1_fg0", "plant2_fg0"]
        >>> mapping["cluster_DRI_natural_gas_CHN"]  # ["plant3_fg0"]
    """
    logger.info("[CLUSTERING] Starting furnace group clustering...")

    # Step 1: Collect all active furnace groups with their plants
    active_fgs: list[tuple[FurnaceGroup, Plant]] = []
    for plant in plants:
        for fg in plant.furnace_groups:
            if fg.status.lower() in config.active_statuses:
                active_fgs.append((fg, plant))

    logger.info(f"[CLUSTERING] Found {len(active_fgs)} active furnace groups to cluster")

    # Step 2: Group by cluster key (including feedstock configuration)
    clusters: dict[ClusterKey, list[tuple[FurnaceGroup, Plant]]] = {}
    for fg, plant in active_fgs:
        # Extract clustering attributes
        technology_name = fg.technology.name
        iso3 = plant.location.iso3
        feedstock_signature = _create_feedstock_signature(fg)

        cluster_key = ClusterKey(
            technology_name=technology_name,
            iso3=iso3,
            feedstock_signature=feedstock_signature,
        )

        if cluster_key not in clusters:
            clusters[cluster_key] = []
        clusters[cluster_key].append((fg, plant))

    logger.info(f"[CLUSTERING] Created {len(clusters)} unique clusters")

    # Log info about feedstock-based splitting
    fgs_with_no_feedstocks = sum(1 for key in clusters.keys() if key.feedstock_signature == "NONE")
    if fgs_with_no_feedstocks > 0:
        logger.warning(
            f"[CLUSTERING] {fgs_with_no_feedstocks} clusters have FGs with no effective feedstocks - "
            f"these may fail during BOM population"
        )

    # Step 3: Create meta-furnace groups for each cluster
    meta_furnace_groups: list[MetaFurnaceGroup] = []
    cluster_mapping: dict[str, list[str]] = {}

    for cluster_key, cluster_fgs in clusters.items():
        # Generate unique ID for this meta-furnace group
        meta_fg_id = f"cluster_{cluster_key}"

        # Extract constituent FG IDs
        constituent_fg_ids = [fg.furnace_group_id for fg, _ in cluster_fgs]

        # Calculate total capacity
        total_capacity = Volumes(sum(float(fg.capacity) for fg, _ in cluster_fgs))

        # Calculate capacity shares
        if float(total_capacity) > 0:
            capacity_shares = {fg.furnace_group_id: float(fg.capacity) / float(total_capacity) for fg, _ in cluster_fgs}
        else:
            # Equal shares if all have zero capacity
            capacity_shares = {fg.furnace_group_id: 1.0 / len(cluster_fgs) for fg, _ in cluster_fgs}

        # Calculate weighted average carbon cost
        if float(total_capacity) > 0:
            weighted_avg_carbon_cost = sum(
                fg.carbon_cost_per_unit * float(fg.capacity) for fg, _ in cluster_fgs
            ) / float(total_capacity)
        else:
            # Simple average if all have zero capacity
            weighted_avg_carbon_cost = sum(fg.carbon_cost_per_unit for fg, _ in cluster_fgs) / len(cluster_fgs)

        # Store constituent locations for disaggregation
        constituent_locations = {fg.furnace_group_id: plant.location for fg, plant in cluster_fgs}

        # Calculate capacity-weighted center of gravity
        location = calculate_center_of_gravity(cluster_fgs)

        # Get dynamic business case (should be identical across cluster)
        # Use the first FG's business case
        dynamic_business_case = cluster_fgs[0][0].technology.dynamic_business_case

        logger.debug(
            f"[CLUSTERING] Cluster {meta_fg_id}: dynamic_business_case from first FG = "
            f"{dynamic_business_case is not None and len(dynamic_business_case) if dynamic_business_case else 0} feedstocks"
        )

        # Validate that all FGs in cluster have compatible business cases
        for i, (fg, _) in enumerate(cluster_fgs[1:], start=1):
            if fg.technology.dynamic_business_case != dynamic_business_case:
                logger.warning(
                    f"[CLUSTERING] Furnace group {fg.furnace_group_id} has different "
                    f"dynamic_business_case than others in cluster {meta_fg_id}. "
                    f"Using first FG's business case."
                )

        # Calculate capacity-weighted average energy costs by metallic charge input
        # Collect all unique metallic charges across the cluster
        all_metallic_charges: set[str] = set()
        for fg, _ in cluster_fgs:
            if hasattr(fg, "energy_vopex_by_input") and fg.energy_vopex_by_input:
                all_metallic_charges.update(fg.energy_vopex_by_input.keys())

        weighted_avg_energy_costs: dict[str, float] = {}
        if float(total_capacity) > 0:
            # For each metallic charge, calculate capacity-weighted average
            for metallic_charge in all_metallic_charges:
                total_weighted_cost = 0.0
                total_weight = 0.0

                for fg, _ in cluster_fgs:
                    if hasattr(fg, "energy_vopex_by_input") and metallic_charge in fg.energy_vopex_by_input:
                        cost = fg.energy_vopex_by_input[metallic_charge]
                        weight = float(fg.capacity)
                        total_weighted_cost += cost * weight
                        total_weight += weight

                if total_weight > 0:
                    weighted_avg_energy_costs[metallic_charge] = total_weighted_cost / total_weight
                else:
                    # No FGs in cluster have this metallic charge with non-zero capacity
                    weighted_avg_energy_costs[metallic_charge] = 0.0
        else:
            # Zero capacity: use simple average
            for metallic_charge in all_metallic_charges:
                costs = [
                    fg.energy_vopex_by_input[metallic_charge]
                    for fg, _ in cluster_fgs
                    if hasattr(fg, "energy_vopex_by_input") and metallic_charge in fg.energy_vopex_by_input
                ]
                if costs:
                    weighted_avg_energy_costs[metallic_charge] = sum(costs) / len(costs)
                else:
                    weighted_avg_energy_costs[metallic_charge] = 0.0

        # Get chosen_reductant from first FG (should be identical across cluster)
        chosen_reductant = normalize_name(getattr(cluster_fgs[0][0], "chosen_reductant", "") or "unknown")

        # Create meta-furnace group
        meta_fg = MetaFurnaceGroup(
            cluster_key=cluster_key,
            meta_furnace_group_id=meta_fg_id,
            constituent_fg_ids=constituent_fg_ids,
            technology_name=cluster_key.technology_name,
            chosen_reductant=chosen_reductant,
            location=location,
            total_capacity=total_capacity,
            weighted_avg_carbon_cost=weighted_avg_carbon_cost,
            dynamic_business_case=dynamic_business_case,
            capacity_shares=capacity_shares,
            constituent_locations=constituent_locations,
            weighted_avg_energy_costs=weighted_avg_energy_costs,
        )

        meta_furnace_groups.append(meta_fg)
        cluster_mapping[meta_fg_id] = constituent_fg_ids

        logger.debug(
            f"[CLUSTERING] Created {meta_fg_id}: "
            f"{len(constituent_fg_ids)} FGs, "
            f"{float(total_capacity) * T_TO_KT:.1f} kt capacity, "
            f"centroid at ({location.lat:.2f}, {location.lon:.2f})"
        )

    # Log clustering statistics
    n_original = len(active_fgs)
    n_clustered = len(meta_furnace_groups)
    reduction_pct = (1 - n_clustered / n_original) * 100 if n_original > 0 else 0
    avg_cluster_size = n_original / n_clustered if n_clustered > 0 else 0
    max_cluster_size = max(len(fgs) for fgs in cluster_mapping.values()) if cluster_mapping else 0

    logger.info(f"[CLUSTERING] Statistics:")
    logger.info(f"  Original FGs: {n_original}")
    logger.info(f"  Meta-FGs: {n_clustered}")
    logger.info(f"  Reduction: {reduction_pct:.1f}%")
    logger.info(f"  Avg cluster size: {avg_cluster_size:.1f}")
    logger.info(f"  Largest cluster: {max_cluster_size} FGs")

    return meta_furnace_groups, cluster_mapping


def _calculate_distance_km(loc1: Location, loc2: Location) -> float:
    """Calculate haversine distance between two locations in kilometers.

    Args:
        loc1: First location with lat and lon attributes
        loc2: Second location with lat and lon attributes

    Returns:
        Distance in kilometers
    """
    return haversine_distance([loc1.lat, loc1.lon, loc2.lat, loc2.lon])


def _is_flow_feasible(commodity, distance_km: float, config: "SimulationConfig") -> bool:
    """Check if a commodity flow is feasible given the distance constraint.

    Hot/cold metal pairings:
    - hot_metal (close) ↔ pig_iron (distant)
    - dri_* (close) ↔ hbi_* (distant)
    - liquid_iron (close) ↔ electrolytic_iron (distant)

    Args:
        commodity: The commodity being transported (Commodity object or string)
        distance_km: Distance between source and destination in kilometers
        config: Simulation configuration with hot_metal_radius and product lists

    Returns:
        True if the flow is feasible, False if it violates distance constraints
    """
    # Convert Commodity object to string if needed
    commodity_name = str(commodity) if hasattr(commodity, "__str__") else commodity

    # Skip distance checks if config doesn't have product lists
    if not hasattr(config, "closely_allocated_products") or not hasattr(config, "distantly_allocated_products"):
        return True

    is_close = distance_km <= config.hot_metal_radius

    # Closely allocated products can only travel short distances
    if commodity_name in config.closely_allocated_products:
        return is_close

    # Distantly allocated products can only travel long distances
    elif commodity_name in config.distantly_allocated_products:
        return not is_close

    # All other commodities have no distance restrictions
    else:
        return True


def _solve_batched_transportation_problem(
    source_supplies: dict[str, float],  # {source_id: supply_volume}
    dest_demands: dict[str, float],  # {dest_id: demand_volume}
    source_locations: dict[str, Location],  # {source_id: Location}
    dest_locations: dict[str, Location],  # {dest_id: Location}
    commodity,
    config: "SimulationConfig",
) -> tuple[dict[tuple[str, str], float], dict]:
    """Solve batched transportation problem for multiple sources and destinations.

    This is the general solver that handles:
    - Many suppliers → Many FGs (Case 3 batched)
    - Many FGs → Many suppliers (Case 2 batched)
    - Many FGs → Many FGs (Case 4, inter-cluster)

    Args:
        source_supplies: Dictionary mapping source IDs to their supply volumes
        dest_demands: Dictionary mapping destination IDs to their demand volumes
        source_locations: Dictionary mapping source IDs to their locations
        dest_locations: Dictionary mapping destination IDs to their locations
        commodity: Commodity being transported
        config: Simulation configuration

    Returns:
        Tuple of (flow_dict, stats_dict) where:
        - flow_dict: {(source_id, dest_id): volume} for non-zero flows only
        - stats_dict: Statistics about reduction (total_pairs, used_edges, etc.)
    """
    # Build bipartite graph for transportation problem
    G = nx.DiGraph()

    # Add source and sink nodes for flow balancing
    SOURCE = "__source__"
    SINK = "__sink__"

    # Track statistics
    total_pairs = len(source_supplies) * len(dest_demands)
    infeasible_pairs = 0

    # Very high cost for infeasible flows (essentially infinity)
    INFEASIBLE_COST = 1e9

    # Calculate total volume
    total_supply = sum(source_supplies.values())
    total_demand = sum(dest_demands.values())

    # Sanity check
    if abs(total_supply - total_demand) > 1e-6:
        logger.warning(f"[DISAGGREGATION] Supply/demand mismatch: {total_supply:.2f} vs {total_demand:.2f}")

    # Add source node with supply (negative demand)
    G.add_node(SOURCE, demand=-total_supply)

    # Add sink node with demand (positive demand)
    G.add_node(SINK, demand=total_demand)

    # Add edges from source to each origin
    for source_id, supply in source_supplies.items():
        source_node = f"from_{source_id}"
        G.add_node(source_node, demand=0)  # Intermediate nodes have zero demand
        G.add_edge(SOURCE, source_node, weight=0, capacity=supply)

    # Add edges from each destination to sink
    for dest_id, demand in dest_demands.items():
        dest_node = f"to_{dest_id}"
        G.add_node(dest_node, demand=0)  # Intermediate nodes have zero demand
        G.add_edge(dest_node, SINK, weight=0, capacity=demand)

    # Add edges between origins and destinations with distance-based costs
    for source_id, supply in source_supplies.items():
        source_location = source_locations.get(source_id)

        for dest_id, demand in dest_demands.items():
            dest_location = dest_locations.get(dest_id)

            # Calculate distance (if both locations available)
            if source_location and dest_location:
                distance_km = _calculate_distance_km(source_location, dest_location)
                is_feasible = _is_flow_feasible(commodity, distance_km, config)
            else:
                # No location data, assume feasible with zero cost
                distance_km = 0
                is_feasible = True

            if is_feasible:
                # Use distance as cost (prefer shorter distances)
                cost = int(distance_km * 100)  # Scale for integer arithmetic
            else:
                # Infeasible flow: use very high cost (but allow as last resort)
                cost = INFEASIBLE_COST
                infeasible_pairs += 1

            # Add edge with capacity = total supply (effectively unbounded)
            max_flow = total_supply
            G.add_edge(f"from_{source_id}", f"to_{dest_id}", weight=cost, capacity=max_flow)

    # Solve min-cost flow (demands are set as node attributes)
    try:
        flow_dict = nx.min_cost_flow(G)
    except nx.NetworkXUnfeasible:
        logger.error(f"[DISAGGREGATION] Batched transportation problem infeasible for {commodity}")
        # Fallback: return empty flows
        return {}, {"total_pairs": total_pairs, "used_edges": 0, "infeasible_pairs": infeasible_pairs}

    # Extract non-zero flows (ignore source/sink flows)
    result_flows = {}
    used_edges = 0

    for from_node, destinations in flow_dict.items():
        if from_node.startswith("from_"):
            source_id = from_node[5:]  # Remove "from_" prefix

            for to_node, flow_value in destinations.items():
                if to_node.startswith("to_") and flow_value > 1e-6:  # Ignore tiny numerical errors
                    dest_id = to_node[3:]  # Remove "to_" prefix
                    result_flows[(source_id, dest_id)] = flow_value
                    used_edges += 1

    # Prepare statistics
    stats = {
        "total_pairs": total_pairs,
        "used_edges": used_edges,
        "infeasible_pairs": infeasible_pairs,
        "reduction_pct": (1 - used_edges / total_pairs) * 100 if total_pairs > 0 else 0,
    }

    return result_flows, stats


def _validate_fg_can_receive_allocation(fg_id: str, plants_repo: "PlantInMemoryRepository") -> bool:
    """Check if a furnace group should receive allocations (minimal validation).

    NOTE: We do NOT check BOMs here because BOMs get populated AFTER allocations
    based on the feedstock rules. Filtering based on BOMs would be backwards.

    Args:
        fg_id: Furnace group ID (format: "plant_id_fg_id")
        plants_repo: Repository to look up the actual FG object

    Returns:
        True if FG should receive allocations (almost always True)
    """
    if not plants_repo:
        return True

    try:
        # Parse FG ID to get plant and FG
        parts = fg_id.rsplit("_", 1)
        if len(parts) != 2:
            return True

        plant_id, fg_index = parts
        plant = plants_repo.get(plant_id)
        if not plant:
            return True

        # Find the FG by index
        fg_index_int = int(fg_index)
        if fg_index_int >= len(plant.furnace_groups):
            return True

        fg = plant.furnace_groups[fg_index_int]

        # Only filter FGs with near-zero capacity (clearly unusable)
        if fg.capacity is not None and fg.capacity < 0.1:  # Less than 0.1 kt/year
            logger.debug(f"[DISAGGREGATION] Filtering FG {fg_id} with near-zero capacity: {fg.capacity}")
            return False

        # Don't filter based on BOM or feedstocks - those get populated later!
        return True

    except Exception as e:
        logger.debug(f"[DISAGGREGATION] Error validating FG {fg_id}: {e}")
        return True  # Assume valid on error


def _solve_transportation_problem(
    from_meta_fg: MetaFurnaceGroup,
    to_meta_fg: MetaFurnaceGroup,
    total_volume: float,
    commodity,
    config: "SimulationConfig",
    plants_repo: "PlantInMemoryRepository" = None,
) -> tuple[dict[tuple[str, str], float], dict]:
    """Solve transportation problem for meta-FG → meta-FG (Case 4).

    Wrapper around _solve_batched_transportation_problem for inter-cluster flows.

    Args:
        from_meta_fg: Source meta-furnace group
        to_meta_fg: Destination meta-furnace group
        total_volume: Total volume to allocate
        commodity: Commodity being transported
        config: Simulation configuration
        plants_repo: Repository for validating FG BOMs

    Returns:
        Tuple of (flow_dict, stats_dict)
    """
    # Filter out FGs without valid BOMs from destination
    valid_dest_fgs = {}
    total_dest_share = 0.0

    for fg_id, share in to_meta_fg.capacity_shares.items():
        if _validate_fg_can_receive_allocation(fg_id, plants_repo):
            valid_dest_fgs[fg_id] = share
            total_dest_share += share

    if not valid_dest_fgs:
        logger.error(f"[DISAGGREGATION] No valid destination FGs with BOMs in {to_meta_fg.meta_furnace_group_id}")
        return {}, {"total_pairs": 0, "used_edges": 0, "infeasible_pairs": 0}

    # Renormalize shares if some FGs were filtered out
    if total_dest_share < 0.99:  # Some FGs were excluded
        logger.warning(
            f"[DISAGGREGATION] Filtered out {len(to_meta_fg.capacity_shares) - len(valid_dest_fgs)} FGs "
            f"without BOMs from {to_meta_fg.meta_furnace_group_id}"
        )
        valid_dest_fgs = {fg_id: share / total_dest_share for fg_id, share in valid_dest_fgs.items()}

    # Prepare supplies (sources) from from_meta_fg
    source_supplies = {fg_id: total_volume * share for fg_id, share in from_meta_fg.capacity_shares.items()}

    # Prepare demands (destinations) from to_meta_fg (only valid FGs)
    dest_demands = {fg_id: total_volume * share for fg_id, share in valid_dest_fgs.items()}

    # Use the batched solver
    return _solve_batched_transportation_problem(
        source_supplies=source_supplies,
        dest_demands=dest_demands,
        source_locations=from_meta_fg.constituent_locations,
        dest_locations={fg_id: to_meta_fg.constituent_locations[fg_id] for fg_id in valid_dest_fgs},
        commodity=commodity,
        config=config,
    )


def disaggregate_allocations(
    clustered_allocations: "Allocations",
    meta_furnace_groups: list[MetaFurnaceGroup],
    plants_repo: "PlantInMemoryRepository",
    config: "SimulationConfig",
) -> "Allocations":
    """Disaggregate allocations from meta-furnace groups to individual furnace groups.

    Takes LP allocation results where ProcessCenters represent clustered meta-furnace groups
    and converts them to allocations for individual furnace groups. This is necessary for
    the TM-PAM connector to correctly update utilization rates and costs for each actual
    furnace group.

    Args:
        clustered_allocations: Allocations object with meta-FG ProcessCenters
        meta_furnace_groups: List of MetaFurnaceGroup objects used in clustering
        plants_repo: Repository for looking up plant/FG locations
        config: Simulation configuration with distance constraints

    Returns:
        Allocations: New allocations object with individual FG ProcessCenters

    Notes:
        - Uses capacity shares for proportional disaggregation
        - Respects hot_metal distance constraints using original FG locations
        - Creates new ProcessCenter objects with individual fg_ids as names
        - Preserves total volumes (within LP tolerance)

    Example:
        >>> # After solving LP with meta-furnace groups
        >>> clustered_allocs = trade_lp.allocations
        >>> disaggregated_allocs = disaggregate_allocations(
        ...     clustered_allocations=clustered_allocs,
        ...     meta_furnace_groups=meta_fgs,
        ...     plants_repo=bus.uow.plants,
        ...     config=bus.env.config
        ... )
        >>> # Pass disaggregated allocations to TM_PAM_connector
        >>> tmpc.set_up_network_and_propagate_costs(solved_trade_allocations=disaggregated_allocs)
    """
    from steelo.domain.trade_modelling.trade_lp_modelling import Allocations, ProcessCenter
    from steelo.adapters.repositories.in_memory_repository import PlantInMemoryRepository

    logger.info(f"[DISAGGREGATION] Starting allocation disaggregation...")
    logger.info(f"[DISAGGREGATION] Input allocations: {len(clustered_allocations.allocations)} flows")

    # Build lookup dicts
    meta_fg_by_id: dict[str, MetaFurnaceGroup] = {mfg.meta_furnace_group_id: mfg for mfg in meta_furnace_groups}

    # Build ProcessCenter lookup by name for non-meta-FG centers (suppliers, demand)
    pc_by_name: dict[str, "ProcessCenter"] = {}
    for (from_pc, to_pc, commodity), volume in clustered_allocations.allocations.items():
        if from_pc.name not in meta_fg_by_id:
            pc_by_name[from_pc.name] = from_pc
        if to_pc.name not in meta_fg_by_id:
            pc_by_name[to_pc.name] = to_pc

    # Create new allocations dict
    disaggregated_allocs: dict = {}

    # Track transportation problem statistics
    transportation_stats: dict = {}
    total_potential_edges = 0
    total_used_edges = 0

    # Helper function to create ProcessCenter for individual FG
    def create_fg_process_center(
        fg_id: str, meta_fg: MetaFurnaceGroup, volume_share: float, process: "Process"
    ) -> ProcessCenter:
        """Create a ProcessCenter for an individual furnace group."""
        return ProcessCenter(
            name=fg_id,
            process=process,  # Use the same process as the meta-FG
            capacity=float(meta_fg.total_capacity) * volume_share,
            location=meta_fg.constituent_locations[fg_id],
            production_cost=meta_fg.weighted_avg_carbon_cost,
            soft_minimum_capacity=None,
        )

    # PASS 1: Group allocations by type for batching
    case1_allocs = []  # Neither is meta-FG (passthrough)
    case2_batches = {}  # Meta-FG → Supplier: group by (from_meta_fg, commodity)
    case3_batches = {}  # Supplier → Meta-FG: group by (to_meta_fg, commodity)
    case4_allocs = []  # Meta-FG → Meta-FG (individual transportation problems)

    for (from_pc, to_pc, commodity), volume in clustered_allocations.allocations.items():
        from_is_meta = from_pc.name in meta_fg_by_id
        to_is_meta = to_pc.name in meta_fg_by_id

        if not from_is_meta and not to_is_meta:
            # Case 1: Neither is meta-FG
            case1_allocs.append((from_pc, to_pc, commodity, volume))

        elif from_is_meta and not to_is_meta:
            # Case 2: Meta-FG → Supplier (batch by source cluster + commodity)
            batch_key = (from_pc.name, str(commodity))
            if batch_key not in case2_batches:
                case2_batches[batch_key] = []
            case2_batches[batch_key].append((from_pc, to_pc, commodity, volume))

        elif not from_is_meta and to_is_meta:
            # Case 3: Supplier → Meta-FG (batch by destination cluster + commodity)
            batch_key = (to_pc.name, str(commodity))
            if batch_key not in case3_batches:
                case3_batches[batch_key] = []
            case3_batches[batch_key].append((from_pc, to_pc, commodity, volume))

        else:
            # Case 4: Meta-FG → Meta-FG
            case4_allocs.append((from_pc, to_pc, commodity, volume))

    logger.info(
        f"[DISAGGREGATION] Grouped allocations: "
        f"Case1={len(case1_allocs)}, Case2={len(case2_batches)} batches, "
        f"Case3={len(case3_batches)} batches, Case4={len(case4_allocs)}"
    )

    # PASS 2: Process each case

    # Case 1: Passthrough (no disaggregation needed)
    for from_pc, to_pc, commodity, volume in case1_allocs:
        disaggregated_allocs[(from_pc, to_pc, commodity)] = volume

    # Case 2: Meta-FG → Suppliers (batched transportation problem)
    for (from_meta_name, commodity_str), batch in case2_batches.items():
        from_meta_fg = meta_fg_by_id[from_meta_name]

        # Collect all suppliers and their demands
        supplier_demands = {}
        supplier_pcs = {}
        total_batch_volume = 0

        for from_pc, to_pc, commodity, volume in batch:
            supplier_demands[to_pc.name] = supplier_demands.get(to_pc.name, 0) + volume
            supplier_pcs[to_pc.name] = to_pc
            total_batch_volume += volume

        # Prepare supplies from FGs in meta-cluster
        fg_supplies = {fg_id: total_batch_volume * share for fg_id, share in from_meta_fg.capacity_shares.items()}

        # Solve batched transportation problem
        flows, stats = _solve_batched_transportation_problem(
            source_supplies=fg_supplies,
            dest_demands=supplier_demands,
            source_locations=from_meta_fg.constituent_locations,
            dest_locations={name: pc.location for name, pc in supplier_pcs.items()},
            commodity=batch[0][2],  # Get commodity from first item
            config=config,
        )

        # Track stats
        total_potential_edges += stats["total_pairs"]
        total_used_edges += stats["used_edges"]
        transportation_stats[(from_meta_name, "suppliers", commodity_str)] = stats

        # Create allocations
        for (fg_id, supplier_name), flow_volume in flows.items():
            fg_pc = create_fg_process_center(
                fg_id,
                from_meta_fg,
                from_meta_fg.capacity_shares[fg_id],
                batch[0][0].process,  # Get process from first from_pc
            )
            supplier_pc = supplier_pcs[supplier_name]
            disaggregated_allocs[(fg_pc, supplier_pc, batch[0][2])] = flow_volume

    # Case 3: Suppliers → Meta-FG (batched transportation problem)
    for (to_meta_name, commodity_str), batch in case3_batches.items():
        to_meta_fg = meta_fg_by_id[to_meta_name]

        # Collect all suppliers and their supplies
        supplier_supplies = {}
        supplier_pcs = {}
        total_batch_volume = 0

        for from_pc, to_pc, commodity, volume in batch:
            supplier_supplies[from_pc.name] = supplier_supplies.get(from_pc.name, 0) + volume
            supplier_pcs[from_pc.name] = from_pc
            total_batch_volume += volume

        # Filter out FGs without valid BOMs from destination
        valid_fg_shares = {}
        total_valid_share = 0.0

        for fg_id, share in to_meta_fg.capacity_shares.items():
            if _validate_fg_can_receive_allocation(fg_id, plants_repo):
                valid_fg_shares[fg_id] = share
                total_valid_share += share

        if not valid_fg_shares:
            logger.error(f"[DISAGGREGATION] Case 3: No valid FGs with BOMs in {to_meta_name}, skipping batch")
            continue

        # Renormalize shares if some FGs were filtered out
        if total_valid_share < 0.99:
            logger.warning(
                f"[DISAGGREGATION] Case 3: Filtered out {len(to_meta_fg.capacity_shares) - len(valid_fg_shares)} "
                f"FGs without BOMs from {to_meta_name}"
            )
            valid_fg_shares = {fg_id: share / total_valid_share for fg_id, share in valid_fg_shares.items()}

        # Prepare demands for FGs in meta-cluster (only valid ones)
        fg_demands = {fg_id: total_batch_volume * share for fg_id, share in valid_fg_shares.items()}

        # Solve batched transportation problem
        flows, stats = _solve_batched_transportation_problem(
            source_supplies=supplier_supplies,
            dest_demands=fg_demands,
            source_locations={name: pc.location for name, pc in supplier_pcs.items()},
            dest_locations={fg_id: to_meta_fg.constituent_locations[fg_id] for fg_id in valid_fg_shares},
            commodity=batch[0][2],  # Get commodity from first item
            config=config,
        )

        # Track stats
        total_potential_edges += stats["total_pairs"]
        total_used_edges += stats["used_edges"]
        transportation_stats[("suppliers", to_meta_name, commodity_str)] = stats

        # Create allocations
        for (supplier_name, fg_id), flow_volume in flows.items():
            supplier_pc = supplier_pcs[supplier_name]
            fg_pc = create_fg_process_center(
                fg_id,
                to_meta_fg,
                to_meta_fg.capacity_shares[fg_id],
                batch[0][1].process,  # Get process from first to_pc
            )
            disaggregated_allocs[(supplier_pc, fg_pc, batch[0][2])] = flow_volume

    # Case 4: Meta-FG → Meta-FG (individual transportation problems)
    for from_pc, to_pc, commodity, volume in case4_allocs:
        from_meta_fg = meta_fg_by_id[from_pc.name]
        to_meta_fg = meta_fg_by_id[to_pc.name]

        # Solve transportation problem using min-cost flow
        flows, stats = _solve_transportation_problem(
            from_meta_fg=from_meta_fg,
            to_meta_fg=to_meta_fg,
            total_volume=volume,
            commodity=commodity,
            config=config,
            plants_repo=plants_repo,
        )

        # Track statistics
        total_potential_edges += stats["total_pairs"]
        total_used_edges += stats["used_edges"]
        transportation_stats[(from_pc.name, to_pc.name, str(commodity))] = stats

        # Create allocations for non-zero flows only
        for (from_fg_id, to_fg_id), flow_volume in flows.items():
            # Create ProcessCenters for this pair
            from_fg_pc = create_fg_process_center(
                from_fg_id, from_meta_fg, from_meta_fg.capacity_shares[from_fg_id], from_pc.process
            )
            to_fg_pc = create_fg_process_center(
                to_fg_id, to_meta_fg, to_meta_fg.capacity_shares[to_fg_id], to_pc.process
            )

            disaggregated_allocs[(from_fg_pc, to_fg_pc, commodity)] = flow_volume

    # Create new Allocations object
    result = Allocations(
        allocations=disaggregated_allocs,
        allocation_costs=None,  # Costs will be recalculated by TM_PAM_connector
    )

    logger.info(f"[DISAGGREGATION] Output allocations: {len(result.allocations)} flows")

    # Log transportation problem statistics (edge reduction)
    if transportation_stats:
        overall_reduction = (1 - total_used_edges / total_potential_edges) * 100 if total_potential_edges > 0 else 0
        logger.info(f"[DISAGGREGATION] === Transportation Problem Summary ===")
        logger.info(
            f"[DISAGGREGATION] Edge reduction: {total_potential_edges} → {total_used_edges} "
            f"({overall_reduction:.1f}% reduction)"
        )
        logger.info(f"[DISAGGREGATION] Inter-cluster flows solved: {len(transportation_stats)}")

        # Aggregate by commodity
        commodity_stats: dict[str, dict] = {}
        for (from_name, to_name, commodity_str), stats in transportation_stats.items():
            if commodity_str not in commodity_stats:
                commodity_stats[commodity_str] = {
                    "total_flows": 0,
                    "total_pairs": 0,
                    "total_used_edges": 0,
                    "total_infeasible": 0,
                }
            commodity_stats[commodity_str]["total_flows"] += 1
            commodity_stats[commodity_str]["total_pairs"] += stats["total_pairs"]
            commodity_stats[commodity_str]["total_used_edges"] += stats["used_edges"]
            commodity_stats[commodity_str]["total_infeasible"] += stats["infeasible_pairs"]

        # Log per-commodity statistics
        for commodity_str, stats in sorted(commodity_stats.items()):
            reduction_pct = (
                (1 - stats["total_used_edges"] / stats["total_pairs"]) * 100 if stats["total_pairs"] > 0 else 0
            )
            logger.info(
                f"[DISAGGREGATION]   {commodity_str}: {stats['total_flows']} inter-cluster flows, "
                f"{stats['total_pairs']} potential edges → {stats['total_used_edges']} used "
                f"({reduction_pct:.1f}% reduction)"
            )

    logger.info(f"[DISAGGREGATION] Disaggregation complete")

    return result
