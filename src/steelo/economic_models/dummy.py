import random
from typing import TYPE_CHECKING

from ..adapters.repositories.in_memory_repository import PlantInMemoryRepository

if TYPE_CHECKING:
    from ..service_layer import MessageBus


class PlantsGoOutOfBusinessOverTime:
    """
    Uses self.fraction_to_keep to determine which plants to keep
    by randomly selecting a subset of the steel plants.
    """

    def __init__(self, *, fraction_to_keep: float = 0.5) -> None:
        self.fraction_to_keep = fraction_to_keep

    def run(self, bus: "MessageBus") -> None:
        steel_plants = bus.uow.plants.list()
        remaining_plants = random.choices(steel_plants, k=int(len(steel_plants) * self.fraction_to_keep))
        bus.uow.plants = PlantInMemoryRepository()
        bus.uow.plants.add_list(remaining_plants)
