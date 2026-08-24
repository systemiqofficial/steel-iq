def convert_resolution_to_string(res):
    """
    Convert resolution to string and remove the dot; e.g., 0.25 -> 025
    """
    return str(res).replace(".", "")


def coverage_to_percentile(coverage: float) -> int:
    """
    Convert a coverage fraction to BOA's percentile p (% of hours not covered);
    e.g., 0.95 -> 5. Must round, not truncate: (1 - 0.9) * 100 is 9.999...
    in float, which int() would silently turn into p9.
    """
    return round((1 - coverage) * 100)
