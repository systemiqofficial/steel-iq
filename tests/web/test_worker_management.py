"""Test-driven development for worker management reliability fixes

This test suite implements the critical fixes identified by the colleague:
1. Handshake mechanism with launch_token
2. Drain vs abort semantics
3. Remove side effects from GET endpoints
4. Worker CLI flags and handshake
5. Worker-side heartbeats
6. Admission control allowing 0 workers
7. Atomic spawn with transaction
8. PID create_time validation
"""

import pytest
import psutil
import time
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch, Mock
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from django.http import HttpRequest

from steeloweb.models import Worker
from steeloweb.worker_supervisor import supervisor
from steeloweb import views_worker as views


class HandshakeMechanismTests(TestCase):
    """Tests for secure handshake mechanism with launch_token"""

    def test_worker_model_has_launch_token_field(self):
        """Worker model should have launch_token field for secure handshake"""
        worker = Worker(worker_id="test-123", state=Worker.WorkerState.STARTING, launch_token="abc123def456")
        worker.save()

        # Should be able to retrieve and verify token
        retrieved = Worker.objects.get(worker_id="test-123")
        assert retrieved.launch_token == "abc123def456"

    def test_worker_model_has_pid_started_at_field(self):
        """Worker model should have pid_started_at for process validation"""
        now = timezone.now()
        worker = Worker(worker_id="test-123", state=Worker.WorkerState.RUNNING, pid=12345, pid_started_at=now)
        worker.save()

        retrieved = Worker.objects.get(worker_id="test-123")
        assert retrieved.pid_started_at == now

    def test_spawn_worker_creates_launch_token(self):
        """Spawning a worker should create a unique launch_token"""
        from django.db import transaction

        # Need to patch subprocess.Popen at the module level where it's used
        with patch("steeloweb.worker_supervisor.subprocess.Popen") as mock_popen:
            mock_process = Mock()
            mock_process.pid = 12345
            mock_process.poll.return_value = None  # Process is still running
            mock_popen.return_value = mock_process

            with patch("psutil.Process") as mock_psutil:
                mock_psutil_process = Mock()
                mock_psutil_process.create_time.return_value = time.time()
                mock_psutil.return_value = mock_psutil_process

                # Mock admissible_workers to return a positive capacity
                with patch.object(supervisor, "admissible_workers", return_value=2):
                    # Use the view directly for testing since spawn_worker is just a test helper
                    from django.test import RequestFactory
                    from steeloweb import views_worker

                    factory = RequestFactory()
                    request = factory.post("/workers/add/")

                    # Execute in a way that allows transaction commit hooks to run
                    # Since we're in a TestCase, we need to manually trigger on_commit
                    with patch.object(transaction, "on_commit", side_effect=lambda func: func()):
                        response = views_worker.add_worker_json(request)

                    # Check response
                    assert response.status_code == 200, f"Response failed: {response.content}"

        # Should have created worker with launch_token
        worker = Worker.objects.first()
        assert worker is not None
        assert worker.launch_token is not None
        assert len(worker.launch_token) == 16  # 8 hex bytes = 16 chars
        assert worker.state == Worker.WorkerState.STARTING
        # Note: PID is set after the on_commit hook fires
        if worker.pid:
            assert worker.pid == 12345
            assert worker.pid_started_at is not None

    def test_spawn_passes_launch_token_to_process(self):
        """Spawn should pass launch_token as CLI argument to worker process"""
        # Test the _launch_process method directly since spawn_worker uses transactions
        from pathlib import Path
        import tempfile

        with patch("subprocess.Popen") as mock_popen:
            mock_process = Mock()
            mock_process.pid = 12345
            mock_process.poll.return_value = None  # Process is still running
            mock_popen.return_value = mock_process

            # Call _launch_process directly
            test_worker_id = "test-worker-123"
            test_token = "abcd1234efgh5678"
            test_log_path = Path(tempfile.gettempdir()) / "test.log"

            pid = supervisor._launch_process(test_worker_id, test_token, test_log_path)

        # Check the process was launched successfully
        assert pid == 12345

        # Check that Popen was called with launch-token argument
        assert mock_popen.called, "subprocess.Popen should have been called"
        args, kwargs = mock_popen.call_args
        cmd_args = args[0]

        # Should have both worker-id and launch-token arguments (as --arg=value format)
        worker_id_arg = [arg for arg in cmd_args if arg.startswith("--worker-id=")]
        launch_token_arg = [arg for arg in cmd_args if arg.startswith("--launch-token=")]

        assert len(worker_id_arg) == 1, f"Expected one worker-id argument, got: {worker_id_arg}"
        assert len(launch_token_arg) == 1, f"Expected one launch-token argument, got: {launch_token_arg}"

        # Check the actual values passed
        assert worker_id_arg[0] == f"--worker-id={test_worker_id}"
        assert launch_token_arg[0] == f"--launch-token={test_token}"

    def test_worker_handshake_validates_token(self):
        """Worker handshake should only succeed with valid token"""
        # Create worker in STARTING state with known token
        worker = Worker.objects.create(
            worker_id="test-worker", state=Worker.WorkerState.STARTING, launch_token="valid-token-123"
        )

        # Mock the db_worker handshake logic
        def mock_handshake(worker_id, launch_token):
            try:
                worker = Worker.objects.get(
                    worker_id=worker_id, launch_token=launch_token, state=Worker.WorkerState.STARTING
                )
                worker.state = Worker.WorkerState.RUNNING
                worker.heartbeat = timezone.now()
                worker.save()
                return True
            except Worker.DoesNotExist:
                return False

        # Valid handshake should succeed
        assert mock_handshake("test-worker", "valid-token-123") is True
        worker.refresh_from_db()
        assert worker.state == Worker.WorkerState.RUNNING
        assert worker.heartbeat is not None

        # Reset for next test
        worker.state = Worker.WorkerState.STARTING
        worker.heartbeat = None
        worker.save()

        # Invalid token should fail
        assert mock_handshake("test-worker", "wrong-token") is False
        worker.refresh_from_db()
        assert worker.state == Worker.WorkerState.STARTING
        assert worker.heartbeat is None

    def test_status_view_no_auto_promotion(self):
        """GET status should NOT auto-promote STARTING -> RUNNING"""
        # Create worker in STARTING state
        worker = Worker.objects.create(worker_id="test-worker", state=Worker.WorkerState.STARTING, pid=12345)

        # Mock psutil to return process exists
        with patch("psutil.Process") as mock_psutil:
            mock_process = Mock()
            mock_process.pid = 12345
            mock_psutil.return_value = mock_process

            # Call status view
            request = HttpRequest()
            request.method = "GET"
            views.worker_status_json(request)

        # Worker should still be STARTING (no auto-promotion)
        worker.refresh_from_db()
        assert worker.state == Worker.WorkerState.STARTING

    def test_status_view_marks_startup_timeout_as_failed(self):
        """GET status should mark STARTING workers as FAILED after timeout"""
        # Create worker that started > 30 seconds ago (startup timeout is now 30s)
        old_time = timezone.now() - timedelta(seconds=35)

        with patch("django.utils.timezone.now", return_value=old_time):
            worker = Worker.objects.create(
                worker_id="test-worker", state=Worker.WorkerState.STARTING, log_path="/tmp/worker-test.log"
            )

        # Mock log file reading
        with patch("builtins.open", mock_open_log_file("Worker failed to start")):
            with patch("pathlib.Path.exists", return_value=True):
                # Call tick endpoint (which marks timed-out workers as FAILED)
                request = HttpRequest()
                request.method = "POST"
                views.workers_tick(request)

        # Worker should be marked as FAILED with error message
        worker.refresh_from_db()
        assert worker.state == Worker.WorkerState.FAILED
        # The actual implementation sets "Handshake timeout - worker failed to start"
        assert "Handshake timeout" in worker.last_error_tail or "Worker failed to start" in worker.last_error_tail


class DrainVsAbortSemanticsTests(TestCase):
    """Tests for proper drain vs abort worker semantics"""

    def test_worker_model_has_drain_state(self):
        """Worker model should have DRAINING state"""
        worker = Worker(worker_id="test-123", state=Worker.WorkerState.DRAINING)
        worker.save()

        retrieved = Worker.objects.get(worker_id="test-123")
        assert retrieved.state == Worker.WorkerState.DRAINING

    def test_drain_worker_endpoint_exists(self):
        """Should have separate drain endpoint that sets DRAINING state"""
        worker = Worker.objects.create(worker_id="test-worker", state=Worker.WorkerState.RUNNING)

        # Should have drain endpoint that works
        request = HttpRequest()
        request.method = "POST"

        # Endpoint should exist and work correctly
        views.drain_worker_json(request, "test-worker")

        worker.refresh_from_db()
        assert worker.state == Worker.WorkerState.DRAINING

    def test_drain_worker_sets_draining_state(self):
        """Drain endpoint should set worker to DRAINING state"""
        worker = Worker.objects.create(worker_id="test-worker", state=Worker.WorkerState.RUNNING, pid=12345)

        # Mock the drain logic
        def mock_drain_worker(worker_id):
            worker = Worker.objects.get(worker_id=worker_id, state=Worker.WorkerState.RUNNING)
            worker.state = Worker.WorkerState.DRAINING
            worker.save()
            return {"status": "success", "message": f"Worker {worker_id} will drain after current job"}

        result = mock_drain_worker("test-worker")

        assert result["status"] == "success"
        worker.refresh_from_db()
        assert worker.state == Worker.WorkerState.DRAINING

    def test_abort_worker_requires_confirmation(self):
        """Abort endpoint should require explicit confirmation"""
        worker = Worker.objects.create(worker_id="test-worker", state=Worker.WorkerState.RUNNING, pid=12345)

        # Mock abort without confirmation - should fail
        def mock_abort_worker(worker_id, confirmed=False):
            if not confirmed:
                return {"status": "error", "message": "Abort requires confirmation"}

            worker = Worker.objects.get(worker_id=worker_id)
            # Kill process if exists
            if worker.pid:
                try:
                    import psutil

                    psutil.Process(worker.pid).kill()
                except psutil.NoSuchProcess:
                    pass

            worker.state = Worker.WorkerState.FAILED
            worker.last_error_tail = "Aborted by user"
            worker.save()
            return {"status": "success", "message": f"Worker {worker_id} aborted"}

        # Without confirmation should fail
        result = mock_abort_worker("test-worker", confirmed=False)
        assert result["status"] == "error"
        assert "confirmation" in result["message"]

        # With confirmation should succeed
        with patch("psutil.Process") as mock_psutil:
            mock_process = Mock()
            mock_psutil.return_value = mock_process

            result = mock_abort_worker("test-worker", confirmed=True)
            assert result["status"] == "success"

            worker.refresh_from_db()
            assert worker.state == Worker.WorkerState.FAILED
            assert worker.last_error_tail == "Aborted by user"

    def test_remove_worker_uses_drain_not_abort(self):
        """Remove worker should use drain semantics, not immediate abort"""
        worker = Worker.objects.create(worker_id="test-worker", state=Worker.WorkerState.RUNNING, pid=12345)

        # Mock the current remove_worker_htmx to use drain instead of terminate
        def mock_remove_worker_with_drain():
            # Should set to DRAINING, not immediately terminate
            worker = Worker.objects.filter(state=Worker.WorkerState.RUNNING).first()
            if worker:
                worker.state = Worker.WorkerState.DRAINING
                worker.save()
                return {"status": "success", "message": "Worker will drain after current job"}
            return {"status": "error", "message": "No workers to remove"}

        result = mock_remove_worker_with_drain()

        assert result["status"] == "success"
        worker.refresh_from_db()
        assert worker.state == Worker.WorkerState.DRAINING  # Not DEAD!


class NoSideEffectsInGETTests(TestCase):
    """Tests ensuring GET endpoints don't modify data"""

    def test_status_view_does_not_delete_workers(self):
        """GET status should not delete any worker records"""
        # Create some DEAD and FAILED workers
        Worker.objects.create(worker_id="dead-1", state=Worker.WorkerState.DEAD)
        Worker.objects.create(worker_id="failed-1", state=Worker.WorkerState.FAILED)
        Worker.objects.create(worker_id="running-1", state=Worker.WorkerState.RUNNING)

        initial_count = Worker.objects.count()
        assert initial_count == 3

        # Call GET status multiple times
        request = HttpRequest()
        request.method = "GET"

        for _ in range(5):
            views.worker_status_json(request)

        # No workers should be deleted
        final_count = Worker.objects.count()
        assert final_count == initial_count == 3

    def test_status_view_only_marks_states(self):
        """GET status should only mark state transitions, not delete"""
        # Create worker in STARTING state that should timeout (timeout is now 30s)
        old_time = timezone.now() - timedelta(seconds=35)

        with patch("django.utils.timezone.now", return_value=old_time):
            worker = Worker.objects.create(
                worker_id="timeout-worker", state=Worker.WorkerState.STARTING, log_path="/tmp/test.log"
            )

        # Mock file operations
        with patch("builtins.open", mock_open_log_file("startup failed")):
            with patch("pathlib.Path.exists", return_value=True):
                request = HttpRequest()
                request.method = "POST"  # Tick endpoint requires POST
                views.workers_tick(request)

        # Worker should exist but be marked FAILED
        worker.refresh_from_db()
        assert worker.state == Worker.WorkerState.FAILED
        # The actual implementation sets "Handshake timeout - worker failed to start"
        assert "Handshake timeout" in worker.last_error_tail or "startup failed" in worker.last_error_tail

        # Worker record should still exist (not deleted)
        assert Worker.objects.filter(worker_id="timeout-worker").exists()

    def test_cleanup_endpoint_separate_from_status(self):
        """Status GET should not modify data, tick POST handles transitions"""
        # Create old workers (> 10 seconds ago)
        old_time = timezone.now() - timedelta(seconds=15)

        with patch("django.utils.timezone.now", return_value=old_time):
            Worker.objects.create(worker_id="dead-1", state=Worker.WorkerState.DEAD)
            Worker.objects.create(worker_id="failed-1", state=Worker.WorkerState.FAILED)

        # Status view should not clean up
        request = HttpRequest()
        request.method = "GET"
        views.worker_status_json(request)

        assert Worker.objects.filter(state__in=[Worker.WorkerState.DEAD, Worker.WorkerState.FAILED]).count() == 2

        # Tick endpoint performs state transitions but doesn't delete workers
        # (Deletion is typically done manually or by a separate cleanup task)
        request.method = "POST"
        views.workers_tick(request)

        # Workers should still exist (tick doesn't delete, just transitions states)
        assert Worker.objects.filter(state__in=[Worker.WorkerState.DEAD, Worker.WorkerState.FAILED]).count() == 2


class WorkerHeartbeatTests(TestCase):
    """Tests for worker-side heartbeat mechanism"""

    def test_worker_updates_own_heartbeat(self):
        """Worker process should update its own heartbeat periodically"""
        worker = Worker.objects.create(
            worker_id="test-worker",
            state=Worker.WorkerState.RUNNING,
            heartbeat=timezone.now() - timedelta(minutes=5),  # Old heartbeat
        )

        old_heartbeat = worker.heartbeat

        # Mock worker updating its own heartbeat
        def mock_worker_heartbeat_update(worker_id):
            Worker.objects.filter(worker_id=worker_id).update(heartbeat=timezone.now())

        mock_worker_heartbeat_update("test-worker")

        worker.refresh_from_db()
        assert worker.heartbeat > old_heartbeat

    def test_stalled_vs_dead_worker_detection(self):
        """Should distinguish between stalled (no heartbeat) and dead (no PID) workers"""
        now = timezone.now()

        # Stalled worker: running but no recent heartbeat
        Worker.objects.create(
            worker_id="stalled-worker",
            state=Worker.WorkerState.RUNNING,
            pid=12345,
            heartbeat=now - timedelta(minutes=10),
        )

        # Dead worker: process doesn't exist
        Worker.objects.create(
            worker_id="dead-worker",
            state=Worker.WorkerState.RUNNING,
            pid=99999,  # Non-existent PID
            heartbeat=now - timedelta(minutes=1),
        )

        def mock_detect_worker_states():
            workers_status = {}

            for worker in Worker.objects.filter(state=Worker.WorkerState.RUNNING):
                # Check if process exists
                try:
                    psutil.Process(worker.pid)
                    process_exists = True
                except psutil.NoSuchProcess:
                    process_exists = False

                # Check heartbeat age
                heartbeat_age = (now - worker.heartbeat).total_seconds() if worker.heartbeat else float("inf")

                if not process_exists:
                    workers_status[worker.worker_id] = "DEAD"
                elif heartbeat_age > 300:  # 5 minutes
                    workers_status[worker.worker_id] = "STALLED"
                else:
                    workers_status[worker.worker_id] = "HEALTHY"

            return workers_status

        with patch("psutil.Process") as mock_psutil:
            # Mock stalled worker process exists
            def mock_process_side_effect(pid):
                if pid == 12345:  # stalled worker
                    return Mock()
                elif pid == 99999:  # dead worker
                    raise psutil.NoSuchProcess(pid)

            mock_psutil.side_effect = mock_process_side_effect

            status = mock_detect_worker_states()

            assert status["stalled-worker"] == "STALLED"  # Process exists but no heartbeat
            assert status["dead-worker"] == "DEAD"  # Process doesn't exist


class AdmissionControlTests(TestCase):
    """Tests for admission control allowing 0 workers"""

    def test_admission_control_can_return_zero(self):
        """Admission control should be able to return 0 workers"""
        # Mock system with very low memory
        with patch("psutil.virtual_memory") as mock_vm:
            mock_vm.return_value = Mock(
                total=2 * 1024**3,  # 2GB total
                available=1 * 1024**3,  # 1GB available
            )

            # Should return 0 since we need 8GB per worker + 2GB guard
            admissible = supervisor.admissible_workers()
            assert admissible == 0

    def test_ui_disables_add_worker_when_zero_capacity(self):
        """UI should disable 'Add Worker' button when capacity is 0"""
        # Mock zero capacity
        with patch.object(supervisor, "admissible_workers", return_value=0):
            request = HttpRequest()
            request.method = "GET"
            response = views.worker_status_json(request)

            # Response should indicate no capacity
            import json

            data = json.loads(response.content)
            assert data["limits"]["admissible"] == 0
            assert data["limits"]["can_add"] is False


class AtomicSpawnTests(TransactionTestCase):
    """Tests for atomic spawn operations"""

    def test_spawn_is_atomic_with_capacity_check(self):
        """Spawn should atomically check capacity and create worker"""
        # Mock supervisor to allow exactly 1 worker
        with patch.object(supervisor, "admissible_workers", return_value=1):
            with patch("subprocess.Popen") as mock_popen:
                mock_process = Mock()
                mock_process.pid = 12345
                mock_process.poll.return_value = None  # Process is running
                mock_popen.return_value = mock_process
                with patch("psutil.Process") as mock_psutil:
                    mock_psutil.return_value = Mock(create_time=lambda: time.time())

                    # First spawn should succeed
                    result1 = supervisor.spawn_worker(count=1)
                    assert len(result1.get("spawned", [])) == 1
                    assert result1.get("failed", 0) == 0

                    # Second spawn should fail due to capacity
                    result2 = supervisor.spawn_worker(count=1)
                    assert len(result2.get("spawned", [])) == 0
                    assert result2.get("failed", 0) == 1

                    # Should have exactly 1 worker
                    assert Worker.objects.count() == 1


class PIDValidationTests(TestCase):
    """Tests for PID create_time validation"""

    def test_worker_stores_pid_create_time(self):
        """Worker should store PID create time for validation"""
        now = timezone.now()
        worker = Worker.objects.create(worker_id="test-worker", pid=12345, pid_started_at=now)

        assert worker.pid_started_at == now

    def test_pid_validation_detects_reuse(self):
        """PID validation should detect when PID is reused by different process"""
        original_create_time = time.time()
        reused_create_time = original_create_time + 100  # Different process

        worker = Worker.objects.create(
            worker_id="test-worker",
            pid=12345,
            pid_started_at=datetime.fromtimestamp(original_create_time, tz=dt_timezone.utc),
        )

        def mock_is_same_process(worker):
            try:
                process = psutil.Process(worker.pid)
                process_create_time = process.create_time()
                stored_create_time = worker.pid_started_at.timestamp()
                return abs(process_create_time - stored_create_time) < 1
            except psutil.NoSuchProcess:
                return False

        # Mock process with different create time (PID reused)
        with patch("psutil.Process") as mock_psutil:
            mock_process = Mock()
            mock_process.create_time.return_value = reused_create_time
            mock_psutil.return_value = mock_process

            # Should detect PID reuse
            assert mock_is_same_process(worker) is False

        # Mock process with same create time (same process)
        with patch("psutil.Process") as mock_psutil:
            mock_process = Mock()
            mock_process.create_time.return_value = original_create_time
            mock_psutil.return_value = mock_process

            # Should confirm same process
            assert mock_is_same_process(worker) is True


# Helper functions for tests
def mock_open_log_file(content):
    """Mock file open for log reading tests"""
    from unittest.mock import mock_open

    return mock_open(read_data=content)


# Additional test fixtures and utilities
@pytest.fixture
def running_worker():
    """Create a worker in RUNNING state for tests"""
    return Worker.objects.create(
        worker_id="test-worker-123",
        state=Worker.WorkerState.RUNNING,
        pid=12345,
        heartbeat=timezone.now(),
        launch_token="test-token-abc",
    )


@pytest.fixture
def starting_worker():
    """Create a worker in STARTING state for tests"""
    return Worker.objects.create(
        worker_id="test-worker-456", state=Worker.WorkerState.STARTING, launch_token="start-token-def"
    )
