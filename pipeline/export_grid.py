"""Export the harmonised record per grid cell, so the browser can answer
any area the user draws rather than only the areas we thought of.

Three compact, sparse payloads:

  cells  one entry per grid cell, with a flat [monthIndex, harmonised, naive]
         triple for every month that had any burning at all;
  mtd    cell-days inside the running month's day window, per cell per year,
         so a month-to-date comparison stays like-for-like anywhere;
  meta   the month axis and the grid origin needed to decode the above.

Zeros are omitted. Most cells are quiet in most months, so the file stays
small enough to ship with a static page.
"""

from __future__ import annotations

import json
import os
from calendar import monthrange
from datetime import date

import duckdb


def month_index(yr: int, mo: int, y0: int, m0: int) -> int:
    return (yr - y0) * 12 + (mo - m0)


def export(
    con: duckdb.DuckDBPyConnection,
    out_path: str,
    grid_deg: float,
    today: date | None = None,
) -> dict:
    today = today or date.today()

    span = con.execute(
        """
        SELECT min(yr), min(mo), max(yr), max(mo) FROM monthly_harmonised
        """
    ).fetchone()
    y_lo = int(span[0])
    # Month axis always starts in January of the first year, so decoding on
    # the client is plain arithmetic rather than a lookup table.
    m0, y0 = 1, y_lo
    y_hi, m_hi = int(span[2]), int(span[3])
    n_months = month_index(y_hi, m_hi, y0, m0) + 1

    # The naive control is raw unfloored detection counts: exactly what a
    # user who downloads FIRMS and counts rows per month would see. The
    # seam chart indexes both series to a pre-VIIRS baseline, so the
    # different units (detections vs cell-days) never share an axis.
    rows = con.execute(
        """
        SELECT COALESCE(h.cell_y, u.cell_y) AS cell_y,
               COALESCE(h.cell_x, u.cell_x) AS cell_x,
               COALESCE(h.yr, u.yr) AS yr,
               COALESCE(h.mo, u.mo) AS mo,
               h.harmonised_cd,
               u.detections AS naive_raw,
               h.factor, h.factor_level, h.factor_n
        FROM monthly_harmonised h
        FULL OUTER JOIN monthly_unfloored u
          ON h.cell_y = u.cell_y AND h.cell_x = u.cell_x
         AND h.yr = u.yr AND h.mo = u.mo
        WHERE COALESCE(h.harmonised_cd, 0) > 0 OR COALESCE(u.detections, 0) > 0
        ORDER BY 1, 2, 3, 4
        """
    ).fetchall()

    cells: dict[tuple[int, int], dict] = {}
    for cy, cx, yr, mo, harm, naive, factor, level, fn in rows:
        mi = month_index(int(yr), int(mo), y0, m0)
        if mi < 0 or mi >= n_months:
            continue
        key = (int(cy), int(cx))
        c = cells.setdefault(
            key, {"y": int(cy), "x": int(cx), "v": [], "f": {}}
        )
        c["v"].extend(
            [
                mi,
                round(float(harm or 0), 1),
                round(float(naive or 0), 1),
            ]
        )
        if level and level not in c["f"]:
            c["f"][level] = [round(float(factor or 1), 3), int(fn or 0)]

    # Month-to-date window for the running month, per cell per year.
    day_cutoff = today.day
    mtd_rows = con.execute(
        """
        SELECT cell_y, cell_x, yr, count(*)
        FROM cell_days
        WHERE sensor = 'VIIRS' AND mo = ? AND day(acq_date) <= ?
        GROUP BY 1, 2, 3
        """,
        [today.month, day_cutoff],
    ).fetchall()
    mtd: dict[str, list] = {}
    for cy, cx, yr, n in mtd_rows:
        mtd.setdefault(f"{int(cy)}:{int(cx)}", []).extend([int(yr), int(n)])

    payload = {
        "grid_deg": grid_deg,
        "year0": y0,
        "month0": m0,
        "n_months": n_months,
        "today": today.isoformat(),
        "mtd_month": today.month,
        "mtd_days": day_cutoff,
        "mtd_days_in_month": monthrange(today.year, today.month)[1],
        "cells": list(cells.values()),
        "mtd": mtd,
    }

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as fh:
        json.dump(payload, fh, separators=(",", ":"))

    return {
        "cells": len(cells),
        "months": n_months,
        "bytes": os.path.getsize(out_path),
    }
