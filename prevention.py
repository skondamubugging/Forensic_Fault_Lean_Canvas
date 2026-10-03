"""Predictive fault-prevention: trend forecasting, relative-risk (health) index, thermography normalisation.
Outputs are a *relative risk index*, not a calibrated failure probability."""
from __future__ import annotations
import math
from datetime import date, datetime
import numpy as np
from .profiles import Limits

def _days(x): return float(x.toordinal()) if isinstance(x, (date, datetime)) else float(x)

def trend_forecast(points: list[tuple], threshold: float, bad_when: str = "below", log: bool = True) -> dict:
    """Fit ln(value) (or value) vs time; return days until threshold is crossed. IR decays ~exponentially → log=True."""
    pts = sorted((_days(t), v) for t, v in points if v is not None and (v > 0 or not log))
    if len(pts) < 2: return {"status": "insufficient data", "n": len(pts), "days_to_threshold": None}
    t = np.array([p[0] for p in pts]); y = np.array([math.log(p[1]) if log else p[1] for p in pts]); thr = math.log(threshold) if log else threshold
    b, a = np.polyfit(t, y, 1); fit = a + b * t
    ss = float(((y - y.mean()) ** 2).sum()); r2 = 1.0 if ss == 0 else 1 - float(((y - fit) ** 2).sum()) / ss
    now_bad = (pts[-1][1] < threshold) if bad_when == "below" else (pts[-1][1] > threshold)
    worsening = b < 0 if bad_when == "below" else b > 0
    days = 0.0 if now_bad else ((thr - (a + b * t[-1])) / b if worsening and b != 0 else None)
    return {"status": "threshold already crossed" if now_bad else ("worsening" if worsening else "stable/improving"), "n": len(pts), "r2": r2,
            "rate_pct_per_year": (math.exp(b * 365) - 1) * 100 if log else b * 365, "days_to_threshold": days,
            "confidence": "low (n<4 or R²<0.6)" if len(pts) < 4 or r2 < 0.6 else "moderate"}

def thermal_assess(dt_meas_k: float, i_meas_a: float, i_rated_a: float, lim: Limits = Limits(), exponent: float = 2.0) -> dict:
    """Normalise ΔT to rated load: ΔT ∝ I^n (n≈1.6–2). Measurements below ~40 % load are indicative only."""
    if i_meas_a <= 0 or i_rated_a <= 0: raise ValueError("currents must be > 0")
    dt = dt_meas_k * (i_rated_a / i_meas_a) ** exponent
    lvl = "URGENT" if dt >= lim.thermal_dt_urgent else "ATTENTION" if dt >= lim.thermal_dt_warn else "OK"
    return {"dt_at_rated_k": dt, "level": lvl, "low_load_warning": i_meas_a / i_rated_a < 0.4}

def loose_joint_power_w(i_a: float, r_good: float, r_bad: float) -> tuple[float, float]: return i_a ** 2 * r_good, i_a ** 2 * r_bad

DEFAULT_W = {"ir": 3, "pe": 2, "zs": 2, "rcd": 3, "load": 1.5, "thermal": 2, "exposure": 2, "age": 1}
_hi = lambda v, lim: min(1.0, max(0.0, (v / lim - 0.5) / 0.5))      # 0 at ≤50 % of limit, 1 at limit
_lo = lambda v, lim: min(1.0, max(0.0, (lim / max(v, 1e-9) - 0.5) / 0.5))

def health_index(d: dict, lim: Limits = Limits(), weights: dict | None = None, zs_max: float | None = None, design_life_y: float = 25) -> dict:
    """d keys (all optional): ir_mohm, pe_ohm, zs_ohm, rcd_t1_ms, ib_over_in, thermal_dt_k, exposure(0-1), age_y.
    Missing data is scored 0.4 (unknown ≠ safe) and listed. Any hard-limit breach floors the index at 85."""
    w = {**DEFAULT_W, **(weights or {})}; r: dict[str, float | None] = {
        "ir": _lo(d["ir_mohm"], lim.ir_warn_mohm) if d.get("ir_mohm") else None,
        "pe": _hi(d["pe_ohm"], lim.pe_cont_max_ohm) if d.get("pe_ohm") is not None else None,
        "zs": _hi(d["zs_ohm"], zs_max) if d.get("zs_ohm") is not None and zs_max else None,
        "rcd": _hi(d["rcd_t1_ms"], lim.rcd_t1_ms) if d.get("rcd_t1_ms") is not None else None,
        "load": _hi(d["ib_over_in"], 1.0) if d.get("ib_over_in") is not None else None,
        "thermal": _hi(d["thermal_dt_k"], lim.thermal_dt_urgent) if d.get("thermal_dt_k") is not None else None,
        "exposure": d.get("exposure"), "age": _hi(d["age_y"], design_life_y) if d.get("age_y") is not None else None}
    miss = [k for k, v in r.items() if v is None]; sc = {k: (0.4 if v is None else v) for k, v in r.items()}
    idx = 100 * sum(w[k] * sc[k] for k in sc) / sum(w.values())
    hard = (d.get("ir_mohm") is not None and d["ir_mohm"] < lim.ir_min_mohm) or (d.get("pe_ohm") or 0) >= lim.pe_open_ohm or \
           (d.get("rcd_t1_ms") or 0) > lim.rcd_t1_ms or (zs_max and (d.get("zs_ohm") or 0) > zs_max)
    if hard: idx = max(idx, 85.0)
    band = "RED" if idx >= 70 else "AMBER" if idx >= 40 else "GREEN"
    return {"index": round(idx, 1), "band": band, "hard_fail": bool(hard), "missing_data": miss,
            "drivers": sorted(((k, round(w[k] * sc[k], 2)) for k in sc), key=lambda x: -x[1])[:3], "retest_months": {"RED": 1, "AMBER": 6, "GREEN": 12}[band]}
