"""Tests for _seed_opening_balances and opening_balance_multiplier config."""

import pytest
from datetime import date
from types import SimpleNamespace

from steelo.domain.models import (
    FurnaceGroup,
    Location,
    Plant,
    PlantGroup,
    PointInTime,
    ProductCategory,
    Technology,
    TimeFrame,
    Volumes,
    Year,
)
from steelo.simulation import SimulationConfig, _seed_opening_balances


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ACTIVE_STATUSES = ["operating", "operating pre-retirement", "operating switching technology"]


def _make_fg(
    fg_id: str = "fg-1",
    tech_name: str = "BF",
    product: str = "iron",
    capacity: float = 1_000_000.0,
    status: str = "operating",
    equity_share: float = 0.2,
    created_by_PAM: bool = False,
) -> FurnaceGroup:
    """Create a minimal FurnaceGroup for testing."""
    return FurnaceGroup(
        furnace_group_id=fg_id,
        capacity=Volumes(capacity),
        status=status,
        last_renovation_date=date(2020, 1, 1),
        technology=Technology(name=tech_name, product=product),
        historical_production={},
        utilization_rate=0.8,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2005), end=Year(2045)),
            plant_lifetime=20,
        ),
        equity_share=equity_share,
        created_by_PAM=created_by_PAM,
    )


def _make_plant(
    plant_id: str = "plant-1",
    iso3: str = "DEU",
    furnace_groups: list[FurnaceGroup] | None = None,
) -> Plant:
    """Create a minimal Plant for testing."""
    return Plant(
        plant_id=plant_id,
        location=Location(lat=50.0, lon=8.0, country="Germany", region="Europe", iso3=iso3),
        furnace_groups=furnace_groups or [],
        power_source="grid",
        soe_status="private",
        parent_gem_id="gem-1",
        workforce_size=500,
        certified=False,
        category_steel_product={ProductCategory("Flat")},
        technology_unit_fopex={},
    )


def _make_pg(plants: list[Plant], plant_group_id: str = "gem-1") -> PlantGroup:
    """Wrap plants in a PlantGroup so the seeder can run group-first."""
    return PlantGroup(plant_group_id=plant_group_id, plants=plants)


def _make_env(
    greenfield_capex: dict | None = None,
    renovation_share: dict | None = None,
    iso3_to_region: dict | None = None,
) -> SimpleNamespace:
    """Create a minimal env mock with CAPEX data."""
    if iso3_to_region is None:
        iso3_to_region = {"DEU": "Europe"}
    if greenfield_capex is None:
        greenfield_capex = {"Europe": {"BF": 800.0, "EAF": 600.0}}
    if renovation_share is None:
        renovation_share = {"BF": 0.45, "EAF": 0.35}

    country_mappings = SimpleNamespace(
        iso3_to_region=lambda: iso3_to_region,
    )
    return SimpleNamespace(
        name_to_capex={"greenfield": greenfield_capex},
        capex_renovation_share=renovation_share,
        country_mappings=country_mappings,
    )


# ---------------------------------------------------------------------------
# _seed_opening_balances tests
# ---------------------------------------------------------------------------


def test_single_fg_group_balance_calculation():
    """Group balance = greenfield_capex * renovation_share * capacity * equity_share * multiplier."""
    fg = _make_fg(capacity=1_000_000.0, equity_share=0.2)
    plant = _make_plant(furnace_groups=[fg])
    pg = _make_pg([plant])
    env = _make_env()

    _seed_opening_balances([pg], env, multiplier=1.0, active_statuses=ACTIVE_STATUSES)

    # 800 * 0.45 * 1_000_000 * 0.2 * 1.0 = 72_000_000
    expected = 800.0 * 0.45 * 1_000_000.0 * 0.2 * 1.0
    assert pg.balance == pytest.approx(expected)


def test_multi_fg_plant_sums_all():
    """Group balance aggregates across all eligible FGs of all plants in the group."""
    fg1 = _make_fg(fg_id="fg-1", tech_name="BF", capacity=500_000.0)
    fg2 = _make_fg(fg_id="fg-2", tech_name="EAF", capacity=300_000.0, product="steel")
    plant = _make_plant(furnace_groups=[fg1, fg2])
    pg = _make_pg([plant])
    env = _make_env()

    _seed_opening_balances([pg], env, multiplier=1.0, active_statuses=ACTIVE_STATUSES)

    bf_cost = 800.0 * 0.45 * 500_000.0 * 0.2
    eaf_cost = 600.0 * 0.35 * 300_000.0 * 0.2
    assert pg.balance == pytest.approx(bf_cost + eaf_cost)


def test_multiplier_scales_balance():
    """Multiplier scales the seeded balance linearly."""
    fg = _make_fg(capacity=1_000_000.0)
    env = _make_env()

    for mult in [0.5, 1.0, 2.0]:
        plant = _make_plant(furnace_groups=[fg])
        pg = _make_pg([plant])
        _seed_opening_balances([pg], env, multiplier=mult, active_statuses=ACTIVE_STATUSES)
        expected = 800.0 * 0.45 * 1_000_000.0 * 0.2 * mult
        assert pg.balance == pytest.approx(expected), f"Failed for multiplier={mult}"


def test_multiplier_zero_disables_seeding():
    """Multiplier of 0.0 results in zero balance."""
    fg = _make_fg(capacity=1_000_000.0)
    plant = _make_plant(furnace_groups=[fg])
    pg = _make_pg([plant])
    env = _make_env()

    _seed_opening_balances([pg], env, multiplier=0.0, active_statuses=ACTIVE_STATUSES)

    assert pg.balance == 0.0


def test_skip_created_by_pam():
    """FGs created by PAM are excluded from opening balance."""
    fg = _make_fg(created_by_PAM=True, capacity=1_000_000.0)
    plant = _make_plant(furnace_groups=[fg])
    pg = _make_pg([plant])
    env = _make_env()

    _seed_opening_balances([pg], env, multiplier=1.0, active_statuses=ACTIVE_STATUSES)

    assert pg.balance == 0.0


def test_skip_inactive_status():
    """FGs with non-active statuses are excluded."""
    for status in ["announced", "construction", "closed", "discarded"]:
        fg = _make_fg(status=status)
        plant = _make_plant(furnace_groups=[fg])
        pg = _make_pg([plant])
        env = _make_env()

        _seed_opening_balances([pg], env, multiplier=1.0, active_statuses=ACTIVE_STATUSES)

        assert pg.balance == 0.0, f"Expected 0 for status={status}"


def test_skip_zero_capacity():
    """FGs with zero capacity are excluded."""
    fg = _make_fg(capacity=0.0)
    plant = _make_plant(furnace_groups=[fg])
    pg = _make_pg([plant])
    env = _make_env()

    _seed_opening_balances([pg], env, multiplier=1.0, active_statuses=ACTIVE_STATUSES)

    assert pg.balance == 0.0


def test_skip_other_technology():
    """FGs with 'other' technology are excluded."""
    fg = _make_fg(tech_name="other")
    plant = _make_plant(furnace_groups=[fg])
    pg = _make_pg([plant])
    env = _make_env(
        greenfield_capex={"Europe": {"other": 500.0}},
        renovation_share={"other": 0.3},
    )

    _seed_opening_balances([pg], env, multiplier=1.0, active_statuses=ACTIVE_STATUSES)

    assert pg.balance == 0.0


def test_skip_missing_region_mapping(caplog):
    """FGs whose plant ISO3 has no region mapping are skipped with warning."""
    fg = _make_fg(capacity=1_000_000.0)
    plant = _make_plant(iso3="ZZZ", furnace_groups=[fg])
    pg = _make_pg([plant])
    env = _make_env(iso3_to_region={"DEU": "Europe"})  # ZZZ not mapped

    _seed_opening_balances([pg], env, multiplier=1.0, active_statuses=ACTIVE_STATUSES)

    assert pg.balance == 0.0
    assert "No region mapping for ZZZ" in caplog.text


def test_skip_missing_greenfield_capex(caplog):
    """FGs whose tech has no greenfield CAPEX in the region are skipped with warning."""
    fg = _make_fg(tech_name="DRI", capacity=1_000_000.0)
    plant = _make_plant(furnace_groups=[fg])
    pg = _make_pg([plant])
    env = _make_env(
        greenfield_capex={"Europe": {"BF": 800.0}},  # no DRI
        renovation_share={"DRI": 0.4},
    )

    _seed_opening_balances([pg], env, multiplier=1.0, active_statuses=ACTIVE_STATUSES)

    assert pg.balance == 0.0
    assert "No greenfield CAPEX for DRI" in caplog.text


def test_skip_missing_renovation_share(caplog):
    """FGs whose tech has no renovation share are skipped with warning."""
    fg = _make_fg(tech_name="BF", capacity=1_000_000.0)
    plant = _make_plant(furnace_groups=[fg])
    pg = _make_pg([plant])
    env = _make_env(
        greenfield_capex={"Europe": {"BF": 800.0}},
        renovation_share={},  # empty
    )

    _seed_opening_balances([pg], env, multiplier=1.0, active_statuses=ACTIVE_STATUSES)

    assert pg.balance == 0.0
    assert "No renovation share for BF" in caplog.text


def test_mixed_eligible_and_ineligible_fgs():
    """Only eligible FGs contribute; ineligible ones are silently skipped."""
    eligible = _make_fg(fg_id="fg-ok", capacity=1_000_000.0)
    pam_created = _make_fg(fg_id="fg-pam", capacity=1_000_000.0, created_by_PAM=True)
    zero_cap = _make_fg(fg_id="fg-zero", capacity=0.0)
    other_tech = _make_fg(fg_id="fg-other", tech_name="other")
    inactive = _make_fg(fg_id="fg-closed", status="closed")

    plant = _make_plant(furnace_groups=[eligible, pam_created, zero_cap, other_tech, inactive])
    pg = _make_pg([plant])
    env = _make_env(
        greenfield_capex={"Europe": {"BF": 800.0, "other": 500.0}},
        renovation_share={"BF": 0.45, "other": 0.3},
    )

    _seed_opening_balances([pg], env, multiplier=1.0, active_statuses=ACTIVE_STATUSES)

    expected = 800.0 * 0.45 * 1_000_000.0 * 0.2
    assert pg.balance == pytest.approx(expected)


def test_active_statuses_case_insensitive():
    """Status matching is case-insensitive."""
    fg = _make_fg(status="Operating", capacity=1_000_000.0)
    plant = _make_plant(furnace_groups=[fg])
    pg = _make_pg([plant])
    env = _make_env()

    _seed_opening_balances([pg], env, multiplier=1.0, active_statuses=ACTIVE_STATUSES)

    assert pg.balance > 0


def test_historic_balance_untouched():
    """FG.historic_balance is not modified by seeding."""
    fg = _make_fg(capacity=1_000_000.0)
    plant = _make_plant(furnace_groups=[fg])
    pg = _make_pg([plant])
    env = _make_env()

    _seed_opening_balances([pg], env, multiplier=1.0, active_statuses=ACTIVE_STATUSES)

    assert fg.historic_balance == 0.0


def test_multiple_plants_in_single_group():
    """Plants sharing a group contribute to a single group balance."""
    fg1 = _make_fg(fg_id="fg-1", capacity=1_000_000.0)
    fg2 = _make_fg(fg_id="fg-2", capacity=500_000.0)
    plant1 = _make_plant(plant_id="p1", furnace_groups=[fg1])
    plant2 = _make_plant(plant_id="p2", furnace_groups=[fg2])
    pg = _make_pg([plant1, plant2])
    env = _make_env()

    _seed_opening_balances([pg], env, multiplier=1.0, active_statuses=ACTIVE_STATUSES)

    expected_fg1 = 800.0 * 0.45 * 1_000_000.0 * 0.2
    expected_fg2 = 800.0 * 0.45 * 500_000.0 * 0.2
    assert pg.balance == pytest.approx(expected_fg1 + expected_fg2)


def test_multiple_groups_are_independent():
    """Each group gets its own independent opening balance."""
    fg1 = _make_fg(fg_id="fg-1", capacity=1_000_000.0)
    fg2 = _make_fg(fg_id="fg-2", capacity=500_000.0)
    plant1 = _make_plant(plant_id="p1", furnace_groups=[fg1])
    plant2 = _make_plant(plant_id="p2", furnace_groups=[fg2])
    pg1 = _make_pg([plant1], plant_group_id="gem-A")
    pg2 = _make_pg([plant2], plant_group_id="gem-B")
    env = _make_env()

    _seed_opening_balances([pg1, pg2], env, multiplier=1.0, active_statuses=ACTIVE_STATUSES)

    expected1 = 800.0 * 0.45 * 1_000_000.0 * 0.2
    expected2 = 800.0 * 0.45 * 500_000.0 * 0.2
    assert pg1.balance == pytest.approx(expected1)
    assert pg2.balance == pytest.approx(expected2)


def test_opening_balance_log_fires_once_per_group(caplog):
    """The [OPENING BALANCE] Group log entry fires once per group with a positive opening."""
    import logging as _logging

    fg1 = _make_fg(fg_id="fg-1", capacity=1_000_000.0)
    fg2 = _make_fg(fg_id="fg-2", capacity=500_000.0)
    plant1 = _make_plant(plant_id="p1", furnace_groups=[fg1])
    plant2 = _make_plant(plant_id="p2", furnace_groups=[fg2])
    pg = _make_pg([plant1, plant2], plant_group_id="gem-multi")
    env = _make_env()

    caplog.set_level(_logging.INFO, logger="steelo.simulation._seed_opening_balances")
    _seed_opening_balances([pg], env, multiplier=1.0, active_statuses=ACTIVE_STATUSES)

    group_lines = [r for r in caplog.records if "[OPENING BALANCE] Group" in r.getMessage()]
    assert len(group_lines) == 1
    assert "gem-multi" in group_lines[0].getMessage()


# ---------------------------------------------------------------------------
# SimulationConfig validation tests
# ---------------------------------------------------------------------------


def test_negative_multiplier_raises():
    """Negative opening_balance_multiplier raises ValueError."""
    with pytest.raises(ValueError, match="opening_balance_multiplier must be >= 0.0"):
        SimulationConfig(
            start_year=Year(2025),
            end_year=Year(2060),
            master_excel_path="dummy.xlsx",
            output_dir="/tmp/test_output",
            opening_balance_multiplier=-0.1,
        )
