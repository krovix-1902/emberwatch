"""Generate FIRMS-shaped synthetic detections for development.

This exists so the pipeline, the statistics and the front end can be built
and validated before the real archive lands. It deliberately reproduces the
three properties that make harmonisation necessary:

  1. MODIS starts 2000-11-01, VIIRS starts 2012-01-20, so there is a seam.
  2. VIIRS resolves finer, so one fire yields more pixels than under MODIS.
  3. VIIRS detects weaker fires, so its FRP distribution reaches lower.

It is NOT science. Every output file is written with a `synthetic_` prefix
and the build refuses to publish a site from synthetic data unless asked.
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import numpy as np

MODIS_START = date(2000, 11, 1)
VIIRS_START = date(2012, 1, 20)
NOAA20_START = date(2018, 4, 1)
NOAA21_START = date(2024, 1, 17)

# Minimum fire radiative power each sensor can realistically pick up (MW).
MODIS_FRP_FLOOR = 8.0
VIIRS_FRP_FLOOR = 1.5

COLUMNS_MODIS = (
    "latitude,longitude,brightness,scan,track,acq_date,acq_time,satellite,"
    "instrument,confidence,version,bright_t31,frp,daynight,type"
)
COLUMNS_VIIRS = (
    "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,"
    "instrument,confidence,version,bright_ti5,frp,daynight,type"
)


# Each area burns in a different season, so the demo is not three copies
# of the same curve.
PROFILES = {
    # Post-monsoon crop residue burning, with a smaller spring peak.
    "punjab": ({10: 9.0, 11: 7.0, 4: 2.2, 5: 3.0, 9: 1.6, 12: 1.1}, 0.35, 2.2),
    # Mediterranean summer wildfire season.
    "portugal": ({7: 6.0, 8: 8.0, 9: 4.0, 6: 2.4, 10: 1.5}, 0.25, 1.4),
    # Sparse spring muirburn under UK burning rules.
    "highlands": ({3: 2.0, 4: 2.6, 5: 1.4, 2: 0.9}, 0.08, 0.35),
}


def _seasonal(month: int, profile: str) -> float:
    peaks, floor, _ = PROFILES[profile]
    return peaks.get(month, floor)


def _trend(year: int) -> float:
    """Gentle rise plus two anomalous years to give the anomaly flag work."""
    base = 1.0 + 0.018 * (year - 2001)
    if year in (2016, 2023):
        base *= 1.7
    if year in (2020,):
        base *= 0.6
    return base


def generate(
    out_dir: str,
    bbox: tuple[float, float, float, float] = (73.8, 29.5, 77.5, 32.5),
    end: date | None = None,
    seed: int = 1902,
    profile: str = "punjab",
) -> dict[str, int]:
    """Write synthetic_<profile>_modis.csv and _viirs.csv into out_dir."""
    rng = np.random.default_rng(seed)
    intensity = PROFILES[profile][2]
    end = end or date.today()
    west, south, east, north = bbox
    os.makedirs(out_dir, exist_ok=True)

    modis_rows: list[str] = []
    viirs_rows: list[str] = []

    day = MODIS_START
    while day <= end:
        lam = intensity * _seasonal(day.month, profile) * _trend(day.year)
        n_fires = rng.poisson(lam)
        if n_fires:
            # True fire intensity: heavy tailed, most fires are small.
            frp = rng.lognormal(mean=1.1, sigma=1.25, size=n_fires)
            lat = rng.uniform(south, north, n_fires)
            lon = rng.uniform(west, east, n_fires)

            for i in range(n_fires):
                f = float(frp[i])
                la, lo = float(lat[i]), float(lon[i])

                if day >= MODIS_START and f >= MODIS_FRP_FLOOR:
                    # Terra ~10:30, Aqua ~13:30 local overpass.
                    for sat, hhmm in (("T", 1035), ("A", 1340)):
                        if rng.random() < 0.55:
                            conf = int(min(100, max(0, rng.normal(72, 18))))
                            modis_rows.append(
                                f"{la:.5f},{lo:.5f},{320 + f * 0.4:.1f},1.1,1.0,"
                                f"{day.isoformat()},{hhmm:04d},{sat},MODIS,"
                                f"{conf},6.1NRT,295.3,{f:.1f},D,0"
                            )

                if day >= VIIRS_START and f >= VIIRS_FRP_FLOOR:
                    sats = ["N"]
                    if day >= NOAA20_START:
                        sats.append("1")
                    if day >= NOAA21_START:
                        sats.append("2")
                    # Finer pixels split one fire across more detections.
                    pixels = 1 + rng.poisson(0.9)
                    for sat in sats:
                        if rng.random() > 0.7:
                            continue
                        for _ in range(pixels):
                            conf = rng.choice(
                                ["l", "n", "h"], p=[0.12, 0.63, 0.25]
                            )
                            jla = la + rng.normal(0, 0.004)
                            jlo = lo + rng.normal(0, 0.004)
                            viirs_rows.append(
                                f"{jla:.5f},{jlo:.5f},{330 + f * 0.5:.1f},0.39,0.36,"
                                f"{day.isoformat()},{1325:04d},{sat},VIIRS,"
                                f"{conf},2.0NRT,290.1,{f / max(1, pixels):.1f},D,0"
                            )
        day += timedelta(days=1)

    modis_path = os.path.join(out_dir, f"synthetic_{profile}_modis.csv")
    viirs_path = os.path.join(out_dir, f"synthetic_{profile}_viirs.csv")
    with open(modis_path, "w") as fh:
        fh.write(COLUMNS_MODIS + "\n")
        fh.write("\n".join(modis_rows) + "\n")
    with open(viirs_path, "w") as fh:
        fh.write(COLUMNS_VIIRS + "\n")
        fh.write("\n".join(viirs_rows) + "\n")

    return {"modis": len(modis_rows), "viirs": len(viirs_rows)}


AREA_BBOX = {
    "punjab": (73.8, 29.5, 77.5, 32.5),
    "portugal": (-9.0, 39.0, -6.8, 41.0),
    "highlands": (-5.5, 56.3, -3.0, 58.0),
}

if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "data/raw"
    for i, (name, box) in enumerate(AREA_BBOX.items()):
        c = generate(target, bbox=box, seed=1902 + i * 17, profile=name)
        print(f"{name:10s} {c['modis']:>7,} MODIS  {c['viirs']:>7,} VIIRS")
