"""Run a single steel-IQ simulation with SimulationConfig field overrides applied.

Meant to be invoked as a subprocess by run_sweep.py (one OS process per parameter
combination), so a crash, hang, or memory leak in one run can't take down the rest
of a sensitivity sweep -- each run gets a clean interpreter.

Data preparation is NOT done here: callers must pass an already-prepared
--data-dir (containing a fixtures/ subfolder -- see `steelo-data-prepare`, or reuse
a directory the CLI already prepared under ~/.steelo/preparation_cache or
~/.steelo/temp_prep) plus the matching --master-excel. A sweep of many runs should
prepare data once and have every run reuse it, not re-prepare per combination.

Writes status.json to --output-dir on both success and failure (with duration_s),
so run_sweep.py can build a manifest and skip already-completed runs on resume.

Usage:
    uv run python -m scripts.sensitivity.run_one \\
        --data-dir ~/.steelo/preparation_cache/<hash>/data \\
        --master-excel ~/.steelo/data_cache/master-input-v2.0.0/master_input.xlsx \\
        --output-dir outputs/sensitivity/demo/run_0003 \\
        --start-year 2025 --end-year 2027 \\
        --params-json '{"steel_price_buffer": 250.0, "chosen_demand_scenario": "BAU"}'
"""

import argparse
import dataclasses
import json
import logging
import sys
import time
import traceback
from pathlib import Path

# Several handlers/postprocessing paths in steelo use plain print() with emoji
# (e.g. handlers.py's finalise_iteration). That's fine on a UTF-8 terminal, but
# fatal under the cp1252 stdout Windows uses when output is redirected to a file
# (as run_sweep.py does for every worker) -- UnicodeEncodeError kills the run at
# the first such print, deterministically, every time. Force UTF-8 regardless of
# how this process's stdout/stderr ended up wired.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="backslashreplace")

from steelo.bootstrap import bootstrap_simulation  # noqa: E402
from steelo.domain import Year  # noqa: E402
from steelo.simulation import SimulationConfig  # noqa: E402

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, required=True, help="Prepared data directory (contains fixtures/)")
    parser.add_argument("--master-excel", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--start-year", type=int, default=2025)
    parser.add_argument("--end-year", type=int, default=2050)
    parser.add_argument(
        "--log-level", type=str, choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], default="WARNING"
    )
    parser.add_argument(
        "--params-json",
        type=str,
        default="{}",
        help="JSON object of SimulationConfig field overrides applied via setattr after "
        "construction, e.g. '{\"steel_price_buffer\": 250.0}'. Keys must be valid "
        "SimulationConfig field names.",
    )
    args = parser.parse_args()

    params: dict = json.loads(args.params_json)
    valid_fields = {f.name for f in dataclasses.fields(SimulationConfig)}
    unknown = set(params) - valid_fields
    if unknown:
        raise ValueError(f"Unknown SimulationConfig field(s) {sorted(unknown)} in --params-json")

    log_level = getattr(logging, args.log_level)
    logging.basicConfig(level=log_level, format="%(asctime)s %(levelname)s %(message)s")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    status_path = args.output_dir / "status.json"
    (args.output_dir / "params.json").write_text(json.dumps(params, indent=2))

    t0 = time.time()
    try:
        config = SimulationConfig.from_data_directory(
            start_year=Year(args.start_year),
            end_year=Year(args.end_year),
            data_dir=args.data_dir,
            output_dir=args.output_dir,
            master_excel_path=args.master_excel,
            log_level=log_level,
        )
        for key, value in params.items():
            setattr(config, key, value)

        runner = bootstrap_simulation(config)
        runner.run()
    except Exception as exc:
        status_path.write_text(
            json.dumps(
                {
                    "status": "failed",
                    "duration_s": time.time() - t0,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                },
                indent=2,
            )
        )
        logger.error(f"Run failed: {exc}")
        sys.exit(1)

    status_path.write_text(json.dumps({"status": "success", "duration_s": time.time() - t0}, indent=2))


if __name__ == "__main__":
    main()
