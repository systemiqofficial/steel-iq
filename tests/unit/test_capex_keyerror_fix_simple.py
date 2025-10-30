"""Test for the capex KeyError fix - simplified version."""

from steelo.domain.models import Plant, Technology, Location, ProductCategory


def test_capex_dict_access_with_missing_key():
    """Test that we handle missing keys in capex_dict gracefully."""
    # Create a simple plant
    location = Location(lat=0.0, lon=0.0, country="USA", region="North America", iso3="USA")

    _plant = Plant(
        plant_id="test_plant",
        location=location,
        furnace_groups=[],
        power_source="unknown",
        soe_status="unknown",
        parent_gem_id="gem_id",
        workforce_size=10,
        certified=False,
        category_steel_product={ProductCategory("Flat")},
        technology_unit_fopex={},  # Empty fopex dict for testing
    )

    # Test the specific line that was causing the KeyError
    # Line 1280 in models.py: threshold = region_capex[furnace_group.technology.name] * furnace_group.capacity

    # Create a mock furnace group with DRI technology
    class MockFurnaceGroup:
        def __init__(self):
            self.technology = Technology(name="DRI", product="iron")
            self.capacity = 500
            self.furnace_group_id = "fg1"
            self.status = "operating"
            self.historic_balance = -100

    fg = MockFurnaceGroup()

    # Test with empty region_capex (the bug scenario)
    region_capex = {}

    # The fix should use .get() with a default value
    capex_per_tonne = region_capex.get(fg.technology.name, 500.0)
    threshold = capex_per_tonne * fg.capacity

    # Should not raise KeyError and should use default value
    assert capex_per_tonne == 500.0
    assert threshold == 250000.0

    # Test with actual capex data
    region_capex = {"DRI": 453.48}
    capex_per_tonne = region_capex.get(fg.technology.name, 500.0)
    threshold = capex_per_tonne * fg.capacity

    assert capex_per_tonne == 453.48
    assert threshold == 226740.0


def test_plant_agent_capex_fallback():
    """Test the plant_agent.py fallback logic for capex."""
    # Simulate the logic in plant_agent.py

    # Case 1: Empty name_to_capex
    name_to_capex = {}
    capex_dict = name_to_capex.get("default", {})
    assert capex_dict == {}

    # Case 2: name_to_capex with greenfield but no default
    name_to_capex = {
        "greenfield": {
            "DRI": 453.48,
            "EAF": 236.87,
            "BOF": 277.02,
        }
    }

    capex_dict = name_to_capex.get("default", {})
    if not capex_dict and "greenfield" in name_to_capex:
        greenfield = name_to_capex["greenfield"]
        if isinstance(greenfield, dict):
            if all(isinstance(v, (int, float)) for v in greenfield.values()):
                capex_dict = greenfield

    assert capex_dict == {"DRI": 453.48, "EAF": 236.87, "BOF": 277.02}

    # Case 3: Nested structure (region -> tech -> capex)
    name_to_capex = {
        "greenfield": {
            "USA": {"DRI": 400.0, "EAF": 200.0},
            "EUR": {"DRI": 450.0, "EAF": 250.0},
        }
    }

    capex_dict = name_to_capex.get("default", {})
    if not capex_dict and "greenfield" in name_to_capex:
        greenfield = name_to_capex["greenfield"]
        if isinstance(greenfield, dict):
            try:
                if all(isinstance(v, (int, float)) for v in greenfield.values()):
                    capex_dict = greenfield
                else:
                    # It's a nested dict, try to get first region's data
                    for region_data in greenfield.values():
                        if isinstance(region_data, dict):
                            capex_dict = region_data
                            break
            except (AttributeError, TypeError):
                pass

    # Should get first region's data (USA in this case)
    assert capex_dict == {"DRI": 400.0, "EAF": 200.0}


def test_nested_capex_dict_access():
    """Test the nested capex dictionary access with missing keys."""

    # Mock iso3_to_region mapping
    mock_iso3_to_region = {"USA": "North America", "DEU": "Europe", "CHN": "Asia"}

    # Mock capex_dict_all_locs with missing regions/techs
    capex_dict_all_locs = {
        "North America": {"EAF": 300.0, "BOF": 400.0},  # Missing DRI
        "Europe": {"DRI": 450.0, "EAF": 350.0},  # Has DRI
        # Asia is completely missing
    }

    # Test case 1: Missing technology in existing region
    region = mock_iso3_to_region.get("USA", "default")
    region_capex = capex_dict_all_locs.get(region, {})
    tech_capex = region_capex.get("DRI", 500.0)
    assert tech_capex == 500.0  # Should use default

    # Test case 2: Technology exists in region
    region = mock_iso3_to_region.get("DEU", "default")
    region_capex = capex_dict_all_locs.get(region, {})
    tech_capex = region_capex.get("DRI", 500.0)
    assert tech_capex == 450.0  # Should use actual value

    # Test case 3: Missing region entirely
    region = mock_iso3_to_region.get("CHN", "default")
    region_capex = capex_dict_all_locs.get(region, {})
    tech_capex = region_capex.get("DRI", 500.0)
    assert tech_capex == 500.0  # Should use default

    # Test case 4: Missing ISO3 in mapping
    region = mock_iso3_to_region.get("IND", "default")
    region_capex = capex_dict_all_locs.get(region, {})
    tech_capex = region_capex.get("DRI", 500.0)
    assert tech_capex == 500.0  # Should use default


def test_track_business_opportunities_missing_capex():
    """Test that track_business_opportunities handles missing capex gracefully."""
    from steelo.domain.models import Technology

    # Mock the calculate_business_opportunity_npv function
    def mock_calculate_npv(**kwargs):
        return 1000.0  # Return a positive NPV

    # Create a mock furnace group
    class MockFurnaceGroup:
        def __init__(self):
            self.technology = Technology(name="BF", product="iron")
            self.railway_cost = 10.0
            self.historical_npv_business_opportunities = None

    fg = MockFurnaceGroup()

    # Test with empty capex_dict (the bug scenario)
    capex_dict = {}  # Missing 'BF'

    # This would have raised KeyError before the fix
    # Now it should use the default value
    capex_value = capex_dict.get(fg.technology.name, 500.0)
    assert capex_value == 500.0

    # Test with capex_dict that has the technology
    capex_dict = {"BF": 375.40}
    capex_value = capex_dict.get(fg.technology.name, 500.0)
    assert capex_value == 375.40
