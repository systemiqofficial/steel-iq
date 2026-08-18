"""Interactive decision-flow Sankey built from a run's global PAM motions.

Turns ``<output>/data/pam_motions.csv`` (written by :mod:`steelo.motions` on
every run) into a single self-contained HTML file: plotly.js and the motion
rows are embedded, and the chart is assembled client-side by the JavaScript in
``decision_flows_template.html``. Column 1 is each furnace group's state when
it first acts — its technology, or NEW for capacity that does not exist yet —
and every further column is one decision round, grouped into bands (Renovate,
Switch, Retire, Pipeline, Expand, Greenfield) with technology sub-nodes.
Link width is the capacity entering the decision, in Mt. The viewer offers a
country filter with a separate dropdown for sub-national geo_keys.

Pipeline motions are capacity that opens during the simulation but was decided
by input data rather than the PAM; like greenfield and expansion they enter
from the NEW state node, into their own band.
"""

import json
import logging
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from matplotlib.colors import to_hex
from plotly.offline import get_plotlyjs

from steelo.utilities.plotting import tech2colours

logger = logging.getLogger(__name__)

TEMPLATE_PATH = Path(__file__).with_name("decision_flows_template.html")

KIND_TO_GROUP = {
    "renovate": "Renovate",
    "switch": "Switch",
    "close": "Retire",
    "pipeline": "Pipeline",
    "expansion": "Expand",
    "greenfield": "Greenfield",
}
GROUP_ORDER = ["State", "Renovate", "Switch", "Retire", "Pipeline", "Expand", "Greenfield"]
TECH_ORDER = ["BF", "BF_CHARCOAL", "BF_CHARCOAL+CCS", "BOF", "EAF", "DRI", "SR", "NEW"]

# Kinds that create capacity that did not exist before the motion: they enter
# from the NEW state node and carry only new_* fields.
NEW_BUILD_KINDS = {"greenfield", "expansion", "pipeline"}

NEW_COLOUR = "#979590"


def _tech_colours() -> dict[str, str]:
    """House technology colours as hex, plus NEW for not-yet-existing capacity."""
    colours = {tech: to_hex(colour) for tech, colour in tech2colours.items()}
    colours["NEW"] = NEW_COLOUR
    return colours


def pack_motions(motions: pd.DataFrame) -> list[dict[str, Any]]:
    """Compact motion rows for embedding in the viewer.

    Args:
        motions: The run's motions table (the pam_motions.csv columns).

    Returns:
        One short parallel-keyed record per motion; capacities in Mt to three
        decimals to keep the embedded payload small.

    Raises:
        ValueError: If the table contains a motion kind this module does not
            know how to band.
    """
    unknown = set(motions["kind"]) - set(KIND_TO_GROUP)
    if unknown:
        raise ValueError(f"Unrecognised motion kinds {sorted(unknown)} — teach decision_flows about them")

    def mt(value: Any) -> Optional[float]:
        return None if pd.isna(value) else round(float(value) / 1e6, 3)

    return [
        {
            "fg": row["furnace_group_id"],
            "year": int(row["year"]),
            "kind": row["kind"],
            "ot": None if pd.isna(row["old_technology"]) else row["old_technology"],
            "nt": None if pd.isna(row["new_technology"]) else row["new_technology"],
            "om": mt(row["old_capacity_t"]),
            "nm": mt(row["new_capacity_t"]),
            "geo": row["geo_key"],
        }
        for row in motions.to_dict("records")
    ]


def write_decision_flows_html(motions_csv: Path, output_path: Path, run_title: str) -> Optional[Path]:
    """Write the decision-flow Sankey viewer for one run.

    Args:
        motions_csv: The run's global motions file (``data/pam_motions.csv``).
        output_path: Where to write the HTML (parents are created).
        run_title: Run name shown in the chart title (e.g. the output dir name).

    Returns:
        The written path, or None when the motions file does not exist. A file
        with zero motions still produces a viewer (it shows an empty-state
        note), so an unexpectedly quiet run stays visible rather than silent.
    """
    if not motions_csv.is_file():
        logger.warning("No motions file at %s — skipping the decision-flow sankey", motions_csv)
        return None
    motions = pd.read_csv(motions_csv)
    data = {
        run_title: {
            "title": run_title,
            "provenance": "Motions from the PAM motion recorder (data/pam_motions.csv).",
            "motions": pack_motions(motions),
        },
    }
    config = {
        "techColours": _tech_colours(),
        "groupOrder": GROUP_ORDER,
        "techOrder": TECH_ORDER,
        "kindToGroup": KIND_TO_GROUP,
        "newBuildKinds": sorted(NEW_BUILD_KINDS),
        "runs": [run_title],
        "defaultRun": run_title,
        "chartTitle": "Furnace-group decision flows",
        "geoLabel": "Geography (geo_key)",
        "geoNoun": "countries",
        "countryNoun": "countries",
        "unitNoun": "geo units",
        "geoStrip": "",
    }
    html = (
        TEMPLATE_PATH.read_text()
        .replace("__PLOTLYJS__", get_plotlyjs())
        .replace("__CONFIG__", json.dumps(config))
        .replace("__DATA__", json.dumps(data))
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html)
    logger.info("Wrote decision-flow sankey %s (%d motions)", output_path, len(motions))
    return output_path
