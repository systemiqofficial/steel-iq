"""Service layer for aggregating plants with duplicate IDs."""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List

from steelo.domain.models import FurnaceGroup, Location, Plant

logger = logging.getLogger(__name__)


@dataclass
class RawPlantData:
    """Raw plant data from Excel/CSV before aggregation."""

    plant_id: str
    location: Location
    furnace_groups: List[FurnaceGroup]
    power_source: str
    soe_status: str
    parent_gem_id: str
    workforce_size: int
    technology_fopex: Dict[str, float]


class PlantAggregationService:
    """Service for aggregating plants with duplicate IDs."""

    def aggregate_plants(self, raw_plants: List[RawPlantData]) -> List[Plant]:
        """
        Aggregate raw plant data, handling duplicate Plant IDs.

        This method:
        - Combines furnace groups from duplicate Plant IDs into single plants
        - Ensures globally unique furnace group IDs using a counter per plant
        - Preserves plant attributes from the first occurrence of each Plant ID

        Args:
            raw_plants: List of raw plant data, potentially with duplicate Plant IDs

        Returns:
            List of aggregated Plant objects with unique IDs and properly numbered furnace groups
        """
        plants_dict: Dict[str, Plant] = {}
        global_fg_counter: Dict[str, int] = {}  # Track FG count per plant

        for raw_plant in raw_plants:
            if raw_plant.plant_id in plants_dict:
                # Aggregate to existing plant
                existing_plant = plants_dict[raw_plant.plant_id]
                counter = global_fg_counter[raw_plant.plant_id]

                # Renumber furnace groups to ensure uniqueness
                for fg in raw_plant.furnace_groups:
                    fg.furnace_group_id = f"{raw_plant.plant_id}_{counter}"
                    existing_plant.furnace_groups.append(fg)
                    counter += 1

                global_fg_counter[raw_plant.plant_id] = counter
            else:
                # Create new plant
                # Renumber furnace groups consistently
                counter = 0
                for fg in raw_plant.furnace_groups:
                    fg.furnace_group_id = f"{raw_plant.plant_id}_{counter}"
                    counter += 1

                plant = Plant(
                    plant_id=raw_plant.plant_id,
                    location=raw_plant.location,
                    furnace_groups=raw_plant.furnace_groups,
                    power_source=raw_plant.power_source,
                    soe_status=raw_plant.soe_status,
                    parent_gem_id=raw_plant.parent_gem_id,
                    workforce_size=raw_plant.workforce_size,
                    certified=False,
                    category_steel_product=set(),
                    technology_unit_fopex=raw_plant.technology_fopex,
                )
                plants_dict[raw_plant.plant_id] = plant
                global_fg_counter[raw_plant.plant_id] = counter

        return list(plants_dict.values())

    def aggregate_plants_with_metadata(
        self,
        raw_plants: List[RawPlantData],
        raw_canonical_metadata: Dict[str, Any],
    ) -> tuple[List[Plant], Dict[str, Any]]:
        """
        Aggregate raw plant data AND remap metadata to final furnace group IDs.

        This method:
        - Combines furnace groups from duplicate Plant IDs into single plants
        - Ensures globally unique furnace group IDs using a counter per plant
        - Remaps metadata keys to match final IDs after aggregation
        - Validates that all furnace groups have metadata

        Args:
            raw_plants: List of raw plant data, potentially with duplicate Plant IDs
            raw_canonical_metadata: Dict mapping temp furnace group IDs to metadata

        Returns:
            Tuple of (aggregated plants, remapped metadata dict)

        Raises:
            ValueError: If metadata is missing for any furnace group after aggregation
        """
        plants_dict: Dict[str, Plant] = {}
        global_fg_counter: Dict[str, int] = {}  # Track FG count per plant
        id_mappings: Dict[str, str] = {}  # old_id -> new_id

        for raw_plant in raw_plants:
            if raw_plant.plant_id in plants_dict:
                # Aggregate to existing plant
                existing_plant = plants_dict[raw_plant.plant_id]
                counter = global_fg_counter[raw_plant.plant_id]

                # Renumber furnace groups to ensure uniqueness
                for fg in raw_plant.furnace_groups:
                    old_id = fg.furnace_group_id
                    new_id = f"{raw_plant.plant_id}_{counter}"
                    fg.furnace_group_id = new_id
                    existing_plant.furnace_groups.append(fg)
                    counter += 1

                    # Track ID mapping for metadata
                    id_mappings[old_id] = new_id

                global_fg_counter[raw_plant.plant_id] = counter
            else:
                # Create new plant
                # Renumber furnace groups consistently
                counter = 0
                for fg in raw_plant.furnace_groups:
                    old_id = fg.furnace_group_id
                    new_id = f"{raw_plant.plant_id}_{counter}"
                    fg.furnace_group_id = new_id
                    counter += 1

                    # Track ID mapping for metadata
                    id_mappings[old_id] = new_id

                plant = Plant(
                    plant_id=raw_plant.plant_id,
                    location=raw_plant.location,
                    furnace_groups=raw_plant.furnace_groups,
                    power_source=raw_plant.power_source,
                    soe_status=raw_plant.soe_status,
                    parent_gem_id=raw_plant.parent_gem_id,
                    workforce_size=raw_plant.workforce_size,
                    certified=False,
                    category_steel_product=set(),
                    technology_unit_fopex=raw_plant.technology_fopex,
                )
                plants_dict[raw_plant.plant_id] = plant
                global_fg_counter[raw_plant.plant_id] = counter

        # Remap metadata keys
        remapped_metadata = {}
        for old_id, metadata in raw_canonical_metadata.items():
            new_id = id_mappings.get(old_id, old_id)  # Use new ID if mapped, else keep old
            remapped_metadata[new_id] = metadata

            # Log remapping for debugging
            if new_id != old_id:
                logger.debug(f"Remapped metadata: {old_id} → {new_id}")

        # Validate all furnace groups have metadata
        final_fg_ids = {fg.furnace_group_id for plant in plants_dict.values() for fg in plant.furnace_groups}
        missing = final_fg_ids - remapped_metadata.keys()
        if missing:
            raise ValueError(
                f"Metadata missing for furnace groups after aggregation: "
                f"{list(sorted(missing))[:10]}{'...' if len(missing) > 10 else ''}"
            )

        return list(plants_dict.values()), remapped_metadata

    def validate_no_duplicate_furnace_group_ids(self, plants: List[Plant]) -> bool:
        """
        Validate that all furnace group IDs are unique across all plants.

        Args:
            plants: List of Plant objects to validate

        Returns:
            True if all furnace group IDs are unique, False otherwise

        Raises:
            ValueError: If duplicate furnace group IDs are found (includes details)
        """
        all_fg_ids = []
        for plant in plants:
            for fg in plant.furnace_groups:
                all_fg_ids.append(fg.furnace_group_id)

        if len(all_fg_ids) != len(set(all_fg_ids)):
            # Find duplicates for error message
            from collections import Counter

            fg_counts = Counter(all_fg_ids)
            duplicates = {fg_id: count for fg_id, count in fg_counts.items() if count > 1}
            duplicate_details = "\n".join([f"{fg_id}: {count} times" for fg_id, count in duplicates.items()])
            raise ValueError(f"Duplicate furnace group IDs found:\n{duplicate_details}")

        return True
