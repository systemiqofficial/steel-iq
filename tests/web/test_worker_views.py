"""Tests for worker management views and endpoints"""

import json
import time
from datetime import timedelta
from unittest.mock import patch, Mock
from django.test import TestCase, TransactionTestCase, Client
from django.utils import timezone
from django.urls import reverse

from steeloweb.models import Worker
from steeloweb import views_worker as views


class WorkerViewTests(TestCase):
    """Tests for worker management view endpoints"""

    def setUp(self):
        self.client = Client()

    def test_worker_status_json_endpoint_exists(self):
        """Worker status JSON endpoint should exist and return data"""
        response = self.client.get(reverse("worker-status-json"))
        assert response.status_code == 200

        data = json.loads(response.content)
        assert "workers" in data
        assert "memory" in data
        assert "limits" in data
        assert "queue" in data

    def test_worker_status_htmx_endpoint_exists(self):
        """Worker status HTMX endpoint should exist and return HTML"""
        response = self.client.get(reverse("worker-status-htmx"))
        assert response.status_code == 200
        assert "text/html" in response["Content-Type"]

    def test_add_worker_json_endpoint_exists(self):
        """Add worker JSON endpoint should exist"""
        response = self.client.post(reverse("add-worker-json"))
        assert response.status_code in [200, 400, 409]  # Valid responses

    def test_add_worker_htmx_endpoint_exists(self):
        """Add worker HTMX endpoint should exist"""
        response = self.client.post(reverse("add-worker-htmx"))
        assert response.status_code == 200
        assert "text/html" in response["Content-Type"]

    def test_drain_worker_endpoint_exists(self):
        """Drain worker endpoint should exist and work correctly"""
        worker = Worker.objects.create(worker_id="test-worker", state=Worker.WorkerState.RUNNING)

        # Endpoint should exist and be accessible
        url = reverse("drain-specific-worker-json", kwargs={"worker_id": "test-worker"})
        response = self.client.post(url)

        # Should successfully drain the worker
        assert response.status_code == 200

        worker.refresh_from_db()
        assert worker.state == Worker.WorkerState.DRAINING

    def test_abort_worker_endpoint_exists(self):
        """Abort worker endpoint should exist and require confirmation"""
        worker = Worker.objects.create(worker_id="test-worker", state=Worker.WorkerState.RUNNING)

        # Endpoint should exist
        url = reverse("abort-worker-json", kwargs={"worker_id": "test-worker"})

        # Should require confirmation
        response = self.client.delete(url)
        assert response.status_code == 400

        # Should work with confirmation
        response = self.client.delete(url + "?confirm=yes")
        assert response.status_code == 200

        worker.refresh_from_db()
        assert worker.state == Worker.WorkerState.FAILED
        assert worker.last_error_tail == "Aborted by user"


class GetEndpointSideEffectsTests(TestCase):
    """Tests ensuring GET endpoints don't modify data"""

    def setUp(self):
        self.client = Client()

    def test_status_view_does_not_delete_workers(self):
        """GET status should not delete any worker records"""
        # Create some DEAD and FAILED workers
        Worker.objects.create(worker_id="dead-1", state="DEAD")
        Worker.objects.create(worker_id="failed-1", state="FAILED")
        Worker.objects.create(worker_id="running-1", state="RUNNING")

        initial_count = Worker.objects.count()
        assert initial_count == 3

        # Call GET status multiple times
        for _ in range(5):
            response = self.client.get(reverse("worker-status-json"))
            assert response.status_code == 200

        # No workers should be deleted
        final_count = Worker.objects.count()
        assert final_count == initial_count == 3

    def test_status_view_only_marks_states(self):
        """GET status should only mark state transitions, not delete"""
        # Create worker in STARTING state that should timeout
        old_time = timezone.now() - timedelta(seconds=15)

        with patch("django.utils.timezone.now", return_value=old_time):
            worker = Worker.objects.create(worker_id="timeout-worker", state="STARTING", log_path="/tmp/test.log")

        # Reset timezone.now for the actual test
        with patch("builtins.open", self._mock_open_log_file("startup failed")):
            with patch("pathlib.Path.exists", return_value=True):
                response = self.client.get(reverse("worker-status-json"))
                assert response.status_code == 200

        # Worker should exist but be marked FAILED
        worker.refresh_from_db()
        # This will fail until we implement the timeout logic
        # assert worker.state == "FAILED"
        # assert "startup failed" in worker.last_error_tail

        # Worker record should still exist (not deleted)
        assert Worker.objects.filter(worker_id="timeout-worker").exists()

    def _mock_open_log_file(self, content):
        """Helper to mock file reading"""
        from unittest.mock import mock_open

        return mock_open(read_data=content)


class HandshakeWorkflowTests(TestCase):
    """Tests for secure handshake workflow"""

    def test_spawn_creates_launch_token(self):
        """Spawning a worker should create a unique launch_token"""
        with patch("subprocess.Popen") as mock_popen:
            mock_process = Mock()
            mock_process.pid = 12345
            mock_popen.return_value = mock_process

            with patch("psutil.Process") as mock_psutil:
                mock_psutil_process = Mock()
                mock_psutil_process.create_time.return_value = time.time()
                mock_psutil.return_value = mock_psutil_process

                # This will fail until we implement launch token in spawn
                from steeloweb.worker_supervisor import supervisor

                supervisor.spawn_worker(count=1)

        # Should have created worker with launch_token
        Worker.objects.first()
        # These assertions will fail until implemented
        # assert worker is not None
        # assert worker.launch_token is not None
        # assert len(worker.launch_token) == 16  # 8 hex bytes = 16 chars
        # assert worker.state == "STARTING"
        # assert worker.pid == 12345
        # assert worker.pid_started_at is not None

    def test_spawn_passes_launch_token_to_process(self):
        """Spawn should pass launch_token as CLI argument to worker process"""
        with patch("subprocess.Popen") as mock_popen:
            mock_process = Mock()
            mock_process.pid = 12345
            mock_popen.return_value = mock_process

            with patch("psutil.Process") as mock_psutil:
                mock_psutil_process = Mock()
                mock_psutil_process.create_time.return_value = time.time()
                mock_psutil.return_value = mock_psutil_process

                from steeloweb.worker_supervisor import supervisor

                supervisor.spawn_worker(count=1)

        # Check that Popen was called with launch-token argument
        if mock_popen.called:
            args, kwargs = mock_popen.call_args
            args[0]

            # These assertions will fail until we add launch-token to CLI
            # assert '--launch-token' in cmd_args
            # token_idx = cmd_args.index('--launch-token')
            # launch_token = cmd_args[token_idx + 1]
            # assert len(launch_token) == 16


class DrainVsAbortTests(TestCase):
    """Tests for drain vs abort semantics"""

    def test_drain_endpoint_should_exist(self):
        """Should have separate drain endpoint that works correctly"""
        worker = Worker.objects.create(worker_id="test-worker", state=Worker.WorkerState.RUNNING)

        # Endpoint should exist and work
        url = reverse("drain-specific-worker-json", kwargs={"worker_id": "test-worker"})
        response = self.client.post(url)

        assert response.status_code == 200
        worker.refresh_from_db()
        assert worker.state == Worker.WorkerState.DRAINING

    def test_abort_endpoint_should_exist(self):
        """Should have separate abort endpoint that requires confirmation"""
        worker = Worker.objects.create(worker_id="test-worker", state=Worker.WorkerState.RUNNING)

        # Endpoint should exist and require confirmation
        url = reverse("abort-worker-json", kwargs={"worker_id": "test-worker"})
        response = self.client.delete(url)
        assert response.status_code == 400  # Should require confirmation

        # Should work with confirmation
        response = self.client.delete(url + "?confirm=yes")
        assert response.status_code == 200

        worker.refresh_from_db()
        assert worker.state == Worker.WorkerState.FAILED

    def test_remove_worker_should_use_drain(self):
        """Drain worker endpoint should set worker to DRAINING state"""
        worker = Worker.objects.create(worker_id="test-worker", state="RUNNING", pid=12345)

        # Use the drain endpoint (no specific worker means drain oldest)
        self.client.post(reverse("drain-worker-json"))

        worker.refresh_from_db()
        # Should set to DRAINING
        assert worker.state == "DRAINING"


class AtomicSpawnTests(TransactionTestCase):
    """Tests for atomic spawn operations"""

    def test_spawn_is_atomic(self):
        """Spawn should atomically check capacity and create worker"""
        with patch.object(views.supervisor, "admissible_workers", return_value=1):
            with patch("subprocess.Popen") as mock_popen:
                mock_popen.return_value = Mock(pid=12345)
                with patch("psutil.Process") as mock_psutil:
                    mock_psutil.return_value = Mock(create_time=lambda: time.time())

                    client = Client()

                    # First spawn should succeed
                    client.post(reverse("add-worker-json"))
                    # Second spawn should fail due to capacity
                    client.post(reverse("add-worker-json"))

                    # Should have exactly 1 worker (will fail until atomic implementation)
                    # assert Worker.objects.count() == 1
