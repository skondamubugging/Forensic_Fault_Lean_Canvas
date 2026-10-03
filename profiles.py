"""System profiles + limit sets. Nothing in the engine hardcodes a voltage, frequency or threshold.

Reference values follow IEC 60364 (4-41 Table 41.1, 6-61 Table 6.1). BS 7671 and AS/NZS 3000 are closely
aligned but NOT identical, NEC (US) differs materially: every Limits field is overridable, and the edition
adopted by your jurisdiction governs."""
from __future__ import annotations
import math
from dataclasses import dataclass

EARTHING = ("TN-S", "TN-C-S", "TN-C", "TT", "IT")

@dataclass(frozen=True)
class SystemProfile:
    name: str
    u0: float                      # nominal line-to-neutral/earth voltage (V)
    freq_hz: float = 50.0
    phases: int = 3
    earthing: str = "TN-S"
    ul: float | None = None        # nominal line-to-line voltage; derived if omitted

    def __post_init__(self):
        if self.u0 <= 0 or self.freq_hz <= 0: raise ValueError("u0 and freq_hz must be > 0")
        if self.phases not in (1, 3): raise ValueError("phases must be 1 or 3")
        if self.earthing not in EARTHING: raise ValueError(f"earthing must be one of {EARTHING}")

    @property
    def u_line(self) -> float: return self.ul or (self.u0 * math.sqrt(3) if self.phases == 3 else self.u0)
    @property
    def u_ref(self) -> float:      # voltage that % drop / power calcs are referred to
        return self.u_line if self.phases == 3 else self.u0

PRESETS = {
    "230/400 V 50 Hz (IN/EU/UK/AU)": SystemProfile("230/400 V 50 Hz", 230, 50, 3, "TN-S", 400),
    "120/208 V 60 Hz (US/CA)": SystemProfile("120/208 V 60 Hz", 120, 60, 3, "TN-S", 208),
    "277/480 V 60 Hz (US/CA)": SystemProfile("277/480 V 60 Hz", 277, 60, 3, "TN-S", 480),
    "127/220 V 60 Hz (BR)": SystemProfile("127/220 V 60 Hz", 127, 60, 3, "TN-S", 220),
    "230 V 1-phase 50 Hz": SystemProfile("230 V 1-ph 50 Hz", 230, 50, 1, "TN-S"),
    "120 V 1-phase 60 Hz": SystemProfile("120 V 1-ph 60 Hz", 120, 60, 1, "TN-S"),
}

@dataclass(frozen=True)
class Limits:
    ir_min_mohm: float = 1.0          # IEC 60364-6 Table 6.1 (<=500 V, 500 V DC test)
    ir_warn_mohm: float = 10.0        # project criterion for NEW outdoor cable (not a standard value)
    ir_outlier_ratio: float = 20.0    # circuit < fleet median / ratio -> suspicious
    ir_degradation_ratio: float = 10.0
    pe_cont_max_ohm: float = 1.0      # project criterion; the standard limits R1+R2 via Zs
    pe_open_ohm: float = 100.0
    zs_factor: float = 0.8            # field-measurement allowance (conductor temperature)
    rcd_max_ma: float = 30.0          # additional protection
    rcd_t1_ms: float = 300.0
    rcd_t5_ms: float = 40.0
    touch_v_max: float = 50.0         # 25 V for special locations (fountains, pools: IEC 60364-7-702)
    vd_max_pct: float = 5.0
    selectivity_ratio: float = 1.6    # indicative; use manufacturer tables
    unbalance_warn_pct: float = 10.0
    earth_rise_pct: float = 50.0
    thermal_dt_warn: float = 4.0      # K, NETA-style bands: 4-15 probable, >15 major
    thermal_dt_urgent: float = 15.0

def ir_requirement(circuit_v: float, selv_pelv: bool = False) -> tuple[float, float]:
    """(test voltage V DC, minimum MΩ) per IEC 60364-6 Table 6.1."""
    if selv_pelv: return 250.0, 0.5
    return (500.0, 1.0) if circuit_v <= 500 else (1000.0, 1.0)

def max_disconnect_time_s(u0: float, earthing: str, rated_a: float) -> float:
    """IEC 60364-4-41 Table 41.1 (final circuits <=32 A); distribution circuits: TN 5 s, TT 1 s."""
    bands = [(120, 0.8, 0.3), (230, 0.4, 0.2), (400, 0.2, 0.07), (math.inf, 0.1, 0.04)]
    tn, tt = next((a, b) for lim, a, b in bands if u0 <= lim)
    t = tt if earthing == "TT" else tn
    if rated_a > 32: t = 1.0 if earthing == "TT" else 5.0
    return t
