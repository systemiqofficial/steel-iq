"""Tests for intra-year handler hooks that update the CO2 storage counters.

Exercises the three counter-writer handler branches wired in Stage 1:
- ``update_status_of_furnace_group``: announced -> construction, announced -> discarded
- ``update_capacity_buildout``: expansion path (``is_new_plant=False``)
- ``change_furnace_group_status_to_switching_technology``: non-CCS -> CCS switch

All writers use ``fg.chosen_reductant`` so the handler<->scan invariant holds.
"""

from dataclasses import dataclass
from pathlib import Path

import pytest

from steelo.domain import Year, events
from steelo.domain.commands import (
    ChangeFurnaceGroupStatusToSwitchingTechnology,
    ChangeFurnaceGroupTechnology,
    UpdateFurnaceGroupStatus,
)
from steelo.domain.models import (
    Environment,
    FurnaceGroup,
    Location,
    Plant,
    PointInTime,
    PrimaryFeedstock,
    Technology,
    TimeFrame,
)
from steelo.service_layer.handlers import (
    change_furnace_group_status_to_switching_technology,
    update_capacity_buildout,
    update_status_of_furnace_group,
)
from steelo.simulation import SimulationConfig
from steelo.simulation_types import TechnologySettings


# ---- Fixtures ----


@dataclass
class FakePlantsRepo:
    plants_by_id: dict

    def get(self, plant_id):
        return self.plants_by_id[plant_id]

    def list(self):
        return list(self.plants_by_id.values())


class FakeUoW:
    def __init__(self, plants: list[Plant]):
        self.plants = FakePlantsRepo({p.plant_id: p for p in plants})
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def commit(self):
        self.committed = True


def _make_env(tmp_path: Path) -> Environment:
    tech_settings = {
        "BF": TechnologySettings(allowed=True, from_year=2025, to_year=None),
        "BFCCS": TechnologySettings(allowed=True, from_year=2030, to_year=None),
    }
    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2060),
        master_excel_path=Path("test.xlsx"),
        output_dir=tmp_path,
        technology_settings=tech_settings,
        capacity_limit=0.95,
        co2_storage_reserved_discount_factor=0.9,
        construction_time=4,
        plant_lifetime=20,
        equity_share=0.2,
    )
    tech_switches_csv = tmp_path / "tech_switches_allowed.csv"
    tech_switches_csv.write_text("origin,BF,BFCCS\nBF,YES,YES\n", encoding="utf-8")
    env = Environment(config=config, tech_switches_csv=tech_switches_csv)
    return env


def _make_ccs_tech(name: str = "BFCCS", reductant: str = "Coke+PCI", co2_stored: float = 2.0) -> Technology:
    pf = PrimaryFeedstock(metallic_charge="IO_low", reductant=reductant, technology=name)
    pf.add_carbon_output("co2_stored", co2_stored)
    return Technology(
        name=name,
        product="hot_metal",
        dynamic_business_case=[pf],
        capex=500.0,
        capex_no_subsidy=500.0,
    )


def _make_non_ccs_tech(name: str = "BF", reductant: str = "Coke+PCI") -> Technology:
    pf = PrimaryFeedstock(metallic_charge="IO_low", reductant=reductant, technology=name)
    return Technology(
        name=name,
        product="hot_metal",
        dynamic_business_case=[pf],
        capex=300.0,
        capex_no_subsidy=300.0,
    )


def _make_fg(
    fg_id: str,
    technology: Technology,
    capacity: float,
    status: str,
    chosen_reductant: str = "Coke+PCI",
) -> FurnaceGroup:
    return FurnaceGroup(
        furnace_group_id=fg_id,
        capacity=capacity,
        status=status,
        last_renovation_date=None,
        technology=technology,
        historical_production={},
        utilization_rate=0.8,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2025), end=Year(2045)),
            plant_lifetime=20,
        ),
        chosen_reductant=chosen_reductant,
        balance=1_000_000.0,
    )


def _make_plant(plant_id: str, iso3: str, furnace_groups: list[FurnaceGroup]) -> Plant:
    return Plant(
        plant_id=plant_id,
        location=Location(lat=0.0, lon=0.0, country=iso3, region="Region", iso3=iso3),
        furnace_groups=furnace_groups,
        power_source="grid",
        soe_status="private",
        parent_gem_id="parent",
        workforce_size=100,
        certified=False,
        category_steel_product=set(),
        steel_capacity=1000,
        technology_unit_fopex={},
    )


# ---- update_status_of_furnace_group: announced -> construction ----


def test_announced_to_construction_moves_reserved_into_firm(tmp_path):
    env = _make_env(tmp_path)
    env.year = Year(2030)
    tech = _make_ccs_tech(co2_stored=2.0)
    fg = _make_fg("fg1", tech, capacity=1000.0, status="announced")
    plant = _make_plant("p1", "USA", [fg])
    # Seed counter as if the scan had already run this year with the announced FG in reserved.
    d = env.config.co2_storage_reserved_discount_factor
    need = env.get_co2_need(tech, 1000.0, "Coke+PCI")
    env.co2_storage_reserved["USA"] = d * need

    uow = FakeUoW([plant])
    cmd = UpdateFurnaceGroupStatus(fg_id="fg1", plant_id="p1", new_status="construction")

    update_status_of_furnace_group(cmd, uow=uow, env=env)

    assert env.co2_storage_firm["USA"] == pytest.approx(need)
    assert env.co2_storage_reserved["USA"] == pytest.approx(0.0, abs=1e-9)
    assert uow.committed is True


def test_announced_to_construction_is_noop_for_non_ccs(tmp_path):
    env = _make_env(tmp_path)
    env.year = Year(2030)
    fg = _make_fg("fg1", _make_non_ccs_tech(), capacity=1000.0, status="announced")
    plant = _make_plant("p1", "USA", [fg])
    uow = FakeUoW([plant])
    cmd = UpdateFurnaceGroupStatus(fg_id="fg1", plant_id="p1", new_status="construction")

    update_status_of_furnace_group(cmd, uow=uow, env=env)

    assert env.co2_storage_firm == {}
    assert env.co2_storage_reserved == {}


# ---- update_status_of_furnace_group: announced -> discarded (new branch) ----


def test_announced_to_discarded_releases_reserved(tmp_path):
    env = _make_env(tmp_path)
    env.year = Year(2030)
    tech = _make_ccs_tech(co2_stored=2.0)
    fg = _make_fg("fg1", tech, capacity=1000.0, status="announced")
    plant = _make_plant("p1", "USA", [fg])
    d = env.config.co2_storage_reserved_discount_factor
    need = env.get_co2_need(tech, 1000.0, "Coke+PCI")
    env.co2_storage_reserved["USA"] = d * need

    uow = FakeUoW([plant])
    cmd = UpdateFurnaceGroupStatus(fg_id="fg1", plant_id="p1", new_status="discarded")

    update_status_of_furnace_group(cmd, uow=uow, env=env)

    assert env.co2_storage_reserved["USA"] == pytest.approx(0.0, abs=1e-9)
    assert "USA" not in env.co2_storage_firm


def test_considered_to_discarded_does_not_touch_reserved(tmp_path):
    """Old-status guard is load-bearing: considered FGs contribute nothing to reserved,
    so the considered -> discarded NPV-TTL path must skip the counter update.
    Without the guard it would drive reserved negative."""
    env = _make_env(tmp_path)
    env.year = Year(2030)
    tech = _make_ccs_tech(co2_stored=2.0)
    fg = _make_fg("fg1", tech, capacity=1000.0, status="considered")
    plant = _make_plant("p1", "USA", [fg])
    # reserved starts empty (considered FGs never contribute).
    uow = FakeUoW([plant])
    cmd = UpdateFurnaceGroupStatus(fg_id="fg1", plant_id="p1", new_status="discarded")

    update_status_of_furnace_group(cmd, uow=uow, env=env)

    assert env.co2_storage_reserved == {}


# ---- update_capacity_buildout: expansion path ----


def test_update_capacity_buildout_expansion_counts_firm_for_ccs(tmp_path):
    env = _make_env(tmp_path)
    env.year = Year(2030)
    tech = _make_ccs_tech(co2_stored=2.0)
    fg = _make_fg("fg1", tech, capacity=1000.0, status="construction")
    plant = _make_plant("p1", "USA", [fg])
    uow = FakeUoW([plant])

    event = events.FurnaceGroupAdded(
        plant_id="p1",
        furnace_group_id="fg1",
        technology_name="BFCCS",
        capacity=1000.0,
        is_new_plant=False,
    )

    update_capacity_buildout(event, uow=uow, env=env)

    expected = env.get_co2_need(tech, 1000.0, "Coke+PCI")
    assert env.co2_storage_firm["USA"] == pytest.approx(expected)


def test_update_capacity_buildout_new_plant_skips_counter(tmp_path):
    """is_new_plant=True: the announced->construction branch already counted this FG,
    counting here too would double-count and violate firm <= limit."""
    env = _make_env(tmp_path)
    env.year = Year(2030)
    tech = _make_ccs_tech(co2_stored=2.0)
    fg = _make_fg("fg1", tech, capacity=1000.0, status="construction")
    plant = _make_plant("p1", "USA", [fg])
    uow = FakeUoW([plant])

    event = events.FurnaceGroupAdded(
        plant_id="p1",
        furnace_group_id="fg1",
        technology_name="BFCCS",
        capacity=1000.0,
        is_new_plant=True,
    )

    update_capacity_buildout(event, uow=uow, env=env)

    assert env.co2_storage_firm == {}


def test_update_capacity_buildout_expansion_noop_for_non_ccs(tmp_path):
    env = _make_env(tmp_path)
    env.year = Year(2030)
    fg = _make_fg("fg1", _make_non_ccs_tech(), capacity=1000.0, status="construction")
    plant = _make_plant("p1", "USA", [fg])
    uow = FakeUoW([plant])

    event = events.FurnaceGroupAdded(
        plant_id="p1",
        furnace_group_id="fg1",
        technology_name="BF",
        capacity=1000.0,
        is_new_plant=False,
    )

    update_capacity_buildout(event, uow=uow, env=env)

    assert env.co2_storage_firm == {}


# ---- change_furnace_group_status_to_switching_technology ----


def test_switching_tech_to_ccs_adds_to_firm_using_old_reductant(tmp_path):
    """P2 handler: commits the new CCS tech's need to firm. Reductant source is the OLD
    chosen_reductant (not env-wide), matching the scan's switching-window rule for bit-identity."""
    env = _make_env(tmp_path)
    env.year = Year(2030)
    pf = PrimaryFeedstock(metallic_charge="IO_low", reductant="Coke+PCI", technology="BFCCS")
    pf.add_carbon_output("co2_stored", 2.5)
    env.dynamic_feedstocks["BFCCS"] = [pf]

    old_tech = _make_non_ccs_tech(name="BF")
    fg = _make_fg("fg1", old_tech, capacity=1000.0, status="operating", chosen_reductant="Coke+PCI")
    plant = _make_plant("p1", "USA", [fg])
    uow = FakeUoW([plant])

    inner_cmd = ChangeFurnaceGroupTechnology(
        plant_id="p1",
        furnace_group_id="fg1",
        technology_name="BFCCS",
        old_technology_name="BF",
        npv=0.0,
        cosa=0.0,
        utilisation=0.0,
        capex=0.0,
        capex_no_subsidy=0.0,
        capacity=1000.0,
        remaining_lifetime=10,
        bom={},
        cost_of_debt=0.05,
        cost_of_debt_no_subsidy=0.05,
        capex_subsidies=[],
        debt_subsidies=[],
    )
    outer_cmd = ChangeFurnaceGroupStatusToSwitchingTechnology(
        plant_id="p1",
        furnace_group_id="fg1",
        year_of_switch=2034,
        cmd=inner_cmd,
    )

    change_furnace_group_status_to_switching_technology(outer_cmd, uow=uow, env=env)

    expected = 2.5 * 1000.0 * 0.95
    assert env.co2_storage_firm["USA"] == pytest.approx(expected)


def test_switching_tech_to_non_ccs_is_noop(tmp_path):
    """Switching to a non-CCS tech (or one not in env.dynamic_feedstocks) → counter unchanged."""
    env = _make_env(tmp_path)
    env.year = Year(2030)

    old_tech = _make_non_ccs_tech(name="BF")
    fg = _make_fg("fg1", old_tech, capacity=1000.0, status="operating")
    plant = _make_plant("p1", "USA", [fg])
    uow = FakeUoW([plant])

    inner_cmd = ChangeFurnaceGroupTechnology(
        plant_id="p1",
        furnace_group_id="fg1",
        technology_name="EAF",
        old_technology_name="BF",
        npv=0.0,
        cosa=0.0,
        utilisation=0.0,
        capex=0.0,
        capex_no_subsidy=0.0,
        capacity=1000.0,
        remaining_lifetime=10,
        bom={},
        cost_of_debt=0.05,
        cost_of_debt_no_subsidy=0.05,
        capex_subsidies=[],
        debt_subsidies=[],
    )
    outer_cmd = ChangeFurnaceGroupStatusToSwitchingTechnology(
        plant_id="p1",
        furnace_group_id="fg1",
        year_of_switch=2034,
        cmd=inner_cmd,
    )

    change_furnace_group_status_to_switching_technology(outer_cmd, uow=uow, env=env)

    assert env.co2_storage_firm == {}


# ---- Handler <-> scan bit-identity invariant ----


def test_handler_scan_bit_identity_after_announced_to_construction(tmp_path):
    """After the announced->construction handler mutates counters, re-running
    scan_co2_storage_counters from scratch produces the same values — ensures the
    handler's counter update matches what the scan would compute for a construction-status FG."""
    env = _make_env(tmp_path)
    env.year = Year(2030)
    tech = _make_ccs_tech(co2_stored=2.0)
    fg = _make_fg("fg1", tech, capacity=1000.0, status="announced")
    plant = _make_plant("p1", "USA", [fg])
    d = env.config.co2_storage_reserved_discount_factor
    need = env.get_co2_need(tech, 1000.0, "Coke+PCI")
    env.co2_storage_reserved["USA"] = d * need

    uow = FakeUoW([plant])
    cmd = UpdateFurnaceGroupStatus(fg_id="fg1", plant_id="p1", new_status="construction")
    update_status_of_furnace_group(cmd, uow=uow, env=env)

    after_handler_firm = dict(env.co2_storage_firm)
    after_handler_reserved = {k: v for k, v in env.co2_storage_reserved.items() if abs(v) > 1e-9}

    env.scan_co2_storage_counters(uow)

    assert env.co2_storage_firm == pytest.approx(after_handler_firm)
    scan_reserved = {k: v for k, v in env.co2_storage_reserved.items() if abs(v) > 1e-9}
    assert scan_reserved == after_handler_reserved


def test_handler_scan_bit_identity_after_switching_technology(tmp_path):
    """After the P2 handler commits a tech switch, scan_co2_storage_counters reading
    fg.future_switch_cmd + fg.chosen_reductant (OLD) produces the same firm value."""
    env = _make_env(tmp_path)
    env.year = Year(2030)
    pf = PrimaryFeedstock(metallic_charge="IO_low", reductant="Coke+PCI", technology="BFCCS")
    pf.add_carbon_output("co2_stored", 2.5)
    env.dynamic_feedstocks["BFCCS"] = [pf]

    old_tech = _make_non_ccs_tech(name="BF")
    fg = _make_fg("fg1", old_tech, capacity=1000.0, status="operating", chosen_reductant="Coke+PCI")
    plant = _make_plant("p1", "USA", [fg])
    uow = FakeUoW([plant])

    inner_cmd = ChangeFurnaceGroupTechnology(
        plant_id="p1",
        furnace_group_id="fg1",
        technology_name="BFCCS",
        old_technology_name="BF",
        npv=0.0,
        cosa=0.0,
        utilisation=0.0,
        capex=0.0,
        capex_no_subsidy=0.0,
        capacity=1000.0,
        remaining_lifetime=10,
        bom={},
        cost_of_debt=0.05,
        cost_of_debt_no_subsidy=0.05,
        capex_subsidies=[],
        debt_subsidies=[],
    )
    outer_cmd = ChangeFurnaceGroupStatusToSwitchingTechnology(
        plant_id="p1",
        furnace_group_id="fg1",
        year_of_switch=2034,
        cmd=inner_cmd,
    )
    change_furnace_group_status_to_switching_technology(outer_cmd, uow=uow, env=env)
    handler_firm = dict(env.co2_storage_firm)

    env.scan_co2_storage_counters(uow)

    assert env.co2_storage_firm == pytest.approx(handler_firm)
