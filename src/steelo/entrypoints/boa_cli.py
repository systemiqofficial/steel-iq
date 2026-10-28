"""
CLI command that reruns BOA, the baseload power model, at the release steel-iq pins.

`steelo-boa-run` hands every argument unchanged to BOA's `boa-lcoe`, run by `uvx` in its own
environment from the pinned tag, with BOA's dependencies as they were at the pinned date, so
`steelo-boa-run --help` shows BOA's own options. It ends by printing the `run_simulation --boa-run`
value for the run BOA produced.
"""

import shutil
import subprocess
import sys

BOA_REPOSITORY = "https://github.com/systemiqofficial/baseload-optimisation-atlas"
# Moved only when no LCOE-changing code landed since the release that built geo-data's default LCOE.
BOA_PIN = "v1.10.0"
# uvx ignores BOA's lock, so packages newer than the release's tested environment are left out; moves with the pin.
BOA_EXCLUDE_NEWER = "2026-10-28T09:30:00Z"
RUN_PREFIX = b"BOA_RUN="
UV_INSTALL_URL = "https://docs.astral.sh/uv/getting-started/installation/"


def boa_command(uvx: str, args: list[str]) -> list[str]:
    """
    The command that runs BOA's `boa-lcoe` at the pinned release, with dependencies as of the pinned date.

    Args:
        uvx: Path to the `uvx` executable.
        args: Arguments for `boa-lcoe`, passed on unchanged.

    Returns:
        The command line, as a list for `subprocess`, so no shell reinterprets an argument.
    """
    return [
        uvx,
        "--exclude-newer",
        BOA_EXCLUDE_NEWER,
        "--from",
        f"git+{BOA_REPOSITORY}@{BOA_PIN}",
        "boa-lcoe",
        *args,
    ]


def steelo_boa_run(argv: list[str] | None = None) -> int:
    """
    Run BOA's `boa-lcoe` at the pinned release and print the `--boa-run` value for its run.

    Args:
        argv: Arguments for `boa-lcoe`; the command line's when None.

    Returns:
        BOA's exit code, or 1 when `uvx` is not on PATH.

    Notes:
        BOA's stdout is relayed line by line as it arrives, as the bytes BOA wrote, so a terminal
        that cannot encode a character BOA prints does not stop the relay. Its stderr goes straight
        to the terminal. The run name comes only from BOA's `BOA_RUN=` line. BOA inherits the
        environment, so `$BOA_DATA_ROOT` places its run where `run_simulation --boa-run` looks for it.
    """
    args = sys.argv[1:] if argv is None else argv
    uvx = shutil.which("uvx")
    if uvx is None:
        print(
            f"steelo-boa-run needs uvx, which comes with uv, on PATH. Install uv ({UV_INSTALL_URL}) and rerun.",
            file=sys.stderr,
        )
        return 1

    run = None
    with subprocess.Popen(boa_command(uvx, args), stdout=subprocess.PIPE) as process:
        assert process.stdout is not None
        for line in process.stdout:
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()
            if line.startswith(RUN_PREFIX):
                run = line.removeprefix(RUN_PREFIX).decode("utf-8", errors="replace").strip()
    if process.returncode != 0:
        return process.returncode
    if run is not None:
        print(f"Run the steel simulation with: run_simulation --boa-run {run}")
    return 0


if __name__ == "__main__":
    sys.exit(steelo_boa_run())
