"""Tests for the geo_hierarchy calibration generator.

Exercises `recreation_functions.build_geo_hierarchy` against a synthetic Natural Earth
admin-1 attribute fixture (no shapefile dependency), covering the first-order filter, the
owned-as-separate-country exclusion, the disputed-placeholder drop, display overrides, the
faithful `ne_region` pass-through, and the fail-safe for finer-than-first-order countries.
"""

import pytest

from steelo.data import recreation_functions


def admin1_records():
    """Synthetic NE admin-1 attribute records mirroring the real column shape."""
    return [
        {
            "adm0_a3": "CHN",
            "iso_a2": "CN",
            "iso_3166_2": "CN-HE",
            "name": "Hebei",
            "type_en": "Province",
            "region": "North China",
        },
        {
            "adm0_a3": "CHN",
            "iso_a2": "CN",
            "iso_3166_2": "CN-FJ",
            "name": "Fujian",
            "type_en": "Province",
            "region": None,
        },
        {
            "adm0_a3": "CHN",
            "iso_a2": "CN",
            "iso_3166_2": "CN-NM",
            "name": "Inner Mongol",
            "type_en": "Autonomous Region",
            "region": "North China",
        },
        {
            "adm0_a3": "CHN",
            "iso_a2": "CN",
            "iso_3166_2": "CN-SH",
            "name": "Shanghai",
            "type_en": "Municipality",
            "region": "East China",
        },
        {
            "adm0_a3": "CHN",
            "iso_a2": "CN",
            "iso_3166_2": "CN-X01~",
            "name": "Paracel Islands",
            "type_en": None,
            "region": None,
        },
        {
            "adm0_a3": "TWN",
            "iso_a2": "CN",
            "iso_3166_2": "CN-TW",
            "name": "Taiwan",
            "type_en": "Province",
            "region": None,
        },
        {
            "adm0_a3": "DEU",
            "iso_a2": "DE",
            "iso_3166_2": "DE-BY",
            "name": "Bavaria",
            "type_en": "State",
            "region": None,
        },
    ]


def test_build_geo_hierarchy_keeps_only_valid_first_order_units():
    """China yields its first-order units; Paracel (invalid code) and Taiwan (owned-as-separate) drop."""
    rows = recreation_functions.build_geo_hierarchy(admin1_records(), declared_iso2=("CN",))

    codes = [row["geo_unit"] for row in rows]
    assert codes == ["CN-FJ", "CN-HE", "CN-NM", "CN-SH"]  # sorted by geo_key
    assert "CN-X01~" not in codes
    assert "CN-TW" not in codes


def test_build_geo_hierarchy_ignores_undeclared_countries():
    """Features of countries not declared are skipped (they resolve at country level)."""
    rows = recreation_functions.build_geo_hierarchy(admin1_records(), declared_iso2=("CN",))

    assert all(row["iso3"] == "CHN" for row in rows)
    assert "DE-BY" not in {row["geo_unit"] for row in rows}


def test_build_geo_hierarchy_composes_geo_key_and_columns():
    """Each row carries the composite geo_key and the expected columns."""
    rows = recreation_functions.build_geo_hierarchy(admin1_records(), declared_iso2=("CN",))
    hebei = next(row for row in rows if row["geo_unit"] == "CN-HE")

    assert hebei == {
        "iso3": "CHN",
        "geo_unit": "CN-HE",
        "geo_key": "CHN:CN-HE",
        "display_name": "Hebei",
        "unit_type": "Province",
        "ne_region": "North China",
        "ne_name": "Hebei",
    }


def test_build_geo_hierarchy_applies_display_override():
    """A display override renames the unit for humans without touching the code/key."""
    rows = recreation_functions.build_geo_hierarchy(admin1_records(), declared_iso2=("CN",))
    inner_mongolia = next(row for row in rows if row["geo_unit"] == "CN-NM")

    assert inner_mongolia["display_name"] == "Inner Mongolia"
    assert inner_mongolia["ne_name"] == "Inner Mongol"


def test_build_geo_hierarchy_passes_ne_region_through_faithfully():
    """NE's blank region is passed through as empty — no foundation-level patching."""
    rows = recreation_functions.build_geo_hierarchy(admin1_records(), declared_iso2=("CN",))
    fujian = next(row for row in rows if row["geo_unit"] == "CN-FJ")

    assert fujian["ne_region"] == ""


def test_build_geo_hierarchy_raises_for_finer_than_first_order_country():
    """A country whose NE features are 2nd-order (Italy provincie) fails loudly, not silently."""
    italy_provincie = [
        {
            "adm0_a3": "ITA",
            "iso_a2": "IT",
            "iso_3166_2": "IT-AG",
            "name": "Agrigento",
            "type_en": "Province",
            "region": "Sicilia",
        },
        {
            "adm0_a3": "ITA",
            "iso_a2": "IT",
            "iso_3166_2": "IT-MI",
            "name": "Milano",
            "type_en": "Province",
            "region": "Lombardia",
        },
    ]

    with pytest.raises(NotImplementedError, match="finer than first-order"):
        recreation_functions.build_geo_hierarchy(italy_provincie, declared_iso2=("IT",))
