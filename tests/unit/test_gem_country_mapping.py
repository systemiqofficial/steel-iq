"""Unit tests for GEM Country/area -> ISO3 normalization."""

import pytest

from steelo.adapters.dataprocessing.gem_country_mapping import country_area_to_iso3, is_china


# Distinct Country/area values actually present in
# gem-update-june-2026/Plant-level_data_Global_Iron_and_Steel_Tracker_June_2026_V1.xlsx
# (frozen here as a regression check).
REAL_GEM_COUNTRY_NAMES = [
    "Albania",
    "Algeria",
    "Angola",
    "Argentina",
    "Australia",
    "Austria",
    "Azerbaijan",
    "Bahrain",
    "Bangladesh",
    "Belarus",
    "Belgium",
    "Bolivia",
    "Bosnia and Herzegovina",
    "Brazil",
    "Bulgaria",
    "Cambodia",
    "Canada",
    "Chile",
    "China",
    "Czech Republic",
    "Egypt",
    "Ethiopia",
    "Finland",
    "France",
    "Germany",
    "Ghana",
    "Greece",
    "Guatemala",
    "Hong Kong",
    "Hungary",
    "India",
    "Indonesia",
    "Iran",
    "Iraq",
    "Italy",
    "Japan",
    "Kazakhstan",
    "Kenya",
    "Kuwait",
    "Latvia",
    "Libya",
    "Luxembourg",
    "Malaysia",
    "Mauritania",
    "Mexico",
    "Moldova",
    "Morocco",
    "Mozambique",
    "Myanmar",
    "Namibia",
    "Netherlands",
    "New Zealand",
    "Nigeria",
    "North Korea",
    "North Macedonia",
    "Norway",
    "Oman",
    "Pakistan",
    "Peru",
    "Philippines",
    "Poland",
    "Portugal",
    "Qatar",
    "Romania",
    "Russia",
    "Saudi Arabia",
    "Serbia",
    "Singapore",
    "Slovakia",
    "Slovenia",
    "South Africa",
    "South Korea",
    "Spain",
    "Sri Lanka",
    "Sweden",
    "Switzerland",
    "Syria",
    "Taiwan",
    "Thailand",
    "Trinidad and Tobago",
    "Tunisia",
    "Türkiye",
    "Uganda",
    "Ukraine",
    "United Arab Emirates",
    "United Kingdom",
    "United States",
    "Uzbekistan",
    "Venezuela",
    "Vietnam",
    "Zimbabwe",
]


class TestCountryAreaToIso3:
    @pytest.mark.parametrize("name", REAL_GEM_COUNTRY_NAMES)
    def test_all_real_gem_country_names_resolve(self, name):
        iso3 = country_area_to_iso3(name)
        assert len(iso3) == 3
        assert iso3.isupper()

    def test_china_resolves_to_chn(self):
        assert country_area_to_iso3("China") == "CHN"

    def test_russia_override(self):
        # pycountry.countries.lookup("Russia") fails - needs the manual override.
        assert country_area_to_iso3("Russia") == "RUS"

    def test_hong_kong_and_taiwan_resolve_to_own_codes(self):
        assert country_area_to_iso3("Hong Kong") == "HKG"
        assert country_area_to_iso3("Taiwan") == "TWN"

    def test_unresolvable_name_raises(self):
        with pytest.raises(ValueError):
            country_area_to_iso3("Not A Real Country")


class TestIsChina:
    def test_china_iso3(self):
        assert is_china("CHN") is True

    def test_hong_kong_and_taiwan_not_treated_as_china(self):
        # The external Chinese BF dataset is mainland-only; treating HKG/TWN as
        # China here would drop GEM's real HKG/TWN blast furnace units with no
        # external replacement.
        assert is_china("HKG") is False
        assert is_china("TWN") is False

    def test_other_country(self):
        assert is_china("USA") is False
