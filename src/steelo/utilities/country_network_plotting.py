"""
Country-level network visualization for commodity allocations.

This module provides functions to create interactive network plots showing
commodity flows between countries, with nodes sized by capacity/demand and
edges representing trade volumes.
"""

import logging
import math
from collections import defaultdict
from typing import Dict, Any, Tuple, Optional
import plotly.graph_objects as go
import networkx as nx
from pathlib import Path
import pycountry

try:
    import pycountry_convert as pc
except ImportError:
    pc = None

from steelo.domain.models import CommodityAllocations, Year

try:
    import json
    from pathlib import Path as PathlibPath

    def load_country_mappings_from_file():
        """Load country mappings from the fixtures file if available."""
        try:
            # Try to find the fixtures file relative to this module
            current_dir = PathlibPath(__file__).parent
            fixtures_path = current_dir.parent.parent.parent / "data" / "fixtures" / "country_mappings.json"

            if fixtures_path.exists():
                with open(fixtures_path) as f:
                    data = json.load(f)

                # Create a simple mapping dict for ISO3 -> region_for_outputs
                return {item["ISO 3-letter code"]: item["region_for_outputs"] for item in data}
        except Exception:
            pass
        return {}
except ImportError:

    def load_country_mappings_from_file():
        return {}


logger = logging.getLogger(__name__)


def iso3_to_country_name(iso3: str) -> str:
    """Convert ISO3 country code to country name.

    Args:
        iso3: ISO3 country code (e.g., 'USA', 'CHN')

    Returns:
        Country name (e.g., 'United States', 'China')
        Returns the ISO3 code if country not found.
    """
    try:
        country = pycountry.countries.get(alpha_3=iso3)
        if country:
            # Use common name if available, otherwise official name
            return country.common_name if hasattr(country, "common_name") else country.name
        return iso3
    except Exception:
        return iso3


def get_region_color_palette():
    """Get a professional color palette for regions.

    Returns a dictionary mapping region names to colors.
    Using a professional palette with good contrast.
    """
    # Professional color palette suitable for data visualization
    return {
        "Europe": "#1f77b4",  # Blue
        "North America": "#ff7f0e",  # Orange
        "East Asia": "#2ca02c",  # Green
        "Latin America": "#d62728",  # Red
        "Subsaharan Africa": "#9467bd",  # Purple
        "Southeast Asia": "#8c564b",  # Brown
        "Other Asia": "#e377c2",  # Pink
        "Middle East": "#7f7f7f",  # Gray
        "Oceania": "#bcbd22",  # Olive
        "North Africa": "#17becf",  # Cyan
        "CIS": "#aec7e8",  # Light blue
        "Other": "#c5b0d5",  # Light purple
    }


def calculate_layout(G: nx.Graph, algorithm: str = "kamada_kawai", **kwargs) -> Dict[str, Tuple[float, float]]:
    """
    Calculate node positions using different layout algorithms.

    Args:
        G: NetworkX graph
        algorithm: Layout algorithm to use
        **kwargs: Additional parameters for layout algorithms

    Returns:
        Dictionary mapping node names to (x, y) positions
    """
    if len(G.nodes) == 0:
        return {}

    # Set default parameters based on algorithm
    seed = kwargs.get("seed", 42)

    try:
        if algorithm == "kamada_kawai":
            # High-quality layout that minimizes edge crossings
            return nx.kamada_kawai_layout(G, pos=None)

        elif algorithm == "spring_layered":
            # Multi-level spring layout with better separation
            return nx.spring_layout(G, k=4, iterations=100, seed=seed)

        elif algorithm == "circular_weighted":
            # Circular layout with weighted positioning
            # Place high-degree nodes on outer circle, low-degree on inner
            pos = nx.circular_layout(G, scale=2)
            # Adjust radial position based on node degree/weight
            for node in pos:
                degree = G.degree(node, weight="weight") if G.is_directed() else G.degree(node)
                max_degree = max([G.degree(n, weight="weight") if G.is_directed() else G.degree(n) for n in G.nodes])
                if max_degree > 0:
                    # Scale radius based on degree (0.5 to 2.0)
                    radius_scale = 0.5 + 1.5 * (degree / max_degree)
                    pos[node] = (pos[node][0] * radius_scale, pos[node][1] * radius_scale)
            return pos

        elif algorithm == "shell":
            # Shell layout with nodes in concentric circles
            # Group nodes by degree for better organization
            degrees = [
                (node, G.degree(node, weight="weight") if G.is_directed() else G.degree(node)) for node in G.nodes
            ]
            degrees.sort(key=lambda x: x[1], reverse=True)

            # Create shells based on degree quartiles
            n_nodes = len(degrees)
            shell_size = max(1, n_nodes // 3)
            shells = [
                [node for node, _ in degrees[:shell_size]],  # High degree
                [node for node, _ in degrees[shell_size : 2 * shell_size]],  # Medium degree
                [node for node, _ in degrees[2 * shell_size :]],  # Low degree
            ]
            # Remove empty shells
            shells = [shell for shell in shells if shell]
            return nx.shell_layout(G, nlist=shells)

        elif algorithm == "force_atlas":
            # Custom force-directed with stronger repulsion
            return nx.spring_layout(G, k=5, iterations=200, seed=seed, pos=nx.random_layout(G, seed=seed))

        elif algorithm == "hierarchical":
            # Try to create a hierarchical layout based on trade flows
            if G.is_directed():
                # Calculate in/out degree ratio to determine hierarchy
                hierarchy_scores = {}
                for node in G.nodes:
                    in_deg = G.in_degree(node, weight="weight")
                    out_deg = G.out_degree(node, weight="weight")
                    total_deg = in_deg + out_deg
                    if total_deg > 0:
                        # Net exporters get higher y-values
                        hierarchy_scores[node] = (out_deg - in_deg) / total_deg
                    else:
                        hierarchy_scores[node] = 0

                # Use spring layout but bias y-coordinates by hierarchy
                pos = nx.spring_layout(G, k=3, iterations=100, seed=seed)

                # Adjust y-coordinates based on hierarchy scores
                if hierarchy_scores:
                    min_score = min(hierarchy_scores.values())
                    max_score = max(hierarchy_scores.values())
                    score_range = max_score - min_score

                    if score_range > 0:
                        for node in pos:
                            normalized_score = (hierarchy_scores[node] - min_score) / score_range
                            # Scale y from -2 to 2 based on hierarchy
                            pos[node] = (pos[node][0], -2 + 4 * normalized_score)

                return pos
            else:
                # Fall back to spring layout for undirected graphs
                return nx.spring_layout(G, k=3, iterations=100, seed=seed)

        elif algorithm == "geographic":
            # Use actual geographic coordinates from the allocation data
            return extract_geographic_layout(G, **kwargs)

        elif algorithm == "geographic_spring":
            # Start with geographic positions, then apply gentle spring forces
            geo_pos = extract_geographic_layout(G, **kwargs)
            if geo_pos:
                # Apply light spring layout using geographic positions as initial positions
                return nx.spring_layout(G, pos=geo_pos, k=1, iterations=30, seed=seed)
            else:
                # Fallback to regular spring if no geographic data
                return nx.spring_layout(G, k=3, iterations=100, seed=seed)

        elif algorithm == "geographic_clustered":
            # Geographic layout with continental/regional clustering
            geo_pos = extract_geographic_layout(G, **kwargs)
            if geo_pos:
                return cluster_geographic_layout(geo_pos, G)
            else:
                # Fallback to shell layout
                return calculate_layout(G, "shell", seed=seed)

        else:
            # Default: original spring layout
            return nx.spring_layout(G, k=3, iterations=50, weight="weight", seed=seed)

    except Exception as e:
        logger.warning(f"Layout algorithm '{algorithm}' failed: {e}. Falling back to spring layout.")
        return nx.spring_layout(G, k=3, iterations=50, seed=seed)


def extract_geographic_layout(G: nx.Graph, **kwargs) -> Dict[str, Tuple[float, float]]:
    """
    Extract geographic coordinates from the allocation data and create layout positions.

    This function attempts to extract latitude/longitude coordinates from the
    country allocation data by examining the source locations.
    """
    # Get allocations data from kwargs if passed
    allocations_by_commodity = kwargs.get("allocations_by_commodity", {})

    # Dictionary to store country coordinates
    country_coords: Dict[str, list[Tuple[float, float]]] = {}

    # Try to extract coordinates from allocation data
    if allocations_by_commodity:
        for commodity_name, commodity_allocations in allocations_by_commodity.items():
            if hasattr(commodity_allocations, "allocations"):
                for source, destinations in commodity_allocations.allocations.items():
                    # Extract coordinates from source locations
                    source_iso3 = None
                    lat, lon = None, None

                    if hasattr(source, "location"):  # Supplier
                        source_iso3 = source.location.iso3  # type: ignore[union-attr]
                        lat, lon = source.location.lat, source.location.lon
                    elif isinstance(source, tuple) and len(source) == 2:  # (Plant, FurnaceGroup)
                        plant, fg = source
                        if hasattr(plant, "location"):
                            source_iso3 = plant.location.iso3
                            lat, lon = plant.location.lat, plant.location.lon

                    if source_iso3 and lat is not None and lon is not None:
                        # Store average coordinates for each country
                        if source_iso3 not in country_coords:
                            country_coords[source_iso3] = []
                        country_coords[source_iso3].append((lat, lon))

                    # Also extract from destinations
                    for dest, volume in destinations.items():
                        if volume <= 0:
                            continue

                        dest_iso3 = None
                        lat, lon = None, None

                        if hasattr(dest, "center_of_gravity"):  # DemandCenter
                            dest_iso3 = dest.center_of_gravity.iso3  # type: ignore[union-attr]
                            lat, lon = dest.center_of_gravity.lat, dest.center_of_gravity.lon
                        elif isinstance(dest, tuple) and len(dest) == 2:  # (Plant, FurnaceGroup)
                            plant, fg = dest
                            if hasattr(plant, "location"):
                                dest_iso3 = plant.location.iso3
                                lat, lon = plant.location.lat, plant.location.lon

                        if dest_iso3 and lat is not None and lon is not None:
                            if dest_iso3 not in country_coords:
                                country_coords[dest_iso3] = []
                            country_coords[dest_iso3].append((lat, lon))

    # Calculate average coordinates for each country
    avg_coords = {}
    for iso3, coords_list in country_coords.items():
        if coords_list:
            avg_lat = sum(lat for lat, lon in coords_list) / len(coords_list)
            avg_lon = sum(lon for lat, lon in coords_list) / len(coords_list)
            avg_coords[iso3] = (avg_lat, avg_lon)

    # Create layout positions for nodes in the graph
    pos = {}
    for node in G.nodes:
        if node in avg_coords:
            lat, lon = avg_coords[node]
            # Convert to plot coordinates (longitude as x, latitude as y)
            # Scale to reasonable plot size
            x = lon / 180.0 * 4  # Scale longitude to roughly -4 to 4
            y = lat / 90.0 * 2  # Scale latitude to roughly -2 to 2
            pos[node] = (x, y)
        else:
            # If no coordinates found, place at origin with small random offset
            import random

            pos[node] = (random.uniform(-0.5, 0.5), random.uniform(-0.5, 0.5))

    logger.info(f"Geographic layout: Found coordinates for {len(avg_coords)} countries out of {len(G.nodes)} nodes")
    return pos


def cluster_geographic_layout(geo_pos: Dict[str, Tuple[float, float]], G: nx.Graph) -> Dict[str, Tuple[float, float]]:
    """
    Apply clustering to geographic layout to group nearby countries and reduce overlaps.
    """
    # Simple regional clustering based on longitude bands
    clustered_pos = geo_pos.copy()

    # Define rough continental/regional longitude bands
    regions = {"Americas": (-180, -30), "Europe_Africa": (-30, 60), "Asia_Pacific": (60, 180)}

    # Group countries by region
    regional_groups: Dict[str, list[str]] = {region: [] for region in regions}
    for country, (x, y) in geo_pos.items():
        # Convert back to longitude for regional classification
        lon = x / 4 * 180

        for region, (min_lon, max_lon) in regions.items():
            if min_lon <= lon < max_lon:
                regional_groups[region].append(country)
                break
        else:
            # Default to Asia_Pacific if no match
            regional_groups["Asia_Pacific"].append(country)

    # Apply slight clustering within regions to reduce overlaps
    for region, countries in regional_groups.items():
        if len(countries) > 1:
            # Calculate regional center
            region_positions = [geo_pos[country] for country in countries if country in geo_pos]
            if region_positions:
                # Calculate center (currently unused but variables kept for potential future use)
                # center_x = sum(x for x, y in region_positions) / len(region_positions)
                # center_y = sum(y for x, y in region_positions) / len(region_positions)

                # Apply slight spreading around the center to reduce overlaps
                for i, country in enumerate(countries):
                    if country in clustered_pos:
                        orig_x, orig_y = clustered_pos[country]
                        # Add small offset based on position in region
                        offset_x = (i % 3 - 1) * 0.3  # -0.3, 0, 0.3
                        offset_y = (i // 3 - 1) * 0.3
                        clustered_pos[country] = (orig_x + offset_x, orig_y + offset_y)

    return clustered_pos


def adjust_layout_for_overlaps(
    pos: Dict[str, Tuple[float, float]], node_sizes: Dict[str, float], min_distance: float = 0.3
) -> Dict[str, Tuple[float, float]]:
    """
    Adjust node positions to prevent overlapping based on node sizes.

    Args:
        pos: Dictionary of node positions
        node_sizes: Dictionary of node sizes (in plot coordinates)
        min_distance: Minimum distance between node centers

    Returns:
        Adjusted positions dictionary
    """
    adjusted_pos = pos.copy()
    nodes = list(pos.keys())

    # Iterative adjustment to separate overlapping nodes
    for iteration in range(50):  # Max iterations to prevent infinite loops
        moved = False

        for i, node1 in enumerate(nodes):
            for j, node2 in enumerate(nodes[i + 1 :], i + 1):
                x1, y1 = adjusted_pos[node1]
                x2, y2 = adjusted_pos[node2]

                # Calculate distance between centers
                dx = x2 - x1
                dy = y2 - y1
                distance = math.sqrt(dx * dx + dy * dy)

                # Calculate required minimum distance based on node sizes
                size1 = node_sizes.get(node1, 0.1)
                size2 = node_sizes.get(node2, 0.1)
                required_distance = max(min_distance, (size1 + size2) * 1.2)

                # If nodes are too close, push them apart
                if distance < required_distance and distance > 0:
                    # Calculate push vector
                    push_distance = (required_distance - distance) / 2
                    dx_norm = dx / distance
                    dy_norm = dy / distance

                    # Move nodes apart
                    adjusted_pos[node1] = (x1 - dx_norm * push_distance, y1 - dy_norm * push_distance)
                    adjusted_pos[node2] = (x2 + dx_norm * push_distance, y2 + dy_norm * push_distance)
                    moved = True

        # If no nodes were moved, we're done
        if not moved:
            break

    return adjusted_pos


def aggregate_allocations_by_country(
    allocations_by_commodity: Dict[str, CommodityAllocations], top_n: int = 20
) -> Dict[str, Dict[str, Any]]:
    """
    Aggregate allocations data by country for each commodity, filtering to top N country-to-country trade routes.

    Args:
        allocations_by_commodity: Dict of commodity allocations
        top_n: Number of top country-to-country trade routes to include per commodity

    Returns a nested dict: {commodity: {country_iso3: {
        'total_production': float,
        'total_demand': float,
        'total_imports': float,
        'total_exports': float,
        'flows_to': {dest_iso3: volume},
        'flows_from': {source_iso3: volume}
    }}}
    """
    country_data: Dict[str, Dict[str, Any]] = {}

    for commodity_name, commodity_allocations in allocations_by_commodity.items():
        # First, aggregate all flows by country pairs
        country_to_country_flows: Dict[Tuple[str, str], float] = {}
        for source, destinations in commodity_allocations.allocations.items():
            # Extract source country ISO3
            source_iso3 = None
            if hasattr(source, "location"):  # Supplier
                source_iso3 = source.location.iso3  # type: ignore[union-attr]
            elif isinstance(source, tuple) and len(source) == 2:  # (Plant, FurnaceGroup)
                plant, fg = source
                if hasattr(plant, "location") and plant.location:
                    source_iso3 = plant.location.iso3
            elif isinstance(source, str):  # String identifier
                source_iso3 = source.split("_")[0]

            if not source_iso3:
                continue

            for dest, volume in destinations.items():
                if volume > 0:
                    # Extract destination country ISO3
                    dest_iso3 = None
                    if isinstance(dest, tuple) and len(dest) == 2:  # (Plant, FurnaceGroup)
                        plant, fg = dest
                        if hasattr(plant, "location") and plant.location:
                            dest_iso3 = plant.location.iso3
                    elif hasattr(dest, "center_of_gravity") and dest.center_of_gravity:  # DemandCenter
                        dest_iso3 = dest.center_of_gravity.iso3  # type: ignore[union-attr]
                    elif isinstance(dest, str):  # String identifier
                        dest_iso3 = dest.split("_")[0]

                    if dest_iso3 and source_iso3 != dest_iso3:  # Only international trade
                        key = (source_iso3, dest_iso3)
                        country_to_country_flows[key] = country_to_country_flows.get(key, 0) + volume

        # Sort by aggregated volume and take top N country-to-country routes
        top_country_routes = sorted(country_to_country_flows.items(), key=lambda x: x[1], reverse=True)[:top_n]
        top_country_pairs = set(route[0] for route in top_country_routes)

        total_volume = sum(country_to_country_flows.values())
        if total_volume > 0:
            top_volume = sum(route[1] for route in top_country_routes)
            logger.info(
                f"Commodity {commodity_name}: Found {len(country_to_country_flows)} total country pairs, "
                f"using top {len(top_country_routes)} routes "
                f"({top_volume / total_volume * 100:.1f}% of total volume)"
            )
            # Count unique countries in top routes
            unique_countries = set()
            for (source, dest), _ in top_country_routes:  # type: ignore[assignment]
                unique_countries.add(source)  # type: ignore[arg-type]
                unique_countries.add(dest)  # type: ignore[arg-type]
            logger.info(
                f"  These top {len(top_country_routes)} routes involve {len(unique_countries)} unique countries"
            )
        else:
            logger.info(f"Commodity {commodity_name}: No international trade flows found")

        country_data[commodity_name] = defaultdict(
            lambda: {
                "total_production": 0,
                "total_demand": 0,
                "total_imports": 0,
                "total_exports": 0,
                "flows_to": defaultdict(float),
                "flows_from": defaultdict(float),
            }
        )

        for source, destinations in commodity_allocations.allocations.items():
            # Determine source country
            source_iso3 = None
            source_capacity = 0.0

            if hasattr(source, "location"):  # Supplier
                source_iso3 = source.location.iso3  # type: ignore[union-attr]  # type: ignore[union-attr]
                # Get capacity for current year or latest available
                if hasattr(source, "capacity_by_year") and source.capacity_by_year:  # type: ignore[union-attr]
                    # Get the latest year's capacity
                    latest_year = max(source.capacity_by_year.keys())  # type: ignore[union-attr]
                    source_capacity = float(source.capacity_by_year[latest_year])  # type: ignore[union-attr]
            elif isinstance(source, tuple) and len(source) == 2:  # (Plant, FurnaceGroup)
                plant, fg = source
                if hasattr(plant, "location") and plant.location:
                    source_iso3 = plant.location.iso3
                    if hasattr(fg, "capacity"):
                        source_capacity = float(fg.capacity)

            if source_iso3:
                country_data[commodity_name][source_iso3]["total_production"] += source_capacity

            # Process destinations (only if part of top N country-to-country routes)
            for dest, volume in destinations.items():
                if volume <= 0:
                    continue

                dest_iso3 = None
                dest_demand = 0.0

                if hasattr(dest, "center_of_gravity") and dest.center_of_gravity:  # type: ignore[union-attr]  # DemandCenter
                    dest_iso3 = dest.center_of_gravity.iso3  # type: ignore[union-attr]
                    # Get demand for current year or latest available
                    if hasattr(dest, "demand_by_year") and dest.demand_by_year:  # type: ignore[union-attr]
                        # Get the latest year's demand
                        latest_year = max(dest.demand_by_year.keys())  # type: ignore[union-attr]
                        dest_demand = float(dest.demand_by_year[latest_year])  # type: ignore[union-attr]
                elif isinstance(dest, tuple) and len(dest) == 2:  # (Plant, FurnaceGroup)
                    plant, fg = dest
                    if hasattr(plant, "location") and plant.location:
                        dest_iso3 = plant.location.iso3

                if source_iso3 and dest_iso3 and volume > 0:
                    if source_iso3 != dest_iso3:
                        # Only include if this country pair is in the top N routes
                        if (source_iso3, dest_iso3) in top_country_pairs:
                            # International trade
                            country_data[commodity_name][source_iso3]["flows_to"][dest_iso3] += volume
                            country_data[commodity_name][source_iso3]["total_exports"] += volume
                            country_data[commodity_name][dest_iso3]["flows_from"][source_iso3] += volume
                            country_data[commodity_name][dest_iso3]["total_imports"] += volume

                if dest_iso3 and dest_demand > 0:
                    country_data[commodity_name][dest_iso3]["total_demand"] = max(
                        country_data[commodity_name][dest_iso3]["total_demand"], dest_demand
                    )

    return country_data


def create_multi_commodity_network_plot(
    allocations_by_commodity: Dict[str, CommodityAllocations],
    year: Year,
    output_dir: Path,
    output_filename: str = "commodity_trade_network.html",
    top_n: int = 20,
    layout_algorithm: str = "geographic",
    country_mappings: Optional[Any] = None,
) -> None:
    """
    Create an interactive network plot with commodity filtering dropdown.

    Args:
        allocations_by_commodity: Dict of commodity allocations
        year: Year for the plot
        output_dir: Directory to save the plot
        output_filename: Name of the output HTML file
        top_n: Number of top country-to-country trade routes to include per commodity
        layout_algorithm: Layout algorithm to use. Options:
            - 'kamada_kawai' (default): High-quality layout, minimizes crossings
            - 'spring_layered': Multi-level spring with better separation
            - 'circular_weighted': Circular layout with degree-based radius
            - 'shell': Concentric circles based on node degree
            - 'force_atlas': Strong repulsion force-directed
            - 'hierarchical': Vertical hierarchy based on import/export ratio
            - 'geographic': Countries positioned by actual latitude/longitude
            - 'geographic_spring': Geographic positions with gentle spring forces
            - 'geographic_clustered': Geographic with regional clustering
            - 'spring': Original spring layout
    """
    # Aggregate data by country
    country_data = aggregate_allocations_by_country(allocations_by_commodity, top_n=top_n)
    # Convert nested defaultdicts to regular dicts for printing
    readable_data: Dict[str, Dict[str, Any]] = {}
    for commodity, countries in country_data.items():
        readable_data[commodity] = {}
        for country, data in countries.items():
            readable_data[commodity][country] = {
                **data,
                "flows_to": dict(data["flows_to"]),
                "flows_from": dict(data["flows_from"]),
            }

    print(f"Aggregated country data: {readable_data}")

    # Create traces for each commodity
    all_traces = {}
    max_volume_global = 0
    max_node_size_global = 0

    # First pass: find global maximums for consistent scaling
    for commodity in country_data:
        commodity_data = country_data[commodity]
        for country, data in commodity_data.items():
            max_node_size_global = max(max_node_size_global, max(data["total_imports"], data["total_exports"], 1))
            for dest_country, volume in data["flows_to"].items():
                max_volume_global = max(max_volume_global, volume)

    # Create combined graph for layout
    G_combined = nx.DiGraph()
    for commodity in country_data:
        commodity_data = country_data[commodity]
        for country_iso3, data in commodity_data.items():
            if (
                data["total_production"] > 0
                or data["total_demand"] > 0
                or data["total_imports"] > 0
                or data["total_exports"] > 0
            ):
                G_combined.add_node(country_iso3)

        for source_country, data in commodity_data.items():
            for dest_country, volume in data["flows_to"].items():
                if volume > 0:
                    G_combined.add_edge(source_country, dest_country)

    # Calculate layout once for all commodities using specified algorithm
    pos = calculate_layout(
        G_combined, layout_algorithm, country_data=country_data, allocations_by_commodity=allocations_by_commodity
    )

    # Calculate representative node sizes for overlap prevention
    node_sizes = {}
    for node in G_combined.nodes:
        max_size = 0
        for commodity in country_data:
            if node in country_data[commodity]:
                data = country_data[commodity][node]
                size = max(data["total_imports"], data["total_exports"], 1)
                max_size = max(max_size, size)

        if max_size > 0:
            circle_size = 20 + 40 * (max_size / max_node_size_global)
            node_sizes[node] = circle_size / 800
        else:
            node_sizes[node] = 0.1

    # Adjust positions to prevent overlaps
    pos = adjust_layout_for_overlaps(pos, node_sizes)

    # First, identify commodities that have international trade flows
    commodities_with_trades = []
    for commodity in country_data:
        commodity_data = country_data[commodity]
        has_flows = False
        for source_country, data in commodity_data.items():
            if data["flows_to"]:  # If there are any destination countries
                has_flows = True
                break
        if has_flows:
            commodities_with_trades.append(commodity)

    # Create traces for each commodity
    for commodity_idx, commodity in enumerate(country_data):
        commodity_data = country_data[commodity]
        traces = []
        # Check if this is the first commodity with trades for initial visibility
        is_first_with_trades = commodities_with_trades and commodity == commodities_with_trades[0]

        # Add edges
        edge_list = []
        for source_country, data in commodity_data.items():
            for dest_country, volume in data["flows_to"].items():
                if volume > 0 and source_country in pos and dest_country in pos:
                    edge_list.append((source_country, dest_country, volume))

        for source, target, volume in edge_list:
            x0, y0 = pos[source]
            x1, y1 = pos[target]

            width = max(2, 8 * (volume / max_volume_global))

            # Calculate arrow direction and position
            dx = x1 - x0
            dy = y1 - y0
            length = math.sqrt(dx * dx + dy * dy)

            # Position arrow at center of line
            if length > 0:
                # Place arrow at midpoint of line
                arrow_x = (x0 + x1) / 2
                arrow_y = (y0 + y1) / 2

                # Calculate angle for arrowhead (pointing toward target)
                # atan2 gives angle from positive x-axis, but triangle-up points up by default
                # So we need to rotate it to align with the line direction
                angle = math.degrees(math.atan2(dy, dx)) + 90
            else:
                arrow_x, arrow_y = x1, y1
                angle = 0

            # Edge line with hover
            source_name = iso3_to_country_name(source)
            target_name = iso3_to_country_name(target)
            hover_text = f"<b>{commodity}</b><br>{source_name} → {target_name}<br>Volume: {volume:,.0f}"
            edge_trace = go.Scatter(
                x=[x0, x1, None],  # Line from source center to target center
                y=[y0, y1, None],
                mode="lines",
                line=dict(width=width, color="rgba(125,125,125,0.7)"),
                hoverinfo="text",
                hovertext=hover_text,
                hoverlabel=dict(bgcolor="white", bordercolor="gray"),
                showlegend=False,
                visible=is_first_with_trades,  # Only first commodity with trades visible initially
                name=f"{source}→{target}",
            )
            traces.append(edge_trace)

            # Arrowhead
            arrow_size = max(8, width * 1.5)
            arrow_trace = go.Scatter(
                x=[arrow_x],
                y=[arrow_y],
                mode="markers",
                marker=dict(
                    size=arrow_size,
                    symbol="triangle-up",  # Use triangle-up and rotate with angle
                    angle=angle,  # Triangle now properly aligned with line direction
                    color="rgba(125,125,125,0.8)",
                    line=dict(width=1, color="rgba(100,100,100,0.8)"),
                ),
                hoverinfo="text",
                hovertext=hover_text,
                hoverlabel=dict(bgcolor="white", bordercolor="gray"),
                showlegend=False,
                visible=is_first_with_trades,
                name=f"{source}→{target}_arrow",
            )
            traces.append(arrow_trace)

        # Add nodes
        node_x = []
        node_y = []
        node_text = []
        node_hover = []
        node_size = []
        node_color = []

        active_countries = set()
        for country, data in commodity_data.items():
            # Only include countries with actual trade flows
            if data["total_imports"] > 0 or data["total_exports"] > 0:
                active_countries.add(country)

        for country in active_countries:
            if country in pos:
                x, y = pos[country]
                node_x.append(x)
                node_y.append(y)
                node_text.append(iso3_to_country_name(country))

                data = commodity_data[country]
                country_name = iso3_to_country_name(country)
                hover_text = f"<b>{country_name} ({country})</b><br>"
                hover_text += f"Commodity: {commodity}<br>"
                hover_text += f"Production: {data['total_production']:,.0f}<br>"
                hover_text += f"Demand: {data['total_demand']:,.0f}<br>"
                hover_text += f"Imports: {data['total_imports']:,.0f}<br>"
                hover_text += f"Exports: {data['total_exports']:,.0f}"
                node_hover.append(hover_text)

                size = 20 + 40 * (max(data["total_imports"], data["total_exports"], 1) / max_node_size_global)
                node_size.append(size)

                # Color by region if country mappings available, otherwise by trade balance
                region = None
                if country_mappings:
                    # Get region from country mappings service
                    for mapping in country_mappings._mappings.values():
                        if mapping.iso3 == country:
                            region = mapping.region_for_outputs
                            break
                else:
                    # Try to load from file as fallback
                    country_to_region = load_country_mappings_from_file()
                    region = country_to_region.get(country)

                if region:
                    color_palette = get_region_color_palette()
                    node_color.append(color_palette.get(region, "#c5b0d5"))  # Default to light purple
                else:
                    # Fallback to trade balance coloring
                    balance = data["total_exports"] - data["total_imports"]
                    if balance > 0:
                        node_color.append("green")
                    elif balance < 0:
                        node_color.append("red")
                    else:
                        node_color.append("gray")

        if node_x:  # Only add node trace if there are nodes
            node_trace = go.Scatter(
                x=node_x,
                y=node_y,
                mode="markers+text",
                text=node_text,
                textposition="top center",
                hoverinfo="text",
                hovertext=node_hover,
                marker=dict(
                    size=node_size,
                    color=node_color,
                    opacity=1.0,  # Make circles fully opaque
                    line=dict(width=2, color="white"),
                ),
                showlegend=False,
                visible=is_first_with_trades,
            )
            traces.append(node_trace)

        all_traces[commodity] = traces

    # Create figure with all traces
    fig = go.Figure()

    # Add all traces to figure
    total_traces = 0
    for commodity in country_data:
        commodity_trace_count = len(all_traces[commodity])
        logger.debug(f"Commodity {commodity}: {commodity_trace_count} traces, starting at index {total_traces}")
        for trace in all_traces[commodity]:
            fig.add_trace(trace)
        total_traces += commodity_trace_count

    # Log the initial visibility state
    logger.debug(f"Commodities with trades: {commodities_with_trades}")
    logger.debug(
        f"First commodity with trades will be: {commodities_with_trades[0] if commodities_with_trades else 'None'}"
    )

    # Create dropdown menu (only for commodities with trade flows)
    dropdown_buttons = []
    for commodity_idx, commodity in enumerate(commodities_with_trades):
        # Create visibility list
        visible_list = []
        trace_idx = 0
        for comm in country_data:
            for _ in all_traces[comm]:
                visible_list.append(comm == commodity)
                trace_idx += 1

        button = dict(
            label=commodity.replace("_", " ").title(),
            method="update",
            args=[
                {"visible": visible_list},
                {"title": f"{commodity.replace('_', ' ').title()} Trade Network - {year}"},
            ],
        )
        dropdown_buttons.append(button)

    # Update layout with dropdown
    fig.update_layout(
        updatemenus=[
            dict(
                buttons=dropdown_buttons,
                direction="down",
                pad={"r": 10, "t": 10},
                showactive=True,
                x=0.1,
                xanchor="left",
                y=1.15,
                yanchor="top",
            )
        ],
        title=f"{commodities_with_trades[0].replace('_', ' ').title() if commodities_with_trades else 'No'} Trade Network - {year}",
        showlegend=False,
        hovermode="closest",
        margin=dict(b=20, l=5, r=5, t=100),
        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        plot_bgcolor="white",
        width=1200,
        height=800,
        annotations=[
            dict(text="Select Commodity:", showarrow=False, x=0, y=1.15, xref="paper", yref="paper", align="left"),
            dict(
                text="Node colors represent regions | Node size: Trade volume"
                if (country_mappings or getattr(create_multi_commodity_network_plot, "_country_mappings_cache", None))
                else "Node colors: Green=Net Exporter, Red=Net Importer, Gray=Balanced | Node size: Trade volume",
                showarrow=False,
                x=0.5,
                y=-0.05,
                xref="paper",
                yref="paper",
                xanchor="center",
            ),
        ],
    )

    # Save to HTML
    output_path = output_dir / output_filename
    fig.write_html(str(output_path), include_plotlyjs="cdn", config={"displayModeBar": True, "displaylogo": False})
    logger.info(f"Saved multi-commodity network plot to: {output_path}")
