from collections import defaultdict
from typing import Any

from .unit_of_work import UnitOfWork
from .message_bus import MessageBus
from .checkpoint import SimulationCheckpoint
from ..adapters.repositories import Repository

__all__ = ["MessageBus", "UnitOfWork", "SimulationCheckpoint"]


def get_markers(repository: Repository):
    plants = repository.plants.list()
    markers = []
    for plant in plants:
        marker = {
            "location": (plant.location.lat, plant.location.lon),
            "popup": plant.plant_id,
        }
        markers.append(marker)
    return markers


# Function to calculate capacity by technology and region
def calculate_capacity_by_technology_and_region(
    repository: Repository, product: str, furnace_statuses: list[str]
) -> defaultdict[Any, defaultdict[Any, int]]:
    plants = repository.plants.list()
    capacity_summary: defaultdict[Any, defaultdict[Any, int]] = defaultdict(lambda: defaultdict(int))

    for plant in plants:
        region = plant.location.region
        for furnace_group in plant.furnace_groups:
            if furnace_group.technology.product != product or furnace_group.status not in furnace_statuses:
                continue
            tech_name = furnace_group.technology.name
            capacity_summary[tech_name][region] += int(furnace_group.capacity)

    return capacity_summary
