"""Tests for energy reconstruction in get_bom_from_avg_boms (Stage 1).

Validates the two-pass approach: MC validation, output-share computation,
and PrimaryFeedstock-based energy accumulation.
"""

import pytest

from steelo.domain.models import Environment


class DummyFeed:
    """Minimal PrimaryFeedstock stand-in for unit tests."""

    def __init__(
        self,
        metallic_charge: str,
        reductant: str,
        required_qty: float,
        secondary_feedstock: dict[str, float] | None = None,
        energy_requirements: dict[str, float] | None = None,
    ):
        self.metallic_charge = metallic_charge
        self.reductant = reductant
        self.required_quantity_per_ton_of_product = required_qty
        self.secondary_feedstock = secondary_feedstock or {}
        self.energy_requirements = energy_requirements or {}


def _make_env(**overrides) -> Environment:
    """Create a minimal Environment stub for get_bom_from_avg_boms tests."""
    env = Environment.__new__(Environment)
    for k, v in overrides.items():
        setattr(env, k, v)
    return env


def test_two_pass_mc_skip_with_warning():
    """MC missing from input_effectiveness is skipped; survivor gets output_share=1.0."""
    env = _make_env(
        dynamic_feedstocks={
            "EAF": [
                DummyFeed("scrap", "coke", 1.1, energy_requirements={"electricity": 400.0}),
            ],
        },
        avg_boms={
            "EAF": {
                "scrap": {"input_share_pct": 0.8, "unit_cost": 300.0},
                # ghost_metal has no matching PrimaryFeedstock → should be skipped
                "ghost_metal": {"input_share_pct": 0.2, "unit_cost": 500.0},
            },
        },
        avg_utilization={"EAF": {"utilization_rate": 0.8}},
    )

    bom, utilization, reductant, _ = env.get_bom_from_avg_boms(
        energy_costs={"electricity": 50.0},
        tech="EAF",
        capacity=100.0,
        most_common_reductant="coke",
    )

    # ghost_metal skipped — only scrap survives
    assert "scrap" in bom["materials"]
    assert "ghost_metal" not in bom["materials"]

    # With single survivor, output_share = 1.0
    # electricity demand = 1.0 * 400.0 * 100.0 = 40,000
    assert bom["energy"]["electricity"]["demand"] == pytest.approx(40_000.0)
    assert bom["energy"]["electricity"]["unit_cost"] == pytest.approx(50.0)


def test_energy_reconstruction_single_mc():
    """Single MC with known PF data → verify carrier demands exactly."""
    env = _make_env(
        dynamic_feedstocks={
            "BF": [
                DummyFeed(
                    "io_high",
                    "coke",
                    1.4,
                    secondary_feedstock={"burnt_lime": 0.05, "coking_coal": 0.3},
                    energy_requirements={"electricity": 50.0, "natural_gas": 2.0},
                ),
            ],
        },
        avg_boms={"BF": {"io_high": {"input_share_pct": 1.0, "unit_cost": 150.0}}},
        avg_utilization={},
    )

    bom, _, _, _ = env.get_bom_from_avg_boms(
        energy_costs={"electricity": 60.0, "natural_gas": 8.0, "burnt_lime": 100.0, "coking_coal": 200.0},
        tech="BF",
        capacity=1000.0,
        most_common_reductant="coke",
    )

    # output_share = 1.0 for single MC
    # electricity: 1.0 * 50.0 * 1000 = 50,000
    assert bom["energy"]["electricity"]["demand"] == pytest.approx(50_000.0)
    assert bom["energy"]["electricity"]["total_cost"] == pytest.approx(3_000_000.0)
    # natural_gas: 1.0 * 2.0 * 1000 = 2,000
    assert bom["energy"]["natural_gas"]["demand"] == pytest.approx(2_000.0)
    # burnt_lime from secondary_feedstock (in ENERGY_FEEDSTOCK_KEYS): 1.0 * 0.05 * 1000 = 50
    assert bom["energy"]["burnt_lime"]["demand"] == pytest.approx(50.0)
    # coking_coal from secondary_feedstock: 1.0 * 0.3 * 1000 = 300
    assert bom["energy"]["coking_coal"]["demand"] == pytest.approx(300.0)


def test_energy_reconstruction_multi_mc_output_shares():
    """Two MCs with different effectivenesses → verify output-share-weighted energy.

    EAF with 80% scrap (eff=1.09) / 20% pig_iron (eff=1.1351) input shares.
    Matches the worked example in the spec (§3.3).
    """
    env = _make_env(
        dynamic_feedstocks={
            "EAF": [
                DummyFeed("scrap", "coke", 1.09, energy_requirements={"electricity": 413.3}),
                DummyFeed("pig_iron", "coke", 1.1351, energy_requirements={"electricity": 100.0}),
            ],
        },
        avg_boms={
            "EAF": {
                "scrap": {"input_share_pct": 0.8, "unit_cost": 300.0},
                "pig_iron": {"input_share_pct": 0.2, "unit_cost": 400.0},
            },
        },
        avg_utilization={},
    )

    bom, _, _, _ = env.get_bom_from_avg_boms(
        energy_costs={"electricity": 50.0},
        tech="EAF",
        capacity=100.0,
        most_common_reductant="coke",
    )

    # Output shares:
    # raw_scrap = 0.8/1.09 = 0.7339, raw_pig = 0.2/1.1351 = 0.1762
    # total_raw = 0.9101
    # o_scrap = 0.7339/0.9101 ≈ 0.8064, o_pig = 0.1762/0.9101 ≈ 0.1936
    # Weighted electricity = 0.8064*413.3 + 0.1936*100.0 ≈ 352.65 kWh/t
    # Total demand = 352.65 * 100 = 35,265
    assert bom["energy"]["electricity"]["demand"] == pytest.approx(35_265.0, rel=1e-3)

    # Materials use output shares, same basis as energy (eff applied exactly once)
    o_scrap = (0.8 / 1.09) / (0.8 / 1.09 + 0.2 / 1.1351)
    o_pig = (0.2 / 1.1351) / (0.8 / 1.09 + 0.2 / 1.1351)
    assert bom["materials"]["scrap"]["demand"] == pytest.approx(o_scrap * 100 * 1.09)
    assert bom["materials"]["pig_iron"]["demand"] == pytest.approx(o_pig * 100 * 1.1351)


def test_cheapest_reductant_fallback():
    """When most_common_reductant is None, cheapest-reductant fallback picks the right one."""
    env = _make_env(
        dynamic_feedstocks={
            "DRI": [
                # natural_gas reductant — cheaper energy
                DummyFeed(
                    "io_low",
                    "natural_gas",
                    1.5,
                    energy_requirements={"electricity": 100.0, "natural_gas": 3.0},
                ),
                # hydrogen reductant — expensive energy
                DummyFeed(
                    "io_low",
                    "hydrogen",
                    1.5,
                    energy_requirements={"electricity": 200.0, "hydrogen": 5.0},
                ),
            ],
        },
        avg_boms={"DRI": {"io_low": {"input_share_pct": 1.0, "unit_cost": 200.0}}},
        avg_utilization={},
    )

    bom, _, reductant, _ = env.get_bom_from_avg_boms(
        energy_costs={"electricity": 50.0, "natural_gas": 10.0, "hydrogen": 500.0},
        tech="DRI",
        capacity=1000.0,
        most_common_reductant=None,  # trigger fallback
    )

    # natural_gas reductant: 100*50 + 3*10 = 5,030
    # hydrogen reductant: 200*50 + 5*500 = 12,500
    # Should pick natural_gas
    assert reductant == "natural_gas"

    # Energy from the natural_gas PF variant
    assert "electricity" in bom["energy"]
    assert bom["energy"]["electricity"]["demand"] == pytest.approx(100_000.0)
    assert "natural_gas" in bom["energy"]
    assert bom["energy"]["natural_gas"]["demand"] == pytest.approx(3_000.0)
    # hydrogen should NOT be present (wrong reductant)
    assert "hydrogen" not in bom["energy"]


def test_zero_cost_carrier_warning():
    """PF has positive intensity but energy_costs has no entry → included with 0 cost."""
    env = _make_env(
        dynamic_feedstocks={
            "DRI+CCU": [
                DummyFeed(
                    "io_low",
                    "hydrogen",
                    1.5,
                    energy_requirements={"electricity": 100.0, "hydrogen": 5.0},
                ),
            ],
        },
        avg_boms={"DRI+CCU": {"io_low": {"input_share_pct": 1.0, "unit_cost": 200.0}}},
        avg_utilization={},
    )

    bom, _, _, _ = env.get_bom_from_avg_boms(
        energy_costs={"electricity": 50.0},  # no hydrogen price
        tech="DRI+CCU",
        capacity=1000.0,
        most_common_reductant="hydrogen",
    )

    # hydrogen included with zero cost (demand is real, price is missing)
    assert "hydrogen" in bom["energy"]
    assert bom["energy"]["hydrogen"]["demand"] == pytest.approx(5_000.0)
    assert bom["energy"]["hydrogen"]["total_cost"] == pytest.approx(0.0)
    assert bom["energy"]["hydrogen"]["unit_cost"] == pytest.approx(0.0)


def test_deployed_tech_reorder_noop():
    """When most_common_reductant is already set, reductant resolution is a no-op."""
    feed = DummyFeed("scrap", "coke", 1.1, energy_requirements={"electricity": 400.0})
    env = _make_env(
        dynamic_feedstocks={"EAF": [feed]},
        avg_boms={"EAF": {"scrap": {"input_share_pct": 1.0, "unit_cost": 300.0}}},
        avg_utilization={"EAF": {"utilization_rate": 0.9}},
    )

    bom, utilization, reductant, _ = env.get_bom_from_avg_boms(
        energy_costs={"electricity": 50.0},
        tech="EAF",
        capacity=100.0,
        most_common_reductant="coke",
    )

    assert reductant == "coke"
    assert utilization == pytest.approx(0.9)
    assert bom["materials"]["scrap"]["demand"] == pytest.approx(110.0)
    assert bom["energy"]["electricity"]["demand"] == pytest.approx(40_000.0)


def test_demand_share_pct_in_materials_output():
    """Output BOM materials include demand_share_pct in TM convention (demand / product_volume).

    Two MCs with different effectivenesses — demand_share_pct = output_share * eff and
    should NOT sum to 1.0 (it sums to the output-share-weighted average efficiency).
    """
    env = _make_env(
        dynamic_feedstocks={
            "EAF": [
                DummyFeed("scrap", "coke", 1.09, energy_requirements={"electricity": 400.0}),
                DummyFeed("pig_iron", "coke", 1.14, energy_requirements={"electricity": 100.0}),
            ],
        },
        avg_boms={
            "EAF": {
                "scrap": {"input_share_pct": 0.8, "unit_cost": 300.0},
                "pig_iron": {"input_share_pct": 0.2, "unit_cost": 400.0},
            },
        },
        avg_utilization={},
    )

    bom, _, _, _ = env.get_bom_from_avg_boms(
        energy_costs={"electricity": 50.0},
        tech="EAF",
        capacity=100.0,
        most_common_reductant="coke",
    )

    o_scrap = (0.8 / 1.09) / (0.8 / 1.09 + 0.2 / 1.14)
    o_pig = (0.2 / 1.14) / (0.8 / 1.09 + 0.2 / 1.14)

    # demand_share_pct = output_share * eff = demand / product_volume (TM convention)
    assert bom["materials"]["scrap"]["demand_share_pct"] == pytest.approx(o_scrap * 1.09)
    assert bom["materials"]["pig_iron"]["demand_share_pct"] == pytest.approx(o_pig * 1.14)

    # Verify it does NOT sum to 1.0 (unlike input_share_pct)
    total = sum(m["demand_share_pct"] for m in bom["materials"].values())
    assert total != pytest.approx(1.0)

    # unit_cost = total_cost / product_volume (per-output, TM convention)
    assert bom["materials"]["scrap"]["unit_cost"] == pytest.approx(300.0 * o_scrap * 1.09)
    assert bom["materials"]["pig_iron"]["unit_cost"] == pytest.approx(400.0 * o_pig * 1.14)
    # total_cost consistent with demand (authoritative value)
    assert bom["materials"]["scrap"]["total_cost"] == pytest.approx(300.0 * o_scrap * 100.0 * 1.09)


def test_material_demand_applies_efficiency_exactly_once():
    """Fleet input shares already embed eff; demand must be output_share * capacity * eff.

    Regression test for the efficiency double-application at models.py get_bom_from_avg_boms:
    a fleet running charge A (eff 2.0) and charge B (eff 1.0) at equal output split has input
    shares 2/3 vs 1/3. Rebuilding a candidate BOM must recover the equal output split — total
    input tonnage C * (s_A*e_A + s_B*e_B) = 1.5 * capacity, not the inflated
    C * Σ share_i * e_i² / Σ s_j*e_j ≈ 1.667 * capacity of the buggy input-share basis.
    """
    env = _make_env(
        dynamic_feedstocks={
            "BF": [
                DummyFeed("io_low", "coke", 2.0, energy_requirements={"electricity": 100.0}),
                DummyFeed("io_high", "coke", 1.0, energy_requirements={"electricity": 100.0}),
            ],
        },
        avg_boms={
            # Equal output split -> input tonnes 2:1 -> input shares 2/3 and 1/3
            "BF": {
                "io_low": {"input_share_pct": 2.0 / 3.0, "unit_cost": 100.0},
                "io_high": {"input_share_pct": 1.0 / 3.0, "unit_cost": 150.0},
            },
        },
        avg_utilization={},
    )

    bom, _, _, output_shares = env.get_bom_from_avg_boms(
        energy_costs={"electricity": 50.0},
        tech="BF",
        capacity=100.0,
        most_common_reductant="coke",
    )

    # Derived output shares recover the equal split
    assert output_shares["io_low"] == pytest.approx(0.5)
    assert output_shares["io_high"] == pytest.approx(0.5)

    # demand = output_share * capacity * eff, applied exactly once
    assert bom["materials"]["io_low"]["demand"] == pytest.approx(0.5 * 100.0 * 2.0)
    assert bom["materials"]["io_high"]["demand"] == pytest.approx(0.5 * 100.0 * 1.0)

    # Total input tonnage matches the physical mix (150 t input per 100 t output)
    total_input = sum(m["demand"] for m in bom["materials"].values())
    assert total_input == pytest.approx(150.0)

    # Materials VOPEX per tonne of output = Σ o_i * e_i * unit_cost_i
    total_cost = sum(m["total_material_cost"] for m in bom["materials"].values())
    assert total_cost / 100.0 == pytest.approx(0.5 * 2.0 * 100.0 + 0.5 * 1.0 * 150.0)
