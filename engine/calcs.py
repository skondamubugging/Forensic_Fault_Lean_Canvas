"""Pure, unit-testable calculations. No Streamlit, no globals, SI units, inputs validated."""
from __future__ import annotations
import cmath, math
from .profiles import SystemProfile, Limits, max_disconnect_time_s

RHO20 = {"Cu": 0.017241, "Al": 0.028264}      # Ω·mm²/m at 20 °C (IEC 60228)
ALPHA = {"Cu": 0.00393, "Al": 0.00403}
K_ADIABATIC = {("Cu", "PVC"): 115, ("Cu", "XLPE"): 143, ("Al", "PVC"): 76, ("Al", "XLPE"): 94}  # IEC 60364-4-43 T43.1
TRIP_MULT = {"B": 5.0, "C": 10.0, "D": 20.0, "K": 14.0, "Z": 3.0}          # upper bound = guaranteed magnetic trip

def _pos(**kw):
    for k, v in kw.items():
        if v is None or not math.isfinite(v) or v <= 0: raise ValueError(f"{k} must be a positive number, got {v!r}")

def conductor_r_ohm_per_km(area_mm2: float, material: str = "Cu", temp_c: float = 70.0) -> float:
    _pos(area_mm2=area_mm2)
    return RHO20[material] * (1 + ALPHA[material] * (temp_c - 20)) / area_mm2 * 1000

def reactance_ohm_per_km(freq_hz: float, x50: float = 0.08) -> float:
    """Typical multicore LV cable ≈0.08 Ω/km @50 Hz; scales with frequency. Override x50 from the datasheet."""
    return x50 * freq_hz / 50.0

def voltage_drop(p: SystemProfile, i_a: float, length_m: float, area_mm2: float, material="Cu", pf=0.9,
                 temp_c=70.0, x_ohm_km=None, limit_pct=5.0) -> dict:
    _pos(i_a=i_a, length_m=length_m)
    if not 0 < pf <= 1: raise ValueError("pf must be in (0,1]")
    r = conductor_r_ohm_per_km(area_mm2, material, temp_c) / 1000
    x = (x_ohm_km if x_ohm_km is not None else reactance_ohm_per_km(p.freq_hz)) / 1000
    k = math.sqrt(3) if p.phases == 3 else 2.0
    dv = k * i_a * length_m * (r * pf + x * math.sqrt(1 - pf ** 2))
    pct = 100 * dv / p.u_ref
    return {"volts": dv, "pct": pct, "limit_pct": limit_pct, "ok": pct <= limit_pct}

def neutral_current(mags: tuple[float, float, float], pf: tuple[float, float, float] = (1, 1, 1), h3_pct: float = 0.0) -> dict:
    """Phasor sum (ABC, lagging pf) + triplen (3rd-harmonic) neutral build-up (LED drivers/SMPS).
    Fundamental part is exact for any magnitudes/pf; h3 is treated as % of each phase current, arithmetic sum."""
    ang = (0.0, -120.0, 120.0)
    ph = [m * cmath.exp(1j * math.radians(a - math.degrees(math.acos(f)))) for m, a, f in zip(mags, ang, pf)]
    n1 = abs(sum(ph)); n3 = sum(m * h3_pct / 100 for m in mags)
    tot = math.hypot(n1, n3); mx = max(mags)
    return {"fundamental_a": n1, "triplen_a": n3, "total_a": tot, "exceeds_phase": tot > mx,
            "note": "Neutral may need to be >= phase size when triplen content is high (IEC 60364-5-52)." if n3 > 0.33 * mx else ""}

def unbalance_pct(mags: tuple[float, float, float]) -> float:
    av = sum(mags) / 3
    return 0.0 if av == 0 else 100 * max(abs(m - av) for m in mags) / av

def trip_current_a(curve: str, in_a: float, im_a: float | None = None, tol: float = 0.2) -> float:
    """Current guaranteeing instantaneous trip. MCB: curve multiple; adjustable MCCB: Im*(1+tol)."""
    _pos(in_a=in_a)
    if im_a: return im_a * (1 + tol)
    if curve.upper() not in TRIP_MULT: raise ValueError(f"unknown curve {curve!r}")
    return TRIP_MULT[curve.upper()] * in_a

def zs_check(p: SystemProfile, zs: float, in_a: float, curve: str, lim: Limits = Limits(), im_a=None) -> dict:
    _pos(zs=zs)
    ia = trip_current_a(curve, in_a, im_a); zmax = lim.zs_factor * p.u0 / ia
    return {"ia": ia, "zs_max": zmax, "fault_a": p.u0 / zs, "t_max_s": max_disconnect_time_s(p.u0, p.earthing, in_a), "ok": zs <= zmax}

def touch_voltage(fault_a: float, r_pe_ohm: float) -> float: return fault_a * r_pe_ohm

def min_conductor_area(fault_a: float, t_s: float, material="Cu", insulation="XLPE") -> float:
    _pos(fault_a=fault_a, t_s=t_s)
    return fault_a * math.sqrt(t_s) / K_ADIABATIC[(material, insulation)]

def tt_check(ra_ohm: float, idn_ma: float, lim: Limits = Limits()) -> dict:
    v = ra_ohm * idn_ma / 1000
    return {"v": v, "ra_max": lim.touch_v_max * 1000 / idn_ma, "ok": v <= lim.touch_v_max}

def rcd_issues(idn_ma, t1_ms, t5_ms, lim: Limits = Limits()) -> list[tuple[str, str]]:
    out = []
    if idn_ma is None: return [("C", "RCD data missing")]
    if idn_ma == 0: out.append(("A", "No RCD"))
    elif idn_ma > lim.rcd_max_ma: out.append(("B", f"RCD {idn_ma:g} mA > {lim.rcd_max_ma:g} mA"))
    if t1_ms is not None and t1_ms > lim.rcd_t1_ms: out.append(("A", f"trip {t1_ms:g} ms @IΔn > {lim.rcd_t1_ms:g}"))
    if t5_ms is not None and t5_ms > lim.rcd_t5_ms: out.append(("B", f"trip {t5_ms:g} ms @5IΔn > {lim.rcd_t5_ms:g}"))
    return out

def ib_in_iz(ib: float, in_a: float, iz_tabulated: float, k_temp=1.0, k_group=1.0, k_other=1.0) -> dict:
    iz = iz_tabulated * k_temp * k_group * k_other
    return {"iz": iz, "ok": ib <= in_a <= iz}

def rod_resistance(rho: float, length_m: float, dia_m: float) -> float:
    """Dwight: R = ρ/(2πL)·(ln(8L/d) − 1)."""
    _pos(rho=rho, length_m=length_m, dia_m=dia_m)
    return rho / (2 * math.pi * length_m) * (math.log(8 * length_m / dia_m) - 1)

def fall_of_potential_probe_m(d_c_m: float) -> float: return 0.618 * d_c_m

def full_load_current(p: SystemProfile, kw: float, pf=0.85, eff=1.0) -> float:
    _pos(kw=kw)
    return kw * 1000 / ((math.sqrt(3) if p.phases == 3 else 1) * p.u_ref * pf * eff)

def cable_loss_kw(p: SystemProfile, i_a: float, length_m: float, area_mm2: float, material="Cu", temp_c=70.0) -> float:
    n = 3 if p.phases == 3 else 2
    return n * i_a ** 2 * conductor_r_ohm_per_km(area_mm2, material, temp_c) / 1000 * length_m / 1000
