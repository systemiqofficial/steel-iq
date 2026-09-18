"""Tests for FurnaceGroup.historical_utilization (per-year utilisation record)."""

from datetime import date

from steelo.domain import Year
from steelo.domain.models import FurnaceGroup, PointInTime, Technology, TimeFrame


def make_furnace_group() -> FurnaceGroup:
    """Build a minimal operating furnace group for utilisation-history tests."""
    return FurnaceGroup(
        furnace_group_id="fg-1",
        capacity=1.0,
        status="operating",
        last_renovation_date=date(2015, 5, 4),
        technology=Technology(name="BF", energy_consumption=1.0, bill_of_materials={}, product="steel"),
        historical_production={Year(2023): 1.0},
        utilization_rate=0.7,
        lifetime=PointInTime(
            current=Year(2026),
            time_frame=TimeFrame(start=Year(2025), end=Year(2050)),
            plant_lifetime=20,
        ),
    )


def test_historical_utilization_accumulates_one_entry_per_year():
    """record_utilization stores the current rate under each simulation year."""
    fg = make_furnace_group()
    assert fg.historical_utilization is None

    fg.utilization_rate = 0.7
    fg.record_utilization(2026)
    fg.utilization_rate = 0.4
    fg.record_utilization(2027)

    assert fg.historical_utilization == {2026: 0.7, 2027: 0.4}


def test_record_utilization_overwrites_replayed_year():
    """Re-recording a year replaces its entry instead of duplicating it."""
    fg = make_furnace_group()
    fg.utilization_rate = 0.2
    fg.record_utilization(2026)
    fg.utilization_rate = 0.9
    fg.record_utilization(2026)

    assert fg.historical_utilization == {2026: 0.9}
