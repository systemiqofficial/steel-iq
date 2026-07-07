"""Tests for the additive sub-national `geo_unit` substrate on `Location`.

Covers the `geo_key` composition, the `Location.resolve` fallback discipline, and the
JSON persistence round-trip (including backward compatibility with saved runs that have
no `geo_unit`). These guard the foundation invariant: a `Location` with `geo_unit=None`
behaves exactly as before.
"""

import logging

import pytest

from steelo.adapters.repositories.json_repository import LocationInDb
from steelo.domain.models import Location


def make_location(iso3="CHN", geo_unit=None):
    """Build a Location with sensible defaults for these tests."""
    return Location(
        lat=38.0,
        lon=114.5,
        country="China",
        region="Asia",
        iso3=iso3,
        geo_unit=geo_unit,
    )


def test_geo_key_composes_subnational_unit():
    """A set geo_unit yields the composite `iso3:code` key."""
    location = make_location(iso3="CHN", geo_unit="CN-HE")

    assert location.geo_key == "CHN:CN-HE"
    assert location.geo_key.split(":", 1)[0] == "CHN"


def test_geo_key_defaults_to_country():
    """With no geo_unit the key is the bare iso3, unchanged from before."""
    assert make_location(iso3="DEU").geo_key == "DEU"


def test_geo_unit_does_not_affect_hash_or_equality():
    """geo_unit must not change identity — hash is over (lat, lon) only."""
    without = make_location(geo_unit=None)
    with_unit = make_location(geo_unit="CN-HE")

    assert hash(without) == hash(with_unit)


def test_resolve_prefers_subnational_value():
    """resolve returns the sub-national value when present in the lookup."""
    costs = {"CHN": 80.0, "CHN:CN-HE": 65.0}

    assert make_location(iso3="CHN", geo_unit="CN-HE").resolve(costs, what="electricity") == 65.0


def test_resolve_logs_when_subnational_value_used(caplog):
    """A sub-national hit is logged at INFO — the positive counterpart to the fall-back log."""
    costs = {"CHN": 80.0, "CHN:CN-HE": 65.0}

    with caplog.at_level(logging.INFO):
        value = make_location(iso3="CHN", geo_unit="CN-HE").resolve(costs, what="electricity")

    assert value == 65.0
    assert "resolved to sub-national unit CHN:CN-HE" in caplog.text


def test_resolve_falls_back_to_country_and_logs(caplog):
    """A supplied unit with no sub-national row falls back to the country value, logged at INFO."""
    costs = {"CHN": 80.0}

    with caplog.at_level(logging.INFO):
        value = make_location(iso3="CHN", geo_unit="CN-SX").resolve(costs, what="electricity")

    assert value == 80.0
    assert "falling back to country CHN" in caplog.text


def test_resolve_country_location_is_silent(caplog):
    """A country-level location resolves straight to iso3 with no fallback logging."""
    costs = {"DEU": 90.0}

    with caplog.at_level(logging.INFO):
        assert make_location(iso3="DEU").resolve(costs, what="electricity") == 90.0

    assert "falling back" not in caplog.text


def test_resolve_returns_none_when_absent():
    """resolve returns None when neither the unit nor the country is in the lookup."""
    assert make_location(iso3="FRA").resolve({"DEU": 1.0}, what="electricity") is None


def test_resolve_raises_on_unrecognised_unit():
    """A supplied unit absent from valid_geo_keys is an error, not a silent fallback."""
    costs = {"CHN": 80.0}
    valid = {"CHN:CN-HE", "CHN:CN-SX"}

    with pytest.raises(KeyError, match="not a recognised unit"):
        make_location(iso3="CHN", geo_unit="CN-ZZ").resolve(costs, what="electricity", valid_geo_keys=valid)


def test_location_indb_round_trips_geo_unit():
    """geo_unit survives the Location -> LocationInDb -> Location JSON round-trip."""
    location = make_location(iso3="CHN", geo_unit="CN-HE")

    in_db = LocationInDb(**location.__dict__)
    assert in_db.geo_unit == "CN-HE"

    restored = Location(**in_db.model_dump())
    assert restored.geo_unit == "CN-HE"
    assert restored.geo_key == "CHN:CN-HE"


def test_legacy_location_without_geo_unit_loads_as_none():
    """Saved runs predating geo_unit (no field in JSON) still load, defaulting to None."""
    legacy_json = {"iso3": "DEU", "country": "Germany", "region": "Europe", "lat": 1.0, "lon": 2.0}

    in_db = LocationInDb(**legacy_json)
    assert in_db.geo_unit is None

    restored = Location(**in_db.model_dump())
    assert restored.geo_unit is None
    assert restored.geo_key == "DEU"
