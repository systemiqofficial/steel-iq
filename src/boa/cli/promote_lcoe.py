#!/usr/bin/env python
"""
Combined-LCOE promotion: the `boa-promote-lcoe` console script.

Turns a run's per-year GLOBAL optimal-solution NetCDFs into one small
``(year, lat, lon)`` LCOE file per scenario — the only thing the steel-iq
simulation reads off a BOA run. See ``boa.model.lcoe_promotion``.

Examples:
    boa-promote-lcoe                                 # promote every scenario in the default run
    boa-promote-lcoe --run cds-2024__china_test
"""

import argparse
import logging
import sys

from boa.cli import reconfigure_streams_utf8
from boa.config.paths import DEFAULT_SET, PathConfig, default_root, resolve_run_dir
from boa.model.lcoe_promotion import promote_all

# Matches boa-run's own bare default (`--run` omitted -> `<cost-input>`, i.e. just the cost
# set); resolve_run_dir finds whichever `<hash>` that label actually forked to on disk.
DEFAULT_RUN = DEFAULT_SET


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="boa-promote-lcoe",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--run", default=DEFAULT_RUN, help=f"Run label to promote, as passed to boa-run (default: {DEFAULT_RUN})."
    )
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging.")
    args = parser.parse_args(argv)

    reconfigure_streams_utf8()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    try:
        run_dir = resolve_run_dir(default_root(), args.run)
        path_config = PathConfig.from_auto_detect(run=run_dir.name)
        promote_all(path_config)
    except (FileNotFoundError, ValueError) as e:
        logging.error(str(e))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
