# Transportation Problem Cost Computation Plan

## Problem
The transportation problem currently uses simple distance-based costs (`distance_km * 100`). We want it to use the same cost structure as the main LP to make disaggregation decisions that align with the LP's economic logic.

## LP Cost Structure (from `add_allocation_costs_as_parameters_to_lp`)
```python
allocation_cost = (
    transportation_cost        # iso3→iso3→commodity lookup (from TransportKPI)
    + bom_energy_costs        # Energy cost at destination FG
    + tariff_tax              # Trade tariffs between countries
    + production_cost         # Carbon/production cost at source FG
    - willingness_to_pay      # Discount for high-value destinations
)
```

## Available Data Sources

### In `disaggregate_allocations()`:
- `clustered_allocations` - LP results with ProcessCenters
- `meta_furnace_groups` - Contains `weighted_avg_carbon_cost`, constituent locations
- `plants_repo` - Access to FurnaceGroups with `carbon_cost_per_unit`
- `config` - SimulationConfig object

### Need to add (from `bus.env`):
- `transport_kpis: list[TransportKPI]` - Has `(reporter_iso, partner_iso, commodity) → transportation_cost`
- `active_trade_tariffs` - Tariff data (if needed)
- `willingness_to_pay` - WTP data

## Implementation Plan

### Phase 1: Pass environment data to disaggregation
1. Update `disaggregate_allocations()` signature to accept `env` or specific data:
   ```python
   def disaggregate_allocations(
       clustered_allocations: "Allocations",
       meta_furnace_groups: list[MetaFurnaceGroup],
       plants_repo: "PlantInMemoryRepository",
       config: "SimulationConfig",
       transport_kpis: list["TransportKPI"] | None = None,
       willingness_to_pay: list["WillingnessToPay"] | None = None,
   ) -> "Allocations":
   ```

2. Update call site in `plant_agent.py` to pass these:
   ```python
   disaggregated_allocations = disaggregate_allocations(
       clustered_allocations=trade_lp_allocations,
       meta_furnace_groups=bus.env.meta_furnace_groups,
       plants_repo=bus.uow.plants,
       config=bus.env.config,
       transport_kpis=bus.env.transport_kpis,  # NEW
       willingness_to_pay=bus.env.willingness_to_pay,  # NEW
   )
   ```

### Phase 2: Build cost lookup helpers
Create helper function to build transportation cost lookup:
```python
def _build_transport_cost_lookup(
    transport_kpis: list[TransportKPI] | None
) -> dict[tuple[str, str, str], float]:
    """Build (from_iso3, to_iso3, commodity) → cost_per_ton lookup."""
    lookup = {}
    if transport_kpis:
        for kpi in transport_kpis:
            key = (kpi.reporter_iso, kpi.partner_iso, kpi.commodity.lower())
            lookup[key] = kpi.transportation_cost
    return lookup
```

Create helper function to build WTP lookup:
```python
def _build_wtp_lookup(
    willingness_to_pay: list[WillingnessToPay] | None
) -> dict[tuple[str, str], float]:
    """Build (iso3, commodity) → wtp_value lookup."""
    lookup = {}
    if willingness_to_pay:
        for wtp in willingness_to_pay:
            key = (wtp.region_or_iso3, wtp.commodity.lower())
            lookup[key] = wtp.value
    return lookup
```

### Phase 3: Compute allocation costs in disaggregate_allocations
Before calling transportation solver, compute costs for each potential edge:
```python
# Build cost lookups once
transport_cost_lookup = _build_transport_cost_lookup(transport_kpis)
wtp_lookup = _build_wtp_lookup(willingness_to_pay)

# For each source-dest pair in transportation problem:
allocation_costs = {}
for source_id in source_supplies:
    source_location = source_locations[source_id]
    source_production_cost = production_costs[source_id]  # from FG or meta-FG

    for dest_id in dest_demands:
        dest_location = dest_locations[dest_id]

        # 1. Transportation cost (iso3 lookup)
        transport_cost = transport_cost_lookup.get(
            (source_location.iso3, dest_location.iso3, commodity_name.lower()),
            0.0
        )

        # 2. Production cost
        production_cost = source_production_cost

        # 3. Willingness to pay (reduces cost)
        wtp = wtp_lookup.get((dest_location.iso3, commodity_name.lower()), 0.0)

        # Total cost (simplified - skip BOM energy and tariffs for now)
        total_cost = transport_cost + production_cost - wtp

        allocation_costs[(source_id, dest_id)] = total_cost
```

### Phase 4: Update transportation solver to use pre-computed costs
```python
def _solve_batched_transportation_problem(
    ...
    allocation_costs: dict[tuple[str, str], float] | None = None,
) -> ...:

    for source_id in source_supplies:
        for dest_id in dest_demands:
            if is_feasible:
                if allocation_costs and (source_id, dest_id) in allocation_costs:
                    # Use pre-computed allocation cost
                    cost = int(allocation_costs[(source_id, dest_id)] * 100)
                else:
                    # Fallback to distance-based
                    cost = int(distance_km * 100)
            else:
                cost = INFEASIBLE_COST
```

### Phase 5: Update all call sites
Update Case 2, 3, 4 to pass allocation_costs when calling the transportation solver.

## Simplifications (for initial implementation)
1. **Skip BOM energy costs** - Requires knowing dest FG's energy consumption patterns
2. **Skip tariffs** - Requires ProcessCenter names not just IDs
3. **Keep these components**:
   - Transportation cost (iso3→iso3)
   - Production cost (from FG)
   - Willingness to pay

These 3 components capture the main economic drivers. BOM energy and tariffs will be included when TM_PAM_connector recalculates full costs anyway.

## Testing Strategy
1. Verify costs match LP structure for a simple 2x2 case
2. Check that transportation problem prefers low-cost routes over short-distance routes when costs differ
3. Validate total disaggregated volumes match clustered volumes

---

## Implementation Summary (Completed)

### Changes Made:

**Phase 1 - Updated signatures to pass environment data:**
- `disaggregate_allocations()` now accepts `transport_kpis` and `willingness_to_pay` parameters
- Updated call site in `plant_agent.py` to pass these from `bus.env.transport_kpis` and `bus.env.willingness_to_pay`

**Phase 2 - Added cost lookup helpers:**
- `_build_transport_cost_lookup()` - Builds (iso3, iso3, commodity) → cost_per_ton lookup from TransportKPI list
- `_build_wtp_lookup()` - Builds (iso3, commodity) → wtp_value lookup from WillingnessToPay list
- Both normalize commodity names to lowercase for consistent lookups

**Phase 3 - Created cost computation function:**
- `_compute_allocation_costs()` - Computes allocation costs for source-destination pairs using LP structure:
  ```python
  cost = transport_cost + production_cost - willingness_to_pay
  ```
- Takes source/dest IDs, locations, production costs, and cost lookups
- Returns dict mapping (source_id, dest_id) → allocation cost ($/ton)
- Note: Skips BOM energy costs and tariffs as they require ProcessCenter names (not just IDs)
  - These components will be added when TM_PAM_connector recalculates full costs

**Phase 4 - Updated transportation solvers:**
- `_solve_batched_transportation_problem()` now accepts optional `allocation_costs` parameter
- When provided, uses pre-computed costs: `cost = int(max(allocation_costs[(source_id, dest_id)], 0.0) * 100)`
- Falls back to distance-based cost if allocation_costs not provided: `cost = int(distance_km * 100)`
- `_solve_transportation_problem()` (Case 4 wrapper) accepts cost lookups and computes allocation costs before calling batched solver

**Phase 5 - Wired costs into all disaggregation cases:**

- **Case 2 (Meta-FG → Suppliers)**:
  - Uses `meta_fg.weighted_avg_carbon_cost` as production cost for all FGs in cluster
  - Computes allocation costs before solving transportation problem

- **Case 3 (Suppliers → Meta-FG)**:
  - Uses `supplier_pc.production_cost` for each supplier
  - Computes allocation costs before solving transportation problem

- **Case 4 (Meta-FG → Meta-FG)**:
  - Passes cost lookups to `_solve_transportation_problem()` wrapper
  - Wrapper computes costs and passes to batched solver

### Cost Structure Implemented:
The transportation problem now minimizes:
```
Total Cost = Transportation Cost (iso3→iso3)
           + Production Cost (source FG/supplier)
           - Willingness to Pay (destination)
```

This aligns with the main LP's economic logic (simplified version without BOM energy and tariffs).

### Key Implementation Details:

1. **Cost lookups built once** at start of `disaggregate_allocations()` for efficiency
2. **Commodity names normalized** to lowercase for consistent lookups across all functions
3. **Graceful fallback**: If cost data unavailable, falls back to distance-based costs
4. **Feasibility preserved**: Infeasible flows (e.g., hot metal beyond radius) still get INFEASIBLE_COST regardless of computed costs
5. **Integer scaling**: Costs multiplied by 100 and converted to int for network simplex algorithm

### Result:
The transportation problem now makes disaggregation decisions based on **total economic cost** instead of just geographic distance, ensuring the disaggregated flows are economically rational and consistent with the LP's objective function.
