"""Helper functions for worker tests to work with the new architecture."""

from django.test import RequestFactory
from steeloweb import views_worker
from steeloweb.models import Worker


def spawn_test_worker():
    """Helper to spawn a worker in tests using the new view-based architecture.

    Returns the created worker ID or None if spawn failed.
    """
    factory = RequestFactory()
    request = factory.post("/htmx/workers/add/")

    # Mock the _spawn_worker_process to avoid actually launching processes in tests
    import steeloweb.views_worker

    original_spawn = steeloweb.views_worker._spawn_worker_process

    def mock_spawn(worker):
        # Just mark the worker as having a PID without launching a real process
        from datetime import datetime, timezone

        Worker.objects.filter(worker_id=worker.worker_id).update(pid=99999, pid_started_at=datetime.now(timezone.utc))

    try:
        steeloweb.views_worker._spawn_worker_process = mock_spawn
        response = views_worker.add_worker_json(request)

        if response.status_code == 200:
            # Find the most recently created worker
            worker = Worker.objects.order_by("-started_at").first()
            return worker.worker_id if worker else None
        return None
    finally:
        steeloweb.views_worker._spawn_worker_process = original_spawn


def perform_tick():
    """Helper to perform tick transitions in tests."""
    factory = RequestFactory()
    request = factory.post("/workers/tick/")
    return views_worker.workers_tick(request)
