# Baseload Optimisation Atlas (BOA)
This module calculates global optimal Levelized Cost of Energy (LCOE) at pixel level (high resolution: 0.25 degrees, i.e., 50 km or better). 
It is computed in two steps: 
1) Simulating renewable energy supply (using weather data and Atlite)
2) Finding the optimum overbuilding factors for solar, wind, and battery. 

Because a BOA run takes hours per simulation year, steel-iq does not run BOA: it reads precomputed LCOE files. BOA is maintained in its own repository, [systemiqofficial/baseload-optimisation-atlas](https://github.com/systemiqofficial/baseload-optimisation-atlas), which documents how to prepare its input data and run it.

### LCOE files read by steel-iq
steel-iq reads one promoted BOA file per baseload coverage. A promoted file holds the LCOE of the optimal solar, wind and battery system for every investment year, in USD per MWh delivered; BOA's `docs/promoted-lcoe-file.md` describes its layout. A "Grid only" or "Not included" power mix reads no file.

- **Default files**: the `geo-data` package (3.0.0 and later) ships the files for 85%, 90% and 95% coverage, and data preparation puts them in the prepared data's `outputs/GEO/baseload_lcoe/`, for example `optimal_lcoe_wy2024_01MWkm2_cov0p85_2025_2060.nc`.
- **Another BOA run**: `run_simulation --boa-run` names a promoted run, by its folder or by its name under `$BOA_DATA_ROOT` (else `~/.boa`) `/lcoe-only/`. An unknown name stops the run before any data preparation and lists the runs there. The run records the folder in `boa_run_dir` and the run's name in `boa_run`, which the scenario viewer compares runs on.
- **Picking the file**: the coverage token in the file name picks it: `cov0p85` for 85%, `cov0p9` for 90% and `cov0p95` for 95%. No file for the coverage, or more than one, stops the run at bootstrap.
- **Checks**: the file must state `schema_version` 1, `lcoe_basis` `per_mwh_delivered` and a `coverage_fraction` equal to the coverage priced. A missing attribute is refused like a wrong one, so files promoted before BOA wrote these attributes are refused.
- **Years**: a year the file holds is read as it is, a year between two of its years is interpolated linearly, and a year outside its span reuses the nearest year, with a warning.

```bash
run_simulation --boa-run my_run                     # a run under $BOA_DATA_ROOT/lcoe-only/ (else ~/.boa/lcoe-only/)
run_simulation --boa-run /path/to/lcoe-only/my_run  # a run's folder
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