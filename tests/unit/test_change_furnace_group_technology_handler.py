import pytest
from unittest.mock import MagicMock

from steelo.domain.commands import ChangeFurnaceGroupTechnology
from steelo.service_layer.handlers import change_furnace_group_technology


class _FakePlantsRepo:
    def __init__(self, plant):
        self._plant = plant

    def get(self, plant_id):
        assert plant_id == self._plant.plant_id
        return self._plant


class _FakeUnitOfWork:
    def __init__(self, plant):
        self.plants = _FakePlantsRepo(plant)
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def commit(self):
        self.committed = True


def _make_command(bom: dict) -> ChangeFurnaceGroupTechnology:
    return ChangeFurnaceGroupTechnology(
        plant_id="plant-1",
        furnace_group_id="fg-1",
        technology_name="DRI+ESF+CCS",
        old_technology_name="SR+CCS",
        npv=1.0,
        cosa=1.0,
        utilisation=0.7,
        capex=100.0,
        capex_no_subsidy=100.0,
        capacity=1000.0,
        remaining_lifetime=20,
        bom=bom,
        cost_of_debt=0.05,
        cost_of_debt_no_subsidy=0.05,
        capex_subsidies=[],
        debt_subsidies=[],
    )


def test_change_handler_rejects_empty_bom():
    plant = MagicMock()
    plant.plant_id = "plant-1"
    uow = _FakeUnitOfWork(plant)
    env = MagicMock()
    env.config.plant_lifetime = 20
    env.dynamic_feedstocks = {}

    cmd = _make_command(bom={"materials": {}, "energy": {}})

    with pytest.raises(ValueError):
        change_furnace_group_technology(cmd, uow=uow, env=env)

    plant.change_furnace_group_technology.assert_not_called()
    assert uow.committed is False


def test_change_handler_accepts_non_empty_bom():
    plant = MagicMock()
    plant.plant_id = "plant-1"
    uow = _FakeUnitOfWork(plant)
    env = MagicMock()
    env.config.plant_lifetime = 20
    env.dynamic_feedstocks = {}

    cmd = _make_command(
        bom={
            "materials": {"io_low": {"demand": 1.0, "total_cost": 1.0, "unit_cost": 1.0, "product_volume": 1.0}},
            "energy": {},
        }
    )

    change_furnace_group_technology(cmd, uow=uow, env=env)

    plant.change_furnace_group_technology.assert_called_once()
    assert uow.committed is True
