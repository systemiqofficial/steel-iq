"""The catalogue of local runs behind the scenario viewer: its ``manifest.json``, ``inputs.json`` and ``geo.json``.

Built on request from the run folders on disk. A run is listed when it holds ``viewer/run.json``. The filters are
the input data and the counted settings of ``simulation_config.json`` that differ between the runs, then the seed,
and the run name when two runs share every other filter value. The View data diff is the website's
``extract_inputs.py`` logic, run over the runs' own ``viewer/inputs.json`` files.
"""

import itertools
import json
import logging
import re
from dataclasses import MISSING, asdict, fields
from pathlib import Path
from typing import Any

from ..capacity_policy.config import CapacityPolicyConfig
from ..domain.constants import MT_TO_T
from ..simulation import GeoConfig, SimulationConfig
from .extract import GEO_NAME, INPUTS_NAME, RUN_NAME, VIEWER_DIR

logger = logging.getLogger(__name__)

TITLE = "Scenario viewer | Steel-IQ"
CONFIG_NAME = "simulation_config.json"
# Settings that cannot change results: paths, unread fields, drawing, logging, the run name, the seed's copy
EXCLUDED_SETTINGS = frozenset(
    {
        "output_dir",
        "plots_dir",
        "geo_plots_dir",
        "pam_plots_dir",
        "tm_output_dir",
        "policy_output_dir",
        "master_excel_path",
        "data_dir",
        "terrain_nc_path",
        "land_cover_tif_path",
        "rail_distance_nc_path",
        "countries_shapefile_dir",
        "disputed_areas_shapefile_dir",
        "landtype_percentage_nc_path",
        "baseload_power_sim_dir",
        "feasibility_mask_path",
        "minimum_active_utilisation_rate",
        "minimum_margin",
        "green_steel_emissions_limit",
        "scrap_generation_scenario",
        "demand_sheet_name",
        "use_master_excel",
        "steel_plant_gem_data_year",
        "production_gem_data_years",
        "excel_reader_start_year",
        "excel_reader_end_year",
        "plot_tm",
        "plot_geo",
        "log_level",
        "run_name",
        "geo_config.random_seed",
    }
)
TONNE_FIELDS = frozenset({"expanded_capacity", "capacity_limit_iron", "capacity_limit_steel"})
MADE_IN = {"cli": "CLI", "app": "App", None: "unknown"}
# Folders named in a "skipped" warning since they were last listed, so a run in progress is named once, not with
# every page load, and a listed run that loses run.json is named again
WARNED_SKIPPED: set[Path] = set()


def read_run(run_dir: Path) -> dict[str, Any]:
    """One run's ``viewer/run.json``.

    Args:
        run_dir: The run folder.

    Returns:
        The parsed file.
    """
    return json.loads((run_dir / VIEWER_DIR / RUN_NAME).read_text())


def discover_runs(output_base: Path | None, run_dirs: list[Path]) -> list[Path]:
    """The run folders the viewer lists, oldest first.

    Args:
        output_base: A folder whose run folders are all listed, or None.
        run_dirs: Run folders named one by one.

    Returns:
        The folders directly under ``output_base`` and those in ``run_dirs`` that hold ``viewer/run.json``,
        sorted by the run's ``started`` time.

    Notes:
        Symlinked folders under ``output_base`` (``latest``) are skipped, so a run is listed once. A folder under
        ``output_base`` with ``simulation_config.json`` but no ``run.json`` (a run in progress, or one whose viewer
        step failed) and a named folder without ``run.json`` are named in one warning, once until the folder is
        listed again, since the server calls this with every set file.
    """
    folders = (
        [] if output_base is None else sorted(p for p in output_base.iterdir() if p.is_dir() and not p.is_symlink())
    )
    candidates = folders + run_dirs
    runs = [folder for folder in candidates if (folder / VIEWER_DIR / RUN_NAME).is_file()]
    WARNED_SKIPPED.difference_update(runs)
    skipped = [
        folder
        for folder in candidates
        if folder not in runs and (folder in run_dirs or (folder / CONFIG_NAME).is_file())
    ]
    unwarned = [folder for folder in skipped if folder not in WARNED_SKIPPED]
    if unwarned:
        logger.warning(f"skipped, no viewer/run.json: {', '.join(str(folder) for folder in unwarned)}")
        WARNED_SKIPPED.update(unwarned)
    return sorted(runs, key=lambda run_dir: read_run(run_dir)["started"])


def flatten_into(flat: dict[str, Any], key: str, value: Any) -> None:
    """Add one setting to ``flat``, a dict value as one dotted key per entry, at any depth.

    Args:
        flat: The flattened settings, filled in place.
        key: The setting's dotted key so far.
        value: Its value from the config file.
    """
    if isinstance(value, dict):
        for entry, entry_value in value.items():
            flatten_into(flat, f"{key}.{entry}", entry_value)
    else:
        flat[key] = value


def flatten_settings(config: dict[str, Any]) -> dict[str, Any]:
    """The counted settings of one ``simulation_config.json``, as dotted keys in config order.

    Args:
        config: The parsed config file, or a dict in its shape.

    Returns:
        ``{dotted key: value}``: ``geo_config.*``, ``capacity_policy.*`` and
        ``technology_settings.<code>.<field>`` flattened (technologies alphabetical), a dict inside them one level
        further per entry, lists kept as lists, ``random_seed`` under ``seed``, and ``EXCLUDED_SETTINGS`` left out.
    """
    flat: dict[str, Any] = {}
    for key, value in config.items():
        if key == "technology_settings":
            value = dict(sorted(value.items()))
        flatten_into(flat, "seed" if key == "random_seed" else key, value)
    return {key: value for key, value in flat.items() if key not in EXCLUDED_SETTINGS}


def format_value(key: str, value: Any) -> str:
    """One setting's value as the viewer shows it, in filters and in the run detail.

    Args:
        key: The setting's dotted key.
        value: Its value from the config file.

    Returns:
        "none" for None and for an empty list, "on" or "off" for booleans, a list joined with ", ", a float through
        ``format(value, ".15g")``, the fields in ``TONNE_FIELDS`` in Mt with the unit ("2.5 Mt"), anything else as
        ``str``.

    Notes:
        Fifteen significant digits keep every digit a setting is given with, so two runs differing in a late digit
        still differ here, while 100.0 still reads "100".
    """
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) if value else "none"
    if key in TONNE_FIELDS:
        return f"{value / MT_TO_T:.15g} Mt"
    if isinstance(value, float):
        return format(value, ".15g")
    return str(value)


def label_for(key: str) -> str:
    """A setting's display label.

    Args:
        key: The setting's dotted key.

    Returns:
        The last field name with underscores as spaces and its first letter capitalised ("Hydrogen ceiling
        percentile"); "Capacity policy: <field>" for ``capacity_policy.*``; "<code> <field>" for
        ``technology_settings.*``; "<field>: <entry>" for an entry of a dict setting.
    """
    parts = key.split(".")
    if parts[0] == "technology_settings":
        return f"{parts[1]} {parts[2].replace('_', ' ')}"
    if parts[0] == "capacity_policy":
        return f"Capacity policy: {parts[1].replace('_', ' ')}"
    if parts[0] == "geo_config":
        parts = parts[1:]
    label = parts[0].replace("_", " ")
    label = label[:1].upper() + label[1:]
    return f"{label}: {' '.join(parts[1:]).replace('_', ' ')}" if len(parts) > 1 else label


def default_settings(config: dict[str, Any]) -> dict[str, Any]:
    """The defaults one run's counted settings are compared with, flattened as :func:`flatten_settings` does.

    Args:
        config: The run's parsed ``simulation_config.json``.

    Returns:
        ``{dotted key: default}`` from the ``SimulationConfig`` field defaults, ``GeoConfig()`` and
        ``CapacityPolicyConfig()``, plus the technology settings of ``<data_dir>/fixtures/technologies.json``, which
        both entry points start from, when that file exists.

    Notes:
        A field without a default and, without the technologies file, every technology setting are left out, so
        their changed-from-default flag is unknown rather than compared with another default.
    """
    defaults: dict[str, Any] = {}
    for config_field in fields(SimulationConfig):
        if config_field.name.startswith("_"):
            continue
        if config_field.default is not MISSING:
            defaults[config_field.name] = config_field.default
        elif config_field.default_factory is not MISSING:
            defaults[config_field.name] = config_field.default_factory()
    defaults["geo_config"] = asdict(GeoConfig())
    defaults["capacity_policy"] = asdict(CapacityPolicyConfig())
    defaults["disposal_cost_outputs"] = sorted(defaults["disposal_cost_outputs"])
    del defaults["technology_settings"]
    technologies = Path(config["data_dir"]) / "fixtures" / "technologies.json" if config["data_dir"] else None
    if technologies is not None and technologies.is_file():
        defaults["technology_settings"] = {
            tech["normalized_code"]: {name: tech[name] for name in ("allowed", "from_year", "to_year")}
            for tech in json.loads(technologies.read_text())["technologies"].values()
        }
    return flatten_settings(defaults)


def input_data_label(run: dict[str, Any]) -> str:
    """The input data one run used, as its filter value.

    Args:
        run: The run's ``viewer/run.json``.

    Returns:
        "<master file name> (default|custom)" for a CLI run, "<master_name> / <preparation_name> (default|custom)"
        for an app run; "unknown" in the brackets when the run records no master source.
    """
    data = run["input_data"]
    source = "unknown" if data["master_source"] is None else data["master_source"]
    if run["made_in"] == "app":
        return f"{data['master_name']} / {data['preparation_name']} ({source})"
    return f"{data['master']} ({source})"


def plot_label(filename: str) -> str:
    """A plot page's tab label (cost_curves.html -> Cost curves).

    Args:
        filename: The page's file name.

    Returns:
        The stem with underscores as spaces, capitalised.
    """
    return Path(filename).stem.replace("_", " ").capitalize()


def natural_key(value: str) -> list[Any]:
    """Sort key treating digit groups numerically (test_2 < test_10).

    Args:
        value: A filter value.

    Returns:
        The value split into text and number parts.
    """
    return [int(token) if token.isdigit() else token for token in re.split(r"(\d+)", value)]


def ordered_values(values: set[str]) -> list[str]:
    """One filter's values in display order.

    Args:
        values: The formatted values the runs hold.

    Returns:
        The values in numeric order when every one is a number, else in natural order.
    """
    try:
        return sorted(values, key=float)
    except ValueError:
        return sorted(values, key=natural_key)


def build_manifest(run_dirs: list[Path], with_made_in: bool) -> dict[str, Any]:
    """The viewer's ``manifest.json`` for a list of runs: one set, "Local runs".

    Args:
        run_dirs: The run folders, each holding ``viewer/run.json`` and ``simulation_config.json``.
        with_made_in: Whether to add the "Made in" filter, for a catalogue holding both app and CLI runs.

    Returns:
        ``{"title", "sets": [set]}``, the set holding ``dimensions`` (made in when asked, input data and the
        counted settings when they differ between runs, seed always, run on a collision), ``plots`` and ``runs``
        oldest first. Each run carries ``dir``, ``name``, ``made_in``, ``started``, ``input_data``, ``link``,
        ``dims``, ``plots`` and ``settings``: every counted setting with its label, formatted value, ``changed``
        (null when the default is unknown) and ``varies``.

    Raises:
        json.JSONDecodeError: If a run's ``run.json`` or ``simulation_config.json`` does not parse.

    Notes:
        The run filter appears only when two runs share every other filter value, since such runs would otherwise
        form one scenario with only the first reachable. Its value is the run name, or "<name> (<folder>)" for a
        name more than one listed run holds (a copied run, or runs from two output bases).
    """
    loaded = []
    for run_dir in run_dirs:
        config = json.loads((run_dir / CONFIG_NAME).read_text())
        settings = {key: format_value(key, value) for key, value in flatten_settings(config).items()}
        defaults = {key: format_value(key, value) for key, value in default_settings(config).items()}
        loaded.append((run_dir, read_run(run_dir), settings, defaults))
    loaded.sort(key=lambda item: item[1]["started"])

    setting_keys = list(dict.fromkeys(key for _, _, settings, _ in loaded for key in settings))
    # A setting one run's config lacks (another master's technologies, an older run) reads "none" for it
    values = [{key: settings.get(key, "none") for key in setting_keys} for _, _, settings, _ in loaded]
    input_data = [input_data_label(run) for _, run, _, _ in loaded]
    columns: list[tuple[str, list[str]]] = []
    if with_made_in:
        columns.append(("made_in", [MADE_IN[run["made_in"]] for _, run, _, _ in loaded]))
    if len(set(input_data)) > 1:
        columns.append(("input_data", input_data))
    for key in setting_keys:
        column = [run_values[key] for run_values in values]
        if key != "seed" and len(set(column)) > 1:
            columns.append((key, column))
    columns.append(("seed", [run_values["seed"] for run_values in values]))
    if len(set(zip(*(column for _, column in columns)))) < len(loaded):
        names = [run["name"] for _, run, _, _ in loaded]
        run_values = [
            name if names.count(name) == 1 else f"{name} ({run_dir.name})"
            for name, (run_dir, _, _, _) in zip(names, loaded)
        ]
        columns.append(("run", run_values))
    varying = {key for key, column in columns if len(set(column)) > 1}

    runs = [
        {
            "dir": run_dir.name,
            "name": run["name"],
            "made_in": MADE_IN[run["made_in"]],
            "started": run["started"],
            "input_data": input_data[i],
            "link": f"runs/{run_dir.name}/",
            "dims": {key: column[i] for key, column in columns},
            "plots": run["pages"],
            "settings": [
                {
                    "key": key,
                    "label": label_for(key),
                    "value": value,
                    "changed": value != defaults[key] if key in defaults else None,
                    "varies": key in varying,
                }
                for key, value in settings.items()
            ],
        }
        for i, (run_dir, run, settings, defaults) in enumerate(loaded)
    ]
    dimensions = [
        {"key": key, "label": label_for(key), "values": ordered_values(set(column))} for key, column in columns
    ]
    plots = sorted({page for run in runs for page in run["plots"]})
    return {
        "title": TITLE,
        "sets": [
            {
                "id": "local",
                "label": "Local runs",
                "base": "runs",
                "dimensions": dimensions,
                "plots": [{"file": page, "label": plot_label(page)} for page in plots],
                "runs": runs,
            }
        ],
    }


def explain(runs_with: set[int], runs: list[dict[str, Any]], dims: list[dict[str, Any]]) -> dict[str, list[str]] | None:
    """Smallest dimension-value combination selecting exactly runs_with.

    Args:
        runs_with: Indices into ``runs`` of the runs sharing one variant of a row.
        runs: The set's runs, each with its ``dims``.
        dims: The dimensions with more than one value, each with its ``values`` in display order.

    Returns:
        ``{dimension key: [values]}`` for the simplest subset of the dimensions for which "run.dims[d] in
        values[d] for every d in the subset" holds for exactly the runs in runs_with: fewest dimensions, then
        fewest values listed. None when no subset explains the split.
    """
    values = {d["key"]: sorted({runs[i]["dims"][d["key"]] for i in runs_with}, key=d["values"].index) for d in dims}
    candidates = [d["key"] for d in dims if len(values[d["key"]]) < len(d["values"])]
    best: tuple[tuple[int, int], dict[str, list[str]]] | None = None
    for n in range(1, len(candidates) + 1):
        for subset in itertools.combinations(candidates, n):
            match = {i for i, r in enumerate(runs) if all(r["dims"][d] in values[d] for d in subset)}
            if match != runs_with:
                continue
            score = (n, sum(len(values[d]) for d in subset))
            if best is None or score < best[0]:
                best = (score, {d: values[d] for d in subset})
        if best:
            break
    return best[1] if best else None


def cond_order(cond: dict[str, list[str]] | None, dims: list[dict[str, Any]]) -> list[Any]:
    """Sort key placing variants in dimension order, then in each dimension's value order.

    Args:
        cond: A variant's condition, None for the base variant.
        dims: The dimensions with more than one value.

    Returns:
        The key: the base variant first, then by dimension position and value positions.
    """
    if cond is None:
        return [[-1]]
    return [[i, [d["values"].index(v) for v in cond[d["key"]]]] for i, d in enumerate(dims) if d["key"] in cond]


def build_topic(
    topic: dict[str, Any],
    per_run: list[dict[tuple[Any, ...], tuple[Any, ...]]],
    fixed: set[tuple[Any, ...]],
    runs: list[dict[str, Any]],
    dims: list[dict[str, Any]],
    base_idx: int,
) -> dict[str, Any]:
    """One View data topic with its rows' values grouped into variants across the runs.

    Args:
        topic: The topic's entry in the first run's ``viewer/inputs.json``.
        per_run: Per run, row key -> value tuple.
        fixed: The keys of fixed rows (one value each, not a series) in any run.
        runs: The set's runs, each with its ``dir`` and ``dims``.
        dims: The dimensions with more than one value.
        base_idx: Index of the base run in ``runs``.

    Returns:
        The topic's id, label, sheet, note, unit, key and value columns, and ``rows``: ``{"k", "fixed"?, "v"}``
        with the base run's variant first (``cond`` null) and every other variant's ``vals`` and ``cond``, plus
        ``runs`` when no condition explains it. ``vals`` null means the row is absent from that variant's masters.
    """
    keys = sorted({k for rows in per_run for k in rows}, key=lambda k: tuple(str(x) for x in k))
    out_rows = []
    for key in keys:
        groups: dict[tuple[Any, ...] | None, set[int]] = {}
        for i, rows in enumerate(per_run):
            groups.setdefault(rows.get(key), set()).add(i)
        base_vals = per_run[base_idx].get(key)
        variants: list[dict[str, Any]] = [{"vals": None if base_vals is None else list(base_vals), "cond": None}]
        for vals, members in groups.items():
            if vals == base_vals:
                continue
            cond = explain(members, runs, dims)
            variants.append(
                {
                    "vals": None if vals is None else list(vals),
                    "cond": cond,
                    **({"runs": sorted(runs[i]["dir"] for i in members)} if cond is None else {}),
                }
            )
        variants[1:] = sorted(variants[1:], key=lambda v: cond_order(v["cond"], dims))
        out_rows.append(
            {"k": list(key[: len(topic["key_columns"])]), **({"fixed": True} if key in fixed else {}), "v": variants}
        )
    return {
        "id": topic["id"],
        "label": topic["label"],
        "sheet": topic["sheet"],
        "note": topic["note"],
        "unit": topic["unit"],
        "key_columns": topic["key_columns"],
        "value_columns": topic["value_columns"],
        "rows": out_rows,
    }


def build_inputs(run_dirs: list[Path], manifest: dict[str, Any]) -> dict[str, Any]:
    """The View data pane's ``inputs.json``: each topic's rows diffed across the runs.

    Args:
        run_dirs: The run folders, each holding ``viewer/inputs.json``.
        manifest: Their :func:`build_manifest` output.

    Returns:
        ``{"dims", "base", "topics"}``: the dimensions with more than one value among the runs, the base run's
        dims (the run on the first value of the most dimensions, ties to the earliest) and the topics as
        :func:`build_topic` writes them.

    Raises:
        ValueError: If a run's ``inputs.json`` does not parse (naming the file), or a topic's runs do not share the
            same value columns.
    """
    by_folder = {run_dir.name: run_dir for run_dir in run_dirs}
    local = manifest["sets"][0]
    runs = [run for run in local["runs"] if run["dir"] in by_folder]
    per_run = []
    for run in runs:
        path = by_folder[run["dir"]] / VIEWER_DIR / INPUTS_NAME
        try:
            per_run.append(json.loads(path.read_text()))
        except json.JSONDecodeError as error:
            raise ValueError(f"{path}: {error}") from error
    # Dimensions and values as these runs span them
    dims = [
        {**d, "values": [v for v in d["values"] if any(r["dims"][d["key"]] == v for r in runs)]}
        for d in local["dimensions"]
    ]
    dims = [d for d in dims if len(d["values"]) > 1]
    at_first = [sum(r["dims"][d["key"]] == d["values"][0] for d in dims) for r in runs]
    base_idx = at_first.index(max(at_first))

    topics = []
    for first in per_run[0]["topics"]:
        run_topics = [next(t for t in inputs["topics"] if t["id"] == first["id"]) for inputs in per_run]
        if any(t["value_columns"] != first["value_columns"] for t in run_topics):
            raise ValueError(f"{first['label']}: the runs' masters do not share the same value columns")
        rows = [{tuple(row["k"]): tuple(row["vals"]) for row in t["rows"]} for t in run_topics]
        fixed = {tuple(row["k"]) for t in run_topics for row in t["rows"] if "fixed" in row}
        topics.append(build_topic(first, rows, fixed, runs, dims, base_idx))
    return {
        "dims": [{"key": d["key"], "label": d["label"], "values": d["values"]} for d in dims],
        "base": runs[base_idx]["dims"],
        "topics": topics,
    }


def geo_path(run_dirs: list[Path]) -> Path | None:
    """The ``geo.json`` the set uses: the newest run's that has one.

    Args:
        run_dirs: The run folders.

    Returns:
        ``<run>/viewer/geo.json`` of the run with the latest ``started`` among those holding the file, or None
        when no run holds one.

    Notes:
        Runs on different masters can differ here only in names and groupings.
    """
    for run_dir in sorted(run_dirs, key=lambda run_dir: read_run(run_dir)["started"], reverse=True):
        path = run_dir / VIEWER_DIR / GEO_NAME
        if path.is_file():
            return path
    return None
