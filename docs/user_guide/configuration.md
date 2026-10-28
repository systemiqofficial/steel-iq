# Configuration

The `steelo.config` module gives the application the two folders it needs before any run is configured. Everything a simulation can vary is passed through `SimulationConfig`.

- **`get_steelo_home()`**: `$STEELO_HOME` when it is set, else `~/.steelo`. Commands such as `run_simulation` also take `--steelo-home`.
- **`root`**: the same folder, read once when the module is imported.
- **`project_root`**: the repository root. In the Windows standalone app it is the STEELO_HOME folder, since the install folder may not be writable.

## The STEELO_HOME Folder

```
$STEELO_HOME/
├── preparation_cache/     # Cached data preparations
│   ├── index.json        # Fast lookup index
│   └── prep_<hash>/      # Individual cache entries
├── output/               # Simulation outputs
│   └── sim_<timestamp>/  # Individual simulation runs
├── data_cache/           # Downloaded S3 packages
├── data/                 # Symlink to latest preparation
└── output_latest/        # Symlink to latest output
```

## Trade Solver Environment Variables

Two environment variables tune the trade LP solver. They are read directly from the process environment, so set them in the shell (for example `STEELO_LP_SOLVER=simplex run_simulation ...`).

| Variable | Values | Effect |
|---|---|---|
| `STEELO_LP_SOLVER` | `hipo` (default), `ipx`, `simplex` | The HiGHS algorithm for the trade LP |
| `STEELO_HIGHS_LOG` | `1`, `true` or `yes` to enable | Mirrors HiGHS's own log to the console (it always reaches the run log) |

See [Solver Details](../domain_simulation_logic/trade_model/overview_trade_model.md#solver-details) for what each algorithm does and the fallback on a failed solve.

## Backward Compatibility

For backward compatibility, the system automatically creates symlinks in the project root:
- `<project_root>/data/` → `$STEELO_HOME/data` (latest preparation)
- `<project_root>/output/` → `$STEELO_HOME/output_latest` (latest simulation)

This ensures existing scripts that reference these paths continue to work without modification.
