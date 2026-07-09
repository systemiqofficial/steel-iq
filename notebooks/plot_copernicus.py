#!/usr/bin/env python3
"""
Plot a time-averaged technology resource (solar or wind) with an exclusion mask applied.

Usage:
    python plot_tech_with_mask.py /path/to/wp_mask.nc /path/to/tech_cf.nc tech [output.png]

Arguments:
    wp_mask.nc    NetCDF with variables:
                     - longitude (nlon)
                     - latitude  (nlat)
                     - wp_mask   (latitude, longitude)  (int mask)
    tech_cf.nc    NetCDF with variables:
                     - longitude, latitude, time, and one of: spv_cf, won, wof
    tech          "solar" | "won" | "wof"
                 - "solar" -> variable "spv_cf"
                 - "won"   -> variable "won"   (onshore wind)
                 - "wof"   -> variable "wof"   (offshore wind)
    output.png    optional output filename; if omitted the plot is shown interactively.
"""

import sys
import numpy as np
import matplotlib.pyplot as plt
from netCDF4 import Dataset
import cartopy.crs as ccrs
import cartopy.feature as cfeature

# -----------------------------
# Configuration / defaults
# -----------------------------
DEFAULT_CF_CMAP = "viridis"
FIGSIZE = (12, 6)
PLOT_METHOD = "pcolormesh"  # "pcolormesh" or "imshow"
SHOW_GRID = True
DRAW_COASTLINE = True
ADD_LAND_SEA = True

# Mapping from technology string to variable name in CF file
TECH_VAR_MAP = {
    "solar": "spv_cf",
    "won": "won",  # onshore wind
    "wof": "wof",  # offshore wind
}

TECH_MASK_MAP = {
    "solar": "PVmask",
    "won": "wp_mask",
    "wof": "mask",
}


# -----------------------------
# Helper functions
# -----------------------------
def read_mask(nc_path, tech):
    ds = Dataset(nc_path, "r")
    print("Mask file variables:", ds.variables.keys())
    lon = np.array(ds.variables["longitude"][:])
    lat = np.array(ds.variables["latitude"][:])
    # mask variable is named according to the technology
    mask_var = TECH_MASK_MAP.get(tech)
    if mask_var not in ds.variables:
        raise KeyError(f"{mask_var} variable not found in mask file.")
    mask = np.array(ds.variables[mask_var][:])  # expected shape (lat, lon)
    ds.close()
    return lon, lat, mask


def read_cf_and_timeavg(nc_path, varname):
    ds = Dataset(nc_path, "r")
    print("CF file variables:", ds.variables.keys())
    if varname not in ds.variables:
        raise KeyError(f"{varname} not found in CF file.")
    lon = np.array(ds.variables["longitude"][:])
    lat = np.array(ds.variables["latitude"][:])
    cf = ds.variables[varname][:]  # expected shape (time, lat, lon)
    # handle _FillValue if present
    fill_value = getattr(ds.variables[varname], "_FillValue", None)
    cf = np.array(cf, dtype=float)
    if fill_value is not None:
        cf[cf == fill_value] = np.nan
    cf_mean = np.nanmean(cf, axis=0)  # shape (lat, lon)
    ds.close()
    return lon, lat, cf_mean


def lon_to_180(lon):
    lon = np.array(lon, dtype=float)
    lon180 = ((lon + 180.0) % 360.0) - 180.0
    return lon180


def sort_grid(lon, lat, data2d):
    """
    Convert lon -> [-180,180), sort lon ascending, reorder columns of data2d accordingly.
    Ensure lat is ascending (south->north); if not, flip data rows.
    Returns sorted lon, sorted lat, reordered data2d.
    """
    # Longitudes: convert and sort
    lon180 = lon_to_180(lon)
    lon_sort_idx = np.argsort(lon180)
    lon_sorted = lon180[lon_sort_idx]

    # Reorder data columns (axis 1) according to lon_sort_idx
    data_lon_reordered = data2d[:, lon_sort_idx]

    # Latitudes: ensure ascending
    lat_sorted = np.array(lat, dtype=float)
    if lat_sorted[0] > lat_sorted[-1]:
        lat_sorted = lat_sorted[::-1]
        data_lon_reordered = np.flipud(data_lon_reordered)

    return lon_sorted, lat_sorted, data_lon_reordered


def prepare_grid_for_pcolormesh(lon, lat):
    def midpoints(arr):
        d = np.diff(arr)
        left = arr[0] - d[0] / 2.0
        right = arr[-1] + d[-1] / 2.0
        edges = np.concatenate([[left], arr[:-1] + d / 2.0, [right]])
        return edges

    lon_edges = midpoints(lon)
    lat_edges = midpoints(lat)
    Lon_edges_2d, Lat_edges_2d = np.meshgrid(lon_edges, lat_edges)
    return Lon_edges_2d, Lat_edges_2d


# -----------------------------
# Plotting
# -----------------------------
def plot_tech_with_mask(lon_mask, lat_mask, mask, lon_cf, lat_cf, cf_mean, tech_label, outname=None):
    # Align and sort both grids
    lon_mask_s, lat_mask_s, mask_s = sort_grid(lon_mask, lat_mask, mask)
    lon_cf_s, lat_cf_s, cf_s = sort_grid(lon_cf, lat_cf, cf_mean)

    # Attempt to align shapes
    if mask_s.shape != cf_s.shape:
        print("Mask and CF shapes after sorting differ:", mask_s.shape, cf_s.shape)
        # Try transposing mask if that helps
        if mask_s.T.shape == cf_s.shape:
            print("Transposing mask to match CF shape after sorting.")
            mask_s = mask_s.T
        else:
            print("Warning: shapes still differ. Mask application may be incorrect if shapes mismatch.")

    # Interpret nonzero mask as excluded
    excluded = mask_s != 0

    # Apply mask: set excluded CF cells to NaN
    cf_masked = cf_s.copy().astype(float)
    if excluded.shape == cf_masked.shape:
        cf_masked[excluded] = np.nan
    else:
        print("Warning: excluded mask shape", excluded.shape, "does not match cf shape", cf_masked.shape)

    # Plot
    plt.figure(figsize=FIGSIZE)
    ax = plt.axes(projection=ccrs.PlateCarree())

    if ADD_LAND_SEA:
        ax.add_feature(cfeature.LAND, facecolor="lightgray", zorder=0)
        ax.add_feature(cfeature.OCEAN, facecolor="lightblue", zorder=0)
    if DRAW_COASTLINE:
        ax.coastlines(resolution="110m", linewidth=0.6)
    ax.add_feature(cfeature.BORDERS, linewidth=0.4)
    ax.set_global()

    if SHOW_GRID:
        gl = ax.gridlines(draw_labels=True, dms=False, x_inline=False, y_inline=False)
        gl.top_labels = False
        gl.right_labels = False

    # Prepare colormap and ensure NaN are white
    cmap = plt.get_cmap(DEFAULT_CF_CMAP)
    # set_bad works for regular cmaps; create a copy if needed
    try:
        cmap.set_bad("white")
    except Exception:
        import matplotlib.colors as mcolors

        colors = plt.get_cmap(DEFAULT_CF_CMAP)(np.linspace(0, 1, 256))
        cmap = mcolors.ListedColormap(colors)
        cmap.set_bad("white")

    if PLOT_METHOD == "pcolormesh":
        Lon_edges, Lat_edges = prepare_grid_for_pcolormesh(lon_cf_s, lat_cf_s)
        pcm = ax.pcolormesh(
            Lon_edges, Lat_edges, cf_masked, transform=ccrs.PlateCarree(), cmap=cmap, shading="auto", zorder=1
        )
        cbar = plt.colorbar(pcm, ax=ax, orientation="vertical", pad=0.02, fraction=0.05)
        cbar.set_label(f"Avg {tech_label} capacity factor")
    else:
        data = cf_masked
        extent = [lon_cf_s[0], lon_cf_s[-1], lat_cf_s[0], lat_cf_s[-1]]
        im = ax.imshow(data, origin="lower", extent=extent, transform=ccrs.PlateCarree(), cmap=cmap, zorder=1)
        cbar = plt.colorbar(im, ax=ax, orientation="vertical", pad=0.02, fraction=0.05)
        cbar.set_label(f"Avg {tech_label} capacity factor")

    ax.set_title(f"Average {tech_label} capacity factor (excluded cells shown as white)")
    plt.tight_layout()
    if outname:
        plt.savefig(outname, dpi=300)
        print(f"Saved figure to {outname}")
    else:
        plt.show()


# -----------------------------
# Command-line entry
# -----------------------------
def main():
    if len(sys.argv) < 4:
        print("Usage: python plot_tech_with_mask.py /path/to/wp_mask.nc /path/to/tech_cf.nc tech [output.png]")
        print('tech must be one of: "solar", "won", "wof"')
        sys.exit(1)

    mask_path = sys.argv[1]
    cf_path = sys.argv[2]
    tech = sys.argv[3].lower()
    outname = sys.argv[4] if len(sys.argv) >= 5 else None

    if tech not in TECH_VAR_MAP:
        print('Invalid tech. Choose one of: "solar", "won", "wof"')
        sys.exit(1)

    varname = TECH_VAR_MAP[tech]
    tech_label = {"solar": "solar PV", "won": "onshore wind", "wof": "offshore wind"}[tech]

    lon_mask, lat_mask, mask = read_mask(mask_path, tech)
    lon_cf, lat_cf, cf_mean = read_cf_and_timeavg(cf_path, varname)

    print("mask lon shape:", lon_mask.shape, "lat shape:", lat_mask.shape, "mask shape:", mask.shape)
    print("cf lon shape:", lon_cf.shape, "lat shape:", lat_cf.shape, "cf_mean shape:", cf_mean.shape)

    plot_tech_with_mask(lon_mask, lat_mask, mask, lon_cf, lat_cf, cf_mean, tech_label, outname=outname)


if __name__ == "__main__":
    main()
