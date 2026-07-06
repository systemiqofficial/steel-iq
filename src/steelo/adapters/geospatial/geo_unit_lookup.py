"""Cached sub-national geo_unit derivation for simulation-time call sites.

Greenfield siting evaluates many candidate sites per step and spawn tags each new plant's
``Location``, so the Natural Earth admin-1 layer and the geo_hierarchy's declared unit codes are
loaded at most once per process and reused across every derivation. The derivation itself is the
same geometry + policy pair the input fleet was tagged with (``derive_iso3_and_geo_unit`` gated by
``resolve_geo_unit``), so evaluation-time and spawn-time results always agree.

A missing admin-1 layer or geo_hierarchy (older geo-data package) degrades to ``None`` — every
site resolves at country level, matching pre-province behaviour.
"""

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import geopandas as gpd

logger = logging.getLogger(__name__)

ADMIN1_LAYER_NAME = "ne_10m_admin_1_states_provinces"


def _default_admin1_shapefile_path() -> Path:
    """The prepared admin-1 shapefile under ``<STEELO_HOME>/data`` (extracted by prep Step 8)."""
    from steelo.config import get_steelo_home

    return get_steelo_home() / "data" / ADMIN1_LAYER_NAME / f"{ADMIN1_LAYER_NAME}.shp"


def _default_geo_hierarchy_path() -> Path:
    """The prepared ``geo_hierarchy.json`` under ``<STEELO_HOME>/data/fixtures``."""
    from steelo.config import get_steelo_home

    return get_steelo_home() / "data" / "fixtures" / "geo_hierarchy.json"


@lru_cache(maxsize=2)
def _load_admin1_layer(shapefile_path: str) -> "gpd.GeoDataFrame | None":
    """Load (once per process) the admin-1 layer, or ``None`` when the shapefile is absent."""
    import geopandas as gpd

    path = Path(shapefile_path)
    if not path.exists():
        logger.info("Admin-1 shapefile missing at %s; geo_unit derivation disabled.", path)
        return None
    logger.info("Loading admin-1 layer from %s (cached for the process).", path)
    return gpd.read_file(path)


@lru_cache(maxsize=2)
def _load_valid_geo_unit_codes(geo_hierarchy_path: str) -> frozenset[str] | None:
    """Load (once per process) the declared geo_unit codes, or ``None`` when the hierarchy is absent."""
    path = Path(geo_hierarchy_path)
    if not path.exists():
        logger.info("geo_hierarchy.json missing at %s; geo_unit derivation disabled.", path)
        return None
    return frozenset(row["geo_unit"] for row in json.loads(path.read_text()))


def derive_geo_unit_for_site(
    lat: float,
    lon: float,
    iso3: str,
    *,
    admin1_shapefile_path: Path | None = None,
    geo_hierarchy_path: Path | None = None,
    log_ctx: str = "",
) -> str | None:
    """Derive the gated sub-national ``geo_unit`` for a site, or ``None`` (country-level).

    Point-in-polygon against the cached admin-1 layer (nearest-unit fallback scoped to ``iso3``
    for coastal slack), then the two policy gates: the derived country must agree with ``iso3``
    and the unit must be declared in geo_hierarchy.

    Args:
        lat: Latitude in WGS84 degrees.
        lon: Longitude in WGS84 degrees.
        iso3: The site's canonical country code (the derived unit must belong to it).
        admin1_shapefile_path: Override for the prepared admin-1 shapefile (tests).
        geo_hierarchy_path: Override for the prepared ``geo_hierarchy.json`` (tests).
        log_ctx: Short label used in the gate's country-mismatch log line.

    Returns:
        The gated ISO 3166-2 unit code, or ``None`` when the reference data is unavailable or
        either gate rejects the derivation.
    """
    from .geospatial_layers import derive_iso3_and_geo_unit, resolve_geo_unit

    admin1_gdf = _load_admin1_layer(str(admin1_shapefile_path or _default_admin1_shapefile_path()))
    valid_codes = _load_valid_geo_unit_codes(str(geo_hierarchy_path or _default_geo_hierarchy_path()))
    if admin1_gdf is None or valid_codes is None:
        return None
    adm0_a3, code = derive_iso3_and_geo_unit(lat, lon, admin1_gdf, restrict_iso3=iso3)
    return resolve_geo_unit(
        adm0_a3,
        code,
        iso3,
        set(valid_codes),
        log_ctx=log_ctx or f"site ({lat:.4f}, {lon:.4f})",
    )
