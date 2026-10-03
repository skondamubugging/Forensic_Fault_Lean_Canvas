"""Rule-based assessment → Findings (class A critical … E observation). Thresholds come from Limits/SystemProfile only."""
from __future__ import annotations
import math, statistics
from dataclasses import dataclass, field
from . import calcs
from .profiles import SystemProfile, Limits

@dataclass(frozen=True)
class Finding:
    cls: str; area: str; text: str; evidence: str; action: str; when: str; ref: str = ""

@dataclass
class CircuitTest:
    name: str
    ir: dict = field(default_factory=dict)       # {"R-E": MΩ, ...}
    pe_ohm: float | None = None; zs_ohm: float | None = None; in_a: float | None = None; curve: str = ""
    rcd_ma: float | None = None; rcd_t1_ms: float | None = None; rcd_t5_ms: float | None = None
    polarity: str = ""; baseline_ir: float | None = None; length_m: float | None = None; ra_ohm: float | None = None
    def has_data(self): return any(v not in (None, "", {}) for k, v in self.__dict__.items() if k != "name")

def _n(x):
    try:
        v = float(x); return None if math.isnan(v) else v
    except (TypeError, ValueError): return None

def assess_circuits(cs: list[CircuitTest], p: SystemProfile, lim: Limits = Limits()) -> list[Finding]:
    F: list[Finding] = []; cs = [c for c in cs if c.has_data()]
    use_len = all(c.length_m for c in cs) and bool(cs)
    norm = lambda c, v: v * c.length_m / 1000 if use_len else v          # MΩ·km if lengths known
    allv = [norm(c, v) for c in cs for v in c.ir.values() if v is not None]
    med = statistics.median(allv) if allv else None
    for c in cs:
        add = lambda *a, **k: F.append(Finding(*a, **k))
        vals = {k: v for k, v in c.ir.items() if v is not None}
        low = {k: v for k, v in vals.items() if v < lim.ir_min_mohm}
        if low:
            pe = any(k.endswith("E") for k in low); t = ", ".join(f"{k}={v:g} MΩ" for k, v in low.items())
            add("A" if pe else "B", "Insulation", f"{c.name}: IR below {lim.ir_min_mohm:g} MΩ ({t})", f"fleet median {med:g}" if med else "Megger",
                "Isolate; sectionalise; repair/replace faulty cable, joint or gland (IP66); re-test IR + PE before energising.", "Immediate", "IEC 60364-6 T6.1")
        sus = {k: v for k, v in vals.items() if k not in low and (v < lim.ir_warn_mohm or (med and norm(c, v) < med / lim.ir_outlier_ratio))}
        if sus: add("C", "Insulation", f"{c.name}: IR low vs criterion/fleet ({', '.join(f'{k}={v:g}' for k, v in sus.items())} MΩ)", "Megger",
                    "Re-test after drying; sectionalise; trend monthly.", "Short term")
        if c.baseline_ir and vals and min(vals.values()) < c.baseline_ir / lim.ir_degradation_ratio:
            add("C", "Insulation", f"{c.name}: IR fell >{(1 - 1 / lim.ir_degradation_ratio) * 100:.0f}% since commissioning", f"{c.baseline_ir:g}→{min(vals.values()):g} MΩ", "Find ageing/water/mechanical cause.", "Short term")
        if c.pe_ohm is not None and c.pe_ohm > lim.pe_cont_max_ohm:
            op = c.pe_ohm >= lim.pe_open_ohm
            add("A" if op else "B", "Earthing", f"{c.name}: PE {'OPEN' if op else 'high resistance'} ({c.pe_ohm:g} Ω)", "Continuity test", "Restore PE continuity; re-test.", "Immediate" if op else "Short term")
        if p.earthing == "TT":
            if c.ra_ohm and c.rcd_ma:
                r = calcs.tt_check(c.ra_ohm, c.rcd_ma, lim)
                if not r["ok"]: add("A", "Earthing", f"{c.name}: TT RA·IΔn = {r['v']:.0f} V > {lim.touch_v_max:g} V", f"RA {c.ra_ohm:g} Ω", "Lower electrode resistance or use lower-IΔn RCD.", "Immediate", "IEC 60364-4-41 411.5")
        elif c.zs_ohm and c.in_a and c.curve:
            r = calcs.zs_check(p, c.zs_ohm, c.in_a, c.curve, lim)
            if not r["ok"]: add("B", "Earthing", f"{c.name}: Zs {c.zs_ohm:g} Ω > max {r['zs_max']:.3g} Ω (disconnect ≤ {r['t_max_s']:g} s)", f"Ia≈{r['ia']:g} A",
                                "Improve PE path / reduce device rating / add RCBO; re-measure.", "Short term", "IEC 60364-4-41 T41.1")
        for lv, msg in calcs.rcd_issues(c.rcd_ma, c.rcd_t1_ms, c.rcd_t5_ms, lim):
            add(lv, "Protection", f"{c.name}: {msg}", "RCD tester", "Install/replace RCD/RCBO; verify with instrument.", "Immediate" if lv == "A" else "Short term")
        if c.polarity.strip().lower() == "fail": add("A", "Wiring", f"{c.name}: polarity fail", "Polarity test", "Correct and re-verify.", "Immediate")
    order = "ABCDE"; F.sort(key=lambda f: order.index(f.cls)); return F

def locate_faulty_section(sections: list[tuple[str, float]], lim: Limits = Limits()) -> Finding | None:
    sv = [(n, v) for n, v in sections if v is not None]
    if len(sv) < 2: return None
    i = min(range(len(sv)), key=lambda k: sv[k][1]); name, v = sv[i]
    rest = [x for k, (_, x) in enumerate(sv) if k != i]; m = statistics.median(rest)
    if v < lim.ir_min_mohm or v < m / lim.ir_outlier_ratio:
        return Finding("A" if v < lim.ir_min_mohm else "B", "Fault location", f"Faulty section: {name} ({v:g} MΩ vs median {m:g})", "Sectional IR", "Open only this section; repair; re-test.", "Immediate")

def assess_earth_pits(pits: list[dict], lim: Limits = Limits()) -> list[Finding]:
    F = []
    for q in pits:
        r, lm, pv = _n(q.get("r")), _n(q.get("limit")), _n(q.get("prev"))
        if r is not None and lm is not None and r > lm: F.append(Finding("C", "Earthing", f"{q['id']}: {r:g} Ω > design {lm:g} Ω", "Earth tester", "Add electrode/treat soil; re-test (61.8 % rule).", "Medium term"))
        if r is not None and pv and r > pv * (1 + lim.earth_rise_pct / 100): F.append(Finding("C", "Earthing", f"{q['id']}: rose {100 * (r / pv - 1):.0f}% ({pv:g}→{r:g} Ω)", "Trend", "Check joints/corrosion; compare same season.", "Medium term"))
    return F

def circuits_from_df(df) -> list[CircuitTest]:
    """Adapter for the Streamlit test-matrix DataFrame used by app.py."""
    out = []
    for _, r in df.iterrows():
        out.append(CircuitTest(str(r["Circuit"]) or "?", {c[3:].replace("-", "-"): _n(r[c]) for c in df.columns if c.startswith("IR ") and _n(r[c]) is not None},
                               _n(r["PE cont (Ω)"]), _n(r["Zs (Ω)"]), _n(r["MCB In (A)"]), str(r["Curve"]), _n(r["RCD IΔn (mA)"]),
                               _n(r["RCD t@IΔn (ms)"]), _n(r["RCD t@5xIΔn (ms)"]), str(r["Polarity"]), _n(r["Baseline IR (MΩ)"])))
    return out
