"""EURYDIKE E9 — forest and funnel plots as SVG written by the PC (no CDN, no plotting library).

The colours come from `currentColor` (the text colour of the page), so the same SVG reads in the light and dark themes of
the dashboard and in the HTML report; the pooled diamond and the prediction interval use a fixed mid grey that has
contrast on both backgrounds. Numbers are formatted with 2 decimals, as in the report tables.
"""
from __future__ import annotations

import math
from xml.sax.saxutils import escape

FONT = "font-family:system-ui,-apple-system,'Segoe UI',Roboto,Arial,sans-serif"
MID = "#7a7f87"


def _fmt(x: float | None, d: int = 2) -> str:
    if x is None or not math.isfinite(x):
        return "—"
    s = f"{x:.{d}f}"
    return "0.00" if s in ("-0.00", "-0.0") else s


def _ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    span = hi - lo
    if span <= 0:
        return [lo]
    raw = span / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    start = math.ceil(lo / step) * step
    out, x = [], start
    while x <= hi + step * 1e-9:
        out.append(round(x, 10))
        x += step
    return out


def forest(labels: list[str], yi: list[float], ci_low: list[float], ci_high: list[float], weights: list[float],
           pooled: dict, *, measure: str = "SMD", left: str = "Favours control", right: str = "Favours intervention",
           title: str = "") -> str:
    """Forest plot: one row per study (square ∝ weight, 95% CI), the pooled diamond, the prediction interval as a line."""
    k = len(yi)
    row_h, top, label_w, plot_w, num_w = 22, 46 if title else 28, 230, 340, 210
    width = label_w + plot_w + num_w
    height = top + row_h * (k + 3) + 44
    lows = [x for x in ci_low + [pooled.get("ci_low"), pooled.get("pi_low")] if x is not None]
    highs = [x for x in ci_high + [pooled.get("ci_high"), pooled.get("pi_high")] if x is not None]
    lo, hi = min(lows + [0.0]), max(highs + [0.0])
    pad = (hi - lo) * 0.06 or 1.0
    lo, hi = lo - pad, hi + pad
    sx = lambda v: label_w + (v - lo) / (hi - lo) * plot_w          # noqa: E731
    wmax = max(weights) if weights else 1
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="100%" role="img" '
           f'aria-label="{escape(title or "Forest plot")}" style="{FONT};font-size:12px;max-width:{width}px">',
           f'<title>{escape(title or "Forest plot")}</title>']
    if title:
        out.append(f'<text x="0" y="16" fill="currentColor" font-weight="600" font-size="13">{escape(title)}</text>')
    hy = top - 8
    out.append(f'<text x="0" y="{hy}" fill="currentColor" font-weight="600">Study</text>')
    out.append(f'<text x="{width - 4}" y="{hy}" fill="currentColor" font-weight="600" text-anchor="end">'
               f'{escape(measure)} [95% CI]   Weight</text>')
    y0 = top + row_h * 0.5
    # zero line
    out.append(f'<line x1="{sx(0):.1f}" y1="{top - 2}" x2="{sx(0):.1f}" y2="{top + row_h * (k + 1.4):.1f}" '
               f'stroke="currentColor" stroke-width="1" stroke-dasharray="3 3" opacity="0.6"/>')
    for i in range(k):
        y = y0 + i * row_h
        lab = labels[i] if len(labels[i]) <= 34 else labels[i][:33] + "…"
        out.append(f'<text x="0" y="{y + 4:.1f}" fill="currentColor">{escape(lab)}</text>')
        x1, x2 = sx(ci_low[i]), sx(ci_high[i])
        out.append(f'<line x1="{x1:.1f}" y1="{y:.1f}" x2="{x2:.1f}" y2="{y:.1f}" stroke="currentColor" stroke-width="1.2"/>')
        side = 4 + 8 * math.sqrt(weights[i] / wmax) if wmax > 0 else 6
        out.append(f'<rect x="{sx(yi[i]) - side / 2:.1f}" y="{y - side / 2:.1f}" width="{side:.1f}" height="{side:.1f}" '
                   f'fill="currentColor"/>')
        out.append(f'<text x="{width - 4}" y="{y + 4:.1f}" fill="currentColor" text-anchor="end" '
                   f'font-variant-numeric="tabular-nums">{_fmt(yi[i])} [{_fmt(ci_low[i])}; {_fmt(ci_high[i])}]   '
                   f'{weights[i]:.1f}%</text>')
    yp = y0 + (k + 0.6) * row_h
    est, cl, ch = pooled["estimate"], pooled["ci_low"], pooled["ci_high"]
    if pooled.get("pi_low") is not None:
        out.append(f'<line x1="{sx(pooled["pi_low"]):.1f}" y1="{yp:.1f}" x2="{sx(pooled["pi_high"]):.1f}" y2="{yp:.1f}" '
                   f'stroke="{MID}" stroke-width="2"/>')
    out.append(f'<polygon points="{sx(cl):.1f},{yp:.1f} {sx(est):.1f},{yp - 7:.1f} {sx(ch):.1f},{yp:.1f} '
               f'{sx(est):.1f},{yp + 7:.1f}" fill="{MID}" stroke="currentColor" stroke-width="1"/>')
    model = {"FE": "Fixed effect", "RE-DL": "Random effects (DL)", "RE-REML": "Random effects (REML)"}.get(
        pooled.get("model"), "Pooled")
    out.append(f'<text x="0" y="{yp + 4:.1f}" fill="currentColor" font-weight="600">{escape(model)}</text>')
    out.append(f'<text x="{width - 4}" y="{yp + 4:.1f}" fill="currentColor" text-anchor="end" font-weight="600">'
               f'{_fmt(est)} [{_fmt(cl)}; {_fmt(ch)}]   100%</text>')
    het = (f"I² = {pooled['i2']:.0f}%, τ² = {_fmt(pooled['tau2'], 3)}, Q = {_fmt(pooled['q'])} "
           f"(df = {pooled['q_df']}), p {'< 0.001' if pooled['q_p'] < 0.001 else '= ' + _fmt(pooled['q_p'], 3)}")
    if pooled.get("pi_low") is not None:
        het += f"; 95% PI [{_fmt(pooled['pi_low'])}; {_fmt(pooled['pi_high'])}]"
    out.append(f'<text x="0" y="{yp + row_h:.1f}" fill="currentColor" font-size="11">{escape(het)}</text>')
    # axis
    ya = yp + row_h * 1.6
    out.append(f'<line x1="{label_w}" y1="{ya:.1f}" x2="{label_w + plot_w}" y2="{ya:.1f}" stroke="currentColor"/>')
    for t in _ticks(lo, hi):
        out.append(f'<line x1="{sx(t):.1f}" y1="{ya:.1f}" x2="{sx(t):.1f}" y2="{ya + 4:.1f}" stroke="currentColor"/>')
        out.append(f'<text x="{sx(t):.1f}" y="{ya + 16:.1f}" fill="currentColor" text-anchor="middle" '
                   f'font-size="11">{t:g}</text>')
    out.append(f'<text x="{sx(0) - 6:.1f}" y="{ya + 30:.1f}" fill="currentColor" text-anchor="end" font-size="11">'
               f'← {escape(left)}</text>')
    out.append(f'<text x="{sx(0) + 6:.1f}" y="{ya + 30:.1f}" fill="currentColor" font-size="11">{escape(right)} →</text>')
    out.append("</svg>")
    return "\n".join(out)


def funnel(yi: list[float], se: list[float], estimate: float, *, measure: str = "SMD", title: str = "") -> str:
    """Funnel plot: effect on x, standard error on y (0 at the top), pseudo 95% limits around the pooled estimate."""
    w, h, l, r, t, b = 420, 320, 56, 16, 34 if title else 16, 44
    smax = max(se) * 1.1 if se else 1
    half = 1.959963984540054 * smax
    lo = min(min(yi), estimate - half)
    hi = max(max(yi), estimate + half)
    pad = (hi - lo) * 0.05 or 1
    lo, hi = lo - pad, hi + pad
    sx = lambda v: l + (v - lo) / (hi - lo) * (w - l - r)          # noqa: E731
    sy = lambda s: t + s / smax * (h - t - b)                       # noqa: E731
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="100%" role="img" '
           f'aria-label="{escape(title or "Funnel plot")}" style="{FONT};font-size:11px;max-width:{w}px">',
           f'<title>{escape(title or "Funnel plot")}</title>']
    if title:
        out.append(f'<text x="0" y="14" fill="currentColor" font-weight="600" font-size="13">{escape(title)}</text>')
    out.append(f'<polygon points="{sx(estimate):.1f},{sy(0):.1f} {sx(estimate - half):.1f},{sy(smax):.1f} '
               f'{sx(estimate + half):.1f},{sy(smax):.1f}" fill="{MID}" fill-opacity="0.15" stroke="{MID}" '
               f'stroke-dasharray="4 3"/>')
    out.append(f'<line x1="{sx(estimate):.1f}" y1="{sy(0):.1f}" x2="{sx(estimate):.1f}" y2="{sy(smax):.1f}" '
               f'stroke="currentColor"/>')
    for y, s in zip(yi, se):
        out.append(f'<circle cx="{sx(y):.1f}" cy="{sy(s):.1f}" r="4" fill="currentColor" fill-opacity="0.75"/>')
    out.append(f'<line x1="{l}" y1="{h - b}" x2="{w - r}" y2="{h - b}" stroke="currentColor"/>')
    for tk in _ticks(lo, hi):
        out.append(f'<text x="{sx(tk):.1f}" y="{h - b + 15}" fill="currentColor" text-anchor="middle">{tk:g}</text>')
    out.append(f'<line x1="{l}" y1="{t}" x2="{l}" y2="{h - b}" stroke="currentColor"/>')
    for tk in _ticks(0, smax, 4):
        out.append(f'<text x="{l - 6}" y="{sy(tk) + 4:.1f}" fill="currentColor" text-anchor="end">{tk:g}</text>')
    out.append(f'<text x="{(l + w - r) / 2:.1f}" y="{h - 8}" fill="currentColor" text-anchor="middle">{escape(measure)}</text>')
    out.append(f'<text x="14" y="{(t + h - b) / 2:.1f}" fill="currentColor" text-anchor="middle" '
               f'transform="rotate(-90 14 {(t + h - b) / 2:.1f})">Standard error</text>')
    out.append("</svg>")
    return "\n".join(out)
