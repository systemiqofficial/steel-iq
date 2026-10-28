"""
`steelo-boa-run` hands its arguments to BOA's `boa-lcoe` through `uvx` and names the run BOA produced.

No real `uvx` or BOA runs: `shutil.which` finds a fake `uvx`, and the command is started as a small
Python child that echoes its arguments and `$BOA_DATA_ROOT`, prints `$FAKE_BOA_OUTPUT` (lines
separated by `|`) and exits with `$FAKE_BOA_EXIT`. Nothing is written outside `tmp_path`.
"""

import io
import json
import subprocess
import sys

import pytest

from steelo.adapters.geospatial import boa_lcoe
from steelo.entrypoints import boa_cli

FAKE_UVX = "/fake/bin/uvx"
FAKE_BOA = """
import json, os, sys
print(json.dumps(sys.argv[1:]), flush=True)
print("child BOA_DATA_ROOT " + os.environ.get("BOA_DATA_ROOT", ""), flush=True)
for line in os.environ.get("FAKE_BOA_OUTPUT", "").split("|"):
    if line:
        print(line, flush=True)
sys.exit(int(os.environ.get("FAKE_BOA_EXIT", "0")))
"""


@pytest.fixture
def started(monkeypatch, tmp_path):
    """
    Replaces `uvx` with the fake BOA child and returns the list of commands started.

    Args:
        monkeypatch: Pytest's monkeypatch fixture.
        tmp_path: The test's temporary folder, used as the BOA data root.

    Returns:
        Every command `steelo_boa_run` started, as it built it.
    """
    monkeypatch.setenv("BOA_DATA_ROOT", str(tmp_path / "boa"))
    monkeypatch.setattr(boa_cli.shutil, "which", lambda name: FAKE_UVX if name == "uvx" else None)
    real_popen = subprocess.Popen
    commands = []

    def popen(command, **kwargs):
        commands.append(command)
        return real_popen([sys.executable, "-c", FAKE_BOA, *command[1:]], **kwargs)

    monkeypatch.setattr(boa_cli.subprocess, "Popen", popen)
    return commands


def test_the_command_runs_boa_lcoe_at_the_pinned_release(started):
    """`uvx` installs BOA from the pinned tag of its repository, with dependencies as of the pinned date."""
    assert boa_cli.steelo_boa_run(["--coverage", "0.85"]) == 0

    assert started == [
        [
            FAKE_UVX,
            "--exclude-newer",
            boa_cli.BOA_EXCLUDE_NEWER,
            "--from",
            f"git+{boa_cli.BOA_REPOSITORY}@{boa_cli.BOA_PIN}",
            "boa-lcoe",
            "--coverage",
            "0.85",
        ],
    ]


def test_every_argument_reaches_boa_unchanged(started, capsys):
    """Spaces, leading dashes and `--help` reach BOA intact, with no shell or parser in between."""
    args = ["--input-file", "my costs.xlsx", "--scenario", "-odd name", "--like=/a b/c.nc", "--help"]

    assert boa_cli.steelo_boa_run(args) == 0

    received = json.loads(capsys.readouterr().out.splitlines()[0])
    assert received[received.index("boa-lcoe") + 1 :] == args


def test_without_uvx_it_stops_and_says_how_to_install_uv(monkeypatch, capsys):
    """No `uvx` on PATH: a non-zero exit and a pointer to installing uv, with nothing started."""
    monkeypatch.setattr(boa_cli.shutil, "which", lambda name: None)

    def popen(command, **kwargs):
        raise AssertionError("a command was started without uvx")

    monkeypatch.setattr(boa_cli.subprocess, "Popen", popen)

    assert boa_cli.steelo_boa_run(["--coverage", "0.85"]) == 1
    err = capsys.readouterr().err
    assert "needs uvx" in err
    assert boa_cli.UV_INSTALL_URL in err


def test_the_run_name_comes_from_the_boa_run_line_and_ends_the_output(started, monkeypatch, capsys):
    """BOA's output is relayed as it is, then one line names the run for `run_simulation --boa-run`."""
    monkeypatch.setenv("FAKE_BOA_OUTPUT", "a log line|BOA_RUN=smoke_49e8|BOA_RUN_DIR=/data/lcoe-only/smoke_49e8")

    assert boa_cli.steelo_boa_run(["--run", "smoke"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[-4:] == [
        "a log line",
        "BOA_RUN=smoke_49e8",
        "BOA_RUN_DIR=/data/lcoe-only/smoke_49e8",
        "Run the steel simulation with: run_simulation --boa-run smoke_49e8",
    ]


def test_a_failing_boa_exit_code_is_passed_on(started, monkeypatch, capsys):
    """BOA's own non-zero exit code comes back, with no run named."""
    monkeypatch.setenv("FAKE_BOA_EXIT", "3")

    assert boa_cli.steelo_boa_run(["--coverage", "0.85"]) == 3
    assert "run_simulation --boa-run" not in capsys.readouterr().out


def test_a_successful_exit_without_a_run_prints_no_final_line(started, capsys):
    """`--help` exits 0 and names no run, so nothing is added to BOA's output."""
    assert boa_cli.steelo_boa_run(["--help"]) == 0
    assert "run_simulation --boa-run" not in capsys.readouterr().out


def test_boa_inherits_the_environment_so_both_sides_use_one_root(started, capsys, tmp_path):
    """BOA sees steel-iq's `$BOA_DATA_ROOT`, the root `run_simulation --boa-run` resolves names under."""
    assert boa_cli.steelo_boa_run([]) == 0

    assert f"child BOA_DATA_ROOT {tmp_path / 'boa'}" in capsys.readouterr().out.splitlines()
    assert boa_lcoe.boa_root() == tmp_path / "boa"


def test_a_stdout_that_cannot_encode_boas_output_does_not_stop_the_relay(started, monkeypatch):
    """
    A redirected Windows console encodes as cp1252, which has no `✓`; BOA's bytes pass through
    unchanged and the run is still named.
    """
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    monkeypatch.setenv("FAKE_BOA_OUTPUT", "✓ Installed the stores|BOA_RUN=smoke_49e8")
    written = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(written, encoding="cp1252", write_through=True))

    assert boa_cli.steelo_boa_run(["--run", "smoke"]) == 0

    lines = written.getvalue().decode("utf-8").splitlines()
    assert "✓ Installed the stores" in lines
    assert lines[-1] == "Run the steel simulation with: run_simulation --boa-run smoke_49e8"
