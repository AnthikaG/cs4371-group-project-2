"""Geo module: Seattle neighbourhood boundaries, point-in-polygon and distances.

Used by Task 4 (hotel neighbourhoods, nearby hotels) and Task 8 (attraction
neighbourhoods, hotels within 1 km).

    from src.common.geo import find_neighbourhood, haversine_km

    find_neighbourhood(47.6097, -122.3422)      # -> "DOWNTOWN COMMERCIAL"
    haversine_km(47.6097, -122.3422, 47.6205, -122.3493)   # -> about 1.3

The boundaries are SPD's Micro Community Policing Plans (MCPP): 58 areas whose
names match the `neighborhood` field of the incident data exactly.

Download the boundaries once (cached afterwards):
    python src/common/geo.py
"""
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

BOUNDARY_URL = (
    "https://services.arcgis.com/ZOyb2t4B0UYuYNYH/arcgis/rest/services/"
    "MCPPs_WebMerc/FeatureServer/0/query"
)
BOUNDARY_PARAMS = {
    "where": "1=1",
    "outFields": "neighborhood,precinct",
    "outSR": "4326",          # plain latitude / longitude
    "f": "geojson",
}
BOUNDARY_FILE = Path("data/seattle_mcpp_boundaries.geojson")
EARTH_RADIUS_KM = 6371.0088

_NEIGHBOURHOODS = None  # loaded once, then reused


# ---------------------------------------------------------------- download
def download_boundaries(force=False):
    """Download the MCPP boundaries as GeoJSON and save them with the retrieval time."""
    if BOUNDARY_FILE.exists() and not force:
        return BOUNDARY_FILE
    resp = requests.get(BOUNDARY_URL, params=BOUNDARY_PARAMS, timeout=120,
                        headers={"User-Agent": "SafeStay-CS4371-student-project"})
    resp.raise_for_status()
    data = resp.json()
    data["retrieved_at"] = datetime.now(timezone.utc).isoformat()
    data["source_url"] = resp.url
    BOUNDARY_FILE.parent.mkdir(parents=True, exist_ok=True)
    BOUNDARY_FILE.write_text(json.dumps(data))
    time.sleep(1)
    return BOUNDARY_FILE


# ---------------------------------------------------------------- loading
def load_neighbourhoods(path=BOUNDARY_FILE):
    """Return a list of (name, polygons, bbox).

    polygons is a list of polygons; each polygon is a list of rings
    (the first ring is the outer edge, any others are holes); each ring is a
    list of (lon, lat) points. bbox = (min_lon, min_lat, max_lon, max_lat) is
    used to skip polygons quickly.
    """
    gj = json.loads(Path(path).read_text())
    result = []
    for feature in gj["features"]:
        name = feature["properties"]["neighborhood"].strip().upper()
        geom = feature["geometry"]
        if geom["type"] == "Polygon":
            polygons = [geom["coordinates"]]
        elif geom["type"] == "MultiPolygon":
            polygons = geom["coordinates"]
        else:
            continue
        polygons = [[[(p[0], p[1]) for p in ring] for ring in poly] for poly in polygons]
        lons = [p[0] for poly in polygons for p in poly[0]]
        lats = [p[1] for poly in polygons for p in poly[0]]
        result.append((name, polygons, (min(lons), min(lats), max(lons), max(lats))))
    return result


def _get_neighbourhoods():
    global _NEIGHBOURHOODS
    if _NEIGHBOURHOODS is None:
        if not BOUNDARY_FILE.exists():
            download_boundaries()
        _NEIGHBOURHOODS = load_neighbourhoods()
    return _NEIGHBOURHOODS


# ---------------------------------------------------------------- ray casting
def point_in_ring(lon, lat, ring):
    """Ray-casting test: is the point inside this closed ring?

    Draw a ray from the point straight to the right (increasing longitude) and
    count how many edges of the ring it crosses. An odd count means inside.
    """
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        # Does edge (j -> i) straddle the horizontal line through the point?
        if (yi > lat) != (yj > lat):
            # Longitude where the edge crosses that horizontal line.
            x_cross = xi + (lat - yi) * (xj - xi) / (yj - yi)
            if lon < x_cross:
                inside = not inside
        j = i
    return inside


def point_in_polygon(lon, lat, polygon):
    """Inside the outer ring and not inside any hole."""
    if not point_in_ring(lon, lat, polygon[0]):
        return False
    return not any(point_in_ring(lon, lat, hole) for hole in polygon[1:])


def find_neighbourhood(lat, lon):
    """Name of the neighbourhood containing the point, or None if it is outside all of them."""
    if lat is None or lon is None:
        return None
    for name, polygons, (min_lon, min_lat, max_lon, max_lat) in _get_neighbourhoods():
        if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
            continue
        if any(point_in_polygon(lon, lat, poly) for poly in polygons):
            return name
    return None


def neighbourhood_names():
    return sorted(name for name, _, _ in _get_neighbourhoods())


# ---------------------------------------------------------------- haversine
def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in kilometres between two latitude/longitude points."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


if __name__ == "__main__":
    path = download_boundaries()
    hoods = load_neighbourhoods(path)
    print(f"Boundaries saved to {path}: {len(hoods)} neighbourhoods")
    print("Pike Place Market ->", find_neighbourhood(47.6097, -122.3422))
    print("Space Needle      ->", find_neighbourhood(47.6205, -122.3493))
    print("Pike Place to Space Needle:",
          round(haversine_km(47.6097, -122.3422, 47.6205, -122.3493), 2), "km")
