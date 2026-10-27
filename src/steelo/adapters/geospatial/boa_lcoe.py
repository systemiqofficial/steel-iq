"""
Find, check and read a promoted BOA LCOE file.

BOA's promotion step writes one NetCDF file per baseload coverage, holding the levelised cost of
electricity (LCOE) of the optimal solar, wind and battery system for every investment year, in USD
per MWh delivered. Its layout is a contract, described in BOA's ``docs/promoted-lcoe-file.md``.
Steel-iq reads it without importing BOA.
"""

import logging
import math
import numbers
import os
from pathlib import Path

import xarray as xr

LCOE_ONLY = "lcoe-only"
SCHEMA_VERSION = 1
LCOE_BASIS = "per_mwh_delivered"


def boa_root() -> Path:
    """
    The root of the local BOA data.

    Returns:
        ``$BOA_DATA_ROOT`` when it is set, else ``~/.boa``.

    Notes:
        BOA's own ``boa.config.paths.default_root`` applies the same rule.
    """
    if (root := os.getenv("BOA_DATA_ROOT")) is not None:
        return Path(root)
    return Path.home() / ".boa"


def resolve_boa_run(value: str) -> Path:
    """
    The folder of the promoted BOA run a user named.

    Args:
        value: A folder, or the name of a run under ``<boa root>/lcoe-only/``.

    Returns:
        The folder: ``value`` itself when it is an existing folder, else the run of that name.

    Raises:
        FileNotFoundError: If neither is a folder. The message names the places tried and lists every
            run under ``<boa root>/lcoe-only/`` with its files, whatever form ``value`` takes.
    """
    given = Path(value).expanduser().absolute()
    if given.is_dir():
        return given
    lcoe_only = boa_root() / LCOE_ONLY
    named = lcoe_only / value
    if named.is_dir():
        return named
    tried = " or ".join(dict.fromkeys(str(path) for path in (given, named)))
    raise FileNotFoundError(f"BOA run '{value}' not found: no folder at {tried}.\n{_list_runs(lcoe_only)}")


def coverage_token(coverage: float) -> str:
    """
    The token a promoted file's name carries for its coverage.

    Args:
        coverage: Baseload coverage as a fraction, e.g. 0.85.

    Returns:
        The coverage without a dot: ``0p85`` for 0.85, ``0p9`` for 0.9, ``1`` for 1.0.

    Notes:
        A copy of BOA's ``boa.config.paths.format_scenario_number`` at its default padding, kept
        here so steel-iq does not import BOA. The two must stay the same.
    """
    int_part, _, frac_part = f"{coverage:g}".partition(".")
    return f"{int_part}p{frac_part}" if frac_part else int_part


def pick_lcoe_file(folder: Path, coverage: float, *, default_folder: bool = False) -> Path:
    """
    The promoted LCOE file for a coverage in a folder.

    Args:
        folder: A promoted BOA run's folder, or the prepared data's default folder.
        coverage: Baseload coverage as a fraction.
        default_folder: Whether ``folder`` is the prepared data's default, used when no BOA run is named.

    Returns:
        The one ``*_cov<token>_*.nc`` file in the folder.

    Raises:
        FileNotFoundError: If the folder does not exist or holds no file for the coverage. The
            message names the folder, the token and the ``.nc`` files it holds.
        ValueError: If several files match.

    Notes:
        The token alone picks the file, as BOA's documentation describes. For the default folder,
        the error also says where its files come from and how to name another run.
    """
    token = f"cov{coverage_token(coverage)}"
    default_note = (
        "\nWithout --boa-run, the run reads the default LCOE files that geo-data 3.0.0 and later ship in "
        "this folder of the prepared data. Prepare the data with such a geo-data package, or name a promoted "
        "BOA run with --boa-run."
        if default_folder
        else ""
    )
    if not folder.is_dir():
        raise FileNotFoundError(f"Baseload LCOE folder {folder} does not exist.{default_note}")
    matches = sorted(folder.glob(f"*_{token}_*.nc"))
    if not matches:
        held = sorted(f.name for f in folder.glob("*.nc"))
        raise FileNotFoundError(
            f"No baseload LCOE file for coverage {coverage:g} ('_{token}_') in {folder}. "
            f"It holds: {', '.join(held) if held else 'no .nc files'}.{default_note}"
        )
    if len(matches) > 1:
        raise ValueError(
            f"Several baseload LCOE files for coverage {coverage:g} ('_{token}_') in {folder}: "
            f"{', '.join(f.name for f in matches)}. Keep one per coverage."
        )
    return matches[0]


def open_lcoe_file(path: Path, coverage: float) -> xr.Dataset:
    """
    Open a promoted LCOE file once it states the contract steel-iq reads.

    Args:
        path: The file, as ``pick_lcoe_file`` returns it.
        coverage: The baseload coverage being priced.

    Returns:
        The open dataset, with ``lcoe`` (year, lat, lon) in USD per MWh delivered. The caller closes it.

    Raises:
        ValueError: If ``schema_version`` is not 1, ``lcoe_basis`` is not ``per_mwh_delivered`` or
            ``coverage_fraction`` is not the number ``coverage``. A missing attribute is refused like a
            wrong one.
            The message names the file and the attribute.
    """
    ds = xr.open_dataset(path)
    attrs = ds.attrs
    refusal = None
    if "schema_version" not in attrs or attrs["schema_version"] != SCHEMA_VERSION:
        refusal = f"schema_version {_attr(attrs, 'schema_version')}; steel-iq reads schema version {SCHEMA_VERSION}"
    elif "lcoe_basis" not in attrs or attrs["lcoe_basis"] != LCOE_BASIS:
        refusal = f"lcoe_basis {_attr(attrs, 'lcoe_basis')}; steel-iq prices LCOE {LCOE_BASIS}"
    elif (
        "coverage_fraction" not in attrs
        or not isinstance(attrs["coverage_fraction"], numbers.Real)
        or not math.isclose(float(attrs["coverage_fraction"]), coverage, rel_tol=1e-9)
    ):
        refusal = f"coverage_fraction {_attr(attrs, 'coverage_fraction')}; the power mix prices coverage {coverage:g}"
    if refusal is not None:
        ds.close()
        raise ValueError(f"Baseload LCOE file {path} is refused: it has {refusal}.")
    return ds


def lcoe_for_year(ds: xr.Dataset, year: int) -> xr.DataArray:
    """
    One year's LCOE grid from a promoted file.

    Args:
        ds: The dataset ``open_lcoe_file`` returns.
        year: Simulation year.

    Returns:
        The LCOE (lat, lon) in USD per MWh delivered, without a ``year`` coordinate.

    Notes:
        A year in the file is read as it is; a year between two of the file's years is interpolated
        linearly; a year outside the file's span takes the nearest year's values, with one warning.
    """
    logger = logging.getLogger(f"{__name__}.lcoe_for_year")
    years = [int(y) for y in ds["year"].values]
    effective_year = min(max(year, min(years)), max(years))
    if effective_year != year:
        logger.warning(f"[BOA LCOE] The LCOE file covers {min(years)} to {max(years)}; {year} reuses {effective_year}.")
    if effective_year in years:
        return ds["lcoe"].sel(year=effective_year).drop_vars("year")
    return ds["lcoe"].interp(year=effective_year, method="linear").drop_vars("year")


def _attr(attrs: dict, name: str) -> str:
    """
    An attribute's value for an error message.

    Args:
        attrs: A dataset's attributes.
        name: Attribute name.

    Returns:
        Text quoted, a number as it reads, or ``missing``.
    """
    if name not in attrs:
        return "missing"
    value = attrs[name]
    return f"'{value}'" if isinstance(value, str) else str(value)


def _list_runs(lcoe_only: Path) -> str:
    """
    The promoted runs under a BOA root, for error messages.

    Args:
        lcoe_only: The ``lcoe-only`` folder under the BOA root.

    Returns:
        One line per run with its ``.nc`` files, or a line saying there are none.
    """
    runs = sorted(d for d in lcoe_only.iterdir() if d.is_dir()) if lcoe_only.is_dir() else []
    if not runs:
        return f"No promoted BOA runs under {lcoe_only}."
    lines = [f"Promoted BOA runs under {lcoe_only}:"]
    for run in runs:
        files = sorted(f.name for f in run.glob("*.nc"))
        lines.append(f"  {run.name}: {', '.join(files) if files else 'no .nc files'}")
    return "\n".join(lines)
