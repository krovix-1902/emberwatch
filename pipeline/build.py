"""Run the full pipeline and emit the JSON the front end reads.

    python3 -m pipeline.build --area punjab --raw data/raw --out site/data

The front end is static: everything it needs is precomputed here. That is
deliberate, because a static site cannot fall over while a judge is
clicking through it.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import date

import duckdb

from . import calibrate, current, export_grid, grid, schema, stats

AREAS = {
    "punjab": {
        "label": "Punjab and Haryana, India",
        "bbox": (73.8, 29.5, 77.5, 32.5),
        "note": "Post-monsoon crop residue burning",
    },
    "portugal": {
        "label": "Central Portugal",
        "bbox": (-9.0, 39.0, -6.8, 41.0),
        "note": "Mediterranean wildfire regime",
    },
    "highlands": {
        "label": "Scottish Highlands",
        "bbox": (-5.5, 56.3, -3.0, 58.0),
        "note": "Sparse northern moorland burning",
    },
}


def _series(rows, sensor_filter=None, field=3):
    out = {}
    for r in rows:
        yr, mo, sensor = r[0], r[1], r[2]
        if sensor_filter and sensor != sensor_filter:
            continue
        out[(yr, mo)] = out.get((yr, mo), 0) + float(r[field] or 0)
    return out


def naive_splice(rows) -> dict[tuple[int, int], float]:
    """What a team that skipped harmonisation would plot.

    VIIRS where it exists, MODIS before that, no scaling. This is the
    control the seam test measures against.
    """
    modis = _series(rows, "MODIS")
    viirs = _series(rows, "VIIRS")
    keys = set(modis) | set(viirs)
    return {k: viirs.get(k, modis.get(k, 0.0)) for k in keys}


def harmonised_series(con, bbox, grid_deg) -> dict[tuple[int, int], float]:
    west, south, east, north = bbox
    rows = con.execute(
        f"""
        SELECT yr, mo, sum(harmonised_cd)
        FROM monthly_harmonised
        WHERE cell_x BETWEEN CAST(floor(? / {grid_deg}) AS INTEGER)
                         AND CAST(floor(? / {grid_deg}) AS INTEGER)
          AND cell_y BETWEEN CAST(floor(? / {grid_deg}) AS INTEGER)
                         AND CAST(floor(? / {grid_deg}) AS INTEGER)
        GROUP BY 1, 2 ORDER BY 1, 2
        """,
        [west, east, south, north],
    ).fetchall()
    return {(r[0], r[1]): float(r[2] or 0) for r in rows}


def factor_summary(con, bbox, grid_deg) -> dict:
    west, south, east, north = bbox
    rows = con.execute(
        f"""
        SELECT factor_level, count(*), median(factor)
        FROM monthly_harmonised
        WHERE basis = 'MODIS_scaled'
          AND cell_x BETWEEN CAST(floor(? / {grid_deg}) AS INTEGER)
                         AND CAST(floor(? / {grid_deg}) AS INTEGER)
          AND cell_y BETWEEN CAST(floor(? / {grid_deg}) AS INTEGER)
                         AND CAST(floor(? / {grid_deg}) AS INTEGER)
        GROUP BY 1 ORDER BY 2 DESC
        """,
        [west, east, south, north],
    ).fetchall()
    return {
        r[0]: {"months": int(r[1]), "median_factor": round(float(r[2] or 0), 3)}
        for r in rows
    }


def ablation(con, bbox, grid_deg: float, floor: float) -> list[dict]:
    """Quantify what each harmonisation step contributes to closing the seam.

    Three variants of the same area, measured the same way. This is the
    evidence that the method works, rather than the claim that it does.
    """
    out = []

    grid.build_cell_days(con, grid_deg=grid_deg, frp_floor=None)
    grid.monthly(con)
    # Keep the unfloored, unharmonised record: it is what a naive user of
    # FIRMS would plot, and the seam chart needs it as the control.
    con.execute("DROP TABLE IF EXISTS monthly_unfloored")
    con.execute(
        """
        CREATE TABLE monthly_unfloored AS
        SELECT cell_y, cell_x, yr, mo, sum(detections) AS detections
        FROM monthly_raw GROUP BY 1, 2, 3, 4
        """
    )
    rows = grid.area_monthly(con, bbox, grid_deg)
    naive_unfloored = naive_splice(rows)
    seam = stats.seam_test(naive_unfloored, naive_unfloored)["raw"]
    out.append({"variant": "Raw splice, no floor", "step_pct": seam.step_pct})

    grid.build_cell_days(con, grid_deg=grid_deg, frp_floor=floor)
    grid.monthly(con)
    rows = grid.area_monthly(con, bbox, grid_deg)
    naive_floored = naive_splice(rows)
    seam = stats.seam_test(naive_floored, naive_floored)["raw"]
    out.append({"variant": "Shared FRP floor", "step_pct": seam.step_pct})

    cal = calibrate.run(con)
    harm = harmonised_series(con, bbox, grid_deg)
    seam = stats.seam_test(naive_floored, harm)["harmonised"]
    out.append({"variant": "Floor plus calibration", "step_pct": seam.step_pct})
    return out


def run(raw_dir: str, out_dir: str, areas: list[str], grid_deg: float) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    con = duckdb.connect()

    n = schema.build_unified(con, raw_dir)
    cov = schema.coverage(con)

    floor = calibrate.modis_frp_floor(con)
    abl = ablation(con, AREAS[areas[0]]["bbox"], grid_deg, floor)

    grid.build_cell_days(con, grid_deg=grid_deg, frp_floor=floor)
    grid.monthly(con)
    cal = calibrate.run(con)

    synthetic = any(
        f.startswith("synthetic_") for f in os.listdir(raw_dir) if f.endswith(".csv")
    )

    manifest = {
        "generated": date.today().isoformat(),
        "synthetic": synthetic,
        "detections": n,
        "grid_deg": grid_deg,
        "frp_floor_mw": round(cal.frp_floor, 2),
        "overlap_years": [cal.overlap_start, cal.overlap_end],
        "global_ratio": round(cal.global_ratio, 3),
        "coverage": [
            {
                "sensor": c[0],
                "first_day": c[1].isoformat(),
                "last_day": c[2].isoformat(),
                "detections": int(c[3]),
                "platforms": int(c[4]),
            }
            for c in cov
        ],
        "areas": [],
    }

    for key in areas:
        spec = AREAS[key]
        bbox = spec["bbox"]
        rows = grid.area_monthly(con, bbox, grid_deg)
        raw = naive_splice(rows)
        harm = harmonised_series(con, bbox, grid_deg)
        if not harm:
            continue

        years = sorted({y for y, _ in harm})
        baseline = (years[0], max(years[0], years[-1] - 1))
        latest = max(harm)

        # The running month is incomplete, so it must not be scored against
        # complete months. Anomaly uses the last finished month; the running
        # month gets its own like-for-like month-to-date comparison.
        today = date.today()
        running = (today.year, today.month)
        complete = sorted(k for k in harm if k < running)
        last_complete = complete[-1] if complete else latest

        payload = {
            "key": key,
            "label": spec["label"],
            "note": spec["note"],
            "bbox": bbox,
            "years": years,
            "calendar": [
                {"year": y, "months": [round(harm.get((y, m), 0.0), 1) for m in range(1, 13)]}
                for y in years
            ],
            "raw_calendar": [
                {"year": y, "months": [round(raw.get((y, m), 0.0), 1) for m in range(1, 13)]}
                for y in years
            ],
            "baseline": list(baseline),
            "latest": {"year": latest[0], "month": latest[1]},
            "last_complete": {"year": last_complete[0], "month": last_complete[1]},
            "anomaly": stats.anomaly(
                harm, last_complete[0], last_complete[1], baseline
            ),
            "current": current.summarise(
                con, bbox, grid_deg, today.year, today.month, today.day
            ),
            "ablation": abl,
            "trend": stats.seasonal_mann_kendall(harm).__dict__,
            "climatology": stats.climatology(harm, baseline),
            "seam": {
                k: v.__dict__ for k, v in stats.seam_test(raw, harm).items()
            },
            "factors": factor_summary(con, bbox, grid_deg),
        }

        with open(os.path.join(out_dir, f"{key}.json"), "w") as fh:
            json.dump(payload, fh, separators=(",", ":"))
        manifest["areas"].append(
            {"key": key, "label": spec["label"], "note": spec["note"], "bbox": bbox}
        )

    manifest["ablation"] = abl
    manifest["ablation_area"] = AREAS[areas[0]]["label"]
    gridinfo = export_grid.export(
        con, os.path.join(out_dir, "grid.json"), grid_deg
    )
    manifest["grid"] = gridinfo

    with open(os.path.join(out_dir, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=1)

    con.close()
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="data/raw")
    ap.add_argument("--out", default="site/data")
    ap.add_argument("--grid", type=float, default=grid.GRID_DEG)
    ap.add_argument("--area", action="append", choices=sorted(AREAS))
    args = ap.parse_args()

    m = run(args.raw, args.out, args.area or ["punjab"], args.grid)
    print(f"detections : {m['detections']:,}")
    print(f"FRP floor  : {m['frp_floor_mw']} MW")
    print(f"overlap    : {m['overlap_years'][0]}-{m['overlap_years'][1]}")
    print(f"ratio      : {m['global_ratio']}")
    for a in m["areas"]:
        print(f"  wrote {a['key']}.json  ({a['label']})")


if __name__ == "__main__":
    main()
