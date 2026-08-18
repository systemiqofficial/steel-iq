"""Global fleet-motion recording: ``pam_motions.csv``, all countries, every run.

The capacity-policy call sites in :mod:`steelo.capacity_policy.handlers` observe
every runtime fleet mutation — closes, switches, renovations, builds and the
pipeline construction→operating flips — but their own recorder is China-only and
exists only when ``--enable-capacity-policy`` binds the policy. This module holds
a second, always-bound :class:`~steelo.capacity_policy.recorder.CapacityPolicyRecorder`
those same call sites dual-write into, so every run leaves a complete global
motions file beside the run's other data outputs. Binding mirrors the
``handlers._policy`` pattern: module-level state, bound fresh at bootstrap so no
rows survive successive runs in one process.
"""

import csv
import logging
from pathlib import Path

from steelo.capacity_policy.recorder import MOTIONS_COLUMNS, CapacityPolicyRecorder

logger = logging.getLogger(__name__)

MOTIONS_FILE = "pam_motions.csv"

_recorder: CapacityPolicyRecorder | None = None


def bind_global_motions() -> None:
    """Install a fresh recorder for this run, replacing any earlier binding."""
    global _recorder
    _recorder = CapacityPolicyRecorder()


def unbind_global_motions() -> None:
    """Drop the binding; the motion call sites stop recording globally."""
    global _recorder
    _recorder = None


def global_motions_recorder() -> CapacityPolicyRecorder | None:
    """The recorder the motion call sites write all-country rows into, or None."""
    return _recorder


def flush_global_motions(output_dir: Path) -> None:
    """Write the run's global motions CSV, or nothing at all while unbound.

    Args:
        output_dir: Directory to write into — ``<output>/data`` on a real run;
            created if it does not exist. Headers are written even when no
            motion was recorded.
    """
    recorder = _recorder
    if recorder is None:
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / MOTIONS_FILE).open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MOTIONS_COLUMNS)
        writer.writeheader()
        writer.writerows(recorder.motions)
    logger.info("[MOTIONS] wrote %s rows=%d", output_dir / MOTIONS_FILE, len(recorder.motions))
