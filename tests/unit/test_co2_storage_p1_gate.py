"""Tests for the P1 CO2 storage gate inside convert_business_opportunity_into_actual_project.

Stage 2 of the CO2 storage capacity gate. P1 fires on the announced → construction
transition for greenfield CCS plants. Unlike P2/P3, the announced FG already
contributes its d*need to the reserved bucket, so headroom is computed with the
own-reserved exemption (``own_reserved_contribution = d * own_need``) and the
block branch additionally issues an immediate-discard command — the plant is
removed from the reserved bucket on the next year-start scan rather than
lingering indefinitely (announced hoarding mitigation).

Placement invariant: the gate sits AFTER the capacity-limit block and BEFORE
the probability-of-construction draw. Capacity-limit keeps the plant announced
for a retry; P1 storage block terminates the announcement outright.
"""

from __future__ import annotations

import logging

from steelo.domain import Year
from steelo.domain.models import (
    FurnaceGroup,
    Location,
    PointInTime,
    PrimaryFeedstock,
    Technology,
    TimeFrame,
    commands,
)


def _make_ccs_tech(name: str = "BFCCS", reductant: str = "Coke+PCI") -> Technology:
    pf = PrimaryFeedstock(metallic_charge="IO_low", reductant=reductant, technology=name)
    return Technology(name=name, product="iron", dynamic_business_case=[pf])


def _make_announced_fg(
    tech: Technology | None = None,
    capacity: float = 1000.0,
    current_year: int = 2030,
) -> FurnaceGroup:
    """Announced FG with lifetime.current set to the simulation year so the P1 gate
    reads the correct current_year for its log line and lookup_year math."""
    fg = FurnaceGroup(
        furnace_group_id="fg_announced_1",
        capacity=capacity,
        status="announced",
        last_renovation_date=None,
        technology=tech or _make_ccs_tech(),
        historical_production={},
        utilization_rate=0.0,
        # Announced FGs have lifetime.time_frame.start = current_year + 1e6 per
        # generate_new_furnace; the sentinel keeps status transitions gated on
        # the P1 handler rather than the year-start construction→operating loop.
        lifetime=PointInTime(
            current=Year(current_year),
            time_frame=TimeFrame(start=Year(current_year + 1_000_000), end=Year(current_year + 1_000_020)),
            plant_lifetime=20,
        ),
        chosen_reductant="Coke+PCI",
    )
    return fg


def _make_location(iso3: str = "USA") -> Location:
    return Location(lat=0.0, lon=0.0, country=iso3, region="Region", iso3=iso3)


def _build_callables(
    headroom: float,
    own_need: float,
    limit: float = 1000.0,
    firm: float = 500.0,
    reserved: float = 300.0,
):
    captured: dict[str, list] = {"headroom_calls": []}

    def get_co2_headroom(iso3: str, year: int, own_reserved_contribution: float = 0.0) -> float:
        captured["headroom_calls"].append((iso3, year, own_reserved_contribution))
        return headroom + own_reserved_contribution

    def get_co2_need(tech: Technology, capacity: float, reductant: str) -> float:
        return own_need

    def co2_storage_diagnostics(iso3: str, year: int) -> tuple[float, float, float]:
        return firm, reserved, limit

    return get_co2_headroom, get_co2_need, co2_storage_diagnostics, captured


def _call(
    fg: FurnaceGroup,
    location: Location,
    get_co2_headroom,
    get_co2_need,
    co2_storage_diagnostics,
    probability_of_construction: float = 1.0,
    allowed_techs: list[str] | None = None,
    capacity_limit_iron: float = 1e12,
    capacity_limit_steel: float = 1e12,
    reserved_discount_factor: float = 0.9,
    construction_time: int = 4,
):
    return fg.convert_business_opportunity_into_actual_project(
        probability_of_construction=probability_of_construction,
        allowed_techs_current_year=allowed_techs or [fg.technology.name],
        new_plant_capacity_in_year=lambda _: 0.0,
        expanded_capacity=fg.capacity,
        capacity_limit_iron=capacity_limit_iron,
        capacity_limit_steel=capacity_limit_steel,
        new_capacity_share_from_new_plants=1.0,
        location=location,
        get_co2_headroom=get_co2_headroom,
        get_co2_need=get_co2_need,
        co2_storage_diagnostics=co2_storage_diagnostics,
        construction_time=construction_time,
        reserved_discount_factor=reserved_discount_factor,
    )


# ---- Gate behaviour ----


def test_p1_blocks_and_discards_when_headroom_insufficient(caplog):
    """headroom (with own exemption) < own_need → block: returns discard command, logs INFO.

    Stub's `headroom` arg is the true headroom (limit - firm - reserved); the
    callable adds the own_reserved_contribution the way Environment does. Block
    arithmetic: own_need=500, d=0.9 → own_contribution=450, stub true_headroom=0
    → returned headroom=450 < 500 → block fires.
    """
    fg = _make_announced_fg(current_year=2030)
    get_co2_headroom, get_co2_need, co2_storage_diagnostics, _ = _build_callables(
        headroom=0.0,  # true headroom — plus own_contribution=450 returns 450 < 500
        own_need=500.0,
        limit=1000.0,
        firm=700.0,
        reserved=300.0,
    )

    with caplog.at_level(logging.INFO):
        cmd = _call(fg, _make_location("USA"), get_co2_headroom, get_co2_need, co2_storage_diagnostics)

    assert isinstance(cmd, commands.UpdateFurnaceGroupStatus)
    assert cmd.new_status == "discarded"

    gate_lines = [r for r in caplog.records if "[CO2 GATE]" in r.getMessage()]
    assert len(gate_lines) == 1
    msg = gate_lines[0].getMessage()
    assert "gate=P1" in msg
    assert "decision=blocked" in msg
    assert "iso3=USA" in msg
    assert "year=2030" in msg
    assert "lookup_year=2034" in msg  # current_year + construction_time=4
    assert "tech=BFCCS" in msg
    assert "fg_id=fg_announced_1" in msg
    assert "need=500" in msg
    assert "firm=700" in msg
    assert "reserved=300" in msg
    assert "limit=1000" in msg
    assert "discarded=true" in msg


def test_p1_passes_when_headroom_covers_need(caplog):
    """headroom >= own_need → no command (falls through to probability draw); DEBUG pass log."""
    fg = _make_announced_fg(current_year=2030)
    get_co2_headroom, get_co2_need, co2_storage_diagnostics, _ = _build_callables(
        headroom=10_000.0,
        own_need=500.0,
    )

    with caplog.at_level(logging.DEBUG):
        cmd = _call(
            fg,
            _make_location("USA"),
            get_co2_headroom,
            get_co2_need,
            co2_storage_diagnostics,
            probability_of_construction=1.0,  # deterministic pass-through to construction
        )

    # Pass branch → prob draw (threshold 1.0 always passes) → construction command
    assert isinstance(cmd, commands.UpdateFurnaceGroupStatus)
    assert cmd.new_status == "construction"

    # Spec format: passes emit at DEBUG, not INFO
    debug_lines = [r for r in caplog.records if "[CO2 GATE]" in r.getMessage() and r.levelno == logging.DEBUG]
    assert len(debug_lines) == 1
    msg = debug_lines[0].getMessage()
    assert "decision=passed" in msg
    assert "discarded=false" in msg

    info_gate_lines = [r for r in caplog.records if "[CO2 GATE]" in r.getMessage() and r.levelno == logging.INFO]
    assert info_gate_lines == []


def test_p1_exempts_own_reserved_contribution():
    """Gate must call get_co2_headroom with own_reserved_contribution = d * own_need."""
    fg = _make_announced_fg(current_year=2030)
    get_co2_headroom, get_co2_need, co2_storage_diagnostics, captured = _build_callables(
        headroom=1_000.0,
        own_need=500.0,
    )

    _call(
        fg,
        _make_location("USA"),
        get_co2_headroom,
        get_co2_need,
        co2_storage_diagnostics,
        reserved_discount_factor=0.9,
    )

    assert captured["headroom_calls"] == [("USA", 2034, 0.9 * 500.0)]


def test_p1_no_gate_when_tech_has_zero_need(caplog):
    """Non-CCS/CCU techs have need=0 → gate short-circuits, no log, falls through."""
    fg = _make_announced_fg(current_year=2030)
    get_co2_headroom, get_co2_need, co2_storage_diagnostics, captured = _build_callables(
        headroom=0.0,
        own_need=0.0,
    )

    with caplog.at_level(logging.DEBUG):
        _call(fg, _make_location("USA"), get_co2_headroom, get_co2_need, co2_storage_diagnostics)

    assert captured["headroom_calls"] == []  # get_co2_headroom never called
    assert not any("[CO2 GATE]" in r.getMessage() for r in caplog.records)


def test_p1_gate_disabled_when_callables_not_wired(caplog):
    """None callables → gate no-ops (legacy path)."""
    fg = _make_announced_fg(current_year=2030)

    with caplog.at_level(logging.INFO):
        cmd = _call(
            fg,
            _make_location("USA"),
            get_co2_headroom=None,
            get_co2_need=None,
            co2_storage_diagnostics=None,
            probability_of_construction=1.0,
        )

    # With prob=1.0, falls through to construction without gate interference
    assert isinstance(cmd, commands.UpdateFurnaceGroupStatus)
    assert cmd.new_status == "construction"
    assert not any("[CO2 GATE]" in r.getMessage() for r in caplog.records)


def test_p1_does_not_run_when_tech_not_allowed(caplog):
    """Tech-not-allowed check precedes P1 → plant discards via that earlier branch."""
    fg = _make_announced_fg(current_year=2030)
    get_co2_headroom, get_co2_need, co2_storage_diagnostics, captured = _build_callables(
        headroom=10_000.0,
        own_need=500.0,
    )

    with caplog.at_level(logging.INFO):
        cmd = _call(
            fg,
            _make_location("USA"),
            get_co2_headroom,
            get_co2_need,
            co2_storage_diagnostics,
            allowed_techs=["OTHER_TECH"],  # FG tech not in list
        )

    assert isinstance(cmd, commands.UpdateFurnaceGroupStatus)
    assert cmd.new_status == "discarded"
    # P1 gate never evaluated — headroom callable not called
    assert captured["headroom_calls"] == []
    assert not any("[CO2 GATE]" in r.getMessage() for r in caplog.records)


def test_p1_does_not_run_when_capacity_limit_reached(caplog):
    """Capacity-limit block precedes P1; plant stays announced, P1 never evaluated.

    Placement invariant check: P1's immediate-discard is stricter than capacity
    block's stay-announced, so the softer capacity block must fire first.
    """
    fg = _make_announced_fg(current_year=2030)
    get_co2_headroom, get_co2_need, co2_storage_diagnostics, captured = _build_callables(
        headroom=0.0,  # would block P1 if reached
        own_need=500.0,
    )

    with caplog.at_level(logging.INFO):
        cmd = _call(
            fg,
            _make_location("USA"),
            get_co2_headroom,
            get_co2_need,
            co2_storage_diagnostics,
            capacity_limit_iron=0.0,  # limit exceeded → early None return
        )

    assert cmd is None  # stays announced
    assert captured["headroom_calls"] == []
    assert not any("[CO2 GATE]" in r.getMessage() for r in caplog.records)


def test_p1_lookup_year_uses_construction_time_offset():
    """P1 checks storage at current_year + construction_time (operating-start year)."""
    fg = _make_announced_fg(current_year=2026)
    get_co2_headroom, get_co2_need, co2_storage_diagnostics, captured = _build_callables(
        headroom=10_000.0,
        own_need=500.0,
    )

    _call(
        fg,
        _make_location("USA"),
        get_co2_headroom,
        get_co2_need,
        co2_storage_diagnostics,
        construction_time=4,
        reserved_discount_factor=0.9,
    )

    assert captured["headroom_calls"] == [("USA", 2030, 450.0)]
