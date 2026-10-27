# Logging Configuration Guide for Steel Model

## Quick Start

### Enable DEBUG logging for a module
Edit `logging_config.yaml` in the repository root:
```yaml
modules:
  geo: DEBUG   # GeospatialModel - set to DEBUG
  pam: INFO    # PlantAgentsModel
  tm: INFO     # AllocationModel (Trade Model)
```

### Add logging to a new function
```python
import logging

def my_function(params):
    logger = logging.getLogger(f"{__name__}.my_function")
    logger.debug(f"Starting with params: {params}")
    # ... function logic ...
    logger.info("Completed successfully")
```

### Suppress a noisy function
Add to `logging_config.yaml`:
```yaml
function_overrides:
  noisy_function_name: WARNING  # Only WARNING+ from this function
```

### Run simulation with DEBUG output
```bash
source .venv/bin/activate
run_simulation --master-excel input.xlsx --log-level DEBUG --end-year 2027 > debug.log 2>&1
```

---

## Log Format

Logs use a structured format with aligned columns:

```
INFO    | CORE | bootstrap: Using fixtures directory...
INFO    | TM   | set_up_steel_trade_lp: Setting up LP model...
WARNING | TM   | enforce_trade_tariffs_on_allocations: cannot find average prices
DEBUG   | PAM  | calculate_subsidies: Processing H2 subsidies for DEU
```

Format: `LEVEL   | MODULE | function_name: message`

| Column | Width | Description |
|--------|-------|-------------|
| LEVEL | 7 chars | DEBUG, INFO, WARNING, ERROR |
| MODULE | 4 chars | GEO, PAM, TM, or CORE (outside model context) |
| function | variable | Last component of logger name |

The MODULE column reflects which economic model is currently executing. The same function shows different prefixes depending on which model called it:
- `PAM  | calculate_subsidies` - called during PlantAgentsModel
- `GEO  | calculate_subsidies` - called during GeospatialModel

---

## Overview

The Steel Model uses YAML-based logging configuration with context-aware filtering. Key features:

- **Module-specific levels**: Control DEBUG output per model (geo/pam/tm)
- **Function overrides**: Suppress or enable specific functions
- **CLI ceiling**: `--log-level INFO` suppresses all DEBUG regardless of YAML
- **Thread-safe**: Context tracking works correctly across concurrent calls

### Configuration File

The logging configuration file `logging_config.yaml` is located in the repository root and ships with sensible defaults.

---

## How It Works

### Priority Hierarchy

Log messages pass through two gates: the **handler** and the **filter**. Both must allow a message for it to appear.

| Gate | Controlled by | Purpose |
|------|---------------|---------|
| Handler threshold | CLI `--log-level` | Sets the minimum level the handler will accept |
| Context-aware filter | YAML config | Decides per-message based on function overrides, module context, and CLI level |

The filter applies settings in this order (highest priority first):

1. **Function overrides** — if the function name matches an entry in `function_overrides`, that level is used. Module and CLI levels are ignored for filtering.
2. **Module context** — if running inside a model context (geo/pam/tm), the YAML module level applies (capped by CLI level).
3. **CLI level** — used directly for code running outside any module context.

**Practical consequence:** A function override of `my_func: DEBUG` lets DEBUG through the *filter*, but the *handler* still enforces `--log-level`. If you run with `--log-level INFO`, the handler discards DEBUG messages before the filter ever sees them. Always run with `--log-level DEBUG` when using DEBUG function overrides.

### Module Contexts

The simulation runs three economic models in sequence. Each model has its own logging context:

| Model | Context | YAML Key | Description |
|-------|---------|----------|-------------|
| GeospatialModel | geo | `modules.geo` | Determines optimal locations for new steel plants |
| PlantAgentsModel | pam | `modules.pam` | Simulates individual plant investment decisions |
| AllocationModel | tm | `modules.tm` | Optimises steel trade flows between regions |

DEBUG logs only appear when:
1. The current module context's level is DEBUG in YAML
2. CLI `--log-level` allows DEBUG (not overridden to INFO/WARNING)
3. No function override suppresses it

INFO, WARNING, and ERROR logs always appear regardless of module context.

### YAML Configuration Structure

```yaml
version: 1
global_level: WARNING

features:
  furnace_group_breakdown: true  # Show detailed furnace-level logs
  dump_failed_lp: false          # Write TM/trade_lp_failed_<year>.mps (large) when the trade LP does not solve optimally
  energy_trace: false            # Trace electricity and hydrogen prices (see Energy-Price Trace below)

modules:
  geo: DEBUG   # Enables DEBUG for GeospatialModel
  pam: INFO    # Only INFO+ for PlantAgentsModel
  tm: INFO     # Only INFO+ for AllocationModel

function_overrides:
  calculate_unit_production_cost: WARNING  # Suppress even when module is DEBUG
  my_debug_function: DEBUG                 # Enable even when module is INFO

external:
  pyomo: ERROR  # Suppress noisy third-party libraries
```

### CLI Level Behaviour

The `--log-level` flag controls logging differently inside and outside module contexts:

**Inside module contexts (geo/pam/tm):**
- Acts as a ceiling for the YAML module levels
- `--log-level INFO` suppresses DEBUG even if YAML says `geo: DEBUG`
- The more restrictive level (CLI or YAML) wins

**Outside module contexts (orchestration, data preparation, startup):**
- CLI level is used directly
- `--log-level DEBUG` shows DEBUG logs from code outside models
- `--log-level INFO` shows only INFO+ from outside models

| CLI Level | Inside Context | Outside Context |
|-----------|----------------|-----------------|
| DEBUG | Respects YAML | Shows DEBUG |
| INFO | Suppresses DEBUG | Shows INFO+ |
| WARNING | Only WARNING+ | Only WARNING+ |

---

## Common Tasks

### Temporarily Enable Verbose Logging

**Option 1**: Edit YAML (recommended)
```yaml
modules:
  pam: DEBUG  # Temporarily set to DEBUG
```

**Option 2**: Use function override
```yaml
function_overrides:
  function_to_debug: DEBUG
```

Remember to revert after debugging.

### Suppress Noisy Third-Party Libraries

```yaml
external:
  pyomo: ERROR
  matplotlib.font_manager: ERROR
  numpy: ERROR
```

### Debug a Specific Function Only

Set module to INFO but enable the specific function:
```yaml
modules:
  pam: INFO

function_overrides:
  the_function_to_debug: DEBUG
```

---

## Energy-Price Trace

The energy-price trace follows electricity and hydrogen prices from the site price rasters and the country hydrogen series to what plants pay, and checks each price against a value it computes independently. LCOE below is the levelised cost of electricity of a site's own baseload supply, and LCOH the levelised cost of hydrogen. The trace changes no price and no result. [Energy Prices of Existing and New Plants](../domain_simulation_logic/overarching_elements/energy_prices.md) describes the price path it watches.

### Switch and lists

```yaml
features:
  energy_trace: true

energy_trace:
  countries: [CHN, IND, USA, JPN, RUS, IRN, DEU, BRA, SAU, AUS]
  plants:
    - P100000120219
    - P100000120085
```

- **`features.energy_trace`**: off in the shipped file. When it is off, every trace call returns before reading its arguments.
- **`energy_trace.countries`**: ISO3 codes. The raster stages write one summary per listed country, and the existing-plant checks cover every active furnace group in these countries.
- **`energy_trace.plants`**: plant IDs. Every active furnace group of a listed plant gets a log line and CSV rows, wherever the plant is.

New sites, the plants the model opens (`parent_gem_id` starting with `indi`), are always traced, since their IDs only exist at run time.

### Output

- **Log lines**: one INFO line `[ENERGY TRACE] stage=<stage> year=<year> ...` per traced entity, and summary lines per stage and year (`checked n, failed m`). INFO lines need `--log-level INFO` or lower.
- **Warnings**: a failed check writes one WARNING `[ENERGY CHECK] ...`, which shows at any CLI level. The logger is `steelo.utilities.energy_trace`, so `function_overrides: {energy_trace: WARNING}` keeps the warnings and drops the INFO lines.
- **CSV**: `energy_trace.csv` in the run folder, one row per stage, year, entity and quantity, appended as each stage runs so a stopped run keeps its rows. It is written whatever the log level, and not at all when a run has no output folder.

### Stages

| Stage | When | Rows | Check |
|-------|------|------|-------|
| `lcoe_source` | At bootstrap, once the baseload LCOE file is accepted, and each year as the LCOE is read | The file, and per listed country | None: a file that breaks the contract is refused before it is read |
| `site_power` | Each year, once the site power price raster is built | Per listed country and `ALL` | Every priced cell equals `(1 - c) × grid + c × LCOE` for an LCOE file priced per MWh delivered, else `(1 - c) × grid + LCOE`, where c is the baseload coverage |
| `site_hydrogen` | Each year, once the site hydrogen price is capped | Per listed country and `ALL` | Every priced cell stays within its region's ceiling |
| `hydrogen_lcoe` | At bootstrap, once per year of the precomputed series, with a baseload power mix | Every geo key | Each listed country's LCOE, unless priced from its nearest cells, equals the `hydrogen_lcoe_percentile` of the year's finite LCOE over the cells whose iso3 is the country, within a relative 1e-6 |
| `country_hydrogen` | At bootstrap, once per year of the precomputed series | Every geo key | The recorded electricity equals `(1 - c) × grid + c × LCOE` from the input-cost grid price and the LCOE series, or the grid price without a baseload share; the LCOH the cap used equals the electrolyser's kWh per kg × that electricity + its capital and operating cost per kg, at the geo key else its country; the capped price stays within `min(LCOH, region's ceiling)` |
| `brownfield` | Each year, before the trade model | Listed plants' active furnace groups; other furnace groups in listed countries only when a check fails | No-subsidy electricity equals the geo key's input cost for the year; no-subsidy hydrogen equals the geo key's capped price × 1,000 |
| `greenfield_select` | Each year, per candidate site | Every candidate site | None |
| `greenfield_revalue` | Each year, per considered or announced new site | Every such site | The no-subsidy hydrogen written equals the site's capped LCOH × 1,000 |
| `greenfield_build` | When a new site enters construction | Every such site | None: it freezes the site's last re-valued raster prices as the reference |
| `greenfield_operating` | Each year, before the trade model | Every operating new site | No-subsidy electricity and hydrogen equal the references frozen at construction |

With the hydrogen ceiling percentile at 100 and intraregional trade off, as by default, neither hydrogen cap check (`site_hydrogen`, `country_hydrogen`) can fail. On the current code the `greenfield_revalue` and `greenfield_operating` hydrogen checks fail for every new site; the energy prices page explains why.

The year-end summary lines also give, per listed country, the candidate sites, their median site prices and how many have a power price outside 0.1 to 10 times the country's; the re-valuation checks; and the sites built, with how many had no reference.

### CSV columns

| Column | Content |
|--------|---------|
| `year`, `stage` | The year the prices belong to and the stage |
| `plant_id`, `furnace_group_id` | Empty for raster summaries, geo keys and candidate sites |
| `iso3`, `geo_key` | Country (`ALL` for every cell) and finest geo key, for example `CHN:CN-LN` |
| `lat`, `lon` | Position of a plant or candidate site |
| `technology`, `status` | The furnace group's |
| `new_site` | 1 for a new site, 0 for an existing plant |
| `quantity`, `value`, `unit` | One quantity per row, in the unit the code holds it in |
| `expected` | The independent value of a checked quantity, else empty |
| `check_failed` | 1 when the check failed, else 0 |

### Quantities and units

| Stage | Quantities |
|-------|------------|
| `lcoe_source` | `file`, `run`, `boa_version` and `lcoe_basis` (text); `coverage_fraction`; `schema_version`; per listed country, `median_lcoe` (USD/MWh) |
| `site_power` | `coverage` (fraction); `median_grid_price`, `median_lcoe` and `median_power_price` (USD/kWh); `cells_checked`; `cells_off_blend` (checked, expected 0) |
| `site_hydrogen` | `median_lcoh`, `ceiling` and `median_capped_lcoh` (USD/kg); `share_capped`, the fraction of checked cells priced below their LCOH; `cells_checked`; `cells_above_ceiling` (checked, expected 0); `cells_nan_lcoh_priced`, cells with no LCOH that still carry a price |
| `hydrogen_lcoe` | `lcoe` (USD/MWh, checked for listed countries not priced from their nearest cells); `from_neighbours`, 1 for a geo key priced from its nearest cells |
| `country_hydrogen` | `coverage` (fraction); `grid`, `lcoe` (with a baseload share only) and `electricity` (USD/kWh, `electricity` checked); `lcoh`, `ceiling` and `capped` (USD/kg, `lcoh` and `capped` checked) |
| `brownfield`, `greenfield_operating` | `electricity` (USD/kWh) and `hydrogen` (USD/t) without subsidy, checked; `electricity_paid` and `hydrogen_paid` with subsidy |
| `greenfield_select` | `site_electricity` (USD/kWh); `site_hydrogen` (USD/t); `country_electricity` (USD/kWh); `country_hydrogen` (USD/t, the master's input cost); `override_reference_year`; `operating_start_year` |
| `greenfield_revalue` | `electricity` and `hydrogen` as written without subsidy (`hydrogen` checked); `site_power_price` (USD/kWh); `site_hydrogen` (USD/t) |
| `greenfield_build` | `electricity` and `hydrogen` as stored; `reference_electricity` (USD/kWh) and `reference_hydrogen` (USD/t), or `reference` reading `missing`; `regional_hydrogen` (USD/t, the geo key's capped price that year, recorded for reading only); `operating_start_year` |

---

## Logging Levels Reference

From most to least verbose:
- `DEBUG` - Detailed diagnostic information
- `INFO` - General informational messages
- `WARNING` - Warning messages
- `ERROR` - Error messages
- `CRITICAL` - Critical errors only

---

## Troubleshooting

### DEBUG logs not appearing?

1. **Check module context**: Is the function called from within a model (geo/pam/tm)?
   - Code that runs outside model contexts (orchestration, data preparation) suppresses DEBUG by design
   - Use INFO level for messages that should always appear

2. **Check YAML module level**: Is the module set to DEBUG?
   ```yaml
   modules:
     geo: DEBUG  # Must be DEBUG, not INFO
   ```

3. **Check CLI level**: Are you running with `--log-level INFO`?
   - INFO ceiling suppresses all DEBUG

4. **Check function override**: Is there an override suppressing it?
   ```yaml
   function_overrides:
     your_function: WARNING  # This would suppress DEBUG
   ```

### Verify logger name

```python
logger = logging.getLogger(f"{__name__}.my_function")
print(f"Logger name: {logger.name}")  # Should be: steelo.domain.module.my_function
```

---

## Adding Custom Logging

This section is for contributors adding logging to new functions.

### Pattern: Function-Level Logger

Always use function-level loggers (not module-level):

```python
import logging

def calculate_renewable_costs(params):
    logger = logging.getLogger(f"{__name__}.calculate_renewable_costs")

    logger.debug(f"Starting calculation with params: {params}")
    result = do_calculation(params)
    logger.debug(f"Calculation complete. Result: {result}")

    return result
```

**Why function-level loggers?**

The `function_overrides` feature requires function-level loggers. The filter extracts the function name from the logger name:

```python
# Logger name: steelo.domain.models.calculate_renewable_costs
#                                    ^^^^^^^^^^^^^^^^^^^^^^^^
#                                    Filter extracts this part
```

With a module-level logger (`logging.getLogger(__name__)`), the filter cannot match individual function overrides. Function-level loggers enable:

- Per-function suppression of noisy DEBUG output
- Per-function enabling of DEBUG in otherwise INFO-only modules
- Granular control without modifying code

### For Class Methods

```python
class MyClass:
    def my_method(self):
        logger = logging.getLogger(f"{__name__}.MyClass.my_method")
        logger.debug("Method executing")
```

### Best Practices

1. **Use function-level loggers**: `logging.getLogger(f"{__name__}.function_name")`
2. **Choose appropriate levels**:
   - DEBUG: Verbose details (filtered by context)
   - INFO: Key milestones (always visible)
   - WARNING: Unexpected situations
   - ERROR: Errors requiring attention
3. **Include context in messages**: `f"[FUNCTION]: description {value}"`
4. **Clean up**: Remove temporary DEBUG settings after fixing issues