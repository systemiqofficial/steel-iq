"""Tests for the P2 CO2 storage gate inside Plant.evaluate_furnace_group_strategy.

Stage 2 of the CO2 storage capacity gate. The gate replaces the pre-existing
substring quick-hack that ran inside the Stage 4 transition-filter dict
comprehension: CCS technologies whose annual need exceeds country headroom at
the operating-start year are skipped before NPV is computed so the NPV race
picks the next-best non-CCS tech naturally.

These tests arrange the transition candidate list so the P2 filter drops the
only CCS option; the downstream NPV race then runs with an empty transition
list and returns None, letting the gate's log line be observed via caplog
without constructing full NPV inputs.
"""

from __future__ import annotations

import logging

from steelo.domain import Year
from steelo.domain.calculate_costs import ReductantScoreSeries
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


def _make_tech(name: str, reductant: str = "Coke+PCI") -> Technology:
    pf = PrimaryFeedstock(metallic_charge="IO_low", reductant=reductant, technology=name)
    return Technology(name=name, product="hot_metal", dynamic_business_case=[pf])


def _make_fg(tech: Technology, capacity: float = 1000.0) -> FurnaceGroup:
    fg = FurnaceGroup(
        furnace_group_id="fg1",
        capacity=capacity,
        status="operating",
        last_renovation_date=None,
        technology=tech,
        historical_production={},
        # util=0 → unit_total_opex_no_subsidy short-circuits to 0.0 without BOM.
        # Keeps the test scoped to the P2 filter log assertion without needing
        # full BOM fixtures.
        utilization_rate=0.0,
        lifetime=PointInTime(
            current=Year(2030),
            time_frame=TimeFrame(start=Year(2025), end=Year(2045)),
            plant_lifetime=20,
        ),
        chosen_reductant="Coke+PCI",
    )
    fg.set_energy_costs(electricity=0.05, coke=0.1)
    fg.balance = 1e9
    fg.historic_balance = 0.0
    return fg


def _make_plant(iso3: str = "USA") -> Plant:
    tech = _make_tech("BF")
    fg = _make_fg(tech)
    p = Plant(
        plant_id="p1",
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


def _make_plant_and_group(iso3: str = "USA") -> tuple[Plant, "PlantGroup"]:
    p = _make_plant(iso3=iso3)
    pg = PlantGroup(plant_group_id="parent", plants=[p])
    pg.balance = 1e15
    return p, pg


def _build_stubs(
    headroom: float,
    need_by_tech: dict[str, float],
    limit: float = 1000.0,
    firm: float = 500.0,
    reserved: float = 200.0,
):
    captured: dict[str, list] = {"need_calls": [], "headroom_calls": []}

    def get_co2_headroom(iso3: str, year: int, own_reserved_contribution: float = 0.0) -> float:
        captured["headroom_calls"].append((iso3, year, own_reserved_contribution))
        return headroom + own_reserved_contribution

    def get_co2_need_by_name(tech_name: str, capacity: float, reductant: str) -> float:
        captured["need_calls"].append((tech_name, capacity, reductant))
        return need_by_tech.get(tech_name, 0.0)

    def co2_storage_diagnostics(iso3: str, year: int) -> tuple[float, float, float]:
        return firm, reserved, limit

    return get_co2_headroom, get_co2_need_by_name, co2_storage_diagnostics, captured


def _call_evaluate(
    plant: Plant,
    allowed_furnace_transitions: dict[str, list[str]],
    allowed_techs_list: list[str],
    get_co2_headroom,
    get_co2_need_by_name,
    co2_storage_diagnostics,
    most_common_reductant_by_tech: dict[str, str] | None = None,
    current_year: Year = Year(2030),
    plant_group: PlantGroup | None = None,
):
    region_capex = {tech: 500.0 for tech in allowed_techs_list + [plant.furnace_groups[0].technology.name]}
    if plant_group is None:
        plant_group = PlantGroup(plant_group_id="parent", plants=[plant])
        plant_group.balance = 1e15

    return plant.evaluate_furnace_group_strategy(
        "fg1",
        plant_group=plant_group,
        market_price_series={"steel": [500.0] * 30, "hot_metal": [500.0] * 30, "iron": [500.0] * 30},
        region_capex=region_capex,
        capex_renovation_share={},
        cost_of_debt_by_tech={tech: 0.05 for tech in region_capex},
        cost_of_equity_by_tech={tech: 0.1 for tech in region_capex},
        get_bom_from_avg_boms=lambda *a, **k: (None, 0.0, "", {}),
        reductant_score_series=lambda *a, **k: ReductantScoreSeries(scores=[], picks=[]),
        probabilistic_agents=False,
        dynamic_business_cases={},
        chosen_emissions_boundary_for_carbon_costs="Scope 1",
        technology_emission_factors=[],
        tech_to_product={t: "hot_metal" for t in allowed_techs_list},
        plant_lifetime=20,
        construction_time=4,
        current_year=current_year,
        allowed_techs={current_year: allowed_techs_list},
        risk_free_rate=0.02,
        allowed_furnace_transitions=allowed_furnace_transitions,
        capacity_limit_steel=Volumes(1e9),
        capacity_limit_iron=Volumes(1e9),
        installed_capacity_in_year=lambda _: Volumes(0),
        new_plant_capacity_in_year=lambda _: Volumes(0),
        most_common_reductant_by_tech=most_common_reductant_by_tech or {},
        get_co2_headroom=get_co2_headroom,
        get_co2_need_by_name=get_co2_need_by_name,
        co2_storage_diagnostics=co2_storage_diagnostics,
    )


# ---- Gate behaviour ----


def test_p2_drops_ccs_tech_when_headroom_below_need(caplog):
    plant = _make_plant("USA")
    get_co2_headroom, get_co2_need_by_name, co2_storage_diagnostics, _ = _build_stubs(
        headroom=100.0,
        need_by_tech={"BFCCS": 500.0},
        limit=1000.0,
    )

    with caplog.at_level(logging.INFO):
        result = _call_evaluate(
            plant,
            allowed_furnace_transitions={"BF": ["BFCCS"]},
            allowed_techs_list=["BFCCS"],
            get_co2_headroom=get_co2_headroom,
            get_co2_need_by_name=get_co2_need_by_name,
            co2_storage_diagnostics=co2_storage_diagnostics,
        )

    # With BFCCS dropped, the transition list collapses to empty, NPV is empty, method returns None.
    assert result is None

    gate_lines = [r for r in caplog.records if "[CO2 GATE]" in r.getMessage()]
    assert len(gate_lines) == 1
    msg = gate_lines[0].getMessage()
    assert "gate=P2" in msg
    assert "iso3=USA" in msg
    assert "year=2030" in msg
    assert "lookup_year=2034" in msg
    assert "fg_id=fg1" in msg
    assert "plant_id=p1" in msg
    assert "dropped_ccs_techs=BFCCS" in msg
    assert "dropped_count=1" in msg
    assert "limit=1000" in msg


def test_p2_does_not_drop_ccu_tech_with_zero_need(caplog):
    """CCU techs have co2_stored=0 in BOM, so gate no-ops regardless of headroom."""
    plant = _make_plant("USA")
    get_co2_headroom, get_co2_need_by_name, co2_storage_diagnostics, _ = _build_stubs(
        headroom=0.0,
        need_by_tech={"BFCCU": 0.0},
    )

    with caplog.at_level(logging.INFO):
        _call_evaluate(
            plant,
            allowed_furnace_transitions={"BF": ["BFCCU"]},
            allowed_techs_list=["BFCCU"],
            get_co2_headroom=get_co2_headroom,
            get_co2_need_by_name=get_co2_need_by_name,
            co2_storage_diagnostics=co2_storage_diagnostics,
        )

    assert not any("[CO2 GATE]" in r.getMessage() for r in caplog.records)


def test_p2_no_log_when_nothing_dropped(caplog):
    plant = _make_plant("USA")
    get_co2_headroom, get_co2_need_by_name, co2_storage_diagnostics, _ = _build_stubs(
        headroom=10_000.0,
        need_by_tech={"BFCCS": 500.0},
    )

    with caplog.at_level(logging.INFO):
        _call_evaluate(
            plant,
            allowed_furnace_transitions={"BF": ["BFCCS"]},
            allowed_techs_list=["BFCCS"],
            get_co2_headroom=get_co2_headroom,
            get_co2_need_by_name=get_co2_need_by_name,
            co2_storage_diagnostics=co2_storage_diagnostics,
        )

    assert not any("[CO2 GATE]" in r.getMessage() for r in caplog.records)


def test_p2_uses_env_wide_reductant():
    """P2 must pass most_common_reductant_by_tech into get_co2_need_by_name so gate
    and downstream cost pipeline agree on the reductant used for the candidate tech."""
    plant = _make_plant("USA")
    get_co2_headroom, get_co2_need_by_name, co2_storage_diagnostics, captured = _build_stubs(
        headroom=10_000.0,
        need_by_tech={"BFCCS": 500.0},
    )

    _call_evaluate(
        plant,
        allowed_furnace_transitions={"BF": ["BFCCS"]},
        allowed_techs_list=["BFCCS"],
        get_co2_headroom=get_co2_headroom,
        get_co2_need_by_name=get_co2_need_by_name,
        co2_storage_diagnostics=co2_storage_diagnostics,
        most_common_reductant_by_tech={"BFCCS": "Coke+PCI+H2"},
    )

    # Gate called need with the env-wide reductant for the candidate tech.
    assert ("BFCCS", plant.furnace_groups[0].capacity, "Coke+PCI+H2") in captured["need_calls"]


def test_p2_gate_disabled_when_callables_not_wired(caplog):
    """None callables → gate degrades to no-op; all candidate techs pass through the filter."""
    plant = _make_plant("USA")

    with caplog.at_level(logging.INFO):
        _call_evaluate(
            plant,
            allowed_furnace_transitions={"BF": ["BFCCS"]},
            allowed_techs_list=["BFCCS"],
            get_co2_headroom=None,  # type: ignore[arg-type]
            get_co2_need_by_name=None,  # type: ignore[arg-type]
            co2_storage_diagnostics=None,  # type: ignore[arg-type]
        )

    assert not any("[CO2 GATE]" in r.getMessage() for r in caplog.records)


def test_p2_lookup_year_uses_construction_time_offset():
    """Gate checks headroom at current_year + construction_time (operating-start)."""
    plant = _make_plant("USA")
    get_co2_headroom, get_co2_need_by_name, co2_storage_diagnostics, captured = _build_stubs(
        headroom=10_000.0,
        need_by_tech={"BFCCS": 500.0},
    )

    _call_evaluate(
        plant,
        allowed_furnace_transitions={"BF": ["BFCCS"]},
        allowed_techs_list=["BFCCS"],
        get_co2_headroom=get_co2_headroom,
        get_co2_need_by_name=get_co2_need_by_name,
        co2_storage_diagnostics=co2_storage_diagnostics,
        current_year=Year(2030),
    )

    # _call_evaluate passes construction_time=4 → lookup_year = 2034.
    assert captured["headroom_calls"] == [("USA", 2034, 0.0)]
