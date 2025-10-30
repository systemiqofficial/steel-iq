import httpx
import osm2geojson  # type: ignore
import geopandas as gpd  # type: ignore


def fetch_and_convert_to_gdf(overpass_query, country):
    overpass_url = "http://overpass-api.de/api/interpreter"
    response = httpx.get(overpass_url, params={"data": overpass_query})
    response.raise_for_status()
    data = response.json()
    geojson = osm2geojson.json2geojson(data)
    gdf = gpd.GeoDataFrame.from_features(geojson["features"], crs="EPSG:4326")
    gdf["country"] = country
    return gdf


def extract_rail(country):
    overpass_query = f"""
    [out:json];
    area["name:en"={country}]->.searchArea;
    way["railway"="rail"]["usage"="main"](area.searchArea);
    out geom;
    """
    return fetch_and_convert_to_gdf(overpass_query, country)


def extract_highways(country):
    overpass_query = f"""
    [out:json];
    area["name:en"={country}]->.searchArea;
    way[highway~"^(motorway|trunk)$"](area.searchArea);
    out geom;
    """
    return fetch_and_convert_to_gdf(overpass_query, country)


def extract_power_lines(country):
    overpass_query = f"""
    [out:json][timeout:25];
    area["name:en"="{country}"]->.searchArea;
    (way["power"="line"](area.searchArea););
    out geom;
    """
    return fetch_and_convert_to_gdf(overpass_query, country)
