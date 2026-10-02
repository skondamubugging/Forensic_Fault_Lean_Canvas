"""Forensic Electrical Fault Investigation Lean Canvas - Streamlit app.
Storage: GitHub (Contents API, via st.secrets) with local ./data fallback."""
import streamlit as st, pandas as pd, numpy as np, json, base64, math, os, re, glob, requests
from datetime import datetime

st.set_page_config(page_title="Forensic Electrical Fault Canvas", page_icon="⚡", layout="wide")
DATA_DIR = "data"

# ───────────────────────── seeds / reference data ─────────────────────────
def mk(cols, strs, rows, first=None):
    d = pd.DataFrame({c: ([""] * rows if c in strs else [np.nan] * rows) for c in cols})
    if first:
        for i, v in enumerate(first): d.loc[i, cols[0]] = v
    return d

STAT = ["Not verified", "Match", "Deviation", "Critical"]
EV_C = ["Parameter", "Design / Expected", "As-built drawing", "Site / Actual", "Evidence", "Status"]
EV_ROWS = [("Lighting feeder cable (cores x mm²)", "4C x 6 mm²"), ("MCB rating (A)", ""), ("RCCB/RCBO 30 mA", "Provided"),
           ("PE conductor size", ""), ("Pole earth connection", "Dedicated"), ("Junction box IP rating", "IP65"),
           ("Cable gland / sealing", "IP-rated"), ("Burial depth / mechanical protection", ""), ("SPD", ""),
           ("Insulation resistance (MΩ)", ""), ("Earth continuity", "Continuous"), ("Circuit identification / tags", "Every circuit labelled")]
EV = pd.DataFrame([[p, e, "", "", "", "Not verified"] for p, e in EV_ROWS], columns=EV_C)

IRC = ["IR R-E", "IR Y-E", "IR B-E", "IR R-Y", "IR Y-B", "IR B-R", "IR N-E"]
TC = ["Circuit"] + IRC + ["PE cont (Ω)", "Zs (Ω)", "MCB In (A)", "Curve", "RCD IΔn (mA)", "RCD t@IΔn (ms)", "RCD t@5xIΔn (ms)", "Polarity", "Baseline IR (MΩ)"]
TESTS = mk(TC, ["Circuit", "Curve", "Polarity"], 4, ["LDB-01/01", "LDB-01/02", "LDB-01/03", "LDB-01/04"])
SEC = mk(["Section", "Length (m)", "IR (MΩ)", "Remarks"], ["Section", "Remarks"], 4, ["DB–JB1", "JB1–JB2", "JB2–Pole", ""])
EARTH = mk(["Pit ID", "Location", "Electrode type", "Resistance (Ω)", "Design limit (Ω)", "Previous (Ω)", "Condition / remarks"],
           ["Pit ID", "Location", "Electrode type", "Condition / remarks"], 3, ["EP-01", "EP-02", "EP-03"])
POLES = mk(["Pole ID", "PE connected (Yes/No)", "PE continuity (Ω)", "Gland OK (Yes/No)", "Door/gasket OK (Yes/No)", "Water seen (Yes/No)", "Corrosion (Yes/No)", "Remarks"],
           ["Pole ID", "PE connected (Yes/No)", "Gland OK (Yes/No)", "Door/gasket OK (Yes/No)", "Water seen (Yes/No)", "Corrosion (Yes/No)", "Remarks"], 4, ["P-01", "P-02", "P-03", "P-04"])
PROT = pd.DataFrame([["Main incomer", "MCCB", 100.0], ["Park DB", "MCCB/MCB", 63.0], ["Lighting outgoing", "MCB/RCBO", 16.0]], columns=["Level (upstream→down)", "Device", "Rating (A)"])
DOCS = ["SLD", "Electrical layout", "Load schedule", "DB schedule", "Cable schedule", "Cable route drawing", "Earthing layout", "Lightning protection layout",
        "Lighting / pole schedule", "Panel GA & control schematic", "MCB/MCCB/RCCB datasheets", "Cable datasheet", "SPD spec", "LED driver / fitting IP spec",
        "Pump/motor nameplate & wiring", "IR test reports (commissioning)", "Earth resistance report", "Earth continuity test", "Loop impedance test",
        "RCD trip test report", "Polarity test", "Pre-commissioning checklist", "Commissioning certificate", "As-built drawings", "Contractor inspection records",
        "Maintenance records", "Instrument calibration certificates"]
DOCT = pd.DataFrame({"Document": DOCS, "Status": ["Not requested"] * len(DOCS), "Remarks": [""] * len(DOCS)})
RISK = pd.DataFrame([["Electric shock to public/staff", 3.0, 5.0, ""], ["Fire at DB/JB/cable", 2.0, 4.0, ""], ["Equipment damage (drivers/pumps)", 3.0, 3.0, ""],
                     ["Recurrence on similar circuits", 4.0, 3.0, ""]], columns=["Hazard", "Likelihood (1-5)", "Severity (1-5)", "Notes"])
ACT = pd.DataFrame([["", "Corrective", "", "", "Open"]] * 3, columns=["Action (specific)", "Type", "Owner", "Due date", "Status"])
EVID = pd.DataFrame([["", "Photo", "", "", ""]] * 4, columns=["ID", "Type", "Description", "Linked finding", "File / reference"])
SEEDS = {"ev": EV, "tests": TESTS, "sec": SEC, "earth": EARTH, "poles": POLES, "prot": PROT, "docs": DOCT, "risk": RISK, "act": ACT, "evid": EVID}

FAULTS = {"Phase–Neutral short": "Very high fault current, MCB/MCCB trips", "Phase–Phase short": "High fault current, breaker/fuse operation",
          "Phase–Earth fault": "RCD may trip; metalwork may become live", "Insulation breakdown": "Low Megger value, leakage/tripping",
          "Earth leakage": "RCD trips repeatedly", "Loose connection": "Heating, carbonisation, discolouration", "Overload": "Cable/MCB heating, nuisance tripping",
          "Water ingress": "Low IR, corrosion", "Wrong termination": "Abnormal voltage/continuity/polarity", "Neutral failure": "Abnormal voltages, equipment malfunction",
          "Protection failure": "Fault persists longer than it should", "External mechanical damage": "Localised cable damage (civil/landscaping)",
          "Lightning/surge": "SPD damage, multiple LED-driver failures"}
ENV = [("Rain in preceding 24–72 h", "Correlate rain with IR/RCD behaviour; re-test IR after rain; use IP66 enclosures, sealed glands."),
       ("Irrigation sprinklers wet electrical equipment", "Re-aim/relocate sprinklers away from poles, DBs, JBs; keep clearance."),
       ("Water collecting at pole bases / low-lying JBs", "Raise pole bases/JBs above ground level, provide drainage, seal cable entries."),
       ("JBs or cable joints below ground / flooded ducts", "Eliminate buried joints (use accessible IP66 JBs), seal & drain ducts."),
       ("Fountain/pump cables exposed or leaking", "Use water-rated cable & IP68 glands; add dedicated 30 mA RCBO and bonding for pump circuits."),
       ("Excavation/landscaping/tree planting after cable laying", "Trace & mark routes; add route markers; re-test IR on all cables near the works."),
       ("Cables crossing drains/irrigation pipes/tree roots", "Re-route or duct-protect crossings; maintain separation."),
       ("Insufficient burial depth / no warning tape / no protection", "Re-lay to specified depth with sand bed, tiles/duct and warning tape; verify before backfill."),
       ("Rodent/insect damage evidence", "Seal entries, use armoured cable/duct, fit gland plates, pest control."),
       ("Corrosion (poles, glands, earth joints)", "Replace corroded hardware with suitable material, apply protective compound, schedule inspections."),
       ("Unauthorised modification / temporary cables after handover", "Remove/regularise additions; update as-built; restrict access to authorised electricians."),
       ("Lightning / surge event nearby", "Install/inspect SPD (correct type, short leads, backup protection); review lightning-protection requirement."),
       ("Vehicle / mechanical impact", "Add barriers/bollards, raise exposed cabling, use mechanical protection.")]
GATE = ["Fault location identified", "Root cause established", "Damaged equipment repaired/replaced", "Cable IR passed", "PE continuity passed", "Earth system verified",
        "Loop impedance verified", "RCD/RCBO tested (instrument, not test button)", "Protection verified", "Functional test passed", "Water ingress eliminated",
        "As-built drawing updated", "Photos recorded", "Final report signed"]
PREV = ["Adopt an Inspection & Test Plan with hold points (cable route/depth, glands, earthing) BEFORE backfilling and before energising.",
        "Make commissioning records mandatory: IR (all 6 combinations), PE continuity, Zs, electrode R, RCD trip time/current, polarity – with instrument calibration.",
        "Periodic programme: RCD test (instrument) every 3–6 months, IR trending annually and after monsoon, earth-pit inspection/electrode test annually, thermography of DBs.",
        "Use IP66 enclosures with correct glands; avoid buried joints; label every circuit (DB, MCB, cable size, length, pole).",
        "Control changes: authorised electricians only, LOTO, handover as-built, no unrecorded additions.",
        "Roll out the same audit to all GVMC parks (lessons learned)."]
IA = {"B": 5, "C": 10, "D": 20}  # magnetic trip multiples
AMP = {2.5: (34, 26), 4: (44, 35), 6: (54, 44), 10: (72, 60), 16: (94, 80), 25: (122, 105), 35: (148, 130), 50: (175, 158), 70: (215, 200), 95: (258, 245)}  # indicative Cu XLPE (ground, air)

# ───────────────────────── helpers / state ─────────────────────────
def num(x):
    try:
        v = float(x)
        return None if math.isnan(v) else v
    except Exception:
        return None

def T(k, label, **kw): st.session_state.setdefault("f_" + k, ""); return st.text_input(label, key="f_" + k, **kw)
def A(k, label, h=90, **kw): st.session_state.setdefault("f_" + k, ""); return st.text_area(label, key="f_" + k, height=h, **kw)
def N(k, label, v, **kw): st.session_state.setdefault("f_" + k, v); return st.number_input(label, key="f_" + k, **kw)
def S(k, label, opts): st.session_state.setdefault("f_" + k, opts[0]); return st.selectbox(label, opts, key="f_" + k)
def C(k, label): st.session_state.setdefault("f_" + k, False); return st.checkbox(label, key="f_" + k)
def g(k, d=None): return st.session_state.get("f_" + k, d)

def coerce(name, d):
    seed = SEEDS[name]
    d = pd.DataFrame(d)
    for c in seed.columns:
        if c not in d: d[c] = seed[c].iloc[0] if len(seed) else ""
    d = d[list(seed.columns)].copy()
    for c in seed.columns:
        d[c] = pd.to_numeric(d[c], errors="coerce") if seed[c].dtype.kind == "f" else d[c].fillna("").astype(str)
    return d.reset_index(drop=True) if len(d) else seed.copy()

def init():
    if "seed" not in st.session_state:
        st.session_state.update(seed={k: v.copy() for k, v in SEEDS.items()}, out={}, ver=0, photos=[])
    if "_pending" in st.session_state:
        rec = st.session_state.pop("_pending")
        for k in [k for k in st.session_state if k.startswith("f_")]: del st.session_state[k]
        st.session_state.update(rec.get("fields", {}))
        st.session_state.seed = {n: coerce(n, rec["tables"][n]) if rec.get("tables", {}).get(n) else SEEDS[n].copy() for n in SEEDS}
        st.session_state.out, st.session_state.photos = {}, rec.get("photos", [])
        st.session_state.ver += 1

def tbl(n): return st.session_state.out.get(n, st.session_state.seed[n])

def edit(n, cfg=None):
    st.session_state.out[n] = st.data_editor(st.session_state.seed[n], key=f"ed_{n}_{st.session_state.ver}", num_rows="dynamic", column_config=cfg or {})
    return st.session_state.out[n]

def record():
    f = {k: v for k, v in st.session_state.items() if k.startswith("f_") and isinstance(v, (str, int, float, bool, list))}
    return {"schema": 1, "saved_at": datetime.now().isoformat(timespec="seconds"), "fields": f, "photos": st.session_state.photos,
            "tables": {n: json.loads(tbl(n).to_json(orient="records")) for n in SEEDS}}

# ───────────────────────── storage (GitHub + local) ─────────────────────────
def gh():
    try:
        s = st.secrets["github"]
        return s["token"], s["repo"], s.get("branch", "main"), s.get("folder", "investigations")
    except Exception:
        return None

def _h(tok, raw=False): return {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json"}

def gh_put(path, content, msg):
    tok, repo, br, _ = gh(); url = f"https://api.github.com/repos/{repo}/contents/{path}"
    r = requests.get(url, headers=_h(tok), params={"ref": br}, timeout=20)
    body = {"message": msg, "content": base64.b64encode(content).decode(), "branch": br}
    if r.status_code == 200: body["sha"] = r.json()["sha"]
    requests.put(url, headers=_h(tok), json=body, timeout=40).raise_for_status()

def list_records():
    c = gh()
    try:
        if c:
            r = requests.get(f"https://api.github.com/repos/{c[1]}/contents/{c[3]}", headers=_h(c[0]), params={"ref": c[2]}, timeout=20)
            return sorted(x["name"][:-5] for x in r.json() if x["type"] == "file" and x["name"].endswith(".json")) if r.status_code == 200 else []
        return sorted(os.path.basename(p)[:-5] for p in glob.glob(f"{DATA_DIR}/*.json"))
    except Exception:
        return []

def save():
    iid = re.sub(r"[^A-Za-z0-9_-]", "_", g("inv_id", "").strip())
    if not iid: return st.sidebar.error("Enter an Investigation ID first.")
    names = list(st.session_state.photos); c = gh()
    try:
        for up in g("photos") or []:
            if up.size > 5_000_000: st.sidebar.warning(f"{up.name} >5 MB, skipped"); continue
            nm = re.sub(r"[^A-Za-z0-9_.-]", "_", up.name)
            if c: gh_put(f"{c[3]}/{iid}/evidence/{nm}", up.getvalue(), f"evidence {iid}: {nm}")
            else:
                os.makedirs(f"{DATA_DIR}/{iid}", exist_ok=True); open(f"{DATA_DIR}/{iid}/{nm}", "wb").write(up.getvalue())
            if nm not in names: names.append(nm)
        st.session_state.photos = names
        data = json.dumps(record(), indent=2).encode()
        if c: gh_put(f"{c[3]}/{iid}.json", data, f"Update investigation {iid}")
        else:
            os.makedirs(DATA_DIR, exist_ok=True); open(f"{DATA_DIR}/{iid}.json", "wb").write(data)
        st.sidebar.success(f"Saved '{iid}' to {'GitHub' if c else 'local ./data (temporary!)'}")
    except Exception as e:
        st.sidebar.error(f"Save failed: {e}")

def load(iid):
    c = gh()
    try:
        if c:
            r = requests.get(f"https://api.github.com/repos/{c[1]}/contents/{c[3]}/{iid}.json", headers=_h(c[0], True), params={"ref": c[2]}, timeout=20)
            r.raise_for_status(); rec = r.json()
        else: rec = json.load(open(f"{DATA_DIR}/{iid}.json"))
        st.session_state["_pending"] = rec; st.rerun()
    except Exception as e:
        if "Rerun" in type(e).__name__: raise
        st.sidebar.error(f"Load failed: {e}")

# ───────────────────────── analysis engine ─────────────────────────
def analyze():
    U0, irmin, irwarn, cmax, zf = g("U0", 230.0), g("irmin", 1.0), g("irwarn", 10.0), g("cmax", 1.0), g("zf", 0.8)
    F, rows = [], []
    def add(cl, area, find, ev, rec, when): F.append(dict(Class=cl, Area=area, Finding=find, Evidence=ev, Recommendation=rec, When=when))
    m = tbl("tests")
    allv = [v for c in IRC for v in (num(x) for x in m[c]) if v is not None]
    med = float(np.median(allv)) if allv else None
    for _, r in m.iterrows():
        nm = r["Circuit"] or "?"; vals = {c: num(r[c]) for c in IRC}; oth = {k: num(r[k]) for k in TC[8:] if k not in ("Curve", "Polarity")}
        if not any(v is not None for v in list(vals.values()) + list(oth.values())): continue
        notes, st_ = [], "PASS"
        def bad(level, msg):
            nonlocal st_
            notes.append(msg); st_ = "FAIL" if level in "AB" or st_ == "FAIL" else ("WARN" if st_ == "PASS" else st_)
        low = {c: v for c, v in vals.items() if v is not None and v < irmin}
        sus = {c: v for c, v in vals.items() if v is not None and c not in low and (v < irwarn or (med and v < med / 20))}
        if low:
            pe = any(c.endswith("-E") for c in low); txt = ", ".join(f"{c[3:]}={v:g} MΩ" for c, v in low.items())
            bad("A" if pe else "B", f"IR below {irmin:g} MΩ: {txt}")
            add("A" if pe else "B", "Insulation", f"{nm}: insulation resistance below minimum ({txt})",
                f"Megger; median of all readings = {med:g} MΩ" if med else "Megger",
                "Isolate circuit; sectionalise at JBs and locate low-IR section; repair/replace cable or joint with approved method; replace damaged gland/enclosure (IP66); re-test IR + PE continuity before energising.", "Immediate")
        if sus:
            txt = ", ".join(f"{c[3:]}={v:g} MΩ" for c, v in sus.items()); bad("C", f"Suspicious vs fleet/new-install norm: {txt}")
            add("C", "Insulation", f"{nm}: IR is low relative to other circuits ({txt})", f"Median {med:g} MΩ; warn level {irwarn:g} MΩ" if med else "", "Re-test after drying/rain; sectionalise; trend IR monthly until understood.", "Short term")
        vv = [v for v in vals.values() if v is not None]; b = oth.get("Baseline IR (MΩ)")
        if b and vv and min(vv) < b / 10:
            bad("C", f"IR fell >90% vs commissioning ({min(vv):g} vs {b:g} MΩ)")
            add("C", "Insulation", f"{nm}: insulation deteriorated >90% since commissioning", f"{b:g} → {min(vv):g} MΩ", "Identify ageing/water/mechanical cause; add IR trending programme.", "Short term")
        pc = oth.get("PE cont (Ω)")
        if pc is not None and pc > cmax:
            op = pc >= 100; bad("A" if op else "B", f"PE continuity {pc:g} Ω {'(OPEN)' if op else ''}")
            add("A" if op else "B", "Earthing", f"{nm}: protective-conductor {'OPEN/' if op else ''}high resistance ({pc:g} Ω > {cmax:g} Ω)", "Continuity test",
                "Restore continuous PE from each pole/metalwork to DB earth bar; replace corroded terminations; record continuity and repeat.", "Immediate" if op else "Short term")
        zs, inn, cv = oth.get("Zs (Ω)"), oth.get("MCB In (A)"), str(r["Curve"]).upper().strip()
        if zs is not None and inn and cv in IA:
            zmax = zf * U0 / (IA[cv] * inn)
            if zs > zmax:
                bad("B", f"Zs {zs:g} Ω > max {zmax:.3g} Ω (curve {cv}, {inn:g} A)")
                add("B", "Earthing", f"{nm}: earth-fault loop impedance too high for instant disconnection ({zs:g} > {zmax:.3g} Ω)", f"Ia≈{IA[cv]*inn:g} A required",
                    "Improve PE path/connections, reduce MCB rating/curve if cable allows, or add RCBO; re-measure Zs.", "Short term")
        elif zs is not None: notes.append("Zs entered but MCB In/curve missing")
        rc, t1, t5 = oth.get("RCD IΔn (mA)"), oth.get("RCD t@IΔn (ms)"), oth.get("RCD t@5xIΔn (ms)")
        if rc is None: bad("C", "RCD data missing")
        else:
            if rc == 0:
                bad("A", "No RCD"); add("A", "Protection", f"{nm}: no RCD/RCBO on a public-park outdoor circuit", "Test sheet", "Install 30 mA RCBO and verify with RCD tester.", "Immediate")
            elif rc > 30: bad("B", f"RCD {rc:g} mA > 30 mA"); add("B", "Protection", f"{nm}: RCD sensitivity {rc:g} mA is too coarse for public outdoor circuit", "Nameplate", "Provide 30 mA RCD/RCBO.", "Short term")
            if t1 is not None and t1 > 300: bad("A", f"RCD t={t1:g} ms @IΔn (>300)"); add("A", "Protection", f"{nm}: RCD failed trip-time test ({t1:g} ms)", "RCD tester", "Replace RCD/RCBO, check wiring/neutral; re-test.", "Immediate")
            if t5 is not None and t5 > 40: bad("B", f"RCD t={t5:g} ms @5xIΔn (>40)"); add("B", "Protection", f"{nm}: RCD slow at 5×IΔn ({t5:g} ms)", "RCD tester", "Replace/verify RCD.", "Short term")
            if t1 is None and rc: notes.append("RCD trip time not tested (test button ≠ test)")
        if str(r["Polarity"]).strip().lower() == "fail": bad("A", "Polarity FAIL"); add("A", "Wiring", f"{nm}: wrong polarity", "Polarity test", "Correct termination; verify switching in phase.", "Immediate")
        rows.append({"Circuit": nm, "Auto status": {"PASS": "🟢 PASS", "WARN": "🟡 WARN", "FAIL": "🔴 FAIL"}[st_], "Notes": "; ".join(notes)})
    s = tbl("sec"); sv = [(r["Section"], num(r["IR (MΩ)"])) for _, r in s.iterrows() if num(r["IR (MΩ)"]) is not None]
    if len(sv) >= 2:
        lo = min(sv, key=lambda x: x[1]); others = sorted(v for _, v in sv if v != lo[1]) or [lo[1]]; mo = float(np.median(others))
        if lo[1] < irmin or lo[1] < mo / 20:
            add("A" if lo[1] < irmin else "B", "Fault location", f"Faulty section isolated: {lo[0]} ({lo[1]:g} MΩ vs median {mo:g} MΩ of others)", "Sectional IR", "Excavate/open only this section; inspect JB, gland, joint; repair; re-test.", "Immediate")
    for _, r in tbl("earth").iterrows():
        rr, lim, prev = num(r["Resistance (Ω)"]), num(r["Design limit (Ω)"]), num(r["Previous (Ω)"]); cond = str(r["Condition / remarks"]).lower()
        if rr is not None and lim is not None and rr > lim: add("C", "Earthing", f"{r['Pit ID']}: electrode {rr:g} Ω exceeds design limit {lim:g} Ω", "Earth tester", "Add electrode/treat soil, re-test (fall-of-potential, 61.8 %).", "Medium term")
        if rr is not None and prev and rr > 1.5 * prev: add("C", "Earthing", f"{r['Pit ID']}: resistance rose >50% ({prev:g} → {rr:g} Ω)", "Trend", "Inspect joints/corrosion; moisten/treat pit; re-test.", "Medium term")
        if any(w in cond for w in ("corr", "loose", "broken", "open")): add("C", "Earthing", f"{r['Pit ID']}: connection defect ({r['Condition / remarks']})", "Visual", "Re-make bolted joint, protect against corrosion, re-test continuity.", "Short term")
    for _, r in tbl("poles").iterrows():
        pid = r["Pole ID"]; yes = lambda c: str(r[c]).strip().lower() == "yes"; no = lambda c: str(r[c]).strip().lower() == "no"
        if no("PE connected (Yes/No)"): add("A", "Earthing", f"{pid}: pole earth disconnected", "Visual/continuity", "Connect PE to pole & DB earth bar; verify continuity & Zs.", "Immediate")
        pc = num(r["PE continuity (Ω)"])
        if pc is not None and pc > cmax: add("B", "Earthing", f"{pid}: PE continuity {pc:g} Ω > {cmax:g} Ω", "Continuity", "Repair PE path.", "Short term")
        if no("Gland OK (Yes/No)") or no("Door/gasket OK (Yes/No)") or yes("Water seen (Yes/No)"): add("C", "IP / water", f"{pid}: gland/door sealing defect or water inside", "Visual/photo", "Replace gland/gasket, dry & clean, IP66 enclosure, re-test IR.", "Short term")
    for _, r in tbl("ev").iterrows():
        sts, p = r["Status"], r["Parameter"]
        if sts in ("Deviation", "Critical"):
            add("A" if sts == "Critical" else "C", "As-built", f"{p}: expected '{r['Design / Expected']}' but found '{r['Site / Actual']}'", r["Evidence"] or "—", "Rectify to specification or obtain engineer-approved deviation; update as-built.", "Short term")
        elif sts == "Match" and r["Design / Expected"] and r["Site / Actual"] and re.sub(r"\W", "", r["Design / Expected"].lower()) != re.sub(r"\W", "", r["Site / Actual"].lower()):
            add("D", "As-built", f"{p}: marked 'Match' but values differ ('{r['Design / Expected']}' vs '{r['Site / Actual']}')", "Table", "Re-check status.", "Medium term")
    p = tbl("prot"); rt = [num(x) for x in p["Rating (A)"] if num(x) is not None]
    for a, b in zip(rt, rt[1:]):
        if b and a / b < 1.6: add("C", "Protection", f"Poor selectivity: upstream {a:g} A / downstream {b:g} A < 1.6", "Rating chain (indicative)", "Check manufacturer selectivity tables; adjust ratings/settings.", "Medium term")
    if g("spd") == "No": add("C", "Protection", "No SPD installed", "Inspection", "Install Type 1+2/2 SPD with backup protection and short earth lead.", "Medium term")
    if g("shock") == "Yes": add("A", "Incident", "Person received an electric shock", "Operator statement", "Treat as serious incident: isolate, secure, report per CEA/Electrical Inspector requirements.", "Immediate")
    if g("fire") == "Yes": add("A", "Incident", "Smoke/fire observed", "Operator statement", "Preserve evidence; thermography; check terminations and cable ratings.", "Immediate")
    for q, rec in ENV:
        if g("env_" + q[:12]) == "Yes": add("C", "Environment", q, "Site observation", rec, "Short term")
    dm = [r["Document"] for _, r in tbl("docs").iterrows() if r["Status"] == "Missing"]
    if dm:
        key = any(w in d for d in dm for w in ("test", "Commissioning", "RCD", "IR", "Earth"))
        add("C" if key else "D", "Process", f"{len(dm)} document(s) missing: " + ", ".join(dm[:6]) + ("…" if len(dm) > 6 else ""), "Document checklist", "Obtain from contractor/GVMC; where tests absent, re-perform and baseline them.", "Short term")
    order = {c: i for i, c in enumerate("ABCDE")}
    F.sort(key=lambda f: order[f["Class"]])
    return F, pd.DataFrame(rows)

def draft_rca(F):
    top = [f for f in F if f["Class"] in "AB"]
    if not top: return "Not enough test data entered to draft an evidence-based RCA statement."
    t = "Evidence-based draft: " + " ".join(f"{f['Finding']}." for f in top[:5])
    env = [f["Finding"] for f in F if f["Area"] == "Environment"]
    if env: t += " Contributing environmental factors: " + "; ".join(env[:3]) + "."
    return t + " Process question to close: why did inspection/commissioning not detect this before handover?"

def md_t(d):
    d = d.copy().astype(object).where(d.notna(), "")
    return "| " + " | ".join(map(str, d.columns)) + " |\n|" + "---|" * len(d.columns) + "\n" + "".join("| " + " | ".join(str(x).replace("|", "/") for x in r) + " |\n" for r in d.values)

def report(F, circ):
    L = [f"# Forensic Electrical Fault Investigation – {g('inv_id', '')}", f"Generated {datetime.now():%d-%b-%Y %H:%M}\n",
         "## 1. Incident", *[f"- **{l}:** {g(k, '')}" for l, k in [("Park", "park"), ("Location", "loc"), ("Fault date/time", "dt"), ("Affected circuit", "circ"), ("Weather", "wx"), ("Investigator", "inv"), ("Contractor", "contractor")]],
         f"- **Fault types:** {', '.join(g('ftypes', []) or [])}", f"\n{g('narr', '')}", "\n## 2. System boundary / fault path", *[f"- **{l}:** {g('sb_' + k, '')}" for l, k in [("Utility/meter", "u"), ("MDB/DB", "d"), ("Cable/JB", "c"), ("Pole/equipment", "p"), ("Earth", "e")]],
         "\n## 3-5. Expected vs Actual vs Evidence", md_t(tbl("ev")), "## Protection chain", md_t(tbl("prot")), "## 6-7. Test matrix", md_t(tbl("tests")), "### Auto-check", md_t(circ) if len(circ) else "_none_",
         "### Section IR", md_t(tbl("sec")), "### Earth pits", md_t(tbl("earth")), "### Poles", md_t(tbl("poles")),
         "\n## Findings & recommendations (auto)", md_t(pd.DataFrame(F)) if F else "_none_", "\n## Root cause", f"- Immediate: {g('l1', '')}", f"- Physical: {g('l2', '')}", f"- Installation: {g('l3', '')}", f"- Process: {g('l4', '')}", f"- Management: {g('l5', '')}",
         f"\n{draft_rca(F)}", "\n## Five Why", *[f"{i}. {g('why' + str(i), '')}" for i in range(1, 6)], f"\n## Protection failure\n{g('pf', '')}",
         "\n## Risk", md_t(tbl("risk")), "\n## Actions", md_t(tbl("act")), "\n## Preventive actions", *[f"- {p}" for p in PREV],
         "\n## Closure gate", *[f"- [{'x' if g('gate_' + str(i)) else ' '}] {x}" for i, x in enumerate(GATE)], f"\n## Lessons learned\n{g('lessons', '')}"]
    return "\n".join(L)

# ───────────────────────── UI ─────────────────────────
init()
with st.sidebar:
    st.title("⚡ Forensic Fault Canvas")
    T("inv_id", "Investigation ID (file name)", placeholder="GVMC-SKL-PARK-2026-01")
    st.caption("💾 GitHub storage connected" if gh() else "⚠️ No GitHub secrets – saving to local ./data (lost on Streamlit Cloud restarts)")
    if st.button("💾 Save investigation", type="primary"): save()
    recs = list_records(); pick = st.selectbox("Saved investigations", [""] + recs)
    c1, c2 = st.columns(2)
    if c1.button("📂 Load") and pick: load(pick)
    if c2.button("🆕 New"):
        for k in list(st.session_state): del st.session_state[k]
        st.rerun()
    with st.expander("⚙️ Criteria / thresholds"):
        N("U0", "Phase-earth voltage U0 (V)", 230.0); N("irmin", "Min IR – fail (MΩ) [IS 732: >1 MΩ @500 V]", 1.0); N("irwarn", "IR warning level for NEW outdoor cable (MΩ)", 10.0)
        N("cmax", "Max PE continuity (Ω)", 1.0); N("zf", "Zs safety factor (field-test 0.8)", 0.8)
    st.file_uploader("📎 Evidence photos/reports (saved with record)", accept_multiple_files=True, key="f_photos")
    if st.session_state.photos: st.caption("Stored: " + ", ".join(st.session_state.photos))

names = ["📊 Dashboard", "1-2 Incident & Boundary", "3 Design Baseline", "4-5 As-Built & Protection", "6-7 Earthing & Tests", "8 Environment", "9 Evidence",
         "10-12 Root Cause", "13 Risk", "🧮 Calculators", "14-17 Actions & Closure", "📄 Report"]
tabs = st.tabs(names)

with tabs[1]:
    st.subheader("Block 1 – Incident definition")
    a, b, c = st.columns(3)
    with a: T("park", "Park name"); T("loc", "Location (Srikakulam)"); T("dt", "Fault date/time"); T("inv", "Investigator"); T("contractor", "Contractor / consultant"); T("handover", "Handover / commissioning date")
    with b: T("circ", "Affected circuit(s)"); T("wx", "Weather"); S("rain", "Rain in last 24–72 h?", ["Unknown", "Yes", "No"]); S("irr", "Irrigation operating?", ["Unknown", "Yes", "No"]); S("pumps", "Fountain/pumps operating?", ["Unknown", "Yes", "No"]); T("brk", "Breaker/RCD that operated")
    with c: S("onsw", "Fault on switching ON?", ["Unknown", "Yes", "No"]); S("shock", "Anyone received shock?", ["Unknown", "No", "Yes"]); S("fire", "Smoke / fire?", ["Unknown", "No", "Yes"]); S("rep", "Repeated failure?", ["Unknown", "Yes", "No"]); S("sys", "Earthing system", ["Unknown", "TN-S", "TN-C-S", "TN-C", "TT", "IT"]); S("spd", "SPD installed?", ["Unknown", "Yes", "No"])
    st.session_state.setdefault("f_ftypes", []); st.multiselect("Fault type hypotheses (don't accept 'shortcut' untested)", list(FAULTS), key="f_ftypes")
    for ft in g("ftypes", []): st.caption(f"• **{ft}** → typical evidence: {FAULTS[ft]}")
    A("narr", "Symptoms / operator statements / sequence of events", 120)
    st.subheader("Block 2 – System boundary (trace source → equipment → earth)")
    a, b = st.columns(2)
    with a: A("sb_u", "Utility supply → meter", 60); A("sb_d", "MDB → DB (ratings, cable)", 60); A("sb_c", "Cable → JB (route, length, joints)", 60)
    with b: A("sb_p", "Pole / equipment (pump, fountain, lights)", 60); A("sb_e", "Earth path (pole PE → DB → pit)", 60); A("sb_fp", "Exact fault path (hypothesis)", 60)
    st.info("Safety first: isolate, LOTO, prove dead, preserve evidence (photograph before cleaning/replacing). Do not re-energise to 'see if it trips again'.")

with tabs[2]:
    st.subheader("Block 3 – Design baseline: documents obtained")
    edit("docs", {"Status": st.column_config.SelectboxColumn(options=["Not requested", "Requested", "Received", "Missing", "N/A"])})
    a, b = st.columns(2)
    with a: A("design", "Design basis (voltage, load schedule, cable schedule, earthing design, IP spec)", 120)
    with b: A("designgap", "Documentation gaps / discrepancies between drawings", 120)

with tabs[3]:
    st.subheader("Blocks 4 & A – Expected vs Actual vs Evidence (design / as-built / site)")
    edit("ev", {"Status": st.column_config.SelectboxColumn(options=STAT)})
    st.subheader("Block 5 – Protection chain (upstream → downstream)")
    edit("prot"); st.caption("Auto-check: indicative selectivity ratio ≥ 1.6 between successive ratings; confirm with manufacturer tables.")
    A("prot_notes", "Equipment & protection notes (MCB/MCCB/RCD/SPD/contactors, IP ratings, nameplates)", 100)

with tabs[4]:
    st.subheader("Block 7 – Test matrix per circuit (IR in MΩ; Megger 500 V DC, circuit isolated & electronics disconnected)")
    edit("tests", {"Curve": st.column_config.SelectboxColumn(options=["", "B", "C", "D"]), "Polarity": st.column_config.SelectboxColumn(options=["", "Pass", "Fail"])})
    F, circ = analyze()
    if len(circ): st.dataframe(circ, hide_index=True)
    st.subheader("Sectionalised IR (divide & test)"); edit("sec")
    st.subheader("Block 6 – Earth pits"); edit("earth")
    st.subheader("Pole / enclosure inspection")
    edit("poles", {c: st.column_config.SelectboxColumn(options=["", "Yes", "No"]) for c in POLES.columns if "(Yes/No)" in c})
    st.subheader("Energised measurements (after safe clearance)")
    a, b, c = st.columns(3)
    with a: A("volt", "Voltages R-Y, Y-B, B-R, R-N, Y-N, B-N, N-E", 80)
    with b: A("curr", "Load currents R / Y / B / N", 80)
    with c: A("therm", "Thermography (hot terminals, ambient, load)", 80)

with tabs[5]:
    st.subheader("Block 8 – Environmental / physical contributors")
    cols = st.columns(2)
    for i, (q, rec) in enumerate(ENV):
        with cols[i % 2]: S("env_" + q[:12], q, ["Unknown", "No", "Yes"])
    A("env_notes", "Environment notes (correlate rain/irrigation → IR → RCD → fault location)", 100)

with tabs[6]:
    st.subheader("Block 9 – Evidence matrix (evidence → finding linkage)")
    edit("evid", {"Type": st.column_config.SelectboxColumn(options=["Photo", "Reading", "Report", "Witness", "Component"])})
    st.caption("Upload files from the sidebar; reference their names here.")

with tabs[7]:
    st.subheader("Blocks 10–12 – Fault tree, protection failure, root cause")
    a, b = st.columns(2)
    with a:
        st.markdown("**Fault tree (5 levels)**")
        A("l1", "L1 Immediate – what physically failed?", 60); A("l2", "L2 Physical – why?", 60); A("l3", "L3 Installation – why not protected?", 60)
        A("l4", "L4 Process – why accepted?", 60); A("l5", "L5 Management – why did the process fail?", 60)
    with b:
        st.markdown("**Five-Why chain**")
        for i, h in enumerate(["Electrical fault occurred because…", "Why? (e.g. insulation breakdown)", "Why? (e.g. water entered JB)", "Why? (e.g. gland/sealing inadequate)", "Why? (e.g. inspection did not verify IP integrity)"], 1): A(f"why{i}", f"Why {i}: {h}", 55)
    A("pf", "Block 11 – Why didn't MCB/RCCB/RCBO/earthing prevent escalation?", 90)
    A("rca", "Block 12 – Root cause statement (primary + contributing + latent/systemic)", 120)
    st.info(draft_rca(F))

with tabs[8]:
    st.subheader("Block 13 – Risk & consequence"); rk = edit("risk"); rk = rk.copy()
    rk["Score"] = rk["Likelihood (1-5)"] * rk["Severity (1-5)"]; rk["Priority"] = rk["Score"].apply(lambda s: "—" if pd.isna(s) else "🔴 High" if s >= 15 else "🟠 Medium" if s >= 8 else "🟢 Low")
    st.dataframe(rk, hide_index=True); st.session_state["_maxrisk"] = rk["Score"].max()
    st.caption("Finding classes: A critical safety defect · B major · C significant · D minor · E observation")

with tabs[9]:
    st.subheader("Engineering calculators")
    U0 = g("U0", 230.0)
    with st.expander("Voltage drop"):
        a, b, c, d = st.columns(4); ph = a.selectbox("System", ["3-phase 415 V", "1-phase 230 V"]); I = b.number_input("Current (A)", 0.0, value=10.0); L = c.number_input("Length (m)", 0.0, value=100.0); Ar = d.number_input("Cond. area (mm²)", 0.5, value=6.0)
        mat = a.selectbox("Material", ["Copper", "Aluminium"]); lim = b.number_input("Limit (%)", 0.0, value=5.0)
        rho = 0.0225 if mat == "Copper" else 0.036; V = 415 if ph.startswith("3") else 230; dv = (math.sqrt(3) if V == 415 else 2) * I * rho * L / Ar
        st.metric("Voltage drop", f"{dv:.1f} V ({100*dv/V:.2f}%)"); _ = (st.success if 100 * dv / V <= lim else st.error)("Within limit" if 100 * dv / V <= lim else "Exceeds limit – upsize cable / shorten run")
    with st.expander("Cable ⇄ breaker coordination (Ib ≤ In ≤ Iz)"):
        a, b, c, d = st.columns(4); Ib = a.number_input("Design current Ib (A)", 0.0, value=8.0); In = b.number_input("MCB rating In (A)", 0.0, value=16.0); sz = c.selectbox("Cable mm² (Cu)", list(AMP), index=2); mth = d.selectbox("Method", ["Buried", "In air"])
        ins = a.selectbox("Insulation", ["XLPE", "PVC (×0.8)"]); kt = b.number_input("Temp factor", 0.1, 1.5, 0.9); kg = c.number_input("Grouping factor", 0.1, 1.0, 0.85)
        Iz = AMP[sz][0 if mth == "Buried" else 1] * (0.8 if "PVC" in ins else 1) * kt * kg; st.write(f"Indicative derated capacity Iz ≈ **{Iz:.1f} A** (verify against manufacturer datasheet)")
        ok = Ib <= In <= Iz; _ = (st.success if ok else st.error)("Ib ≤ In ≤ Iz satisfied" if ok else "NOT satisfied: " + ("load exceeds breaker" if Ib > In else "breaker exceeds cable capacity – cable unprotected from overload"))
    with st.expander("Earth-fault loop, disconnection, touch voltage, PE size"):
        a, b, c, d = st.columns(4); zs = a.number_input("Measured Zs (Ω)", 0.001, value=1.2); In2 = b.number_input("MCB In (A)", 1.0, value=16.0); cv = c.selectbox("Curve", ["B", "C", "D"], index=1); t = d.number_input("Disconnect time (s)", 0.01, value=0.4)
        Ifl = U0 / zs; Ia = IA[cv] * In2; zmx = g("zf", 0.8) * U0 / Ia; Rpe = a.number_input("PE resistance to pole (Ω)", 0.0, value=0.2); kk = b.selectbox("k (Cu PE)", [115, 143], help="115 PVC, 143 XLPE")
        m1, m2, m3, m4 = st.columns(4); m1.metric("Fault current", f"{Ifl:.0f} A"); m2.metric("Required Ia", f"{Ia:.0f} A"); m3.metric("Max Zs", f"{zmx:.3f} Ω"); m4.metric("Touch voltage ≈", f"{Ifl*Rpe:.0f} V")
        _ = (st.success if zs <= zmx else st.error)("Instant disconnection achievable" if zs <= zmx else "Zs too high – breaker may not trip magnetically; add RCBO/improve PE")
        st.write(f"Min PE/earthing conductor (adiabatic): **{Ifl*math.sqrt(t)/kk:.2f} mm²**" + ("  ⚠️ touch voltage > 50 V" if Ifl * Rpe > 50 else ""))
    with st.expander("RCD / TT-system check"):
        a, b, c = st.columns(3); RA = a.number_input("Earth electrode RA (Ω)", 0.0, value=5.0); Idn = b.number_input("RCD IΔn (mA)", 1.0, value=30.0); st.write(f"RA × IΔn = **{RA*Idn/1000:.1f} V** (limit 50 V) – max RA = {50000/Idn:.0f} Ω")
        _ = (st.success if RA * Idn / 1000 <= 50 else st.error)("OK for TT" if RA * Idn / 1000 <= 50 else "Fail")
        st.caption("Acceptance: trip ≤300 ms at IΔn, ≤40 ms at 5×IΔn, trip current 50–100% IΔn. Earth resistance alone does not prove safety.")
    with st.expander("Loose connection heating  (P = I²R)"):
        a, b, c = st.columns(3); I3 = a.number_input("Current (A)", 0.0, value=20.0); Rg = b.number_input("Good joint R (Ω)", 0.0, value=0.001, format="%.4f"); Rb = c.number_input("Loose joint R (Ω)", 0.0, value=0.1, format="%.4f")
        st.write(f"Good: **{I3**2*Rg:.2f} W** · Loose: **{I3**2*Rb:.1f} W** (×{(Rb/Rg if Rg else 0):.0f}) → local hot-spot, carbonisation risk")
    with st.expander("Phase imbalance & neutral current"):
        a, b, c = st.columns(3); R_ = a.number_input("R (A)", 0.0, value=8.0); Y_ = b.number_input("Y (A)", 0.0, value=8.5); B_ = c.number_input("B (A)", 0.0, value=22.0)
        av = (R_ + Y_ + B_) / 3; im = 100 * (max(R_, Y_, B_) - av) / av if av else 0; ineu = math.sqrt(max(0, R_**2 + Y_**2 + B_**2 - R_ * Y_ - Y_ * B_ - B_ * R_))
        st.metric("Imbalance", f"{im:.1f}%"); st.metric("Expected neutral current", f"{ineu:.1f} A"); _ = (st.error if im > 10 else st.success)("Rebalance loads / check neutral & faulty equipment" if im > 10 else "Acceptable")
    with st.expander("Motor / pump full-load current"):
        a, b, c, d = st.columns(4); kw = a.number_input("kW", 0.1, value=5.5); pf = b.number_input("PF", 0.1, 1.0, 0.82); ef = c.number_input("Efficiency", 0.1, 1.0, 0.88); Vm = d.number_input("V", 100.0, value=415.0)
        fla = kw * 1000 / (math.sqrt(3) * Vm * pf * ef); st.write(f"FLA ≈ **{fla:.1f} A** – compare with nameplate/clamp reading; set overload ≈ 1.0–1.05×FLA; DOL MCB ≈ 1.25–1.5×FLA (motor-duty curve).")
    with st.expander("IR trend / degradation"):
        a, b = st.columns(2); b0 = a.number_input("Commissioning IR (MΩ)", 0.001, value=300.0); b1 = b.number_input("Now (MΩ)", 0.001, value=1.0)
        st.metric("Change", f"{100*(b1-b0)/b0:.1f}%"); _ = (st.error if b1 < b0 / 10 else st.success)("Severe degradation – investigate" if b1 < b0 / 10 else "Within expectation")
    with st.expander("Earth electrode estimate & fall-of-potential layout"):
        a, b, c, d = st.columns(4); rho_s = a.number_input("Soil resistivity (Ω·m)", 1.0, value=100.0); Lr = b.number_input("Rod length (m)", 0.5, value=3.0); dr = c.number_input("Rod dia (m)", 0.005, value=0.0125, format="%.4f"); Dc = d.number_input("Current probe C distance (m)", 5.0, value=30.0)
        st.write(f"Single-rod R ≈ **{rho_s/(2*math.pi*Lr)*math.log(4*Lr/dr):.1f} Ω** (Dwight) · place potential probe P at **{0.618*Dc:.1f} m** (61.8%) from electrode; take several readings.")

with tabs[10]:
    st.subheader("Blocks 14 & 15 – Corrective & preventive actions (auto-recommended from your data)")
    if F:
        for w in ["Immediate", "Short term", "Medium term"]:
            sub = [f for f in F if f["When"] == w]
            if sub: st.markdown(f"**{w}**"); st.dataframe(pd.DataFrame(sub)[["Class", "Area", "Finding", "Recommendation"]], hide_index=True)
    else: st.info("Enter test data, as-built comparison and environment answers – recommendations appear here automatically.")
    st.markdown("**Standard preventive actions**"); st.markdown("\n".join(f"- {p}" for p in PREV))
    st.markdown("**Your action register**"); edit("act", {"Type": st.column_config.SelectboxColumn(options=["Corrective", "Preventive"]), "Status": st.column_config.SelectboxColumn(options=["Open", "In progress", "Done", "Verified"])})
    st.subheader("Block 16 / C – Closure gate"); st.caption("'Breaker replaced, lights working' ≠ closed.")
    cols = st.columns(2); [C(f"gate_{i}", x) if True else None for i, x in []]
    for i, x in enumerate(GATE):
        with cols[i % 2]: C(f"gate_{i}", x)
    done = sum(bool(g(f"gate_{i}")) for i in range(len(GATE))); st.progress(done / len(GATE), f"{done}/{len(GATE)} gates passed")
    _ = (st.success if done == len(GATE) else st.warning)("CLOSED – all gates passed" if done == len(GATE) else "NOT CLOSED")
    A("verif", "Verification evidence (retest values, photos, drawing revision, sign-off)", 80)
    st.subheader("Block 17 – Lessons learned (all GVMC parks)"); A("lessons", "What must change organisation-wide?", 100)

# ── report + dashboard (computed after all inputs are read) ──
with tabs[11]:
    md = report(F, circ); st.download_button("⬇️ Download report (.md)", md, f"{g('inv_id', 'report') or 'report'}.md")
    st.download_button("⬇️ Download data (.json)", json.dumps(record(), indent=2), f"{g('inv_id', 'investigation') or 'investigation'}.json"); st.markdown(md)

with tabs[0]:
    cnt = {c: sum(f["Class"] == c for f in F) for c in "ABCDE"}
    light = "🔴 CRITICAL" if cnt["A"] else "🟠 MAJOR" if cnt["B"] else "🟡 ATTENTION" if cnt["C"] else "🟢 NO CRITICAL FINDINGS"
    st.header(f"{g('park', '') or 'Park'} – {light}"); st.caption(f"ID: {g('inv_id', '—')} · Fault: {g('dt', '—')} · Circuit: {g('circ', '—')}")
    m = st.columns(6); [m[i].metric(f"Class {c}", cnt[c]) for i, c in enumerate("ABCDE")]
    done = sum(bool(g(f"gate_{i}")) for i in range(len(GATE))); m[5].metric("Closure gate", f"{done}/{len(GATE)}")
    a, b = st.columns(2)
    with a:
        st.subheader("Top findings")
        if F: st.dataframe(pd.DataFrame(F)[["Class", "Area", "Finding"]].head(8), hide_index=True)
        else: st.info("No findings yet – fill the test matrix, as-built table and environment checks.")
    with b:
        st.subheader("Circuit status")
        if len(circ): st.dataframe(circ, hide_index=True)
        else: st.info("No circuit tests entered.")
        mr = st.session_state.get("_maxrisk"); st.metric("Highest risk score", "—" if mr is None or pd.isna(mr) else f"{mr:.0f} / 25")
    st.subheader("Draft root cause"); st.write(draft_rca(F))
    st.warning("Decision aid only: results use indicative criteria (editable in sidebar). Final conclusions must be verified against CEA regulations, IS 732/IS 3043, project specification and by a competent person.")
