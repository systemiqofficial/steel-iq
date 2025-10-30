# TM-PAM Connector Documentation

## Purpose

The `TM_PAM_connector` bridges the Trade Module (TM) and Plant Agent Model (PAM) by translating trade optimization results into operational parameters for individual furnace groups. It propagates costs through supply chains and updates furnace group attributes (utilization rates, bill of materials, emissions) based on actual trade flows.

**Location**: `src/steelo/domain/trade_modelling/TM_PAM_connector.py`

---

## Role in Simulation

The Trade Module solves an LP optimization to determine:
- Which furnace groups should produce how much
- Where products should be shipped
- Optimal allocation of feedstocks (scrap, DRI, iron ore, etc.)

The TM-PAM Connector then:
1. **Converts optimization results** → NetworkX graph representing the supply chain
2. **Propagates costs** from raw materials through processing stages to final products
3. **Updates furnace groups** with their allocated production volumes, material costs, and emissions

**When it runs**: After every Trade Module optimization, before PAM makes strategic decisions.

---

## Key Concepts

### 1. Supply Chain as a Directed Graph

The connector models the entire steel production supply chain as a **NetworkX MultiDiGraph**:

```
[Iron Ore] ──→ [BF Furnace] ──→ [BOF Furnace] ──→ [Demand]
                    

[Scrap] ──────────→ [EAF Furnace] ──→ [Demand]
```

- **Nodes**: Process centers (furnace groups, supply sources, demand sinks)
- **Edges**: Material flows with volumes, costs, and efficiencies

### 2. Cost Propagation

Costs accumulate as materials move through the supply chain:

```
Raw Material Cost
    + Transport Cost (from supplier to furnace)
    + Processing Energy Cost (electricity, gas, etc.)
    ↓
Intermediate Product Cost
    + Transport Cost (from intermediate to final furnace)
    + Processing Energy Cost
    ↓
Final Product Cost
```

**Algorithm**: Breadth-first traversal starting from raw material sources (iron ore, scrap), accumulating costs at each processing stage.

### 3. Allocations vs Volumes

- **Volume**: Amount of material shipped/produced (output quantity)
- **Allocation**: Amount of material required as input (accounts for process inefficiency)
- **Formula**: `Allocation = Volume / Process Efficiency`

Example: If EAF has 95% yield, producing 100t steel requires 100/0.95 = 105.3t of scrap input.

---

## Main Workflow

### Stage 1: Graph Construction

**Method**: `create_graph(solved_trade_allocations)`

1. Extracts all non-zero allocations from LP solution: `(from_pc, to_pc, commodity) → volume`
2. Creates nodes for each process center (furnace group)
3. Creates edges for each material flow, storing:
   - `volume`: Shipped quantity
   - `transport_cost`: Cost to move material between locations
   - `processing_energy_cost`: Energy cost at source (electricity, gas, etc.)
   - `process`: Process identifier (e.g., "eaf_scrap_electricity")
   - `process_efficiency`: Yield/conversion rate
   - `output`: Primary product of this process

**Result**: `self.G` populated with nodes and edges representing the trade network.

---

### Stage 2: Input Allocation Calculation

**Method**: `calculate_allocations_for_graph()`

Converts output volumes to input requirements using process efficiencies:

```python
allocation = edge['volume'] / edge['process_efficiency']
```

For each edge, updates the `allocations` attribute with the computed input requirement.

**Result**: Each edge now has both `volume` (output) and `allocations` (input) attributes.

---

### Stage 3: Cost Propagation

**Method**: `propage_cost_forward_by_layers_and_normalize()`

Propagates costs forward through the graph using breadth-first search:

1. **Identify roots**: Nodes with no incoming edges (raw material sources)
2. **BFS traversal**: Process nodes layer by layer
3. **For each edge (u → v)**:
   - Get base cost from source node `u` (raw material cost or accumulated upstream cost)
   - Add processing energy cost at destination `v`
   - Add transport cost for this shipment
   - Multiply by volume shipped
   - Accumulate total cost at destination node `v`
4. **Normalize**: Divide total cost by total outgoing volume to get per-unit cost

**Result**: Each node has:
- `product_cost`: Total accumulated cost by commodity
- `unit_cost`: Cost per tonne by commodity
- `allocations`: Volume and cost breakdown by commodity

---

### Stage 4: Update Furnace Groups

After cost propagation, the connector updates furnace group attributes:

#### 4a. Update Utilization Rates

**Method**: `update_furnace_group_utilisation(furnace_groups)`

```python
allocated_volumes = sum(outgoing_edge_volumes)
utilization_rate = allocated_volumes / capacity
```

Sets `fg.utilization_rate` based on actual production assigned by Trade Module.

#### 4b. Update Bill of Materials

**Method**: `update_bill_of_materials(furnace_groups)`

For each furnace group, extracts from the graph:

**Materials** (from node allocations):
```python
{
    "scrap": {
        "demand": 105.3,          # tonnes required
        "total_cost": 31590,      # USD
        "unit_cost": 300          # USD/t
    },
    "dri": { ... }
}
```

**Energy** (from edge processing costs):
```python
{
    "electricity": {
        "demand": 100.0,          # production volume
        "total_cost": 6000,       # USD
        "unit_cost": 60           # USD/t
    }
}
```

Sets `fg.bill_of_materials = {"materials": {...}, "energy": {...}}`

#### 4c. Update Emissions

**Method**: `update_furnace_group_emissions(furnace_groups)`

Calls `fg.set_emissions_based_on_allocated_volumes()` for each furnace group if it has a valid BOM. This calculates emissions from material consumption and technology emission factors.

Sets `fg.emissions` with structure:
```python
{
    "plant_boundary": {"scope_1": 500, "scope_2": 200},
    "supply_chain": {"scope_3": 1000}
}
```

---

## Key Methods Reference

### Initialization

**`__init__(dynamic_feedstocks_classes, plants, transport_kpis)`**

Creates connector and populates lookup tables:
- `flat_feedstocks_dict`: O(1) feedstock lookup by name
- `feedstock_energy_requirements`: Energy requirements per feedstock
- `processing_energy_cost`: Energy costs by furnace group and commodity
- `chosen_reductant`: Reductant choice for each furnace
- `transport_costs`: Transport cost lookup `(from_iso, to_iso, commodity) → cost`
- `iron_furnaces`, `steel_furnaces`: Lists of furnace group IDs by product type

### Graph Construction & Cost Propagation

**`set_up_network_and_propagate_costs(solved_trade_allocations)`**

High-level orchestration method that calls in sequence:
1. `create_graph()` - Build NetworkX graph from trade results
2. `calculate_allocations_for_graph()` - Convert volumes to input requirements
3. `validate_edge_attributes()` - Check graph structure
4. `propage_cost_forward_by_layers_and_normalize()` - Propagate costs through network

### Furnace Group Updates

**`update_furnace_group_utilisation(furnace_groups)`**

Sets `fg.utilization_rate = allocated_volumes / capacity`

**`update_bill_of_materials(furnace_groups)`**

Sets `fg.bill_of_materials` with material and energy costs from graph

**`update_furnace_group_emissions(furnace_groups)`**

Calculates and sets `fg.emissions` based on BOM and emission factors

### Utility Methods

**`get_transport_cost(from_iso, to_iso, commodity)`**

Returns transport cost between two countries for a commodity (USD/t)

**`extract_transportation_costs(furnace_groups)`**

Returns detailed transport cost breakdown for each furnace group's incoming shipments

---

## Integration Points

### Called By

**`update_furnace_utilization_rates` handler** (in `handlers.py:241`):

```python
tmpc = TM_PAM_connector(
    dynamic_feedstocks_classes=env.dynamic_feedstocks,
    plants=uow.plants,
    transport_kpis=env.transport_kpis,
)
tmpc.set_up_network_and_propagate_costs(solved_trade_allocations=trade_allocations)
tmpc.update_furnace_group_utilisation(fgs)
tmpc.update_bill_of_materials(fgs)
tmpc.update_furnace_group_emissions(fgs)
env.allocation_and_transportation_costs = tmpc.extract_transportation_costs(fgs)
```

### Dependencies

**Inputs**:
- `Allocations` object from Trade Module LP solver
- `PlantInMemoryRepository` for accessing all plants and furnace groups
- `dynamic_feedstocks` dict mapping technologies to feedstock options
- `TransportKPI` list with transportation costs

**Updates**:
- `FurnaceGroup.utilization_rate`
- `FurnaceGroup.allocated_volumes`
- `FurnaceGroup.bill_of_materials`
- `FurnaceGroup.emissions`

**Used By**:
- PAM decision-making (uses updated costs and utilization)
- Balance sheet calculations (uses updated BOM costs)
- Emissions reporting (uses updated emissions)

---

## Data Flow Through Connector

```
Trade Module LP Solver
    ↓
Allocations: (from_pc, to_pc, commodity) → volume
    ↓
TM_PAM_connector.set_up_network_and_propagate_costs()
    ↓
1. create_graph()
   → NetworkX MultiDiGraph with nodes (furnaces) and edges (flows)
    ↓
2. calculate_allocations_for_graph()
   → Add input allocations (volume / efficiency) to edges
    ↓
3. propage_cost_forward_by_layers_and_normalize()
   → BFS cost accumulation from raw materials to final products
   → Each node gets: product_cost, unit_cost, allocations
    ↓
4. update_furnace_group_utilisation()
   → fg.utilization_rate = sum(outgoing_volumes) / capacity
    ↓
5. update_bill_of_materials()
   → fg.bill_of_materials = {materials: {...}, energy: {...}}
    ↓
6. update_furnace_group_emissions()
   → fg.emissions = {boundary: {scope: value}}
    ↓
PAM uses updated FurnaceGroup attributes for decision-making
```
---


## Known Limitations

1. **Assumes DAG Structure**: Cost propagation assumes no cycles in the supply chain graph. This is determined by the structure of the dynamic bill of materials and legal process connectors given to the Trade Module.

4. **No Transport Mode Differentiation**: All transport costs treated equally - no distinction between rail, sea, truck, etc.

5. **Zero-Volume Edge Handling**: Edges with volume < LP_TOLERANCE are skipped, which may lose some small flows.

---

## Summary

The TM-PAM Connector serves as the critical bridge between optimization and simulation:

- **Input**: Trade optimization results (allocations)
- **Process**: Graph-based cost propagation through supply chains
- **Output**: Updated furnace group parameters (utilization, BOM, emissions)
- **Purpose**: Ensures PAM makes decisions based on actual trade-optimized costs and production levels

This two-way integration enables:
1. **Trade → PAM**: Operational parameters reflect optimized production schedules
2. **PAM → Trade**: Strategic decisions (technology switches, expansions) feed back into future trade optimizations

---

## Related Documentation

For more on PAM decision-making, see **[Agent Definitions](agent_definitions.md)**, **[Furnace Group Strategy](furnace_group_strategy.md)**, and **[Plant Expansions](plant_expansions.md)**.
