# Baseload Optimisation Atlas (BOA)
BOA answers one question for every land pixel of a 0.25-degree grid: which combination of solar, wind and battery capacity supplies a constant baseload demand for a chosen share of the year's hours most cheaply, and what does that cost per MWh delivered? The steel model uses the answer as the price of baseload renewable power at each location and investment year.

Because a full BOA run takes about 75 minutes per coverage, the steel simulation does not run BOA: it reads precomputed LCOE files. BOA is maintained in its own repository, [systemiqofficial/baseload-optimisation-atlas](https://github.com/systemiqofficial/baseload-optimisation-atlas), which documents how to prepare its input data and run it. To produce a BOA run from steel-iq, use `steelo-boa-run`; see [Rerunning BOA for Baseload Power Prices](../../user_guide/running_simulations_locally.md#rerunning-boa-for-baseload-power-prices).

### How BOA computes the LCOE
- **Weather first, costs second**: for each land pixel, BOA searches solar, wind and battery sizes against the pixel's hourly weather, finding for each solar and wind size the smallest battery that meets the coverage target by bisection. This search depends on the weather, not on costs, so it runs once per region, coverage and weather year and is cached. Each investment year's and each country's costs then reprice the cached designs, and the cheapest is the optimum.
- **Weather**: hourly solar PV and onshore wind capacity factors for one weather year, from the Copernicus Climate Change Service (CDS dataset `sis-energy-global-reanalysis`), on the 0.25-degree ERA5 grid.
- **Coverage**: the share of the year's hours in which solar, wind and battery meet the demand. The default files cover 85%, 90% and 95%; the steel model's power mix names the share the grid supplies.
- **LCOE per MWh delivered**: the system's CAPEX, plus OPEX as a share of CAPEX per year, discounted at the country's cost of capital over a 25-year lifetime, divided by the energy delivered to the demand rather than all the energy generated. It is in the cost data's currency year: 2024 USD for the default data.
- **Costs**: CAPEX per technology and investment year, by IRENA region, country or province; OPEX and cost of capital do not vary by year.
- **Years**: investment years 2025 to 2060. BOA holds a cost workbook's last CAPEX year flat for any later year. The default cost data keeps its 2050 CAPEX in every later year BOA covers, so its LCOE from 2051 on equals 2050's.
- **Known limit**: the land available to each pixel is not yet applied as a ceiling on its solar and wind capacity, so the LCOE is the unconstrained optimum. The default files state `capacity_ceiling_applied = 0`.

BOA's [methodology](https://github.com/systemiqofficial/baseload-optimisation-atlas/blob/v1.10.0/docs/methodology.md) describes the search, and its [cost data guide](https://github.com/systemiqofficial/baseload-optimisation-atlas/blob/v1.10.0/docs/cost-data-prep.md) the cost workbook.

### Where the BOA data comes from
- **Default LCOE files**: geo-data 3.0.0 ships the files for 85%, 90% and 95% coverage. BOA v1.9.0 built them from the default cost data (`boa-cost-data` v0.1) and 2024 weather, at 1 MW/km² (the load density does not change the LCOE), for investment years 2025 to 2060. Data preparation places them in the prepared data's `outputs/GEO/baseload_lcoe/`.
- **BOA's inputs for a rerun**: on the public `steelo-data` bucket, under `https://steelo-data.s3.eu-north-1.amazonaws.com/boa-standalone-input-data/`, pinned by version and sha256 in BOA's `src/boa/config/data_packages.py`. `steelo-boa-run` downloads them on first use:
  - `boa-core-data-v0.1.zip`: the Natural Earth shapefiles and the ERA5 land-sea mask.
  - `boa-cost-data-v0.1.zip`: the default cost workbook, `boa-cost-data-v0.1.xlsx`, with `boa-cost-data.json`, which records its sheets, columns and units.
  - `cds-capacity-factors-<year>.zip`, about 6.6 GB each: the re-published capacity factors for weather years 2020 to 2025. They are Copernicus Climate Change Service information, licensed CC-BY 4.0. Other weather years need a CDS account and BOA's own `boa-cds-download`.
- **Disk**: about 60 GB under the BOA data root for one weather year and five coverages.

### LCOE files read by steel-iq
steel-iq reads one promoted BOA file per baseload coverage. A promoted file holds the LCOE of the optimal solar, wind and battery system for every investment year, in USD per MWh delivered; BOA's `docs/promoted-lcoe-file.md` describes its layout. A "Grid only" or "Not included" power mix reads no file.

- **Default files**: the `geo-data` package (3.0.0 and later) ships the files for 85%, 90% and 95% coverage, and data preparation puts them in the prepared data's `outputs/GEO/baseload_lcoe/`, for example `optimal_lcoe_wy2024_01MWkm2_cov0p85_2025_2060.nc`. Preparation also writes the geo-data version to `geo_data_version.txt` at the prepared data's root; the run records it in `geo_data_version`, which the scenario viewer compares runs on, so runs on the default files of two geo-data versions are told apart.
- **Another BOA run**: `run_simulation --boa-run` names a promoted run, by its folder or by its name under `$BOA_DATA_ROOT` (else `~/.boa`) `/lcoe-only/`. An unknown name stops the run before any data preparation and lists the runs there. The run records the folder in `boa_run_dir` and the run's name in `boa_run`, which the scenario viewer compares runs on.
- **Picking the file**: the coverage token in the file name picks it: `cov0p85` for 85%, `cov0p9` for 90% and `cov0p95` for 95%. No file for the coverage, or more than one, stops the run at bootstrap.
- **Checks**: the file must state `schema_version` 1, `lcoe_basis` `per_mwh_delivered` and a `coverage_fraction` equal to the coverage priced. A missing attribute is refused like a wrong one, so files promoted before BOA wrote these attributes are refused.
- **Years**: a year the file holds is read as it is, a year between two of its years is interpolated linearly, and a year outside its span reuses the nearest year, with a warning.

```bash
run_simulation --boa-run my_run                     # a run under $BOA_DATA_ROOT/lcoe-only/ (else ~/.boa/lcoe-only/)
run_simulation --boa-run /path/to/lcoe-only/my_run  # a run's folder
```

### The pinned BOA release
`steelo-boa-run` runs the BOA release that `BOA_PIN` names in `src/steelo/entrypoints/boa_cli.py`. Move the pin to a newer BOA release only if no LCOE-changing code landed in BOA since the release that built geo-data's default LCOE files: BOA v1.9.0 for geo-data 3.0.0. Otherwise, rerun the default LCOE files at the new release and publish them in a new geo-data version, so the default files and a rerun of them come from the same BOA code. `BOA_EXCLUDE_NEWER`, beside the pin, is the date `uvx` resolves BOA's dependencies at, since `uvx` does not read BOA's lock file; move it with the pin, to the time the new release's environment was tested.
