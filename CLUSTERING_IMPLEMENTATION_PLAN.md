# Furnace Group Clustering Implementation Plan

## Overview
Implement a clustering mechanism to reduce LP complexity by aggregating furnace groups into meta-furnace groups. This document outlines the implementation steps for a **simple clustering approach** based on technology, chosen reductant, and country.

---

## Phase 1: Core Clustering Logic

### 1.1 Create Clustering Key and Meta-Furnace Group Data Structure

**File**: `src/steelo/domain/trade_modelling/furnace_group_clustering.py` (NEW)

**Tasks**:
- [ ] Define `ClusterKey` dataclass with attributes:
  - `technology_name: str`
  - `chosen_reductant: str`
  - `iso3: str` (country code)

- [ ] Define `MetaFurnaceGroup` dataclass to represent clustered furnace groups:
  ```python
  @dataclass
  class MetaFurnaceGroup:
      cluster_key: ClusterKey
      meta_furnace_group_id: str  # e.g., "cluster_BF_coke_CHN"
      constituent_fg_ids: list[str]  # Original furnace group IDs
      technology_name: str
      chosen_reductant: str
      location: Location  # Capacity-weighted center of gravity
      total_capacity: Volumes
      weighted_avg_carbon_cost: float
      dynamic_business_case: list[PrimaryFeedstock]
      # Mapping for disaggregation
      capacity_shares: dict[str, float]  # fg_id -> share of total cluster capacity
      constituent_locations: dict[str, Location]  # fg_id -> original location (for disaggregation)
  ```

### 1.2 Implement Center of Gravity Calculation

**File**: `src/steelo/domain/trade_modelling/furnace_group_clustering.py`

**Function**: `calculate_center_of_gravity(furnace_groups: list[FurnaceGroup]) -> Location`

**Logic**:
```python
def calculate_center_of_gravity(furnace_groups: list[FurnaceGroup]) -> Location:
    """Calculate capacity-weighted center of gravity for a cluster of furnace groups.

    Uses weighted average of latitude and longitude, where weights are furnace capacities.

    Args:
        furnace_groups: List of furnace groups in the cluster

    Returns:
        Location: New location at the weighted centroid

    Notes:
        - For small geographic areas (within same country), simple lat/lon averaging is acceptable
        - For large areas spanning multiple time zones, consider using proper spherical geometry
        - Preserves iso3, country, and region from the cluster (should be identical for all FGs)
    """
    total_capacity = sum(float(fg.capacity) for fg in furnace_groups)

    if total_capacity == 0:
        # Fall back to simple average if all capacities are zero
        avg_lat = sum(fg.plant.location.lat for fg in furnace_groups) / len(furnace_groups)
        avg_lon = sum(fg.plant.location.lon for fg in furnace_groups) / len(furnace_groups)
    else:
        # Capacity-weighted average
        avg_lat = sum(
            fg.plant.location.lat * float(fg.capacity)
            for fg in furnace_groups
        ) / total_capacity

        avg_lon = sum(
            fg.plant.location.lon * float(fg.capacity)
            for fg in furnace_groups
        ) / total_capacity

    # Use the first furnace group's location metadata (iso3, country, region)
    # These should be identical for all FGs in the cluster
    reference_location = furnace_groups[0].plant.location

    return Location(
        lat=avg_lat,
        lon=avg_lon,
        iso3=reference_location.iso3,
        country=reference_location.country,
        region=reference_location.region,
        distance_to_other_iso3=None,  # Will be recalculated if needed
    )
```

**Edge Cases**:
- All furnace groups at same location → centroid = that location
- Very dispersed furnace groups → centroid may be far from any actual plant
- Zero total capacity → fall back to unweighted average

### 1.3 Implement Clustering Function

**File**: `src/steelo/domain/trade_modelling/furnace_group_clustering.py`

**Function**: `cluster_furnace_groups(plants: list[Plant], config: SimulationConfig) -> tuple[list[MetaFurnaceGroup], dict[str, list[str]]]`

**Logic**:
1. Iterate through all plants and active furnace groups
2. Extract clustering key: `(technology.name, chosen_reductant, plant.location.iso3)`
3. Group furnace groups by cluster key
4. For each cluster:
   - Generate unique `meta_furnace_group_id`
   - Calculate aggregated attributes:
     - `total_capacity = sum(fg.capacity for fg in cluster)`
     - `weighted_avg_carbon_cost = sum(fg.carbon_cost_per_unit * fg.capacity) / total_capacity`
     - `capacity_shares = {fg.id: fg.capacity / total_capacity for fg in cluster}`
     - `constituent_locations = {fg.id: fg.plant.location for fg in cluster}`
   - **Calculate capacity-weighted center of gravity** using `calculate_center_of_gravity()`
   - Verify all FGs in cluster have identical `dynamic_business_case` structure
   - Store constituent FG IDs for disaggregation

**Returns**:
- `list[MetaFurnaceGroup]`: List of clustered meta furnace groups
- `dict[str, list[str]]`: Mapping from meta_fg_id to list of constituent fg_ids

**Edge Cases**:
- Single furnace group per cluster → still create meta-FG for consistency (location = original location)
- Missing `chosen_reductant` → use default value or skip
- Mismatched `dynamic_business_case` within cluster → log warning, use first FG's business case

---

## Phase 2: Adapt Trade LP Setup

### 2.1 Refactor `set_up_steel_trade_lp` to Accept Flexible Furnace Group List

**File**: `src/steelo/domain/trade_modelling/set_up_steel_trade_lp.py`

**Changes**:

1. **Modify function signature**:
   ```python
   def set_up_steel_trade_lp(
       message_bus: MessageBus,
       year: Year,
       config: SimulationConfig,
       legal_process_connectors: list[LegalProcessConnector],
       active_trade_tariffs: list[TradeTariff] | None = None,
       secondary_feedstock_constraints: dict[str, dict[tuple[str, ...], float]] | None = None,
       aggregated_metallic_charge_constraints: list[AggregatedMetallicChargeConstraint] | None = None,
       transport_kpis: list[TransportKPI] | None = None,
       furnace_groups_override: list[MetaFurnaceGroup] | None = None,  # NEW PARAMETER
   ) -> tlp.TradeLPModel:
   ```

2. **Modify `add_furnace_groups_as_process_centers` function**:
   ```python
   def add_furnace_groups_as_process_centers(
       furnace_groups: list[FurnaceGroup | MetaFurnaceGroup],  # Accept both types
       lp_model: tlp.TradeLPModel,
       config: SimulationConfig
   ):
   ```

   **Logic updates**:
   - If `furnace_groups_override` is provided, use it instead of extracting from repository
   - Create `ProcessCenter` objects from `MetaFurnaceGroup` objects:
     - `name = meta_fg.meta_furnace_group_id`
     - `capacity = config.capacity_limit * meta_fg.total_capacity`
     - `location = meta_fg.location`  # This is now the capacity-weighted centroid
     - `production_cost = meta_fg.weighted_avg_carbon_cost`
   - Handle `MetaFurnaceGroup.dynamic_business_case` when creating Process objects

3. **Update main function logic**:
   ```python
   if furnace_groups_override is not None:
       # Use provided meta-furnace groups
       add_furnace_groups_as_process_centers(
           furnace_groups=furnace_groups_override,
           lp_model=lp_model,
           config=config
       )
   else:
       # Original behavior: extract from repository
       add_furnace_groups_as_process_centers(
           furnace_groups=[fg for plant in repository.plants.list() for fg in plant.furnace_groups],
           lp_model=lp_model,
           config=config
       )
   ```

**Tests**:
- [ ] Verify LP solves correctly with meta-furnace groups
- [ ] Verify LP results are consistent with disaggregated model (within tolerance)
- [ ] Verify center of gravity is calculated correctly (visual inspection on map recommended)

---

## Phase 3: Disaggregation Logic

### 3.1 Implement Smart Disaggregation Function

**File**: `src/steelo/domain/trade_modelling/furnace_group_clustering.py`

**Function**: `disaggregate_allocations(clustered_allocations: Allocations, cluster_mapping: dict[str, list[str]], meta_furnace_groups: list[MetaFurnaceGroup], plants_repo: PlantInMemoryRepository) -> Allocations`

**Logic**:

1. **Initialize disaggregated allocations dict**:
   ```python
   disaggregated_allocs: dict[tuple[str, str, str], float] = {}
   ```

2. **For each allocation in clustered_allocations**:
   ```python
   for (from_pc, to_pc, commodity), volume in clustered_allocations.allocations.items():
   ```

3. **Case 1: From meta-FG to demand/supply center**:
   - Extract `meta_fg_id = from_pc.name`
   - Get constituent `fg_ids` from `cluster_mapping[meta_fg_id]`
   - Get `capacity_shares` from corresponding `MetaFurnaceGroup`
   - Disaggregate by capacity share:
     ```python
     for fg_id in constituent_fg_ids:
         share = meta_fg.capacity_shares[fg_id]
         disaggregated_allocs[(fg_id, to_pc.name, commodity)] = volume * share
     ```

4. **Case 2: From supply center to meta-FG**:
   - Extract `meta_fg_id = to_pc.name`
   - **Smart disaggregation by technology/reductant**:
     - If commodity is metallic charge (hot_metal, pig_iron, dri, etc.):
       - Check distance constraints (hot_metal can only travel short distances)
       - Calculate distance from supplier to each constituent FG's **original location** (not centroid)
       - Assign to geographically feasible FGs
     - If commodity is reductant-specific (e.g., coal for coke, natural_gas for DRI):
       - Match to FGs with compatible `chosen_reductant`
     - Otherwise, disaggregate proportionally by capacity share

5. **Case 3: Between two meta-FGs** (iron → steel):
   - Disaggregate both source and destination
   - Calculate distances between **original FG locations** (not centroids)
   - Preserve overall flow ratios while respecting:
     - Distance constraints (hot_metal proximity)
     - Technology compatibility (BF → BOF, DRI → EAF)
   - Algorithm:
     ```python
     for from_fg_id in source_constituent_fgs:
         from_fg = get_furnace_group(from_fg_id)
         from_location = meta_fg_source.constituent_locations[from_fg_id]

         for to_fg_id in dest_constituent_fgs:
             to_fg = get_furnace_group(to_fg_id)
             to_location = meta_fg_dest.constituent_locations[to_fg_id]

             if is_compatible(from_fg.technology, to_fg.technology, commodity):
                 distance = haversine_distance(from_location, to_location)
                 if commodity == "hot_metal" and distance > hot_metal_radius:
                     continue  # Skip, hot metal can't travel this far

                 # Allocate proportionally
                 share = from_fg.capacity * to_fg.capacity / (source_total * dest_total)
                 disaggregated_allocs[(from_fg_id, to_fg_id, commodity)] = volume * share
     ```

**Key Note on Locations**:
- **During LP solve**: Use capacity-weighted centroid for distance calculations (more efficient)
- **During disaggregation**: Use original furnace group locations from `meta_fg.constituent_locations` to ensure accurate distance constraints (especially for hot_metal)

**Returns**:
- `Allocations`: New allocations object with disaggregated flows mapped to individual furnace group IDs

**Edge Cases**:
- Zero-capacity FGs → skip
- Infeasible disaggregation (e.g., hot_metal too far from centroid-based allocation) → redistribute to feasible FGs within cluster
- Unmatched reductants → fall back to proportional allocation

---

## Phase 4: TM-PAM Connector Integration

### 4.1 Update TM_PAM_connector to Handle Disaggregated Allocations

**File**: `src/steelo/domain/trade_modelling/TM_PAM_connector.py`

**Changes**:

1. **Update `__init__` method signature** (if needed):
   - Add optional parameter to signal whether allocations are clustered:
     ```python
     def __init__(
         self,
         dynamic_feedstocks_classes: dict[str, list[PrimaryFeedstock]],
         plants: PlantInMemoryRepository,
         transport_kpis: list[TransportKPI] | None = None,
         cluster_mapping: dict[str, list[str]] | None = None,  # NEW
     ):
     ```

2. **Update allocation processing methods**:
   - Methods that reference `furnace_group_id` should expect individual FG IDs (not meta IDs)
   - Verify `create_graph()` correctly handles disaggregated allocations
   - Ensure `chosen_reductant` lookup works with individual FG IDs

3. **No changes needed if**:
   - Disaggregation happens before passing allocations to TM_PAM_connector
   - All downstream methods already work with individual furnace group IDs

**Validation**:
- [ ] Verify utilization rates are correctly calculated for individual FGs
- [ ] Verify energy costs and reductant choices are preserved
- [ ] Check that trade flow graph is correctly constructed

---

## Phase 5: Integration and Testing

### 5.1 Wire Up Clustering in Allocation Model

**File**: `src/steelo/economic_models/allocation_model.py` (or wherever trade LP is called)

**Changes**:

1. **Add clustering step before LP setup**:
   ```python
   from steelo.domain.trade_modelling.furnace_group_clustering import (
       cluster_furnace_groups,
       disaggregate_allocations
   )

   # Cluster furnace groups
   meta_furnace_groups, cluster_mapping = cluster_furnace_groups(
       plants=bus.uow.plants.list(),
       config=bus.env.config
   )

   logging.info(
       f"[CLUSTERING] Reduced {sum(len(fgs) for fgs in cluster_mapping.values())} "
       f"furnace groups to {len(meta_furnace_groups)} meta-furnace groups"
   )
   ```

2. **Pass meta-FGs to LP setup**:
   ```python
   lp_model = set_up_steel_trade_lp(
       message_bus=bus,
       year=bus.env.year,
       config=bus.env.config,
       legal_process_connectors=bus.env.legal_process_connectors,
       active_trade_tariffs=active_trade_tariffs,
       secondary_feedstock_constraints=secondary_feedstock_constraints,
       aggregated_metallic_charge_constraints=aggregated_metallic_charge_constraints,
       transport_kpis=bus.env.transport_kpis,
       furnace_groups_override=meta_furnace_groups,  # NEW
   )
   ```

3. **Disaggregate allocations after solving**:
   ```python
   clustered_allocations = solve_steel_trade_lp_and_return_commodity_allocations(
       trade_lp=lp_model,
       config=bus.env.config
   )

   # Disaggregate before passing to TM-PAM connector
   disaggregated_allocations = disaggregate_allocations(
       clustered_allocations=clustered_allocations,
       cluster_mapping=cluster_mapping,
       meta_furnace_groups=meta_furnace_groups,
       plants_repo=bus.uow.plants
   )

   # Use disaggregated allocations downstream
   tm_pam_connector.process_allocations(disaggregated_allocations)
   ```

### 5.2 Add Configuration Flag

**File**: `src/steelo/simulation.py`

**Changes**:
```python
@dataclass
class SimulationConfig:
    # ... existing fields ...

    # Clustering configuration
    enable_furnace_group_clustering: bool = False  # Feature flag
    clustering_method: str = "technology_reductant_country"  # Future: support multiple methods
```

### 5.3 Testing Strategy

**Unit Tests**:
- [ ] `test_calculate_center_of_gravity()`: Verify centroid calculation
  - Test with 2 FGs at different locations
  - Test with equal capacities (should be midpoint)
  - Test with unequal capacities (should be weighted)
  - Test with zero capacities (should fall back to average)
  - Test with all FGs at same location (should return that location)

- [ ] `test_cluster_furnace_groups()`: Verify clustering logic
  - Test with homogeneous FGs (all same cluster)
  - Test with heterogeneous FGs (multiple clusters)
  - Test with single FG per cluster
  - Test capacity share calculations
  - Verify centroid is within bounding box of constituent FGs

- [ ] `test_disaggregate_allocations()`: Verify disaggregation logic
  - Test proportional disaggregation
  - Test hot_metal distance constraints using original locations
  - Test reductant matching
  - Test inter-cluster flows (iron → steel)
  - Verify distances are calculated from original locations, not centroid

**Integration Tests**:
- [ ] Compare clustered vs. unclustered LP results:
  - Total production volumes should match (within tolerance)
  - Total costs should be within acceptable range
  - Utilization rates should be reasonable

- [ ] Validate TM-PAM connector integration:
  - Verify all FGs receive allocations
  - Check utilization rates are between 0 and 1
  - Ensure no "lost" material flows

- [ ] Geographic validation:
  - Plot meta-FG centroids on map, verify they make sense
  - Check that hot_metal constraints are respected in disaggregation
  - Verify distances from suppliers to FGs are reasonable

**Performance Tests**:
- [ ] Measure LP solve time reduction
- [ ] Measure memory usage reduction
- [ ] Log clustering statistics (reduction ratio, cluster sizes)

---

## Phase 6: Documentation and Refinement

### 6.1 Add Logging and Diagnostics

**Key Metrics to Log**:
- Number of furnace groups before/after clustering
- Cluster size distribution (min, max, avg, median)
- Geographic dispersion of clusters (avg distance from centroid to constituents)
- LP solve time with/without clustering
- Disaggregation validation (total volume conservation)

**Diagnostic Output**:
```python
logging.info(f"[CLUSTERING] Statistics:")
logging.info(f"  Original FGs: {n_original}")
logging.info(f"  Meta-FGs: {n_clustered}")
logging.info(f"  Reduction: {(1 - n_clustered/n_original)*100:.1f}%")
logging.info(f"  Avg cluster size: {avg_cluster_size:.1f}")
logging.info(f"  Largest cluster: {max_cluster_size} FGs")
logging.info(f"  Avg geographic dispersion: {avg_dispersion:.1f} km")
logging.info(f"  LP solve time: {solve_time:.2f}s")
```

### 6.2 Update Documentation

**Files to Update**:
- [ ] `claude.md`: Add section on clustering logic and center of gravity calculation
- [ ] `README.md`: Document new configuration flags
- [ ] Code docstrings: Add detailed explanations to all new functions

### 6.3 Future Enhancements (Out of Scope for Phase 1)

**Potential Improvements**:
1. **Geographic sub-clustering**: Cluster by region/grid cell within countries for hot_metal
2. **Carbon cost binning**: Add carbon cost bins to clustering key
3. **Dynamic clustering**: Adjust clusters based on LP complexity (adaptive)
4. **Hierarchical clustering**: Multi-level clusters (country → region → plant)
5. **Validation mode**: Run both clustered and unclustered, compare results
6. **Proper spherical geometry**: Use great circle distances for centroid calculation over large areas

---

## Implementation Order (Recommended)

1. **Week 1**: Phase 1 - Core clustering logic, center of gravity calculation, and data structures
2. **Week 2**: Phase 2 - Adapt trade LP setup to accept meta-FGs
3. **Week 3**: Phase 3 - Implement disaggregation logic with original location tracking
4. **Week 4**: Phase 4 - TM-PAM connector integration
5. **Week 5**: Phase 5 - End-to-end integration and testing
6. **Week 6**: Phase 6 - Documentation, refinement, performance validation

---

## Success Criteria

✅ **Functional**:
- LP solves successfully with clustered furnace groups at capacity-weighted centroids
- Disaggregated allocations use original locations for distance checks
- Disaggregated allocations sum to correct totals (within LP tolerance)
- All furnace groups receive valid allocations in TM-PAM connector
- Hot metal distance constraints are respected in disaggregation
- Simulation runs end-to-end without errors

✅ **Performance**:
- LP solve time reduced by at least 30%
- Memory usage reduced by at least 20%
- Results differ from unclustered baseline by less than 5%

✅ **Code Quality**:
- All functions have comprehensive docstrings
- Unit test coverage > 80%
- Integration tests pass
- Code follows existing project patterns

---

## Risk Mitigation

| Risk | Mitigation Strategy |
|------|---------------------|
| Centroid too far from any actual plant | Log max distance from centroid to constituents, flag outliers |
| Disaggregation produces infeasible flows | Add validation checks, fall back to proportional allocation |
| Hot metal constraints violated after disaggregation | Implement strict distance checking using original locations in disaggregation logic |
| Performance gains insufficient | Profile code, optimize clustering algorithm, consider caching |
| Results diverge significantly from baseline | Add diagnostic mode to compare results, tune disaggregation weights |
| Integration breaks existing functionality | Feature flag to disable clustering, comprehensive regression tests |
| Centroid-based LP distances don't reflect reality | Acceptable approximation; disaggregation uses original locations for accuracy |

---

## Notes

- **Chosen reductant**: Verify this attribute exists and is consistently populated across all furnace groups
- **Dynamic business case**: Ensure all FGs in a cluster have compatible business cases (same feedstock options)
- **Location representation**: Meta-FG location is **capacity-weighted center of gravity** for LP efficiency
- **Disaggregation accuracy**: Uses **original FG locations** from `constituent_locations` for precise distance checks
- **Soft minimum capacity**: May need adjustment at cluster level to avoid over-constraining LP
- **Geographic validation**: Recommend visual inspection of centroids on a map during testing
