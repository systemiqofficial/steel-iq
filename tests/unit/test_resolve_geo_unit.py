"""Tests for `geospatial_layers.resolve_geo_unit`.

The pure policy gate over a derived admin-1 unit: the country cross-check (derived adm0_a3 must
agree with the canonical iso3) and the hierarchy-membership gate (only declared units are stored).
Ports the gate cases from the deleted enrichment-pass tests; the geometry itself is covered by
`derive_iso3_and_geo_unit` tests.
"""

from steelo.adapters.geospatial.geospatial_layers import resolve_geo_unit

VALID_CODES = {"CN-HE", "CN-LN"}


def test_valid_unit_in_hierarchy_is_returned():
    """A derived unit whose country agrees and that is declared in the hierarchy passes the gate."""
    assert resolve_geo_unit("CHN", "CN-HE", "CHN", VALID_CODES) == "CN-HE"


def test_country_mismatch_is_gated_to_none():
    """A derived country disagreeing with the canonical iso3 gates the unit to None."""
    assert resolve_geo_unit("USA", "US-CA", "CHN", VALID_CODES) is None


def test_country_mismatch_wins_even_for_declared_unit():
    """The country cross-check fires before hierarchy membership — a declared unit of the wrong
    country never contradicts the canonical iso3."""
    assert resolve_geo_unit("TWN", "CN-HE", "CHN", VALID_CODES) is None


def test_unit_outside_hierarchy_is_gated_to_none():
    """A unit of an undeclared country (no authored rows) resolves at country level."""
    assert resolve_geo_unit("USA", "US-CA", "USA", VALID_CODES) is None


def test_no_hit_returns_none():
    """A point-in-polygon no-hit (both derivations None) yields None without logging a mismatch."""
    assert resolve_geo_unit(None, None, "CHN", VALID_CODES) is None


def test_missing_code_with_agreeing_country_returns_none():
    """A matched country with a falsy unit code (blank iso_3166_2 in the source layer) stays None."""
    assert resolve_geo_unit("CHN", None, "CHN", VALID_CODES) is None
