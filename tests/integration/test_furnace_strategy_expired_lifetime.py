"""Tests that an expired furnace-group lifetime always resolves to renovate, switch, or close.

``lifetime.expired`` is true only in the renovation-boundary year, so a no-action
outcome would roll the furnace group into a fresh cycle without paying renovation
capex. These tests pin the fallback behaviour of every no-action exit in
Plant.evaluate_furnace_group_strategy for expired furnace groups.
"""

from unittest.mock import MagicMock

from steelo.devdata import get_furnace_group, get_plant
from steelo.domain import PointInTime, TimeFrame, Volumes, Year
from steelo.domain.commands import CloseFurnaceGroup, RenovateFurnaceGroup
from steelo.domain.models import PlantGroup

REGION_CAPEX = {"EAF": 400.0, "DRI": 600.0}
RENOVATION_SHARE = {"EAF": 0.7, "DRI": 0.7}
ALLOWED_TECHS = {Year(year): ["EAF", "DRI"] for year in range(2020, 2031)}
TRANSITIONS = {"EAF": ["EAF", "DRI"]}

# With capacity=100 and the devdata default equity_share=0.2:
# renovate_cost (EAF) = 400 × 100 × 0.2 = 8,000
# switch_cost (DRI) = 600 × 100 × 0.2 = 12,000
RENOVATE_COST = 8_000.0
SWITCH_COST = 12_000.0


def make_plant_and_group(*, expired: bool, balance: float):
    """Build an EAF plant plus owning group; start year controls lifetime expiry in 2025."""
    fg = get_furnace_group(
        fg_id="fg_expired_test",
        utilization_rate=0.7,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2005) if expired else Year(2015), end=Year(2045)),
            plant_lifetime=20,
        ),
        capacity=100,
        tech_name="EAF",
    )
    plant = get_plant(furnace_groups=[fg], plant_id="plant_expired_test")
    plant.location.iso3 = "USA"
    plant.technology_unit_fopex = {"EAF": 50.0, "DRI": 70.0}
    plant.carbon_cost_series = [0.0] * 22
    plant_group = PlantGroup(plant_group_id="gem_expired_test", plants=[plant])
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
        ),
    )


def evaluate(plant, plant_group, *, probabilistic_agents: bool = False):
    """Call evaluate_furnace_group_strategy with a minimal fixed argument set."""
    furnace_group = plant.furnace_groups[0]
    return plant.evaluate_furnace_group_strategy(
        furnace_group_id=furnace_group.furnace_group_id,
        plant_group=plant_group,
        market_price_series={"steel": [600.0] * 22, "iron": [400.0] * 22},
        region_capex=REGION_CAPEX,
        capex_renovation_share=RENOVATION_SHARE,
        cost_of_debt=0.04,
        cost_of_equity=0.08,
        get_bom_from_avg_boms=MagicMock(),
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


def test_expired_fg_with_all_negative_npvs_closes(mocker):
    """All options value-destroying at the renovation boundary: the group closes.

    Previously Stage 5 returned None, letting the group roll into a fresh
    lifetime cycle without renovating or closing.
    """
    plant, plant_group = make_plant_and_group(expired=True, balance=1_000_000.0)
    mock_npvs(mocker, plant.furnace_groups[0], {"EAF": -1_000.0, "DRI": -5_000.0})

    command = evaluate(plant, plant_group)

    assert isinstance(command, CloseFurnaceGroup)
    assert command.furnace_group_id == "fg_expired_test"


def test_mid_life_fg_with_all_negative_npvs_takes_no_action(mocker):
    """Mid-life groups with all-negative NPVs keep operating on sunk capital (unchanged)."""
    plant, plant_group = make_plant_and_group(expired=False, balance=1_000_000.0)
    mock_npvs(mocker, plant.furnace_groups[0], {"EAF": -1_000.0, "DRI": -5_000.0})

    command = evaluate(plant, plant_group)

    assert command is None


def test_expired_fg_renovates_when_switch_unaffordable(mocker):
    """A blocked switch at the boundary falls back to renovating the viable incumbent."""
    plant, plant_group = make_plant_and_group(expired=True, balance=10_000.0)
    mock_npvs(mocker, plant.furnace_groups[0], {"EAF": 500.0, "DRI": 1_000_000.0})
    mocker.patch("steelo.domain.models.random.choices", return_value=["DRI"])

    command = evaluate(plant, plant_group)

    assert isinstance(command, RenovateFurnaceGroup)
    assert command.capex_no_subsidy == REGION_CAPEX["EAF"]
    assert plant_group.balance == 10_000.0 - RENOVATE_COST


def test_expired_fg_closes_when_neither_switch_nor_renovation_affordable(mocker):
    """No affordable action at the boundary: the group closes without debiting the treasury."""
    plant, plant_group = make_plant_and_group(expired=True, balance=5_000.0)
    mock_npvs(mocker, plant.furnace_groups[0], {"EAF": 500.0, "DRI": 1_000_000.0})
    mocker.patch("steelo.domain.models.random.choices", return_value=["DRI"])

    command = evaluate(plant, plant_group)

    assert isinstance(command, CloseFurnaceGroup)
    assert plant_group.balance == 5_000.0


def test_expired_fg_with_optimal_incumbent_renovates(mocker):
    """Stage 8 regression: an optimal incumbent at the boundary renovates as before."""
    plant, plant_group = make_plant_and_group(expired=True, balance=1_000_000.0)
    mock_npvs(mocker, plant.furnace_groups[0], {"EAF": 1_000_000.0, "DRI": 500.0})

    command = evaluate(plant, plant_group)

    assert isinstance(command, RenovateFurnaceGroup)
    assert command.capex == REGION_CAPEX["EAF"] / RENOVATION_SHARE["EAF"]
    assert command.capex_no_subsidy == REGION_CAPEX["EAF"]
    assert plant_group.balance == 1_000_000.0 - RENOVATE_COST


def test_expired_fg_renovates_after_probabilistic_rejection(mocker):
    """A probabilistically rejected switch at the boundary falls back to renovation."""
    plant, plant_group = make_plant_and_group(expired=True, balance=1_000_000.0)
    # accept_prob = exp(-switch_cost / NPV) = exp(-12) — the mocked draw of 0.5 rejects it
    mock_npvs(mocker, plant.furnace_groups[0], {"EAF": 500.0, "DRI": 1_000.0})
    mocker.patch("steelo.domain.models.random.choices", return_value=["DRI"])
    mocker.patch("steelo.domain.models.random.random", return_value=0.5)

    command = evaluate(plant, plant_group, probabilistic_agents=True)

    assert isinstance(command, RenovateFurnaceGroup)
    assert plant_group.balance == 1_000_000.0 - RENOVATE_COST
