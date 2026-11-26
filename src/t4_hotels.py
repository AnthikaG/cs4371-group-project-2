"""Seattle hotel search. Run from the repo root with python -m src.t4_hotels."""

import argparse
import csv
import json
import math
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from src.common.geo import find_neighbourhood, haversine_km
from src.common.hotel_cache import HotelServiceError, get_response


XOTELO = "https://data.xotelo.com/api"
SERPAPI = "https://serpapi.com/search.json"
DEFAULT_HOTELS = 55
PRICE_FIELDS = {
    "cheapest_site": None, "price_per_night": None, "total": None,
    "source": None, "retrieved_at": None, "provider_timestamp": None,
    "price_status": "unavailable", "live": None, "cache_age_seconds": None,
}
HOTEL_COLUMNS = (
    "hotel_key", "name", "lat", "lon", "neighbourhood", "check_in", "check_out",
    "nights", "guests", "currency", "prices_by_site", *PRICE_FIELDS,
    "catalog_source", "catalog_retrieved_at",
)


def _positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _hotel(key, name, coordinates, source, record):
    if not key or not isinstance(name, str) or not name.strip() or not isinstance(coordinates, dict):
        return None
    lat, lon = coordinates.get("latitude"), coordinates.get("longitude")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None
    neighbourhood = find_neighbourhood(lat, lon)
    if not neighbourhood:
        return None
    return {
        "hotel_key": key, "name": name.strip(), "lat": lat, "lon": lon,
        "neighbourhood": neighbourhood, "catalog_source": source,
        "catalog_retrieved_at": record["retrieved_at"], "prices_by_site": [], **PRICE_FIELDS,
    }


def _catalog(limit, mode):
    hotels = {}
    for offset in range(0, 300, 100):
        query = {"location_key": "g60878", "limit": 100, "offset": offset, "sort": "best_value"}
        record, _ = get_response("xotelo", XOTELO + "/list", query, mode, result_list="list")
        if record is None:
            break
        page = record["data"]["result"]["list"]
        for item in page:
            if isinstance(item, dict) and item.get("accommodation_type") == "Hotel":
                hotel = _hotel(item.get("key"), item.get("name"), item.get("geo") or {}, "xotelo", record)
                if hotel:
                    hotels.setdefault(hotel["hotel_key"], hotel)
            if len(hotels) == limit:
                return list(hotels.values())
        if len(page) < 100:
            break
    return list(hotels.values())


def _quote(offers, record, live, source):
    if not offers:
        return {}
    offers.sort(key=lambda offer: (offer["price_per_night"], offer["site"].casefold()))
    cheapest = offers[0]
    retrieved_at = record["retrieved_at"]
    age = datetime.now(timezone.utc) - datetime.fromisoformat(retrieved_at)
    timestamp = record["data"].get("timestamp")
    provider_time = None
    if _positive(timestamp):
        seconds = timestamp / 1000 if timestamp >= 10**12 else timestamp
        try:
            provider_time = datetime.fromtimestamp(seconds, timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            pass
    return {
        "prices_by_site": offers, "cheapest_site": cheapest["site"],
        "price_per_night": cheapest["price_per_night"], "total": cheapest["total"],
        "source": source, "retrieved_at": retrieved_at, "provider_timestamp": provider_time,
        "price_status": "live" if live else "cached", "live": live,
        "cache_age_seconds": None if live else max(0, int(age.total_seconds())),
    }


def _xotelo_hotels(stay, limit, mode):
    hotels = _catalog(limit, mode)
    for hotel in hotels:
        query = {
            "hotel_key": hotel["hotel_key"], "chk_in": stay["check_in"], "chk_out": stay["check_out"],
            "currency": "USD", "rooms": 1, "adults": stay["guests"],
        }
        record, live = get_response("xotelo", XOTELO + "/rates", query, mode, result_list="rates")
        if record is None:
            continue
        offers = []
        for item in record["data"]["result"]["rates"]:
            if not isinstance(item, dict):
                continue
            rate = item.get("rate")
            site = item.get("name") or item.get("code")
            if site and _positive(rate):
                offers.append({
                    "site": site, "site_code": item.get("code"), "price_per_night": rate,
                    "total": round(rate * stay["nights"], 2), "reported_tax": item.get("tax"),
                })
        hotel.update(_quote(offers, record, live, "xotelo"))
    return hotels


def _serp_offers(property_data, nights):
    offers = []
    for item in property_data.get("prices") or []:
        nightly = _serp_price(item.get("rate_per_night") or {})
        total = _serp_price(item.get("total_rate") or {})
        if item.get("source") and _positive(nightly):
            offers.append({
                "site": item["source"], "price_per_night": nightly,
                "total": total if _positive(total) else round(nightly * nights, 2),
            })
    return offers


def _serp_price(amounts):
    return amounts.get("extracted_before_taxes_fees") or amounts.get("extracted_display_price")


def _add_backup_quotes(hotels, stay, limit, mode, notices):
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    key = os.getenv("SERPAPI_API_KEY")
    backup_mode = "auto" if key and mode != "cached" else "cached"
    query = {
        "engine": "google_hotels", "q": "hotels in Seattle, Washington",
        "check_in_date": stay["check_in"], "check_out_date": stay["check_out"],
        "adults": stay["guests"], "currency": "USD", "hl": "en", "gl": "us",
    }
    for _ in range(4):
        try:
            record, live = get_response("serpapi", SERPAPI, query, backup_mode, api_key=key)
        except HotelServiceError as exc:
            notices.append(f"SerpApi unavailable ({exc}).")
            break
        if record is None:
            break
        for item in record["data"].get("properties") or []:
            if item.get("type") != "hotel" or not item.get("property_token"):
                continue
            hotel = _hotel("serpapi:" + item["property_token"], item.get("name"),
                           item.get("gps_coordinates") or {}, "serpapi", record)
            if hotel is None:
                continue
            offers = _serp_offers(item, stay["nights"])
            quote = _quote(offers, record, live, "serpapi")
            match = next((row for row in hotels if _same_hotel(row, hotel)), None)
            if match is None and len(hotels) < limit:
                hotels.append({**hotel, **quote})
            elif match is not None and match["price_per_night"] is None:
                match.update(quote)
        token = (record["data"].get("serpapi_pagination") or {}).get("next_page_token")
        have_all_prices = len(hotels) == limit and all(row["price_per_night"] is not None for row in hotels)
        if not token or have_all_prices:
            break
        query["next_page_token"] = token
    if any(row["source"] == "serpapi" for row in hotels):
        notices.append("SerpApi Google Hotels supplied backup prices.")
    elif not key and mode != "cached":
        notices.append("SerpApi backup has no matching cached price and SERPAPI_API_KEY is unset.")


def _same_hotel(left, right):
    if left["hotel_key"] == right["hotel_key"]:
        return True
    return (left["name"].casefold() == right["name"].casefold()
            and haversine_km(left["lat"], left["lon"], right["lat"], right["lon"]) <= 0.25)


def _stay(check_in, nights, guests):
    if isinstance(check_in, str):
        parsed = date.fromisoformat(check_in)
        if parsed.isoformat() != check_in:
            raise ValueError("check_in must use YYYY-MM-DD")
        check_in = parsed
    today = date.today()
    if type(check_in) is not date or not today < check_in <= today + timedelta(days=335):
        raise ValueError("check_in must be in the future, within approximately 11 months")
    if type(nights) is not int or nights < 1:
        raise ValueError("nights must be a positive integer")
    if type(guests) is not int or not 1 <= guests <= 32:
        raise ValueError("guests must be an adult count from 1 to 32 for one room")
    try:
        check_out = check_in + timedelta(days=nights)
    except OverflowError:
        raise ValueError("nights is too large") from None
    return {"check_in": check_in.isoformat(), "check_out": check_out.isoformat(),
            "nights": nights, "guests": guests, "currency": "USD"}


def _by_price(hotel):
    price = hotel["price_per_night"]
    return (math.inf if price is None else price, hotel["name"].casefold(), hotel["hotel_key"])


def search_hotels(check_in, nights, guests, max_price, exclude_neighbourhoods=None,
                  *, limit=DEFAULT_HOTELS, mode="auto"):
    """Return the hotel list, both budget picks, and any service notices.

    Auto reuses cached requests, live refreshes the sample, and cached stays offline.
    """
    stay = _stay(check_in, nights, guests)
    if not _positive(max_price):
        raise ValueError("max_price must be a positive nightly budget in USD")
    if type(limit) is not int or not 1 <= limit <= 100 or mode not in {"auto", "live", "cached"}:
        raise ValueError("Use a limit from 1 to 100 and mode auto, live, or cached")
    if isinstance(exclude_neighbourhoods, str):
        raise ValueError("exclude_neighbourhoods must be a collection of names")
    excluded = {name.strip().casefold() for name in (exclude_neighbourhoods or [])}
    notices = []
    try:
        hotels = _xotelo_hotels(stay, limit, mode)
    except HotelServiceError as exc:
        notices.append(f"Xotelo unavailable ({exc}). Using matching cached prices where available.")
        # Successful calls made before the failure are already on disk.
        hotels = _xotelo_hotels(stay, limit, "cached")

    if len(hotels) < limit or (notices and any(row["price_per_night"] is None for row in hotels)):
        _add_backup_quotes(hotels, stay, limit, mode, notices)
    for hotel in hotels:
        hotel.update(stay, search_limit=limit)
    hotels.sort(key=_by_price)
    priced = [hotel for hotel in hotels if hotel["price_per_night"] is not None]
    within_budget = [hotel for hotel in priced if hotel["price_per_night"] <= max_price]
    outside = next((hotel for hotel in within_budget
                    if hotel["neighbourhood"].casefold() not in excluded), None)
    if len(hotels) < limit:
        notices.append(f"Found {len(hotels)} of {limit} requested Seattle hotels.")
    if len(priced) < len(hotels):
        notices.append(f"No matching price was returned for {len(hotels) - len(priced)} hotels.")
    if not priced:
        notices.append("No hotel price is available for this exact stay and guest count.")
    return {
        **stay, "hotels": hotels, "cheapest": within_budget[0] if within_budget else None,
        "cheapest_outside_exclusions": outside, "notices": notices,
        "priced_hotels": len(priced), "catalog_hotels": len(hotels),
    }


def nearby(hotel, km=2, *, hotels=None):
    """List other hotels in range. Pass the search results to preserve live labels."""
    if not _positive(km):
        raise ValueError("km must be a positive distance")
    if hotels is None:
        hotels = search_hotels(hotel["check_in"], hotel["nights"], hotel["guests"], 1,
                               limit=hotel.get("search_limit", DEFAULT_HOTELS), mode="cached")["hotels"]
    alternatives = []
    for other in hotels:
        if other["hotel_key"] == hotel.get("hotel_key"):
            continue
        distance = haversine_km(hotel["lat"], hotel["lon"], other["lat"], other["lon"])
        if distance <= km:
            alternatives.append({**other, "distance_km": round(distance, 6)})
    return sorted(alternatives, key=_by_price)


def _write_csv(path, columns, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "prices_by_site": json.dumps(row["prices_by_site"], ensure_ascii=False)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("check_in", help="Future date in YYYY-MM-DD format")
    parser.add_argument("--nights", type=int, default=2)
    parser.add_argument("--guests", type=int, default=2)
    parser.add_argument("--budget", type=float, default=150)
    parser.add_argument("--exclude", action="append", default=[], metavar="NEIGHBOURHOOD")
    parser.add_argument("--mode", choices=("auto", "live", "cached"), default="auto")
    parser.add_argument("--demo", action="store_true", help="Quote only 20 hotels")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--no-export", action="store_true")
    args = parser.parse_args()
    try:
        result = search_hotels(args.check_in, args.nights, args.guests, args.budget, args.exclude,
                               limit=20 if args.demo else DEFAULT_HOTELS, mode=args.mode)
    except ValueError as exc:
        parser.error(str(exc))

    hotels = result["hotels"]
    print(f"{result['priced_hotels']} priced of {len(hotels)} Seattle hotels")
    for notice in result["notices"]:
        print(notice)
    for label, pick in (("Cheapest", result["cheapest"]),
                        ("Outside exclusions", result["cheapest_outside_exclusions"])):
        if pick is None:
            print(f"{label}: no matching price within budget")
            continue
        age = "" if pick["live"] else f", {pick['cache_age_seconds']} s old"
        print(f"{label}: {pick['name']} ({pick['neighbourhood']}), "
              f"USD {pick['price_per_night']:.2f}/night on {pick['cheapest_site']}, "
              f"USD {pick['total']:.2f} stay, {pick['source']} "
              f"{pick['price_status']} at {pick['retrieved_at']}{age}")

    alternatives = []
    for hotel in hotels[:2]:
        rows = nearby(hotel, hotels=hotels)
        print(f"{len(rows)} alternatives within 2 km of {hotel['name']}")
        for row in rows:
            alternatives.append({**row, "anchor_key": hotel["hotel_key"], "anchor_name": hotel["name"]})
    if not args.no_export and result["priced_hotels"]:
        _write_csv(args.output_dir / "t4_hotels.csv", HOTEL_COLUMNS, hotels)
        _write_csv(args.output_dir / "t4_nearby.csv",
                   ("anchor_key", "anchor_name", "distance_km", *HOTEL_COLUMNS), alternatives)
    return 0 if result["priced_hotels"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
