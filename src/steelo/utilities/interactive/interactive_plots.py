"""Self-contained interactive plotly viewers written to ``<plots>/interactive/``.

Each viewer is one HTML file with plotly.js, the shared toolbar shell
(``common.js`` / ``common.css``) and the chart's data inlined, so it opens
anywhere without a server. :class:`InteractivePlotter` mirrors
:class:`~steelo.utilities.steeliq_plotter.SteelPlotter`: one instance per run,
one method per chart. A chart is a template (its own controls plus a
``render()``) and a packing module that turns a run output file into compact
rows; the shell gives every chart the same run selector and geography filter
(countries, sub-national geo units, trade blocs, regions) and an optional
technology filter, all driven by the country mappings passed in here.
"""

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from matplotlib.colors import to_hex
from plotly.offline import get_plotlyjs

from steelo.domain.models import CountryMapping
from steelo.utilities.plotting import region2colours, tech2colours


logger = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).parent


def trade_bloc_members(country_mappings: list[CountryMapping]) -> dict[str, list[str]]:
    """Trade blocs and their member ISO3 codes, from the boolean membership attributes.

    Args:
        country_mappings: The run's country mappings (the Country mapping sheet).

    Returns:
        ``{bloc_name: [iso3, ...]}`` for every bloc with at least one member, both sorted.
        Any boolean attribute on the mapping counts as a bloc, so blocs added as new
        sheet columns appear without changes here.
    """
    members: dict[str, list[str]] = defaultdict(list)
    for mapping in country_mappings:
        for name, value in vars(mapping).items():
            if isinstance(value, bool) and value:
                members[name].append(mapping.iso3)
    return {bloc: sorted(iso3s) for bloc, iso3s in sorted(members.items())}


def geo_info(country_mappings: list[CountryMapping]) -> dict[str, dict[str, str]]:
    """Country name and output region per ISO3, for labels and the region filter/grouping.

    Args:
        country_mappings: The run's country mappings.

    Returns:
        ``{iso3: {"country": name, "region": region_for_outputs}}``.
    """
    return {m.iso3: {"country": m.country, "region": m.region_for_outputs} for m in country_mappings}


def geo_unit_names(geo_hierarchy_json: Optional[Path]) -> dict[str, str]:
    """Display names of the sub-national units, keyed by geo_key (``CHN:CN-HE`` → ``Hebei``).

    Args:
        geo_hierarchy_json: The prepared ``geo_hierarchy.json``, or None.

    Returns:
        ``{geo_key: display_name}``; empty when no file is given or it does not exist
        (older geo-data packages prepare no hierarchy), so the viewers fall back to codes.
    """
    if geo_hierarchy_json is None or not geo_hierarchy_json.is_file():
        return {}
    return {row["geo_key"]: row["display_name"] for row in json.loads(geo_hierarchy_json.read_text())}


def _hex(colours: dict[str, str]) -> dict[str, str]:
    return {name: to_hex(colour) for name, colour in colours.items()}


class InteractivePlotter:
    """Writes the interactive viewers of one run.

    Example:
        >>> interactive = InteractivePlotter(plots_dir, country_mappings, run_title="sim_2026")
        >>> interactive.plot_emissions(post_processed_csv)
    """

    SUBDIR = "interactive"

    def __init__(
        self,
        plots_dir: Path,
        country_mappings: list[CountryMapping],
        run_title: str,
        geo_hierarchy_json: Optional[Path] = None,
    ) -> None:
        """
        Args:
            plots_dir: The run's plots directory; viewers go to its ``interactive`` subfolder.
            country_mappings: The run's country mappings (trade blocs, regions, country names).
            run_title: Run name shown in chart titles (e.g. the output dir name).
            geo_hierarchy_json: The prepared ``geo_hierarchy.json`` for sub-national unit names;
                None (or a missing file) leaves the units labelled by code.
        """
        self.output_dir = plots_dir / self.SUBDIR
        self.country_mappings = country_mappings
        self.run_title = run_title
        self.geo_unit_names = geo_unit_names(geo_hierarchy_json)

    def _config(self, chart_title: str, **chart_config: Any) -> dict[str, Any]:
        """The shell's config (colours, geography, runs) plus chart-specific keys."""
        return {
            "chartTitle": chart_title,
            "runs": [self.run_title],
            "defaultRun": self.run_title,
            "techColours": _hex(tech2colours),
            "regionColours": _hex(region2colours),
            "tradeBlocs": trade_bloc_members(self.country_mappings),
            "geoInfo": geo_info(self.country_mappings),
            "geoUnitNames": self.geo_unit_names,
            **chart_config,
        }

    def _write(self, template_name: str, config: dict[str, Any], data: dict[str, Any]) -> Path:
        """Inline plotly.js, the shared shell and the payload into the template; write it under the output dir."""
        html = (
            (ASSETS_DIR / template_name)
            .read_text()
            .replace("__COMMON_CSS__", (ASSETS_DIR / "common.css").read_text())
            .replace("__COMMON_JS__", (ASSETS_DIR / "common.js").read_text())
            .replace("__PLOTLYJS__", get_plotlyjs())
            .replace("__CONFIG__", json.dumps(config))
            .replace("__DATA__", json.dumps(data))
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / template_name
        path.write_text(html)
        return path
