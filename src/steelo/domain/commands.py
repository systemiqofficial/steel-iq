from dataclasses import dataclass
from typing import TYPE_CHECKING, Any


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
class InstallCarbonCapture(Command):
    """Install carbon capture technology (CCS/CCU) on a furnace group.

    Represents a command to add carbon capture and storage (CCS) or carbon capture
    and utilization (CCU) capacity to an existing furnace group. The installed capacity
    reduces direct CO2 emissions from the production process.

    Carbon capture technologies:
        - CCS (Carbon Capture and Storage): Captures CO2 and stores it permanently underground
        - CCU (Carbon Capture and Utilization): Captures CO2 and uses it in products/processes

    Attributes:
        installed_capacity: Annual carbon capture capacity in tCO2e per year. This amount
            will be subtracted from the furnace group's direct_ghg emissions each year
            (cannot reduce emissions below zero).

    Example:
        >>> # Install 500,000 tCO2e/year capture capacity
        >>> cmd = InstallCarbonCapture(installed_capacity=500000.0)
        >>> message_bus.handle(cmd)
        >>> # FurnaceGroup.installed_carbon_capture will be set to 500000.0
        >>> # Direct emissions reduced accordingly in emissions calculations

    Notes:
        - Installed capacity should match the furnace group's emission profile and physical constraints.
        - Typical capture rates range from 60-90% of direct emissions depending on technology.
        - Economic viability depends on carbon price, capture costs, and subsidy availability.
        - Only affects direct_ghg scope; indirect and biogenic emissions unchanged.
    """

    installed_capacity: float


@dataclass
class AddFurnaceGroup(Command):
    """Add a furnace group to a plant."""

    furnace_group_id: str
    plant_id: str
    technology_name: str
    capacity: float
    product: str
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
        - Electricity costs from own renewable energy parc or grid
        - Hydrogen costs from own renewable energy parc or grid
        - Bill of materials with updated energy prices
    """

    plant_id: str
    furnace_group_id: str
    new_cost_of_debt: float
    new_cost_of_debt_no_subsidy: float
    new_capex: float
    new_capex_no_subsidy: float
    new_electricity_cost: float
    new_hydrogen_cost: float
    new_bill_of_materials: dict[str, dict[str, dict[str, Any]]] | None


# @dataclass
# class AddSinteringCapacityToPlant(Command):
#     """Add sintering furnace group to a plant with iron-making capacity."""

#     plant_id: str
