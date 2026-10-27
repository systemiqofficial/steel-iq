"""Run-end step that writes the viewer files."""

import json
import logging
import os
from datetime import datetime

from steelo import simulation
from steelo.adapters.repositories.in_memory_repository import InMemoryRepository
from steelo.simulation import SimulationConfig
from steelo.viewer import extract


def test_viewer_step_error_is_logged_and_leaves_no_run_json(tmp_path, caplog):
    """
    Tests that an error inside the viewer step is logged with its traceback instead of
    failing the run, and that the run folder then holds no run.json.
    """
    with caplog.at_level(logging.ERROR, logger="steelo.simulation"):
        simulation.write_viewer_files_after_run(tmp_path)

    errors = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "viewer files incomplete" in errors[0].getMessage()
    assert errors[0].exc_info is not None
    assert not (tmp_path / "viewer" / "run.json").exists()


def test_start_time_carries_its_utc_offset_and_round_trips_to_the_config_file_time(tmp_path):
    """
    Tests that the start time run.json records, the modification time of simulation_config.json, is written in UTC
    with its offset, so the app's worker (Django's UTC) and a CLI run (the machine's zone) write the same clock, and
    that it parses back to the file's modification time.
    """
    config_path = tmp_path / "simulation_config.json"
    config_path.write_text("{}")
    os.utime(config_path, (1792587061, 1792587061))

    started = extract.utc_time(config_path.stat().st_mtime)

    assert started == "2026-10-21T12:51:01+00:00"
    assert datetime.fromisoformat(started).timestamp() == config_path.stat().st_mtime


def test_regenerate_refuses_a_run_made_before_the_viewer_and_writes_nothing(tmp_path):
    """
    Tests that regenerate exits 1 and writes no viewer/ for a run whose simulation_config.json lacks run_name and
    chosen_scrap_scenario and stores geo_config as a repr string, naming each, and that a current config passes.
    """
    master = tmp_path / "master_input.xlsx"
    master.write_bytes(b"")
    run_dir = tmp_path / "sim_old"
    (run_dir / "plots" / "interactive").mkdir(parents=True)
    (run_dir / "plots" / "interactive" / "emissions.html").write_text("")
    (run_dir / "post_processed_2026.csv").write_text("")
    (run_dir / "preparation_metadata.json").write_text(json.dumps({"master_excel": str(master)}))
    (run_dir / "simulation_config.json").write_text(json.dumps({"geo_config": "GeoConfig(max_altitude=1500.0)"}))

    assert extract.regenerate([str(run_dir)]) == 1
    assert not (run_dir / "viewer").exists()
    assert extract.missing_inputs(run_dir) == [
        run_dir / "simulation_config.json:run_name",
        run_dir / "simulation_config.json:chosen_scrap_scenario",
        run_dir / "simulation_config.json:geo_config",
    ]

    config = SimulationConfig.for_testing(InMemoryRepository(), output_dir=run_dir)
    (run_dir / "simulation_config.json").write_text(json.dumps(simulation.config_as_json_dict(config)))
    assert extract.missing_inputs(run_dir) == []
