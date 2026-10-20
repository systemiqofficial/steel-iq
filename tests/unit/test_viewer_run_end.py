"""Run-end step that writes the viewer files."""

import logging

from steelo import simulation


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
