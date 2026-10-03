"""Forensic Electrical Fault Investigation Lean Canvas - Streamlit app (engine-backed).
All engineering maths and limits live in ./engine (profile-driven, unit-tested).
Storage: GitHub with optimistic locking + atomic commits (local ./data fallback)."""
import streamlit as st, pandas as pd, numpy as np, json, math, os, re
from dataclasses import replace
from datetime import datetime, timedelta
from engine import PRESETS, Limits, calcs, assess, energy, prevention
from engine.profiles import EARTHING
from engine.store import GitHubStore, LocalStore, ConflictError, prepare_evidence
from engine.state import SCHEMA, migrate, fingerprint, unload_guard

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
LOADS = pd.DataFrame([["Park lighting (LED)", 0.15, 40.0, 11.0, 365.0, 1.0, 0.9], ["Fountain pump", 5.5, 1.0, 6.0, 300.0, 0.8, 0.82]],
                     columns=["Load", "kW per unit", "Qty", "Hours/day", "Days/yr", "Load factor", "PF"])
SEEDS = {"loads": LOADS, "ev": EV, "tests": TESTS, "sec": SEC, "earth": EARTH, "poles": POLES, "prot": PROT, "docs": DOCT, "risk": RISK, "act": ACT, "evid": EVID}

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
def S(k, label, opts):
    if st.session_state.get("f_" + k) not in opts: st.session_state["f_" + k] = opts[0]     # stale/loaded value no longer valid
    return st.selectbox(label, opts, key="f_" + k)
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
        st.session_state.update(seed={k: v.copy() for k, v in SEEDS.items()}, out={}, ver=0, photos=[], _set_fp=True)
        iid = st.query_params.get("inv")
        if iid:                                            # refresh recovery: reopen the record named in the URL
            try:
                rec, sha = get_store().read(iid); st.session_state["_pending"] = migrate(rec); st.session_state["_pending_rev"] = (iid, sha)
            except Exception:
                pass
    if "_pending" in st.session_state:
        rec = st.session_state.pop("_pending")
        for k in [k for k in st.session_state if k.startswith("f_")]: del st.session_state[k]
        st.session_state.update({k: v for k, v in rec.get("fields", {}).items() if k != "f_photos"})
        st.session_state.seed = {n: coerce(n, rec["tables"][n]) if rec.get("tables", {}).get(n) else SEEDS[n].copy() for n in SEEDS}
        st.session_state.out, st.session_state.photos = {}, rec.get("photos", [])
        st.session_state.ver += 1; st.session_state["_set_fp"] = True
        iid, sha = st.session_state.pop("_pending_rev", (None, None)); st.session_state.update(rev=sha, rev_id=iid)

def tbl(n): return st.session_state.out.get(n, st.session_state.seed[n])

def edit(n, cfg=None):
    st.session_state.out[n] = st.data_editor(st.session_state.seed[n], key=f"ed_{n}_{st.session_state.ver}", num_rows="dynamic", column_config=cfg or {})
    return st.session_state.out[n]

def record():
    f = {k: v for k, v in st.session_state.items() if k.startswith("f_") and k != "f_photos" and isinstance(v, (str, int, float, bool, list))}
    return {"schema": SCHEMA, "saved_at": datetime.now().isoformat(timespec="seconds"), "fields": f, "photos": st.session_state.photos,
            "tables": {n: json.loads(tbl(n).to_json(orient="records")) for n in SEEDS}}

# ───────────────────────── storage (conflict-safe) ─────────────────────────
@st.cache_resource
def _gh(token, repo, branch, folder): return GitHubStore(token, repo, branch, folder)

def get_store():
    try: s = st.secrets["github"]; return _gh(s["token"], s["repo"], s.get("branch", "main"), s.get("folder", "investigations"))
    except Exception: return LocalStore(DATA_DIR)

def records(force=False):
    if force or "_recs" not in st.session_state:
        try: st.session_state["_recs"] = get_store().list()
        except Exception as e: st.sidebar.warning(f"Could not list records: {e}"); st.session_state["_recs"] = []
    return st.session_state["_recs"]

def save():
    iid = re.sub(r"[^A-Za-z0-9_-]", "_", g("inv_id", "").strip())
    if not iid: return st.sidebar.error("Enter an Investigation ID first.")
    ev = {}
    for up in g("photos") or []:
        try: n, b = prepare_evidence(up.name, up.getvalue()); ev[n] = b
        except Exception as e: st.sidebar.warning(str(e))
    rec = record(); rec["photos"] = sorted(set(st.session_state.photos) | set(ev))
    expected = st.session_state.get("rev") if st.session_state.get("rev_id") == iid else None
    try: sha = get_store().save(iid, rec, expected, ev)
    except ConflictError: return st.sidebar.error("⚠️ NOT saved: this ID already exists or was changed by someone else since you loaded it. Load the latest version, or save under a new ID.")
    except Exception as e: return st.sidebar.error(f"Save failed: {e}")
    st.session_state.update(photos=rec["photos"], rev=sha, rev_id=iid); st.session_state.saved_fp = fingerprint(record())
    st.query_params["inv"] = iid; records(True); st.sidebar.success(f"Saved '{iid}'" + (f" + {len(ev)} evidence file(s)" if ev else ""))

def load(iid):
    try: rec, sha = get_store().read(iid)
    except FileNotFoundError: return st.sidebar.error("Record not found.")
    except Exception as e: return st.sidebar.error(f"Load failed: {e}")
    st.session_state["_pending"] = migrate(rec); st.session_state["_pending_rev"] = (iid, sha); st.query_params["inv"] = iid; st.rerun()

def reset():
    for k in list(st.session_state): del st.session_state[k]
    st.query_params.clear(); st.rerun()

def profile():
    p = PRESETS[g("profile", list(PRESETS)[0])]; e = g("sys")
    return replace(p, earthing=e) if e in EARTHING else p

def limits():
    return Limits(ir_min_mohm=g("irmin", 1.0), ir_warn_mohm=g("irwarn", 10.0), pe_cont_max_ohm=g("cmax", 1.0), zs_factor=g("zf", 0.8),
                  vd_max_pct=g("vd", 5.0), rcd_max_ma=g("rcdma", 30.0), touch_v_max=g("touchv", 50.0))

def safe(fn, *a, **k):
    try: return fn(*a, **k)
    except (ValueError, KeyError, ZeroDivisionError) as e: st.warning(f"Check inputs: {e}"); return None

def flag(ok, good, bad): (st.success if ok else st.error)(good if ok else bad)

# ───────────────────────── analysis engine ─────────────────────────
def analyze():
    prof, LIM = profile(), limits(); cmax = LIM.pe_cont_max_ohm
    F, rows = [], []
    def add(cl, area, find, ev, rec, when): F.append(dict(Class=cl, Area=area, Finding=find, Evidence=ev, Recommendation=rec, When=when))
    circuits = assess.circuits_from_df(tbl("tests"))
    try: eng = assess.assess_circuits(circuits, prof, LIM)
    except ValueError as e: eng = []; st.sidebar.error(f"Test matrix has an invalid value: {e}")
    for f in eng: add(f.cls, f.area, f.text, f.evidence, f.action, f.when)
    for c in circuits:
        if not c.has_data(): continue
        mine = [f for f in eng if f.text.startswith(c.name + ":")]; worst = min((f.cls for f in mine), default=None)
        rows.append({"Circuit": c.name, "Auto status": "🔴 FAIL" if worst in ("A", "B") else "🟡 WARN" if worst else "🟢 PASS", "Notes": "; ".join(f.text.split(": ", 1)[-1] for f in mine)})
    sf = assess.locate_faulty_section([(r["Section"], num(r["IR (MΩ)"])) for _, r in tbl("sec").iterrows()], LIM)
    if sf: add(sf.cls, sf.area, sf.text, sf.evidence, sf.action, sf.when)
    pits = [{"id": r["Pit ID"], "r": r["Resistance (Ω)"], "limit": r["Design limit (Ω)"], "prev": r["Previous (Ω)"]} for _, r in tbl("earth").iterrows()]
    for f in assess.assess_earth_pits(pits, LIM): add(f.cls, f.area, f.text, f.evidence, f.action, f.when)
    for _, r in tbl("earth").iterrows():
        if any(w in str(r["Condition / remarks"]).lower() for w in ("corr", "loose", "broken", "open")): add("C", "Earthing", f"{r['Pit ID']}: connection defect ({r['Condition / remarks']})", "Visual", "Re-make bolted joint, protect against corrosion, re-test continuity.", "Short term")
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
        if b and a / b < LIM.selectivity_ratio: add("C", "Protection", f"Poor selectivity: upstream {a:g} A / downstream {b:g} A < {LIM.selectivity_ratio:g}", "Rating chain (indicative)", "Check manufacturer selectivity tables; adjust ratings/settings.", "Medium term")
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
    st.caption("💾 GitHub storage (conflict-safe)" if isinstance(get_store(), GitHubStore) else "⚠️ No GitHub secrets – local ./data (temporary on cloud hosts)")
    if st.button("💾 Save investigation", type="primary"): save()
    dirty_slot = st.empty()
    pick = st.selectbox("Saved investigations", [""] + records())
    c1, c2, c3 = st.columns(3)
    if c1.button("📂 Load") and pick: load(pick)
    if c2.button("🔄", help="Refresh list"): records(True); st.rerun()
    if c3.button("🆕 New"):
        if st.session_state.get("_dirty"): st.session_state["_confirm_new"] = True
        else: reset()
    if st.session_state.get("_confirm_new"):
        st.warning("Unsaved changes will be lost."); d1, d2 = st.columns(2)
        if d1.button("Discard & new"): reset()
        if d2.button("Cancel"): st.session_state["_confirm_new"] = False; st.rerun()
    S("profile", "System profile (voltage / frequency)", list(PRESETS))
    with st.expander("⚙️ Criteria / thresholds"):
        N("irmin", "Min IR – fail (MΩ) [IEC 60364-6 T6.1: 1.0 for ≤500 V]", 1.0, min_value=0.01); N("irwarn", "IR warning level for NEW outdoor cable (MΩ)", 10.0, min_value=0.01)
        N("cmax", "Max PE continuity (Ω) – project criterion", 1.0, min_value=0.01); N("zf", "Zs factor (0.8 field-test allowance)", 0.8, min_value=0.1, max_value=1.0)
        N("vd", "Max voltage drop (%)", 5.0, min_value=0.1); N("rcdma", "Max RCD IΔn (mA)", 30.0, min_value=1.0); N("touchv", "Touch-voltage limit (V; 25 for fountains/pools)", 50.0, min_value=1.0)
    _p = profile(); st.caption(f"{_p.name} · U0 {_p.u0:g} V · {_p.freq_hz:g} Hz · {_p.earthing}")
    st.file_uploader("📎 Evidence photos/PDFs (compressed & saved with record)", accept_multiple_files=True, key="f_photos")
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
    prof, LIM = profile(), limits()
    st.caption(f"Active system: **{prof.name}** · U0 {prof.u0:g} V · U_L {prof.u_line:g} V · {prof.freq_hz:g} Hz · {prof.earthing} (change in sidebar)")
    with st.expander("Voltage drop"):
        a, b, c, d = st.columns(4); I = a.number_input("Current (A)", 0.1, value=10.0); L = b.number_input("Length (m)", 1.0, value=100.0); Ar = c.number_input("Conductor area (mm²)", 0.5, value=6.0); mat = d.selectbox("Material", ["Cu", "Al"])
        pf = a.number_input("Power factor", 0.1, 1.0, 0.9); tc = b.number_input("Conductor temp (°C)", 20.0, 120.0, 70.0); xk = c.number_input("Reactance Ω/km (0 = auto for frequency)", 0.0, value=0.0)
        r = safe(calcs.voltage_drop, prof, I, L, Ar, mat, pf, tc, xk or None, LIM.vd_max_pct)
        if r: st.metric("Voltage drop", f"{r['volts']:.1f} V ({r['pct']:.2f}% of {prof.u_ref:g} V)"); flag(r["ok"], "Within limit", f"Exceeds {LIM.vd_max_pct:g}% – upsize cable / shorten run")
    with st.expander("Cable ⇄ breaker coordination (Ib ≤ In ≤ Iz)"):
        a, b, c, d = st.columns(4); Ib = a.number_input("Design current Ib (A)", 0.0, value=8.0); In = b.number_input("Device rating In (A)", 0.0, value=16.0); sz = c.selectbox("Cable mm² (Cu)", list(AMP), index=2); mth = d.selectbox("Method", ["Buried", "In air"])
        ins = a.selectbox("Insulation", ["XLPE", "PVC (×0.8)"]); kt = b.number_input("Temp factor", 0.1, 1.5, 0.9); kg = c.number_input("Grouping factor", 0.1, 1.0, 0.85)
        r = calcs.ib_in_iz(Ib, In, AMP[sz][0 if mth == "Buried" else 1] * (0.8 if "PVC" in ins else 1), kt, kg)
        st.write(f"Indicative derated capacity Iz ≈ **{r['iz']:.1f} A** (verify against manufacturer datasheet)")
        flag(r["ok"], "Ib ≤ In ≤ Iz satisfied", "NOT satisfied: " + ("load exceeds device rating" if Ib > In else "device rating exceeds cable capacity – cable unprotected from overload"))
    with st.expander("Earth-fault loop, disconnection, touch voltage, conductor size"):
        a, b, c, d = st.columns(4); zs = a.number_input("Measured Zs (Ω)", 0.001, value=1.2); In2 = b.number_input("Device rating In (A)", 1.0, value=16.0); cv = c.selectbox("Curve", ["B", "C", "D", "K", "Z"], index=1); im = d.number_input("Adjustable Im (A), 0 = MCB", 0.0, value=0.0)
        r = safe(calcs.zs_check, prof, zs, In2, cv, LIM, im or None)
        if r:
            Rpe = a.number_input("PE resistance to pole (Ω)", 0.0, value=0.2); t = b.number_input("Disconnection time (s)", 0.01, value=float(r["t_max_s"]), help="Default from IEC 60364-4-41 Table 41.1 for the active profile")
            kk = c.selectbox("Conductor / insulation", [("Cu", "XLPE"), ("Cu", "PVC"), ("Al", "XLPE"), ("Al", "PVC")], format_func=lambda x: f"{x[0]} {x[1]}"); tv = calcs.touch_voltage(r["fault_a"], Rpe)
            m1, m2, m3, m4 = st.columns(4); m1.metric("Fault current", f"{r['fault_a']:.0f} A"); m2.metric("Required Ia", f"{r['ia']:.0f} A"); m3.metric("Max Zs", f"{r['zs_max']:.3f} Ω"); m4.metric("Touch voltage ≈", f"{tv:.0f} V")
            flag(r["ok"], f"Instant disconnection achievable (limit {r['t_max_s']:g} s)", "Zs too high – device may not trip magnetically; add RCBO / improve PE")
            st.write(f"Min earthing conductor (adiabatic): **{calcs.min_conductor_area(r['fault_a'], t, *kk):.2f} mm²**")
            if tv > LIM.touch_v_max: st.warning(f"Touch voltage above {LIM.touch_v_max:g} V")
            if prof.earthing == "TT": st.info("TT system: protection relies on the RCD – use the RCD/TT check below; Zs shown for reference only.")
    with st.expander("RCD / TT-system check"):
        a, b, c = st.columns(3); RA = a.number_input("Earth electrode RA (Ω)", 0.0, value=5.0); Idn = b.number_input("RCD IΔn (mA)", 1.0, value=30.0); r = calcs.tt_check(RA, Idn, LIM)
        st.write(f"RA × IΔn = **{r['v']:.1f} V** (limit {LIM.touch_v_max:g} V) – max RA = {r['ra_max']:.0f} Ω"); flag(r["ok"], "OK for TT", "Fail")
        st.caption(f"Acceptance: trip ≤{LIM.rcd_t1_ms:g} ms at IΔn, ≤{LIM.rcd_t5_ms:g} ms at 5×IΔn, trip current 50–100% IΔn. Earth resistance alone does not prove safety.")
    with st.expander("Loose connection heating  (P = I²R)"):
        a, b, c = st.columns(3); I3 = a.number_input("Current (A)", 0.0, value=20.0); Rg = b.number_input("Good joint R (Ω)", 0.0, value=0.001, format="%.4f"); Rb = c.number_input("Loose joint R (Ω)", 0.0, value=0.1, format="%.4f")
        pg, pb = prevention.loose_joint_power_w(I3, Rg, Rb); st.write(f"Good: **{pg:.2f} W** · Loose: **{pb:.1f} W** (×{(Rb / Rg if Rg else 0):.0f}) → local hot-spot, carbonisation risk")
    with st.expander("Thermography normalisation"):
        a, b, c = st.columns(3); dT = a.number_input("Measured ΔT (K)", 0.0, value=6.0); Im_ = b.number_input("Load during survey (A)", 0.1, value=8.0); Ir_ = c.number_input("Rated/design current (A)", 0.1, value=16.0)
        r = safe(prevention.thermal_assess, dT, Im_, Ir_, LIM); st.write(f"ΔT at rated load ≈ **{r['dt_at_rated_k']:.1f} K** → **{r['level']}**" + ("  ⚠️ survey load <40% – indicative only" if r["low_load_warning"] else "") if r else "")
    with st.expander("Phase imbalance & neutral current (incl. LED/SMPS triplens)"):
        a, b, c, d = st.columns(4); R_ = a.number_input("R (A)", 0.0, value=8.0); Y_ = b.number_input("Y (A)", 0.0, value=8.5); B_ = c.number_input("B (A)", 0.0, value=22.0); pfn = d.number_input("PF (all phases)", 0.1, 1.0, 0.95)
        h3 = a.number_input("3rd-harmonic content (% of phase current; LED/SMPS ≈15–40)", 0.0, 100.0, 0.0)
        if prof.phases == 3:
            n = calcs.neutral_current((R_, Y_, B_), (pfn,) * 3, h3); ub = calcs.unbalance_pct((R_, Y_, B_))
            m1, m2, m3 = st.columns(3); m1.metric("Imbalance", f"{ub:.1f}%"); m2.metric("Neutral (fundamental)", f"{n['fundamental_a']:.1f} A"); m3.metric("Neutral total", f"{n['total_a']:.1f} A")
            flag(ub <= LIM.unbalance_warn_pct, "Imbalance acceptable", "Rebalance loads / check neutral & faulty equipment")
            if n["exceeds_phase"]: st.error("Neutral current exceeds the largest phase current – check neutral conductor size and terminations.")
            if n["note"]: st.info(n["note"])
        else: st.info("Select a three-phase system profile.")
    with st.expander("Motor / pump full-load current"):
        a, b, c = st.columns(3); kw = a.number_input("kW", 0.1, value=5.5); pf2 = b.number_input("PF", 0.1, 1.0, 0.82); ef = c.number_input("Efficiency", 0.1, 1.0, 0.88)
        st.write(f"FLA ≈ **{calcs.full_load_current(prof, kw, pf2, ef):.1f} A** at {prof.u_ref:g} V – compare with nameplate/clamp reading; set overload ≈ 1.0–1.05×FLA; DOL device per motor-duty curve.")
    with st.expander("IR trend / degradation"):
        a, b = st.columns(2); b0 = a.number_input("Commissioning IR (MΩ)", 0.001, value=300.0); b1 = b.number_input("Now (MΩ)", 0.001, value=1.0)
        st.metric("Change", f"{100 * (b1 - b0) / b0:.1f}%"); flag(b1 >= b0 / 10, "Within expectation", "Severe degradation – investigate")
    with st.expander("Predictive IR forecast (≥3 dated readings)"):
        A("irtrend", "Readings, one per line: YYYY-MM-DD, value (MΩ)", 100, placeholder="2025-06-01, 400\n2025-12-01, 150\n2026-06-01, 40")
        pts = []
        for ln in g("irtrend", "").splitlines():
            try: d_, v_ = [x.strip() for x in ln.split(",")]; pts.append((datetime.strptime(d_, "%Y-%m-%d").date(), float(v_)))
            except ValueError:
                if ln.strip(): st.warning(f"Skipped line: {ln}")
        if len(pts) >= 2:
            f = prevention.trend_forecast(pts, LIM.ir_min_mohm); st.write(f"**{f['status']}** · {f['n']} readings · confidence: {f.get('confidence', '—')}")
            if f.get("rate_pct_per_year") is not None: st.write(f"Trend: {f['rate_pct_per_year']:+.0f}% per year")
            if f["days_to_threshold"] is not None: st.warning(f"Projected to fall below {LIM.ir_min_mohm:g} MΩ in ~{f['days_to_threshold']:.0f} days (≈ {max(pts)[0] + timedelta(days=f['days_to_threshold']):%d-%b-%Y})")
    with st.expander("Earth electrode estimate & fall-of-potential layout"):
        a, b, c, d = st.columns(4); rho_s = a.number_input("Soil resistivity (Ω·m)", 1.0, value=100.0); Lr = b.number_input("Rod length (m)", 0.5, value=3.0); dr = c.number_input("Rod dia (m)", 0.005, value=0.0125, format="%.4f"); Dc = d.number_input("Current probe C distance (m)", 5.0, value=30.0)
        st.write(f"Single-rod R ≈ **{calcs.rod_resistance(rho_s, Lr, dr):.1f} Ω** · place potential probe P at **{calcs.fall_of_potential_probe_m(Dc):.1f} m** (61.8%) from the electrode; take several readings.")
    with st.expander("Energy audit & optimisation"):
        ld = edit("loads"); a, b, c = st.columns(3); tar = a.number_input("Tariff (per kWh)", 0.0, value=8.0); dem = b.number_input("Demand charge (per kW-month)", 0.0, value=0.0); tpf = c.number_input("Target PF", 0.8, 1.0, 0.95)
        loads = []
        for _, r in ld.iterrows():
            v = [num(r[k]) for k in ["kW per unit", "Qty", "Hours/day", "Days/yr", "Load factor", "PF"]]
            if r["Load"] and all(x is not None for x in v) and v[0] > 0 and 0 < v[5] <= 1: loads.append(energy.Load(r["Load"], v[0], int(v[1]), v[2], int(v[3]), v[4], v[5]))
        if loads:
            au = energy.energy_audit(loads, prof, tar, dem, 1.0, tpf); m1, m2, m3, m4 = st.columns(4)
            m1.metric("Energy / yr", f"{au['kwh_year']:,.0f} kWh"); m2.metric("Energy cost / yr", f"{au['energy_cost']:,.0f}"); m3.metric("Avg PF", f"{au['avg_pf']:.2f}"); m4.metric(f"kVAr to reach {tpf:g}", f"{au['kvar_to_target']:.1f}")
            st.dataframe(pd.DataFrame(au["ranking"]), hide_index=True); nm_ = [l.name for l in loads]; sel = st.selectbox("Retrofit candidate", nm_); ls = loads[nm_.index(sel)]
            x, y = st.columns(2); nk = x.number_input("Replacement kW per unit", 0.0, value=round(ls.kw * 0.4, 3), format="%.3f"); cu = y.number_input("Capex per unit", 0.0, value=2500.0)
            e = energy.evaluate(energy.led_retrofit(ls, nk, cu), tar); pbk = e["simple_payback_y"]
            st.write(f"Saving **{e['annual_saving']:,.0f}/yr** · payback **{'n/a' if pbk == float('inf') else f'{pbk:.1f} y'}** · NPV **{e['npv']:,.0f}**" + (f" · IRR {e['irr'] * 100:.0f}%" if e["irr"] is not None else ""))
        else: st.info("Enter valid load rows (kW, quantity, hours, days, load factor, PF) to run the audit.")


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
    cols = st.columns(2)
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
    m = st.columns(6)
    for i, c in enumerate("ABCDE"): m[i].metric(f"Class {c}", cnt[c])
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

# ── unsaved-changes tracking (after all inputs are read) ──
_fp = fingerprint(record())
if st.session_state.pop("_set_fp", False): st.session_state["saved_fp"] = _fp
_dirty = _fp != st.session_state.get("saved_fp"); st.session_state["_dirty"] = _dirty
dirty_slot.caption("🟠 Unsaved changes – Load/New will discard them" if _dirty else "🟢 All changes saved")
unload_guard(_dirty)
