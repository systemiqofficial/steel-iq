"""Views that serve the offline scenario viewer from the app, over the app's runs and the CLI runs beside them."""

from pathlib import Path

from steelo.config import get_steelo_home


def cli_base() -> Path | None:
    """The output base of the CLI runs on this machine, when there is one.

    Returns:
        ``$STEELO_HOME/output`` when that folder exists, else ``None``.
    """
    output_base = get_steelo_home() / "output"
    return output_base if output_base.is_dir() else None
