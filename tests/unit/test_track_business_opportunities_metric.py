"""The yearly opportunity re-check values with the creation (B3) metric.

Pins the metric unification: materials-only VOPEX + production-scaled fixed OPEX
+ the year-wise reductant score, valued with the B3-signature NPV (no separate
carbon or by-product terms - both live inside the score).
"""

import pytest
from unittest.mock import patch

from steelo.domain.calculate_costs import (
    ReductantScoreSeries,
    calculate_business_opportunity_npvs,
    calculate_npv_full,
    calculate_opex_list_with_subsidies,
    calculate_variable_opex,
    scale_fopex_to_production,
)
from steelo.domain.commands import UpdateFurnaceGroupStatus
from steelo.domain.models import Location
from steelo.devdata import get_furnace_group, PointInTime, TimeFrame, Year

LOCATION = Location(lat=40.0, lon=-100.0, country="USA", region="Americas", iso3="USA")
MARKET_PRICE = {"steel": [600.0] * 12, "iron": [400.0] * 12}
SITE_PRICES = {"electricity": 0.05, "hydrogen": 3500.0}
BOM = {
    "materials": {
        "scrap": {
            "demand": 110_000.0,
            "total_material_cost": 22_000_000.0,
            "product_volume": 100_000.0,
            "unit_cost": 200.0,
        },
    },
    "energy": {
        "electricity": {
            "total_cost": 8_000_000.0,
            "product_volume": 100_000.0,
            "unit_cost": 0.05,
            "demand": 160_000_000.0,
        },
    },
}


def _make_opportunity_fg(*, utilization_rate: float = 0.7):
    """A considered opportunity furnace group carrying the creation-time fields.

    Args:
        utilization_rate: Expected utilisation stored at creation.

    Returns:
        FurnaceGroup ready for track_business_opportunities.
    """
    fg = get_furnace_group(
        fg_id="fg_metric",
        tech_name="EAF",
        capacity=100_000,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2030), end=Year(2050)),
            plant_lifetime=20,
        ),
        utilization_rate=utilization_rate,
    )
    fg.status = "considered"
    fg.cost_of_debt = 0.05
    fg.technology.capex = 1000.0
    fg.tech_unit_fopex = 35.0
    fg.equity_share = 0.3
    fg.railway_cost = 10.0
    fg.chosen_reductant = "scrap"
    fg.bill_of_materials = BOM
    fg.output_shares = {"scrap": 1.0}
    fg.energy_costs_no_subsidy = dict(SITE_PRICES)
    return fg


def _recording_stub(scores: list[float]):
    """A score-series stub returning fixed scores and recording its call args.

    Args:
        scores: Score list returned regardless of the requested window length
            (must match the window the re-check computes).

    Returns:
        Callable with a ``calls`` list of kwargs dicts.
    """

    def stub(location, tech, output_shares, start, end, **kwargs):
        stub.calls.append(
            {
                "location": location,
                "tech": tech,
                "output_shares": output_shares,
                "start": start,
                "end": end,
                **kwargs,
            },
        )
        assert len(scores) == int(end) - int(start)
        return ReductantScoreSeries(scores=list(scores), picks=["scrap"] * len(scores))

    stub.calls = []
    return stub


def _track(fg, year, stub, *, probability_of_announcement: float = 0.8):
    """Invoke the re-check with the shared fixture parameters."""
    return fg.track_business_opportunities(
        year=Year(year),
        location=LOCATION,
        market_price=MARKET_PRICE,
        cost_of_equity=0.08,
        plant_lifetime=5,
        construction_time=2,
        consideration_time=3,
        probability_of_announcement=probability_of_announcement,
        all_opex_subsidies=[],
        reductant_score_series=stub,
    )


def test_re_check_npv_equals_creation_npv():
    """The first re-check reproduces the creation NPV to machine precision.

    Notes:
        Creation values at target_year = current + consideration_time + 1 with the
        operating window [target_year + construction_time, + plant_lifetime); the
        re-check's sliding window lands on the same absolute years, so with identical
        inputs the two metrics must agree exactly. This pins the fopex scaling and
        the forwarded equity share.
    """
    scores = [12.0, 11.0, 10.0, 9.0, 8.0]
    cost_data = {
        "steel": {
            (40.0, -100.0, "USA"): {
                "EAF": {
                    "capex": 1000.0,
                    "cost_of_debt": 0.05,
                    "cost_of_equity": 0.08,
                    "fopex": 35.0,
                    "utilization_rate": 0.7,
                    "railway_cost": 10.0,
                    "bom": BOM,
                    "score_series": scores,
                    "all_opex_subsidies": [],
                },
            },
        },
    }
    npv_dict = calculate_business_opportunity_npvs(
        cost_data=cost_data,
        target_year=2029,  # 2025 + consideration_time (3) + 1
        market_price=MARKET_PRICE,
        steel_plant_capacity=100_000.0,
        plant_lifetime=5,
        construction_time=2,
        equity_share=0.3,
    )
    creation_npv = npv_dict["steel"][(40.0, -100.0, "USA")]["EAF"]

    fg = _make_opportunity_fg()
    fg.historical_npv_business_opportunities = {Year(2025): creation_npv}
    stub = _recording_stub(scores)

    _track(fg, 2026, stub)

    assert fg.historical_npv_business_opportunities[Year(2026)] == pytest.approx(creation_npv, rel=1e-12)
    call = stub.calls[0]
    assert (call["start"], call["end"]) == (Year(2031), Year(2036))
    assert call["location"] is LOCATION
    assert call["output_shares"] == {"scrap": 1.0}
    assert call["overrides"] == SITE_PRICES
    assert call["override_reference_year"] == Year(2026)


def test_improving_scores_announce_where_frozen_metric_would_discard():
    """Falling scores rescue an opportunity the frozen-BOM metric priced negative.

    Notes:
        The old metric valued the frozen BOM (materials + energy rows) flat over the
        lifetime; with these inputs that NPV is negative every year, so the
        opportunity would have been discarded after three re-checks. The score series
        makes the improvement visible and the opportunity announces instead.
    """
    fg = _make_opportunity_fg()
    fg.technology.capex = 100.0
    fg.historical_npv_business_opportunities = {Year(2025): 1.0}

    frozen_opex = (
        calculate_variable_opex(BOM["materials"], BOM["energy"])
        + scale_fopex_to_production(fg.tech_unit_fopex, fg.utilization_rate)
        + 400.0  # an expensive frozen reductant the per-year re-pick would abandon
    )
    old_metric_npv = calculate_npv_full(
        capex=100.0,
        capacity=100_000.0,
        unit_total_opex_list=[frozen_opex] * 5,
        expected_utilisation_rate=0.7,
        price_series=MARKET_PRICE["steel"],
        lifetime=5,
        construction_time=2,
        cost_of_debt=0.05,
        cost_of_equity=0.08,
        equity_share=0.3,
        infrastructure_costs=10.0,
    )
    assert old_metric_npv < 0

    stub = _recording_stub([80.0, 60.0, 40.0, 20.0, 10.0])
    with patch("random.random", return_value=0.0):
        first = _track(fg, 2026, stub)
        second = _track(fg, 2027, stub)

    assert first is None  # only two NPVs on record after this call
    assert all(npv > 0 for npv in fg.historical_npv_business_opportunities.values())
    assert isinstance(second, UpdateFurnaceGroupStatus)
    assert second.new_status == "announced"


def test_flat_score_reproduces_the_frozen_energy_metric():
    """With no dynamics the new metric equals the old frozen-BOM valuation exactly.

    Notes:
        A flat score equal to the frozen BOM's energy VOPEX, utilisation 1.0 (so the
        old threshold-clamped fopex scaling and scale_fopex_to_production coincide),
        zero carbon and no by-products make the two metrics identical term for term.
    """
    fg = _make_opportunity_fg(utilization_rate=1.0)
    fg.historical_npv_business_opportunities = {Year(2025): 1.0}

    energy_vopex = calculate_variable_opex({}, BOM["energy"])
    old_style_opex = calculate_variable_opex(BOM["materials"], BOM["energy"]) + fg.tech_unit_fopex
    old_metric_npv = calculate_npv_full(
        capex=1000.0,
        capacity=100_000.0,
        unit_total_opex_list=calculate_opex_list_with_subsidies(
            opex=old_style_opex,
            opex_subsidies=[],
            start_year=Year(2031),
            end_year=Year(2036),
        ),
        expected_utilisation_rate=1.0,
        price_series=MARKET_PRICE["steel"],
        lifetime=5,
        construction_time=2,
        cost_of_debt=0.05,
        cost_of_equity=0.08,
        equity_share=0.3,
        infrastructure_costs=10.0,
    )

    stub = _recording_stub([energy_vopex] * 5)
    _track(fg, 2026, stub)

    assert fg.historical_npv_business_opportunities[Year(2026)] == pytest.approx(old_metric_npv, rel=1e-12)


def test_missing_output_shares_records_negative_infinity():
    """A furnace group without output shares cannot be valued and records -inf."""
    from collections import Counter

    fg = _make_opportunity_fg()
    fg.output_shares = None
    fg.historical_npv_business_opportunities = {Year(2025): 1.0}
    status_stats = Counter()

    command = fg.track_business_opportunities(
        year=Year(2026),
        location=LOCATION,
        market_price=MARKET_PRICE,
        cost_of_equity=0.08,
        plant_lifetime=5,
        construction_time=2,
        consideration_time=3,
        probability_of_announcement=0.8,
        all_opex_subsidies=[],
        reductant_score_series=_recording_stub([0.0] * 5),
        status_stats=status_stats,
    )

    assert command is None
    assert fg.historical_npv_business_opportunities[Year(2026)] == float("-inf")
    assert status_stats["npv_inputs_missing"] == 1
