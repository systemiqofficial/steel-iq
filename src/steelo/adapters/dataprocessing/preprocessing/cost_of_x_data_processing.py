import pandas as pd
import geopandas as gpd  # type: ignore

# Default geographic boundaries URL
DEFAULT_GEO_BOUNDARIES_URL = "https://raw.githubusercontent.com/holtzy/D3-graph-gallery/master/DATA/world.geojson"


# Function to Merge Data with GeoDataFrame
def merge_geographical_data(
    data_df: pd.DataFrame, country_column: str, geo_boundaries_url: str = DEFAULT_GEO_BOUNDARIES_URL
) -> pd.DataFrame:
    """
    Merge geographical data with statistical data based on ISO3 country codes.

    Parameters:
    data_df : pd.DataFrame
        Statistical data with country codes and values.
    geo_df : gpd.GeoDataFrame
        GeoDataFrame with country geometries.
    country_column : str
        Column in data_df with country codes matching 'iso_a3' in geo_df.

    Returns:
    gpd.GeoDataFrame
        Merged GeoDataFrame.
    """
    geo_df = gpd.read_file(
        geo_boundaries_url,
    )
    if "iso_a3" not in geo_df.columns:
        raise ValueError("The GeoDataFrame does not contain the required 'iso_a3' column.")

    # Check if the country_column in data_df contains valid ISO3 codes
    if not data_df[country_column].str.match(r"^[A-Z]{3}$").all():
        raise ValueError(f"The column '{country_column}' does not contain valid ISO3 country codes.")

    return data_df.merge(geo_df, right_on="iso_a3", left_on=country_column)
