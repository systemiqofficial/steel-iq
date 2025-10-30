from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .trade_modelling.trade_lp_modelling import Allocations
    from . import Year


class Event:
    """Base class for all events."""


@dataclass
class FurnaceGroupClosed(Event):
    """Furnace group closed."""

    furnace_group_id: str


@dataclass
class FurnaceGroupTechChanged(Event):
    """Furnace group technology was changed."""

    furnace_group_id: str
    technology_name: str
    capacity: int
    is_new_plant: bool = False  # True if this is a new plant, False if it's a switch


@dataclass
class FurnaceGroupRenovated(Event):
    """Furnace group renovated."""

    furnace_group_id: str


@dataclass
class FurnaceGroupAdded(Event):
    """Furnace group added."""

    plant_id: str
    furnace_group_id: str
    technology_name: str
    capacity: int
    is_new_plant: bool = False  # True if this is a new plant, False if it's an expansion


@dataclass
class SinteringCapacityAdded(Event):
    """Sintering furnace group added to plant."""

    plant_id: str
    furnace_group_id: str
    capacity: float


@dataclass
class SteelAllocationsCalculated(Event):
    """Steel allocations calculated."""

    trade_allocations: "Allocations"


@dataclass
class IterationOver(Event):
    """This timestep is finalised."""

    time_step_increment: int
    iron_price: float
    # dc: DataCollector


@dataclass
class SaveCheckpoint(Event):
    """Event to trigger checkpoint saving."""

    year: "Year"


@dataclass
class LoadCheckpoint(Event):
    """Event to trigger checkpoint loading."""

    year: "Year"
