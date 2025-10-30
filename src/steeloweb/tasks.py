from django.utils import timezone
import threading
from contextlib import contextmanager

from django_tasks import task

from .models import ModelRun, ResultImages, DataPreparation, SimulationPlot
from .services import DataPreparationService


@contextmanager
def heartbeat_context(modelrun_id: int, interval: int = 30):
    """
    Context manager that starts a heartbeat thread to update modelrun.updated_at
    every `interval` seconds. This allows reliable crash detection and immediate
    progress feedback.
    """
    stop_event = threading.Event()

    def heartbeat():
        while not stop_event.is_set():
            try:
                modelrun = ModelRun.objects.get(pk=modelrun_id)
                # Just save to update the updated_at timestamp
                modelrun.save(update_fields=["updated_at"])
            except ModelRun.DoesNotExist:
                break
            except Exception:
                # Continue heartbeat even if there are temporary database issues
                pass

            # Wait for interval or until stop is requested
            stop_event.wait(interval)

    # Start heartbeat thread
    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()

    try:
        yield
    finally:
        # Stop heartbeat
        stop_event.set()
        thread.join(timeout=1)  # Wait up to 1 second for thread to finish


@task()
def run_simulation_task(modelrun_id: int) -> None:
    import traceback
    import logging
    import os
    from logging.handlers import RotatingFileHandler

    logger = logging.getLogger(__name__)
    modelrun = ModelRun.objects.get(pk=modelrun_id)

    # Setup file logging handler if STEELO_LOG_DIR is set
    log_dir = os.environ.get("STEELO_LOG_DIR")
    file_handler = None

    if log_dir:
        try:
            # Ensure log directory exists
            os.makedirs(log_dir, exist_ok=True)

            # Create log file path using UUID for uniqueness across app reinstalls
            log_file_path = os.path.join(log_dir, f"model-run-{modelrun.log_file_uuid}.log")

            # Write metadata header BEFORE creating handler to avoid file locking issues on Windows
            with open(log_file_path, "w") as f:
                f.write("# Steel Model Simulation Log\n")
                f.write(f"# ModelRun ID: {modelrun_id}\n")
                f.write(f"# ModelRun Name: {modelrun.name or '(unnamed)'}\n")
                f.write(f"# Started: {modelrun.started_at.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"# UUID: {modelrun.log_file_uuid}\n")
                f.write(f"# {'=' * 60}\n\n")

            # Create rotating file handler (100MB max, 5 backups)
            # Handler opens in append mode by default, preserving the header
            file_handler = RotatingFileHandler(
                log_file_path,
                maxBytes=100 * 1024 * 1024,  # 100MB
                backupCount=5,
                encoding="utf-8",
            )

            # Configure formatter with millisecond precision
            formatter = logging.Formatter(
                "%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
            file_handler.setFormatter(formatter)
            file_handler.setLevel(logging.INFO)

            # Add handler to steeloweb and steelo loggers (they have propagate=False in settings)
            steeloweb_logger = logging.getLogger("steeloweb")
            steeloweb_logger.addHandler(file_handler)
            steelo_logger = logging.getLogger("steelo")
            steelo_logger.addHandler(file_handler)

            logger.info(f"Performance logging enabled for ModelRun {modelrun_id} at {log_file_path}")
        except Exception as e:
            logger.warning(f"Failed to set up file logging for ModelRun {modelrun_id}: {e}")
            file_handler = None

    try:
        # Start heartbeat for crash detection and immediate progress feedback
        with heartbeat_context(modelrun_id, interval=30):
            try:
                logger.info(f"Running simulation for ModelRun {modelrun_id}")
                results = modelrun.run()
                modelrun.results = results if results is not None else {}

                # Check if simulation was cancelled
                if isinstance(results, dict) and results.get("status") == "cancelled":
                    logger.info(f"Simulation was cancelled for ModelRun {modelrun_id}")
                    # ModelRun state is already set to CANCELLED by SimulationRunner
                    # Don't try to capture CSV or plots for cancelled runs
                    return

                # Capture CSV results
                if modelrun.capture_result_csv():
                    logger.info(f"Attached CSV results to ModelRun {modelrun_id}")

                    # Capture simulation plots generated from CSV
                    plots = SimulationPlot.capture_simulation_plots(modelrun)
                    if plots:
                        logger.info(f"Captured {len(plots)} simulation plots for ModelRun {modelrun_id}")
                    else:
                        logger.warning(f"No simulation plots found for ModelRun {modelrun_id}")
                else:
                    logger.warning(f"No CSV results found for ModelRun {modelrun_id}")

                # Create ResultImages after successful model run
                ResultImages.create_from_plots(modelrun)

                modelrun.state = ModelRun.RunState.FINISHED
            except Exception as e:
                modelrun.state = ModelRun.RunState.FAILED
                # Capture full traceback
                tb = traceback.format_exc()
                modelrun.error_message = f"{str(e)}\n\nTraceback:\n{tb}"
                logger.error(f"Simulation failed for ModelRun {modelrun_id}: {str(e)}\n{tb}")
                # Ensure results is not None even on failure
                if modelrun.results is None:
                    modelrun.results = {}

            modelrun.finished_at = timezone.now()
            modelrun.save()
    finally:
        # Clean up file handler to prevent accumulation in long-lived workers
        if file_handler:
            try:
                # Remove handler from both loggers
                steeloweb_logger = logging.getLogger("steeloweb")
                steeloweb_logger.removeHandler(file_handler)
                steelo_logger = logging.getLogger("steelo")
                steelo_logger.removeHandler(file_handler)
                file_handler.close()
                logger.info(f"Closed log file handler for ModelRun {modelrun_id}")
            except Exception as e:
                # Don't let cleanup errors fail the task
                logger.warning(f"Error cleaning up file handler for ModelRun {modelrun_id}: {e}")


@task()
def prepare_data_task(preparation_id: int) -> None:
    """Task to prepare data from packages."""
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"prepare_data_task called with preparation_id: {preparation_id}")

    # Create a simple heartbeat function for data preparation
    # DataPreparation doesn't have updated_at, so we'll update a timestamp field
    def heartbeat_for_preparation():
        stop_event = threading.Event()

        def heartbeat():
            while not stop_event.is_set():
                try:
                    prep = DataPreparation.objects.get(pk=preparation_id)
                    prep.save(update_fields=["updated_at"])
                except Exception:
                    pass
                stop_event.wait(30)

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        return stop_event, thread

    try:
        preparation = DataPreparation.objects.get(pk=preparation_id)
        logger.info(f"Starting data preparation task for {preparation_id}, current status: {preparation.status}")

        # Start heartbeat for data preparation
        stop_event, heartbeat_thread = heartbeat_for_preparation()

        try:
            service = DataPreparationService()
            success, message = service.prepare_data(preparation)
        finally:
            # Stop heartbeat
            stop_event.set()
            heartbeat_thread.join(timeout=1)

        if not success:
            # Log error
            preparation.status = DataPreparation.Status.FAILED
            preparation.error_message = message
            preparation.save()
            logger.error(f"Data preparation failed for {preparation_id}: {message}")
        else:
            logger.info(f"Data preparation completed successfully for {preparation_id}")

    except Exception as e:
        logger.exception(f"Unexpected error in prepare_data_task for {preparation_id}")
        try:
            preparation = DataPreparation.objects.get(pk=preparation_id)
            preparation.status = DataPreparation.Status.FAILED
            preparation.error_message = str(e)
            preparation.save()
        except:  # noqa
            pass
