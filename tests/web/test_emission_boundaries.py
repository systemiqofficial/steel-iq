"""Tests for the emissions boundaries a data preparation offers the run form."""

import json

import pytest

from steeloweb.models import DataPreparation


@pytest.mark.django_db
def test_get_emission_boundaries_returns_each_boundary_once_sorted_and_fails_without_the_file(tmp_path):
    """Each ``boundary`` of the prepared emission factors comes back once, sorted; a preparation without the file raises."""
    fixtures = tmp_path / "data" / "fixtures"
    fixtures.mkdir(parents=True)
    records = [
        {"boundary": boundary}
        for boundary in ("worldsteel_opt_credits", "rs-inspired", "worldsteel_opt_credits", "worldsteel_no_opt_credits")
    ]
    (fixtures / "technology_emission_factors.json").write_text(json.dumps(records))

    boundaries = DataPreparation(data_directory=str(tmp_path)).get_emission_boundaries()

    assert boundaries == ["rs-inspired", "worldsteel_no_opt_credits", "worldsteel_opt_credits"]
    with pytest.raises(FileNotFoundError):
        DataPreparation(data_directory=str(tmp_path / "unprepared")).get_emission_boundaries()
