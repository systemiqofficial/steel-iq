"""Tests for grid emissivity scenario discovery and filtering."""

import json
import tempfile
from pathlib import Path

import pytest

from steeloweb.models import DataPreparation


def _write_region_emissivity(tmpdir: str, records: list[dict]) -> None:
    """Write a region_emissivity.json fixture under {tmpdir}/data/fixtures/.

    Args:
        tmpdir: Temporary directory acting as a prepared data directory.
        records: List of record dicts in the schema produced by the data prep step.

    Notes:
        Mirrors the layout DataPreparation.get_region_emissivity_scenarios expects.
    """
    path = Path(tmpdir) / "data" / "fixtures" / "region_emissivity.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"root": records}, f)


@pytest.mark.django_db
def test_get_region_emissivity_scenarios_returns_distinct_forecast_scenarios():
    """Should return distinct scenario names with at least one year >= 2025."""
    with tempfile.TemporaryDirectory() as tmpdir:
        _write_region_emissivity(
            tmpdir,
            [
                {
                    "iso3": "POL",
                    "country_name": "Poland",
                    "scenario": "Business As Usual",
                    "grid_emissivity": {"2025": {"Electricity": 0.5}, "2050": {"Electricity": 0.3}},
                    "coke_emissivity": {},
                    "gas_emissivity": {},
                },
                {
                    "iso3": "DEU",
                    "country_name": "Germany",
                    "scenario": "Net Zero",
                    "grid_emissivity": {"2030": {"Electricity": 0.2}, "2050": {"Electricity": 0.0}},
                    "coke_emissivity": {},
                    "gas_emissivity": {},
                },
            ],
        )

        prep = DataPreparation(data_directory=tmpdir)
        scenarios = prep.get_region_emissivity_scenarios()

        assert scenarios == ["Business As Usual", "Net Zero"]


@pytest.mark.django_db
def test_get_region_emissivity_scenarios_excludes_pre_2025_only_scenarios():
    """A scenario whose latest year is < 2025 (e.g. 'Historical') is filtered out."""
    with tempfile.TemporaryDirectory() as tmpdir:
        _write_region_emissivity(
            tmpdir,
            [
                {
                    "iso3": "POL",
                    "country_name": "Poland",
                    "scenario": "Business As Usual",
                    "grid_emissivity": {"2025": {"Electricity": 0.5}},
                    "coke_emissivity": {},
                    "gas_emissivity": {},
                },
                {
                    "iso3": "POL",
                    "country_name": "Poland",
                    "scenario": "Historical",
                    "grid_emissivity": {"2010": {"Electricity": 0.7}, "2020": {"Electricity": 0.6}},
                    "coke_emissivity": {},
                    "gas_emissivity": {},
                },
            ],
        )

        prep = DataPreparation(data_directory=tmpdir)
        scenarios = prep.get_region_emissivity_scenarios()

        assert "Historical" not in scenarios
        assert scenarios == ["Business As Usual"]


@pytest.mark.django_db
def test_get_region_emissivity_scenarios_promotes_business_as_usual_to_first_position():
    """'Business As Usual' should sort first; remaining scenarios alphabetical."""
    with tempfile.TemporaryDirectory() as tmpdir:
        _write_region_emissivity(
            tmpdir,
            [
                {
                    "iso3": "USA",
                    "country_name": "USA",
                    "scenario": "Net Zero",
                    "grid_emissivity": {"2050": {"Electricity": 0.0}},
                    "coke_emissivity": {},
                    "gas_emissivity": {},
                },
                {
                    "iso3": "USA",
                    "country_name": "USA",
                    "scenario": "Business As Usual",
                    "grid_emissivity": {"2025": {"Electricity": 0.4}},
                    "coke_emissivity": {},
                    "gas_emissivity": {},
                },
                {
                    "iso3": "USA",
                    "country_name": "USA",
                    "scenario": "Aggressive Decarbonisation",
                    "grid_emissivity": {"2050": {"Electricity": 0.0}},
                    "coke_emissivity": {},
                    "gas_emissivity": {},
                },
            ],
        )

        prep = DataPreparation(data_directory=tmpdir)
        scenarios = prep.get_region_emissivity_scenarios()

        assert scenarios == ["Business As Usual", "Aggressive Decarbonisation", "Net Zero"]


@pytest.mark.django_db
def test_get_region_emissivity_scenarios_returns_empty_when_no_data_directory():
    """Should return empty list when data_directory is unset."""
    prep = DataPreparation(data_directory="")
    assert prep.get_region_emissivity_scenarios() == []


@pytest.mark.django_db
def test_get_region_emissivity_scenarios_returns_empty_when_file_missing():
    """Should return empty list when region_emissivity.json is absent."""
    with tempfile.TemporaryDirectory() as tmpdir:
        prep = DataPreparation(data_directory=tmpdir)
        assert prep.get_region_emissivity_scenarios() == []


@pytest.mark.django_db
def test_get_region_emissivity_scenarios_returns_empty_when_json_malformed():
    """Should swallow JSON errors and return empty list rather than raising."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "data" / "fixtures" / "region_emissivity.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not valid json")

        prep = DataPreparation(data_directory=tmpdir)
        assert prep.get_region_emissivity_scenarios() == []
