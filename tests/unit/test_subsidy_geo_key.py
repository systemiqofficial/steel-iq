"""Tests for the subsidy geo_key re-key: province-scoped subsidy rows end-to-end.

The Subsidies sheet's Location column carries a trade bloc, a bare iso3, or a combined geo_key
("CHN:CN-HE"); the pipeline keys subsidies by geo_key through the Environment and the JSON mirror.
Lookups use `collect_subsidies_for_geo`, whose semantics are additive, not finest-available: a
country-wide row applies to every plant in the country (province-tagged included), while a
province row applies only to plants in that province and never replaces a country-wide row.
"""

import tempfile
from pathlib import Path

import pandas as pd
import pytest

from steelo.adapters.dataprocessing.excel_reader import read_subsidies
from steelo.adapters.dataprocessing.master_excel_validator import MasterExcelValidator
from steelo.adapters.repositories.json_repository import SubsidyInDb
from steelo.domain.calculate_costs import collect_subsidies_for_geo
from steelo.domain.constants import Year
from steelo.domain.models import Environment, Subsidy
from steelo.domain.new_plant_opening import NewPlantLocation, prepare_cost_data_for_business_opportunity


def make_subsidy(iso3="CHN", geo_unit=None, cost_item="capex", amount=0.2, tech="EAF"):
    """A relative Subsidy row with the given geography."""
    return Subsidy(
        scenario_name="test",
        iso3=iso3,
        geo_unit=geo_unit,
        start_year=Year(2025),
        end_year=Year(2035),
        technology_name=tech,
        cost_item=cost_item,
        subsidy_type="relative",
        subsidy_amount=amount,
    )


# ---------------------------------------------------------------- reader


def _write_subsidies_excel(path: Path, locations: list[str]):
    """A minimal master Excel with Subsidies, Country mapping, and Techno-economic sheets."""
    subsidies = pd.DataFrame(
        {
            "Scenario name": ["s"] * len(locations),
            "Location": locations,
            "Technology": ["EAF"] * len(locations),
            "Cost item": ["CAPEX"] * len(locations),
            "Subsidy type": ["relative"] * len(locations),
            "Subsidy amount": [10.0] * len(locations),
            "Start year": [2025] * len(locations),
            "End year": [2035] * len(locations),
        },
    )
    country_mapping = pd.DataFrame(
        {
            "ISO 3-letter code": ["CHN", "DEU", "CHE"],
            "TestBloc": [False, True, True],
        },
    )
    techno = pd.DataFrame({"Technology": ["EAF", "BF"]})
    with pd.ExcelWriter(path) as writer:
        subsidies.to_excel(writer, sheet_name="Subsidies", index=False)
        country_mapping.to_excel(writer, sheet_name="Country mapping", index=False)
        techno.to_excel(writer, sheet_name="Techno-economic details", index=False)


def test_read_subsidies_splits_geo_key_location():
    """The Location column accepts trade bloc, bare iso3, and combined geo_key rows."""
    with tempfile.NamedTemporaryFile(suffix=".xlsx") as tf:
        _write_subsidies_excel(Path(tf.name), ["TestBloc", "CHN", "CHN:CN-HE"])

        subsidies = read_subsidies(Path(tf.name))

        by_geo_key = {s.geo_key for s in subsidies}
        # Trade bloc expands to its member countries at country level
        assert {"DEU", "CHE"} <= by_geo_key
        # Bare iso3 stays country-level; combined splits into iso3 + geo_unit
        country_row = next(s for s in subsidies if s.geo_key == "CHN")
        province_row = next(s for s in subsidies if s.geo_key == "CHN:CN-HE")
        assert country_row.geo_unit is None
        assert province_row.iso3 == "CHN"
        assert province_row.geo_unit == "CN-HE"
        # The geo-keyed subsidy_name keeps a country row and a province row distinct
        assert country_row.subsidy_name != province_row.subsidy_name


# ---------------------------------------------------------------- Environment init


def test_environment_keys_subsidies_by_geo_key(tmp_path):
    """The four Environment inits key on geo_key, so province rows stay separate."""
    from steelo.simulation import SimulationConfig

    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2030),
        master_excel_path=Path("test.xlsx"),
        output_dir=tmp_path,
    )
    tech_switches_csv = tmp_path / "tech_switches_allowed.csv"
    tech_switches_csv.write_text("origin,BF\nBF,YES\n", encoding="utf-8")
    env = Environment(config=config, tech_switches_csv=tech_switches_csv)
    rows = [
        make_subsidy(cost_item="capex"),
        make_subsidy(geo_unit="CN-HE", cost_item="capex"),
        make_subsidy(cost_item="opex"),
        make_subsidy(geo_unit="CN-HE", cost_item="cost of debt"),
        make_subsidy(geo_unit="CN-HE", cost_item="hydrogen"),
    ]
    env.initiate_capex_subsidies(rows)
    env.initiate_opex_subsidies(rows)
    env.initiate_debt_subsidies(rows)
    env.initiate_energy_subsidies(rows)

    assert set(env.capex_subsidies) == {"CHN", "CHN:CN-HE"}
    assert set(env.opex_subsidies) == {"CHN"}
    assert set(env.debt_subsidies) == {"CHN:CN-HE"}
    assert set(env.energy_subsidies["hydrogen"]) == {"CHN:CN-HE"}


# ---------------------------------------------------------------- collect semantics


def test_country_row_applies_to_province_tagged_plant():
    """A country-wide subsidy applies to every plant in the country, province-tagged included."""
    country_sub = make_subsidy()
    lookup = {"CHN": {"EAF": [country_sub]}}

    assert collect_subsidies_for_geo(lookup, "CHN:CN-HE")["EAF"] == [country_sub]


def test_province_row_applies_only_inside_its_province():
    """A province row reaches plants in that province and no one else — no country spreading."""
    province_sub = make_subsidy(geo_unit="CN-HE")
    lookup = {"CHN:CN-HE": {"EAF": [province_sub]}}

    assert collect_subsidies_for_geo(lookup, "CHN:CN-HE")["EAF"] == [province_sub]
    # A plant in another province and a country-level plant get nothing
    assert collect_subsidies_for_geo(lookup, "CHN:CN-AH") == {}
    assert collect_subsidies_for_geo(lookup, "CHN") == {}


def test_country_and_province_rows_are_additive():
    """A province row never replaces a country-wide row — the plant receives both."""
    country_sub = make_subsidy()
    province_sub = make_subsidy(geo_unit="CN-HE", amount=0.1)
    lookup = {"CHN": {"EAF": [country_sub]}, "CHN:CN-HE": {"EAF": [province_sub]}}

    merged = collect_subsidies_for_geo(lookup, "CHN:CN-HE")

    assert merged["EAF"] == [country_sub, province_sub]
    # The source lists are untouched by the merge
    assert lookup["CHN"]["EAF"] == [country_sub]
    assert lookup["CHN:CN-HE"]["EAF"] == [province_sub]


def test_province_subsidy_application_is_logged(caplog):
    """Applying sub-national subsidy rows is logged at INFO (province differentiation is active)."""
    import logging

    lookup = {"CHN": {"EAF": [make_subsidy()]}, "CHN:CN-HE": {"EAF": [make_subsidy(geo_unit="CN-HE")]}}

    with caplog.at_level(logging.INFO):
        collect_subsidies_for_geo(lookup, "CHN:CN-HE")

    assert "sub-national rows for CHN:CN-HE" in caplog.text


# ---------------------------------------------------------------- JSON mirror


def test_subsidy_json_mirror_round_trips_geo_unit():
    """SubsidyInDb carries geo_unit through from_domain/to_domain; old JSON defaults to None."""
    province_sub = make_subsidy(geo_unit="CN-HE")

    restored = SubsidyInDb.from_domain(province_sub).to_domain
    assert restored == province_sub
    assert restored.geo_key == "CHN:CN-HE"

    # A pre-geo_unit JSON record (no geo_unit field) loads as country-level
    legacy = SubsidyInDb(
        scenario_name="s",
        iso3="CHN",
        start_year=Year(2025),
        end_year=Year(2035),
        technology_name="EAF",
        cost_item="capex",
        subsidy_type="relative",
        subsidy_amount=0.2,
        subsidy_name="CHN_s_EAF_capex_relative",
    )
    assert legacy.to_domain.geo_unit is None


# ---------------------------------------------------------------- greenfield siting (E.3)


def _mock_get_bom(_energy_costs, tech, _capacity, _most_common_reductant=None):
    if tech == "EAF":
        return (
            {"energy": {"electricity": {"unit_cost": 50.0, "demand": 0.5}}},
            0.7,
            "scrap",
        )
    return None, 0.0, None


def test_candidate_site_collects_country_and_province_capex_subsidies():
    """A candidate site inside a subsidised province stacks the province row on the country
    row; a site elsewhere in the country only gets the country row."""
    capex_subsidies = {
        "CHN": {"EAF": [make_subsidy(amount=0.2)]},
        "CHN:CN-HE": {"EAF": [make_subsidy(geo_unit="CN-HE", amount=0.1)]},
    }
    sites = [
        NewPlantLocation(Latitude=39.0, Longitude=114.5, iso3="CHN", power_price=0.05, capped_lcoh=3.0, rail_cost=10.0),
        NewPlantLocation(Latitude=31.6, Longitude=118.5, iso3="CHN", power_price=0.05, capped_lcoh=3.0, rail_cost=10.0),
    ]

    def derive_geo_unit(lat, lon, iso3):
        return "CN-HE" if lat == 39.0 else "CN-AH"

    cost_data = prepare_cost_data_for_business_opportunity(
        product_to_tech={"steel": ["EAF"]},
        best_locations_subset={"steel": sites},
        current_year=Year(2025),
        target_year=Year(2030),
        energy_costs={"CHN": {Year(2025): {"electricity": 0.05, "hydrogen": 3500.0}}},
        capex_dict_all_locs_techs={"Asia": {"EAF": 1000.0}},
        cost_of_debt_all_locs={"CHN": 0.05},
        cost_of_equity_all_locs={"CHN": 0.08},
        fopex_all_locs_techs={"CHN": {"eaf": 50.0}},
        steel_plant_capacity=100.0,
        get_bom_from_avg_boms=_mock_get_bom,
        iso3_to_region_map={"CHN": "Asia"},
        global_risk_free_rate=0.03,
        capex_subsidies=capex_subsidies,
        debt_subsidies={},
        opex_subsidies={},
        energy_subsidies={},
        carbon_costs={"CHN": {Year(2030): 50.0}},
        most_common_reductant={},
        environment_most_common_reductant={},
        derive_geo_unit=derive_geo_unit,
    )

    in_province = cost_data["steel"][(39.0, 114.5, "CHN")]["EAF"]
    elsewhere = cost_data["steel"][(31.6, 118.5, "CHN")]["EAF"]
    # Country 20% and province 10% stack additively inside CN-HE
    assert in_province["capex"] == pytest.approx(1000.0 * (1 - 0.2 - 0.1))
    # A CN-AH site gets only the country-wide 20%
    assert elsewhere["capex"] == pytest.approx(1000.0 * (1 - 0.2))


# ---------------------------------------------------------------- validator (E.4)


def _run_subsidies_validator(locations: list[str]):
    """Validate a temp Subsidies sheet with injected reference sets; return INVALID_GEO_KEY issues."""
    validator = MasterExcelValidator(
        valid_countries={"CHN", "DEU", "CHE"},
        modelled_countries={"CHN", "DEU", "CHE"},
        valid_geo_keys={"CHN:CN-HE"},
    )
    with tempfile.NamedTemporaryFile(suffix=".xlsx") as tf:
        _write_subsidies_excel(Path(tf.name), locations)
        xl_file = pd.ExcelFile(tf.name)
        validator._validate_subsidies_location(pd.read_excel(xl_file, sheet_name="Subsidies"), xl_file)
    return [issue for issue in validator.report.all_issues() if issue.error_type == "INVALID_GEO_KEY"]


def test_subsidies_validator_passes_valid_locations():
    """A trade-bloc name, a bare country, and a declared geo-key all pass."""
    assert _run_subsidies_validator(["TestBloc", "CHN", "CHN:CN-HE"]) == []


def test_subsidies_validator_flags_bogus_geo_key():
    """An undeclared sub-national unit is INVALID_GEO_KEY; the bloc name is not flagged."""
    errors = _run_subsidies_validator(["TestBloc", "CHN:CN-XX"])

    assert len(errors) == 1
    assert "CHN:CN-XX" in errors[0].message
