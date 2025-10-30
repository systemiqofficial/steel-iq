"""Pure unit tests for plant aggregation logic (no file I/O)."""

import pytest
from datetime import date

from steelo.domain.models import FurnaceGroup, Location, Plant, Technology, TimeFrame, Year, Volumes, PointInTime
from steelo.adapters.dataprocessing.plant_aggregation import PlantAggregationService, RawPlantData


def create_test_location(iso3: str = "USA") -> Location:
    """Create a test location object."""
    return Location(
        lat=40.0,
        lon=-100.0,
        country=iso3,
        region="test_region",
        iso3=iso3,
    )


def create_test_furnace_group(
    fg_id: str,
    technology_name: str = "BF",
    capacity: float = 1000000,
) -> FurnaceGroup:
    """Create a test furnace group object."""
    return FurnaceGroup(
        furnace_group_id=fg_id,
        capacity=Volumes(capacity),
        status="operating",
        last_renovation_date=date(2020, 1, 1),
        technology=Technology(
            name=technology_name,
            product="iron" if technology_name in ["BF", "DRI"] else "steel",
            technology_readiness_level=None,
            process_emissions=None,
            dynamic_business_case=[],
        ),
        historical_production={},
        utilization_rate=0.8,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2020), end=Year(2040)),
            plant_lifetime=20,
        ),
    )


class TestPlantAggregation:
    """Test the PlantAggregationService."""

    def test_aggregate_duplicate_plant_ids(self):
        """Test that duplicate Plant IDs are properly aggregated into single plants."""
        # Arrange
        raw_plants = [
            RawPlantData(
                plant_id="P100",
                location=create_test_location(),
                furnace_groups=[
                    create_test_furnace_group("temp_1", "BF", 1000000),
                    create_test_furnace_group("temp_2", "BOF", 2000000),
                ],
                power_source="grid",
                soe_status="private",
                parent_gem_id="G100",
                workforce_size=1000,
                technology_fopex={},
            ),
            RawPlantData(
                plant_id="P100",  # Duplicate ID
                location=create_test_location(),
                furnace_groups=[
                    create_test_furnace_group("temp_3", "EAF", 500000),
                ],
                power_source="grid",
                soe_status="private",
                parent_gem_id="G100",
                workforce_size=1000,
                technology_fopex={},
            ),
            RawPlantData(
                plant_id="P200",
                location=create_test_location(),
                furnace_groups=[
                    create_test_furnace_group("temp_4", "DRI", 1500000),
                ],
                power_source="renewable",
                soe_status="SOE",
                parent_gem_id="G200",
                workforce_size=2000,
                technology_fopex={},
            ),
        ]

        # Act
        service = PlantAggregationService()
        plants = service.aggregate_plants(raw_plants)

        # Assert
        assert len(plants) == 2, "Should have 2 unique plants (P100 and P200)"

        # Find P100 plant
        p100 = next((p for p in plants if p.plant_id == "P100"), None)
        assert p100 is not None, "Plant P100 should exist"
        assert len(p100.furnace_groups) == 3, "P100 should have 3 furnace groups (aggregated)"

        # Find P200 plant
        p200 = next((p for p in plants if p.plant_id == "P200"), None)
        assert p200 is not None, "Plant P200 should exist"
        assert len(p200.furnace_groups) == 1, "P200 should have 1 furnace group"

    def test_furnace_group_ids_globally_unique(self):
        """Test that furnace group IDs remain globally unique after aggregation."""
        # Arrange
        raw_plants = [
            RawPlantData(
                plant_id="P100",
                location=create_test_location(),
                furnace_groups=[
                    create_test_furnace_group("will_be_replaced_0", "BF"),
                    create_test_furnace_group("will_be_replaced_1", "BOF"),
                ],
                power_source="grid",
                soe_status="private",
                parent_gem_id="G100",
                workforce_size=1000,
                technology_fopex={},
            ),
            RawPlantData(
                plant_id="P100",  # Duplicate ID
                location=create_test_location(),
                furnace_groups=[
                    create_test_furnace_group("will_be_replaced_0", "EAF"),  # Same temp ID
                    create_test_furnace_group("will_be_replaced_1", "DRI"),  # Same temp ID
                ],
                power_source="grid",
                soe_status="private",
                parent_gem_id="G100",
                workforce_size=1000,
                technology_fopex={},
            ),
        ]

        # Act
        service = PlantAggregationService()
        plants = service.aggregate_plants(raw_plants)

        # Assert
        all_fg_ids = []
        for plant in plants:
            for fg in plant.furnace_groups:
                all_fg_ids.append(fg.furnace_group_id)

        assert len(all_fg_ids) == len(set(all_fg_ids)), "All furnace group IDs should be unique"

        # Check specific IDs
        p100 = plants[0]
        fg_ids = [fg.furnace_group_id for fg in p100.furnace_groups]
        expected_ids = ["P100_0", "P100_1", "P100_2", "P100_3"]
        assert fg_ids == expected_ids, f"Expected {expected_ids}, got {fg_ids}"

    def test_plant_attributes_preserved_from_first_occurrence(self):
        """Test that plant attributes are preserved from the first occurrence."""
        # Arrange
        raw_plants = [
            RawPlantData(
                plant_id="P100",
                location=create_test_location("USA"),
                furnace_groups=[create_test_furnace_group("temp_1", "BF")],
                power_source="grid",
                soe_status="private",
                parent_gem_id="G100_FIRST",
                workforce_size=1000,
                technology_fopex={"BF": 100.0},
            ),
            RawPlantData(
                plant_id="P100",  # Duplicate with different attributes
                location=create_test_location("CHN"),  # Different location
                furnace_groups=[create_test_furnace_group("temp_2", "EAF")],
                power_source="renewable",  # Different power source
                soe_status="SOE",  # Different SOE status
                parent_gem_id="G100_SECOND",  # Different parent
                workforce_size=2000,  # Different workforce
                technology_fopex={"EAF": 200.0},  # Different fopex
            ),
        ]

        # Act
        service = PlantAggregationService()
        plants = service.aggregate_plants(raw_plants)

        # Assert
        p100 = plants[0]
        assert p100.location.iso3 == "USA", "Location should be from first occurrence"
        assert p100.power_source == "grid", "Power source should be from first occurrence"
        assert p100.soe_status == "private", "SOE status should be from first occurrence"
        assert p100.parent_gem_id == "G100_FIRST", "Parent GEM ID should be from first occurrence"
        assert p100.workforce_size == 1000, "Workforce size should be from first occurrence"
        assert p100.technology_unit_fopex == {"BF": 100.0}, "Technology fopex should be from first occurrence"

    def test_single_plant_ids_not_affected(self):
        """Test that plants without duplicate IDs are not affected by aggregation."""
        # Arrange
        raw_plants = [
            RawPlantData(
                plant_id="P100",
                location=create_test_location(),
                furnace_groups=[
                    create_test_furnace_group("temp_1", "BF"),
                ],
                power_source="grid",
                soe_status="private",
                parent_gem_id="G100",
                workforce_size=1000,
                technology_fopex={},
            ),
            RawPlantData(
                plant_id="P200",
                location=create_test_location(),
                furnace_groups=[
                    create_test_furnace_group("temp_2", "DRI"),
                ],
                power_source="renewable",
                soe_status="SOE",
                parent_gem_id="G200",
                workforce_size=2000,
                technology_fopex={},
            ),
        ]

        # Act
        service = PlantAggregationService()
        plants = service.aggregate_plants(raw_plants)

        # Assert
        assert len(plants) == 2, "Should have 2 plants"

        p100 = next((p for p in plants if p.plant_id == "P100"), None)
        assert p100 is not None
        assert len(p100.furnace_groups) == 1
        assert p100.furnace_groups[0].furnace_group_id == "P100_0"

        p200 = next((p for p in plants if p.plant_id == "P200"), None)
        assert p200 is not None
        assert len(p200.furnace_groups) == 1
        assert p200.furnace_groups[0].furnace_group_id == "P200_0"

    def test_empty_input_returns_empty_list(self):
        """Test that empty input returns empty output."""
        service = PlantAggregationService()
        plants = service.aggregate_plants([])
        assert plants == [], "Empty input should return empty list"

    def test_multiple_duplicates_complex_scenario(self):
        """Test complex scenario with multiple plants having different numbers of duplicates."""
        # Arrange
        raw_plants = [
            # P100 appears 3 times
            RawPlantData(
                plant_id="P100",
                location=create_test_location(),
                furnace_groups=[
                    create_test_furnace_group("temp", "BF"),
                    create_test_furnace_group("temp", "BOF"),
                ],
                power_source="grid",
                soe_status="private",
                parent_gem_id="G100",
                workforce_size=1000,
                technology_fopex={},
            ),
            RawPlantData(
                plant_id="P100",
                location=create_test_location(),
                furnace_groups=[
                    create_test_furnace_group("temp", "EAF"),
                ],
                power_source="grid",
                soe_status="private",
                parent_gem_id="G100",
                workforce_size=1000,
                technology_fopex={},
            ),
            RawPlantData(
                plant_id="P100",
                location=create_test_location(),
                furnace_groups=[
                    create_test_furnace_group("temp", "DRI"),
                    create_test_furnace_group("temp", "MOE"),
                ],
                power_source="grid",
                soe_status="private",
                parent_gem_id="G100",
                workforce_size=1000,
                technology_fopex={},
            ),
            # P200 appears once
            RawPlantData(
                plant_id="P200",
                location=create_test_location(),
                furnace_groups=[
                    create_test_furnace_group("temp", "BF"),
                ],
                power_source="renewable",
                soe_status="SOE",
                parent_gem_id="G200",
                workforce_size=2000,
                technology_fopex={},
            ),
            # P300 appears twice
            RawPlantData(
                plant_id="P300",
                location=create_test_location(),
                furnace_groups=[
                    create_test_furnace_group("temp", "EAF"),
                    create_test_furnace_group("temp", "BOF"),
                    create_test_furnace_group("temp", "BF"),
                ],
                power_source="grid",
                soe_status="private",
                parent_gem_id="G300",
                workforce_size=3000,
                technology_fopex={},
            ),
            RawPlantData(
                plant_id="P300",
                location=create_test_location(),
                furnace_groups=[
                    create_test_furnace_group("temp", "DRI"),
                ],
                power_source="grid",
                soe_status="private",
                parent_gem_id="G300",
                workforce_size=3000,
                technology_fopex={},
            ),
        ]

        # Act
        service = PlantAggregationService()
        plants = service.aggregate_plants(raw_plants)

        # Assert
        assert len(plants) == 3, "Should have 3 unique plants"

        # Check P100 (3 occurrences, 5 total furnace groups)
        p100 = next((p for p in plants if p.plant_id == "P100"), None)
        assert p100 is not None
        assert len(p100.furnace_groups) == 5
        p100_fg_ids = [fg.furnace_group_id for fg in p100.furnace_groups]
        assert p100_fg_ids == ["P100_0", "P100_1", "P100_2", "P100_3", "P100_4"]

        # Check P200 (1 occurrence, 1 furnace group)
        p200 = next((p for p in plants if p.plant_id == "P200"), None)
        assert p200 is not None
        assert len(p200.furnace_groups) == 1
        assert p200.furnace_groups[0].furnace_group_id == "P200_0"

        # Check P300 (2 occurrences, 4 total furnace groups)
        p300 = next((p for p in plants if p.plant_id == "P300"), None)
        assert p300 is not None
        assert len(p300.furnace_groups) == 4
        p300_fg_ids = [fg.furnace_group_id for fg in p300.furnace_groups]
        assert p300_fg_ids == ["P300_0", "P300_1", "P300_2", "P300_3"]

    def test_validate_no_duplicate_furnace_group_ids(self):
        """Test the validation function for duplicate furnace group IDs."""
        # Arrange - create plants with duplicate FG IDs
        p1 = Plant(
            plant_id="P100",
            location=create_test_location(),
            furnace_groups=[
                create_test_furnace_group("P100_0", "BF"),
                create_test_furnace_group("P100_1", "BOF"),
            ],
            power_source="grid",
            soe_status="private",
            parent_gem_id="G100",
            workforce_size=1000,
            certified=False,
            category_steel_product=set(),
            technology_unit_fopex={},
        )

        p2 = Plant(
            plant_id="P200",
            location=create_test_location(),
            furnace_groups=[
                create_test_furnace_group("P100_0", "EAF"),  # Duplicate ID!
            ],
            power_source="grid",
            soe_status="private",
            parent_gem_id="G200",
            workforce_size=2000,
            certified=False,
            category_steel_product=set(),
            technology_unit_fopex={},
        )

        # Act & Assert
        service = PlantAggregationService()

        # Should raise ValueError with duplicate details
        with pytest.raises(ValueError) as exc_info:
            service.validate_no_duplicate_furnace_group_ids([p1, p2])

        assert "Duplicate furnace group IDs found" in str(exc_info.value)
        assert "P100_0: 2 times" in str(exc_info.value)

        # Test with valid plants (no duplicates)
        p2.furnace_groups[0].furnace_group_id = "P200_0"  # Fix the duplicate
        assert service.validate_no_duplicate_furnace_group_ids([p1, p2]) is True
