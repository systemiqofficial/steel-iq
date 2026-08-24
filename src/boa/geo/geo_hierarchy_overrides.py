"""Human-verified calibration overrides for the generated ``geo_hierarchy`` table.

This is the sign-off surface for the sub-national first-order unit calibration: the small
set of hand-checked decisions the generator (``boa.geo.geo_hierarchy.build_geo_hierarchy``)
applies on top of Natural Earth admin-1 + ISO 3166-2. Kept as data, separate from the
generation logic, so it can be reviewed as data. Populated for China only at first.

Ported from steel-iq's ``src/steelo/data/geo_hierarchy_overrides.py`` and intended to stay
closely aligned with it — the two repos share the province taxonomy, so changes to the
overrides (new declared countries, ownership guards, display names) should be mirrored in
both. Keep this module a faithful counterpart rather than letting it drift.
"""

from __future__ import annotations

# Countries whose first-order units are populated. Others resolve at country level via the
# cost-key fallback (bare iso3). Keyed by ISO 3166-1 alpha-2. China only for now.
DECLARED_ISO2: tuple[str, ...] = ("CN",)

# Codes the model represents as their own country (e.g. Taiwan is iso3 TWN) or that carry no
# baseload-power relevance under their parent (Hong Kong / Macau) — never populated under
# their parent country. Natural Earth files these under their own ``iso_a2``, so this is a
# defensive guard against politically-inconsistent NE builds, keyed by ISO 3166-1 alpha-2.
OWNED_AS_SEPARATE_COUNTRY: dict[str, set[str]] = {
    "CN": {"CN-TW", "CN-HK", "CN-MO"},
}

# Friendlier display names where NE's ``name`` reads awkwardly. Display only — the key is
# always the ISO 3166-2 code, so these never affect lookups. Keyed by ISO 3166-2 code.
GEO_UNIT_DISPLAY_OVERRIDES: dict[str, str] = {
    "CN-NM": "Inner Mongolia",  # NE: "Inner Mongol"
    "CN-XZ": "Tibet",  # NE: "Xizang"
}

# Natural Earth admin-1 attribute columns the generator reads.
ADMIN1_COLUMNS: list[str] = ["adm0_a3", "iso_a2", "iso_3166_2", "name", "type_en", "region"]
