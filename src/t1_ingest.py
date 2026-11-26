"""Task 1, step 1: download every Seattle incident report for 2022-2024.

Pages through the Socrata API with $limit / $offset, saves every page to
cache/socrata/ with the time it was received, and reuses cached pages on
later runs. Writes the combined raw data to data/raw/seattle_incidents_raw.parquet.

Run from the repo root:
    python src/t1_ingest.py
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

API_URL = "https://data.seattle.gov/resource/tazs-3rd5.json"
APP_TOKEN = os.environ.get("SOCRATA_APP_TOKEN", "")
PAGE_SIZE = 50000
WHERE = (
    "offense_date >= '2022-01-01T00:00:00' "
    "AND offense_date <= '2024-12-31T23:59:59'"
)

CACHE_DIR = Path("cache/socrata")
RAW_OUT = Path("data/raw/seattle_incidents_raw.parquet")


def fetch_page(offset):
    """Return the rows for one page, from the cache if we already have it."""
    cache_file = CACHE_DIR / f"incidents_offset_{offset:07d}.json"
    if cache_file.exists():
        saved = json.loads(cache_file.read_text())
        print(f"offset {offset}: {len(saved['data'])} rows (cached {saved['retrieved_at']})")
        return saved["data"]

    params = {
        "$where": WHERE,
        "$order": ":id",          # stable order, so paging never skips or repeats rows
        "$limit": PAGE_SIZE,
        "$offset": offset,
    }
    headers = {"X-App-Token": APP_TOKEN} if APP_TOKEN else {}
    resp = requests.get(API_URL, params=params, headers=headers, timeout=120)
    resp.raise_for_status()
    rows = resp.json()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps({
        "url": API_URL,
        "params": params,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "data": rows,
    }))
    print(f"offset {offset}: {len(rows)} rows (live)")
    time.sleep(1)  # at least one second between calls to the same service
    return rows


def expected_count():
    headers = {"X-App-Token": APP_TOKEN} if APP_TOKEN else {}
    resp = requests.get(
        API_URL,
        params={"$select": "count(*) AS n", "$where": WHERE},
        headers=headers,
        timeout=60,
    )
    resp.raise_for_status()
    time.sleep(1)
    return int(resp.json()[0]["n"])


def main():
    if not APP_TOKEN:
        print("Warning: SOCRATA_APP_TOKEN is not set; requests may be throttled.")

    total = expected_count()
    print(f"API reports {total} rows for 2022-2024")

    all_rows, offset = [], 0
    while True:
        rows = fetch_page(offset)
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    print(f"Downloaded {len(all_rows)} rows (API said {total})")

    # Keep every column as text in the raw file; Spark casts types during cleaning.
    df = pd.DataFrame(all_rows).astype("string")
    RAW_OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(RAW_OUT, index=False)
    print(f"Saved {RAW_OUT}  ({len(df.columns)} columns)")


if __name__ == "__main__":
    main()
