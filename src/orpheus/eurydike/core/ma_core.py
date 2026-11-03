"""EURYDIKE E9 — meta-analysis core: effect sizes, pooling, heterogeneity, prediction interval, Egger, leave-one-out.

Pure functions, standard library only (budget zero: no scipy, no R). The definitions follow metafor (Viechtbauer 2010)
so that the results can be checked against it (gate E9: estimate, CI, tau², I², Q and Egger equal to metafor within
1e-4 on reference datasets, tests in tests/test_eurydike_ma_core.py):

- MD: yi = m1 - m2, vi = sd1²/n1 + sd2²/n2 (escalc measure="MD", vtype="LS");
- SMD: Hedges' g = J·d with the exact correction J = Γ(m/2) / (√(m/2)·Γ((m-1)/2)), m = n1 + n2 - 2, d on the pooled SD,
  vi = 1/n1 + 1/n2 + g² / (2(n1 + n2)) (escalc measure="SMD", vtype="LS");
- FE: inverse-variance weights; RE-DL: DerSimonian-Laird tau²; RE-REML: restricted maximum likelihood tau² (fixed-point
  iteration of the REML equation, tau² ≥ 0);
- CI: Wald (z) or Hartung-Knapp-Sidik-Jonkman (t with k-1 df, rma(test="knha"), no truncation of the scale factor);
- Q from the fixed-effect weights; I² and H² as metafor: FE I² = (Q - df)/Q, RE I² = tau² / (tau² + s²) with the
  "typical" within-study variance s² = (k-1)·Σw / ((Σw)² - Σw²);
- prediction interval as predict.rma: mu ± crit·√(tau² + SE²), crit = z (Wald) or t(k-1) (HKSJ); none for FE;
- Egger: regtest(model="lm"), weighted regression of yi on sei with weights 1/vi and multiplicative dispersion;
  the test is on the slope (t, k-2 df); the "limit estimate" is the intercept. Run only with k ≥ 10 (Cochrane 13.3.5.4).
The model never computes anything here (principle 4): every number comes from the confirmed outcome rows.
"""
from __future__ import annotations

import math

VERSION = "ma-core-1"
Z975 = 1.959963984540054
MEASURES = ("MD", "SMD")
MODELS = ("FE", "RE-DL", "RE-REML")
CI_METHODS = ("wald", "hksj")
EGGER_MIN_K = 10


class MAError(ValueError):
    """A problem with the data of an analysis (message in Italian, for the user)."""


# ------------------------------------------------------------------------------------------------ distributions

def norm_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction of the incomplete beta function (modified Lentz)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 1000):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta function I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbt = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(lbt) * _betacf(a, b, x) / a
    return 1.0 - math.exp(lbt) * _betacf(b, a, 1.0 - x) / b


def t_cdf(t: float, df: float) -> float:
    x = df / (df + t * t)
    tail = 0.5 * betainc(df / 2.0, 0.5, x)
    return 1.0 - tail if t > 0 else tail


def t_pdf(t: float, df: float) -> float:
    return math.exp(math.lgamma((df + 1) / 2) - math.lgamma(df / 2) - 0.5 * math.log(df * math.pi)
                    - (df + 1) / 2 * math.log1p(t * t / df))


def t_ppf(p: float, df: float) -> float:
    """Quantile of Student's t (Newton's method on t_cdf from the normal quantile, then bisection if needed)."""
    if not 0.0 < p < 1.0:
        raise ValueError("p deve essere fra 0 e 1")
    if p == 0.5:
        return 0.0
    from .stats import norm_inv
    x = norm_inv(p)
    for _ in range(100):
        f = t_cdf(x, df) - p
        step = f / max(t_pdf(x, df), 1e-300)
        x -= step
        if abs(step) < 1e-13 * max(1.0, abs(x)):
            return x
    lo, hi = (-1e6, 0.0) if p < 0.5 else (0.0, 1e6)
    for _ in range(300):
        mid = (lo + hi) / 2
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _gammainc_upper(a: float, x: float) -> float:
    """Regularized upper incomplete gamma Q(a, x)."""
    if x <= 0:
        return 1.0
    gln = math.lgamma(a)
    if x < a + 1.0:                                   # series for P(a, x)
        ap, s = a, 1.0 / a
        delta = s
        for _ in range(10000):
            ap += 1.0
            delta *= x / ap
            s += delta
            if abs(delta) < abs(s) * 1e-16:
                break
        return 1.0 - s * math.exp(-x + a * math.log(x) - gln)
    tiny = 1e-300                                     # continued fraction for Q(a, x)
    b = x + 1.0 - a
    c, d = 1.0 / tiny, 1.0 / b
    h = d
    for i in range(1, 10000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = b + an / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-16:
            break
    return math.exp(-x + a * math.log(x) - gln) * h


def chi2_sf(x: float, df: float) -> float:
    return _gammainc_upper(df / 2.0, x / 2.0)


# ------------------------------------------------------------------------------------------------ effect sizes

def _check_group(m, sd, n, which: str) -> None:
    for v, name in ((m, "media"), (sd, "DS"), (n, "n")):
        if v is None or not math.isfinite(v):
            raise MAError(f"{which}: manca {name}")
    if sd <= 0:
        raise MAError(f"{which}: la DS deve essere > 0")
    if n < 2 or n != int(n):
        raise MAError(f"{which}: n deve essere un intero ≥ 2")


def mean_difference(m1: float, sd1: float, n1: float, m2: float, sd2: float, n2: float) -> dict:
    _check_group(m1, sd1, n1, "gruppo sperimentale")
    _check_group(m2, sd2, n2, "gruppo di controllo")
    return {"yi": m1 - m2, "vi": sd1 ** 2 / n1 + sd2 ** 2 / n2}


def hedges_j(m: float) -> float:
    """Exact small-sample correction of Hedges (1981), computed with log-gamma (no overflow for large m)."""
    return math.exp(math.lgamma(m / 2) - 0.5 * math.log(m / 2) - math.lgamma((m - 1) / 2))


def hedges_g(m1: float, sd1: float, n1: float, m2: float, sd2: float, n2: float) -> dict:
    _check_group(m1, sd1, n1, "gruppo sperimentale")
    _check_group(m2, sd2, n2, "gruppo di controllo")
    sp = math.sqrt(((n1 - 1) * sd1 ** 2 + (n2 - 1) * sd2 ** 2) / (n1 + n2 - 2))
    d = (m1 - m2) / sp
    j = hedges_j(n1 + n2 - 2)
    g = j * d
    return {"yi": g, "vi": 1 / n1 + 1 / n2 + g ** 2 / (2 * (n1 + n2)), "d": d, "j": j, "sd_pooled": sp}


def effect(measure: str, m1, sd1, n1, m2, sd2, n2) -> dict:
    if measure == "MD":
        return mean_difference(m1, sd1, n1, m2, sd2, n2)
    if measure == "SMD":
        return hedges_g(m1, sd1, n1, m2, sd2, n2)
    raise MAError(f"misura sconosciuta: una di {', '.join(MEASURES)}")


# ------------------------------------------------------------------------------------------------ tau²

def tau2_dl(yi: list[float], vi: list[float]) -> float:
    w = [1 / v for v in vi]
    sw, sw2 = sum(w), sum(x * x for x in w)
    mu = sum(a * b for a, b in zip(w, yi)) / sw
    q = sum(a * (y - mu) ** 2 for a, y in zip(w, yi))
    c = sw - sw2 / sw
    return max(0.0, (q - (len(yi) - 1)) / c) if c > 0 else 0.0


def tau2_reml(yi: list[float], vi: list[float], *, tol: float = 1e-12, max_iter: int = 100000) -> float:
    """REML estimate of tau² (fixed point of the REML estimating equation, Viechtbauer 2005), truncated at 0."""
    tau2 = tau2_dl(yi, vi)
    for _ in range(max_iter):
        w = [1 / (v + tau2) for v in vi]
        sw = sum(w)
        mu = sum(a * y for a, y in zip(w, yi)) / sw
        sw2 = sum(a * a for a in w)
        new = sum(a * a * ((y - mu) ** 2 - v) for a, y, v in zip(w, yi, vi)) / sw2 + 1 / sw
        new = max(0.0, new)
        if abs(new - tau2) < tol:
            return new
        tau2 = new
    raise MAError("REML non converge: prova DL")


# ------------------------------------------------------------------------------------------------ pooling

def pool(yi: list[float], vi: list[float], *, model: str = "RE-REML", ci: str = "wald") -> dict:
    """Pooled estimate with its CI, heterogeneity and prediction interval (95%)."""
    if model not in MODELS:
        raise MAError(f"modello sconosciuto: uno di {', '.join(MODELS)}")
    if ci not in CI_METHODS:
        raise MAError(f"intervallo sconosciuto: uno di {', '.join(CI_METHODS)}")
    k = len(yi)
    if k != len(vi):
        raise MAError("yi e vi hanno lunghezze diverse")
    if k < 2:
        raise MAError("servono almeno 2 studi")
    if any((not math.isfinite(v)) or v <= 0 for v in vi) or any(not math.isfinite(y) for y in yi):
        raise MAError("ogni studio deve avere un effetto e una varianza > 0")
    w = [1 / v for v in vi]
    sw, sw2 = sum(w), sum(x * x for x in w)
    mu_fe = sum(a * y for a, y in zip(w, yi)) / sw
    q = sum(a * (y - mu_fe) ** 2 for a, y in zip(w, yi))
    df = k - 1
    tau2 = 0.0 if model == "FE" else (tau2_dl(yi, vi) if model == "RE-DL" else tau2_reml(yi, vi))
    ws = [1 / (v + tau2) for v in vi]
    sws = sum(ws)
    mu = sum(a * y for a, y in zip(ws, yi)) / sws
    se = math.sqrt(1 / sws)
    if ci == "hksj":
        scale = sum(a * (y - mu) ** 2 for a, y in zip(ws, yi)) / df
        se = math.sqrt(scale / sws)
        crit = t_ppf(0.975, df)
        stat = mu / se
        p = 2 * (1 - t_cdf(abs(stat), df))
        stat_name, stat_df = "t", df
    else:
        crit = Z975
        stat = mu / se
        p = 2 * (1 - norm_cdf(abs(stat)))
        stat_name, stat_df = "z", None
    if model == "FE":
        i2 = max(0.0, 100 * (q - df) / q) if q > 0 else 0.0
        h2 = q / df
        pi_low = pi_high = None
    else:
        s2 = df * sw / (sw * sw - sw2)
        i2 = 100 * tau2 / (s2 + tau2)
        h2 = (s2 + tau2) / s2
        half = crit * math.sqrt(tau2 + se * se)
        pi_low, pi_high = mu - half, mu + half
    studies = [{"yi": y, "vi": v, "se": math.sqrt(v), "ci_low": y - Z975 * math.sqrt(v),
                "ci_high": y + Z975 * math.sqrt(v), "weight": 100 * a / sws} for y, v, a in zip(yi, vi, ws)]
    return {"k": k, "model": model, "ci_method": ci, "estimate": mu, "se": se, "ci_low": mu - crit * se,
            "ci_high": mu + crit * se, "stat": stat, "stat_name": stat_name, "stat_df": stat_df, "p": p,
            "tau2": tau2, "tau": math.sqrt(tau2), "i2": i2, "h2": h2, "q": q, "q_df": df, "q_p": chi2_sf(q, df),
            "pi_low": pi_low, "pi_high": pi_high, "studies": studies}


def leave_one_out(yi: list[float], vi: list[float], *, model: str = "RE-REML", ci: str = "wald") -> list[dict]:
    """The analysis repeated without each study in turn (needs k ≥ 3)."""
    if len(yi) < 3:
        return []
    out = []
    for i in range(len(yi)):
        r = pool(yi[:i] + yi[i + 1:], vi[:i] + vi[i + 1:], model=model, ci=ci)
        out.append({"omitted": i, **{k: r[k] for k in ("estimate", "se", "ci_low", "ci_high", "p", "q", "tau2",
                                                        "i2", "h2")}})
    return out


def egger(yi: list[float], vi: list[float]) -> dict:
    """Classical Egger test: weighted regression of yi on sei (weights 1/vi), as regtest(model="lm")."""
    k = len(yi)
    if k < 3:
        raise MAError("il test di Egger richiede almeno 3 studi")
    w = [1 / v for v in vi]
    x = [math.sqrt(v) for v in vi]
    s0, s1, s2 = sum(w), sum(a * b for a, b in zip(w, x)), sum(a * b * b for a, b in zip(w, x))
    t0, t1 = sum(a * y for a, y in zip(w, yi)), sum(a * b * y for a, b, y in zip(w, x, yi))
    det = s0 * s2 - s1 * s1
    if det <= 0:
        raise MAError("errori standard tutti uguali: il test di Egger non si può fare")
    b0 = (s2 * t0 - s1 * t1) / det
    b1 = (s0 * t1 - s1 * t0) / det
    df = k - 2
    rss = sum(a * (y - b0 - b1 * b) ** 2 for a, b, y in zip(w, x, yi))
    sigma2 = rss / df
    se0, se1 = math.sqrt(sigma2 * s2 / det), math.sqrt(sigma2 * s0 / det)
    t = b1 / se1
    crit = t_ppf(0.975, df)
    return {"t": t, "df": df, "p": 2 * (1 - t_cdf(abs(t), df)), "slope": b1, "limit_estimate": b0,
            "limit_ci_low": b0 - crit * se0, "limit_ci_high": b0 + crit * se0}


def analyse(yi: list[float], vi: list[float], *, model: str = "RE-REML", ci: str = "wald") -> dict:
    """The full result stored in ma_analysis.result: pooling, leave-one-out and Egger (only with k ≥ 10)."""
    res = pool(yi, vi, model=model, ci=ci)
    res["leave_one_out"] = leave_one_out(yi, vi, model=model, ci=ci)
    if len(yi) >= EGGER_MIN_K:
        res["egger"] = egger(yi, vi)
    else:
        res["egger"] = None
        res["egger_note"] = f"Egger test not run: fewer than {EGGER_MIN_K} studies"
    res["version"] = VERSION
    return res
