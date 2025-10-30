import pandas as pd
import pytest

from steelo.utilities.plotting import _prepare_cost_curve_dataframe, _compute_market_clearing


def test_duplicate_feedstocks_do_not_double_count_capacity():
    """Rows that duplicate capacity across feedstocks should not inflate totals."""
    raw = pd.DataFrame(
        [
            {
                "furnace_group_id": "FG1",
                "year": 2050,
                "product": "steel",
                "feedstock": "scrap",
                "unit_production_cost": 150.0,
                "capacity": 75.0,
                "region": "Europe",
            },
            {
                "furnace_group_id": "FG1",
                "year": 2050,
                "product": "steel",
                "feedstock": "hot_metal",
                "unit_production_cost": 150.0,
                "capacity": 75.0,
                "region": "Europe",
            },
            {
                "furnace_group_id": "FG1",
                "year": 2050,
                "product": "steel",
                "feedstock": "hbi_low",
                "unit_production_cost": 150.0,
                "capacity": 75.0,
                "region": "Europe",
            },
            {
                "furnace_group_id": "FG2",
                "year": 2050,
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
        year=2050,
        aggregation="region",
        capacity_limit=1.0,
    )

    assert list(cost_df["production_cost"]) == [150.0, 320.0]
    # Feedstock slices are collapsed to one furnace entry (75 + 50).
    assert pytest.approx(cost_df["capacity"].sum()) == pytest.approx(125.0)

    # Demand of 70 should clear within the first furnace block.
    clearing_cost, demand_line_x, total_capacity = _compute_market_clearing(cost_df, demand=70.0)
    assert pytest.approx(total_capacity) == 125.0
    assert pytest.approx(demand_line_x) == 70.0
    assert pytest.approx(clearing_cost) == 150.0


def test_feedstock_slices_with_partial_capacity_are_preserved():
    """If feedstocks split capacity unevenly we still keep the total."""
    raw = pd.DataFrame(
        [
            {
                "furnace_group_id": "FG1",
                "year": 2050,
                "product": "steel",
                "feedstock": "scrap",
                "unit_production_cost": 150.0,
                "capacity": 40.0,
                "region": "Europe",
            },
            {
                "furnace_group_id": "FG1",
                "year": 2050,
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
        year=2050,
        aggregation="region",
        capacity_limit=1.0,
    )

    # Distinct capacity slices should be combined into a single 75 unit block with weighted cost.
    assert len(cost_df) == 1
    assert pytest.approx(cost_df["capacity"].iloc[0]) == pytest.approx(75.0)
    expected_cost = (150.0 * 40.0 + 160.0 * 35.0) / 75.0
    assert pytest.approx(cost_df["production_cost"].iloc[0]) == pytest.approx(expected_cost)


def test_market_clearing_skips_outlier_when_supply_shortfall():
    """When the last slice is an obvious outlier, use the previous block for price."""
    raw = pd.DataFrame(
        [
            {
                "furnace_group_id": "FG1",
                "year": 2050,
                "product": "steel",
                "unit_production_cost": 200.0,
                "capacity": 120.0,
                "region": "Europe",
            },
            {
                "furnace_group_id": "FG2",
                "year": 2050,
                "product": "steel",
                "unit_production_cost": 410.0,
                "capacity": 120.0,
                "region": "Europe",
            },
            {
                "furnace_group_id": "FG3",
                "year": 2050,
                "product": "steel",
                "unit_production_cost": 18420.0,
                "capacity": 1.0,  # Tiny outlier slice
                "region": "Europe",
            },
        ]
    )

    cost_df = _prepare_cost_curve_dataframe(
        data_frame=raw,
        product_type="steel",
        year=2050,
        aggregation="region",
        capacity_limit=1.0,
    )

    clearing_cost, demand_line_x, total_capacity = _compute_market_clearing(cost_df, demand=260.0)

    assert pytest.approx(total_capacity) == 241.0
    assert pytest.approx(demand_line_x) == 241.0
    # Should use the last non-outlier cost (410) rather than the 18k outlier.
    assert pytest.approx(clearing_cost) == pytest.approx(410.0)


def test_market_clearing_uses_last_price_when_supply_insufficient():
    raw = pd.DataFrame(
        [
            {
                "furnace_group_id": "FG1",
                "year": 2050,
                "product": "steel",
                "unit_production_cost": 100.0,
                "capacity": 40.0,
                "region": "Europe",
            },
            {
                "furnace_group_id": "FG2",
                "year": 2050,
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
        year=2050,
        aggregation="region",
        capacity_limit=1.0,
    )

    clearing_cost, demand_line_x, total_capacity = _compute_market_clearing(cost_df, demand=120.0)

    assert pytest.approx(total_capacity) == 70.0
    assert pytest.approx(demand_line_x) == 70.0
    assert pytest.approx(clearing_cost) == 220.0
