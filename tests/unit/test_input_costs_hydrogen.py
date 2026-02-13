import json
from pathlib import Path

from steelo.domain.constants import Year, T_TO_KG
from steelo.domain.models import (
    InputCosts,
    PrimaryFeedstock,
    TechnologyEmissionFactors,
)
from steelo.domain.calculate_emissions import calculate_emissions


def test_recreate_input_costs_derives_hydrogen_price(monkeypatch, tmp_path):
    """Ensure hydrogen is written into input-cost fixtures using LCOH-derived USD/t values."""
    from steelo.data import recreation_functions

    sample_input_costs = [
        InputCosts(year=Year(2025), iso3="USA", costs={"electricity": 0.05}),
        InputCosts(year=Year(2025), iso3="DEU", costs={"electricity": 0.07}),
    ]

    monkeypatch.setattr(
        recreation_functions,
        "read_regional_input_prices_from_master_excel",
        lambda excel_path, input_costs_sheet="Input costs": sample_input_costs,
    )

    class DummyHydrogenEfficiency:
        def __init__(self, year, efficiency):
            self.year = year
            self.efficiency = efficiency

    class DummyHydrogenCapex:
        def __init__(self, country_code, values):
            self.country_code = country_code
            self.values = values

    monkeypatch.setattr(
        recreation_functions,
        "read_hydrogen_efficiency",
        lambda excel_path: [DummyHydrogenEfficiency(Year(2025), 0.05)],
    )
    monkeypatch.setattr(
        recreation_functions,
        "read_hydrogen_capex_opex",
        lambda excel_path: [
            DummyHydrogenCapex("USA", {Year(2025): 0.9}),
            DummyHydrogenCapex("DEU", {Year(2025): 1.1}),
        ],
    )

    lcoh_stub = {"USA": 1.5, "DEU": 2.0}  # USD/kg

    def fake_lcoh(electricity_by_country, hydrogen_efficiency, hydrogen_capex_opex, year):
        assert year == Year(2025)
        return {iso3: lcoh_stub[iso3] for iso3 in electricity_by_country}

    monkeypatch.setattr(
        recreation_functions,
        "calculate_lcoh_from_electricity_country_level",
        fake_lcoh,
    )

    output_path = tmp_path / "input_costs.json"
    repo = recreation_functions.recreate_input_costs_data(
        input_costs_json_path=output_path,
        excel_path=Path("dummy.xlsx"),
    )

    written = repo.get("USA", 2025)
    assert written.costs["hydrogen"] == lcoh_stub["USA"] * T_TO_KG

    # Verify we actually persisted to disk in the expected schema.
    raw = json.loads(output_path.read_text())
    entries = raw["root"] if isinstance(raw, dict) and "root" in raw else raw
    usa_entry = next(entry for entry in entries if entry["iso3"] == "USA" and entry["year"] == 2025)
    assert usa_entry["costs"]["hydrogen"] == lcoh_stub["USA"] * T_TO_KG


def test_calculate_emissions_handles_spaced_reductant():
    """Emission lookup matches normalised reductant against emission factors."""
    feedstock = PrimaryFeedstock(metallic_charge="io_high", reductant="natural_gas", technology="DRI")
    feedstock.required_quantity_per_ton_of_product = 1.0

    material_bill = {"io_high": {"demand": 100.0}}

    factors = [
        TechnologyEmissionFactors(
            business_case="iron_dri",
            technology="DRI",
            boundary="responsible_steel",
            metallic_charge="io_high",
            reductant="natural_gas",
            direct_ghg_factor=2.5,
            direct_with_biomass_ghg_factor=2.5,
            indirect_ghg_factor=1.1,
        )
    ]

    emissions = calculate_emissions(
        material_bill=material_bill,
        business_cases={"io_high": feedstock},
        technology_emission_factors=factors,
        installed_carbon_capture=0.0,
        grid_emissions=0.0,
    )

    assert emissions["responsible_steel"]["direct_ghg"] == 2.5 * 100.0
