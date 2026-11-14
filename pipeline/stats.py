"""Baselines, anomalies, trend significance, and the seam test.

Fire counts are strongly right-skewed and seasonal, so this module avoids
anything that assumes normality or a single annual value:

  * anomalies are reported as a percentile rank within the same calendar
    month's historical distribution, not as a z-score;
  * trend uses the seasonal Mann-Kendall test, which is non-parametric and
    compares each month only against the same month in other years, with a
    Theil-Sen slope for the rate.

The seam test is the self-check: if harmonisation worked, the artificial
step where VIIRS joined the record should largely disappear.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

VIIRS_FIRST_FULL_YEAR = 2013
SEAM_WINDOW = 3


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


@dataclass
class Trend:
    direction: str
    z: float
    p_value: float
    slope_per_decade: float
    significant: bool
    n_years: int


@dataclass
class Seam:
    before_mean: float
    after_mean: float
    step_pct: float


def seasonal_mann_kendall(
    series: dict[tuple[int, int], float], alpha: float = 0.05
) -> Trend:
    """Seasonal Mann-Kendall over a {(year, month): value} series.

    Each calendar month is its own season, so seasonality cannot masquerade
    as trend. Months are only ever compared with the same month in other
    years.
    """
    by_month: dict[int, list[tuple[int, float]]] = {}
    for (yr, mo), val in series.items():
        by_month.setdefault(mo, []).append((yr, float(val)))

    s_total = 0.0
    var_total = 0.0
    slopes: list[float] = []
    years = {yr for yr, _ in series}

    for mo, pairs in by_month.items():
        pairs.sort()
        vals = [v for _, v in pairs]
        yrs = [y for y, _ in pairs]
        n = len(vals)
        if n < 3:
            continue

        s = 0
        for i in range(n - 1):
            for j in range(i + 1, n):
                s += (vals[j] > vals[i]) - (vals[j] < vals[i])
                if yrs[j] != yrs[i]:
                    slopes.append((vals[j] - vals[i]) / (yrs[j] - yrs[i]))
        s_total += s

        # Variance with a correction for tied values.
        counts: dict[float, int] = {}
        for v in vals:
            counts[v] = counts.get(v, 0) + 1
        ties = sum(t * (t - 1) * (2 * t + 5) for t in counts.values() if t > 1)
        var_total += (n * (n - 1) * (2 * n + 5) - ties) / 18.0

    if var_total <= 0 or not slopes:
        return Trend("insufficient data", 0.0, 1.0, 0.0, False, len(years))

    if s_total > 0:
        z = (s_total - 1) / math.sqrt(var_total)
    elif s_total < 0:
        z = (s_total + 1) / math.sqrt(var_total)
    else:
        z = 0.0

    p = 2 * (1 - _phi(abs(z)))
    slopes.sort()
    mid = len(slopes) // 2
    sen = (
        slopes[mid]
        if len(slopes) % 2
        else 0.5 * (slopes[mid - 1] + slopes[mid])
    )

    if p >= alpha:
        direction = "no significant trend"
    elif z > 0:
        direction = "rising"
    else:
        direction = "falling"

    return Trend(
        direction=direction,
        z=round(z, 3),
        p_value=round(p, 5),
        slope_per_decade=round(sen * 10, 3),
        significant=p < alpha,
        n_years=len(years),
    )


def climatology(
    series: dict[tuple[int, int], float],
    baseline: tuple[int, int],
) -> dict[int, dict[str, float]]:
    """Median and interquartile range per calendar month over the baseline."""
    lo, hi = baseline
    out: dict[int, dict[str, float]] = {}
    for mo in range(1, 13):
        vals = sorted(
            v for (yr, m), v in series.items() if m == mo and lo <= yr <= hi
        )
        if not vals:
            continue
        out[mo] = {
            "median": _quantile(vals, 0.5),
            "q25": _quantile(vals, 0.25),
            "q75": _quantile(vals, 0.75),
            "max": vals[-1],
            "n": len(vals),
        }
    return out


def _quantile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] + (pos - lo) * (sorted_vals[hi] - sorted_vals[lo])


def anomaly(
    series: dict[tuple[int, int], float],
    year: int,
    month: int,
    baseline: tuple[int, int],
) -> dict[str, float | str | None]:
    """Where one month sits in its own month's historical distribution."""
    lo, hi = baseline
    history = sorted(
        v for (yr, m), v in series.items() if m == month and lo <= yr <= hi and yr != year
    )
    value = series.get((year, month))
    if value is None or not history:
        return {"value": value, "percentile": None, "flag": "no baseline"}

    below = sum(1 for v in history if v < value)
    pct = 100.0 * below / len(history)

    # An empty month in a month that is always empty is not an anomaly.
    if value == 0 and max(history) == 0:
        return {
            "value": 0.0,
            "median": 0.0,
            "percentile": None,
            "flag": "no burning, normal for this month",
            "baseline_years": len(history),
            "vs_median_pct": None,
        }

    if pct >= 98:
        flag = "extreme"
    elif pct >= 90:
        flag = "elevated"
    elif pct <= 10:
        flag = "unusually quiet"
    else:
        flag = "within normal range"

    med = _quantile(history, 0.5)
    return {
        "value": round(value, 1),
        "median": round(med, 1),
        "percentile": round(pct, 1),
        "flag": flag,
        "baseline_years": len(history),
        "vs_median_pct": round(100 * (value - med) / med, 1) if med else None,
    }


def seam_test(
    raw: dict[tuple[int, int], float],
    harmonised: dict[tuple[int, int], float],
    join_year: int = VIIRS_FIRST_FULL_YEAR,
    window: int = SEAM_WINDOW,
) -> dict[str, Seam]:
    """Size of the step at the sensor change, before and after harmonising.

    A naive splice jumps when VIIRS joins because the instrument changed,
    not because burning did. The harmonised series should jump far less.
    """
    before_years = range(join_year - window - 1, join_year - 1)
    after_years = range(join_year, join_year + window)

    def annual(series: dict[tuple[int, int], float], years) -> float:
        totals = [
            sum(v for (yr, _), v in series.items() if yr == y) for y in years
        ]
        totals = [t for t in totals if t > 0]
        return sum(totals) / len(totals) if totals else 0.0

    result = {}
    for name, series in (("raw", raw), ("harmonised", harmonised)):
        b = annual(series, before_years)
        a = annual(series, after_years)
        step = (100.0 * (a - b) / b) if b else 0.0
        result[name] = Seam(
            before_mean=round(b, 1),
            after_mean=round(a, 1),
            step_pct=round(step, 1),
        )
    return result
