import csv
from steelo.domain.models import CommodityAllocations
import logging


def export_commodity_allocations_to_csv(
    commodity_allocations_dict: dict[str, CommodityAllocations], year: int, filename: str
) -> None:
    """
    Exports all allocation information from a CommodityAllocations instance to a CSV file.
    The CSV includes the commodity, source/destination types, IDs, locations, demand at destination,
    and supply at source. Adjust the details within `extract_source_info` and `extract_destination_info`
    to match your exact data structures.
    """

    # Define your CSV columns
    fieldnames = [
        "commodity",
        "source_type",
        "source_id",
        "source_location",
        "capacity_at_source",
        "source_tech",
        "destination_type",
        "destination_id",
        "destination_location",
        "allocated_volume",
        "allocation_cost",
        "demand_at_destination",
        "supply_at_source",
    ]

    # Ensure the directory exists
    import os

    os.makedirs(os.path.dirname(filename), exist_ok=True)

    with open(filename, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for comm, commodity_allocations in commodity_allocations_dict.items():
            logging.info(f"Exporting commodity: {comm}")
            # Loop over all allocations in the CommodityAllocations object
            for source, destination_dict in commodity_allocations.allocations.items():
                # Extract details about the source
                source_type, source_id, source_location, supply_at_source, capacity_at_source, source_tech = (
                    extract_source_info(source, year)
                )

                # For each destination and volume, extract details and write a row
                for destination, volume in destination_dict.items():
                    dest_type, dest_id, dest_location, demand_at_destination = extract_destination_info(
                        destination, year
                    )

                    # Convert volume (Volumes class) to float or string as needed
                    allocated_volume = float(volume)  # or adapt if your Volumes class needs a different approach

                    row = {
                        "commodity": comm,
                        "source_type": source_type,
                        "source_id": source_id,
                        "source_location": str(source_location),
                        "capacity_at_source": capacity_at_source,
                        "source_tech": source_tech,
                        "destination_type": dest_type,
                        "destination_id": dest_id,
                        "destination_location": str(dest_location),
                        "allocated_volume": allocated_volume,
                        "allocation_cost": commodity_allocations.get_cost(source, destination),
                        "demand_at_destination": demand_at_destination,
                        "supply_at_source": supply_at_source,
                    }
                    writer.writerow(row)


def extract_source_info(source, year):
    """
    Helper function to determine source type, ID, location, supply, capacity, and tech based on whether the source is:
      - a Supplier
      - a (Plant, FurnaceGroup) tuple
    Adjust this to match the attributes in your actual classes.
    """
    source_type = "Unknown"
    source_id = "N/A"
    source_location = "N/A"
    supply_at_source = "N/A"
    capacity_at_source = "N/A"
    source_tech = "N/A"

    # If it's a Supplier object
    if hasattr(source, "supplier_id"):
        source_type = "Supplier"
        source_id = source.supplier_id
        source_location = source.location
        # Example: sum capacity across all years, or store as a string
        supply_at_source = source.capacity_by_year[year]
        capacity_at_source = "N/A"  # Suppliers don't have furnace group capacity
        source_tech = "N/A"  # Suppliers don't have furnace group tech

    # If it's a (Plant, FurnaceGroup) tuple
    elif isinstance(source, tuple) and len(source) == 2:
        plant, furnace_group = source
        source_type = "Plant-FurnaceGroup"
        source_id = furnace_group.furnace_group_id
        # You might store location on the plant (or furnace group)
        source_location = getattr(plant, "location", "N/A")
        supply_at_source = "N/A"  # or any logic you need to get supply from the plant/furnace
        # Add furnace group capacity and technology
        capacity_at_source = float(furnace_group.capacity) if hasattr(furnace_group, "capacity") else "N/A"
        source_tech = furnace_group.technology.name if hasattr(furnace_group, "technology") else "N/A"

    return source_type, source_id, source_location, supply_at_source, capacity_at_source, source_tech


def extract_destination_info(destination, year):
    """
    Helper function to determine destination type, ID, location, and demand based on whether the destination is:
      - a DemandCenter
      - a (Plant, FurnaceGroup) tuple
    Adjust this to match your actual data structures.
    """
    dest_type = "Unknown"
    dest_id = "N/A"
    dest_location = "N/A"
    demand_at_destination = "N/A"

    # If it's a DemandCenter object
    if hasattr(destination, "demand_center_id"):
        dest_type = "DemandCenter"
        dest_id = destination.demand_center_id
        dest_location = destination.center_of_gravity
        # Example: sum demand across all years
        demand_at_destination = destination.demand_by_year[year]

    # If it's a (Plant, FurnaceGroup) tuple
    elif isinstance(destination, tuple) and len(destination) == 2:
        plant, furnace_group = destination
        dest_type = "Plant-FurnaceGroup"
        dest_id = furnace_group.furnace_group_id
        dest_location = getattr(plant, "location", "N/A")
        demand_at_destination = "N/A"  # or any logic you need to get demand from the plant/furnace

    return dest_type, dest_id, dest_location, demand_at_destination
