"""Human-verified calibration overrides for the generated `geo_hierarchy` table.

This is the *sign-off surface* for the sub-national first-order unit calibration: the small
set of hand-checked decisions the generator (`recreation_functions.build_geo_hierarchy`)
applies on top of Natural Earth admin-1 + ISO 3166-2. Kept as data, separate from the
generation logic, so it can be reviewed as data. Populated for China only at first.
"""

# Codes the model represents as their own country (e.g. Taiwan is iso3 TWN) or that carry no
# steel industry (Hong Kong / Macau) — never populated under their parent country. Natural
# Earth files these under their own `iso_a2`, so this is a defensive guard against
# politically-inconsistent NE builds, keyed by ISO 3166-1 alpha-2 country code.
OWNED_AS_SEPARATE_COUNTRY: dict[str, set[str]] = {
    "CN": {"CN-TW", "CN-HK", "CN-MO"},
}

# Friendlier display names where NE's `name` reads awkwardly. Display only — the key is always
# the ISO 3166-2 code, so these never affect lookups. Keyed by ISO 3166-2 code.
GEO_UNIT_DISPLAY_OVERRIDES = {
    "CN-NM": "Inner Mongolia",  # NE: "Inner Mongol"
    "CN-XZ": "Tibet",  # NE: "Xizang"
}

# Natural Earth admin-1 attribute columns the generator reads.
ADMIN1_COLUMNS = ["adm0_a3", "iso_a2", "iso_3166_2", "name", "type_en", "region"]
