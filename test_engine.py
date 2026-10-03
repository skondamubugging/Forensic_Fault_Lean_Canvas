import math, os, io, tempfile, pytest
from datetime import date, timedelta
from engine import PRESETS, SystemProfile, Limits, max_disconnect_time_s, calcs, assess, energy, prevention, store

P50 = PRESETS["230/400 V 50 Hz (IN/EU/UK/AU)"]; P60 = PRESETS["120/208 V 60 Hz (US/CA)"]

def test_neutral_matches_closed_form_and_detects_h3():
    a, b, c = 8, 8.5, 22
    n = calcs.neutral_current((a, b, c))["fundamental_a"]
    assert n == pytest.approx(math.sqrt(a*a + b*b + c*c - a*b - b*c - c*a))
    assert calcs.neutral_current((10, 10, 10))["fundamental_a"] < 1e-9           # balanced → 0
    assert calcs.neutral_current((10, 10, 10), h3_pct=40)["total_a"] == pytest.approx(12.0)  # triplen adds arithmetically
    assert calcs.neutral_current((10, 10, 10), h3_pct=40)["exceeds_phase"]

def test_voltage_drop_is_profile_driven():
    r3 = calcs.voltage_drop(P50, 20, 100, 6, pf=1.0); r1 = calcs.voltage_drop(P60, 20, 100, 6, pf=1.0)
    assert r3["volts"] == pytest.approx(math.sqrt(3) * 20 * 100 * calcs.conductor_r_ohm_per_km(6) / 1000)
    assert r1["pct"] == pytest.approx(100 * r1["volts"] / 208)                    # 208 V reference, not 415
    assert calcs.reactance_ohm_per_km(60) == pytest.approx(0.096)

def test_disconnect_times_iec_table():
    assert max_disconnect_time_s(230, "TN-S", 16) == 0.4 and max_disconnect_time_s(120, "TN-S", 16) == 0.8
    assert max_disconnect_time_s(277, "TN-S", 16) == 0.2 and max_disconnect_time_s(230, "TT", 16) == 0.2
    assert max_disconnect_time_s(230, "TN-S", 63) == 5.0

def test_zs_and_rod_and_conductor():
    r = calcs.zs_check(P50, 1.2, 16, "C"); assert r["ia"] == 160 and r["zs_max"] == pytest.approx(0.8 * 230 / 160) and not r["ok"]
    assert calcs.rod_resistance(100, 3, 0.016) == pytest.approx(33.5, abs=0.2)
    assert calcs.conductor_r_ohm_per_km(6, "Cu", 20) == pytest.approx(2.874, abs=0.01)
    with pytest.raises(ValueError): calcs.voltage_drop(P50, -1, 10, 6)

def test_assess_flags_critical_circuit():
    ok = assess.CircuitTest("A", {"R-E": 250, "Y-E": 240, "B-E": 230}, 0.2, 0.8, 16, "C", 30, 120, 20)
    bad = assess.CircuitTest("B", {"R-E": 200, "B-E": 0.32}, 0.2, 3.0, 16, "C", 30, 500, 20)
    F = assess.assess_circuits([ok, bad], P50)
    assert not [f for f in F if f.text.startswith("A:")] and {f.cls for f in F if f.text.startswith("B:")} >= {"A", "B"}
    assert assess.locate_faulty_section([("s1", 300), ("s2", 0.3), ("s3", 280)]).text.startswith("Faulty section: s2")

def test_trend_forecast_and_health():
    d0 = date(2025, 1, 1); pts = [(d0 + timedelta(days=90 * i), 400 * math.exp(-0.004 * 90 * i)) for i in range(6)]
    f = prevention.trend_forecast(pts, 10.0); assert f["status"] == "worsening" and f["r2"] > 0.99
    assert f["days_to_threshold"] == pytest.approx((math.log(400 / 10)) / 0.004 - 450, rel=0.02)
    assert prevention.health_index({"ir_mohm": 0.3})["band"] == "RED"
    assert prevention.health_index({"ir_mohm": 500, "pe_ohm": .1, "zs_ohm": .3, "rcd_t1_ms": 100, "ib_over_in": .3, "thermal_dt_k": 0, "exposure": 0, "age_y": 1}, zs_max=1)["band"] == "GREEN"
    assert prevention.thermal_assess(5, 10, 20)["dt_at_rated_k"] == pytest.approx(20) and prevention.thermal_assess(5, 10, 20)["level"] == "URGENT"

def test_energy_audit_and_measures():
    l = energy.Load("Lights", 0.15, 40, 11, 365, 1.0, 0.9); a = energy.energy_audit([l], P50, 8.0)
    assert a["kwh_year"] == pytest.approx(0.15 * 40 * 11 * 365) and a["kvar_to_target"] > 0
    m = energy.led_retrofit(l, 0.06, 2500); e = energy.evaluate(m, 8.0)
    assert e["simple_payback_y"] == pytest.approx(m.capex / (m.kwh_saved * 8.0)) and e["irr"] > 0
    assert energy.dimming_schedule(l, [(5, 1.0), (6, 0.5)], 0).kwh_saved == pytest.approx(l.kwh_year * (1 - (5 + 3) / 11))

def test_store_conflict_and_image(tmp_path):
    s = store.LocalStore(str(tmp_path)); sha = s.save("X", {"a": 1}, None)
    rec, sha_b = s.read("X"); assert sha == sha_b
    s.save("X", {"a": 2}, sha)                                                       # user B saves first
    with pytest.raises(store.ConflictError): s.save("X", {"a": 3}, sha)              # user A's stale save is rejected
    from PIL import Image; import numpy as np
    buf = io.BytesIO(); Image.fromarray((np.random.rand(3000, 4000, 3) * 255).astype("uint8")).save(buf, "JPEG", quality=95)
    n, b = store.prepare_evidence("IMG 1.JPG", buf.getvalue()); assert len(b) <= 350_000 and n.endswith(".jpg") and len(buf.getvalue()) > 3_000_000
    assert store.prepare_evidence("IMG 1.JPG", buf.getvalue())[0] == n               # content-addressed, deduplicated
    with pytest.raises(ValueError): store.prepare_evidence("x.exe", b"1")
