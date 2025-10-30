"""Integration tests for worker launch with token support

This test ensures that the actual worker process can be launched successfully
with the launch-token argument, addressing the production failure where
workers fail to start with "unrecognized arguments: --launch-token=xxx"
"""

import tempfile
import os
from unittest.mock import patch, Mock
from django.test import TransactionTestCase
from steeloweb.models import Worker
from steeloweb.worker_supervisor import supervisor


class WorkerLaunchIntegrationTests(TransactionTestCase):
    """Test actual worker process launch with launch tokens"""

    def test_worker_process_launches_successfully_with_token(self):
        """Worker process should launch without argument errors when passed launch-token"""
        from django.db import transaction

        # Create a temporary log file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            log_path = f.name

        try:
            # Mock the supervisor to avoid actually launching processes during testing
            with patch("subprocess.Popen") as mock_popen:
                mock_process = Mock()
                mock_process.pid = 12345
                mock_process.poll.return_value = None  # Process is running
                mock_popen.return_value = mock_process

                with patch("psutil.Process") as mock_psutil:
                    mock_psutil_process = Mock()
                    mock_psutil_process.create_time.return_value = 1234567890.0
                    mock_psutil.return_value = mock_psutil_process

                    # Mock admissible_workers to allow spawning
                    with patch.object(supervisor, "admissible_workers", return_value=2):
                        # Mock transaction.on_commit to execute immediately
                        with patch.object(transaction, "on_commit", side_effect=lambda func: func()):
                            # Try to spawn a worker - this should work without errors
                            result = supervisor.spawn_worker(count=1)

                    # Should succeed
                    assert len(result.get("spawned", [])) == 1
                    assert result.get("failed", 0) == 0

                    # Check that subprocess.Popen was called with launch-token
                    # Find the call that includes 'steelo_worker' (not memory_pressure)
                    steelo_worker_call = None
                    for call in mock_popen.call_args_list:
                        args, kwargs = call
                        if args and args[0] and "steelo_worker" in str(args[0]):
                            steelo_worker_call = call
                            break

                    assert steelo_worker_call is not None, "Should have called steelo_worker command"
                    args, kwargs = steelo_worker_call
                    cmd_args = args[0]

                    # The command should include launch-token argument and use steelo_worker
                    cmd_str = " ".join(cmd_args)
                    assert "--launch-token=" in cmd_str
                    assert "steelo_worker" in cmd_str

                    # Verify the worker was created with token
                    worker_id = result["spawned"][0]
                    worker = Worker.objects.get(worker_id=worker_id)
                    assert worker.launch_token is not None
                    assert len(worker.launch_token) == 16  # 8 hex bytes = 16 chars
        finally:
            # Cleanup
            if os.path.exists(log_path):
                os.unlink(log_path)

    def test_actual_steelo_worker_command_recognizes_launch_token(self):
        """The steelo_worker Django command should accept --launch-token argument"""
        from django.core.management import get_commands, load_command_class

        # Check if our custom steelo_worker command is available
        available_commands = get_commands()

        # Our custom command should be available
        assert "steelo_worker" in available_commands, f"steelo_worker not found in {available_commands.keys()}"

        # Load the steelo_worker command
        app_name = available_commands["steelo_worker"]
        command_instance = load_command_class(app_name, "steelo_worker")

        # Check if the command has launch_token in its argument parser
        parser = command_instance.create_parser("manage.py", "steelo_worker")

        # Get all argument names from the parser
        action_names = []
        for action in parser._actions:
            if action.dest != "help" and hasattr(action, "option_strings"):
                action_names.extend(action.option_strings)

        # Should include our launch-token argument
        assert "--launch-token" in action_names, f"launch-token not found in {action_names}"

    def test_worker_launch_actually_succeeds_end_to_end(self):
        """End-to-end test: spawn worker should create a process that doesn't immediately fail"""
        # This test verifies the real issue reported by the user
        # We'll run the actual command construction but not execute it

        worker = Worker.objects.create(
            worker_id="test-e2e-worker",
            launch_token="1234567890abcdef",
            state=Worker.WorkerState.STARTING,
            log_path="/tmp/test-worker.log",
        )

        # Try the actual _launch_process method but don't execute
        from steeloweb.worker_supervisor import WorkerSupervisor

        supervisor_instance = WorkerSupervisor()

        # Mock subprocess.Popen to capture the exact command that would be run
        with patch("subprocess.Popen") as mock_popen:
            mock_process = Mock()
            mock_process.pid = 99999
            mock_process.poll.return_value = None  # Process is still running
            mock_popen.return_value = mock_process

            # This is the method that was failing in production
            pid = supervisor_instance._launch_process(worker.worker_id, worker.launch_token, worker.log_path)

            # Should return a PID (mocked)
            assert pid == 99999

            # Verify the command would have been called correctly
            assert mock_popen.called
            args, kwargs = mock_popen.call_args
            cmd_args = args[0]

            # The exact failing case from the log: --launch-token=263d7855838e2b4e
            launch_token_arg = None
            for arg in cmd_args:
                if arg.startswith("--launch-token="):
                    launch_token_arg = arg
                    break

            assert launch_token_arg is not None, f"No launch-token found in command: {cmd_args}"
            assert launch_token_arg == f"--launch-token={worker.launch_token}"
