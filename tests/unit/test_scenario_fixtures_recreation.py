"""The preparation writes the demand centres of every demand scenario and the suppliers of every scrap scenario."""

import json
import pickle

import pandas as pd

from steelo.adapters.repositories.json_repository import DemandCenterJsonRepository, SupplierJsonRepository
from steelo.data import recreation_functions
from steelo.domain.models import Year

SHEET = "Demand and scrap availability"
MINES_SHEET = "Iron ore mines"


def test_recreate_scenario_fixtures_writes_one_file_per_scenario_and_the_index(tmp_path):
    """Two demand and two scrap scenarios give four files, each holding its own scenario, and an index naming them."""
    rows = []
    for iso3, country in [("AUT", "Austria"), ("PRT", "Portugal")]:
        for scenario, demand in [("BAU", 100), ("High growth", 200)]:
            rows.append([country, iso3, "Crude steel consumption for forming [kt]", scenario, "kt", demand])
        for scenario, scrap in [("BAU", 10), ("High recycling", 30)]:
            rows.append([country, iso3, "Total available scrap", scenario, "kt", scrap])
    master = tmp_path / "master.xlsx"
    with pd.ExcelWriter(master) as writer:
        pd.DataFrame(rows, columns=["Country", "ISO-3 code", "Metric", "Scenario", "Unit", 2025]).to_excel(
            writer,
            sheet_name=SHEET,
            index=False,
        )
        pd.DataFrame(
            {
                "Region": ["Australia"],
                "Products": ["IO_low"],
                "capacity Mtpa 2025": [5],
                "costs $/t 2025": [50],
                "lat": [-25.0],
                "lon": [133.0],
            },
        ).to_excel(writer, sheet_name=MINES_SHEET, index=False)
    location_csv = tmp_path / "countries.csv"
    pd.DataFrame(
        {"COUNTRY": ["Austria", "Portugal"], "ISO": ["AT", "PT"], "latitude": [47.5, 39.5], "longitude": [14.5, -8.0]},
    ).to_csv(location_csv, index=False)
    gravity_path = tmp_path / "gravity_distances_dict.pkl"
    gravity_path.write_bytes(pickle.dumps({}))
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()

    index_path = recreation_functions.recreate_scenario_fixtures(
        json_path=fixtures / "scenarios.json",
        master_excel_path=master,
        sheet_name=SHEET,
        mines_sheet_name=MINES_SHEET,
        gravity_distances_path=gravity_path,
        location_csv=location_csv,
    )

    index = json.loads(index_path.read_text())
    assert index == {
        "demand": {
            "BAU": "demand_scenarios/bau/demand_centers.json",
            "High growth": "demand_scenarios/high-growth/demand_centers.json",
        },
        "scrap": {
            "BAU": "scrap_scenarios/bau/suppliers.json",
            "High recycling": "scrap_scenarios/high-recycling/suppliers.json",
        },
    }
    demand_2025 = {
        name: sum(dc.demand_by_year[Year(2025)] for dc in DemandCenterJsonRepository(fixtures / path).list())
        for name, path in index["demand"].items()
    }
    assert demand_2025 == {"BAU": 200_000, "High growth": 400_000}
    for name, scrap_kt in [("BAU", 20), ("High recycling", 60)]:
        suppliers = SupplierJsonRepository(fixtures / index["scrap"][name]).list()
        scrap = [s for s in suppliers if s.commodity == "scrap"]
        assert sum(s.capacity_by_year[Year(2025)] for s in scrap) == scrap_kt * 1000
        assert [s.location.iso3 for s in suppliers if s.commodity != "scrap"] == ["AUS"]
    assert sorted(path.name for path in fixtures.iterdir()) == ["demand_scenarios", "scenarios.json", "scrap_scenarios"]
