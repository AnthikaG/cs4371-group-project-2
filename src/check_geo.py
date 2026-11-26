"""Check the geo module against the city's own data.

Every incident report already says which neighbourhood it is in. Run our
ray-casting code on a random sample of incident coordinates and see how often
it agrees. Use the result in the report.

Run from the repo root (after python src/common/geo.py):
    python src/check_geo.py
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.common.geo import find_neighbourhood, haversine_km  # noqa: E402

SAMPLE = 3000

df = pd.read_parquet("data/raw/seattle_incidents_raw.parquet",
                     columns=["neighborhood", "latitude", "longitude"])
df["lat"] = pd.to_numeric(df["latitude"], errors="coerce")
df["lon"] = pd.to_numeric(df["longitude"], errors="coerce")
df = df[df["lat"].between(47.49, 47.74) & df["lon"].between(-122.44, -122.22)]
df = df[~df["neighborhood"].isin(["-", "", "UNKNOWN"]) & df["neighborhood"].notna()]
df = df.sample(min(SAMPLE, len(df)), random_state=42)

df["ours"] = [find_neighbourhood(a, b) for a, b in zip(df["lat"], df["lon"])]
df["city"] = df["neighborhood"].str.strip().str.upper()

found = df["ours"].notna().mean()
match = (df["ours"] == df["city"]).mean()
print(f"Sample size: {len(df)}")
print(f"Point found inside some neighbourhood: {found:.1%}")
print(f"Our neighbourhood matches the city's:   {match:.1%}")

wrong = df[df["ours"].notna() & (df["ours"] != df["city"])]
if len(wrong):
    print("\nMost common disagreements (city -> ours):")
    print(wrong.groupby(["city", "ours"]).size().sort_values(ascending=False).head(10))

# Haversine sanity check: Seattle to Portland is about 233 km in a straight line.
print(f"\nHaversine Seattle -> Portland: {haversine_km(47.6062, -122.3321, 45.5152, -122.6784):.1f} km")
