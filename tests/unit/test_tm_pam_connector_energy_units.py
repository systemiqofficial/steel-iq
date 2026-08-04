"""End-to-end unit check for the TM->PAM energy-cost conversion.

Two suppliers feed a two-charge blast furnace that exports hot metal to a demand
sink. Energy costs are authored per tonne of product; edge volumes flow in input
tonnes. The connector must convert once at the snapshot so that booked BOM energy
equals per-product cost x product tonnes, and the downstream node's material cost
embeds the converted (not inflated) energy.
"""

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
            SimpleNamespace(metallic_charge="io_low", required_quantity_per_ton_of_product=1.6),
            SimpleNamespace(metallic_charge="io_mid", required_quantity_per_ton_of_product=1.5),
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
    assert energy["demand"] == pytest.approx(310.0)
    assert energy["unit_cost"] == pytest.approx(93.0)

    materials = bom["materials"]
    assert materials["io_low"]["demand"] == pytest.approx(160.0)
    assert materials["io_low"]["total_cost"] == pytest.approx(50.0 * 160.0 + 60.0 * 160.0)
    assert materials["io_mid"]["total_cost"] == pytest.approx(40.0 * 150.0 + 60.0 * 150.0)

    # Downstream: the BF's outgoing hot-metal unit cost embeds converted energy:
    # (materials 14,000 + energy 18,600) / 200 t = 163 USD/t.
    assert connector.G.nodes["plant_bf1"]["unit_cost"]["hot_metal"] == pytest.approx(163.0)


def test_energy_booking_validator_detects_unit_regression():
    """Reintroducing an undivided (per-product) edge cost must fail the two-leg check."""
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

    with pytest.raises(ValueError, match="plant_bf1"):
        connector.update_bill_of_materials([furnace_group])
