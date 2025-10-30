"""Load technologies from prepared data."""

import json
from pathlib import Path
from typing import Dict, Set


class TechnologyRepository:
    """Load technologies from prepared data."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir

    def load_technologies(self) -> Dict[str, dict]:
        """Load technology definitions from technologies.json."""
        tech_path = self.data_dir / "fixtures" / "technologies.json"
        if not tech_path.exists():
            raise FileNotFoundError(f"No technologies.json at {tech_path}")

        with open(tech_path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        # Validate schema version
        schema_version = raw.get("schema_version")
        if schema_version not in (2, 3):
            raise ValueError(
                f"Unsupported technologies.json schema_version={schema_version}; "
                "rerun data preparation to upgrade to v2 or v3."
            )

        # Extract technologies dict
        techs = raw.get("technologies", {})
        if not isinstance(techs, dict):
            raise ValueError("Invalid technologies.json: 'technologies' must be an object")
        return techs  # mapping: slug -> tech dict

    def get_normalized_codes(self) -> Set[str]:
        """Get set of normalized technology codes with collision detection."""
        techs = self.load_technologies()
        codes = [t["normalized_code"] for t in techs.values()]

        # Detect collisions
        dupes = {c for c in codes if codes.count(c) > 1}
        if dupes:
            raise ValueError(f"Duplicate normalized_code(s) after normalization: {', '.join(sorted(dupes))}")

        return set(codes)
