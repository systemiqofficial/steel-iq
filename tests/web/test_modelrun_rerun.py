import types
from pathlib import Path
from unittest.mock import Mock

import pytest
from django.core.files.base import ContentFile
from django.urls import reverse
from django.utils import timezone

from steeloweb.models import ModelRun, ResultImages, SimulationPlot


@pytest.fixture(autouse=True)
def _no_worker_check(monkeypatch):
    """Allow rerun tests to bypass worker capacity checks."""

    monkeypatch.setattr(
        "steeloweb.views_worker.check_worker_availability_for_simulation",
        lambda: {"status": "ok", "message": "", "data": {}},
    )


@pytest.mark.django_db
def test_detail_shows_rerun_button_for_exception_failure(client):
    modelrun = ModelRun.objects.create(
        state=ModelRun.RunState.FAILED,
        error_message="Out of disk space\n\nTraceback:\nOSError: no space left",
    )

    response = client.get(reverse("modelrun-detail", args=[modelrun.pk]))
    assert response.status_code == 200

    content = response.content.decode("utf-8")
    assert "Rerun Simulation" in content


@pytest.mark.django_db
def test_detail_hides_rerun_button_for_validation_failure(client):
    modelrun = ModelRun.objects.create(
        state=ModelRun.RunState.FAILED,
        error_message="Legacy technology fields present: bf_capacity. Reconfigure this run with the new technology table.",
    )

    response = client.get(reverse("modelrun-detail", args=[modelrun.pk]))
    assert response.status_code == 200

    content = response.content.decode("utf-8")
    assert "Rerun Simulation" not in content


@pytest.mark.django_db
def test_rerun_resets_state_and_enqueues_task(client, monkeypatch):
    modelrun = ModelRun.objects.create(
        state=ModelRun.RunState.FAILED,
        error_message="RuntimeError\n\nTraceback:\nValueError: boom",
        finished_at=timezone.now(),
        results={"foo": "bar"},
        progress={"years": [{"start_year": 2020, "end_year": 2030, "current_year": 2025}]},
        task_id="stale-task",
    )

    fake_result = types.SimpleNamespace(id="new-task-id")
    enqueue_stub = Mock(return_value=fake_result)
    monkeypatch.setattr("steeloweb.views.run_simulation_task", types.SimpleNamespace(enqueue=enqueue_stub))

    response = client.post(reverse("rerun-modelrun", args=[modelrun.pk]))
    assert response.status_code == 302
    assert response.url == reverse("modelrun-detail", args=[modelrun.pk])

    modelrun.refresh_from_db()
    assert modelrun.state == ModelRun.RunState.RUNNING
    assert modelrun.task_id == "new-task-id"
    assert modelrun.error_message == ""
    assert modelrun.results == {}
    assert modelrun.progress == {}
    assert modelrun.finished_at is None

    enqueue_stub.assert_called_once_with(modelrun.pk)


@pytest.mark.django_db
def test_detail_shows_rerun_button_for_cancelled_run(client):
    modelrun = ModelRun.objects.create(
        state=ModelRun.RunState.CANCELLED,
        error_message="Simulation was canceled by user. The background worker was terminated.",
    )

    response = client.get(reverse("modelrun-detail", args=[modelrun.pk]))
    assert response.status_code == 200
    assert "Rerun Simulation" in response.content.decode("utf-8")


@pytest.mark.django_db
def test_rerun_from_cancelled_resets_and_enqueues(client, monkeypatch):
    modelrun = ModelRun.objects.create(
        state=ModelRun.RunState.CANCELLED,
        error_message="Simulation was canceled by user. The background worker was terminated.",
        finished_at=timezone.now(),
        results={"foo": "bar"},
        progress={"years": [{"start_year": 2020, "end_year": 2030, "current_year": 2025}]},
    )

    fake_result = types.SimpleNamespace(id="fresh-task")
    enqueue_stub = Mock(return_value=fake_result)
    monkeypatch.setattr("steeloweb.views.run_simulation_task", types.SimpleNamespace(enqueue=enqueue_stub))

    response = client.post(reverse("rerun-modelrun", args=[modelrun.pk]))
    assert response.status_code == 302
    assert response.url == reverse("modelrun-detail", args=[modelrun.pk])

    modelrun.refresh_from_db()
    assert modelrun.state == ModelRun.RunState.RUNNING
    assert modelrun.task_id == "fresh-task"
    assert modelrun.error_message == ""
    assert modelrun.results == {}
    assert modelrun.progress == {}
    assert modelrun.finished_at is None

    enqueue_stub.assert_called_once_with(modelrun.pk)


@pytest.mark.django_db
def test_reset_for_rerun_removes_previous_outputs(temp_media_root):
    """A reset removes the output folder, result CSV and plot and image rows, so a rerun shows only its own results."""
    output_dir = Path(temp_media_root) / "model_outputs" / "run_old"
    output_dir.mkdir(parents=True)
    (output_dir / "old.csv").write_text("stale")
    modelrun = ModelRun.objects.create(
        state=ModelRun.RunState.FAILED,
        output_directory=str(output_dir),
        result_csv=ContentFile(b"stale", name="old_results.csv"),
    )
    SimulationPlot.objects.create(
        modelrun=modelrun,
        plot_type=SimulationPlot.PlotType.OTHER,
        title="Old plot",
        image=ContentFile(b"png", name="old.png"),
    )
    ResultImages.objects.create(modelrun=modelrun)

    modelrun.reset_for_rerun()

    modelrun.refresh_from_db()
    assert not modelrun.result_csv
    assert modelrun.output_directory == ""
    assert not output_dir.exists()
    assert not modelrun.simulation_plots.exists()
    assert not modelrun.result_images.exists()
