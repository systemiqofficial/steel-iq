"""Tests for Environment CO2 storage counters, helpers, and year-start baseline scan."""

from dataclasses import dataclass
from pathlib import Path

import pytest

from steelo.domain import Year
from steelo.domain.models import (
    Environment,
    FurnaceGroup,
    Location,
    Plant,
    PointInTime,
    PrimaryFeedstock,
    SecondaryFeedstockConstraint,
    Technology,
    TimeFrame,
    commands,
)
from steelo.simulation import SimulationConfig
from steelo.simulation_types import TechnologySettings


# ---- Fixtures ----


@dataclass
class FakePlantsRepo:
    plants: list[Plant]

    def list(self) -> list[Plant]:
        return list(self.plants)


@dataclass
class FakeUoW:
    plants: FakePlantsRepo


def _make_env(tmp_path: Path, constraints: list[SecondaryFeedstockConstraint] | None = None) -> Environment:
    tech_settings = {
        "BF": TechnologySettings(allowed=True, from_year=2025, to_year=None),
        "BFCCS": TechnologySettings(allowed=True, from_year=2030, to_year=None),
        "DRICCS": TechnologySettings(allowed=True, from_year=2030, to_year=None),
    }
    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2050),
        master_excel_path=Path("test.xlsx"),
        output_dir=tmp_path,
        technology_settings=tech_settings,
        capacity_limit=0.95,
        co2_storage_reserved_discount_factor=0.9,
    )
    tech_switches_csv = tmp_path / "tech_switches_allowed.csv"
    tech_switches_csv.write_text("origin,BF,BFCCS\nBF,YES,YES\n", encoding="utf-8")
    env = Environment(config=config, tech_switches_csv=tech_switches_csv)
    if constraints is not None:
        env.secondary_feedstock_constraints = constraints
    return env


def _make_ccs_tech(name: str = "BFCCS", reductant: str = "Coke+PCI", co2_stored: float = 2.853) -> Technology:
    pf = PrimaryFeedstock(metallic_charge="IO_low", reductant=reductant, technology=name)
    pf.add_carbon_output("co2_stored", co2_stored)
    return Technology(name=name, product="hot_metal", dynamic_business_case=[pf])


def _make_non_ccs_tech(name: str = "BF", reductant: str = "Coke+PCI") -> Technology:
    pf = PrimaryFeedstock(metallic_charge="IO_low", reductant=reductant, technology=name)
    return Technology(name=name, product="hot_metal", dynamic_business_case=[pf])


def _make_fg(
    fg_id: str,
    technology: Technology,
    capacity: float,
    status: str,
    chosen_reductant: str = "Coke+PCI",
    future_switch_cmd: commands.ChangeFurnaceGroupTechnology | None = None,
) -> FurnaceGroup:
    fg = FurnaceGroup(
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
    )
    if future_switch_cmd is not None:
        fg.future_switch_cmd = future_switch_cmd
    return fg


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


def _make_uow(plants: list[Plant]) -> FakeUoW:
    return FakeUoW(plants=FakePlantsRepo(plants=plants))


# ---- get_co2_need ----


def test_get_co2_need_applies_capacity_limit(tmp_path):
    env = _make_env(tmp_path)
    tech = _make_ccs_tech(co2_stored=2.0)

    need = env.get_co2_need(tech, capacity=1000.0, reductant="Coke+PCI")

    assert need == pytest.approx(2.0 * 1000.0 * 0.95)


def test_get_co2_need_by_name_uses_env_dynamic_feedstocks(tmp_path):
    env = _make_env(tmp_path)
    pf = PrimaryFeedstock(metallic_charge="IO_low", reductant="Coke+PCI", technology="BFCCS")
    pf.add_carbon_output("co2_stored", 2.5)
    env.dynamic_feedstocks["BFCCS"] = [pf]

    need = env.get_co2_need_by_name("BFCCS", capacity=500.0, reductant="Coke+PCI")

    assert need == pytest.approx(2.5 * 500.0 * 0.95)


def test_get_co2_need_by_name_returns_zero_for_unknown_tech(tmp_path):
    env = _make_env(tmp_path)

    assert env.get_co2_need_by_name("NOT_IN_FEEDSTOCKS", capacity=1000.0, reductant="Coke+PCI") == 0.0


def test_get_co2_need_non_ccs_tech_returns_zero(tmp_path):
    env = _make_env(tmp_path)
    tech = _make_non_ccs_tech()  # no co2_stored in carbon_outputs

    assert env.get_co2_need(tech, capacity=1000.0, reductant="Coke+PCI") == 0.0


# ---- _co2_storage_limit_for_year year-lookup rule ----


def test_year_lookup_returns_zero_before_earliest(tmp_path):
    c = SecondaryFeedstockConstraint(
        secondary_feedstock_name="co2_stored",
        region_iso3s=["USA"],
        maximum_constraint_per_year={Year(2024): 10.0, Year(2027): 20.0, Year(2030): 30.0},
    )
    env = _make_env(tmp_path, constraints=[c])

    assert env._co2_storage_limit_for_year("USA", 2023) == 0.0


def test_year_lookup_picks_max_leq_year_with_interior_gap(tmp_path):
    c = SecondaryFeedstockConstraint(
        secondary_feedstock_name="co2_stored",
        region_iso3s=["USA"],
        maximum_constraint_per_year={Year(2024): 10.0, Year(2027): 20.0, Year(2030): 30.0},
    )
    env = _make_env(tmp_path, constraints=[c])

    assert env._co2_storage_limit_for_year("USA", 2025) == 10.0
    assert env._co2_storage_limit_for_year("USA", 2028) == 20.0
    assert env._co2_storage_limit_for_year("USA", 2030) == 30.0


def test_year_lookup_carries_forward_past_latest(tmp_path):
    c = SecondaryFeedstockConstraint(
        secondary_feedstock_name="co2_stored",
        region_iso3s=["USA"],
        maximum_constraint_per_year={Year(2024): 10.0, Year(2030): 30.0},
    )
    env = _make_env(tmp_path, constraints=[c])

    assert env._co2_storage_limit_for_year("USA", 2060) == 30.0


def test_year_lookup_missing_country_returns_zero(tmp_path):
    c = SecondaryFeedstockConstraint(
        secondary_feedstock_name="co2_stored",
        region_iso3s=["USA"],
        maximum_constraint_per_year={Year(2024): 10.0},
    )
    env = _make_env(tmp_path, constraints=[c])

    assert env._co2_storage_limit_for_year("DEU", 2025) == 0.0


def test_year_lookup_ignores_non_co2_constraints(tmp_path):
    c = SecondaryFeedstockConstraint(
        secondary_feedstock_name="bio_pci",
        region_iso3s=["USA"],
        maximum_constraint_per_year={Year(2024): 100.0},
    )
    env = _make_env(tmp_path, constraints=[c])

    assert env._co2_storage_limit_for_year("USA", 2025) == 0.0


# ---- get_co2_headroom formula ----


def test_headroom_is_limit_minus_firm_minus_reserved(tmp_path):
    c = SecondaryFeedstockConstraint(
        secondary_feedstock_name="co2_stored",
        region_iso3s=["USA"],
        maximum_constraint_per_year={Year(2024): 1000.0},
    )
    env = _make_env(tmp_path, constraints=[c])
    env.co2_storage_firm["USA"] = 300.0
    env.co2_storage_reserved["USA"] = 200.0

    assert env.get_co2_headroom("USA", 2025) == pytest.approx(500.0)


def test_headroom_exempts_own_reserved_contribution(tmp_path):
    """P1 passes its own d*need as own_reserved_contribution so it doesn't double-count."""
    c = SecondaryFeedstockConstraint(
        secondary_feedstock_name="co2_stored",
        region_iso3s=["USA"],
        maximum_constraint_per_year={Year(2024): 1000.0},
    )
    env = _make_env(tmp_path, constraints=[c])
    env.co2_storage_firm["USA"] = 300.0
    env.co2_storage_reserved["USA"] = 200.0  # includes this FG's d*need = 90

    assert env.get_co2_headroom("USA", 2025, own_reserved_contribution=90.0) == pytest.approx(590.0)


def test_headroom_zero_when_no_constraint_for_country(tmp_path):
    """Missing CO2 storage constraint → headroom = 0 (CCS disallowed), opposite of usual
    missing-means-unlimited convention."""
    env = _make_env(tmp_path, constraints=[])
    env.co2_storage_firm["DEU"] = 0.0
    env.co2_storage_reserved["DEU"] = 0.0

    assert env.get_co2_headroom("DEU", 2025) == 0.0


def test_headroom_zero_before_earliest_year(tmp_path):
    c = SecondaryFeedstockConstraint(
        secondary_feedstock_name="co2_stored",
        region_iso3s=["USA"],
        maximum_constraint_per_year={Year(2030): 100.0},
    )
    env = _make_env(tmp_path, constraints=[c])

    assert env.get_co2_headroom("USA", 2025) == 0.0


# ---- co2_storage_diagnostics ----


def test_diagnostics_returns_firm_reserved_limit_tuple(tmp_path):
    """Helper used by gate log lines to surface (firm, reserved, limit) without leaking env."""
    c = SecondaryFeedstockConstraint(
        secondary_feedstock_name="co2_stored",
        region_iso3s=["USA"],
        maximum_constraint_per_year={Year(2024): 1000.0},
    )
    env = _make_env(tmp_path, constraints=[c])
    env.co2_storage_firm["USA"] = 300.0
    env.co2_storage_reserved["USA"] = 200.0

    firm, reserved, limit = env.co2_storage_diagnostics("USA", 2025)

    assert firm == pytest.approx(300.0)
    assert reserved == pytest.approx(200.0)
    assert limit == pytest.approx(1000.0)


def test_diagnostics_reflects_counter_mutation(tmp_path):
    """Snapshot must observe live counter state — used mid-simulation."""
    c = SecondaryFeedstockConstraint(
        secondary_feedstock_name="co2_stored",
        region_iso3s=["USA"],
        maximum_constraint_per_year={Year(2024): 500.0},
    )
    env = _make_env(tmp_path, constraints=[c])

    firm0, reserved0, limit0 = env.co2_storage_diagnostics("USA", 2025)
    assert (firm0, reserved0, limit0) == pytest.approx((0.0, 0.0, 500.0))

    env.co2_storage_firm["USA"] = 100.0
    env.co2_storage_reserved["USA"] = 50.0
    firm1, reserved1, limit1 = env.co2_storage_diagnostics("USA", 2025)

    assert firm1 == pytest.approx(100.0)
    assert reserved1 == pytest.approx(50.0)
    assert limit1 == pytest.approx(500.0)


def test_diagnostics_unknown_country_returns_zeros(tmp_path):
    env = _make_env(tmp_path, constraints=[])

    firm, reserved, limit = env.co2_storage_diagnostics("DEU", 2025)

    assert firm == 0.0
    assert reserved == 0.0
    assert limit == 0.0


def test_diagnostics_honours_year_lookup_rule(tmp_path):
    """limit follows strict-0 pre-earliest, carry-forward past-latest."""
    c = SecondaryFeedstockConstraint(
        secondary_feedstock_name="co2_stored",
        region_iso3s=["USA"],
        maximum_constraint_per_year={Year(2030): 100.0, Year(2040): 200.0},
    )
    env = _make_env(tmp_path, constraints=[c])

    assert env.co2_storage_diagnostics("USA", 2025)[2] == 0.0  # pre-earliest
    assert env.co2_storage_diagnostics("USA", 2035)[2] == pytest.approx(100.0)  # interior
    assert env.co2_storage_diagnostics("USA", 2060)[2] == pytest.approx(200.0)  # carry-forward


# ---- scan_co2_storage_counters ----


def test_scan_counts_operating_as_firm(tmp_path):
    env = _make_env(tmp_path)
    fg = _make_fg("fg1", _make_ccs_tech(co2_stored=2.0), capacity=1000.0, status="operating")
    uow = _make_uow([_make_plant("p1", "USA", [fg])])

    env.scan_co2_storage_counters(uow)

    assert env.co2_storage_firm["USA"] == pytest.approx(2.0 * 1000.0 * 0.95)
    assert "USA" not in env.co2_storage_reserved


def test_scan_counts_construction_as_firm(tmp_path):
    env = _make_env(tmp_path)
    fg = _make_fg("fg1", _make_ccs_tech(co2_stored=2.0), capacity=1000.0, status="construction")
    uow = _make_uow([_make_plant("p1", "USA", [fg])])

    env.scan_co2_storage_counters(uow)

    assert env.co2_storage_firm["USA"] == pytest.approx(2.0 * 1000.0 * 0.95)


def test_scan_counts_operating_pre_retirement_as_firm(tmp_path):
    env = _make_env(tmp_path)
    fg = _make_fg("fg1", _make_ccs_tech(co2_stored=2.0), capacity=1000.0, status="operating pre-retirement")
    uow = _make_uow([_make_plant("p1", "USA", [fg])])

    env.scan_co2_storage_counters(uow)

    assert env.co2_storage_firm["USA"] == pytest.approx(2.0 * 1000.0 * 0.95)


def test_scan_counts_announced_as_reserved_with_discount(tmp_path):
    env = _make_env(tmp_path)
    fg = _make_fg("fg1", _make_ccs_tech(co2_stored=2.0), capacity=1000.0, status="announced")
    uow = _make_uow([_make_plant("p1", "USA", [fg])])

    env.scan_co2_storage_counters(uow)

    assert env.co2_storage_reserved["USA"] == pytest.approx(0.9 * 2.0 * 1000.0 * 0.95)
    assert "USA" not in env.co2_storage_firm


def test_scan_excludes_considered_discarded_closed(tmp_path):
    env = _make_env(tmp_path)
    tech = _make_ccs_tech(co2_stored=2.0)
    fgs = [
        _make_fg("fg1", tech, capacity=1000.0, status="considered"),
        _make_fg("fg2", tech, capacity=1000.0, status="discarded"),
        _make_fg("fg3", tech, capacity=1000.0, status="closed"),
    ]
    uow = _make_uow([_make_plant("p1", "USA", fgs)])

    env.scan_co2_storage_counters(uow)

    assert env.co2_storage_firm == {}
    assert env.co2_storage_reserved == {}


def test_scan_switching_window_reads_future_switch_cmd(tmp_path):
    """FG in 'operating switching technology' with committed CCS future switch →
    firm counts the NEW tech's need, not the (non-CCS) current tech's."""
    env = _make_env(tmp_path)
    # Seed env.dynamic_feedstocks so get_co2_need_by_name finds the new tech's BOM.
    pf = PrimaryFeedstock(metallic_charge="IO_low", reductant="Coke+PCI", technology="BFCCS")
    pf.add_carbon_output("co2_stored", 2.5)
    env.dynamic_feedstocks["BFCCS"] = [pf]

    old_tech = _make_non_ccs_tech(name="BF")  # currently still BF
    future_cmd = commands.ChangeFurnaceGroupTechnology(
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
    fg = _make_fg(
        "fg1",
        old_tech,
        capacity=1000.0,
        status="operating switching technology",
        chosen_reductant="Coke+PCI",
        future_switch_cmd=future_cmd,
    )
    uow = _make_uow([_make_plant("p1", "USA", [fg])])

    env.scan_co2_storage_counters(uow)

    assert env.co2_storage_firm["USA"] == pytest.approx(2.5 * 1000.0 * 0.95)


def test_scan_post_switch_reads_fg_technology_no_double_count(tmp_path):
    """After finalise_iteration executes the switch: status is 'operating', fg.technology
    is the new CCS tech, fg.future_switch_cmd may still be set (stale). The status-keyed
    rule should read fg.technology, not the stale future_switch_cmd, so no double count."""
    env = _make_env(tmp_path)
    stale_cmd = commands.ChangeFurnaceGroupTechnology(
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
    new_tech = _make_ccs_tech(name="BFCCS", co2_stored=2.5)
    fg = _make_fg(
        "fg1",
        new_tech,
        capacity=1000.0,
        status="operating",
        chosen_reductant="Coke+PCI",
        future_switch_cmd=stale_cmd,
    )
    uow = _make_uow([_make_plant("p1", "USA", [fg])])

    env.scan_co2_storage_counters(uow)

    # Not 2.5 * 1000 * 0.95 * 2 (which would be the double-count bug)
    assert env.co2_storage_firm["USA"] == pytest.approx(2.5 * 1000.0 * 0.95)


def test_scan_is_idempotent(tmp_path):
    """Second call produces identical counters — rebuild-from-scratch invariant."""
    env = _make_env(tmp_path)
    fgs = [
        _make_fg("fg1", _make_ccs_tech(co2_stored=2.0), capacity=1000.0, status="operating"),
        _make_fg("fg2", _make_ccs_tech(co2_stored=2.0), capacity=500.0, status="announced"),
    ]
    uow = _make_uow([_make_plant("p1", "USA", fgs)])

    env.scan_co2_storage_counters(uow)
    first_firm = dict(env.co2_storage_firm)
    first_reserved = dict(env.co2_storage_reserved)

    env.scan_co2_storage_counters(uow)

    assert env.co2_storage_firm == first_firm
    assert env.co2_storage_reserved == first_reserved


def test_scan_zeroes_prior_counters_self_healing(tmp_path):
    """Prior-year counters are cleared at scan start — closed/discarded FGs from last
    year free up their storage automatically without explicit unreserve bookkeeping."""
    env = _make_env(tmp_path)
    env.co2_storage_firm["USA"] = 99999.0
    env.co2_storage_reserved["USA"] = 88888.0
    env.co2_storage_firm["DEU"] = 77777.0

    uow = _make_uow([])  # no plants at all
    env.scan_co2_storage_counters(uow)

    assert env.co2_storage_firm == {}
    assert env.co2_storage_reserved == {}


def test_scan_aggregates_multiple_plants_same_country(tmp_path):
    env = _make_env(tmp_path)
    tech = _make_ccs_tech(co2_stored=2.0)
    plants = [
        _make_plant("p1", "USA", [_make_fg("fg1", tech, 1000.0, "operating")]),
        _make_plant("p2", "USA", [_make_fg("fg2", tech, 500.0, "operating")]),
    ]
    uow = _make_uow(plants)

    env.scan_co2_storage_counters(uow)

    assert env.co2_storage_firm["USA"] == pytest.approx(2.0 * 1500.0 * 0.95)


def test_scan_splits_by_country(tmp_path):
    env = _make_env(tmp_path)
    tech = _make_ccs_tech(co2_stored=2.0)
    plants = [
        _make_plant("p1", "USA", [_make_fg("fg1", tech, 1000.0, "operating")]),
        _make_plant("p2", "DEU", [_make_fg("fg2", tech, 500.0, "operating")]),
    ]
    uow = _make_uow(plants)

    env.scan_co2_storage_counters(uow)

    assert env.co2_storage_firm["USA"] == pytest.approx(2.0 * 1000.0 * 0.95)
    assert env.co2_storage_firm["DEU"] == pytest.approx(2.0 * 500.0 * 0.95)


def test_scan_skips_non_ccs_techs(tmp_path):
    env = _make_env(tmp_path)
    non_ccs = _make_non_ccs_tech(name="BF")
    fg = _make_fg("fg1", non_ccs, capacity=1000.0, status="operating")
    uow = _make_uow([_make_plant("p1", "USA", [fg])])

    env.scan_co2_storage_counters(uow)

    assert env.co2_storage_firm == {}


def test_scan_handles_switching_window_without_future_cmd(tmp_path):
    """Guard: status is a switching-window status but future_switch_cmd is None (should
    not happen in normal flow, but scan must not crash)."""
    env = _make_env(tmp_path)
    fg = _make_fg(
        "fg1",
        _make_non_ccs_tech(name="BF"),
        capacity=1000.0,
        status="operating switching technology",
        future_switch_cmd=None,
    )
    uow = _make_uow([_make_plant("p1", "USA", [fg])])

    env.scan_co2_storage_counters(uow)  # should not raise

    assert env.co2_storage_firm == {}


def test_scan_warns_on_announced_ccs_with_empty_bom(tmp_path, caplog):
    """FG in announced with is_ccs_or_ccu=True but empty dynamic_business_case → scan
    contributes 0 to reserved and logs a warning to signal the dict misconfiguration."""
    env = _make_env(tmp_path)
    broken_tech = Technology(name="BFCCS", product="hot_metal", dynamic_business_case=None)
    fg = _make_fg("fg1", broken_tech, capacity=1000.0, status="announced")
    uow = _make_uow([_make_plant("p1", "USA", [fg])])

    with caplog.at_level("WARNING"):
        env.scan_co2_storage_counters(uow)

    assert env.co2_storage_reserved == {}
    assert any("empty dynamic_business_case" in r.getMessage() for r in caplog.records)
