"""Tests for the P3 CO2 storage gate inside PlantGroup.evaluate_expansion_options.

Stage 2 of the CO2 storage capacity gate. The gate replaces the pre-existing
substring quick-hack at the pre-NPV filter site: CCS technologies whose annual
need exceeds country headroom at the operating-start year are skipped before NPV
is computed so the next-best non-CCS tech wins the per-plant race naturally.

These tests stub ``get_bom_from_avg_boms=None`` so that surviving techs exit
early (before NPV), letting the gate's drop decision be observed via caplog
without constructing a full BOM/NPV fixture.
"""

from __future__ import annotations

import logging

from steelo.domain import Year
from steelo.domain.models import (
    FurnaceGroup,
    Location,
    Plant,
    PlantGroup,
    PointInTime,
    PrimaryFeedstock,
    Technology,
    TimeFrame,
    Volumes,
)


# ---- Fixtures ----


def _make_ccs_tech(name: str, reductant: str = "Coke+PCI") -> Technology:
    pf = PrimaryFeedstock(metallic_charge="IO_low", reductant=reductant, technology=name)
    return Technology(name=name, product="hot_metal", dynamic_business_case=[pf])


def _make_fg(tech: Technology, capacity: float = 1000.0) -> FurnaceGroup:
    return FurnaceGroup(
        furnace_group_id="fg1",
        capacity=capacity,
        status="operating",
        last_renovation_date=None,
        technology=tech,
        historical_production={},
        utilization_rate=0.8,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2025), end=Year(2045)),
            plant_lifetime=20,
        ),
        chosen_reductant="Coke+PCI",
    )


def _make_plant(plant_id: str, iso3: str, balance: float = 1e15) -> Plant:
    """Balance is intentionally huge so the affordability pre-filter never fires (seeded on the group)."""
    tech = _make_ccs_tech("BF")
    fg = _make_fg(tech)
    fg.set_energy_costs(electricity=0.05, coke=0.1)
    p = Plant(
        plant_id=plant_id,
        location=Location(lat=0.0, lon=0.0, country=iso3, region="Region", iso3=iso3),
        furnace_groups=[fg],
        power_source="grid",
        soe_status="private",
        parent_gem_id="parent",
        workforce_size=100,
        certified=False,
        category_steel_product=set(),
        steel_capacity=Volumes(1000),
        technology_unit_fopex={},
    )
    return p


def _make_pg(plants: list[Plant], balance: float = 1e15) -> PlantGroup:
    pg = PlantGroup(plant_group_id="pg1", plants=plants)
    pg.balance = balance
    return pg


def _build_stubs(
    headroom_by_iso3: dict[str, float],
    need_by_tech: dict[str, float],
    limit: float = 1000.0,
    firm: float = 300.0,
    reserved: float = 200.0,
):
    """Closures mimicking the narrow callables threaded from Environment."""

    def get_co2_headroom(iso3: str, year: int, own_reserved_contribution: float = 0.0) -> float:
        return headroom_by_iso3.get(iso3, 0.0) + own_reserved_contribution

    def get_co2_need_by_name(tech_name: str, capacity: float, reductant: str) -> float:
        return need_by_tech.get(tech_name, 0.0)

    def co2_storage_diagnostics(iso3: str, year: int) -> tuple[float, float, float]:
        return firm, reserved, limit

    return get_co2_headroom, get_co2_need_by_name, co2_storage_diagnostics


def _call_evaluate(
    pg: PlantGroup,
    allowed_techs_list: list[str],
    region_capex: dict[str, dict[str, float]],
    get_co2_headroom,
    get_co2_need_by_name,
    co2_storage_diagnostics,
    environment_most_common_reductant: dict[str, str] | None = None,
) -> dict[str, tuple[float | None, str, float]]:
    """Shared invocation: stubs NPV path via get_bom_from_avg_boms=None.

    All callables required by ``evaluate_expansion_options`` are passed as
    minimal empty/neutral values; the gate fires before the BOM short-circuit.
    """
    fopex_for_iso3 = {p.location.iso3: {t: 10.0 for t in allowed_techs_list} for p in pg.plants}
    iso3_to_region_map = {p.location.iso3: "Region" for p in pg.plants}
    price_series = {"hot_metal": [500.0] * 30, "steel": [500.0] * 30}
    tech_to_product = {t: "hot_metal" for t in allowed_techs_list}
    cost_of_debt_dict = {p.location.iso3: {t: 0.05 for t in allowed_techs_list} for p in pg.plants}
    cost_of_equity_dict = {p.location.iso3: {t: 0.1 for t in allowed_techs_list} for p in pg.plants}

    return pg.evaluate_expansion_options(
        price_series=price_series,
        capacity=Volumes(1000.0),
        region_capex=region_capex,
        cost_of_debt_dict=cost_of_debt_dict,
        cost_of_equity_dict=cost_of_equity_dict,
        get_bom_from_avg_boms=None,
        dynamic_feedstocks={},
        fopex_for_iso3=fopex_for_iso3,
        iso3_to_region_map=iso3_to_region_map,
        chosen_emissions_boundary_for_carbon_costs="Scope 1",
        technology_emission_factors=[],
        global_risk_free_rate=0.02,
        equity_share=0.3,
        tech_to_product=tech_to_product,
        plant_lifetime=20,
        construction_time=4,
        current_year=Year(2030),
        allowed_techs={Year(2030): allowed_techs_list},
        active_statuses=["operating"],
        environment_most_common_reductant=environment_most_common_reductant or {},
        get_co2_headroom=get_co2_headroom,
        get_co2_need_by_name=get_co2_need_by_name,
        co2_storage_diagnostics=co2_storage_diagnostics,
    )


# ---- Gate behaviour ----


def test_p3_drops_ccs_tech_when_headroom_below_need(caplog):
    pg = _make_pg([_make_plant("p1", "USA")])
    region_capex = {"Region": {"BFCCS": 500.0, "DRI": 400.0}}

    get_co2_headroom, get_co2_need_by_name, co2_storage_diagnostics = _build_stubs(
        headroom_by_iso3={"USA": 100.0},
        need_by_tech={"BFCCS": 500.0, "DRI": 0.0},
        limit=1000.0,
        firm=700.0,
        reserved=200.0,
    )

    with caplog.at_level(logging.INFO):
        NPV_p = _call_evaluate(
            pg,
            allowed_techs_list=["BFCCS", "DRI"],
            region_capex=region_capex,
            get_co2_headroom=get_co2_headroom,
            get_co2_need_by_name=get_co2_need_by_name,
            co2_storage_diagnostics=co2_storage_diagnostics,
        )

    assert NPV_p == {}  # all techs skip past the bom=None guard

    gate_lines = [r for r in caplog.records if "[CO2 GATE]" in r.getMessage()]
    assert len(gate_lines) == 1
    msg = gate_lines[0].getMessage()
    assert "gate=P3" in msg
    assert "iso3=USA" in msg
    assert "year=2030" in msg
    assert "lookup_year=2034" in msg  # current_year + construction_time
    assert "plant_id=p1" in msg
    assert "dropped_ccs_techs=BFCCS" in msg
    assert "dropped_count=1" in msg
    assert "limit=1000" in msg


def test_p3_does_not_drop_ccu_tech_with_zero_need(caplog):
    """CCU techs have co2_stored=0 in BOM, so get_co2_need returns 0 → gate no-ops."""
    pg = _make_pg([_make_plant("p1", "USA")])
    region_capex = {"Region": {"BFCCU": 500.0}}

    get_co2_headroom, get_co2_need_by_name, co2_storage_diagnostics = _build_stubs(
        headroom_by_iso3={"USA": 0.0},
        need_by_tech={"BFCCU": 0.0},
    )

    with caplog.at_level(logging.INFO):
        NPV_p = _call_evaluate(
            pg,
            allowed_techs_list=["BFCCU"],
            region_capex=region_capex,
            get_co2_headroom=get_co2_headroom,
            get_co2_need_by_name=get_co2_need_by_name,
            co2_storage_diagnostics=co2_storage_diagnostics,
        )

    assert NPV_p == {}
    assert not any("[CO2 GATE]" in r.getMessage() for r in caplog.records)


def test_p3_no_log_when_nothing_dropped(caplog):
    """Headroom covers need → gate passes all techs, no INFO line emitted."""
    pg = _make_pg([_make_plant("p1", "USA")])
    region_capex = {"Region": {"BFCCS": 500.0}}

    get_co2_headroom, get_co2_need_by_name, co2_storage_diagnostics = _build_stubs(
        headroom_by_iso3={"USA": 10_000.0},
        need_by_tech={"BFCCS": 500.0},
    )

    with caplog.at_level(logging.INFO):
        _call_evaluate(
            pg,
            allowed_techs_list=["BFCCS"],
            region_capex=region_capex,
            get_co2_headroom=get_co2_headroom,
            get_co2_need_by_name=get_co2_need_by_name,
            co2_storage_diagnostics=co2_storage_diagnostics,
        )

    assert not any("[CO2 GATE]" in r.getMessage() for r in caplog.records)


def test_p3_drops_when_headroom_strictly_less_than_need(caplog):
    """Gate uses strict '<': headroom == need passes, headroom < need blocks."""
    pg = _make_pg([_make_plant("p1", "USA")])
    region_capex = {"Region": {"BFCCS": 500.0}}

    # Case 1: headroom == need → passes (no log)
    get_co2_headroom, get_co2_need_by_name, co2_storage_diagnostics = _build_stubs(
        headroom_by_iso3={"USA": 500.0},
        need_by_tech={"BFCCS": 500.0},
    )
    with caplog.at_level(logging.INFO):
        _call_evaluate(
            pg,
            allowed_techs_list=["BFCCS"],
            region_capex=region_capex,
            get_co2_headroom=get_co2_headroom,
            get_co2_need_by_name=get_co2_need_by_name,
            co2_storage_diagnostics=co2_storage_diagnostics,
        )
    assert not any("[CO2 GATE]" in r.getMessage() for r in caplog.records)

    caplog.clear()

    # Case 2: headroom < need → blocks
    get_co2_headroom, get_co2_need_by_name, co2_storage_diagnostics = _build_stubs(
        headroom_by_iso3={"USA": 499.0},
        need_by_tech={"BFCCS": 500.0},
    )
    with caplog.at_level(logging.INFO):
        _call_evaluate(
            pg,
            allowed_techs_list=["BFCCS"],
            region_capex=region_capex,
            get_co2_headroom=get_co2_headroom,
            get_co2_need_by_name=get_co2_need_by_name,
            co2_storage_diagnostics=co2_storage_diagnostics,
        )
    gate_lines = [r for r in caplog.records if "[CO2 GATE]" in r.getMessage()]
    assert len(gate_lines) == 1
    assert "dropped_ccs_techs=BFCCS" in gate_lines[0].getMessage()


def test_p3_pg_local_reductant_overrides_env_reductant():
    """PlantGroup.most_common_reductant wins over environment_most_common_reductant.

    The per-tech gate lookup is ``self.most_common_reductant.get(tech,
    environment_most_common_reductant.get(tech, ""))`` — PG-local is tried first.
    When both are set, the stub's need_by_tech is probed with the PG-local value;
    here we verify the callable receives the PG-local reductant when one exists.
    """
    tech = _make_ccs_tech("BFCCS", reductant="Coke+PCI")
    fg = _make_fg(tech)
    fg.set_energy_costs(electricity=0.05, coke=0.1)

    plant = Plant(
        plant_id="p1",
        location=Location(lat=0.0, lon=0.0, country="USA", region="Region", iso3="USA"),
        furnace_groups=[fg],
        power_source="grid",
        soe_status="private",
        parent_gem_id="parent",
        workforce_size=100,
        certified=False,
        category_steel_product=set(),
        steel_capacity=Volumes(1000),
        technology_unit_fopex={},
    )
    pg = _make_pg([plant])

    captured_reductants: list[str] = []

    def get_co2_headroom(iso3, year, own=0.0):
        return 100.0 + own

    def get_co2_need_by_name(tech_name, capacity, reductant):
        captured_reductants.append(reductant)
        return 50.0  # below headroom so nothing dropped

    def co2_storage_diagnostics(iso3, year):
        return 0.0, 0.0, 1000.0

    _call_evaluate(
        pg,
        allowed_techs_list=["BFCCS"],
        region_capex={"Region": {"BFCCS": 500.0}},
        get_co2_headroom=get_co2_headroom,
        get_co2_need_by_name=get_co2_need_by_name,
        co2_storage_diagnostics=co2_storage_diagnostics,
        environment_most_common_reductant={"BFCCS": "Hydrogen"},
    )

    # FG tech is "BFCCS" with chosen_reductant="Coke+PCI", so
    # get_most_common_reductant_by_technology returns {"BFCCS": "Coke+PCI"}.
    # Gate tries PG-local first, finds "Coke+PCI", and never falls back to the
    # env dict's "Hydrogen" — proving PG-local takes precedence.
    assert captured_reductants == ["Coke+PCI"]


def test_p3_gate_disabled_when_callables_not_wired(caplog):
    """get_co2_headroom or get_co2_need_by_name None → gate is a no-op (safety guard)."""
    pg = _make_pg([_make_plant("p1", "USA")])
    region_capex = {"Region": {"BFCCS": 500.0}}

    with caplog.at_level(logging.INFO):
        NPV_p = _call_evaluate(
            pg,
            allowed_techs_list=["BFCCS"],
            region_capex=region_capex,
            get_co2_headroom=None,  # type: ignore[arg-type]
            get_co2_need_by_name=None,  # type: ignore[arg-type]
            co2_storage_diagnostics=None,  # type: ignore[arg-type]
        )

    assert NPV_p == {}
    assert not any("[CO2 GATE]" in r.getMessage() for r in caplog.records)
