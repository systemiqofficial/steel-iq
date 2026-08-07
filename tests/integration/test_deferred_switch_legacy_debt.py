"""Tests that a deferred technology switch books the old technology's debt tail from
decision-time state.

A switch decided in year Y executes in year Y + construction_time, by which point
finalise_iteration may already have replaced the furnace group's lifetime for the new
cycle (boundary-crossing switches). Reading the lifetime at execution then re-amortises
a fresh full-life loan of the old technology; the fix anchors the tail to the
decision-time remaining lifetime carried on the command (``remaining_lifetime``).
"""

from types import SimpleNamespace

import pytest

from steelo.devdata import get_furnace_group, get_plant
from steelo.domain import PointInTime, TimeFrame, Volumes, Year
from steelo.domain import commands
from steelo.domain.calculate_costs import calculate_debt_repayment
from steelo.service_layer.handlers import execute_scheduled_technology_switch

PLANT_LIFETIME = 20
CONSTRUCTION_TIME = 4
OLD_CAPEX = 400.0
NEW_CAPEX = 600.0
CAPACITY = 100
BOM = {"materials": {"ore": {"unit_cost": 100.0, "demand": 1.4}}, "energy": {}}


def make_plant(cycle_start: int, boundary_end: int):
    """Build an EAF plant whose only furnace group runs the given renovation cycle.

    Args:
        cycle_start: First year of the furnace group's current lifetime cycle.
        boundary_end: Renovation-boundary year (``lifetime.time_frame.end``).

    Returns:
        Tuple of (plant, furnace_group) positioned at current year 2025.
    """
    furnace_group = get_furnace_group(
        fg_id="fg_probe",
        tech_name="EAF",
        capacity=Volumes(CAPACITY),
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(cycle_start), end=Year(boundary_end)),
            plant_lifetime=PLANT_LIFETIME,
        ),
    )
    furnace_group.technology.capex = OLD_CAPEX
    plant = get_plant(furnace_groups=[furnace_group], plant_id="plant_probe")
    plant.technology_unit_fopex = {"eaf": 50.0, "dri": 70.0}
    return plant, furnace_group


def make_switch_command(plant, furnace_group) -> commands.ChangeFurnaceGroupTechnology:
    """Build the switch command as PAM would at decision time, capturing remaining lifetime."""
    return commands.ChangeFurnaceGroupTechnology(
        plant_id=plant.plant_id,
        furnace_group_id=furnace_group.furnace_group_id,
        technology_name="DRI",
        old_technology_name="EAF",
        npv=1.0,
        cosa=0.0,
        utilisation=0.7,
        capex=NEW_CAPEX,
        capex_no_subsidy=NEW_CAPEX,
        capacity=furnace_group.capacity,
        remaining_lifetime=furnace_group.lifetime.remaining_number_of_years,
        bom=BOM,
        chosen_reductant="",
        cost_of_debt=0.05,
        cost_of_debt_no_subsidy=0.05,
        capex_subsidies=[],
        debt_subsidies=[],
    )


def run_deferred_switch(plant, furnace_group, decision_year: int = 2025):
    """Replay decision → finalise-iteration years → deferred execution for one switch.

    Mirrors finalise_iteration's per-year steps: advance the lifetime (3a), execute the
    scheduled switch in its effective year via the real handler (3b), and flip a
    boundary-crossing group to "construction switching technology" with a fresh
    lifetime (3c).

    Returns:
        The furnace group after the deferred execution.
    """
    year_of_switch = decision_year + CONSTRUCTION_TIME
    cmd = make_switch_command(plant, furnace_group)
    plant.change_furnace_group_status_to_switching_technology(furnace_group.furnace_group_id, year_of_switch, cmd)

    env = SimpleNamespace(
        config=SimpleNamespace(plant_lifetime=PLANT_LIFETIME, construction_time=CONSTRUCTION_TIME),
        dynamic_feedstocks={},
    )
    uow = SimpleNamespace(plants=SimpleNamespace(get=lambda plant_id: plant))

    for env_year in range(decision_year + 1, year_of_switch + 1):
        furnace_group.lifetime.current = Year(env_year)
        if furnace_group.future_switch_year == env_year and furnace_group.future_switch_cmd is not None:
            execute_scheduled_technology_switch(furnace_group.future_switch_cmd, uow=uow, env=env)
            continue
        if (
            furnace_group.lifetime.current > furnace_group.lifetime.time_frame.end
            and furnace_group.status.lower() == "operating switching technology"
        ):
            furnace_group.status = "construction switching technology"
            furnace_group.utilization_rate = 0.0
            furnace_group.lifetime = PointInTime(
                plant_lifetime=PLANT_LIFETIME,
                current=Year(env_year),
                time_frame=TimeFrame(
                    start=Year(year_of_switch),
                    end=Year(year_of_switch + PLANT_LIFETIME),
                ),
            )
    return furnace_group


def test_switch_decided_at_boundary_books_no_legacy_debt():
    """A switch decided with zero remaining lifetime carries no old-technology debt.

    Notes:
        Regression for the phantom legacy-debt bug: the boundary crossing replaced the
        lifetime before the deferred execution, so the capture read 20 remaining years
        and booked a full re-amortised loan of the already-repaid old technology.
    """
    plant, furnace_group = make_plant(cycle_start=2005, boundary_end=2025)
    assert furnace_group.lifetime.remaining_number_of_years == 0

    run_deferred_switch(plant, furnace_group)

    assert furnace_group.legacy_debt_schedule == []


def test_switch_crossing_boundary_inside_construction_window_books_no_legacy_debt():
    """Remaining lifetime shorter than construction time: old debt amortises out, none is booked."""
    plant, furnace_group = make_plant(cycle_start=2007, boundary_end=2027)
    assert furnace_group.lifetime.remaining_number_of_years == 2

    run_deferred_switch(plant, furnace_group)

    assert furnace_group.legacy_debt_schedule == []


def test_mid_life_switch_books_decision_time_tail_net_of_construction():
    """A mid-life switch books exactly the old loan's tail left after the construction window."""
    plant, furnace_group = make_plant(cycle_start=2011, boundary_end=2031)
    assert furnace_group.lifetime.remaining_number_of_years == 6

    run_deferred_switch(plant, furnace_group)

    expected_tail = calculate_debt_repayment(
        total_investment=OLD_CAPEX * CAPACITY,
        equity_share=furnace_group.equity_share,
        lifetime=PLANT_LIFETIME,
        cost_of_debt=0.05,
        lifetime_remaining=6 - CONSTRUCTION_TIME,
    )
    assert furnace_group.legacy_debt_schedule == pytest.approx(expected_tail)
    assert len(furnace_group.legacy_debt_schedule) == 2


def test_second_switch_combines_existing_legacy_debt_once():
    """A switch on a group already carrying legacy debt merges it into the new schedule once.

    Notes:
        The capture previously read debt_repayment_per_year, which already merges
        legacy_debt_schedule, and then the combine step added the existing legacy
        again — double-counting the first switch's debt on every later switch.
    """
    plant, furnace_group = make_plant(cycle_start=2011, boundary_end=2031)
    prior_legacy = [100.0, 100.0, 100.0]
    furnace_group.legacy_debt_schedule = list(prior_legacy)

    plant.change_furnace_group_technology(
        furnace_group_id=furnace_group.furnace_group_id,
        technology_name="DRI",
        plant_lifetime=PLANT_LIFETIME,
        lag=0,
        capex=NEW_CAPEX,
        capex_no_subsidy=NEW_CAPEX,
        cost_of_debt=0.05,
        cost_of_debt_no_subsidy=0.05,
        bom=BOM,
        legacy_years=furnace_group.lifetime.remaining_number_of_years,
    )

    old_tech_tail = calculate_debt_repayment(
        total_investment=OLD_CAPEX * CAPACITY,
        equity_share=furnace_group.equity_share,
        lifetime=PLANT_LIFETIME,
        cost_of_debt=0.05,
        lifetime_remaining=6,
    )
    expected = [
        tail_payment + (prior_legacy[i] if i < len(prior_legacy) else 0.0)
        for i, tail_payment in enumerate(old_tech_tail)
    ]
    assert furnace_group.legacy_debt_schedule == pytest.approx(expected)
