"""Tests for the cached geo_unit lookup and greenfield spawn tagging.

`derive_geo_unit_for_site` is the simulation-time derivation (geometry + the two policy gates)
backed by process-cached reference data, and `generate_new_plant` tags each spawned plant's
Location through an injected derivation so province-level costs and subsidies resolve for
greenfield plants.
"""

import json

import geopandas
from shapely.geometry import box

from steelo.adapters.geospatial import geo_unit_lookup
from steelo.domain.models import PlantGroup


def _write_fixtures(tmp_path):
    """Write a temp admin-1 shapefile (China + US units) and a China-only geo_hierarchy.json."""
    admin1 = geopandas.GeoDataFrame(
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


def test_derives_gated_geo_unit(tmp_path):
    """A China site inside a declared unit derives its gated geo_unit."""
    admin1_shp, geo_hierarchy = _write_fixtures(tmp_path)

    geo_unit = geo_unit_lookup.derive_geo_unit_for_site(
        39.0,
        114.5,
        "CHN",
        admin1_shapefile_path=admin1_shp,
        geo_hierarchy_path=geo_hierarchy,
    )

    assert geo_unit == "CN-HE"


def test_gates_apply_to_derivation(tmp_path):
    """The country cross-check and hierarchy gate both leave geo_unit None."""
    admin1_shp, geo_hierarchy = _write_fixtures(tmp_path)

    # Country mismatch: stored CHN but the point falls in the US polygon.
    assert (
        geo_unit_lookup.derive_geo_unit_for_site(
            37.0,
            -120.0,
            "CHN",
            admin1_shapefile_path=admin1_shp,
            geo_hierarchy_path=geo_hierarchy,
        )
        is None
    )
    # Out of hierarchy: US-CA is not a declared unit in the China-only hierarchy.
    assert (
        geo_unit_lookup.derive_geo_unit_for_site(
            37.0,
            -120.0,
            "USA",
            admin1_shapefile_path=admin1_shp,
            geo_hierarchy_path=geo_hierarchy,
        )
        is None
    )


def test_missing_reference_data_degrades_to_none(tmp_path):
    """A missing admin-1 layer (older geo-data package) yields None, never an error."""
    geo_hierarchy = tmp_path / "geo_hierarchy.json"
    geo_hierarchy.write_text(json.dumps([{"geo_unit": "CN-HE"}]))

    geo_unit = geo_unit_lookup.derive_geo_unit_for_site(
        39.0,
        114.5,
        "CHN",
        admin1_shapefile_path=tmp_path / "missing" / "ne.shp",
        geo_hierarchy_path=geo_hierarchy,
    )

    assert geo_unit is None


def test_admin1_layer_loaded_once_across_sites(tmp_path, monkeypatch):
    """Repeated derivations reuse the cached admin-1 layer — the shapefile is read once."""
    admin1_shp, geo_hierarchy = _write_fixtures(tmp_path)

    real_read_file = geopandas.read_file
    read_counts = {"count": 0}

    def counting_read_file(*args, **kwargs):
        read_counts["count"] += 1
        return real_read_file(*args, **kwargs)

    monkeypatch.setattr(geopandas, "read_file", counting_read_file)

    for _ in range(3):
        geo_unit_lookup.derive_geo_unit_for_site(
            39.0,
            114.5,
            "CHN",
            admin1_shapefile_path=admin1_shp,
            geo_hierarchy_path=geo_hierarchy,
        )

    assert read_counts["count"] == 1


def _minimal_cost_data(site_id, tech):
    """The cost_data slice generate_new_plant reads for one product/site/tech."""
    return {
        "steel": {
            site_id: {
                tech: {
                    "fopex": 10.0,
                    "capex": 500.0,
                    "capex_no_subsidy": 550.0,
                    "cost_of_debt": 0.05,
                    "cost_of_debt_no_subsidy": 0.06,
                    "utilization_rate": 0.8,
                    "reductant": "scrap",
                    "railway_cost": 0.0,
                    "energy_costs": {"electricity": 0.05},
                },
            },
        },
    }


def test_generate_new_plant_tags_geo_unit_via_injected_derivation():
    """The spawn Location carries the derived geo_unit; without an injected derivation it stays None."""
    group = PlantGroup(plant_group_id="indi", plants=[])
    site_id = (39.0, 114.5, "CHN")

    def fake_derive(lat, lon, iso3):
        assert (lat, lon, iso3) == site_id
        return "CN-HE"

    tagged = group.generate_new_plant(
        site_id=site_id,
        technology_name="EAF",
        product="steel",
        npv=1.0,
        current_year=2026,
        existent_plant_ids=[],
        cost_data=_minimal_cost_data(site_id, "EAF"),
        equity_share=0.2,
        steel_plant_capacity=1_000_000,
        dynamic_feedstocks=[],
        plant_lifetime=20,
        derive_geo_unit=fake_derive,
    )
    untagged = group.generate_new_plant(
        site_id=site_id,
        technology_name="EAF",
        product="steel",
        npv=1.0,
        current_year=2026,
        existent_plant_ids=[tagged.plant_id],
        cost_data=_minimal_cost_data(site_id, "EAF"),
        equity_share=0.2,
        steel_plant_capacity=1_000_000,
        dynamic_feedstocks=[],
        plant_lifetime=20,
    )

    assert tagged.location.geo_unit == "CN-HE"
    assert tagged.location.geo_key == "CHN:CN-HE"
    assert untagged.location.geo_unit is None
    assert untagged.location.geo_key == "CHN"
