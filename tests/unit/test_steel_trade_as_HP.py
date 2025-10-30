from steelo.domain import Year
from steelo.domain.trade_modelling.hitchcock_modelling import steel_trade_HP


class BillsOfMaterial:
    BF = {
        "materials": {
            "Iron Ore": {"demand": 2, "unit_cost": 1.5},
            "Scrap": {"demand": 0.2, "unit_cost": 3.0},
        },
        "energy": {
            "Electricity": {"demand": -1.0, "unit_cost": 0.75},
            "Hydrogen": {"demand": 0.0, "unit_cost": 2.5},  # 5 times electricity
            "Coal": {"demand": 10.0, "unit_cost": 0.5},
            "Gas": {"demand": 5.0, "unit_cost": 0.5},
        },
    }


def test_set_up_HP_steel_trading(
    repository_for_trade, plant, second_plant, furnace_group, second_furnace_group, demand_center
):
    for p in repository_for_trade.plants.list():
        for fg in p.furnace_groups:
            fg.bill_of_materials = getattr(BillsOfMaterial, fg.technology.name.replace("-", "_"))

    # Call your function to get the actual allocation
    active_statuses = ["operating", "operating pre-retirement"]
    allocation = steel_trade_HP(repository_for_trade, Year(2023), active_statuses)

    # Check that we have at least one allocation
    assert len(allocation.allocations) > 0

    # Check that the total allocated amount matches the total demand
    total_allocated = sum(allocation.allocations.values())
    total_demand = sum(dc.demand_by_year[Year(2023)] for dc in repository_for_trade.demand_centers.list())
    assert total_allocated == total_demand
