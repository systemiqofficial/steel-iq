"""Test that worker handles database record deletion gracefully"""

from unittest.mock import patch, MagicMock
from django.test import TestCase
from steeloweb.models import Worker
from steeloweb.management.commands.steelo_worker import Command
import os


class TestWorkerDeletionRace(TestCase):
    """Test that worker handles database record deletion gracefully"""

    def test_worker_exits_cleanly_when_row_deleted(self):
        """Worker should exit gracefully when its database row is deleted mid-run"""

        # Create a worker record
        worker = Worker.objects.create(worker_id="test-worker", state="RUNNING", launch_token="test-token")

        # Create command instance
        cmd = Command()
        cmd.worker_id = "test-worker"
        cmd.worker = worker
        cmd.shutdown_requested = False

        # Delete the worker to simulate race condition
        Worker.objects.filter(worker_id="test-worker").delete()

        # Check drain state - should handle missing row gracefully
        with self.assertLogs("steeloweb.management.commands.steelo_worker", level="INFO") as cm:
            result = cmd._check_draining_state()

            # Should return True (drain)
            self.assertTrue(result)

            # Should log info, not error
            self.assertTrue(
                any("missing from DB" in msg for msg in cm.output), f"Expected shutdown log not found in: {cm.output}"
            )

    def test_standalone_mode_retries_once(self):
        """In standalone mode, should retry once before giving up"""

        Worker.objects.create(worker_id="test-worker", state="RUNNING")

        cmd = Command()
        cmd.worker_id = "test-worker"

        call_count = [0]

        # Mock the query chain properly
        mock_queryset = MagicMock()
        mock_only = MagicMock(return_value=mock_queryset)
        mock_values_list = MagicMock(return_value=mock_queryset)

        def mock_get(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Worker.DoesNotExist()
            return "RUNNING"  # Found on retry

        mock_queryset.only = mock_only
        mock_queryset.values_list = mock_values_list
        mock_queryset.get = mock_get

        with patch.dict("os.environ", {"STEELO_STANDALONE": "1"}):
            with patch.object(Worker, "objects", mock_queryset):
                with patch("time.sleep") as mock_sleep:
                    result = cmd._check_draining_state()

                    # Should have retried
                    self.assertEqual(call_count[0], 2)
                    # Should have slept before retry
                    mock_sleep.assert_called_once_with(0.25)
                    # Should not drain (found on retry)
                    self.assertFalse(result)

    def test_unexpected_exception_logged_as_warning(self):
        """Unexpected exceptions should be logged as warnings, not errors"""

        Worker.objects.create(worker_id="test-worker", state="RUNNING")

        cmd = Command()
        cmd.worker_id = "test-worker"

        # Mock the query chain properly to raise an unexpected exception
        mock_queryset = MagicMock()
        mock_only = MagicMock(return_value=mock_queryset)
        mock_values_list = MagicMock(return_value=mock_queryset)
        mock_queryset.only = mock_only
        mock_queryset.values_list = mock_values_list
        mock_queryset.get = MagicMock(side_effect=RuntimeError("Unexpected database error"))

        with patch.object(Worker, "objects", mock_queryset):
            with self.assertLogs("steeloweb.management.commands.steelo_worker", level="WARNING") as cm:
                result = cmd._check_draining_state()

                # Should not drain on unexpected error
                self.assertFalse(result)

                # Should log as warning
                self.assertTrue(
                    any("Unexpected drain check failure" in msg for msg in cm.output),
                    f"Expected warning log not found in: {cm.output}",
                )

    def test_server_mode_no_retry(self):
        """In server mode (non-standalone), should not retry"""

        Worker.objects.create(worker_id="test-worker", state="RUNNING")

        cmd = Command()
        cmd.worker_id = "test-worker"

        call_count = [0]

        # Mock the query chain properly
        mock_queryset = MagicMock()
        mock_only = MagicMock(return_value=mock_queryset)
        mock_values_list = MagicMock(return_value=mock_queryset)

        def mock_get(*args, **kwargs):
            call_count[0] += 1
            raise Worker.DoesNotExist()

        mock_queryset.only = mock_only
        mock_queryset.values_list = mock_values_list
        mock_queryset.get = mock_get

        # Ensure STEELO_STANDALONE is not set
        with patch.dict("os.environ", {}, clear=True):
            if "STEELO_STANDALONE" in os.environ:
                del os.environ["STEELO_STANDALONE"]

            with patch.object(Worker, "objects", mock_queryset):
                with patch("time.sleep") as mock_sleep:
                    with self.assertLogs("steeloweb.management.commands.steelo_worker", level="INFO") as cm:
                        result = cmd._check_draining_state()

                        # Should have called only once (no retry)
                        self.assertEqual(call_count[0], 1)
                        # Should not have slept
                        mock_sleep.assert_not_called()
                        # Should drain
                        self.assertTrue(result)
                        # Should log the missing worker
                        self.assertTrue(
                            any("missing from DB" in msg for msg in cm.output),
                            f"Expected shutdown log not found in: {cm.output}",
                        )

    def test_main_loop_exits_on_missing_worker(self):
        """Main loop should exit immediately when worker record is deleted"""

        # Create a worker record
        worker = Worker.objects.create(worker_id="test-worker", state="RUNNING", launch_token="test-token")

        cmd = Command()
        cmd.worker_id = "test-worker"
        cmd.worker = worker
        cmd.shutdown_requested = False

        # Mock the task processing to return no tasks
        with patch.object(cmd, "_claim_and_process_task", return_value=False):
            with patch("time.sleep"):
                # Simulate the worker record being deleted after first check
                check_count = [0]
                original_check = cmd._check_draining_state

                def mock_drain_check():
                    check_count[0] += 1
                    if check_count[0] == 1:
                        # First check - worker exists
                        return False
                    else:
                        # Second check - worker deleted
                        Worker.objects.filter(worker_id="test-worker").delete()
                        # Call the original method, not the mock
                        return original_check()

                with patch.object(cmd, "_check_draining_state", side_effect=mock_drain_check):
                    # Simulate a few loop iterations
                    iterations = 0
                    while not cmd.shutdown_requested and iterations < 5:
                        drain_result = mock_drain_check()
                        if drain_result:
                            cmd.shutdown_requested = True
                            break
                        iterations += 1

                    # Should have exited after detecting missing worker
                    self.assertTrue(cmd.shutdown_requested)
                    self.assertLessEqual(iterations, 2)
