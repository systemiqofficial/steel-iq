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

SCHEMA_VERSION = 2


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


def _availability_signature(path_config: PathConfig) -> str | None:
    """
    The availability signature the installed ceiling stores were built with.

    Recorded so the drift guard actually fires: the ceilings are baked into the design
    cache, so rebuilding max-capacity against a different layer set or density and then
    reusing a warm cache is wrong with no downstream symptom. `mixed:` marks a live dir
    holding stores from more than one build, which is itself the thing to notice.
    """
    from boa.cds.install import stored_signature  # lazy: config must not import the cds package

    stores = sorted(path_config.zarr_dir.glob("max_capacity_*.zarr")) if path_config.zarr_dir.exists() else []
    found = {sig for sig in (stored_signature(store) for store in stores) if sig}
    if not found:
        return None
    return found.pop() if len(found) == 1 else "mixed:" + ",".join(sorted(found))


def provenance(path_config: PathConfig) -> dict[str, Any]:
    """Everything that must stay fixed within a run."""
    from boa.inputs.profiles import detect_weather_year  # lazy: config must stay importable without the inputs package

    try:
        weather_year = detect_weather_year(path_config)
    except (FileNotFoundError, ValueError):
        weather_year = None
    return {
        "input_set": path_config.input_set,
        "cost_set": path_config.cost_set,
        "input_data_sha256": _sha256(path_config.input_data_path),
        "availability_signature": _availability_signature(path_config),
        "boa_version": boa.__version__,
        "settings": {
            "overscale_sampling_k": settings.OVERSCALE_SAMPLING_K,
            "lifetimes": settings.LIFETIMES,
            "era5_data_year": weather_year,
        },
    }


def load(path_config: PathConfig) -> dict[str, Any] | None:
    p = path_config.run_manifest_path
    return json.loads(p.read_text()) if p.exists() else None


def record_invocation(
    path_config: PathConfig, command: str, argv: list[str], parameters: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Create the manifest on first use, verify provenance on later ones, append this invocation.

    ``parameters`` holds the fully resolved settings (defaults expanded), so a bare
    ``boa-run`` is reconstructible from the manifest even though its argv is empty.
    """
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
    elif manifest.get("schema_version") != SCHEMA_VERSION:
        # A v1 manifest predates `availability_signature`, so every field-by-field
        # comparison against it would report a spurious difference. Refuse rather than
        # guess which of the two provenances the outputs in this dir actually came from.
        raise RuntimeError(
            f"Run '{path_config.run}' has a schema-{manifest.get('schema_version')} manifest, "
            f"but this build writes schema {SCHEMA_VERSION}. Its outputs predate the "
            f"availability signature and cannot be verified against it -- use a new --run."
        )
    else:
        diffs = {
            k: (manifest["provenance"].get(k), v) for k, v in current.items() if manifest["provenance"].get(k) != v
        }
        if diffs:
            raise RuntimeError(
                f"Run '{path_config.run}' was produced with different provenance: {diffs}. "
                f"Use a new --run (or --cost-input/--weather-input set) instead of mixing outputs."
            )

    manifest["updated_at"] = now
    invocation: dict[str, Any] = {"at": now, "command": command, "argv": argv, "git_sha": _git_sha()}
    if parameters is not None:
        invocation["parameters"] = parameters
    manifest["invocations"].append(invocation)
    path_config.run_dir.mkdir(parents=True, exist_ok=True)
    path_config.run_manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    logging.info(f"Run manifest: {path_config.run_manifest_path}")
    return manifest
