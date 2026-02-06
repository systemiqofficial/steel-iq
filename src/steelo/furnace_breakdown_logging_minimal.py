"""Minimal furnace breakdown logging for simulation.py lines 920-928."""

import logging
from typing import Dict, Any, List, Optional, Set
from steelo.domain.constants import T_TO_KT


class FurnaceBreakdownLogger:
    """Collects and logs furnace group information for debugging and analysis."""

    def __init__(self) -> None:
        """Initialize the furnace breakdown logger."""
        self.previous_debt_balances: Dict[str, float] = {}

    def get_command_cost(self, cmd) -> str:
        """Extract cost information from a command."""
        try:
            # Start with technology name if available
            tech_info = ""
            if hasattr(cmd, "technology_name") and cmd.technology_name:
                tech_info = f" -> {cmd.technology_name}"

            # Collect all available cost information
            cost_parts = []
            if hasattr(cmd, "capex") and cmd.capex:
                cost_parts.append(f"CAPEX: ${cmd.capex:,.0f}")
            if hasattr(cmd, "equity_needed") and cmd.equity_needed:
                cost_parts.append(f"Equity: ${cmd.equity_needed:,.0f}")
            if hasattr(cmd, "npv") and cmd.npv:
                cost_parts.append(f"NPV: ${cmd.npv:,.0f}")
            if hasattr(cmd, "cosa") and cmd.cosa:
                cost_parts.append(f"COSA: ${cmd.cosa:,.0f}")
            if hasattr(cmd, "cost_of_debt") and cmd.cost_of_debt:
                cost_parts.append(f"CoD: {cmd.cost_of_debt:.2%}")
            if hasattr(cmd, "capacity") and cmd.capacity:
                cost_parts.append(f"Cap: {cmd.capacity * T_TO_KT:,.0f} kt")
            if hasattr(cmd, "utilisation") and cmd.utilisation:
                cost_parts.append(f"Util: {cmd.utilisation:.1%}")

            # Combine all cost information
            if cost_parts:
                return f"{tech_info} ({', '.join(cost_parts)})"
            else:
                return tech_info
        except Exception:
            return ""

    def calculate_remaining_debt(self, fg) -> float:
        """Calculate the total remaining debt for a furnace group."""
        try:
            if hasattr(fg, "debt_repayment_per_year"):
                # Sum all future debt repayments
                debt_schedule = fg.debt_repayment_per_year
                if debt_schedule and len(debt_schedule) > 0:
                    return sum(debt_schedule)
            return 0.0
        except Exception:
            return 0.0

    def calculate_remaining_debt_years(self, fg) -> int:
        """Calculate the number of years of remaining debt repayment."""
        try:
            if hasattr(fg, "debt_repayment_per_year"):
                debt_schedule = fg.debt_repayment_per_year
                if debt_schedule and len(debt_schedule) > 0:
                    # Count non-zero debt repayment years
                    return len([payment for payment in debt_schedule if payment > 0])
            return 0
        except Exception:
            return 0

    def collect_furnace_group_data(self, fg, plant, bus, commands) -> Dict[str, Any]:
        """
        Collect comprehensive data about a single furnace group.

        Args:
            fg: Furnace group object
            plant: Parent plant object
            bus: Message bus with environment
            commands: Commands dictionary

        Returns:
            Dictionary containing all furnace group data
        """
        # Get plant_id properly
        plant_id = None
        if hasattr(plant, "plant_id"):
            plant_id = plant.plant_id
        elif hasattr(plant, "id"):
            plant_id = str(plant.id)

        data = {
            "furnace_group_id": fg.furnace_group_id,
            "plant_id": plant_id,
            "status": fg.status,
            "capacity": fg.capacity,
            "capacity_kt": fg.capacity * T_TO_KT,
            "production": fg.production,
            "production_kt": fg.production * T_TO_KT,
            "utilization_rate": fg.utilization_rate,
            "lifetime_remaining": fg.lifetime.remaining_number_of_years if hasattr(fg, "lifetime") else 0,
        }

        # Capture full lifetime/PointInTime object information
        if hasattr(fg, "lifetime") and fg.lifetime:
            lifetime_obj = {
                "start": fg.lifetime.start,
                "end": fg.lifetime.end,
                "current": fg.lifetime.current,
                "plant_lifetime": fg.lifetime.plant_lifetime,
                "total_years": fg.lifetime.number_of_years,
                "age": fg.lifetime.current - fg.lifetime.start,
            }
            data["lifetime_obj"] = lifetime_obj
        else:
            data["lifetime_obj"] = None

        # Technology information
        if fg.technology:
            data["technology"] = {
                "name": fg.technology.name,
                "product": fg.technology.product if hasattr(fg.technology, "product") else "Unknown",
            }
        else:
            data["technology"] = None

        # Location information (ISO3 code for country filtering)
        try:
            if hasattr(plant, "location") and plant.location:
                if hasattr(plant.location, "iso3"):
                    data["location"] = plant.location.iso3  # Just the ISO3 code for filtering
                    data["location_full"] = (
                        f"{plant.location.name}, {plant.location.iso3}"
                        if hasattr(plant.location, "name")
                        else plant.location.iso3
                    )
                else:
                    data["location"] = str(plant.location)
                    data["location_full"] = str(plant.location)
            else:
                data["location"] = "Unknown"
                data["location_full"] = "Unknown"
        except Exception:
            data["location"] = "Unknown"
            data["location_full"] = "Unknown"

        # Commands for this year
        data["commands"] = []
        if bus.env.year in commands and fg.furnace_group_id in commands[bus.env.year]:
            fg_commands = commands[bus.env.year][fg.furnace_group_id]
            if isinstance(fg_commands, list):
                for cmd in fg_commands:
                    data["commands"].append({"name": type(cmd).__name__, "details": self.get_command_cost(cmd)})
            else:
                data["commands"].append(
                    {"name": type(fg_commands).__name__, "details": self.get_command_cost(fg_commands)}
                )

        # Financial information
        data["financial"] = {
            "historic_balance": getattr(fg, "historic_balance", 0),
            "debt_repayment_current": getattr(fg, "debt_repayment_for_current_year", 0),
            "remaining_debt": self.calculate_remaining_debt(fg),
            "remaining_debt_years": self.calculate_remaining_debt_years(fg),
        }

        # Calculate balance change
        prev_balance = self.previous_debt_balances.get(fg.furnace_group_id, data["financial"]["historic_balance"])
        data["financial"]["balance_change"] = data["financial"]["historic_balance"] - prev_balance
        self.previous_debt_balances[fg.furnace_group_id] = data["financial"]["historic_balance"]

        # Additional data collection - properly capture emissions structure
        # The emissions are stored as nested dict: emissions[boundary][scope] = value
        data["emissions"] = getattr(fg, "emissions", {})

        # Get unit emissions - FurnaceGroup already calculates this
        data["emissions_per_unit"] = fg.emissions_per_unit if hasattr(fg, "emissions_per_unit") else {}

        # Carbon costs and operational data
        data["operations"] = {
            "carbon_costs": getattr(fg, "carbon_costs_for_emissions", 0),
            "transport_emissions": getattr(fg, "transport_emissions", 0),
            "railway_cost": getattr(fg, "railway_cost", 0),
            "unit_fopex": getattr(fg, "unit_fopex", 0),
            "energy_cost_dict": getattr(fg, "energy_cost_dict", {}),
            "bill_of_materials": getattr(fg, "bill_of_materials", None),
            "energy_costs_no_subsidy": getattr(fg, "energy_costs_no_subsidy", {}),
        }

        # Commodity price
        try:
            commodity = data["technology"]["product"] if data["technology"] else "Unknown"
            if commodity.lower() == "steel":
                data["commodity_price"] = bus.env.extract_price_from_costcurve(
                    demand=bus.env.current_demand, product="steel"
                )
            elif commodity.lower() == "iron":
                data["commodity_price"] = bus.env.extract_price_from_costcurve(
                    demand=bus.env.iron_demand, product="iron"
                )
            else:
                data["commodity_price"] = 0
        except Exception:
            data["commodity_price"] = 0

        # Applied subsidies tracking
        data["subsidies"] = {}
        if hasattr(fg, "applied_subsidies") and fg.applied_subsidies:
            # Track each type of subsidy
            for subsidy_type in ["capex", "debt", "opex", "hydrogen", "electricity"]:
                if subsidy_type in fg.applied_subsidies:
                    subsidies_list = fg.applied_subsidies[subsidy_type]
                    if subsidies_list:
                        data["subsidies"][subsidy_type] = {"count": len(subsidies_list), "details": []}
                        for subsidy in subsidies_list:
                            subsidy_info = {
                                "name": getattr(subsidy, "subsidy_name", "Unknown"),
                                "type": getattr(subsidy, "subsidy_type", "absolute"),
                                "amount": getattr(subsidy, "subsidy_amount", 0),
                                "start_year": getattr(subsidy, "start_year", 0),
                                "end_year": getattr(subsidy, "end_year", 0),
                            }
                            data["subsidies"][subsidy_type]["details"].append(subsidy_info)

        return data

    def log_furnace_group(self, fg_data: Dict[str, Any]) -> None:
        """
        Log detailed information about a single furnace group.

        Args:
            fg_data: Dictionary containing furnace group data
        """
        # Only log if there are commands or the furnace has capacity
        if not fg_data["commands"] and fg_data["capacity"] <= 0:
            return

        logging.info(f"  FG: [{fg_data['furnace_group_id']}]  ({fg_data['location']}):")

        # Technology and status
        tech_name = fg_data["technology"]["name"] if fg_data["technology"] else "None"
        logging.info(f"    Tech: {tech_name}, Status: {fg_data['status']}, Capacity: {fg_data['capacity_kt']:,.0f} kt")

        # Product and price
        if fg_data["technology"]:
            product = fg_data["technology"]["product"]
            price_info = f" @ ${fg_data['commodity_price']:,.0f}/t" if fg_data["commodity_price"] > 0 else ""
            logging.info(f"    Product: {product}{price_info}")

        # Production and utilization
        logging.info(
            f"    Production: {fg_data['production_kt']:,.0f} kt, Utilization: {fg_data['utilization_rate']:.1%}"
        )

        # Lifetime - show full PointInTime object details
        if "lifetime_obj" in fg_data and fg_data["lifetime_obj"]:
            lifetime = fg_data["lifetime_obj"]
            logging.info(
                f"    Lifetime: Start={lifetime['start']}, End={lifetime['end']}, "
                f"Current={lifetime['current']}, Plant Lifetime={lifetime['plant_lifetime']} years"
            )
            logging.info(
                f"    Lifetime Status: Remaining={fg_data['lifetime_remaining']} years, "
                f"Total Years={lifetime['total_years']}, Age={lifetime['age']} years"
            )
        else:
            logging.info(f"    Remaining number of years: {fg_data['lifetime_remaining']}")

        # Emissions information - separated by boundary
        emissions = fg_data.get("emissions", {})
        emissions_per_unit = fg_data.get("emissions_per_unit", {})

        if emissions and isinstance(emissions, dict):
            for boundary, scope_data in emissions.items():
                if isinstance(scope_data, dict) and scope_data:
                    # Calculate total for this boundary (excluding biomass)
                    # Only include direct_ghg and indirect_ghg, exclude direct_with_biomass_ghg
                    boundary_total = 0
                    for scope, value in scope_data.items():
                        if value is not None and scope != "direct_with_biomass_ghg":
                            boundary_total += value

                    # Format the scope data as a readable string for total emissions
                    scope_items = []
                    for scope, value in scope_data.items():
                        if value is not None:
                            # Format the scope name nicely (e.g., "direct_ghg" -> "Direct GHG")
                            scope_name = scope.replace("_", " ").title()
                            scope_items.append(f"{scope_name}: {value:,.2f}")

                    if scope_items:
                        # Format boundary name (e.g., "ghg_factor" -> "GHG Factor")
                        boundary_name = boundary.replace("_", " ").title()

                        # Log total emissions for this boundary
                        logging.info(f"    Emissions Total ({boundary_name}): {boundary_total:,.2f} t CO2")
                        logging.info(f"      Breakdown: {', '.join(scope_items)}")

                        # Log unit emissions if available
                        if boundary in emissions_per_unit and emissions_per_unit[boundary]:
                            unit_scope_data = emissions_per_unit[boundary]
                            # Calculate unit total (excluding biomass)
                            unit_total = 0
                            for scope, value in unit_scope_data.items():
                                if value is not None and scope != "direct_with_biomass_ghg":
                                    unit_total += value
                            unit_items = []
                            for scope, value in unit_scope_data.items():
                                if value is not None:
                                    scope_name = scope.replace("_", " ").title()
                                    unit_items.append(f"{scope_name}: {value:.2f}")
                            if unit_items:
                                logging.info(
                                    f"    Emissions Per Unit ({boundary_name}): {unit_total:.2f} t CO2/t product"
                                )
                                logging.info(f"      Breakdown: {', '.join(unit_items)}")

        # Operations and costs information
        operations = fg_data.get("operations", {})
        if operations:
            # Carbon and transport costs
            if operations.get("carbon_costs", 0) > 0 or operations.get("transport_emissions", 0) > 0:
                cost_items = []
                if operations.get("carbon_costs", 0) > 0:
                    cost_items.append(f"Carbon Cost: ${operations['carbon_costs']:,.0f}")
                if operations.get("transport_emissions", 0) > 0:
                    cost_items.append(f"Transport Emissions: {operations['transport_emissions']:,.2f} t CO2")
                if operations.get("railway_cost", 0) > 0:
                    cost_items.append(f"Railway Cost: ${operations['railway_cost']:,.0f}")
                if cost_items:
                    logging.info(f"    Operations: {', '.join(cost_items)}")

            # Unit FOPEX
            if operations.get("unit_fopex", 0) > 0:
                logging.info(f"    Unit FOPEX: ${operations['unit_fopex']:,.0f}")

            # Energy costs
            energy_costs = operations.get("energy_cost_dict", {})
            if energy_costs:
                energy_items = [f"{k}: ${v:,.0f}" for k, v in energy_costs.items() if v > 0]
                if energy_items:
                    logging.info(f"    Energy Costs: {', '.join(energy_items[:3])}")  # Show first 3

            # H2/Electricity subsidy effect (show before -> after if subsidised)
            no_sub = operations.get("energy_costs_no_subsidy", {})
            if no_sub:
                h2_before = no_sub.get("hydrogen", 0)
                h2_after = energy_costs.get("hydrogen", 0)
                elec_before = no_sub.get("electricity", 0)
                elec_after = energy_costs.get("electricity", 0)
                if h2_before > 0 and h2_before != h2_after:
                    logging.info(f"    H2 Subsidy: ${h2_before:.2f} -> ${h2_after:.2f}/kg")
                if elec_before > 0 and elec_before != elec_after:
                    logging.info(f"    Elec Subsidy: ${elec_before:.4f} -> ${elec_after:.4f}/kWh")

        # Financial information
        financial = fg_data["financial"]
        logging.info(f"    Balance: ${financial['historic_balance']:,.0f} (Δ{financial['balance_change']:+,.0f})")

        debt_years_info = f" ({financial['remaining_debt_years']} yrs)" if financial["remaining_debt_years"] > 0 else ""
        logging.info(
            f"    Debt Remaining: ${financial['remaining_debt']:,.0f}{debt_years_info}, "
            f"Current Payment: ${financial['debt_repayment_current']:,.0f}"
        )

        # Applied subsidies
        if fg_data.get("subsidies"):
            subsidy_info = []
            for subsidy_type, subsidy_data in fg_data["subsidies"].items():
                if subsidy_data and "count" in subsidy_data:
                    # Format subsidy type nicely
                    type_name = subsidy_type.upper()
                    count = subsidy_data["count"]

                    # Get total absolute and relative values
                    total_absolute = sum(s.get("absolute", 0) for s in subsidy_data.get("details", []))
                    max_relative = max((s.get("relative", 0) for s in subsidy_data.get("details", [])), default=0)

                    if total_absolute > 0 or max_relative > 0:
                        subsidy_str = f"{type_name}: {count} active"
                        if total_absolute > 0:
                            subsidy_str += f" (${total_absolute:,.0f}"
                            if max_relative > 0:
                                subsidy_str += f" + {max_relative:.1%})"
                            else:
                                subsidy_str += ")"
                        elif max_relative > 0:
                            subsidy_str += f" ({max_relative:.1%})"
                        subsidy_info.append(subsidy_str)

            if subsidy_info:
                logging.info(f"    Subsidies: {', '.join(subsidy_info)}")

        # Commands/actions
        if fg_data["commands"]:
            command_strs = [f"{cmd['name']}{cmd['details']}" for cmd in fg_data["commands"]]
            logging.info(f"    >>> ACTIONS: {', '.join(command_strs)}")

        logging.info("")  # Empty line between furnace groups

    def log_all_furnace_groups(
        self,
        bus,
        commands,
        plant_ids: Optional[Set[str]] = None,
        countries: Optional[Set[str]] = None,
        first_plant_per_country: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Collect and log furnace groups with optional filtering.

        Args:
            bus: Message bus with environment and unit of work
            commands: Commands dictionary
            plant_ids: Optional set of plant IDs to track (if None, track all)
            countries: Optional set of country ISO3 codes to filter by
            first_plant_per_country: If True with countries set, only track first plant per country

        Returns:
            List of dictionaries containing furnace group data
        """
        all_furnace_groups = []
        seen_countries = set()

        for plant in bus.uow.plants.list():
            # Filter by plant IDs if specified
            if plant_ids is not None:
                plant_id = plant.id if hasattr(plant, "id") else None
                if plant_id not in plant_ids:
                    continue

            # Filter by countries if specified
            if countries is not None:
                country = None
                try:
                    if hasattr(plant, "location") and plant.location:
                        if hasattr(plant.location, "iso3"):
                            country = plant.location.iso3
                except Exception:
                    pass

                if country not in countries:
                    continue

                # If only first plant per country, check if we've seen this country
                if first_plant_per_country:
                    if country in seen_countries:
                        continue
                    seen_countries.add(country)

            # Collect data for all furnace groups in this plant
            for fg in plant.furnace_groups:
                fg_data = self.collect_furnace_group_data(fg, plant, bus, commands)
                all_furnace_groups.append(fg_data)
                self.log_furnace_group(fg_data)

        return all_furnace_groups
