"""Tests for the run-wide carbon border charges CSV appended to beside each year's trade allocation pickle."""

import csv

from steelo.domain.trade_modelling import trade_lp_modelling as tlp
from steelo.utilities import file_output


class DummyLocation:
    def __init__(self, iso3: str):
        self.iso3 = iso3


def _make_process_center(
    name: str,
    iso3: str,
    process_type: tlp.ProcessType,
    process_name: str,
    technology: str | None = None,
    **carbon,
) -> tlp.ProcessCenter:
    process = tlp.Process(name=process_name, type=process_type, bill_of_materials=[], technology=technology)
    return tlp.ProcessCenter(name=name, process=process, capacity=1000.0, location=DummyLocation(iso3), **carbon)


def _read_rows(path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _export(tmp_path, allocations: tlp.Allocations, year: int = 2030) -> list[dict[str, str]]:
    path = tmp_path / "TM" / "carbon_border_charges.csv"
    file_output.export_carbon_border_charges_to_csv(allocations=allocations, year=year, filename=str(path))
    return _read_rows(path)


def test_charge_row_carries_emissions_carbon_paid_and_money(tmp_path):
    """A charged arc yields one row whose implied price reproduces adjustment = E * P_d - paid."""
    furnace = _make_process_center(
        "P1_0",
        "CHN",
        tlp.ProcessType.PRODUCTION,
        "BF_coke",
        technology="BF",
        production_cost=10.0,
        emission_intensity=1.5,
        upstream_emission_intensity=0.5,
        upstream_carbon_cost_paid=5.0,
    )
    demand = _make_process_center("Germany", "DEU", tlp.ProcessType.DEMAND, "demand")
    steel = tlp.Commodity(name="steel")
    # E = 2.0 tCO2/t, paid = 15 USD/t, destination price 100 USD/tCO2 -> adjustment 185 USD/t
    allocations = tlp.Allocations(
        allocations={(furnace, demand, steel): 2_000_000.0},
        carbon_border_charges={(furnace, demand, steel): 185.0},
    )

    rows = _export(tmp_path, allocations)

    assert rows == [
        {
            "year": "2030",
            "kind": "charge",
            "from_name": "P1_0",
            "from_iso3": "CHN",
            "from_technology": "BF",
            "to_name": "Germany",
            "to_iso3": "DEU",
            "to_type": "DEMAND",
            "commodity": "steel",
            "volume_t": "2000000.0",
            "own_E_tco2_t": "1.5",
            "upstream_E_tco2_t": "0.5",
            "E_tco2_t": "2.0",
            "own_paid_usd_t": "10.0",
            "upstream_paid_usd_t": "5.0",
            "paid_usd_t": "15.0",
            "implied_P_d_usd_tco2": "100.0",
            "adjustment_usd_t": "185.0",
            "adjustment_musd": "370.0",
        },
    ]


def test_negative_adjustment_is_a_rebate_with_negative_money(tmp_path):
    """An export rebate keeps its sign in both the per-tonne and the total column."""
    furnace = _make_process_center(
        "P2_0",
        "DEU",
        tlp.ProcessType.PRODUCTION,
        "EAF",
        production_cost=40.0,
        emission_intensity=0.4,
    )
    demand = _make_process_center("Chile", "CHL", tlp.ProcessType.DEMAND, "demand")
    steel = tlp.Commodity(name="steel")
    allocations = tlp.Allocations(
        allocations={(furnace, demand, steel): 500_000.0},
        carbon_border_charges={(furnace, demand, steel): -40.0},
    )

    (row,) = _export(tmp_path, allocations)

    assert row["kind"] == "rebate"
    assert row["adjustment_usd_t"] == "-40.0"
    assert row["adjustment_musd"] == "-20.0"


def test_arcs_without_volume_or_adjustment_are_skipped(tmp_path):
    """Only realised charges reach the file: no allocated volume, no recorded arc or a zero charge writes nothing."""
    furnace = _make_process_center("P3_0", "IND", tlp.ProcessType.PRODUCTION, "BF", emission_intensity=2.0)
    germany = _make_process_center("Germany", "DEU", tlp.ProcessType.DEMAND, "demand")
    france = _make_process_center("France", "FRA", tlp.ProcessType.DEMAND, "demand")
    italy = _make_process_center("Italy", "ITA", tlp.ProcessType.DEMAND, "demand")
    steel = tlp.Commodity(name="steel")
    allocations = tlp.Allocations(
        allocations={(furnace, germany, steel): 0.0, (furnace, italy, steel): 100.0},
        carbon_border_charges={
            (furnace, germany, steel): 50.0,
            (furnace, france, steel): 50.0,
            (furnace, italy, steel): 0.0,
        },
    )

    assert _export(tmp_path, allocations) == []


def test_implied_price_is_blank_for_a_source_without_emissions(tmp_path):
    """A rebate on a zero-emission source has no recoverable destination price."""
    furnace = _make_process_center("P4_0", "SWE", tlp.ProcessType.PRODUCTION, "EAF", production_cost=3.0)
    demand = _make_process_center("Chile", "CHL", tlp.ProcessType.DEMAND, "demand")
    steel = tlp.Commodity(name="steel")
    allocations = tlp.Allocations(
        allocations={(furnace, demand, steel): 100.0},
        carbon_border_charges={(furnace, demand, steel): -3.0},
    )

    (row,) = _export(tmp_path, allocations)

    assert row["implied_P_d_usd_tco2"] == ""


def test_year_without_charges_writes_a_header_only_file(tmp_path):
    """Allocations carrying no charges still produce the file, so every run has one."""
    supplier = _make_process_center("sup", "AUS", tlp.ProcessType.SUPPLY, "io_low_supply")
    furnace = _make_process_center("P5_0", "CHN", tlp.ProcessType.PRODUCTION, "BF")
    allocations = tlp.Allocations(
        allocations={(supplier, furnace, tlp.Commodity(name="io_low")): 100.0},
        carbon_border_charges=None,
    )
    path = tmp_path / "TM" / "carbon_border_charges.csv"

    file_output.export_carbon_border_charges_to_csv(allocations=allocations, year=2030, filename=str(path))

    assert path.read_text(encoding="utf-8").strip() == ",".join(file_output.CARBON_BORDER_CHARGE_COLUMNS)


def test_later_years_append_to_the_same_file_under_one_header(tmp_path):
    """Each year's rows land below the previous year's, with the header written once."""
    furnace = _make_process_center("P6_0", "CHN", tlp.ProcessType.PRODUCTION, "BF", emission_intensity=2.0)
    demand = _make_process_center("Germany", "DEU", tlp.ProcessType.DEMAND, "demand")
    steel = tlp.Commodity(name="steel")
    allocations = tlp.Allocations(
        allocations={(furnace, demand, steel): 100.0},
        carbon_border_charges={(furnace, demand, steel): 50.0},
    )

    _export(tmp_path, allocations, year=2030)
    rows = _export(tmp_path, allocations, year=2031)

    assert [row["year"] for row in rows] == ["2030", "2031"]
