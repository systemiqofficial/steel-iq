import matplotlib
import os
import sys
import shutil

import matplotlib.pyplot as plt
import logging
import cartopy.crs as ccrs  # type: ignore
import cartopy.feature as cfeature  # type: ignore
import geopandas as gpd  # type: ignore
import cartopy.io.shapereader as shpreader  # type: ignore
from cartopy.mpl.geoaxes import GeoAxes  # type: ignore
import matplotlib.cm as cm
import pandas as pd
import numpy as np
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator
from collections import defaultdict
import folium
import math
import pydeck as pdk
import xarray as xr

from matplotlib.patches import Patch

from steelo.domain import Year
from steelo.domain.models import CommodityAllocations, DemandCenter, Supplier, Plant, Location, Volumes
from steelo.adapters.repositories.interface import PlantRepository


from steelo.utilities.variable_matching import LULC_LABELS_TO_NUM
from pathlib import Path
from typing import Optional, TYPE_CHECKING, Dict, Any, Tuple
from steelo.domain.constants import T_TO_MT

if TYPE_CHECKING:
    from steelo.domain.models import PlotPaths

# Set non-interactive backend if in headless environment or if explicitly requested
mpl_backend = os.environ.get("MPLBACKEND")
if mpl_backend:
    matplotlib.use(mpl_backend)
elif "pytest" in sys.modules or os.environ.get("CI") or not os.environ.get("DISPLAY"):
    # Use non-interactive backend for tests, CI, or headless environments
    matplotlib.use("Agg")

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


def _copy_deckgl_to_output_dir(output_dir: Path) -> Optional[str]:
    """Copy deck.gl library to output directory for standalone HTML files.

    Standalone HTML files opened with file:// protocol cannot access /static/ URLs.
    This function copies deck.gl to the same directory as the HTML file so it can be
    referenced with a relative path.

    Args:
        output_dir: Directory where the HTML file will be written

    Returns:
        Relative path to deck.gl file if successful, None if vendor file not found
        (caller should fall back to CDN in that case)
    """
    # Find the vendor deck.gl file
    # This file is at: src/steeloweb/static/vendor/mapping-libs/deck.gl@8.9.35/dist.min.js
    # plotting.py is at: src/steelo/utilities/plotting.py
    # So we go up 2 levels to src/, then navigate to steeloweb/static/vendor/...

    plotting_file = Path(__file__)  # src/steelo/utilities/plotting.py
    src_dir = plotting_file.parents[2]  # Go up to src/
    vendor_deckgl = src_dir / "steeloweb" / "static" / "vendor" / "mapping-libs" / "deck.gl@8.9.35" / "dist.min.js"

    if not vendor_deckgl.exists():
        logger.warning(
            f"Vendor deck.gl not found at {vendor_deckgl}. "
            "Standalone HTML files will use CDN fallback (requires internet)."
        )
        return None

    # Copy to output directory with a simple filename
    dest_file = output_dir / "deck.gl.min.js"

    try:
        shutil.copy2(vendor_deckgl, dest_file)
        logger.debug(f"Copied deck.gl to {dest_file} for standalone HTML")
        return "./deck.gl.min.js"  # Relative path for file:// protocol
    except Exception as e:
        logger.warning(f"Failed to copy deck.gl to output directory: {e}. Using CDN fallback.")
        return None


known_techs = [
    "BOF",
    "EAF",
    "BF",
    "DRI",
    "DRI+ESF",
    "MOE",
    "BF_CHARCOAL",
    "E-WIN",
    "SR",
    "BF+CCS",
    "BF+CCU",
    "DRI+CCS",
    "DRI+CCU",
    "DRI+ESF+CCS",
    "DRI+ESF+CCU",
    "BF_CHARCOAL+CCS",
    "BF_CHARCOAL+CCU",
    "SR+CCS",
    "SR+CCU",
    "scrap_supply",
    "IO_high_supply",
    "IO_mid_supply",
    "IO_low_supply",
    "DRI_high_supply",
    "DRI_mid_supply",
    "DRI_low_supply",
]

region2colours = {
    "Europe": "#003399",
    "India": "#008000",
    "China": "#DE2910",
    "MENA": "#9ACD32",
    "Developed Asia": "#FF6F00",
    "Other Asia": "#0099CC",
    "CIS": "#1E90FF",
    "Subsaharan Africa": "#800000",
    "North America": "#3C3B6E",
    "Latin America": "#00A86B",
    "Oceania": "#40E0D0",
    "Asia & Oceania": "#FFB347",
    "Africa": "#8B4513",
    "Americas": "#4B0082",
}

tech2colours = {
    "DRI": "#003399",
    "BF": "#DE2910",
    "EAF": "#FF6F00",
    "BOF": "black",
    "DRI+ESF": "#008000",
    "MOE": "#FFC0CB",
    "BF_CHARCOAL": "#A52A2A",
    "E-WIN": "#800000",
    "SR": "#0099CC",
    "BF+CCS": "#1E90FF",
    "BF+CCU": "#00A86B",
    "DRI+CCS": "#FFB347",
    "DRI+CCU": "#8B4513",
    "DRI+ESF+CCS": "#4B0082",
    "DRI+ESF+CCU": "#9ACD32",
    "BF_CHARCOAL+CCS": "#FF69B4",
    "BF_CHARCOAL+CCU": "#40E0D0",
    "SR+CCS": "#800080",
    "SR+CCU": "#A020F0",
}


def plot_steel_cost_curve(curve, demand):
    """
    Plots a steel cost curve (sorted by ascending cost) and indicates the
    marginal producer for a given demand level.

    :param curve: A list of dictionaries of the form
                  [
                    {"cumulative_capacity": float, "production_cost": float},
                    ...
                  ],
                  assumed to be sorted in ascending order of production cost
                  (i.e., a "cost curve").
    :param demand: A float indicating the demanded capacity. The function will
                   highlight the point on the curve where this demand level
                   intersects.
    """

    # Extract x (cumulative capacities) and y (production costs) from the curve
    cumulative_capacities = [point["cumulative_capacity"] for point in curve]
    production_costs = [point["production_cost"] for point in curve]

    # Convert capacities from t to kt
    cumulative_capacities = [x / 1000 for x in cumulative_capacities]

    # Find maximum capacity to check demand boundaries
    max_capacity = cumulative_capacities[-1] if curve else 0

    # If demand is out of range, we can clip or just note it
    if demand < 0:
        demand = 0
    elif demand > max_capacity:
        logger.warning(
            f"Demand ({demand}) exceeds total available capacity ({max_capacity}). "
            "Marginal producer cost will be that of the final producer."
        )
        demand = max_capacity

    # Find the marginal producer:
    # The marginal producer is the first point where cumulative_capacity >= demand
    marginal_producer = None
    for point in curve:
        if point["cumulative_capacity"] >= demand:
            marginal_producer = point
            break

    # Plot the cost curve
    plt.figure(figsize=(8, 5))

    # Often cost curves are visualized as a step plot:
    #   The 'where="post"' makes the step stay at the current y until x changes.
    #   You can also use a simple line plot if you prefer.
    plt.step(cumulative_capacities, production_costs, where="post", label="Cost Curve")

    # Plot a vertical line at the demand level
    plt.axvline(x=demand, color="red", linestyle="--", label="Demand")

    # If we found a valid marginal producer, annotate it
    if marginal_producer:
        marginal_x = demand
        marginal_cost = marginal_producer["production_cost"]

        # A horizontal line at the marginal cost (optional)
        plt.axhline(y=marginal_cost, color="gray", linestyle="--")

        # Mark the intersection
        plt.scatter(marginal_x, marginal_cost, color="red", zorder=5)

        # Add text annotation
        plt.text(
            marginal_x,
            marginal_cost,
            f"  Marginal producer cost = {marginal_cost:.2f}",
            color="red",
            verticalalignment="bottom",
        )

    # Labeling the plot
    plt.title("Steel Cost Curve")
    plt.xlabel("Cumulative Capacity (kt)")
    plt.ylabel("Production Cost (US$/t)")
    plt.legend()
    plt.grid(True)

    # Show the plot
    plt.show()


def plot_cost_curve_per_region(plants: PlantRepository) -> Figure:
    # Initialize variables for plotting
    current_capacity = 0
    rectangles = []
    regional_data: dict = {}

    # Collect data for rectangle plotting
    for plant in plants.list():
        if hasattr(plant, "average_steel_cost") is False or hasattr(plant, "steel_capacity") is False:
            continue
        region = plant.location.region
        regional_data[region] = regional_data.get(region, []) + [
            (
                (float(plant.steel_capacity) if plant.steel_capacity is not None else 0.0)
                * (float(plant.average_steel_cost) if plant.average_steel_cost is not None else 0.0),
                float(plant.steel_capacity) if plant.steel_capacity is not None else 0.0,
            )
        ]

    for region, data in regional_data.items():
        total_cost = sum([x[0] for x in data])
        total_capacity = sum([x[1] for x in data])
        width = total_capacity * T_TO_MT  # Convert tons to megatons
        height = total_cost / total_capacity
        rectangles.append((current_capacity, height, width))
        current_capacity += total_capacity

    # Reset current capacity for sorted rectangles
    current_capacity = 0
    sorted_rectangles = []
    region_colors = {}
    color_palette = plt.cm.get_cmap("tab20", len(regional_data))

    for idx, (region, data) in enumerate(regional_data.items()):
        total_cost = sum([x[0] for x in data])
        total_capacity = sum([x[1] for x in data])
        width = total_capacity * T_TO_MT  # Convert to megatons
        height = total_cost / total_capacity
        sorted_rectangles.append((current_capacity, height, width, region))
        region_colors[region] = color_palette(idx)
        current_capacity += width

    # Sort rectangles by height (average cost) in ascending order
    sorted_rectangles.sort(key=lambda x: x[1])

    # Sort rectangles by height (average cost) in ascending order
    sorted_rectangles.sort(key=lambda x: x[1])

    # Reset current capacity for sorted rectangles
    current_capacity = 0
    final_rectangles = []
    for x_start, height, width, region in sorted_rectangles:
        final_rectangles.append((current_capacity, height, width, region))
        current_capacity += width

    # Plot the rectangles
    fig, ax = plt.subplots(figsize=(10, 6))
    for x_start, height, width, region in final_rectangles:
        ax.bar(x_start, height, width=width, align="edge", edgecolor="black", color=region_colors[region], label=region)

    # Add legend
    handles, labels = ax.get_legend_handles_labels()
    by_label = dict(zip(labels, handles))
    ax.legend(by_label.values(), by_label.keys(), title="Region")

    # Add labels and grid
    ax.set_xlabel("Cumulative Annual Capacity [megaton per year]", fontsize=12)
    ax.set_ylabel("Cost per Unit [US$/t]", fontsize=12)
    ax.set_title("Cost Curve per Region", fontsize=14)
    ax.grid(True, linestyle="--", alpha=0.6)

    # Show the plot
    return fig


def plot_global_heatmap_per_country(
    df: pd.DataFrame,
    var_name: str,
    title: str,
    colormap_type: str = "continous_high_best",
    outlier_treatment: bool = False,
    outlier_side="both",
) -> None:
    """
    Reads in global country level data, converts the country name to the corresponding geometry and plots a global heatmap
    of a certain variable per country. Missing values (and outliers, if applicable) are hatched.
        Inputs:
            - df: DataFrame with the data to plot. Must contain a "Country" column and a column "var_name" with the variable to plot (at least).
            - var_name: Name of the column in df with the variable to plot.
            - title: Title of the plot.
            - colormap_type: Type of colormap to use. Options: "divergent_low_best", "divergent_high_best", "continuous_low_best", "continuous_high_best".
            - outlier_treatment: If True, outliers are set to nan to avoid distorting the color scale.
            - outlier_side: If "both", outliers on both sides are set to nan. If "high"/"low", only high/low outliers are set to nan.
    """

    # Load world geometries from Cartopy's naturalearth_shapefile
    shapefile = shpreader.natural_earth(resolution="110m", category="cultural", name="admin_0_countries")
    reader = shpreader.Reader(shapefile)
    world = reader.records()
    countries_geom = {record.attributes["NAME"]: record.geometry for record in world}
    countries_geom_df = pd.DataFrame(list(countries_geom.items()), columns=["Country", "Geometry"]).set_index("Country")

    # Create a GeoDataFrame
    for i in countries_geom_df.index:
        if i not in df.index:
            logger.warning(f"Warning: {i} not found in df")
    df_with_geom = df.merge(countries_geom_df, left_index=True, right_index=True)
    gdf = gpd.GeoDataFrame(df_with_geom, geometry="Geometry")
    # Rename the column which contains var_name to var_name
    gdf = gdf.rename(columns={col: var_name for col in gdf.columns if str(var_name) in str(col)})

    # Set outliers to nan to avoid distorting the color scale
    if outlier_treatment:
        mean = gdf[var_name].mean()
        if outlier_side in ["both", "high"]:
            gdf.loc[gdf[var_name] > 2 * mean, var_name] = np.nan
        if outlier_side in ["both", "low"]:
            gdf.loc[gdf[var_name] < 0.5 * mean, var_name] = np.nan

    # Set colormap
    if "divergent" in colormap_type:
        colormap = "RdBu"
    elif "continuous" in colormap_type:
        colormap = "Blues"
    if "low_best" in colormap_type:
        colormap = colormap + "_r"

    # Plot heatmap
    fig, ax = plt.subplots(1, 1, figsize=(15, 10), subplot_kw={"projection": ccrs.PlateCarree()})
    assert isinstance(ax, GeoAxes)
    ax.coastlines()
    ax.add_feature(cfeature.BORDERS, linestyle=":")
    ax.add_feature(cfeature.LAND, edgecolor="black")
    gdf.plot(
        ax=ax,
        column=var_name,
        cmap=colormap,
        missing_kwds={
            "color": "none",
            "edgecolor": "black",
            "alpha": 0.5,
            "hatch": "//////",
            "label": "Missing values",
        },
    )
    sm = plt.cm.ScalarMappable(cmap=colormap, norm=plt.Normalize(vmin=gdf[var_name].min(), vmax=gdf[var_name].max()))
    sm.set_array([])
    cbar = plt.colorbar(sm, ax=ax, orientation="horizontal", pad=0.05, shrink=0.5, label=var_name)
    cbar.ax.tick_params(labelsize=16)
    cbar.set_label(var_name, fontsize=18)
    ax.set_title(title, fontsize=20)
    fig.tight_layout()
    plt.show()
    plt.close()


def plot_detailed_trade_map_folium(
    allocations_by_commodity: dict[str, CommodityAllocations],
    chosen_year: Year,
    plot_paths: Optional["PlotPaths"] = None,
) -> folium.Map:
    """
    Generates an interactive map to visualize allocations for multiple commodities,
    with:
      - Line hover tooltip (Commodity + Volume).
      - Triangle marker near each line's end with the same hover tooltip.
    """

    fol_map = folium.Map(location=[0, 0], zoom_start=2)

    arcs_by_tech = defaultdict(list)
    all_sources = set()
    all_destinations = set()

    def get_source_tech(source) -> str:
        if isinstance(source, tuple):  # (Plant, FurnaceGroup)
            _, furnace_group = source
            return furnace_group.technology.name or "unknown"
        elif isinstance(source, Supplier):
            return f"{source.commodity}_supply"
        else:
            return "unknown"

    # Find max allocation
    all_volumes = []
    for commodity, commodity_allocations in allocations_by_commodity.items():
        for source, destinations in commodity_allocations.allocations.items():
            for _, volume in destinations.items():
                all_volumes.append(volume)
    max_allocation = max(all_volumes) if all_volumes else 1

    # Build arc data
    for commodity, commodity_allocations in allocations_by_commodity.items():
        for source, destinations in commodity_allocations.allocations.items():
            source_tech = get_source_tech(source)
            if source_tech not in known_techs:
                source_tech = "unknown"

            if isinstance(source, tuple):  # (Plant, FurnaceGroup)
                plant, _ = source
                source_location = (plant.location.lat, plant.location.lon)
            elif isinstance(source, Supplier):
                source_location = (source.location.lat, source.location.lon)
            else:
                logger.warning(f"[WARNING] Unexpected source type: {source}")
                continue

            all_sources.add(source)

            for destination, volume in destinations.items():
                if isinstance(destination, tuple):  # (Plant, FurnaceGroup)
                    dest_plant, _ = destination
                    dest_location = (dest_plant.location.lat, dest_plant.location.lon)
                elif isinstance(destination, DemandCenter):
                    dest_location = (
                        destination.center_of_gravity.lat,
                        destination.center_of_gravity.lon,
                    )
                else:
                    logger.warning(f"[WARNING] Unexpected destination type: {destination}")
                    continue

                all_destinations.add(destination)

                normalized_weight = (volume / max_allocation) * 20

                color_map = {
                    "BOF": "black",
                    "EAF": "darkgreen",
                    "other": "red",
                    "unknown": "brown",
                    "BF": "grey",
                    "DRI": "blue",
                    "scrap_supply": "brown",
                    "Prep Sinter:": "orange",
                    "Prep Pellets:": "yellow",
                    "IO_high_supply": "purple",
                    "IO_low_supply": "purple",
                    "IO_mid_supply": "purple",
                    "pellets_high_supply": "yellow",
                    "pellets_mid_supply": "yellow",
                }
                arc_color = color_map.get(source_tech, "blue")

                arcs_by_tech[source_tech].append(
                    {
                        "source_loc": source_location,
                        "dest_loc": dest_location,
                        "commodity": commodity,
                        "volume": volume,
                        "color": arc_color,
                        "weight": normalized_weight,
                        "cost": commodity_allocations.allocation_costs[source][destination],
                    }
                )

    def compute_bearing(lat1, lon1, lat2, lon2):
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        d_lambda = math.radians(lon2 - lon1)
        y = math.sin(d_lambda) * math.cos(phi2)
        x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(d_lambda)
        bearing = math.degrees(math.atan2(y, x))
        return (bearing + 360) % 360

    def fraction_along_line(lat1, lon1, lat2, lon2, fraction=1.0):
        lat = lat1 + fraction * (lat2 - lat1)
        lon = lon1 + fraction * (lon2 - lon1)
        return (lat, lon)

    for tech_name in known_techs:
        if tech_name not in arcs_by_tech:
            continue

        fg = folium.FeatureGroup(name=tech_name, show=True)
        for arc in arcs_by_tech[tech_name]:
            source_loc = arc["source_loc"]
            dest_loc = arc["dest_loc"]
            (lat1, lon1) = source_loc if isinstance(source_loc, tuple) else (0.0, 0.0)
            (lat2, lon2) = dest_loc if isinstance(dest_loc, tuple) else (0.0, 0.0)
            coords = [arc["source_loc"], arc["dest_loc"]]

            # (1) PolyLine with hover tooltip
            try:
                weight_raw = arc["weight"]
                weight_value = (
                    float(weight_raw) if weight_raw is not None and isinstance(weight_raw, (int, float, str)) else 1.0
                )
                arc_weight = max(2, min(int(weight_value * 3), 15))
            except (ValueError, TypeError):
                arc_weight = 2  # fallback weight
            # weight prop to volume, but at least 4px and at most 20px
            polyline = folium.PolyLine(locations=coords, color=arc["color"], weight=arc_weight, opacity=1.0)
            polyline.add_child(
                folium.Tooltip(f"Commodity: {arc['commodity']}, Volume: {arc['volume']:.2f} at {arc['cost']:.2f}")
            )
            polyline.add_to(fg)

            # (2) Triangle marker near the end, also with a tooltip
            arrow_lat, arrow_lon = fraction_along_line(lat1, lon1, lat2, lon2, fraction=1.01)
            bearing = compute_bearing(lat1, lon1, lat2, lon2)
            try:
                weight_raw = arc["weight"]
                weight_value = (
                    float(weight_raw) if weight_raw is not None and isinstance(weight_raw, (int, float, str)) else 1.0
                )
                triangle_size = int(max(10, min(int(weight_value * 6), 40)))
            except (ValueError, TypeError):
                triangle_size = 10  # fallback size
            # arrow heads prop to weight, but at least 10px and at most 50px

            arrow_html = f"""
            <div style="
                transform: rotate({bearing}deg);
                font-size: {triangle_size}px;
                line-height: {triangle_size}px;
                color: {arc["color"]};
                text-align: center;
            ">
                &#9650;
            </div>
            """
            icon = folium.DivIcon(
                icon_size=(triangle_size, triangle_size),
                icon_anchor=(triangle_size // 2, triangle_size // 2),
                html=arrow_html,
            )

            # Attach same tooltip to the marker
            marker = folium.Marker(
                location=(arrow_lat, arrow_lon),
                icon=icon,
                tooltip=f"Commodity: {arc['commodity']}, Volume: {arc['volume']}",
            )
            marker.add_to(fg)

        fg.add_to(fol_map)

    # Markers for plants, mines, demand centers
    marker_fg = folium.FeatureGroup(name="Nodes", show=True)
    marker_fg.add_to(fol_map)

    def get_plant_tech_str(plant: Plant) -> str:
        by_tech: dict[str, float] = defaultdict(float)
        cost_by_tech: dict[str, float] = defaultdict(float)
        for fg_obj in plant.furnace_groups:
            by_tech[fg_obj.technology.name] += fg_obj.capacity
            if fg_obj.technology.dynamic_business_case is not None:
                tot_cost = 0.0
                num_costs = 0
                for eff_pm in fg_obj.effective_primary_feedstocks:
                    mc = eff_pm.metallic_charge
                    if mc in fg_obj.energy_vopex_by_input:
                        tot_cost += fg_obj.energy_vopex_by_input[mc]
                        num_costs += 1
                if num_costs > 0:
                    cost_by_tech[fg_obj.technology.name] = tot_cost / num_costs
                else:
                    cost_by_tech[fg_obj.technology.name] = 999
        lines = [f"{t}: {cap:,.2f} t at {cost_by_tech[t]:,.2f}" for t, cap in by_tech.items()]
        return "<br/>".join(lines)

    for source in all_sources:
        if isinstance(source, tuple):
            plant, _ = source
            location = (plant.location.lat, plant.location.lon)
            popup_html = f"""
            <b>Plant: {plant.plant_id}</b><br/>
            {get_plant_tech_str(plant)}
            """
            icon_color = "blue"
            icon_type = "industry"
        elif isinstance(source, Supplier):
            location = (source.location.lat, source.location.lon)
            popup_html = f"""
            <b>{source.supplier_id}</b><br/>
            Capacity: {source.capacity_by_year[chosen_year]:,.2f} t
            """
            icon_color = "gray"
            icon_type = "industry"
        else:
            continue

        folium.Marker(
            location=location,
            popup=popup_html,
            icon=folium.Icon(icon=icon_type, color=icon_color, prefix="fa"),
        ).add_to(marker_fg)

    for destination in all_destinations:
        if isinstance(destination, tuple):
            plant, _ = destination
            location = (plant.location.lat, plant.location.lon)
            popup_html = f"""
            <b>Plant: {plant.plant_id}</b><br/>
            {get_plant_tech_str(plant)}
            """
            icon_color = "blue"
            icon_type = "industry"
        elif isinstance(destination, DemandCenter):
            location = (destination.center_of_gravity.lat, destination.center_of_gravity.lon)
            total_allocation_to_demand_center = sum(
                allocations_by_commodity[commodity].allocations[source][destination]
                for commodity in allocations_by_commodity
                for source in allocations_by_commodity[commodity].allocations
                if destination in allocations_by_commodity[commodity].allocations[source]
            )
            popup_html = f"""
            <b>Demand Center: {destination.demand_center_id}</b><br/>
            Demand: {destination.demand_by_year[chosen_year]:,.2f} t
            Allocated: {total_allocation_to_demand_center:,.2f} t
            """
            if total_allocation_to_demand_center >= destination.demand_by_year[chosen_year] - 1:
                icon_color = "green"
            else:
                icon_color = "red"
            icon_type = "cart-shopping"
        else:
            continue

        folium.Marker(
            location=location,
            popup=popup_html,
            icon=folium.Icon(icon=icon_type, color=icon_color, prefix="fa"),
        ).add_to(marker_fg)

    folium.LayerControl().add_to(fol_map)

    # Save the map if plot_paths is provided
    if plot_paths and plot_paths.tm_plots_dir:
        output_path = plot_paths.tm_plots_dir / f"steel_trade_allocations_{chosen_year}_folium.html"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fol_map.save(str(output_path))
        logger.info(f"✅ Folium trade map saved to: {output_path}")

    return fol_map


def plot_detailed_trade_map(
    allocations_by_commodity, chosen_year, plot_paths: Optional["PlotPaths"] = None, zoom_threshold=5
):
    arc_data_detailed = []
    arc_data_aggregated: dict[str, dict[tuple[tuple[float, float], tuple[float, float]], float]] = defaultdict(
        lambda: defaultdict(float)
    )
    node_data = {}
    # Dictionary to track nodes by coordinates (lat, lon) -> list of node info
    nodes_by_coords = defaultdict(list)

    commodity_color_map = {
        "steel": [0, 100, 255],  # strong blue
        "hot metal": [255, 85, 0],  # orange-red
        "dri_low": [200, 0, 0],  # dark red
        "dri_mid": [255, 40, 40],  # red
        "dri_high": [255, 90, 90],  # pink-red
        "pellets_high": [255, 215, 0],  # gold
        "pellets_mid": [240, 180, 0],  # mustard
        "pellets_low": [200, 140, 0],  # dark yellow
        "pig iron": [100, 100, 100],  # gray
        "sinter_low": [128, 64, 0],  # brown
        "sinter_mid": [160, 82, 45],  # sienna
        "sinter_high": [210, 105, 30],  # chocolate
        "liquid steel": [0, 200, 255],  # light blue
        "hbi_low": [102, 0, 204],  # indigo
        "hbi_mid": [153, 51, 255],  # violet
        "hbi_high": [204, 153, 255],  # light purple
        "scrap": [80, 80, 80],  # dark gray
        "io_high": [0, 153, 76],  # emerald green
        "io_mid": [0, 204, 102],  # mint
        "io_low": [153, 255, 204],  # pastel mint
    }

    def get_source_tech(source):
        if isinstance(source, tuple):
            _, fg = source
            return fg.technology.name or "unknown"
        elif hasattr(source, "commodity"):
            return f"{source.commodity}_supply"
        return "unknown"

    all_volumes = []

    # Filter out commodities with no allocations before processing
    commodities_with_allocations = {
        commodity: ca
        for commodity, ca in allocations_by_commodity.items()
        if ca.allocations and any(len(dests) > 0 for dests in ca.allocations.values())
    }

    for commodity, ca in commodities_with_allocations.items():
        print(f"Plotting commodity: {commodity}")
        for source, destinations in ca.allocations.items():
            # tech = get_source_tech(source)
            color = commodity_color_map.get(commodity, [0, 0, 255])

            # Extract source information
            if isinstance(source, tuple):
                plant, _ = source
                lat1, lon1 = plant.location.lat, plant.location.lon
                source_name = plant.plant_id
                source_country = plant.location.iso3
                coord_key = (lat1, lon1)
                nodes_by_coords[coord_key].append(
                    {"type": "plant", "id": plant.plant_id, "info": f"Plant: {plant.plant_id}"}
                )
            elif hasattr(source, "location"):
                lat1, lon1 = source.location.lat, source.location.lon
                source_name = source.supplier_id
                source_country = source.location.iso3
                coord_key = (lat1, lon1)
                capacity = source.capacity_by_year[chosen_year]
                nodes_by_coords[coord_key].append(
                    {
                        "type": "supplier",
                        "id": source.supplier_id,
                        "info": f"Supplier: {source.supplier_id}",
                        "capacity": capacity,
                    }
                )
            else:
                continue

            for dest, vol in destinations.items():
                # Extract destination information
                if isinstance(dest, tuple):
                    p, _ = dest
                    lat2, lon2 = p.location.lat, p.location.lon
                    dest_name = p.plant_id
                    dest_country = p.location.iso3
                    coord_key = (lat2, lon2)
                    nodes_by_coords[coord_key].append(
                        {"type": "plant", "id": p.plant_id, "info": f"Plant: {p.plant_id}"}
                    )
                elif hasattr(dest, "center_of_gravity"):
                    lat2, lon2 = dest.center_of_gravity.lat, dest.center_of_gravity.lon
                    dest_name = dest.demand_center_id
                    dest_country = dest.center_of_gravity.iso3
                    coord_key = (lat2, lon2)
                    allocated = sum(
                        allocations_by_commodity[c].allocations[s].get(dest, 0)
                        for c in allocations_by_commodity
                        for s in allocations_by_commodity[c].allocations
                    )
                    demand = dest.demand_by_year[chosen_year]
                    nodes_by_coords[coord_key].append(
                        {
                            "type": "demand",
                            "id": dest.demand_center_id,
                            "info": f"Demand Center: {dest.demand_center_id}",
                            "demand": demand,
                            "allocated": allocated,
                        }
                    )
                else:
                    continue

                cost = ca.allocation_costs[source][dest]

                # Create detailed tooltip with from/to information
                arc_tooltip = (
                    f"From {source_name} ({source_country}) to {dest_name} ({dest_country})\n"
                    f"Commodity: {commodity}\n"
                    f"Volume: {vol:,.0f} t\n"
                    f"Cost: ${cost:,.0f}"
                )

                arc_data_detailed.append(
                    {
                        "lat1": lat1,
                        "lon1": lon1,
                        "lat2": lat2,
                        "lon2": lon2,
                        "commodity": str(commodity),
                        "volume": float(vol),
                        "cost": float(cost),
                        "color": list(color),
                        "tooltip": arc_tooltip,
                    }
                )
                all_volumes.append(vol)

                src_cluster = (round(lat1, 1), round(lon1, 1))
                dst_cluster = (round(lat2, 1), round(lon2, 1))
                arc_data_aggregated[commodity][(src_cluster, dst_cluster)] += vol

    if not arc_data_detailed:
        raise ValueError("No data to plot")

    # Process collected nodes to combine those at the same coordinates
    for coord_key, node_list in nodes_by_coords.items():
        lat, lon = coord_key

        # Remove duplicates based on type and id
        unique_nodes = {}
        for node in node_list:
            node_key = (node["type"], node["id"])
            if node_key not in unique_nodes:
                unique_nodes[node_key] = node

        # Create combined tooltip if multiple unique nodes share coordinates
        if len(unique_nodes) > 1:
            # Group nodes by type
            suppliers = [n for n in unique_nodes.values() if n["type"] == "supplier"]
            demand_centers = [n for n in unique_nodes.values() if n["type"] == "demand"]
            plants = [n for n in unique_nodes.values() if n["type"] == "plant"]

            tooltip_parts = []

            # Add suppliers info
            if suppliers:
                total_capacity = sum(s.get("capacity", 0) for s in suppliers)
                if len(suppliers) == 1:
                    tooltip_parts.append(f"Supplier: {suppliers[0]['id']}\nCapacity: {total_capacity:,.0f} t")
                else:
                    supplier_ids = ", ".join(s["id"] for s in suppliers)
                    tooltip_parts.append(
                        f"Suppliers ({len(suppliers)}): {supplier_ids}\nTotal Capacity: {total_capacity:,.0f} t"
                    )

            # Add demand centers info
            if demand_centers:
                total_demand = sum(d.get("demand", 0) for d in demand_centers)
                total_allocated = sum(d.get("allocated", 0) for d in demand_centers)
                if len(demand_centers) == 1:
                    tooltip_parts.append(
                        f"Demand Center: {demand_centers[0]['id']}\nDemand: {total_demand:,.0f} t\nAllocated: {total_allocated:,.0f} t"
                    )
                else:
                    dc_ids = ", ".join(d["id"] for d in demand_centers)
                    tooltip_parts.append(
                        f"Demand Centers ({len(demand_centers)}): {dc_ids}\nTotal Demand: {total_demand:,.0f} t\nTotal Allocated: {total_allocated:,.0f} t"
                    )

            # Add plants info
            if plants:
                if len(plants) == 1:
                    tooltip_parts.append(f"Plant: {plants[0]['id']}")
                else:
                    plant_ids = ", ".join(p["id"] for p in plants)
                    tooltip_parts.append(f"Plants ({len(plants)}): {plant_ids}")

            combined_tooltip = "\n\n".join(tooltip_parts)
            node_id = f"node_{lat}_{lon}".replace(".", "_").replace("-", "neg")
        else:
            # Single node at this location
            single_node = list(unique_nodes.values())[0]
            node_id = f"{single_node['type']}_{single_node['id']}"

            if single_node["type"] == "supplier":
                combined_tooltip = f"Supplier: {single_node['id']}\nCapacity: {single_node.get('capacity', 0):,.0f} t"
            elif single_node["type"] == "demand":
                combined_tooltip = f"Demand Center: {single_node['id']}\nDemand: {single_node.get('demand', 0):,.0f} t\nAllocated: {single_node.get('allocated', 0):,.0f} t"
            else:  # plant
                combined_tooltip = f"Plant: {single_node['id']}"

        node_data[node_id] = {
            "lat": lat,
            "lon": lon,
            "info": combined_tooltip,
            "tooltip": combined_tooltip,
            "node_count": len(unique_nodes),
        }

    arc_df = pd.DataFrame(arc_data_detailed)
    node_df = pd.DataFrame.from_dict(node_data, orient="index").reset_index(drop=True)
    max_vol = max(all_volumes)

    arc_df["width"] = arc_df["volume"] / max_vol * 10
    arc_df["commodity_id"] = arc_df["commodity"].astype("category").cat.codes.astype(int)

    arc_df_agg = []
    for commodity, flows in arc_data_aggregated.items():
        color = commodity_color_map.get(commodity, [0, 0, 255])
        for (src, dst), vol in flows.items():
            arc_df_agg.append(
                {
                    "lat1": src[0],
                    "lon1": src[1],
                    "lat2": dst[0],
                    "lon2": dst[1],
                    "commodity": str(commodity),
                    "volume": float(vol),
                    "color": list(color),
                    "width": float(vol / max_vol * 10),
                    "tooltip": f"Aggregated flow\nCommodity: {commodity}\nVolume: {vol:,.0f} t",
                }
            )

    df_agg = pd.DataFrame(arc_df_agg)
    df_agg["commodity_id"] = df_agg["commodity"].astype("category").cat.codes

    view_state = pdk.ViewState(latitude=arc_df["lat1"].mean(), longitude=arc_df["lon1"].mean(), zoom=zoom_threshold - 1)

    show_detailed = view_state.zoom >= zoom_threshold
    arc_layer = pdk.Layer(
        "ArcLayer",
        data=arc_df if show_detailed else df_agg,
        get_source_position=["lon1", "lat1"],
        get_target_position=["lon2", "lat2"],
        get_source_color="color",
        get_target_color="color",
        get_width="width",
        pickable=True,
        auto_highlight=True,
        get_filter_value="commodity_id",
        filter_range=[0, arc_df["commodity_id"].max()],
    )

    node_layer = pdk.Layer(
        "ScatterplotLayer",
        data=node_df,
        get_position=["lon", "lat"],
        get_fill_color=[100, 100, 100],
        get_radius=20000,
        pickable=True,
    )

    def validate_df(df, name):
        for col in df.columns:
            sample = df[col].iloc[0]
            if isinstance(sample, object) and not isinstance(sample, (str, int, float, list, dict)):
                print(f"[WARNING] Column '{col}' has non-serializable object of type {type(sample)}:")
                print(sample)

    validate_df(arc_df, "arc_df")
    validate_df(node_df, "node_df")

    # Convert all non-numeric columns to primitive types to ensure serializability
    # This fixes issues with pydeck serialization in standalone environments
    for col in arc_df.columns:
        if col in ["lat1", "lon1", "lat2", "lon2", "volume", "cost", "width", "commodity_id"]:
            # Ensure these are float/int
            if col in ["commodity_id"]:
                arc_df[col] = arc_df[col].astype(int)
            else:
                arc_df[col] = arc_df[col].astype(float)
        elif col == "color":
            # Ensure color is a list of integers
            arc_df[col] = arc_df[col].apply(lambda x: [int(c) for c in x] if isinstance(x, (list, tuple)) else x)
        else:
            # Convert everything else to string
            arc_df[col] = arc_df[col].astype(str)

    for col in node_df.columns:
        if col in ["lat", "lon"]:
            # Ensure these are float
            node_df[col] = node_df[col].astype(float)
        else:
            # Convert everything else to string
            node_df[col] = node_df[col].astype(str)

    # Convert dataframes to dict for pydeck to avoid serialization issues
    arc_data_for_deck = arc_df.to_dict("records")
    node_data_for_deck = node_df.to_dict("records")

    # Recreate layers with dict data instead of dataframes
    arc_layer = pdk.Layer(
        "ArcLayer",
        data=arc_data_for_deck if show_detailed else df_agg.to_dict("records"),
        get_source_position=["lon1", "lat1"],
        get_target_position=["lon2", "lat2"],
        get_source_color="color",
        get_target_color="color",
        get_width="width",
        pickable=True,
        auto_highlight=True,
    )

    node_layer = pdk.Layer(
        "ScatterplotLayer",
        data=node_data_for_deck,
        get_position=["lon", "lat"],
        get_fill_color=[100, 100, 100],
        get_radius=20000,
        pickable=True,
    )

    # Create deck with explicit tooltip
    deck = pdk.Deck(layers=[arc_layer, node_layer], initial_view_state=view_state, tooltip={"text": "{tooltip}"})

    # Ensure output directory exists
    if plot_paths and plot_paths.tm_plots_dir:
        output_path = plot_paths.tm_plots_dir
    else:
        raise ValueError("plot_paths with tm_plots_dir must be provided")
    output_path.mkdir(parents=True, exist_ok=True)

    # Copy deck.gl to output directory for standalone HTML files (file:// protocol)
    # This allows the HTML to work when opened directly from disk, not just when served by Django
    deckgl_path = _copy_deckgl_to_output_dir(output_path)
    if deckgl_path is None:
        # Fallback to CDN if vendor file not available (requires internet)
        deckgl_path = "https://unpkg.com/deck.gl@8.9.35/dist.min.js"

    # Always use custom HTML generation for commodity filtering support
    generate_custom_html = True

    if not generate_custom_html:
        try:
            deck.to_html(output_path / f"steel_trade_allocations_{chosen_year}_pydeck.html")
        except Exception as e:
            print("=" * 80)
            print("ERROR: Failed to serialize deck to HTML")
            print(f"Error type: {type(e).__name__}")
            print(f"Error message: {e}")
            print("=" * 80)
            generate_custom_html = True

    if generate_custom_html:
        print("\nGenerating interactive HTML with commodity filter...")

        try:
            # Convert view state to dict
            view_state_dict = {
                "latitude": float(arc_df["lat1"].mean()),
                "longitude": float(arc_df["lon1"].mean()),
                "zoom": int(zoom_threshold - 1),
                "pitch": 0,
                "bearing": 0,
            }

            # Generate HTML manually with commodity filter
            import json

            # Get unique commodities for the filter
            unique_commodities = sorted(arc_df["commodity"].unique())

            # Check if we have any commodities with allocations
            if not unique_commodities:
                raise ValueError("No commodities with allocations found in the data")

            html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Steel Trade Allocations {chosen_year}</title>
    <!-- Deck.gl: copied to output dir for standalone HTML (file:// protocol support) -->
    <script src="{deckgl_path}"></script>

    <!-- Mapbox GL JS - EXCEPTION: stays on CDN due to licensing constraints -->
    <!-- DO NOT vendor this library - see specs/2025-10-13_no_cdn.md -->
    <script src="https://unpkg.com/mapbox-gl@2.15.0/dist/mapbox-gl.js"></script>
    <link href="https://unpkg.com/mapbox-gl@2.15.0/dist/mapbox-gl.css" rel="stylesheet" />
    <style>
        body {{
            margin: 0;
            padding: 0;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', 'Helvetica Neue', Arial, sans-serif;
        }}
        #container {{
            width: 100vw;
            height: 100vh;
            position: relative;
        }}
        #controls {{
            position: absolute;
            top: 10px;
            right: 10px;
            background: rgba(40, 40, 40, 0.9);
            padding: 12px 15px;
            border-radius: 4px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.5);
            z-index: 1;
            pointer-events: auto;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', 'Helvetica Neue', Arial, sans-serif;
            min-width: 280px;
            max-width: 350px;
        }}
        .control-header {{
            margin-bottom: 10px;
            font-weight: 500;
            color: #e0e0e0;
            font-size: 13px;
            letter-spacing: 0.3px;
        }}
        .select-buttons {{
            margin: 5px 0 10px 0;
        }}
        .select-btn {{
            padding: 4px 8px;
            font-size: 12px;
            background: rgba(80, 120, 200, 0.8);
            color: white;
            border: none;
            border-radius: 3px;
            cursor: pointer;
            margin-right: 5px;
        }}
        .select-btn:hover {{
            background: rgba(100, 140, 220, 0.9);
        }}
        .commodity-toggles {{
            display: flex;
            flex-direction: column;
            gap: 4px;
        }}
        .commodity-toggle {{
            display: flex;
            align-items: center;
            padding: 6px 8px;
            border-radius: 3px;
            cursor: pointer;
            transition: background-color 0.2s ease;
            user-select: none;
        }}
        .commodity-toggle:hover {{
            background: rgba(60, 60, 60, 0.5);
        }}
        .commodity-toggle.active {{
            background: rgba(80, 120, 200, 0.3);
            border-left: 3px solid rgba(80, 120, 200, 0.8);
        }}
        .commodity-toggle.active:hover {{
            background: rgba(80, 120, 200, 0.4);
        }}
        .commodity-checkbox {{
            width: 16px;
            height: 16px;
            margin-right: 8px;
            border: 2px solid rgba(120, 120, 120, 0.6);
            border-radius: 3px;
            background: transparent;
            position: relative;
            flex-shrink: 0;
        }}
        .commodity-toggle.active .commodity-checkbox {{
            background: rgba(80, 120, 200, 0.8);
            border-color: rgba(80, 120, 200, 0.8);
        }}
        .commodity-checkbox::after {{
            content: '✓';
            position: absolute;
            top: -2px;
            left: 2px;
            color: white;
            font-size: 12px;
            font-weight: bold;
            opacity: 0;
            transition: opacity 0.2s ease;
        }}
        .commodity-toggle.active .commodity-checkbox::after {{
            opacity: 1;
        }}
        .commodity-label {{
            color: #e0e0e0;
            font-size: 13px;
            flex: 1;
        }}
        .commodity-toggle.active .commodity-label {{
            color: #f0f0f0;
            font-weight: 500;
        }}
        label {{
            font-weight: 500;
            margin-right: 10px;
            color: #e0e0e0;
            font-size: 13px;
            letter-spacing: 0.3px;
        }}
        .mapboxgl-map {{
            position: absolute;
            top: 0;
            bottom: 0;
            width: 100%;
        }}
        /* Style the deck.gl tooltip */
        .deck-tooltip {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', 'Helvetica Neue', Arial, sans-serif !important;
            font-size: 12px !important;
            background: rgba(40, 40, 40, 0.95) !important;
            color: #e0e0e0 !important;
            padding: 8px 12px !important;
            border-radius: 4px !important;
            box-shadow: 0 2px 4px rgba(0,0,0,0.3) !important;
            border: 1px solid rgba(100, 100, 100, 0.3) !important;
            max-width: 300px !important;
            line-height: 1.4 !important;
            white-space: pre-line !important;
        }}
    </style>
</head>
<body>
    <div id="container">
        <div id="controls">
            <div class="control-header">Filter by Commodity:</div>
            <div class="select-buttons">
                <button class="select-btn" onclick="selectAll()">All</button>
                <button class="select-btn" onclick="selectNone()">None</button>
            </div>
            <div class="commodity-toggles" id="commodity-toggles">
                {chr(10).join(f'                <div class="commodity-toggle active" data-commodity="{comm}" onclick="toggleCommodity(&quot;{comm}&quot;)" title="Click to toggle {comm}"><div class="commodity-checkbox"></div><div class="commodity-label">{comm}</div></div>' for comm in unique_commodities)}
            </div>
        </div>
    </div>
    <script>
        // Store all arc data
        // Always use detailed arc data for the interactive map
        const allArcData = {json.dumps(arc_data_for_deck)};
        const nodeData = {json.dumps(node_data_for_deck)};

        // Initialize view state
        const INITIAL_VIEW_STATE = {{
            latitude: {view_state_dict["latitude"]},
            longitude: {view_state_dict["longitude"]},
            zoom: {view_state_dict["zoom"]},
            pitch: 0,
            bearing: 0
        }};

        let deckgl;

        // Make functions available globally for onclick handlers
        window.toggleCommodity = function(commodity) {{
            console.log('toggleCommodity called with:', commodity);
            // Use a safer selector approach
            const toggles = document.querySelectorAll('.commodity-toggle');
            let toggle = null;
            for (let i = 0; i < toggles.length; i++) {{
                if (toggles[i].dataset.commodity === commodity) {{
                    toggle = toggles[i];
                    break;
                }}
            }}
            console.log('Found toggle element:', toggle);
            if (toggle) {{
                toggle.classList.toggle('active');
                const selectedCommodities = getSelectedCommodities();
                console.log('Selected commodities after toggle:', selectedCommodities);
                updateDeck(selectedCommodities);
            }} else {{
                console.error('Toggle element not found for commodity:', commodity);
                console.log('Available toggles:', Array.from(toggles).map(t => t.dataset.commodity));
            }}
        }};

        window.selectAll = function() {{
            console.log('selectAll called');
            const toggles = document.querySelectorAll('.commodity-toggle');
            toggles.forEach(toggle => toggle.classList.add('active'));
            updateDeck(getSelectedCommodities());
        }};

        window.selectNone = function() {{
            console.log('selectNone called');
            const toggles = document.querySelectorAll('.commodity-toggle');
            toggles.forEach(toggle => toggle.classList.remove('active'));
            updateDeck([]);
        }};

        // Create function to create/update layers
        function createLayers(selectedCommodities) {{
            console.log('createLayers called with:', selectedCommodities);
            console.log('Total arc data length:', allArcData.length);

            let filteredArcData = allArcData;

            if (selectedCommodities && selectedCommodities.length > 0) {{
                filteredArcData = allArcData.filter(arc => selectedCommodities.includes(arc.commodity));
                console.log('Filtered arc data length:', filteredArcData.length);
            }} else {{
                console.log('No commodities selected, showing empty data');
                filteredArcData = [];
            }}

            return [
                new deck.ArcLayer({{
                    id: 'arc-layer',
                    data: filteredArcData,
                    getSourcePosition: d => [d.lon1, d.lat1],
                    getTargetPosition: d => [d.lon2, d.lat2],
                    getSourceColor: d => d.color || [0, 100, 255],
                    getTargetColor: d => d.color || [0, 100, 255],
                    getWidth: d => d.width || 1,
                    pickable: true,
                    autoHighlight: true
                }}),
                new deck.ScatterplotLayer({{
                    id: 'node-layer',
                    data: nodeData,
                    getPosition: d => [parseFloat(d.lon), parseFloat(d.lat)],
                    getFillColor: d => {{
                        // Use different colors based on node count
                        const nodeCount = d.node_count || 1;
                        if (nodeCount > 1) {{
                            // Orange for combined nodes
                            return [255, 150, 50, 220];
                        }} else {{
                            // Gray for single nodes
                            return [100, 100, 100, 200];
                        }}
                    }},
                    getRadius: d => {{
                        // Larger radius for combined nodes
                        const nodeCount = d.node_count || 1;
                        return nodeCount > 1 ? 25000 : 20000;
                    }},
                    pickable: true,
                    filled: true,
                    stroked: true,
                    getLineColor: [255, 255, 255, 100],
                    lineWidthMinPixels: 1
                }})
            ];
        }}

        // Update function for filter changes
        function updateDeck(selectedCommodities) {{
            console.log('updateDeck called with:', selectedCommodities);
            if (deckgl) {{
                console.log('Updating deck layers...');
                deckgl.setProps({{
                    layers: createLayers(selectedCommodities)
                }});
                console.log('Deck layers updated');
            }} else {{
                console.error('deckgl not initialized');
            }}
        }}

        // Helper function to get selected commodities
        function getSelectedCommodities() {{
            const toggles = document.querySelectorAll('.commodity-toggle.active');
            return Array.from(toggles).map(toggle => toggle.dataset.commodity);
        }}


        // Initialize on page load
        window.addEventListener('DOMContentLoaded', function() {{
            console.log('DOM loaded, initializing...');

            // Check if toggles are present
            const toggles = document.querySelectorAll('.commodity-toggle');
            console.log('Found toggles:', toggles.length);
            toggles.forEach((toggle, index) => {{
                console.log(`Toggle ${{index}}: commodity="${{toggle.dataset.commodity}}", active=${{toggle.classList.contains('active')}}`);
            }});

            // Create deck with dark CartoDB tiles
            deckgl = new deck.DeckGL({{
                container: 'container',
                mapStyle: {{
                    version: 8,
                    sources: {{
                        'carto-dark': {{
                            type: 'raster',
                            tiles: [
                                'https://a.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}.png',
                                'https://b.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}.png',
                                'https://c.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}.png'
                            ],
                            tileSize: 256,
                            attribution: '© OpenStreetMap contributors © CARTO'
                        }}
                    }},
                    layers: [{{
                        id: 'carto-dark-layer',
                        type: 'raster',
                        source: 'carto-dark',
                        minzoom: 0,
                        maxzoom: 19
                    }}]
                }},
                initialViewState: INITIAL_VIEW_STATE,
                controller: true,
                layers: createLayers(getSelectedCommodities()),
                getTooltip: ({{object}}) => object && (object.tooltip || object.info)
            }});

            // Event listeners are handled by onclick attributes on toggle elements
        }});
    </script>
</body>
</html>"""

            output_file = output_path / f"steel_trade_allocations_{chosen_year}_pydeck.html"
            with open(output_file, "w") as f:
                f.write(html_content)

            print(f"Fallback HTML generated successfully: {output_file}")

        except Exception as fallback_error:
            print(f"\nFallback also failed: {fallback_error}")
            print("\nDEBUG: arc_df info:")
            print(f"Shape: {arc_df.shape}")
            print(f"Columns: {list(arc_df.columns)}")
            print("\nDEBUG: node_df info:")
            print(f"Shape: {node_df.shape}")
            print(f"Columns: {list(node_df.columns)}")

            # Save data as CSV as last resort
            arc_df.to_csv(output_path / f"steel_trade_allocations_{chosen_year}_arcs.csv", index=False)
            node_df.to_csv(output_path / f"steel_trade_allocations_{chosen_year}_nodes.csv", index=False)
            print(f"\nData saved as CSV files in {output_path}")

        print("=" * 80)


def plot_cost_curve_for_commodity(cost_curve: list, total_demand: float, image_path: str = "cost_curve.png") -> Figure:
    """
    Plots and saves the cost curve to the specified path.

    Parameters:
    - cost_curve: list of dicts with 'cumulative_capacity' and 'production_cost'
    - demand: float or numeric volume representing total demand
    - path: file path (including .png) where the plot should be saved
    """

    # Prepare x and y data for plotting
    x_vals = []
    y_vals = []

    for point in cost_curve:
        # assuming cumulative_capacity can be a Volumes object, try extracting numeric value
        try:
            cap = float(point["cumulative_capacity"])
        except (TypeError, ValueError):
            cap = (
                point["cumulative_capacity"].value
                if hasattr(point["cumulative_capacity"], "value")
                else point["cumulative_capacity"]
            )

        x_vals.append(cap)
        y_vals.append(point["production_cost"])

    # Create the plot
    fig = plt.figure(figsize=(10, 6))
    plt.step(x_vals, y_vals, where="post", label="Cost Curve", linewidth=2)

    # Add demand line
    plt.axvline(x=total_demand, color="red", linestyle="--", label="Total Demand")

    # Labeling
    plt.xlabel("Cumulative Capacity")
    plt.ylabel("Production Cost")
    plt.title("Cost Curve")
    plt.legend()
    plt.grid(True)
    plt.tight_layout()

    # Save to file
    plt.savefig(image_path, format="png")
    plt.close()
    return fig  # noqa


def plot_screenshot(
    data,
    var=None,
    title=None,
    var_type="diverging",
    max_val=None,
    min_val=None,
    save_name=None,
    plot_paths: Optional["PlotPaths"] = None,
    show=False,
):
    # Select variable to plot
    if var:
        data_to_plot = data[var]
    else:
        data_to_plot = data

    # Check if all data is NaN/masked - skip plotting if so
    if data_to_plot.isnull().all():
        logger.warning(
            f"[PLOTTING] Skipping plot '{save_name or title or 'unnamed'}' - all data is NaN/masked. "
            f"This may indicate invalid input data or over-aggressive masking."
        )
        return

    # Initialize plot
    lat_lon_ratio = len(data.lat) / len(data.lon)
    fig = plt.figure(figsize=(10, 10 * lat_lon_ratio))
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree())
    ax.set_extent(  # type: ignore[attr-defined]
        [
            data.coords["lon"].min(),
            data.coords["lon"].max(),
            data.coords["lat"].min(),
            data.coords["lat"].max(),
        ],
        crs=ccrs.PlateCarree(),
    )

    # Select the colormap
    if "sequential" in var_type:
        mycmap = "viridis"
    elif "diverging" in var_type:
        mycmap = "coolwarm"
    if "_r" in var_type:
        mycmap = mycmap + "_r"
    if "binary" in var_type:
        mycmap = "Greens"

    # Set color scale limits
    ## Calculate actual min/max from data
    actual_min = data_to_plot.min().item()
    actual_max = data_to_plot.max().item()
    ## Handle edge case where all values are identical
    if actual_min == actual_max:
        logger.warning(
            f"[PLOTTING] All values are identical ({actual_min:.2f}) for '{save_name or title or 'unnamed'}'. "
            "Using small range around the value for plotting."
        )
        vmin = actual_min - 0.5
        vmax = actual_max + 0.5
    else:
        vmin = min_val if min_val is not None else actual_min
        vmax = max_val if max_val is not None else actual_max
        ## Swap if min >= max
        if vmin >= vmax:
            logger.warning(f"[PLOTTING] Invalid color scale: vmin ({vmin:.2f}) >= vmax ({vmax:.2f}). Swapping.")
            vmin, vmax = vmax, vmin

    # Plot
    data_to_plot.plot(
        ax=ax,
        transform=ccrs.PlateCarree(),
        cmap=mycmap,
        cbar_kwargs={"shrink": 0.4},
        vmin=vmin,
        vmax=vmax,
    )
    ax.add_feature(cfeature.COASTLINE)  # type: ignore[attr-defined]
    ax.add_feature(cfeature.BORDERS, linestyle=":")  # type: ignore[attr-defined]
    if title:
        plt.title(title)
    if save_name:
        # Ensure the directory exists before saving
        if plot_paths is None or plot_paths.geo_plots_dir is None:
            raise ValueError("plot_paths with geo_plots_dir must be provided when save_name is specified")
        geo_plots_dir = plot_paths.geo_plots_dir
        geo_plots_dir.mkdir(parents=True, exist_ok=True)
        plt.savefig(geo_plots_dir / f"{save_name}.png", dpi=300)
    if show:
        plt.show()
    plt.close()


def plot_landtype(data, plot_paths: "PlotPaths", var=None, title=None, save_name=None):
    # Initialize plot
    lat_lon_ratio = len(data.lat) / len(data.lon)
    fig = plt.figure(figsize=(10, 10 * lat_lon_ratio))
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree())
    ax.set_extent(  # type: ignore[attr-defined]
        [
            data.coords["lon"].min(),
            data.coords["lon"].max(),
            data.coords["lat"].min(),
            data.coords["lat"].max(),
        ],
        crs=ccrs.PlateCarree(),
    )

    # Select variable to plot
    if var:
        data_to_plot = data[var]
    else:
        data_to_plot = data

    # Plot
    # Create a mapping of values to grouped labels
    value_to_group = {value: label for label, values in LULC_LABELS_TO_NUM.items() for value in values}

    # Map the data to grouped labels
    grouped_data = np.vectorize(value_to_group.get)(data_to_plot.values)

    # Get unique grouped labels and assign colors
    unique_grouped_labels = list(LULC_LABELS_TO_NUM.keys())
    grouped_cmap = plt.cm.get_cmap("tab20", len(unique_grouped_labels))

    # Plot
    # Map grouped_data (categories) to numerical indices
    # categories = np.unique(grouped_data.astype(str))
    category_to_index = {category: idx for idx, category in enumerate(unique_grouped_labels) if category is not None}
    numerical_data = np.vectorize(lambda x: category_to_index.get(x, 0))(grouped_data)

    # Plot the numerical data
    im = ax.imshow(
        numerical_data,
        transform=ccrs.PlateCarree(),
        cmap=grouped_cmap,
        extent=(
            float(data.coords["lon"].min()),
            float(data.coords["lon"].max()),
            float(data.coords["lat"].max()),
            float(data.coords["lat"].min()),
        ),
        interpolation="nearest",
    )

    # Create a custom colorbar
    cbar = fig.colorbar(im, ax=ax, orientation="vertical", shrink=1.0, pad=0.1)
    cbar.set_ticks(range(len(unique_grouped_labels)))
    cbar.set_ticklabels(unique_grouped_labels)

    ax.add_feature(cfeature.COASTLINE)  # type: ignore[attr-defined]
    ax.add_feature(cfeature.BORDERS, linestyle=":")  # type: ignore[attr-defined]
    if title:
        plt.title(title)
    if save_name:
        # Ensure the directory exists before saving
        if plot_paths is None or plot_paths.geo_plots_dir is None:
            raise ValueError("plot_paths with geo_plots_dir must be provided when save_name is specified")
        geo_plots_dir = plot_paths.geo_plots_dir
        geo_plots_dir.mkdir(parents=True, exist_ok=True)
        plt.savefig(geo_plots_dir / f"{save_name}.png", dpi=300)
    plt.close()


def plot_bubble_map(
    data: dict[Location, float | int | Volumes],
    bubble_scaler: float | None = None,
    unit: str | None = None,
    title: str | None = None,
    save_name: str | None = None,
    plot_paths: Optional["PlotPaths"] = None,
    show: bool = False,
):
    """
    Plots a bubble map where the size of each bubble corresponds to the weight at that location.
    """
    # Initialize plot
    fig = plt.figure(figsize=(10, 6))
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree())

    # Plot a bubble at each location, size proportional to weight
    for loc, weight in data.items():
        ax.scatter(
            loc.lon,
            loc.lat,
            s=weight * bubble_scaler if bubble_scaler else weight,
            alpha=0.5,
            transform=ccrs.PlateCarree(),
        )

    # Add coastlines and borders
    ax.add_feature(cfeature.COASTLINE)  # type: ignore[attr-defined]
    ax.add_feature(cfeature.BORDERS, linestyle=":")  # type: ignore[attr-defined]

    # Add title and save/show the plot
    plt.title(title if title else "Bubble Map")
    if save_name:
        if plot_paths is None or plot_paths.geo_plots_dir is None:
            raise ValueError("plot_paths with geo_plots_dir must be provided when save_name is specified")
        geo_plots_dir = plot_paths.geo_plots_dir
        geo_plots_dir.mkdir(parents=True, exist_ok=True)
        plt.savefig(geo_plots_dir / f"{save_name}.png", dpi=300)
    if show:
        plt.show()
    plt.close()


def plot_value_histogram(
    ds, var_name=None, bins=50, threshold=None, log_scale=False, plot_paths: Optional["PlotPaths"] = None
) -> None:
    """
    Plots a histogram of the values in the selected xarray variable.

    Parameters:
    - ds (xarray.Dataset): The dataset containing the variable.
    - var_name (str, optional): Name of the variable. Defaults to the first in the dataset.
    - bins (int or array): Number of histogram bins or array of bin edges.
    - threshold (float, optional): If provided, adds a vertical line for threshold.
    - log_scale (bool): Whether to use logarithmic y-axis.
    """
    if var_name is None:
        var_name = list(ds.data_vars)[0]

    da = ds[var_name]

    # Flatten the data and remove NaNs
    values = da.values.flatten()
    values = values[np.isfinite(values)]
    values = values[values >= 0.01]  # Filter out negative values

    # Plot histogram
    plt.figure(figsize=(8, 5))
    plt.hist(values, bins=bins, color="skyblue", edgecolor="black")

    if threshold is not None:
        plt.axvline(threshold, color="red", linestyle="--", linewidth=2, label=f"Threshold = {threshold}")

    if log_scale:
        plt.yscale("log")

    plt.xlabel(f"Values of '{var_name}'")
    plt.ylabel("Frequency")
    if log_scale:
        plt.title(f"Logarithmic Histogram of '{var_name}'")
        plt.ylabel("Log Frequency")
    else:
        plt.title(f"Histogram of '{var_name}'")
    if threshold is not None:
        plt.legend()
    plt.grid(True, linestyle="--", alpha=0.6)
    plt.tight_layout()

    # Ensure the directory exists before saving
    if plot_paths is None or plot_paths.geo_plots_dir is None:
        raise ValueError("plot_paths with geo_plots_dir must be provided when saving plots")
    geo_plots_dir = plot_paths.geo_plots_dir
    geo_plots_dir.mkdir(parents=True, exist_ok=True)
    if log_scale:
        plt.savefig(geo_plots_dir / f"{var_name}_log_hist.png", dpi=300)
    else:
        plt.savefig(geo_plots_dir / f"{var_name}_hist.png", dpi=300)
    plt.close()


def plot_global_grid_with_iso3(grid, plot_paths: "PlotPaths") -> None:
    # Visualize the global grid with ISO3 codes
    plt.figure(figsize=(10, 5))
    ax = plt.axes(projection=ccrs.PlateCarree())  # type: ignore[call-arg]
    ax.set_extent([-180, 180, -90, 90], crs=ccrs.PlateCarree())  # type: ignore[attr-defined]
    ax.add_feature(cfeature.COASTLINE)  # type: ignore[attr-defined]
    ax.add_feature(cfeature.BORDERS, linestyle=":")  # type: ignore[attr-defined]
    lon, lat = np.meshgrid(grid["lon"].values, grid["lat"].values)
    iso3_values = grid["iso3"].values.flatten()
    unique_iso3 = np.unique(iso3_values[(~pd.isnull(iso3_values)) & (iso3_values != "nan")])  # Exclude NaN values
    iso3_to_color = {iso3: idx for idx, iso3 in enumerate(unique_iso3)}
    colors = np.array([iso3_to_color.get(iso3, np.nan) for iso3 in iso3_values])
    plt.scatter(
        lon.flatten(),
        lat.flatten(),
        c=colors,
        cmap="tab20",
        s=1,
    )
    plt.colorbar(label="ISO3 Codes")
    plt.title("Global Grid with ISO3 Codes")
    # Ensure the directory exists before saving
    if plot_paths is None or plot_paths.geo_plots_dir is None:
        raise ValueError("plot_paths with geo_plots_dir must be provided when saving plots")
    geo_plots_dir = plot_paths.geo_plots_dir
    geo_plots_dir.mkdir(parents=True, exist_ok=True)
    plt.savefig(geo_plots_dir / "global_grid_with_iso3.png", dpi=300)
    plt.close()


def plot_area_chart_of_column_by_region_or_technology(
    dataframe: pd.DataFrame,
    column_name: str,
    title: str,
    units: str,
    pivot_columns: list[str] = [],
    product_type="steel",
    plot_paths: Optional["PlotPaths"] = None,
) -> Figure:
    """
    Plots an area chart of the specified column grouped by region.

    :param dataframe: DataFrame containing the data to plot.
    :param column_name: Name of the column to plot.
    :param title: Title of the plot.
    :return: Matplotlib Figure object.
    """
    dataframe = dataframe.copy()
    fig, ax = plt.subplots(figsize=(10, 6))

    # dataframe[column_name] = dataframe[column_name]  # convert t to kt

    # Pivot to get a column for each (Technology, Source)
    dataframe = dataframe.drop_duplicates(subset=["furnace_group_id", "year"]).copy()
    dataframe = dataframe[dataframe["product"] == product_type]

    df_pivot = dataframe.pivot_table(index="year", columns=pivot_columns, values=column_name, aggfunc="sum").fillna(0)

    # Optional: flatten the multi-index columns
    # df_pivot.columns = [f"{tech}_{source}" for tech, source in df_pivot.columns]
    if pivot_columns:
        legend_title = "_".join(pivot_columns)
    else:
        legend_title = ""

    # Plot as stacked area chart
    if pivot_columns == ["region"]:
        colour_map = region2colours
    elif pivot_columns == ["technology"]:
        colour_map = tech2colours
    df_pivot.plot(ax=ax, kind="area", stacked=True, figsize=(12, 6), color=colour_map)

    ax.set_title(title)
    ax.set_xlabel("Year")
    ax.set_ylabel(f"{column_name} [{units}]")
    ax.legend(title=legend_title)
    if pivot_columns == ["region"]:
        handles, labels = plt.gca().get_legend_handles_labels()
        for handle, label in zip(handles, labels):
            color = colour_map.get(label, "#CCCCCC")
            if hasattr(handle, "set_facecolor"):
                handle.set_facecolor(color)
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    if not df_pivot.index.empty:
        ax.set_xlim(df_pivot.index.min(), df_pivot.index.max())
    ax.grid(True)
    fig.tight_layout()

    # Use provided paths or fall back to module-level defaults
    if plot_paths is None or plot_paths.pam_plots_dir is None:
        raise ValueError("plot_paths with pam_plots_dir must be provided when saving plots")
    pam_plots_dir = plot_paths.pam_plots_dir
    pam_plots_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(pam_plots_dir / f"{product_type}_{column_name}_development_by_{pivot_columns[0]}.png", dpi=300)
    plt.close()
    return fig


def plot_bar_chart_of_new_plants_by_status(status_counts, plot_paths: "PlotPaths"):
    """
    Plots a bar chart with the number of indi plants per year that are in each status.
    """
    if not status_counts:
        logger.warning("No status counts data available for new plants - skipping plant status bar chart generation")
        return

    for product, status_per_prod in status_counts.items():
        records = []
        for year, status_per_year in status_per_prod.items():
            status_totals: dict[str, float] = defaultdict(float)
            for _, statuses_per_tech in status_per_year.items():
                for status, count in statuses_per_tech.items():
                    status_totals[status] += count
            record = {"year": year}
            record.update(status_totals)
            records.append(record)
        if not records:
            logger.info(f"No new {product} plants found in any year - skipping {product} plant status chart")
            continue

        status_df = pd.DataFrame(records).set_index("year").fillna(0).astype(int)
        status_df = status_df.reindex(sorted(status_df.index), axis=0)
        status_colors = {
            "considered": "#a6cee3",
            "announced": "#1f78b4",
            "construction": "#f1dc1e",
            "operating": "#24851b",
            "operating pre-retirement": "#084302",
            "discarded": "#e31a1c",
            "closed": "#882626",
        }
        all_statuses = list(status_colors.keys())
        for s in status_df.columns:
            if s not in all_statuses:
                all_statuses.append(s)
        status_df = status_df.reindex(columns=all_statuses, fill_value=0)
        used_colors = [status_colors.get(s, "#cccccc") for s in status_df.columns]
        status_df.plot(kind="bar", stacked=True, figsize=(12, 6), color=used_colors)
        plt.title(f"New {product} plants")
        plt.xlabel("Year")
        plt.ylabel("Number of Plants")
        plt.xticks(rotation=45)
        plt.legend(title="Status")
        plt.tight_layout()
        # Ensure the directory exists before saving
        geo_plots_dir = plot_paths.geo_plots_dir
        if geo_plots_dir is None:
            raise ValueError("geo_plots_dir must be set in PlotPaths")
        geo_plots_dir.mkdir(parents=True, exist_ok=True)
        output_path = geo_plots_dir / f"new_{product}_plants_by_status.png"
        plt.savefig(output_path, dpi=300)
        plt.close()
        logger.info(f"Generated plant status chart for {product} at: {output_path}")


def plot_map_of_new_plants_operating(new_plant_locations, plot_paths: "PlotPaths"):
    """
    Plots the locations of new plants which just started operating.
    """
    if not new_plant_locations:
        logger.warning("No new plant location data available - skipping map generation")
        return

    for product, locations_per_year in new_plant_locations.items():
        if not locations_per_year:
            logger.info(f"No new {product} plant locations found - skipping map generation")
            continue

        # Check if there are any locations at all
        total_locations = sum(len(locs) for locs in locations_per_year.values())
        if total_locations == 0:
            logger.info(f"No new {product} plants operating in any year - skipping map generation")
            continue

        fig = plt.figure(figsize=(10, 6))
        ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree())
        ax.set_extent(  # type: ignore[attr-defined]
            [-180, 180, -90, 90],  # Global extent
            crs=ccrs.PlateCarree(),
        )
        # Prepare at least 25 distinct colors
        years = sorted(locations_per_year.keys())
        n_colors = max(25, len(years))
        color_map = cm.get_cmap("tab20", n_colors)
        year_to_color = {year: color_map(i % n_colors) for i, year in enumerate(years)}

        plotted_locations = set()
        for i, year in enumerate(years):
            locations = locations_per_year[year]
            if not locations:
                continue
            unique_locs = []
            for loc in locations:
                key = (loc["lat"], loc["lon"])
                if key not in plotted_locations:
                    unique_locs.append(loc)
                    plotted_locations.add(key)
            if unique_locs:
                lats = [loc["lat"] for loc in unique_locs]
                lons = [loc["lon"] for loc in unique_locs]
                plt.scatter(lons, lats, label=year, alpha=0.7, color=year_to_color[year])

        ax.add_feature(cfeature.COASTLINE)  # type: ignore[attr-defined]
        ax.add_feature(cfeature.BORDERS, linestyle=":")  # type: ignore[attr-defined]
        plt.title(f"Locations of new {product} plants")
        plt.xlabel("Longitude")
        plt.ylabel("Latitude")
        plt.legend(title="Operational start year", loc="lower left", ncol=2)
        plt.grid()
        plt.tight_layout()
        # Ensure the directory exists before saving
        geo_plots_dir = plot_paths.geo_plots_dir
        if geo_plots_dir is None:
            raise ValueError("geo_plots_dir must be set in PlotPaths")
        geo_plots_dir.mkdir(parents=True, exist_ok=True)
        output_path = geo_plots_dir / f"new_{product}_plants_map.png"
        plt.savefig(output_path, dpi=300)
        plt.close()
        logger.info(f"Generated new plant map for {product} at: {output_path}")


# Capacity addition by technology
def plot_added_capacity_by_technology(
    data_file: pd.DataFrame,
    units: str,
    plot_paths: Optional["PlotPaths"] = None,
):
    """
    Plots the added capacity by technology over the years.
    """
    data_file = data_file.copy()
    if "furnace_group_id" not in data_file.columns or "year" not in data_file.columns:
        raise ValueError("Output DataFrame must contain 'furnace_group_id' and 'year' columns.")

    if (
        "technology" not in data_file.columns
        or "capacity" not in data_file.columns
        or "product" not in data_file.columns
    ):
        raise ValueError("Output DataFrame must contain 'technology', 'capacity', and 'product' columns.")

    # Get unique technologies from data and ensure all have colors
    unique_techs = data_file["technology"].unique()
    for tech in unique_techs:
        if tech not in tech2colours:
            # Assign a default color for unknown technologies
            tech2colours[tech] = "brown"
            logger.warning(f"No color defined for technology '{tech}', using brown")

    fig, ax = plt.subplots(2, 1, figsize=(10, 12))
    added_capacity = (
        data_file.drop_duplicates(subset=["furnace_group_id", "year"])
        .groupby(["furnace_group_id", "technology"], as_index=False)[["year", "capacity", "product"]]
        .first()
    )
    by_product = added_capacity.groupby(["product", "year", "technology"])["capacity"].sum().unstack("technology")
    for i, product in enumerate(added_capacity["product"].unique()):
        product_data = by_product.loc[product]
        # Fill NaN with 0 for missing technologies in certain years
        product_data = product_data.fillna(0)
        # Remove columns (technologies) that are all zeros
        product_data = product_data.loc[:, (product_data != 0).any(axis=0)]

        if len(product_data) > 0:  # Plot if we have any data
            # Calculate the capacity added (difference from previous year)
            capacity_diff = product_data.diff()
            # Remove the first year (NaN from diff) as it's initial capacity
            capacity_diff = capacity_diff.iloc[1:]
            # Only keep positive additions (new capacity)
            capacity_diff = capacity_diff.clip(lower=0)
            # Remove rows where all values are 0
            capacity_diff = capacity_diff.loc[(capacity_diff != 0).any(axis=1)]

            if not capacity_diff.empty:
                capacity_diff.plot.bar(
                    stacked=True,
                    title=f"Added capacity by technology - {product}",
                    ylabel=f"Capacity [{units}]",
                    xlabel="Year",
                    figsize=(10, 6),
                    legend=True,
                    ax=ax[i],
                    color=tech2colours,
                )
            else:
                # Clear the subplot if no new capacity added
                ax[i].text(
                    0.5,
                    0.5,
                    f"No new capacity added for {product}",
                    ha="center",
                    va="center",
                    transform=ax[i].transAxes,
                )
                ax[i].set_title(f"Added capacity by technology - {product}")
                ax[i].set_xlabel("Year")
                ax[i].set_ylabel(f"Capacity [{units}]")
        else:
            # Clear the subplot if no data to plot
            ax[i].text(
                0.5,
                0.5,
                f"No data available for {product}",
                ha="center",
                va="center",
                transform=ax[i].transAxes,
            )
            ax[i].set_title(f"Added capacity by technology - {product}")
            ax[i].set_xlabel("Year")
            ax[i].set_ylabel(f"Capacity [{units}]")
    fig.tight_layout()
    if plot_paths is None or plot_paths.pam_plots_dir is None:
        raise ValueError("plot_paths with pam_plots_dir must be provided when saving plots")
    pam_plots_dir = plot_paths.pam_plots_dir
    pam_plots_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(pam_plots_dir / "year2year_added_capacity_by_technology.png", dpi=300)
    plt.close()


def plot_year_on_year_technology_development(
    data_file: pd.DataFrame,
    units: str,
    plot_paths: Optional["PlotPaths"] = None,
):
    """
    Plots the year-on-year development of technologies in terms of capacity.
    """
    data_file = data_file.copy()
    fig, ax = plt.subplots(figsize=(10, 6))

    if "furnace_group_id" not in data_file.columns or "year" not in data_file.columns:
        raise ValueError("Output DataFrame must contain 'furnace_group_id' and 'year' columns.")

    if (
        "technology" not in data_file.columns
        or "capacity" not in data_file.columns
        or "product" not in data_file.columns
    ):
        raise ValueError("Output DataFrame must contain 'technology', 'capacity', and 'product' columns.")

    # Define colors for each technology
    tech_color = {
        "DRI": "green",
        "BF": "grey",
        "EAF": "red",
        "BOF": "black",
        "ESF": "blue",
        "MOE": "pink",
        "other": "orange",
        # Combined technologies
        "BF-BOF": "darkgrey",
        "DRI-EAF": "darkgreen",
        "Scrap-EAF": "darkred",
        "H2-DRI": "lightgreen",
        "Innovative": "purple",
    }

    # Get unique technologies from data and ensure all have colors
    unique_techs = data_file["technology"].unique()
    for tech in unique_techs:
        if tech not in tech_color:
            # Assign a default color for unknown technologies
            tech_color[tech] = "brown"
            logger.warning(f"No color defined for technology '{tech}', using brown")

    # # year on year development by technology
    by_technology = (
        data_file.drop_duplicates(subset=["furnace_group_id", "year"])
        .groupby(["year", "technology"])["capacity"]
        .sum()
        .unstack("technology")
    )
    # Fill missing values with 0 to ensure all technologies appear in diff
    by_technology = by_technology.fillna(0)
    by_technology.diff().plot(
        ax=ax,
        kind="bar",
        stacked=False,
        title="Year on year development by technology",
        ylabel=f"Capacity [{units}]",
        xlabel="Year",
        figsize=(12, 6),
        legend=True,  # , ax = ax[1],
        color=tech_color,
    )

    if plot_paths is None or plot_paths.pam_plots_dir is None:
        raise ValueError("plot_paths with pam_plots_dir must be provided when saving plots")
    pam_plots_dir = plot_paths.pam_plots_dir
    pam_plots_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(pam_plots_dir / "Capacity_development_by_technology.png", dpi=300)
    plt.close()


def plot_cost_curve_with_breakdown(
    data_file,
    product_type,
    year,
    region2colours=region2colours,
    plot_paths: Optional["PlotPaths"] = None,
    show_breakdown: bool = True,
):
    """
    Plot cost curve with stacked bar breakdown showing cost components per furnace group.

    Args:
        data_file: DataFrame with furnace group data
        product_type: Type of product ('steel' or 'iron')
        year: Year to plot
        region2colours: Dictionary mapping regions to colors
        plot_paths: Optional plot paths configuration
        show_breakdown: If True, show cost breakdown as stacked bars
    """

    df = data_file.groupby(["furnace_group_id", "year"], as_index=False).first().set_index(["product", "year"])

    # Check if the requested year exists in the data
    available_years = df.index.get_level_values("year").unique()
    if year not in available_years:
        logger.warning(f"Year {year} not found in data. Available years: {sorted(available_years)}")
        # Use the closest available year
        year = max([y for y in available_years if y <= year] or [min(available_years)])
        logger.info(f"Using year {year} instead for cost curve plot")

    # Check if product_type exists
    if product_type not in df.index.get_level_values("product"):
        logger.warning(f"Product type '{product_type}' not found in data. Skipping cost curve plot.")
        return

    # Sort the index to avoid performance warning
    df = df.sort_index()

    # Get the data for the specific product and year
    demand = df.loc[product_type].loc[year].production.sum()

    # Prepare cost breakdown components
    component_columns = []

    if show_breakdown:
        # Extract cost components from the data

        # Check which cost columns are available
        logger.debug(f"[DEBUG] Available columns in df: {list(df.columns)}")
        if "cost_breakdown" in df.columns:
            # If we have the cost_breakdown dictionary column (nested by feedstock)
            cost_df = df.loc[(product_type, year)].copy()

            # Parse and flatten the cost_breakdown structure
            # cost_breakdown is a dict like: {"iron_ore": {"total_cost": X, "electricity": Y, ...}, ...}
            logger.debug(f"[DEBUG] cost_breakdown column exists, processing {len(cost_df)} rows")
            for idx, row in cost_df.iterrows():
                if pd.notna(row.get("cost_breakdown")) and isinstance(row["cost_breakdown"], dict):
                    logger.debug(f"[DEBUG] Processing cost_breakdown for {idx}: {row['cost_breakdown']}")
                    # Aggregate costs across all feedstocks
                    aggregated_costs = {}

                    for feedstock, costs in row["cost_breakdown"].items():
                        if isinstance(costs, dict):
                            for cost_type, value in costs.items():
                                if cost_type == "total_cost":
                                    # This is the material cost for this feedstock
                                    if "material_cost" not in aggregated_costs:
                                        aggregated_costs["material_cost"] = 0
                                    aggregated_costs["material_cost"] += value
                                elif cost_type not in ["demand", "unit_cost"]:  # Skip non-cost fields
                                    if cost_type not in aggregated_costs:
                                        aggregated_costs[cost_type] = 0
                                    aggregated_costs[cost_type] += value

                    # Add the aggregated costs as columns to the dataframe
                    for cost_type, value in aggregated_costs.items():
                        cost_df.at[idx, cost_type] = value

            # Now identify component columns from the flattened data
            if "material_cost" in cost_df.columns:
                component_columns.append("material_cost")

            # Handle columns with spaces in names
            for col in ["fixed opex", "carbon cost", "debt share"]:
                if col in cost_df.columns:
                    # Rename to use underscore for consistency
                    new_col = col.replace(" ", "_")
                    cost_df[new_col] = cost_df[col]
                    component_columns.append(new_col)

            # Energy columns
            energy_cols = [
                col
                for col in cost_df.columns
                if col in ["electricity", "coal", "natural gas", "natural_gas", "bf gas", "bof gas", "cog", "steam"]
            ]
            for col in energy_cols:
                if pd.api.types.is_numeric_dtype(cost_df[col]):
                    component_columns.append(col)
        else:
            # Build cost breakdown from available columns
            cost_df = df.loc[(product_type, year)].copy()

            # Add cost breakdown columns that start with "cost_breakdown - "
            breakdown_cols = [col for col in cost_df.columns if col.startswith("cost_breakdown - ")]
            breakdown_names = set()  # Track which cost types are covered by breakdown columns

            for col in breakdown_cols:
                # Clean up the column name for display
                clean_name = col.replace("cost_breakdown - ", "").strip()
                cost_df[clean_name] = cost_df[col]
                component_columns.append(clean_name)
                breakdown_names.add(clean_name)

            # Material costs - only add if not already covered by cost breakdown
            if "material_cost" not in breakdown_names:
                if "material_unit_cost" in cost_df.columns:
                    # Use unit cost instead of total cost for per-unit breakdown
                    cost_df["material_cost"] = cost_df["material_unit_cost"]
                    component_columns.append("material_cost")
                elif "material_cost" in cost_df.columns:
                    component_columns.append("material_cost")
                elif "material cost" in cost_df.columns:
                    # Rename to use underscore for consistency
                    cost_df["material_cost"] = cost_df["material cost"]
                    component_columns.append("material_cost")

            # Skip old energy processing since we now handle it via cost_breakdown columns

            # Fixed OPEX - only add if not already covered by cost breakdown
            if "unit_fopex" in cost_df.columns and "unit fopex" not in breakdown_names:
                component_columns.append("unit_fopex")

            # Carbon cost - only add if not already covered by cost breakdown
            if "carbon_cost" in cost_df.columns and "carbon cost" not in breakdown_names:
                component_columns.append("carbon_cost")
            elif "carbon_cost_per_unit" in cost_df.columns and "carbon cost" not in breakdown_names:
                # Calculate total carbon cost if we have per-unit values
                if "production" in cost_df.columns:
                    cost_df["carbon_cost"] = cost_df["carbon_cost_per_unit"] * cost_df["production"]
                    component_columns.append("carbon_cost")

            # Debt service - only add if not already covered by cost breakdown
            if "debt_share" in cost_df.columns and "debt share" not in breakdown_names:
                component_columns.append("debt_share")

            # Calculate total production cost as sum of components
            if component_columns:
                cost_df["production_cost"] = cost_df[component_columns].sum(axis=1, skipna=True)
            else:
                # Fallback to original calculation
                cost_df["production_cost"] = df["unit_production_cost"]
    else:
        # Original simple calculation
        df["production_cost"] = df["unit_production_cost"]
        cost_df = df.loc[(product_type, year)][["production_cost", "region", "capacity"]].copy()

    # Sort by production cost
    cost_df = cost_df.sort_values("production_cost")

    # Check if we have valid data
    if cost_df.empty:
        logger.warning(f"No data for {product_type} in year {year}. Skipping cost curve plot.")
        return

    if cost_df["production_cost"].isna().all():
        logger.warning(f"All production costs are NaN for {product_type} in year {year}. Skipping cost curve plot.")
        return

    # Remove rows with NaN production costs
    cost_df = cost_df.dropna(subset=["production_cost"])
    if cost_df.empty:
        logger.warning(f"No valid production costs for {product_type} in year {year}. Skipping cost curve plot.")
        return

    cost_df["clearing_capacity"] = cost_df["capacity"].cumsum()

    # Create the plot
    fig, ax = plt.subplots(figsize=(12, 7))
    cum_x = 0.0

    if show_breakdown and component_columns:
        # Define colors for each cost component
        component_colors = {
            "material_cost": "#8B4513",  # Saddle brown
            "electricity": "#FFD700",  # Gold
            "coal": "#2F4F4F",  # Dark slate gray
            "natural_gas": "#87CEEB",  # Sky blue
            "bf gas": "#4682B4",  # Steel blue
            "bof gas": "#5F9EA0",  # Cadet blue
            "cog": "#696969",  # Dim gray
            "steam": "#FFA500",  # Orange
            "unit_fopex": "#708090",  # Slate gray
            "unit fopex": "#708090",  # Slate gray (space version)
            "carbon_cost": "#DC143C",  # Crimson
            "carbon cost": "#DC143C",  # Crimson (space version)
            "debt_share": "#4B0082",  # Indigo
            "debt share": "#4B0082",  # Indigo (space version)
        }

        # Plot stacked bars for each furnace group
        for _, row in cost_df.iterrows():
            cap = row["capacity"]

            x0 = cum_x
            x1 = cum_x + cap

            # Stack the cost components
            y_bottom = 0
            for component in component_columns:
                if component in row and pd.notna(row[component]) and row[component] > 0:
                    component_value = row[component]

                    # Get color for this component
                    if component in component_colors:
                        color = component_colors[component]
                    else:
                        # Generate a color for unknown components
                        color = cm.Set3(hash(component) % 12 / 12)  # type: ignore[attr-defined]
                        logger.debug(f"[DEBUG] Unknown cost component '{component}' using generated color {color}")
                        # Add unknown components to legend colors dictionary
                        component_colors[component] = color

                    # Draw the stacked segment
                    ax.fill_between(
                        [x0, x1],
                        [y_bottom, y_bottom],
                        [y_bottom + component_value, y_bottom + component_value],
                        color=color,
                        alpha=0.8,
                        linewidth=0.5,
                        edgecolor="black",
                        label=component if cum_x == 0 else "",  # Only label once
                    )

                    y_bottom += component_value

            # Add region border/outline
            rectangle = plt.Rectangle(
                (x0, 0),
                cap,
                row["production_cost"],
                fill=False,
                edgecolor="black",
                linewidth=2,
                linestyle="-",
            )
            ax.add_patch(rectangle)

            cum_x = x1
    else:
        # Original simple filled rectangles (fallback)
        for _, row in cost_df.iterrows():
            cost = row["production_cost"]
            cap = row["capacity"]
            color = "steelblue"  # Use single color instead of regional colors

            x0 = cum_x
            x1 = cum_x + cap

            ax.fill_between(
                [x0, x1],
                [cost, cost],
                [0, 0],
                color=color,
                step="pre",
                linewidth=0,
            )

            ax.hlines(y=cost, xmin=x0, xmax=x1, colors="black", linewidth=0.8, zorder=2)

            cum_x = x1

    # Add market clearing price and demand lines
    clearing_cost = cost_df[cost_df["clearing_capacity"] <= demand].iloc[-1].production_cost if not cost_df.empty else 0

    ax.axhline(y=clearing_cost, color="r", linestyle="--", linewidth=1.5, label="Market clearing price")
    ax.axvline(x=demand, color="r", linestyle="--", linewidth=1.5, label="Demand")

    # Add clearing price annotation
    ax.text(
        x=cost_df.iloc[-1]["clearing_capacity"] * 0.7 if not cost_df.empty else 0,
        y=clearing_cost + 30,
        s=f"Market clearing price = {clearing_cost:.1f} $/t",
        ha="center",
        va="bottom",
        color="black",
        fontsize=10,
        backgroundcolor="white",
        alpha=0.8,
    )

    # Set title and labels
    breakdown_text = " with Cost Breakdown" if show_breakdown else ""
    ax.set_title(
        f"Cost Curve for {product_type.capitalize()} in {year}{breakdown_text}", fontsize=14, fontweight="bold"
    )
    ax.set_xlabel("Cumulative Capacity (kt)", fontsize=12)
    ax.set_ylabel("Production Cost ($/t)", fontsize=12)

    # Set axis limits
    if cum_x > 0:
        ax.set_xlim(0, cum_x * 1.02)
    else:
        ax.set_xlim(0, 1)

    max_cost = cost_df["production_cost"].max()
    if pd.isna(max_cost) or np.isinf(max_cost) or max_cost <= 0:
        logger.warning(f"Invalid max production cost: {max_cost}. Using default y-axis range.")
        ax.set_ylim(0, 1000)
    else:
        ax.set_ylim(0, max_cost * 1.1)

    # Create legend
    if show_breakdown and component_columns:
        # Create custom legend with both cost components and regions
        handles, labels = ax.get_legend_handles_labels()

        # Show only cost components legend (no regional distinction)
        if handles:
            ax.legend(
                handles=handles[: len(component_columns)],
                title="Cost Components",
                loc="upper left",
                frameon=True,
                fontsize=9,
            )
    # No regional legend when not showing breakdown

    # Add grid
    ax.grid(True, alpha=0.3, linestyle="--")

    # Save the figure
    if plot_paths is None or plot_paths.pam_plots_dir is None:
        raise ValueError("plot_paths with pam_plots_dir must be provided when saving plots")
    pam_plots_dir = plot_paths.pam_plots_dir
    pam_plots_dir.mkdir(parents=True, exist_ok=True)

    filename = (
        f"{product_type}_cost_curve_breakdown_{year}.png" if show_breakdown else f"{product_type}_cost_curve_{year}.png"
    )
    fig.savefig(pam_plots_dir / filename, dpi=300, bbox_inches="tight")
    plt.close()

    return fig


def _prepare_cost_curve_dataframe(
    data_frame: pd.DataFrame,
    product_type: str,
    year: int,
    aggregation: str,
    capacity_limit: float,
) -> pd.DataFrame:
    """Prepare per-plant cost data (filter, scale, order) before plotting."""
    df = data_frame.copy()
    if aggregation not in df.columns:
        df[aggregation] = "Unknown"

    df = df[(df["product"] == product_type) & (df["year"] == year)]
    if df.empty:
        return pd.DataFrame(columns=["production_cost", aggregation, "capacity", "clearing_capacity"])

    df = df.dropna(subset=["unit_production_cost", "capacity"])
    df = df[df["unit_production_cost"] > 0]
    df = df[df["capacity"] > 0]

    df = df.copy()
    df["production_cost"] = df["unit_production_cost"].astype(float)
    df["capacity"] = df["capacity"].astype(float)
    df[aggregation] = df[aggregation].fillna("Unknown")

    if "furnace_group_id" in df.columns:
        collapsed_rows: list[dict[str, Any]] = []
        for fg_id, group in df.groupby("furnace_group_id"):
            agg_values = group[aggregation].dropna().unique()
            if len(agg_values) > 1:
                logger.warning(
                    "Multiple '%s' labels found for furnace_group_id %s; using the first one: %s",
                    aggregation,
                    fg_id,
                    agg_values[0],
                )
            agg_value = agg_values[0] if len(agg_values) else "Unknown"

            capacities = group["capacity"].dropna()
            if capacities.empty:
                capacity_value = 0.0
            else:
                unique_capacities = capacities.unique()
                if len(unique_capacities) == 1:
                    capacity_value = float(unique_capacities[0])
                else:
                    capacity_value = float(capacities.sum())

            if capacity_value > 0:
                weight_series = group["capacity"].fillna(0.0)
                total_weight = float(weight_series.sum())
                if total_weight > 0:
                    weighted_cost = float((group["production_cost"] * weight_series).sum() / total_weight)
                else:
                    weighted_cost = float(group["production_cost"].iloc[0])
            else:
                weighted_cost = float(group["production_cost"].iloc[0])

            collapsed_rows.append(
                {
                    "furnace_group_id": fg_id,
                    aggregation: agg_value,
                    "capacity": capacity_value,
                    "production_cost": weighted_cost,
                }
            )

        df = pd.DataFrame(collapsed_rows)

    df["capacity"] = df["capacity"] * capacity_limit
    df = df[df["capacity"] > 0]

    df = df.sort_values("production_cost").copy()
    df = df[["production_cost", aggregation, "capacity"]]
    df["clearing_capacity"] = df["capacity"].cumsum()
    return df


def _compute_market_clearing(cost_df: pd.DataFrame, demand: float) -> Tuple[float, float, float]:
    """
    Determine clearing cost, vertical demand marker, and total capacity.
    """
    if cost_df.empty:
        return 0.0, 0.0, 0.0

    total_capacity = float(cost_df["clearing_capacity"].iloc[-1])
    demand_line_x = float(min(demand, total_capacity)) if total_capacity > 0 else 0.0

    match_demand = cost_df[cost_df["clearing_capacity"] >= demand]
    if match_demand.empty:
        clearing_cost = float(cost_df["production_cost"].iloc[-1])

        # If the final block is an extreme outlier with negligible capacity, fall back to the
        # last non-outlier slice so the market-clearing price remains meaningful on the plot.
        if len(cost_df) > 1 and total_capacity > 0:
            last_row = cost_df.iloc[-1]
            prev_rows = cost_df.iloc[:-1]
            reference_percentile = prev_rows["production_cost"].quantile(0.99)
            if pd.isna(reference_percentile) or reference_percentile <= 0:
                reference_percentile = float(prev_rows["production_cost"].max())
            threshold = float(reference_percentile) * 1.05
            capacity_share = float(last_row["capacity"]) / total_capacity

            if last_row["production_cost"] > threshold and capacity_share < 0.01:
                fallback_index = len(cost_df) - 2
                while fallback_index >= 0:
                    candidate = cost_df.iloc[fallback_index]
                    cand_share = float(candidate["capacity"]) / total_capacity
                    if candidate["production_cost"] <= threshold or cand_share >= 0.01:
                        clearing_cost = float(candidate["production_cost"])
                        break
                    fallback_index -= 1
    else:
        clearing_cost = float(match_demand.iloc[0]["production_cost"])

    return clearing_cost, demand_line_x, total_capacity


def plot_cost_curve_step_from_dataframe(
    data_file,
    product_type,
    product_demand,
    year,
    capacity_limit,
    units,
    aggregation="region",
    plot_paths: Optional["PlotPaths"] = None,
):
    """
    cost_df must contain at least:
      - 'production_cost' (y-value)
      - 'capacity' (width of each step)
      - 'region'
      - 'clearing_capacity' (cumsum of 'capacity', but we only need it to know boundaries)
    region_to_color should map each region string to an (R, G, B, A) tuple or hex string.
    """
    demand = product_demand
    if (demand is None) or (isinstance(demand, (int, float, np.floating)) and demand <= 0):
        if "production" in data_file.columns:
            mask = (data_file["product"] == product_type) & (data_file["year"] == year)
            demand = float(data_file.loc[mask, "production"].sum())
        else:
            demand = 0.0

    if "year" in data_file.columns:
        available_years = sorted(set(int(y) for y in data_file["year"].dropna()))
        if year not in available_years and available_years:
            logger.warning(f"Year {year} not found in data. Available years: {available_years}")
            lower_years = [y for y in available_years if y <= year]
            year = max(lower_years) if lower_years else min(available_years)
            logger.info(f"Using year {year} instead for cost curve plot")

    if aggregation == "region":
        colour_scheme = region2colours
    elif aggregation == "technology":
        colour_scheme = tech2colours
    else:
        logger.warning(f"Unsupported aggregation '{aggregation}' for cost curve plot. Defaulting to 'region'.")
        aggregation = "region"
        colour_scheme = region2colours

    cost_df = _prepare_cost_curve_dataframe(
        data_frame=data_file,
        product_type=product_type,
        year=year,
        aggregation=aggregation,
        capacity_limit=capacity_limit,
    )

    if cost_df.empty:
        logger.warning(f"No data for {product_type} in year {year}. Skipping cost curve plot.")
        return

    clearing_cost, demand_line_x, total_capacity = _compute_market_clearing(cost_df, demand)

    fig, ax = plt.subplots(figsize=(10, 6))
    cum_x = 0.0

    # Loop over each plant (row) in ascending‐cost order, drawing one “filled rectangle” per row:
    for _, row in cost_df.iterrows():
        cost = row["production_cost"]
        cap = row["capacity"]
        agg_object = row[aggregation]

        color = colour_scheme.get(agg_object, "#8c8c8c")

        x0 = cum_x
        x1 = cum_x + cap

        # Fill the rectangle from y=0 up to y=cost, between x0 and x1:
        ax.fill_between(
            [x0, x1],  # x‐coordinates for the two corners of this step
            [cost, cost],  # y‐value of the top edge
            [0, 0],  # y=0 for the bottom edge
            color=color,
            step="pre",  # ensures a vertical drop at x0 if needed
            linewidth=0,  # no border on the fill itself
        )

        # (Optional) draw a thin black line on top of the fill, to emphasize the step:
        ax.hlines(y=cost, xmin=x0, xmax=x1, colors="black", linewidth=0.4, zorder=2)

        cum_x = x1

    costs = cost_df["production_cost"].values
    cum_caps = cost_df["clearing_capacity"].values
    for i in range(len(cost_df) - 1):
        boundary_x = cum_caps[i]
        y0 = costs[i]
        y1 = costs[i + 1]
        ax.vlines(x=boundary_x, ymin=y0, ymax=y1, colors="gray", linewidth=0.6, linestyle="--", zorder=1)

    # Set x‐ and y‐limits so the plot is tight:

    # Build a legend that maps each region → its fill color:
    legend_handles = [
        Patch(color=color, label=agg) for agg, color in colour_scheme.items() if agg in cost_df[aggregation].values
    ]
    ax.legend(handles=legend_handles, title=aggregation, loc="upper left", frameon=False)
    annotation_text = f"Market clearing price = {clearing_cost:.1f} $/t"
    if demand > total_capacity:
        annotation_text += "\n(Demand exceeds available supply)"

    ax.set_title(f"Cost curve for {product_type} in {year}")

    # Set x limits
    if cum_x > 0:
        if demand > total_capacity:
            x_padding = max(total_capacity * 0.02, 1e-6)
            ax.set_xlim(0, total_capacity + x_padding)
        else:
            ax.set_xlim(0, total_capacity)
    else:
        ax.set_xlim(0, 1)  # Default if no capacity

    # Set y limits safely
    max_cost = cost_df["production_cost"].max()
    if pd.isna(max_cost) or np.isinf(max_cost) or max_cost <= 0:
        logger.warning(f"Invalid max production cost: {max_cost}. Using default y-axis range.")
        ax.set_ylim(0, 1000)  # Default reasonable range
    else:
        outlier_cap = 2000.0
        if max_cost > outlier_cap:
            y_limit = outlier_cap
        else:
            upper_percentile = cost_df["production_cost"].quantile(0.995)
            if pd.notna(upper_percentile) and upper_percentile > 0 and max_cost > upper_percentile * 1.5:
                y_limit = upper_percentile * 1.1
            else:
                y_limit = max_cost * 1.1
        ax.set_ylim(0, y_limit)

    ax.set_xlabel(f"Cumulative Capacity [{units}]")
    ax.set_ylabel("Production Cost ($/t)")
    display_clearing_cost = min(clearing_cost, ax.get_ylim()[1])
    ax.axhline(y=display_clearing_cost, color="r", linestyle="--", linewidth=1.2, zorder=7, clip_on=False)
    ax.axvline(x=demand_line_x, color="r", linestyle="--", linewidth=1.2, zorder=7, clip_on=False)
    if display_clearing_cost < clearing_cost:
        annotation_text += "\n(clipped for scale)"

    # Draw market clearing annotation after axis limits are final
    x_min, x_max = ax.get_xlim()
    y_min, y_max = ax.get_ylim()
    x_span = max(x_max - x_min, 1e-6)
    y_span = max(y_max - y_min, 1e-6)
    text_x = min(demand_line_x - 0.1 * x_span, x_max - 0.05 * x_span)
    if text_x <= x_min:
        text_x = x_min + 0.05 * x_span
    text_y = display_clearing_cost + 0.15 * y_span
    if text_y >= y_max:
        text_y = y_max - 0.05 * y_span
    ax.text(
        text_x,
        text_y,
        annotation_text,
        ha="right",
        va="bottom",
        color="black",
        fontsize=10,
        bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="gray", alpha=0.8),
        zorder=6,
    )
    if plot_paths is None or plot_paths.pam_plots_dir is None:
        raise ValueError("plot_paths with pam_plots_dir must be provided when saving plots")
    pam_plots_dir = plot_paths.pam_plots_dir
    pam_plots_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(pam_plots_dir / f"{product_type}_cost_curve_by_{aggregation}_{year}.png", dpi=300)
    plt.close()


def plot_geo_layers(
    dataset: xr.Dataset,
    plot_paths: Optional["PlotPaths"] = None,
    year: int = 2025,
    lcoh_dataset: Optional[xr.Dataset] = None,
) -> None:
    """
    Plot geographic layers from simulation output.

    This function creates various geographic visualizations including:
    - LCOE (Levelized Cost of Energy) maps
    - LCOH (Levelized Cost of Hydrogen) maps
    - Priority location maps for iron and steel plants

    Args:
        dataset: xarray Dataset containing geo_priority_kpi data
        plot_paths: PlotPaths object containing output directories
        year: Year to include in plot titles and filenames
        lcoh_dataset: Optional xarray Dataset containing LCOH data
    """
    if plot_paths is None or plot_paths.geo_plots_dir is None:
        raise ValueError("plot_paths with geo_plots_dir must be provided when saving plots")
    geo_plots_dir = plot_paths.geo_plots_dir
    geo_plots_dir.mkdir(parents=True, exist_ok=True)

    # Plot LCOE
    if "lcoe" in dataset and "feasibility_mask" in dataset:
        plot_screenshot(
            dataset.where(dataset["feasibility_mask"] > 0),
            var="lcoe",
            title=f"Optimal LCOE for 85% coverage at all feasible locations in {year} (USD/MWh)",
            var_type="sequential",
            max_val=200,
            save_name=f"optimal_lcoe_{year}",
            plot_paths=plot_paths,
        )

    # Plot LCOH if data is provided
    if lcoh_dataset is not None and "lcoh" in lcoh_dataset and "feasibility_mask" in dataset:
        plot_screenshot(
            lcoh_dataset.where(dataset["feasibility_mask"] > 0),
            var="lcoh",
            title=f"Optimal LCOH for 85% coverage at all feasible locations in {year} (USD/kg)",
            var_type="sequential",
            max_val=10,
            save_name=f"optimal_lcoh_{year}",
            plot_paths=plot_paths,
        )

    # Plot location priority KPI
    # Auto-detect the priority percentage from dataset variables (e.g., top5_iron, top20_iron)
    import re

    for product in ["iron", "steel"]:
        # Find variable matching pattern "top<percentage>_<product>" (e.g., "top20_iron")
        matching_vars = [v for v in dataset.data_vars if re.match(rf"top(\d+)_{product}$", str(v))]
        if matching_vars:
            var_name = str(matching_vars[0])
            match = re.match(rf"top(\d+)_{product}$", var_name)
            if match:
                pct = int(match.group(1))
                plot_screenshot(
                    dataset[var_name],
                    title=f"Top {pct}% priority locations to build {product} plants",
                    var_type="binary",
                    save_name=f"top{pct}_priority_locations_{product}",
                    plot_paths=plot_paths,
                )


def plot_trade_allocation_visualization(
    allocations_by_commodity: Dict[str, CommodityAllocations],
    chosen_year: Year,
    plot_paths: Optional["PlotPaths"] = None,
    country_mappings: Optional[Any] = None,
    top_n: int = 20,
) -> None:
    """
    Create an interactive network visualization of trade allocations between countries.

    This is a wrapper function that calls the network plotting functionality from
    the country_network_plotting module.

    Args:
        allocations_by_commodity: Dictionary mapping commodity names to their allocations
        chosen_year: Year for the visualization
        plot_paths: PlotPaths object containing output directories
        country_mappings: Optional country mapping service for region information
        top_n: Number of top country-to-country trade routes to show
    """
    from steelo.utilities.country_network_plotting import create_multi_commodity_network_plot

    # Determine output directory - use tm_plots_dir for trade maps
    if plot_paths and plot_paths.tm_plots_dir:
        output_dir = plot_paths.tm_plots_dir
    else:
        output_dir = Path("plot_output")
        output_dir.mkdir(exist_ok=True)

    # Create the network plot
    create_multi_commodity_network_plot(
        allocations_by_commodity=allocations_by_commodity,
        year=chosen_year,
        output_dir=output_dir,
        output_filename=f"trade_network_visualization_{chosen_year}.html",
        top_n=top_n,
        layout_algorithm="geographic",
        country_mappings=country_mappings,
    )

    logger.info(
        f"Created trade allocation network visualization for {chosen_year}: {output_dir / f'trade_network_visualization_{chosen_year}.html'}"
    )
