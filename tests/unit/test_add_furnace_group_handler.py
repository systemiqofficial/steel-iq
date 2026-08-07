"""Tests that expansion furnace groups are financed on the command's equity share.

The AddFurnaceGroup handler must pass the evaluated equity_share through to the new
furnace group; previously it was dropped, leaving the FurnaceGroup default (0.2) while
the treasury was debited on the config value.
"""

from unittest.mock import MagicMock

from steelo.devdata import get_furnace_group, get_plant
from steelo.domain import Volumes
from steelo.domain.commands import AddFurnaceGroup
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
