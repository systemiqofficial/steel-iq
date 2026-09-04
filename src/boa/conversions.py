def convert_resolution_to_string(res):
    """
    Convert resolution to string and remove the dot; e.g., 0.25 -> 025
    """
    return str(res).replace(".", "")
