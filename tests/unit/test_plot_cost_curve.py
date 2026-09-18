import pandas as pd
import pytest

from steelo.utilities.plotting import _prepare_cost_curve_dataframe, _compute_market_clearing


def test_duplicate_feedstocks_do_not_double_count_capacity():
    """Rows that duplicate capacity across feedstocks should not inflate totals."""
    raw = pd.DataFrame(
        [
            {
                "furnace_group_id": "FG1",
                "year": 2060,
                "product": "steel",
                "feedstock": "scrap",
                "unit_production_cost": 150.0,
                "capacity": 75.0,
                "region": "Europe",
            },
            {
                "furnace_group_id": "FG1",
                "year": 2060,
                "product": "steel",
                "feedstock": "hot_metal",
                "unit_production_cost": 150.0,
                "capacity": 75.0,
                "region": "Europe",
            },
            {
                "furnace_group_id": "FG1",
                "year": 2060,
                "product": "steel",
                "feedstock": "hbi_low",
                "unit_production_cost": 150.0,
                "capacity": 75.0,
                "region": "Europe",
            },
            {
                "furnace_group_id": "FG2",
                "year": 2060,
                "product": "steel",
                "feedstock": "scrap",
                "unit_production_cost": 320.0,
                "capacity": 50.0,
                "region": "Americas",
            },
        ]
    )

    cost_df = _prepare_cost_curve_dataframe(
        data_frame=raw,
        product_type="steel",
        year=2060,
        aggregation="region",
        capacity_limit=1.0,
    )

    assert list(cost_df["production_cost"]) == [150.0, 320.0]
    # Feedstock slices are collapsed to one furnace entry (75 + 50).
    assert pytest.approx(cost_df["capacity"].sum()) == pytest.approx(125.0)

    # Demand of 70 should clear within the first furnace block. Legacy mode (share=1.0, no buffer)
    # so the assertion stays unchanged from the pre-truncation behaviour.
    clearing_cost, demand_line_x, total_capacity = _compute_market_clearing(
        cost_df, demand=70.0, clearing_share=1.0, price_buffer=0.0
    )
    assert pytest.approx(total_capacity) == 125.0
    assert pytest.approx(demand_line_x) == 70.0
    assert pytest.approx(clearing_cost) == 150.0


def test_feedstock_slices_with_partial_capacity_are_preserved():
    """If feedstocks split capacity unevenly we still keep the total."""
    raw = pd.DataFrame(
        [
            {
                "furnace_group_id": "FG1",
                "year": 2060,
                "product": "steel",
                "feedstock": "scrap",
                "unit_production_cost": 150.0,
                "capacity": 40.0,
                "region": "Europe",
            },
            {
                "furnace_group_id": "FG1",
                "year": 2060,
                "product": "steel",
                "feedstock": "hot_metal",
                "unit_production_cost": 160.0,
                "capacity": 35.0,
                "region": "Europe",
            },
        ]
    )

    cost_df = _prepare_cost_curve_dataframe(
        data_frame=raw,
        product_type="steel",
        year=2060,
        aggregation="region",
        capacity_limit=1.0,
    )

    # Distinct capacity slices should be combined into a single 75 unit block with weighted cost.
    assert len(cost_df) == 1
    assert pytest.approx(cost_df["capacity"].iloc[0]) == pytest.approx(75.0)
    expected_cost = (150.0 * 40.0 + 160.0 * 35.0) / 75.0
    assert pytest.approx(cost_df["production_cost"].iloc[0]) == pytest.approx(expected_cost)


@pytest.fixture
def three_furnace_outlier_curve():
    """Three furnaces, total=241, with a tiny 18420/t outlier on top.

    Reused by the share=0.95 truncation test and the share=1.0 legacy reproduction test.
    """
    raw = pd.DataFrame(
        [
            {
                "furnace_group_id": "FG1",
                "year": 2060,
                "product": "steel",
                "unit_production_cost": 200.0,
                "capacity": 120.0,
                "region": "Europe",
            },
            {
                "furnace_group_id": "FG2",
                "year": 2060,
                "product": "steel",
                "unit_production_cost": 410.0,
                "capacity": 120.0,
                "region": "Europe",
            },
            {
                "furnace_group_id": "FG3",
                "year": 2060,
                "product": "steel",
                "unit_production_cost": 18420.0,
                "capacity": 1.0,  # Tiny outlier slice
                "region": "Europe",
            },
        ]
    )
    return _prepare_cost_curve_dataframe(
        data_frame=raw,
        product_type="steel",
        year=2060,
        aggregation="region",
        capacity_limit=1.0,
    )


def test_clearing_share_drops_boundary_and_above_at_default_share(three_furnace_outlier_curve):
    """At default share=0.95 on this 3-furnace fixture, both the boundary furnace AND the outlier are dropped."""
    # total=241, threshold=0.95 * 241 = 228.95.
    # FG1 cum=120 ≤ 228.95 ⇒ included.
    # FG2 cum=240 > 228.95 ⇒ first to strictly exceed ⇒ boundary, EXCLUDED (Option A).
    # FG3 cum=241 > 228.95 ⇒ also excluded (the actual outlier).
    # Truncated slice = [FG1] only. last_truncated.production_cost = 200.
    # demand=260 > 120 ⇒ shortage band ⇒ price = 200 + 200 = 400.
    # This documents the deliberate aggressiveness of strict-inequality boundary exclusion on a
    # degenerate three-furnace fixture; on a real curve only the actual long-tail entries are dropped.
    clearing_cost, demand_line_x, total_capacity = _compute_market_clearing(
        three_furnace_outlier_curve, demand=260.0, clearing_share=0.95, price_buffer=200.0
    )
    assert pytest.approx(total_capacity) == 241.0
    assert pytest.approx(demand_line_x) == 241.0
    assert pytest.approx(clearing_cost) == pytest.approx(200.0 + 200.0)


def test_legacy_share_one_keeps_full_curve(three_furnace_outlier_curve):
    """At share=1.0 the truncation is a no-op, the full curve is kept including the outlier."""
    # No truncation. demand=260 > total=241 ⇒ shortage band ⇒ last entry (18420) + buffer (200).
    # The previous test asserted 410.0 here, which was a plot-only band-aid from the old outlier
    # heuristic. The spec deliberately removes that, so the displayed price now matches what the
    # engine returns at share=1.0 (legacy mode).
    clearing_cost, demand_line_x, total_capacity = _compute_market_clearing(
        three_furnace_outlier_curve, demand=260.0, clearing_share=1.0, price_buffer=200.0
    )
    assert pytest.approx(total_capacity) == 241.0
    assert pytest.approx(demand_line_x) == 241.0
    assert pytest.approx(clearing_cost) == pytest.approx(18420.0 + 200.0)


def test_market_clearing_uses_last_price_when_supply_insufficient():
    raw = pd.DataFrame(
        [
            {
                "furnace_group_id": "FG1",
                "year": 2060,
                "product": "steel",
                "unit_production_cost": 100.0,
                "capacity": 40.0,
                "region": "Europe",
            },
            {
                "furnace_group_id": "FG2",
                "year": 2060,
                "product": "steel",
                "unit_production_cost": 220.0,
                "capacity": 30.0,
                "region": "Americas",
            },
        ]
    )

    cost_df = _prepare_cost_curve_dataframe(
        data_frame=raw,
        product_type="steel",
        year=2060,
        aggregation="region",
        capacity_limit=1.0,
    )

    # Legacy mode (share=1.0, no truncation). demand=120 > total=70 ⇒ shortage branch fires.
    # Updated assertion: the helper now adds price_buffer in the shortage branch (closes the
    # pre-existing gap where the plot omitted the buffer that the engine has always applied).
    clearing_cost, demand_line_x, total_capacity = _compute_market_clearing(
        cost_df, demand=120.0, clearing_share=1.0, price_buffer=200.0
    )

    assert pytest.approx(total_capacity) == 70.0
    assert pytest.approx(demand_line_x) == 70.0
    assert pytest.approx(clearing_cost) == 220.0 + 200.0
