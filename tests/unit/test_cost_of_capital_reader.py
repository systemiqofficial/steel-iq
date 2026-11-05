"""Tests for the long-format (per-technology) cost of capital reader and fixture round-trip."""

import pandas as pd
import pytest

from steelo.adapters.dataprocessing import excel_reader
from steelo.adapters.repositories.json_repository import CostOfCapitalInDb, CostOfCapitalJsonRepository
from steelo.domain.models import CostOfCapital, TechFinancingRates, RENEWABLES_KEY, HYDROGEN_KEY

MODEL_TECHS = ["BF", "EAF"]
ALL_TECHS = MODEL_TECHS + [RENEWABLES_KEY, HYDROGEN_KEY]


def rates_row(country, iso3, tech, debt, equity):
    """Build one long-format sheet row with a WACC consistent with 20% equity share."""
    return {
        "Country": country,
        "ISO-3 Code": iso3,
        "Tech": tech,
        "Cost of debt": debt,
        "Cost of equity": equity,
        "Cost of capital": 0.2 * equity + 0.8 * debt,
    }


def write_workbook(path, coc_rows):
    """Write a synthetic master workbook with the cost of capital and techno-economic sheets."""
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame(coc_rows).to_excel(writer, sheet_name="Cost of capital", index=False)
        pd.DataFrame({"Technology": MODEL_TECHS}).to_excel(writer, sheet_name="Techno-economic details", index=False)


def full_country_rows(country, iso3, debt=0.04, equity=0.08):
    """Rows covering all required techs for one country."""
    return [rates_row(country, iso3, tech, debt, equity) for tech in ALL_TECHS]


def test_read_cost_of_capital_parses_long_format(tmp_path):
    """One CostOfCapital per country, with per-tech rates keyed verbatim."""
    path = tmp_path / "master.xlsx"
    write_workbook(path, full_country_rows("Germany", "DEU") + full_country_rows("France", "FRA", debt=0.05))

    result = excel_reader.read_cost_of_capital(str(path))

    assert [c.iso3 for c in result] == ["DEU", "FRA"]
    deu = result[0]
    assert deu.country == "Germany"
    assert set(deu.techs) == set(ALL_TECHS)
    assert deu.techs["BF"].cost_of_debt == pytest.approx(0.04)
    assert deu.techs["BF"].cost_of_equity == pytest.approx(0.08)
    assert deu.techs["BF"].cost_of_capital == pytest.approx(0.2 * 0.08 + 0.8 * 0.04)
    assert result[1].techs["EAF"].cost_of_debt == pytest.approx(0.05)


def test_read_cost_of_capital_collapses_identical_duplicates(tmp_path):
    """Duplicate ISO3 rows with identical values (e.g. shared codes like ESP) collapse to one entry."""
    path = tmp_path / "master.xlsx"
    rows = full_country_rows("Spain", "ESP") + full_country_rows("Canarias", "ESP")
    write_workbook(path, rows)

    result = excel_reader.read_cost_of_capital(str(path))

    assert len(result) == 1
    assert result[0].iso3 == "ESP"


def test_read_cost_of_capital_raises_on_conflicting_duplicates(tmp_path):
    """Duplicate (ISO3, Tech) rows with different values must fail loudly."""
    path = tmp_path / "master.xlsx"
    rows = full_country_rows("Spain", "ESP") + [rates_row("Canarias", "ESP", "BF", 0.09, 0.15)]
    write_workbook(path, rows)

    with pytest.raises(ValueError, match="Conflicting duplicate"):
        excel_reader.read_cost_of_capital(str(path))


def test_read_cost_of_capital_raises_on_missing_tech(tmp_path):
    """A country missing a model technology (or Renewables/Hydrogen) must fail loudly."""
    path = tmp_path / "master.xlsx"
    rows = [r for r in full_country_rows("Germany", "DEU") if r["Tech"] != "EAF"]
    write_workbook(path, rows)

    with pytest.raises(ValueError, match="missing technologies for DEU.*EAF"):
        excel_reader.read_cost_of_capital(str(path))


def test_cost_of_capital_repository_round_trip(tmp_path):
    """Nested per-tech rates survive a write/read cycle through the JSON repository."""
    path = tmp_path / "cost_of_capital.json"
    repo = CostOfCapitalJsonRepository(path)
    rates = TechFinancingRates(cost_of_debt=0.04, cost_of_equity=0.08, cost_of_capital=0.048)
    repo.add_list(
        [
            CostOfCapital(country="Germany", iso3="DEU", techs={"BF": rates, RENEWABLES_KEY: rates}),
        ]
    )

    fresh_repo = CostOfCapitalJsonRepository(path)
    fresh_repo._all = None  # Drop the class-level cache so the file is actually re-read
    loaded = fresh_repo.get("DEU")

    assert loaded.techs["BF"] == rates
    assert loaded.techs[RENEWABLES_KEY].cost_of_equity == pytest.approx(0.08)


def test_cost_of_capital_in_db_from_domain_round_trip():
    """CostOfCapitalInDb mirrors the domain object in both directions."""
    rates = TechFinancingRates(cost_of_debt=0.05, cost_of_equity=0.1, cost_of_capital=0.06)
    domain = CostOfCapital(country="France", iso3="FRA", techs={"EAF": rates})

    db_model = CostOfCapitalInDb.from_domain(domain)
    restored = db_model.to_domain

    assert restored.iso3 == "FRA"
    assert restored.techs == {"EAF": rates}
