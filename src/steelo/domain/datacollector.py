from .models import Environment, PlantGroup, Plant

# Global variables moved to Environment/Config
from steelo.domain.constants import Commodities  # Keep enum as constant
import pickle
from collections import defaultdict
from typing import Any, cast
from pathlib import Path
import os
from .constants import Year
import logging


class DataCollector:
    def __init__(
        self, world_plant_groups: list[PlantGroup], env: Environment, custom_function=None, output_dir=None
    ) -> None:
        self.plant_groups = world_plant_groups
        self.env = env
        if output_dir is None:
            raise ValueError("output_dir is required")
        self.output_dir = Path(output_dir)
        self.cost_breakdown: dict[str, dict] = {}
        self.trace_capacity: dict[int, dict[str, float]] = {}
        self.trace_price: dict[int, dict[str, float]] = {}  # {year: {product: price}}
        # self.trace_cost_curve = {}
        self.trace_production: dict[int, float] = {}
        self.step = 0
        self.plant_emissions: dict[int, float] = {}
        self.trace_utilisation_rate: dict[int, dict[str, float]] = {}
        self.capacity_by_technology_and_PAM_status: dict[int, dict[str, dict[bool, float]]] = {}
        self.capacity_deltas: dict[int, dict[str, float]] = {}
        self.logged_events: list = []
        self.trace_decisions: dict = {}
        self.status_counts: dict[Any, dict[Any, dict[Any, dict[Any, int]]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
        )
        self.new_plant_locations: dict[Any, dict[Any, list]] = defaultdict(lambda: defaultdict(list))

        if custom_function is not None:
            pass

        # Delete all pickle files in the output directory
        if os.path.exists(self.output_dir):
            for file in os.listdir(self.output_dir):
                if file.endswith(".pkl"):
                    os.remove(os.path.join(self.output_dir, file))

    @property
    def plants(self):
        """
        Collect all plants from the plant groups
        """
        return [plant for pg in self.plant_groups for plant in pg.plants if plant is not None]

    def collect_cost_breakdrown(self):
        """
        Collect the cost breakdown of the plant, in terms of principal debt, interest, bill of materials, o&m and other opex
        """
        breakdown = {}
        for plant_group in self.plant_groups:
            for plant in plant_group.plants:
                breakdown[plant.plant_id] = plant.report_cost_breakdown()
        return breakdown

    def collect_capacity(self):
        """
        Collect the iron and steel prodction capacity of the plants by region
        """
        plants = [plant for pg in self.plant_groups for plant in pg.plants if plant is not None]
        self.env.update_regional_capacity(plants)
        return {
            Commodities.IRON.value: self.env.regional_iron_capacity,
            Commodities.STEEL.value: self.env.regional_steel_capacity,
        }

    def collect_market_iron_steel_price(self, demand: int = 300):
        """
        Collect the market price of iron and steel for the given iteration
        """
        return {
            Commodities.IRON.value: self.env.extract_price_from_costcurve(self.env.iron_demand, Commodities.IRON.value),
            Commodities.STEEL.value: self.env.extract_price_from_costcurve(
                self.env.current_demand, Commodities.STEEL.value
            ),
        }

    # def collect_params4steel_cost_curve(self):
    #     """
    #     This function will return the steel cost curve and the current demand.
    #     """
    #     return {
    #         "steel_cost_curve": self.env.steel_cost_curve,
    #         "current_demand": self.env.current_demand,
    #         "plants": self.plants,
    #     }

    def collect_emissions_by_plants(self):
        """
        Collect the emissions by plants
        """
        emissions = {}
        for plant_group in self.plant_groups:
            for plant in plant_group.plants:
                emissions[plant.plant_id] = plant.emissions
        return emissions

    def collect_utilisation_rates(self):
        """collect furnace_group utilisisation_rates"""
        return {
            fg.furnace_group_id: fg.utilization_rate
            for plant_group in self.plant_groups
            for plant in plant_group.plants
            for fg in plant.furnace_groups
            if fg.status.lower() in self.env.config.active_statuses
        }

    def collect_capacity_deltas(self):
        plants = [plant for pg in self.plant_groups for plant in pg.plants if plant is not None]
        delta_added = [plant.added_capacity for plant in plants]
        delta_removed = [plant.removed_capacity for plant in plants]
        return {"added": sum(delta_added), "removed": sum(delta_removed)}

    def collect_global_steel_production(self):
        """
        Collect the production by each operating steel furnace group and return the global total.
        """
        total_production = {}

        for plant in self.plants:
            for fg in plant.furnace_groups:
                tech = fg.technology.name

                if (fg.status.lower() in self.env.config.active_statuses) and (
                    fg.technology.product.lower() in [Commodities.STEEL.value, Commodities.IRON.value]
                ):
                    if tech not in total_production:
                        total_production[tech] = 0
                    total_production[tech] += fg.production
        return total_production

    def collect_capacity_by_technology_and_PAM_status(self):
        """
        Collect the capacity by technology and PAM status
        """
        capacity = {}
        for tech in [  # TODO: @Marcus, remove hardcoded technologies
            "EAF",
            "BOF",
        ]:
            cap_tech_pre_existing = [
                fg.capacity
                for plant in self.plants
                for fg in plant.furnace_groups
                if fg.technology.name == tech
                and fg.status.lower() in self.env.config.active_statuses
                and not fg.created_by_PAM
            ]
            cap_tech_created = [
                fg.capacity
                for plant in self.plants
                for fg in plant.furnace_groups
                if fg.technology.name == tech
                and fg.status.lower() in self.env.config.active_statuses
                and fg.created_by_PAM
            ]
            capacity[tech] = {"pre_existing": sum(cap_tech_pre_existing), "created": sum(cap_tech_created)}
        return capacity

    def production_trade(self):
        pass

    def log_event(self, event):
        self.logged_events.append(event)

    def attach_to_bus(self, bus):
        """Attach the log_event method to all event handlers in the bus."""
        for event_type, handlers in bus.event_handlers.items():
            handlers.append(self.log_event)

    def collect_events(self):
        """
        Collect the logged events in each time step as dictionary of furnace_group_id: event_type
        """
        event_collection = {evt.furnace_group_id: type(evt) for evt in self.logged_events}
        self.logged_events = []
        return event_collection

    def collect_new_plant_data(self, year: Year):
        """
        Collect the locations of new plants set to operating in the given year, as well as how many.
        """
        logger = logging.getLogger(f"{__name__}.collect_new_plant_data")
        indi_pg = None
        for pg in self.plant_groups:
            if pg.plant_group_id == "indi":
                indi_pg = pg

        if indi_pg is None:
            logger.warning("No plant group with ID 'indi' found. Skipping new plant data collection.")
            return

        for plant in indi_pg.plants:
            for fg in plant.furnace_groups:
                self.status_counts[fg.technology.product][year][fg.technology.name][fg.status] += 1
                if fg.status == "operating" and fg.lifetime.start == year:
                    self.new_plant_locations[fg.technology.product][year].append(
                        ({"lat": plant.location.lat, "lon": plant.location.lon})
                    )

    def collect(self, world_plant_list: list[Plant], world_plant_groups: list[PlantGroup], year):
        """
        Execute the data collection process
        """
        # Update our own attributes:
        self.plant_groups = world_plant_groups
        self.capacity_by_technology_and_PAM_status[self.step] = self.collect_capacity_by_technology_and_PAM_status()
        self.plant_emissions[self.step] = self.collect_emissions_by_plants().copy()
        self.collect_new_plant_data(self.env.year)

        plants = {}
        for p in world_plant_list:
            plant_dict = []

            for fg in p.furnace_groups:
                if fg is None:
                    continue
                if not isinstance(fg.status, str) or not isinstance(fg.technology.product, str):
                    continue
                # Include all iron and steel related products for reporting
                iron_steel_products = [
                    Commodities.STEEL.value,
                    Commodities.IRON.value,
                    Commodities.HOT_METAL.value,
                    Commodities.DRI_LOW.value,
                    Commodities.DRI_MID.value,
                    Commodities.DRI_HIGH.value,
                    Commodities.HBI_LOW.value,
                    Commodities.HBI_MID.value,
                    Commodities.HBI_HIGH.value,
                    Commodities.PIG_IRON.value,
                    Commodities.LIQUID_STEEL.value,
                ]
                if (
                    fg.status.lower() not in self.env.config.active_statuses
                    or fg.technology.product.lower() not in iron_steel_products
                ):
                    continue

                bill_of_materials = fg.bill_of_materials
                materials: dict[str, dict[str, Any]] | None = None
                energy: dict[str, dict[str, Any]] = {}
                if bill_of_materials and bill_of_materials.get("materials"):
                    materials = cast(dict[str, dict[str, Any]], bill_of_materials["materials"])
                    energy = cast(dict[str, dict[str, Any]], bill_of_materials.get("energy", {}))

                has_materials = materials is not None
                record: dict[str, Any] = {
                    "furnace_group_id": fg.furnace_group_id,
                    "technology": fg.technology.name,
                    "chosen_reductant": fg.chosen_reductant,
                    "production": fg.production,
                    "capacity": fg.capacity,
                    "product": fg.technology.product,
                    "unit_fopex": fg.unit_fopex,
                    "unit_debt_repayment": fg.unit_current_debt_repayment,
                    "unit_production_cost": fg.unit_production_cost,
                    "debt_repayment_per_year": fg.debt_repayment_per_year,
                    "debt_repayment_for_current_year": fg.debt_repayment_for_current_year,
                    "historic_balance": fg.historic_balance,
                }

                if fg.production and fg.production > 0 and has_materials:
                    assert materials is not None
                    for feed_key in set(materials.keys()) & set(energy.keys()):
                        mat_entry = materials[feed_key]
                        energy_entry = energy[feed_key]
                        if energy_entry.get("unit_cost") == mat_entry.get("unit_cost") and energy_entry.get(
                            "total_cost"
                        ) == mat_entry.get("total_cost"):
                            logging.getLogger(__name__).warning(
                                "[DATA COLLECTOR] Energy costs for %s/%s match material costs exactly "
                                "(unit_cost=%s, total_cost=%s).",
                                fg.furnace_group_id,
                                feed_key,
                                energy_entry.get("unit_cost"),
                                energy_entry.get("total_cost"),
                            )
                    record.update(
                        {
                            "bill_of_materials": bill_of_materials,  # type: ignore
                            "materials": materials,  # type: ignore
                            "energy": energy,  # type: ignore
                            "unit_vopex": fg.unit_vopex,
                            "unit_secondary_output_costs": fg.cost_adjustments_from_secondary_outputs,
                            "unit_carbon_cost": fg.carbon_cost_per_unit,
                            "cost_breakdown": fg.cost_breakdown_by_feedstock,  # type: ignore
                            "carbon_breakdown": fg.carbon_breakdown_by_feedstock,  # type: ignore
                        }
                    )

                    ccs_outputs = {
                        name: {
                            "demand": info.get("demand"),
                            "total_cost": info.get("total_cost"),
                            "unit_cost_per_input": info.get("unit_cost_per_input"),
                        }
                        for name, info in materials.items()
                        if "co2" in name.lower()
                    }
                    if ccs_outputs:
                        record["ccs_outputs"] = ccs_outputs
                    if fg.emissions is not None:
                        for boundary in fg.emissions:
                            for scope in fg.emissions[boundary]:
                                record[f"emissions_{boundary}_{scope}"] = fg.emissions[boundary][scope]
                else:
                    record = {
                        "furnace_group_id": fg.furnace_group_id,
                        "technology": fg.technology.name,
                        "chosen_reductant": fg.chosen_reductant,
                        "production": fg.production,
                        "capacity": fg.capacity,
                        "product": fg.technology.product,
                        "bill_of_materials": None,
                        "materials": None,
                        "energy": None,
                        "cost_breakdown": None,
                        "carbon_breakdown": None,
                        "unit_vopex": None,
                        "unit_carbon_cost": None,
                        "unit_secondary_outputs_revenue": None,
                        "unit_fopex": fg.unit_fopex,
                        "unit_debt_repayment": fg.unit_current_debt_repayment,
                        "unit_production_cost": fg.unit_production_cost,
                        "debt_repayment_per_year": fg.debt_repayment_per_year,
                        "debt_repayment_for_current_year": fg.debt_repayment_for_current_year,
                        "historic_balance": fg.historic_balance,
                    }
                plant_dict.append(record)

            plant_group = p.ultimate_plant_group

            plants[p.plant_id] = {
                "furnace_groups": plant_dict,
                "plant_group": plant_group,
                "location": p.location.iso3,
                "balance": p.balance,
            }

        # Ensure the TM directory exists
        tm_dir = self.output_dir / "TM"
        tm_dir.mkdir(parents=True, exist_ok=True)

        with open(tm_dir / f"datacollection_post_allocation_{year}.pkl", "wb") as f:
            pickle.dump(plants, f)
