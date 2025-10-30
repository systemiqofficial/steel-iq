"""Worker supervisor with production-grade improvements"""

import os
import sys
import time
import logging
import logging.handlers
import platform
import subprocess
from pathlib import Path
import psutil

logger = logging.getLogger(__name__)


class WorkerSupervisor:
    """Manages worker processes with production-grade resource management"""

    def __init__(self):
        self.peak_memory = 8 * 1024**3  # 8GB peak per worker
        self.is_windows = platform.system() == "Windows"

        # Platform-specific settings
        if self.is_windows:
            self.memory_guard_extra = 1 * 1024**3  # Extra 1GB guard on Windows
        else:
            self.memory_guard_extra = 0

    def get_macos_memory_pressure(self):
        """Get macOS memory pressure status - normalized names"""
        if platform.system() != "Darwin":
            return "normal"

        try:
            result = subprocess.run(
                ["memory_pressure", "-Q"],  # Quick mode
                capture_output=True,
                text=True,
                timeout=2,
            )

            # Parse the free percentage from output
            for line in result.stdout.split("\n"):
                if "memory free percentage:" in line:
                    parts = line.split(":")
                    if len(parts) > 1:
                        percent_str = parts[1].strip().rstrip("%")
                        try:
                            free_percent = int(percent_str)
                            if free_percent >= 50:
                                return "normal"
                            elif free_percent >= 25:
                                return "warning"  # Normalized from 'warn'
                            else:
                                return "critical"
                        except ValueError:
                            pass

            return "normal"  # Default to normal if can't parse
        except Exception as e:
            logger.warning(f"Failed to get memory pressure: {e}")
            return "normal"  # Default to normal on error

    def admissible_workers(self, guard=None, hard_cap=None):
        """
        Calculate maximum safe worker count based on system resources.
        NOW RETURNS ZERO when there's no capacity!
        """
        # Get hard cap from environment if not provided
        if hard_cap is None:
            hard_cap = int(os.environ.get("WORKER_MAX_COUNT", "4"))
        vm = psutil.virtual_memory()

        # Use physical cores, not logical (SMT doesn't give memory headroom)
        cpu_count = psutil.cpu_count(logical=False) or psutil.cpu_count(logical=True) or 1

        # Conservative memory guard
        if guard is None:
            guard = max(2 * 1024**3, int(0.10 * vm.total))

        # Platform-specific memory adjustments (use vm.available directly)
        available = max(0, vm.available - guard - self.memory_guard_extra)

        # macOS memory pressure handling
        if platform.system() == "Darwin":
            pressure = self.get_macos_memory_pressure()
            if pressure in ["warning", "critical"]:
                available = available // 2  # Halve available memory under pressure
                logger.info(f"macOS memory pressure: {pressure}, reducing available memory")

        # Calculate limits
        memory_based_limit = max(0, available // self.peak_memory)
        cpu_based_limit = cpu_count  # Physical cores

        # Apply hard cap
        final_limit = min(int(memory_based_limit), cpu_based_limit, hard_cap)

        # CRITICAL: Return 0 if no capacity, don't force minimum of 1
        return max(0, final_limit)

    def can_spawn(self, count=1):
        """Check if we can spawn additional workers"""
        from steeloweb.models import Worker

        # This check is now done transactionally in the view
        # But keep for backward compatibility
        active_workers = Worker.objects.filter(state__in=["STARTING", "RUNNING", "DRAINING"]).count()

        admissible = self.admissible_workers()
        return active_workers + count <= admissible

    def spawn_worker(self, count=1):
        """Test helper method - spawns workers using the view logic.

        IMPORTANT: This method exists only for backward compatibility with tests.
        Production code should use views.add_worker_htmx/json instead.
        """
        from django.test import RequestFactory
        from steeloweb import views_worker

        spawned = []
        factory = RequestFactory()

        for _ in range(count):
            request = factory.post("/htmx/workers/add/")
            response = views_worker.add_worker_json(request)

            if response.status_code == 200:
                # Find the most recently created worker
                from steeloweb.models import Worker

                worker = Worker.objects.order_by("-started_at").first()
                if worker:
                    spawned.append(worker.worker_id)

        return {"spawned": spawned, "failed": count - len(spawned)}

    def _launch_process(self, worker_id: str, token: str, log_path: Path):
        """
        Launch a steelo_worker subprocess with launch token.
        Returns PID on success, None on failure.
        """
        # Determine correct working directory
        from django.conf import settings

        manage_dir = Path(settings.BASE_DIR)
        manage_py = manage_dir / "manage.py"

        if not manage_py.exists():
            # Try parent directory
            manage_dir = manage_dir.parent
            manage_py = manage_dir / "manage.py"
            if not manage_py.exists():
                logger.error(f"Could not find manage.py in {manage_dir}")
                return None

        # Build command with steelo_worker
        cmd = [
            sys.executable,
            "manage.py",
            "steelo_worker",
            f"--worker-id={worker_id}",
            f"--launch-token={token}",
            "--verbosity=2",
        ]

        # Add optional arguments if specified
        if os.environ.get("WORKER_QUEUE_NAME"):
            cmd.extend(["--queue-name", os.environ["WORKER_QUEUE_NAME"]])

        try:
            # Setup rotating log file with proper permissions
            self._setup_worker_log_rotation(log_path)

            # Launch with file-based logging to prevent PIPE deadlocks
            with open(log_path, "a") as log_file:  # Append mode for rotation
                kwargs = {
                    "stdout": log_file,
                    "stderr": subprocess.STDOUT,
                    "cwd": str(manage_dir),
                }

                # Platform-specific process group handling
                if self.is_windows:
                    # Windows: Create new process group for clean termination
                    kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
                else:
                    # POSIX: New session for process group management
                    kwargs["start_new_session"] = True

                process = subprocess.Popen(cmd, **kwargs)

                # Wait briefly to check for immediate failure
                time.sleep(0.5)
                if process.poll() is not None:
                    logger.error(f"Worker {worker_id} exited immediately with code {process.returncode}")
                    return None

                return process.pid

        except Exception as e:
            logger.error(f"Failed to launch worker {worker_id}: {e}")
            return None

    def _setup_worker_log_rotation(self, log_path: Path):
        """Setup log rotation for worker log files"""
        try:
            # Create log directory if it doesn't exist
            log_path.parent.mkdir(parents=True, exist_ok=True)

            # Check if log needs rotation (10MB max size)
            max_size = 10 * 1024 * 1024  # 10MB

            if log_path.exists() and log_path.stat().st_size > max_size:
                # Rotate existing logs
                for i in range(2, 0, -1):  # Rotate .log.2 -> .log.3, .log.1 -> .log.2
                    old_path = log_path.with_suffix(f".{i}")
                    new_path = log_path.with_suffix(f".{i + 1}")
                    if old_path.exists():
                        if new_path.exists():
                            new_path.unlink()  # Remove oldest
                        old_path.rename(new_path)

                # Move current log to .1
                backup_path = log_path.with_suffix(".1")
                if backup_path.exists():
                    backup_path.unlink()
                log_path.rename(backup_path)

            # Create new log file
            log_path.touch()
            log_path.chmod(0o644)

        except Exception as e:
            logger.warning(f"Failed to setup log rotation for {log_path}: {e}")

    def get_system_info(self):
        """Get detailed system information for monitoring"""
        vm = psutil.virtual_memory()
        cpu_percent = psutil.cpu_percent(interval=1)

        # Get load average (Unix) or processor queue length (Windows)
        if hasattr(os, "getloadavg"):
            load_avg = os.getloadavg()
        else:
            load_avg = (cpu_percent / 100.0,) * 3  # Approximate for Windows

        return {
            "memory": {
                "total": vm.total,
                "available": vm.available,
                "used": vm.used,
                "percent": vm.percent,
                "swap_percent": psutil.swap_memory().percent,
            },
            "cpu": {
                "count_logical": psutil.cpu_count(logical=True),
                "count_physical": psutil.cpu_count(logical=False),
                "percent": cpu_percent,
                "load_average": load_avg,
            },
            "limits": {
                "admissible_workers": self.admissible_workers(),
                "peak_memory_per_worker": self.peak_memory,
                "memory_pressure": self.get_macos_memory_pressure() if platform.system() == "Darwin" else "n/a",
            },
        }


# Singleton instance
supervisor = WorkerSupervisor()
