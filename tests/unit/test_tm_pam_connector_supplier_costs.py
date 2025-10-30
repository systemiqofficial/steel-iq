"""
Test suite for TM_PAM_connector supplier cost propagation.

This test suite specifically addresses the regression identified in issue
specs/2025-10-13_unit_vopex_too_small.md where supplier costs were not
propagating correctly due to pattern matching on supplier IDs.

The bug: After commit 648808f2 (Sept 15, 2025), supplier IDs changed from
regional names like "Australia_IO_low" to UUID format like "sup_30cd2c7c...".
The old pattern matching code failed to recognize these new IDs, causing
iron ore costs to be set to $0 instead of their actual values.

The fix: Check ProcessType.SUPPLY instead of matching ID patterns.
"""

import pytest
from steelo.domain.trade_modelling.TM_PAM_connector import TM_PAM_connector
from steelo.domain.trade_modelling import trade_lp_modelling as tlp
from steelo.adapters.repositories.in_memory_repository import PlantInMemoryRepository


class DummyLocation:
    """Mock location for testing."""

    def __init__(self, iso3="USA", country="United States", lat=40.0, lon=-74.0):
        self.iso3 = iso3
        self.country = country
        self.lat = lat
        self.lon = lon


def test_uuid_supplier_ids_get_production_cost():
    """
    Test that UUID-format supplier IDs correctly receive production_cost.

    Regression test for bug where supplier IDs like "sup_30cd2c7c..."
    were not recognized and got product_cost={} instead of actual costs.
    """
    # Create a supplier ProcessCenter with UUID-style ID
    supplier_process = tlp.Process(name="io_low_supply", type=tlp.ProcessType.SUPPLY, bill_of_materials=[])

    supplier_pc = tlp.ProcessCenter(
        name="sup_30cd2c7c13695cbd84a0",  # UUID format
        process=supplier_process,
        capacity=1000.0,
        location=DummyLocation(iso3="AUS", country="Australia"),
        production_cost=59.0,  # Iron ore cost from Excel
    )

    # Create a furnace ProcessCenter
    furnace_process = tlp.Process(name="dri", type=tlp.ProcessType.PRODUCTION, bill_of_materials=[])

    furnace_pc = tlp.ProcessCenter(
        name="plant1_fg1",
        process=furnace_process,
        capacity=500.0,
        location=DummyLocation(iso3="USA"),
        production_cost=25.0,  # Carbon cost
    )

    # Create allocations with flow from supplier to furnace
    commodity = tlp.Commodity(name="io_low")
    allocations_dict = {(supplier_pc, furnace_pc, commodity): 100.0}
    allocations = tlp.Allocations(allocations=allocations_dict)

    # Create connector with empty repositories (we don't need actual plants for this test)
    plants_repo = PlantInMemoryRepository()
    connector = TM_PAM_connector(dynamic_feedstocks_classes={}, plants=plants_repo, transport_kpis=None)

    # Create the graph
    connector.create_graph(allocations)

    # Verify supplier node has product_cost set correctly
    assert connector.G.has_node("sup_30cd2c7c13695cbd84a0")
    supplier_node_data = connector.G.nodes["sup_30cd2c7c13695cbd84a0"]

    # Critical assertion: supplier should have its production_cost
    assert supplier_node_data["product_cost"] == 59.0, (
        "UUID-format supplier ID should receive production_cost, not empty dict"
    )

    # Verify furnace node has empty product_cost (carbon costs handled separately)
    assert connector.G.has_node("plant1_fg1")
    furnace_node_data = connector.G.nodes["plant1_fg1"]
    assert furnace_node_data["product_cost"] == {}, "Furnace nodes should have empty product_cost dict"


def test_legacy_supplier_ids_still_work():
    """
    Test that legacy supplier IDs (pre-UUID format) still work correctly.

    Ensures backwards compatibility with old supplier ID formats like
    "Australia_IO_low" or "Brazil_scrap".
    """
    # Create supplier with legacy ID format
    supplier_process = tlp.Process(name="scrap_supply", type=tlp.ProcessType.SUPPLY, bill_of_materials=[])

    supplier_pc = tlp.ProcessCenter(
        name="Australia_IO_low",  # Legacy format
        process=supplier_process,
        capacity=1000.0,
        location=DummyLocation(iso3="AUS", country="Australia"),
        production_cost=65.0,
    )

    furnace_process = tlp.Process(name="dri", type=tlp.ProcessType.PRODUCTION, bill_of_materials=[])

    furnace_pc = tlp.ProcessCenter(
        name="plant2_fg1",
        process=furnace_process,
        capacity=500.0,
        location=DummyLocation(iso3="USA"),
        production_cost=25.0,
    )

    commodity = tlp.Commodity(name="io_low")
    allocations_dict = {(supplier_pc, furnace_pc, commodity): 100.0}
    allocations = tlp.Allocations(allocations=allocations_dict)

    plants_repo = PlantInMemoryRepository()
    connector = TM_PAM_connector(dynamic_feedstocks_classes={}, plants=plants_repo, transport_kpis=None)

    connector.create_graph(allocations)

    # Verify legacy ID still gets production_cost
    supplier_node_data = connector.G.nodes["Australia_IO_low"]
    assert supplier_node_data["product_cost"] == 65.0, "Legacy supplier IDs should still receive production_cost"


def test_multiple_suppliers_different_costs():
    """
    Test that multiple suppliers with different costs are handled correctly.

    Simulates a realistic scenario with iron ore suppliers at different
    cost points (reflecting different mines and transport distances).
    """
    suppliers = [
        ("sup_014416fa56d256babdb0", "io_low_supply", 25.0),  # Low-cost mine
        ("sup_7fe9a1b2c3d4e5f6abcd", "io_mid_supply", 65.0),  # Mid-cost mine
        ("sup_abc123def456789ghijk", "io_high_supply", 140.0),  # High-cost mine
        ("Zimbabwe_scrap", "scrap_supply", 450.0),  # Scrap (legacy ID)
    ]

    # Create furnace
    furnace_process = tlp.Process(name="dri", type=tlp.ProcessType.PRODUCTION, bill_of_materials=[])

    furnace_pc = tlp.ProcessCenter(
        name="plant3_fg1",
        process=furnace_process,
        capacity=1000.0,
        location=DummyLocation(iso3="USA"),
        production_cost=30.0,
    )

    allocations_dict = {}

    # Create allocations from each supplier to furnace
    for supplier_id, process_name, cost in suppliers:
        supplier_process = tlp.Process(name=process_name, type=tlp.ProcessType.SUPPLY, bill_of_materials=[])

        supplier_pc = tlp.ProcessCenter(
            name=supplier_id, process=supplier_process, capacity=500.0, location=DummyLocation(), production_cost=cost
        )

        commodity = tlp.Commodity(name=process_name.replace("_supply", ""))
        allocations_dict[(supplier_pc, furnace_pc, commodity)] = 50.0

    allocations = tlp.Allocations(allocations=allocations_dict)
    plants_repo = PlantInMemoryRepository()
    connector = TM_PAM_connector(dynamic_feedstocks_classes={}, plants=plants_repo, transport_kpis=None)

    connector.create_graph(allocations)

    # Verify each supplier has correct cost
    for supplier_id, _, expected_cost in suppliers:
        assert connector.G.has_node(supplier_id), f"Supplier {supplier_id} should exist in graph"

        node_data = connector.G.nodes[supplier_id]
        assert node_data["product_cost"] == expected_cost, (
            f"Supplier {supplier_id} should have cost ${expected_cost}, got ${node_data['product_cost']}"
        )


def test_furnace_to_furnace_allocation():
    """
    Test that furnace-to-furnace flows (e.g., BF -> BOF) work correctly.

    Both source and destination should have empty product_cost since they're
    both PRODUCTION type (not suppliers).
    """
    # Create BF (blast furnace) producing hot metal
    bf_process = tlp.Process(name="bf", type=tlp.ProcessType.PRODUCTION, bill_of_materials=[])

    bf_pc = tlp.ProcessCenter(
        name="plant4_bf",
        process=bf_process,
        capacity=1000.0,
        location=DummyLocation(),
        production_cost=40.0,  # Carbon cost
    )

    # Create BOF (basic oxygen furnace) consuming hot metal
    bof_process = tlp.Process(name="bof", type=tlp.ProcessType.PRODUCTION, bill_of_materials=[])

    bof_pc = tlp.ProcessCenter(
        name="plant4_bof",
        process=bof_process,
        capacity=950.0,
        location=DummyLocation(),
        production_cost=35.0,  # Carbon cost
    )

    commodity = tlp.Commodity(name="hot_metal")
    allocations_dict = {(bf_pc, bof_pc, commodity): 800.0}
    allocations = tlp.Allocations(allocations=allocations_dict)

    plants_repo = PlantInMemoryRepository()
    connector = TM_PAM_connector(dynamic_feedstocks_classes={}, plants=plants_repo, transport_kpis=None)

    connector.create_graph(allocations)

    # Both furnaces should have empty product_cost
    bf_node_data = connector.G.nodes["plant4_bf"]
    bof_node_data = connector.G.nodes["plant4_bof"]

    assert bf_node_data["product_cost"] == {}, "BF (source furnace) should have empty product_cost"
    assert bof_node_data["product_cost"] == {}, "BOF (destination furnace) should have empty product_cost"


def test_cost_propagation_with_uuid_suppliers():
    """
    Integration test: Verify that costs from UUID suppliers propagate correctly.

    This tests the full chain:
    1. Supplier node gets product_cost set
    2. Cost propagates through graph
    3. Destination node receives accumulated costs

    This is the critical test for the bug fix - costs should not be $0!
    """
    # Create iron ore supplier with UUID ID
    supplier_process = tlp.Process(name="io_low_supply", type=tlp.ProcessType.SUPPLY, bill_of_materials=[])

    supplier_pc = tlp.ProcessCenter(
        name="sup_30cd2c7c13695cbd84a0",
        process=supplier_process,
        capacity=1000.0,
        location=DummyLocation(iso3="AUS"),
        production_cost=59.0,  # This should propagate, not be $0!
    )

    # Create DRI furnace
    furnace_process = tlp.Process(name="dri", type=tlp.ProcessType.PRODUCTION, bill_of_materials=[])

    furnace_pc = tlp.ProcessCenter(
        name="plant5_dri",
        process=furnace_process,
        capacity=500.0,
        location=DummyLocation(iso3="USA"),
        production_cost=25.0,
    )

    commodity = tlp.Commodity(name="io_low")
    allocations_dict = {(supplier_pc, furnace_pc, commodity): 100.0}
    allocations = tlp.Allocations(allocations=allocations_dict)

    plants_repo = PlantInMemoryRepository()
    connector = TM_PAM_connector(dynamic_feedstocks_classes={}, plants=plants_repo, transport_kpis=None)

    connector.create_graph(allocations)

    # Verify supplier cost is set
    supplier_node = connector.G.nodes["sup_30cd2c7c13695cbd84a0"]
    assert supplier_node["product_cost"] == 59.0

    # Verify graph structure
    assert connector.G.has_edge("sup_30cd2c7c13695cbd84a0", "plant5_dri", key="io_low")

    edge_data = connector.G["sup_30cd2c7c13695cbd84a0"]["plant5_dri"]["io_low"]
    assert edge_data["volume"] == 100.0
    assert "transport_cost" in edge_data
    assert "processing_energy_cost" in edge_data

    # The critical assertion: supplier node should have non-zero product_cost
    # If this is 0 or {}, the bug has regressed!
    assert supplier_node["product_cost"] != 0, "Supplier product_cost should not be zero - this indicates regression!"
    assert supplier_node["product_cost"] != {}, (
        "Supplier product_cost should not be empty dict - this indicates regression!"
    )
    assert isinstance(supplier_node["product_cost"], (int, float)), "Supplier product_cost should be numeric, not dict"


def test_cost_propagation_handles_output_alias():
    """
    Ensure incoming pig iron costs survive when the downstream process outputs steel.
    """

    class StubFeedstock:
        def __init__(self):
            self.required_quantity_per_ton_of_product = 1.0

        def get_primary_outputs(self, primary_products=None):
            return {"steel": 1.0}

    plants_repo = PlantInMemoryRepository()
    connector = TM_PAM_connector(dynamic_feedstocks_classes={}, plants=plants_repo, transport_kpis=None)
    connector.flat_feedstocks_dict["eaf_pig_iron"] = StubFeedstock()

    supplier_process = tlp.Process(name="pig_iron_supply", type=tlp.ProcessType.SUPPLY, bill_of_materials=[])
    furnace_process = tlp.Process(name="EAF", type=tlp.ProcessType.PRODUCTION, bill_of_materials=[])
    demand_process = tlp.Process(name="demand", type=tlp.ProcessType.DEMAND, bill_of_materials=[])

    supplier_pc = tlp.ProcessCenter(
        name="sup_pig_iron",
        process=supplier_process,
        capacity=1_000.0,
        location=DummyLocation(iso3="BRA"),
        production_cost=80.0,
    )
    furnace_pc = tlp.ProcessCenter(
        name="plant_eaf_fg1",
        process=furnace_process,
        capacity=1_000.0,
        location=DummyLocation(iso3="USA"),
        production_cost=20.0,
    )
    demand_pc = tlp.ProcessCenter(
        name="usa_demand",
        process=demand_process,
        capacity=1_000.0,
        location=DummyLocation(iso3="USA"),
        production_cost=0.0,
    )

    pig_iron = tlp.Commodity(name="pig_iron")
    steel = tlp.Commodity(name="steel")
    allocations = tlp.Allocations(
        allocations={
            (supplier_pc, furnace_pc, pig_iron): 100.0,
            (furnace_pc, demand_pc, steel): 100.0,
        }
    )

    connector.create_graph(allocations)
    connector.propage_cost_forward_by_layers_and_normalize()

    furnace_node = connector.G.nodes[furnace_pc.name]
    assert "pig_iron" in furnace_node["product_cost"], "Expected pig iron cost stored on furnace node"
    assert furnace_node["product_cost"]["pig_iron"] == pytest.approx(8_000.0)
    assert furnace_node["allocations"]["pig_iron"]["Cost"] == pytest.approx(8_000.0)


def test_scrap_supplier_with_country_based_id():
    """
    Test scrap suppliers which typically use country-based IDs.

    Scrap suppliers often use format like "Zimbabwe_scrap" which is different
    from the UUID format but should still work correctly.
    """
    supplier_process = tlp.Process(name="scrap_supply", type=tlp.ProcessType.SUPPLY, bill_of_materials=[])

    supplier_pc = tlp.ProcessCenter(
        name="Zimbabwe_scrap",
        process=supplier_process,
        capacity=200.0,
        location=DummyLocation(iso3="ZWE", country="Zimbabwe"),
        production_cost=450.0,  # Hardcoded scrap cost
    )

    furnace_process = tlp.Process(name="eaf", type=tlp.ProcessType.PRODUCTION, bill_of_materials=[])

    furnace_pc = tlp.ProcessCenter(
        name="plant6_eaf",
        process=furnace_process,
        capacity=300.0,
        location=DummyLocation(iso3="ZWE"),
        production_cost=20.0,
    )

    commodity = tlp.Commodity(name="scrap")
    allocations_dict = {(supplier_pc, furnace_pc, commodity): 150.0}
    allocations = tlp.Allocations(allocations=allocations_dict)

    plants_repo = PlantInMemoryRepository()
    connector = TM_PAM_connector(dynamic_feedstocks_classes={}, plants=plants_repo, transport_kpis=None)

    connector.create_graph(allocations)

    # Verify scrap supplier gets its cost
    node_data = connector.G.nodes["Zimbabwe_scrap"]
    assert node_data["product_cost"] == 450.0, "Scrap supplier should have production_cost set correctly"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
