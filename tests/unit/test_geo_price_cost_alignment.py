"""Regression tests for GEO price/cost year alignment (audit A3).

The opportunity NPV pairs each cost year with the market price of the same calendar
year: the current-year-anchored price series is re-anchored to the opportunity's
earliest construction start before ``calculate_npv_full``, whose construction-time
padding then makes price index i and cost index i the same calendar year. Pre-fix,
prices were read from the current year while costs came from the target window,
up to ``consideration_time + 1`` years later.
"""

import pytest

from steelo.devdata import PointInTime, TimeFrame, get_furnace_group
from steelo.domain.calculate_costs import (
    ReductantScoreSeries,
    calculate_business_opportunity_npvs,
    calculate_npv_full,
    calculate_unit_total_opex,
    calculate_variable_opex,
    scale_fopex_to_production,
)
from steelo.domain.models import Location, PlantGroup
from steelo.domain.constants import Year

SITE_ID = (40.0, -100.0, "USA")


def _flat_score_series(location, tech, output_shares, start, end, **kwargs):
    """Flat 5.0 score over the requested operating window, recording the window."""
    n = int(end) - int(start)
    _score_windows.append((Year(start), Year(end)))
    return ReductantScoreSeries(scores=[5.0] * n, picks=["scrap"] * n)


_score_windows: list[tuple[Year, Year]] = []


def _make_cost_data(plant_lifetime: int) -> dict:
    """Minimal complete cost_data for one steel site with one technology."""
    return {
        "steel": {
            SITE_ID: {
                "EAF": {
                    "railway_cost": 10.0,
                    "energy_costs": {"electricity": 0.05, "hydrogen": 3500.0},
                    "output_costs": {"electricity": 0.05, "hydrogen": 3500.0},
                    "no_subsidy_prices": {"electricity": 0.05, "hydrogen": 3500.0},
                    "capex": 1000.0,
                    "capex_no_subsidy": 1000.0,
                    "fopex": 50.0,
                    "utilization_rate": 0.7,
                    "reductant": "scrap",
                    "bom": {"materials": {}},
                    "output_shares": {"scrap": 1.0},
                    "all_opex_subsidies": [],
                    "score_series": [5.0] * plant_lifetime,
                    "cost_of_debt": 0.05,
                    "cost_of_debt_no_subsidy": 0.05,
                    "cost_of_equity": 0.08,
                }
            }
        }
    }


def _expected_npv(price_series: list[float], plant_lifetime: int, construction_time: int) -> float:
    """The NPV the GEO path must produce for _make_cost_data with the given prices."""
    unit_vopex = calculate_variable_opex({}, {})
    unit_fopex = scale_fopex_to_production(50.0, 0.7)
    opex = [unit_vopex + unit_fopex + 5.0] * plant_lifetime
    return calculate_npv_full(
        capex=1000.0,
        capacity=1000.0,
        unit_total_opex_list=opex,
        expected_utilisation_rate=0.7,
        price_series=price_series,
        lifetime=plant_lifetime,
        construction_time=construction_time,
        cost_of_debt=0.05,
        cost_of_equity=0.08,
        equity_share=0.3,
        infrastructure_costs=10.0,
    )


def _run_identify(monkeypatch, market_price_steel: list[float]) -> dict:
    """Run identify_new_business_opportunities_4indi and capture the npv_dict it computes."""
    consideration_time, construction_time, plant_lifetime = 3, 2, 4
    cost_data = _make_cost_data(plant_lifetime)
    captured: dict = {}

    monkeypatch.setattr(
        "steelo.domain.new_plant_opening.prepare_cost_data_for_business_opportunity",
        lambda *args, **kwargs: cost_data,
    )

    def _capture_select_top(npv_dict, **kwargs):
        captured["npv_dict"] = npv_dict
        return {product: {} for product in npv_dict}

    monkeypatch.setattr(
        "steelo.domain.new_plant_opening.select_top_opportunities_by_npv",
        _capture_select_top,
    )

    plant_group = PlantGroup(plant_group_id="indi", plants=[])
    plant_group.identify_new_business_opportunities_4indi(
        current_year=Year(2025),
        consideration_time=consideration_time,
        construction_time=construction_time,
        plant_lifetime=plant_lifetime,
        input_costs={"USA": {Year(2025): {"electricity": 0.05, "hydrogen": 3500.0}}},
        locations={"steel": [{"Latitude": 40.0, "Longitude": -100.0, "iso3": "USA"}]},
        iso3_to_region_map={"USA": "Americas"},
        market_price={"steel": market_price_steel},
        capex_dict_all_locs_techs={"Americas": {"EAF": 1000.0}},
        cost_of_debt_all_locs={"USA": {"EAF": 0.05}},
        cost_of_equity_all_locs={"USA": {"EAF": 0.08}},
        steel_plant_capacity=1000.0,
        all_plant_ids=[],
        fopex_all_locs_techs={"USA": {"eaf": 50.0}},
        equity_share=0.3,
        dynamic_feedstocks={},
        get_bom_from_avg_boms=lambda *args, **kwargs: (None, 0.7, "scrap", {}),
        reductant_score_series=_flat_score_series,
        global_risk_free_rate=0.03,
        tech_to_product={"EAF": "steel"},
        allowed_techs={Year(2025): ["EAF"], Year(2029): ["EAF"]},
        technology_emission_factors=[],
        chosen_emissions_boundary_for_carbon_costs="scope_1",
        active_statuses=["operating"],
        top_n_loctechs_as_business_op=1,
        probabilistic_agents=False,
    )
    return captured["npv_dict"]


def test_creation_npv_prices_anchored_at_target_year(monkeypatch):
    """The creation NPV prices the operating window, not the current year.

    With consideration_time=3 the target year is current + 4, so the first four
    price entries belong to years before construction can even start. They must
    not influence the NPV: the result equals a direct calculate_npv_full call on
    the target-anchored slice, and is invariant to the pre-target entries.
    """
    plant_lifetime, construction_time = 4, 2
    operating_window_prices = [100.0] * (construction_time + plant_lifetime)

    npv_a = _run_identify(monkeypatch, [999.0] * 4 + operating_window_prices)["steel"][SITE_ID]["EAF"]
    npv_b = _run_identify(monkeypatch, [111.0] * 4 + operating_window_prices)["steel"][SITE_ID]["EAF"]

    expected = _expected_npv(operating_window_prices, plant_lifetime, construction_time)
    assert npv_a == pytest.approx(expected, rel=1e-12)
    assert npv_b == pytest.approx(expected, rel=1e-12)


def _make_opportunity_fg(plant_lifetime: int):
    """A considered opportunity FG with everything the real NPV path reads."""
    fg = get_furnace_group(
        fg_id="fg_a3",
        tech_name="EAF",
        capacity=100,
        lifetime=PointInTime(
            current=Year(2030),
            time_frame=TimeFrame(start=Year(2035), end=Year(2060)),
            plant_lifetime=plant_lifetime,
        ),
        utilization_rate=0.7,
    )
    fg.status = "considered"
    fg.cost_of_debt = 0.05
    fg.technology.capex = 1000.0
    fg.tech_unit_fopex = 35.0
    fg.equity_share = 0.3
    fg.railway_cost = 10.0
    fg.chosen_reductant = "scrap"
    fg.output_shares = {"scrap": 1.0}
    fg.energy_costs_no_subsidy = {"electricity": 0.05, "hydrogen": 3500.0}
    return fg


def _expected_refresh_npv(fg, price_series: list[float], plant_lifetime: int, construction_time: int) -> float:
    unit_vopex = calculate_variable_opex(fg.bill_of_materials["materials"], {})
    unit_fopex = scale_fopex_to_production(fg.tech_unit_fopex, fg.utilization_rate)
    opex = [unit_vopex + unit_fopex + 5.0] * plant_lifetime
    return calculate_npv_full(
        capex=fg.technology.capex,
        capacity=fg.capacity,
        unit_total_opex_list=opex,
        expected_utilisation_rate=fg.utilization_rate,
        price_series=price_series,
        lifetime=plant_lifetime,
        construction_time=construction_time,
        cost_of_debt=fg.cost_of_debt,
        cost_of_equity=0.08,
        equity_share=fg.equity_share,
        infrastructure_costs=fg.railway_cost,
    )


def test_refresh_npv_prices_anchored_at_opportunity_window():
    """The yearly re-valuation prices the opportunity's own cost window.

    One year after creation (k=1, consideration_time=3) the window anchors at
    year + 3, so the first three price entries are pre-window and must not
    influence the NPV.
    """
    plant_lifetime, construction_time, consideration_time = 4, 2, 3
    fg = _make_opportunity_fg(plant_lifetime)
    fg.historical_npv_business_opportunities = {Year(2030): 50.0}
    window_prices = [100.0] * 7
    market_price = {"steel": [999.0] * 3 + window_prices}

    _score_windows.clear()
    fg.track_business_opportunities(
        year=Year(2031),
        location=Location(lat=40.0, lon=-100.0, country="USA", region="Americas", iso3="USA"),
        market_price=market_price,
        cost_of_equity=0.08,
        plant_lifetime=plant_lifetime,
        construction_time=construction_time,
        consideration_time=consideration_time,
        probability_of_announcement=1.0,
        all_opex_subsidies=[],
        reductant_score_series=_flat_score_series,
    )

    expected = _expected_refresh_npv(fg, window_prices, plant_lifetime, construction_time)
    assert fg.historical_npv_business_opportunities[Year(2031)] == pytest.approx(expected, rel=1e-12)
    # Cost window anchored at year + consideration_time + 1 - k + construction_time = 2036
    assert _score_windows == [(Year(2036), Year(2040))]


def test_refresh_straggler_window_floors_at_announcement_plus_construction():
    """An opportunity older than the consideration window prices the soonest physical path.

    With 6 considered years (k=6 > consideration_time + 1) the raw formula would
    anchor the window in the past. It must pin at announce-this-year + construction:
    operations from year + 1 + construction_time, price offset 1.
    """
    plant_lifetime, construction_time, consideration_time = 4, 2, 3
    fg = _make_opportunity_fg(plant_lifetime)
    # Mixed signs so no announce/discard decision (and no RNG draw) fires
    fg.historical_npv_business_opportunities = {Year(2025 + i): (100.0 if i % 2 == 0 else -100.0) for i in range(6)}
    window_prices = [100.0] * 9
    market_price = {"steel": [999.0] + window_prices}

    _score_windows.clear()
    fg.track_business_opportunities(
        year=Year(2031),
        location=Location(lat=40.0, lon=-100.0, country="USA", region="Americas", iso3="USA"),
        market_price=market_price,
        cost_of_equity=0.08,
        plant_lifetime=plant_lifetime,
        construction_time=construction_time,
        consideration_time=consideration_time,
        probability_of_announcement=1.0,
        all_opex_subsidies=[],
        reductant_score_series=_flat_score_series,
    )

    # Operations can start no earlier than 2031 (announce) + 1 + 2 (construction) = 2034
    assert _score_windows == [(Year(2034), Year(2038))]
    expected = _expected_refresh_npv(fg, window_prices, plant_lifetime, construction_time)
    assert fg.historical_npv_business_opportunities[Year(2031)] == pytest.approx(expected, rel=1e-12)


def test_geo_npv_composition_matches_pam_formula():
    """GEO's opportunity NPV reproduces the PAM opex composition to 1e-9.

    Identical inputs — materials-only VOPEX, production-scaled FOPEX, flat score,
    flat prices — through calculate_business_opportunity_npvs and through the
    PAM-side composition (calculate_unit_total_opex + score -> calculate_npv_full)
    must agree, locking the two valuation paths together.
    """
    plant_lifetime, construction_time = 4, 2
    flat_prices = [100.0] * (construction_time + plant_lifetime)

    npv_dict = calculate_business_opportunity_npvs(
        cost_data=_make_cost_data(plant_lifetime),
        target_year=2029,
        market_price={"steel": flat_prices},
        steel_plant_capacity=1000.0,
        plant_lifetime=plant_lifetime,
        construction_time=construction_time,
        equity_share=0.3,
    )

    unit_vopex = calculate_variable_opex({}, {})
    unit_fopex = scale_fopex_to_production(50.0, 0.7)
    pam_opex = [calculate_unit_total_opex(unit_vopex, unit_fopex, 0.7) + 5.0] * plant_lifetime
    pam_npv = calculate_npv_full(
        capex=1000.0,
        capacity=1000.0,
        unit_total_opex_list=pam_opex,
        expected_utilisation_rate=0.7,
        price_series=flat_prices,
        lifetime=plant_lifetime,
        construction_time=construction_time,
        cost_of_debt=0.05,
        cost_of_equity=0.08,
        equity_share=0.3,
        infrastructure_costs=10.0,
    )

    assert npv_dict["steel"][SITE_ID]["EAF"] == pytest.approx(pam_npv, abs=1e-9)
