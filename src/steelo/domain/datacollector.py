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
        self.trace_capex: dict[int, dict[str, dict[str, float]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(float))
        )  # {year: {technology: {iso3: total_capex}}}
        # {boundary: {year: {technology: {scope: emissions_tCO2e}}}}
        # scope in {direct_ghg, direct_with_biomass_ghg, indirect_ghg}
        self.trace_emissions: dict[str, dict[int, dict[str, dict[str, float]]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
        )
        self.trace_production_by_product: dict[int, dict[str, float]] = defaultdict(
            lambda: defaultdict(float)
        )  # {year: {product: total_production_tonnes}}, product in {iron, steel}
        self.trace_iron_ore: dict[int, dict[str, float]] = defaultdict(
            lambda: defaultdict(float)
        )  # {year: {quality: total_consumption_tonnes}}
        self.trace_metallic_charges: dict[int, dict[str, float]] = defaultdict(
            lambda: defaultdict(float)
        )  # {year: {charge_type: total_consumption_tonnes}}
        self.trace_international_iron_trade: dict[int, dict[str, float]] = defaultdict(
            lambda: defaultdict(float)
        )  # {year: {iron_product: total_international_trade_tonnes}}

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

    def collect_market_iron_steel_price(self, world_suppliers=None):
        """
        Collect the market price of iron and steel for the given iteration, plus scrap
        and weighted-average iron production cost when data is available.

        Args:
            world_suppliers: Optional list of Supplier objects; used to compute the
                capacity-weighted average scrap market price.

        Returns:
            dict with keys: "iron", "steel", and optionally "scrap" and
            "iron_weighted_avg".
        """
        result = {
            Commodities.IRON.value: self.env.extract_price_from_costcurve(self.env.iron_demand, Commodities.IRON.value),
            Commodities.STEEL.value: self.env.extract_price_from_costcurve(
                self.env.current_demand, Commodities.STEEL.value
            ),
        }

        # Scrap price: capacity-weighted average of scrap supplier production costs
        if world_suppliers:
            year = self.env.year
            scrap_suppliers = [s for s in world_suppliers if s.commodity == "scrap"]
            total_cap = 0.0
            total_cost = 0.0
            for s in scrap_suppliers:
                cap = float(s.capacity_by_year.get(year, 0.0))
                cost = s.production_cost_by_year.get(year)
                if cap > 0 and cost is not None:
                    total_cap += cap
                    total_cost += cap * float(cost)
            if total_cap > 0:
                result["scrap"] = total_cost / total_cap

        # Weighted-average iron material cost from avg_boms:
        # for every technology, find any iron-product material and accumulate
        # share-weighted unit costs across all (tech, iron_material) entries.
        from steelo.domain.constants import IRON_PRODUCTS

        iron_product_set = set(IRON_PRODUCTS)
        avg_boms = getattr(self.env, "avg_boms", {})
        total_share = 0.0
        total_weighted_cost = 0.0
        for _tech, materials in avg_boms.items():
            for mat_name, mat_data in materials.items():
                if mat_name.lower() not in iron_product_set:
                    continue
                share = mat_data.get("input_share_pct", 0.0)
                cost = mat_data.get("unit_cost")
                if share > 0 and cost is not None:
                    total_share += share
                    total_weighted_cost += share * float(cost)
        if total_share > 0:
            result["iron_weighted_avg"] = total_weighted_cost / total_share

        return result

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
        indi_groups = [pg for pg in self.plant_groups if pg.plant_group_id.startswith("indi")]
        if not indi_groups:
            logger.warning("No indi plant groups found. Skipping new plant data collection.")
            return

        for indi_pg in indi_groups:
            for plant in indi_pg.plants:
                for fg in plant.furnace_groups:
                    self.status_counts[fg.technology.product][year][fg.technology.name][fg.status] += 1
                    if fg.status == "operating" and fg.lifetime.start == year:
                        self.new_plant_locations[fg.technology.product][year].append(
                            ({"lat": plant.location.lat, "lon": plant.location.lon})
                        )

    def collect_capex_investments(self, year: Year):
        """
        Collect CAPEX investments by technology and location for newly operating plants.

        Tracks total capital expenditure (CAPEX) for furnace groups that started operating
        in the given year. This includes both new greenfield plants and renovations/technology
        switches in existing plants.

        CAPEX is recorded in the year the plant becomes operational, even though the actual
        investment was made during construction (construction_time years earlier).

        Args:
            year: The year to collect CAPEX data for

        Returns:
            dict: {technology: {iso3: total_capex_in_usd}}
        """
        logger = logging.getLogger(f"{__name__}.collect_capex_investments")
        capex_by_tech_and_location: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

        # Collect from all plant groups to capture both new plants and renovations
        for pg in self.plant_groups:
            for plant in pg.plants:
                iso3 = plant.location.iso3

                for fg in plant.furnace_groups:
                    # Track CAPEX for furnace groups that started operating this year
                    if fg.status == "operating" and fg.lifetime.start == year:
                        technology = fg.technology.name
                        capex = fg.total_investment

                        if capex > 0:
                            capex_by_tech_and_location[technology][iso3] += capex
                            logger.debug(
                                f"[CAPEX] Year {year}: {technology} in {iso3} - "
                                f"${capex:,.0f} (capacity: {fg.capacity:,.0f} t/yr)"
                            )

        # Store in trace_capex for later analysis
        if capex_by_tech_and_location:
            for tech, locations in capex_by_tech_and_location.items():
                for iso3, capex in locations.items():
                    self.trace_capex[year][tech][iso3] += capex

            # Log summary
            total_capex = sum(sum(locations.values()) for locations in capex_by_tech_and_location.values())
            logger.info(
                f"[CAPEX] Year {year}: Total CAPEX = ${total_capex:,.0f} across "
                f"{len(capex_by_tech_and_location)} technologies"
            )

        return dict(capex_by_tech_and_location)

    def collect_emissions_by_technology(self, year: Year):
        """
        Collect emissions by boundary, technology and scope, plus production by product, for the given year.

        Aggregates emissions from all operating furnace groups by technology type, keeping
        the three scopes (``direct_ghg``, ``direct_with_biomass_ghg``, ``indirect_ghg``)
        separate so downstream charts can present each view (or sums of compatible views)
        without double-counting. ``direct_ghg`` and ``direct_with_biomass_ghg`` are
        alternative accountings of the same direct emissions and must never be added
        together.

        Every boundary present on a furnace group's ``emissions`` dict is recorded — not
        just the configured carbon-cost boundary — so charts can be produced per boundary
        without re-walking the plant graph. The same iteration also accumulates production
        by product (iron / steel) into ``trace_production_by_product``.

        Args:
            year: The year to collect emissions data for

        Returns:
            dict: {boundary: {technology: {scope: total_emissions_tCO2e}}}.
        """
        logger = logging.getLogger(f"{__name__}.collect_emissions_by_technology")
        scopes = ("direct_ghg", "direct_with_biomass_ghg", "indirect_ghg")
        emissions_by_boundary: dict[str, dict[str, dict[str, float]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(float))
        )
        production_by_product: dict[str, float] = defaultdict(float)

        for pg in self.plant_groups:
            for plant in pg.plants:
                for fg in plant.furnace_groups:
                    if fg.status.lower() not in self.env.config.active_statuses:
                        continue

                    product = (fg.technology.product or "").lower() if fg.technology.product else ""
                    if product in ("iron", "steel") and fg.production:
                        production_by_product[product] += fg.production

                    if not fg.emissions:
                        continue

                    technology = fg.technology.name
                    for boundary, boundary_data in fg.emissions.items():
                        if not isinstance(boundary_data, dict):
                            continue
                        for scope in scopes:
                            value = boundary_data.get(scope)
                            if value is None:
                                continue
                            emissions_by_boundary[boundary][technology][scope] += value

        for boundary, by_tech in emissions_by_boundary.items():
            for tech, by_scope in by_tech.items():
                for scope, value in by_scope.items():
                    self.trace_emissions[boundary][year][tech][scope] += value

            total_direct_plus_indirect = sum(
                by_scope.get("direct_ghg", 0.0) + by_scope.get("indirect_ghg", 0.0) for by_scope in by_tech.values()
            )
            logger.info(
                f"[EMISSIONS] Year {year} ({boundary}): direct+indirect = "
                f"{total_direct_plus_indirect:,.0f} tCO2e across {len(by_tech)} technologies"
            )

        for product, tonnes in production_by_product.items():
            self.trace_production_by_product[year][product] += tonnes

        return {
            boundary: {tech: dict(by_scope) for tech, by_scope in by_tech.items()}
            for boundary, by_tech in emissions_by_boundary.items()
        }

    def collect_iron_ore_by_quality(self, year: Year):
        """
        Collect iron ore consumption by quality for the given year.

        Aggregates iron ore/pellets consumption from all operating furnace groups by quality type.
        Tracks pellets_high, pellets_mid, pellets_low, and other iron ore materials.

        Args:
            year: The year to collect iron ore data for

        Returns:
            dict: {quality: total_consumption_tonnes}
        """
        logger = logging.getLogger(f"{__name__}.collect_iron_ore_by_quality")
        iron_ore_by_quality: dict[str, float] = defaultdict(float)

        # Keywords to identify iron ore and pellet materials
        iron_ore_keywords = [
            "io_",
            "iron_ore",
        ]

        for pg in self.plant_groups:
            for plant in pg.plants:
                for fg in plant.furnace_groups:
                    # Only collect from operating furnace groups
                    if fg.status.lower() not in self.env.config.active_statuses:
                        continue

                    # Check bill of materials for iron ore/pellets
                    if not fg.bill_of_materials or "materials" not in fg.bill_of_materials:
                        continue

                    materials = fg.bill_of_materials["materials"]
                    if not materials:
                        continue

                    # Iterate through materials to find iron ore/pellets
                    for material_name, material_data in materials.items():
                        material_lower = material_name.lower()

                        # Check if this is an iron ore related material
                        is_iron_ore = any(keyword in material_lower for keyword in iron_ore_keywords)

                        if is_iron_ore:
                            # Get the demand (total consumption in tonnes)
                            demand = material_data.get("demand", 0)
                            if demand and demand > 0:
                                # Use the material name as the quality identifier
                                iron_ore_by_quality[material_name] += demand

        # Store in trace_iron_ore for later analysis
        if iron_ore_by_quality:
            for quality, consumption in iron_ore_by_quality.items():
                self.trace_iron_ore[year][quality] += consumption

            # Log summary
            total_consumption = sum(iron_ore_by_quality.values())
            logger.info(
                f"[IRON ORE] Year {year}: Total consumption = {total_consumption:,.0f} tonnes across "
                f"{len(iron_ore_by_quality)} qualities"
            )

        return dict(iron_ore_by_quality)

    def collect_metallic_charges(self, year: Year):
        """
        Collect metallic charge consumption for the given year.

        Aggregates consumption of all metallic charges from operating furnace groups.
        Uses the metallic_charge field from each technology's primary feedstocks to
        dynamically identify what materials are metallic charges.

        Args:
            year: The year to collect metallic charge data for

        Returns:
            dict: {charge_type: total_consumption_tonnes}
        """
        logger = logging.getLogger(f"{__name__}.collect_metallic_charges")
        metallic_charges: dict[str, float] = defaultdict(float)

        for pg in self.plant_groups:
            for plant in pg.plants:
                for fg in plant.furnace_groups:
                    # Only collect from operating furnace groups
                    if fg.status.lower() not in self.env.config.active_statuses:
                        continue

                    # Get the metallic charges from this furnace group's primary feedstocks
                    feedstock_metallic_charges = set()
                    for feedstock in fg.effective_primary_feedstocks:
                        if feedstock.metallic_charge:
                            feedstock_metallic_charges.add(feedstock.metallic_charge.lower())

                    if not feedstock_metallic_charges:
                        continue

                    # Check bill of materials for these metallic charges
                    if not fg.bill_of_materials or "materials" not in fg.bill_of_materials:
                        continue

                    materials = fg.bill_of_materials["materials"]
                    if not materials:
                        continue

                    # Collect consumption for materials that match metallic charges
                    for material_name, material_data in materials.items():
                        material_lower = material_name.lower()

                        # Check if this material is one of the metallic charges for this technology
                        if material_lower in feedstock_metallic_charges:
                            # Get the demand (total consumption in tonnes)
                            demand = material_data.get("demand", 0)
                            if demand and demand > 0:
                                metallic_charges[material_name] += demand

        # Store in trace_metallic_charges for later analysis
        if metallic_charges:
            for charge_type, consumption in metallic_charges.items():
                self.trace_metallic_charges[year][charge_type] += consumption

            # Log summary
            total_consumption = sum(metallic_charges.values())
            logger.info(
                f"[METALLIC CHARGES] Year {year}: Total consumption = {total_consumption:,.0f} tonnes across "
                f"{len(metallic_charges)} charge types"
            )

        return dict(metallic_charges)

    def collect_international_iron_trade(self, year: Year, trade_allocations):
        """
        Collect international trade volumes for iron products.

        Analyzes commodity flows from the trade optimization model and identifies
        international trade (flows where source and destination are in different countries).
        Only tracks iron products as defined in IRON_PRODUCTS constant.

        Args:
            year: The year to collect trade data for
            trade_allocations: Allocations object from trade LP solution containing
                              dict of (ProcessCenter_from, ProcessCenter_to, Commodity) -> volume

        Returns:
            dict: {iron_product: total_international_trade_volume_tonnes}
        """
        logger = logging.getLogger(f"{__name__}.collect_international_iron_trade")

        # Import here to avoid circular dependency
        from steelo.domain.constants import IRON_PRODUCTS

        international_trade: dict[str, float] = defaultdict(float)

        if trade_allocations is None:
            logger.warning(f"[INTERNATIONAL IRON TRADE] Year {year}: No trade allocations provided")
            return dict(international_trade)

        # Check if trade_allocations has the expected structure
        if not hasattr(trade_allocations, "allocations"):
            logger.warning(f"[INTERNATIONAL IRON TRADE] Year {year}: trade_allocations missing 'allocations' attribute")
            return dict(international_trade)

        # Iterate through all allocations: (from_pc, to_pc, commodity) -> volume
        for (from_pc, to_pc, commodity), volume in trade_allocations.allocations.items():
            # Skip if volume is negligible
            if volume < 1e-6:
                continue

            # Get commodity name
            commodity_name = commodity.name if hasattr(commodity, "name") else str(commodity)
            commodity_name_lower = commodity_name.lower()

            # Only track iron products
            if commodity_name_lower not in [ip.lower() for ip in IRON_PRODUCTS]:
                continue

            # Check if this is international trade (different countries)
            from_iso3 = from_pc.location.iso3 if hasattr(from_pc, "location") else None
            to_iso3 = to_pc.location.iso3 if hasattr(to_pc, "location") else None

            if from_iso3 is None or to_iso3 is None:
                logger.debug(
                    f"[INTERNATIONAL IRON TRADE] Skipping flow: missing ISO3 codes (from={from_iso3}, to={to_iso3})"
                )
                continue

            # Only count international flows
            if from_iso3 != to_iso3:
                international_trade[commodity_name_lower] += volume
                logger.debug(
                    f"[INTERNATIONAL IRON TRADE] {commodity_name_lower}: {from_iso3} -> {to_iso3}: {volume:,.0f} tonnes"
                )

        # Store in trace_international_iron_trade for later analysis
        if international_trade:
            for product, volume in international_trade.items():
                self.trace_international_iron_trade[year][product] += volume

            # Log summary
            total_trade = sum(international_trade.values())
            logger.info(
                f"[INTERNATIONAL IRON TRADE] Year {year}: Total = {total_trade:,.0f} tonnes "
                f"across {len(international_trade)} iron products"
            )
            logger.info(
                f"[INTERNATIONAL IRON TRADE] Year {year}: Products traded: "
                f"{', '.join(f'{k}={v:,.0f}t' for k, v in sorted(international_trade.items()))}"
            )
        else:
            logger.info(f"[INTERNATIONAL IRON TRADE] Year {year}: No international iron trade detected")

        return dict(international_trade)

    def collect(self, world_plant_list: list[Plant], world_plant_groups: list[PlantGroup], year):
        """
        Execute the data collection process
        """
        # Update our own attributes:
        self.plant_groups = world_plant_groups
        self.capacity_by_technology_and_PAM_status[self.step] = self.collect_capacity_by_technology_and_PAM_status()
        self.plant_emissions[self.step] = self.collect_emissions_by_plants().copy()
        self.collect_new_plant_data(self.env.year)
        self.collect_capex_investments(self.env.year)
        self.collect_emissions_by_technology(self.env.year)
        self.collect_iron_ore_by_quality(self.env.year)
        self.collect_metallic_charges(self.env.year)

        # Authoritative plant -> group lookup from the live PlantGroup objects (not derived from
        # parent_gem_id string parsing).
        collect_logger = logging.getLogger(f"{__name__}.collect")
        pg_by_plant_id: dict[str, PlantGroup] = {plant.plant_id: pg for pg in world_plant_groups for plant in pg.plants}

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
                    "furnace_group_profit_and_loss": fg.historic_balance,
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
                            "unit_carbon_cost_contribution - co2_slip": fg.co2_slip_carbon_cost_contribution,
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

                    # Subsidy tracking - calculate $/t (per unit production)
                    unit_subsidies: dict[str, float] = {
                        "capex": 0.0,
                        "opex": 0.0,
                        "debt": 0.0,
                    }
                    if hasattr(fg, "applied_subsidies") and fg.applied_subsidies:
                        # CAPEX subsidies (use base capex from technology for relative)
                        capex_base = fg.technology.capex_no_subsidy if fg.technology.capex_no_subsidy else 0
                        for sub in fg.applied_subsidies.get("capex", []):
                            if sub.subsidy_type == "absolute":
                                unit_subsidies["capex"] += sub.subsidy_amount
                            elif sub.subsidy_type == "relative" and capex_base > 0:
                                unit_subsidies["capex"] += capex_base * sub.subsidy_amount

                        # OPEX subsidies (use unit_total_opex_no_subsidy for relative)
                        opex_base = fg.unit_total_opex_no_subsidy if hasattr(fg, "unit_total_opex_no_subsidy") else 0
                        for sub in fg.applied_subsidies.get("opex", []):
                            if sub.subsidy_type == "absolute":
                                unit_subsidies["opex"] += sub.subsidy_amount
                            elif sub.subsidy_type == "relative" and opex_base > 0:
                                unit_subsidies["opex"] += opex_base * sub.subsidy_amount

                        # Debt subsidies (only absolute - relative affects interest rate, not $/t)
                        for sub in fg.applied_subsidies.get("debt", []):
                            if sub.subsidy_type == "absolute":
                                unit_subsidies["debt"] += sub.subsidy_amount

                    # Energy subsidies: (price_before - price_after) * consumption_per_tonne
                    if hasattr(fg, "energy_costs_no_subsidy") and fg.energy_costs_no_subsidy:
                        for carrier, price_before in fg.energy_costs_no_subsidy.items():
                            price_after = fg.energy_costs.get(carrier, 0) if fg.energy_costs else 0
                            carrier_data = energy.get(carrier, {}) if energy else {}
                            if carrier_data.get("unit_cost", 0) > 0 and fg.production > 0:
                                per_t = carrier_data.get("demand", 0) / fg.production
                                unit_subsidies[carrier] = (price_before - price_after) * per_t

                    for key, value in unit_subsidies.items():
                        record[f"unit_subsidy_{key}"] = value
                    record["unit_subsidy_total"] = sum(unit_subsidies.values())

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
                        "unit_carbon_cost_contribution - co2_slip": None,
                        "unit_secondary_outputs_revenue": None,
                        "unit_fopex": fg.unit_fopex,
                        "unit_debt_repayment": fg.unit_current_debt_repayment,
                        "unit_production_cost": fg.unit_production_cost,
                        "debt_repayment_per_year": fg.debt_repayment_per_year,
                        "debt_repayment_for_current_year": fg.debt_repayment_for_current_year,
                        "furnace_group_profit_and_loss": fg.historic_balance,
                        "unit_subsidy_capex": 0.0,
                        "unit_subsidy_opex": 0.0,
                        "unit_subsidy_debt": 0.0,
                        "unit_subsidy_total": 0.0,
                    }
                plant_dict.append(record)

            pg = pg_by_plant_id.get(p.plant_id)
            if pg is None:
                collect_logger.warning(
                    "[ORPHAN PLANT] plant_id=%s parent_gem_id=%s ultimate_plant_group=%s",
                    p.plant_id,
                    p.parent_gem_id,
                    p.ultimate_plant_group,
                )
                plant_group_id: str | None = p.ultimate_plant_group
                plant_group_balance: float | None = None
            else:
                plant_group_id = pg.plant_group_id
                plant_group_balance = pg.balance

            plants[p.plant_id] = {
                "furnace_groups": plant_dict,
                "plant_group_id": plant_group_id,
                "location": p.location.iso3,
                "plant_profit_and_loss": sum(fg.historic_balance for fg in p.furnace_groups),
                "plant_group_balance": plant_group_balance,
            }

        # Ensure the TM directory exists
        tm_dir = self.output_dir / "TM"
        tm_dir.mkdir(parents=True, exist_ok=True)

        with open(tm_dir / f"datacollection_post_allocation_{year}.pkl", "wb") as f:
            pickle.dump(plants, f)
