"""Path safety of the scenario viewer's local server."""

from steelo.viewer import server


def test_resolve_run_file_refuses_paths_outside_the_run_folder(tmp_path):
    """
    Tests that a run file URL resolves to the file inside its run folder, and that a ../ path, a symlink pointing
    outside the run folder and an unknown run folder are refused.
    """
    run_dir = tmp_path / "output" / "sim_1"
    (run_dir / "viewer").mkdir(parents=True)
    (run_dir / "viewer" / "run.json").write_text("{}")
    (tmp_path / "output" / "other").write_text("outside")
    (run_dir / "outside").symlink_to(tmp_path / "output" / "other")
    runs = {"sim_1": run_dir}

    assert server.resolve_run_file(runs, "/runs/sim_1/viewer/run.json") == run_dir / "viewer" / "run.json"
    assert server.resolve_run_file(runs, "/runs/sim_1/../other") is None
    assert server.resolve_run_file(runs, "/runs/sim_1/outside") is None
    assert server.resolve_run_file(runs, "/runs/nope/viewer/run.json") is None
