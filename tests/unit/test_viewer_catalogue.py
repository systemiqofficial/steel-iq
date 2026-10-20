"""Catalogue of local runs for the scenario viewer."""

import dataclasses
import json

from steelo import simulation
from steelo.adapters.repositories.in_memory_repository import InMemoryRepository
from steelo.capacity_policy.config import CapacityPolicyConfig
from steelo.simulation import GeoConfig, SimulationConfig
from steelo.viewer import catalogue


def write_run(run_dir, name, started, master="master_input.xlsx", master_source="default", **overrides):
    """Write a fake run folder holding simulation_config.json and a hand-written viewer/run.json."""
    config = SimulationConfig.for_testing(InMemoryRepository(), output_dir=run_dir, run_name=name, **overrides)
    (run_dir / "simulation_config.json").write_text(json.dumps(simulation.config_as_json_dict(config)))
    (run_dir / "viewer").mkdir()
    run = {
        "schema": 1,
        "name": name,
        "folder": run_dir.name,
        "made_in": "cli",
        "started": started,
        "ended": started,
        "steelo_version": "2.0.0",
        "input_data": {
            "master": master,
            "master_source": master_source,
            "master_name": None,
            "preparation_name": None,
        },
        "pages": ["emissions.html"],
        "files": [],
    }
    (run_dir / "viewer" / "run.json").write_text(json.dumps(run))
    return run_dir


def test_catalogue_dimensions_are_the_differing_settings_input_data_seed_and_run(tmp_path):
    """
    Tests that four runs (two differing in one setting, one on a custom master, one a copy of another under the
    same name) give exactly the filters input data, that setting, seed and run, with a distinct run value for the
    copy; that a run differing only in an excluded field adds none; and that the run detail flags the changed
    setting as changed from the default and varying.
    """
    runs = [
        write_run(tmp_path / "sim_1", "base", "2026-10-20T10:00:00"),
        write_run(
            tmp_path / "sim_2",
            "h20",
            "2026-10-20T10:01:00",
            geo_config=GeoConfig(hydrogen_ceiling_percentile=20.0),
        ),
        write_run(
            tmp_path / "sim_3",
            "custom",
            "2026-10-20T10:02:00",
            master="my_master.xlsx",
            master_source="custom",
        ),
        write_run(tmp_path / "sim_4", "base", "2026-10-20T10:03:00"),
    ]
    plots_only = write_run(
        tmp_path / "sim_5",
        "base_3",
        "2026-10-20T10:04:00",
        plots_dir=tmp_path / "elsewhere",
    )

    local = catalogue.build_manifest(runs, with_made_in=False)["sets"][0]
    with_plots_only = catalogue.build_manifest(runs + [plots_only], with_made_in=False)["sets"][0]

    keys = [dimension["key"] for dimension in local["dimensions"]]
    assert keys == ["input_data", "geo_config.hydrogen_ceiling_percentile", "seed", "run"]
    assert [dimension["key"] for dimension in with_plots_only["dimensions"]] == keys
    run_values = {run["dir"]: run["dims"]["run"] for run in local["runs"]}
    assert run_values["sim_1"] != run_values["sim_4"]
    settings = {run["dir"]: {s["key"]: s for s in run["settings"]} for run in local["runs"]}
    percentile = "geo_config.hydrogen_ceiling_percentile"
    assert settings["sim_2"][percentile]["changed"] is True
    assert settings["sim_2"][percentile]["varies"] is True
    assert settings["sim_1"][percentile]["changed"] is False


def test_excluded_settings_name_only_real_config_fields():
    """
    Tests that every entry of the exclude list is a SimulationConfig, GeoConfig or CapacityPolicyConfig field,
    so a renamed or removed field cannot leave a stale entry behind.
    """
    real = (
        {f.name for f in dataclasses.fields(SimulationConfig)}
        | {f"geo_config.{f.name}" for f in dataclasses.fields(GeoConfig)}
        | {f"capacity_policy.{f.name}" for f in dataclasses.fields(CapacityPolicyConfig)}
    )

    assert catalogue.EXCLUDED_SETTINGS <= real
