"""Country/area -> ISO3 normalization for GEM's raw unit-level tracker data.

GEM's `Country/area` columns hold free-text country names (e.g. "China",
"Russia"), not ISO3 codes. This module resolves those names to ISO3 using
pycountry, with a small manual override table for names pycountry doesn't
recognize as-is. Built by running the lookup once over the real distinct
`Country/area` values in gem-update-june-2026/Plant-level_data_...xlsx and
filling in the one exception found (Russia -> RUS; pycountry expects
"Russian Federation").
"""

import pycountry

GEM_COUNTRY_NAME_OVERRIDES: dict[str, str] = {
    "russia": "RUS",
}


def country_area_to_iso3(country_area: str) -> str:
    """Resolve a GEM `Country/area` value to its ISO3 code.

    Raises ValueError if the name is unresolvable via pycountry or the
    override table, so gaps surface loudly instead of silently mis-mapping.
    """
    name = str(country_area).strip()

    override = GEM_COUNTRY_NAME_OVERRIDES.get(name.lower())
    if override:
        return override

    try:
        return pycountry.countries.lookup(name).alpha_3
    except LookupError as e:
        raise ValueError(
            f"Could not resolve GEM Country/area {country_area!r} to an ISO3 code. "
            "Add an entry to GEM_COUNTRY_NAME_OVERRIDES."
        ) from e


def is_china(iso3: str) -> bool:
    """Whether an ISO3 code counts as "China" for the GEM-BF/external-BF exclusion
    filter. Deliberately excludes Hong Kong (HKG) and Taiwan (TWN) - the external
    Chinese BF dataset is mainland-only, so treating those as China here would
    incorrectly drop GEM's real HKG/TWN blast furnace units with no external
    replacement.
    """
    return iso3 == "CHN"
