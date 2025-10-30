from typing import Protocol, runtime_checkable, Iterable

from ...domain import (
    Plant,
    FurnaceGroup,
    DemandCenter,
    PlantGroup,
    Supplier,
    TradeTariff,
    RegionEmissivity,
    CarbonCostSeries,
    InputCosts,
    PrimaryFeedstock,
)


@runtime_checkable
class PlantRepository(Protocol):
    seen: set[Plant]

    def add(self, plant: Plant) -> None:
        """Add a steel plant to the repository."""
        ...

    def add_list(self, plants: Iterable[Plant]) -> None:
        """Add an iterable of steel plants to the repository."""
        ...

    def get(self, plant_id) -> Plant:
        """Get a steel plant from the repository by ID."""
        ...

    def list(self) -> list[Plant]:
        """Get a list of all steel plants."""
        ...

    def get_by_furnace_group_id(self, furnace_group_id: str) -> Plant:
        """Get a steel plant from the repository by furnace group ID."""
        ...


@runtime_checkable
class FurnaceGroupRepository(Protocol):
    def add(self, furnace_group: FurnaceGroup) -> None:
        """Add a furnace group to the repository."""
        ...

    def add_list(self, plants: Iterable[FurnaceGroup]) -> None:
        """Add an iterable of furnace groups to the repository."""
        ...

    def get(self, furnace_group) -> FurnaceGroup:
        """Get a furnace group from the repository by ID."""
        ...

    def list(self) -> list[FurnaceGroup]:
        """Get a list of all furnace groups."""
        ...


@runtime_checkable
class DemandCenterRepository(Protocol):
    def add(self, demand_center: DemandCenter) -> None:
        """Add a demand center to the repository."""
        ...

    def add_list(self, demand_center: Iterable[DemandCenter]) -> None:
        """Add an iterable of demand center to the repository."""
        ...

    def get(self, demand_center) -> DemandCenter:
        """Get a demand center from the repository by ID."""
        ...

    def list(self) -> list[DemandCenter]:
        """Get a list of all demand center."""
        ...


@runtime_checkable
class PlantGroupRepository(Protocol):
    seen: set[PlantGroup]

    def add(self, plant_group: PlantGroup) -> None:
        """Add a steel plant to the repository."""
        ...

    def add_list(self, plant_groups: Iterable[PlantGroup]) -> None:
        """Add an iterable of steel plants to the repository."""
        ...

    def get(self, plant_group_id) -> PlantGroup:
        """Get a plant group from the repository by ID."""
        ...

    def list(self) -> list[PlantGroup]:
        """Get a list of all plant groups."""
        ...

    def get_by_plant_id(self, plant_id: str) -> PlantGroup:
        """Get a plant group from the repository by plant ID."""
        ...


@runtime_checkable
class SupplierRepository(Protocol):
    def add(self, demand_center: Supplier) -> None:
        """Add a supplier to the repository."""
        ...

    def add_list(self, demand_center: Iterable[Supplier]) -> None:
        """Add an iterable of suppliers to the repository."""
        ...

    def get(self, demand_center) -> Supplier:
        """Get a supplier from the repository by ID."""
        ...

    def list(self) -> list[Supplier]:
        """Get a list of all suppliers."""
        ...


@runtime_checkable
class TradeTariffRepository(Protocol):
    def add(self, trade_tariff: TradeTariff) -> None:
        """Add a trade tariff with its ID and value."""
        ...

    def add_list(self, trade_tariff: Iterable[TradeTariff]) -> None:
        """Add an iterable of trade tariffs."""
        ...

    def get(self, trade_tariff: str) -> TradeTariff:
        """Get a trade tariff by its ID."""
        ...

    def list(self) -> list[TradeTariff]:
        """List all trade tariffs."""
        ...


@runtime_checkable
class PrimaryFeedstockRepository(Protocol):
    def add(self, trade_tariff: PrimaryFeedstock) -> None:
        """Add a trade tariff with its ID and value."""
        ...

    def add_list(self, trade_tariff: Iterable[PrimaryFeedstock]) -> None:
        """Add an iterable of trade tariffs."""
        ...

    def get(self, trade_tariff: str) -> PrimaryFeedstock:
        """Get a trade tariff by its ID."""
        ...

    def list(self) -> list[PrimaryFeedstock]:
        """List all trade tariffs."""
        ...


@runtime_checkable
class CarbonCostsRepository(Protocol):
    def add(self, trade_tariff: CarbonCostSeries) -> None:
        """Add a trade tariff with its ID and value."""
        ...

    def add_list(self, trade_tariff: Iterable[CarbonCostSeries]) -> None:
        """Add an iterable of trade tariffs."""
        ...

    def get(self, trade_tariff: str) -> CarbonCostSeries:
        """Get a trade tariff by its ID."""
        ...

    def list(self) -> list[CarbonCostSeries]:
        """List all trade tariffs."""
        ...


@runtime_checkable
class InputCostsRepository(Protocol):
    def add(self, trade_tariff: InputCosts) -> None:
        """Add a trade tariff with its ID and value."""
        ...

    def add_list(self, trade_tariff: Iterable[InputCosts]) -> None:
        """Add an iterable of trade tariffs."""
        ...

    def get(self, trade_tariff: str) -> InputCosts:
        """Get a trade tariff by its ID."""
        ...

    def list(self) -> list[InputCosts]:
        """List all trade tariffs."""
        ...


@runtime_checkable
class RegionEmissivityRepository(Protocol):
    def add(self, region_emissivity: RegionEmissivity) -> None:
        """Add a region emissivity with its ID and value."""
        ...

    def add_list(self, region_emissivity: Iterable[RegionEmissivity]) -> None:
        """Add an iterable of region emissivities."""
        ...

    def get(self, region_emissivity: str) -> RegionEmissivity:
        """Get a region emissivity by its ID."""
        ...

    def list(self) -> list[RegionEmissivity]:
        """List all region emissivities."""
        ...


@runtime_checkable
class Repository(Protocol):
    plants: PlantRepository
    furnace_groups: FurnaceGroupRepository
    demand_centers: DemandCenterRepository
    plant_groups: PlantGroupRepository
    suppliers: SupplierRepository
    trade_tariffs: TradeTariffRepository
