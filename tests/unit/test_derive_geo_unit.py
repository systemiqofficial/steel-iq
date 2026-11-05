"""Tests for `geospatial_layers.derive_iso3_and_geo_unit`.

Exercises the point-in-polygon derivation and the iso3-scoped nearest-polygon coastal fallback
against a synthetic admin-1 GeoDataFrame (no shapefile dependency). The fallback fixture is shaped
so a nearest-*centroid* rule would pick the wrong unit, locking in the nearest-*edge* behaviour.
"""

import geopandas as gpd
from shapely.geometry import box

from steelo.adapters.geospatial import geospatial_layers


def admin1_gdf():
    """Synthetic NE admin-1 polygons: two China units and one US unit, non-overlapping.

    CN-AH is a large box whose nearest edge is close to the fallback test point but whose centroid
    is far (south-east), so it traps a nearest-centroid rule into preferring the smaller CN-HE.
    """
    return gpd.GeoDataFrame(
        {
            "adm0_a3": ["CHN", "CHN", "USA"],
            "iso_3166_2": ["CN-HE", "CN-AH", "US-CA"],
            "geometry": [
                box(112.0, 37.0, 117.0, 41.0),  # CN-HE: small, just west of the test point
                box(117.5, 20.0, 130.0, 38.8),  # CN-AH: large, edge near point but centroid far SE
                box(-125.0, 32.0, -114.0, 42.0),  # US-CA
            ],
        },
        crs="EPSG:4326",
    )


def test_point_inside_polygon_yields_country_and_unit():
    """A point inside CN-HE returns both the country and the ISO 3166-2 code from that geometry."""
    adm0_a3, code = geospatial_layers.derive_iso3_and_geo_unit(39.0, 114.5, admin1_gdf())

    assert (adm0_a3, code) == ("CHN", "CN-HE")


def test_point_in_other_country_returns_truthful_adm0_a3():
    """PIP against the full set returns the real country, so a caller can detect disagreement."""
    adm0_a3, code = geospatial_layers.derive_iso3_and_geo_unit(37.0, -120.0, admin1_gdf())

    assert (adm0_a3, code) == ("USA", "US-CA")


def test_no_hit_without_restrict_returns_none():
    """A point inside no polygon and no restrict_iso3 yields (None, None) — no fallback."""
    assert geospatial_layers.derive_iso3_and_geo_unit(0.0, 0.0, admin1_gdf()) == (None, None)


def test_coastal_fallback_uses_nearest_edge_not_centroid():
    """A no-hit point near CN-AH's edge falls back to CN-AH; a centroid rule would wrongly pick CN-HE."""
    adm0_a3, code = geospatial_layers.derive_iso3_and_geo_unit(39.0, 118.0, admin1_gdf(), restrict_iso3="CHN")

    assert (adm0_a3, code) == ("CHN", "CN-AH")


def test_fallback_scoped_to_restrict_iso3():
    """The fallback only considers units of restrict_iso3, ignoring a nearer foreign polygon."""
    # This point is far from every polygon; restricting to USA must return the US unit, never a
    # geographically nearer China one.
    adm0_a3, code = geospatial_layers.derive_iso3_and_geo_unit(50.0, 100.0, admin1_gdf(), restrict_iso3="USA")

    assert (adm0_a3, code) == ("USA", "US-CA")


def test_fallback_returns_none_when_country_absent():
    """A no-hit with a restrict_iso3 that has no polygons in the set yields (None, None)."""
    assert geospatial_layers.derive_iso3_and_geo_unit(0.0, 0.0, admin1_gdf(), restrict_iso3="BRA") == (
        None,
        None,
    )
