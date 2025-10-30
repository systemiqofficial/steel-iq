"""Integration tests for JSON persistence of historical production data."""

import json
from datetime import date

from steelo.domain.models import Plant, FurnaceGroup, Technology, Location, PointInTime, TimeFrame, Year, Volumes
from steelo.adapters.repositories.json_repository import PlantJsonRepository


def test_plant_json_serialization_with_historical_production(tmp_path):
    """Test that historical production is correctly serialized to JSON."""
    # Create mock plant with historical production
    furnace_group = FurnaceGroup(
        furnace_group_id="P001_0",
        capacity=Volumes(1200.0),
        status="operating",
        last_renovation_date=date(2010, 1, 1),
        technology=Technology(name="BOF", product="steel"),
        historical_production={
            Year(2019): Volumes(1100.0),
            Year(2020): Volumes(1050.0),
            Year(2021): Volumes(1150.0),
            Year(2022): Volumes(1180.0),
        },
        utilization_rate=0.8,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2010), end=Year(2060)),
            plant_lifetime=20,
        ),
    )

    plant = Plant(
        plant_id="P001",
        location=Location(lat=52.52, lon=13.40, country="DEU", region="Europe", iso3="DEU"),
        furnace_groups=[furnace_group],
        power_source="grid",
        soe_status="private",
        parent_gem_id="GEM001",
        workforce_size=1000,
        certified=False,
        category_steel_product=set(),
        technology_unit_fopex={},
    )

    # Save to JSON
    json_path = tmp_path / "test_plants.json"
    repo = PlantJsonRepository(json_path, plant_lifetime=20)
    repo.add(plant)

    # Read back
    loaded_plants = repo.list()
    assert len(loaded_plants) == 1

    loaded_fg = loaded_plants[0].furnace_groups[0]
    assert loaded_fg.historical_production == {
        Year(2019): Volumes(1100.0),
        Year(2020): Volumes(1050.0),
        Year(2021): Volumes(1150.0),
        Year(2022): Volumes(1180.0),
    }

    # Verify JSON structure
    with open(json_path) as f:
        data = json.load(f)

    fg_data = data["root"][0]["furnace_groups"][0]
    assert "historical_production" in fg_data
    assert fg_data["historical_production"] == {"2019": 1100.0, "2020": 1050.0, "2021": 1150.0, "2022": 1180.0}


def test_plant_json_serialization_with_empty_historical_production(tmp_path):
    """Test that empty historical production is correctly handled."""
    # Create mock plant with empty historical production
    furnace_group = FurnaceGroup(
        furnace_group_id="P002_0",
        capacity=Volumes(800.0),
        status="operating",
        last_renovation_date=date(2015, 1, 1),
        technology=Technology(name="EAF", product="steel"),
        historical_production={},  # Empty historical production
        utilization_rate=0.8,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2015), end=Year(2065)),
            plant_lifetime=20,
        ),
    )

    plant = Plant(
        plant_id="P002",
        location=Location(lat=48.85, lon=2.35, country="FRA", region="Europe", iso3="FRA"),
        furnace_groups=[furnace_group],
        power_source="renewable",
        soe_status="private",
        parent_gem_id="GEM002",
        workforce_size=500,
        certified=False,
        category_steel_product=set(),
        technology_unit_fopex={},
    )

    # Save to JSON
    json_path = tmp_path / "test_plants_empty.json"
    repo = PlantJsonRepository(json_path, plant_lifetime=20)
    repo.add(plant)

    # Read back
    loaded_plants = repo.list()
    assert len(loaded_plants) == 1

    loaded_fg = loaded_plants[0].furnace_groups[0]
    assert loaded_fg.historical_production == {}

    # Verify JSON structure
    with open(json_path) as f:
        data = json.load(f)

    fg_data = data["root"][0]["furnace_groups"][0]
    assert "historical_production" in fg_data
    assert fg_data["historical_production"] == {}


def test_multiple_plants_json_persistence(tmp_path):
    """Test JSON persistence of multiple plants with different historical production patterns."""
    plants = []

    # Plant 1: Full historical production
    fg1 = FurnaceGroup(
        furnace_group_id="P001_0",
        capacity=Volumes(1000.0),
        status="operating",
        last_renovation_date=date(2010, 1, 1),
        technology=Technology(name="BF", product="iron"),
        historical_production={
            Year(2019): Volumes(950.0),
            Year(2020): Volumes(960.0),
            Year(2021): Volumes(970.0),
            Year(2022): Volumes(980.0),
        },
        utilization_rate=0.95,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2010), end=Year(2060)),
            plant_lifetime=20,
        ),
    )

    plant1 = Plant(
        plant_id="P001",
        location=Location(lat=52.52, lon=13.40, country="DEU", region="Europe", iso3="DEU"),
        furnace_groups=[fg1],
        power_source="grid",
        soe_status="private",
        parent_gem_id="GEM001",
        workforce_size=1000,
        certified=False,
        category_steel_product=set(),
        technology_unit_fopex={},
    )
    plants.append(plant1)

    # Plant 2: Partial historical production (missing some years)
    fg2 = FurnaceGroup(
        furnace_group_id="P002_0",
        capacity=Volumes(800.0),
        status="operating",
        last_renovation_date=date(2015, 1, 1),
        technology=Technology(name="EAF", product="steel"),
        historical_production={
            Year(2019): Volumes(750.0),
            Year(2020): Volumes(760.0),
            # 2021 missing
            Year(2022): Volumes(780.0),
        },
        utilization_rate=0.94,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2015), end=Year(2065)),
            plant_lifetime=20,
        ),
    )

    plant2 = Plant(
        plant_id="P002",
        location=Location(lat=48.85, lon=2.35, country="FRA", region="Europe", iso3="FRA"),
        furnace_groups=[fg2],
        power_source="renewable",
        soe_status="private",
        parent_gem_id="GEM002",
        workforce_size=500,
        certified=False,
        category_steel_product=set(),
        technology_unit_fopex={},
    )
    plants.append(plant2)

    # Save all plants
    json_path = tmp_path / "test_multiple_plants.json"
    repo = PlantJsonRepository(json_path, plant_lifetime=20)
    repo.add_list(plants)

    # Read back and verify
    loaded_plants = repo.list()
    assert len(loaded_plants) == 2

    # Verify Plant 1
    p1 = next(p for p in loaded_plants if p.plant_id == "P001")
    assert p1.furnace_groups[0].historical_production == {
        Year(2019): Volumes(950.0),
        Year(2020): Volumes(960.0),
        Year(2021): Volumes(970.0),
        Year(2022): Volumes(980.0),
    }

    # Verify Plant 2
    p2 = next(p for p in loaded_plants if p.plant_id == "P002")
    assert p2.furnace_groups[0].historical_production == {
        Year(2019): Volumes(750.0),
        Year(2020): Volumes(760.0),
        Year(2022): Volumes(780.0),
    }
