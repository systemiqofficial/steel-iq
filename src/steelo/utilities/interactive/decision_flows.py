"""Row packing for the decision-flow Sankey viewer (``decision_flows.html``).

Turns ``<output>/data/pam_motions.csv`` (written by :mod:`steelo.motions` on
every run) into compact motion records. In the viewer, column 1 is each
furnace group's state when it first acts — its technology, or NEW for capacity
that does not exist yet — and every further column is one decision round,
grouped into bands (Renovate, Switch, Retire, Pipeline, Expand, Greenfield)
with technology sub-nodes. Link width is the capacity entering the decision,
in Mt.

Pipeline motions are capacity that opens during the simulation but was decided
by input data rather than the PAM; like greenfield and expansion they enter
from the NEW state node, into their own band.
"""

from typing import Any, Optional

import pandas as pd

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

CHART_CONFIG: dict[str, Any] = {
    "groupOrder": GROUP_ORDER,
    "techOrder": TECH_ORDER,
    "kindToGroup": KIND_TO_GROUP,
    "newBuildKinds": sorted(NEW_BUILD_KINDS),
    "newColour": NEW_COLOUR,
}


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
