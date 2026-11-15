"""Rebuild per-sensor CSVs from the window cache and report coverage.

The parallel pull writes one small file per 5-day window. This stitches
every cached window for each source into one CSV under data/raw/ and says
how many windows are still missing, so the pull can be rerun until clean.
"""

from __future__ import annotations

import glob
import os
import sys
from datetime import date, datetime, timedelta

CACHE = ".cache"
OUT = "data/raw"

# source -> (start, end) that the pull is expected to cover
EXPECT = {
    "MODIS_SP": ("2000-11-01", "2026-08-31"),
    "MODIS_NRT": ("2026-09-01", "2026-11-14"),
    "VIIRS_SNPP_SP": ("2012-01-20", "2026-07-31"),
    "VIIRS_SNPP_NRT": ("2026-08-01", "2026-11-02"),
    "VIIRS_NOAA20_NRT": ("2026-08-01", "2026-11-14"),
}
CHUNK = 5


def windows(start: str, end: str) -> list[str]:
    s = datetime.strptime(start, "%Y-%m-%d").date()
    e = datetime.strptime(end, "%Y-%m-%d").date()
    out = []
    cur = s
    while cur <= e:
        days = min(CHUNK, (e - cur).days + 1)
        out.append(f"{cur.isoformat()}_{days}.csv")
        cur += timedelta(days=CHUNK)
    return out


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    incomplete = 0
    for source, (start, end) in EXPECT.items():
        dirs = glob.glob(os.path.join(CACHE, f"{source}_*"))
        if not dirs:
            print(f"{source:18s} no cache yet")
            incomplete += 1
            continue
        cdir = dirs[0]
        want = windows(start, end)
        have, missing, empty = [], [], 0
        for w in want:
            p = os.path.join(cdir, w)
            if not os.path.exists(p):
                missing.append(w)
                continue
            with open(p) as fh:
                txt = fh.read()
            if not txt.strip():
                # a failed window was cached as empty; refetch it
                os.remove(p)
                missing.append(w)
                continue
            have.append(txt)

        header, body = "", []
        for txt in have:
            lines = [ln for ln in txt.splitlines() if ln.strip()]
            if not lines:
                continue
            if lines[0].startswith("latitude"):
                if not header:
                    header = lines[0]
                body.extend(lines[1:])
            else:
                body.extend(lines)

        kind = "modis" if "MODIS" in source else "viirs"
        if header:
            path = os.path.join(OUT, f"firms_{kind}_{source.lower()}.csv")
            with open(path, "w") as fh:
                fh.write(header + "\n")
                if body:
                    fh.write("\n".join(body) + "\n")
        print(
            f"{source:18s} {len(have):5d}/{len(want):5d} windows  "
            f"{len(body):9,d} detections  missing {len(missing)}"
        )
        if missing:
            incomplete += 1
    print("COMPLETE" if not incomplete else f"INCOMPLETE ({incomplete} sources)")
    return 0 if not incomplete else 1


if __name__ == "__main__":
    sys.exit(main())
