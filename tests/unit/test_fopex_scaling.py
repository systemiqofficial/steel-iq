"""Regression tests for fixed-OPEX capacity-to-production scaling in investment NPVs.

Technology fixed OPEX is quoted per tonne of capacity per year, while NPV cash flows
multiply unit costs by expected production (utilisation x capacity). Each investment
path (creation, switch, expansion) must therefore divide fopex by the same expected
utilisation rate it passes to calculate_npv_full, so the full fixed cost is counted.
"""

from datetime import date

import pytest

from steelo.domain import calculate_costs
from steelo.domain.calculate_costs import ReductantScoreSeries, scale_fopex_to_production
from steelo.domain.constants import Volumes
from steelo.domain.models import (
    FurnaceGroup,
    Location,
    Plant,
    PlantGroup,
    PointInTime,
    Technology,
    Year,
)

MATERIALS = {"iron_ore": {"unit_cost": 100.0, "demand": 1.0}}


def _stub_score_series(tech, output_shares, start, end):
    n = int(end) - int(start)
    return ReductantScoreSeries(scores=[0.0] * n, picks=["hydrogen"] * n)


def _make_fg(utilization_rate: float) -> FurnaceGroup:
    """Build a minimal operating BF furnace group with a materials-only BOM."""
    fg = FurnaceGroup(
        furnace_group_id="fg1",
        capacity=100_000,
        status="operating",
        last_renovation_date=date(2020, 1, 1),
        technology=Technology(name="BF", product="iron"),
        historical_production={},
        utilization_rate=utilization_rate,
        lifetime=PointInTime(plant_lifetime=20, current=Year(2025)),
        chosen_reductant="coke",
        energy_cost_dict={},
    )
    fg.bill_of_materials = {
        "materials": dict(MATERIALS),
        "energy": {"coke": {"unit_cost": 300.0, "demand": 0.4}},
    }
    fg.set_energy_costs(coke=300.0, hydrogen=5000.0, electricity=0.05)
    return fg


def _make_plant(fg: FurnaceGroup) -> Plant:
    return Plant(
        plant_id="P000000000001",
        location=Location(lat=0.0, lon=0.0, country="New Zealand", region="oceania", iso3="NZL"),
        furnace_groups=[fg],
        power_source="grid",
        soe_status="private",
        parent_gem_id="G01",
        workforce_size=100,
        certified=False,
        category_steel_product=set(),
        technology_unit_fopex={"bf": 10.0},
    )


def _mock_get_bom(util_rate: float):
    """Mock get_bom_from_avg_boms returning a materials-only BOM at the given utilisation."""

    def mock(energy_costs, tech, _capacity, _reductant=None):
        return (
            {"materials": dict(MATERIALS), "energy": {}},
            util_rate,
            "hydrogen",
            {"iron_ore": 1.0},
        )

    return mock


def _capture_npv_full(monkeypatch) -> list[dict]:
    """Replace calculate_npv_full with a recorder; all call sites resolve it from the module."""
    captured: list[dict] = []

    def fake(**kwargs):
        captured.append(kwargs)
        return 0.0

    monkeypatch.setattr(calculate_costs, "calculate_npv_full", fake)
    return captured


# ── helper ────────────────────────────────────────────────────────────────────


def test_scale_fopex_to_production_spreads_over_utilisation():
    """Per-capacity fopex divided by expected utilisation gives per-production fopex."""
    assert scale_fopex_to_production(80.0, 0.8) == pytest.approx(100.0)
    assert scale_fopex_to_production(0.0, 0.5) == 0.0


def test_scale_fopex_to_production_rejects_non_positive_utilisation():
    """A candidate with no expected production cannot be priced."""
    with pytest.raises(ValueError, match="utilisation"):
        scale_fopex_to_production(80.0, 0.0)
    with pytest.raises(ValueError, match="utilisation"):
        scale_fopex_to_production(80.0, -0.1)


# ── switch path ───────────────────────────────────────────────────────────────


def test_switch_candidate_counts_full_fixed_cost(monkeypatch):
    """optimal_technology_name prices candidate fopex per tonne of production.

    With fopex $50/t-capacity and BOM utilisation 0.9, the opex fed to the NPV must
    carry 50/0.9 so that opex x production recovers fopex x capacity.
    """
    captured = _capture_npv_full(monkeypatch)
    fg = _make_fg(utilization_rate=0.8)

    fg.optimal_technology_name(
        market_price_series={"steel": [500.0] * 30, "iron": [400.0] * 30},
        cost_of_debt_by_tech={"BF": 0.05, "DRI": 0.05},
        cost_of_equity_by_tech={"BF": 0.1, "DRI": 0.1},
        get_bom_from_avg_boms=_mock_get_bom(util_rate=0.9),
        score_series_for_tech=_stub_score_series,
        capex_dict={"DRI": 500.0},
        capex_renovation_share={},
        technology_fopex_dict={"dri": 50.0},
        dynamic_business_cases={},
        chosen_emissions_boundary_for_carbon_costs="Scope 1",
        technology_emission_factors=[],
        tech_to_product={"DRI": "iron"},
        plant_lifetime=20,
        construction_time=2,
        current_year=Year(2025),
        risk_free_rate=0.02,
        allowed_furnace_transitions={"BF": ["DRI"]},
    )

    assert len(captured) == 1
    call = captured[0]
    expected_vopex = calculate_costs.calculate_variable_opex(dict(MATERIALS), {})
    assert call["expected_utilisation_rate"] == pytest.approx(0.9)
    assert call["unit_total_opex_list"][0] == pytest.approx(expected_vopex + 50.0 / 0.9)


# ── expansion path ────────────────────────────────────────────────────────────


def test_expansion_candidates_count_full_fixed_cost(monkeypatch):
    """evaluate_expansion_options scales fopex by the NPV's expected utilisation.

    The NPV uses max(BOM utilisation, plant historical average) for the product, so
    the divisor must be that same maximum: the DRI (iron) candidate takes the plant's
    0.95 historical average over the BOM's 0.9, while the EAF (steel) candidate has
    no historical average and keeps the BOM's 0.9.
    """
    captured = _capture_npv_full(monkeypatch)
    fg = _make_fg(utilization_rate=0.95)
    pg = PlantGroup(plant_group_id="pg1", plants=[_make_plant(fg)])
    pg.balance = 1e12

    pg.evaluate_expansion_options(
        price_series={"steel": [500.0] * 30, "iron": [400.0] * 30},
        capacity=Volumes(1000.0),
        region_capex={"Region": {"DRI": 500.0, "EAF": 400.0}},
        cost_of_debt_dict={"NZL": {"DRI": 0.05, "EAF": 0.05}},
        cost_of_equity_dict={"NZL": {"DRI": 0.1, "EAF": 0.1}},
        get_bom_from_avg_boms=_mock_get_bom(util_rate=0.9),
        reductant_score_series=lambda *a, **k: ReductantScoreSeries(scores=[0.0] * 20, picks=["hydrogen"] * 20),
        dynamic_feedstocks={},
        fopex_for_iso3={"NZL": {"dri": 50.0, "eaf": 40.0}},
        iso3_to_region_map={"NZL": "Region"},
        chosen_emissions_boundary_for_carbon_costs="Scope 1",
        technology_emission_factors=[],
        global_risk_free_rate=0.02,
        equity_share=0.3,
        tech_to_product={"DRI": "iron", "EAF": "steel"},
        plant_lifetime=20,
        construction_time=2,
        current_year=Year(2025),
        allowed_techs={Year(2025): ["DRI", "EAF"]},
        active_statuses=["operating"],
    )

    expected_vopex = calculate_costs.calculate_variable_opex(dict(MATERIALS), {})
    by_capex = {call["capex"]: call for call in captured}
    dri_call = by_capex[500.0]
    assert dri_call["expected_utilisation_rate"] == pytest.approx(0.95)
    assert dri_call["unit_total_opex_list"][0] == pytest.approx(expected_vopex + 50.0 / 0.95)
    eaf_call = by_capex[400.0]
    assert eaf_call["expected_utilisation_rate"] == pytest.approx(0.9)
    assert eaf_call["unit_total_opex_list"][0] == pytest.approx(expected_vopex + 40.0 / 0.9)


# ── creation path ─────────────────────────────────────────────────────────────


def test_new_plant_npv_counts_full_fixed_cost(monkeypatch):
    """calculate_business_opportunity_npvs converts the raw per-capacity fopex."""
    captured = _capture_npv_full(monkeypatch)
    cost_data = {
        "iron": {
            (0.0, 0.0, "SITE1"): {
                "DRI": {
                    "bom": {"materials": dict(MATERIALS), "energy": {}},
                    "fopex": 50.0,
                    "utilization_rate": 0.8,
                    "score_series": [0.0] * 20,
                    "capex": 500.0,
                    "cost_of_debt": 0.05,
                    "cost_of_equity": 0.1,
                    "railway_cost": 0.0,
                    "all_opex_subsidies": [],
                },
            },
        },
    }

    npvs = calculate_costs.calculate_business_opportunity_npvs(
        cost_data=cost_data,
        target_year=2025,
        market_price={"iron": [400.0] * 30},
        steel_plant_capacity=2_500_000.0,
        plant_lifetime=20,
        construction_time=2,
        equity_share=0.3,
    )

    assert len(captured) == 1
    call = captured[0]
    expected_vopex = calculate_costs.calculate_variable_opex(dict(MATERIALS), {})
    assert call["expected_utilisation_rate"] == pytest.approx(0.8)
    assert call["unit_total_opex_list"][0] == pytest.approx(expected_vopex + 50.0 / 0.8)
    assert npvs["iron"][(0.0, 0.0, "SITE1")]["DRI"] == 0.0
