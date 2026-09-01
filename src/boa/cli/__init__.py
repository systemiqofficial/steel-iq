"""Console-script entry points: boa-run, boa-promote-lcoe, boa-cds-prepare, boa-cds-download."""

import sys


def reconfigure_streams_utf8() -> None:
    """
    Reconfigure stdout/stderr to UTF-8 so non-ASCII output survives a redirected console.

    A redirected stdout on Windows gets the cp1252 codec, which cannot encode the em
    dashes, arrows, and status glyphs these CLIs print. `print`/`rich` let the resulting
    UnicodeEncodeError propagate and crash the process; the stdlib `logging` module
    catches it instead and substitutes "--- Logging error ---" for the line. Neither
    outcome is acceptable when the run is unattended and redirected to a log for its
    whole duration: one loses the run, the other silently loses the message.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass  # already detached or not reconfigurable
