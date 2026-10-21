"""Path safety of the scenario viewer's run file view in the app."""

import json

import pytest

from steeloweb import views_viewer
from steeloweb.models import ModelRun


@pytest.mark.django_db
def test_run_file_view_serves_only_files_inside_a_listed_run_folder(client, tmp_path):
    """
    A file inside a finished app run's folder is served; a ../ path, an encoded .., a symlink inside the folder
    pointing outside it and an unknown run folder are 404.
    """
    run_dir = tmp_path / "model_outputs" / "run_1"
    (run_dir / "viewer").mkdir(parents=True)
    (run_dir / "viewer" / "run.json").write_text(json.dumps({"started": "2026-10-21T10:00:00"}))
    (tmp_path / "secret.txt").write_text("outside")
    (run_dir / "outside").symlink_to(tmp_path / "secret.txt")
    ModelRun.objects.create(state=ModelRun.RunState.FINISHED, output_directory=str(run_dir))
    # The page's manifest request finds the runs that the run file view then serves
    views_viewer.viewer_runs()

    assert client.get("/viewer/runs/run_1/viewer/run.json").status_code == 200
    assert client.get("/viewer/runs/run_1/../../secret.txt").status_code == 404
    assert client.get("/viewer/runs/run_1/%2e%2e/%2e%2e/secret.txt").status_code == 404
    assert client.get("/viewer/runs/run_1/outside").status_code == 404
    assert client.get("/viewer/runs/run_2/viewer/run.json").status_code == 404


@pytest.mark.django_db
def test_inputs_view_answers_no_listed_run_with_a_plain_404(client):
    """With no finished run listed yet, inputs.json is a plain-text 404 saying so, which View data shows, not a 500."""
    response = client.get("/viewer/inputs.json")

    assert response.status_code == 404
    assert response["Content-Type"] == "text/plain; charset=utf-8"
    assert response.content == b"no finished run yet"
