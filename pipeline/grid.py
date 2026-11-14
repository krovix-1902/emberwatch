"""Bin detections onto a common grid and reduce them to cell-days.

Raw detection counts cannot be compared across sensors: a 375 m VIIRS pixel
and a 1 km MODIS pixel over the same burning field produce a different
number of points, and the count also rises every time another platform is
launched. So the unit of analysis is the *cell-day*: one grid cell on one
calendar day on which that sensor saw active burning at all.

Cell-days are close to invariant under pixel size and platform count, which
removes most of the inter-sensor bias before any calibration is applied.
"""

from __future__ import annotations

import duckdb

GRID_DEG = 0.25


def build_cell_days(
    con: duckdb.DuckDBPyConnection,
    grid_deg: float = GRID_DEG,
    frp_floor: float | None = None,
) -> int:
    """Create `cell_days`, one row per cell per day per sensor.

    frp_floor clips both sensors to a shared sensitivity, so the record
    means the same thing before and after VIIRS joined it.
    """
    where = "WHERE frp IS NOT NULL"
    if frp_floor is not None:
        where += f" AND frp >= {float(frp_floor)}"

    con.execute("DROP TABLE IF EXISTS cell_days")
    con.execute(
        f"""
        CREATE TABLE cell_days AS
        SELECT
            CAST(floor(latitude  / {grid_deg}) AS INTEGER) AS cell_y,
            CAST(floor(longitude / {grid_deg}) AS INTEGER) AS cell_x,
            acq_date,
            year(acq_date)  AS yr,
            month(acq_date) AS mo,
            sensor,
            count(*)        AS detections,
            sum(frp)        AS frp_sum,
            max(frp)        AS frp_max
        FROM detections
        {where}
        GROUP BY 1, 2, 3, 4, 5, 6
        """
    )
    return con.execute("SELECT count(*) FROM cell_days").fetchone()[0]


def monthly(con: duckdb.DuckDBPyConnection) -> None:
    """Roll cell-days up to one row per cell per month per sensor."""
    con.execute("DROP TABLE IF EXISTS monthly_raw")
    con.execute(
        """
        CREATE TABLE monthly_raw AS
        SELECT
            cell_y, cell_x, yr, mo, sensor,
            count(*)          AS cell_days,
            sum(detections)   AS detections,
            sum(frp_sum)      AS frp_sum
        FROM cell_days
        GROUP BY 1, 2, 3, 4, 5
        """
    )


def area_monthly(
    con: duckdb.DuckDBPyConnection,
    bbox: tuple[float, float, float, float] | None = None,
    grid_deg: float = GRID_DEG,
) -> list[tuple]:
    """Monthly totals for an area of interest, per sensor.

    bbox is west, south, east, north in degrees. None means every cell.
    """
    clause = ""
    params: list[float] = []
    if bbox:
        west, south, east, north = bbox
        clause = """
            WHERE cell_x >= CAST(floor(? / {g}) AS INTEGER)
              AND cell_x <= CAST(floor(? / {g}) AS INTEGER)
              AND cell_y >= CAST(floor(? / {g}) AS INTEGER)
              AND cell_y <= CAST(floor(? / {g}) AS INTEGER)
        """.format(g=grid_deg)
        params = [west, east, south, north]

    return con.execute(
        f"""
        SELECT yr, mo, sensor,
               sum(cell_days)  AS cell_days,
               sum(detections) AS detections
        FROM monthly_raw
        {clause}
        GROUP BY 1, 2, 3
        ORDER BY 1, 2, 3
        """,
        params,
    ).fetchall()
