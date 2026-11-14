"""Pull FIRMS active fire data for an area and date range.

    python3 -m pipeline.fetch_firms --key YOUR_MAP_KEY \
        --source MODIS_SP --bbox 73.8,29.5,77.5,32.5 \
        --start 2000-11-01 --end 2026-11-10 --out data/raw

The area API serves one to five days per request, so a long record means
thousands of requests. Three things are therefore load bearing:

  * every chunk is cached to disk, so an interrupted run resumes;
  * failures retry with exponential backoff, because the connection to
    FIRMS drops intermittently and a dropped request is not a missing fire;
  * requests are paced under the documented limit of 5,000 transactions per
    ten minutes, and a longer window counts as more than one transaction.

Check what a source actually covers before trusting a long pull:
https://firms.modaps.eosdis.nasa.gov/api/data_availability/
"""

from __future__ import annotations

import argparse
import os
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta

BASE = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
CHUNK_DAYS = 5
MAX_ATTEMPTS = 6
PACE_SECONDS = 0.35

SOURCES = [
    "MODIS_SP", "MODIS_NRT",
    "VIIRS_SNPP_SP", "VIIRS_SNPP_NRT",
    "VIIRS_NOAA20_SP", "VIIRS_NOAA20_NRT",
    "VIIRS_NOAA21_SP", "VIIRS_NOAA21_NRT",
]


def _get(url: str, timeout: int = 60) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "EmberWatch/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_chunk(key: str, source: str, bbox: str, start: date, days: int) -> str:
    """One API window, retried. Returns CSV text including its header."""
    url = f"{BASE}/{key}/{source}/{bbox}/{days}/{start.isoformat()}"
    delay = 2.0
    last = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            text = _get(url)
            low = text[:400].lower()
            if "invalid" in low and "key" in low:
                raise SystemExit(f"FIRMS rejected the MAP_KEY: {text[:200]}")
            if low.startswith("<!doctype") or "<html" in low:
                raise RuntimeError("got HTML instead of CSV")
            return text
        except SystemExit:
            raise
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, RuntimeError) as exc:
            last = str(exc)
            if attempt == MAX_ATTEMPTS:
                break
            time.sleep(delay)
            delay = min(delay * 2, 45)
    print(f"  ! giving up on {start} after {MAX_ATTEMPTS} attempts: {last}")
    return ""


def pull(
    key: str,
    source: str,
    bbox: str,
    start: date,
    end: date,
    out_dir: str,
    cache_dir: str = ".cache",
) -> str:
    """Fetch the whole range and write one concatenated CSV. Resumable."""
    tag = f"{source}_{bbox.replace(',', '_')}"
    cache = os.path.join(cache_dir, tag)
    os.makedirs(cache, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)

    windows: list[date] = []
    cur = start
    while cur <= end:
        windows.append(cur)
        cur += timedelta(days=CHUNK_DAYS)

    header = ""
    body: list[str] = []
    fetched = cached = empty = 0

    for i, win in enumerate(windows, 1):
        days = min(CHUNK_DAYS, (end - win).days + 1)
        path = os.path.join(cache, f"{win.isoformat()}_{days}.csv")

        if os.path.exists(path):
            with open(path) as fh:
                text = fh.read()
            cached += 1
        else:
            text = fetch_chunk(key, source, bbox, win, days)
            with open(path, "w") as fh:
                fh.write(text)
            fetched += 1
            time.sleep(PACE_SECONDS)

        lines = [ln for ln in text.splitlines() if ln.strip()]
        if not lines:
            empty += 1
            continue
        if not header:
            header = lines[0]
        body.extend(lines[1:])

        if i % 100 == 0 or i == len(windows):
            print(
                f"  {i}/{len(windows)} windows  "
                f"{len(body):,} rows  (new {fetched}, cached {cached}, empty {empty})"
            )

    if not header:
        raise SystemExit(
            f"no data returned for {source}. Check the MAP_KEY and that this "
            f"source covers {start} to {end} (see /api/data_availability/)."
        )

    kind = "modis" if "MODIS" in source else "viirs"
    out = os.path.join(out_dir, f"firms_{kind}_{source.lower()}.csv")
    with open(out, "w") as fh:
        fh.write(header + "\n")
        fh.write("\n".join(body) + "\n")
    print(f"  wrote {out}  ({len(body):,} detections)")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", required=True, help="FIRMS MAP_KEY")
    ap.add_argument("--source", required=True, choices=SOURCES)
    ap.add_argument("--bbox", required=True, help="west,south,east,north")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", default=date.today().isoformat())
    ap.add_argument("--out", default="data/raw")
    args = ap.parse_args()

    s = datetime.strptime(args.start, "%Y-%m-%d").date()
    e = datetime.strptime(args.end, "%Y-%m-%d").date()
    print(f"{args.source}  {args.bbox}  {s} to {e}")
    pull(args.key, args.source, args.bbox, s, e, args.out)


if __name__ == "__main__":
    main()
