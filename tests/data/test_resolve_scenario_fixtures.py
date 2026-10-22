"""The one resolver that finds a run's demand centres and suppliers files in a preparation."""

import json

import pytest

from steelo.data import path_resolver


def _write_index(fixtures, index):
    """Write ``scenarios.json`` and an empty file at every path it names."""
    for kind_paths in index.values():
        for relative in kind_paths.values():
            (fixtures / relative).parent.mkdir(parents=True, exist_ok=True)
            (fixtures / relative).write_text("[]")
    (fixtures / "scenarios.json").write_text(json.dumps(index))


def test_resolve_scenario_fixtures_returns_the_files_the_index_names(tmp_path):
    """The demand and scrap names each resolve to the file the index maps them to, never to a folder by name."""
    _write_index(
        tmp_path,
        {
            "demand": {"BAU": "demand_scenarios/bau/demand_centers.json", "High growth": "demand_scenarios/x/a.json"},
            "scrap": {"BAU": "scrap_scenarios/bau/suppliers.json", "High recycling": "scrap_scenarios/y/b.json"},
        },
    )

    demand, suppliers = path_resolver.resolve_scenario_fixtures(tmp_path, "High growth", "BAU")

    assert demand == tmp_path / "demand_scenarios" / "x" / "a.json"
    assert suppliers == tmp_path / "scrap_scenarios" / "bau" / "suppliers.json"


def test_resolve_scenario_fixtures_without_an_index_asks_to_prepare_again(tmp_path):
    """A preparation made before run-time scenarios holds no index, and the error says to prepare the master again."""
    (tmp_path / "demand_centers.json").write_text("[]")

    with pytest.raises(ValueError, match="prepared before run-time scenarios; prepare this master again"):
        path_resolver.resolve_scenario_fixtures(tmp_path, "BAU", "BAU")


def test_resolve_scenario_fixtures_with_an_unknown_name_lists_the_available_names(tmp_path):
    """A scenario the index lacks raises, naming the scenarios of that kind the preparation does hold."""
    _write_index(
        tmp_path,
        {
            "demand": {"BAU": "demand_scenarios/bau/demand_centers.json"},
            "scrap": {"BAU": "scrap_scenarios/bau/suppliers.json", "Low": "scrap_scenarios/low/suppliers.json"},
        },
    )

    with pytest.raises(ValueError, match=r"No scrap scenario 'Nope' in .*; available: \['BAU', 'Low'\]"):
        path_resolver.resolve_scenario_fixtures(tmp_path, "BAU", "Nope")


def test_resolve_scenario_fixtures_with_a_missing_file_raises(tmp_path):
    """A file the index names but the folder lacks raises, since the JSON repositories would read it as empty."""
    _write_index(
        tmp_path,
        {
            "demand": {"BAU": "demand_scenarios/bau/demand_centers.json"},
            "scrap": {"BAU": "scrap_scenarios/bau/suppliers.json"},
        },
    )
    (tmp_path / "scrap_scenarios" / "bau" / "suppliers.json").unlink()

    with pytest.raises(FileNotFoundError, match="suppliers.json"):
        path_resolver.resolve_scenario_fixtures(tmp_path, "BAU", "BAU")
