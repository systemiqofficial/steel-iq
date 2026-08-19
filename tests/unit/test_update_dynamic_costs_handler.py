"""Tests that the UpdateDynamicCosts handler applies every field of the command.

The yearly business-opportunity refresh emits UpdateDynamicCosts commands; the handler
must land all of them on the furnace group, including the expected utilisation added
alongside the cost fields.
"""

from unittest.mock import MagicMock

from steelo.devdata import get_furnace_group, get_plant
from steelo.domain.commands import UpdateDynamicCosts
from steelo.service_layer.handlers import update_dynamic_costs


class _FakeUnitOfWork:
    def __init__(self, plant):
        self.plants = MagicMock()
        self.plants.get.return_value = plant
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def commit(self):
        self.committed = True


def test_handler_applies_all_command_fields_to_furnace_group():
    """Costs, energy dicts and utilisation all land on the targeted furnace group."""
    fg = get_furnace_group(fg_id="fg_dyn", tech_name="EAF", capacity=100, utilization_rate=0.7)
    plant = get_plant(plant_id="plant_dyn", furnace_groups=[fg])
    uow = _FakeUnitOfWork(plant)

    cmd = UpdateDynamicCosts(
        plant_id="plant_dyn",
        furnace_group_id="fg_dyn",
        new_cost_of_debt=0.04,
        new_cost_of_debt_no_subsidy=0.05,
        new_capex=900.0,
        new_capex_no_subsidy=1000.0,
        new_energy_costs={"electricity": 45.0},
        new_output_energy_costs={"electricity": 46.0},
        new_energy_costs_no_subsidy={"electricity": 50.0},
        new_utilization_rate=0.85,
    )

    update_dynamic_costs(cmd, uow, env=MagicMock())

    assert fg.cost_of_debt == 0.04
    assert fg.cost_of_debt_no_subsidy == 0.05
    assert fg.technology.capex == 900.0
    assert fg.technology.capex_no_subsidy == 1000.0
    assert fg.energy_costs == {"electricity": 45.0}
    assert fg.output_energy_costs == {"electricity": 46.0}
    assert fg.energy_costs_no_subsidy == {"electricity": 50.0}
    assert fg.utilization_rate == 0.85
    assert uow.committed
