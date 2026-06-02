"""Template context processors for steeloweb."""

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings


def _git(args: list[str]) -> str | None:
    """Run a git command and return stripped stdout, or None on failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=settings.BASE_DIR,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _load_build_info() -> dict[str, str | None]:
    """Load build_info.json written by build-django.js, falling back to live git for dev."""
    bundle_file = Path(settings.BASE_DIR).parent / "build_info.json"
    if bundle_file.is_file():
        try:
            return json.loads(bundle_file.read_text())
        except (OSError, json.JSONDecodeError):
            pass

    # Dev fallback: live git state, version from electron package.json if available
    version = "dev"
    pkg_json = Path(settings.BASE_DIR).parent / "electron" / "package.json"
    if pkg_json.is_file():
        try:
            version = json.loads(pkg_json.read_text()).get("version", "dev")
        except (OSError, json.JSONDecodeError):
            pass

    return {
        "version": version,
        "commit": _git(["rev-parse", "--short", "HEAD"]),
        "branch": _git(["rev-parse", "--abbrev-ref", "HEAD"]),
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "is_dev": True,
    }


_BUILD_INFO = _load_build_info()


def build_info(request):
    """Expose build_info dict to all templates."""
    return {"build_info": _BUILD_INFO}
