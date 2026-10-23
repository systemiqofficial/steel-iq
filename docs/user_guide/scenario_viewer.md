# Scenario Viewer

The scenario viewer shows the results of local runs in the browser and compares them: each run's interactive plots,
one measure across several scenarios, the annual cost of a tonne of iron or steel, the input data each run was made
with, and each run's result tables. It reads what each run leaves in its own folder: the `viewer/` folder every run
writes at its end, the interactive plot pages and the run's settings. It works offline. Each file in `viewer/` and
its source are listed in
[Outputs and Post-Processing](../domain_simulation_logic/overarching_elements/outputs_and_postprocessing.md#scenario-viewer-files-viewer).

## Opening It from the Command Line

At its end, `run_simulation` prints the command that opens the run:

```text
View it: steelo-viewer /Users/me/.steelo/output/sim_20261023_101500
```

`steelo-viewer` serves the viewer on `127.0.0.1`, prints its address and "Runs served: N; press Ctrl-C to stop", and
opens it in the default browser. Press Ctrl-C to stop it.

```bash
# Every run under $STEELO_HOME/output
steelo-viewer

# Only the runs named, on a fixed port, without opening the browser
steelo-viewer ~/.steelo/output/latest ~/.steelo/output/sim_20261022_093000 --port 8080 --no-browser
```

- With no run folder named, it serves every run folder directly under the output folder: `--output-dir`, by default
  `<steelo-home>/output`, where `--steelo-home` defaults to `$STEELO_HOME`, else `~/.steelo`. Symlinked folders such
  as `latest` are skipped there, so each run is listed once.
- Run folders named on the command line are served alone, by their real paths, so `.` and `latest` work;
  `--output-dir` and `--steelo-home` are then not used.
- `--port` sets the port (default: a free one); `--no-browser` leaves the browser closed.
- The viewer looks for runs again each time the page loads, so a run that finishes while it is open appears after a
  reload.
- It answers only requests addressed to `127.0.0.1` or `localhost`.
- It exits with code 1 and serves nothing when the output folder does not exist, when no run holds
  `viewer/run.json`, or when two named run folders share a folder name (serve them one at a time).

The full option list is in [CLI Commands](cli_commands.md).

## Opening It from the App

In the app, **Scenario viewer** in the navigation bar opens the viewer below the app's navigation, under the title
"STEEL-IQ Scenario Viewer". **Tour** above it starts the viewer's tour, and **Open in browser** opens the same viewer
in a browser tab of its own (the standalone application hands it to the system's browser). A finished run's page
shows **Open in viewer** once the run has written its viewer files; it opens the viewer on that run.

The app's viewer lists the app's finished runs and, beside them, the command-line runs under `$STEELO_HOME/output`
(the app's `STEELO_HOME`). A command-line run folder named like an app run's folder (`run_<id>`) is skipped, with a
warning in the app's log. In the run details, an app run's folder link opens its run page; a command-line run's
opens its folder listing in a new window.

## Run Names

A run's name comes from `--run-name` on the command line or the name field of the app's run form. A name already
taken gets the first free suffix: `_2`, `_3` and so on. A command-line run checks the names of the runs under its
output folder; an app run checks, when it is created, the other app runs and the command-line runs under
`$STEELO_HOME/output`. Only an exact match counts, so "BAU high" beside "BAU" keeps its name.

The run folders keep their own names: `sim_<timestamp>` for a command-line run and `run_<id>` for an app run. A run
without a name shows as its folder name (command line) or as "Model Run #<id>" (app).

## How the Filters Are Chosen

The viewer groups the runs it lists into scenarios, using filters it builds from those runs, so the filters change
with the runs:

- **Made in** (App or CLI): only in the app's viewer, which lists both kinds of run.
- **Input data**: only when the runs' input data differ. A command-line run is labelled by its master workbook's file
  name, an app run by the master's name in the app and the data preparation's name, each followed by "(default)" or
  "(custom)". One master prepared both ways therefore shows as two values.
- **Settings**: every run setting whose value, as the viewer shows it, differs between the runs. Settings that cannot
  change the results never count: file and folder paths, settings the model does not read, the plot and logging
  switches, the run name, and the copy of the seed inside the geospatial settings.
- **Seed**: always present, but not a sidebar filter. Runs that differ only in their seed form one scenario, and
  Explore shows a **Seed** row to pick between them.
- **Run**: only when two runs would otherwise share every value above, seed included. It holds the run name, or
  "<name> (<folder>)" for a name that more than one listed run holds.

A scenario is one combination of the filter values. Each gets a fixed **#n** tag, which the charts repeat.

## The Run Table and Run Details

With nothing picked, a pane shows the run table: one row per run, with its scenario's **#n**, its name, when it
started and its input data. A row that the sidebar's filters leave out is faded. A click on a row picks that run in
Explore and Download results (Explore can also take two runs for its split view), and sets the filters to the run's
scenario in View data. In Compare and Annual costs the table picks up to 20 scenarios, then **Show** draws them; the
sidebar's **select all** can pick more, and Compare then charts the first 20 and says so.

**details** on a row opens the run's settings. The top shows the run's input data, where it was made (App, CLI or
unknown), when it started and a link to its folder, then its period, seed and scenarios, and two lists: **Changed
from the default**, with each default, and, when more than one run is listed, **Varies across the listed runs**.
Below them come the settings in groups; a group whose switch is off is greyed out. The technology table marks each
technology excluded from greenfield builds as **Excluded**.

The defaults are those of the run settings in the code and, for the technologies, the run's prepared
`technologies.json`. Without that file, the technologies' defaults are unknown, and the table says "Defaults unknown
without the run's technologies.json".

## The Panes

- **Explore**: one scenario's plot pages, the model's own interactive pages, in tabs. **Split view** above the
  scenario list shows two scenarios, A and B, side by side; the A / B toggle chooses which side the next click in the
  list fills, and **⇄ Swap** trades them. A **Seed** row above the tabs picks between a scenario's runs when it has
  several.
- **Compare**: one measure across the picked scenarios on one chart, with the numbers in a **Data table** below it.
- **Annual costs**: the cost of a tonne of iron or crude steel, split into its components, for the picked scenarios.
- **View data**: the input data the runs were made with, by topic, read from their master workbooks. A row that is
  the same in every run shows once; a row that differs shows once per variant, tagged with the filter value that
  selects it.
- **Download results**: one run's result tables, in those of the folders Run, Data, LCOE, LCOH, Trade and Policy that
  the run has. A table downloads as a gzipped CSV, all of them as one zip, or opens in the page, page by page.

## Rebuilding the Viewer Files

`steelo-viewer regenerate` writes a finished run's `viewer/` folder again, for example when the run's own viewer step
failed:

```bash
steelo-viewer regenerate ~/.steelo/output/sim_20261023_101500

# Read the master workbook from another path than the one the run recorded
steelo-viewer regenerate RUN_DIR [RUN_DIR ...] --master-excel /path/to/master.xlsx
```

Each run folder must hold a `post_processed_*.csv`, `simulation_config.json`, `preparation_metadata.json` and
`plots/interactive/emissions.html`, and the master workbook the run recorded (or the one `--master-excel` names) must
exist. The command checks every run first. When anything is missing, it names each missing file, writes nothing for
any run and exits with code 1. Otherwise it deletes and rewrites each run's `viewer/` folder and leaves the plot pages
and the model's outputs alone; a missing optional plot page skips the file taken from it, with a warning.

An app run's folder is `model_outputs/run_<id>/` in the app's media folder (`src/django/media/` when the app runs
from source); it is rebuilt the same way.

## When a Run Is Missing

The viewer lists a run only once its folder holds `viewer/run.json`, the file written last at the run's end. A run
is missing when:

- it is still running;
- its viewer step failed: the run itself still finishes, its log says "viewer files incomplete for this run", and
  `steelo-viewer regenerate` writes the files again;
- in the app: it is an app run that has not finished, or a command-line run whose folder is named like an app run's
  (`run_<id>`).

Run folders skipped for want of `viewer/run.json` are named in a warning in the terminal (or the app's log).
