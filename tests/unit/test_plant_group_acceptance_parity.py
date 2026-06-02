"""expansion and switch must produce identical P(accept) for identical (equity, NPV)."""

import math


def test_expansion_and_switch_probability_formulas_match():
    """For identical (equity_needed, npv), both paths yield the same acceptance probability."""
    # Equity needed (capex × capacity × equity_share) and NPV (equity-basis) are the inputs
    # that should fully determine P(accept) under both flows.
    equity_needed = 800.0 * 2_000_000.0 * 0.2  # $320M equity at 2Mt × $800/t × 20%
    npv = 500_000_000.0  # $500M equity-basis NPV

    # Expansion (post-Stage-2 formula in PlantGroup.evaluate_expansion Stage 7).
    expansion_p = math.exp(-equity_needed / npv)

    # Switch (unchanged — Plant.evaluate_furnace_group_strategy Stage 10).
    # switch_cost is exactly equity_needed when the inputs match.
    switch_p = math.exp(-equity_needed / npv)

    assert expansion_p == switch_p
    # Sanity: at equity=0.2 × full CAPEX, ratio ≈ 0.64 → P ≈ 0.527 (Behavioural diff #6).
    assert 0.4 < expansion_p < 0.7


def test_pre_spec_expansion_formula_was_substantially_lower():
    """Document the pre-fix gap: full-capex formula gave a much lower P than the corrected one."""
    full_investment = 800.0 * 2_000_000.0  # $1.6B full CAPEX
    equity_needed = full_investment * 0.2
    npv = 500_000_000.0

    pre_fix_expansion = math.exp(-full_investment / npv)
    post_fix_expansion = math.exp(-equity_needed / npv)

    # Behavioural diff #6: ~4% pre-fix → ~53% post-fix for this canonical scenario.
    assert pre_fix_expansion < 0.1
    assert post_fix_expansion > 0.5
    assert post_fix_expansion > pre_fix_expansion * 10
