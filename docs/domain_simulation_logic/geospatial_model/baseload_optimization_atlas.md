# Baseload Optimisation Atlas (BOA)
This module calculates global optimal Levelized Cost of Energy (LCOE) at pixel level (high resolution: 0.25 degrees, i.e., 50 km or better). 
It is computed in two steps: 
1) Simulating renewable energy supply (using weather data and Atlite)
2) Finding the optimum overbuilding factors for solar, wind, and battery. 

Due to long runtimes (>6h per simulation year with parallelization on a powerful computer) this module runs as a standalone script (not as part of the main pipeline) and its precomputed outputs are read by the main model (steelo). However, we do provide the option to re-run step 2 of this module in case the user wants to change some of the input data or assumptions (e.g., cost of capital, solar PV CAPEX). For this, the output data from step 1 are required and are not provided in the default data package due to its large size (70 GB). Please, reach out to Steel-IQ@systemiq.earth to obtain these data. 

### 1. Prerequisites
Ensure all required data files are in place:
- Download and save the outputs from step 1 to the `data/atlite/` folder:
    - Renewable energy profiles to `data/atlite/output/`
    - Maximum capacity constraints to `data/atlite/cav/`
- Add the Renewable Energy Input Data Excel file at `data/Renewable_Energy_Input_Data.xlsx`
- Master input Excel file - is generated automatically when running the main pipeline, but can also be added manually

### 2. Running the simulation
The simulation is run with the `boa-run` command (after installing with `uv sync`). Runs
are always GLOBAL (all 9 regions); the one exception is the single-point mode:

```bash
# Full run with default parameters (2025-2060, 1000 MW demand, cds-2024 weather)
boa-run

# Full run with custom parameters
boa-run --demand 800 --coverage 0.95

# Prepare the weather stores and cost set inline, then run
boa-run --cds-prepare 2024 --data-prepare master.xlsx test_scenario

# Build only the year-independent design caches
boa-run build-cache --samples 2000

# Re-derive optimal-solution NetCDFs from pre-built caches
boa-run query --start-year 2030 --end-year 2030

# Single-point run (region auto-derived from coordinates)
boa-run point --lat 52.5 --lon 13.4

# See all available options
boa-run --help
```

### 3. Available parameters

**Temporal Parameters** (full run, `query`, `point`):
- `-s`/`--start-year`: Starting investment year (default: 2025)
- `-e`/`--end-year`: Ending investment year (default: 2060)
- `-f`/`--frequency`: Years between simulations (default: 1)

**Scenario Parameters:**
- `-d`/`--demand`: Baseload demand in MW (default: 1000.0, typical range: 150-1000)
- `-c`/`--coverage`: Required demand coverage fraction, e.g., 0.85 means 85% coverage (default: 0.85)
- `-n`/`--samples`: Number of design samples per grid point (default: 1000)

**Data Selection:**
- `--weather-input`: Input set under the data root's `inputs/` (profile + max-capacity stores and
  design cache; the weather year is read off the store filenames; default: `cds-2024`)
- `--cost-input`: Cost set under `costs/` (workbook + per-year cost cache; default: `default`)
- `--run`: Run name for outputs (default: `<weather-input>__<cost-input>`)
- `--cds-prepare YEAR`: Run `boa-cds-prepare` for YEAR first, building the weather-input
  set's missing stores (the weather-input default then becomes `cds-<YEAR>`)
- `--data-prepare XLSX SCENARIO`: Run `boa-data-prepare` first, extracting the cost workbook
  XLSX into cost set SCENARIO (the cost-input default then becomes SCENARIO)

**Optional Parameters:**
- `-w`/`--workers`: Threads for parallel grid-point optimisation (integer or preset small/normal/fast)
- `--verbose`: Enable detailed logging output
- `--no-plots`: Skip map plotting during the run
- `--dry-run` (full run only): Resolve paths and run the preflight check without simulating
- `--force` (`build-cache` and `query` only): Rebuild the targeted artifacts even if present

### 4. Output
The simulation will:
- Run the baseload power simulation for the selected years
- Process all regions in parallel
- Generate optimal renewable energy system designs for each grid point
- Save results as NetCDF files in `outputs/GEO/baseload_power_simulation/p{X}/`
- Create visualization plots in `outputs/plots/geo_layers/baseload_power_simulation/`

Results include:
- LCOE (Levelized Cost of Energy) in USD/MWh
- Solar overscale factor (relative to baseload demand)
- Wind overscale factor (relative to baseload demand)
- Battery overscale factor (relative to baseload demand)
- Total installation cost in USD

To run the steelo simulation based on the custom BOA output for the LCOE (instead of the default), the path to the output data must be provided explicitly:

```bash
run_simulation --baseload-power-sim-dir path-to-baseload-power-simulation-folder
```

## Methodology:
1. Project investment costs for solar and wind technologies for each country and year
    - Use SSP-RCP projected renewable buildouts until 2100 from IAASA (SSP1-2.6 (Sustainability), SSP2-4.5 (Middle of the Road), and SSP3-Baseline
    (Business-as-usual)).
    - Correct those projections with historical installed capacity data from IRENA.
    - Apply technology-specific learning curves to project CAPEX across time and space for solar and wind technologies.

2. Simulate hourly solar PV and onshore wind generation potential
    - Use high spatial resolution (30 km or higher) based on reanalysis weather data (ERA5 from Copernicus), including variables like radiation, temperature,
    and wind speed.
    - Use the Atlite package to simulate solar PV panels and onshore wind turbines at each location.

3. Determine the installation limits for solar and wind at each grid point
    - Capacity limits are based on physical constraints only (such as the area per grid cell at a certain latitude and the minimum spacing between turbines
    and panels).
    - Land use/cover is not considered.

4. Identify grid points eligible for renewable system deployment
    - Filter out water bodies (oceans and seas).
    - Set a maximum altitude and slope.
    - Exclude grid points with zero potential for both solar and wind.
    - Note: this is BOA's own eligibility filter, separate from the steelo siting feasibility mask (which additionally allows land at or below sea level).

5. For each eligible grid point
    - Sample many system design candidates
        - Use stochastic sampling (exponential or homogeneous distributions) to generate potential ratios of capacity vs. demand for solar and wind. This
        method does not provide the globally optimal solution, but is very close to the optimum and runs orders of magnitude faster than full optimization.
        - Ensure overscale factors do not exceed physical limits.
        - For each design, calculate net hourly energy production assuming a constant demand.
    - Estimate the required battery capacity for each sampled design
        - For each hour in the year, calculate the net generated energy as the installed solar and wind capacity multiplied by their respective capacity factor
        at that time minus the baseload demand (no batteries included yet).
        - Compute the q-th percentile (e.g., 5th) of the net generated energy to measure the magnitude of the typical deficit (without batteries).
        - Estimate how long such deficits last by analysing contiguous deficit durations.
        - Approximate the required battery size as the deficit magnitude multiplied by the typical duration.
    - Correct the battery CAPEX for modular installation
        - The cost of installing a battery module with several units at once is cheaper than installing many single units.
        - Use the overscale factor to reduce the unit price of the battery installation.
    - Simulate the operation of the battery for each design over one year
        - Track the hourly state of charge assuming ideal efficiency. Batteries are assumed to start empty at the start of the year.
        - Discard energy exceeding battery capacity and track demand shortfalls.
    - Filter designs based on demand coverage
        - Calculate the average hourly coverage of demand by the full system (solar, wind, battery).
        - Accept designs only if demand is met at least x% of the time (e.g., 95%; user configurable).
    - Calculate installation cost and Levelized Cost of Energy (LCOE) for accepted designs
        - Use technology-specific CAPEX, OPEX, and cost of capital for the investment year and country.
        - The Levelized Cost of Electricity (LCOE) quantifies the average cost of generating one unit of electricity (USD/MWh) over the investment horizon of
        a renewable energy system. It divides the total discounted costs—including initial capital investment (CAPEX), fixed annual operating expenses (OPEX),
        and a correction for the remaining value of the system after the investment horizon—by the total discounted electricity generated over that same period.
        - Electricity generation is calculated from hourly solar and wind profiles and is adjusted for technical degradation and downtime over time.
        - All cash flows and electricity outputs are discounted using a country-specific cost of capital to reflect investment risk.
        - The model optionally accounts for curtailment by excluding unused energy from the output.
        - Key assumptions include immediate system installation (generation starts in year one), no grid connection, and no taxation. With a fixed baseload
        demand, the cost estimate is purely supply-driven.
    - Select the optimal system design
        - Choose the design with the lowest LCOE (with curtailment) among accepted candidates.
        - Record its solar, wind, and battery overscale factors, LCOE, and total cost.

6. Extrapolate in time and space
    - Repeat the above steps for each grid point and year until 2050.
    - Combine all optimal designs and LCOEs into a set of global maps.