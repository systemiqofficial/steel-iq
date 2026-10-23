# Scenario viewer: developer note

The offline scenario viewer: files written at the end of every run, a catalogue built from the runs on disk, and a
browser app served by `steelo-viewer` or by the Django app. The user's view is
`docs/user_guide/scenario_viewer.md`; the files' user view is the "Scenario viewer files" section of
`docs/domain_simulation_logic/overarching_elements/outputs_and_postprocessing.md`. This note is the code view.

## Layout

- `app/`: the browser app, `index.html` and `assets/`, plain JavaScript with no build step. Plotly comes from the
  installed `plotly` package, not from this folder.
- `extract.py`: writes `<run>/viewer/` (`write_viewer_files`) and is `steelo-viewer regenerate` (`regenerate`).
- `catalogue.py`: finds the runs (`discover_runs`) and builds `manifest.json` and `inputs.json`, and picks `geo.json`,
  from the runs on disk, again with each request.
- `server.py`: the `steelo-viewer` command (`main`): serves `app/` and the runs' files on 127.0.0.1, and hands an
  argument list starting with `regenerate` to `extract.regenerate`.
- `scripts/`: `check_tour.mjs` and `check_viewer_offline.mjs`, Playwright checks run by hand.
- `src/steeloweb/views_viewer.py`: the app's `/viewer/` views over the same catalogue, with the "Made in" filter, and
  `/scenario-viewer/`, the app page that frames the viewer below the app's navigation.

## Run end and `regenerate`

`SimulationRunner.run` ends with `simulation.write_viewer_files_after_run`, which calls
`extract.write_viewer_files` and logs a failure as "viewer files incomplete for this run" instead of raising it: the
model's outputs are on disk by then, so the run still finishes. `write_viewer_files` deletes `viewer/` first and
writes `run.json` last, so a folder holding `run.json` is complete, and the catalogue lists only such folders.

`steelo-viewer regenerate RUN_DIR ... [--master-excel PATH]` checks every run's inputs first (`missing_inputs`) and
writes nothing when one is missing; otherwise it calls `write_viewer_files` on each run. It reads no prepared data.

## The files

`write_viewer_files` reads the last `post_processed_*.csv` in sorted order, `simulation_config.json`,
`preparation_metadata.json`, the master workbook (the recorded `master_excel`, or the one given) and
`plots/interactive/*.html`. A plot page the plotter skipped skips the file lifted from it, with a warning
(without `emissions.html`, no `geo.json`; the run is still listed). Only `regenerate` requires `emissions.html`
(`missing_inputs`).

| File | Content | Source (`extract.py`) |
|---|---|---|
| `timeseries.csv` | Year x geo key x product x technology: capacity, production and the nine emissions accountings, in kt | Post-processed CSV, `extract_timeseries` |
| `greenfield.csv` | New furnace groups and their capacity by build year | Post-processed CSV, `extract_greenfield` |
| `annual_costs.csv` | Cost components in million USD by year, geo key, product, technology and reductant | Post-processed CSV, `plant_lifetime` and `equity_share` from the config, the master's "Cost of capital" and "Techno-economic details" sheets (`extract_annual_costs`) |
| `charge.csv`, `reductant.csv`, `trade.csv` | As the plot pages show them | The DATA blocks of `metallic_charge_use.html`, `reductant_use.html` and `trade_matrix.html` (`extract_charge`, `extract_reductant`, `extract_trade`) |
| `supply.csv` | Use and availability by year, geo key, commodity and grade | `supply_demand.html` (`extract_supply`); steel demand from the master's "Demand and scrap availability" sheet for the run's `chosen_demand_scenario` (`steel_demand_table`) |
| `geo.json` | Trade blocs, country and geo-unit names, region budgets | The CONFIG blocks of `emissions.html` and `supply_demand.html` (`extract_geo`) |
| `inputs.json` | The run's View data rows, undiffed | The master's sheets in `TOPICS`, for the run's demand, scrap and grid scenarios (`extract_inputs`) |
| `results/` | The Download results tables: folders `RESULT_FOLDERS` names, each table as `<stem>.csv.gz`, pages of 5,000 rows and `index.json` | Globs on the run folder (`extract_results`, `result_tables`) |
| `run.json`, last | `schema`, `name`, `folder`, `made_in`, `started`, `ended`, `steelo_version`, `input_data`, `pages`, `files` | The config, the metadata and the package version |

`SCHEMA_VERSION` (1) is written into `run.json` and `inputs.json`; no reader checks it yet.

## The catalogue's rules

All in `catalogue.py`:

- `EXCLUDED_SETTINGS`: the config keys that never become a filter or a run-details row (paths, unread fields, the
  plot and logging switches, the run name, the seed's copy in `geo_config`).
  `test_excluded_settings_name_only_real_config_fields` in `tests/unit/test_viewer_catalogue.py` fails on an entry
  that is no config field (older configs' `scrap_generation_scenario` excepted).
- `SETTING_FORMATS`: each counted setting's label, unit and number format; `setting_format` builds one for a key it
  does not list.
- `SETTINGS_LAYOUT`: the run details' sections and groups, with each group's switch;
  `test_settings_layout_places_every_counted_setting_once` fails when a counted setting falls out of it.
- `build_manifest`: which filters a set of runs gets ("Made in" when asked, input data and settings when they
  differ, seed always, run name on a collision).

## The browser checks

`scripts/check_tour.mjs URL [--print]` walks the tour at 1440x900 and fails on a missing target, a card off screen,
a page or console error, or a view not restored; it prints "Tour OK". It does not compare a card's words with the
page: read them with `--print`. `scripts/check_viewer_offline.mjs URL` loads every pane and the first run's plot
pages with every request outside the viewer's origin blocked; it prints "Offline OK". Both need Playwright, which
resolves from the script's folder upwards, so install it at the repository root without saving it:
`npm install --no-save playwright && npx playwright install chromium`. `TOUR_BROWSER` points both at another
Chromium executable. URL is the address `steelo-viewer --no-browser` prints.

## Public repository

This package is public. Before each commit, search the added lines and the commit message for client, project and
campaign names, for country wording beyond the capacity policy's, and for the website's domain; justify each
remaining hit in the pull request.

The scenario viewer website keeps its own copy of the browser app. The two are separate until the website moves
over to this package.
