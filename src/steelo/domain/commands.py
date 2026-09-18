from dataclasses import dataclass
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from .models import Plant, Subsidy


class Command:
    """Base class for all commands."""


@dataclass
class CloseFurnaceGroup(Command):
    """Close a furnace group."""

    plant_id: str
    furnace_group_id: str


@dataclass
class RenovateFurnaceGroup(Command):
    """Renovate a furnace group."""

    plant_id: str
    furnace_group_id: str
    capex: float
    capex_no_subsidy: float
    cost_of_debt: float
    cost_of_debt_no_subsidy: float
    capex_subsidies: list["Subsidy"]
    debt_subsidies: list["Subsidy"]


@dataclass
class ChangeFurnaceGroupTechnology(Command):
    """Change the technology of a furnace group."""

    plant_id: str
    furnace_group_id: str
    technology_name: str
    old_technology_name: str
    npv: float
    cosa: float
    utilisation: float
    capex: float
    capex_no_subsidy: float
    capacity: float
    remaining_lifetime: int
    bom: dict
    chosen_reductant: str
    cost_of_debt: float
    cost_of_debt_no_subsidy: float
    capex_subsidies: list["Subsidy"]
    debt_subsidies: list["Subsidy"]


@dataclass
class ChangeFurnaceGroupStatusToSwitchingTechnology(Command):
    """Change the status of a furnace group to 'operating switching technology'"""

    plant_id: str
    furnace_group_id: str
    year_of_switch: int  # The year when the technology switch will occur
    cmd: ChangeFurnaceGroupTechnology  # The command to be executed in the future


@dataclass
class AddFurnaceGroup(Command):
    """Add a furnace group to a plant."""

    furnace_group_id: str
    plant_id: str
    technology_name: str
    capacity: float
    product: str
    chosen_reductant: str
    equity_share: float
    equity_needed: float
    npv: float
    capex: float
    capex_no_subsidy: float
    cost_of_debt: float
    cost_of_debt_no_subsidy: float
    capex_subsidies: list["Subsidy"]
    debt_subsidies: list["Subsidy"]


@dataclass
class AddNewBusinessOpportunities(Command):
    """Identifies bussines opportunities."""

    new_plants: list["Plant"]  # List of plant IDs where new business opportunities were identified


@dataclass
class UpdateFurnaceGroupStatus(Command):
    """Updates the status of bussines opportunities."""

    fg_id: str
    plant_id: str
    new_status: str


@dataclass
class UpdateDynamicCosts(Command):
    """Update dynamic costs for a furnace group in a business opportunity.

    This command updates the dynamic costs that change yearly:
        - Cost of debt (with subsidies, if applicable)
        - CAPEX (with subsidies, if applicable)
        - Energy costs for all carriers (subsidised input, output, and unsubsidised)
        - Expected utilisation (fleet average for the technology at emission time)
    """

    plant_id: str
    furnace_group_id: str
    new_cost_of_debt: float
    new_cost_of_debt_no_subsidy: float
    new_capex: float
    new_capex_no_subsidy: float
    new_energy_costs: dict[str, float]
    new_output_energy_costs: dict[str, float]
    new_energy_costs_no_subsidy: dict[str, float]
    new_utilization_rate: float


# @dataclass
# class AddSinteringCapacityToPlant(Command):
#     """Add sintering furnace group to a plant with iron-making capacity."""

#     plant_id: str
