#!/usr/bin/env python
"""Command-line tool to generate the geo_options.csv reference.

geo_options.csv lists every authorable sub-national geo-key (one row per populated unit) for pasting
into the master Excel. It is derived from a prepared ``geo_hierarchy.json``.

Usage:
    generate-geo-options [--fixtures-dir <dir>] [--out <csv>]
"""

import argparse
import sys
from pathlib import Path

from steelo.config import get_steelo_home
from steelo.data.recreation_functions import write_geo_options_csv


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate geo_options.csv (authorable geo-keys) from a prepared geo_hierarchy.json",
    )
    parser.add_argument(
        "--fixtures-dir",
        type=Path,
        default=get_steelo_home() / "data" / "fixtures",
        help="Directory containing geo_hierarchy.json (default: ~/.steelo/data/fixtures)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path.cwd() / "geo_options.csv",
        help="Output CSV path (default: ./geo_options.csv)",
    )
    args = parser.parse_args()

    geo_hierarchy_path = args.fixtures_dir / "geo_hierarchy.json"
    if not geo_hierarchy_path.exists():
        print(f"Error: geo_hierarchy.json not found in {args.fixtures_dir}. Run data preparation first.")
        return 1

    write_geo_options_csv(geo_hierarchy_path, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
