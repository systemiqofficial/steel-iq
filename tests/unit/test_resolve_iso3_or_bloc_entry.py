"""Tests for the _resolve_iso3_or_bloc_entry helper used by read_tariffs.

Covers the four recognized forms:
    "DEU", "EU", "NOT EU", "NOT DEU", and the literal wildcard "*".
"""

import pytest

from steelo.adapters.dataprocessing.excel_reader import _resolve_iso3_or_bloc_entry


class _StubMapping:
    """Minimal stand-in for steelo.domain.models.CountryMapping.

    Only carries the attributes the helper reads: ``iso3`` and bool flags
    representing trade bloc memberships.
    """

    def __init__(self, iso3: str, **blocs: bool):
        self.iso3 = iso3
        for name, value in blocs.items():
            setattr(self, name, value)


@pytest.fixture
def country_mappings():
    return [
        _StubMapping("DEU", EU=True, OECD=True),
        _StubMapping("FRA", EU=True, OECD=True),
        _StubMapping("ITA", EU=True, OECD=True),
        _StubMapping("USA", EU=False, OECD=True),
        _StubMapping("BRA", EU=False, OECD=False),
    ]


@pytest.fixture
def supported_blocs():
    return ["EU", "OECD"]


def test_single_iso3_returns_singleton(country_mappings, supported_blocs):
    assert _resolve_iso3_or_bloc_entry("DEU", country_mappings, supported_blocs) == ["DEU"]


def test_bloc_returns_member_iso3s(country_mappings, supported_blocs):
    assert sorted(_resolve_iso3_or_bloc_entry("EU", country_mappings, supported_blocs)) == ["DEU", "FRA", "ITA"]


def test_not_bloc_returns_non_members(country_mappings, supported_blocs):
    assert sorted(_resolve_iso3_or_bloc_entry("NOT EU", country_mappings, supported_blocs)) == ["BRA", "USA"]


def test_not_iso3_returns_all_iso3s_except_target(country_mappings, supported_blocs):
    """'NOT DEU' should expand to every known iso3 other than DEU."""
    result = _resolve_iso3_or_bloc_entry("NOT DEU", country_mappings, supported_blocs)
    assert sorted(result) == ["BRA", "FRA", "ITA", "USA"]


def test_not_iso3_works_for_non_eu_country(country_mappings, supported_blocs):
    """Sanity check: 'NOT USA' covers EU members and other non-EU countries alike."""
    result = _resolve_iso3_or_bloc_entry("NOT USA", country_mappings, supported_blocs)
    assert sorted(result) == ["BRA", "DEU", "FRA", "ITA"]


def test_not_unknown_target_raises(country_mappings, supported_blocs):
    """'NOT XYZ' where XYZ is neither a bloc nor a known iso3 must raise."""
    with pytest.raises(ValueError, match="neither a known trade bloc nor a known iso3"):
        _resolve_iso3_or_bloc_entry("NOT XYZ", country_mappings, supported_blocs)


def test_wildcard_kept_as_literal(country_mappings, supported_blocs):
    """'*' is passed through; downstream matching interprets it."""
    assert _resolve_iso3_or_bloc_entry("*", country_mappings, supported_blocs) == ["*"]
