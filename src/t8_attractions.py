"""Task 8, first part: Seattle attractions from OpenStreetMap (Overpass API).

Downloads museums, galleries, viewpoints, zoos, aquariums, theme parks,
places tagged tourism=attraction, and named parks inside Seattle's bounding
box; keeps those with a name and coordinates; flags whether each has a
Wikipedia article; and finds each one's neighbourhood with our ray-casting code.

Writes outputs/t8_attractions_base.csv  (month-independent list for Tulasi)
       cache/overpass/attractions.json  (raw response with retrieval time)

Run from the repo root:
    python src/t8_attractions.py
Data (c) OpenStreetMap contributors, ODbL.
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.common.geo import find_neighbourhood  # noqa: E402

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# Public mirror running the same Overpass software, used only if the main server keeps failing.
OVERPASS_MIRROR = "https://overpass.kumi.systems/api/interpreter"
USER_AGENT = "SafeStay-CS4371-Group08-student-project"
CACHE_FILE = Path("cache/overpass/attractions.json")
OUT = Path("outputs/t8_attractions_base.csv")

# south, west, north, east
BBOX = (47.49, -122.44, 47.74, -122.22)
TOURISM_TYPES = "museum|gallery|viewpoint|zoo|aquarium|theme_park|attraction"

QUERY = f"""
[out:json][timeout:120];
(
  nwr["tourism"~"^({TOURISM_TYPES})$"]["name"]{BBOX};
  nwr["leisure"="park"]["name"]{BBOX};
);
out center tags;
"""


def fetch():
    """Return the Overpass elements, from the cache if we already have them."""
    if CACHE_FILE.exists():
        saved = json.loads(CACHE_FILE.read_text())
        print(f"Using cached Overpass response from {saved['retrieved_at']}")
        return saved["data"]["elements"], saved["retrieved_at"]

    resp, used_url = None, None
    attempts = [OVERPASS_URL, OVERPASS_URL, OVERPASS_URL, OVERPASS_MIRROR, OVERPASS_MIRROR]
    for n, url in enumerate(attempts, start=1):
        try:
            r = requests.post(url, data={"data": QUERY},
                              headers={"User-Agent": USER_AGENT}, timeout=180)
            if r.status_code == 200:
                resp, used_url = r, url
                break
            print(f"Attempt {n} ({url}): HTTP {r.status_code}, server busy")
        except requests.RequestException as e:
            print(f"Attempt {n} ({url}): {type(e).__name__}")
        wait = 15 * n
        print(f"  waiting {wait} s before trying again...")
        time.sleep(wait)
    if resp is None:
        raise SystemExit("Overpass is not answering right now. Try again in a few minutes.")
    retrieved_at = datetime.now(timezone.utc).isoformat()
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps({
        "url": used_url, "query": QUERY,
        "retrieved_at": retrieved_at, "data": resp.json(),
    }))
    time.sleep(1)
    print(f"Downloaded from {used_url} at {retrieved_at}")
    return resp.json()["elements"], retrieved_at


def kind_of(tags):
    if tags.get("tourism") in TOURISM_TYPES.split("|"):
        return tags["tourism"]
    return "park"


def main():
    elements, retrieved_at = fetch()
    print(f"Elements returned: {len(elements)}")

    rows = []
    for el in elements:
        tags = el.get("tags", {})
        name = (tags.get("name") or "").strip()
        lat = el.get("lat", el.get("center", {}).get("lat"))
        lon = el.get("lon", el.get("center", {}).get("lon"))
        if not name or lat is None or lon is None:
            continue
        rows.append({
            "name": name,
            "type": kind_of(tags),
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "has_wikipedia": bool(tags.get("wikipedia") or tags.get("wikidata")),
            "wikipedia": tags.get("wikipedia", ""),
            "wikidata": tags.get("wikidata", ""),
            "osm_id": f"{el['type']}/{el['id']}",
        })
    df = pd.DataFrame(rows)
    print(f"With a name and coordinates: {len(df)}")

    # The same place is sometimes mapped twice (e.g. a node and a building).
    # Keep one per name and type, preferring the one with a Wikipedia tag.
    df = (df.sort_values("has_wikipedia", ascending=False)
            .drop_duplicates(subset=["name", "type"], keep="first"))
    print(f"After removing duplicates: {len(df)}")

    df["neighbourhood"] = [find_neighbourhood(a, b) for a, b in zip(df["lat"], df["lon"])]
    outside = df["neighbourhood"].isna().sum()
    df = df[df["neighbourhood"].notna()]
    print(f"Outside Seattle's neighbourhoods (removed): {outside}")
    print(f"Attractions kept: {len(df)}")

    df["retrieved_at"] = retrieved_at
    df = df.sort_values(["type", "name"]).reset_index(drop=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False)

    print("\nBy type:")
    print(df["type"].value_counts().to_string())
    print(f"\nWith a Wikipedia/Wikidata tag: {df['has_wikipedia'].sum()}")
    print(f"Neighbourhoods with at least one attraction: {df['neighbourhood'].nunique()}")
    print(f"\nSaved {OUT}")


if __name__ == "__main__":
    main()
