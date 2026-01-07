# SteelModel Installation Guide

:::{only} public
## Install from Published Package

1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/) if it is not already available on your machine.
2. Create a new virtual environment (optional but recommended):

   ```shell
   uv venv -p python3.13
   source .venv/bin/activate
   ```

3. Install the Steel Model package and its CLI tools:

   ```shell
   uv add steel-model
   ```

4. Verify the installation:

   ```shell
   run_simulation --help
   ```

   This command prints the CLI usage information and confirms the installation worked.

If you need to pin a specific version, append `==<version>` to the package name when running `uv add`.
:::

:::{only} not public
## Basic Installation

Check out the source repository:

```shell
git clone git@github.com:systemiqofficial/steel-model.git
cd steel-model
```

Install `uv` following the instructions on their [website](https://docs.astral.sh/uv/getting-started/installation/).

Create a virtual environment:

```shell
uv venv -p python3.13
```

If you don't have Python 3.13 installed, you can use `uv` to install it:

```shell
uv python install 3.13
```

Activate the virtual environment:

```shell
source .venv/bin/activate  # use the appropriate command for your shell
```

Install the dependencies:

```shell
uv sync
```

Install the pre-commit hooks:

```shell
uvx pre-commit install
```

## Running the Tests

To run the tests, use the following command:

```shell
pytest
```

Run the tests including the ones for the `wind_and_pv` package which are skipped by default:

```shell
pytest --run-wind-and-pv-tests
```

To run the tests with coverage, use the following command:

```shell
coverage run -m pytest
```

To generate a coverage report, use the following command:

```shell
coverage report
```

or 

```shell
coverage html
```

and open the `htmlcov/index.html` file in your browser.

## Running the Static Type Checker

To run the static type checker, use the following command:

```shell
mypy src/
```

## Managing Dependencies

To add a new dependency, use the `uv` command:

```shell
uv add <package-name>
```

To add a development dependency, use the `--dev` flag:

```shell
uv add --dev <package-name>
```

To update all dependencies to their latest versions, use the `uv sync` command:

```shell
uv sync
```

To upgrade the dependencies in the lockfile to their latest compatible versions:

```shell
uv lock --upgrade
```

## Build the Package

To build the package, use the following command:

```shell
uv build --sdist --wheel
```

## Run Jupyter Notebook

To run the Jupyter notebook, use the following command:

```shell
uv run --with jupyter jupyter lab --notebook-dir=notebooks/
```

Maybe prefix your notebooks with your name to avoid conflicts.

## Build Standalone Application

Via GitHub Actions, you can build a standalone application for Windows and macOS. The built applications are available
in the `dist` folder.

```shell
gh workflow run standalone_app.yaml
```
:::

## System Requirements

This package requires the [CBC solver](https://github.com/coin-or/Cbc) to be installed separately.
You can install it via:
- macOS: `brew install cbc`
- Ubuntu: `apt install coinor-cbc`
- Windows: Download from [Release Page](https://github.com/coin-or/Cbc/releases)

Ensure `cbc` is available in your system `PATH`.
