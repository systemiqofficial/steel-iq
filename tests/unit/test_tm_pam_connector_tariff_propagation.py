"""
Test suite for tariff cost propagation through TM_PAM_connector.

Verifies that tariff costs from the LP model flow correctly through
the trade graph into BOM total_material_cost, matching the same
propagation path as transport costs.
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


def _make_supplier(name, iso3, commodity_name, cost, capacity=1000.0):
    """Create a supplier ProcessCenter with given parameters."""
    process = tlp.Process(
        name=f"{commodity_name}_supply",
        type=tlp.ProcessType.SUPPLY,
        bill_of_materials=[],
    )
    return tlp.ProcessCenter(
        name=name,
        process=process,
        capacity=capacity,
        location=DummyLocation(iso3=iso3),
        production_cost=cost,
    )


def _make_furnace(name, iso3, process_name="bf", capacity=500.0):
    """Create a furnace ProcessCenter."""
    process = tlp.Process(
        name=process_name,
        type=tlp.ProcessType.PRODUCTION,
        bill_of_materials=[],
    )
    return tlp.ProcessCenter(
        name=name,
        process=process,
        capacity=capacity,
        location=DummyLocation(iso3=iso3),
        production_cost=0.0,
    )


def _make_demand(name, iso3, capacity=1000.0):
    """Create a demand ProcessCenter."""
    process = tlp.Process(
        name="demand",
        type=tlp.ProcessType.DEMAND,
        bill_of_materials=[],
    )
    return tlp.ProcessCenter(
        name=name,
        process=process,
        capacity=capacity,
        location=DummyLocation(iso3=iso3),
        production_cost=0.0,
    )


def _make_connector():
    """Create a TM_PAM_connector with empty repositories."""
    plants_repo = PlantInMemoryRepository()
    return TM_PAM_connector(
        dynamic_feedstocks_classes={},
        plants=plants_repo,
        transport_kpis=None,
    )


def test_tariff_stored_as_edge_attribute():
    """Tariff taxes from Allocations are stored as edge attributes on the graph."""
    supplier_pc = _make_supplier("sup_aus", "AUS", "io_low", cost=70.0)
    furnace_pc = _make_furnace("plant_deu_bf", "DEU")
    commodity = tlp.Commodity(name="io_low")

    tariff_taxes = {("AUS", "DEU", "io_low"): 10.0}

    allocations = tlp.Allocations(
        allocations={(supplier_pc, furnace_pc, commodity): 100.0},
        tariff_taxes=tariff_taxes,
    )

    connector = _make_connector()
    connector.create_graph(allocations)

    edge_data = connector.G["sup_aus"]["plant_deu_bf"]["io_low"]
    assert edge_data["tariff_cost"] == 10.0, "Tariff should be stored as edge attribute"


def test_tariff_zero_when_no_tariff_applies():
    """Edges without matching tariffs get tariff_cost=0."""
    supplier_pc = _make_supplier("sup_aus", "AUS", "io_low", cost=70.0)
    furnace_pc = _make_furnace("plant_chn_bf", "CHN")
    commodity = tlp.Commodity(name="io_low")

    # Tariff only on AUS->DEU, not AUS->CHN
    tariff_taxes = {("AUS", "DEU", "io_low"): 10.0}

    allocations = tlp.Allocations(
        allocations={(supplier_pc, furnace_pc, commodity): 100.0},
        tariff_taxes=tariff_taxes,
    )

    connector = _make_connector()
    connector.create_graph(allocations)

    edge_data = connector.G["sup_aus"]["plant_chn_bf"]["io_low"]
    assert edge_data["tariff_cost"] == 0.0, "No tariff should apply on this route"


def test_tariff_wildcard_source():
    """Wildcard '*' in from_iso3 matches any source country."""
    supplier_pc = _make_supplier("sup_bra", "BRA", "io_low", cost=60.0)
    furnace_pc = _make_furnace("plant_deu_bf", "DEU")
    commodity = tlp.Commodity(name="io_low")

    tariff_taxes = {("*", "DEU", "io_low"): 15.0}

    allocations = tlp.Allocations(
        allocations={(supplier_pc, furnace_pc, commodity): 100.0},
        tariff_taxes=tariff_taxes,
    )

    connector = _make_connector()
    connector.create_graph(allocations)

    edge_data = connector.G["sup_bra"]["plant_deu_bf"]["io_low"]
    assert edge_data["tariff_cost"] == 15.0, "Wildcard source tariff should match any origin"


def test_tariff_wildcard_destination():
    """Wildcard '*' in to_iso3 matches any destination country."""
    supplier_pc = _make_supplier("sup_aus", "AUS", "io_low", cost=70.0)
    furnace_pc = _make_furnace("plant_chn_bf", "CHN")
    commodity = tlp.Commodity(name="io_low")

    tariff_taxes = {("AUS", "*", "io_low"): 8.0}

    allocations = tlp.Allocations(
        allocations={(supplier_pc, furnace_pc, commodity): 100.0},
        tariff_taxes=tariff_taxes,
    )

    connector = _make_connector()
    connector.create_graph(allocations)

    edge_data = connector.G["sup_aus"]["plant_chn_bf"]["io_low"]
    assert edge_data["tariff_cost"] == 8.0, "Wildcard destination tariff should match any destination"


def test_tariff_wildcard_commodity():
    """Wildcard '*' in commodity matches any commodity."""
    supplier_pc = _make_supplier("sup_aus", "AUS", "io_low", cost=70.0)
    furnace_pc = _make_furnace("plant_deu_bf", "DEU")
    commodity = tlp.Commodity(name="io_low")

    tariff_taxes = {("AUS", "DEU", "*"): 5.0}

    allocations = tlp.Allocations(
        allocations={(supplier_pc, furnace_pc, commodity): 100.0},
        tariff_taxes=tariff_taxes,
    )

    connector = _make_connector()
    connector.create_graph(allocations)

    edge_data = connector.G["sup_aus"]["plant_deu_bf"]["io_low"]
    assert edge_data["tariff_cost"] == 5.0, "Wildcard commodity tariff should match any commodity"


def test_multiple_wildcard_tariffs_are_summed():
    """Multiple matching wildcard tariffs are summed together."""
    supplier_pc = _make_supplier("sup_aus", "AUS", "io_low", cost=70.0)
    furnace_pc = _make_furnace("plant_deu_bf", "DEU")
    commodity = tlp.Commodity(name="io_low")

    tariff_taxes = {
        ("AUS", "DEU", "io_low"): 10.0,  # Exact match
        ("*", "DEU", "io_low"): 5.0,  # Wildcard source
        ("AUS", "*", "io_low"): 3.0,  # Wildcard destination
        ("AUS", "DEU", "*"): 2.0,  # Wildcard commodity
    }

    allocations = tlp.Allocations(
        allocations={(supplier_pc, furnace_pc, commodity): 100.0},
        tariff_taxes=tariff_taxes,
    )

    connector = _make_connector()
    connector.create_graph(allocations)

    edge_data = connector.G["sup_aus"]["plant_deu_bf"]["io_low"]
    assert edge_data["tariff_cost"] == pytest.approx(20.0), "All matching tariffs should be summed: 10 + 5 + 3 + 2 = 20"


def test_tariff_propagates_into_material_cost():
    """
    Tariff cost propagates into MaterialCost on the destination node.

    Verifies the full chain: edge tariff_cost -> material_tariff_transportation_cost
    -> node allocations MaterialCost.
    """
    supplier_pc = _make_supplier("sup_aus", "AUS", "io_low", cost=70.0)
    furnace_pc = _make_furnace("plant_deu_bf", "DEU")
    demand_pc = _make_demand("deu_demand", "DEU")
    commodity = tlp.Commodity(name="io_low")
    steel = tlp.Commodity(name="steel")

    tariff_taxes = {("AUS", "DEU", "io_low"): 15.0}

    allocations = tlp.Allocations(
        allocations={
            (supplier_pc, furnace_pc, commodity): 100.0,
            (furnace_pc, demand_pc, steel): 100.0,
        },
        tariff_taxes=tariff_taxes,
    )

    connector = _make_connector()
    connector.create_graph(allocations)
    connector.propage_cost_forward_by_layers_and_normalize()

    furnace_node = connector.G.nodes["plant_deu_bf"]
    alloc = furnace_node["allocations"]["io_low"]

    # MaterialCost = (base + transport + tariff) * volume
    # base = 70.0 (supplier cost), transport = 0.0 (no TransportKPIs), tariff = 15.0
    # MaterialCost = (70 + 0 + 15) * 100 = 8500
    assert alloc["MaterialCost"] == pytest.approx(8_500.0), (
        "MaterialCost should include tariff: (70 + 0 + 15) * 100 = 8500"
    )

    # Cost = MaterialCost + energy = 8500 + 0 = 8500 (no energy cost configured)
    assert alloc["Cost"] == pytest.approx(8_500.0), (
        "Cost should equal MaterialCost when no processing energy is configured"
    )


def test_tariff_excluded_from_no_tariff_route():
    """
    Only the route with a tariff gets the extra cost; other routes are unaffected.
    """
    supplier_aus = _make_supplier("sup_aus", "AUS", "io_low", cost=70.0)
    supplier_bra = _make_supplier("sup_bra", "BRA", "io_low", cost=60.0)
    furnace_pc = _make_furnace("plant_deu_bf", "DEU")
    demand_pc = _make_demand("deu_demand", "DEU")
    commodity = tlp.Commodity(name="io_low")
    steel = tlp.Commodity(name="steel")

    # Tariff only on AUS->DEU, not BRA->DEU
    tariff_taxes = {("AUS", "DEU", "io_low"): 15.0}

    allocations = tlp.Allocations(
        allocations={
            (supplier_aus, furnace_pc, commodity): 100.0,
            (supplier_bra, furnace_pc, commodity): 200.0,
            (furnace_pc, demand_pc, steel): 300.0,
        },
        tariff_taxes=tariff_taxes,
    )

    connector = _make_connector()
    connector.create_graph(allocations)
    connector.propage_cost_forward_by_layers_and_normalize()

    furnace_node = connector.G.nodes["plant_deu_bf"]
    alloc = furnace_node["allocations"]["io_low"]

    # AUS: (70 + 0 + 15) * 100 = 8500 (with tariff)
    # BRA: (60 + 0 + 0)  * 200 = 12000 (no tariff)
    # MaterialCost = 8500 + 12000 = 20500
    assert alloc["MaterialCost"] == pytest.approx(20_500.0), "MaterialCost should be AUS(8500) + BRA(12000) = 20500"


def test_no_tariff_taxes_defaults_to_zero():
    """When Allocations has no tariff_taxes, all edges get tariff_cost=0."""
    supplier_pc = _make_supplier("sup_aus", "AUS", "io_low", cost=70.0)
    furnace_pc = _make_furnace("plant_deu_bf", "DEU")
    commodity = tlp.Commodity(name="io_low")

    allocations = tlp.Allocations(
        allocations={(supplier_pc, furnace_pc, commodity): 100.0},
        tariff_taxes=None,
    )

    connector = _make_connector()
    connector.create_graph(allocations)

    edge_data = connector.G["sup_aus"]["plant_deu_bf"]["io_low"]
    assert edge_data["tariff_cost"] == 0.0, "No tariff_taxes means zero tariff on all edges"


def test_tariff_cascades_through_multi_step_supply_chain():
    """
    Tariff on io_low cascades through BF -> BOF supply chain.

    The tariff enters at the supplier->BF edge, gets incorporated into
    BF's MaterialCost, then propagates further when BF's output (hot_metal)
    flows to the BOF.
    """

    class StubFeedstock:
        """Minimal feedstock stub for process efficiency lookup."""

        def __init__(self):
            self.required_quantity_per_ton_of_product = 1.0

        def get_primary_outputs(self, primary_products=None):
            return {"hot_metal": 1.0}

    supplier_pc = _make_supplier("sup_aus", "AUS", "io_low", cost=70.0)
    bf_pc = _make_furnace("plant_deu_bf", "DEU", process_name="bf")
    bof_pc = _make_furnace("plant_deu_bof", "DEU", process_name="bof")
    demand_pc = _make_demand("deu_demand", "DEU")

    io_low = tlp.Commodity(name="io_low")
    hot_metal = tlp.Commodity(name="hot_metal")
    steel = tlp.Commodity(name="steel")

    tariff_taxes = {("AUS", "DEU", "io_low"): 15.0}

    allocations = tlp.Allocations(
        allocations={
            (supplier_pc, bf_pc, io_low): 100.0,
            (bf_pc, bof_pc, hot_metal): 100.0,
            (bof_pc, demand_pc, steel): 100.0,
        },
        tariff_taxes=tariff_taxes,
    )

    connector = _make_connector()
    connector.flat_feedstocks_dict["bf_io_low"] = StubFeedstock()
    connector.create_graph(allocations)
    connector.propage_cost_forward_by_layers_and_normalize()

    # BF node: io_low MaterialCost = (70 + 0 + 15) * 100 = 8500
    bf_node = connector.G.nodes["plant_deu_bf"]
    assert bf_node["allocations"]["io_low"]["MaterialCost"] == pytest.approx(8_500.0), (
        "BF should accumulate tariff in io_low MaterialCost"
    )

    # BOF node: hot_metal cost should carry the upstream tariff
    # per_unit_base at BF = 8500 / 100 (export volume) = 85.0
    # hot_metal MaterialCost at BOF = 85.0 * 100 = 8500
    bof_node = connector.G.nodes["plant_deu_bof"]
    assert bof_node["allocations"]["hot_metal"]["MaterialCost"] == pytest.approx(8_500.0), (
        "BOF should receive upstream tariff via hot_metal cost propagation"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
