"""Collapse FIRMS MODIS and VIIRS CSV exports into one common schema.

Both sensors ship similar but not identical columns. MODIS carries
`brightness` / `bright_t31` and a numeric 0-100 confidence; VIIRS carries
`bright_ti4` / `bright_ti5` and a categorical low/nominal/high confidence.
Everything downstream reads the unified table only, never a raw file.
"""

from __future__ import annotations

import glob
import os

import duckdb

# FIRMS documents MODIS confidence as a 0-100 percentage. These are the
# conventional band cut points used to map it onto the VIIRS categorical
# scale. Verify against the FIRMS attribute table before quoting them.
MODIS_CONF_NOMINAL = 30
MODIS_CONF_HIGH = 80

SENSORS = {
    "modis": {
        "glob": "*modis*.csv",
        "sensor": "MODIS",
        # 1 km nominal fire pixel
        "nominal_res_km": 1.0,
    },
    "viirs": {
        "glob": "*viirs*.csv",
        "sensor": "VIIRS",
        # 375 m I-band fire pixel
        "nominal_res_km": 0.375,
    },
}

# Columns every FIRMS export has, so the loader can stay defensive about
# the sensor-specific brightness columns it does not need.
REQUIRED = [
    "latitude",
    "longitude",
    "acq_date",
    "acq_time",
    "satellite",
    "confidence",
    "frp",
    "daynight",
]


def _files(raw_dir: str, pattern: str) -> list[str]:
    return sorted(glob.glob(os.path.join(raw_dir, pattern)))


def _confidence_expr(sensor: str) -> str:
    """Normalise both confidence encodings to low / nominal / high."""
    if sensor == "MODIS":
        return f"""
            CASE
                WHEN TRY_CAST(confidence AS INTEGER) IS NULL THEN 'unknown'
                WHEN TRY_CAST(confidence AS INTEGER) >= {MODIS_CONF_HIGH} THEN 'high'
                WHEN TRY_CAST(confidence AS INTEGER) >= {MODIS_CONF_NOMINAL} THEN 'nominal'
                ELSE 'low'
            END
        """
    return """
        CASE lower(trim(CAST(confidence AS VARCHAR)))
            WHEN 'h' THEN 'high'
            WHEN 'high' THEN 'high'
            WHEN 'n' THEN 'nominal'
            WHEN 'nominal' THEN 'nominal'
            WHEN 'l' THEN 'low'
            WHEN 'low' THEN 'low'
            ELSE 'unknown'
        END
    """


def build_unified(con: duckdb.DuckDBPyConnection, raw_dir: str) -> int:
    """Create the `detections` table from every CSV in raw_dir.

    Returns the row count. Raises if neither sensor has any files, because
    harmonisation is meaningless with one sensor.
    """
    con.execute("DROP TABLE IF EXISTS detections")
    con.execute(
        """
        CREATE TABLE detections (
            latitude   DOUBLE,
            longitude  DOUBLE,
            acq_ts     TIMESTAMP,
            acq_date   DATE,
            sensor     VARCHAR,
            satellite  VARCHAR,
            confidence VARCHAR,
            frp        DOUBLE,
            daynight   VARCHAR
        )
        """
    )

    loaded = {}
    for key, spec in SENSORS.items():
        files = _files(raw_dir, spec["glob"])
        if not files:
            loaded[key] = 0
            continue

        con.execute(
            f"""
            INSERT INTO detections
            SELECT
                CAST(latitude  AS DOUBLE),
                CAST(longitude AS DOUBLE),
                CAST(acq_date AS DATE)
                    + to_hours(CAST(acq_time AS INTEGER) // 100)
                    + to_minutes(CAST(acq_time AS INTEGER) % 100),
                CAST(acq_date AS DATE),
                '{spec["sensor"]}',
                CAST(satellite AS VARCHAR),
                {_confidence_expr(spec["sensor"])},
                TRY_CAST(frp AS DOUBLE),
                CAST(daynight AS VARCHAR)
            FROM read_csv($files, union_by_name := true, filename := false,
                          sample_size := -1, ignore_errors := true)
            """,
            {"files": files},
        )
        loaded[key] = con.execute(
            "SELECT count(*) FROM detections WHERE sensor = ?",
            [spec["sensor"]],
        ).fetchone()[0]

    if not any(loaded.values()):
        raise RuntimeError(f"no FIRMS CSVs found under {raw_dir}")

    con.execute("CREATE INDEX IF NOT EXISTS det_date ON detections(acq_date)")
    total = con.execute("SELECT count(*) FROM detections").fetchone()[0]
    return total


def coverage(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Per-sensor record span, used to pick the overlap calibration window."""
    return con.execute(
        """
        SELECT sensor,
               min(acq_date)  AS first_day,
               max(acq_date)  AS last_day,
               count(*)       AS detections,
               count(DISTINCT satellite) AS platforms
        FROM detections
        GROUP BY sensor
        ORDER BY sensor
        """
    ).fetchall()
