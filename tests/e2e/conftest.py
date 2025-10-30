import json
import pickle
import pytest


@pytest.fixture(autouse=True)
def mock_settings_paths(monkeypatch, tmp_path):
    """Mock all the settings paths to use temporary files."""

    # Create temporary directories
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir(exist_ok=True)

    # Create a mock cost_of_x.json
    cost_of_x_data = {
        "Country code": {"0": "USA", "1": "CHN", "2": "DEU", "3": "JPN", "4": "BEL"},
        "Cost of equity - industrial assets": {"0": 0.25, "1": 0.30, "2": 0.20, "3": 0.22, "4": 0.21},
    }
    cost_of_x_path = fixtures_dir / "cost_of_x.json"
    with open(cost_of_x_path, "w") as f:
        json.dump(cost_of_x_data, f)

    # Create a mock tech_switches_allowed.csv
    tech_switches_csv = fixtures_dir / "tech_switches_allowed.csv"
    with open(tech_switches_csv, "w") as f:
        f.write("Origin,BF,BOF,DRI,EAF\n")
        f.write("BF,NO,NO,NO,NO\n")
        f.write("BOF,NO,NO,YES,YES\n")
        f.write("DRI,NO,NO,NO,NO\n")
        f.write("EAF,NO,NO,NO,NO\n")

    # Create mock gravity distances pickle file
    gravity_pkl = fixtures_dir / "gravity_distances_dict.pkl"
    gravity_data = {
        ("USA", "CHN"): 11000,
        ("USA", "DEU"): 6500,
        ("CHN", "DEU"): 7500,
        ("CHN", "JPN"): 2100,
        ("DEU", "BEL"): 300,
    }
    with open(gravity_pkl, "wb") as f:
        pickle.dump(gravity_data, f)

    # Create mock geolocator_raster.csv with the format expected by reverse_geocoder
    geolocator_csv = fixtures_dir / "geolocator_raster.csv"
    with open(geolocator_csv, "w") as f:
        f.write("lat,lon,name,admin1,admin2,cc\n")
        f.write("36.152418,114.15839,Anyang,Henan,,CHN\n")
        f.write("36.122129,114.283145,Anyang,Henan,,CHN\n")
        f.write("50.414998,4.532443,Chatelet,Wallonie,,BEL\n")

    # Create technology_lcop.csv
    tech_lcop_csv = fixtures_dir / "technology_lcop.csv"
    with open(tech_lcop_csv, "w") as f:
        f.write("Technology,Unit cost\n")
        f.write("BF,50\n")
        f.write("BOF,30\n")
        f.write("EAF,40\n")
        f.write("DRI,60\n")

    # No longer need to mock settings since it was removed
    # The CLI functions now require explicit paths

    # Create JSON repository files if needed
    plants_json = fixtures_dir / "plants.json"
    with open(plants_json, "w") as f:
        json.dump([], f)  # Empty list of plants

    demand_centers_json = fixtures_dir / "demand_centers.json"
    with open(demand_centers_json, "w") as f:
        json.dump([], f)

    suppliers_json = fixtures_dir / "suppliers.json"
    with open(suppliers_json, "w") as f:
        json.dump([], f)

    plant_groups_json = fixtures_dir / "plant_groups.json"
    with open(plant_groups_json, "w") as f:
        json.dump([], f)

    tariffs_json = fixtures_dir / "tariffs.json"
    with open(tariffs_json, "w") as f:
        json.dump([], f)

    input_costs_json = fixtures_dir / "input_costs.json"
    with open(input_costs_json, "w") as f:
        json.dump([], f)

    primary_feedstocks_json = fixtures_dir / "primary_feedstocks.json"
    with open(primary_feedstocks_json, "w") as f:
        json.dump([], f)

    carbon_costs_json = fixtures_dir / "carbon_costs.json"
    with open(carbon_costs_json, "w") as f:
        json.dump([], f)

    grid_emissivity_json = fixtures_dir / "grid_emissivity.json"
    with open(grid_emissivity_json, "w") as f:
        json.dump([], f)

    return fixtures_dir
