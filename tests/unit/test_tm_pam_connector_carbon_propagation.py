"""
Test suite for producing-furnace carbon-cost propagation through TM_PAM_connector.

Verifies that a producing furnace's `production_cost` (= `carbon_cost_per_unit`
on a furnace group) is embedded once on its outgoing flows so that downstream
bills of materials see the upstream carbon as part of their input cost. This
defends the invariant that own carbon flows out, never inward — so the
furnace's own BOM never picks up its own self-carbon.

See specs/MD_TRADE_CARBON_PROPAGATION/spec.md for full motivation.
"""

from types import SimpleNamespace

import pytest

from steelo.adapters.repositories.in_memory_repository import PlantInMemoryRepository
from steelo.domain.trade_modelling import trade_lp_modelling as tlp
from steelo.domain.trade_modelling.TM_PAM_connector import TM_PAM_connector


class DummyLocation:
    """Minimal location stub for ProcessCenter construction."""

    def __init__(self, iso3="USA", country="United States", lat=40.0, lon=-74.0):
        self.iso3 = iso3
        self.country = country
        self.lat = lat
        self.lon = lon


def _make_supplier(name, iso3, commodity_name, cost, capacity=1000.0):
    """Build a SUPPLY ProcessCenter (root node, scalar production_cost)."""
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


def _make_furnace(name, iso3, process_name="bf", carbon_cost=0.0, capacity=500.0):
    """Build a PRODUCTION ProcessCenter; production_cost stands in for carbon_cost_per_unit."""
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
        production_cost=carbon_cost,
    )


def _make_demand(name, iso3, capacity=1000.0):
    """Build a DEMAND ProcessCenter (sink node)."""
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
    """Build a TM_PAM_connector with empty repositories and no transport KPIs."""
    plants_repo = PlantInMemoryRepository()
    return TM_PAM_connector(
        dynamic_feedstocks_classes={},
        plants=plants_repo,
        transport_kpis=None,
    )


def _make_fg_stub(furnace_group_id, technology_name="BF"):
    """Build a minimal FurnaceGroup stub for update_bill_of_materials calls.

    Args:
        furnace_group_id: Identifier matching the graph node name.
        technology_name: Technology label for diagnostic logging only.

    Returns:
        SimpleNamespace with the attributes update_bill_of_materials reads.

    Notes:
        - bill_of_materials starts as {} so the merge path is exercised.
        - utilization_rate is intentionally left unset so the function uses
          the merge branch (matching production behaviour).
    """
    return SimpleNamespace(
        furnace_group_id=furnace_group_id,
        technology=SimpleNamespace(name=technology_name, product="iron"),
        status="operating",
        bill_of_materials={},
        production=0.0,
    )


def test_bf_carbon_cascades_into_bof_bom():
    """BF's own carbon flows onto its hot_metal output and lands in BOF's BOM.

    Notes:
        Setup: ore supplier (cost 70) -> BF (carbon 200) -> BOF (carbon 80) -> sink.
        Volumes: 100t at every edge. No transport, tariff, or processing energy.
        Expected: BOF BOM hot_metal total_material_cost = 70*100 + 200*100 = 27000.
    """
    supplier = _make_supplier("sup_aus", "AUS", "io_low", cost=70.0)
    bf = _make_furnace("plant_bf", "DEU", process_name="bf", carbon_cost=200.0)
    bof = _make_furnace("plant_bof", "DEU", process_name="bof", carbon_cost=80.0)
    sink = _make_demand("deu_demand", "DEU")

    io_low = tlp.Commodity(name="io_low")
    hot_metal = tlp.Commodity(name="hot_metal")
    steel = tlp.Commodity(name="steel")

    allocations = tlp.Allocations(
        allocations={
            (supplier, bf, io_low): 100.0,
            (bf, bof, hot_metal): 100.0,
            (bof, sink, steel): 100.0,
        },
    )

    connector = _make_connector()
    connector.create_graph(allocations)
    connector.propage_cost_forward_by_layers_and_normalize()

    bof_alloc = connector.G.nodes["plant_bof"]["allocations"]["hot_metal"]
    assert bof_alloc["MaterialCost"] == pytest.approx(27_000.0), (
        "BOF hot_metal MaterialCost should equal upstream ore (70*100=7000) + "
        "BF own carbon embedded on the outgoing flow (200*100=20000) = 27000."
    )

    bof_fg = _make_fg_stub("plant_bof", technology_name="BOF")
    connector.update_bill_of_materials([bof_fg])

    hot_metal_bom = bof_fg.bill_of_materials["materials"]["hot_metal"]
    assert hot_metal_bom["total_material_cost"] == pytest.approx(27_000.0), (
        "BOF BOM hot_metal total_material_cost should reflect BF's pass-through carbon."
    )


def test_bf_self_carbon_not_in_own_bom():
    """BF's own carbon must not appear in BF's own bill of materials.

    Notes:
        Defends the invariant: own_unit_cost flows out (onto BF's outgoing edges
        only), never inward. BF's BOM is built from incoming allocations, so
        nothing from own_unit_cost can reach it.
    """
    supplier = _make_supplier("sup_aus", "AUS", "io_low", cost=70.0)
    bf = _make_furnace("plant_bf", "DEU", process_name="bf", carbon_cost=200.0)
    bof = _make_furnace("plant_bof", "DEU", process_name="bof", carbon_cost=80.0)
    sink = _make_demand("deu_demand", "DEU")

    io_low = tlp.Commodity(name="io_low")
    hot_metal = tlp.Commodity(name="hot_metal")
    steel = tlp.Commodity(name="steel")

    allocations = tlp.Allocations(
        allocations={
            (supplier, bf, io_low): 100.0,
            (bf, bof, hot_metal): 100.0,
            (bof, sink, steel): 100.0,
        },
    )

    connector = _make_connector()
    connector.create_graph(allocations)
    connector.propage_cost_forward_by_layers_and_normalize()

    bf_fg = _make_fg_stub("plant_bf", technology_name="BF")
    connector.update_bill_of_materials([bf_fg])

    io_low_bom = bf_fg.bill_of_materials["materials"]["io_low"]
    assert io_low_bom["total_material_cost"] == pytest.approx(7_000.0), (
        "BF BOM io_low total_material_cost should be the supplier cost only "
        "(70*100=7000); BF's own 200/t carbon must not appear here."
    )


def test_bof_self_carbon_not_in_own_bom():
    """BOF's own carbon must not appear in BOF's own bill of materials.

    Notes:
        BOF's 80/t carbon enters its economics later via the
        unit_production_cost = unit_total_opex + carbon_cost_per_unit pathway;
        adding it to BOF's BOM as well would double-count.
    """
    supplier = _make_supplier("sup_aus", "AUS", "io_low", cost=70.0)
    bf = _make_furnace("plant_bf", "DEU", process_name="bf", carbon_cost=200.0)
    bof = _make_furnace("plant_bof", "DEU", process_name="bof", carbon_cost=80.0)
    sink = _make_demand("deu_demand", "DEU")

    io_low = tlp.Commodity(name="io_low")
    hot_metal = tlp.Commodity(name="hot_metal")
    steel = tlp.Commodity(name="steel")

    allocations = tlp.Allocations(
        allocations={
            (supplier, bf, io_low): 100.0,
            (bf, bof, hot_metal): 100.0,
            (bof, sink, steel): 100.0,
        },
    )

    connector = _make_connector()
    connector.create_graph(allocations)
    connector.propage_cost_forward_by_layers_and_normalize()

    bof_fg = _make_fg_stub("plant_bof", technology_name="BOF")
    connector.update_bill_of_materials([bof_fg])

    materials = bof_fg.bill_of_materials["materials"]

    # Only hot_metal flows in; the BOF self-carbon would have shown up either
    # as a phantom entry or by inflating the hot_metal cost beyond 27000.
    assert set(materials.keys()) == {"hot_metal"}, (
        "BOF BOM should only contain its inputs (hot_metal); no self-carbon entry."
    )
    assert materials["hot_metal"]["total_material_cost"] == pytest.approx(27_000.0), (
        "BOF BOM hot_metal total_material_cost = 70*100 (ore) + 200*100 (BF own carbon)."
        " It must NOT include BOF's own 80/t carbon."
    )


def test_multi_output_bf_distributes_carbon_to_all_outputs():
    """BF carbon distributes equally per-ton across hot_metal and pig_iron outputs.

    Notes:
        For multi-output processes the propagation uses total output volume as
        the denominator (TM_PAM_connector lines 482-487), so adding
        own_unit_cost to per_unit_base is dimensionally consistent and yields
        the same per-ton uplift on every outgoing commodity.

        Setup: ore supplier (60) -> BF (carbon 100) -> {BOF consumes hot_metal,
        EAF consumes pig_iron} -> sink. V_hm=60, V_pi=40, total output 100.
        Per-ton input cost at BF = (60*100)/(60+40) = 60. With own carbon
        added: 60 + 100 = 160/t on every outgoing edge.
    """
    supplier = _make_supplier("sup_aus", "AUS", "io_low", cost=60.0)
    bf = _make_furnace("plant_bf", "DEU", process_name="bf", carbon_cost=100.0)
    bof = _make_furnace("plant_bof", "DEU", process_name="bof", carbon_cost=0.0)
    eaf = _make_furnace("plant_eaf", "DEU", process_name="eaf", carbon_cost=0.0)
    sink_steel = _make_demand("deu_steel_demand", "DEU")
    sink_iron = _make_demand("deu_iron_demand", "DEU")

    io_low = tlp.Commodity(name="io_low")
    hot_metal = tlp.Commodity(name="hot_metal")
    pig_iron = tlp.Commodity(name="pig_iron")
    steel = tlp.Commodity(name="steel")

    allocations = tlp.Allocations(
        allocations={
            (supplier, bf, io_low): 100.0,
            (bf, bof, hot_metal): 60.0,
            (bf, eaf, pig_iron): 40.0,
            (bof, sink_steel, steel): 60.0,
            (eaf, sink_iron, steel): 40.0,
        },
    )

    connector = _make_connector()
    connector.create_graph(allocations)
    connector.propage_cost_forward_by_layers_and_normalize()

    bof_alloc = connector.G.nodes["plant_bof"]["allocations"]["hot_metal"]
    eaf_alloc = connector.G.nodes["plant_eaf"]["allocations"]["pig_iron"]

    # Per-ton uplift on each output should equal BF carbon (100/t).
    bof_unit = bof_alloc["MaterialCost"] / bof_alloc["Volume"]
    eaf_unit = eaf_alloc["MaterialCost"] / eaf_alloc["Volume"]
    assert bof_unit == pytest.approx(160.0), (
        "BOF hot_metal per-ton cost = 60 (upstream ore per ton output) + 100 (BF carbon)."
    )
    assert eaf_unit == pytest.approx(160.0), (
        "EAF pig_iron per-ton cost should match BOF's per-ton: equal carbon allocation."
    )
    assert bof_unit == pytest.approx(eaf_unit), (
        "Carbon must be allocated equally per ton across BF's multi-output products."
    )


def test_multi_input_bof_only_furnace_inputs_carry_carbon():
    """When BOF has multiple inputs, only the furnace-sourced one carries carbon.

    Notes:
        Scrap supplier (SUPPLY type) has no carbon to pass through, so its cost
        flows through unchanged. Only BF's hot_metal output carries BF's own
        carbon.
    """
    ore_supplier = _make_supplier("sup_ore", "AUS", "io_low", cost=50.0)
    scrap_supplier = _make_supplier("sup_scrap", "DEU", "scrap", cost=200.0)
    bf = _make_furnace("plant_bf", "DEU", process_name="bf", carbon_cost=150.0)
    bof = _make_furnace("plant_bof", "DEU", process_name="bof", carbon_cost=0.0)
    sink = _make_demand("deu_demand", "DEU")

    io_low = tlp.Commodity(name="io_low")
    hot_metal = tlp.Commodity(name="hot_metal")
    scrap = tlp.Commodity(name="scrap")
    steel = tlp.Commodity(name="steel")

    allocations = tlp.Allocations(
        allocations={
            (ore_supplier, bf, io_low): 100.0,
            (bf, bof, hot_metal): 100.0,
            (scrap_supplier, bof, scrap): 50.0,
            (bof, sink, steel): 100.0,
        },
    )

    connector = _make_connector()
    connector.create_graph(allocations)
    connector.propage_cost_forward_by_layers_and_normalize()

    bof_node = connector.G.nodes["plant_bof"]
    hot_metal_alloc = bof_node["allocations"]["hot_metal"]
    scrap_alloc = bof_node["allocations"]["scrap"]

    # Hot metal input: ore (50*100=5000) + BF carbon (150*100=15000) = 20000.
    assert hot_metal_alloc["MaterialCost"] == pytest.approx(20_000.0), (
        "BOF hot_metal MaterialCost must include BF's own carbon (150*100=15000)."
    )

    # Scrap input: supplier cost only (200*50=10000); no carbon to add.
    assert scrap_alloc["MaterialCost"] == pytest.approx(10_000.0), (
        "BOF scrap MaterialCost should be supplier cost only — suppliers have no carbon to pass through."
    )


def test_zero_carbon_price_unchanged():
    """When all furnaces have zero carbon, BOM costs match the pre-fix behaviour.

    Notes:
        Defends the byte-identical claim: with own_unit_cost=0, the `if own:`
        guard short-circuits the addition. Cost values are exactly as they
        were before the fix.
    """
    supplier = _make_supplier("sup_aus", "AUS", "io_low", cost=70.0)
    bf = _make_furnace("plant_bf", "DEU", process_name="bf", carbon_cost=0.0)
    bof = _make_furnace("plant_bof", "DEU", process_name="bof", carbon_cost=0.0)
    sink = _make_demand("deu_demand", "DEU")

    io_low = tlp.Commodity(name="io_low")
    hot_metal = tlp.Commodity(name="hot_metal")
    steel = tlp.Commodity(name="steel")

    allocations = tlp.Allocations(
        allocations={
            (supplier, bf, io_low): 100.0,
            (bf, bof, hot_metal): 100.0,
            (bof, sink, steel): 100.0,
        },
    )

    connector = _make_connector()
    connector.create_graph(allocations)
    connector.propage_cost_forward_by_layers_and_normalize()

    bof_alloc = connector.G.nodes["plant_bof"]["allocations"]["hot_metal"]
    assert bof_alloc["MaterialCost"] == pytest.approx(7_000.0), (
        "With zero carbon on every furnace, BOF hot_metal MaterialCost should be the "
        "supplier cost only (70*100=7000) — no carbon uplift."
    )


def test_supplier_path_unchanged_without_upstream_furnace():
    """When a supplier feeds a furnace directly (no upstream producer), behaviour is unchanged.

    Notes:
        Supplier is a root node (in_degree=0), so the new producer-side guard
        `G.in_degree(u) > 0` skips it. Suppliers must also not be stamped with
        own_unit_cost — only producing furnaces are. The downstream furnace's
        MaterialCost should reflect supplier cost only.
    """
    supplier = _make_supplier("sup_scrap", "DEU", "scrap", cost=200.0)
    eaf = _make_furnace("plant_eaf", "DEU", process_name="eaf", carbon_cost=0.0)
    sink = _make_demand("deu_demand", "DEU")

    scrap = tlp.Commodity(name="scrap")
    steel = tlp.Commodity(name="steel")

    allocations = tlp.Allocations(
        allocations={
            (supplier, eaf, scrap): 100.0,
            (eaf, sink, steel): 100.0,
        },
    )

    connector = _make_connector()
    connector.create_graph(allocations)
    connector.propage_cost_forward_by_layers_and_normalize()

    # Suppliers must never be stamped with own_unit_cost (only producing furnaces are).
    assert "own_unit_cost" not in connector.G.nodes["sup_scrap"], (
        "Suppliers (SUPPLY type) must not be stamped with own_unit_cost."
    )

    # EAF receives only the supplier's cost; nothing is added by the producer-side guard
    # because the supplier is a root (in_degree=0).
    eaf_alloc = connector.G.nodes["plant_eaf"]["allocations"]["scrap"]
    assert eaf_alloc["MaterialCost"] == pytest.approx(20_000.0), (
        "Direct supplier->furnace MaterialCost = supplier cost * volume = 200*100 = 20000. "
        "The new producer-side addition must not fire on supplier root nodes."
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
