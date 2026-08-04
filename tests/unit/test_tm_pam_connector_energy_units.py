"""End-to-end unit check for the TM->PAM energy-cost conversion.

Two suppliers feed a two-charge blast furnace that exports hot metal to a demand
sink. Energy costs are authored per tonne of product; edge volumes flow in input
tonnes. The connector must convert once at the snapshot so that booked BOM energy
equals per-product cost x product tonnes, and the downstream node's material cost
embeds the converted (not inflated) energy.
"""

import logging

import pytest
from types import SimpleNamespace

import steelo.domain.trade_modelling.trade_lp_modelling as tlp
from steelo.domain.trade_modelling.TM_PAM_connector import TM_PAM_connector


class _StubRepo:
    def __init__(self, plants):
        self._plants = plants

    def list(self):
        return self._plants


def _location(iso3):
    return SimpleNamespace(iso3=iso3, country=iso3, lat=0.0, lon=0.0)


def _make_bf_furnace_group():
    return SimpleNamespace(
        furnace_group_id="plant_bf1",
        technology=SimpleNamespace(name="BF", product="iron"),
        status="operating",
        chosen_reductant="coke+pci",
        energy_vopex_by_input={"io_low": 96.0, "io_mid": 90.0},
        energy_vopex_breakdown_by_input={
            "io_low": {"coking_coal": 96.0},
            "io_mid": {"coking_coal": 90.0},
        },
        effective_primary_feedstocks=[
            SimpleNamespace(
                metallic_charge="io_low",
                required_quantity_per_ton_of_product=1.6,
                energy_requirements={"coking_coal": 0.48},
                secondary_feedstock={},
            ),
            SimpleNamespace(
                metallic_charge="io_mid",
                required_quantity_per_ton_of_product=1.5,
                energy_requirements={"coking_coal": 0.45},
                secondary_feedstock={},
            ),
        ],
        bill_of_materials={},
        production=200.0,
    )


def test_booked_energy_equals_per_product_cost_times_product_tonnes():
    """160 t io_low (req 1.6) + 150 t io_mid (req 1.5) -> 200 t hot metal.

    Correct booking: 96 x 100 + 90 x 100 = 18,600 USD, not per-product cost x
    input tonnes (96 x 160 + 90 x 150 = 28,860 USD).
    """
    furnace_group = _make_bf_furnace_group()
    plant = SimpleNamespace(plant_id="plant", furnace_groups=[furnace_group])
    connector = TM_PAM_connector(
        dynamic_feedstocks_classes={},
        plants=_StubRepo([plant]),
        transport_kpis=None,
    )

    sup_low = tlp.ProcessCenter(
        name="sup_low",
        process=tlp.Process(name="io_low_supply", type=tlp.ProcessType.SUPPLY, bill_of_materials=[]),
        capacity=1000.0,
        location=_location("AUS"),
        production_cost=50.0,
    )
    sup_mid = tlp.ProcessCenter(
        name="sup_mid",
        process=tlp.Process(name="io_mid_supply", type=tlp.ProcessType.SUPPLY, bill_of_materials=[]),
        capacity=1000.0,
        location=_location("BRA"),
        production_cost=40.0,
    )
    bf_pc = tlp.ProcessCenter(
        name="plant_bf1",
        process=tlp.Process(name="BF", type=tlp.ProcessType.PRODUCTION, bill_of_materials=[]),
        capacity=500.0,
        location=_location("DEU"),
        production_cost=0.0,
    )
    sink_pc = tlp.ProcessCenter(
        name="deu_demand",
        process=tlp.Process(name="demand", type=tlp.ProcessType.DEMAND, bill_of_materials=[]),
        capacity=1000.0,
        location=_location("DEU"),
    )

    allocations = tlp.Allocations(
        allocations={
            (sup_low, bf_pc, tlp.Commodity(name="io_low")): 160.0,
            (sup_mid, bf_pc, tlp.Commodity(name="io_mid")): 150.0,
            (bf_pc, sink_pc, tlp.Commodity(name="hot_metal")): 200.0,
        },
    )

    connector.create_graph(allocations)
    connector.propage_cost_forward_by_layers_and_normalize()
    connector.update_bill_of_materials([furnace_group])

    bom = furnace_group.bill_of_materials

    energy = bom["energy"]["coking_coal"]
    assert energy["total_cost"] == pytest.approx(18_600.0)
    # Carrier quantity: 100 t product x 0.48 t/t + 100 t x 0.45 t/t
    assert energy["demand"] == pytest.approx(93.0)
    assert energy["unit_cost"] == pytest.approx(93.0)

    materials = bom["materials"]
    assert materials["io_low"]["demand"] == pytest.approx(160.0)
    assert materials["io_low"]["total_cost"] == pytest.approx(50.0 * 160.0 + 60.0 * 160.0)
    assert materials["io_mid"]["total_cost"] == pytest.approx(40.0 * 150.0 + 60.0 * 150.0)

    # Downstream: the BF's outgoing hot-metal unit cost embeds converted energy:
    # (materials 14,000 + energy 18,600) / 200 t = 163 USD/t.
    assert connector.G.nodes["plant_bf1"]["unit_cost"]["hot_metal"] == pytest.approx(163.0)


def test_energy_booking_validator_detects_unit_regression():
    """Reintroducing an undivided (per-product) edge cost must trip the two-leg check."""
    furnace_group = _make_bf_furnace_group()
    plant = SimpleNamespace(plant_id="plant", furnace_groups=[furnace_group])
    connector = TM_PAM_connector(
        dynamic_feedstocks_classes={},
        plants=_StubRepo([plant]),
        transport_kpis=None,
    )

    sup_low = tlp.ProcessCenter(
        name="sup_low",
        process=tlp.Process(name="io_low_supply", type=tlp.ProcessType.SUPPLY, bill_of_materials=[]),
        capacity=1000.0,
        location=_location("AUS"),
        production_cost=50.0,
    )
    bf_pc = tlp.ProcessCenter(
        name="plant_bf1",
        process=tlp.Process(name="BF", type=tlp.ProcessType.PRODUCTION, bill_of_materials=[]),
        capacity=500.0,
        location=_location("DEU"),
        production_cost=0.0,
    )
    sink_pc = tlp.ProcessCenter(
        name="deu_demand",
        process=tlp.Process(name="demand", type=tlp.ProcessType.DEMAND, bill_of_materials=[]),
        capacity=1000.0,
        location=_location("DEU"),
    )

    allocations = tlp.Allocations(
        allocations={
            (sup_low, bf_pc, tlp.Commodity(name="io_low")): 160.0,
            (bf_pc, sink_pc, tlp.Commodity(name="hot_metal")): 100.0,
        },
    )

    connector.create_graph(allocations)
    for _, _, key, data in connector.G.edges(keys=True, data=True):
        if key == "io_low":
            data["processing_energy_breakdown"] = {"coking_coal": 96.0}  # per-product, undivided
    connector.propage_cost_forward_by_layers_and_normalize()

    # Capture on the function's own logger — immune to other tests' logging reconfiguration.
    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[method-assign]
    target = logging.getLogger("steelo.domain.trade_modelling.TM_PAM_connector.update_bill_of_materials")
    target.addHandler(handler)
    target.disabled = False
    try:
        connector.update_bill_of_materials([furnace_group])
    finally:
        target.removeHandler(handler)

    assert any(
        "Energy booking mismatch" in record.getMessage() and "plant_bf1" in record.getMessage() for record in records
    )


def test_snapshot_raises_on_missing_carrier_breakdown():
    """A charge with an energy total but no per-carrier breakdown cannot be booked by carrier."""
    furnace_group = _make_bf_furnace_group()
    furnace_group.energy_vopex_breakdown_by_input = {"io_low": {"coking_coal": 96.0}}  # io_mid missing

    plant = SimpleNamespace(plant_id="plant", furnace_groups=[furnace_group])
    with pytest.raises(ValueError, match="io_mid.*no per-carrier breakdown"):
        TM_PAM_connector(
            dynamic_feedstocks_classes={},
            plants=_StubRepo([plant]),
            transport_kpis=None,
        )


def test_snapshot_raises_on_conflicting_required_quantities():
    """Two feedstock rows for the same charge with different req_qty must fail loudly, not
    silently convert with whichever row came last."""
    furnace_group = _make_bf_furnace_group()
    furnace_group.effective_primary_feedstocks.append(
        SimpleNamespace(
            metallic_charge="io_low",
            required_quantity_per_ton_of_product=1.4,
            energy_requirements={"coking_coal": 0.48},
            secondary_feedstock={},
        ),
    )

    plant = SimpleNamespace(plant_id="plant", furnace_groups=[furnace_group])
    with pytest.raises(ValueError, match="conflicting required_quantity_per_ton_of_product.*io_low"):
        TM_PAM_connector(
            dynamic_feedstocks_classes={},
            plants=_StubRepo([plant]),
            transport_kpis=None,
        )


def test_multi_commodity_source_energy_not_double_booked():
    """Two commodities from ONE source node must each book their energy exactly once."""
    furnace_group = _make_bf_furnace_group()
    plant = SimpleNamespace(plant_id="plant", furnace_groups=[furnace_group])
    connector = TM_PAM_connector(
        dynamic_feedstocks_classes={},
        plants=_StubRepo([plant]),
        transport_kpis=None,
    )

    dual_supplier = tlp.ProcessCenter(
        name="sup_dual",
        process=tlp.Process(name="dual_supply", type=tlp.ProcessType.SUPPLY, bill_of_materials=[]),
        capacity=1000.0,
        location=_location("AUS"),
        production_cost=50.0,
    )
    bf_pc = tlp.ProcessCenter(
        name="plant_bf1",
        process=tlp.Process(name="BF", type=tlp.ProcessType.PRODUCTION, bill_of_materials=[]),
        capacity=500.0,
        location=_location("DEU"),
        production_cost=0.0,
    )
    sink_pc = tlp.ProcessCenter(
        name="deu_demand",
        process=tlp.Process(name="demand", type=tlp.ProcessType.DEMAND, bill_of_materials=[]),
        capacity=1000.0,
        location=_location("DEU"),
    )

    # Both charges arrive from the SAME source node -> two parallel edges sup_dual -> plant_bf1.
    allocations = tlp.Allocations(
        allocations={
            (dual_supplier, bf_pc, tlp.Commodity(name="io_low")): 160.0,
            (dual_supplier, bf_pc, tlp.Commodity(name="io_mid")): 150.0,
            (bf_pc, sink_pc, tlp.Commodity(name="hot_metal")): 200.0,
        },
    )

    connector.create_graph(allocations)
    connector.propage_cost_forward_by_layers_and_normalize()
    connector.update_bill_of_materials([furnace_group])

    energy = furnace_group.bill_of_materials["energy"]["coking_coal"]
    assert energy["total_cost"] == pytest.approx(18_600.0)  # 96 x 100 + 90 x 100, once each


def test_cost_propagation_is_allocation_order_independent():
    """A node's outgoing cost must include ALL upstream legs regardless of allocation order.

    Diamond topology: scrap root -> BOF, ore root -> BF -> BOF, BOF -> demand. BFS
    discovery order processed the BOF before the BF whenever the scrap edge came first
    in the allocations dict, dropping the entire hot-metal leg from the BOF's unit cost
    (150 instead of 310 USD/t). Topological ordering must give 310 either way.
    """

    def _pc(name, process_type, cost=None):
        return tlp.ProcessCenter(
            name=name,
            process=tlp.Process(name=name, type=process_type, bill_of_materials=[]),
            capacity=1000.0,
            location=_location("DEU"),
            production_cost=cost,
        )

    scrap_sup = _pc("scrap_sup", tlp.ProcessType.SUPPLY, 300.0)
    ore_sup = _pc("ore_sup", tlp.ProcessType.SUPPLY, 100.0)
    bf_pc = _pc("bf1", tlp.ProcessType.PRODUCTION, 0.0)
    bof_pc = _pc("bof1", tlp.ProcessType.PRODUCTION, 0.0)
    demand_pc = _pc("dem", tlp.ProcessType.DEMAND)

    edges = [
        ((scrap_sup, bof_pc, tlp.Commodity(name="scrap")), 500.0),
        ((ore_sup, bf_pc, tlp.Commodity(name="io_low")), 1600.0),
        ((bf_pc, bof_pc, tlp.Commodity(name="hot_metal")), 500.0),
        ((bof_pc, demand_pc, tlp.Commodity(name="steel")), 1000.0),
    ]

    for order in (edges, [edges[1], edges[0], *edges[2:]]):
        connector = TM_PAM_connector(
            dynamic_feedstocks_classes={},
            plants=_StubRepo([]),
            transport_kpis=None,
        )
        connector.create_graph(tlp.Allocations(allocations=dict(order)))
        connector.propage_cost_forward_by_layers_and_normalize()

        # (scrap 500 t x 300 + ore 1600 t x 100) / 1000 t steel = 310 USD/t
        assert connector.G.nodes["bof1"]["unit_cost"]["steel"] == pytest.approx(310.0)


def test_utilization_correction_refreshes_shares_and_energy():
    """After a min-share correction the BOM must stay internally consistent.

    hot_metal fixed at 400 t against a 70 % minimum -> production 1000 -> 571.43 t,
    scrap 600 -> 171.43 t. demand_share_pct must land exactly on 0.7 / 0.3 and the
    energy entries must equal demand/req x intensity (quantities) and x vopex (costs)
    for the corrected mix, not a stale or uniformly scaled copy.
    """
    furnace_group = SimpleNamespace(
        furnace_group_id="plant_bof1",
        technology=SimpleNamespace(name="BOF", product="steel"),
        capacity=1000.0,
        allocated_volumes=1000.0,
        utilization_rate=1.0,
        chosen_reductant="none",
        energy_vopex_breakdown_by_input={
            "hot_metal": {"electricity": 10.0},
            "scrap": {"electricity": 20.0},
        },
        effective_primary_feedstocks=[
            SimpleNamespace(
                metallic_charge="hot_metal",
                required_quantity_per_ton_of_product=1.0,
                energy_requirements={"electricity": 0.5},
                secondary_feedstock={},
            ),
            SimpleNamespace(
                metallic_charge="scrap",
                required_quantity_per_ton_of_product=1.0,
                energy_requirements={"electricity": 0.8},
                secondary_feedstock={},
            ),
        ],
        bill_of_materials={
            "materials": {
                "hot_metal": {
                    "demand": 400.0,
                    "total_cost": 40_000.0,
                    "total_material_cost": 40_000.0,
                    "unit_cost": 40.0,
                    "unit_material_cost": 40.0,
                    "product_volume": 1000.0,
                    "demand_share_pct": 0.4,
                },
                "scrap": {
                    "demand": 600.0,
                    "total_cost": 30_000.0,
                    "total_material_cost": 30_000.0,
                    "unit_cost": 30.0,
                    "unit_material_cost": 30.0,
                    "product_volume": 1000.0,
                    "demand_share_pct": 0.6,
                },
            },
            "energy": {
                "electricity": {
                    "demand": 680.0,
                    "total_cost": 16_000.0,
                    "unit_cost": 16.0,
                    "product_volume": 1000.0,
                }
            },
        },
    )
    furnace_group.set_allocated_volumes = lambda v: setattr(furnace_group, "allocated_volumes", v)

    connector = TM_PAM_connector(
        dynamic_feedstocks_classes={},
        plants=_StubRepo([]),
        transport_kpis=None,
    )
    corrected = connector.correct_utilization_for_supply_constraints(
        furnace_groups=[furnace_group],
        bom_issues=[
            {
                "fg_id": "plant_bof1",
                "check": "min_share",
                "commodity": "hot_metal",
                "actual_share": 0.4,
                "required_share": 0.7,
            }
        ],
    )
    assert corrected == 1

    new_production = 400.0 / 0.7
    assert furnace_group.allocated_volumes == pytest.approx(new_production)

    materials = furnace_group.bill_of_materials["materials"]
    assert materials["hot_metal"]["demand"] == pytest.approx(400.0)
    assert materials["scrap"]["demand"] == pytest.approx(new_production - 400.0)
    assert materials["hot_metal"]["demand_share_pct"] == pytest.approx(0.7)
    assert materials["scrap"]["demand_share_pct"] == pytest.approx(0.3)
    assert materials["scrap"]["total_material_cost"] == pytest.approx(30_000.0 * (new_production - 400.0) / 600.0)
    assert materials["scrap"]["unit_material_cost"] == pytest.approx(
        materials["scrap"]["total_material_cost"] / new_production
    )

    energy = furnace_group.bill_of_materials["energy"]["electricity"]
    scrap_product_tonnes = new_production - 400.0
    assert energy["demand"] == pytest.approx(400.0 * 0.5 + scrap_product_tonnes * 0.8)
    assert energy["total_cost"] == pytest.approx(400.0 * 10.0 + scrap_product_tonnes * 20.0)
    assert energy["unit_cost"] == pytest.approx(energy["total_cost"] / new_production)
    assert energy["product_volume"] == pytest.approx(new_production)
