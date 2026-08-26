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

from boa.config.paths import PathConfig
from boa.model.lcoe_promotion import promote_all

DEFAULT_RUN = "cds-2024__default"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="boa-promote-lcoe",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--run", default=DEFAULT_RUN, help=f"Run to promote (default: {DEFAULT_RUN}).")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging.")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    path_config = PathConfig.from_auto_detect(run=args.run)
    try:
        promote_all(path_config)
    except (FileNotFoundError, ValueError) as e:
        logging.error(str(e))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
