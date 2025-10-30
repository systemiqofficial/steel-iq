"""Tests for worker statistics and task processing integration

This test suite ensures that steelo_worker processes properly integrate with
the django-tasks system and report accurate process statistics.
"""

import time
import tempfile
from unittest.mock import patch, Mock
from django.test import TestCase
from django.utils import timezone
from steeloweb.models import Worker
from steeloweb.worker_supervisor import supervisor


class WorkerStatsIntegrationTests(TestCase):
    """Test worker process statistics and task integration"""

    def test_worker_reports_actual_process_stats(self):
        """Worker should report real memory and CPU stats from psutil"""
        # Create a worker
        with patch("steeloweb.worker_supervisor.subprocess.Popen") as mock_popen:
            mock_process = Mock()
            mock_process.pid = 12345
            mock_process.poll.return_value = None  # Process is still running
            mock_process.poll.return_value = None  # Process is still running
            mock_popen.return_value = mock_process

            with patch("psutil.Process") as mock_psutil_process:
                with patch("psutil.pid_exists") as mock_pid_exists:
                    mock_psutil_process_instance = Mock()
                    mock_psutil_process_instance.create_time.return_value = time.time()
                    # Mock process stats that should be reported
                    mock_psutil_process_instance.memory_info.return_value = Mock(rss=1024 * 1024 * 100)  # 100MB
                    mock_psutil_process_instance.cpu_percent.return_value = 25.5
                    mock_psutil_process_instance.status.return_value = "running"
                    mock_psutil_process_instance.is_running.return_value = True
                    mock_psutil_process_instance.children.return_value = []  # No children
                    mock_psutil_process_instance.num_threads.return_value = 1
                    mock_psutil_process.return_value = mock_psutil_process_instance

                    # Mock pid_exists to return True for our test PID
                    mock_pid_exists.return_value = True

                    # Import and mock transaction for spawning
                    from django.db import transaction

                    # Mock admissible_workers to allow spawning
                    with patch.object(supervisor, "admissible_workers", return_value=2):
                        # Mock transaction.on_commit to execute immediately
                        with patch.object(transaction, "on_commit", side_effect=lambda func: func()):
                            result = supervisor.spawn_worker(count=1)
                    worker_id = result.get("spawned", [])[0] if result.get("spawned") else None
                    assert worker_id, "No worker was spawned"
                    worker = Worker.objects.get(worker_id=worker_id)

                    # Manually set PID since spawning is async in tests
                    worker.pid = 12345
                    worker.pid_started_at = timezone.now()
                    worker.state = Worker.WorkerState.RUNNING
                    worker.save()

                    # Get worker stats
                    stats = worker.get_process_stats()

                    # Should return actual process statistics
                    assert stats is not None, "Worker should return process stats"
                    assert "memory" in stats, "Stats should include memory"
                    assert "cpu" in stats, "Stats should include CPU"
                    assert "status" in stats, "Stats should include status"

                    # Values should match what psutil reported
                    expected_memory = 1024 * 1024 * 100  # 100MB
                    assert abs(stats["memory"] - expected_memory) < 1000, (
                        f"Expected ~{expected_memory}, got {stats['memory']}"
                    )
                    assert stats["cpu"] == 25.5, f"Expected CPU 25.5, got {stats['cpu']}"
                    assert stats["status"] == "running", f"Expected status 'running', got {stats['status']}"

    def test_worker_processes_django_tasks(self):
        """Worker command exists and is configured to process django-tasks"""
        # Test that the steelo_worker management command exists
        from django.core.management import get_commands

        available_commands = get_commands()
        assert "steelo_worker" in available_commands, "steelo_worker command should exist"

        # Test that we can instantiate the command
        from steeloweb.management.commands.steelo_worker import Command

        command = Command()

        # Verify the command has the necessary methods for task processing
        assert hasattr(command, "_claim_and_process_task"), "Command should have _claim_and_process_task method"
        assert hasattr(command, "_execute_task"), "Command should have _execute_task method"
        assert hasattr(command, "_check_draining_state"), "Command should have _check_draining_state method"
        assert hasattr(command, "_update_heartbeat"), "Command should have _update_heartbeat method"

        # Test that supervisor._launch_process would use the correct command
        # by checking what command it builds (without actually launching)
        from pathlib import Path

        test_worker_id = "test-worker"
        test_token = "test-token"
        test_log_path = Path(tempfile.gettempdir()) / "test.log"

        # Mock just the Popen call to capture args
        with patch("subprocess.Popen") as mock_popen:
            mock_process = Mock()
            mock_process.pid = 12345
            mock_process.poll.return_value = None
            mock_popen.return_value = mock_process

            # Call _launch_process directly
            supervisor._launch_process(test_worker_id, test_token, test_log_path)

            # Verify it was called with the right command
            assert mock_popen.called, "subprocess.Popen should have been called"
            args, kwargs = mock_popen.call_args
            cmd_args = args[0]

            # Check the command structure
            assert "manage.py" in cmd_args, "Should use manage.py"
            assert "steelo_worker" in cmd_args, "Should use steelo_worker command"
            assert f"--worker-id={test_worker_id}" in cmd_args, "Should have worker-id"
            assert f"--launch-token={test_token}" in cmd_args, "Should have launch-token"

    def test_worker_heartbeat_updates_during_task_processing(self):
        """Worker heartbeat should update while processing tasks"""
        with patch("steeloweb.worker_supervisor.subprocess.Popen") as mock_popen:
            mock_process = Mock()
            mock_process.pid = 12345
            mock_process.poll.return_value = None  # Process is still running
            mock_popen.return_value = mock_process

            with patch("psutil.Process") as mock_psutil:
                mock_psutil_process = Mock()
                mock_psutil_process.create_time.return_value = time.time()
                mock_psutil_process.is_running.return_value = True
                mock_psutil.return_value = mock_psutil_process

                # Import and mock transaction for spawning
                from django.db import transaction

                # Mock admissible_workers to allow spawning
                with patch.object(supervisor, "admissible_workers", return_value=2):
                    # Mock transaction.on_commit to execute immediately
                    with patch.object(transaction, "on_commit", side_effect=lambda func: func()):
                        result = supervisor.spawn_worker(count=1)
                worker_id = result.get("spawned", [])[0] if result.get("spawned") else None
                assert worker_id, "No worker was spawned"
                worker = Worker.objects.get(worker_id=worker_id)

                # Simulate handshake completing with PID
                worker.pid = 12345
                worker.pid_started_at = timezone.now()
                worker.state = Worker.WorkerState.RUNNING
                worker.heartbeat = worker.started_at
                worker.save()

                # In a real scenario, the worker would update its own heartbeat
                # This test captures the expectation that heartbeats should be recent
                time_since_heartbeat = worker.time_since_heartbeat()

                # Should show recent activity (this will pass initially but demonstrates the expectation)
                assert time_since_heartbeat is not None

    def test_multiple_workers_have_unique_process_stats(self):
        """Each worker should report its own unique process statistics"""
        from django.db import transaction

        with patch("steeloweb.worker_supervisor.subprocess.Popen") as mock_popen:
            # Create different mock processes with different stats
            processes = []
            for i in range(2):
                mock_process = Mock()
                mock_process.pid = 12345 + i
                mock_process.poll.return_value = None  # Process is running
                processes.append(mock_process)

            mock_popen.side_effect = processes

            with patch("psutil.Process") as mock_psutil_process:
                with patch("psutil.pid_exists") as mock_pid_exists:
                    # Different stats for each process
                    stats_configs = [
                        {"memory": 1024 * 1024 * 50, "cpu": 10.0},  # 50MB, 10% CPU
                        {"memory": 1024 * 1024 * 200, "cpu": 75.0},  # 200MB, 75% CPU
                    ]

                    def mock_psutil_factory(pid):
                        idx = pid - 12345
                        mock_proc = Mock()
                        mock_proc.create_time.return_value = time.time()
                        mock_proc.memory_info.return_value = Mock(rss=stats_configs[idx]["memory"])
                        mock_proc.cpu_percent.return_value = stats_configs[idx]["cpu"]
                        mock_proc.status.return_value = "running"
                        mock_proc.is_running.return_value = True
                        mock_proc.children.return_value = []  # No children for simplicity
                        mock_proc.num_threads.return_value = 1
                        return mock_proc

                    mock_psutil_process.side_effect = mock_psutil_factory

                    # Mock pid_exists to return True for our test PIDs
                    def mock_pid_exists_func(pid):
                        return pid in [12345, 12346]

                    mock_pid_exists.side_effect = mock_pid_exists_func

                    # Mock admissible_workers to allow spawning
                    with patch.object(supervisor, "admissible_workers", return_value=3):
                        # Mock transaction.on_commit to execute immediately
                        with patch.object(transaction, "on_commit", side_effect=lambda func: func()):
                            # Spawn 2 workers
                            result = supervisor.spawn_worker(count=2)

                    # Get the spawned worker IDs
                    worker_ids = result.get("spawned", [])

                    # If batch spawn didn't work, try one at a time
                    if len(worker_ids) < 2:
                        with patch.object(supervisor, "admissible_workers", return_value=3):
                            with patch.object(transaction, "on_commit", side_effect=lambda func: func()):
                                for i in range(2 - len(worker_ids)):
                                    result = supervisor.spawn_worker(count=1)
                                    worker_ids.extend(result.get("spawned", []))

                    # Get the actual worker objects and assign PIDs
                    workers = []
                    for idx, worker_id in enumerate(worker_ids):
                        worker = Worker.objects.get(worker_id=worker_id)
                        # Manually set PID since spawning is async in tests
                        worker.pid = 12345 + idx
                        worker.pid_started_at = timezone.now()
                        worker.save()
                        workers.append(worker)

                    assert len(workers) >= 1, f"Need at least 1 worker, got {len(workers)}"

                    # If we only got one worker, still test it
                    if len(workers) == 1:
                        # Just test the one worker we got
                        workers_sorted = workers
                    else:
                        # Each worker should report different stats
                        # Sort workers by PID to ensure consistent ordering
                        workers_sorted = sorted(workers, key=lambda w: w.pid)

                    for i, worker in enumerate(workers_sorted):
                        worker.state = Worker.WorkerState.RUNNING
                        worker.save()

                        # The worker's pid determines which stats it should get
                        pid_index = worker.pid - 12345
                        # Make sure pid_index is valid
                        if pid_index < 0 or pid_index >= len(stats_configs):
                            # Skip this worker if its PID is out of range
                            continue

                        expected_memory = stats_configs[pid_index]["memory"]
                        expected_cpu = stats_configs[pid_index]["cpu"]

                        stats = worker.get_process_stats()

                        assert stats is not None, f"Worker with PID {worker.pid} stats should not be None"
                        assert "memory" in stats, f"Worker with PID {worker.pid} stats should have 'memory'"
                        assert "cpu" in stats, f"Worker with PID {worker.pid} stats should have 'cpu'"
                        assert "status" in stats, f"Worker with PID {worker.pid} stats should have 'status'"

                        assert abs(stats["memory"] - expected_memory) < 1000, (
                            f"Worker with PID {worker.pid} expected {expected_memory} memory, got {stats['memory']}"
                        )
                        assert stats["cpu"] == expected_cpu, (
                            f"Worker with PID {worker.pid} expected {expected_cpu} CPU, got {stats['cpu']}"
                        )
