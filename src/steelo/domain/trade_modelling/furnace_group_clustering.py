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
from steelo.utilities.data_processing import normalize_product_name
from steelo.adapters.geospatial.geospatial_toolbox import haversine_distance

if TYPE_CHECKING:
    from steelo.simulation import SimulationConfig
    from steelo.adapters.repositories.in_memory_repository import PlantInMemoryRepository
    from steelo.domain.trade_modelling.trade_lp_modelling import Allocations, Process

logger = logging.getLogger(__name__)

# Commodity substitution mappings: cold (distant) → hot (close)
# These are the same product in different thermal/transport states
COLD_TO_HOT_COMMODITY = {
    "pig_iron": "hot_metal",
    "hbi_low": "dri_low",
    "hbi_mid": "dri_mid",
    "hbi_high": "dri_high",
    "electrolytic_iron": "liquid_iron",
}


def _substitute_commodity_by_distance(
    commodity,
    distance_km: float,
    config: "SimulationConfig",
):
    """Substitute cold commodity with hot version if distance allows.

    LP uses only cold commodities (pig_iron, hbi_*, electrolytic_iron) to avoid
    distance-based infeasibility. During disaggregation, we substitute hot versions
    (hot_metal, dri_*, liquid_iron) when FGs are close enough.

    Args:
        commodity: Original commodity from LP (cold version) - can be Commodity object or string
        distance_km: Distance between source and destination
        config: Config with hot_metal_radius

    Returns:
        Commodity object (hot if close, cold if far) - returns same type as input
    """
    # Import here to avoid circular dependency
    from steelo.domain.trade_modelling.trade_lp_modelling import Commodity

    # Get commodity name as string
    if hasattr(commodity, "name"):
        commodity_name = str(commodity.name).lower()
        is_commodity_object = True
    else:
        commodity_name = str(commodity).lower()
        is_commodity_object = False

    # Check if this is a cold commodity that has a hot equivalent
    if commodity_name in COLD_TO_HOT_COMMODITY:
        # If within hot metal radius, use hot version
        if distance_km <= config.hot_metal_radius:
            hot_name = COLD_TO_HOT_COMMODITY[commodity_name]
            # Return same type as input
            return Commodity(name=hot_name) if is_commodity_object else hot_name

    # Otherwise keep original commodity
    return commodity


def _build_transport_cost_lookup(transport_kpis: list | None) -> dict[tuple[str, str, str], float]:
    """Build (from_iso3, to_iso3, commodity) → cost_per_ton lookup from TransportKPI list.

    Args:
        transport_kpis: List of TransportKPI objects with transportation cost data

    Returns:
        Dictionary mapping (from_iso3, to_iso3, commodity) to cost per ton
    """
    lookup = {}
    if transport_kpis:
        for kpi in transport_kpis:
            # Normalize commodity name to lowercase for consistent lookups
            commodity_lower = kpi.commodity.lower() if hasattr(kpi.commodity, "lower") else str(kpi.commodity).lower()
            key = (kpi.reporter_iso, kpi.partner_iso, commodity_lower)
            lookup[key] = kpi.transportation_cost
    return lookup


def _build_wtp_lookup(willingness_to_pay: list | None) -> dict[tuple[str, str], float]:
    """Build (iso3, commodity) → wtp_value lookup from WillingnessToPay list.

    Args:
        willingness_to_pay: List of WillingnessToPay objects

    Returns:
        Dictionary mapping (iso3, commodity) to willingness to pay value
    """
    lookup = {}
    if willingness_to_pay:
        for wtp in willingness_to_pay:
            # Normalize commodity name to lowercase for consistent lookups
            commodity_lower = wtp.commodity.lower() if hasattr(wtp.commodity, "lower") else str(wtp.commodity).lower()
            key = (wtp.region_or_iso3, commodity_lower)
            lookup[key] = wtp.value
    return lookup


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


def _is_affected_by_hot_metal_radius(fg: FurnaceGroup, config: "SimulationConfig") -> bool:
    """Whether a furnace group consumes or produces a commodity gated by hot_metal_radius.

    Inspects `effective_primary_feedstocks`: any feedstock whose `metallic_charge` or
    `outputs` key matches `config.closely_allocated_products` marks the FG as affected.
    Affected FGs cluster by plant group instead of iso3 so the hot/cold commodity
    substitution in the LP disaggregator stays local.
    """
    closely_allocated = {normalize_product_name(p) for p in config.closely_allocated_products}

    feedstocks = getattr(fg, "effective_primary_feedstocks", None) or []
    for fs in feedstocks:
        metallic_charge = normalize_product_name(getattr(fs, "metallic_charge", "") or "")
        if metallic_charge in closely_allocated:
            return True
        outputs = getattr(fs, "outputs", None) or {}
        for output_name in outputs.keys():
            if normalize_product_name(output_name or "") in closely_allocated:
                return True
    return False


@dataclass(frozen=True)
class ClusterKey:
    """Key for grouping furnace groups into clusters.

    Furnace groups with identical keys will be aggregated into a single meta-furnace group.

    Attributes:
        technology_name: Technology type (e.g., "BF", "DRI", "EAF")
        location_key: Country ISO3 for techs unaffected by the hot-metal radius,
            or a plant_group_id for techs that consume/produce a closely-allocated
            commodity (hot_metal, dri_*, liquid_iron). Plant-group clustering keeps
            the hot/cold commodity substitution local.
        feedstock_signature: Hash of effective_primary_feedstocks to ensure compatibility
            (includes reductant information, making chosen_reductant redundant)
    """

    technology_name: str
    location_key: str
    feedstock_signature: str  # Hashable representation of feedstocks

    def __str__(self) -> str:
        # Include feedstock_signature prefix for readability
        fs_prefix = (
            self.feedstock_signature.split(":")[0] if ":" in self.feedstock_signature else self.feedstock_signature
        )
        return f"{self.technology_name}_{fs_prefix}_{self.location_key}"


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
        ...     cluster_key=ClusterKey("BF", "CHN", "coke:io_low"),
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
        logger.warning("Total capacity is zero for cluster, using unweighted average for center of gravity")
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

    # Use the first furnace group's location metadata (iso3, country, region).
    # These should be identical across the cluster: either we clustered by iso3,
    # or by plant_group_id (which is strictly nested within a single iso3).
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
    n_plant_group_keyed = 0
    n_iso3_keyed = 0
    for fg, plant in active_fgs:
        # Extract clustering attributes
        technology_name = fg.technology.name
        feedstock_signature = _create_feedstock_signature(fg)

        # When the plant-group flag is on, hot-metal-affected techs cluster by plant_group
        # so cold/hot commodity substitution stays local. Otherwise all techs cluster by iso3.
        use_plant_group = getattr(config, "cluster_hot_metal_techs_by_plant_group", False) and (
            _is_affected_by_hot_metal_radius(fg, config)
        )
        if use_plant_group:
            location_key = plant.ultimate_plant_group
            n_plant_group_keyed += 1
        else:
            location_key = plant.location.iso3
            n_iso3_keyed += 1

        cluster_key = ClusterKey(
            technology_name=technology_name,
            location_key=location_key,
            feedstock_signature=feedstock_signature,
        )

        if cluster_key not in clusters:
            clusters[cluster_key] = []
        clusters[cluster_key].append((fg, plant))

    logger.info(
        f"[CLUSTERING] Created {len(clusters)} unique clusters "
        f"({n_plant_group_keyed} FGs keyed by plant_group, {n_iso3_keyed} by iso3)"
    )

    # Filter out FGs without effective_primary_feedstocks from each cluster
    filtered_clusters = {}
    total_filtered_fgs = 0

    for cluster_key, cluster_fgs in clusters.items():
        # Filter FGs without feedstocks
        valid_fgs = []
        for fg, plant in cluster_fgs:
            if (
                hasattr(fg, "effective_primary_feedstocks")
                and fg.effective_primary_feedstocks
                and len(fg.effective_primary_feedstocks) > 0
            ):
                valid_fgs.append((fg, plant))
            else:
                total_filtered_fgs += 1
                logger.debug(
                    f"[CLUSTERING] Filtered out FG {fg.furnace_group_id} with no effective_primary_feedstocks. "
                    f"Tech: {fg.technology.name}, Capacity: {fg.capacity}"
                )

        # Check if cluster has any valid FGs
        if not valid_fgs:
            raise ValueError(
                f"[CLUSTERING] Cluster {cluster_key} has no FGs with valid effective_primary_feedstocks! "
                f"All {len(cluster_fgs)} FGs were filtered out. Cannot create meta-furnace group."
            )

        filtered_clusters[cluster_key] = valid_fgs

    if total_filtered_fgs > 0:
        logger.warning(
            f"[CLUSTERING] Filtered out {total_filtered_fgs} FGs without effective_primary_feedstocks. "
            f"These FGs will not participate in the LP."
        )
    else:
        logger.info("[CLUSTERING] No FGs were filtered - all have valid effective_primary_feedstocks")

    # Step 3: Create meta-furnace groups for each cluster (using filtered clusters)
    meta_furnace_groups: list[MetaFurnaceGroup] = []
    cluster_mapping: dict[str, list[str]] = {}

    for cluster_key, cluster_fgs in filtered_clusters.items():
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

    logger.info("[CLUSTERING] Statistics:")
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

    When clustering is enabled:
        - All flows are feasible (no distance restrictions)
        - LP uses only cold commodities which can travel any distance
        - Hot commodities are substituted during allocation creation, not checked here

    When clustering is disabled (backwards compatibility):
        - Hot/cold metal pairings enforced:
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
    # Check if clustering is enabled
    enable_clustering = getattr(config, "enable_furnace_group_clustering", False)

    # When clustering is enabled, all flows are feasible (cold commodities have no distance limits)
    if enable_clustering:
        return True

    # OLD BEHAVIOR: Apply distance restrictions for backwards compatibility
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


def _compute_allocation_costs(
    source_ids: list[str],
    dest_ids: list[str],
    source_locations: dict[str, Location],
    dest_locations: dict[str, Location],
    source_production_costs: dict[str, float],
    commodity_name: str,
    transport_cost_lookup: dict[tuple[str, str, str], float],
    wtp_lookup: dict[tuple[str, str], float],
) -> dict[tuple[str, str], float]:
    """Compute allocation costs for a set of source-destination pairs.

    Uses LP cost structure: transport + production - willingness_to_pay

    Args:
        source_ids: List of source IDs
        dest_ids: List of destination IDs
        source_locations: Source locations by ID
        dest_locations: Destination locations by ID
        source_production_costs: Production costs by source ID ($/ton)
        commodity_name: Commodity name (lowercase)
        transport_cost_lookup: (from_iso3, to_iso3, commodity) → transport_cost
        wtp_lookup: (iso3, commodity) → willingness_to_pay

    Returns:
        Dictionary mapping (source_id, dest_id) to allocation cost ($/ton)
    """
    allocation_costs = {}

    for source_id in source_ids:
        source_location = source_locations.get(source_id)
        source_prod_cost = source_production_costs.get(source_id, 0.0)

        for dest_id in dest_ids:
            dest_location = dest_locations.get(dest_id)

            if not source_location or not dest_location:
                # No location data, can't compute cost
                continue

            # 1. Transportation cost (iso3 → iso3)
            transport_cost = transport_cost_lookup.get((source_location.iso3, dest_location.iso3, commodity_name), 0.0)

            # 2. Production cost at source
            production_cost = source_prod_cost

            # 3. Willingness to pay (reduces cost for high-value destinations)
            wtp = wtp_lookup.get((dest_location.iso3, commodity_name), 0.0)

            # Total cost (matching LP structure)
            # Note: Skipping BOM energy costs and tariffs as they require PC names
            total_cost = transport_cost + production_cost - wtp

            allocation_costs[(source_id, dest_id)] = total_cost

    return allocation_costs


def _solve_batched_transportation_problem(
    source_supplies: dict[str, float],  # {source_id: supply_volume}
    dest_demands: dict[str, float],  # {dest_id: demand_volume}
    source_locations: dict[str, Location],  # {source_id: Location}
    dest_locations: dict[str, Location],  # {dest_id: Location}
    commodity,
    config: "SimulationConfig",
    allocation_costs: dict[tuple[str, str], float] | None = None,  # {(source_id, dest_id): $/ton}
    is_hot_commodity: bool = False,  # Whether this commodity is a hot (close) product
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
        allocation_costs: Optional pre-computed costs per edge (source_id, dest_id) → $/ton
            If provided, uses these costs instead of distance-based fallback

    Returns:
        Tuple of (flow_dict, stats_dict) where:
        - flow_dict: {(source_id, dest_id): volume} for non-zero flows only
        - stats_dict: Statistics about reduction (total_pairs, used_edges, etc.)
    """
    print(f"Solving transportation problem for {len(source_supplies)} sources → {len(dest_demands)} destinations...")
    # Build bipartite graph for transportation problem
    G = nx.DiGraph()

    # Add source and sink nodes for flow balancing
    SOURCE = "__source__"
    SINK = "__sink__"

    # Track statistics
    total_pairs = len(source_supplies) * len(dest_demands)
    infeasible_pairs = 0

    # Very high cost for infeasible flows (essentially infinity)
    INFEASIBLE_COST = int(1e9)

    # Store original (exact) totals for scaling solution back
    original_total_supply = sum(source_supplies.values())

    # Floor all volumes to integers for NetworkX (requires integer edge weights/capacities)
    # We'll scale the solution back to preserve exact BOM constraints
    source_supplies_floored = {k: int(v) for k, v in source_supplies.items()}
    dest_demands_floored = {k: int(v) for k, v in dest_demands.items()}

    # Filter out zeros AFTER flooring
    source_supplies_floored = {k: v for k, v in source_supplies_floored.items() if v > 0}
    dest_demands_floored = {k: v for k, v in dest_demands_floored.items() if v > 0}

    if not source_supplies_floored or not dest_demands_floored:
        # No valid sources or destinations after flooring
        return {}, {"total_pairs": 0, "used_edges": 0, "infeasible_pairs": 0}

    # Calculate total volume from FLOORED dictionaries (integers for NetworkX)
    total_supply = sum(source_supplies_floored.values())
    total_demand = sum(dest_demands_floored.values())

    # Use floored values for the transportation problem
    # Type ignore needed because we're intentionally converting from dict[str, float] to dict[str, int]
    source_supplies = source_supplies_floored  # type: ignore[assignment]
    dest_demands = dest_demands_floored  # type: ignore[assignment]

    # After flooring, adjust demands to match supply exactly (for NetworkX balance requirement)
    diff = total_supply - total_demand

    if diff != 0:
        # Adjust largest demand to balance (small adjustments from flooring)
        if dest_demands:
            # Find demand with largest value
            max_demand_key = max(dest_demands.keys(), key=lambda k: dest_demands[k])
            dest_demands[max_demand_key] += diff
            total_demand = total_supply  # Now balanced

    # Add source node with supply (negative demand)
    # IMPORTANT: Use same variable for both to ensure sum = 0 exactly
    G.add_node(SOURCE, demand=-total_supply)

    # Add sink node with demand (positive demand)
    # Use total_supply to ensure exact balance (sum of demands = 0)
    G.add_node(SINK, demand=total_supply)

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

            # Get commodity name for logging
            commodity_name = commodity.name if hasattr(commodity, "name") else str(commodity)
            if is_hot_commodity and distance_km > config.hot_metal_radius:
                # Additional check for hot commodities: enforce distance constraint
                is_feasible = False

            if is_feasible:
                # Use pre-computed allocation cost if available, otherwise fall back to distance
                if allocation_costs and (source_id, dest_id) in allocation_costs:
                    # Use pre-computed cost (already in $/ton from LP cost structure)
                    cost = int(max(allocation_costs[(source_id, dest_id)], 0.0) * 100)
                else:
                    # Fallback: use distance as proxy for transportation cost
                    cost = int(distance_km * 100)  # Scale for integer arithmetic
            else:
                # Infeasible flow: use very high cost (but allow as last resort)
                cost = INFEASIBLE_COST
                infeasible_pairs += 1

            # Add edge with capacity = total supply (effectively unbounded)
            max_flow = total_supply
            G.add_edge(f"from_{source_id}", f"to_{dest_id}", weight=cost, capacity=max_flow)

    # Validate graph before solving
    # Check: sum of all node demands must be 0
    total_node_demand = sum(G.nodes[n].get("demand", 0) for n in G.nodes())
    if abs(total_node_demand) > 1e-9:
        commodity_name = commodity.name if hasattr(commodity, "name") else str(commodity)
        logger.error(
            f"[DISAGGREGATION] Graph validation FAILED for {commodity_name}: "
            f"Sum of node demands = {total_node_demand:.15f} (should be 0)"
        )

    # Check for negative capacities or zero-capacity edges
    negative_capacity_edges = [(u, v) for u, v, d in G.edges(data=True) if d.get("capacity", 0) <= 0]
    if negative_capacity_edges:
        commodity_name = commodity.name if hasattr(commodity, "name") else str(commodity)
        logger.error(
            f"[DISAGGREGATION] Found {len(negative_capacity_edges)} edges with capacity <= 0 for {commodity_name}"
        )

    # Check capacity balance: sum of capacities from SOURCE should equal sum of capacities to SINK
    source_out_capacity = sum(G[SOURCE][n]["capacity"] for n in G.successors(SOURCE))
    sink_in_capacity = sum(G[n][SINK]["capacity"] for n in G.predecessors(SINK))
    if abs(source_out_capacity - sink_in_capacity) > 1e-6:
        commodity_name = commodity.name if hasattr(commodity, "name") else str(commodity)
        logger.error(
            f"[DISAGGREGATION] Capacity mismatch for {commodity_name}: "
            f"SOURCE out={source_out_capacity:.10f}, SINK in={sink_in_capacity:.10f}, "
            f"diff={abs(source_out_capacity - sink_in_capacity):.10f}"
        )

    # Solve min-cost flow (demands are set as node attributes)
    try:
        flow_dict = nx.min_cost_flow(G)
    except nx.NetworkXUnfeasible:
        commodity_name = commodity.name if hasattr(commodity, "name") else str(commodity)

        # Debug: Check graph connectivity
        num_nodes = G.number_of_nodes()
        num_edges = G.number_of_edges()

        # Check if all source/dest nodes are reachable
        source_nodes_in_graph = [n for n in G.nodes() if n.startswith("from_")]
        dest_nodes_in_graph = [n for n in G.nodes() if n.startswith("to_")]

        # Check edges between sources and dests
        source_dest_edges = [(u, v) for u, v in G.edges() if u.startswith("from_") and v.startswith("to_")]

        logger.error(
            f"[DISAGGREGATION] Batched transportation problem infeasible for commodity={commodity_name}. "
            f"Sources={len(source_supplies)}, Destinations={len(dest_demands)}, "
            f"Total supply={total_supply:.2f}, Total demand={total_demand:.2f}, "
            f"Infeasible edges={infeasible_pairs}/{total_pairs}. "
            f"Graph: {num_nodes} nodes, {num_edges} edges. "
            f"Source nodes={len(source_nodes_in_graph)}, Dest nodes={len(dest_nodes_in_graph)}, "
            f"Source→Dest edges={len(source_dest_edges)}"
        )

        # If there are no source→dest edges, that's the problem!
        if len(source_dest_edges) == 0:
            logger.error("[DISAGGREGATION] PROBLEM: No edges between sources and destinations! Graph is disconnected.")

        # For small cases, log detailed graph state for debugging
        if len(source_supplies) <= 2 and len(dest_demands) <= 2:
            logger.error(
                f"[DISAGGREGATION] DETAILED DEBUG for {commodity_name}:\n"
                f"  source_supplies: {source_supplies}\n"
                f"  dest_demands: {dest_demands}\n"
                f"  Node demands: {dict(G.nodes(data='demand'))}\n"
                f"  Edges: {[(u, v, d.get('capacity'), d.get('weight')) for u, v, d in G.edges(data=True)]}"
            )

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

    # Scale flows back to original (pre-flooring) total to preserve exact BOM constraints
    # The flow pattern is correct, we just need to scale to match original volumes
    if total_supply > 0 and abs(original_total_supply - total_supply) > 1e-9:
        scale_factor = original_total_supply / total_supply
        result_flows = {k: v * scale_factor for k, v in result_flows.items()}
        commodity_name = commodity.name if hasattr(commodity, "name") else str(commodity)
        logger.debug(
            f"[DISAGGREGATION] Scaled flows for {commodity_name} by {scale_factor:.10f} "
            f"to preserve exact BOM (floored: {total_supply}, original: {original_total_supply:.2f})"
        )

    # Prepare statistics
    stats = {
        "total_pairs": total_pairs,
        "used_edges": used_edges,
        "infeasible_pairs": infeasible_pairs,
        "reduction_pct": (1 - used_edges / total_pairs) * 100 if total_pairs > 0 else 0,
    }

    return result_flows, stats


def _validate_fg_can_receive_allocation(fg_id: str, plants_repo: "PlantInMemoryRepository | None") -> bool:
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

        # Note: FGs without effective_primary_feedstocks are already filtered during clustering
        # so we don't need to check that here
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
    plants_repo: "PlantInMemoryRepository | None" = None,
    transport_cost_lookup: dict[tuple[str, str, str], float] | None = None,
    wtp_lookup: dict[tuple[str, str], float] | None = None,
    is_hot_commodity: bool = False,
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
        transport_cost_lookup: Optional transport cost lookup dict
        wtp_lookup: Optional willingness to pay lookup dict
        is_hot_commodity: Flag indicating if the commodity is hot (affects disaggregation logic)
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

    # Compute allocation costs if lookups provided
    allocation_costs = None
    if transport_cost_lookup is not None and wtp_lookup is not None:
        commodity_name = str(commodity).lower()
        source_production_costs = {fg_id: from_meta_fg.weighted_avg_carbon_cost for fg_id in source_supplies.keys()}

        allocation_costs = _compute_allocation_costs(
            source_ids=list(source_supplies.keys()),
            dest_ids=list(dest_demands.keys()),
            source_locations=from_meta_fg.constituent_locations,
            dest_locations={fg_id: to_meta_fg.constituent_locations[fg_id] for fg_id in valid_dest_fgs},
            source_production_costs=source_production_costs,
            commodity_name=commodity_name,
            transport_cost_lookup=transport_cost_lookup,
            wtp_lookup=wtp_lookup,
        )

    if commodity.name in config.closely_allocated_products:
        is_hot_commodity = True
    else:
        is_hot_commodity = False

    # Use the batched solver
    return _solve_batched_transportation_problem(
        source_supplies=source_supplies,
        dest_demands=dest_demands,
        source_locations=from_meta_fg.constituent_locations,
        dest_locations={fg_id: to_meta_fg.constituent_locations[fg_id] for fg_id in valid_dest_fgs},
        commodity=commodity,
        config=config,
        allocation_costs=allocation_costs,
        is_hot_commodity=is_hot_commodity,
    )


def disaggregate_allocations(
    clustered_allocations: "Allocations",
    meta_furnace_groups: list[MetaFurnaceGroup],
    plants_repo: "PlantInMemoryRepository",
    config: "SimulationConfig",
    transport_kpis: list | None = None,
    willingness_to_pay: list | None = None,
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
        transport_kpis: Optional list of TransportKPI objects for transportation costs
        willingness_to_pay: Optional list of WillingnessToPay objects

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

    logger.info("[DISAGGREGATION] Starting allocation disaggregation...")
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

    # Build cost lookups once for all transportation problems
    transport_cost_lookup = _build_transport_cost_lookup(transport_kpis)
    wtp_lookup = _build_wtp_lookup(willingness_to_pay)
    logger.info(
        f"[DISAGGREGATION] Built cost lookups: {len(transport_cost_lookup)} transport routes, "
        f"{len(wtp_lookup)} WTP entries"
    )

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
    case2_batches: dict[tuple[str, str], list] = {}  # Meta-FG → DemandCenter: group by (from_meta_fg, commodity)
    case3_batches: dict[tuple[str, str], list] = {}  # Supplier → Meta-FG: group by (to_meta_fg, commodity)
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

    # Case 2: Meta-FG → DemandCenter (batched transportation problem)
    for (from_meta_name, commodity_str), batch in case2_batches.items():
        from_meta_fg = meta_fg_by_id[from_meta_name]

        # Collect all suppliers and their demands
        demand_volume: dict[str, float] = {}
        demand_pcs = {}
        total_batch_volume = 0

        for from_pc, to_pc, commodity, volume in batch:
            demand_volume[to_pc.name] = demand_volume.get(to_pc.name, 0) + volume
            demand_pcs[to_pc.name] = to_pc
            total_batch_volume += volume

        # Prepare supplies from FGs in meta-cluster
        fg_supplies = {fg_id: total_batch_volume * share for fg_id, share in from_meta_fg.capacity_shares.items()}

        # Compute allocation costs using LP cost structure
        commodity_name = str(batch[0][2]).lower()
        fg_production_costs = {
            fg_id: from_meta_fg.weighted_avg_carbon_cost for fg_id in fg_supplies.keys()
        }  # All FGs in cluster have same avg cost

        allocation_costs = _compute_allocation_costs(
            source_ids=list(fg_supplies.keys()),
            dest_ids=list(demand_volume.keys()),
            source_locations=from_meta_fg.constituent_locations,
            dest_locations={name: pc.location for name, pc in demand_pcs.items()},
            source_production_costs=fg_production_costs,
            commodity_name=commodity_name,
            transport_cost_lookup=transport_cost_lookup,
            wtp_lookup=wtp_lookup,
        )

        # Solve batched transportation problem
        flows, stats = _solve_batched_transportation_problem(
            source_supplies=fg_supplies,
            dest_demands=demand_volume,
            source_locations=from_meta_fg.constituent_locations,
            dest_locations={name: pc.location for name, pc in demand_pcs.items()},
            commodity=batch[0][2],  # Get commodity from first item
            config=config,
            allocation_costs=allocation_costs,
            is_hot_commodity=False,
        )

        # Track stats
        total_potential_edges += stats["total_pairs"]
        total_used_edges += stats["used_edges"]
        transportation_stats[(from_meta_name, "suppliers", commodity_str)] = stats

        # Create allocations with commodity substitution
        for (fg_id, supplier_name), flow_volume in flows.items():
            fg_pc = create_fg_process_center(
                fg_id,
                from_meta_fg,
                from_meta_fg.capacity_shares[fg_id],
                batch[0][0].process,  # Get process from first from_pc
            )
            supplier_pc = demand_pcs[supplier_name]

            # Calculate distance and substitute commodity if close enough
            fg_location = from_meta_fg.constituent_locations[fg_id]
            supplier_location = supplier_pc.location
            distance_km = _calculate_distance_km(fg_location, supplier_location)
            substituted_commodity = _substitute_commodity_by_distance(batch[0][2], distance_km, config)

            disaggregated_allocs[(fg_pc, supplier_pc, substituted_commodity)] = flow_volume

    # Case 3: Suppliers → Meta-FG (batched transportation problem)
    for (to_meta_name, commodity_str), batch in case3_batches.items():
        to_meta_fg = meta_fg_by_id[to_meta_name]

        # Collect all suppliers and their supplies
        supplier_supplies: dict[str, float] = {}
        demand_pcs = {}
        total_batch_volume = 0

        for from_pc, to_pc, commodity, volume in batch:
            supplier_supplies[from_pc.name] = supplier_supplies.get(from_pc.name, 0) + volume
            demand_pcs[from_pc.name] = from_pc
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

        # Compute allocation costs using LP cost structure
        commodity_name = str(batch[0][2]).lower()
        supplier_production_costs = {name: pc.production_cost for name, pc in demand_pcs.items()}

        allocation_costs = _compute_allocation_costs(
            source_ids=list(supplier_supplies.keys()),
            dest_ids=list(fg_demands.keys()),
            source_locations={name: pc.location for name, pc in demand_pcs.items()},
            dest_locations={fg_id: to_meta_fg.constituent_locations[fg_id] for fg_id in valid_fg_shares},
            source_production_costs=supplier_production_costs,
            commodity_name=commodity_name,
            transport_cost_lookup=transport_cost_lookup,
            wtp_lookup=wtp_lookup,
        )

        # Solve batched transportation problem
        flows, stats = _solve_batched_transportation_problem(
            source_supplies=supplier_supplies,
            dest_demands=fg_demands,
            source_locations={name: pc.location for name, pc in demand_pcs.items()},
            dest_locations={fg_id: to_meta_fg.constituent_locations[fg_id] for fg_id in valid_fg_shares},
            commodity=batch[0][2],  # Get commodity from first item
            config=config,
            allocation_costs=allocation_costs,
            is_hot_commodity=False,
        )

        # Track stats
        total_potential_edges += stats["total_pairs"]
        total_used_edges += stats["used_edges"]
        transportation_stats[("suppliers", to_meta_name, commodity_str)] = stats

        # Create allocations with commodity substitution
        for (supplier_name, fg_id), flow_volume in flows.items():
            supplier_pc = demand_pcs[supplier_name]
            fg_pc = create_fg_process_center(
                fg_id,
                to_meta_fg,
                to_meta_fg.capacity_shares[fg_id],
                batch[0][1].process,  # Get process from first to_pc
            )

            # Calculate distance and substitute commodity if close enough
            supplier_location = supplier_pc.location
            fg_location = to_meta_fg.constituent_locations[fg_id]
            distance_km = _calculate_distance_km(supplier_location, fg_location)
            substituted_commodity = _substitute_commodity_by_distance(batch[0][2], distance_km, config)

            disaggregated_allocs[(supplier_pc, fg_pc, substituted_commodity)] = flow_volume

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
            transport_cost_lookup=transport_cost_lookup,
            wtp_lookup=wtp_lookup,
        )

        # Track statistics
        total_potential_edges += stats["total_pairs"]
        total_used_edges += stats["used_edges"]
        transportation_stats[(from_pc.name, to_pc.name, str(commodity))] = stats

        # Create allocations for non-zero flows with commodity substitution
        for (from_fg_id, to_fg_id), flow_volume in flows.items():
            # Create ProcessCenters for this pair
            from_fg_pc = create_fg_process_center(
                from_fg_id, from_meta_fg, from_meta_fg.capacity_shares[from_fg_id], from_pc.process
            )
            to_fg_pc = create_fg_process_center(
                to_fg_id, to_meta_fg, to_meta_fg.capacity_shares[to_fg_id], to_pc.process
            )

            # Calculate distance and substitute commodity if close enough
            from_fg_location = from_meta_fg.constituent_locations[from_fg_id]
            to_fg_location = to_meta_fg.constituent_locations[to_fg_id]
            distance_km = _calculate_distance_km(from_fg_location, to_fg_location)
            substituted_commodity = _substitute_commodity_by_distance(commodity, distance_km, config)

            disaggregated_allocs[(from_fg_pc, to_fg_pc, substituted_commodity)] = flow_volume

    # Create new Allocations object
    result = Allocations(
        allocations=disaggregated_allocs,
        allocation_costs=None,  # Costs will be recalculated by TM_PAM_connector
    )

    logger.info(f"[DISAGGREGATION] Output allocations: {len(result.allocations)} flows")

    # Validate that totals are preserved after disaggregation
    logger.info("[DISAGGREGATION] === Volume Preservation Validation ===")

    # Helper function to normalize commodity name (treat hot/cold versions as same)
    def normalize_commodity_name(commodity) -> str:
        """Normalize commodity name to group hot/cold versions together."""
        commodity_name = commodity.name if hasattr(commodity, "name") else str(commodity)
        commodity_name = commodity_name.lower()

        # Map hot versions back to cold for comparison
        hot_to_cold = {v: k for k, v in COLD_TO_HOT_COMMODITY.items()}
        if commodity_name in hot_to_cold:
            return hot_to_cold[commodity_name]

        return commodity_name

    # Group by commodity for validation (using normalized names)
    input_totals_by_commodity: dict[str, float] = {}
    output_totals_by_commodity: dict[str, float] = {}

    for (from_pc, to_pc, commodity), volume in clustered_allocations.allocations.items():
        commodity_name = normalize_commodity_name(commodity)
        input_totals_by_commodity[commodity_name] = input_totals_by_commodity.get(commodity_name, 0.0) + volume

    for (from_pc, to_pc, commodity), volume in disaggregated_allocs.items():
        commodity_name = normalize_commodity_name(commodity)
        output_totals_by_commodity[commodity_name] = output_totals_by_commodity.get(commodity_name, 0.0) + volume

    # Compare totals
    all_commodities = set(input_totals_by_commodity.keys()) | set(output_totals_by_commodity.keys())
    max_discrepancy = 0.0

    for commodity_name in sorted(all_commodities):
        input_total = input_totals_by_commodity.get(commodity_name, 0.0)
        output_total = output_totals_by_commodity.get(commodity_name, 0.0)
        discrepancy = output_total - input_total
        discrepancy_pct = (discrepancy / input_total * 100) if input_total > 0 else 0.0
        max_discrepancy = max(max_discrepancy, abs(discrepancy))

        if abs(discrepancy) > 0.01:  # Only log if discrepancy > 0.01 tonnes
            print(
                f"[DISAGGREGATION] {commodity_name}: Input={input_total:.2f}t, Output={output_total:.2f}t, "
                f"Discrepancy={discrepancy:+.2f}t ({discrepancy_pct:+.3f}%)"
            )
            exit()
        else:
            print(
                f"[DISAGGREGATION] {commodity_name}: Input={input_total:.2f}t, Output={output_total:.2f}t, "
                f"Discrepancy={discrepancy:+.4f}t (OK)"
            )

    if max_discrepancy > 1.0:
        logger.error(f"[DISAGGREGATION] WARNING: Maximum volume discrepancy is {max_discrepancy:.2f} tonnes!")
    elif max_discrepancy > 0.1:
        logger.warning(f"[DISAGGREGATION] Maximum volume discrepancy: {max_discrepancy:.4f} tonnes")
    else:
        logger.info(f"[DISAGGREGATION] Volume preservation: OK (max discrepancy: {max_discrepancy:.4f}t)")

    # Log transportation problem statistics (edge reduction)
    if transportation_stats:
        overall_reduction = (1 - total_used_edges / total_potential_edges) * 100 if total_potential_edges > 0 else 0
        logger.info("[DISAGGREGATION] === Transportation Problem Summary ===")
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

    # Hot-metal-radius audit: count disaggregated flows where a closely-allocated
    # commodity (hot_metal, dri_*, liquid_iron) moves farther than hot_metal_radius.
    closely_allocated_names = {normalize_product_name(p) for p in getattr(config, "closely_allocated_products", [])}
    hot_metal_radius = getattr(config, "hot_metal_radius", 0.0)
    violations_by_commodity: dict[str, dict[str, float]] = {}
    total_violations = 0
    total_violation_volume = 0.0
    total_hot_flows = 0
    total_hot_volume = 0.0
    for (from_pc, to_pc, commodity), volume in result.allocations.items():
        commodity_name = normalize_product_name(commodity.name if hasattr(commodity, "name") else str(commodity))
        if commodity_name not in closely_allocated_names:
            continue
        from_loc = getattr(from_pc, "location", None)
        to_loc = getattr(to_pc, "location", None)
        if from_loc is None or to_loc is None:
            continue
        distance_km = _calculate_distance_km(from_loc, to_loc)
        bucket = violations_by_commodity.setdefault(
            commodity_name, {"flows": 0, "volume": 0.0, "violating_flows": 0, "violating_volume": 0.0}
        )
        bucket["flows"] += 1
        bucket["volume"] += float(volume)
        total_hot_flows += 1
        total_hot_volume += float(volume)
        if distance_km > hot_metal_radius:
            bucket["violating_flows"] += 1
            bucket["violating_volume"] += float(volume)
            total_violations += 1
            total_violation_volume += float(volume)

    logger.info("[DISAGGREGATION] === Hot Metal Radius Audit ===")
    logger.info(
        f"[DISAGGREGATION] Closely-allocated flows: {total_hot_flows} total "
        f"({total_hot_volume:.0f} t); violating radius={hot_metal_radius}km: "
        f"{total_violations} flows ({total_violation_volume:.0f} t)"
    )
    for commodity_name in sorted(violations_by_commodity):
        b = violations_by_commodity[commodity_name]
        logger.info(
            f"[DISAGGREGATION]   {commodity_name}: {b['violating_flows']}/{b['flows']} flows violate, "
            f"{b['violating_volume']:.0f}/{b['volume']:.0f} t violate"
        )

    logger.info("[DISAGGREGATION] Disaggregation complete")

    return result
