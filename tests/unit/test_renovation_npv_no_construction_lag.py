"""Tests that the incumbent's renovation NPV carries no construction lag.

Renovation executes instantaneously (lifetime restarts, status stays operating,
production continues), so its NPV must not zero the first construction_time years
the way greenfield challengers do.
"""

from datetime import date

import pytest

from steelo.domain.calculate_costs import (
    ReductantScoreSeries,
    calculate_npv_full,
    calculate_unit_total_opex,
    calculate_variable_opex,
    scale_fopex_to_production,
)
from steelo.domain.models import FurnaceGroup, PointInTime, Technology, Year

BOM = {
    "materials": {"scrap": {"unit_cost": 200.0, "demand": 1.1}},
    "energy": {"electricity": {"unit_cost": 80.0, "demand": 0.5}},
}
CAPEX = 400.0
RENOVATION_SHARE = 0.7
UNIT_FOPEX = 50.0
UTILISATION = 0.8
PRICES = [600.0] * 30


def _flat_score_series(tech, output_shares, start, end):
    n = int(end) - int(start)
    return ReductantScoreSeries(scores=[0.0] * n, picks=[""] * n)


def make_furnace_group() -> FurnaceGroup:
    """Build an operating EAF furnace group with a valid BOM for renovation pricing."""
    furnace_group = FurnaceGroup(
        furnace_group_id="fg_renovation_lag",
        capacity=100_000,
        status="operating",
        last_renovation_date=date(2015, 1, 1),
        technology=Technology(name="EAF", product="steel"),
        historical_production={},
        utilization_rate=UTILISATION,
        lifetime=PointInTime(plant_lifetime=20, current=Year(2025)),
        chosen_reductant="",
        energy_cost_dict={},
    )
    furnace_group.bill_of_materials = BOM
    return furnace_group


def evaluate_incumbent_npv(furnace_group: FurnaceGroup, construction_time: int) -> float:
    """Run optimal_technology_name restricted to the incumbent and return its NPV."""
    npv_dict, _, _, _, _ = furnace_group.optimal_technology_name(
        market_price_series={"steel": PRICES, "iron": [400.0] * 30},
        cost_of_debt_by_tech={"EAF": 0.05},
        cost_of_equity_by_tech={"EAF": 0.1},
        get_bom_from_avg_boms=None,
        score_series_for_tech=_flat_score_series,
        capex_dict={"EAF": CAPEX},
        capex_renovation_share={"EAF": RENOVATION_SHARE},
        technology_fopex_dict={"eaf": UNIT_FOPEX},
        dynamic_business_cases={},
        chosen_emissions_boundary_for_carbon_costs="Scope 1",
        technology_emission_factors=[],
        tech_to_product={"EAF": "steel"},
        plant_lifetime=20,
        construction_time=construction_time,
        current_year=Year(2025),
        risk_free_rate=0.02,
        allowed_furnace_transitions={"EAF": ["EAF"]},
    )
    return npv_dict["EAF"]


def test_incumbent_npv_invariant_to_construction_time():
    """The renovation NPV must be identical whether construction_time is 4 or 0.

    Notes:
        Regression for the phantom construction delay: the incumbent's first
        construction_time years of cash flow were zeroed and everything shifted
        out, understating renovation against challengers and closing marginal
        but viable groups at the boundary.
    """
    npv_lagged = evaluate_incumbent_npv(make_furnace_group(), construction_time=4)
    npv_instant = evaluate_incumbent_npv(make_furnace_group(), construction_time=0)

    assert npv_lagged == pytest.approx(npv_instant)


def test_incumbent_npv_matches_direct_lag_free_calculation():
    """The incumbent's NPV equals calculate_npv_full at renovation-basis capex with
    construction_time=0 (production from year one)."""
    furnace_group = make_furnace_group()
    npv = evaluate_incumbent_npv(furnace_group, construction_time=4)

    unit_opex = calculate_unit_total_opex(
        unit_fopex=scale_fopex_to_production(UNIT_FOPEX, UTILISATION),
        unit_vopex=calculate_variable_opex(BOM["materials"], {}),
        utilization_rate=UTILISATION,
    )
    expected = calculate_npv_full(
        capex=CAPEX * RENOVATION_SHARE,
        capacity=furnace_group.capacity,
        unit_total_opex_list=[unit_opex] * 20,
        expected_utilisation_rate=UTILISATION,
        price_series=PRICES,
        lifetime=20,
        construction_time=0,
        cost_of_debt=0.05,
        cost_of_equity=0.1,
        equity_share=furnace_group.equity_share,
    )

    assert npv == pytest.approx(expected)
