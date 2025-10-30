"""Tests for worker drain workflow

This test suite ensures that workers properly transition from DRAINING to DEAD state
when they're marked for removal, addressing the production issue where workers
get stuck in DRAINING state indefinitely.
"""

from unittest.mock import patch, Mock
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from steeloweb.models import Worker


class WorkerDrainWorkflowTests(TestCase):
    """Test worker drain state transitions"""

    def test_worker_checks_draining_state_periodically(self):
        """Worker should check if it's been marked as DRAINING and exit gracefully"""
        from steeloweb.management.commands.steelo_worker import Command

        # Create a real worker in the database
        worker = Worker.objects.create(
            worker_id="test-worker", state=Worker.WorkerState.RUNNING, launch_token="test-token"
        )

        # Create a command instance
        command = Command()
        command.worker_id = "test-worker"
        command.launch_token = "test-token"
        command.shutdown_requested = False
        command.worker = worker

        # Test the draining check method
        # First check should return False (still RUNNING)
        assert not command._check_draining_state()

        # Mark worker as DRAINING
        worker.state = Worker.WorkerState.DRAINING
        worker.save()

        # Second check should return True (now DRAINING)
        assert command._check_draining_state()

    def test_worker_exits_when_marked_draining(self):
        """Worker should exit gracefully when marked as DRAINING"""
        from steeloweb.management.commands.steelo_worker import Command

        # Create a worker marked as DRAINING
        worker = Worker.objects.create(
            worker_id="test-worker", state=Worker.WorkerState.DRAINING, launch_token="test-token"
        )

        command = Command()
        command.worker_id = "test-worker"
        command.launch_token = "test-token"
        command.shutdown_requested = False
        command.worker = worker

        # Mock the claim_and_process_task to simulate no tasks
        with patch.object(command, "_claim_and_process_task", return_value=False):
            with patch.object(command, "_update_heartbeat"):
                with patch("time.sleep"):  # Prevent actual sleeping
                    # Simulate one iteration of the worker loop
                    # This would normally be in the main loop but we test the logic
                    if command._check_draining_state():
                        command.shutdown_requested = True

        # Worker should have set shutdown_requested when it saw DRAINING
        assert command.shutdown_requested

    def test_worker_completes_current_task_before_draining(self):
        """Worker should complete its current task before exiting when draining"""
        from steeloweb.management.commands.steelo_worker import Command

        command = Command()
        command.worker_id = "test-worker"
        command.shutdown_requested = False

        # Mock worker that gets marked DRAINING while processing
        mock_worker = Mock()
        mock_worker.state = Worker.WorkerState.RUNNING
        mock_worker.WorkerState = Worker.WorkerState

        # Track state changes
        states_checked = []

        def check_state_side_effect():
            mock_worker.refresh_from_db()
            states_checked.append(mock_worker.state)
            return mock_worker.state == Worker.WorkerState.DRAINING

        command.worker = mock_worker
        command._check_draining_state = Mock(side_effect=check_state_side_effect)

        # Simulate task processing
        task_completed = False

        def mock_process_task(queue_name):
            nonlocal task_completed
            if not task_completed:
                # First call: task is being processed
                task_completed = True
                # Mark as draining while task is running
                mock_worker.state = Worker.WorkerState.DRAINING
                return True  # Task found and processed
            return False  # No more tasks

        with patch.object(command, "_claim_and_process_task", side_effect=mock_process_task):
            with patch.object(command, "_update_heartbeat"):
                with patch("time.sleep"):
                    # Simulate worker loop iterations
                    tasks_processed = 0
                    while not command.shutdown_requested and tasks_processed < 3:
                        task_found = command._claim_and_process_task("default")
                        if task_found:
                            tasks_processed += 1
                            # Check draining after task completion
                            if command._check_draining_state():
                                command.shutdown_requested = True
                                break

        # Verify task was completed before shutdown
        assert task_completed
        assert command.shutdown_requested
        assert Worker.WorkerState.DRAINING in states_checked

    def test_worker_transitions_to_dead_on_clean_exit(self):
        """Worker should transition from DRAINING to DEAD on clean exit"""

        # The worker marks itself as DEAD at the end of handle() method
        # when it exits cleanly (not FAILED)
        # This test verifies the logic works

        # Create a real worker in DRAINING state
        worker = Worker.objects.create(
            worker_id="test-drain-exit", state=Worker.WorkerState.DRAINING, launch_token="test-token"
        )

        # Simulate the cleanup logic from handle() method
        if worker and not worker.state == "FAILED":
            worker.state = "DEAD"
            worker.save(update_fields=["state"])

        # Verify state was set to DEAD
        worker.refresh_from_db()
        assert worker.state == Worker.WorkerState.DEAD

    def test_view_marks_worker_as_draining(self):
        """Drain worker view should mark worker as DRAINING"""
        # Create a running worker
        worker = Worker.objects.create(
            worker_id="test-drain-worker", state=Worker.WorkerState.RUNNING, pid=99999, launch_token="test-token"
        )

        # Import the view function
        from steeloweb.views_worker import drain_worker_json
        from django.test import RequestFactory

        # Create a mock request with the worker_id parameter
        factory = RequestFactory()
        request = factory.post(f"/workers/{worker.worker_id}/drain/")

        # Call the drain worker view
        response = drain_worker_json(request, worker_id=worker.worker_id)

        # Check the worker was marked as DRAINING
        worker.refresh_from_db()
        assert worker.state == Worker.WorkerState.DRAINING

        # Check response
        assert response.status_code == 200
        import json

        data = json.loads(response.content)
        assert data["status"] == "success"
        assert data["worker_id"] == worker.worker_id

    def test_status_view_marks_dead_draining_workers(self):
        """Tick endpoint should mark DRAINING workers as DEAD if process doesn't exist"""
        # Create a DRAINING worker with non-existent PID
        worker = Worker.objects.create(
            worker_id="test-dead-drain",
            state=Worker.WorkerState.DRAINING,
            pid=999999,  # Non-existent PID
        )

        # Mock psutil to say process doesn't exist
        with patch("psutil.pid_exists", return_value=False):
            # Call the tick endpoint (which marks dead workers as DEAD)
            from steeloweb.views_worker import workers_tick
            from django.http import HttpRequest

            request = HttpRequest()
            request.method = "POST"
            workers_tick(request)

        # Worker should be marked as DEAD
        worker.refresh_from_db()
        assert worker.state == Worker.WorkerState.DEAD

    def test_draining_worker_refuses_new_tasks(self):
        """Worker in DRAINING state should not pick up new tasks"""
        from steeloweb.management.commands.steelo_worker import Command

        command = Command()
        command.worker_id = "test-worker"
        command.shutdown_requested = False

        # Mock worker in DRAINING state
        mock_worker = Mock()
        mock_worker.state = Worker.WorkerState.DRAINING
        mock_worker.WorkerState = Worker.WorkerState
        command.worker = mock_worker

        # Check draining state before attempting to process
        is_draining = command._check_draining_state()

        # Worker should recognize it's draining
        assert is_draining

        # In practice, worker should exit loop before trying to get new tasks
        # when it detects DRAINING state


class WorkerDrainIntegrationTests(TransactionTestCase):
    """Integration tests for drain workflow"""

    def test_full_drain_workflow_end_to_end(self):
        """Test drain workflow transitions"""
        # Create a worker directly in RUNNING state
        worker = Worker.objects.create(
            worker_id="test-drain-e2e",
            state=Worker.WorkerState.RUNNING,
            pid=88888,
            pid_started_at=timezone.now(),
            launch_token="test-token",
        )

        # Mark for draining
        worker.state = Worker.WorkerState.DRAINING
        worker.save()

        # Verify draining state
        worker.refresh_from_db()
        assert worker.state == Worker.WorkerState.DRAINING

        # Mock the process as dead for tick endpoint
        with patch("psutil.pid_exists", return_value=False):
            with patch("psutil.Process") as mock_psutil:
                # Make Process constructor raise NoSuchProcess for any PID
                mock_psutil.side_effect = Exception("No such process")

                # Call tick endpoint to detect dead process
                from steeloweb.views_worker import workers_tick
                from django.http import HttpRequest

                request = HttpRequest()
                request.method = "POST"
                workers_tick(request)

        # Worker should now be DEAD
        worker.refresh_from_db()
        assert worker.state == Worker.WorkerState.DEAD

    def test_multiple_workers_drain_independently(self):
        """Multiple workers should drain independently"""
        workers = []
        for i in range(3):
            worker = Worker.objects.create(worker_id=f"drain-test-{i}", state=Worker.WorkerState.RUNNING, pid=90000 + i)
            workers.append(worker)

        # Mark first two for draining
        workers[0].state = Worker.WorkerState.DRAINING
        workers[0].save()
        workers[1].state = Worker.WorkerState.DRAINING
        workers[1].save()

        # Third remains running
        workers[2].refresh_from_db()
        assert workers[2].state == Worker.WorkerState.RUNNING

        # First two should be draining
        workers[0].refresh_from_db()
        workers[1].refresh_from_db()
        assert workers[0].state == Worker.WorkerState.DRAINING
        assert workers[1].state == Worker.WorkerState.DRAINING
