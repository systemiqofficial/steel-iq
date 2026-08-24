"""Provenance record (``run.json``) for one run directory.

A run pairs one input set with one cost set. The manifest pins what produced the
outputs and refuses to let a later invocation mix in different provenance — add
years to a run freely, but a changed xlsx or a different input set is a new run.
"""

import hashlib
import json
import logging
import subprocess
from datetime import datetime, timezone
from typing import Any

import boa
from boa.config import settings
from boa.config.paths import PathConfig

SCHEMA_VERSION = 1


def _sha256(path) -> str | None:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL, text=True
        ).strip()
    except Exception:
        return None


def provenance(path_config: PathConfig) -> dict[str, Any]:
    """Everything that must stay fixed within a run."""
    return {
        "input_set": path_config.input_set,
        "cost_set": path_config.cost_set,
        "input_data_sha256": _sha256(path_config.input_data_path),
        "boa_version": boa.__version__,
        "settings": {
            "random_seed": settings.RANDOM_SEED,
            "min_survivor_fraction": settings.MIN_SURVIVOR_FRACTION,
            "overscale_sampling_means": settings.OVERSCALE_SAMPLING_MEANS,
            "lifetimes": settings.LIFETIMES,
            "era5_data_year": settings.ERA5_DATA_YEAR,
        },
    }


def load(path_config: PathConfig) -> dict[str, Any] | None:
    p = path_config.run_manifest_path
    return json.loads(p.read_text()) if p.exists() else None


def record_invocation(path_config: PathConfig, command: str, argv: list[str]) -> dict[str, Any]:
    """Create the manifest on first use, verify provenance on later ones, append this invocation."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    current = provenance(path_config)
    manifest = load(path_config)

    if manifest is None:
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "run": path_config.run,
            "created_at": now,
            "provenance": current,
            "invocations": [],
        }
    else:
        diffs = {
            k: (manifest["provenance"].get(k), v) for k, v in current.items() if manifest["provenance"].get(k) != v
        }
        if diffs:
            raise RuntimeError(
                f"Run '{path_config.run}' was produced with different provenance: {diffs}. "
                f"Use a new --run (or --costs/--inputs set) instead of mixing outputs."
            )

    manifest["updated_at"] = now
    manifest["invocations"].append({"at": now, "command": command, "argv": argv, "git_sha": _git_sha()})
    path_config.run_dir.mkdir(parents=True, exist_ok=True)
    path_config.run_manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    logging.info(f"Run manifest: {path_config.run_manifest_path}")
    return manifest
