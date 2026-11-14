"""Make MODIS and VIIRS comparable.

Two corrections, applied in order:

  1. A shared sensitivity floor. VIIRS detects weaker fires than MODIS, so
     counting every VIIRS detection would make the record look like it grew
     in 2012 when only the instrument changed. Both sensors are clipped to
     the weakest fire MODIS could realistically see.

  2. A per-cell, per-calendar-month scaling factor fitted on the years when
     both sensors flew. The MODIS era is expressed in VIIRS-equivalent
     cell-days, so one series runs from 2000 to now.

Every harmonised value carries the factor used, how many overlap years it
was fitted on, and whether it fell back to a coarser estimate. Visible
uncertainty is the point: a factor fitted on two years is not a fact.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb

# VIIRS S-NPP began 2012-01-20, so 2013 is the first complete year.
FIRST_FULL_VIIRS_YEAR = 2013

# Percentile of the MODIS FRP distribution taken as its detection floor.
FLOOR_PERCENTILE = 0.05

# Minimum overlap years before a per-cell factor is trusted.
MIN_CELL_YEARS = 5
MIN_MONTH_YEARS = 3


@dataclass
class Calibration:
    frp_floor: float
    overlap_start: int
    overlap_end: int
    global_ratio: float
    n_cell_factors: int
    n_month_factors: int


def modis_frp_floor(
    con: duckdb.DuckDBPyConnection, percentile: float = FLOOR_PERCENTILE
) -> float:
    """The weakest fire MODIS reliably reports, from its own distribution."""
    row = con.execute(
        """
        SELECT quantile_cont(frp, ?)
        FROM detections
        WHERE sensor = 'MODIS' AND frp IS NOT NULL AND frp > 0
        """,
        [percentile],
    ).fetchone()
    return float(row[0]) if row and row[0] is not None else 0.0


def overlap_window(con: duckdb.DuckDBPyConnection) -> tuple[int, int]:
    """Complete calendar years in which both sensors were flying."""
    last_modis, last_viirs = con.execute(
        """
        SELECT max(CASE WHEN sensor = 'MODIS' THEN yr END),
               max(CASE WHEN sensor = 'VIIRS' THEN yr END)
        FROM monthly_raw
        """
    ).fetchone()
    end = min(int(last_modis or 0), int(last_viirs or 0)) - 1
    return FIRST_FULL_VIIRS_YEAR, max(FIRST_FULL_VIIRS_YEAR, end)


def fit_ratios(con: duckdb.DuckDBPyConnection, start: int, end: int) -> tuple[int, int, float]:
    """Fit VIIRS-to-MODIS cell-day ratios at three levels of granularity.

    Medians, not means: fire data is heavily right-skewed and a single
    extreme season would otherwise set the factor for a whole cell.
    """
    con.execute("DROP TABLE IF EXISTS overlap_pairs")
    con.execute(
        """
        CREATE TABLE overlap_pairs AS
        SELECT
            m.cell_y, m.cell_x, m.yr, m.mo,
            m.cell_days AS modis_cd,
            v.cell_days AS viirs_cd,
            CAST(v.cell_days AS DOUBLE) / m.cell_days AS ratio
        FROM monthly_raw m
        JOIN monthly_raw v
          ON  m.cell_y = v.cell_y AND m.cell_x = v.cell_x
          AND m.yr = v.yr AND m.mo = v.mo
          AND v.sensor = 'VIIRS'
        WHERE m.sensor = 'MODIS'
          AND m.yr BETWEEN ? AND ?
          AND m.cell_days > 0
        """,
        [start, end],
    )

    # Level 1: this cell, this calendar month.
    con.execute("DROP TABLE IF EXISTS ratio_cell_month")
    con.execute(
        f"""
        CREATE TABLE ratio_cell_month AS
        SELECT cell_y, cell_x, mo,
               median(ratio) AS ratio,
               count(*)      AS n_years
        FROM overlap_pairs
        GROUP BY 1, 2, 3
        HAVING count(*) >= {MIN_CELL_YEARS}
        """
    )

    # Level 2: every cell, this calendar month.
    con.execute("DROP TABLE IF EXISTS ratio_month")
    con.execute(
        f"""
        CREATE TABLE ratio_month AS
        SELECT mo, median(ratio) AS ratio, count(*) AS n_obs
        FROM overlap_pairs
        GROUP BY 1
        HAVING count(*) >= {MIN_MONTH_YEARS}
        """
    )

    n_cell = con.execute("SELECT count(*) FROM ratio_cell_month").fetchone()[0]
    n_month = con.execute("SELECT count(*) FROM ratio_month").fetchone()[0]
    g = con.execute("SELECT median(ratio) FROM overlap_pairs").fetchone()[0]
    return n_cell, n_month, float(g or 1.0)


def harmonise(con: duckdb.DuckDBPyConnection, global_ratio: float) -> None:
    """Build `monthly_harmonised`: one comparable series per cell per month.

    VIIRS is the reference instrument wherever it flew, because it is the
    one that continues. The MODIS-only era is converted up to match it.
    """
    con.execute("DROP TABLE IF EXISTS monthly_harmonised")
    con.execute(
        f"""
        CREATE TABLE monthly_harmonised AS
        WITH wide AS (
            SELECT cell_y, cell_x, yr, mo,
                   sum(CASE WHEN sensor = 'MODIS' THEN cell_days END) AS modis_cd,
                   sum(CASE WHEN sensor = 'VIIRS' THEN cell_days END) AS viirs_cd
            FROM monthly_raw
            GROUP BY 1, 2, 3, 4
        )
        SELECT
            w.cell_y, w.cell_x, w.yr, w.mo,
            w.modis_cd,
            w.viirs_cd,
            COALESCE(rcm.ratio, rm.ratio, {global_ratio}) AS factor,
            CASE
                WHEN rcm.ratio IS NOT NULL THEN 'cell_month'
                WHEN rm.ratio  IS NOT NULL THEN 'month'
                ELSE 'global'
            END AS factor_level,
            COALESCE(rcm.n_years, 0) AS factor_n,
            CASE
                WHEN w.viirs_cd IS NOT NULL THEN CAST(w.viirs_cd AS DOUBLE)
                ELSE w.modis_cd * COALESCE(rcm.ratio, rm.ratio, {global_ratio})
            END AS harmonised_cd,
            CASE WHEN w.viirs_cd IS NOT NULL THEN 'VIIRS' ELSE 'MODIS_scaled' END AS basis
        FROM wide w
        LEFT JOIN ratio_cell_month rcm
               ON rcm.cell_y = w.cell_y AND rcm.cell_x = w.cell_x AND rcm.mo = w.mo
        LEFT JOIN ratio_month rm ON rm.mo = w.mo
        """
    )


def run(con: duckdb.DuckDBPyConnection) -> Calibration:
    """Full calibration pass. Assumes monthly_raw already exists."""
    floor = modis_frp_floor(con)
    start, end = overlap_window(con)
    n_cell, n_month, g = fit_ratios(con, start, end)
    harmonise(con, g)
    return Calibration(
        frp_floor=floor,
        overlap_start=start,
        overlap_end=end,
        global_ratio=g,
        n_cell_factors=n_cell,
        n_month_factors=n_month,
    )
