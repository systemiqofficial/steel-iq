"""Utility functions for steeloweb."""

import os


def get_log_file_path(model_run_id: int) -> str | None:
    """
    Get the absolute path to the log file for a model run.

    Returns the path only if STEELO_LOG_DIR is set and the file exists.
    Used by the UI to display/open log files.

    Tries UUID-based filename first (new runs), then falls back to
    PK-based filename (legacy runs) for backward compatibility.

    Args:
        model_run_id: The ID of the ModelRun

    Returns:
        Absolute path to log file if it exists, None otherwise
    """
    from steeloweb.models import ModelRun

    log_dir = os.environ.get("STEELO_LOG_DIR")
    if not log_dir:
        return None

    try:
        modelrun = ModelRun.objects.get(id=model_run_id)

        # Try UUID-based filename first (new runs)
        uuid_log_path = os.path.join(log_dir, f"model-run-{modelrun.log_file_uuid}.log")
        if os.path.exists(uuid_log_path):
            return uuid_log_path

        # Fallback to PK-based filename (legacy runs)
        pk_log_path = os.path.join(log_dir, f"model-run-{model_run_id}.log")
        if os.path.exists(pk_log_path):
            return pk_log_path

        return None

    except ModelRun.DoesNotExist:
        return None
