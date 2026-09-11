"""Tests for embedded direct emissions and carbon already paid propagating through TM_PAM_connector.

Each producing node ships its own-stage intensity and carbon cost plus everything upstream of it;
the receiving node's totals, spread over its exports, become its upstream values for the next year.
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


class StubFurnaceGroup:
    def __init__(self, fg_id: str, utilization_rate: float = 0.8):
        self.furnace_group_id = fg_id
        self.utilization_rate = utilization_rate
        self.upstream_emission_intensity = -1.0
        self.upstream_carbon_cost_paid = -1.0


def _supplier(name: str, iso3: str = "AUS") -> tlp.ProcessCenter:
    process = tlp.Process(name="io_low_supply", type=tlp.ProcessType.SUPPLY, bill_of_materials=[])
    return tlp.ProcessCenter(
        name=name, process=process, capacity=1000.0, location=DummyLocation(iso3), production_cost=70.0
    )


def _producer(name: str, iso3: str, process_name: str, intensity: float, carbon_cost: float) -> tlp.ProcessCenter:
    process = tlp.Process(name=process_name, type=tlp.ProcessType.PRODUCTION, bill_of_materials=[])
    return tlp.ProcessCenter(
        name=name,
        process=process,
        capacity=1000.0,
        location=DummyLocation(iso3),
        production_cost=carbon_cost,
        emission_intensity=intensity,
    )


def _demand(name: str, iso3: str = "DEU") -> tlp.ProcessCenter:
    process = tlp.Process(name="demand", type=tlp.ProcessType.DEMAND, bill_of_materials=[])
    return tlp.ProcessCenter(name=name, process=process, capacity=1000.0, location=DummyLocation(iso3))


def _propagate(allocations: tlp.Allocations) -> TM_PAM_connector:
    connector = TM_PAM_connector(dynamic_feedstocks_classes={}, plants=PlantInMemoryRepository(), transport_kpis=None)
    connector.create_graph(allocations)
    connector.propage_cost_forward_by_layers_and_normalize()
    return connector


IO_LOW, HOT_METAL, STEEL, SLAB = (tlp.Commodity(name) for name in ("io_low", "hot_metal", "steel", "slab"))


def test_bf_emissions_embed_into_bof_upstream_fields():
    """The BOF's upstream values are the BF's own intensity and carbon cost; supplier inputs carry nothing."""
    ore, bf, bof, demand = (
        _supplier("sup"),
        _producer("bf", "CHN", "bf", 1.8, 200.0),
        _producer("bof", "DEU", "bof", 0.0, 0.0),
        _demand("dem"),
    )
    connector = _propagate(
        tlp.Allocations(
            allocations={(ore, bf, IO_LOW): 100.0, (bf, bof, HOT_METAL): 100.0, (bof, demand, STEEL): 100.0}
        )
    )

    assert connector.G.nodes["bf"]["upstream_emission_intensity"] == 0.0
    assert connector.G.nodes["bf"]["upstream_carbon_cost_paid"] == 0.0
    assert connector.G.nodes["bof"]["upstream_emission_intensity"] == pytest.approx(1.8)
    assert connector.G.nodes["bof"]["upstream_carbon_cost_paid"] == pytest.approx(200.0)

    bof_fg = StubFurnaceGroup("bof")
    connector.update_furnace_group_embedded_carbon([bof_fg])
    assert (bof_fg.upstream_emission_intensity, bof_fg.upstream_carbon_cost_paid) == (
        pytest.approx(1.8),
        pytest.approx(200.0),
    )


def test_two_stage_chain_accumulates_own_and_upstream():
    """A third stage sees the BOF's own values added to what the BOF itself received."""
    ore, bf, bof, mill, demand = (
        _supplier("sup"),
        _producer("bf", "CHN", "bf", 1.8, 200.0),
        _producer("bof", "CHN", "bof", 0.2, 10.0),
        _producer("mill", "DEU", "mill", 0.0, 0.0),
        _demand("dem"),
    )
    connector = _propagate(
        tlp.Allocations(
            allocations={
                (ore, bf, IO_LOW): 100.0,
                (bf, bof, HOT_METAL): 100.0,
                (bof, mill, SLAB): 100.0,
                (mill, demand, STEEL): 100.0,
            }
        )
    )

    assert connector.G.nodes["mill"]["upstream_emission_intensity"] == pytest.approx(2.0)
    assert connector.G.nodes["mill"]["upstream_carbon_cost_paid"] == pytest.approx(210.0)


def test_inbound_border_charge_counts_as_carbon_paid():
    """A border charge paid on the inbound arc is carbon already paid at the receiver; intensity is unchanged."""
    ore, bf, bof, demand = (
        _supplier("sup"),
        _producer("bf", "CHN", "bf", 1.8, 200.0),
        _producer("bof", "DEU", "bof", 0.0, 0.0),
        _demand("dem"),
    )
    connector = _propagate(
        tlp.Allocations(
            allocations={(ore, bf, IO_LOW): 100.0, (bf, bof, HOT_METAL): 100.0, (bof, demand, STEEL): 100.0},
            carbon_border_charges={(bf, bof, HOT_METAL): 32.0},
        )
    )

    assert connector.G.nodes["bof"]["upstream_emission_intensity"] == pytest.approx(1.8)
    assert connector.G.nodes["bof"]["upstream_carbon_cost_paid"] == pytest.approx(232.0)


def test_multi_output_producer_normalises_by_total_export():
    """Inbound totals are spread over all of a node's exports, not over each commodity separately."""
    ore, sinter, bf, bof, demand_iron, demand_steel = (
        _supplier("sup"),
        _producer("sinter", "CHN", "sinter", 1.0, 10.0),
        _producer("bf", "CHN", "bf", 0.5, 5.0),
        _producer("bof", "CHN", "bof", 0.0, 0.0),
        _demand("dem_iron"),
        _demand("dem_steel"),
    )
    pig_iron = tlp.Commodity("pig_iron")
    connector = _propagate(
        tlp.Allocations(
            allocations={
                (ore, sinter, IO_LOW): 200.0,
                (sinter, bf, IO_LOW): 200.0,
                (bf, bof, HOT_METAL): 100.0,
                (bf, demand_iron, pig_iron): 100.0,
                (bof, demand_steel, STEEL): 100.0,
            }
        )
    )

    # sinter ships 200 t at (1.0, 10); bf spreads 200 tCO2 and 2000 USD over 200 t of total export
    assert connector.G.nodes["bf"]["upstream_emission_intensity"] == pytest.approx(1.0)
    assert connector.G.nodes["bf"]["upstream_carbon_cost_paid"] == pytest.approx(10.0)
    assert connector.G.nodes["bof"]["upstream_emission_intensity"] == pytest.approx(1.5)
    assert connector.G.nodes["bof"]["upstream_carbon_cost_paid"] == pytest.approx(15.0)


def test_idle_and_absent_furnace_groups_are_zeroed():
    """Furnace groups outside the graph, without inbound edges, or idle get 0.0 for both fields."""
    ore, bf, bof, demand = (
        _supplier("sup"),
        _producer("bf", "CHN", "bf", 1.8, 200.0),
        _producer("bof", "DEU", "bof", 0.0, 0.0),
        _demand("dem"),
    )
    connector = _propagate(
        tlp.Allocations(
            allocations={(ore, bf, IO_LOW): 100.0, (bf, bof, HOT_METAL): 100.0, (bof, demand, STEEL): 100.0}
        )
    )
    absent, idle, source_only = (
        StubFurnaceGroup("nowhere"),
        StubFurnaceGroup("bof", utilization_rate=0.0),
        StubFurnaceGroup("sup"),
    )

    connector.update_furnace_group_embedded_carbon([absent, idle, source_only])

    for fg in (absent, idle, source_only):
        assert (fg.upstream_emission_intensity, fg.upstream_carbon_cost_paid) == (0.0, 0.0)
