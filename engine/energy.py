"""Energy audit + optimisation economics. Site-agnostic: voltage/frequency enter only via SystemProfile."""
from __future__ import annotations
import math
from dataclasses import dataclass
from . import calcs
from .profiles import SystemProfile

@dataclass(frozen=True)
class Load:
    name: str; kw: float; qty: int = 1; hours_day: float = 0.0; days_year: int = 365
    load_factor: float = 1.0; pf: float = 0.9; kind: str = "general"
    @property
    def connected_kw(self): return self.kw * self.qty
    @property
    def kwh_year(self): return self.connected_kw * self.load_factor * self.hours_day * self.days_year

def energy_audit(loads: list[Load], p: SystemProfile, tariff_kwh: float, demand_charge_kw_month: float = 0.0,
                 diversity: float = 1.0, target_pf: float = 0.95) -> dict:
    tot = sum(l.kwh_year for l in loads) or 1e-9
    demand = sum(l.connected_kw * l.load_factor for l in loads) * diversity
    rows = sorted(({"load": l.name, "kwh_year": l.kwh_year, "share_pct": 100 * l.kwh_year / tot, "cost": l.kwh_year * tariff_kwh} for l in loads), key=lambda r: -r["kwh_year"])
    p_sum = sum(l.connected_kw * l.load_factor for l in loads) or 1e-9
    s_sum = sum(l.connected_kw * l.load_factor / l.pf for l in loads) or 1e-9
    pf = p_sum / s_sum; tan = lambda f: math.tan(math.acos(f))
    kvar = max(0.0, demand * (tan(pf) - tan(target_pf)))
    return {"kwh_year": tot if loads else 0.0, "energy_cost": tot * tariff_kwh, "demand_kw": demand, "demand_cost": demand * demand_charge_kw_month * 12,
            "avg_pf": pf, "kvar_to_target": kvar, "design_current_a": calcs.full_load_current(p, demand, pf) if demand > 0 else 0.0, "ranking": rows}

@dataclass(frozen=True)
class Measure:
    name: str; kwh_saved: float; capex: float; kw_saved: float = 0.0; om_year: float = 0.0; life_years: int = 10

def evaluate(m: Measure, tariff_kwh: float, demand_charge_kw_year: float = 0.0, discount: float = 0.08, escalation: float = 0.03) -> dict:
    annual = m.kwh_saved * tariff_kwh + m.kw_saved * demand_charge_kw_year - m.om_year
    cash = [-m.capex] + [annual * (1 + escalation) ** t for t in range(1, m.life_years + 1)]
    npv = sum(c / (1 + discount) ** t for t, c in enumerate(cash))
    irr = None
    if annual > 0:
        lo, hi = -0.99, 10.0
        f = lambda r: sum(c / (1 + r) ** t for t, c in enumerate(cash))
        if f(lo) * f(hi) < 0:
            for _ in range(80):
                mid = (lo + hi) / 2; lo, hi = (mid, hi) if f(lo) * f(mid) > 0 else (lo, mid)
            irr = (lo + hi) / 2
    return {"name": m.name, "annual_saving": annual, "simple_payback_y": m.capex / annual if annual > 0 else math.inf, "npv": npv, "irr": irr}

def rank(measures: list[Measure], tariff_kwh: float, **kw) -> list[dict]:
    return sorted((evaluate(m, tariff_kwh, **kw) for m in measures), key=lambda r: (-r["npv"]))

# ── measure builders ──
def led_retrofit(l: Load, new_kw: float, capex_per_unit: float, **kw) -> Measure:
    d = l.kw - new_kw
    return Measure(f"LED retrofit – {l.name}", d * l.qty * l.load_factor * l.hours_day * l.days_year, capex_per_unit * l.qty, d * l.qty, **kw)

def dimming_schedule(l: Load, profile: list[tuple[float, float]], capex: float, **kw) -> Measure:
    """profile = [(hours/day, level 0-1), …]; LED power assumed ~linear with level (conservative: driver efficiency ignored)."""
    new = l.connected_kw * l.load_factor * l.days_year * sum(h * lv for h, lv in profile)
    return Measure(f"Dimming/schedule – {l.name}", max(0.0, l.kwh_year - new), capex, **kw)

def pf_correction(p_kw: float, pf_now: float, pf_target: float, cost_per_kvar: float, annual_penalty_avoided: float = 0.0,
                  feeder_loss_kw: float = 0.0, hours: float = 4000, **kw) -> tuple[Measure, float]:
    t = lambda f: math.tan(math.acos(f)); kvar = p_kw * (t(pf_now) - t(pf_target))
    loss_saved = feeder_loss_kw * (1 - (pf_now / pf_target) ** 2) * hours
    return Measure("Power-factor correction", loss_saved, kvar * cost_per_kvar, om_year=0.0, **kw), kvar

def cable_upsize(p: SystemProfile, i_a: float, length_m: float, a_old: float, a_new: float, hours_year: float,
                 extra_cost_per_m: float, material="Cu", loss_load_factor: float = 0.4, **kw) -> Measure:
    """Loss-load factor ≈ fraction of hours-at-full-load equivalent for I²R losses (≈0.3-0.5 for lighting/pump duty)."""
    saved_kw = calcs.cable_loss_kw(p, i_a, length_m, a_old, material) - calcs.cable_loss_kw(p, i_a, length_m, a_new, material)
    return Measure(f"Upsize {a_old:g}→{a_new:g} mm²", saved_kw * hours_year * loss_load_factor, extra_cost_per_m * length_m, saved_kw, **kw)
