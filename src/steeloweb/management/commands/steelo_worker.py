"""Production-grade steelo_worker with SQLite-safe task claiming"""

import time
import signal
import logging
import os
import threading
from django.core.management.base import BaseCommand
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone
from django.utils.module_loading import import_string
from django.db import transaction
from django_tasks.base import Task
from django_tasks.backends.database.models import DBTaskResult
from steeloweb.models import Worker

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run a database-backed task worker with SQLite-safe claiming"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.shutdown_requested = False
        self.worker_id = None
        self.launch_token = None
        self.worker = None
        self.should_stop = threading.Event()  # Clean shutdown mechanism

    def add_arguments(self, parser):
        """Add custom arguments"""
        parser.add_argument("--worker-id", type=str, help="Unique identifier for this worker")
        parser.add_argument("--launch-token", type=str, required=False, help="Security token for worker handshake")
        parser.add_argument("--queue-name", default="default", help="The queue to process (default: default)")
        parser.add_argument("--interval", type=int, default=1, help="Polling interval in seconds")

    def handle(self, *args, **options):
        """Main worker loop"""
        self.worker_id = options.get("worker_id")
        self.launch_token = options.get("launch_token")
        queue_name = options.get("queue_name", "default")
        interval = options.get("interval", 1)

        # Perform handshake
        if not self._perform_handshake():
            logger.error(f"Handshake failed for worker {self.worker_id}")
            return

        logger.info(f"Worker {self.worker_id} processing queue '{queue_name}' (interval: {interval}s)")

        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._handle_shutdown_signal)
        signal.signal(signal.SIGINT, self._handle_shutdown_signal)

        # Main processing loop with finally block for cleanup
        tasks_processed = 0
        last_heartbeat = time.time()
        heartbeat_interval = 30  # Update heartbeat every 30 seconds

        try:
            while not self.shutdown_requested and not self.should_stop.is_set():
                try:
                    # Check if marked for draining (including missing record)
                    if self._check_draining_state():
                        logger.info(f"Worker {self.worker_id} draining – exiting main loop")
                        self.shutdown_requested = True
                        self.should_stop.set()
                        break  # Exit immediately

                    # Update heartbeat periodically (WORKER updates its own heartbeat)
                    now = time.time()
                    if now - last_heartbeat > heartbeat_interval:
                        self._update_heartbeat()
                        last_heartbeat = now

                    # Process next task (SQLite-safe claiming)
                    task_found = self._claim_and_process_task(queue_name)

                    if task_found:
                        tasks_processed += 1
                        logger.debug(f"Processed task {tasks_processed}")

                        # Update heartbeat after task completion
                        self._update_heartbeat()

                        # Check if we should drain after completing this task
                        if self._check_draining_state():
                            logger.info(f"Worker {self.worker_id} draining after completing task")
                            self.shutdown_requested = True
                            self.should_stop.set()
                            break
                    else:
                        # No task found, wait before checking again
                        time.sleep(interval)

                except Exception as e:
                    logger.error(f"Error in main loop: {e}", exc_info=True)
                    self._mark_worker_failed(str(e))
                    break

            logger.info(f"Worker {self.worker_id} processed {tasks_processed} tasks")

        finally:
            # Always mark as DEAD on exit (unless already FAILED)
            if self.worker:
                try:
                    # Only update if worker record still exists
                    try:
                        self.worker = Worker.objects.get(worker_id=self.worker_id)
                        if self.worker.state != "FAILED":
                            self.worker.state = Worker.WorkerState.DEAD
                            self.worker.save(update_fields=["state"])
                            logger.info(f"Worker {self.worker_id} marked as DEAD (clean exit)")
                    except Worker.DoesNotExist:
                        logger.debug(f"Worker {self.worker_id} already removed from DB")
                except Exception as e:
                    logger.warning(f"Failed to update final worker state: {e}")

        logger.info(f"Worker {self.worker_id} shutdown complete")

    def _perform_handshake(self):
        """Perform handshake with database"""
        from steeloweb.models import Worker

        try:
            # Find worker record and validate token
            self.worker = Worker.objects.get(worker_id=self.worker_id, launch_token=self.launch_token)

            import os

            # Store current state before any modifications
            current_state = self.worker.state
            logger.debug(f"Worker {self.worker_id} handshake: initial state = {current_state}")

            # Refresh from DB to check if state was changed externally
            self.worker.refresh_from_db()
            logger.debug(f"Worker {self.worker_id} handshake: state after refresh = {self.worker.state}")

            # Don't proceed if worker has been marked as FAILED or DRAINING
            if self.worker.state in ["FAILED", "DRAINING"]:
                logger.warning(f"Worker {self.worker_id} is marked as {self.worker.state}, aborting handshake")
                return False

            # Build list of fields to update
            update_fields = ["heartbeat", "pid"]

            # Only update to RUNNING if currently STARTING
            if self.worker.state == "STARTING":
                self.worker.state = "RUNNING"
                update_fields.append("state")

            self.worker.heartbeat = timezone.now()

            # Store actual PID and creation time for reuse detection
            import psutil
            from datetime import datetime

            if not self.worker.pid:
                proc = psutil.Process(os.getpid())
                self.worker.pid = proc.pid
                self.worker.pid_started_at = timezone.make_aware(datetime.fromtimestamp(proc.create_time()))
                update_fields.extend(["pid", "pid_started_at"])

            self.worker.save(update_fields=update_fields)

            logger.info(f"Handshake successful for worker {self.worker_id}")
            return True

        except Worker.DoesNotExist:
            logger.error(f"Worker {self.worker_id} not found or invalid token")
            return False
        except Exception as e:
            logger.error(f"Handshake error: {e}", exc_info=True)
            return False

    def _claim_and_process_task(self, queue_name):
        """
        SQLite-safe task claiming using compare-and-set.
        Avoids select_for_update(skip_locked=True) which SQLite doesn't support.
        """
        max_retries = 8

        for attempt in range(max_retries):
            try:
                with transaction.atomic():
                    # Find next READY task
                    task = (
                        DBTaskResult.objects.filter(status="READY", queue_name=queue_name)
                        .order_by("priority", "enqueued_at")
                        .first()
                    )

                    if not task:
                        return False  # No tasks available

                    # Try to claim it (compare-and-set)
                    updated = DBTaskResult.objects.filter(
                        pk=task.pk,
                        status="READY",  # Ensure it's still READY
                    ).update(
                        status="RUNNING",
                        started_at=timezone.now(),
                        worker_ids=[self.worker_id],  # Track which worker claimed it
                    )

                    if updated == 1:
                        # Successfully claimed the task
                        break
                    # else: Another worker claimed it, retry

            except Exception as e:
                logger.warning(f"Error claiming task (attempt {attempt + 1}): {e}")
                time.sleep(0.1 * (2**attempt))  # Exponential backoff
                continue
        else:
            # Failed to claim after max retries
            return False

        # Execute the claimed task
        return self._execute_task(task)

    def _execute_task(self, task_result):
        """Execute a claimed task with optional timeout"""
        logger.debug(f"Executing task {task_result.id}: {task_result.task_path}")

        try:
            # Import the task function
            task_func = import_string(task_result.task_path)

            # Get args and kwargs
            args_kwargs = task_result.args_kwargs or {"args": [], "kwargs": {}}
            args = args_kwargs.get("args", [])
            kwargs = args_kwargs.get("kwargs", {})

            # Get timeout from environment (default: 48 hours)
            # Simulations commonly take 14+ hours on Windows, so we need a generous timeout
            timeout = int(os.environ.get("WORKER_TASK_TIMEOUT", "172800"))

            # Execute with timeout protection
            result = self._execute_with_timeout(task_func, args, kwargs, timeout)

            # Mark as succeeded
            task_result.status = "SUCCEEDED"
            task_result.finished_at = timezone.now()
            task_result.return_value = result
            task_result.save()

            logger.debug(f"Task {task_result.id} completed successfully")
            return True

        except TimeoutError as e:
            # Mark as failed due to timeout
            task_result.status = "FAILED"
            task_result.finished_at = timezone.now()
            task_result.exception_class_path = f"{e.__class__.__module__}.{e.__class__.__name__}"
            task_result.traceback = str(e)
            task_result.save()

            logger.error(f"Task {task_result.id} timed out after {timeout}s: {e}")
            return True  # Still processed, just failed

        except Exception as e:
            # Mark as failed
            task_result.status = "FAILED"
            task_result.finished_at = timezone.now()
            task_result.exception_class_path = f"{e.__class__.__module__}.{e.__class__.__name__}"

            # Store traceback
            import traceback

            task_result.traceback = traceback.format_exc()
            task_result.save()

            logger.error(f"Task {task_result.id} failed: {e}")
            return True  # Still processed, just failed

    def _execute_with_timeout(self, task_func, args, kwargs, timeout):
        """Execute task function with timeout protection (POSIX only)"""
        if timeout <= 0:
            # No timeout enforcement
            if isinstance(task_func, Task):
                return task_func.call(*args, **kwargs)
            else:
                return task_func(*args, **kwargs)

        # Try POSIX signal-based timeout first
        if hasattr(signal, "SIGALRM") and hasattr(signal, "alarm"):
            return self._execute_with_signal_timeout(task_func, args, kwargs, timeout)
        else:
            # Fallback for Windows/non-POSIX: no timeout enforcement
            logger.warning("Task timeout not supported on this platform - executing without timeout")
            if isinstance(task_func, Task):
                return task_func.call(*args, **kwargs)
            else:
                return task_func(*args, **kwargs)

    def _execute_with_signal_timeout(self, task_func, args, kwargs, timeout):
        """Execute with SIGALRM timeout (POSIX only)"""

        def timeout_handler(signum, frame):
            raise TimeoutError(f"Task execution exceeded {timeout} seconds timeout")

        # Save original signal handler
        old_handler = signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(timeout)

        try:
            # Execute the task
            if isinstance(task_func, Task):
                result = task_func.call(*args, **kwargs)
            else:
                result = task_func(*args, **kwargs)

            # Clear the alarm
            signal.alarm(0)
            return result

        except TimeoutError:
            # Timeout occurred - let it propagate
            signal.alarm(0)
            raise
        except Exception:
            # Other exception - clear alarm and re-raise
            signal.alarm(0)
            raise
        finally:
            # Restore original signal handler
            signal.signal(signal.SIGALRM, old_handler)

    def _check_draining_state(self) -> bool:
        """Return True if this worker should drain/exit."""
        try:
            # Fetch only the state field for efficiency
            state = Worker.objects.only("state").values_list("state", flat=True).get(worker_id=self.worker_id)
            return state == "DRAINING"

        except Worker.DoesNotExist:
            # In standalone mode, retry once to handle transient DB issues
            if os.getenv("STEELO_STANDALONE") == "1":
                time.sleep(0.25)
                try:
                    state = Worker.objects.only("state").values_list("state", flat=True).get(worker_id=self.worker_id)
                    return state == "DRAINING"
                except Worker.DoesNotExist:
                    logger.info(f"Worker {self.worker_id} still missing from DB – initiating shutdown")
                    return True

            logger.info(f"Worker {self.worker_id} missing from DB – initiating shutdown")
            return True

        except ObjectDoesNotExist:
            logger.info(f"Worker {self.worker_id} not found (ObjectDoesNotExist) – initiating shutdown")
            return True

        except Exception as e:
            # Unexpected failure: log as warning, not error
            logger.warning(f"Unexpected drain check failure: {e}", exc_info=True)
            try:
                import sentry_sdk

                with sentry_sdk.push_scope() as scope:
                    scope.set_tag("worker_id", self.worker_id)
                    scope.fingerprint = ["worker-drain-check-unexpected"]
            except (ImportError, AttributeError):
                pass  # Sentry not available or different version
            return False  # Don't drain on unexpected errors

    def _update_heartbeat(self):
        """Update worker heartbeat - ONLY THE WORKER UPDATES ITS OWN HEARTBEAT"""
        if not self.worker:
            return

        try:
            # Use update() to avoid race conditions
            # Only update heartbeat if worker is not FAILED or DEAD
            from steeloweb.models import Worker

            updated = (
                Worker.objects.filter(worker_id=self.worker_id)
                .exclude(state__in=["FAILED", "DEAD"])
                .update(heartbeat=timezone.now())
            )

            if updated:
                logger.debug(f"Updated heartbeat for worker {self.worker_id}")
            else:
                logger.warning(f"Failed to update heartbeat - worker {self.worker_id} not found")

        except Exception as e:
            logger.error(f"Failed to update heartbeat: {e}")

    def _mark_worker_failed(self, error_message):
        """Mark worker as failed with error details"""
        if not self.worker:
            return

        try:
            self.worker.state = "FAILED"
            self.worker.last_error_tail = error_message[:1000]
            self.worker.save()
            logger.info(f"Marked worker {self.worker_id} as FAILED")
        except Exception as e:
            logger.error(f"Failed to mark worker as failed: {e}")

    def _handle_shutdown_signal(self, signum, frame):
        """Handle shutdown signals gracefully"""
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        self.shutdown_requested = True

        # Mark worker as draining for clean shutdown
        if self.worker:
            try:
                self.worker.state = "DRAINING"
                self.worker.save(update_fields=["state"])
                logger.info(f"Worker {self.worker_id} marked as draining")
            except Exception as e:
                logger.error(f"Failed to update worker state: {e}")
