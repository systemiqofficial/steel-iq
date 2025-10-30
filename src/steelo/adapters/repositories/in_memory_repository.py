from typing import Iterable

from .interface import (
    PlantRepository,
    FurnaceGroupRepository,
    DemandCenterRepository,
    PlantGroupRepository,
    SupplierRepository,
    TradeTariffRepository,
)
from ...domain import Plant, FurnaceGroup, DemandCenter, PlantGroup, Supplier, TradeTariff


class PlantInMemoryRepository:
    def __init__(self) -> None:
        self.data: dict[str, Plant] = {}
        self.furnace_id_to_plant_id: dict[str, str] = {}
        self.seen: set[Plant] = set()

    def add(self, plant: Plant) -> None:
        self.data[plant.plant_id] = plant
        for furnace_group in plant.furnace_groups:
            self.furnace_id_to_plant_id[furnace_group.furnace_group_id] = plant.plant_id
        self.seen.add(plant)

    def add_list(self, plants: Iterable[Plant]) -> None:
        for plant in plants:
            self.add(plant)

    def get(self, plant_id) -> Plant:
        return self.data[plant_id]

    def list(self) -> list[Plant]:
        return list(self.data.values())

    def get_by_furnace_group_id(self, furnace_group_id: str) -> Plant:
        plant_id = self.furnace_id_to_plant_id[furnace_group_id]
        return self.data[plant_id]


class PlantGroupInMemoryRepository:
    def __init__(self) -> None:
        self.data: dict[str, PlantGroup] = {}
        self.plant_id_to_plantgroup_id: dict[str, str] = {}
        self.seen: set[PlantGroup] = set()

    def add(self, plantgroup: PlantGroup) -> None:
        self.data[plantgroup.plant_group_id] = plantgroup
        self.seen.add(plantgroup)
        # Map plant IDs to plant group ID
        for plant in plantgroup.plants:
            self.plant_id_to_plantgroup_id[plant.plant_id] = plantgroup.plant_group_id

    def add_list(self, plantgroups: Iterable[PlantGroup]) -> None:
        for plant_group in plantgroups:
            self.add(plant_group)

    def get(self, plant_group_id) -> PlantGroup:
        return self.data[plant_group_id]

    def list(self) -> list[PlantGroup]:
        return list(self.data.values())

    def get_by_plant_id(self, plant_id: str) -> PlantGroup:
        """Get a plant group from the repository by plant ID."""
        if plant_id not in self.plant_id_to_plantgroup_id:
            raise ValueError(f"No plant group found for plant ID: {plant_id}")
        plant_group_id = self.plant_id_to_plantgroup_id[plant_id]
        return self.data[plant_group_id]


class FurnaceGroupInMemoryRepository:
    def __init__(self) -> None:
        self.data: dict[str, FurnaceGroup] = {}

    def add(self, furnace_group: FurnaceGroup) -> None:
        self.data[furnace_group.furnace_group_id] = furnace_group

    def add_list(self, furnace_groups: Iterable[FurnaceGroup]) -> None:
        for furnace_group in furnace_groups:
            self.add(furnace_group)

    def get(self, furnace_group_id) -> FurnaceGroup:
        return self.data[furnace_group_id]

    def list(self) -> list[FurnaceGroup]:
        return list(self.data.values())


class DemandCenterInMemoryRepository:
    def __init__(self) -> None:
        self.data: dict[str, DemandCenter] = {}

    def add(self, demand_center: DemandCenter) -> None:
        self.data[demand_center.demand_center_id] = demand_center

    def add_list(self, demand_centers: Iterable[DemandCenter]) -> None:
        for demand_center in demand_centers:
            self.add(demand_center)

    def get(self, demand_center_id) -> DemandCenter:
        return self.data[demand_center_id]

    def list(self) -> list[DemandCenter]:
        return list(self.data.values())


class SupplierInMemoryRepository:
    def __init__(self) -> None:
        self.data: dict[str, Supplier] = {}

    def add(self, supplier: Supplier) -> None:
        self.data[supplier.supplier_id] = supplier

    def add_list(self, suppliers: Iterable[Supplier]) -> None:
        for supplier in suppliers:
            self.add(supplier)

    def get(self, supplier_id) -> Supplier:
        return self.data[supplier_id]

    def list(self) -> list[Supplier]:
        return list(self.data.values())


class TradeTariffInMemoryRepository:
    def __init__(self) -> None:
        self.data: dict[str, TradeTariff] = {}

    def add(self, trade_tariff: TradeTariff) -> None:
        self.data[trade_tariff.tariff_id] = trade_tariff

    def add_list(self, trade_tariffs: Iterable[TradeTariff]) -> None:
        for trade_tariff in trade_tariffs:
            self.add(trade_tariff)

    def get(self, tariff_id: str) -> TradeTariff:
        return self.data[tariff_id]

    def list(self) -> list[TradeTariff]:
        return list(self.data.values())


class InMemoryRepository:
    plant_groups: PlantGroupRepository
    plants: PlantRepository
    furnace_groups: FurnaceGroupRepository
    demand_centers: DemandCenterRepository
    suppliers: SupplierRepository
    trade_tariffs: TradeTariffRepository

    def __init__(self) -> None:
        self.plants = PlantInMemoryRepository()
        self.furnace_groups = FurnaceGroupInMemoryRepository()
        self.demand_centers = DemandCenterInMemoryRepository()
        self.plant_groups = PlantGroupInMemoryRepository()
        self.suppliers = SupplierInMemoryRepository()
        self.trade_tariffs = TradeTariffInMemoryRepository()
