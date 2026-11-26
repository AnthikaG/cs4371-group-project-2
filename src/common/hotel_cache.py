"""Read and save timestamped hotel API responses."""

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4

import requests


CACHE_ROOT = Path(__file__).resolve().parents[2] / "cache" / "hotels"
_locks = {source: Lock() for source in ("xotelo", "serpapi")}
_last_call = {}


class HotelServiceError(RuntimeError):
    """A provider could not return usable data."""


def _usable(record, params, result_list):
    data = record.get("data")
    if record.get("status") != 200 or not isinstance(data, dict) or data.get("error"):
        return False
    if result_list is None:
        return True
    result = data.get("result")
    if not isinstance(result, dict) or not isinstance(result.get(result_list), list):
        return False
    return result_list != "rates" or (
        result.get("chk_in") == params["chk_in"] and result.get("chk_out") == params["chk_out"]
        and result.get("currency", "USD") == params["currency"]
    )


def get_response(source, url, params, mode, *, api_key=None, result_list=None):
    """Return a response and its live flag. An offline cache miss returns None."""
    identity = json.dumps([url, params], sort_keys=True).encode()
    folder = CACHE_ROOT / source / hashlib.sha256(identity).hexdigest()
    if mode != "live":
        for path in sorted(folder.glob("*.json"), reverse=True):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if _usable(record, params, result_list):
                    return record, False
            except (OSError, ValueError, AttributeError):
                continue
        if mode == "cached":
            return None, False

    query = {**params, "api_key": api_key} if api_key else params
    with _locks[source]:
        elapsed = time.monotonic() - _last_call.get(source, -1)
        time.sleep(max(0, 1 - elapsed))
        _last_call[source] = time.monotonic()
        try:
            response = requests.get(url, params=query, timeout=(4, 10),
                                    headers={"User-Agent": "SafeStay-CS4371-Group08"})
        except requests.RequestException as exc:
            raise HotelServiceError(type(exc).__name__) from None
        now = datetime.now(timezone.utc)

    try:
        data = response.json()
    except ValueError:
        data = response.text
    record = {"endpoint": url, "params": params, "status": response.status_code,
              "retrieved_at": now.isoformat(), "data": data}
    encoded = json.dumps(record, ensure_ascii=False)
    if api_key:
        encoded = encoded.replace(api_key, "[REDACTED]")
        record = json.loads(encoded)

    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{now.strftime('%Y%m%dT%H%M%S%f')}-{uuid4().hex}.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(encoded, encoding="utf-8")
    temporary.replace(path)
    if not _usable(record, params, result_list):
        raise HotelServiceError(f"unusable response (HTTP {response.status_code})")
    return record, True
