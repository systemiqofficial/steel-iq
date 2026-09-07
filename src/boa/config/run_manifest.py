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
from boa.config import physical_parameters
from boa.config.paths import PathConfig
from boa.model.bisection import SearchParams

SCHEMA_VERSION = 3


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


def non_scenario_params_hash(search_params: SearchParams) -> str:
    """
    Stable 4-hex digest over everything a run folder name must fork on: physical constants
    plus the full search-quality dial set. Baked directly into the run directory name (see
    ``paths.make_run_dirname``), so the fork decision is automatic -- the same run label with
    the same non-scenario parameters always resolves to the same directory, and a changed
    parameter always resolves to a different one, with no manifest scan needed.

    Narrower in scope than `provenance()`: `input_set`/`cost_set`/the xlsx hash are allowed
    to evolve within a run and are separately guarded by `record_invocation`'s field-by-field
    diff, not folded into this hash.
    """
    payload = {"lifetimes": physical_parameters.LIFETIMES, "search_params": search_params.as_dict()}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:4]


def provenance(path_config: PathConfig, search_params: SearchParams = SearchParams()) -> dict[str, Any]:
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
        "search_params": search_params.as_dict(),
        "lifetimes": physical_parameters.LIFETIMES,
        "era5_data_year": weather_year,
    }


def load(path_config: PathConfig) -> dict[str, Any] | None:
    p = path_config.run_manifest_path
    return json.loads(p.read_text()) if p.exists() else None


def record_invocation(
    path_config: PathConfig,
    command: str,
    argv: list[str],
    parameters: dict[str, Any] | None = None,
    search_params: SearchParams = SearchParams(),
) -> dict[str, Any]:
    """Create the manifest on first use, verify provenance on later ones, append this invocation.

    ``parameters`` holds the fully resolved settings (defaults expanded), so a bare
    ``boa-run`` is reconstructible from the manifest even though its argv is empty.
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    current = provenance(path_config, search_params)
    manifest = load(path_config)

    if manifest is None:
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "run": path_config.run,
            "non_scenario_params_hash": non_scenario_params_hash(search_params),
            "created_at": now,
            "provenance": current,
            "invocations": [],
        }
    elif manifest.get("schema_version") != SCHEMA_VERSION:
        # An older-schema manifest predates fields the current provenance always carries
        # (e.g. v1 -> v2 added `availability_signature`, v2 -> v3 flattened `settings` into
        # the full `search_params`), so every field-by-field comparison against it would
        # report a spurious difference. Refuse rather than guess which provenance the
        # outputs in this dir actually came from.
        raise RuntimeError(
            f"Run '{path_config.run}' has a schema-{manifest.get('schema_version')} manifest, "
            f"but this build writes schema {SCHEMA_VERSION}. Its provenance predates the full "
            f"SearchParams record and cannot be verified against it -- use a new --run."
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
