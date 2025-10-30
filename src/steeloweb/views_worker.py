"""Worker management views for production-grade parallel task execution.

Features:
- Side-effect-free GET endpoints (reads don't modify state)
- Transactional worker spawning (prevents race conditions)
- Graceful draining vs immediate abort functionality
- PID reuse protection with process create_time tracking
- Automatic cleanup of dead/failed workers
- Real-time status updates via HTMX
"""

import os
import logging
import psutil
from pathlib import Path
from datetime import timedelta
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods, require_POST
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.db import transaction
from .worker_supervisor import supervisor
from .models import Worker, AdmissionControl

logger = logging.getLogger(__name__)


def _read_log_tail(log_path, max_bytes=2000):
    """Read the tail of a log file safely"""
    if not log_path:
        return None

    try:
        path = Path(log_path)
        if path.exists():
            with open(path, "rb") as f:
                # Seek to end minus max_bytes
                f.seek(0, 2)  # End of file
                file_size = f.tell()
                start = max(0, file_size - max_bytes)
                f.seek(start)

                # Read and decode
                content = f.read().decode("utf-8", errors="replace")

                # If we started mid-line, skip to next line
                if start > 0:
                    first_newline = content.find("\n")
                    if first_newline != -1:
                        content = content[first_newline + 1 :]

                return content[-1000:]  # Return last 1000 chars
    except Exception as e:
        logger.error(f"Failed to read log {log_path}: {e}")

    return None


def _get_worker_status_data():
    """Get worker status data - PURE READ ONLY, NO SIDE EFFECTS"""
    vm = psutil.virtual_memory()

    # Just read from database, no state changes
    workers = []
    for worker in Worker.objects.all():
        worker_data = {
            "worker_id": worker.worker_id,  # Changed from 'id' to 'worker_id'
            "pid": worker.pid,
            "state": worker.state,
            "started_at": worker.started_at.isoformat(),
            "heartbeat": worker.heartbeat.isoformat() if worker.heartbeat else None,
            "time_since_heartbeat": worker.time_since_heartbeat(),
            "log_path": worker.log_path,
        }

        # Add process stats if available
        stats = worker.get_process_stats()
        if stats:
            worker_data.update(stats)

        # Check if worker appears dead (but don't change state)
        if worker.state in ["RUNNING", "DRAINING"]:
            if not worker.is_same_process():  # Uses pid_started_at
                worker_data["appears_dead"] = True

        # Check if worker appears stalled (no heartbeat for 3+ minutes while running)
        if worker.state == "RUNNING" and worker.heartbeat:
            # Simulations spend many hours inside the solver without touching the DB; allow generous stall window
            stall_threshold = timezone.now() - timedelta(hours=18)
            if worker.heartbeat < stall_threshold:
                worker_data["is_stalled"] = True

        workers.append(worker_data)

    # Count active workers
    active_count = Worker.objects.filter(state__in=["STARTING", "RUNNING", "DRAINING"]).count()

    # Get admissible count (may be zero!)
    admissible = supervisor.admissible_workers()

    return {
        "workers": workers,
        "memory": {
            "total": vm.total,
            "available": vm.available,
            "used": vm.used,
            "percent": vm.percent,
        },
        "limits": {
            "admissible": admissible,
            "active": active_count,
            "peak_per_worker": supervisor.peak_memory,
            "can_add": active_count < admissible,  # Handles zero capacity correctly
            "no_capacity": admissible == 0,  # Explicit flag for UI
        },
        "queue": {"pending": _get_pending_jobs_count(), "running": _get_running_jobs_count()},
    }


def _get_pending_jobs_count():
    """Get count of pending jobs"""
    try:
        from django_tasks.backends.database.models import DBTaskResult

        return DBTaskResult.objects.filter(status="READY").count()
    except ImportError:
        return 0


def _get_running_jobs_count():
    """Get count of running jobs"""
    try:
        from django_tasks.backends.database.models import DBTaskResult

        return DBTaskResult.objects.filter(status="RUNNING").count()
    except ImportError:
        return 0


# ============================================================================
# SHARED HELPER FUNCTIONS
# ============================================================================


def _abort_worker(worker_id):
    """Shared logic to abort a worker - marks as FAILED and kills process"""
    # FIRST: IMMEDIATELY mark as FAILED in database (before fetching or killing)
    updated = Worker.objects.filter(worker_id=worker_id).update(state="FAILED", last_error_tail="Aborted by user")

    if not updated:
        raise Worker.DoesNotExist(f"Worker {worker_id} not found")

    # Now fetch the worker for process killing (state is already FAILED in DB)
    worker = Worker.objects.get(worker_id=worker_id)
    logger.info(f"Attempting to kill worker {worker_id}: pid={worker.pid}, state={worker.state}")

    # Now attempt to kill the process tree (handles child processes)
    if worker.pid:
        if not worker.is_same_process():
            logger.warning(f"Worker {worker_id} PID {worker.pid} is not the same process (PID reuse or dead)")
        else:
            try:
                import psutil

                try:
                    process = psutil.Process(worker.pid)

                    # Kill children first (recursive)
                    children = process.children(recursive=True)
                    for child in children:
                        try:
                            child.kill()
                        except psutil.NoSuchProcess:
                            pass

                    # Then kill parent
                    process.kill()

                    logger.warning(f"Forcefully killed worker {worker_id} and {len(children)} child processes")

                except psutil.NoSuchProcess:
                    logger.info(f"Worker {worker_id} process already dead")

            except Exception as e:
                logger.error(f"Failed to kill worker {worker_id}: {e}")
                # Process might not be killed, but we already marked it as FAILED


def _perform_tick_transitions():
    """Perform all worker state transitions - returns transition counts"""
    from datetime import timedelta

    now = timezone.now()
    startup_timeout = timedelta(seconds=int(os.environ.get("WORKER_STARTUP_TIMEOUT", "30")))

    transitions = {
        "failed_on_timeout": 0,
        "marked_dead": 0,
        "cleaned_up": 0,
        "tasks_requeued": 0,  # NEW
    }

    # 1. STARTING → FAILED on timeout
    for worker in Worker.objects.filter(state="STARTING"):
        if (now - worker.started_at) > startup_timeout:
            tail = _read_log_tail(worker.log_path)
            updated = Worker.objects.filter(pk=worker.pk, state="STARTING").update(
                state="FAILED", last_error_tail=tail or "Handshake timeout - worker failed to start"
            )
            if updated:
                logger.info(f"Marked worker {worker.worker_id} as FAILED (startup timeout)")
                transitions["failed_on_timeout"] += updated

    # 2. Check for dead processes (RUNNING/DRAINING → DEAD)
    for worker in Worker.objects.filter(state__in=["RUNNING", "DRAINING"]):
        if not worker.is_same_process():
            # DEFENSIVE FIX: Log comprehensive diagnostic information before marking DEAD
            # This helps diagnose future occurrences of the "worker disappears" issue
            is_alive = worker.is_alive()
            logger.info(
                f"Worker {worker.worker_id} validation failed - marking DEAD:\n"
                f"  state={worker.state}\n"
                f"  pid={worker.pid}\n"
                f"  pid_started_at={worker.pid_started_at}\n"
                f"  started_at={worker.started_at}\n"
                f"  heartbeat={worker.heartbeat}\n"
                f"  is_alive()={is_alive}"
            )

            # Try to get actual process info for diagnostics
            if worker.pid:
                try:
                    process = psutil.Process(worker.pid)
                    from datetime import datetime

                    actual_create_time = datetime.fromtimestamp(process.create_time())
                    stored_create_time = worker.pid_started_at

                    if stored_create_time:
                        time_diff = abs(process.create_time() - stored_create_time.timestamp())
                        logger.info(
                            f"  Process exists with PID {worker.pid}:\n"
                            f"    Actual create_time: {actual_create_time}\n"
                            f"    Stored pid_started_at: {stored_create_time}\n"
                            f"    Time difference: {time_diff:.2f}s"
                        )
                    else:
                        logger.info(
                            f"  Process exists with PID {worker.pid} but pid_started_at is None!\n"
                            f"    Actual create_time: {actual_create_time}"
                        )
                except psutil.NoSuchProcess:
                    logger.info(f"  psutil.NoSuchProcess for PID {worker.pid}")
                except psutil.AccessDenied:
                    logger.info(f"  psutil.AccessDenied for PID {worker.pid}")
                except Exception as e:
                    logger.info(f"  Process check failed: {type(e).__name__}: {e}")

            updated = Worker.objects.filter(pk=worker.pk).update(state="DEAD")
            if updated:
                logger.info(f"Marked worker {worker.worker_id} as DEAD (process not found or PID reused)")
                transitions["marked_dead"] += updated

    # 3. Clean up old dead/failed workers with mode-aware defaults
    # Standalone apps get 30 minutes (preserves evidence for debugging, slower/quirky environments)
    # Server installs get 1 minute (faster cleanup)
    default_cleanup = 1800 if os.getenv("STEELO_STANDALONE") == "1" else 60
    cleanup_age = timedelta(seconds=int(os.environ.get("WORKER_CLEANUP_AGE", str(default_cleanup))))

    old_workers = Worker.objects.filter(state__in=["DEAD", "FAILED"], started_at__lt=(now - cleanup_age))

    # DEFENSIVE FIX: Kill processes before deleting worker records to prevent orphans
    # CRITICAL: Only kill processes that we can positively verify belong to our worker
    # Old workers may have PID reuse - killing unverified PIDs would be catastrophic
    killed_count = 0
    skipped_count = 0
    for worker in old_workers:
        if worker.pid and worker.pid_started_at:
            # Initial check - skip if obviously not our process
            if not worker.is_same_process():
                logger.debug(
                    f"Skipping kill for worker {worker.worker_id} PID {worker.pid} "
                    f"(not same process, already dead, or cannot verify)"
                )
                skipped_count += 1
                continue

            # Get Process object and RE-VALIDATE before killing
            # CRITICAL: Prevents TOCTOU race where PID is reused between is_same_process() and kill()
            try:
                process = psutil.Process(worker.pid)

                # RE-VERIFY identity on this specific Process object before killing
                # Must use same tolerance as is_same_process() (1.0 second)
                process_create_time = process.create_time()
                expected_create_time = worker.pid_started_at.timestamp()
                time_diff = abs(process_create_time - expected_create_time)

                if time_diff > 1.0:
                    # PID was reused between check and now - DO NOT KILL
                    logger.warning(
                        f"Skipping kill for worker {worker.worker_id} PID {worker.pid} - "
                        f"PID reused between verification and kill "
                        f"(time diff: {time_diff:.2f}s > 1.0s tolerance)"
                    )
                    skipped_count += 1
                    continue

                # Identity re-verified on actual Process object - safe to kill
                children = process.children(recursive=True)
                for child in children:
                    try:
                        child.kill()
                    except psutil.NoSuchProcess:
                        pass

                # Kill parent
                process.kill()
                killed_count += 1
                logger.info(
                    f"Killed verified process {worker.pid} for worker {worker.worker_id} "
                    f"before cleanup ({len(children)} children, time_diff={time_diff:.3f}s)"
                )

            except psutil.NoSuchProcess:
                # Process already dead - that's fine
                logger.debug(f"Process {worker.pid} for worker {worker.worker_id} already dead")
                pass
            except psutil.AccessDenied as e:
                # Can't access process - skip rather than risk
                logger.warning(f"Skipping kill for worker {worker.worker_id} PID {worker.pid} - access denied: {e}")
                skipped_count += 1
            except Exception as e:
                # Log but continue - we'll still delete the database record
                logger.warning(
                    f"Failed to kill process {worker.pid} for worker {worker.worker_id}: {type(e).__name__}: {e}"
                )
                skipped_count += 1

    if killed_count or skipped_count:
        logger.info(
            f"Cleanup process handling: killed {killed_count} verified process(es), "
            f"skipped {skipped_count} unverified/dead PID(s)"
        )

    deleted_count = old_workers.delete()[0]
    if deleted_count:
        logger.info(f"Cleaned up {deleted_count} old dead/failed workers (cleanup age: {cleanup_age.total_seconds()}s)")
        transitions["cleaned_up"] = deleted_count

    # 4. Requeue orphaned tasks (NEW - safer than failing)
    try:
        from django_tasks.backends.database.models import DBTaskResult

        # Grace period to avoid race conditions
        grace = timedelta(minutes=int(os.environ.get("ORPHAN_GRACE_MINUTES", "5")))
        cutoff = timezone.now() - grace  # Compute in Python for simplicity

        # Get valid worker IDs in one query
        valid_workers = set(
            Worker.objects.filter(state__in=["STARTING", "RUNNING", "DRAINING"]).values_list("worker_id", flat=True)
        )

        # Find orphaned tasks (started >grace ago, worker gone)
        orphaned_tasks = DBTaskResult.objects.filter(status="RUNNING", started_at__lt=cutoff)

        for task in orphaned_tasks:
            if hasattr(task, "worker_ids") and task.worker_ids:
                if not any(wid in valid_workers for wid in task.worker_ids):
                    # For standalone users, mark orphaned tasks as FAILED instead of requeuing
                    # This prevents old tasks from cluttering the queue when users can't manually clean them
                    updated = DBTaskResult.objects.filter(
                        pk=task.pk,
                        status="RUNNING",  # Only if still running
                        started_at__lt=cutoff,  # Prevent race with late finishers
                    ).update(
                        status="FAILED",
                        worker_ids=[],  # Clear assignment (empty list, not NULL)
                        return_value=None,  # Standard for failed tasks
                        finished_at=timezone.now(),  # Mark completion time
                        exception_class_path="builtins.RuntimeError",  # Real importable exception
                        traceback="Worker disappeared - task orphaned",  # Error message
                    )

                    if updated:
                        transitions["tasks_requeued"] += 1  # Keep counter name for compatibility
                        logger.info(f"Marked orphaned task {task.id} as FAILED (worker gone)")

        # Log if we handled any orphaned tasks
        if transitions["tasks_requeued"]:
            logger.warning(f"Marked {transitions['tasks_requeued']} orphaned tasks as FAILED (workers disappeared)")

    except ImportError:
        pass  # django_tasks not available
    except Exception as e:
        logger.error(f"Error checking orphaned tasks: {e}")

    return transitions


# ============================================================================
# TICK ENDPOINT - All state transitions happen here
# ============================================================================


def workers_tick(request):
    """
    Perform all state transitions and health checks.
    This is the ONLY endpoint that modifies worker state.
    """
    _perform_tick_transitions()
    return worker_status_htmx(request)


# ============================================================================
# HTMX VIEWS
# ============================================================================


@require_http_methods(["GET", "POST", "DELETE"])  # Accept POST/DELETE when called from other views
def worker_status_htmx(request):
    """Return worker status as HTML fragment for HTMX - READ ONLY"""
    data = _get_worker_status_data()

    # Convert bytes to GB for template display
    if "memory" in data:
        data["memory"]["available_gb"] = data["memory"]["available"] / (1024**3)
        for worker in data.get("workers", []):
            if "memory" in worker:
                worker["memory_gb"] = worker["memory"] / (1024**3)
            if "memory_with_children" in worker:
                worker["memory_with_children"] = worker["memory_with_children"] / (1024**3)

    # Check if there are any dead workers for cleanup button
    data["has_dead_workers"] = any(
        worker.get("appears_dead") or worker.get("state") in ["FAILED", "DEAD"] for worker in data.get("workers", [])
    )

    return render(request, "steeloweb/partials/worker_status_fragment.html", {"data": data})


@require_POST
def add_worker_htmx(request):
    """Add exactly one worker with TRANSACTIONAL spawn"""
    # Parse force parameter from query string
    force = request.GET.get("force") == "true"

    try:
        with transaction.atomic():
            # Force SQLite writer lock for admission control (single write)
            AdmissionControl.objects.update_or_create(pk=1, defaults={"timestamp": timezone.now()})

            # Now safely check capacity with writer lock held
            active = Worker.objects.filter(state__in=["STARTING", "RUNNING", "DRAINING"]).count()
            capacity = supervisor.admissible_workers()

            if not force and active >= capacity:
                logger.warning(f"Cannot add worker: {active}/{capacity} at capacity (force={force})")
                # Return current status without error to update UI
                return worker_status_htmx(request)

            # Generate IDs
            import uuid

            worker_id = uuid.uuid4().hex[:8]
            launch_token = uuid.uuid4().hex[:16]

            # Determine log path (use platform-appropriate directory)
            if os.environ.get("WORKER_LOG_DIR"):
                log_dir = Path(os.environ["WORKER_LOG_DIR"])
            else:
                # Use platform-appropriate temp directory
                import tempfile

                log_dir = Path(tempfile.gettempdir()) / "steelmodel_workers"

            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"worker_{worker_id}.log"

            # Log forced worker creation
            if force:
                vm = psutil.virtual_memory()
                logger.warning(
                    f"FORCED worker creation: {worker_id} | "
                    f"Memory: {vm.available / (1024**3):.1f}GB available, "
                    f"{capacity} admissible capacity | "
                    f"Active workers: {active}"
                )

            # Create worker record
            worker = Worker.objects.create(
                worker_id=worker_id, launch_token=launch_token, state="STARTING", log_path=str(log_path)
            )

            # Launch process OUTSIDE transaction to avoid holding lock
            transaction.on_commit(lambda: _spawn_worker_process(worker))

            logger.info(f"Created worker {worker_id} (will spawn after commit, force={force})")

    except Exception as e:
        logger.error(f"Failed to add worker: {e}")

    # Return updated status
    return worker_status_htmx(request)


def _spawn_worker_process(worker):
    """Actually spawn the worker process (called after transaction commits)"""
    try:
        from .worker_supervisor import supervisor

        # Launch the process
        pid = supervisor._launch_process(worker.worker_id, worker.launch_token, Path(worker.log_path))

        if pid:
            # Get process create time for PID reuse protection
            try:
                import psutil

                process = psutil.Process(pid)
                from datetime import datetime, timezone as dt_timezone

                create_time = datetime.fromtimestamp(process.create_time(), tz=dt_timezone.utc)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                create_time = timezone.now()

            # Update worker with PID and create time
            Worker.objects.filter(worker_id=worker.worker_id).update(pid=pid, pid_started_at=create_time)
            logger.info(f"Spawned worker {worker.worker_id} with PID {pid}")
        else:
            # Mark as failed if spawn failed
            Worker.objects.filter(worker_id=worker.worker_id).update(
                state="FAILED", last_error_tail="Failed to launch process"
            )
            logger.error(f"Failed to spawn worker {worker.worker_id}")

    except Exception as e:
        logger.error(f"Error spawning worker {worker.worker_id}: {e}")
        Worker.objects.filter(worker_id=worker.worker_id).update(state="FAILED", last_error_tail=str(e)[:1000])


@require_POST
def drain_worker_htmx(request, worker_id=None):
    """Drain one worker gracefully (complete current task then exit)"""
    if worker_id:
        # Drain specific worker
        worker = Worker.objects.filter(worker_id=worker_id, state="RUNNING").first()
        if not worker:
            logger.warning(f"Worker {worker_id} not found or not running")
            return worker_status_htmx(request)
    else:
        # Get oldest running worker
        worker = Worker.objects.filter(state="RUNNING").order_by("started_at").first()
        if not worker:
            logger.warning("No running workers to drain")
            return worker_status_htmx(request)

    # Set to DRAINING - worker will exit after current task
    updated = Worker.objects.filter(worker_id=worker.worker_id, state="RUNNING").update(state="DRAINING")

    if updated:
        logger.info(f"Worker {worker.worker_id} set to drain (will exit after current job)")

    return worker_status_htmx(request)


@require_http_methods(["DELETE"])
def abort_worker_htmx(request, worker_id):
    """Forcefully abort a worker (kills current task - DATA LOSS WARNING)"""
    try:
        _abort_worker(worker_id)
    except Worker.DoesNotExist:
        # Worker already gone (maybe cleaned up by tick)
        logger.info(f"Worker {worker_id} already gone, nothing to abort")
    return worker_status_htmx(request)


def cleanup_workers_htmx(request):
    """Immediately cleanup all dead/failed workers"""
    deleted = Worker.objects.filter(state__in=["DEAD", "FAILED"]).delete()[0]
    logger.info(f"Cleaned up {deleted} dead/failed workers")
    return worker_status_htmx(request)


# ============================================================================
# JSON API ENDPOINTS
# ============================================================================


@require_http_methods(["GET"])
def worker_status_json(request):
    """Get current worker status as JSON - READ ONLY"""
    data = _get_worker_status_data()
    return JsonResponse(data)


@require_POST
def add_worker_json(request):
    """Add exactly one worker - JSON response"""
    # Parse force parameter from JSON body
    import json

    try:
        body = json.loads(request.body or "{}")
        force = body.get("force", False)
    except json.JSONDecodeError:
        force = False

    # Same transactional logic as HTMX version
    try:
        with transaction.atomic():
            # Force SQLite writer lock for admission control (single write)
            AdmissionControl.objects.update_or_create(pk=1, defaults={"timestamp": timezone.now()})

            # Now safely check capacity with writer lock held
            active = Worker.objects.filter(state__in=["STARTING", "RUNNING", "DRAINING"]).count()
            capacity = supervisor.admissible_workers()

            if not force and active >= capacity:
                return JsonResponse({"status": "error", "message": f"At capacity ({active}/{capacity})"}, status=400)

            # Create worker (same as HTMX version)
            import uuid

            worker_id = uuid.uuid4().hex[:8]
            launch_token = uuid.uuid4().hex[:16]

            # Use appropriate log directory
            if os.environ.get("WORKER_LOG_DIR"):
                log_dir = Path(os.environ["WORKER_LOG_DIR"])
            else:
                import tempfile

                log_dir = Path(tempfile.gettempdir()) / "steelmodel_workers"

            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"worker_{worker_id}.log"

            # Log forced worker creation
            if force:
                vm = psutil.virtual_memory()
                logger.warning(
                    f"FORCED worker creation: {worker_id} | "
                    f"Memory: {vm.available / (1024**3):.1f}GB available, "
                    f"{capacity} admissible capacity | "
                    f"Active workers: {active}"
                )

            worker = Worker.objects.create(
                worker_id=worker_id, launch_token=launch_token, state="STARTING", log_path=str(log_path)
            )

            transaction.on_commit(lambda: _spawn_worker_process(worker))

            logger.info(f"Created worker {worker_id} via JSON API (force={force})")

            return JsonResponse({"status": "success", "worker_id": worker_id})

    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=500)


@require_POST
def drain_worker_json(request, worker_id=None):
    """Drain worker gracefully - JSON response"""
    if worker_id:
        # Drain specific worker
        updated = Worker.objects.filter(worker_id=worker_id, state="RUNNING").update(state="DRAINING")
    else:
        # Drain oldest worker
        worker = Worker.objects.filter(state="RUNNING").order_by("started_at").first()
        if worker:
            updated = Worker.objects.filter(worker_id=worker.worker_id, state="RUNNING").update(state="DRAINING")
            worker_id = worker.worker_id
        else:
            updated = 0

    if updated:
        logger.info(f"Worker {worker_id} set to drain")
        return JsonResponse({"status": "success", "worker_id": worker_id})
    else:
        return JsonResponse({"status": "error", "message": "No worker to drain"}, status=400)


@csrf_exempt
@require_POST
def drain_all_workers_json(request):
    """Drain all active workers for graceful shutdown - JSON response

    CSRF exempt because this is called from Electron main process during shutdown,
    which has no CSRF token. This endpoint is ONLY accessible in standalone mode
    from localhost for security.

    Security: Restricted to standalone mode + localhost to prevent unauthorized
    worker draining in server/development deployments.
    """
    # Security check: Only allow in standalone mode
    if os.getenv("STEELO_STANDALONE") != "1":
        logger.warning("drain_all_workers_json called outside standalone mode - rejected")
        return JsonResponse(
            {"success": False, "error": "This endpoint is only available in standalone mode"}, status=403
        )

    # Security check: Only allow from localhost
    remote_addr = request.META.get("REMOTE_ADDR", "")
    if remote_addr not in ["127.0.0.1", "::1", "localhost"]:
        logger.warning(f"drain_all_workers_json called from non-localhost address: {remote_addr} - rejected")
        return JsonResponse({"success": False, "error": "This endpoint is only accessible from localhost"}, status=403)

    try:
        # Mark all active workers as DRAINING
        updated = Worker.objects.filter(state__in=["STARTING", "RUNNING"]).update(state="DRAINING")

        logger.info(f"Marked {updated} workers as DRAINING for shutdown")
        return JsonResponse({"success": True, "workers_draining": updated})
    except Exception as e:
        logger.error(f"Failed to drain workers: {e}")
        return JsonResponse({"success": False, "error": str(e)}, status=500)


@require_http_methods(["DELETE"])
def abort_worker_json(request, worker_id):
    """Force abort a worker - JSON response"""
    try:
        Worker.objects.get(worker_id=worker_id)
    except Worker.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Worker not found"}, status=404)

    # Require explicit confirmation
    if request.GET.get("confirm") != "yes":
        return JsonResponse(
            {"status": "error", "message": "Abort requires confirmation (add ?confirm=yes)"}, status=400
        )

    _abort_worker(worker_id)
    return JsonResponse({"status": "success"})


@require_POST
def workers_tick_json(request):
    """Perform state transitions - JSON response"""
    transitions = _perform_tick_transitions()
    return JsonResponse({"status": "success", "transitions": transitions})


# ============================================================================
# WORKER AVAILABILITY CHECK (GENERALIZED FOR SIMULATION & DATA PREPARATION)
# ============================================================================


def check_worker_availability(context="simulation"):
    """
    Check if workers are available to run a task (simulation or data preparation).

    Args:
        context: Either "simulation" or "data_preparation" to customize messaging

    Returns:
        dict: {
            'can_start': bool,
            'status': 'ok' | 'no_workers' | 'all_busy' | 'no_capacity',
            'message': str,  # HTML message (must be marked safe with mark_safe() for links)
            'data': dict,  # Additional context for display
        }

    data payload structure:
        {
            'active_workers': int,      # Count of STARTING/RUNNING/DRAINING workers
            'admissible_workers': int,  # Max workers system can support
            'available_memory_gb': float,
            'pending_tasks': int,
            'running_tasks': int,
            'modelrun_list_url': str,   # Link to worker management page
        }

    Note: Messages containing HTML (like links) must be marked safe:
        from django.utils.safestring import mark_safe
        message = mark_safe(f'...{url}...')
    Or rendered in template with |safe filter.
    """
    from django.urls import reverse
    from django.utils.safestring import mark_safe

    # Validate context parameter
    if context not in ["simulation", "data_preparation"]:
        raise ValueError(f"Invalid context: {context}. Must be 'simulation' or 'data_preparation'")

    # Customize messages based on context
    if context == "simulation":
        action_noun = "simulation"
        action_noun_plural = "simulations"
        retry_instruction = "Return here and click 'Run Simulation' again"
    else:  # data_preparation
        action_noun = "data preparation"
        action_noun_plural = "data preparations"
        retry_instruction = "Return here and click 'Prepare Data' again"

    # Get current worker status
    worker_data = _get_worker_status_data()

    # Count only healthy workers (exclude dead/stalled)
    healthy_workers = [
        w
        for w in worker_data["workers"]
        if w["state"] in ["STARTING", "RUNNING", "DRAINING"]
        and not w.get("appears_dead", False)
        and not w.get("is_stalled", False)
    ]
    active_workers = len(healthy_workers)

    # Extract metrics
    admissible_workers = worker_data["limits"]["admissible"]
    pending_tasks = worker_data["queue"]["pending"]
    running_tasks = worker_data["queue"]["running"]
    available_memory_gb = worker_data["memory"]["available"] / (1024**3)

    # Build data payload
    modelrun_list_url = reverse("modelrun-list")
    data = {
        "active_workers": active_workers,
        "admissible_workers": admissible_workers,
        "available_memory_gb": available_memory_gb,
        "pending_tasks": pending_tasks,
        "running_tasks": running_tasks,
        "modelrun_list_url": modelrun_list_url,
    }

    # Evaluate conditions in priority order
    if admissible_workers == 0 and active_workers == 0:
        # System lacks resources AND no workers running to process queue
        message = mark_safe(
            f"⚠️ Cannot start {action_noun} - Insufficient system resources<br><br>"
            f"Your system does not have enough available memory to run workers. "
            f"Workers require sufficient RAM to operate.<br><br>"
            f"<strong>Current available memory:</strong> {available_memory_gb:.1f}GB<br><br>"
            f"Please close other applications to free up memory, or run {action_noun_plural} on a system with more RAM."
        )
        logger.warning(
            f"{action_noun.capitalize()} start blocked: no system capacity (available: {available_memory_gb:.1f}GB)"
        )
        return {
            "can_start": False,
            "status": "no_capacity",
            "message": message,
            "data": data,
        }

    if active_workers == 0:
        # No workers running
        message = mark_safe(
            f"⚠️ Cannot start {action_noun} - No workers available<br><br>"
            f"The {action_noun} requires a worker to run, but no workers are currently active. "
            f'Please start at least one worker from the <a href="{modelrun_list_url}">Simulation Runs</a> page.<br><br>'
            f"<strong>What to do:</strong><br>"
            f'1. Go to the <a href="{modelrun_list_url}">Simulation Runs</a> page<br>'
            f'2. Expand the "Worker Status" section<br>'
            f'3. Click "Add Worker" to start a new worker<br>'
            f"4. {retry_instruction}"
        )
        logger.warning(f"{action_noun.capitalize()} start blocked: no workers available")
        return {
            "can_start": False,
            "status": "no_workers",
            "message": message,
            "data": data,
        }

    if active_workers > 0 and (pending_tasks > 0 or running_tasks >= active_workers):
        # All workers busy
        message = mark_safe(
            f"⚠️ All workers are busy<br><br>"
            f"There are currently {active_workers} worker(s) running, but all are busy processing other tasks. "
            f"Your {action_noun} will be queued and will start as soon as a worker becomes available.<br><br>"
            f"<strong>Currently {pending_tasks} task(s) are waiting in queue.</strong>"
        )
        logger.info(
            f"{action_noun.capitalize()} start warning shown: all workers busy "
            f"(active={active_workers}, pending={pending_tasks}, running={running_tasks})"
        )
        return {
            "can_start": True,
            "status": "all_busy",
            "message": message,
            "data": data,
        }

    # Workers available
    return {
        "can_start": True,
        "status": "ok",
        "message": "",
        "data": data,
    }


def check_worker_availability_for_simulation():
    """
    Legacy wrapper for backward compatibility with existing simulation code.
    Check if workers are available to run a simulation.

    Returns:
        dict: Same structure as check_worker_availability()
    """
    return check_worker_availability(context="simulation")
