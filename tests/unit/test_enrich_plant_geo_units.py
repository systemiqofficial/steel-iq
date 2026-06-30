"""Tests for `recreation_functions.enrich_plant_geo_units`.

Drives the post-geo enrichment pass end-to-end over a temp plant repo, a temp admin-1 shapefile,
and a temp geo_hierarchy.json. Covers tagging a declared-country plant, the country cross-check
(derived adm0_a3 must match the stored geocoder iso3), the hierarchy gate (undeclared unit stays
None), and persistence through the plants JSON.
"""

import json

import geopandas as gpd
from shapely.geometry import box

from steelo.adapters.repositories.json_repository import PlantJsonRepository
from steelo.domain.constants import PLANT_LIFETIME
from steelo.domain.models import Location, Plant


def _make_plant(plant_id: str, lat: float, lon: float, iso3: str) -> Plant:
    """Minimal Plant at a given coordinate for enrichment tests."""
    return Plant(
        plant_id=plant_id,
        location=Location(lat=lat, lon=lon, country=iso3, region="unknown", iso3=iso3),
        furnace_groups=[],
        power_source="grid",
        soe_status="private",
        parent_gem_id="indi",
        workforce_size=500,
        certified=False,
        category_steel_product=set(),
        technology_unit_fopex={},
    )


def _write_fixtures(tmp_path):
    """Write a temp admin-1 shapefile (China + US units) and a China-only geo_hierarchy.json."""
    admin1 = gpd.GeoDataFrame(
        {
            "adm0_a3": ["CHN", "USA"],
            "iso_3166_2": ["CN-HE", "US-CA"],
            "geometry": [box(112.0, 37.0, 117.0, 41.0), box(-125.0, 32.0, -114.0, 42.0)],
        },
        crs="EPSG:4326",
    )
    admin1_shp = tmp_path / "ne_10m_admin_1_states_provinces" / "ne_10m_admin_1_states_provinces.shp"
    admin1_shp.parent.mkdir(parents=True, exist_ok=True)
    admin1.to_file(admin1_shp)

    geo_hierarchy = tmp_path / "geo_hierarchy.json"
    geo_hierarchy.write_text(json.dumps([{"geo_unit": "CN-HE", "iso3": "CHN", "geo_key": "CHN:CN-HE"}]))
    return admin1_shp, geo_hierarchy


def _enrich(tmp_path, plants):
    """Persist `plants`, run enrichment, and return the re-listed plants (JSON round-trip)."""
    from steelo.data import recreation_functions

    admin1_shp, geo_hierarchy = _write_fixtures(tmp_path)
    plants_json = tmp_path / "plants.json"
    PlantJsonRepository(plants_json, PLANT_LIFETIME).add_list(plants)

    tagged = recreation_functions.enrich_plant_geo_units(
        plants_json_path=plants_json,
        admin1_shapefile_path=admin1_shp,
        geo_hierarchy_json_path=geo_hierarchy,
    )
    restored = {p.plant_id: p for p in PlantJsonRepository(plants_json, PLANT_LIFETIME).list()}
    return tagged, restored


def test_china_plant_in_hierarchy_is_tagged_and_persisted(tmp_path):
    """A China plant inside a declared unit gains geo_unit, persisted through the plants JSON."""
    tagged, restored = _enrich(tmp_path, [_make_plant("P1", 39.0, 114.5, "CHN")])

    assert tagged == 1
    assert restored["P1"].location.geo_unit == "CN-HE"
    assert restored["P1"].location.geo_key == "CHN:CN-HE"


def test_country_mismatch_is_flagged_to_none(tmp_path):
    """A plant whose geocoder iso3 disagrees with the derived adm0_a3 stays None (never contradicts iso3)."""
    # Stored iso3 says CHN, but the coordinate falls inside the US polygon.
    tagged, restored = _enrich(tmp_path, [_make_plant("P1", 37.0, -120.0, "CHN")])

    assert tagged == 0
    assert restored["P1"].location.geo_unit is None


def test_unit_outside_hierarchy_stays_none(tmp_path):
    """A US plant derives US-CA, which is not in the China-only hierarchy, so geo_unit stays None."""
    tagged, restored = _enrich(tmp_path, [_make_plant("P1", 37.0, -120.0, "USA")])

    assert tagged == 0
    assert restored["P1"].location.geo_unit is None


def test_mixed_set_tags_only_declared_units(tmp_path):
    """Across a mixed set only the in-hierarchy China plant is tagged; the US plant is left None."""
    tagged, restored = _enrich(
        tmp_path,
        [_make_plant("CN", 39.0, 114.5, "CHN"), _make_plant("US", 37.0, -120.0, "USA")],
    )

    assert tagged == 1
    assert restored["CN"].location.geo_unit == "CN-HE"
    assert restored["US"].location.geo_unit is None


def test_missing_admin1_layer_is_tolerated(tmp_path):
    """A missing admin-1 shapefile leaves every geo_unit None and returns 0 (older geo-data package)."""
    from steelo.data import recreation_functions

    geo_hierarchy = tmp_path / "geo_hierarchy.json"
    geo_hierarchy.write_text(json.dumps([{"geo_unit": "CN-HE"}]))
    plants_json = tmp_path / "plants.json"
    PlantJsonRepository(plants_json, PLANT_LIFETIME).add_list([_make_plant("P1", 39.0, 114.5, "CHN")])

    tagged = recreation_functions.enrich_plant_geo_units(
        plants_json_path=plants_json,
        admin1_shapefile_path=tmp_path / "missing" / "ne.shp",
        geo_hierarchy_json_path=geo_hierarchy,
    )

    assert tagged == 0
    restored = PlantJsonRepository(plants_json, PLANT_LIFETIME).list()
    assert restored[0].location.geo_unit is None
