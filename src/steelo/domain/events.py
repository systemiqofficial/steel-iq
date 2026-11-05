from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .trade_modelling.trade_lp_modelling import Allocations
    from . import Year


class Event:
    """Base class for all events."""


@dataclass
class FurnaceGroupClosed(Event):
    """Furnace group closed.

    Carries the plant context the capacity policy and the motion recorders
    need: the country and sub-national unit, the product, and ``owner_id`` =
    ``Plant.ultimate_plant_group`` — which still reports ``indi_<iso3>`` for a
    greenfield the policy attributed to a funding company, so the policy
    resolves ownership by group membership instead.
    """

    furnace_group_id: str
    capacity: float
    iso3: str
    geo_unit: str | None
    owner_id: str
    product: str


@dataclass
class FurnaceGroupTechChanged(Event):
    """Furnace group technology was changed.

    ``old_capacity`` is the capacity before the change; it exceeds ``capacity``
    when the capacity policy shrank a penalised replacement. The location,
    product and ``owner_id`` context is as on :class:`FurnaceGroupClosed`.
    """

    furnace_group_id: str
    technology_name: str
    capacity: float
    iso3: str
    geo_unit: str | None
    old_technology_name: str
    old_capacity: float
    owner_id: str
    product: str
    is_new_plant: bool = False  # True if this is a new plant, False if it's a switch


@dataclass
class FurnaceGroupRenovated(Event):
    """Furnace group renovated.

    ``old_capacity`` is the capacity before the renovation; it exceeds
    ``capacity`` when the capacity policy treated the renovation as a penalised
    replacement. The location, product and ``owner_id`` context is as on
    :class:`FurnaceGroupClosed`.
    """

    furnace_group_id: str
    capacity: float
    iso3: str
    geo_unit: str | None
    old_technology_name: str
    new_technology_name: str
    old_capacity: float
    owner_id: str
    product: str


@dataclass
class FurnaceGroupAdded(Event):
    """Furnace group added."""

    plant_id: str
    furnace_group_id: str
    technology_name: str
    capacity: float
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
