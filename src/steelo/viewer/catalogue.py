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
from typing import Any, NamedTuple

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
        "boa_run_dir",
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
MADE_IN = {"cli": "CLI", "app": "App", None: "unknown"}


class SettingFormat(NamedTuple):
    """How one counted setting reads in the viewer."""

    label: str  # In its group of the run details
    full: str = ""  # Standing alone: the filters and the run details' lists; the label when empty
    unit: str = ""  # After the number, muted in the run details
    template: str = "{}"  # The number with its symbols: "{}%", "{}×", "÷ {}"
    scale: float = 1  # Config value to the number shown
    none: str = "none"
    yes_no: bool = False  # True and False as yes and no, not on and off


SHARE: dict[str, Any] = {"template": "{}%", "scale": 100}
TECHNOLOGIES = "technology_settings."
LAND_COVER = "geo_config.land_cover_factor."
TRADE_MATRIX = "geo_config.intraregional_trade_matrix."
SETTING_FORMATS = {
    "start_year": SettingFormat("Start year"),
    "end_year": SettingFormat("End year"),
    "seed": SettingFormat("Seed"),
    "chosen_demand_scenario": SettingFormat("Steel demand"),
    "chosen_scrap_scenario": SettingFormat("Scrap availability"),
    "chosen_grid_emissions_scenario": SettingFormat("Grid emissions"),
    "chosen_emissions_boundary_for_carbon_costs": SettingFormat(
        "Emissions boundary", full="Carbon cost emissions boundary"
    ),
    "carbon_border_export_rebates": SettingFormat("Border export rebates", full="Carbon border export rebates"),
    "include_tariffs": SettingFormat("Tariffs"),
    "capacity_policy.enabled": SettingFormat("Capacity policy"),
    "capacity_policy.replacement_ratio": SettingFormat(
        "Replacement ratio, old to new", full="Capacity policy replacement ratio", template="{} : 1"
    ),
    "capacity_policy.emission_intense_penalty_divisor": SettingFormat(
        "Emission-intense build divisor", full="Capacity policy emission-intense build divisor", template="÷ {}"
    ),
    "capacity_policy.min_utilization_for_renovation": SettingFormat(
        "Minimum utilisation for renovation", full="Capacity policy minimum utilisation for renovation", **SHARE
    ),
    "capacity_policy.utilization_window_years": SettingFormat(
        "Utilisation window", full="Capacity policy utilisation window", unit="years"
    ),
    "capacity_policy.inter_company_swap_cutoff_year": SettingFormat(
        "Inter-company swap cut-off", full="Capacity policy inter-company swap cut-off"
    ),
    "capacity_policy.banked_credit_rule": SettingFormat("Banked credits", full="Capacity policy banked credits"),
    "capacity_policy.credit_validity_years": SettingFormat(
        "Credit validity", full="Capacity policy credit validity", unit="years", none="never expires"
    ),
    "capacity_policy.capacity_pool_max_retry_years": SettingFormat(
        "Greenfield retry limit", full="Capacity policy greenfield retry limit", unit="years"
    ),
    "capacity_policy.renovation_counts_as_replace": SettingFormat(
        "Renovation counts as replacement", full="Capacity policy renovation counts as replacement", yes_no=True
    ),
    "plant_lifetime": SettingFormat("Plant lifetime", unit="years"),
    "equity_share": SettingFormat("Equity share", **SHARE),
    "global_risk_free_rate": SettingFormat("Risk-free rate", full="Global risk-free rate", **SHARE),
    "opening_balance_multiplier": SettingFormat("Opening balance multiplier", template="{}×"),
    "active_statuses": SettingFormat("Active", full="Active statuses"),
    "announced_statuses": SettingFormat("Announced", full="Announced statuses"),
    "expanded_capacity": SettingFormat("Expansion or new plant size", unit="Mt", scale=1 / MT_TO_T),
    "capacity_limit_iron": SettingFormat(
        "Yearly limit, iron", full="Yearly new capacity limit, iron", unit="Mt", scale=1 / MT_TO_T
    ),
    "capacity_limit_steel": SettingFormat(
        "Yearly limit, steel", full="Yearly new capacity limit, steel", unit="Mt", scale=1 / MT_TO_T
    ),
    "new_capacity_share_from_new_plants": SettingFormat(
        "Share from new plants", full="Share of new capacity from new plants", **SHARE
    ),
    "consideration_time": SettingFormat("Consideration", full="Consideration time", unit="years"),
    "construction_time": SettingFormat("Construction", full="Construction time", unit="years"),
    "probabilistic_agents": SettingFormat("Probabilistic agents"),
    "probability_of_announcement": SettingFormat("Announcement probability", **SHARE),
    "probability_of_construction": SettingFormat("Construction probability", **SHARE),
    "geo_config.pick_priority_sites_share": SettingFormat(
        "Priority sites", full="Priority sites share", unit="of grid points", **SHARE
    ),
    "calculate_npv_sites_share": SettingFormat(
        "NPV sample", full="NPV sample share", unit="of priority sites", **SHARE
    ),
    "top_n_loctechs_as_business_op": SettingFormat("Top opportunities per product and year"),
    "opportunity_pool_depth": SettingFormat("Pool depth", full="Opportunity pool depth"),
    "co2_storage_reserved_discount_factor": SettingFormat("CCS storage reservation", unit="of CO₂ need", **SHARE),
    "geo_config.max_altitude": SettingFormat("Maximum altitude", unit="m"),
    "geo_config.max_slope": SettingFormat("Maximum slope", template="{}°"),
    "geo_config.max_latitude": SettingFormat("Maximum latitude", template="{}°"),
    "geo_config.include_transport_cost": SettingFormat("Transport cost"),
    "geo_config.transportation_cost_per_km_per_ton.iron_mine_to_plant": SettingFormat(
        "Mine → iron plant", full="Transport cost, mine → iron plant", unit="USD/t/km"
    ),
    "geo_config.transportation_cost_per_km_per_ton.iron_to_steel_plant": SettingFormat(
        "Iron plant → steel plant", full="Transport cost, iron plant → steel plant", unit="USD/t/km"
    ),
    "geo_config.transportation_cost_per_km_per_ton.steel_to_demand": SettingFormat(
        "Steel plant → demand", full="Transport cost, steel plant → demand", unit="USD/t/km"
    ),
    "geo_config.include_infrastructure_cost": SettingFormat("Rail infrastructure cost"),
    "geo_config.iron_ore_steel_ratio": SettingFormat("Iron ore per tonne of steel", unit="t"),
    "geo_config.share_iron_vs_steel.iron.capex_share": SettingFormat("Ironmaking share of CAPEX", **SHARE),
    "geo_config.share_iron_vs_steel.steel.capex_share": SettingFormat("Steelmaking share of CAPEX", **SHARE),
    "geo_config.share_iron_vs_steel.iron.energy_consumption_per_t": SettingFormat(
        "Ironmaking energy use", unit="MWh/t"
    ),
    "geo_config.share_iron_vs_steel.steel.energy_consumption_per_t": SettingFormat(
        "Steelmaking energy use", unit="MWh/t"
    ),
    "geo_config.include_lulc_cost": SettingFormat("Land cover cost"),
    "geo_config.excluded_greenfield_technologies": SettingFormat("Excluded from greenfield"),
    "geo_config.included_power_mix": SettingFormat("Power mix"),
    "boa_run": SettingFormat("Baseload LCOE", full="Baseload LCOE run", none="geo-data default"),
    "geo_data_version": SettingFormat("Geo-data", full="Geo-data version"),
    "geo_config.hydrogen_ceiling_percentile": SettingFormat("Hydrogen price ceiling"),
    "geo_config.long_dist_pipeline_transport_cost": SettingFormat(
        "Long-distance pipeline", full="Long-distance hydrogen pipeline cost", unit="USD/kg H₂"
    ),
    "geo_config.intraregional_trade_allowed": SettingFormat("Intraregional hydrogen trade"),
    "optimization_solver": SettingFormat("Solver", full="LP solver"),
    "lp_epsilon": SettingFormat("Epsilon", full="LP epsilon"),
    "capacity_limit": SettingFormat("Capacity availability", **SHARE),
    "soft_minimum_capacity_share": SettingFormat("Soft minimum utilisation", **SHARE),
    "enable_furnace_group_clustering": SettingFormat("Furnace group clustering"),
    "geographical_clustering_scope": SettingFormat("Scope", full="Clustering scope"),
    "hot_metal_radius": SettingFormat("Hot metal radius", unit="km"),
    "primary_products": SettingFormat("Primary", full="Primary products"),
    "closely_allocated_products": SettingFormat("Closely allocated", full="Closely allocated products"),
    "distantly_allocated_products": SettingFormat("Distantly allocated", full="Distantly allocated products"),
    "steel_price_buffer": SettingFormat("Steel", full="Steel shortage premium", unit="USD/t"),
    "iron_price_buffer": SettingFormat("Iron", full="Iron shortage premium", unit="USD/t"),
    "steel_market_clearing_share": SettingFormat("Steel", full="Steel market clearing share", **SHARE),
    "iron_market_clearing_share": SettingFormat("Iron", full="Iron market clearing share", **SHARE),
    "peg_iron_to_steel_price": SettingFormat("Iron price peg"),
    "iron_to_steel_price_ratio": SettingFormat("Iron price floor", unit="of steel price", **SHARE),
    "initial_scrap_production_cost": SettingFormat(
        "Initial scrap cost", full="Initial scrap production cost", unit="USD/t"
    ),
    "use_iron_ore_premiums": SettingFormat("Iron ore premiums"),
    "disposal_cost_outputs": SettingFormat("Priced as disposal", full="Outputs priced as disposal costs"),
}
# The run details in reading order: the run, its levers, then the model from plants to prices. A group lists its
# settings in rows or takes every key under its prefix; its switch, when off, greys it out.
SETTINGS_LAYOUT: list[dict[str, Any]] = [
    {
        "title": "Overview",
        "tiles": [
            ["Period", ["start_year", "end_year"]],
            ["Seed", ["seed"]],
            ["Steel demand", ["chosen_demand_scenario"]],
            ["Scrap availability", ["chosen_scrap_scenario"]],
            ["Grid emissions", ["chosen_grid_emissions_scenario"]],
        ],
        "groups": [],
    },
    {
        "title": "Policy",
        "groups": [
            {
                "title": "Carbon cost",
                "rows": ["chosen_emissions_boundary_for_carbon_costs", "carbon_border_export_rebates"],
            },
            {"title": "Trade policy", "rows": ["include_tariffs"]},
            {
                "title": "Capacity policy",
                "switch": "capacity_policy.enabled",
                "off": "Not used while the policy is off",
                "wide": True,
                "columns": True,
                "rows": [
                    "capacity_policy.replacement_ratio",
                    "capacity_policy.emission_intense_penalty_divisor",
                    "capacity_policy.min_utilization_for_renovation",
                    "capacity_policy.utilization_window_years",
                    "capacity_policy.inter_company_swap_cutoff_year",
                    "capacity_policy.banked_credit_rule",
                    "capacity_policy.credit_validity_years",
                    "capacity_policy.capacity_pool_max_retry_years",
                    "capacity_policy.renovation_counts_as_replace",
                ],
            },
        ],
    },
    {
        "title": "Technologies",
        "groups": [
            {
                "kind": "technologies",
                "prefix": TECHNOLOGIES,
                "greenfield": "geo_config.excluded_greenfield_technologies",
                "wide": True,
                "rows": [],
            },
        ],
    },
    {
        "title": "Plants",
        "groups": [
            {
                "title": "Lifetime and finance",
                "rows": ["plant_lifetime", "equity_share", "global_risk_free_rate", "opening_balance_multiplier"],
            },
            {"title": "Statuses", "lines": True, "wide": True, "rows": ["active_statuses", "announced_statuses"]},
        ],
    },
    {
        "title": "New capacity",
        "groups": [
            {
                "title": "Additions",
                "rows": [
                    "expanded_capacity",
                    "capacity_limit_iron",
                    "capacity_limit_steel",
                    "new_capacity_share_from_new_plants",
                ],
            },
            {"title": "Timing", "rows": ["consideration_time", "construction_time"]},
            {
                "title": "Probabilistic agents",
                "switch": "probabilistic_agents",
                "off": "Forced to deterministic values while off",
                "rows": [
                    "probability_of_announcement",
                    "probability_of_construction",
                    "geo_config.pick_priority_sites_share",
                    "calculate_npv_sites_share",
                ],
            },
            {
                "title": "Opportunity selection",
                "rows": [
                    "top_n_loctechs_as_business_op",
                    "opportunity_pool_depth",
                    "co2_storage_reserved_discount_factor",
                ],
            },
        ],
    },
    {
        "title": "Site selection",
        "groups": [
            {
                "title": "Feasibility",
                "rows": ["geo_config.max_altitude", "geo_config.max_slope", "geo_config.max_latitude"],
            },
            {
                "title": "Transport cost",
                "switch": "geo_config.include_transport_cost",
                "off": "Not used while off",
                "rows": [
                    "geo_config.transportation_cost_per_km_per_ton.iron_mine_to_plant",
                    "geo_config.transportation_cost_per_km_per_ton.iron_to_steel_plant",
                    "geo_config.transportation_cost_per_km_per_ton.steel_to_demand",
                ],
            },
            {
                "title": "Site cost estimate",
                "rows": [
                    "geo_config.include_infrastructure_cost",
                    "geo_config.iron_ore_steel_ratio",
                    "geo_config.share_iron_vs_steel.iron.capex_share",
                    "geo_config.share_iron_vs_steel.steel.capex_share",
                    "geo_config.share_iron_vs_steel.iron.energy_consumption_per_t",
                    "geo_config.share_iron_vs_steel.steel.energy_consumption_per_t",
                ],
            },
            {
                "title": "Land cover",
                "switch": "geo_config.include_lulc_cost",
                "off": "Not used while off",
                "note": "Multiplies a new plant's CAPEX; 1× adds nothing",
                "kind": "bars",
                "prefix": LAND_COVER,
                "wide": True,
                "rows": [],
            },
        ],
    },
    {
        "title": "Power and hydrogen",
        "groups": [
            {"title": "Power", "rows": ["geo_config.included_power_mix", "boa_run", "geo_data_version"]},
            {
                "title": "Hydrogen",
                "rows": ["geo_config.hydrogen_ceiling_percentile", "geo_config.long_dist_pipeline_transport_cost"],
            },
            {
                "title": "Intraregional hydrogen trade",
                "switch": "geo_config.intraregional_trade_allowed",
                "off": "Not used while off",
                "note": "Each region imports hydrogen from the regions listed against it",
                "kind": "regions",
                "prefix": TRADE_MATRIX,
                "wide": True,
                "rows": [],
            },
        ],
    },
    {
        "title": "Trade",
        "groups": [
            {"title": "Solver", "rows": ["optimization_solver", "lp_epsilon"]},
            {"title": "Capacity use", "rows": ["capacity_limit", "soft_minimum_capacity_share"]},
            {"title": "Clustering", "rows": ["enable_furnace_group_clustering", "geographical_clustering_scope"]},
            {
                "title": "Products",
                "lines": True,
                "wide": True,
                "rows": [
                    "hot_metal_radius",
                    "primary_products",
                    "closely_allocated_products",
                    "distantly_allocated_products",
                ],
            },
        ],
    },
    {
        "title": "Prices and costs",
        "groups": [
            {"title": "Shortage premium", "rows": ["steel_price_buffer", "iron_price_buffer"]},
            {"title": "Market clearing share", "rows": ["steel_market_clearing_share", "iron_market_clearing_share"]},
            {
                "title": "Iron price peg",
                "switch": "peg_iron_to_steel_price",
                "off": "Not used while off",
                "rows": ["iron_to_steel_price_ratio"],
            },
            {
                "title": "Costs",
                "rows": ["initial_scrap_production_cost", "use_iron_ore_premiums", "disposal_cost_outputs"],
            },
        ],
    },
]
# Folders named in a "skipped" warning since they were last listed, so a run in progress is named once, not with
# every page load, and a listed run that loses run.json is named again
WARNED_SKIPPED: set[Path] = set()


def inside_run(run_dir: Path, path: Path) -> bool:
    """Whether a file of a run folder stays inside it once symlinks are resolved.

    Args:
        run_dir: The run folder.
        path: A path under ``run_dir``.

    Returns:
        True when ``path`` resolves under the resolved ``run_dir``.
    """
    return path.resolve().is_relative_to(run_dir.resolve())


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
        Symlinked folders under ``output_base`` (``latest``) are skipped, so a run is listed once, and so is a
        folder whose ``viewer/run.json`` or ``simulation_config.json`` resolves outside it. A folder under
        ``output_base`` with ``simulation_config.json`` but no ``run.json`` (a run in progress, or one whose viewer
        step failed) and a named folder without ``run.json`` are named in one warning, once until the folder is
        listed again, since the server calls this with every set file.
    """
    folders = (
        [] if output_base is None else sorted(p for p in output_base.iterdir() if p.is_dir() and not p.is_symlink())
    )
    candidates = folders + run_dirs
    runs = [
        folder
        for folder in candidates
        if (folder / VIEWER_DIR / RUN_NAME).is_file()
        and inside_run(folder, folder / VIEWER_DIR / RUN_NAME)
        and inside_run(folder, folder / CONFIG_NAME)
    ]
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


def setting_format(key: str) -> SettingFormat:
    """How one counted setting reads in the viewer.

    Args:
        key: The setting's dotted key.

    Returns:
        Its ``SETTING_FORMATS`` entry; for a technology, land cover class or region, whose keys come from the data,
        one built from the entry's name; for a setting not listed, its last field name with underscores as spaces
        ("Capacity policy: <field>" under ``capacity_policy``, "<field>: <entry>" for an entry of a dict setting).
    """
    if key in SETTING_FORMATS:
        return SETTING_FORMATS[key]
    parts = key.split(".")
    if key.startswith(TECHNOLOGIES):
        code, name = parts[1], parts[2]
        if name == "allowed":
            return SettingFormat(f"{code} allowed", yes_no=True)
        if name == "from_year":
            return SettingFormat(f"{code} available from")
        if name == "to_year":
            return SettingFormat(f"{code} available until", none="open")
        return SettingFormat(f"{code} {name.replace('_', ' ')}")
    if key.startswith(LAND_COVER):
        name = key.removeprefix(LAND_COVER)
        return SettingFormat(name, full=f"Land cover factor, {name}", template="{}×")
    if key.startswith(TRADE_MATRIX):
        region = key.removeprefix(TRADE_MATRIX)
        return SettingFormat(region, full=f"{region} imports hydrogen from")
    if parts[0] == "capacity_policy":
        return SettingFormat(f"Capacity policy: {parts[1].replace('_', ' ')}")
    if parts[0] == "geo_config":
        parts = parts[1:]
    label = parts[0].replace("_", " ")
    label = label[:1].upper() + label[1:]
    return SettingFormat(f"{label}: {' '.join(parts[1:]).replace('_', ' ')}" if len(parts) > 1 else label)


def ordinal(number: float) -> str:
    """A number as an ordinal.

    Args:
        number: A whole or decimal number.

    Returns:
        "1st", "2nd", "3rd", "11th", "20th"; "th" after a decimal ("37.5th").
    """
    text = format(number, ".15g")
    if not text.isdigit():
        return f"{text}th"
    whole = int(text)
    return text + ("th" if whole % 100 in (11, 12, 13) else {1: "st", 2: "nd", 3: "rd"}.get(whole % 10, "th"))


def format_value(key: str, value: Any) -> str:
    """One setting's value as the viewer shows it, in filters and in the run details.

    Args:
        key: The setting's dotted key.
        value: Its value from the config file.

    Returns:
        For None the format's ``none`` text ("none", "open", "never expires"); "none" for an empty list; "on" or
        "off" for booleans, "yes" or "no" where the format says so; a list joined with ", "; a number scaled, put in
        its template and followed by its unit ("20%", "2.5 Mt", "200 USD/t"); the hydrogen ceiling as "no cap" at
        100 and as a percentile below ("20th percentile"); anything else as ``str``.

    Notes:
        Fifteen significant digits keep every digit a setting is given with, so two runs differing in a late digit
        still differ here, while 100.0 still reads "100".
    """
    spec = setting_format(key)
    if value is None:
        return spec.none
    if isinstance(value, bool):
        if spec.yes_no:
            return "yes" if value else "no"
        return "on" if value else "off"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) if value else "none"
    if not isinstance(value, (int, float)):
        return str(value)
    # GeoConfig: a ceiling at the 100th percentile caps nothing
    if key == "geo_config.hydrogen_ceiling_percentile":
        return "no cap" if value >= 100 else f"{ordinal(value)} percentile"
    number = str(value) if isinstance(value, int) and spec.scale == 1 else format(value * spec.scale, ".15g")
    text = spec.template.format(number)
    return f"{text} {spec.unit}" if spec.unit else text


def label_for(key: str) -> str:
    """A setting's label standing alone: in the filters and the run details' lists.

    Args:
        key: The setting's dotted key.

    Returns:
        The full label of its :func:`setting_format`, or its label when it has no full one.
    """
    spec = setting_format(key)
    return spec.full or spec.label


def settings_layout() -> list[dict[str, Any]]:
    """The run details' sections as the manifest carries them.

    Returns:
        ``SETTINGS_LAYOUT`` with each group's ``rows`` as ``[key, label, unit]``, the label as the group shows it.
    """
    return [
        {
            **section,
            "groups": [
                {**group, "rows": [[key, setting_format(key).label, setting_format(key).unit] for key in group["rows"]]}
                for group in section["groups"]
            ],
        }
        for section in SETTINGS_LAYOUT
    ]


def default_settings(config: dict[str, Any]) -> dict[str, Any]:
    """The defaults one run's counted settings are compared with, flattened as :func:`flatten_settings` does.

    Args:
        config: The run's parsed ``simulation_config.json``.

    Returns:
        ``{dotted key: default}`` from the ``SimulationConfig`` field defaults, ``GeoConfig()`` and
        ``CapacityPolicyConfig()``, plus the technology settings of ``<data_dir>/fixtures/technologies.json``, which
        both entry points start from, when that file exists.

    Notes:
        A field without a default, ``geo_data_version`` (a record of the prepared data, not a choice) and, without the
        technologies file, every technology setting are left out, so their changed-from-default flag is unknown rather
        than compared with another default.
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
    del defaults["geo_data_version"]
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


def input_data_labels(runs: list[dict[str, Any]]) -> list[str]:
    """The input data each run used, as its filter value, one value per master workbook's content.

    Args:
        runs: The runs' ``viewer/run.json`` files, oldest first.

    Returns:
        One label per run, in order. The runs recording one ``master_sha256`` share one label: the master file name
        of the first CLI run among them, otherwise the first app run's master name. Two hashes with one label each
        get their first eight characters appended. A run without ``master_sha256`` gets :func:`input_data_label`.

    Notes:
        A grouped label drops "(default)" and "(custom)", since one workbook can be both. App preparations of one
        master on different data-package versions therefore share one value. Runs written before ``master_sha256``
        keep the old label until ``steelo-viewer regenerate`` rewrites them; the standalone app has no
        ``regenerate``, so its older runs keep theirs. A run recording no ``made_in`` counts as a CLI run, as in
        :func:`input_data_label`.
    """
    hashes = [run["input_data"].get("master_sha256") for run in runs]
    groups: dict[str, list[dict[str, Any]]] = {}
    for run, sha256 in zip(runs, hashes):
        if sha256 is not None:
            groups.setdefault(sha256, []).append(run)
    names = {}
    for sha256, group in groups.items():
        cli = [run for run in group if run["made_in"] != "app"]
        names[sha256] = cli[0]["input_data"]["master"] if cli else group[0]["input_data"]["master_name"]
    labels = {
        sha256: f"{name} {sha256[:8]}" if list(names.values()).count(name) > 1 else name
        for sha256, name in names.items()
    }
    return [input_data_label(run) if sha256 is None else labels[sha256] for run, sha256 in zip(runs, hashes)]


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
        The values in numeric order when every one starts with a number ("2.25 Mt" before "10 Mt"), else in natural
        order.
    """
    matches = [match for value in values if (match := re.match(r"-?\d+(?:\.\d+)?(?:e[-+]?\d+)?", value))]
    if len(matches) == len(values):
        return [match.string for match in sorted(matches, key=lambda match: (float(match.group()), match.string))]
    return sorted(values, key=natural_key)


def setting_entry(key: str, value: Any, defaults: dict[str, Any], varies: bool) -> dict[str, Any]:
    """One counted setting of a run as the run details show it.

    Args:
        key: The setting's dotted key.
        value: Its value from the run's config file.
        defaults: The run's :func:`default_settings`.
        varies: Whether the setting differs between the listed runs.

    Returns:
        ``key``, ``label`` (:func:`label_for`), ``value`` (:func:`format_value`), ``changed`` (null when the default
        is unknown) and ``varies``; plus the formatted ``default`` when changed, the ``items`` of a list and the
        ``bool`` of a switch.
    """
    text = format_value(key, value)
    default = format_value(key, defaults[key]) if key in defaults else None
    entry: dict[str, Any] = {
        "key": key,
        "label": label_for(key),
        "value": text,
        "changed": None if default is None else text != default,
        "varies": varies,
    }
    if entry["changed"]:
        entry["default"] = default
    if isinstance(value, list):
        entry["items"] = [str(item) for item in value]
    if isinstance(value, bool):
        entry["bool"] = value
    return entry


def build_manifest(run_dirs: list[Path], with_made_in: bool) -> dict[str, Any]:
    """The viewer's ``manifest.json`` for a list of runs: one set, "Local runs".

    Args:
        run_dirs: The run folders, each holding ``viewer/run.json`` and ``simulation_config.json``.
        with_made_in: Whether to add the "Made in" filter, for a catalogue holding both app and CLI runs.

    Returns:
        ``{"title", "sets": [set]}``, the set holding ``dimensions`` (made in when asked, input data and the
        counted settings when they differ between runs, seed always, run on a collision), ``plots``, ``runs``
        oldest first and the run details' ``settings_layout``. Each run carries ``dir``, ``name``, ``made_in``,
        ``started``, ``input_data``, ``link``, ``dims``, ``plots`` and ``settings``: every counted setting as
        :func:`setting_entry` gives it.

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
        loaded.append((run_dir, read_run(run_dir), flatten_settings(config), default_settings(config)))
    loaded.sort(key=lambda item: item[1]["started"])

    setting_keys = list(dict.fromkeys(key for _, _, settings, _ in loaded for key in settings))
    # A setting one run's config lacks (another master's technologies, an older run) reads "none" for it
    values = [
        {key: format_value(key, settings[key]) if key in settings else "none" for key in setting_keys}
        for _, _, settings, _ in loaded
    ]
    input_data = input_data_labels([run for _, run, _, _ in loaded])
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
            "settings": [setting_entry(key, value, defaults, key in varying) for key, value in settings.items()],
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
                "settings_layout": settings_layout(),
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
        ValueError: If a run's ``inputs.json`` resolves outside its run folder or does not parse (naming the
            file), or a topic's runs do not share the same value columns.
    """
    by_folder = {run_dir.name: run_dir for run_dir in run_dirs}
    local = manifest["sets"][0]
    runs = [run for run in local["runs"] if run["dir"] in by_folder]
    per_run = []
    for run in runs:
        path = by_folder[run["dir"]] / VIEWER_DIR / INPUTS_NAME
        if not inside_run(by_folder[run["dir"]], path):
            raise ValueError(f"{path}: resolves outside its run folder")
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
        ``<run>/viewer/geo.json`` of the run with the latest ``started`` among those holding the file inside the
        run folder, symlinks resolved, or None when no run holds one.

    Notes:
        Runs on different masters can differ here only in names and groupings.
    """
    for run_dir in sorted(run_dirs, key=lambda run_dir: read_run(run_dir)["started"], reverse=True):
        path = run_dir / VIEWER_DIR / GEO_NAME
        if path.is_file() and inside_run(run_dir, path):
            return path
    return None
