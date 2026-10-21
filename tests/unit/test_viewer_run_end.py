"""Run-end step that writes the viewer files."""

import logging
import os
from datetime import datetime

from steelo import simulation
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
