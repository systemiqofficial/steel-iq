"""
Turning a cost dataset into the anchor set the frontier build searches under.

An anchor is one set of cost coefficients. It decides *where* the build places its dense
patches and nothing else: the values stored at those patches are pure dispatch, so a badly
chosen anchor costs search quality, never accuracy. The containment certificate detects the
case where it mattered.

Anchors have to span two axes, and the second is the larger of the two:

- **Investment year.** Capex declines over the horizon, and the mix shifts with it.
- **Cost key.** Each pixel is priced with its own country's capex, opex and WACC. Measured on
  the real workbook, the spread of mixes across the 244 keys at a single year is 0.157 against
  0.088 of drift across the whole 2025-2060 horizon -- so *where* a key sits matters about
  twice as much as *when* it is queried.

Anchoring on years alone would leave keys at the edges of that spread served by an anchor built
for the middle. `covering_anchors` therefore selects over the joint set, guaranteeing every
`(key, year)` combination lies within `tol` of some anchor.

The build takes the resulting list as an *input* and never derives a cost key itself, which is
what keeps the frontier cache independent of the canonical iso3 grid.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

import xarray as xr

from boa.model.bisection import CostCoefficients, covering_anchors
from boa.model.cost_calculations import lcoe_coefficients
from boa.model.single_point_run import costs_for_key

# Simplex distance two anchors may sit apart before both are kept. Expressed in shares of the
# cost mix, so it is independent of currency, units and baseload. TODO: settle against the
# re-anchoring benchmark, which should measure how much mix movement it takes to move a seed --
# the total available drift is only ~0.088, so this is a meaningful fraction of it.
DEFAULT_ANCHOR_TOL = 0.02

# Anchors are searched under, and each one costs seed selection plus whatever distinct patch
# boxes it adds. This bounds the build if `tol` is set pathologically small; exceeding it leaves
# some cost keys further than `tol` from any anchor, which costs uncertified queries rather than
# wrong ones.
DEFAULT_MAX_ANCHORS = 32


def anchor_cost_coefficients(
    years: Sequence[int],
    costs_for_year: Callable[[int], tuple[xr.Dataset, int]],
    tol: float = DEFAULT_ANCHOR_TOL,
    max_anchors: int = DEFAULT_MAX_ANCHORS,
) -> list[CostCoefficients]:
    """
    The anchor set covering every `(cost key, investment year)` this cost set can produce.

    `costs_for_year` returns that year's cost dataset and investment horizon, already sliced so
    index 0 is the investment year -- the same shape the query uses, so an anchor is priced
    exactly as a real query would price it. It is called once per year and its result reused
    across keys.

    Coefficients are built at baseload 1.0. Baseload scales all four linearly and so cancels
    out of the mix entirely, which is the same reason LCOE is exactly baseload-invariant.
    """
    per_year: dict[int, tuple[xr.Dataset, int]] = {}
    candidates: list[tuple[int, str]] = []
    for year in years:
        costs, horizon = costs_for_year(year)
        per_year[year] = (costs, horizon)
        candidates.extend((year, str(key)) for key in costs["iso3"].values)

    if not candidates:
        raise ValueError("no (cost key, year) candidates; the cost set has no iso3 coordinate")

    def coefficients_for(candidate: tuple[int, str]) -> CostCoefficients:
        year, key = candidate
        costs, horizon = per_year[year]
        capex, opex_pct, cost_of_capital = costs_for_key(key, costs)
        return lcoe_coefficients(horizon, capex, opex_pct, cost_of_capital, 1.0)

    chosen = covering_anchors(candidates, coefficients_for, tol=tol, max_anchors=max_anchors)
    if len(chosen) == max_anchors:
        logging.warning(
            f"Anchor selection hit max_anchors={max_anchors} at tol={tol}; some cost keys sit "
            f"further than {tol} from every anchor. Seeds may be misplaced for those keys, which "
            f"costs uncertified queries rather than wrong answers -- raise tol or max_anchors."
        )
    logging.info(
        f"Anchor set: {len(chosen)} anchors covering {len(candidates)} (cost key, year) "
        f"combinations at tol={tol} — {sorted({y for y, _ in chosen})}"
    )
    return [coefficients_for(c) for c in chosen]
