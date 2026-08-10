"""Regression lock: the announced -> construction transition does not debit.

Before, the ``update_status_of_furnace_group`` handler subtracted
``env.config.equity_share * capex`` from the FG balance. That debit was dead (the sweep
immediately resets fg.balance) and semantically misguided — new-plant construction is
financed externally, outside the simulated treasury. This test locks the removal in: any
future edit that re-introduces the debit will fail here.
"""

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

from steelo.domain import Year, TimeFrame, PointInTime, Volumes
from steelo.domain import commands, events
from steelo.domain.models import (
    FurnaceGroup,
    Location,
    Plant,
    PlantGroup,
    ProductCategory,
    Technology,
)
from steelo.service_layer.handlers import update_status_of_furnace_group


def _make_fg(fg_id: str, capex: float = 500.0) -> FurnaceGroup:
    return FurnaceGroup(
        furnace_group_id=fg_id,
        capacity=Volumes(1_000_000.0),
        status="announced",
        last_renovation_date=date(2020, 1, 1),
        technology=Technology(name="EAF", product="steel", capex=capex),
        historical_production={},
        utilization_rate=0.0,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2030), end=Year(2060)),
            plant_lifetime=20,
        ),
    )


def _make_plant(fg: FurnaceGroup) -> Plant:
    return Plant(
        plant_id="plant-new-1",
        location=Location(lat=0.0, lon=0.0, country="Germany", region="Europe", iso3="DEU"),
        furnace_groups=[fg],
        power_source="grid",
        soe_status="private",
        parent_gem_id="indi_DEU",
        workforce_size=50,
        certified=False,
        category_steel_product={ProductCategory("Flat")},
        technology_unit_fopex={"eaf": 50.0},
    )


def _make_env(equity_share: float = 0.2) -> SimpleNamespace:
    config = SimpleNamespace(
        equity_share=equity_share,
        construction_time=2,
        plant_lifetime=20,
        co2_storage_reserved_discount_factor=1.0,
    )
    return SimpleNamespace(
        year=Year(2025),
        config=config,
        get_co2_need=lambda tech, capacity, reductant: 0.0,
        co2_storage_firm={},
        co2_storage_reserved={},
    )


def _make_uow(plant: Plant, pg: PlantGroup):
    plants_repo = MagicMock()
    plants_repo.get.return_value = plant
    uow = MagicMock()
    uow.__enter__.return_value = uow
    uow.__exit__.return_value = False
    uow.plants = plants_repo
    uow.plant_groups = MagicMock()
    uow.plant_groups.list.return_value = [pg]
    return uow


def test_announced_to_construction_transition_does_not_debit_group_balance():
    """Construction transition: pg.balance is unchanged; fg.balance is unchanged."""
    fg = _make_fg("fg-1", capex=500.0)
    plant = _make_plant(fg)
    pg = PlantGroup(plant_group_id="indi_DEU", plants=[plant])
    pg.balance = 1_000_000.0
    balance_before = pg.balance
    fg_balance_before = fg.balance

    env = _make_env(equity_share=0.2)
    uow = _make_uow(plant, pg)

    cmd = commands.UpdateFurnaceGroupStatus(
        plant_id=plant.plant_id, fg_id=fg.furnace_group_id, new_status="construction"
    )
    update_status_of_furnace_group(cmd, uow, env)

    # No debit to the group treasury — construction is externally financed.
    assert pg.balance == balance_before
    # No debit to fg.balance either — the old dead debit targeted fg.balance and was deleted.
    assert fg.balance == fg_balance_before
    # Status did transition.
    assert fg.status == "construction"


# Silence unused-import warnings while keeping the import available if tests grow.
_ = events
