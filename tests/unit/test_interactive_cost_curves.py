"""Tests for the cost-curve viewer's packing and clearing helpers (steelo.utilities.interactive.cost_curves)."""

import pandas as pd
import pytest

from steelo.utilities.interactive import cost_curves

CLEARING = cost_curves.clearing_config(
    capacity_limit=0.95, steel_share=0.95, steel_buffer=200.0, iron_share=0.95, iron_buffer=200.0
)


def sample_post_processed() -> pd.DataFrame:
    """A small post-processed table: one furnace group repeated over two feedstock rows, one without a cost."""
    columns = ["year", "iso3", "geo_key", "furnace_group_id", "technology", "product", "capacity", "production"]
    columns += ["unit_production_cost"]
    rows = [
        # Same furnace group and year, two feedstock rows → one bar
        [2025, "CHN", "CHN:CN-HE", "P1_0", "BF", "iron", 3_000_000.0, 2_000_000.0, 300.123],
        [2025, "CHN", "CHN:CN-HE", "P1_0", "BF", "iron", 3_000_000.0, 2_000_000.0, 300.123],
        [2025, "DEU", "DEU", "P2_0", "EAF", "steel", 1_200_000.0, 1_000_000.0, 500.0],
        [2025, "DEU", "DEU", "P3_0", "BOF", "steel", 2_000_000.0, 1_500_000.0, 400.0],
        [2025, "IND", "IND", "P4_0", "EAF", "steel", 1_000_000.0, 100_000.0, None],
        [2026, "DEU", "DEU", "P2_0", "EAF", "steel", 1_200_000.0, 1_100_000.0, 520.0],
    ]
    return pd.DataFrame(rows, columns=columns)


def test_furnace_group_rows_counts_each_furnace_group_year_once() -> None:
    """Feedstock rows collapse to one; quantities are in Mt and a missing cost packs as 0 (off the curve)."""
    fgs = cost_curves.furnace_group_rows(sample_post_processed())

    assert len(fgs) == 5
    chn = fgs[fgs["furnace_group_id"] == "P1_0"].iloc[0]
    assert chn["geo"] == "CHN:CN-HE"
    assert chn["capacity_mt"] == pytest.approx(3.0)
    assert chn["production_mt"] == pytest.approx(2.0)
    assert chn["cost"] == pytest.approx(300.12)
    assert fgs[fgs["furnace_group_id"] == "P4_0"].iloc[0]["cost"] == 0.0


def test_furnace_group_rows_rejects_table_without_cost() -> None:
    """A table missing a required column fails loudly rather than producing an empty chart."""
    with pytest.raises(ValueError, match="unit_production_cost"):
        cost_curves.furnace_group_rows(sample_post_processed().drop(columns=["unit_production_cost"]))


def test_pack_rows_compacts_furnace_groups() -> None:
    """Rows carry short keys with capacity and production in Mt and the cost in $/t."""
    packed = cost_curves.pack_rows(cost_curves.furnace_group_rows(sample_post_processed()))

    deu_2026 = next(row for row in packed if row["fg"] == "P2_0" and row["y"] == 2026)
    assert deu_2026 == {
        "y": 2026,
        "g": "DEU",
        "t": "EAF",
        "p": "steel",
        "fg": "P2_0",
        "cap": 1.2,
        "pr": 1.1,
        "c": 520.0,
    }


def test_market_clearing_on_a_hand_built_curve() -> None:
    """Unsorted furnace groups sort by cost; furnace groups without a positive cost and capacity stay off the curve."""
    costs = [300.0, 100.0, 0.0, 200.0, 150.0]
    capacities = [10.0, 10.0, 5.0, 10.0, 0.0]

    # Demand of 15 lands inside the second-cheapest furnace group (cumulative 20 ≥ 15).
    assert cost_curves.market_clearing(costs, capacities, 15.0, 0.9, 50.0) == (200.0, 15.0, 30.0)
    # Demand of 28 exceeds the dispatchable 27: last dispatchable cost (200) plus the premium.
    assert cost_curves.market_clearing(costs, capacities, 28.0, 0.9, 50.0) == (250.0, 28.0, 30.0)
    # Demand beyond total supply: the demand marker is clipped to the curve.
    assert cost_curves.market_clearing(costs, capacities, 40.0, 0.9, 50.0) == (250.0, 30.0, 30.0)
    assert cost_curves.market_clearing([], [], 10.0, 0.9, 50.0) == (0.0, 0.0, 0.0)


def test_steel_demand_by_year_reads_the_demand_column(tmp_path) -> None:
    """The steel_demand_t column gives demand per year; a file without it, or no file, gives None."""
    prices = tmp_path / "market_prices_2025_2026.csv"
    pd.DataFrame(
        {"year": [2025, 2026], "steel_price_usd_per_t": [600.0, 550.0], "steel_demand_t": [2.5e6, 2.6e6]}
    ).to_csv(prices, index=False)
    assert cost_curves.steel_demand_by_year(prices) == {2025: 2.5e6, 2026: 2.6e6}

    pd.DataFrame({"year": [2025], "steel_price_usd_per_t": [600.0]}).to_csv(prices, index=False)
    assert cost_curves.steel_demand_by_year(prices) is None
    assert cost_curves.steel_demand_by_year(tmp_path / "absent.csv") is None


def test_clearing_table_uses_engine_steel_demand_and_realised_iron_production() -> None:
    """Steel clears against the recorded demand, iron against its production; both fall back to production."""
    fgs = cost_curves.furnace_group_rows(sample_post_processed())

    table = cost_curves.clearing_table(fgs, {2025: 2.5e6, 2026: 0.0}, CLEARING)

    # Steel 2025: curve P3_0 (400, 1.9 Mt) then P2_0 (500, 1.14 Mt); demand 2.5 Mt is within the
    # dispatchable 95% of 3.04 Mt and lands in P2_0.
    assert table["steel"][2025] == {"d": 2.5, "c": 500.0}
    # Steel 2026: no positive demand recorded → production (1.1 Mt) against 1.14 Mt dispatchable.
    assert table["steel"][2026] == {"d": 1.1, "c": 520.0}
    # Iron 2025: production 2.0 Mt against 2.85 Mt dispatchable.
    assert table["iron"][2025] == {"d": 2.0, "c": 300.12}

    without_demand = cost_curves.clearing_table(fgs, None, CLEARING)
    assert without_demand["steel"][2025] == {"d": 2.6, "c": 500.0}
