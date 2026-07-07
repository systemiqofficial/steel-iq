"""Tests for the geo_options.csv reference generator.

Exercises `recreation_functions.write_geo_options_csv` against a synthetic geo_hierarchy.json: it
emits one row per unit sorted by geo_key, with the expected columns, the country name joined from a
sibling country_mappings.json (blank when that file is absent), and a blank Region for an unregioned
unit.
"""

import csv
import json
from pathlib import Path

from steelo.data import recreation_functions

HIERARCHY_ROWS = [
    {
        "iso3": "CHN",
        "geo_unit": "CN-SH",
        "geo_key": "CHN:CN-SH",
        "display_name": "Shanghai",
        "unit_type": "Municipality",
        "ne_region": "East China",
        "ne_name": "Shanghai",
    },
    {
        "iso3": "CHN",
        "geo_unit": "CN-FJ",
        "geo_key": "CHN:CN-FJ",
        "display_name": "Fujian",
        "unit_type": "Province",
        "ne_region": "",
        "ne_name": "Fujian",
    },
]

COUNTRY_MAPPINGS = [{"Country": "China", "ISO 3-letter code": "CHN"}]


def write_hierarchy(directory: Path, with_country_mappings: bool = True) -> Path:
    """Write a synthetic geo_hierarchy.json (and optional sibling country_mappings.json)."""
    geo_hierarchy_path = directory / "geo_hierarchy.json"
    geo_hierarchy_path.write_text(json.dumps(HIERARCHY_ROWS))
    if with_country_mappings:
        (directory / "country_mappings.json").write_text(json.dumps(COUNTRY_MAPPINGS))
    return geo_hierarchy_path


def read_csv(path: Path):
    """Read the generated CSV into a header list and a list of row dicts."""
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return reader.fieldnames, list(reader)


def test_columns_match_expected(tmp_path):
    """The CSV header is exactly the expected geo_options columns in order."""
    geo_hierarchy_path = write_hierarchy(tmp_path)
    out_path = tmp_path / "geo_options.csv"

    recreation_functions.write_geo_options_csv(geo_hierarchy_path, out_path)

    fieldnames, _ = read_csv(out_path)
    assert fieldnames == recreation_functions.GEO_OPTIONS_COLUMNS


def test_one_row_per_unit_sorted_by_geo_key(tmp_path):
    """Output has one row per hierarchy unit, sorted alphabetically by geo_key."""
    geo_hierarchy_path = write_hierarchy(tmp_path)
    out_path = tmp_path / "geo_options.csv"

    recreation_functions.write_geo_options_csv(geo_hierarchy_path, out_path)

    _, rows = read_csv(out_path)
    assert [row["geo_key"] for row in rows] == ["CHN:CN-FJ", "CHN:CN-SH"]


def test_country_name_joined_and_blank_region(tmp_path):
    """Country name is joined from country_mappings.json; an unregioned unit has a blank Region."""
    geo_hierarchy_path = write_hierarchy(tmp_path)
    out_path = tmp_path / "geo_options.csv"

    recreation_functions.write_geo_options_csv(geo_hierarchy_path, out_path)

    _, rows = read_csv(out_path)
    fujian = next(row for row in rows if row["geo_key"] == "CHN:CN-FJ")
    assert fujian["Country (name)"] == "China"
    assert fujian["Country (iso3)"] == "CHN"
    assert fujian["Subnational (display_name)"] == "Fujian"
    assert fujian["Region"] == ""


def test_country_name_blank_without_country_mappings(tmp_path):
    """When no sibling country_mappings.json is present, the country name is left blank."""
    geo_hierarchy_path = write_hierarchy(tmp_path, with_country_mappings=False)
    out_path = tmp_path / "geo_options.csv"

    recreation_functions.write_geo_options_csv(geo_hierarchy_path, out_path)

    _, rows = read_csv(out_path)
    assert all(row["Country (name)"] == "" for row in rows)
    assert all(row["Country (iso3)"] == "CHN" for row in rows)
