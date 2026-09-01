"""
Non-ASCII output must survive a non-UTF-8 stdout/stderr.

On Windows, a redirected stdout gets the cp1252 codec, which cannot encode the
status glyphs, em dashes, and arrows these CLIs print. `boa.cli.reconfigure_streams_utf8`
reconfigures the streams to UTF-8 (`errors="replace"`) so writers never see the failure,
rather than patching each call site.
"""

import io
import logging
import sys

import pytest

from boa.cli import reconfigure_streams_utf8
from boa.cli.run_cds import _utf8_console

# The literals that motivated the fix: rich's own progress-bar glyphs plus the
# status ticks/crosses and dashes/arrows used across the CLI modules.
NON_ASCII_SAMPLE = "✓ ✗ — → ━ ╺"


def _cp1252_stream() -> io.TextIOWrapper:
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")


def test_cp1252_stream_rejects_the_sample_without_the_fix():
    # Establishes the failure this module exists to prevent.
    with pytest.raises(UnicodeEncodeError):
        _cp1252_stream().write(NON_ASCII_SAMPLE)


def test_reconfigure_streams_utf8_survives_non_ascii_writes(monkeypatch):
    stdout = _cp1252_stream()
    stderr = _cp1252_stream()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    reconfigure_streams_utf8()

    stdout.write(NON_ASCII_SAMPLE)
    stderr.write(NON_ASCII_SAMPLE)


def test_reconfigure_streams_utf8_ignores_streams_without_reconfigure(monkeypatch):
    class NoReconfigure:
        pass

    monkeypatch.setattr(sys, "stdout", NoReconfigure())
    monkeypatch.setattr(sys, "stderr", NoReconfigure())

    reconfigure_streams_utf8()  # must not raise


def test_utf8_console_prints_status_and_progress_glyphs_on_cp1252_stdout(monkeypatch):
    monkeypatch.setattr(sys, "stdout", _cp1252_stream())

    console = _utf8_console()
    console.print(f"[green]✓ done[/green] {NON_ASCII_SAMPLE}")
    console.print("━━╺")  # rich's own bar-fill and bar-tip glyphs


def test_logging_through_cp1252_stream_writes_the_message_once_reconfigured(monkeypatch):
    # logging swallows an encoding failure into a "--- Logging error ---" traceback on
    # stderr rather than crashing (see run_simulation.py / promote_lcoe.py, which only
    # configure logging handlers, no Console) -- the message itself is lost either way.
    # A non-raise alone wouldn't distinguish "wrote the message" from "silently dropped
    # it", so this checks the underlying bytes too.
    buf = io.BytesIO()
    stream = io.TextIOWrapper(buf, encoding="cp1252", errors="strict")
    fake_stderr = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", fake_stderr)
    reconfigure_streams_utf8()

    logger = logging.getLogger("test_cli_encoding")
    handler = logging.StreamHandler(stream)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        logger.info(f"build design caches first {NON_ASCII_SAMPLE}")
    finally:
        logger.removeHandler(handler)

    assert "Logging error" not in fake_stderr.getvalue()
    stream.flush()
    assert NON_ASCII_SAMPLE.encode("utf-8") in buf.getvalue()
