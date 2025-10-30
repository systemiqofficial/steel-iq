"""Tests for cancelling model runs with active workers."""

import uuid

from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from unittest.mock import patch

from steeloweb.models import ModelRun
from django_tasks.backends.database.models import DBTaskResult


class CancelModelRunTests(TestCase):
    """Ensure cancelling a run terminates the associated worker and task."""

    def setUp(self):
        self.client = Client()

    @patch("steeloweb.views_worker._abort_worker")
    def test_cancel_running_modelrun_aborts_worker_and_marks_task(self, mock_abort_worker):
        """Cancelling a RUNNING model run should kill the worker and flag the task as failed."""
        task_id = str(uuid.uuid4())
        worker_id = "worker-abc"

        modelrun = ModelRun.objects.create(
            name="Cancelable run",
            state=ModelRun.RunState.RUNNING,
            task_id=task_id,
        )

        DBTaskResult.objects.create(
            id=task_id,
            status="RUNNING",
            queue_name="default",
            task_path="steeloweb.tasks.run_simulation_task",
            args_kwargs={"args": [modelrun.pk], "kwargs": {}},
            worker_ids=[worker_id],
            enqueued_at=timezone.now(),
            started_at=timezone.now(),
        )

        response = self.client.post(reverse("cancel-modelrun", args=[modelrun.pk]))
        assert response.status_code == 302

        modelrun.refresh_from_db()
        assert modelrun.state == ModelRun.RunState.CANCELLED
        assert modelrun.task_id is None
        assert modelrun.error_message and "worker was terminated" in modelrun.error_message
        assert mock_abort_worker.call_args_list == [((worker_id,), {})]

        task = DBTaskResult.objects.get(id=task_id)
        assert task.status == "FAILED"
        assert task.worker_ids == []
        assert task.exception_class_path == "django_tasks.exceptions.TaskCancelled"
        assert task.traceback == "Task cancelled by user; worker terminated."
        assert task.finished_at is not None
