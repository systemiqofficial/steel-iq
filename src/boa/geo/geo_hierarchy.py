"""Generate the canonical sub-national ``geo_hierarchy`` from Natural Earth admin-1.

A ``geo_unit`` is a first-order sub-national division identified by its ISO 3166-2 code
(e.g. ``CN-HB`` = Hubei). The finest-available geographic key is the ``geo_key``,
``iso3:geo_unit`` (e.g. ``CHN:CN-HB``); with no sub-national unit it is the bare ``iso3``.

This module is the *source of truth* for which geo_units exist: it filters NE admin-1 features
to pycountry first-order ISO 3166-2 codes and applies the hand-verified overrides in
``geo_hierarchy_overrides``. There is no derived shapefile — both the runtime point-in-polygon
registry (``boa.geo.iso3_finder.load_subregion_polygons``) and the input validator build directly
off the NE admin-1 dataset via the helpers here.

Comparison with steel-iq (kept intentionally aligned): the filtering logic and overrides are a
faithful port of steel-iq's ``build_geo_hierarchy`` (``src/steelo/data/recreation_functions.py``),
and — like steel-iq's ``adapters/geospatial/geo_unit_lookup.py`` — the province geometry is read
straight from the NE admin-1 shapefile rather than a derived file. The one deliberate divergence:
steel-iq *persists* ``geo_hierarchy.json`` during data prep and its validator reads that file,
whereas BOA computes the hierarchy **in memory** from admin-1 on demand. This keeps one fewer
generated artifact; both approaches soft-degrade (skip the sub-national check) when the admin-1
source is absent, so the behaviour is equivalent. If a persisted registry is ever needed, add it
alongside these helpers rather than changing the in-memory default.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

import pycountry

from boa.geo.geo_hierarchy_overrides import (
    ADMIN1_COLUMNS,
    DECLARED_ISO2,
    GEO_UNIT_DISPLAY_OVERRIDES,
    OWNED_AS_SEPARATE_COUNTRY,
)

if TYPE_CHECKING:
    import geopandas as gpd

logger = logging.getLogger(__name__)


def compose_geo_key(iso3: str, geo_unit: str | None) -> str:
    """Finest-available geographic key: ``iso3:geo_unit`` (e.g. ``"CHN:CN-HB"``), else ``iso3``.

    The country ``iso3`` is always recoverable via ``geo_key.split(":", 1)[0]``.
    """
    return f"{iso3}:{geo_unit}" if geo_unit else iso3


def _pycountry_top_level_codes(iso2: str) -> set[str]:
    """Return the first-order (top-level) ISO 3166-2 codes pycountry lists for a country.

    First-order divisions are those whose subdivision has no parent.
    """
    return {
        sub.code
        for sub in pycountry.subdivisions
        if sub.country_code == iso2 and getattr(sub, "parent_code", None) in (None, "")
    }


def build_geo_hierarchy(
    records: Iterable[dict[str, Any]],
    *,
    declared_iso2: Iterable[str] = DECLARED_ISO2,
) -> list[dict[str, Any]]:
    """Build geo_hierarchy rows from Natural Earth admin-1 attribute records.

    For each declared country, keeps NE features whose ``iso_3166_2`` is a pycountry first-order
    (top-level) code, excluding any the model owns as a separate country
    (``OWNED_AS_SEPARATE_COUNTRY``). The top-level gate doubles as a family detector: a country
    whose NE features are one level too deep (Italy's provincie, France's departements) yields
    zero admitted units, raised as a loud error rather than emitted at the wrong level.

    Validating against pycountry top-level codes also drops NE disputed placeholders such as
    ``CN-X01~`` (Paracel Islands) without a hand-maintained list.

    Returns one row per first-order unit, sorted by ``geo_key``, each with ``iso3``,
    ``geo_unit`` (ISO 3166-2 code), ``geo_key`` (``iso3:code``), ``display_name``, ``unit_type``.

    Raises:
        NotImplementedError: If a declared country has features but none are first-order codes
            (NE feature level is finer than first-order) — it needs the region-rollup path.
    """
    declared = set(declared_iso2)
    records = list(records)
    rows: list[dict[str, Any]] = []

    for iso2 in declared:
        top_level = _pycountry_top_level_codes(iso2)
        excluded = OWNED_AS_SEPARATE_COUNTRY.get(iso2, set())
        country_records = [r for r in records if r.get("iso_a2") == iso2]
        admitted = 0

        for record in country_records:
            code = (record.get("iso_3166_2") or "").strip()
            if not code or code in excluded or code not in top_level:
                continue
            admitted += 1
            iso3 = record["adm0_a3"]
            ne_name = (record.get("name") or "").strip()
            rows.append(
                {
                    "iso3": iso3,
                    "geo_unit": code,
                    "geo_key": compose_geo_key(iso3, code),
                    "display_name": GEO_UNIT_DISPLAY_OVERRIDES.get(code, ne_name),
                    "unit_type": (record.get("type_en") or "").strip(),
                }
            )

        if country_records and admitted == 0:
            raise NotImplementedError(
                f"{iso2}: no NE admin-1 feature is a first-order ISO 3166-2 unit — the feature "
                f"level is finer than first-order (e.g. provinces, not regions). This country "
                f"needs the NE `region`-column rollup path, which is not implemented."
            )

    rows.sort(key=lambda row: row["geo_key"])
    return rows


def geo_hierarchy_from_admin1(
    admin1_shapefile_path: Path,
    *,
    declared_iso2: Iterable[str] = DECLARED_ISO2,
) -> list[dict[str, Any]]:
    """Read the NE admin-1 shapefile and build the geo_hierarchy rows, or ``[]`` if absent."""
    import geopandas as gpd

    if not Path(admin1_shapefile_path).exists():
        logger.info("NE admin-1 shapefile %s not found; geo_hierarchy empty.", admin1_shapefile_path)
        return []
    gdf = gpd.read_file(admin1_shapefile_path)
    present = [c for c in ADMIN1_COLUMNS if c in gdf.columns]
    return build_geo_hierarchy(gdf[present].to_dict("records"), declared_iso2=declared_iso2)


def province_polygons_from_admin1(
    admin1_shapefile_path: Path,
    *,
    declared_iso2: Iterable[str] = DECLARED_ISO2,
) -> "gpd.GeoDataFrame":
    """Build the sub-national polygon frame from NE admin-1, keyed for the subregion registry.

    Filters NE admin-1 to the canonical first-order units (``build_geo_hierarchy``) and returns
    a GeoDataFrame with columns ``iso3``, ``subregion`` (the ``geo_key``, e.g. ``CHN:CN-HB``),
    ``display_name``, ``unit_type``, ``geometry`` — the schema the subregion loader expects.
    Returns an empty frame if the shapefile is absent.
    """
    import geopandas as gpd

    if not Path(admin1_shapefile_path).exists():
        return gpd.GeoDataFrame(
            columns=["iso3", "subregion", "display_name", "unit_type", "geometry"], geometry="geometry"
        )
    gdf = gpd.read_file(admin1_shapefile_path)
    rows = build_geo_hierarchy(
        gdf[[c for c in ADMIN1_COLUMNS if c in gdf.columns]].to_dict("records"),
        declared_iso2=declared_iso2,
    )
    geom_by_code = dict(zip(gdf["iso_3166_2"], gdf.geometry))
    records = []
    for row in rows:
        code = row["geo_unit"]
        if code not in geom_by_code:
            raise ValueError(
                f"geo_unit {code} ({row['geo_key']}) has no matching admin-1 polygon in {admin1_shapefile_path}."
            )
        records.append(
            {
                "iso3": row["iso3"],
                "subregion": row["geo_key"],
                "display_name": row["display_name"],
                "unit_type": row["unit_type"],
                "geometry": geom_by_code[code],
            }
        )
    return gpd.GeoDataFrame(records, geometry="geometry", crs=gdf.crs)


def valid_geo_keys(rows: Iterable[dict[str, Any]]) -> set[str]:
    """The set of recognised sub-national ``geo_key`` strings from geo_hierarchy rows."""
    return {row["geo_key"] for row in rows}
