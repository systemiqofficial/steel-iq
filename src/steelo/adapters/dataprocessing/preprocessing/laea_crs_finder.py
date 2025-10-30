from pyproj import CRS


def get_laea_crs(geometry):
    """
    Creates a LAEA CRS centered on the geometry's representative point with WGS84 ellipsoid.
    """
    rep_point = geometry.representative_point()
    lat, lon = rep_point.y, rep_point.x
    laea_proj = f"+proj=laea +lat_0={lat} +lon_0={lon} +x_0=0 +y_0=0 +ellps=WGS84 +units=m +no_defs"
    return CRS.from_proj4(laea_proj)
