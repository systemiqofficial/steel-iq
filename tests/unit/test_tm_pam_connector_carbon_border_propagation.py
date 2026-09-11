"""Tests for carbon border charge propagation through TM_PAM_connector.

The LP records a per-arc adjustment on Allocations; the connector stamps it on the edge and books it
into the receiving node's MaterialCost exactly like a tariff.
"""

import pytest

from steelo.adapters.repositories.in_memory_repository import PlantInMemoryRepository
from steelo.domain.trade_modelling import trade_lp_modelling as tlp
from steelo.domain.trade_modelling.TM_PAM_connector import TM_PAM_connector


class DummyLocation:
    def __init__(self, iso3: str):
        self.iso3 = iso3
        self.country = iso3
        self.lat = 40.0
        self.lon = -74.0


def _make_process_center(name: str, iso3: str, process_type: tlp.ProcessType, process_name: str, cost: float = 0.0):
    process = tlp.Process(name=process_name, type=process_type, bill_of_materials=[])
    return tlp.ProcessCenter(
        name=name, process=process, capacity=1000.0, location=DummyLocation(iso3), production_cost=cost
    )


def _make_connector() -> TM_PAM_connector:
    return TM_PAM_connector(dynamic_feedstocks_classes={}, plants=PlantInMemoryRepository(), transport_kpis=None)


def _chain(charge: float | None):
    """Supplier in CHN -> furnace in DEU -> demand in DEU, with an optional charge on the import arc."""
    supplier_pc = _make_process_center("sup_chn", "CHN", tlp.ProcessType.SUPPLY, "io_low_supply", cost=70.0)
    furnace_pc = _make_process_center("plant_deu_bf", "DEU", tlp.ProcessType.PRODUCTION, "bf")
    demand_pc = _make_process_center("deu_demand", "DEU", tlp.ProcessType.DEMAND, "demand")
    io_low = tlp.Commodity(name="io_low")
    steel = tlp.Commodity(name="steel")
    allocations = tlp.Allocations(
        allocations={(supplier_pc, furnace_pc, io_low): 100.0, (furnace_pc, demand_pc, steel): 100.0},
        carbon_border_charges=None if charge is None else {(supplier_pc, furnace_pc, io_low): charge},
    )
    return allocations, furnace_pc


def test_carbon_border_charge_stored_as_edge_attribute():
    """The recorded per-arc charge is stamped on the graph edge."""
    allocations, _ = _chain(charge=12.0)

    connector = _make_connector()
    connector.create_graph(allocations)

    assert connector.G["sup_chn"]["plant_deu_bf"]["io_low"]["carbon_border_cost"] == 12.0


def test_carbon_border_charge_zero_when_absent():
    """An arc without a recorded charge carries none, whether the dict is missing or lacks the arc."""
    allocations, furnace_pc = _chain(charge=None)
    connector = _make_connector()
    connector.create_graph(allocations)
    assert connector.G["sup_chn"]["plant_deu_bf"]["io_low"]["carbon_border_cost"] == 0.0

    other_pc = _make_process_center("plant_fra_bf", "FRA", tlp.ProcessType.PRODUCTION, "bf")
    allocations.carbon_border_charges = {(other_pc, furnace_pc, tlp.Commodity(name="io_low")): 12.0}
    connector = _make_connector()
    connector.create_graph(allocations)
    assert connector.G["sup_chn"]["plant_deu_bf"]["io_low"]["carbon_border_cost"] == 0.0


def test_carbon_border_charge_propagates_into_material_cost():
    """MaterialCost at the importer is (base + transport + tariff + charge) times volume."""
    allocations, _ = _chain(charge=12.0)

    connector = _make_connector()
    connector.create_graph(allocations)
    connector.propage_cost_forward_by_layers_and_normalize()

    alloc = connector.G.nodes["plant_deu_bf"]["allocations"]["io_low"]
    assert alloc["MaterialCost"] == pytest.approx((70.0 + 12.0) * 100.0)
    assert alloc["Cost"] == pytest.approx((70.0 + 12.0) * 100.0)


def test_negative_carbon_border_charge_reduces_material_cost():
    """An export rebate is a negative charge and lowers the importer's MaterialCost."""
    allocations, _ = _chain(charge=-12.0)

    connector = _make_connector()
    connector.create_graph(allocations)
    connector.propage_cost_forward_by_layers_and_normalize()

    alloc = connector.G.nodes["plant_deu_bf"]["allocations"]["io_low"]
    assert alloc["MaterialCost"] == pytest.approx((70.0 - 12.0) * 100.0)
