"""Tests for the reductant overhaul (A1 status-filtered modal vote, A2 candidate
energy subsidies, B5 total-cost annual re-pick)."""

from datetime import date

from steelo.domain.models import (
    Environment,
    FurnaceGroup,
    Location,
    Plant,
    PlantGroup,
    PointInTime,
    PrimaryFeedstock,
    Subsidy,
    Technology,
    Year,
)
from steelo.domain.calculate_costs import ReductantScoreSeries
from steelo.domain.constants import Volumes
from steelo.utilities import utils


def _stub_score_series(tech, output_shares, start, end):
    n = int(end) - int(start)
    return ReductantScoreSeries(scores=[0.0] * n, picks=["hydrogen"] * n)


def _make_fg(fg_id: str, tech_name: str, reductant: str, status: str = "operating") -> FurnaceGroup:
    """Build a minimal furnace group with a fixed chosen reductant."""
    return FurnaceGroup(
        furnace_group_id=fg_id,
        capacity=100_000,
        status=status,
        last_renovation_date=date(2020, 1, 1),
        technology=Technology(name=tech_name, product="iron"),
        historical_production={},
        utilization_rate=0.8,
        lifetime=PointInTime(plant_lifetime=20, current=Year(2025)),
        chosen_reductant=reductant,
        energy_cost_dict={},
    )


# ── A1: status-filtered fleet-modal aggregation ──────────────────────────────


def test_most_common_reductant_excludes_inactive_statuses():
    """Closed and discarded furnace groups do not vote in the modal aggregation.

    Two closed coal FGs must not outvote one operating natural_gas FG.
    """
    furnace_groups = [
        _make_fg("fg1", "DRI", "coal", status="closed"),
        _make_fg("fg2", "DRI", "coal", status="discarded"),
        _make_fg("fg3", "DRI", "natural_gas", status="operating"),
    ]
    result = utils.get_most_common_reductant_by_technology(furnace_groups, ["operating"])
    assert result == {"DRI": "natural_gas"}


def test_most_common_reductant_only_listed_statuses_count():
    """Only statuses named in active_statuses vote (case-insensitively)."""
    furnace_groups = [
        _make_fg("fg1", "DRI", "coal", status="Operating"),
        _make_fg("fg2", "DRI", "hydrogen", status="announced"),
        _make_fg("fg3", "DRI", "hydrogen", status="announced"),
    ]
    only_operating = utils.get_most_common_reductant_by_technology(furnace_groups, ["operating"])
    assert only_operating == {"DRI": "coal"}

    both = utils.get_most_common_reductant_by_technology(furnace_groups, ["operating", "announced"])
    assert both == {"DRI": "hydrogen"}

    none_active = utils.get_most_common_reductant_by_technology(furnace_groups, [])
    assert none_active == {}


def _make_env_for_feedstocks(dynamic_feedstocks: dict) -> Environment:
    """Build a minimal Environment stub for set_primary_feedstocks_in_furnace_groups."""
    env = object.__new__(Environment)
    env.dynamic_feedstocks = dynamic_feedstocks
    env.technology_to_product = {"DRI": "iron"}
    env.cost_breakdown_keys = []
    env.carbon_breakdown_keys = []
    env.config = type(
        "Config",
        (),
        {"active_statuses": ["operating"], "chosen_emissions_boundary_for_carbon_costs": "plant_boundary"},
    )()
    env.most_common_reductant_by_tech = {}
    env.carbon_costs = {}
    env.technology_emission_factors = _dri_emission_factors()
    env.year = Year(2025)
    return env


def _make_dri_plant(fg: FurnaceGroup) -> Plant:
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
        technology_unit_fopex={"dri": 10.0},
    )


def _dri_feedstocks() -> list[PrimaryFeedstock]:
    """Two DRI business cases (coal vs natural_gas) over the same metallic charge."""
    coal = PrimaryFeedstock(metallic_charge="iron_ore", reductant="coal", technology="DRI")
    coal.add_energy_requirement("coal", 10.0)
    coal.required_quantity_per_ton_of_product = 1.5
    gas = PrimaryFeedstock(metallic_charge="iron_ore", reductant="natural_gas", technology="DRI")
    gas.add_energy_requirement("natural_gas", 10.0)
    gas.required_quantity_per_ton_of_product = 1.5
    return [coal, gas]


def test_set_primary_feedstocks_skips_closed_furnace_groups():
    """A closed FG keeps its previous chosen_reductant; an operating one re-picks."""
    env = _make_env_for_feedstocks({"DRI": _dri_feedstocks()})

    # Coal is cheaper, so a re-pick would choose coal.
    energy_costs = {"coal": 1.0, "natural_gas": 2.0}
    closed_fg = _make_fg("fg_closed", "DRI", "natural_gas", status="closed")
    closed_fg.set_energy_costs(**energy_costs)
    operating_fg = _make_fg("fg_open", "DRI", "natural_gas", status="operating")
    operating_fg.set_energy_costs(**energy_costs)

    env.set_primary_feedstocks_in_furnace_groups(
        world_plants=[_make_dri_plant(closed_fg), _make_dri_plant(operating_fg)]
    )

    assert closed_fg.chosen_reductant == "natural_gas"  # frozen
    assert operating_fg.chosen_reductant == "coal"  # re-picked
    # The dynamic business case is still attached to both FGs.
    assert closed_fg.technology.dynamic_business_case
    # The environment-level vote only counts the operating FG.
    assert env.most_common_reductant_by_tech == {"DRI": "coal"}


# ── A2: candidate-technology energy subsidies ────────────────────────────────


def _hydrogen_subsidy(tech: str) -> Subsidy:
    return Subsidy(
        scenario_name="test",
        iso3="NZL",
        start_year=Year(2025),
        end_year=Year(2035),
        technology_name=tech,
        cost_item="hydrogen",
        subsidy_type="absolute",
        subsidy_amount=1000.0,
    )


def test_set_energy_costs_refreshes_no_subsidy_snapshot():
    """The yearly price refresh must not leave a stale pre-subsidy snapshot behind."""
    fg = _make_fg("fg1", "DRI", "coal")
    fg.set_subsidised_energy_costs(
        input_costs={"coke": 200.0},
        output_costs={"coke": 200.0},
        no_subsidy_prices={"coke": 300.0},
        energy_subsidies={"coke": []},
    )
    assert fg.energy_costs_no_subsidy["coke"] == 300.0

    # Next year's refresh with new unsubsidised prices (subsidy has lapsed)
    fg.set_energy_costs(coke=250.0, hydrogen=5000.0)

    assert fg.energy_costs_no_subsidy["coke"] == 250.0
    assert fg.energy_costs_no_subsidy["hydrogen"] == 5000.0


def test_switch_candidate_priced_without_incumbent_subsidy():
    """Candidate BOMs in optimal_technology_name use unsubsidised prices plus the
    candidate's own energy subsidies — the incumbent's subsidy must not leak, and a
    hydrogen subsidy scoped to the DRI candidate must reach it."""
    fg = _make_fg("fg1", "BF", "coke")
    fg.technology = Technology(name="BF", product="iron")
    fg.bill_of_materials = {
        "materials": {"iron_ore": {"unit_cost": 100.0, "demand": 1.0}},
        "energy": {"coke": {"unit_cost": 300.0, "demand": 0.4}},
    }
    # Incumbent coke subsidy: coke $300 -> $200; hydrogen unsubsidised at $5000.
    fg.set_subsidised_energy_costs(
        input_costs={"coke": 200.0, "hydrogen": 5000.0},
        output_costs={"coke": 400.0, "hydrogen": 5000.0},
        no_subsidy_prices={"coke": 300.0, "hydrogen": 5000.0},
        energy_subsidies={"coke": []},
    )

    captured: dict[str, dict[str, float]] = {}

    def mock_get_bom(energy_costs, tech, _capacity, _reductant=None):
        captured[tech] = dict(energy_costs)
        return (
            {
                "materials": {"iron_ore": {"unit_cost": 100.0, "demand": 1.0}},
                "energy": {"hydrogen": {"unit_cost": energy_costs["hydrogen"], "demand": 0.05}},
            },
            0.9,
            "hydrogen",
            {"iron_ore": 1.0},
        )

    fg.optimal_technology_name(
        market_price_series={"steel": [500.0] * 30, "iron": [400.0] * 30},
        cost_of_debt_by_tech={"BF": 0.05, "DRI": 0.05},
        cost_of_equity_by_tech={"BF": 0.1, "DRI": 0.1},
        get_bom_from_avg_boms=mock_get_bom,
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
        tech_energy_subsidies={"hydrogen": {"DRI": [_hydrogen_subsidy("DRI")]}},
    )

    assert captured["DRI"]["coke"] == 300.0  # incumbent coke subsidy did not leak
    assert captured["DRI"]["hydrogen"] == 4000.0  # candidate hydrogen subsidy applied


def test_expansion_candidate_priced_without_incumbent_subsidy():
    """evaluate_expansion_options prices candidates from unsubsidised carrier prices
    and applies energy subsidies scoped to each candidate technology."""
    fg = _make_fg("fg1", "BF", "coke")
    fg.set_subsidised_energy_costs(
        input_costs={"coke": 200.0, "hydrogen": 5000.0, "electricity": 0.05},
        output_costs={"coke": 400.0, "hydrogen": 5000.0, "electricity": 0.05},
        no_subsidy_prices={"coke": 300.0, "hydrogen": 5000.0, "electricity": 0.05},
        energy_subsidies={"coke": []},
    )
    plant = _make_dri_plant(fg)
    pg = PlantGroup(plant_group_id="pg1", plants=[plant])
    pg.balance = 1e12

    captured: dict[str, dict[str, float]] = {}

    def mock_get_bom(energy_costs, tech, _capacity, _reductant=None):
        captured[tech] = dict(energy_costs)
        return (
            {
                "materials": {"iron_ore": {"unit_cost": 100.0, "demand": 1.0}},
                "energy": {"electricity": {"unit_cost": 50.0, "demand": 0.5}},
            },
            0.9,
            "hydrogen",
            {"iron_ore": 1.0},
        )

    pg.evaluate_expansion_options(
        price_series={"steel": [500.0] * 30, "iron": [400.0] * 30},
        capacity=Volumes(1000.0),
        region_capex={"Region": {"DRI": 500.0, "EAF": 400.0}},
        cost_of_debt_dict={"NZL": {"DRI": 0.05, "EAF": 0.05}},
        cost_of_equity_dict={"NZL": {"DRI": 0.1, "EAF": 0.1}},
        get_bom_from_avg_boms=mock_get_bom,
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
        energy_subsidies={"hydrogen": {"NZL": {"DRI": [_hydrogen_subsidy("DRI")]}}},
    )

    # DRI candidate: incumbent coke subsidy did not leak; its hydrogen subsidy applied.
    assert captured["DRI"]["coke"] == 300.0
    assert captured["DRI"]["hydrogen"] == 4000.0
    # EAF candidate has no energy subsidy: pure unsubsidised prices.
    assert captured["EAF"]["coke"] == 300.0
    assert captured["EAF"]["hydrogen"] == 5000.0


# ── B5: secondary-output adjustment helper equivalence ───────────────────────


def _secondary_output_fixture():
    """Two-material fixture exercising revenue, disposal-cost, and carbon outputs."""
    dbc_ore = PrimaryFeedstock(metallic_charge="iron_ore", reductant="coal", technology="DRI")
    dbc_ore.required_quantity_per_ton_of_product = 1.5
    dbc_ore.outputs = {"bf_gas": 0.8, "steelmaking_slag": 0.2}
    dbc_ore.carbon_outputs = {"co2_stored": 0.5}

    dbc_scrap = PrimaryFeedstock(metallic_charge="scrap", reductant="coal", technology="DRI")
    dbc_scrap.required_quantity_per_ton_of_product = 1.0
    dbc_scrap.outputs = {"bof_gas": 0.3}

    bill_of_materials = {
        "materials": {
            "iron_ore": {"demand": 3.0, "unit_cost": 100.0},
            "scrap": {"demand": 1.0, "unit_cost": 200.0},
        },
        "energy": {},
    }
    output_costs = {"bf_gas": 0.02, "bof_gas": 0.04, "steelmaking_slag": 5.0, "co2_stored": 30.0}
    disposal_cost_outputs = frozenset({"steelmaking_slag"})
    return [dbc_ore, dbc_scrap], bill_of_materials, output_costs, disposal_cost_outputs


def test_secondary_output_adjustment_unchanged_by_refactor():
    """Result of calculate_cost_adjustments_from_secondary_outputs on a mixed fixture.

    Hand-computed against the pre-refactor implementation:
    - iron_ore: product_volume = 3.0/1.5 = 2.0;
      per tonne: -|0.02|*0.8 + 5.0*0.2 (disposal) + 30.0*0.5 (carbon) = 15.984
    - scrap: product_volume = 1.0; per tonne: -|0.04|*0.3 = -0.012
    - total = (2.0*15.984 - 0.012) / 3.0 = 10.652
    """
    import pytest
    from steelo.domain import calculate_costs

    dbcs, bom, output_costs, disposal = _secondary_output_fixture()
    result = calculate_costs.calculate_cost_adjustments_from_secondary_outputs(
        bill_of_materials=bom,
        dynamic_business_cases=dbcs,
        output_costs=output_costs,
        disposal_cost_outputs=disposal,
    )
    assert result == pytest.approx(10.652, rel=1e-12)


def test_secondary_output_adjustment_per_tonne_matches_per_material_maths():
    """The helper reproduces the pre-refactor per-material pricing rule."""
    import pytest
    from steelo.domain import calculate_costs

    dbcs, _bom, output_costs, disposal = _secondary_output_fixture()
    dbc_ore, dbc_scrap = dbcs
    assert calculate_costs.calculate_secondary_output_adjustment_per_tonne(
        dbc_ore, output_costs, disposal
    ) == pytest.approx(15.984, rel=1e-12)
    assert calculate_costs.calculate_secondary_output_adjustment_per_tonne(
        dbc_scrap, output_costs, disposal
    ) == pytest.approx(-0.012, rel=1e-12)


# ── B5: total-cost-aware annual re-pick ──────────────────────────────────────


def _make_scoring_fg(energy_costs: dict[str, float]) -> FurnaceGroup:
    """DRI furnace group with coal and natural_gas business cases attached."""
    fg = _make_fg("fg1", "DRI", "")
    fg.technology.dynamic_business_case = _dri_feedstocks()
    fg.set_energy_costs(**energy_costs)
    return fg


def _dri_emission_factors():
    from steelo.domain.models import TechnologyEmissionFactors

    def _ef(reductant: str, direct: float, boundary: str = "plant_boundary") -> TechnologyEmissionFactors:
        return TechnologyEmissionFactors(
            business_case=f"DRI_iron_ore_{reductant}",
            technology="DRI",
            boundary=boundary,
            metallic_charge="iron_ore",
            reductant=reductant,
            direct_ghg_factor=direct,
            direct_with_biomass_ghg_factor=direct,
            indirect_ghg_factor=0.0,
        )

    # Coal-DRI is far dirtier than gas-DRI; add another boundary to check filtering.
    return [
        _ef("coal", 2.0),
        _ef("natural_gas", 0.5),
        _ef("coal", 99.0, boundary="supply_chain"),
        _ef("natural_gas", 0.0, boundary="supply_chain"),
    ]


def test_reductant_score_carbon_price_flips_pick():
    """Energy-only pick is coal; with an EF gap and a sufficient carbon price the
    total-cost pick flips to natural_gas. Reporting dicts stay pure energy VOPEX."""
    # Coal energy VOPEX 10, gas 20: energy-only picks coal.
    fg = _make_scoring_fg({"coal": 1.0, "natural_gas": 2.0})
    assert fg.generate_energy_vopex_by_reductant() == {"iron_ore": 10.0}
    assert fg.chosen_reductant == "coal"

    # Carbon: coal 2.0 tCO2/t x $10 = $20; gas 0.5 x $10 = $5. Totals: 30 vs 25 -> gas.
    result = fg.generate_energy_vopex_by_reductant(
        carbon_price=10.0,
        technology_emission_factors=_dri_emission_factors(),
        chosen_emissions_boundary="plant_boundary",
    )
    assert fg.chosen_reductant == "natural_gas"
    # Reporting stays pure energy VOPEX for the chosen reductant.
    assert result == {"iron_ore": 20.0}
    assert fg.energy_vopex_by_input == {"iron_ore": 20.0}
    assert fg.energy_vopex_by_carrier == {"natural_gas": 20.0}

    # An insufficient carbon price keeps coal (also proves the supply_chain EFs are ignored).
    fg.generate_energy_vopex_by_reductant(
        carbon_price=5.0,
        technology_emission_factors=_dri_emission_factors(),
        chosen_emissions_boundary="plant_boundary",
    )
    assert fg.chosen_reductant == "coal"


def test_reductant_score_by_product_credit_flips_pick():
    """A by-product credit on the gas business case flips the pick without carbon."""
    fg = _make_scoring_fg({"coal": 1.0, "natural_gas": 1.2, "bf_gas": 15.0})
    gas_dbc = next(d for d in fg.technology.dynamic_business_case if d.reductant == "natural_gas")
    gas_dbc.outputs = {"bf_gas": 0.2}  # revenue: -15 x 0.2 = -3/t

    # Energy-only: coal 10 vs gas 12 -> coal.
    fg.generate_energy_vopex_by_reductant()
    assert fg.chosen_reductant == "coal"

    # Zero carbon price: only the by-product credit enters (12 - 3 = 9 < 10).
    fg.generate_energy_vopex_by_reductant(
        carbon_price=0.0,
        technology_emission_factors=_dri_emission_factors(),
        chosen_emissions_boundary="plant_boundary",
    )
    assert fg.chosen_reductant == "natural_gas"


def test_reductant_score_missing_ef_fails_loudly():
    """Scoring mode with incomplete EFs raises instead of silently scoring carbon as 0."""
    import pytest

    fg = _make_scoring_fg({"coal": 1.0, "natural_gas": 2.0})
    with pytest.raises(KeyError):
        fg.generate_energy_vopex_by_reductant(
            carbon_price=10.0,
            technology_emission_factors=[],
            chosen_emissions_boundary="plant_boundary",
        )


def test_reductant_score_defaults_reproduce_legacy_choice():
    """Calling with defaults gives the energy-only pick and identical reporting."""
    fg_legacy = _make_scoring_fg({"coal": 1.0, "natural_gas": 2.0})
    fg_default = _make_scoring_fg({"coal": 1.0, "natural_gas": 2.0})

    legacy = fg_legacy.generate_energy_vopex_by_reductant()
    default = fg_default.generate_energy_vopex_by_reductant(
        carbon_price=0.0, technology_emission_factors=None, chosen_emissions_boundary=""
    )
    assert legacy == default
    assert fg_legacy.chosen_reductant == fg_default.chosen_reductant == "coal"
    assert fg_legacy.energy_vopex_breakdown_by_input == fg_default.energy_vopex_breakdown_by_input


def test_reductant_score_material_drift_warning():
    """A cross-reductant material difference triggers the data-drift warning."""
    import logging

    fg = _make_scoring_fg({"coal": 1.0, "natural_gas": 2.0})
    gas_dbc = next(d for d in fg.technology.dynamic_business_case if d.reductant == "natural_gas")
    gas_dbc.required_quantity_per_ton_of_product = 1.6  # differs from coal's 1.5

    # Attach a handler directly to the function logger — caplog relies on propagation,
    # which the project logging config (installed by other tests) can disable.
    records: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("steelo.domain.models.generate_energy_vopex_by_reductant")
    collector = _Collector(level=logging.WARNING)
    logger.addHandler(collector)
    try:
        fg.generate_energy_vopex_by_reductant()
    finally:
        logger.removeHandler(collector)

    assert any("differ across" in record.getMessage() for record in records)
