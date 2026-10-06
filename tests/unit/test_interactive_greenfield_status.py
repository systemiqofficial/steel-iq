"""Tests for the greenfield status viewer's row packing (steelo.utilities.interactive.greenfield_status)."""

import pandas as pd
import pytest

from steelo.utilities.interactive import greenfield_status


def sample_status_timeseries() -> pd.DataFrame:
    """A small status timeseries: one group reaching operating, one discarded and persisting."""
    columns = ["year", "furnace_group_id", "product", "technology", "status", "geo_key", "capacity", "production"]
    rows = [
        [2025, "P1_0", "steel", "EAF", "considered", "CHN:CN-NM", 2_500_000.0, 0.0],
        [2026, "P1_0", "steel", "EAF", "construction", "CHN:CN-NM", 2_500_000.0, 0.0],
        [2027, "P1_0", "steel", "EAF", "operating", "CHN:CN-NM", 2_500_000.0, 2_000_000.0],
        [2025, "P2_0", "iron", "DRI", "considered", "SAU", 2_500_000.0, 0.0],
        [2026, "P2_0", "iron", "DRI", "discarded", "SAU", 2_500_000.0, 0.0],
        [2027, "P2_0", "iron", "DRI", "discarded", "SAU", 2_500_000.0, 0.0],
        [2027, "P3_0", "steel", "EAF", "considered", "CHN:CN-NM", 1_500_000.0, 0.0],
    ]
    return pd.DataFrame(rows, columns=columns)


def test_aggregate_stock_counts_groups_in_status_per_year() -> None:
    """A persisting status keeps counting as stock; capacity and production come out in Mt."""
    aggregated = greenfield_status.aggregate_status(sample_status_timeseries())

    operating = aggregated[(aggregated["year"] == 2027) & (aggregated["status"] == "operating")].iloc[0]
    assert operating["n"] == 1
    assert operating["capacity_mt"] == pytest.approx(2.5)
    assert operating["production_mt"] == pytest.approx(2.0)
    discarded_2027 = aggregated[(aggregated["year"] == 2027) & (aggregated["status"] == "discarded")].iloc[0]
    assert discarded_2027["n"] == 1
    assert list(aggregated.columns) == [
        "year",
        "geo",
        "technology",
        "product",
        "status",
        "n",
        "capacity_mt",
        "production_mt",
        "n_new",
        "capacity_new_mt",
    ]


def test_aggregate_flow_counts_status_entries_only() -> None:
    """A group enters a status in its first snapshot year and on each change, never while persisting."""
    aggregated = greenfield_status.aggregate_status(sample_status_timeseries())

    def flow(year: int, status: str) -> tuple[int, float]:
        rows = aggregated[(aggregated["year"] == year) & (aggregated["status"] == status)]
        return int(rows["n_new"].sum()), float(rows["capacity_new_mt"].sum())

    assert flow(2025, "considered") == (2, pytest.approx(5.0))  # both groups' first snapshots
    assert flow(2026, "discarded") == (1, pytest.approx(2.5))  # P2 enters discarded
    assert flow(2027, "discarded") == (0, pytest.approx(0.0))  # persisting, not re-entering
    assert flow(2027, "operating") == (1, pytest.approx(2.5))  # P1 enters operating


def test_aggregate_rejects_table_without_status() -> None:
    """A table missing a required column fails loudly rather than producing an empty chart."""
    with pytest.raises(ValueError, match="status"):
        greenfield_status.aggregate_status(sample_status_timeseries().drop(columns=["status"]))


def test_pack_rows_compacts_aggregates() -> None:
    """Rows carry short keys, tonnage in Mt to four decimals, and the flow quantities."""
    packed = greenfield_status.pack_rows(greenfield_status.aggregate_status(sample_status_timeseries()))

    operating = next(row for row in packed if row["s"] == "operating")
    assert operating == {
        "y": 2027,
        "g": "CHN:CN-NM",
        "t": "EAF",
        "p": "steel",
        "s": "operating",
        "n": 1,
        "cap": 2.5,
        "pr": 2.0,
        "nn": 1,
        "ncap": 2.5,
    }
