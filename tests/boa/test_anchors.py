"""
Contracts for building the frontier build's anchor set from a cost dataset.

The theme is that anchors span two axes, not one. Anchoring on investment years alone is the
easy mistake: measured on the real workbook the spread of cost mixes across keys at a single
year is larger than the drift across the whole horizon, so a key at the edge of that spread
must not be left to an anchor built for the middle.
"""

import numpy as np
import pytest
import xarray as xr

from _gate import require

require("boa.model.anchors", "anchor_cost_coefficients")

from boa.model.anchors import anchor_cost_coefficients  # noqa: E402
from boa.model.bisection import cost_ratio_simplex  # noqa: E402

HORIZON = 25
N_YEARS = HORIZON + 1


def _costs(keys_to_capex: dict[str, dict[str, float]], coc: float = 0.08) -> xr.Dataset:
    """
    A minimal cost dataset shaped like the real one: `Capex <tech>` over (iso3, year) with
    index 0 the investment year, plus per-key opex and cost of capital.
    """
    keys = list(keys_to_capex)
    data = {}
    for tech in ("solar", "wind", "battery"):
        data[f"Capex {tech}"] = (
            ("iso3", "year"),
            np.array([[keys_to_capex[k][tech]] * N_YEARS for k in keys], dtype=float).reshape(len(keys), N_YEARS),
        )
        data[f"Opex {tech}"] = (("iso3",), np.full(len(keys), 0.02))
    data["Cost of capital"] = (("iso3",), np.full(len(keys), coc))
    return xr.Dataset(data, coords={"iso3": keys, "year": np.arange(N_YEARS)})


def _one_year(dataset: xr.Dataset):
    return lambda year: (dataset, HORIZON)


# --------------------------------------------------------------------------


def test_identical_keys_and_years_collapse_to_one_anchor():
    """Nothing varies, so nothing is worth a second anchor."""
    same = {t: 1000.0 for t in ("solar", "wind", "battery")}
    costs = _costs({"AAA": same, "BBB": same, "CCC": same})
    anchors = anchor_cost_coefficients([2025, 2030, 2040], _one_year(costs))
    assert len(anchors) == 1


def test_a_key_with_a_different_mix_earns_its_own_anchor():
    """
    The case anchoring on years alone would miss: two countries in the same year whose cost
    mixes differ enough that they would place seeds in different places.
    """
    costs = _costs(
        {
            "SOLAR_CHEAP": {"solar": 100.0, "wind": 2000.0, "battery": 1000.0},
            "WIND_CHEAP": {"solar": 2000.0, "wind": 100.0, "battery": 1000.0},
        }
    )
    anchors = anchor_cost_coefficients([2025], _one_year(costs))
    assert len(anchors) == 2

    mixes = sorted(cost_ratio_simplex(a)[0] for a in anchors)
    assert mixes[0] < mixes[1], "the two anchors should sit at opposite ends of the solar share"


def test_every_key_ends_up_within_tol_of_some_anchor():
    """The guarantee the whole selection exists to provide."""
    rng = np.random.default_rng(0)
    keys = {
        f"K{i:03d}": {
            "solar": float(rng.uniform(200.0, 2000.0)),
            "wind": float(rng.uniform(200.0, 2000.0)),
            "battery": float(rng.uniform(200.0, 2000.0)),
        }
        for i in range(60)
    }
    costs = _costs(keys)
    tol = 0.05
    anchors = anchor_cost_coefficients([2025], _one_year(costs), tol=tol)
    kept = [np.asarray(cost_ratio_simplex(a)) for a in anchors]

    from boa.model.cost_calculations import lcoe_coefficients
    from boa.model.single_point_run import costs_for_key

    for key in keys:
        capex, opex, coc = costs_for_key(key, costs)
        r = np.asarray(cost_ratio_simplex(lcoe_coefficients(HORIZON, capex, opex, coc, 1.0)))
        assert min(float(np.max(np.abs(r - k))) for k in kept) <= tol + 1e-12, key


def test_a_declining_capex_trajectory_earns_anchors_over_time():
    """
    The other axis. Same single key, but its mix moves as capex declines, so later years are
    not covered by the 2025 anchor.
    """
    per_year = {
        2025: _costs({"AAA": {"solar": 1000.0, "wind": 1000.0, "battery": 1000.0}}),
        2040: _costs({"AAA": {"solar": 300.0, "wind": 950.0, "battery": 800.0}}),
    }
    anchors = anchor_cost_coefficients([2025, 2040], lambda y: (per_year[y], HORIZON))
    assert len(anchors) == 2


def test_max_anchors_bounds_the_build_and_warns(caplog):
    """
    Stopping early leaves keys uncovered. That costs uncertified queries rather than wrong
    answers, but it must not pass silently -- the tolerance was asked for and not delivered.
    """
    rng = np.random.default_rng(1)
    keys = {
        f"K{i:03d}": {t: float(rng.uniform(100.0, 3000.0)) for t in ("solar", "wind", "battery")} for i in range(40)
    }
    costs = _costs(keys)
    with caplog.at_level("WARNING"):
        anchors = anchor_cost_coefficients([2025], _one_year(costs), tol=1e-6, max_anchors=3)
    assert len(anchors) == 3
    assert "max_anchors" in caplog.text


def test_anchors_do_not_depend_on_baseload():
    """
    Coefficients are built at baseload 1.0 because baseload scales all four linearly and so
    cancels out of the mix -- the same identity that makes LCOE baseload-invariant. If it ever
    stopped cancelling, the anchor set would silently become baseload-specific.
    """
    costs = _costs(
        {
            "AAA": {"solar": 500.0, "wind": 1500.0, "battery": 900.0},
            "BBB": {"solar": 1800.0, "wind": 400.0, "battery": 900.0},
        }
    )
    anchors = anchor_cost_coefficients([2025], _one_year(costs))
    for a in anchors:
        scaled = type(a)(a_s=a.a_s * 7.0, a_w=a.a_w * 7.0, a_b=a.a_b * 7.0, d0=a.d0 * 7.0)
        assert cost_ratio_simplex(scaled) == pytest.approx(cost_ratio_simplex(a))


def test_an_empty_cost_set_raises():
    empty = _costs({})
    with pytest.raises(ValueError, match="no .* candidates"):
        anchor_cost_coefficients([2025], _one_year(empty))
