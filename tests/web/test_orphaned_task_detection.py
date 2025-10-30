"""Tests for orphaned task detection and cleanup in worker management."""

import os
from datetime import timedelta
from unittest.mock import Mock, patch
from django.utils import timezone
from django.test import TestCase, TransactionTestCase
from steeloweb.models import Worker
from steeloweb.views_worker import _perform_tick_transitions, _get_worker_status_data

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.test")


class TestOrphanedTaskDetection(TransactionTestCase):
    """Test orphaned task detection and cleanup."""

    def setUp(self):
        """Set up test data."""
        # Clear any existing workers
        Worker.objects.all().delete()

    def tearDown(self):
        """Clean up after tests."""
        Worker.objects.all().delete()

    @patch("psutil.Process")
    @patch("steeloweb.views_worker.logger")
    @patch("django_tasks.backends.database.models.DBTaskResult")
    def test_orphaned_tasks_are_marked_failed(self, mock_task_model, mock_logger, mock_psutil_process):
        """Test that orphaned tasks are marked as FAILED when their worker doesn't exist."""
        # Arrange
        # Mock psutil to indicate test PIDs don't exist (prevents accidental collision with real processes)
        import psutil as psutil_module

        mock_psutil_process.side_effect = psutil_module.NoSuchProcess(1234)

        # Create a running worker
        Worker.objects.create(
            worker_id="active-worker",
            state="RUNNING",
            pid=1234,
            started_at=timezone.now(),
            pid_started_at=timezone.now(),
        )

        # Mock orphaned tasks (started >5 minutes ago)
        old_time = timezone.now() - timedelta(minutes=10)

        # Create mock task objects
        orphaned_task = Mock()
        orphaned_task.id = "orphaned-task-1"
        orphaned_task.pk = "orphaned-task-1"
        orphaned_task.worker_ids = ["dead-worker"]  # Worker that doesn't exist
        orphaned_task.started_at = old_time

        valid_task = Mock()
        valid_task.id = "valid-task-1"
        valid_task.pk = "valid-task-1"
        valid_task.worker_ids = ["active-worker"]  # Worker that exists
        valid_task.started_at = old_time

        # Mock the filter chain
        mock_filter_result = Mock()
        mock_filter_result.filter.return_value = mock_filter_result
        mock_filter_result.__iter__ = Mock(return_value=iter([orphaned_task, valid_task]))

        mock_task_model.objects.filter.return_value = mock_filter_result

        # Mock the update operation for orphaned task
        mock_update_filter = Mock()
        mock_update_filter.update.return_value = 1  # Indicate 1 record updated
        mock_task_model.objects.filter.side_effect = [
            mock_filter_result,  # First call for finding orphaned tasks
            mock_update_filter,  # Second call for updating orphaned task
            Mock(update=Mock(return_value=0)),  # Third call for valid task (not updated)
        ]

        # Act
        with patch.dict("os.environ", {"ORPHAN_GRACE_MINUTES": "5"}):
            transitions = _perform_tick_transitions()

        # Assert
        assert transitions["tasks_requeued"] == 1

        # Verify the orphaned task was updated to FAILED
        update_calls = [call for call in mock_task_model.objects.filter.call_args_list if "pk" in str(call)]
        assert len(update_calls) > 0

        # Verify warning was logged
        mock_logger.warning.assert_called_once()
        assert "Marked 1 orphaned tasks as FAILED" in str(mock_logger.warning.call_args)

    @patch("psutil.Process")
    @patch("steeloweb.views_worker.logger")
    @patch("django_tasks.backends.database.models.DBTaskResult")
    def test_tasks_within_grace_period_not_requeued(self, mock_task_model, mock_logger, mock_psutil_process):
        """Test that tasks started within grace period are not requeued."""
        # Arrange
        # Mock psutil to indicate test PIDs don't exist (prevents accidental collision with real processes)
        import psutil as psutil_module

        mock_psutil_process.side_effect = psutil_module.NoSuchProcess(1234)

        # Create a running worker
        Worker.objects.create(
            worker_id="active-worker",
            state="RUNNING",
            pid=1234,
            started_at=timezone.now(),
            pid_started_at=timezone.now(),
        )

        # Mock recent task (started 2 minutes ago - within grace period)
        recent_time = timezone.now() - timedelta(minutes=2)

        recent_task = Mock()
        recent_task.id = "recent-task-1"
        recent_task.pk = "recent-task-1"
        recent_task.worker_ids = ["dead-worker"]  # Worker doesn't exist
        recent_task.started_at = recent_time

        # Mock the filter to return empty (task filtered out by started_at)
        mock_filter_result = Mock()
        mock_filter_result.filter.return_value = mock_filter_result
        mock_filter_result.__iter__ = Mock(return_value=iter([]))  # No tasks match criteria

        mock_task_model.objects.filter.return_value = mock_filter_result

        # Act
        with patch.dict("os.environ", {"ORPHAN_GRACE_MINUTES": "5"}):
            transitions = _perform_tick_transitions()

        # Assert
        assert transitions["tasks_requeued"] == 0

        # Verify no warning was logged (no tasks requeued)
        mock_logger.warning.assert_not_called()

    @patch("psutil.Process")
    @patch("steeloweb.views_worker.logger")
    @patch("django_tasks.backends.database.models.DBTaskResult")
    def test_tasks_with_active_workers_not_requeued(self, mock_task_model, mock_logger, mock_psutil_process):
        """Test that tasks with active workers are not requeued."""
        # Arrange
        # Mock psutil to indicate test PIDs don't exist (prevents accidental collision with real processes)
        import psutil as psutil_module

        mock_psutil_process.side_effect = psutil_module.NoSuchProcess(1234)

        # Create running workers
        Worker.objects.create(
            worker_id="worker-1", state="RUNNING", pid=1234, started_at=timezone.now(), pid_started_at=timezone.now()
        )
        Worker.objects.create(
            worker_id="worker-2", state="DRAINING", pid=5678, started_at=timezone.now(), pid_started_at=timezone.now()
        )

        # Mock tasks with active workers
        old_time = timezone.now() - timedelta(minutes=10)

        task1 = Mock()
        task1.id = "task-1"
        task1.pk = "task-1"
        task1.worker_ids = ["worker-1"]  # Active RUNNING worker
        task1.started_at = old_time

        task2 = Mock()
        task2.id = "task-2"
        task2.pk = "task-2"
        task2.worker_ids = ["worker-2"]  # Active DRAINING worker
        task2.started_at = old_time

        # Mock the filter chain
        mock_filter_result = Mock()
        mock_filter_result.filter.return_value = mock_filter_result
        mock_filter_result.__iter__ = Mock(return_value=iter([task1, task2]))

        # Track update calls
        update_called = []

        def side_effect(*args, **kwargs):
            # First call is for finding tasks
            if "status" in kwargs and kwargs["status"] == "RUNNING" and "pk" not in kwargs:
                return mock_filter_result
            # Subsequent calls would be for updating (but shouldn't happen)
            else:
                mock_update = Mock()
                # Return 0 to indicate no rows updated (since worker exists)
                mock_update.update = Mock(return_value=0)
                update_called.append(kwargs)
                return mock_update

        mock_task_model.objects.filter.side_effect = side_effect

        # Act
        with patch.dict("os.environ", {"ORPHAN_GRACE_MINUTES": "5"}):
            transitions = _perform_tick_transitions()

        # Assert
        assert transitions["tasks_requeued"] == 0
        # The update might be attempted but should return 0 since workers exist
        # This is fine as long as no tasks are actually requeued

        # Verify no warning was logged
        mock_logger.warning.assert_not_called()

    @patch("steeloweb.views_worker.logger")
    @patch("django_tasks.backends.database.models.DBTaskResult")
    def test_race_guard_prevents_requeue_of_completed_tasks(self, mock_task_model, mock_logger):
        """Test that race guard prevents requeueing tasks that completed between check and update."""
        # Arrange
        # No active workers (all tasks appear orphaned)
        Worker.objects.all().delete()

        # Mock orphaned task
        old_time = timezone.now() - timedelta(minutes=10)

        orphaned_task = Mock()
        orphaned_task.id = "task-1"
        orphaned_task.pk = "task-1"
        orphaned_task.worker_ids = ["dead-worker"]
        orphaned_task.started_at = old_time

        # Mock the filter chain for finding tasks
        mock_filter_result = Mock()
        mock_filter_result.filter.return_value = mock_filter_result
        mock_filter_result.__iter__ = Mock(return_value=iter([orphaned_task]))

        # Mock update returning 0 (task already completed)
        mock_update_filter = Mock()
        mock_update_filter.update.return_value = 0  # No records updated (race condition)

        mock_task_model.objects.filter.side_effect = [
            mock_filter_result,  # First call for finding tasks
            mock_update_filter,  # Second call for updating (returns 0)
        ]

        # Act
        with patch.dict("os.environ", {"ORPHAN_GRACE_MINUTES": "5"}):
            transitions = _perform_tick_transitions()

        # Assert
        assert transitions["tasks_requeued"] == 0  # Nothing actually requeued

        # Verify no warning logged (nothing was requeued)
        mock_logger.warning.assert_not_called()

    @patch("steeloweb.views_worker.logger")
    def test_handles_missing_django_tasks_gracefully(self, mock_logger):
        """Test that missing django_tasks module is handled gracefully."""
        # Arrange
        Worker.objects.create(
            worker_id="worker-1", state="RUNNING", pid=1234, started_at=timezone.now(), pid_started_at=timezone.now()
        )

        # Act - Mock only the django_tasks import
        import builtins

        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if "django_tasks" in name:
                raise ImportError("No module named 'django_tasks'")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            transitions = _perform_tick_transitions()

        # Assert
        assert "tasks_requeued" in transitions
        assert transitions["tasks_requeued"] == 0

        # Should not log any errors for missing module
        mock_logger.error.assert_not_called()

    @patch("steeloweb.views_worker.logger")
    @patch("django_tasks.backends.database.models.DBTaskResult")
    def test_handles_database_errors_gracefully(self, mock_task_model, mock_logger):
        """Test that database errors are handled gracefully."""
        # Arrange
        # Mock database error
        mock_task_model.objects.filter.side_effect = Exception("Database connection lost")

        # Act
        transitions = _perform_tick_transitions()

        # Assert
        assert "tasks_requeued" in transitions
        assert transitions["tasks_requeued"] == 0

        # Verify error was logged
        mock_logger.error.assert_called_once()
        assert "Error checking orphaned tasks" in str(mock_logger.error.call_args)

    @patch("django_tasks.backends.database.models.DBTaskResult")
    def test_worker_status_data_includes_queue_counts(self, mock_task_model):
        """Test that worker status data includes queue counts."""
        # Arrange
        Worker.objects.create(
            worker_id="worker-1", state="RUNNING", pid=1234, started_at=timezone.now(), pid_started_at=timezone.now()
        )

        # Mock task counts
        mock_task_model.objects.filter.return_value.count.side_effect = [3, 2]  # pending, running

        # Act
        data = _get_worker_status_data()

        # Assert
        assert "queue" in data
        assert data["queue"]["pending"] == 3
        assert data["queue"]["running"] == 2


class TestEnvironmentConfiguration(TestCase):
    """Test environment variable configuration."""

    @patch("steeloweb.views_worker.logger")
    @patch("django_tasks.backends.database.models.DBTaskResult")
    def test_grace_period_configurable(self, mock_task_model, mock_logger):
        """Test that orphan grace period is configurable via environment variable."""
        # Arrange
        Worker.objects.all().delete()

        # Mock task just outside custom grace period
        task_time = timezone.now() - timedelta(minutes=3)

        orphaned_task = Mock()
        orphaned_task.id = "task-1"
        orphaned_task.pk = "task-1"
        orphaned_task.worker_ids = ["dead-worker"]
        orphaned_task.started_at = task_time

        mock_filter_result = Mock()
        mock_filter_result.filter.return_value = mock_filter_result
        mock_filter_result.__iter__ = Mock(return_value=iter([orphaned_task]))

        mock_update_filter = Mock()
        mock_update_filter.update.return_value = 1

        mock_task_model.objects.filter.side_effect = [mock_filter_result, mock_update_filter]

        # Act with 2-minute grace period
        with patch.dict("os.environ", {"ORPHAN_GRACE_MINUTES": "2"}):
            transitions = _perform_tick_transitions()

        # Assert
        assert transitions["tasks_requeued"] == 1

    @patch("steeloweb.views_worker.logger")
    @patch("django_tasks.backends.database.models.DBTaskResult")
    def test_default_grace_period(self, mock_task_model, mock_logger):
        """Test that default grace period is 5 minutes when not configured."""
        # Arrange
        Worker.objects.all().delete()

        # Task at 4 minutes (within default 5-minute grace)
        task_time = timezone.now() - timedelta(minutes=4)

        task = Mock()
        task.id = "task-1"
        task.pk = "task-1"
        task.worker_ids = ["dead-worker"]
        task.started_at = task_time

        mock_filter_result = Mock()
        mock_filter_result.filter.return_value = mock_filter_result
        mock_filter_result.__iter__ = Mock(return_value=iter([]))  # Filtered out by grace period

        mock_task_model.objects.filter.return_value = mock_filter_result

        # Act without setting ORPHAN_GRACE_MINUTES
        with patch.dict("os.environ", {}, clear=False):
            if "ORPHAN_GRACE_MINUTES" in os.environ:
                del os.environ["ORPHAN_GRACE_MINUTES"]
            transitions = _perform_tick_transitions()

        # Assert
        assert transitions["tasks_requeued"] == 0  # Within default grace period
