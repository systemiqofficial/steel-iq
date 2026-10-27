"""Tests that expansion furnace groups are financed on the command's equity share.

The AddFurnaceGroup handler must pass the evaluated equity_share through to the new
furnace group; previously it was dropped, leaving the FurnaceGroup default (0.2) while
the treasury was debited on the config value.
"""

import dataclasses
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from steelo.devdata import get_furnace_group, get_plant
from steelo.domain import Volumes, Year
from steelo.domain.commands import AddFurnaceGroup
from steelo.domain.models import CaptiveEnergy, Environment
from steelo.service_layer.handlers import add_furnace_group_to_plant


class _FakeUnitOfWork:
    def __init__(self, plant, plant_group):
        self.plants = MagicMock()
        self.plants.get.return_value = plant
        self.plant_groups = MagicMock()
        self.plant_groups.get_by_plant_id.return_value = plant_group
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def commit(self):
        self.committed = True


def make_command(equity_share: float) -> AddFurnaceGroup:
    return AddFurnaceGroup(
        furnace_group_id="plant-1_new_furnace",
        plant_id="plant-1",
        technology_name="EAF",
        capacity=100.0,
        product="steel",
        chosen_reductant="",
        equity_share=equity_share,
        equity_needed=400.0 * 100.0 * equity_share,
        npv=1_000.0,
        capex=400.0,
        capex_no_subsidy=400.0,
        cost_of_debt=0.05,
        cost_of_debt_no_subsidy=0.05,
        capex_subsidies=[],
        debt_subsidies=[],
        captive_energy=None,
    )


def test_handler_passes_command_equity_share_to_new_furnace():
    """The new furnace group is created with the command's equity share, not the default."""
    plant = MagicMock()
    plant.plant_id = "plant-1"
    plant.added_capacity = Volumes(0)
    plant_group = MagicMock()
    uow = _FakeUnitOfWork(plant, plant_group)
    env = MagicMock()
    env.config.construction_time = 4
    env.config.plant_lifetime = 20
    env.dynamic_feedstocks = {}
    cmd = make_command(equity_share=0.3)

    add_furnace_group_to_plant(cmd, uow=uow, env=env)

    assert plant.generate_new_furnace.call_args.kwargs["equity_share"] == 0.3
    plant_group.deduct_equity.assert_called_once_with(cmd.equity_needed, reason="expansion")
    assert uow.committed is True


def test_generate_new_furnace_stores_equity_share():
    """The factory forwards equity_share into the FurnaceGroup it builds."""
    plant = get_plant(
        plant_id="plant_equity_test",
        furnace_groups=[get_furnace_group(fg_id="plant_equity_test_1")],
    )

    furnace_group = plant.generate_new_furnace(
        technology_name="EAF",
        product="steel",
        current_year=2025,
        capex=400.0,
        capex_no_subsidy=400.0,
        cost_of_debt=0.05,
        cost_of_debt_no_subsidy=0.05,
        capacity=100.0,
        lag=4,
        status="construction",
        util_rate=0.0,
        plant_lifetime=20,
        chosen_reductant="",
        equity_share=0.3,
    )

    assert furnace_group.equity_share == 0.3


def make_site_plant(parent_gem_id: str):
    """
    A plant whose furnace group pays 3,000 USD/t hydrogen less a 500 USD/t subsidy, and gas less a subsidy.

    Args:
        parent_gem_id: ``indi_DEU`` for a new site, a company ID for an existing plant.

    Returns:
        The plant: 2,500 USD/t hydrogen and 0.02 USD/kWh gas paid, 3,000 and 0.03 without subsidy.
    """
    fg = get_furnace_group(fg_id="plant_site_1")
    fg.set_subsidised_energy_costs(
        input_costs={"electricity": 0.05, "hydrogen": 2500.0, "natural_gas": 0.02},
        output_costs={"electricity": 0.05, "hydrogen": 2500.0, "natural_gas": 0.02},
        no_subsidy_prices={"electricity": 0.05, "hydrogen": 3000.0, "natural_gas": 0.03},
        energy_subsidies={"hydrogen": [], "natural_gas": []},
    )
    plant = get_plant(plant_id="plant_site", furnace_groups=[fg])
    plant.parent_gem_id = parent_gem_id
    return plant


def expansion_environment() -> Environment:
    """An environment holding what the expansion handler reads in 2040, with DEU's grid at 0.09 USD/kWh."""
    env = object.__new__(Environment)
    env.year = Year(2040)
    env.config = SimpleNamespace(construction_time=4, plant_lifetime=20, disposal_cost_outputs=frozenset())
    env.dynamic_feedstocks = {}
    env.input_costs = {"DEU": {Year(2040): {"electricity": 0.09, "natural_gas": 0.04}}}
    env.get_bom_from_avg_boms = lambda *args: (None, 0.0, "", {})
    return env


EXPANSION_STACK = CaptiveEnergy(Year(2040), 0.85, 0.036, 0.046, 0.9, None)


def test_expansion_at_a_new_site_locks_its_own_stack_with_no_subsidy_in_its_base_price():
    """
    The new group of an expansion in 2040 holds the stack of 2040, and its prices without subsidy are the stack's on
    the year's grid, not its sibling's subsidised 2,500 USD/t hydrogen; its other carriers carry no subsidy either.
    """
    plant = make_site_plant("indi_DEU")
    cmd = dataclasses.replace(make_command(equity_share=0.2), plant_id="plant_site", captive_energy=EXPANSION_STACK)

    add_furnace_group_to_plant(cmd, uow=_FakeUnitOfWork(plant, MagicMock()), env=expansion_environment())

    new = plant.furnace_groups[-1]
    electricity = 0.85 * 0.036 + 0.15 * 0.09
    assert new.captive_energy is EXPANSION_STACK
    assert new.captive_energy.lock_year == 2040
    assert new.energy_costs_no_subsidy["electricity"] == pytest.approx(electricity)
    assert new.energy_costs_no_subsidy["hydrogen"] == pytest.approx((0.046 * 1000 * electricity + 0.9) * 1000)
    assert new.energy_costs_no_subsidy["natural_gas"] == 0.03
    assert new.energy_costs == new.energy_costs_no_subsidy


def test_expansion_at_an_existing_plant_keeps_taking_the_plants_prices_and_no_stack():
    """An existing plant's new group holds no stack and starts from the plant's prices, as before."""
    plant = make_site_plant("E100000000001")
    cmd = dataclasses.replace(make_command(equity_share=0.2), plant_id="plant_site")

    add_furnace_group_to_plant(cmd, uow=_FakeUnitOfWork(plant, MagicMock()), env=expansion_environment())

    new = plant.furnace_groups[-1]
    assert new.captive_energy is None
    assert new.energy_costs["hydrogen"] == 2500.0
