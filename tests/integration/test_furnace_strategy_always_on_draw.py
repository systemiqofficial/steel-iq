"""Tests pinning the always-on Stage-7 draw in Plant.evaluate_furnace_group_strategy.

The NPV-weighted draw runs for every probabilistic evaluation, with the incumbent
competing on its own NPV — there is no argmax-incumbent short-circuit. Weights are
linear NPV-proportional with non-positive NPVs zeroed. Deterministic mode
(probabilistic_agents=False) still picks the max-NPV option without any draw.
"""

from unittest.mock import MagicMock

from steelo.devdata import get_furnace_group, get_plant
from steelo.domain import PointInTime, TimeFrame, Volumes, Year
from steelo.domain.commands import ChangeFurnaceGroupTechnology, RenovateFurnaceGroup
from steelo.domain.models import PlantGroup

REGION_CAPEX = {"EAF": 400.0, "DRI": 600.0}
RENOVATION_SHARE = {"EAF": 0.7, "DRI": 0.7}
ALLOWED_TECHS = {Year(year): ["EAF", "DRI"] for year in range(2020, 2031)}
TRANSITIONS = {"EAF": ["EAF", "DRI"]}

# With capacity=100 and the devdata default equity_share=0.2:
# renovate_cost (EAF) = 400 × 100 × 0.2 = 8,000
RENOVATE_COST = 8_000.0


def make_plant_and_group(*, expired: bool, balance: float):
    """Build an EAF plant plus owning group; start year controls lifetime expiry in 2025."""
    fg = get_furnace_group(
        fg_id="fg_draw_test",
        utilization_rate=0.7,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2005) if expired else Year(2015), end=Year(2045)),
            plant_lifetime=20,
        ),
        capacity=100,
        tech_name="EAF",
    )
    plant = get_plant(furnace_groups=[fg], plant_id="plant_draw_test")
    plant.location.iso3 = "USA"
    plant.technology_unit_fopex = {"EAF": 50.0, "DRI": 70.0}
    plant.carbon_cost_series = [0.0] * 22
    plant_group = PlantGroup(plant_group_id="gem_draw_test", plants=[plant])
    plant_group.balance = balance
    return plant, plant_group


def mock_npvs(mocker, furnace_group, tech_npv_dict):
    """Patch optimal_technology_name to return fixed NPVs with REGION_CAPEX-priced capex."""
    bom = {
        "materials": {"scrap": {"unit_cost": 200.0, "demand": 1.0}},
        "energy": {"electricity": {"unit_cost": 80.0, "demand": 0.5}},
    }
    mocker.patch.object(
        furnace_group,
        "optimal_technology_name",
        return_value=(
            tech_npv_dict,
            {tech: REGION_CAPEX[tech] for tech in tech_npv_dict},
            10_000.0,
            {tech: bom for tech in tech_npv_dict},
            {tech: "scrap" for tech in tech_npv_dict},
        ),
    )


def evaluate(plant, plant_group, *, probabilistic_agents: bool = True):
    """Call evaluate_furnace_group_strategy with a minimal fixed argument set."""
    furnace_group = plant.furnace_groups[0]
    return plant.evaluate_furnace_group_strategy(
        furnace_group_id=furnace_group.furnace_group_id,
        plant_group=plant_group,
        market_price_series={"steel": [600.0] * 22, "iron": [400.0] * 22},
        region_capex=REGION_CAPEX,
        capex_renovation_share=RENOVATION_SHARE,
        cost_of_debt_by_tech={"EAF": 0.04, "DRI": 0.04},
        cost_of_equity_by_tech={"EAF": 0.08, "DRI": 0.08},
        get_bom_from_avg_boms=MagicMock(),
        reductant_score_series=MagicMock(),
        probabilistic_agents=probabilistic_agents,
        dynamic_business_cases={"EAF": [], "DRI": []},
        chosen_emissions_boundary_for_carbon_costs="scope_1",
        technology_emission_factors=[],
        tech_to_product={"EAF": "steel", "DRI": "iron"},
        plant_lifetime=20,
        construction_time=2,
        current_year=Year(2025),
        allowed_techs=ALLOWED_TECHS,
        risk_free_rate=0.02,
        allowed_furnace_transitions=TRANSITIONS,
        capacity_limit_steel=Volumes(10_000),
        capacity_limit_iron=Volumes(10_000),
        installed_capacity_in_year=lambda product: Volumes(1_000),
        new_plant_capacity_in_year=lambda product: Volumes(0),
    )


def test_draw_runs_when_incumbent_is_argmax_and_challenger_can_win(mocker):
    """The argmax incumbent no longer short-circuits the draw: a challenger drawn
    against a better incumbent switches.

    Previously an argmax incumbent went straight to renovate/no-action with zero
    probability mass on challengers.
    """
    plant, plant_group = make_plant_and_group(expired=False, balance=1_000_000.0)
    mock_npvs(mocker, plant.furnace_groups[0], {"EAF": 1_000_000.0, "DRI": 500_000.0})
    choices = mocker.patch("steelo.domain.models.random.choices", return_value=["DRI"])
    mocker.patch("steelo.domain.models.random.random", return_value=0.0)

    command = evaluate(plant, plant_group)

    assert isinstance(command, ChangeFurnaceGroupTechnology)
    assert command.technology_name == "DRI"
    assert command.old_technology_name == "EAF"
    choices.assert_called_once()
    assert choices.call_args.kwargs["population"] == ["EAF", "DRI"]
    assert choices.call_args.kwargs["weights"] == [1_000_000.0, 500_000.0]


def test_incumbent_winning_draw_mid_life_takes_no_action(mocker):
    """An incumbent that wins the draw mid-life results in no action, but the draw ran."""
    plant, plant_group = make_plant_and_group(expired=False, balance=1_000_000.0)
    mock_npvs(mocker, plant.furnace_groups[0], {"EAF": 1_000_000.0, "DRI": 500_000.0})
    choices = mocker.patch("steelo.domain.models.random.choices", return_value=["EAF"])

    command = evaluate(plant, plant_group)

    assert command is None
    choices.assert_called_once()


def test_incumbent_winning_draw_at_boundary_renovates(mocker):
    """An incumbent that wins the draw in its renovation-boundary year renovates."""
    plant, plant_group = make_plant_and_group(expired=True, balance=1_000_000.0)
    mock_npvs(mocker, plant.furnace_groups[0], {"EAF": 1_000_000.0, "DRI": 500_000.0})
    mocker.patch("steelo.domain.models.random.choices", return_value=["EAF"])

    command = evaluate(plant, plant_group)

    assert isinstance(command, RenovateFurnaceGroup)
    assert command.capex_no_subsidy == REGION_CAPEX["EAF"]
    assert plant_group.balance == 1_000_000.0 - RENOVATE_COST


def test_non_positive_npv_gets_zero_weight_in_draw(mocker):
    """Linear weighting zeroes non-positive NPVs: a loss-making incumbent has no
    probability mass, while positive options keep their raw NPV as weight."""
    plant, plant_group = make_plant_and_group(expired=False, balance=1_000_000.0)
    mock_npvs(mocker, plant.furnace_groups[0], {"EAF": -1_000.0, "DRI": 500_000.0})
    choices = mocker.patch("steelo.domain.models.random.choices", return_value=["DRI"])
    mocker.patch("steelo.domain.models.random.random", return_value=0.0)

    command = evaluate(plant, plant_group)

    assert isinstance(command, ChangeFurnaceGroupTechnology)
    assert choices.call_args.kwargs["weights"] == [0, 500_000.0]


def test_deterministic_mode_picks_argmax_without_a_draw(mocker):
    """probabilistic_agents=False is behaviourally unchanged: max-NPV incumbent
    renovates at the boundary and random.choices is never consulted."""
    plant, plant_group = make_plant_and_group(expired=True, balance=1_000_000.0)
    mock_npvs(mocker, plant.furnace_groups[0], {"EAF": 1_000_000.0, "DRI": 500_000.0})
    choices = mocker.patch("steelo.domain.models.random.choices")

    command = evaluate(plant, plant_group, probabilistic_agents=False)

    assert isinstance(command, RenovateFurnaceGroup)
    assert plant_group.balance == 1_000_000.0 - RENOVATE_COST
    choices.assert_not_called()
