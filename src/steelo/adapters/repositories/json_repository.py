import json
import logging
from pathlib import Path
from datetime import date
from typing import Self, List, Iterable, Optional, Any
from dataclasses import dataclass
from pydantic import BaseModel, Field, model_validator, field_validator

from .interface import (
    PlantRepository,
    FurnaceGroupRepository,
    DemandCenterRepository,
    PlantGroupRepository,
    SupplierRepository,
)
from .metadata_loader import MetadataProvider, JsonMetadata
from ...domain import (
    BiomassAvailability,
    Plant,
    Volumes,
    Year,
    Location,
    Technology,
    FurnaceGroup,
    PointInTime,
    TimeFrame,
    DemandCenter,
    PlantGroup,
    Supplier,
    TradeTariff,
    CarbonCostSeries,
    InputCosts,
    RegionEmissivity,
    Capex,
    CostOfCapital,
    RailwayCost,
    Subsidy,
)
from ...domain.models import (
    ProductionThreshold,
    PrimaryFeedstock,
    LegalProcessConnector,
    CountryMapping,
    HydrogenEfficiency,
    HydrogenCapexOpex,
    TransportKPI,
    TechnologyEmissionFactors,
    FOPEX,
    CarbonBorderMechanism,
    FallbackMaterialCost,
)
from ...domain.constants import Commodities

logger = logging.getLogger(__name__)


class LocationInDb(BaseModel):
    iso3: str | None
    country: str
    region: str
    lat: float
    lon: float


class PrimaryFeedstockInDb(BaseModel):
    """
    Pydantic model for serializing/deserializing a PrimaryFeedstock to/from JSON.
    """

    name: Optional[str] = None
    metallic_charge: str
    reductant: Optional[str]
    technology: str
    required_quantity_per_ton_of_product: Optional[float] = None
    secondary_feedstock: dict[str, float] = Field(default_factory=dict)
    energy_requirements: dict[str, float] = Field(default_factory=dict)
    maximum_share_in_product: Optional[float] = None
    minimum_share_in_product: Optional[float] = None
    outputs: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def compute_name_if_missing(self) -> "PrimaryFeedstockInDb":
        """Compute the name field if it's missing (for backward compatibility)."""
        if self.name is None:
            reductant_str = self.reductant or ""
            self.name = f"{self.technology}_{self.metallic_charge}_{reductant_str}".lower()
        return self

    @property
    def to_domain(self) -> PrimaryFeedstock:
        """
        Convert this DB model into the domain‐level `PrimaryFeedstock` object.
        """
        obj = PrimaryFeedstock(
            metallic_charge=self.metallic_charge,
            reductant=self.reductant or "",
            technology=self.technology,
        )
        obj.required_quantity_per_ton_of_product = self.required_quantity_per_ton_of_product  # type: ignore
        obj.secondary_feedstock = self.secondary_feedstock.copy()
        obj.energy_requirements = self.energy_requirements.copy()
        obj.maximum_share_in_product = self.maximum_share_in_product
        obj.minimum_share_in_product = self.minimum_share_in_product
        obj.outputs = self.outputs.copy()
        return obj

    @classmethod
    def from_domain(cls, pf: PrimaryFeedstock) -> "PrimaryFeedstockInDb":
        """
        Create a PrimaryFeedstockInDb (Pydantic model) from a domain‐level PrimaryFeedstock instance.
        """
        return cls(
            name=pf.name,
            metallic_charge=pf.metallic_charge,
            reductant=pf.reductant,
            technology=pf.technology,
            required_quantity_per_ton_of_product=pf.required_quantity_per_ton_of_product,
            secondary_feedstock=pf.secondary_feedstock.copy(),
            energy_requirements=pf.energy_requirements.copy(),
            maximum_share_in_product=pf.maximum_share_in_product,
            minimum_share_in_product=pf.minimum_share_in_product,
            outputs=pf.outputs.copy(),
        )

    def __lt__(self, other: "PrimaryFeedstockInDb") -> bool:
        """
        Enable sorting by `name` for stable JSON dumps.
        """
        # Handle None values
        if self.name is None and other.name is None:
            return False
        if self.name is None:
            return True
        if other.name is None:
            return False
        return self.name < other.name


class PrimaryFeedstockListInDb(BaseModel):
    """
    Root model wrapping a list of PrimaryFeedstockInDb, for JSON (de)serialization.
    """

    root: List[PrimaryFeedstockInDb]


class TechnologyInDb(BaseModel):
    name: str
    product: str
    technology_readiness_level: int | None
    process_emissions: float | None = None
    dynamic_business_case: list[PrimaryFeedstockInDb] = Field(default_factory=list)
    energy_consumption: float | None
    bill_of_materials: dict[str, dict[str, dict[str, float]]] | None = None
    lcop: float | None = None
    capex_type: str | None = None


class TimeFrameInDb(BaseModel):
    start: int
    end: int


class PointInTimeInDb(BaseModel):
    current: int
    time_frame: TimeFrameInDb


class ProductionThresholdInDb(BaseModel):
    low: float
    high: float


class FurnaceGroupInDb(BaseModel):
    furnace_group_id: str
    capacity: Volumes
    status: str
    last_renovation_date: date | None
    technology: TechnologyInDb
    historical_production: dict[Year, Volumes]
    utilization_rate: float
    lifetime: PointInTimeInDb
    production_threshold: ProductionThresholdInDb
    equity_share: float
    cost_of_debt: float
    emissions_factor: float | None
    bill_of_materials: dict[str, dict[str, dict[str, float]]]
    energy_costs: dict
    chosen_reductant: str
    energy_vopex_by_input: dict[str, float]
    energy_vopex_breakdown_by_input: dict[str, dict[str, float]] | None = None
    energy_vopex_by_carrier: dict[str, float] | None = None
    tech_unit_fopex: float
    balance: float
    historic_balance: float
    allocated_volumes: float
    carbon_costs_for_emissions: float = 0.0
    emissions: dict | None
    created_by_PAM: bool

    def to_domain(self, plant_lifetime: int) -> FurnaceGroup:
        # Process dynamic business cases from InDb to domain format
        dynamic_business_cases = []
        for dbc_in_db in self.technology.dynamic_business_case:
            dbc_dict = dbc_in_db.model_dump()
            feedstock = PrimaryFeedstock(**dbc_dict)
            feedstock.energy_requirements = dbc_dict.get("energy_requirements") or {}
            feedstock.outputs = dbc_dict.get("outputs") or {}
            feedstock.secondary_feedstock = dbc_dict.get("secondary_feedstock") or {}
            feedstock.maximum_share_in_product = dbc_dict.get("maximum_share_in_product")
            feedstock.minimum_share_in_product = dbc_dict.get("minimum_share_in_product")
            feedstock.required_quantity_per_ton_of_product = dbc_dict.get("required_quantity_per_ton_of_product")
            dynamic_business_cases.append(feedstock)

        # Determine product (handle liquid_steel -> steel normalization)
        product = self.technology.product
        if product.lower() == Commodities.LIQUID_STEEL.value.lower():
            product = Commodities.STEEL.value

        return FurnaceGroup(
            furnace_group_id=self.furnace_group_id,
            capacity=self.capacity,
            status=self.status,
            last_renovation_date=self.last_renovation_date,
            technology=Technology(
                name=self.technology.name,
                product=product,
                technology_readiness_level=self.technology.technology_readiness_level,
                process_emissions=self.technology.process_emissions,
                dynamic_business_case=dynamic_business_cases if dynamic_business_cases else None,
                energy_consumption=self.technology.energy_consumption,
                bill_of_materials=(
                    {} if self.technology.bill_of_materials is None else self.technology.bill_of_materials
                ),
                lcop=self.technology.lcop,
                capex_type=self.technology.capex_type,
            ),
            historical_production=self.historical_production,
            utilization_rate=self.utilization_rate,
            lifetime=PointInTime(
                current=Year(self.lifetime.current),
                time_frame=TimeFrame(
                    start=Year(self.lifetime.time_frame.start), end=Year(self.lifetime.time_frame.end)
                ),
                plant_lifetime=plant_lifetime,
            ),
            production_threshold=ProductionThreshold(
                low=self.production_threshold.low, high=self.production_threshold.high
            ),
            equity_share=self.equity_share,
            cost_of_debt=self.cost_of_debt,
            energy_cost_dict=self.energy_costs,
            emissions_factor=None,  # Convert from float to dict format in domain
            bill_of_materials=self.bill_of_materials,
            chosen_reductant=self.chosen_reductant,
            energy_vopex_by_input=self.energy_vopex_by_input,
            energy_vopex_breakdown_by_input=self.energy_vopex_breakdown_by_input or {},
            energy_vopex_by_carrier=self.energy_vopex_by_carrier or {},
            tech_unit_fopex=self.tech_unit_fopex,
            balance=self.balance,
            historic_balance=self.historic_balance,
            allocated_volumes=self.allocated_volumes,
            carbon_costs_for_emissions=self.carbon_costs_for_emissions,
            emissions=self.emissions,
        )

    def to_domain_with_metadata(
        self, plant_lifetime: int, metadata: MetadataProvider, current_simulation_year: int
    ) -> FurnaceGroup:
        """
        Reconstruct FurnaceGroup with runtime-specified plant_lifetime.
        Uses metadata to recalculate PointInTime based on canonical facts.

        Args:
            plant_lifetime: Desired plant lifetime for this simulation
            metadata: Metadata provider for furnace group canonical facts
            current_simulation_year: Current year in simulation

        Returns:
            FurnaceGroup with reconstructed lifetime

        Raises:
            ValueError: If no metadata found for furnace group or age data is missing
        """
        meta = metadata.get_furnace_group_metadata(self.furnace_group_id)

        if not meta:
            raise ValueError(
                f"No metadata found for {self.furnace_group_id}. "
                f"Cannot reconstruct with variable plant_lifetime. "
                f"Please regenerate data with metadata support."
            )

        # Calculate age at current simulation year
        data_ref_year = metadata.get_data_reference_year()
        years_since_ref = current_simulation_year - data_ref_year

        if meta.get("commissioning_year"):
            # Known commissioning - calculate from commissioning year
            base_year = meta["commissioning_year"]
            age_at_current = current_simulation_year - base_year
        elif meta.get("age_at_reference_year") is not None:
            # Imputed age - offset from reference year
            age_at_current = meta["age_at_reference_year"] + years_since_ref
        else:
            source_sheet = meta.get("source_sheet", "unknown")
            source_row = meta.get("source_row", "?")
            raise ValueError(f"No age data for {self.furnace_group_id}. Source: {source_sheet}:{source_row}")

        # Account for last renovation if known
        if meta.get("last_renovation_year"):
            age_at_current = current_simulation_year - meta["last_renovation_year"]

        # Handle future plants (not yet commissioned)
        if age_at_current <= 0:
            # Plant hasn't started operations yet - use commissioning year as cycle start
            if meta.get("commissioning_year"):
                cycle_start = meta["commissioning_year"]
            elif meta.get("age_at_reference_year") is not None:
                # Implied commissioning year from imputed age
                cycle_start = data_ref_year - meta["age_at_reference_year"]
            else:
                raise ValueError(f"Cannot determine commissioning year for future plant {self.furnace_group_id}")
            cycle_end = cycle_start + plant_lifetime
        else:
            # Plant is operating - calculate position in current cycle
            cycle_position = age_at_current % plant_lifetime

            # Build fresh PointInTime
            if cycle_position == 0 and age_at_current > 0:
                # At renovation boundary
                cycle_start = current_simulation_year
                cycle_end = current_simulation_year + plant_lifetime
            else:
                cycle_start = current_simulation_year - cycle_position
                cycle_end = cycle_start + plant_lifetime

        # Reconstruct PointInTime with new lifetime
        reconstructed_lifetime = PointInTime(
            plant_lifetime=plant_lifetime,
            current=Year(current_simulation_year),
            time_frame=TimeFrame(start=Year(cycle_start), end=Year(cycle_end)),
        )

        # Create FurnaceGroup using standard to_domain, then replace lifetime
        fg = self.to_domain(plant_lifetime)
        fg.lifetime = reconstructed_lifetime

        # Recalculate dependent properties using existing domain logic
        fg.set_is_first_renovation_cycle()

        return fg

    @classmethod
    def from_domain(cls, furnace_group: FurnaceGroup) -> Self:
        technology_data = furnace_group.technology.__dict__.copy()
        if technology_data["bill_of_materials"] is not None:
            technology_data["bill_of_materials"] = dict(technology_data["bill_of_materials"])
        production_threshold_data = furnace_group.production_threshold.__dict__.copy()
        return cls(
            furnace_group_id=furnace_group.furnace_group_id,
            capacity=furnace_group.capacity,
            status=furnace_group.status,
            last_renovation_date=furnace_group.last_renovation_date,
            technology=TechnologyInDb(**technology_data),
            historical_production=furnace_group.historical_production,
            utilization_rate=furnace_group.utilization_rate,
            lifetime=PointInTimeInDb(
                current=furnace_group.lifetime.current,
                time_frame=TimeFrameInDb(
                    start=furnace_group.lifetime.start,
                    end=furnace_group.lifetime.end,
                ),
            ),
            production_threshold=ProductionThresholdInDb(**production_threshold_data),
            equity_share=furnace_group.equity_share,
            cost_of_debt=furnace_group.cost_of_debt,
            emissions_factor=getattr(furnace_group, "emissions_factor", 0.0),
            bill_of_materials=getattr(furnace_group, "bill_of_materials", {}),
            energy_costs=furnace_group.energy_costs,
            chosen_reductant=getattr(furnace_group, "chosen_reductant", ""),
            energy_vopex_by_input=dict(getattr(furnace_group, "energy_vopex_by_input", {})),
            energy_vopex_breakdown_by_input={
                str(feed): dict(carriers)
                for feed, carriers in getattr(furnace_group, "energy_vopex_breakdown_by_input", {}).items()
            },
            energy_vopex_by_carrier=dict(getattr(furnace_group, "energy_vopex_by_carrier", {})),
            tech_unit_fopex=getattr(furnace_group, "tech_unit_fopex", 0.0),
            balance=getattr(furnace_group, "balance", 0.0),
            historic_balance=getattr(furnace_group, "historic_balance", 0.0),
            allocated_volumes=getattr(furnace_group, "allocated_volumes", 0.0),
            carbon_costs_for_emissions=getattr(furnace_group, "carbon_costs_for_emissions", 0.0),
            emissions=getattr(furnace_group, "emissions", {}),
            created_by_PAM=getattr(furnace_group, "created_by_PAM", False),
        )


class PlantInDb(BaseModel):
    plant_id: str
    location: LocationInDb
    furnace_groups: list[FurnaceGroupInDb]
    power_source: str
    soe_status: str
    parent_gem_id: str
    workforce_size: int
    certified: bool
    category_steel_product: set[str]
    average_steel_cost: float | None
    steel_capacity: Volumes | None
    technology_unit_fopex: dict[str, float] | None = None

    def __lt__(self, other: Self) -> bool:
        """
        Make PlantInDb sortable by id. This is useful to keep the order of plants in the json file.
        Which is useful for diffing the file when the list of plants is updated.
        """
        return self.plant_id < other.plant_id

    def to_domain(
        self,
        plant_lifetime: int,
        metadata: MetadataProvider | None = None,
        current_simulation_year: int | None = None,
    ) -> Plant:
        """
        Convert to domain Plant object.

        Args:
            plant_lifetime: Desired plant lifetime for this simulation
            metadata: Optional metadata provider for reconstruction
            current_simulation_year: Required if metadata provided

        Returns:
            Plant domain object

        Raises:
            ValueError: If metadata provided but current_simulation_year is None
        """
        if metadata and current_simulation_year is None:
            raise ValueError("current_simulation_year required when metadata is provided")

        location = Location(**self.location.model_dump())
        furnace_groups = []

        for fg in self.furnace_groups:
            if metadata and current_simulation_year:
                # Reconstruct with metadata - this handles lifetime correctly
                fg_domain = fg.to_domain_with_metadata(plant_lifetime, metadata, current_simulation_year)
            else:
                # Legacy path - use baked values from JSON
                fg_domain = fg.to_domain(plant_lifetime)

            furnace_groups.append(fg_domain)

        plant_data = self.model_dump()
        plant_data["location"] = location
        plant_data["furnace_groups"] = furnace_groups
        # Handle missing technology_unit_fopex for backward compatibility
        if "technology_unit_fopex" not in plant_data or plant_data["technology_unit_fopex"] is None:
            # Provide default technology_unit_fopex values TODO: hardcoded remove?
            plant_data["technology_unit_fopex"] = {
                "EAF": 10.0,
                "DRI": 15.0,
                "BF": 20.0,
                "BOF": 10.0,
                "ESF": 10.0,
                "MOE": 10.0,
                "Other": 150.0,
                "unknown": 150.0,
                "Prep Sinter": 5.0,
                "Prep Pellet": 5.0,
                "Prep Coke": 5.0,
            }
        return Plant(**plant_data)

    @classmethod
    def from_domain(cls, plant: Plant) -> Self:
        plant_data = plant.__dict__
        plant_data["location"] = LocationInDb(**plant_data["location"].__dict__)
        furnace_groups_in_db = []
        for fg in plant_data["furnace_groups"]:
            fg_data = fg.__dict__
            technology_data = fg_data["technology"].__dict__.copy()
            if not technology_data["bill_of_materials"]:
                technology_data["bill_of_materials"] = dict()
            dynamic_business_cases = []
            if technology_data["dynamic_business_case"]:
                for dbc in technology_data["dynamic_business_case"]:
                    dbc_dict = dbc.__dict__.copy()
                    # Remove emissions to avoid storage bloat - will be calculated dynamically
                    dbc_dict.pop("emissions", None)
                    dynamic_business_cases.append(PrimaryFeedstockInDb(**dbc_dict))
            technology_data["dynamic_business_case"] = dynamic_business_cases
            fg_data["technology"] = TechnologyInDb(**technology_data)
            lifetime = fg_data["lifetime"]
            if isinstance(lifetime.time_frame, TimeFrame):
                time_frame_in_db = TimeFrameInDb(start=lifetime.start, end=lifetime.end)
            elif isinstance(lifetime.time_frame, dict):
                # FIXME 2025-05-26 - this should not happen, Jochen
                time_frame_in_db = TimeFrameInDb(**lifetime.time_frame)
            else:
                raise ValueError("unexpected time frame type")
            fg_data["lifetime"] = PointInTimeInDb(current=lifetime.current, time_frame=time_frame_in_db)
            production_threshold = fg_data["production_threshold"]
            if isinstance(production_threshold, dict):
                # FIXME 2025-05-26 - this should not happen, Jochen
                fg_data["production_threshold"] = ProductionThresholdInDb(**fg_data["production_threshold"].copy())
            else:
                fg_data["production_threshold"] = ProductionThresholdInDb(
                    **fg_data["production_threshold"].__dict__.copy()
                )
            # Handle missing carbon_costs_for_emissions (removed in refactoring)
            if "carbon_costs_for_emissions" not in fg_data:
                fg_data["carbon_costs_for_emissions"] = 0.0
            # Handle missing or None bill_of_materials
            if fg_data.get("bill_of_materials") is None:
                fg_data["bill_of_materials"] = {}
            furnace_groups_in_db.append(FurnaceGroupInDb(**fg_data))

        plant_data["furnace_groups"] = furnace_groups_in_db
        # technology_unit_fopex should always be present in Plant objects
        return cls(**plant_data)


class PlantListInDb(BaseModel):
    """
    Used to make a list of plants serializable to json.
    """

    root: list[PlantInDb]


class PlantJsonRepository:
    """
    Repository for storing plants in a json file. Uses pydantic models for serialization / deserialization.
    All input and output is done using domain models. Domain models are converted to / from db models
    and then validated and dumped to json.

    Supports metadata-based plant lifetime reconstruction for variable plant_lifetime.
    """

    _all = None

    def __init__(self, path: Path, plant_lifetime: int, current_simulation_year: int | None = None) -> None:
        """
        Initialize plant repository.

        Args:
            path: Path to plants.json file
            plant_lifetime: Plant lifetime for this simulation
            current_simulation_year: Current simulation year (required for metadata reconstruction)
        """
        self.path = path
        self.plant_lifetime = plant_lifetime
        self.current_simulation_year = current_simulation_year
        self.metadata = self._load_metadata()
        self.seen: set[Plant] = set()

    def _load_metadata(self) -> JsonMetadata | None:
        """
        Load metadata from plants_metadata.json if it exists.

        Returns:
            JsonMetadata instance or None if file doesn't exist
        """
        metadata_path = self.path.parent / "plants_metadata.json"
        if metadata_path.exists():
            try:
                return JsonMetadata(metadata_path)
            except Exception as e:
                logger.warning(f"Failed to load metadata from {metadata_path}: {e}")
                return None
        return None

    def _fetch_all(self) -> dict[str, PlantInDb]:
        try:
            if not self.path.exists():
                return {}
            with self.path.open("r", encoding="utf-8") as f:
                file_content = f.read().strip()

                # Handle empty file or empty list
                if not file_content or file_content == "[]":
                    return {}

                # Try to parse as PlantListInDb first
                try:
                    plants_in_db = PlantListInDb.model_validate_json(file_content)
                    return {plant.plant_id: plant for plant in plants_in_db.root}
                except Exception:
                    # If that fails, try to handle as a direct list
                    import json

                    data = json.loads(file_content)
                    if isinstance(data, list):
                        # Convert list to PlantListInDb format
                        if not data:  # Empty list
                            return {}
                        plants_in_db = PlantListInDb(root=data)
                        return {plant.plant_id: plant for plant in plants_in_db.root}
                    else:
                        # Re-raise the original exception if it's not a list
                        plants_in_db = PlantListInDb.model_validate_json(file_content)
                        return {plant.plant_id: plant for plant in plants_in_db.root}
        except FileNotFoundError:
            return {}

    @property
    def all(self) -> dict[str, PlantInDb]:
        """
        Cache fetching all models from file.
        """
        if self._all is None:
            self._all = self._fetch_all()
        return self._all

    def get(self, plant_id) -> Plant:
        return self.all[plant_id].to_domain(self.plant_lifetime, self.metadata, self.current_simulation_year)

    def get_by_furnace_group_id(self, furnace_group_id: str) -> Plant:
        for plant_in_db in self.all.values():
            for fg in plant_in_db.furnace_groups:
                if fg.furnace_group_id == furnace_group_id:
                    return plant_in_db.to_domain(self.plant_lifetime, self.metadata, self.current_simulation_year)
        raise KeyError(f"No plant with furnace group id {furnace_group_id}")

    def list(self) -> list[Plant]:
        return [
            plant_in_db.to_domain(self.plant_lifetime, self.metadata, self.current_simulation_year)
            for plant_in_db in self.all.values()
        ]

    def _write_models(self, locked: List[PlantInDb]) -> None:  # use List because of list method
        self.path.parent.mkdir(exist_ok=True)
        locked.sort()  # sort by id to keep the order of plants in the file for easier diffing
        plants_in_db = PlantListInDb(root=locked)
        with self.path.open("w", encoding="utf-8") as f:
            f.write(plants_in_db.model_dump_json(indent=2))

    def add(self, plant: Plant) -> None:
        """Add a single plant to the repository."""
        plant_in_db = PlantInDb.from_domain(plant)
        locked = self._fetch_all()
        locked[plant_in_db.plant_id] = plant_in_db
        self._write_models(list(locked.values()))
        self.seen.add(plant)

    def add_list(self, plants: Iterable[Plant]) -> None:
        """Add a list of plant to the repository."""
        plants_in_db = [PlantInDb.from_domain(plant) for plant in plants]
        # locked = self._fetch_all()
        locked = {}
        for plant_in_db in plants_in_db:
            locked[plant_in_db.plant_id] = plant_in_db
        self._write_models(list(locked.values()))


class PlantGroupInDb(BaseModel):
    plant_group_id: str
    plants: list[PlantInDb]

    def to_domain(
        self,
        plant_lifetime: int,
        metadata: MetadataProvider | None = None,
        current_simulation_year: int | None = None,
    ) -> PlantGroup:
        """
        Convert to domain PlantGroup object.

        Args:
            plant_lifetime: Plant lifetime for this simulation
            metadata: Optional metadata provider for reconstruction
            current_simulation_year: Required if metadata provided

        Returns:
            PlantGroup domain object
        """
        return PlantGroup(
            plant_group_id=self.plant_group_id,
            plants=[p.to_domain(plant_lifetime, metadata, current_simulation_year) for p in self.plants],
        )

    @classmethod
    def from_domain(cls, plant_group: PlantGroup) -> Self:
        return cls(
            plant_group_id=plant_group.plant_group_id,
            plants=[PlantInDb.from_domain(p) for p in plant_group.plants],
        )


class PlantGroupListInDb(BaseModel):
    root: list[PlantGroupInDb]


class PlantGroupJsonRepository:
    """
    Repository for storing plant groups in a json file.

    Supports metadata-based plant lifetime reconstruction using shared plants_metadata.json.
    """

    _all = None

    def __init__(self, path: Path, plant_lifetime: int, current_simulation_year: int | None = None) -> None:
        """
        Initialize plant group repository.

        Args:
            path: Path to plant_groups.json file
            plant_lifetime: Plant lifetime for this simulation
            current_simulation_year: Current simulation year (required for metadata reconstruction)
        """
        self.path = path
        self.plant_lifetime = plant_lifetime
        self.current_simulation_year = current_simulation_year
        self.metadata = self._load_metadata()
        self.seen: set[PlantGroup] = set()

    def _load_metadata(self) -> JsonMetadata | None:
        """
        Load metadata from shared plants_metadata.json if it exists.

        Uses the same plants_metadata.json file as PlantJsonRepository.

        Returns:
            JsonMetadata instance or None if file doesn't exist
        """
        metadata_path = self.path.parent / "plants_metadata.json"
        if metadata_path.exists():
            try:
                return JsonMetadata(metadata_path)
            except Exception as e:
                logger.warning(f"Failed to load metadata from {metadata_path}: {e}")
                return None
        return None

    def _fetch_all(self) -> dict[str, PlantGroupInDb]:
        try:
            if not self.path.exists():
                return {}
            with self.path.open("r", encoding="utf-8") as f:
                pgroups = PlantGroupListInDb.model_validate_json(f.read())
            return {pg.plant_group_id: pg for pg in pgroups.root}
        except FileNotFoundError:
            return {}

    @property
    def all(self) -> dict[str, PlantGroupInDb]:
        if self._all is None:
            self._all = self._fetch_all()
        return self._all

    def get(self, plant_group_id) -> PlantGroup:
        return self.all[plant_group_id].to_domain(self.plant_lifetime, self.metadata, self.current_simulation_year)

    def get_by_plant_id(self, plant_id: str) -> PlantGroup:
        for plant_group_in_db in self.all.values():
            for plant_in_db in plant_group_in_db.plants:
                if plant_in_db.plant_id == plant_id:
                    return plant_group_in_db.to_domain(self.plant_lifetime, self.metadata, self.current_simulation_year)
        raise KeyError(f"No plant group found containing plant with id {plant_id}")

    def list(self) -> list[PlantGroup]:
        return [
            pg.to_domain(self.plant_lifetime, self.metadata, self.current_simulation_year) for pg in self.all.values()
        ]

    def _write_models(self, locked: List[PlantGroupInDb]) -> None:
        self.path.parent.mkdir(exist_ok=True)
        locked.sort(key=lambda pg: pg.plant_group_id)
        pgroups = PlantGroupListInDb(root=locked)
        with self.path.open("w", encoding="utf-8") as f:
            f.write(pgroups.model_dump_json(indent=2))

    def add(self, plant_group: PlantGroup) -> None:
        pg_in_db = PlantGroupInDb.from_domain(plant_group)
        locked = self._fetch_all()
        locked[pg_in_db.plant_group_id] = pg_in_db
        self._write_models(list(locked.values()))
        self.seen.add(plant_group)

    def add_list(self, plant_groups: Iterable[PlantGroup]) -> None:
        pgs_in_db = [PlantGroupInDb.from_domain(pg) for pg in plant_groups]
        locked = self._fetch_all()
        for pg in pgs_in_db:
            locked[pg.plant_group_id] = pg
        self._write_models(list(locked.values()))


class FurnaceGroupJsonRepository:
    """
    Repository for storing furnace groups in a json file. Uses pydantic models for serialization / deserialization.
    All input and output is done using domain models. Domain models are converted to / from db models
    and then validated and dumped to json.
    """

    _all = None

    def __init__(self, path: Path, plant_lifetime: int) -> None:
        self.path = path
        self.plant_lifetime = plant_lifetime
        self.seen: set[FurnaceGroup] = set()

    def _fetch_all(self) -> dict[str, FurnaceGroupInDb]:
        try:
            if not self.path.exists():
                return {}
            with self.path.open("r", encoding="utf-8") as f:
                furnace_groups_in_db = [FurnaceGroupInDb.model_validate_json(line) for line in f]
            return {fg.furnace_group_id: fg for fg in furnace_groups_in_db}
        except FileNotFoundError:
            return {}

    @property
    def all(self) -> dict[str, FurnaceGroupInDb]:
        """
        Cache fetching all models from file.
        """
        if self._all is None:
            self._all = self._fetch_all()
        return self._all

    def get(self, furnace_group_id) -> FurnaceGroup:
        return self.all[furnace_group_id].to_domain(self.plant_lifetime)

    def list(self) -> list[FurnaceGroup]:
        return [fg.to_domain(self.plant_lifetime) for fg in self.all.values()]

    def _write_models(self, locked: List[FurnaceGroupInDb]) -> None:
        self.path.parent.mkdir(exist_ok=True)
        locked.sort(
            key=lambda fg: fg.furnace_group_id
        )  # sort by id to keep the order of furnace groups in the file for easier diffing
        with self.path.open("w", encoding="utf-8") as f:
            for fg in locked:
                f.write(fg.model_dump_json(indent=2) + "\n")

    def add(self, furnace_group: FurnaceGroup) -> None:
        """Add a single furnace group to the repository."""
        fg_in_db = FurnaceGroupInDb.from_domain(furnace_group)
        locked = self._fetch_all()
        locked[fg_in_db.furnace_group_id] = fg_in_db
        self._write_models(list(locked.values()))
        self.seen.add(furnace_group)

    def add_list(self, furnace_groups: Iterable[FurnaceGroup]) -> None:
        """Add a list of furnace groups to the repository."""
        fgs_in_db = [FurnaceGroupInDb.from_domain(fg) for fg in furnace_groups]
        locked = self._fetch_all()
        for fg_in_db in fgs_in_db:
            locked[fg_in_db.furnace_group_id] = fg_in_db
        self._write_models(list(locked.values()))

    def to_json(self) -> str:
        """
        Return the JSON content of the furnace groups.
        """
        furnace_groups_in_db = list(self.all.values())
        return "[\n" + ",\n".join(fg.model_dump_json(indent=2) for fg in furnace_groups_in_db) + "\n]"


class DemandCenterInDb(BaseModel):
    demand_center_id: str
    center_of_gravity: LocationInDb
    demand_by_year: dict[Year, Volumes]

    @field_validator("demand_by_year", mode="before")
    @classmethod
    def convert_year_keys(cls, v):
        """Convert string year keys to integers."""
        if isinstance(v, dict):
            return {Year(int(k) if isinstance(k, str) else k): Volumes(val) for k, val in v.items()}
        return v

    @property
    def to_domain(self) -> DemandCenter:
        location = Location(**self.center_of_gravity.model_dump())
        return DemandCenter(
            demand_center_id=self.demand_center_id,
            center_of_gravity=location,
            demand_by_year=self.demand_by_year,
        )

    @classmethod
    def from_domain(cls, demand_center: DemandCenter) -> Self:
        location_in_db = LocationInDb(**demand_center.center_of_gravity.__dict__)
        return cls(
            demand_center_id=demand_center.demand_center_id,
            center_of_gravity=location_in_db,
            demand_by_year=demand_center.demand_by_year,
        )

    def __lt__(self, other: Self) -> bool:
        """
        Make DemandCenterInDb sortable by id. This is useful to keep the order of demand centers in the json file.
        Which is useful for diffing the file when the list of demand centers is updated.
        """
        return self.demand_center_id < other.demand_center_id


class DemandCenterListInDb(BaseModel):
    """
    Used to make a list of plants serializable to json.
    """

    root: list[DemandCenterInDb]


class DemandCenterJsonRepository:
    """
    Repository for storing demand centers in a json file. Uses pydantic models for serialization / deserialization.
    All input and output is done using domain models. Domain models are converted to / from db models
    and then validated and dumped to json.
    """

    _all = None

    def __init__(self, path: Path) -> None:
        self.path = path
        self.seen: set[DemandCenter] = set()

    def _fetch_all_old(self) -> dict[str, DemandCenterInDb]:
        try:
            if not self.path.exists():
                return {}
            with self.path.open("r", encoding="utf-8") as f:
                demand_centers_in_db = [DemandCenterInDb.model_validate_json(line) for line in f]
            return {dc.demand_center_id: dc for dc in demand_centers_in_db}
        except FileNotFoundError:
            return {}

    def _fetch_all(self) -> dict[str, DemandCenterInDb]:
        try:
            if not self.path.exists():
                return {}
            with self.path.open("r", encoding="utf-8") as f:
                demand_centers_in_db = DemandCenterListInDb.model_validate_json(f.read())
            return {dc.demand_center_id: dc for dc in demand_centers_in_db.root}
        except FileNotFoundError:
            return {}

    @property
    def all(self) -> dict[str, DemandCenterInDb]:
        """
        Cache fetching all models from file.
        """
        if self._all is None:
            self._all = self._fetch_all()
        return self._all

    def get(self, demand_center_id) -> DemandCenter:
        return self.all[demand_center_id].to_domain

    def list(self) -> list[DemandCenter]:
        return [dc.to_domain for dc in self.all.values()]

    def _write_models(self, locked: List[DemandCenterInDb]) -> None:  # use List because of list method
        self.path.parent.mkdir(exist_ok=True)
        locked.sort()  # sort by id to keep the order of demand center in the file for easier diffing
        demand_centers_in_db = DemandCenterListInDb(root=locked)
        with self.path.open("w", encoding="utf-8") as f:
            f.write(demand_centers_in_db.model_dump_json(indent=2))

    def add(self, demand_center: DemandCenter) -> None:
        """Add a single demand center to the repository."""
        dc_in_db = DemandCenterInDb.from_domain(demand_center)
        locked = self._fetch_all()
        locked[dc_in_db.demand_center_id] = dc_in_db
        self._write_models(list(locked.values()))
        self.seen.add(demand_center)

    def add_list(self, demand_centers: Iterable[DemandCenter]) -> None:
        """Add a list of demand centers to the repository."""
        dcs_in_db = [DemandCenterInDb.from_domain(dc) for dc in demand_centers]
        # locked = self._fetch_all()
        locked = {}
        for dc_in_db in dcs_in_db:
            locked[dc_in_db.demand_center_id] = dc_in_db
        self._write_models(list(locked.values()))

    def to_json(self) -> str:
        """
        Return the JSON content of the demand centers.
        """
        demand_centers_in_db = list(self.all.values())
        return "[\n" + ",\n".join(dc.model_dump_json(indent=2) for dc in demand_centers_in_db) + "\n]"


class SupplierInDb(BaseModel):
    supplier_id: str
    location: LocationInDb
    commodity: str
    capacity_by_year: dict[Year, Volumes]
    production_cost_by_year: dict[Year, float]
    mine_cost_by_year: dict[Year, float] | None = None
    mine_price_by_year: dict[Year, float] | None = None

    @property
    def to_domain(self) -> Supplier:
        location = Location(**self.location.model_dump())
        return Supplier(
            supplier_id=self.supplier_id,
            location=location,
            commodity=self.commodity,
            capacity_by_year=self.capacity_by_year,
            production_cost_by_year=self.production_cost_by_year,
            mine_cost_by_year=self.mine_cost_by_year,
            mine_price_by_year=self.mine_price_by_year,
        )

    @classmethod
    def from_domain(cls, supplier: Supplier) -> Self:
        location_in_db = LocationInDb(**supplier.location.__dict__)
        return cls(
            supplier_id=supplier.supplier_id,
            location=location_in_db,
            commodity=supplier.commodity,
            capacity_by_year=supplier.capacity_by_year,
            production_cost_by_year=supplier.production_cost_by_year,
            mine_cost_by_year=supplier.mine_cost_by_year,
            mine_price_by_year=supplier.mine_price_by_year,
        )

    def __lt__(self, other: Self) -> bool:
        """Make SupplierInDb sortable by id. Useful for diffing json when the list is updated."""
        return self.supplier_id < other.supplier_id


class SupplierListInDb(BaseModel):
    """Used to make a list of suppliers serializable to json."""

    root: list[SupplierInDb]


class SupplierJsonRepository:
    """Repository for storing suppliers in a json file. Uses pydantic models for serialization / deserialization.
    All input and output is done using domain models. Domain models are converted to/from db models and then validated and dumped to json.
    """

    _all = None

    def __init__(self, path: Path) -> None:
        self.path = path
        self.seen: set[Supplier] = set()

    def _fetch_all(self) -> dict[str, SupplierInDb]:
        try:
            if not self.path.exists():
                return {}
            with self.path.open("r", encoding="utf-8") as f:
                suppliers_in_db = SupplierListInDb.model_validate_json(f.read())
            return {supplier.supplier_id: supplier for supplier in suppliers_in_db.root}
        except FileNotFoundError:
            return {}

    @property
    def all(self) -> dict[str, SupplierInDb]:
        """Cache fetching all models from file."""
        if self._all is None:
            self._all = self._fetch_all()
        return self._all

    def get(self, supplier_id) -> Supplier:
        return self.all[supplier_id].to_domain

    def list(self) -> list[Supplier]:
        return [supplier.to_domain for supplier in self.all.values()]

    def _write_models(self, locked: List[SupplierInDb]) -> None:
        self.path.parent.mkdir(exist_ok=True)
        locked.sort()
        suppliers_in_db = SupplierListInDb(root=locked)
        with self.path.open("w", encoding="utf-8") as f:
            f.write(suppliers_in_db.model_dump_json(indent=2))

    def add(self, supplier: Supplier) -> None:
        """Add a single supplier to the repository."""
        supplier_in_db = SupplierInDb.from_domain(supplier)
        locked = self._fetch_all()
        locked[supplier_in_db.supplier_id] = supplier_in_db
        self._write_models(list(locked.values()))
        self.seen.add(supplier)

    def add_list(self, suppliers: Iterable[Supplier]) -> None:
        """Add a list of suppliers to the repository, failing on duplicate IDs."""
        suppliers_in_db = [SupplierInDb.from_domain(s) for s in suppliers]
        locked = self._fetch_all()

        # Check for duplicates within the new suppliers
        new_ids = [s.supplier_id for s in suppliers_in_db]
        if len(new_ids) != len(set(new_ids)):
            duplicates = [id for id in new_ids if new_ids.count(id) > 1]
            raise ValueError(f"Duplicate supplier_id in input: {set(duplicates)}")

        # Check for duplicates with existing suppliers
        for supplier_in_db in suppliers_in_db:
            if supplier_in_db.supplier_id in locked:
                existing = locked[supplier_in_db.supplier_id]
                # Allow updating if it's exactly the same supplier
                if (
                    existing.location.lat != supplier_in_db.location.lat
                    or existing.location.lon != supplier_in_db.location.lon
                    or existing.commodity != supplier_in_db.commodity
                    or existing.production_cost_by_year != supplier_in_db.production_cost_by_year
                ):
                    raise ValueError(
                        f"Duplicate supplier_id {supplier_in_db.supplier_id} with different data. "
                        f"Existing location: ({existing.location.lat}, {existing.location.lon}), "
                        f"New location: ({supplier_in_db.location.lat}, {supplier_in_db.location.lon})"
                    )
            locked[supplier_in_db.supplier_id] = supplier_in_db
        self._write_models(list(locked.values()))

    def to_json(self) -> str:
        """Return the JSON content of the suppliers."""
        suppliers_in_db = list(self.all.values())
        return "[\n" + ",\n".join(s.model_dump_json(indent=2) for s in suppliers_in_db) + "\n]"


class TariffInDb(BaseModel):
    """
    Pydantic model for serializing/deserializing a TradeTariff to/from JSON.
    """

    tariff_id: str
    tariff_name: str
    from_iso3: str
    to_iso3: str
    tax_absolute: float | None
    tax_percentage: float | None
    quota: Volumes | None
    start_date: Year
    end_date: Year
    metric: str
    commodity: str

    @property
    def to_domain(self) -> TradeTariff:
        """
        Convert this DB model into the domain‐level `TradeTariff` object.
        """
        return TradeTariff(
            tariff_id=self.tariff_id,
            tariff_name=self.tariff_name,
            from_iso3=self.from_iso3,
            to_iso3=self.to_iso3,
            tax_absolute=self.tax_absolute,
            tax_percentage=self.tax_percentage,
            quota=self.quota,
            start_date=self.start_date,
            end_date=self.end_date,
            metric=self.metric,
            commodity=self.commodity,
        )

    @classmethod
    def from_domain(cls, tariff: TradeTariff) -> Self:
        """
        Create a TariffInDb (Pydantic model) from a domain‐level `TradeTariff` instance.
        """
        return cls(
            tariff_id=tariff.tariff_id,
            tariff_name=tariff.tariff_name,
            from_iso3=tariff.from_iso3,
            to_iso3=tariff.to_iso3,
            tax_absolute=tariff.tax_absolute,
            tax_percentage=tariff.tax_percentage,
            quota=tariff.quota,
            start_date=tariff.start_date,
            end_date=tariff.end_date,
            metric=tariff.metric,
            commodity=tariff.commodity,
        )

    def __lt__(self, other: Self) -> bool:
        """
        Allow sorting by `tariff_name` for stable JSON dumps.
        """
        return self.tariff_name < other.tariff_name


class TariffListInDb(BaseModel):
    """
    Used to make a list of TariffInDb serializable to JSON.
    """

    root: list[TariffInDb]


class TariffJsonRepository:
    """
    Repository for storing tariffs in a JSON file. Uses Pydantic models
    (TariffInDb / TariffListInDb) for serialization / deserialization.
    All I/O is done using domain models (`Tariff`). Internally, we convert
    back and forth between `Tariff` ↔ `TariffInDb`.
    """

    _all: dict[str, TariffInDb] | None = None

    def __init__(self, path: Path) -> None:
        self.path = path
        self.seen: set[TradeTariff] = set()

    def _fetch_all(self) -> dict[str, TariffInDb]:
        """
        Read the JSON file (if it exists), parse it into a TariffListInDb,
        and return a dict mapping tariff_id → TariffInDb.
        """
        try:
            if not self.path.exists():
                return {}
            with self.path.open("r", encoding="utf-8") as f:
                tariffs_in_db = TariffListInDb.model_validate_json(f.read())
            return {t.tariff_id: t for t in tariffs_in_db.root}
        except FileNotFoundError:
            return {}

    @property
    def all(self) -> dict[str, TariffInDb]:
        """
        Cache‐and‐return all entries from the JSON file.
        """
        if self._all is None:
            self._all = self._fetch_all()
        return self._all

    def get(self, tariff_id: str) -> TradeTariff:
        """
        Return a domain‐level `Tariff` for the given tariff_id.
        Raises KeyError if not found.
        """
        return self.all[tariff_id].to_domain

    def list(self) -> list[TradeTariff]:
        """
        Return a list of all `Tariff` domain objects in the repository.
        """
        return [t_in_db.to_domain for t_in_db in self.all.values()]

    def _write_models(self, locked: List[TariffInDb]) -> None:
        """
        Given a list of TariffInDb (unsorted), sort them by tariff_id and
        write out to JSON (using TariffListInDb).
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        locked.sort()  # uses TariffInDb.__lt__ (sort by tariff_id)
        tariffs_list = TariffListInDb(root=locked)
        with self.path.open("w", encoding="utf-8") as f:
            f.write(tariffs_list.model_dump_json(indent=2))

    def add(self, tariff: TradeTariff) -> None:
        """
        Add (or overwrite) a single Tariff in the JSON DB.
        """
        tariff_in_db = TariffInDb.from_domain(tariff)
        locked = self._fetch_all()
        locked[tariff_in_db.tariff_id] = tariff_in_db
        self._write_models(list(locked.values()))
        self.seen.add(tariff)

    def add_list(self, tariffs: Iterable[TradeTariff]) -> None:
        """
        Add (or overwrite) a list of Tariffs in the JSON DB.
        """
        tariffs_in_db = [TariffInDb.from_domain(t) for t in tariffs]
        locked = self._fetch_all()
        for t_in_db in tariffs_in_db:
            locked[t_in_db.tariff_id] = t_in_db
        self._write_models(list(locked.values()))

    def to_json(self) -> str:
        """
        Return the JSON text for all Tariffs currently in‐memory (cached).
        Useful for debugging or exporting without writing to disk again.
        """
        tariffs_in_db = list(self.all.values())
        # Dump each TariffInDb to JSON (indented), then wrap in a JSON array
        json_lines = [t.model_dump_json(indent=2) for t in tariffs_in_db]
        return "[\n" + ",\n".join(json_lines) + "\n]"


class PrimaryFeedstockJsonRepository:
    """
    Repository for storing PrimaryFeedstock entries in a JSON file.
    Uses Pydantic models (PrimaryFeedstockInDb / PrimaryFeedstockListInDb) for serialization/deserialization.
    All I/O is done using domain‐level `PrimaryFeedstock`. Internally, we convert back and forth:
    `PrimaryFeedstock` ↔ `PrimaryFeedstockInDb`.
    """

    _all: dict[str, PrimaryFeedstockInDb] | None = None

    def __init__(self, path: Path) -> None:
        self.path = path

    def _fetch_all(self) -> dict[str, PrimaryFeedstockInDb]:
        """
        Read the JSON file (if it exists), parse it into a PrimaryFeedstockListInDb,
        and return a dict mapping name → PrimaryFeedstockInDb.
        """
        try:
            if not self.path.exists():
                return {}

            raw = self.path.read_text(encoding="utf-8")
            parsed = PrimaryFeedstockListInDb.parse_raw(raw)
            return {entry.name: entry for entry in parsed.root if entry.name is not None}
        except FileNotFoundError:
            return {}

    @property
    def all(self) -> dict[str, PrimaryFeedstockInDb]:
        """
        Cache‐and‐return all entries from the JSON file.
        """
        if self._all is None:
            self._all = self._fetch_all()
        return self._all

    def get(self, name: str) -> PrimaryFeedstock:
        """
        Return a domain‐level `PrimaryFeedstock` for the given name.
        Raises KeyError if not found.
        """
        entry_in_db = self.all[name]
        return entry_in_db.to_domain

    def list(self) -> List[PrimaryFeedstock]:
        """
        Return a list of all `PrimaryFeedstock` domain objects in the repository.
        """
        return [entry.to_domain for entry in self.all.values()]

    def _write_models(self, locked: List[PrimaryFeedstockInDb]) -> None:
        """
        Given a list of PrimaryFeedstockInDb (unsorted), sort them by name and
        write out to JSON (using PrimaryFeedstockListInDb).
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        locked.sort()  # uses PrimaryFeedstockInDb.__lt__ (sort by name)
        wrapper = PrimaryFeedstockListInDb(root=locked)
        with self.path.open("w", encoding="utf-8") as f:
            f.write(wrapper.model_dump_json(indent=2))

    def add(self, pf: PrimaryFeedstock) -> None:
        """
        Add (or overwrite) a single PrimaryFeedstock in the JSON DB.
        """
        entry_in_db = PrimaryFeedstockInDb.from_domain(pf)
        if entry_in_db.name is None:
            raise ValueError("Cannot add PrimaryFeedstock with None name")
        locked = self._fetch_all()
        locked[entry_in_db.name] = entry_in_db
        self._write_models(list(locked.values()))
        self._all = None  # clear cache

    def add_list(self, pf_list: List[PrimaryFeedstock]) -> None:
        """
        Add (or overwrite) a list of PrimaryFeedstock domain objects in the JSON DB.
        """
        locked = self._fetch_all()
        for pf in pf_list:
            entry_in_db = PrimaryFeedstockInDb.from_domain(pf)
            if entry_in_db.name is None:
                raise ValueError("Cannot add PrimaryFeedstock with None name")
            locked[entry_in_db.name] = entry_in_db
        self._write_models(list(locked.values()))
        self._all = None

    def to_json(self) -> str:
        """
        Return the JSON text for all PrimaryFeedstockInDb entries currently cached.
        Useful for debugging or exporting without writing to disk again.
        """
        entries = list(self.all.values())
        json_lines = [e.model_dump_json(indent=2) for e in entries]
        return "[\n" + ",\n".join(json_lines) + "\n]"


class CarbonCostInDb(BaseModel):
    """
    Pydantic model for serializing/deserializing one (ISO3, year, carbon_cost) entry.
    """

    iso3: str
    carbon_cost: dict[Year, float]

    @field_validator("carbon_cost", mode="before")
    @classmethod
    def convert_keys_to_year(cls, v: Any) -> dict[Year, float]:
        """
        Convert JSON string/int keys to Year objects.
        Pydantic does not coerce dict keys, so we normalize them here.
        """
        if not isinstance(v, dict):
            return v

        converted: dict[Year, float] = {}
        for key, value in v.items():
            try:
                year_key = Year(int(key))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Cannot convert carbon_cost key {key!r} to Year") from exc

            converted[year_key] = float(value)

        return converted

    def __lt__(self, other: Self) -> bool:
        """
        Allow sorting by `tariff_name` for stable JSON dumps.
        """
        return self.iso3 < other.iso3

    @property
    def to_domain(self) -> CarbonCostSeries:
        """
        Convert this DB model into the domain‐level `CarbonCostSeries` object.
        """
        return CarbonCostSeries(
            iso3=self.iso3,
            carbon_cost=self.carbon_cost,
        )

    @classmethod
    def from_domain(cls, carbon_cost_series: CarbonCostSeries) -> Self:
        """
        Create a TariffInDb (Pydantic model) from a domain‐level `TradeTariff` instance.
        """
        return cls(
            iso3=carbon_cost_series.iso3,
            carbon_cost=carbon_cost_series.carbon_cost,
        )


class CarbonCostListInDb(BaseModel):
    """
    Root model wrapping a list of CarbonCostInDb, for JSON (de)serialization.
    """

    root: List[CarbonCostInDb]


class CarbonCostsJsonRepository:
    """
    Repository for storing CarbonCostSeries entries in a JSON file.
    Uses Pydantic models (CarbonCostSeriesInDb / CarbonCostSeriesListInDb) for serialization/deserialization.
    All I/O is done with domain‐level `CarbonCostSeries`. Internally, we convert back and forth:
    `CarbonCostSeries` ↔ `CarbonCostSeriesInDb`.
    """

    _all: dict[str, CarbonCostInDb] | None = None

    def __init__(self, path: Path) -> None:
        self.path = path
        self.seen: set[CarbonCostSeries] = set()

    def _fetch_all(self) -> dict[str, CarbonCostInDb]:
        """
        Read the JSON file (if it exists), parse it into a TariffListInDb,
        and return a dict mapping tariff_id → TariffInDb.
        """
        try:
            if not self.path.exists():
                return {}
            with self.path.open("r", encoding="utf-8") as f:
                carbon_cost_in_db = CarbonCostListInDb.model_validate_json(f.read())
            return {c.iso3: c for c in carbon_cost_in_db.root}
        except FileNotFoundError:
            return {}

    @property
    def all(self) -> dict[str, CarbonCostInDb]:
        """
        Cache‐and‐return all entries from the JSON file.
        """
        if self._all is None:
            self._all = self._fetch_all()
        return self._all

    def get(self, iso3: str) -> CarbonCostSeries:
        """
        Return a domain‐level `CarbonCostSeries` for the given ISO3.
        Raises KeyError if not found.
        """
        entry_in_db = self.all[iso3]
        return entry_in_db.to_domain

    def list(self) -> List[CarbonCostSeries]:
        """
        Return a list of all `CarbonCostSeries` domain objects in the repository.
        """
        return [entry.to_domain for entry in self.all.values()]

    def _write_models(self, locked: List[CarbonCostInDb]) -> None:
        """
        Given an unsorted list of CarbonCostSeriesInDb, sort them by iso3
        and write out to JSON (using CarbonCostSeriesListInDb).
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        locked.sort()  # uses CarbonCostSeriesInDb.__lt__ (sort by iso3)
        wrapper = CarbonCostListInDb(root=locked)
        with self.path.open("w", encoding="utf-8") as f:
            f.write(wrapper.model_dump_json(indent=2))

    def add(self, series: CarbonCostSeries) -> None:
        """
        Add (or overwrite) a single CarbonCostSeries in the JSON DB.
        """
        entry_in_db = CarbonCostInDb.from_domain(series)
        locked = self._fetch_all()
        locked[entry_in_db.iso3] = entry_in_db
        self._write_models(list(locked.values()))
        self._all = None  # clear cache

    def add_list(self, series_list: List[CarbonCostSeries]) -> None:
        """
        Add (or overwrite) a list of CarbonCostSeries domain objects in the JSON DB.
        """
        locked = self._fetch_all()
        for series in series_list:
            entry_in_db = CarbonCostInDb.from_domain(series)
            locked[entry_in_db.iso3] = entry_in_db
        self._write_models(list(locked.values()))
        self._all = None

    def to_json(self) -> str:
        """
        Return the JSON text for all CarbonCostSeriesInDb entries currently in‐memory (cached).
        Useful for debugging or exporting without writing to disk again.
        """
        entries = list(self.all.values())
        json_lines = [e.model_dump_json(indent=2) for e in entries]
        return "[\n" + ",\n".join(json_lines) + "\n]"


# ---- Pydantic "In-DB" Models ----
class RegionEmissivityInDb(BaseModel):
    iso3: str
    country_name: str
    scenario: str
    grid_emissivity: dict[Year, dict[str, float]]
    coke_emissivity: dict[str, float]
    gas_emissivity: dict[str, float]
    id: str = Field(..., description="iso3_scenario id")

    def __lt__(self, other: "RegionEmissivityInDb") -> bool:
        return self.id < other.id

    @property
    def to_domain(self) -> RegionEmissivity:
        return RegionEmissivity(
            iso3=self.iso3,
            country_name=self.country_name,
            scenario=self.scenario,
            grid_emissivity=self.grid_emissivity,
            coke_emissivity=self.coke_emissivity,
            gas_emissivity=self.gas_emissivity,
        )

    @classmethod
    def from_domain(cls, domain: RegionEmissivity) -> "RegionEmissivityInDb":
        return cls(
            iso3=domain.iso3,
            country_name=domain.country_name,
            scenario=domain.scenario,
            grid_emissivity=domain.grid_emissivity,
            coke_emissivity=domain.coke_emissivity,
            gas_emissivity=domain.gas_emissivity,
            id=domain.id,
        )


class RegionEmissivityListInDb(BaseModel):
    root: List[RegionEmissivityInDb]


# ---- JSON Repository ----
class RegionEmissivityJsonRepository:
    _all: dict[str, RegionEmissivityInDb] | None = None

    def __init__(self, path: Path) -> None:
        self.path = path

    def _fetch_all(self) -> dict[str, RegionEmissivityInDb]:
        if not self.path.exists():
            return {}
        raw = self.path.read_text(encoding="utf-8")
        wrapper = RegionEmissivityListInDb.model_validate_json(raw)
        return {item.id: item for item in wrapper.root}

    @property
    def all(self) -> dict[str, RegionEmissivityInDb]:
        if self._all is None:
            self._all = self._fetch_all()
        return self._all

    def get(self, id: str) -> RegionEmissivity:
        return self.all[id].to_domain

    def list(self) -> List[RegionEmissivity]:
        return [item.to_domain for item in self.all.values()]

    def _write_models(self, models: List[RegionEmissivityInDb]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        models.sort()
        wrapper = RegionEmissivityListInDb(root=models)
        self.path.write_text(wrapper.model_dump_json(indent=2), encoding="utf-8")
        self._all = None

    def add(self, item: RegionEmissivity) -> None:
        db_item = RegionEmissivityInDb.from_domain(item)
        locked = self._fetch_all()
        locked[db_item.id] = db_item
        self._write_models(list(locked.values()))

    def add_list(self, items: List[RegionEmissivity]) -> None:
        locked = self._fetch_all()
        for item in items:
            db_item = RegionEmissivityInDb.from_domain(item)
            locked[db_item.id] = db_item
        self._write_models(list(locked.values()))

    def to_json(self) -> str:
        return "[\n" + ",\n".join(db.model_dump_json(indent=2) for db in self.all.values()) + "\n]"


class InputCostsInDb(BaseModel):
    """
    Pydantic model for serializing/deserializing an InputCosts record to/from JSON.
    """

    iso3: str
    year: Year
    costs: dict[str, float] = Field(..., description="Mapping from commodity → cost")

    @property
    def to_domain(self) -> InputCosts:
        """
        Convert this DB model into the domain‐level `InputCosts` object.
        """
        return InputCosts(year=self.year, iso3=self.iso3, costs=self.costs.copy())

    @classmethod
    def from_domain(cls, ic: InputCosts) -> "InputCostsInDb":
        """
        Create an InputCostsInDb (Pydantic model) from a domain‐level `InputCosts` instance.
        """
        return cls(iso3=ic.iso3, year=ic.year, costs=ic.costs.copy())

    def __lt__(self, other: "InputCostsInDb") -> bool:
        """
        Enable sorting by `name` (iso3_year) for stable JSON dumps.
        """
        return f"{self.iso3}_{self.year}" < f"{other.iso3}_{other.year}"


class InputCostsListInDb(BaseModel):
    """
    Root model wrapping a list of InputCostsInDb for JSON (de)serialization.
    """

    root: List[InputCostsInDb]


class InputCostsJsonRepository:
    """
    Repository for storing InputCosts entries in a JSON file.
    Uses Pydantic models (InputCostsInDb / InputCostsListInDb) for serialization/deserialization.
    All I/O is done using domain‐level `InputCosts`. Internally, we convert back and forth:
    `InputCosts` ↔ `InputCostsInDb`.
    """

    _all: dict[str, InputCostsInDb] | None = None

    def __init__(self, path: Path) -> None:
        self.path = path
        self.seen: set[InputCosts] = set()

    def _fetch_all(self) -> dict[str, InputCostsInDb]:
        """
        Read the JSON file (if it exists), parse it into an InputCostsListInDb,
        and return a dict mapping name (iso3_year) → InputCostsInDb.
        """
        try:
            if not self.path.exists():
                return {}
            raw = self.path.read_text(encoding="utf-8")
            parsed = InputCostsListInDb.model_validate_json(raw)
            return {f"{entry.iso3}_{entry.year}": entry for entry in parsed.root}
        except FileNotFoundError:
            return {}

    @property
    def all(self) -> dict[str, InputCostsInDb]:
        """
        Cache‐and‐return all entries from the JSON file.
        """
        if self._all is None:
            self._all = self._fetch_all()
        return self._all

    def get(self, iso3: str, year: int) -> InputCosts:
        """
        Return a domain‐level `InputCosts` for the given iso3 and year.
        Raises KeyError if not found.
        """
        key = f"{iso3}_{year}"
        return self.all[key].to_domain

    def list(self) -> List[InputCosts]:
        """
        Return a list of all `InputCosts` domain objects in the repository.
        """
        return [entry.to_domain for entry in self.all.values()]

    def _write_models(self, locked: List[InputCostsInDb]) -> None:
        """
        Given a list of InputCostsInDb (unsorted), sort them by name and
        write out to JSON (using InputCostsListInDb).
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        locked.sort()  # uses InputCostsInDb.__lt__ (sort by iso3_year)
        wrapper = InputCostsListInDb(root=locked)
        with self.path.open("w", encoding="utf-8") as f:
            f.write(wrapper.model_dump_json(indent=2))

    def add(self, ic: InputCosts) -> None:
        """
        Add (or overwrite) a single InputCosts in the JSON “database.”
        """
        entry_in_db = InputCostsInDb.from_domain(ic)
        locked = self._fetch_all()
        key = f"{entry_in_db.iso3}_{entry_in_db.year}"
        locked[key] = entry_in_db
        self._write_models(list(locked.values()))
        self._all = None  # clear cache

    def add_list(self, ic_list: Iterable[InputCosts]) -> None:
        """
        Add (or overwrite) a list of InputCosts domain objects in the JSON DB.
        """
        locked = self._fetch_all()
        for ic in ic_list:
            entry_in_db = InputCostsInDb.from_domain(ic)
            key = f"{entry_in_db.iso3}_{entry_in_db.year}"
            locked[key] = entry_in_db
        self._write_models(list(locked.values()))
        self._all = None

    def to_json(self) -> str:
        """
        Return the JSON text for all InputCostsInDb entries currently cached.
        Useful for debugging or exporting without writing to disk again.
        """
        entries = list(self.all.values())
        json_lines = [entry.model_dump_json(indent=2) for entry in entries]
        return "[\n" + ",\n".join(json_lines) + "\n]"

    def time_series(self, iso3: str) -> dict[Year, dict[str, float]]:
        """
        Return a time series of input costs for a given ISO3 country.
        Returns a dict mapping Year → dict[commodity → cost].
        """
        return {entry.year: entry.costs for entry in self.all.values() if entry.iso3 == iso3}


class CapexInDb(BaseModel):
    technology_name: str
    product: str
    greenfield_capex: float
    capex_renovation_share: float
    learning_rate: float

    def __lt__(self, other: "CapexInDb") -> bool:
        return self.technology_name < other.technology_name

    @property
    def to_domain(self) -> Capex:
        return Capex(
            technology_name=self.technology_name,
            product=self.product,
            greenfield_capex=self.greenfield_capex,
            capex_renovation_share=self.capex_renovation_share,
            learning_rate=self.learning_rate,
        )

    @classmethod
    def from_domain(cls, domain: Capex) -> "CapexInDb":
        return cls(
            technology_name=domain.technology_name,
            product=domain.product,
            greenfield_capex=domain.greenfield_capex,
            capex_renovation_share=domain.capex_renovation_share,
            learning_rate=domain.learning_rate,
        )


class CapexListInDb(BaseModel):
    root: List[CapexInDb]


# ---- JSON Repository ----
class CapexJsonRepository:
    """
    Repository for storing Capex entries in a JSON file.
    Uses Pydantic models (CapexInDb / CapexListInDb) for serialization/deserialization.
    All I/O is with domain‐level `Capex`. Internally converts:
    `Capex` ↔ `CapexInDb`.
    """

    _all: dict[str, CapexInDb] | None = None

    def __init__(self, path: Path) -> None:
        self.path = path

    def _fetch_all(self) -> dict[str, CapexInDb]:
        if not self.path.exists():
            return {}
        raw = self.path.read_text(encoding="utf-8")
        wrapper = CapexListInDb.model_validate_json(raw)
        return {item.technology_name: item for item in wrapper.root}

    @property
    def all(self) -> dict[str, CapexInDb]:
        if self._all is None:
            self._all = self._fetch_all()
        return self._all

    def get(self, tech: str) -> Capex:
        """Return a domain-level Capex for the given technology."""
        return self.all[tech].to_domain

    def list(self) -> List[Capex]:
        """List all domain-level Capex entries."""
        return [item.to_domain for item in self.all.values()]

    def _write_models(self, models: List[CapexInDb]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        models.sort()
        wrapper = CapexListInDb(root=models)
        self.path.write_text(wrapper.model_dump_json(indent=2), encoding="utf-8")
        self._all = None

    def add(self, item: Capex) -> None:
        """Add or overwrite a single Capex entry."""
        db_item = CapexInDb.from_domain(item)
        locked = self._fetch_all()
        locked[db_item.technology_name] = db_item
        self._write_models(list(locked.values()))

    def add_list(self, items: List[Capex]) -> None:
        """Add or overwrite multiple Capex entries."""
        locked = self._fetch_all()
        for item in items:
            db_item = CapexInDb.from_domain(item)
            locked[db_item.technology_name] = db_item
        self._write_models(list(locked.values()))

    def to_json(self) -> str:
        """Get raw JSON string of all entries (for debugging)."""
        entries = list(self.all.values())
        json_lines = [e.model_dump_json(indent=2) for e in entries]
        return "[\n" + ",\n".join(json_lines) + "\n]"


class CostOfCapitalInDb(BaseModel):
    country: str
    iso3: str
    debt_res: float
    equity_res: float
    wacc_res: float
    debt_other: float
    equity_other: float
    wacc_other: float

    def __lt__(self, other: "CostOfCapitalInDb") -> bool:
        return self.iso3 < other.iso3

    @property
    def to_domain(self) -> CostOfCapital:
        return CostOfCapital(
            country=self.country,
            iso3=self.iso3,
            debt_res=self.debt_res,
            equity_res=self.equity_res,
            wacc_res=self.wacc_res,
            debt_other=self.debt_other,
            equity_other=self.equity_other,
            wacc_other=self.wacc_other,
        )

    @classmethod
    def from_domain(cls, domain: CostOfCapital) -> "CostOfCapitalInDb":
        return cls(
            country=domain.country,
            iso3=domain.iso3,
            debt_res=domain.debt_res,
            equity_res=domain.equity_res,
            wacc_res=domain.wacc_res,
            debt_other=domain.debt_other,
            equity_other=domain.equity_other,
            wacc_other=domain.wacc_other,
        )


class CostOfCapitalListInDb(BaseModel):
    root: List[CostOfCapitalInDb]


class CostOfCapitalJsonRepository:
    _all: dict[str, CostOfCapitalInDb] | None = None

    def __init__(self, path: Path) -> None:
        self.path = path

    def _fetch_all(self) -> dict[str, CostOfCapitalInDb]:
        if not self.path.exists():
            return {}
        raw = self.path.read_text(encoding="utf-8")
        wrapper = CostOfCapitalListInDb.model_validate_json(raw)
        return {item.iso3: item for item in wrapper.root}

    @property
    def all(self) -> dict[str, CostOfCapitalInDb]:
        if self._all is None:
            self._all = self._fetch_all()
        return self._all

    def get(self, iso3: str) -> CostOfCapital:
        return self.all[iso3].to_domain

    def list(self) -> List[CostOfCapital]:
        return [item.to_domain for item in self.all.values()]

    def _write_models(self, models: List[CostOfCapitalInDb]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        models.sort()
        wrapper = CostOfCapitalListInDb(root=models)
        self.path.write_text(wrapper.model_dump_json(indent=2), encoding="utf-8")
        self._all = None

    def add(self, item: CostOfCapital) -> None:
        db_item = CostOfCapitalInDb.from_domain(item)
        locked = self._fetch_all()
        locked[db_item.iso3] = db_item
        self._write_models(list(locked.values()))

    def add_list(self, items: List[CostOfCapital]) -> None:
        locked = self._fetch_all()
        for item in items:
            db_item = CostOfCapitalInDb.from_domain(item)
            locked[db_item.iso3] = db_item
        self._write_models(list(locked.values()))

    def to_json(self) -> str:
        entries = list(self.all.values())
        json_lines = [e.model_dump_json(indent=2) for e in entries]
        return "[\n" + ",\n".join(json_lines) + "\n]"


class LegalProcessConnectorInDb(BaseModel):
    from_technology_name: str
    to_technology_name: str

    def to_domain(self) -> LegalProcessConnector:
        """Convert to domain object."""
        return LegalProcessConnector(
            from_technology_name=self.from_technology_name,
            to_technology_name=self.to_technology_name,
        )

    @classmethod
    def from_domain(cls, obj: LegalProcessConnector) -> "LegalProcessConnectorInDb":
        """Create from domain object."""
        return cls(
            from_technology_name=obj.from_technology_name,
            to_technology_name=obj.to_technology_name,
        )


class LegalProcessConnectorJsonRepository:
    """Repository for equipment cost data stored in JSON format."""

    def __init__(self, path: Path):
        self.path = path
        self._data: list[LegalProcessConnector] | None = None
        self._loaded = False

    def add_list(self, items: list[LegalProcessConnector]) -> None:
        """Add multiple equipment cost entries."""
        self._ensure_loaded()
        assert self._data is not None  # _ensure_loaded guarantees this
        self._data.extend(items)
        self.save()

    def list(self) -> list[LegalProcessConnector]:
        """Return all equipment costs."""
        self._ensure_loaded()
        assert self._data is not None  # _ensure_loaded guarantees this
        return self._data.copy()

    def save(self) -> None:
        """Save data to JSON file."""
        self._ensure_loaded()
        assert self._data is not None  # _ensure_loaded guarantees this
        db_items = [LegalProcessConnectorInDb.from_domain(item) for item in self._data]
        with open(self.path, "w") as f:
            json.dump([item.model_dump() for item in db_items], f, indent=2)

    def load(self) -> None:
        """Load data from JSON file."""
        with open(self.path, "r") as f:
            data = json.load(f)
        self._data = [LegalProcessConnectorInDb(**item).to_domain() for item in data]
        self._loaded = True

    def _ensure_loaded(self) -> None:
        """Lazy load data on first access."""
        if not self._loaded:
            if self._data is None:
                self._data = []
            if self.path.exists():
                self.load()
            else:
                self._loaded = True


class CountryMappingInDb(BaseModel):
    """
    Pydantic model for serializing/deserializing CountryMapping to/from JSON.
    """

    model_config = {"populate_by_name": True}  # Allow both alias and field name

    country: str = Field(..., alias="Country")
    iso2: str = Field(..., alias="ISO 2-letter code")
    iso3: str = Field(..., alias="ISO 3-letter code")
    irena_name: str = Field(..., alias="irena_name")
    irena_region: str | None = Field(None, alias="irena_region")
    region_for_outputs: str = Field(..., alias="region_for_outputs")
    ssp_region: str = Field(..., alias="ssp_region")
    gem_country: str | None = Field(None, alias="gem_country")
    ws_region: str | None = Field(None, alias="ws_region")
    tiam_ucl_region: str = Field(..., alias="tiam-ucl_region")
    eu_region: str | None = Field(None, alias="eu_or_non_eu")
    # CBAM-related region memberships
    EU: bool = Field(False, alias="EU")
    EFTA_EUCJ: bool = Field(False, alias="EFTA/EUCJ")
    OECD: bool = Field(False, alias="OECD")
    NAFTA: bool = Field(False, alias="NAFTA")
    Mercosur: bool = Field(False, alias="Mercosur")
    ASEAN: bool = Field(False, alias="ASEAN")
    RCEP: bool = Field(False, alias="RCEP")

    def to_domain(self) -> CountryMapping:
        """Convert to domain object."""
        return CountryMapping(
            country=self.country,
            iso2=self.iso2,
            iso3=self.iso3,
            irena_name=self.irena_name,
            irena_region=self.irena_region,
            region_for_outputs=self.region_for_outputs,
            ssp_region=self.ssp_region,
            gem_country=self.gem_country,
            ws_region=self.ws_region,
            tiam_ucl_region=self.tiam_ucl_region,
            eu_region=self.eu_region,
            EU=self.EU,
            EFTA_EUCJ=self.EFTA_EUCJ,
            OECD=self.OECD,
            NAFTA=self.NAFTA,
            Mercosur=self.Mercosur,
            ASEAN=self.ASEAN,
            RCEP=self.RCEP,
        )

    @classmethod
    def from_domain(cls, obj: CountryMapping) -> "CountryMappingInDb":
        """Create from domain object."""
        return cls(
            country=obj.country,
            iso2=obj.iso2,
            iso3=obj.iso3,
            irena_name=obj.irena_name,
            irena_region=obj.irena_region,
            region_for_outputs=obj.region_for_outputs,
            ssp_region=obj.ssp_region,
            gem_country=obj.gem_country,
            ws_region=obj.ws_region,
            tiam_ucl_region=obj.tiam_ucl_region,
            eu_region=obj.eu_region,
            EU=obj.EU,
            EFTA_EUCJ=obj.EFTA_EUCJ,
            OECD=obj.OECD,
            NAFTA=obj.NAFTA,
            Mercosur=obj.Mercosur,
            ASEAN=obj.ASEAN,
            RCEP=obj.RCEP,
        )

    def __lt__(self, other: "CountryMappingInDb") -> bool:
        """Enable sorting by iso3 for stable JSON dumps."""
        return self.iso3 < other.iso3


class CountryMappingsRepository:
    def __init__(self, path: Path):
        self.path = path
        self._data: list[CountryMapping] | None = None

    def _load(self):
        with open(self.path, "r") as f:
            data = json.load(f)
        self._data = [CountryMappingInDb(**item).to_domain() for item in data]

    def get_all(self) -> list[CountryMapping]:
        if self._data is None:
            self._load()
        assert self._data is not None  # _load() always sets _data
        return self._data

    def get_by_country(self, country: str) -> CountryMapping | None:
        mappings = self.get_all()
        return next((m for m in mappings if m.country == country), None)


class FOPEXInDb(BaseModel):
    """
    Pydantic model for serializing/deserializing FOPEX to/from JSON.
    """

    iso3: str
    technology_fopex: dict[str, float]

    def to_domain(self) -> FOPEX:
        """Convert to domain object."""
        return FOPEX(iso3=self.iso3, technology_fopex=self.technology_fopex)

    @classmethod
    def from_domain(cls, obj: FOPEX) -> "FOPEXInDb":
        """Create from domain object."""
        return cls(iso3=obj.iso3, technology_fopex=obj.technology_fopex)


class FOPEXRepository:
    """Repository for FOPEX data stored in JSON format."""

    def __init__(self, path: Path):
        self.path = path
        self._data: list[FOPEX] = []
        if self.path.exists():
            self.load()

    def add(self, item: FOPEX) -> None:
        """Add a single FOPEX entry."""
        self._data.append(item)
        self.save()

    def add_list(self, items: list[FOPEX]) -> None:
        """Add multiple FOPEX entries."""
        self._data.extend(items)
        self.save()

    def list(self) -> list[FOPEX]:
        """Return all FOPEX entries."""
        return self._data.copy()

    def get_by_iso3(self, iso3: str) -> FOPEX | None:
        """Get FOPEX for a specific ISO3 code."""
        return next((item for item in self._data if item.iso3 == iso3), None)

    def get_all_as_dict(self) -> dict[str, dict[str, float]]:
        """Get all FOPEX data as a dictionary mapping ISO3 to technology values."""
        return {item.iso3: item.technology_fopex for item in self._data}

    def save(self) -> None:
        """Save data to JSON file."""
        db_items = [FOPEXInDb.from_domain(item) for item in self._data]
        with open(self.path, "w") as f:
            json.dump([item.model_dump() for item in db_items], f, indent=2)

    def load(self) -> None:
        """Load data from JSON file."""
        with open(self.path, "r") as f:
            data = json.load(f)
        self._data = [FOPEXInDb(**item).to_domain() for item in data]


# ---- Pydantic "In-DB" Models ----
class SubsidyInDb(BaseModel):
    scenario_name: str
    iso3: str
    start_year: Year
    end_year: Year
    technology_name: str
    cost_item: str
    absolute_subsidy: float
    relative_subsidy: float
    subsidy_name: str = Field(..., description="Unique subsidy identifier")

    def __lt__(self, other: "SubsidyInDb") -> bool:
        return self.subsidy_name < other.subsidy_name

    @property
    def to_domain(self) -> Subsidy:
        return Subsidy(
            scenario_name=self.scenario_name,
            iso3=self.iso3,
            start_year=self.start_year,
            end_year=self.end_year,
            technology_name=self.technology_name,
            cost_item=self.cost_item,
            absolute_subsidy=self.absolute_subsidy,
            relative_subsidy=self.relative_subsidy,
        )

    @classmethod
    def from_domain(cls, domain: Subsidy) -> "SubsidyInDb":
        return cls(
            scenario_name=domain.scenario_name,
            iso3=domain.iso3,
            start_year=domain.start_year,
            end_year=domain.end_year,
            technology_name=domain.technology_name,
            cost_item=domain.cost_item,
            absolute_subsidy=domain.absolute_subsidy,
            relative_subsidy=domain.relative_subsidy,
            subsidy_name=domain.subsidy_name,
        )


class SubsidyListInDb(BaseModel):
    root: list[SubsidyInDb]


# ---- JSON Repository ----
class SubsidyJsonRepository:
    """
    Repository for storing Subsidy entries in a JSON file.
    Uses Pydantic models for serialization/deserialization.
    """

    _all: dict[str, SubsidyInDb] | None = None

    def __init__(self, path: Path) -> None:
        self.path = path

    def _fetch_all(self) -> dict[str, SubsidyInDb]:
        if not self.path.exists():
            return {}
        raw = self.path.read_text(encoding="utf-8")
        wrapper = SubsidyListInDb.model_validate_json(raw)
        return {item.subsidy_name: item for item in wrapper.root}

    @property
    def all(self) -> dict[str, SubsidyInDb]:
        if self._all is None:
            self._all = self._fetch_all()
        return self._all

    def get(self, name: str) -> Subsidy:
        """Return a domain-level Subsidy for the given subsidy_name."""
        return self.all[name].to_domain

    def list(self) -> list[Subsidy]:
        """List all Subsidy domain entries."""
        return [item.to_domain for item in self.all.values()]

    def _write_models(self, models: List[SubsidyInDb]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        models.sort()
        wrapper = SubsidyListInDb(root=models)
        self.path.write_text(wrapper.model_dump_json(indent=2), encoding="utf-8")
        self._all = None

    def add(self, item: Subsidy) -> None:
        """Add or overwrite a single Subsidy entry."""
        db_item = SubsidyInDb.from_domain(item)
        locked = self._fetch_all()
        locked[db_item.subsidy_name] = db_item
        self._write_models(list(locked.values()))

    def add_list(self, subsidies: Iterable[Subsidy]) -> None:
        """Add or overwrite multiple Subsidy entries."""
        subsidies_in_db = [SubsidyInDb.from_domain(t) for t in subsidies]
        locked = self._fetch_all()
        for s_in_db in subsidies_in_db:
            locked[s_in_db.subsidy_name] = s_in_db
        self._write_models(list(locked.values()))

    def to_json(self) -> str:
        """Get raw JSON of all entries."""
        entries = list(self.all.values())
        json_lines = [e.model_dump_json(indent=2) for e in entries]
        return "[\n" + ",\n".join(json_lines) + "\n]"


class HydrogenEfficiencyInDb(BaseModel):
    year: int
    efficiency: float

    def to_domain(self) -> HydrogenEfficiency:
        """Convert to domain object."""
        return HydrogenEfficiency(year=Year(self.year), efficiency=self.efficiency)

    @classmethod
    def from_domain(cls, obj: HydrogenEfficiency) -> "HydrogenEfficiencyInDb":
        """Create from domain object."""
        # Handle case where year might already be an int
        year_value = obj.year.value if hasattr(obj.year, "value") else obj.year
        return cls(year=year_value, efficiency=obj.efficiency)


class HydrogenEfficiencyJsonRepository:
    """Repository for hydrogen efficiency data stored in JSON format."""

    def __init__(self, path: Path):
        self.path = path
        self._data: list[HydrogenEfficiency] | None = None
        self._loaded = False

    def add(self, item: HydrogenEfficiency) -> None:
        """Add a single hydrogen efficiency entry."""
        self._ensure_loaded()
        assert self._data is not None  # _ensure_loaded guarantees this
        self._data.append(item)
        self.save()

    def add_list(self, items: list[HydrogenEfficiency]) -> None:
        """Add multiple hydrogen efficiency entries."""
        self._ensure_loaded()
        assert self._data is not None  # _ensure_loaded guarantees this
        self._data.extend(items)
        self.save()

    def list(self) -> list[HydrogenEfficiency]:
        """Return all hydrogen efficiency data."""
        self._ensure_loaded()
        assert self._data is not None  # _ensure_loaded guarantees this
        return self._data.copy()

    def get_by_year(self, year: Year) -> Optional[HydrogenEfficiency]:
        """Get hydrogen efficiency for a specific year."""
        self._ensure_loaded()
        assert self._data is not None  # _ensure_loaded guarantees this
        return next((item for item in self._data if item.year == year), None)

    def save(self) -> None:
        """Save data to JSON file."""
        self._ensure_loaded()
        assert self._data is not None  # _ensure_loaded guarantees this
        db_items = [HydrogenEfficiencyInDb.from_domain(item) for item in self._data]
        with open(self.path, "w") as f:
            json.dump([item.model_dump() for item in db_items], f, indent=2)

    def load(self) -> None:
        """Load data from JSON file."""
        with open(self.path, "r") as f:
            data = json.load(f)
        self._data = [HydrogenEfficiencyInDb(**item).to_domain() for item in data]
        self._loaded = True

    def _ensure_loaded(self) -> None:
        """Lazy load data on first access."""
        if not self._loaded:
            if self._data is None:
                self._data = []
            if self.path.exists():
                self.load()
            else:
                self._loaded = True


class HydrogenCapexOpexInDb(BaseModel):
    country_code: str
    values: dict[int, float]  # year -> value mapping

    def to_domain(self) -> HydrogenCapexOpex:
        """Convert to domain object."""
        year_values = {Year(year): value for year, value in self.values.items()}
        return HydrogenCapexOpex(country_code=self.country_code, values=year_values)

    @classmethod
    def from_domain(cls, obj: HydrogenCapexOpex) -> "HydrogenCapexOpexInDb":
        """Create from domain object."""
        # Handle case where year might already be an int
        int_values = {}
        for year, value in obj.values.items():
            year_int = year.value if hasattr(year, "value") else year
            int_values[year_int] = value
        return cls(country_code=obj.country_code, values=int_values)


class HydrogenCapexOpexJsonRepository:
    """Repository for hydrogen CAPEX/OPEX data stored in JSON format."""

    def __init__(self, path: Path):
        self.path = path
        self._data: list[HydrogenCapexOpex] | None = None
        self._loaded = False

    def add(self, item: HydrogenCapexOpex) -> None:
        """Add a single hydrogen CAPEX/OPEX entry."""
        self._ensure_loaded()
        assert self._data is not None  # _ensure_loaded guarantees this
        self._data.append(item)
        self.save()

    def add_list(self, items: list[HydrogenCapexOpex]) -> None:
        """Add multiple hydrogen CAPEX/OPEX entries."""
        self._ensure_loaded()
        assert self._data is not None  # _ensure_loaded guarantees this
        self._data.extend(items)
        self.save()

    def list(self) -> list[HydrogenCapexOpex]:
        """Return all hydrogen CAPEX/OPEX data."""
        self._ensure_loaded()
        assert self._data is not None  # _ensure_loaded guarantees this
        return self._data.copy()

    def get_by_country(self, country_code: str) -> Optional[HydrogenCapexOpex]:
        """Get hydrogen CAPEX/OPEX data for a specific country."""
        self._ensure_loaded()
        assert self._data is not None  # _ensure_loaded guarantees this
        return next((item for item in self._data if item.country_code == country_code), None)

    def save(self) -> None:
        """Save data to JSON file."""
        self._ensure_loaded()
        assert self._data is not None  # _ensure_loaded guarantees this
        db_items = [HydrogenCapexOpexInDb.from_domain(item) for item in self._data]
        with open(self.path, "w") as f:
            json.dump([item.model_dump() for item in db_items], f, indent=2)

    def load(self) -> None:
        """Load data from JSON file."""
        with open(self.path, "r") as f:
            data = json.load(f)
        self._data = [HydrogenCapexOpexInDb(**item).to_domain() for item in data]
        self._loaded = True

    def _ensure_loaded(self) -> None:
        """Lazy load data on first access."""
        if not self._loaded:
            if self._data is None:
                self._data = []
            if self.path.exists():
                self.load()
            else:
                self._loaded = True


# ---- Db Model for Railway Cost ----
class RailwayCostInDb(BaseModel):
    """Database model for railway cost data."""

    iso3: str
    cost_per_km: float

    def to_domain(self) -> RailwayCost:
        """Convert to domain object."""
        return RailwayCost(iso3=self.iso3, cost_per_km=self.cost_per_km)

    @classmethod
    def from_domain(cls, obj: RailwayCost) -> "RailwayCostInDb":
        """Create from domain object."""
        return cls(iso3=obj.iso3, cost_per_km=obj.cost_per_km)


class RailwayCostListInDb(BaseModel):
    """List wrapper for railway costs."""

    root: List[RailwayCostInDb]


# ---- JSON Repository for Railway Cost ----
class RailwayCostJsonRepository:
    """
    Repository for storing RailwayCost entries in a JSON file.
    Uses Pydantic models (RailwayCostInDb / RailwayCostListInDb) for serialization/deserialization.
    All I/O is done using domain‐level `RailwayCost`. Internally, we convert back and forth:
    `RailwayCost` ↔ `RailwayCostInDb`.
    """

    _all: dict[str, RailwayCostInDb] | None = None

    def __init__(self, path: Path) -> None:
        self.path = path

    def _fetch_all(self) -> dict[str, RailwayCostInDb]:
        if not self.path.exists():
            return {}
        raw = self.path.read_text(encoding="utf-8")
        wrapper = RailwayCostListInDb.model_validate_json(raw)
        return {item.iso3: item for item in wrapper.root}

    def fetch_all(self) -> dict[str, RailwayCostInDb]:
        if self._all is None:
            self._all = self._fetch_all()
        return self._all

    def list(self) -> List[RailwayCost]:
        """Get all railway costs as domain objects."""
        return [item.to_domain() for item in self.fetch_all().values()]

    def get(self, iso3: str) -> Optional[RailwayCost]:
        """Get railway cost for a specific country."""
        all_costs = self.fetch_all()
        if iso3 in all_costs:
            return all_costs[iso3].to_domain()
        return None

    def get_cost_per_km(self, iso3: str, default: float | None = None) -> float | None:
        """Get cost per km for a specific country, with optional default."""
        cost = self.get(iso3)
        if cost:
            return cost.cost_per_km
        return default

    def add(self, cost: RailwayCost) -> None:
        """Add a single railway cost entry."""
        all_costs = self.fetch_all()
        all_costs[cost.iso3] = RailwayCostInDb.from_domain(cost)
        self._write_all(list(all_costs.values()))

    def add_list(self, costs: List[RailwayCost]) -> None:
        """Add multiple railway cost entries."""
        all_costs = self.fetch_all()
        for cost in costs:
            all_costs[cost.iso3] = RailwayCostInDb.from_domain(cost)
        self._write_all(list(all_costs.values()))

    def _write_all(self, costs_in_db: List[RailwayCostInDb]) -> None:
        """Write all costs to file."""
        wrapper = RailwayCostListInDb(root=costs_in_db)
        self.path.write_text(wrapper.model_dump_json(indent=2), encoding="utf-8")
        self._all = {item.iso3: item for item in costs_in_db}


# ---- Pydantic Models for Transport Emissions ----
class TransportKPIInDb(BaseModel):
    reporter_iso: str
    partner_iso: str
    commodity: str
    ghg_factor: float
    transportation_cost: float | None = None  # Made optional for backward compatibility
    updated_on: str

    def to_domain(self) -> "TransportKPI":
        # Apply the same liquid_steel -> steel conversion as in excel reader
        commodity = self.commodity
        if commodity.lower() == Commodities.LIQUID_STEEL.value.lower():
            commodity = Commodities.STEEL.value

        return TransportKPI(
            reporter_iso=self.reporter_iso,
            partner_iso=self.partner_iso,
            commodity=commodity,
            ghg_factor=self.ghg_factor,
            transportation_cost=self.transportation_cost if self.transportation_cost is not None else 0.0,
            updated_on=self.updated_on,
        )

    @classmethod
    def from_domain(cls, obj: "TransportKPI") -> "TransportKPIInDb":
        return cls(
            reporter_iso=obj.reporter_iso,
            partner_iso=obj.partner_iso,
            commodity=obj.commodity,
            ghg_factor=obj.ghg_factor,
            transportation_cost=obj.transportation_cost,
            updated_on=obj.updated_on,
        )


class TransportKPIJsonRepository:
    """Repository for transport KPI data (emissions and costs) stored in JSON format."""

    def __init__(self, path: Path):
        self.path = path
        self._data: list["TransportKPI"] | None = None
        self._loaded = False

    def add_list(self, items: list["TransportKPI"]) -> None:
        self._ensure_loaded()
        assert self._data is not None  # _ensure_loaded guarantees this
        self._data.extend(items)
        self.save()

    def list(self) -> list["TransportKPI"]:
        self._ensure_loaded()
        assert self._data is not None  # _ensure_loaded guarantees this
        return self._data.copy()

    def get_as_dict(self) -> dict[tuple[str, str, str], float]:
        """Convert to dictionary format used by Environment (legacy - returns only ghg_factor)."""
        self._ensure_loaded()
        assert self._data is not None  # _ensure_loaded guarantees this
        return {(te.reporter_iso, te.partner_iso, te.commodity): te.ghg_factor for te in self._data}

    def save(self) -> None:
        self._ensure_loaded()
        assert self._data is not None  # _ensure_loaded guarantees this
        db_items = [TransportKPIInDb.from_domain(item) for item in self._data]
        with open(self.path, "w") as f:
            json.dump([item.model_dump() for item in db_items], f, indent=2)

    def load(self) -> None:
        with open(self.path, "r") as f:
            data = json.load(f)
        self._data = [TransportKPIInDb(**item).to_domain() for item in data]
        self._loaded = True

    def _ensure_loaded(self) -> None:
        """Lazy load data on first access."""
        if not self._loaded:
            if self._data is None:
                self._data = []
            if self.path.exists():
                self.load()
            else:
                self._loaded = True


# ---- Pydantic "In-DB" Models for BiomassAvailability ----
class BiomassAvailabilityInDb(BaseModel):
    region: str
    country: Optional[str]
    metric: str
    scenario: str
    unit: str
    year: int
    availability: float

    def to_domain(self) -> BiomassAvailability:
        return BiomassAvailability(
            region=self.region,
            country=self.country,
            metric=self.metric,
            scenario=self.scenario,
            unit=self.unit,
            year=Year(self.year),
            availability=self.availability,
        )

    @classmethod
    def from_domain(cls, obj: BiomassAvailability) -> "BiomassAvailabilityInDb":
        return cls(
            region=obj.region,
            country=obj.country,
            metric=obj.metric,
            scenario=obj.scenario,
            unit=obj.unit,
            year=obj.year,  # Year is a NewType of int
            availability=obj.availability,
        )


class BiomassAvailabilityJsonRepository:
    """Repository for biomass and CO2 storage availability data stored in JSON format."""

    def __init__(self, path: Path):
        self.path = path
        self._data: list[BiomassAvailability] | None = None
        self._loaded = False

    def add_list(self, items: list[BiomassAvailability]) -> None:
        self._ensure_loaded()
        assert self._data is not None  # _ensure_loaded guarantees this
        self._data = items  # Replace instead of extend
        self.save()

    def list(self) -> list[BiomassAvailability]:
        self._ensure_loaded()
        assert self._data is not None  # _ensure_loaded guarantees this
        return self._data.copy()

    def get_constraints_for_year(
        self, year: Year, country_mapping: Any
    ) -> dict[str, dict[tuple[str, ...], dict[int, float]]]:
        """
        Convert biomass and CO2 storage availability to secondary feedstock constraints format.
        Returns: {"bio-pci": {(iso3_codes_tuple): total_availability},
                  "co2 - stored": {(iso3_codes_tuple): total_availability}}
        """
        from collections import defaultdict

        constraints: dict[str, dict[tuple[str, ...], dict[int, float]]] = defaultdict(lambda: defaultdict(dict))

        self._ensure_loaded()
        assert self._data is not None  # _ensure_loaded guarantees this
        for item in self._data:
            if item.year != year:
                continue

            # Determine the constraint type based on the metric
            if item.metric and "co2" in item.metric.lower():
                constraint_type = "co2 - stored"  # Lowercase to match BOM normalization
            else:
                constraint_type = "bio-pci"

            # Map region to ISO3 codes
            # For CO2 storage, country field contains ISO3 directly
            if constraint_type == "co2 - stored" and item.country:
                iso3_codes = [item.country]  # Country field contains ISO3 for CO2 storage
            else:
                iso3_codes = self._map_region_to_iso3_codes(item.region, item.country, country_mapping)

            if iso3_codes:
                iso3_tuple = tuple(sorted(iso3_codes))
                if iso3_tuple not in constraints[constraint_type]:
                    constraints[constraint_type][iso3_tuple] = {}
                # Store with year as key
                constraints[constraint_type][iso3_tuple][int(year)] = (
                    constraints[constraint_type][iso3_tuple].get(int(year), 0.0) + item.availability
                )

        # Always return bio-pci key even if empty for backward compatibility
        result = dict(constraints)
        if "bio-pci" not in result:
            result["bio-pci"] = {}
        return result

    def _map_region_to_iso3_codes(self, region: str, country: Optional[str], country_mapping: Any) -> List[str]:
        """Map region/country to ISO3 codes using the country_mapping data."""
        # If country is specified, use that
        if country:
            # Handle both CountryMappingService and individual CountryMapping objects
            if hasattr(country_mapping, "_mappings"):
                # CountryMappingService
                for cm in country_mapping._mappings.values():
                    if cm.country.lower() == country.lower():
                        return [cm.iso3]
            elif hasattr(country_mapping, "country"):
                # Single CountryMapping object
                if country_mapping.country.lower() == country.lower():
                    return [country_mapping.iso3]
            else:
                # Handle None or other cases
                return []

        # Otherwise, map region to all countries in that tiam_ucl_region
        if hasattr(country_mapping, "_mappings"):
            # CountryMappingService - find all ISO3 codes with matching tiam_ucl_region
            iso3_codes = [cm.iso3 for cm in country_mapping._mappings.values() if cm.tiam_ucl_region == region]
            if iso3_codes:
                return iso3_codes

            # Fallback 1: Check if region is already an ISO3 code
            if region.upper() in [cm.iso3 for cm in country_mapping._mappings.values()]:
                return [region.upper()]

            # Fallback 2: Check if region is a country name
            for cm in country_mapping._mappings.values():
                if cm.country.lower() == region.lower():
                    return [cm.iso3]

        raise ValueError(
            f"Don't know how to map region '{region}' without country information. "
            f"No countries found with tiam_ucl_region='{region}' in country_mapping."
        )

    def save(self) -> None:
        self._ensure_loaded()
        assert self._data is not None  # _ensure_loaded guarantees this
        db_items = [BiomassAvailabilityInDb.from_domain(item) for item in self._data]
        with open(self.path, "w") as f:
            json.dump([item.model_dump() for item in db_items], f, indent=2)

    def load(self) -> None:
        try:
            with open(self.path, "r") as f:
                data = json.load(f)
            self._data = [BiomassAvailabilityInDb(**item).to_domain() for item in data]
        except (FileNotFoundError, json.JSONDecodeError):
            self._data = []
        self._loaded = True

    def _ensure_loaded(self) -> None:
        """Lazy load data on first access."""
        if not self._loaded:
            if self._data is None:
                self._data = []
            if self.path.exists():
                self.load()
            else:
                self._loaded = True


class TechnologyEmissionFactorsInDb(BaseModel):
    business_case: str
    technology: str
    boundary: str
    metallic_charge: str
    reductant: str
    direct_ghg_factor: float
    direct_with_biomass_ghg_factor: float
    indirect_ghg_factor: float

    def to_domain(self) -> TechnologyEmissionFactors:
        return TechnologyEmissionFactors(
            business_case=self.business_case,
            technology=self.technology,
            boundary=self.boundary,
            metallic_charge=self.metallic_charge,
            reductant=self.reductant,
            direct_ghg_factor=self.direct_ghg_factor,
            direct_with_biomass_ghg_factor=self.direct_with_biomass_ghg_factor,
            indirect_ghg_factor=self.indirect_ghg_factor,
        )

    @classmethod
    def from_domain(cls, obj: TechnologyEmissionFactors) -> "TechnologyEmissionFactorsInDb":
        return cls(
            business_case=obj.business_case,
            technology=obj.technology,
            boundary=obj.boundary,
            metallic_charge=obj.metallic_charge,
            reductant=obj.reductant,
            direct_ghg_factor=obj.direct_ghg_factor,
            direct_with_biomass_ghg_factor=obj.direct_with_biomass_ghg_factor,
            indirect_ghg_factor=obj.indirect_ghg_factor,
        )


class TechnologyEmissionFactorsJsonRepository:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: list[TechnologyEmissionFactorsInDb] = []
        if self.path.exists():
            self.load()

    def add(self, item: TechnologyEmissionFactors) -> None:
        """Add a single technology emission factors entry."""
        self._data.append(TechnologyEmissionFactorsInDb.from_domain(item))
        self.save()

    def add_list(self, items: list[TechnologyEmissionFactors]) -> None:
        """Add multiple technology emission factors entries."""
        self._data.extend(TechnologyEmissionFactorsInDb.from_domain(item) for item in items)
        self.save()

    def list(self) -> list[TechnologyEmissionFactors]:
        """Return all technology emission factors."""
        return [item.to_domain() for item in self._data]

    def save(self) -> None:
        """Save data to JSON file."""
        with open(self.path, "w") as f:
            json.dump([item.model_dump() for item in self._data], f, indent=2)

    def load(self) -> None:
        """Load data from JSON file."""
        with open(self.path, "r") as f:
            data = json.load(f)
        self._data = [TechnologyEmissionFactorsInDb(**item) for item in data]


class CarbonBorderMechanismInDb(BaseModel):
    """Database model for CarbonBorderMechanism."""

    mechanism_name: str
    applying_region_column: str
    start_year: int
    end_year: int | None = None

    def to_domain(self) -> CarbonBorderMechanism:
        """Convert to domain model."""
        return CarbonBorderMechanism(
            mechanism_name=self.mechanism_name,
            applying_region_column=self.applying_region_column,
            start_year=self.start_year,
            end_year=self.end_year,
        )

    @classmethod
    def from_domain(cls, mechanism: CarbonBorderMechanism) -> Self:
        """Create from domain model."""
        return cls(
            mechanism_name=mechanism.mechanism_name,
            applying_region_column=mechanism.applying_region_column,
            start_year=mechanism.start_year,
            end_year=mechanism.end_year,
        )


class CarbonBorderMechanismListInDb(BaseModel):
    """Used to make a list of CarbonBorderMechanismInDb serializable to JSON."""

    root: List[CarbonBorderMechanismInDb]


class CarbonBorderMechanismJsonRepository:
    """Repository for storing carbon border mechanisms in a JSON file."""

    _all: Optional[dict[str, CarbonBorderMechanismInDb]] = None
    _mechanisms_to_write: List[CarbonBorderMechanism]

    def __init__(self, path: Path) -> None:
        self.path = path
        self._mechanisms_to_write = []

    def _fetch_all(self) -> dict[str, CarbonBorderMechanismInDb]:
        """Read the JSON file and return a dict mapping mechanism_name → CarbonBorderMechanismInDb."""
        try:
            if not self.path.exists():
                return {}
            with self.path.open("r", encoding="utf-8") as f:
                mechanisms_in_db = CarbonBorderMechanismListInDb.model_validate_json(f.read())
            return {m.mechanism_name: m for m in mechanisms_in_db.root}
        except FileNotFoundError:
            return {}

    @property
    def all(self) -> dict[str, CarbonBorderMechanismInDb]:
        """Lazy-load all mechanisms from the JSON file."""
        if self._all is None:
            self._all = self._fetch_all()
        return self._all

    def list(self) -> List[CarbonBorderMechanism]:
        """Return all mechanisms as domain models."""
        return [m.to_domain() for m in self.all.values()]

    def add(self, mechanism: CarbonBorderMechanism) -> None:
        """Add a single mechanism."""
        self._mechanisms_to_write.append(mechanism)

    def add_list(self, mechanisms: List[CarbonBorderMechanism]) -> None:
        """Add a list of mechanisms."""
        self._mechanisms_to_write.extend(mechanisms)

    def _write_models(self, models: List[CarbonBorderMechanismInDb]) -> None:
        """Write models to the JSON file."""
        try:
            mechanisms_list = CarbonBorderMechanismListInDb(root=sorted(models, key=lambda x: x.mechanism_name))
            with self.path.open("w", encoding="utf-8") as f:
                f.write(mechanisms_list.model_dump_json(indent=2))
        except Exception as e:
            logger.error(f"Error writing to {self.path}: {e}")
            raise


class JsonRepository:
    plants: PlantRepository
    furnace_groups: FurnaceGroupRepository
    demand_centers: DemandCenterRepository
    plant_groups: PlantGroupRepository
    suppliers: SupplierRepository
    trade_tariffs: TariffJsonRepository
    subsidies: SubsidyJsonRepository
    input_costs: InputCostsJsonRepository
    primary_feedstocks: PrimaryFeedstockJsonRepository
    carbon_costs: CarbonCostsJsonRepository
    region_emissivity: RegionEmissivityJsonRepository
    capex: CapexJsonRepository
    cost_of_capital: CostOfCapitalJsonRepository
    legal_process_connectors: LegalProcessConnectorJsonRepository
    country_mappings: CountryMappingsRepository
    hydrogen_efficiency: HydrogenEfficiencyJsonRepository
    hydrogen_capex_opex: HydrogenCapexOpexJsonRepository
    railway_costs: RailwayCostJsonRepository
    transport_emissions: TransportKPIJsonRepository
    biomass_availability: BiomassAvailabilityJsonRepository
    technology_emission_factors: TechnologyEmissionFactorsJsonRepository
    fopex: FOPEXRepository
    carbon_border_mechanisms: CarbonBorderMechanismJsonRepository
    fallback_material_costs: "FallbackMaterialCostJsonRepository"

    def __init__(
        self,
        *,
        plant_lifetime: int,
        plants_path: Path,
        demand_centers_path: Path,
        suppliers_path: Path,
        plant_groups_path: Path,
        trade_tariffs_path: Path,
        subsidies_path: Path,
        input_costs_path: Path,
        primary_feedstocks_path: Path,
        carbon_costs_path: Path,
        region_emissivity_path: Path,
        capex_path: Path,
        cost_of_capital_path: Path,
        legal_process_connectors_path: Path,
        country_mappings_path: Path,
        hydrogen_efficiency_path: Path,
        hydrogen_capex_opex_path: Path,
        railway_costs_path: Path,
        transport_emissions_path: Optional[Path] = None,
        biomass_availability_path: Optional[Path] = None,
        co2_storage_availability_path: Optional[Path] = None,
        technology_emission_factors_path: Optional[Path] = None,
        fopex_path: Optional[Path] = None,
        carbon_border_mechanisms_path: Optional[Path] = None,
        fallback_material_costs_path: Optional[Path] = None,
        current_simulation_year: Optional[int] = None,
    ) -> None:
        self.plants = PlantJsonRepository(
            plants_path, plant_lifetime=plant_lifetime, current_simulation_year=current_simulation_year
        )
        # self.furnace_groups = FurnaceGroupJsonRepository(furnace_groups_path)
        self.demand_centers = DemandCenterJsonRepository(demand_centers_path)
        self.suppliers = SupplierJsonRepository(suppliers_path)
        self.plant_groups = PlantGroupJsonRepository(
            plant_groups_path, plant_lifetime=plant_lifetime, current_simulation_year=current_simulation_year
        )
        self.trade_tariffs = TariffJsonRepository(trade_tariffs_path)
        self.subsidies = SubsidyJsonRepository(subsidies_path)
        self.input_costs = InputCostsJsonRepository(input_costs_path)
        self.primary_feedstocks = PrimaryFeedstockJsonRepository(primary_feedstocks_path)
        self.carbon_costs = CarbonCostsJsonRepository(carbon_costs_path)
        self.region_emissivity = RegionEmissivityJsonRepository(region_emissivity_path)
        self.capex = CapexJsonRepository(capex_path)
        self.cost_of_capital = CostOfCapitalJsonRepository(cost_of_capital_path)
        self.legal_process_connectors = LegalProcessConnectorJsonRepository(legal_process_connectors_path)
        self.country_mappings = CountryMappingsRepository(country_mappings_path)
        self.hydrogen_efficiency = HydrogenEfficiencyJsonRepository(hydrogen_efficiency_path)
        self.hydrogen_capex_opex = HydrogenCapexOpexJsonRepository(hydrogen_capex_opex_path)
        self.railway_costs = RailwayCostJsonRepository(railway_costs_path)

        # Handle optional transport_emissions_path by creating empty repository
        if transport_emissions_path:
            self.transport_emissions = TransportKPIJsonRepository(transport_emissions_path)
        else:
            # Create a temporary empty file for default behavior
            import tempfile

            temp_file = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
            temp_file.write("[]")  # Empty JSON array
            temp_file.close()
            self.transport_emissions = TransportKPIJsonRepository(Path(temp_file.name))

        # Handle optional biomass_availability_path by creating empty repository
        if biomass_availability_path:
            self.biomass_availability = BiomassAvailabilityJsonRepository(biomass_availability_path)
        else:
            # Create a temporary empty file for default behavior
            import tempfile

            temp_file = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
            temp_file.write("[]")  # Empty JSON array
            temp_file.close()
            self.biomass_availability = BiomassAvailabilityJsonRepository(Path(temp_file.name))

        # Handle optional technology_emission_factors_path by creating empty repository
        if technology_emission_factors_path:
            self.technology_emission_factors = TechnologyEmissionFactorsJsonRepository(technology_emission_factors_path)
        else:
            # Create a temporary empty file for default behavior
            import tempfile

            temp_file = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
            temp_file.write("[]")  # Empty JSON array
            temp_file.close()
            self.technology_emission_factors = TechnologyEmissionFactorsJsonRepository(Path(temp_file.name))

        # Handle optional fopex_path by creating empty repository
        if fopex_path:
            self.fopex = FOPEXRepository(fopex_path)
        else:
            # Create a temporary empty file for default behavior
            import tempfile

            temp_file = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
            temp_file.write("[]")  # Empty JSON array
            temp_file.close()
            self.fopex = FOPEXRepository(Path(temp_file.name))

        # Handle optional carbon_border_mechanisms_path by creating empty repository
        if carbon_border_mechanisms_path:
            self.carbon_border_mechanisms = CarbonBorderMechanismJsonRepository(carbon_border_mechanisms_path)
        else:
            # Create a temporary empty file for default behavior
            import tempfile

            temp_file = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
            temp_file.write("[]")  # Empty JSON array
            temp_file.close()
            self.carbon_border_mechanisms = CarbonBorderMechanismJsonRepository(Path(temp_file.name))

        # Handle optional fallback_material_costs_path by creating empty repository
        if fallback_material_costs_path:
            self.fallback_material_costs = FallbackMaterialCostJsonRepository(fallback_material_costs_path)
        else:
            # Create a temporary empty file for default behavior
            import tempfile

            temp_file = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
            temp_file.write("[]")  # Empty JSON array
            temp_file.close()
            self.fallback_material_costs = FallbackMaterialCostJsonRepository(Path(temp_file.name))


@dataclass
class FallbackMaterialCostInDb:
    """Database model for FallbackMaterialCost."""

    iso3: str
    technology: str
    metric: str
    unit: str
    costs_by_year: dict[str, float]  # Store year as string for JSON compatibility

    def to_domain(self) -> FallbackMaterialCost:
        """Convert to domain model."""
        from ...domain import Year

        # Convert string keys back to Year objects
        costs_by_year = {Year(int(year_str)): cost for year_str, cost in self.costs_by_year.items()}
        return FallbackMaterialCost(
            iso3=self.iso3,
            technology=self.technology,
            metric=self.metric,
            unit=self.unit,
            costs_by_year=costs_by_year,
        )

    @classmethod
    def from_domain(cls, obj: FallbackMaterialCost) -> "FallbackMaterialCostInDb":
        """Convert from domain model."""
        # Convert Year objects to strings for JSON storage
        costs_by_year = {
            str(year.value if hasattr(year, "value") else int(year)): cost for year, cost in obj.costs_by_year.items()
        }
        return cls(
            iso3=obj.iso3,
            technology=obj.technology,
            metric=obj.metric,
            unit=obj.unit,
            costs_by_year=costs_by_year,
        )


class FallbackMaterialCostJsonRepository:
    """Repository for fallback material cost data stored in JSON format."""

    def __init__(self, path: Path):
        self.path = path
        self._data: list[FallbackMaterialCostInDb] = []
        if self.path.exists():
            self.load()

    def add(self, item: FallbackMaterialCost) -> None:
        """Add a single fallback material cost entry."""
        self._data.append(FallbackMaterialCostInDb.from_domain(item))
        self.save()

    def add_list(self, items: list[FallbackMaterialCost]) -> None:
        """Add multiple fallback material cost entries."""
        self._data.extend(FallbackMaterialCostInDb.from_domain(item) for item in items)
        self.save()

    def list(self) -> List["FallbackMaterialCost"]:  # use List because of list method
        """Return all fallback material costs."""
        return [item.to_domain() for item in self._data]

    def get_by_iso3_and_technology(self, iso3: str, technology: str) -> List["FallbackMaterialCost"]:
        """Get fallback material costs for a specific country and technology."""
        return [
            item.to_domain()
            for item in self._data
            if item.iso3.upper() == iso3.upper() and item.technology == technology
        ]

    def get_by_iso3(self, iso3: str) -> List["FallbackMaterialCost"]:
        """Get all fallback material costs for a specific country."""
        return [item.to_domain() for item in self._data if item.iso3.upper() == iso3.upper()]

    def save(self) -> None:
        """Save data to JSON file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = [item.__dict__ for item in self._data]
        with open(self.path, "w") as f:
            json.dump(data, f, indent=2)

    def load(self) -> None:
        """Load data from JSON file."""
        with open(self.path, "r") as f:
            data = json.load(f)
        self._data = [FallbackMaterialCostInDb(**item) for item in data]
