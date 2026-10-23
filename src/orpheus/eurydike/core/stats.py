"""Small statistics for EURYDIKE (Tabula §4.4): inverse normal, t quantile, number parsing and the conversion of a
reported outcome row to mean and standard deviation.

The model never converts (principle 4): a row holds the statistics as written in the paper; this module derives
mean and SD and writes the method next to them. Results are rounded to 3 decimals, half away from zero, as Tabula
(JavaScript `Math.round(x * 1000) / 1000` for positive values)."""
from __future__ import annotations

import math
import re

# ---------------------------------------------------------------- method labels (English, stored with the result)

REPORTED = "reported"
MEAN_LUO_IQR = "mean: Luo 2018 (median, IQR)"
MEAN_LUO_RANGE = "mean: Luo 2018 (median, range)"
SD_SE = "SD = SE·√n"
SD_CI_T = "SD from 95% CI (t)"
SD_CI_Z = "SD from 95% CI (z)"
MEAN_CI = "mean = CI midpoint"
SD_WAN_IQR = "SD: Wan 2014 (IQR)"
SD_WAN_RANGE = "SD: Wan 2014 (range)"

REFERENCES = {
    "Luo 2018": "Luo D, Wan X, Liu J, Tong T. Optimally estimating the sample mean from the sample size, median, "
                "mid-range, and/or mid-quartile range. Stat Methods Med Res. 2018;27(6):1785-1805.",
    "Wan 2014": "Wan X, Wang W, Liu J, Tong T. Estimating the sample mean and standard deviation from the sample size, "
                "median, range and/or interquartile range. BMC Med Res Methodol. 2014;14:135.",
    "Cochrane Handbook 6.5.2": "Higgins JPT et al. (eds). Cochrane Handbook for Systematic Reviews of Interventions, "
                               "section 6.5.2 (SD from SE and from confidence intervals).",
}


def round3(x: float) -> float:
    """Round half away from zero to 3 decimals (Tabula: Math.round(x*1000)/1000)."""
    return math.floor(x * 1000 + 0.5) / 1000


# ---------------------------------------------------------------- inverse normal (Acklam) and t quantile

_A = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
      1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
_B = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
      6.680131188771972e+01, -1.328068155288572e+01)
_C = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
      -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
_D = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00)
_P_LOW = 0.02425


def norm_inv(p: float) -> float:
    """Inverse of the standard normal distribution (Acklam's algorithm, relative error < 1.2e-9)."""
    if not 0.0 < p < 1.0:
        raise ValueError("p deve essere strettamente fra 0 e 1")
    if p < _P_LOW:
        q = math.sqrt(-2 * math.log(p))
        return ((((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5])
                / ((((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1))
    if p <= 1 - _P_LOW:
        q = p - 0.5
        r = q * q
        return ((((((_A[0] * r + _A[1]) * r + _A[2]) * r + _A[3]) * r + _A[4]) * r + _A[5]) * q
                / (((((_B[0] * r + _B[1]) * r + _B[2]) * r + _B[3]) * r + _B[4]) * r + 1))
    q = math.sqrt(-2 * math.log(1 - p))
    return -((((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5])
             / ((((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1))


_Z975 = 1.959963984540054


def t_inv_975(df: float) -> float:
    """0.975 quantile of Student's t with `df` degrees of freedom (Cornish-Fisher expansion in 1/df, four terms;
    error below 1e-3 for df >= 3, which is all the 95% CI conversion needs: it is used only with n < 60)."""
    if df <= 0:
        raise ValueError("i gradi di libertà devono essere positivi")
    z = _Z975
    g1 = (z ** 3 + z) / 4
    g2 = (5 * z ** 5 + 16 * z ** 3 + 3 * z) / 96
    g3 = (3 * z ** 7 + 19 * z ** 5 + 17 * z ** 3 - 15 * z) / 384
    g4 = (79 * z ** 9 + 776 * z ** 7 + 1482 * z ** 5 - 1920 * z ** 3 - 945 * z) / 92160
    return z + g1 / df + g2 / df ** 2 + g3 / df ** 3 + g4 / df ** 4


# ---------------------------------------------------------------- numbers as written

_NOT_NUMBER = re.compile(r"[^0-9eE+\-.]")


def num(v) -> float | None:
    """A number from a cell as written: accepts a decimal comma ("3,5"), the unicode minus, and drops other
    characters ("24 kPa" -> 24). Empty or unreadable -> None."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) else None
    s = str(v).strip().replace("−", "-").replace("–", "-")
    if not s:
        return None
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    s = _NOT_NUMBER.sub("", s)
    try:
        x = float(s)
    except ValueError:
        return None
    return x if math.isfinite(x) else None


# ---------------------------------------------------------------- mean and SD from a row

def to_mean_sd(row: dict) -> dict | None:
    """Mean and SD of one group from the statistics reported for it (Tabula `toMeanSD`, §4.4).

    Keys read: n, mean, sd, median, q1, q3, min, max, se, ci_l (or ci_low), ci_u (or ci_high).
    Order of attempts: reported mean+SD; mean from Luo 2018 (median+IQR, then median+range); SD from SE; SD (and,
    if missing, mean) from a 95% CI (t with n-1 df when n < 60, otherwise z = 3.92 total width); SD from Wan 2014
    (IQR, then range). Returns {mean, sd, method} rounded to 3 decimals (a missing part stays None), or None when
    neither mean nor SD can be obtained. The estimators need n >= 2 (n < 2 -> no conversion); an inverted IQR or
    range, a SE <= 0 or swapped CI bounds are not used (Tabula would return a negative SD)."""
    g = lambda *keys: next((x for x in (num(row.get(k)) for k in keys) if x is not None), None)  # noqa: E731
    n, mean, sd = g("n"), g("mean"), g("sd")
    median, q1, q3, lo, hi = g("median"), g("q1"), g("q3"), g("min"), g("max")
    se, ci_l, ci_u = g("se"), g("ci_l", "ci_low"), g("ci_u", "ci_high")
    if mean is not None and sd is not None:
        return {"mean": round3(mean), "sd": round3(sd), "method": REPORTED}
    ok_n = n is not None and n >= 2
    # sets that are written inconsistently are not used (never a negative SD): see row_problems for the messages
    if q1 is not None and q3 is not None and q1 > q3:
        q1 = q3 = None
    if lo is not None and hi is not None and lo > hi:
        lo = hi = None
    if se is not None and se <= 0:
        se = None
    if ci_l is not None and ci_u is not None and ci_l >= ci_u:
        ci_l = ci_u = None
    methods: list[str] = []
    if mean is None and ok_n and median is not None and q1 is not None and q3 is not None:
        mean = (0.7 + 0.39 / n) * (q1 + q3) / 2 + (0.3 - 0.39 / n) * median
        methods.append(MEAN_LUO_IQR)
    elif mean is None and ok_n and median is not None and lo is not None and hi is not None:
        w = 4 / (4 + n ** 0.75)
        mean = w * (lo + hi) / 2 + (1 - w) * median
        methods.append(MEAN_LUO_RANGE)
    if sd is None and ok_n and se is not None:
        sd = se * math.sqrt(n)
        methods.append(SD_SE)
    if sd is None and ok_n and ci_l is not None and ci_u is not None:
        if n < 60:
            k, label = 2 * t_inv_975(n - 1), SD_CI_T
        else:
            k, label = 3.92, SD_CI_Z
        sd = math.sqrt(n) * (ci_u - ci_l) / k
        methods.append(label)
        if mean is None:
            mean = (ci_l + ci_u) / 2
            methods.append(MEAN_CI)
    if sd is None and ok_n and q1 is not None and q3 is not None:
        sd = (q3 - q1) / (2 * norm_inv((0.75 * n - 0.125) / (n + 0.25)))
        methods.append(SD_WAN_IQR)
    elif sd is None and ok_n and lo is not None and hi is not None:
        sd = (hi - lo) / (2 * norm_inv((n - 0.375) / (n + 0.25)))
        methods.append(SD_WAN_RANGE)
    if mean is None and sd is None:
        return None
    return {"mean": None if mean is None else round3(mean), "sd": None if sd is None else round3(sd),
            "method": "; ".join(methods) if methods else REPORTED}


def row_problems(row: dict) -> list[str]:
    """Consistency of a row as written (contract eurydike-ma-v1, validator `coerenza`); messages in Italian.
    An inverted IQR or range, a non-positive SD or SE, or swapped CI bounds make the conversion meaningless."""
    g = lambda k: num(row.get(k))  # noqa: E731
    out = []
    n = g("n")
    if n is not None and (n < 2 or n != int(n)):
        out.append("n deve essere un intero ≥ 2")
    q1, med, q3 = g("q1"), g("median"), g("q3")
    if q1 is not None and q3 is not None and q1 > q3:
        out.append("IQR invertito (q1 > q3)")
    elif med is not None and ((q1 is not None and med < q1) or (q3 is not None and med > q3)):
        out.append("mediana fuori dall'IQR")
    lo, hi = g("min"), g("max")
    if lo is not None and hi is not None and lo > hi:
        out.append("range invertito (min > max)")
    for k in ("sd", "se"):
        x = g(k)
        if x is not None and x <= 0:
            out.append(f"{k.upper()} deve essere > 0")
    cl, cu = g("ci_l") if g("ci_l") is not None else g("ci_low"), g("ci_u") if g("ci_u") is not None else g("ci_high")
    if cl is not None and cu is not None and cl >= cu:
        out.append("IC con i limiti invertiti o uguali")
    m = g("mean")
    if m is not None and lo is not None and hi is not None and not lo <= m <= hi:
        out.append("media fuori dal range")
    return out
