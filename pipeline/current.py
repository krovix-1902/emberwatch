"""Month-to-date comparison that does not cheat.

The running month is incomplete. Comparing 14 days of November against 25
full Novembers makes every current month look quiet, which is exactly the
kind of error that turns a monitoring tool into a dangerous one.

So the current month is compared only against the same day-of-month window
in previous years. Today is the current VIIRS era, so no scaling is needed:
VIIRS is compared with VIIRS.
"""

from __future__ import annotations

from calendar import monthrange

import duckdb


def month_to_date(
    con: duckdb.DuckDBPyConnection,
    bbox: tuple[float, float, float, float],
    grid_deg: float,
    month: int,
    day_cutoff: int,
    sensor: str = "VIIRS",
) -> dict[int, float]:
    """Cell-days in `month`, days 1..day_cutoff, per year, for one sensor."""
    west, south, east, north = bbox
    rows = con.execute(
        f"""
        SELECT yr, count(*) AS cell_days
        FROM cell_days
        WHERE sensor = ?
          AND mo = ?
          AND day(acq_date) <= ?
          AND cell_x BETWEEN CAST(floor(? / {grid_deg}) AS INTEGER)
                         AND CAST(floor(? / {grid_deg}) AS INTEGER)
          AND cell_y BETWEEN CAST(floor(? / {grid_deg}) AS INTEGER)
                         AND CAST(floor(? / {grid_deg}) AS INTEGER)
        GROUP BY 1 ORDER BY 1
        """,
        [sensor, month, day_cutoff, west, east, south, north],
    ).fetchall()
    return {int(r[0]): float(r[1]) for r in rows}


def summarise(
    con: duckdb.DuckDBPyConnection,
    bbox: tuple[float, float, float, float],
    grid_deg: float,
    year: int,
    month: int,
    day_cutoff: int,
) -> dict:
    """Where the running month sits against the same window historically."""
    series = month_to_date(con, bbox, grid_deg, month, day_cutoff)
    value = series.pop(year, 0.0)
    history = sorted(series.values())
    days_in_month = monthrange(year, month)[1]

    out = {
        "year": year,
        "month": month,
        "days_elapsed": day_cutoff,
        "days_in_month": days_in_month,
        "complete": day_cutoff >= days_in_month,
        "value": round(value, 1),
        "comparison": "same day window in previous years",
        "baseline_years": len(history),
    }

    if not history:
        out.update({"percentile": None, "flag": "no baseline"})
        return out

    below = sum(1 for v in history if v < value)
    pct = 100.0 * below / len(history)
    mid = len(history) // 2
    med = (
        history[mid]
        if len(history) % 2
        else 0.5 * (history[mid - 1] + history[mid])
    )

    # Out of season, this window is normally empty. Calling an empty
    # off-season month "unusually quiet" would be actively misleading.
    if value == 0 and max(history) == 0:
        out.update(
            {
                "median": 0.0,
                "percentile": None,
                "flag": "no burning, normal for this month",
                "vs_median_pct": None,
            }
        )
        return out

    if pct >= 98:
        flag = "extreme"
    elif pct >= 90:
        flag = "elevated"
    elif pct <= 10:
        flag = "unusually quiet"
    else:
        flag = "within normal range"

    out.update(
        {
            "median": round(med, 1),
            "percentile": round(pct, 1),
            "flag": flag,
            "vs_median_pct": round(100 * (value - med) / med, 1) if med else None,
        }
    )
    return out
