import copy
import networkx as nx
import pytest
from types import SimpleNamespace

from steelo.domain.trade_modelling.TM_PAM_connector import TM_PAM_connector


class _StubRepo:
    def __init__(self, plants):
        self._plants = plants

    def list(self):
        return self._plants


class _StubPlant:
    def __init__(self, plant_id, furnace_groups):
        self.plant_id = plant_id
        self.furnace_groups = furnace_groups


class _StubFurnaceGroup:
    def __init__(self):
        self.furnace_group_id = "plant_fg1"
        self.technology = SimpleNamespace(name="EAF", product="steel")
        self.status = "operating"
        self.energy_vopex_by_input = {"hbi_low": 120.0}
        self.energy_vopex_breakdown_by_input = {"hbi_low": {"electricity": 70.0, "hydrogen": 50.0}}
        self.energy_vopex_by_carrier = {"electricity": 70.0, "hydrogen": 50.0}
        self.chosen_reductant = "hydrogen"
        self.bill_of_materials = {}
        self.effective_primary_feedstocks = [
            SimpleNamespace(metallic_charge="hbi_low", required_quantity_per_ton_of_product=1.2),
        ]


def test_snapshot_converts_energy_costs_to_per_input_tonne():
    """The connector snapshot divides per-product energy costs by each charge's req_qty."""
    furnace_group = _StubFurnaceGroup()
    plant = _StubPlant("plant", [furnace_group])
    repo = _StubRepo([plant])

    connector = TM_PAM_connector(dynamic_feedstocks_classes={}, plants=repo, transport_kpis=None)

    entry = connector.processing_energy_cost["plant_fg1"]["hbi_low"]
    assert entry["total"] == pytest.approx(120.0 / 1.2)
    assert entry["carriers"]["electricity"] == pytest.approx(70.0 / 1.2)
    assert entry["carriers"]["hydrogen"] == pytest.approx(50.0 / 1.2)


def test_snapshot_divides_each_charge_by_its_own_req_qty():
    """Multi-charge furnace groups convert per charge, not with a blended factor."""
    furnace_group = _StubFurnaceGroup()
    furnace_group.energy_vopex_by_input = {"io_low": 96.0, "io_mid": 90.0}
    furnace_group.energy_vopex_breakdown_by_input = {
        "io_low": {"coking_coal": 96.0},
        "io_mid": {"coking_coal": 90.0},
    }
    furnace_group.effective_primary_feedstocks = [
        SimpleNamespace(metallic_charge="io_low", required_quantity_per_ton_of_product=1.6),
        SimpleNamespace(metallic_charge="io_mid", required_quantity_per_ton_of_product=1.5),
    ]
    plant = _StubPlant("plant", [furnace_group])
    repo = _StubRepo([plant])

    connector = TM_PAM_connector(dynamic_feedstocks_classes={}, plants=repo, transport_kpis=None)

    per_feed = connector.processing_energy_cost["plant_fg1"]
    assert per_feed["io_low"]["total"] == pytest.approx(60.0)
    assert per_feed["io_mid"]["total"] == pytest.approx(60.0)
    assert per_feed["io_low"]["carriers"]["coking_coal"] == pytest.approx(60.0)
    assert per_feed["io_mid"]["carriers"]["coking_coal"] == pytest.approx(60.0)


def test_snapshot_raises_when_charge_has_no_feedstock_row():
    """A charge carrying energy cost without a req_qty row must fail loudly."""
    furnace_group = _StubFurnaceGroup()
    furnace_group.effective_primary_feedstocks = []
    plant = _StubPlant("plant", [furnace_group])
    repo = _StubRepo([plant])

    with pytest.raises(ValueError, match="hbi_low"):
        TM_PAM_connector(dynamic_feedstocks_classes={}, plants=repo, transport_kpis=None)


def test_update_bill_of_materials_uses_energy_carriers():
    furnace_group = _StubFurnaceGroup()
    plant = _StubPlant("plant", [furnace_group])
    repo = _StubRepo([plant])

    connector = TM_PAM_connector(dynamic_feedstocks_classes={}, plants=repo, transport_kpis=None)

    furnace_group.production = 10.0

    connector.G = nx.MultiDiGraph()
    connector.G.add_node(
        "supplier_node",
        product_cost={},
        unit_cost={},
        allocations={},
        export={},
    )
    connector.G.add_node(
        furnace_group.furnace_group_id,
        process="eaf_hbi_low",
        allocations={"hbi_low": {"Volume": 12.0, "Cost": 1000.0}},
        export={},
        unit_cost={},
        product_cost={},
    )
    # 12 t of input at req_qty 1.2 -> 10 t of product; edge costs come from the
    # connector's own converted (per-input-tonne) snapshot, as create_graph stamps them.
    snapshot = connector.processing_energy_cost[furnace_group.furnace_group_id]["hbi_low"]
    connector.G.add_edge(
        "supplier_node",
        furnace_group.furnace_group_id,
        key="hbi_low",
        volume=12.0,
        processing_energy_cost=snapshot["total"],
        processing_energy_breakdown=snapshot["carriers"],
        commodity="hbi_low",
    )

    connector.update_bill_of_materials([furnace_group])

    energy_block = furnace_group.bill_of_materials["energy"]

    assert set(energy_block.keys()) == {"electricity", "hydrogen"}
    assert energy_block["electricity"]["unit_cost"] == pytest.approx(70.0)
    assert energy_block["hydrogen"]["unit_cost"] == pytest.approx(50.0)
    assert energy_block["electricity"]["total_cost"] == pytest.approx(700.0)
    assert energy_block["hydrogen"]["total_cost"] == pytest.approx(500.0)


def test_update_bill_of_materials_populates_materials_without_energy():
    furnace_group = _StubFurnaceGroup()
    plant = _StubPlant("plant", [furnace_group])
    repo = _StubRepo([plant])

    connector = TM_PAM_connector(dynamic_feedstocks_classes={}, plants=repo, transport_kpis=None)

    connector.G = nx.MultiDiGraph()
    connector.G.add_node(
        "supplier_node",
        product_cost={},
        unit_cost={},
        allocations={},
        export={},
    )
    connector.G.add_node(
        furnace_group.furnace_group_id,
        process="eaf_hbi_low",
        allocations={"hbi_low": {"Volume": 10.0, "Cost": 1000.0}},
        export={},
        unit_cost={},
        product_cost={},
    )
    connector.G.add_edge(
        "supplier_node",
        furnace_group.furnace_group_id,
        key="hbi_low",
        volume=10.0,
        commodity="hbi_low",
    )

    connector.update_bill_of_materials([furnace_group])

    materials_block = furnace_group.bill_of_materials["materials"]

    assert "hbi_low" in materials_block
    assert materials_block["hbi_low"]["demand"] == pytest.approx(10.0)
    assert materials_block["hbi_low"]["total_cost"] == pytest.approx(1000.0)
    assert materials_block["hbi_low"]["unit_cost"] == pytest.approx(100.0)
    assert furnace_group.bill_of_materials["energy"] == {}


def test_update_bill_of_materials_preserves_existing_bom_when_trade_empty():
    furnace_group = _StubFurnaceGroup()
    original_bom = {
        "materials": {
            "io_low": {
                "demand": 200.0,
                "total_cost": 60000.0,
                "unit_cost": 300.0,
                "product_volume": 150.0,
            }
        },
        "energy": {
            "electricity": {
                "demand": 400.0,
                "total_cost": 32000.0,
                "unit_cost": 80.0,
                "product_volume": 150.0,
            }
        },
    }
    furnace_group.bill_of_materials = copy.deepcopy(original_bom)
    furnace_group.production = 150.0

    plant = _StubPlant("plant", [furnace_group])
    repo = _StubRepo([plant])

    connector = TM_PAM_connector(dynamic_feedstocks_classes={}, plants=repo, transport_kpis=None)
    connector.G = nx.MultiDiGraph()
    connector.G.add_node(
        furnace_group.furnace_group_id,
        process="dri_esf_ccs",
        allocations={},
        export={},
        unit_cost={},
        product_cost={},
    )

    connector.update_bill_of_materials([furnace_group])

    assert furnace_group.bill_of_materials == original_bom


def test_update_bill_of_materials_initializes_empty_bom_when_no_existing_and_no_trade():
    furnace_group = _StubFurnaceGroup()
    furnace_group.bill_of_materials = None
    furnace_group.production = 0.0

    plant = _StubPlant("plant", [furnace_group])
    repo = _StubRepo([plant])

    connector = TM_PAM_connector(dynamic_feedstocks_classes={}, plants=repo, transport_kpis=None)
    connector.G = nx.MultiDiGraph()
    connector.G.add_node(
        furnace_group.furnace_group_id,
        process="dri_esf_ccs",
        allocations={},
        export={},
        unit_cost={},
        product_cost={},
    )

    connector.update_bill_of_materials([furnace_group])

    assert furnace_group.bill_of_materials == {"materials": {}, "energy": {}}
