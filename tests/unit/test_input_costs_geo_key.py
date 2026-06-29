"""Tests for re-keying `InputCosts` off iso3 onto the sub-national `geo_key`.

Covers the `geo_key` composition on `InputCosts` (and that a country row and a province row
for the same iso3+year stay distinct), the excel_reader split of the geo-key column, the
`InputCostsInDb` persistence (round-trip, legacy load, country+province coexistence), and the
province-aware hydrogen LCOH path (province electricity + country capex, province inheriting
its country's regional ceiling — including the intraregional-trade case).
"""

import pandas as pd
import pytest

from steelo.adapters.dataprocessing.excel_reader import read_regional_input_prices_from_master_excel
from steelo.adapters.repositories.json_repository import InputCostsInDb, InputCostsJsonRepository
from steelo.domain.calculate_costs import (
    apply_hydrogen_price_cap_country_level,
    calculate_lcoh_from_electricity_country_level,
)
from steelo.domain.constants import Year
from steelo.domain.models import InputCosts


def test_geo_key_composes_and_defaults():
    """InputCosts.geo_key mirrors Location.geo_key: composite when set, bare iso3 otherwise."""
    assert InputCosts(Year(2025), "CHN", {}, geo_unit="CN-HE").geo_key == "CHN:CN-HE"
    assert InputCosts(Year(2025), "CHN", {}).geo_key == "CHN"


def test_country_and_province_rows_are_distinct():
    """name/__eq__ key off geo_key, so a country row and a province row for the same iso3+year
    are not equal (and would not collide in a name-keyed structure)."""
    country = InputCosts(Year(2025), "CHN", {"electricity": 80.0})
    province = InputCosts(Year(2025), "CHN", {"electricity": 65.0}, geo_unit="CN-HE")

    assert country.name == "CHN_2025"
    assert province.name == "CHN:CN-HE_2025"
    assert country != province


def test_excel_reader_splits_geo_key_column(tmp_path):
    """The "ISO-3 code" column holding CHN or CHN:CN-HE splits into iso3 + geo_unit."""
    excel_path = tmp_path / "input_costs.xlsx"
    pd.DataFrame(
        {
            "ISO-3 code": ["CHN", "CHN:CN-HE"],
            "Commodity": ["Electricity", "Electricity"],
            "Unit": ["USD/MWh", "USD/MWh"],
            "2025": [50.0, 30.0],
        }
    ).to_excel(excel_path, index=False)

    result = read_regional_input_prices_from_master_excel(excel_path=excel_path, input_costs_sheet="Sheet1")

    country = next(r for r in result if r.geo_unit is None)
    province = next(r for r in result if r.geo_unit == "CN-HE")
    assert country.iso3 == "CHN" and country.geo_key == "CHN"
    assert province.iso3 == "CHN" and province.geo_key == "CHN:CN-HE"


def test_input_costs_indb_round_trips_geo_unit():
    """geo_unit survives the InputCosts -> InputCostsInDb -> InputCosts round-trip."""
    ic = InputCosts(Year(2025), "CHN", {"electricity": 65.0}, geo_unit="CN-HE")

    in_db = InputCostsInDb.from_domain(ic)
    assert in_db.geo_unit == "CN-HE"
    assert in_db.geo_key == "CHN:CN-HE"
    assert in_db.to_domain.geo_unit == "CN-HE"


def test_legacy_input_costs_without_geo_unit_loads_as_none():
    """Saved input costs predating geo_unit load with geo_unit None and a bare-iso3 geo_key."""
    in_db = InputCostsInDb(iso3="DEU", year=Year(2025), costs={"electricity": 90.0})

    assert in_db.geo_unit is None
    assert in_db.geo_key == "DEU"
    assert in_db.to_domain.geo_unit is None


def test_repository_keys_by_geo_key_so_province_and_country_coexist(tmp_path):
    """The repo keys entries by geo_key_year, so a province row does not overwrite its country row."""
    repo = InputCostsJsonRepository(tmp_path / "input_costs.json")
    repo.add_list(
        [
            InputCosts(Year(2025), "CHN", {"electricity": 80.0}),
            InputCosts(Year(2025), "CHN", {"electricity": 65.0}, geo_unit="CN-HE"),
        ]
    )

    stored = repo.list()
    assert len(stored) == 2
    assert repo.get("CHN", 2025).costs["electricity"] == 80.0


def test_lcoh_keyed_by_geo_key_uses_country_capex():
    """A province LCOH uses province electricity but its country's H2 capex/opex, keyed by geo_key."""
    electricity = {"CHN": 0.05, "CHN:CN-HE": 0.03}  # USD/kWh
    efficiency = {Year(2025): 0.05}  # MWh/kg -> 50 kWh/kg
    capex = {"CHN": {Year(2025): 1.0}}  # USD/kg, country-keyed only

    lcoh = calculate_lcoh_from_electricity_country_level(
        electricity_by_country=electricity,
        hydrogen_efficiency=efficiency,
        hydrogen_capex_opex=capex,
        year=Year(2025),
    )

    assert lcoh["CHN"] == pytest.approx(50 * 0.05 + 1.0)
    assert lcoh["CHN:CN-HE"] == pytest.approx(50 * 0.03 + 1.0)  # province electricity + country capex


def test_province_inherits_country_ceiling_without_trade():
    """A province key resolves its region via iso3 and is capped by its country's ceiling."""
    capped = apply_hydrogen_price_cap_country_level(
        lcoh_by_country={"CHN": 3.5, "CHN:CN-HE": 2.5},
        regional_ceilings={"China": 3.0},
        country_to_region={"CHN": "China"},
        intraregional_trade_allowed=False,
        intraregional_trade_matrix={},
        long_dist_pipeline_transport_cost=0.0,
    )

    assert capped["CHN"] == pytest.approx(3.0)  # capped at ceiling
    assert capped["CHN:CN-HE"] == pytest.approx(2.5)  # below ceiling, kept


def test_province_inherits_country_intraregional_trade_ceiling():
    """With intraregional trade, a province inherits its country's trade-adjusted ceiling."""
    capped = apply_hydrogen_price_cap_country_level(
        lcoh_by_country={"CHN": 3.5, "CHN:CN-HE": 2.5},
        regional_ceilings={"China": 3.0, "RoW": 1.0},
        country_to_region={"CHN": "China"},
        intraregional_trade_allowed=True,
        intraregional_trade_matrix={"China": ["RoW"]},
        long_dist_pipeline_transport_cost=0.5,
    )

    # China's effective ceiling = min(3.0, RoW 1.0 + 0.5 transport) = 1.5; the province inherits it.
    assert capped["CHN"] == pytest.approx(1.5)
    assert capped["CHN:CN-HE"] == pytest.approx(1.5)
