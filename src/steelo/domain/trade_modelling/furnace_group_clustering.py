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
# Reverse mapping: hot (close) → cold (distant) for relabeling flows that exceed the radius
HOT_TO_COLD_COMMODITY = {hot: cold for cold, hot in COLD_TO_HOT_COMMODITY.items()}


def _substitute_commodity_by_distance(
    commodity,
    distance_km: float,
    config: "SimulationConfig",
):
    """Relabel commodity based on transport distance.

    Commodities in COLD_TO_HOT_COMMODITY have a hot (close-transport) and cold
    (long-distance) form that represent the same physical product in different
    thermal/transport states. We align the label with the actual distance:

    - Cold → Hot when the flow is within `hot_metal_radius` (transport is close enough
      to keep the product hot, e.g. pig_iron → hot_metal).
    - Hot → Cold when the flow exceeds `hot_metal_radius` (the LP allocated hot but
      the actual route is too long, so the physical flow must be the cold form,
      e.g. dri_high → hbi_high).

    Args:
        commodity: Original commodity from LP — can be Commodity object or string.
        distance_km: Distance between source and destination.
        config: Config with `hot_metal_radius`.

    Returns:
        Commodity with label matching the actual transport distance — returns same
        type (Commodity object or string) as input.
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

    # Cold → Hot when route is short enough to keep the product hot
    if commodity_name in COLD_TO_HOT_COMMODITY:
        if distance_km <= config.hot_metal_radius:
            hot_name = COLD_TO_HOT_COMMODITY[commodity_name]
            return Commodity(name=hot_name) if is_commodity_object else hot_name

    # Hot → Cold when route exceeds the radius (physical flow must be cold form)
    elif commodity_name in HOT_TO_COLD_COMMODITY:
        if distance_km > config.hot_metal_radius:
            cold_name = HOT_TO_COLD_COMMODITY[commodity_name]
            return Commodity(name=cold_name) if is_commodity_object else cold_name

    # Otherwise keep original commodity
    return commodity


def _commodity_equivalent_names(commodity) -> set[str]:
    """Return the commodity name plus its hot/cold equivalent, all lowercased.

    Feedstock and aggregated-constraint data can reference either form (e.g.
    `hot_metal` or `pig_iron`), so we compare against the full equivalence class.
    """
    commodity_name = (commodity.name if hasattr(commodity, "name") else str(commodity)).lower()
    equivalent_names = {commodity_name}
    if commodity_name in COLD_TO_HOT_COMMODITY:
        equivalent_names.add(COLD_TO_HOT_COMMODITY[commodity_name])
    if commodity_name in HOT_TO_COLD_COMMODITY:
        equivalent_names.add(HOT_TO_COLD_COMMODITY[commodity_name])
    return equivalent_names


def _destination_has_min_constraint_for_commodity(
    to_meta_fg: "MetaFurnaceGroup",
    commodity,
    aggregated_constraints: list | None = None,
) -> bool:
    """Return True if `to_meta_fg`'s technology requires a minimum share of `commodity`.

    Two sources of minimum-share constraints are consulted:

    1. **Individual feedstock constraints** on `PrimaryFeedstock.minimum_share_in_product`
       (from `to_meta_fg.dynamic_business_case`). A feedstock with
       `minimum_share_in_product > 0` whose `metallic_charge` matches the commodity
       (or its hot/cold equivalent) counts as a minimum.

    2. **Aggregated wildcard constraints** (`AggregatedMetallicChargeConstraint`) —
       e.g. `BOF`, pattern `hot_metal`, `minimum_share=0.70`. We match when the
       constraint's `technology_name` equals `to_meta_fg.technology_name` AND the
       commodity's name (or its hot/cold equivalent) starts with `feedstock_pattern`.

    A minimum from either source means we cannot route the commodity via an
    out-of-radius edge without physically breaking the constraint.

    Args:
        to_meta_fg: Destination meta-furnace group.
        commodity: Commodity object (or string) being transported.
        aggregated_constraints: Optional list of `AggregatedMetallicChargeConstraint`
            from the environment (typically `bus.env.aggregated_metallic_charge_constraints`).

    Returns:
        True if any matching minimum-share constraint exists.
    """
    equivalent_names = _commodity_equivalent_names(commodity)

    # 1. Individual feedstock minimums
    if to_meta_fg.dynamic_business_case:
        for feedstock in to_meta_fg.dynamic_business_case:
            min_share = getattr(feedstock, "minimum_share_in_product", None)
            if min_share is None or min_share <= 0:
                continue
            fs_charge = str(getattr(feedstock, "metallic_charge", "")).lower()
            if fs_charge in equivalent_names:
                return True

    # 2. Aggregated wildcard constraints (e.g. BOF hot_metal* min=0.70)
    if aggregated_constraints:
        dest_tech = to_meta_fg.technology_name.lower()
        for c in aggregated_constraints:
            min_share = getattr(c, "minimum_share", None)
            if min_share is None or min_share <= 0:
                continue
            if str(getattr(c, "technology_name", "")).lower() != dest_tech:
                continue
            pattern = str(getattr(c, "feedstock_pattern", "")).lower()
            if not pattern:
                continue
            if any(name.startswith(pattern) for name in equivalent_names):
                return True

    return False


def _get_hot_metal_min_share_for_fg(
    fg: "FurnaceGroup",
    aggregated_constraints: list | None,
    config: "SimulationConfig",
) -> float | None:
    """Extract the maximum hot-metallic-charge minimum-share constraint for a furnace group.

    Checks both individual feedstock constraints (PrimaryFeedstock.minimum_share_in_product)
    and aggregated wildcard constraints (AggregatedMetallicChargeConstraint).  Only considers
    feedstocks/patterns that map to a hot commodity in config.closely_allocated_products.

    Args:
        fg: Furnace group to inspect.
        aggregated_constraints: Optional list of AggregatedMetallicChargeConstraint.
        config: Simulation configuration (for closely_allocated_products).

    Returns:
        Maximum hot-metallic min-share found, or None if no such constraint exists.
    """
    tech = fg.technology.name.lower()
    best_share: float | None = None

    # Build set of hot-commodity base names from config
    hot_names: set[str] = set()
    if hasattr(config, "closely_allocated_products"):
        for h in config.closely_allocated_products:
            hot_names.add(str(h).lower())

    # 1. Per-feedstock constraints on effective_primary_feedstocks
    feedstocks = getattr(fg, "effective_primary_feedstocks", None) or []
    for fs in feedstocks:
        min_share = getattr(fs, "minimum_share_in_product", None)
        if not min_share or min_share <= 0:
            continue
        charge = str(getattr(fs, "metallic_charge", "")).lower()
        if not charge:
            continue
        # Match charge to any hot commodity (prefix match in either direction)
        if any(charge.startswith(h) or h.startswith(charge) for h in hot_names):
            best_share = max(best_share or 0.0, float(min_share))

    # 2. Aggregated wildcard constraints
    if aggregated_constraints:
        for c in aggregated_constraints:
            constraint_min = getattr(c, "minimum_share", None)
            if not constraint_min or constraint_min <= 0:
                continue
            if str(getattr(c, "technology_name", "")).lower() != tech:
                continue
            pattern = str(getattr(c, "feedstock_pattern", "")).lower()
            if not pattern:
                continue
            # Match pattern against hot commodities
            if any(str(h).lower().startswith(pattern) or pattern.startswith(str(h).lower()) for h in hot_names):
                best_share = max(best_share or 0.0, float(constraint_min))

    return best_share if best_share and best_share > 0 else None


def _compute_effective_bof_capacity(
    fg: "FurnaceGroup",
    plant: "Plant",
    hot_metal_producers_by_iso3: dict[str, list[tuple["FurnaceGroup", "Plant"]]],
    aggregated_constraints: list | None,
    config: "SimulationConfig",
) -> float:
    """Compute the hot-metal-supply-limited effective capacity for a BOF furnace group.

    For non-BOF FGs returns the physical capacity unchanged.  For BOF FGs, caps
    capacity at ``reachable_hot_metal_supply / min_hot_metal_share`` so the LP cannot
    assign more production to a BOF FG than its local BF supply can support.

    Args:
        fg: The furnace group.
        plant: Plant that contains the FG (used for location/distance calculations).
        hot_metal_producers_by_iso3: Active BF/ESF/SR groups indexed by ISO3.
        aggregated_constraints: Aggregated metallic-charge constraints (may be None).
        config: Simulation configuration.

    Returns:
        Effective capacity in tonnes.  Always ≤ physical capacity.
    """
    physical_cap = float(fg.capacity)

    if fg.technology.name.lower() != "bof":
        return physical_cap

    min_share = _get_hot_metal_min_share_for_fg(fg, aggregated_constraints, config)
    if min_share is None or min_share <= 0:
        return physical_cap

    # Sum BF/ESF/SR capacity within hot_metal_radius of this BOF FG (same ISO3 only,
    # matching the per-country clustering constraint)
    iso3 = plant.location.iso3
    producers = hot_metal_producers_by_iso3.get(iso3, [])
    reachable_hm_cap = sum(
        float(bf_fg.capacity)
        for bf_fg, bf_plant in producers
        if plant.distance_to(bf_plant.location) <= config.hot_metal_radius
    )

    if reachable_hm_cap <= 0:
        # No reachable hot metal — this FG should have been filtered out in step 1b,
        # but guard here for safety.
        return 0.0

    cap_from_hm = reachable_hm_cap / min_share
    return min(physical_cap, cap_from_hm)


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
    plants: list[Plant],
    config: "SimulationConfig",
    aggregated_constraints: list | None = None,
) -> tuple[list[MetaFurnaceGroup], dict[str, list[str]]]:
    """Cluster furnace groups by technology, chosen reductant, and country.

    Groups furnace groups with identical technology, reductant, and country into
    meta-furnace groups. Each meta-furnace group represents the aggregated capacity
    and characteristics of all constituent furnace groups.

    For BOF clusters, each FG's capacity is capped at
    ``reachable_BF_capacity / min_hot_metal_share`` before summing into the cluster
    total.  This prevents the LP from allocating more BOF production than the local
    hot-metal supply can support, which would otherwise cause BOM constraint
    violations during disaggregation.

    Args:
        plants: List of all plants in the simulation
        config: Simulation configuration containing active_statuses
        aggregated_constraints: Optional list of AggregatedMetallicChargeConstraint
            objects (e.g. BOF hot_metal ≥ 70%).  Used to determine the hot-metal
            minimum-share when capping BOF effective capacities.

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

    # Step 1: Collect all active furnace groups with their plants.
    # Drop BOFs that have no active, in-country BF/ESF/SR within hot_metal_radius.
    #
    # The domain-model flag fg.has_hot_metal_access (set by PlantGroup.update_hot_metal_access)
    # is too permissive: PlantGroups can span countries, so a BOF near a border may get access
    # from a BF in a neighbouring country. Clustering is per-ISO3, so that cross-border BF ends
    # up in a different cluster and the BOF has no in-cluster source. Similarly, an inactive BF
    # at the same plant gives the flag but isn't in any cluster.
    #
    # We therefore build a location index of active hot-metal producers first, then check each
    # BOF against it using same-ISO3 + within-radius.

    # 1a. Index active hot-metal producers by country
    hot_metal_producers_by_iso3: dict[str, list[tuple[FurnaceGroup, Plant]]] = {}
    for plant in plants:
        for fg in plant.furnace_groups:
            if fg.status.lower() in config.active_statuses and fg.technology.name.lower() in ("bf", "esf", "sr"):
                iso3 = plant.location.iso3
                hot_metal_producers_by_iso3.setdefault(iso3, []).append((fg, plant))

    # 1b. Collect active FGs, filtering BOFs without an in-country producer within radius
    active_fgs: list[tuple[FurnaceGroup, Plant]] = []
    filtered_bofs_no_hot_metal = 0
    for plant in plants:
        for fg in plant.furnace_groups:
            if fg.status.lower() in config.active_statuses:
                if fg.technology.name.lower() == "bof":
                    iso3 = plant.location.iso3
                    producers = hot_metal_producers_by_iso3.get(iso3, [])
                    has_in_country_access = any(
                        plant.distance_to(p.location) <= config.hot_metal_radius for _, p in producers
                    )
                    if not has_in_country_access:
                        filtered_bofs_no_hot_metal += 1
                        logger.debug(
                            f"[CLUSTERING] Filtering BOF FG {fg.furnace_group_id} "
                            f"(plant {plant.plant_id}, {iso3}): no active BF/ESF/SR "
                            f"in same country within {config.hot_metal_radius:.0f} km"
                        )
                        continue
                active_fgs.append((fg, plant))

    if filtered_bofs_no_hot_metal > 0:
        logger.warning(
            f"[CLUSTERING] Filtered out {filtered_bofs_no_hot_metal} BOF furnace group(s) "
            f"without in-country hot-metal access within {config.hot_metal_radius:.0f} km"
        )

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

        # Calculate effective capacities.  For BOF FGs these are capped by the
        # reachable hot-metal supply so the LP doesn't over-allocate production.
        effective_caps: dict[str, float] = {
            fg.furnace_group_id: _compute_effective_bof_capacity(
                fg, plant, hot_metal_producers_by_iso3, aggregated_constraints, config
            )
            for fg, plant in cluster_fgs
        }
        total_eff_cap = sum(effective_caps.values())
        total_phys_cap = sum(float(fg.capacity) for fg, _ in cluster_fgs)
        total_capacity = Volumes(total_eff_cap)

        # Log effective capacity reductions for BOF clusters
        if cluster_key.technology_name.lower() == "bof" and total_phys_cap > 0:
            if abs(total_eff_cap - total_phys_cap) > 0.5:
                reduction_pct = (1.0 - total_eff_cap / total_phys_cap) * 100.0
                logger.debug(
                    f"[CLUSTERING] BOF cluster {meta_fg_id}: "
                    f"physical {total_phys_cap * T_TO_KT:.1f} kt → "
                    f"effective {total_eff_cap * T_TO_KT:.1f} kt "
                    f"({reduction_pct:.1f}% reduction from hot-metal supply limit)"
                )
                for fg, _ in cluster_fgs:
                    phys = float(fg.capacity)
                    eff = effective_caps[fg.furnace_group_id]
                    if abs(eff - phys) > 0.5:
                        logger.debug(
                            f"[CLUSTERING]   FG {fg.furnace_group_id}: {phys * T_TO_KT:.1f} kt → {eff * T_TO_KT:.1f} kt"
                        )

        # Calculate capacity shares based on effective capacities
        if total_eff_cap > 0:
            capacity_shares = {fg_id: eff / total_eff_cap for fg_id, eff in effective_caps.items()}
        else:
            # Equal shares if all effective capacities are zero
            capacity_shares = {fg.furnace_group_id: 1.0 / len(cluster_fgs) for fg, _ in cluster_fgs}

        # Calculate weighted average carbon cost (weighted by effective capacity)
        if total_eff_cap > 0:
            weighted_avg_carbon_cost = (
                sum(fg.carbon_cost_per_unit * effective_caps[fg.furnace_group_id] for fg, _ in cluster_fgs)
                / total_eff_cap
            )
        else:
            # Simple average if all have zero effective capacity
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
        if total_eff_cap > 0:
            # For each metallic charge, calculate effective-capacity-weighted average
            for metallic_charge in all_metallic_charges:
                total_weighted_cost = 0.0
                total_weight = 0.0

                for fg, _ in cluster_fgs:
                    if hasattr(fg, "energy_vopex_by_input") and metallic_charge in fg.energy_vopex_by_input:
                        cost = fg.energy_vopex_by_input[metallic_charge]
                        weight = effective_caps[fg.furnace_group_id]
                        total_weighted_cost += cost * weight
                        total_weight += weight

                if total_weight > 0:
                    weighted_avg_energy_costs[metallic_charge] = total_weighted_cost / total_weight
                else:
                    # No FGs in cluster have this metallic charge with non-zero effective capacity
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

    # OLD BEHAVIOR: Apply distance restrictions for backwards compatibility
    # Convert Commodity object to string if needed
    commodity_name = commodity.name if hasattr(commodity, "name") else str(commodity)

    # Skip distance checks if config doesn't have product lists
    if not hasattr(config, "closely_allocated_products") or not hasattr(config, "distantly_allocated_products"):
        return True

    is_close = distance_km <= config.hot_metal_radius

    # Closely allocated products can only travel short distances
    if commodity_name in config.closely_allocated_products:
        return is_close
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
    strict_radius: bool = False,  # If True, radius-violating edges are omitted and solver failure raises
    context_label: str = "",  # Optional label for error messages (e.g. "hot_metal → BOF cluster X")
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
    # Build bipartite graph for transportation problem
    G = nx.DiGraph()

    # Add source and sink nodes for flow balancing
    SOURCE = "__source__"
    SINK = "__sink__"

    # Track statistics
    total_pairs = len(source_supplies) * len(dest_demands)
    infeasible_pairs = 0
    infeasible_pairs_set: set[tuple[str, str]] = set()  # (source_id, dest_id) pairs that violate hot metal radius
    # (source_id, dest_id) -> (distance_km, source_iso3, dest_iso3) for all infeasible pairs
    infeasible_edge_metadata: dict[tuple[str, str], tuple[float, str, str]] = {}

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
        return {}, {
            "total_pairs": 0,
            "used_edges": 0,
            "infeasible_pairs": 0,
            "infeasible_flow_volume": 0.0,
            "total_flow_volume": 0.0,
            "infeasible_edge_details": [],
        }

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
                commodity_name = commodity.name if hasattr(commodity, "name") else str(commodity)
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
                infeasible_pairs_set.add((source_id, dest_id))
                infeasible_edge_metadata[(source_id, dest_id)] = (
                    distance_km,
                    source_location.iso3 if source_location else "unknown",
                    dest_location.iso3 if dest_location else "unknown",
                )
                if strict_radius:
                    # Hard-infeasible: omit the edge entirely so the solver cannot route across it
                    continue

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

        if strict_radius:
            # In strict mode, infeasibility is a hard error — identify which destinations are unreachable
            unreachable_dests = []
            for dest_id in dest_demands:
                dest_node = f"to_{dest_id}"
                if dest_node in G and not any(u.startswith("from_") for u in G.predecessors(dest_node)):
                    unreachable_dests.append((dest_id, dest_demands[dest_id]))

            # For small problems, include every node's supply/demand and every edge
            # (both feasible and blocked) so the infeasibility can be diagnosed inline.
            debug_details = ""
            largest_side = max(len(source_supplies), len(dest_demands))
            if largest_side <= 10:
                lines = ["", "  Source supplies:"]
                for sid, supply in sorted(source_supplies.items(), key=lambda x: -x[1]):
                    loc = source_locations.get(sid)
                    iso3 = loc.iso3 if loc else "?"
                    lines.append(f"    {sid} ({iso3}): {supply:.2f}t supply")
                lines.append("  Destination demands:")
                for did, demand in sorted(dest_demands.items(), key=lambda x: -x[1]):
                    loc = dest_locations.get(did)
                    iso3 = loc.iso3 if loc else "?"
                    lines.append(f"    {did} ({iso3}): {demand:.2f}t demand")
                feasible_lines = []
                # Only show sources that actually participate in the graph (supply > 0).
                # Sources with 0 supply (e.g. from _reach_based_source_supplies) were never
                # evaluated for feasibility, so they'd incorrectly appear as "feasible".
                active_sources = set(source_supplies_floored.keys())
                for sid in sorted(active_sources):
                    src_loc = source_locations.get(sid)
                    for did, dst_loc in dest_locations.items():
                        if (sid, did) in infeasible_pairs_set:
                            continue
                        if src_loc and dst_loc:
                            dkm = _calculate_distance_km(src_loc, dst_loc)
                            feasible_lines.append(f"    {sid} → {did}: {dkm:.0f} km  [feasible]")
                        else:
                            feasible_lines.append(f"    {sid} → {did}: (no location data)  [feasible]")
                if feasible_lines:
                    lines.append("  Feasible edges (within radius):")
                    lines.extend(feasible_lines)
                else:
                    lines.append(
                        "  Feasible edges (within radius): NONE — every source is out of radius for every destination"
                    )
                if infeasible_edge_metadata:
                    lines.append("  Blocked edges (radius-violating, omitted from graph):")
                    for (sid, did), (dkm, src_iso3, dst_iso3) in sorted(infeasible_edge_metadata.items()):
                        lines.append(f"    {sid} ({src_iso3}) → {did} ({dst_iso3}): {dkm:.0f} km  [blocked]")
                debug_details = "\n" + "\n".join(lines)

            raise RuntimeError(
                f"[DISAGGREGATION] STRICT-RADIUS transportation problem infeasible "
                f"for commodity={commodity_name}"
                f"{f' ({context_label})' if context_label else ''}. "
                f"{len(unreachable_dests)} destination(s) have no in-radius supplier. "
                f"Unreachable: {unreachable_dests[:5]}{'...' if len(unreachable_dests) > 5 else ''}. "
                f"Total supply={total_supply:.2f}, total demand={total_demand:.2f}."
                f"{debug_details}"
            )

        # Fallback: return empty flows
        return {}, {
            "total_pairs": total_pairs,
            "used_edges": 0,
            "infeasible_pairs": infeasible_pairs,
            "infeasible_flow_volume": 0.0,
            "total_flow_volume": 0.0,
            "infeasible_edge_details": [],
        }

    # Extract non-zero flows (ignore source/sink flows)
    result_flows = {}
    used_edges = 0
    infeasible_flow_volume = 0.0
    total_flow_volume = 0.0
    # (distance_km, volume, source_iso3, dest_iso3) for each violated edge that carried flow
    infeasible_edge_details: list[tuple[float, float, str, str]] = []

    for from_node, destinations in flow_dict.items():
        if from_node.startswith("from_"):
            source_id = from_node[5:]  # Remove "from_" prefix

            for to_node, flow_value in destinations.items():
                if to_node.startswith("to_") and flow_value > 1e-6:  # Ignore tiny numerical errors
                    dest_id = to_node[3:]  # Remove "to_" prefix
                    result_flows[(source_id, dest_id)] = flow_value
                    used_edges += 1
                    total_flow_volume += flow_value
                    if (source_id, dest_id) in infeasible_pairs_set:
                        infeasible_flow_volume += flow_value
                        dist_km, src_iso3, dst_iso3 = infeasible_edge_metadata[(source_id, dest_id)]
                        infeasible_edge_details.append((dist_km, flow_value, src_iso3, dst_iso3))

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

    # Log if any flows ended up on radius-violating edges. For substitutable hot commodities
    # (those with a cold equivalent in HOT_TO_COLD_COMMODITY), this is resolved downstream by
    # relabeling the flow to the cold commodity — so we log it at INFO level as a substitution
    # notice rather than WARNING. For non-substitutable cases, it remains a real violation.
    if infeasible_flow_volume > 1e-6:
        commodity_name = commodity.name if hasattr(commodity, "name") else str(commodity)
        violation_pct = (infeasible_flow_volume / total_flow_volume * 100) if total_flow_volume > 0 else 0.0
        will_be_substituted = commodity_name.lower() in HOT_TO_COLD_COMMODITY
        if will_be_substituted:
            cold_name = HOT_TO_COLD_COMMODITY[commodity_name.lower()]
            logger.info(
                f"[HOT_METAL_RADIUS] {commodity_name}: {infeasible_flow_volume:.2f}t "
                f"({violation_pct:.1f}% of {total_flow_volume:.2f}t total) exceeds radius — "
                f"will be relabeled to {cold_name} in disaggregated output"
            )
        else:
            logger.warning(
                f"[HOT_METAL_RADIUS] {commodity_name}: {infeasible_flow_volume:.2f}t "
                f"({violation_pct:.1f}% of {total_flow_volume:.2f}t total) routed across "
                f"hot-metal-radius-violating edges (last-resort fallback)"
            )

    # Prepare statistics
    stats = {
        "total_pairs": total_pairs,
        "used_edges": used_edges,
        "infeasible_pairs": infeasible_pairs,
        "infeasible_flow_volume": infeasible_flow_volume,
        "total_flow_volume": total_flow_volume,
        "infeasible_edge_details": infeasible_edge_details,
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


def _reach_based_source_supplies(
    source_capacity_shares: dict[str, float],
    source_locations: dict[str, Location],
    dest_demands: dict[str, float],
    dest_locations: dict[str, Location],
    hot_metal_radius: float,
    strict_radius: bool = False,
    context_label: str = "",
) -> dict[str, float]:
    """Split cluster supply by the destination capacity each source can physically reach.

    For HOT commodities like hot_metal / dri_*, an intra-cluster flow is only feasible
    if the specific source and destination furnace groups are within `hot_metal_radius`.
    A straight capacity-share split (source_supply = total × source_capacity_share) can
    strand supply at a source FG whose sibling source covers the local demand, while
    another physically separated source cluster pocket runs short — even when the LP's
    aggregate numbers balance.

    This helper instead attributes each destination's demand to the source FGs that can
    reach it, weighted by source capacity share among the reaching sources. The result:

    - Each destination's demand is exactly allocated (across its reaching sources).
    - Each source's supply equals the sum of demand it was attributed from reachable dests.
    - Intra-cluster "pockets" (plants separated by > radius) naturally balance locally.

    When locations are missing for a source/dest, the pair is treated as reachable (no
    distance constraint enforceable).

    Behaviour when a destination has no reaching source depends on `strict_radius`:

    - `strict_radius=False` (default): the destination's demand is dropped from the
      supply side. Downstream, the substitution fallback relabels the flow to the cold
      equivalent so the physical commodity is valid even if the LP allocated it as hot.
    - `strict_radius=True`: raise `RuntimeError`. Use this when the destination has a
      BOM minimum-share constraint on the hot commodity — any dropped demand would
      physically violate that minimum, so silent fallback is not acceptable.

    Args:
        source_capacity_shares: `from_meta_fg.capacity_shares` (fg_id → share ∈ [0, 1]).
        source_locations: fg_id → Location for every source FG.
        dest_demands: fg_id → demand volume for valid destinations.
        dest_locations: fg_id → Location for every destination FG.
        hot_metal_radius: km; pairs beyond this are considered unreachable.
        strict_radius: If True, raise when a dest has no in-radius source instead of
            silently dropping demand.
        context_label: Optional label included in the strict-mode error message
            (e.g. "hot_metal → cluster_BOF__DEU (BOF, min-constraint)").

    Returns:
        fg_id → supply volume for every source FG, including zeros for isolated sources.

    Raises:
        RuntimeError: If `strict_radius=True` and one or more destinations have no
            source within `hot_metal_radius`.
    """
    supplies: dict[str, float] = {sid: 0.0 for sid in source_capacity_shares}
    # (dest_id, demand, dest_location, sorted_source_distances) for unreachable dests
    unreachable: list[tuple[str, float, "Location | None", list[tuple[str, float]]]] = []

    for did, demand in dest_demands.items():
        dloc = dest_locations.get(did)
        # Collect sources that can reach this destination, with their capacity shares
        reaching: list[tuple[str, float]] = []
        for sid, cap_share in source_capacity_shares.items():
            sloc = source_locations.get(sid)
            if sloc is None or dloc is None:
                # No location data → assume reachable (can't enforce)
                reaching.append((sid, cap_share))
                continue
            if _calculate_distance_km(sloc, dloc) <= hot_metal_radius:
                reaching.append((sid, cap_share))
        if not reaching:
            if strict_radius:
                # Collect every source distance for this dest so the error can show
                # how far the nearest in-cluster source is.
                source_distances: list[tuple[str, float]] = []
                if dloc is not None:
                    for sid in source_capacity_shares:
                        sloc = source_locations.get(sid)
                        if sloc is not None:
                            source_distances.append((sid, _calculate_distance_km(sloc, dloc)))
                    source_distances.sort(key=lambda x: x[1])
                unreachable.append((did, demand, dloc, source_distances))
            # Non-strict: silently drop demand; substitution fallback takes over downstream.
            continue
        total_reaching_cap = sum(c for _, c in reaching)
        if total_reaching_cap <= 0:
            # All reaching sources have zero capacity — split the demand equally
            per_source = demand / len(reaching)
            for sid, _ in reaching:
                supplies[sid] += per_source
        else:
            for sid, c in reaching:
                supplies[sid] += demand * (c / total_reaching_cap)

    if strict_radius and unreachable:
        total_unmet = sum(demand for _, demand, _, _ in unreachable)
        header = (
            f"[DISAGGREGATION] STRICT-RADIUS infeasibility"
            f"{f' ({context_label})' if context_label else ''}: "
            f"{len(unreachable)} destination(s) have no source FG within "
            f"hot_metal_radius={hot_metal_radius:.0f} km. "
            f"Total unmet demand={total_unmet:.2f}t. "
            f"The destination technology has a BOM minimum-share constraint on this "
            f"commodity, so the LP's allocation cannot be physically routed without "
            f"violating that minimum."
        )
        lines = [header]
        shown = unreachable[:10]
        for did, demand, dloc, source_distances in shown:
            iso3 = dloc.iso3 if dloc is not None else "?"
            lines.append(f"  Dest FG {did} ({iso3}): demand={demand:.2f}t")
            if source_distances:
                nearest = source_distances[:3]
                for sid, d in nearest:
                    lines.append(f"    nearest source {sid}: {d:.0f} km (> {hot_metal_radius:.0f} km)")
            else:
                lines.append("    no source-location data available")
        if len(unreachable) > 10:
            lines.append(f"  ... and {len(unreachable) - 10} more unreachable destination(s)")
        raise RuntimeError("\n".join(lines))

    return supplies


def _reach_based_joint_supplies(
    source_fg_supplies: dict[str, float],
    source_locations: dict[str, "Location"],
    dest_demands: dict[str, float],
    dest_locations: dict[str, "Location"],
    hot_metal_radius: float,
    strict_radius: bool = True,
    context_label: str = "",
) -> dict[str, float]:
    """Compute reach-based supplies for a joint multi-source hot-metal disaggregation.

    For each destination BOF FG, distributes its demand to reachable source BF FGs
    weighted by their LP-allocated supply (``LP_volume × cap_share``).  This gives
    each geographic pocket a supply that exactly equals its local demand, so the
    per-component normalisation in ``_solve_strict_by_components`` becomes a no-op
    (scale factor = 1) and no BF FG output is silently inflated or deflated.

    Key properties of the resulting supply vector:
    - ``sum(supplies) == sum(dest_demands)``  (total preserved)
    - For every connected component: ``component_supply == component_demand``
      (pocket-balanced; no normalisation needed)
    - In the single-component case: per-source-cluster totals equal the original
      LP allocations exactly (reach-weighted shares cancel to capacity shares).
    - BF FG actual outgoing = reach-based supply → consistent with iron-ore inputs
      when ``_compute_effective_shares_by_cluster`` uses the same attribution.

    Args:
        source_fg_supplies: BF FG → LP_volume × cap_share (initial absolute supply).
        source_locations: BF FG → Location.
        dest_demands: BOF FG → effective_share × total LP hot_metal (target demand).
        dest_locations: BOF FG → Location.
        hot_metal_radius: km; pairs beyond this distance are considered unreachable.
        strict_radius: If True, raise ``RuntimeError`` when a destination has no
            reachable source.  If False, silently drop the demand.
        context_label: Optional label included in error messages.

    Returns:
        BF FG → reach-based supply (sums to total demand).

    Raises:
        RuntimeError: When ``strict_radius=True`` and a destination has no in-radius
            source FG.
    """
    supplies: dict[str, float] = {sid: 0.0 for sid in source_fg_supplies}

    for did, demand in dest_demands.items():
        dloc = dest_locations.get(did)
        reaching: list[tuple[str, float]] = []  # (source_fg_id, supply_weight)

        for sid, fg_supply in source_fg_supplies.items():
            sloc = source_locations.get(sid)
            if sloc is None or dloc is None:
                reaching.append((sid, fg_supply))
            elif _calculate_distance_km(sloc, dloc) <= hot_metal_radius:
                reaching.append((sid, fg_supply))

        if not reaching:
            if strict_radius:
                raise RuntimeError(
                    f"[DISAGGREGATION] Reach-based joint supplies: dest FG {did} "
                    f"has no reachable source FG within {hot_metal_radius:.0f} km"
                    f"{f' ({context_label})' if context_label else ''}."
                )
            # Non-strict: silently drop demand (caller handles the gap).
            continue

        total_weight = sum(w for _, w in reaching)
        if total_weight <= 0:
            # All reaching sources have zero supply — split equally
            per_source = demand / len(reaching)
            for sid, _ in reaching:
                supplies[sid] += per_source
        else:
            for sid, weight in reaching:
                supplies[sid] += demand * (weight / total_weight)

    return supplies


def _solve_strict_by_components(
    source_supplies: dict[str, float],
    dest_demands: dict[str, float],
    source_locations: dict[str, "Location"],
    dest_locations: dict[str, "Location"],
    commodity,
    config: "SimulationConfig",
    allocation_costs: dict[tuple[str, str], float] | None = None,
    context_label: str = "",
) -> tuple[dict[tuple[str, str], float], dict]:
    """Solve a strict-radius transportation problem by connected component.

    Under strict radius, only edges within ``config.hot_metal_radius`` are
    allowed.  A single cluster may span several geographic pockets that are
    mutually unreachable.  Running one big min-cost-flow would expose integer-
    flooring artefacts across pockets (the rebalancing step shifts rounding
    error to a destination that might be unreachable from the source that lost
    the fractional ton).

    This helper decomposes the bipartite feasibility graph into connected
    components.  Within each component every source can (transitively) reach
    every destination, so the existing per-solve integer rebalancing stays
    local and can never create cross-pocket infeasibility.

    Supply is normalised per component so that ``component_supply ==
    component_demand``.  The final flows are rescaled back to the original
    total volume by ``_solve_batched_transportation_problem``'s built-in
    rescale step.

    Args:
        source_supplies: fg_id → supply volume (from ``_reach_based_source_supplies``).
        dest_demands: fg_id → demand volume.
        source_locations: fg_id → Location for source FGs.
        dest_locations: fg_id → Location for destination FGs.
        commodity: Commodity being transported.
        config: Simulation config (for ``hot_metal_radius``).
        allocation_costs: Optional pre-computed ``(source_id, dest_id) → $/ton``.
        context_label: Label for error messages.

    Returns:
        Merged ``(flow_dict, stats_dict)`` across all components.
    """
    import networkx as nx

    # Only sources with supply > 0 participate in the graph.
    active_sources = {sid: s for sid, s in source_supplies.items() if s > 1e-6}
    active_dests = {did: d for did, d in dest_demands.items() if d > 1e-6}

    # Build undirected bipartite feasibility graph.  Prefixes distinguish the
    # two sides so source/dest FG IDs can overlap (e.g. same plant).
    fg = nx.Graph()
    for sid in active_sources:
        fg.add_node(f"s_{sid}")
    for did in active_dests:
        fg.add_node(f"d_{did}")

    for sid in active_sources:
        sloc = source_locations.get(sid)
        for did in active_dests:
            dloc = dest_locations.get(did)
            if sloc is None or dloc is None:
                # No location data → assume reachable (can't enforce radius)
                fg.add_edge(f"s_{sid}", f"d_{did}")
            elif _calculate_distance_km(sloc, dloc) <= config.hot_metal_radius:
                fg.add_edge(f"s_{sid}", f"d_{did}")

    components = list(nx.connected_components(fg))
    logger.info(
        f"[DISAGGREGATION] Strict-radius decomposition: "
        f"{len(components)} connected component(s) for {context_label or commodity}"
    )

    merged_flows: dict[tuple[str, str], float] = {}
    merged_stats: dict[str, object] = {
        "total_pairs": 0,
        "used_edges": 0,
        "infeasible_pairs": 0,
        "infeasible_flow_volume": 0.0,
        "total_flow_volume": 0.0,
        "infeasible_edge_details": [],
    }

    for comp_nodes in components:
        comp_source_ids = {n[2:] for n in comp_nodes if n.startswith("s_")}
        comp_dest_ids = {n[2:] for n in comp_nodes if n.startswith("d_")}

        comp_supply = {sid: active_sources[sid] for sid in comp_source_ids if sid in active_sources}
        comp_demand = {did: active_dests[did] for did in comp_dest_ids if did in active_dests}

        if not comp_supply or not comp_demand:
            # Isolated source(s) with no reachable dest, or vice versa.
            # _reach_based_source_supplies should have caught this under strict
            # mode, but guard defensively.
            if comp_demand:
                unmet = sum(comp_demand.values())
                raise RuntimeError(
                    f"[DISAGGREGATION] Strict-radius component has destinations "
                    f"with no reachable source ({context_label}). "
                    f"Unmet demand={unmet:.2f}t, dest FGs={list(comp_demand.keys())[:5]}"
                )
            continue  # orphan sources with no destinations — nothing to route

        # Normalise supply within this component so supply == demand.
        total_s = sum(comp_supply.values())
        total_d = sum(comp_demand.values())
        if total_s > 0 and abs(total_s - total_d) > 0.01:
            scale = total_d / total_s
            comp_supply = {sid: s * scale for sid, s in comp_supply.items()}

        # Solve this component independently.  strict_radius=True inside the
        # batched solver ensures individual edges beyond the radius are still
        # omitted (belt-and-braces; the component graph already guarantees
        # feasibility, but the solver's own radius check is cheap insurance).
        flows, stats = _solve_batched_transportation_problem(
            source_supplies=comp_supply,
            dest_demands=comp_demand,
            source_locations=source_locations,
            dest_locations=dest_locations,
            commodity=commodity,
            config=config,
            allocation_costs=allocation_costs,
            is_hot_commodity=True,
            strict_radius=True,
            context_label=context_label,
        )

        merged_flows.update(flows)
        merged_stats["total_pairs"] += stats.get("total_pairs", 0)  # type: ignore[operator]
        merged_stats["used_edges"] += stats.get("used_edges", 0)  # type: ignore[operator]
        merged_stats["infeasible_pairs"] += stats.get("infeasible_pairs", 0)  # type: ignore[operator]
        merged_stats["infeasible_flow_volume"] += stats.get("infeasible_flow_volume", 0.0)  # type: ignore[operator]
        merged_stats["total_flow_volume"] += stats.get("total_flow_volume", 0.0)  # type: ignore[operator]
        merged_stats["infeasible_edge_details"] += stats.get("infeasible_edge_details", [])  # type: ignore[operator]

    return merged_flows, merged_stats


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
    aggregated_constraints: list | None = None,
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
        aggregated_constraints: Optional list of `AggregatedMetallicChargeConstraint`.
            Consulted (in addition to per-feedstock minimums) when deciding whether
            to enforce strict radius on a hot commodity.

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
        return {}, {
            "total_pairs": 0,
            "used_edges": 0,
            "infeasible_pairs": 0,
            "infeasible_flow_volume": 0.0,
            "total_flow_volume": 0.0,
            "infeasible_edge_details": [],
        }

    # Renormalize shares if some FGs were filtered out
    if total_dest_share < 0.99:  # Some FGs were excluded
        logger.warning(
            f"[DISAGGREGATION] Filtered out {len(to_meta_fg.capacity_shares) - len(valid_dest_fgs)} FGs "
            f"without BOMs from {to_meta_fg.meta_furnace_group_id}"
        )
        valid_dest_fgs = {fg_id: share / total_dest_share for fg_id, share in valid_dest_fgs.items()}

    # Determine if this is a hot commodity (only these need radius-aware splitting)
    is_hot_commodity = commodity.name in config.closely_allocated_products

    # Strict radius enforcement: for any HOT commodity going to a destination whose
    # technology has a BOM minimum constraint on that commodity, radius violations
    # would physically break the min-ratio guarantee. We must route only within-radius
    # edges and error out if any destination is unreachable. Determined here so the
    # reach-based supply split can fail fast before the solver runs.
    strict_radius = is_hot_commodity and _destination_has_min_constraint_for_commodity(
        to_meta_fg, commodity, aggregated_constraints
    )
    context_label = (
        f"{commodity.name} → {to_meta_fg.meta_furnace_group_id} ({to_meta_fg.technology_name}, min-constraint)"
        if strict_radius
        else ""
    )

    # Prepare demands (destinations) from to_meta_fg (only valid FGs)
    dest_demands = {fg_id: total_volume * share for fg_id, share in valid_dest_fgs.items()}

    # Prepare supplies (sources) from from_meta_fg.
    # For HOT commodities, we split each destination's demand across the source FGs that
    # can physically reach it (within hot_metal_radius), weighted by source capacity share.
    # This prevents the LP's capacity-share split from stranding supply at isolated source
    # FGs whose local sibling source can't serve it — e.g. two plants in the same cluster
    # 113 km apart each need independent supply/demand balance. Cold commodities don't
    # need this; the global capacity-share split is fine for them.
    dest_locations_for_valid = {fg_id: to_meta_fg.constituent_locations[fg_id] for fg_id in valid_dest_fgs}
    if is_hot_commodity and strict_radius:
        # Strict radius: only within-radius sources serve each destination.
        # Supply is sized to exactly cover reachable demand; unreachable destinations
        # raise a RuntimeError (caught upstream). This is only safe to call when
        # strict_radius=True because _reach_based_source_supplies silently drops
        # unreachable demand, which would create a massive supply < demand imbalance
        # if most FGs in the cluster are beyond the radius.
        source_supplies = _reach_based_source_supplies(
            source_capacity_shares=from_meta_fg.capacity_shares,
            source_locations=from_meta_fg.constituent_locations,
            dest_demands=dest_demands,
            dest_locations=dest_locations_for_valid,
            hot_metal_radius=config.hot_metal_radius,
            strict_radius=True,
            context_label=context_label,
        )
    else:
        # Non-strict (cold commodity, or hot commodity with no min-constraint):
        # use simple proportional supply so total_supply == total_demand.
        # The solver may route some hot commodity flows beyond hot_metal_radius
        # (at high penalty cost); _substitute_commodity_by_distance will relabel
        # those long flows to the cold form (e.g. hot_metal → pig_iron).
        source_supplies = {fg_id: total_volume * share for fg_id, share in from_meta_fg.capacity_shares.items()}

    # Compute allocation costs if lookups provided
    allocation_costs = None
    if transport_cost_lookup is not None and wtp_lookup is not None:
        commodity_name = str(commodity).lower()
        source_production_costs = {fg_id: from_meta_fg.weighted_avg_carbon_cost for fg_id in source_supplies.keys()}

        allocation_costs = _compute_allocation_costs(
            source_ids=list(source_supplies.keys()),
            dest_ids=list(dest_demands.keys()),
            source_locations=from_meta_fg.constituent_locations,
            dest_locations=dest_locations_for_valid,
            source_production_costs=source_production_costs,
            commodity_name=commodity_name,
            transport_cost_lookup=transport_cost_lookup,
            wtp_lookup=wtp_lookup,
        )

    # When strict_radius is on, the cluster may contain geographically separated
    # pockets. Running one big min-cost-flow over the whole cluster causes integer-
    # flooring mismatches to propagate across pockets (the rebalancing step shifts
    # rounding error to a destination that might be unreachable from the source that
    # lost the fraction). Instead, decompose the problem into connected components
    # of the feasibility graph — each pocket gets its own balanced sub-problem so
    # rounding is always local to nodes that can actually cover each other.
    if strict_radius:
        return _solve_strict_by_components(
            source_supplies=source_supplies,
            dest_demands=dest_demands,
            source_locations=from_meta_fg.constituent_locations,
            dest_locations=dest_locations_for_valid,
            commodity=commodity,
            config=config,
            allocation_costs=allocation_costs,
            context_label=context_label,
        )

    # Non-strict: single batched solve (radius-violating edges get high penalty
    # but are still allowed; downstream substitution relabels them to the cold form).
    return _solve_batched_transportation_problem(
        source_supplies=source_supplies,
        dest_demands=dest_demands,
        source_locations=from_meta_fg.constituent_locations,
        dest_locations=dest_locations_for_valid,
        commodity=commodity,
        config=config,
        allocation_costs=allocation_costs,
        is_hot_commodity=is_hot_commodity,
    )


def _compute_effective_shares_by_cluster(
    meta_furnace_groups: list[MetaFurnaceGroup],
    case4_allocs: list,
    meta_fg_by_id: dict[str, MetaFurnaceGroup],
    plants_repo: "PlantInMemoryRepository | None",
    config: "SimulationConfig",
    aggregated_constraints: list | None = None,
    case2_batches: dict | None = None,
) -> dict[str, dict[str, float]]:
    """Compute per-FG "effective shares" for each cluster, reflecting geographic reality.

    In a cluster whose members span more than `hot_metal_radius`, a naive capacity-share
    split breaks per-FG BOM balance: the LP sees the cluster as one blob, but physically
    each FG's output is limited by the demand it can actually reach within radius. To
    keep downstream allocations BOM-consistent per FG, we compute one share vector per
    cluster and use it for ALL flows (raw-material inflows, outgoing hot/cold, etc.).

    Per-FG effective output is aggregated from case-4 outgoing flows:

    - **Hot commodities with strict radius** (destination has a BOM min-constraint):
      each dest FG's demand is attributed to the source FGs that can reach it (within
      `hot_metal_radius`), weighted by their capacity share among the reachers. This
      mirrors the supply-side logic in `_solve_transportation_problem` exactly.
    - **Hot commodities without strict radius** (no min-constraint on destination):
      capacity-share attribution, same as cold. The transportation solver uses proportional
      supply for these flows, so the effective shares must match.
    - **Cold commodities**: capacity-share attribution (no radius constraint).
    - **Case 2 outgoing (cluster → demand center)**: always capacity-share attribution.
      This is critical for clusters (e.g. DRI) that have BOTH strict Case 4 outgoing
      (hot DRI to nearby EAF) and Case 2 outgoing (cold HBI export to demand). An FG
      that is isolated from all EAF FGs within radius gets zero share from strict Case 4
      attribution, but still has real production through Case 2. Including Case 2 here
      ensures that FG's effective_share is non-zero, so it gets raw-material inputs in
      Case 3 that match its actual Case 2 production.

    After summing, the per-FG totals are normalized to shares. Clusters with no outgoing
    flows (or zero total) fall back to `capacity_shares`.

    Args:
        meta_furnace_groups: All clusters in this disaggregation.
        case4_allocs: List of (from_pc, to_pc, commodity, volume) — cluster→cluster flows.
        meta_fg_by_id: Lookup from cluster_id → MetaFurnaceGroup.
        plants_repo: Used to filter destination FGs without valid BOMs (same filter as
            case-3 / case-4 apply), so effective shares ignore FGs the solver will skip.
        config: Simulation config (for `hot_metal_radius` and `closely_allocated_products`).
        aggregated_constraints: Optional aggregated metallic charge constraints. Used to
            determine whether a hot commodity flow is strict-radius (destination has a
            min-constraint) or not. Non-strict flows use capacity-share attribution to
            stay consistent with `_solve_transportation_problem`.
        case2_batches: Optional Case 2 batches {(from_meta_name, commodity_str): [...]}.
            Used to include outgoing cluster→demand flows in the effective_share calculation
            so FGs that export cold product (no strict radius) contribute their capacity
            share to the totals.

    Returns:
        `{cluster_id: {fg_id: effective_share, ...}, ...}` — shares sum to 1 per cluster,
        or equal `capacity_shares` as fallback.
    """
    effective_shares: dict[str, dict[str, float]] = {
        mfg.meta_furnace_group_id: dict(mfg.capacity_shares) for mfg in meta_furnace_groups
    }

    # Accumulate per-FG output volume per source cluster
    per_cluster_fg_out: dict[str, dict[str, float]] = {
        mfg.meta_furnace_group_id: {fg_id: 0.0 for fg_id in mfg.capacity_shares} for mfg in meta_furnace_groups
    }
    clusters_with_outgoing: set[str] = set()

    closely_allocated = set(config.closely_allocated_products)

    # Separate strict hot-metal flows (need joint reach-based attribution) from all others.
    # For non-strict flows the solver uses cap-share supply, so cap-share attribution here
    # stays consistent.  For strict flows (e.g. hot_metal → BOF with ≥70% min-constraint),
    # the joint transportation problem uses reach-based supplies so each geographic pocket
    # is self-balancing.  The source effective-shares computed here must mirror that exactly
    # so that BF FG iron-ore inputs (Case 3) match actual hot-metal output.
    joint_strict_groups: dict[tuple[str, str], list] = {}  # (to_id, commodity_name) → flows

    for from_pc, to_pc, commodity, volume in case4_allocs:
        src_cluster_id = from_pc.name
        dst_cluster_id = to_pc.name
        if src_cluster_id not in meta_fg_by_id or dst_cluster_id not in meta_fg_by_id:
            continue
        from_mfg = meta_fg_by_id[src_cluster_id]
        to_mfg = meta_fg_by_id[dst_cluster_id]
        clusters_with_outgoing.add(src_cluster_id)

        is_hot = commodity.name in closely_allocated
        is_strict = is_hot and _destination_has_min_constraint_for_commodity(to_mfg, commodity, aggregated_constraints)

        if is_strict:
            key = (dst_cluster_id, commodity.name)
            joint_strict_groups.setdefault(key, []).append((from_pc, to_pc, commodity, volume))
        else:
            # Cold / non-strict hot: cap-share attribution mirrors what the solver does.
            per_fg_out = per_cluster_fg_out[src_cluster_id]
            for sid, cap_share in from_mfg.capacity_shares.items():
                per_fg_out[sid] += volume * cap_share

    # For each joint strict group, compute reach-based attribution across all source clusters.
    # This mirrors _reach_based_joint_supplies exactly so effective_shares and actual flows
    # are always consistent — pocket supply == pocket demand, no component normalisation.
    for (to_meta_fg_id, commodity_name), group_flows in joint_strict_groups.items():
        to_mfg = meta_fg_by_id[to_meta_fg_id]
        total_volume = sum(v for _, _, _, v in group_flows)

        # Build joint source supplies (LP × cap_share for each BF FG)
        joint_source_supplies: dict[str, float] = {}
        joint_source_locations_local: dict[str, Location] = {}
        fg_to_src_cluster: dict[str, str] = {}

        for from_pc, _, _, vol in group_flows:
            from_mfg = meta_fg_by_id[from_pc.name]
            for fg_id, cap_share in from_mfg.capacity_shares.items():
                joint_source_supplies[fg_id] = joint_source_supplies.get(fg_id, 0.0) + vol * cap_share
                joint_source_locations_local[fg_id] = from_mfg.constituent_locations[fg_id]
                fg_to_src_cluster[fg_id] = from_pc.name

        # Build destination demands (effective_share × total LP volume for each BOF FG)
        valid_dest = {
            fg_id: share
            for fg_id, share in to_mfg.capacity_shares.items()
            if _validate_fg_can_receive_allocation(fg_id, plants_repo)
        }
        total_valid = sum(valid_dest.values())
        if total_valid <= 0:
            continue
        if total_valid < 0.99:
            valid_dest = {fg_id: s / total_valid for fg_id, s in valid_dest.items()}

        joint_dest_demands_local = {fg_id: total_volume * share for fg_id, share in valid_dest.items()}
        joint_dest_locations_local = {fg_id: to_mfg.constituent_locations[fg_id] for fg_id in valid_dest}

        # Reach-based attribution: distribute each BOF FG's demand to reachable BF FGs
        # weighted by their LP supply.  Falls back to cap-share if a BOF FG is unreachable.
        try:
            joint_reach_supplies = _reach_based_joint_supplies(
                source_fg_supplies=joint_source_supplies,
                source_locations=joint_source_locations_local,
                dest_demands=joint_dest_demands_local,
                dest_locations=joint_dest_locations_local,
                hot_metal_radius=config.hot_metal_radius,
                strict_radius=True,
                context_label=f"{commodity_name} → {to_meta_fg_id} (effective-shares)",
            )
        except RuntimeError:
            # Fallback: cap-share attribution (should not normally happen after Part 1 fix)
            for from_pc, _, _, vol in group_flows:
                from_mfg = meta_fg_by_id[from_pc.name]
                per_fg_out = per_cluster_fg_out[from_pc.name]
                for sid, cap_share in from_mfg.capacity_shares.items():
                    per_fg_out[sid] += vol * cap_share
            continue

        # Accumulate reach-based supply into per-cluster attribution
        for fg_id, supply in joint_reach_supplies.items():
            src_cluster = fg_to_src_cluster[fg_id]
            per_cluster_fg_out[src_cluster][fg_id] = per_cluster_fg_out[src_cluster].get(fg_id, 0.0) + supply

    # Include Case 2 outgoing flows (cluster → demand center).
    # These are always capacity-share attributed (no radius constraint). An FG that is
    # geographically isolated from strict-radius Case 4 destinations (e.g. a DRI FG too
    # far from any EAF) still has real production through cold-form Case 2 exports (HBI).
    # Without this, such FGs get effective_share=0 and are starved of raw-material inputs
    # in Case 3 even though they produce and export via Case 2.
    if case2_batches:
        for (from_meta_name, _), batch in case2_batches.items():
            if from_meta_name not in meta_fg_by_id:
                continue
            from_mfg = meta_fg_by_id[from_meta_name]
            clusters_with_outgoing.add(from_meta_name)
            per_fg_out = per_cluster_fg_out[from_meta_name]
            volume = sum(v for _, _, _, v in batch)
            for sid, cap_share in from_mfg.capacity_shares.items():
                per_fg_out[sid] += volume * cap_share

    # Normalize to shares, skipping clusters with no outgoing
    for cluster_id in clusters_with_outgoing:
        per_fg_out = per_cluster_fg_out[cluster_id]
        total = sum(per_fg_out.values())
        if total > 0:
            effective_shares[cluster_id] = {fg_id: out / total for fg_id, out in per_fg_out.items()}
        # else: keep capacity_shares fallback

    return effective_shares


def disaggregate_allocations(
    clustered_allocations: "Allocations",
    meta_furnace_groups: list[MetaFurnaceGroup],
    plants_repo: "PlantInMemoryRepository",
    config: "SimulationConfig",
    transport_kpis: list | None = None,
    willingness_to_pay: list | None = None,
    aggregated_constraints: list | None = None,
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

    # Log which (technology, commodity-or-pattern) pairs have minimum-share constraints.
    # These pairs trigger strict-radius enforcement when the commodity is hot.
    # Two sources are collected:
    #   - Per-feedstock minimums on PrimaryFeedstock.minimum_share_in_product, keyed by
    #     (technology, metallic_charge). Different reductants can yield different min shares,
    #     so we track a min..max range.
    #   - Aggregated wildcard constraints (AggregatedMetallicChargeConstraint) from the
    #     environment, keyed by (technology, feedstock_pattern + "*").
    feedstock_mins: dict[tuple[str, str], list[float]] = {}
    for mfg in meta_furnace_groups:
        if not mfg.dynamic_business_case:
            continue
        tech = mfg.technology_name
        for feedstock in mfg.dynamic_business_case:
            min_share = getattr(feedstock, "minimum_share_in_product", None)
            if min_share is None or min_share <= 0:
                continue
            charge = str(getattr(feedstock, "metallic_charge", "")).lower()
            if not charge:
                continue
            feedstock_mins.setdefault((tech, charge), []).append(float(min_share))

    aggregated_mins: dict[tuple[str, str], float] = {}
    if aggregated_constraints:
        for c in aggregated_constraints:
            min_share = getattr(c, "minimum_share", None)
            if min_share is None or min_share <= 0:
                continue
            tech = str(getattr(c, "technology_name", "")).strip()
            pattern = str(getattr(c, "feedstock_pattern", "")).lower().strip()
            if not tech or not pattern:
                continue
            aggregated_mins[(tech, pattern + "*")] = float(min_share)

    def _is_charge_hot(charge_or_pattern: str) -> bool:
        """A charge or wildcard pattern triggers strict radius if any matching name is hot."""
        stripped = charge_or_pattern.rstrip("*")
        for hot in config.closely_allocated_products:
            if str(hot).lower().startswith(stripped) or stripped.startswith(str(hot).lower()):
                return True
        return False

    if feedstock_mins or aggregated_mins:
        logger.info("[DISAGGREGATION] Feedstock minimum-share constraints (trigger strict-radius for hot commodities):")
        for tech, charge in sorted(feedstock_mins.keys()):
            shares = feedstock_mins[(tech, charge)]
            lo, hi = min(shares), max(shares)
            hot_tag = " [hot commodity → strict radius]" if _is_charge_hot(charge) else ""
            rng = f"{lo:.1%}" if lo == hi else f"{lo:.1%}–{hi:.1%}"
            logger.info(f"[DISAGGREGATION]   {tech} requires min {rng} {charge}{hot_tag} (per-feedstock)")
        for tech, pattern in sorted(aggregated_mins.keys()):
            share = aggregated_mins[(tech, pattern)]
            hot_tag = " [hot commodity → strict radius]" if _is_charge_hot(pattern) else ""
            logger.info(f"[DISAGGREGATION]   {tech} requires min {share:.1%} {pattern}{hot_tag} (aggregated wildcard)")
    else:
        logger.info("[DISAGGREGATION] No feedstock minimum-share constraints detected across clusters")

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

    # Compute per-FG effective shares for each cluster from case-4 outgoing flows.
    # For clusters whose members span more than hot_metal_radius (geographically split
    # clusters), these differ from capacity_shares — each FG's share reflects the demand
    # it can physically reach. We then use these effective shares for ALL flows through
    # the cluster (incoming raw materials, outgoing hot/cold) so per-FG BOM balance
    # holds. Clusters without outgoing case-4 flows keep their capacity_shares.
    effective_shares_by_cluster = _compute_effective_shares_by_cluster(
        meta_furnace_groups=meta_furnace_groups,
        case4_allocs=case4_allocs,
        meta_fg_by_id=meta_fg_by_id,
        plants_repo=plants_repo,
        config=config,
        aggregated_constraints=aggregated_constraints,
        case2_batches=case2_batches,
    )
    # Log any cluster where effective shares diverge from capacity shares (i.e. the
    # cluster is geographically split — the reach-based split is doing real work here).
    diverged = 0
    for mfg in meta_furnace_groups:
        cid = mfg.meta_furnace_group_id
        eff = effective_shares_by_cluster[cid]
        cap = mfg.capacity_shares
        max_delta = max((abs(eff[k] - cap[k]) for k in cap), default=0.0)
        if max_delta > 0.01:  # >1 pp divergence worth mentioning
            diverged += 1
            logger.info(
                f"[DISAGGREGATION] Effective shares diverge from capacity shares in {cid} "
                f"(max Δ {max_delta:.1%}) — cluster likely split by hot-metal radius"
            )
    if diverged == 0:
        logger.info("[DISAGGREGATION] Effective shares match capacity shares in all clusters")

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

        # Prepare supplies from FGs in meta-cluster — use effective shares so per-FG
        # BOM stays consistent with case-4 outgoing (which may have used reach-based
        # attribution for geographically split clusters).
        source_shares = effective_shares_by_cluster[from_meta_fg.meta_furnace_group_id]
        fg_supplies = {fg_id: total_batch_volume * share for fg_id, share in source_shares.items()}

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

        # Create allocations with commodity substitution.
        # Use effective shares (reach-aware for hot commodities, capacity shares elsewhere)
        # so the per-FG ProcessCenter capacity matches the split used for the flow volumes.
        source_eff_shares = effective_shares_by_cluster[from_meta_fg.meta_furnace_group_id]
        for (fg_id, supplier_name), flow_volume in flows.items():
            fg_pc = create_fg_process_center(
                fg_id,
                from_meta_fg,
                source_eff_shares[fg_id],
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

        # Filter out FGs without valid BOMs from destination — use EFFECTIVE shares so
        # incoming raw materials are attributed per-FG in the same proportion as the
        # cluster's outgoing hot/cold output (preserves per-FG BOM balance).
        dest_shares = effective_shares_by_cluster[to_meta_fg.meta_furnace_group_id]
        valid_fg_shares = {}
        total_valid_share = 0.0

        for fg_id, share in dest_shares.items():
            if _validate_fg_can_receive_allocation(fg_id, plants_repo):
                valid_fg_shares[fg_id] = share
                total_valid_share += share

        if not valid_fg_shares:
            logger.error(f"[DISAGGREGATION] Case 3: No valid FGs with BOMs in {to_meta_name}, skipping batch")
            continue

        # Renormalize shares if some FGs were filtered out
        if total_valid_share < 0.99:
            logger.warning(
                f"[DISAGGREGATION] Case 3: Filtered out {len(dest_shares) - len(valid_fg_shares)} "
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

        # Create allocations with commodity substitution.
        # Use effective shares so the per-FG ProcessCenter capacity matches the
        # reach-aware split used when sizing the per-FG demand above.
        dest_eff_shares = effective_shares_by_cluster[to_meta_fg.meta_furnace_group_id]
        for (supplier_name, fg_id), flow_volume in flows.items():
            supplier_pc = demand_pcs[supplier_name]
            fg_pc = create_fg_process_center(
                fg_id,
                to_meta_fg,
                dest_eff_shares[fg_id],
                batch[0][1].process,  # Get process from first to_pc
            )

            # Calculate distance and substitute commodity if close enough
            supplier_location = supplier_pc.location
            fg_location = to_meta_fg.constituent_locations[fg_id]
            distance_km = _calculate_distance_km(supplier_location, fg_location)
            substituted_commodity = _substitute_commodity_by_distance(batch[0][2], distance_km, config)

            disaggregated_allocs[(supplier_pc, fg_pc, substituted_commodity)] = flow_volume

    # Case 4: Meta-FG → Meta-FG
    #
    # Strict hot-metal flows (e.g. hot_metal → BOF with ≥70% min-share) are solved as a
    # *joint* transportation problem per destination BOF cluster: all contributing BF
    # clusters are merged into one supply side so the solver can freely route each BF FG's
    # output to the nearest BOF FG.  This guarantees each BOF FG receives exactly
    # effective_share × total_LP_hot_metal — the amount its effective capacity entitles it
    # to — regardless of how many BF clusters contributed.
    #
    # Everything else (cold commodities, non-strict hot) is solved per-flow as before.

    # Separate flows into joint (strict hot) and individual (everything else).
    case4_joint_groups: dict[tuple[str, str], list] = {}  # (to_id, commodity_name) → flows
    case4_individual: list = []

    for from_pc, to_pc, commodity, volume in case4_allocs:
        to_mfg = meta_fg_by_id[to_pc.name]
        is_hot = commodity.name in config.closely_allocated_products
        strict = is_hot and _destination_has_min_constraint_for_commodity(to_mfg, commodity, aggregated_constraints)
        if strict:
            key = (to_pc.name, commodity.name)
            case4_joint_groups.setdefault(key, []).append((from_pc, to_pc, commodity, volume))
        else:
            case4_individual.append((from_pc, to_pc, commodity, volume))

    logger.info(
        f"[DISAGGREGATION] Case 4: {len(case4_joint_groups)} joint group(s) "
        f"(strict hot metal), {len(case4_individual)} individual flow(s)"
    )

    # --- Joint groups: all BF clusters → one BOF cluster, single transportation problem ---
    for (to_meta_fg_id, commodity_name), group_flows in case4_joint_groups.items():
        to_meta_fg = meta_fg_by_id[to_meta_fg_id]
        commodity = group_flows[0][2]
        total_lp_volume = sum(v for _, _, _, v in group_flows)
        n_sources = len(group_flows)

        context_label = (
            f"{commodity_name} → {to_meta_fg_id} ({to_meta_fg.technology_name}, joint {n_sources} source cluster(s))"
        )

        logger.info(
            f"[DISAGGREGATION] Case 4 joint: {n_sources} source cluster(s) → "
            f"{to_meta_fg_id} ({commodity_name}), total {total_lp_volume:.1f}t"
        )

        # Supply side (initial): each source BF FG gets LP_volume × cap_share.
        # Multiple flows from different BF clusters are simply accumulated.
        # This gives an initial absolute supply per BF FG; it will be redistributed
        # reach-based below to ensure pocket-balanced supplies for _solve_strict_by_components.
        initial_source_supplies: dict[str, float] = {}
        joint_source_locations: dict[str, Location] = {}
        fg_to_from_meta: dict[str, tuple[str, "ProcessCenter"]] = {}

        for from_pc, _, _, vol in group_flows:
            from_mfg = meta_fg_by_id[from_pc.name]
            for fg_id, cap_share in from_mfg.capacity_shares.items():
                fg_supply = vol * cap_share
                initial_source_supplies[fg_id] = initial_source_supplies.get(fg_id, 0.0) + fg_supply
                joint_source_locations[fg_id] = from_mfg.constituent_locations[fg_id]
                fg_to_from_meta[fg_id] = (from_pc.name, from_pc)

        # Demand side: each BOF FG demands effective_share × total LP volume.
        to_eff_shares = effective_shares_by_cluster[to_meta_fg_id]
        valid_dest_fgs = {
            fg_id: share
            for fg_id, share in to_eff_shares.items()
            if _validate_fg_can_receive_allocation(fg_id, plants_repo)
        }
        total_valid = sum(valid_dest_fgs.values())
        if not valid_dest_fgs or total_valid <= 0:
            logger.error(f"[DISAGGREGATION] Case 4 joint: no valid dest FGs in {to_meta_fg_id}, skipping")
            continue
        if total_valid < 0.99:
            logger.warning(
                f"[DISAGGREGATION] Case 4 joint: renormalising dest shares in {to_meta_fg_id} "
                f"(filtered {len(to_eff_shares) - len(valid_dest_fgs)} FG(s))"
            )
            valid_dest_fgs = {fg_id: s / total_valid for fg_id, s in valid_dest_fgs.items()}

        joint_dest_demands = {fg_id: total_lp_volume * share for fg_id, share in valid_dest_fgs.items()}
        joint_dest_locations = {fg_id: to_meta_fg.constituent_locations[fg_id] for fg_id in valid_dest_fgs}

        # Pre-filter BOF FGs that have no reachable BF FG in the contributing source cluster(s).
        # This happens when a BOF FG's neighbouring BF uses a different reductant (different
        # cluster key) and the LP routed hot metal only from a different BF cluster, so those
        # BF FGs are absent from joint_source_locations.  The removed FG's demand is
        # redistributed to the remaining FGs proportionally to their available headroom
        # (effective capacity − already-allocated demand), so total LP volume is preserved
        # and no FG is pushed above its effective capacity.
        unreachable_dest_fgs: set[str] = set()
        for fg_id, dloc in joint_dest_locations.items():
            if dloc is None:
                continue  # no location data — treated as reachable downstream
            if not any(
                sloc is not None and _calculate_distance_km(sloc, dloc) <= config.hot_metal_radius
                for sloc in joint_source_locations.values()
            ):
                unreachable_dest_fgs.add(fg_id)

        if unreachable_dest_fgs:
            logger.warning(
                f"[DISAGGREGATION] Case 4 joint: {len(unreachable_dest_fgs)} BOF FG(s) in "
                f"{to_meta_fg_id} have no reachable BF FG in the contributing source cluster(s) "
                f"(neighbouring BF likely uses a different reductant). Excluded; demand "
                f"redistributed to reachable FGs. FG(s): "
                f"{sorted(unreachable_dest_fgs)[:5]}" + ("..." if len(unreachable_dest_fgs) > 5 else "")
            )
            joint_dest_demands = {
                fg_id: v for fg_id, v in joint_dest_demands.items() if fg_id not in unreachable_dest_fgs
            }
            joint_dest_locations = {
                fg_id: v for fg_id, v in joint_dest_locations.items() if fg_id not in unreachable_dest_fgs
            }
            if not joint_dest_demands:
                logger.error(
                    f"[DISAGGREGATION] Case 4 joint: all dest FGs in {to_meta_fg_id} are "
                    f"unreachable from the source cluster — skipping"
                )
                continue

            # Redistribute removed demand, capped by each remaining FG's effective capacity.
            # Effective capacity per FG = capacity_share × cluster total_capacity.
            overflow = total_lp_volume - sum(joint_dest_demands.values())
            if overflow > 1.0:
                fg_eff_caps = {
                    fg_id: to_meta_fg.capacity_shares.get(fg_id, 0.0) * to_meta_fg.total_capacity
                    for fg_id in joint_dest_demands
                }
                fg_headroom = {
                    fg_id: max(0.0, fg_eff_caps[fg_id] - joint_dest_demands[fg_id]) for fg_id in joint_dest_demands
                }
                total_headroom = sum(fg_headroom.values())
                if total_headroom >= overflow:
                    for fg_id in joint_dest_demands:
                        joint_dest_demands[fg_id] += overflow * (fg_headroom[fg_id] / total_headroom)
                else:
                    # Remaining FGs collectively at capacity — fill them up and log shortfall
                    for fg_id in joint_dest_demands:
                        joint_dest_demands[fg_id] = fg_eff_caps[fg_id]
                    unabsorbed = overflow - total_headroom
                    logger.warning(
                        f"[DISAGGREGATION] Case 4 joint: {unabsorbed:.1f}t of hot metal in "
                        f"{to_meta_fg_id} cannot be absorbed (remaining FGs at effective capacity). "
                        f"LP cluster total is not fully preserved for this group."
                    )

        # Redistribute initial supplies reach-based so that every geographic pocket is
        # self-balancing (pocket_supply == pocket_demand).  This makes the per-component
        # normalisation in _solve_strict_by_components a no-op (scale = 1) and ensures
        # BF FG actual hot-metal outputs are consistent with their iron-ore BOM inputs.
        joint_source_supplies = _reach_based_joint_supplies(
            source_fg_supplies=initial_source_supplies,
            source_locations=joint_source_locations,
            dest_demands=joint_dest_demands,
            dest_locations=joint_dest_locations,
            hot_metal_radius=config.hot_metal_radius,
            strict_radius=True,
            context_label=context_label,
        )

        # Allocation costs: weighted-average production cost across all source clusters.
        joint_allocation_costs = None
        if transport_cost_lookup is not None and wtp_lookup is not None:
            avg_prod_cost = (
                sum(meta_fg_by_id[fp.name].weighted_avg_carbon_cost * v for fp, _, _, v in group_flows)
                / total_lp_volume
            )
            source_prod_costs = {fg_id: avg_prod_cost for fg_id in joint_source_supplies}
            joint_allocation_costs = _compute_allocation_costs(
                source_ids=list(joint_source_supplies.keys()),
                dest_ids=list(joint_dest_demands.keys()),
                source_locations=joint_source_locations,
                dest_locations=joint_dest_locations,
                source_production_costs=source_prod_costs,
                commodity_name=commodity_name.lower(),
                transport_cost_lookup=transport_cost_lookup,
                wtp_lookup=wtp_lookup,
            )

        # Solve joint strict-radius transportation problem.
        # _solve_strict_by_components decomposes geographically isolated pockets so
        # integer-flooring artefacts stay local and cross-pocket infeasibility is avoided.
        joint_flows, stats = _solve_strict_by_components(
            source_supplies=joint_source_supplies,
            dest_demands=joint_dest_demands,
            source_locations=joint_source_locations,
            dest_locations=joint_dest_locations,
            commodity=commodity,
            config=config,
            allocation_costs=joint_allocation_costs,
            context_label=context_label,
        )

        # Track statistics
        total_potential_edges += stats.get("total_pairs", 0)
        total_used_edges += stats.get("used_edges", 0)
        transportation_stats[(f"joint_{commodity_name}", to_meta_fg_id, "")] = stats

        # Build allocations from joint flows.
        from_eff_shares_cache: dict[str, dict[str, float]] = {}
        to_eff_shares_full = effective_shares_by_cluster[to_meta_fg_id]

        for (from_fg_id, to_fg_id), flow_volume in joint_flows.items():
            from_meta_id, from_pc = fg_to_from_meta[from_fg_id]
            from_mfg = meta_fg_by_id[from_meta_id]
            if from_meta_id not in from_eff_shares_cache:
                from_eff_shares_cache[from_meta_id] = effective_shares_by_cluster[from_meta_id]
            from_eff_share = from_eff_shares_cache[from_meta_id].get(from_fg_id, 0.0)
            to_eff_share = to_eff_shares_full.get(to_fg_id, 0.0)

            from_fg_pc = create_fg_process_center(from_fg_id, from_mfg, from_eff_share, from_pc.process)
            to_fg_pc = create_fg_process_center(to_fg_id, to_meta_fg, to_eff_share, group_flows[0][1].process)

            from_loc = from_mfg.constituent_locations[from_fg_id]
            to_loc = to_meta_fg.constituent_locations[to_fg_id]
            distance_km = _calculate_distance_km(from_loc, to_loc)
            substituted_commodity = _substitute_commodity_by_distance(commodity, distance_km, config)

            disaggregated_allocs[(from_fg_pc, to_fg_pc, substituted_commodity)] = flow_volume

    # --- Individual flows: cold commodities and non-strict hot ---
    for from_pc, to_pc, commodity, volume in case4_individual:
        from_meta_fg = meta_fg_by_id[from_pc.name]
        to_meta_fg = meta_fg_by_id[to_pc.name]

        flows, stats = _solve_transportation_problem(
            from_meta_fg=from_meta_fg,
            to_meta_fg=to_meta_fg,
            total_volume=volume,
            commodity=commodity,
            config=config,
            plants_repo=plants_repo,
            transport_cost_lookup=transport_cost_lookup,
            wtp_lookup=wtp_lookup,
            aggregated_constraints=aggregated_constraints,
        )

        # Track statistics
        total_potential_edges += stats["total_pairs"]
        total_used_edges += stats["used_edges"]
        transportation_stats[(from_pc.name, to_pc.name, str(commodity))] = stats

        from_eff_shares = effective_shares_by_cluster[from_meta_fg.meta_furnace_group_id]
        to_eff_shares = effective_shares_by_cluster[to_meta_fg.meta_furnace_group_id]
        for (from_fg_id, to_fg_id), flow_volume in flows.items():
            from_fg_pc = create_fg_process_center(
                from_fg_id, from_meta_fg, from_eff_shares[from_fg_id], from_pc.process
            )
            to_fg_pc = create_fg_process_center(to_fg_id, to_meta_fg, to_eff_shares[to_fg_id], to_pc.process)

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
            logging.error(
                f"[DISAGGREGATION] {commodity_name}: Input={input_total:.2f}t, Output={output_total:.2f}t, "
                f"Discrepancy={discrepancy:+.2f}t ({discrepancy_pct:+.3f}%)"
            )
        else:
            logging.info(
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
        for (*_, commodity_raw), stats in transportation_stats.items():
            commodity_str = commodity_raw.value if hasattr(commodity_raw, "value") else str(commodity_raw)
            if commodity_str not in commodity_stats:
                commodity_stats[commodity_str] = {
                    "total_flows": 0,
                    "total_pairs": 0,
                    "total_used_edges": 0,
                    "total_infeasible": 0,
                    "total_infeasible_flow_volume": 0.0,
                    "total_flow_volume": 0.0,
                    "infeasible_edge_details": [],  # list of (distance_km, volume, src_iso3, dst_iso3)
                }
            commodity_stats[commodity_str]["total_flows"] += 1
            commodity_stats[commodity_str]["total_pairs"] += stats["total_pairs"]
            commodity_stats[commodity_str]["total_used_edges"] += stats["used_edges"]
            commodity_stats[commodity_str]["total_infeasible"] += stats["infeasible_pairs"]
            commodity_stats[commodity_str]["total_infeasible_flow_volume"] += stats.get("infeasible_flow_volume", 0.0)
            commodity_stats[commodity_str]["total_flow_volume"] += stats.get("total_flow_volume", 0.0)
            commodity_stats[commodity_str]["infeasible_edge_details"] += stats.get("infeasible_edge_details", [])

        # Log per-commodity edge-reduction statistics
        for commodity_str, stats in sorted(commodity_stats.items()):
            reduction_pct = (
                (1 - stats["total_used_edges"] / stats["total_pairs"]) * 100 if stats["total_pairs"] > 0 else 0
            )
            logger.info(
                f"[DISAGGREGATION]   {commodity_str}: {stats['total_flows']} inter-cluster flows, "
                f"{stats['total_pairs']} potential edges → {stats['total_used_edges']} used "
                f"({reduction_pct:.1f}% reduction)"
            )

        # Log hot-metal radius summary. Distinguish three cases per commodity:
        #   1. TRUE violation: hot commodity routed beyond radius AND cannot be relabeled
        #      (no cold equivalent → truly breaks the physical model).
        #   2. SUBSTITUTION: hot commodity routed beyond radius but has a cold equivalent —
        #      the output is relabeled to the cold commodity, so no physical violation.
        #   3. NO violations: either no infeasible pairs, or all pairs respected.
        logger.info("[DISAGGREGATION] === Hot-Metal Radius Summary ===")
        any_violations = False
        for commodity_str, stats in sorted(commodity_stats.items()):
            infeasible_vol = stats["total_infeasible_flow_volume"]
            total_vol = stats["total_flow_volume"]
            details: list[tuple[float, float, str, str]] = stats["infeasible_edge_details"]
            will_be_substituted = commodity_str.lower() in HOT_TO_COLD_COMMODITY
            if infeasible_vol > 1e-6:
                pct = (infeasible_vol / total_vol * 100) if total_vol > 0 else 0.0
                distances = [d for d, _, _, _ in details]
                avg_dist = sum(distances) / len(distances)
                # Top-3 destination countries by affected volume
                dest_volumes: dict[str, float] = {}
                for _, vol, _, dst_iso3 in details:
                    dest_volumes[dst_iso3] = dest_volumes.get(dst_iso3, 0.0) + vol
                top3 = sorted(dest_volumes.items(), key=lambda x: x[1], reverse=True)[:3]
                top3_str = ", ".join(f"{iso3} ({v:.1f}t)" for iso3, v in top3)

                if will_be_substituted:
                    cold_name = HOT_TO_COLD_COMMODITY[commodity_str.lower()]
                    logger.info(
                        f"[HOT_METAL_RADIUS]   {commodity_str} → {cold_name} (relabeled): "
                        f"{infeasible_vol:.2f}t ({pct:.1f}% of {total_vol:.2f}t) exceeded radius | "
                        f"distance min/avg/max: {min(distances):.0f}/{avg_dist:.0f}/{max(distances):.0f} km"
                    )
                    logger.info(f"[HOT_METAL_RADIUS]     top destinations: {top3_str}")
                else:
                    any_violations = True
                    logger.warning(
                        f"[HOT_METAL_RADIUS]   {commodity_str}: {infeasible_vol:.2f}t violated "
                        f"({pct:.1f}% of {total_vol:.2f}t total flow) | "
                        f"distance min/avg/max: {min(distances):.0f}/{avg_dist:.0f}/{max(distances):.0f} km"
                    )
                    logger.warning(f"[HOT_METAL_RADIUS]     top destinations: {top3_str}")
            elif stats["total_infeasible"] > 0:
                # Radius constraint was active (some pairs were blocked) but all flow respected it
                logger.info(
                    f"[HOT_METAL_RADIUS]   {commodity_str}: no violations "
                    f"({total_vol:.2f}t total flow, {stats['total_infeasible']} pair(s) correctly blocked)"
                )
        if not any_violations:
            logger.info("[HOT_METAL_RADIUS] No unresolved hot-metal radius violations this year")

    logger.info("[DISAGGREGATION] Disaggregation complete")

    return result
