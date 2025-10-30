# Blablabla fixture

import pytest

from steelo.devdata import get_furnace_group
from steelo.domain import Year, Volumes
from steelo.domain.models import PointInTime, TimeFrame


### Global set of variables
@pytest.fixture
def technology_consumption_dict():
    return {
        "BFBOF": {
            "materials_demand_cost": {
                "Iron Ore": {"demand": 2, "cost_per_unit": 1.5},
                "Scrap": {"demand": 0.2, "cost_per_unit": 3.0},
            },
            "energy_demand_cost": {
                "Electricity": {"demand": -1.0, "cost_per_unit": 0.75},
                "Hydrogen": {"demand": 0.0, "cost_per_unit": 2.5},  # 5 times electricity
                "Coal": {"demand": 10.0, "cost_per_unit": 0.5},
                "Gas": {"demand": 5.0, "cost_per_unit": 0.5},
            },
        },
        "DRI-EAF": {
            "materials_demand_cost": {
                "Iron Ore": {"demand": 2, "cost_per_unit": 1.5},
                "Scrap": {"demand": 0.2, "cost_per_unit": 3.0},
            },
            "energy_demand_cost": {
                "Electricity": {"demand": 6.0, "cost_per_unit": 0.75},
                "Hydrogen": {"demand": 5.0, "cost_per_unit": 2.5},
                "Coal": {"demand": 0.0, "cost_per_unit": 0.5},
            },
        },
        "EAF": {
            "materials_demand_cost": {
                "Iron": {"demand": 0.2, "cost_per_unit": 2.5},
                "Scrap": {"demand": 1.2, "cost_per_unit": 3.0},
            },
            "energy_demand_cost": {
                "Electricity": {"demand": 6.0, "cost_per_unit": 0.75},
                "Hydrogen": {"demand": 0.0, "cost_per_unit": 2.5},
                "Coal": {"demand": 0.0, "cost_per_unit": 0.5},
            },
        },
    }


capex_switch_dict = {
    # "BFBOF": {"EAF": 10, "DRI-EAF": 13, "BFBOF": 4},
    # "DRI-EAF": {"EAF": 4, "DRI-EAF": 5.5, "BFBOF": 8},
    "EAF": {"EAF": 0, "DRI-EAF": 0.5, "BFBOF": 1},
}


@pytest.fixture
def Fopex():
    return 1


@pytest.fixture
def equity_share():
    return 0.2


@pytest.fixture
def lifetime():
    return 20


@pytest.fixture
def cost_of_debt():
    return 0.05


@pytest.fixture
def furnace_group(technology_consumption_dict):
    fg = get_furnace_group(
        fg_id="Plant_0_0",
        tech_name="EAF",
        capacity=Volumes(100),
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2011), end=Year(2045)),
            plant_lifetime=20,
        ),
    )
    return fg
