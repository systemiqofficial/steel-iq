"""Integration tests for plant metadata capture and ID remapping."""

import pytest
from steelo.adapters.dataprocessing.plant_metadata import (
    FurnaceGroupMetadata,
    validate_commissioning_year,
    validate_age_at_reference,
    create_metadata_dict,
)
from steelo.adapters.dataprocessing.plant_aggregation import (
    PlantAggregationService,
    RawPlantData,
)
from steelo.domain.models import (
    FurnaceGroup,
    Technology,
    Location,
    PointInTime,
    TimeFrame,
    Year,
    Volumes,
)


def test_validate_commissioning_year_valid():
    """Test commissioning year validation with valid year."""
    warnings = validate_commissioning_year(2000, "test_fg")
    assert warnings == []


def test_validate_commissioning_year_too_old():
    """Test commissioning year validation with year too old."""
    warnings = validate_commissioning_year(1800, "test_fg")
    assert len(warnings) == 1
    assert "commissioning_year_too_old" in warnings[0]


def test_validate_commissioning_year_in_future():
    """Test commissioning year validation with future year."""
    warnings = validate_commissioning_year(2100, "test_fg")
    assert len(warnings) == 1
    assert "commissioning_year_in_future" in warnings[0]


def test_validate_commissioning_year_missing():
    """Test commissioning year validation with None."""
    warnings = validate_commissioning_year(None, "test_fg")
    assert warnings == ["commissioning_year_missing"]


def test_validate_age_at_reference_valid():
    """Test age validation with valid age."""
    warnings = validate_age_at_reference(15, 2025, "test_fg")
    assert warnings == []


def test_validate_age_at_reference_negative():
    """Test age validation with negative age."""
    warnings = validate_age_at_reference(-5, 2025, "test_fg")
    assert len(warnings) == 1
    assert "negative_age" in warnings[0]


def test_validate_age_at_reference_too_old():
    """Test age validation with implausibly old age."""
    warnings = validate_age_at_reference(200, 2025, "test_fg")
    assert len(warnings) >= 1
    assert any("implausibly_old" in w for w in warnings)


def test_metadata_dict_creation(tmp_path):
    """Test creating metadata dictionary for JSON serialization."""
    # Create sample metadata
    metadata = {
        "CHN_001_0": FurnaceGroupMetadata(
            commissioning_year=1985,
            age_at_reference_year=None,
            last_renovation_year=2005,
            age_source="exact",
            source_sheet="Iron and steel plants",
            source_row=42,
            validation_warnings=[],
        ),
        "CHN_001_1": FurnaceGroupMetadata(
            commissioning_year=None,
            age_at_reference_year=15,
            last_renovation_year=None,
            age_source="imputed",
            source_sheet="Iron and steel plants",
            source_row=43,
            validation_warnings=["commissioning_year_missing"],
        ),
    }

    # Create a dummy Excel file
    excel_path = tmp_path / "test.xlsx"
    excel_path.write_text("dummy")

    # Create metadata dict
    result = create_metadata_dict(
        furnace_group_metadata=metadata,
        plant_lifetime_used=20,
        data_reference_year=2025,
        master_excel_path=excel_path,
        master_excel_version="v1.0",
    )

    # Validate structure
    assert result["schema_version"] == "1.0"
    assert result["metadata"]["plant_lifetime_used"] == 20
    assert result["metadata"]["data_reference_year"] == 2025
    assert result["metadata"]["master_excel_version"] == "v1.0"
    assert "master_excel_hash" in result["metadata"]
    assert "generated_at" in result["metadata"]

    # Validate furnace group entries
    assert "CHN_001_0" in result["furnace_groups"]
    assert "CHN_001_1" in result["furnace_groups"]

    fg0 = result["furnace_groups"]["CHN_001_0"]
    assert fg0["commissioning_year"] == 1985
    assert fg0["age_at_reference_year"] is None
    assert fg0["last_renovation_year"] == 2005
    assert fg0["age_source"] == "exact"

    fg1 = result["furnace_groups"]["CHN_001_1"]
    assert fg1["commissioning_year"] is None
    assert fg1["age_at_reference_year"] == 15
    assert fg1["age_source"] == "imputed"


def test_aggregation_with_metadata_id_remapping():
    """Test that aggregation correctly remaps metadata IDs."""
    # Create sample raw plants with temporary IDs
    tech = Technology(name="BF", product="iron")
    location = Location(lat=40.0, lon=116.0, country="CHN", region="Asia", iso3="CHN")

    fg1 = FurnaceGroup(
        furnace_group_id="temp_CHN_001_BF_0",
        capacity=Volumes(1000000),
        status="operating",
        technology=tech,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2020), end=Year(2040)),
            plant_lifetime=20,
        ),
        last_renovation_date=None,
        historical_production={},
        utilization_rate=0.8,
    )

    fg2 = FurnaceGroup(
        furnace_group_id="temp_CHN_001_BF_1",
        capacity=Volumes(500000),
        status="operating",
        technology=tech,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2015), end=Year(2035)),
            plant_lifetime=20,
        ),
        last_renovation_date=None,
        historical_production={},
        utilization_rate=0.8,
    )

    raw_plant = RawPlantData(
        plant_id="CHN_001",
        location=location,
        furnace_groups=[fg1, fg2],
        power_source="grid",
        soe_status="private",
        parent_gem_id="",
        workforce_size=1000,
        technology_fopex={},
    )

    # Create metadata with temporary IDs
    metadata = {
        "temp_CHN_001_BF_0": FurnaceGroupMetadata(
            commissioning_year=1985,
            age_at_reference_year=None,
            last_renovation_year=2005,
            age_source="exact",
            source_sheet="test",
            source_row=1,
            validation_warnings=[],
        ),
        "temp_CHN_001_BF_1": FurnaceGroupMetadata(
            commissioning_year=1990,
            age_at_reference_year=None,
            last_renovation_year=2010,
            age_source="exact",
            source_sheet="test",
            source_row=2,
            validation_warnings=[],
        ),
    }

    # Aggregate
    aggregator = PlantAggregationService()
    plants, remapped_metadata = aggregator.aggregate_plants_with_metadata([raw_plant], metadata)

    # Verify plants were aggregated
    assert len(plants) == 1
    plant = plants[0]
    assert plant.plant_id == "CHN_001"
    assert len(plant.furnace_groups) == 2

    # Verify IDs were remapped
    assert plant.furnace_groups[0].furnace_group_id == "CHN_001_0"
    assert plant.furnace_groups[1].furnace_group_id == "CHN_001_1"

    # Verify metadata was remapped
    assert "CHN_001_0" in remapped_metadata
    assert "CHN_001_1" in remapped_metadata
    assert "temp_CHN_001_BF_0" not in remapped_metadata
    assert "temp_CHN_001_BF_1" not in remapped_metadata

    # Verify metadata content preserved
    assert remapped_metadata["CHN_001_0"].commissioning_year == 1985
    assert remapped_metadata["CHN_001_1"].commissioning_year == 1990


def test_aggregation_metadata_validation_catches_missing():
    """Test that aggregation validates all furnace groups have metadata."""
    tech = Technology(name="BF", product="iron")
    location = Location(lat=40.0, lon=116.0, country="CHN", region="Asia", iso3="CHN")

    fg = FurnaceGroup(
        furnace_group_id="temp_CHN_001_BF_0",
        capacity=Volumes(1000000),
        status="operating",
        technology=tech,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2020), end=Year(2040)),
            plant_lifetime=20,
        ),
        last_renovation_date=None,
        historical_production={},
        utilization_rate=0.8,
    )

    raw_plant = RawPlantData(
        plant_id="CHN_001",
        location=location,
        furnace_groups=[fg],
        power_source="grid",
        soe_status="private",
        parent_gem_id="",
        workforce_size=1000,
        technology_fopex={},
    )

    # Metadata is EMPTY - should fail validation
    metadata = {}

    aggregator = PlantAggregationService()
    with pytest.raises(ValueError, match="Metadata missing for furnace groups"):
        aggregator.aggregate_plants_with_metadata([raw_plant], metadata)
