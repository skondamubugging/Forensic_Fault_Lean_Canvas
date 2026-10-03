# Plugging `engine/` into app.py

```python
from engine import PRESETS, Limits, calcs, assess, energy, prevention
from engine.store import GitHubStore, LocalStore, ConflictError, prepare_evidence
from engine import state as S

# sidebar: one profile drives every tab
prof = PRESETS[st.sidebar.selectbox("System", list(PRESETS), key="f_profile")]
lim = Limits(ir_min_mohm=g("irmin", 1.0), ir_warn_mohm=g("irwarn", 10.0), pe_cont_max_ohm=g("cmax", 1.0), zs_factor=g("zf", 0.8))

# Tests tab: replace analyze()'s circuit block
F = assess.assess_circuits(assess.circuits_from_df(tbl("tests")), prof, lim)

# Calculators tab (no hardcoded 415 V / 50 Hz)
dv = calcs.voltage_drop(prof, I, L, A, mat, pf, limit_pct=lim.vd_max_pct)
nc = calcs.neutral_current((R, Y, B), pf=(0.95, 0.95, 0.95), h3_pct=25)   # LED park lighting → triplens

# Storage: remember the sha you loaded; pass it back on save
store = GitHubStore(**st.secrets["github"]) if "github" in st.secrets else LocalStore()
rec, st.session_state["rev"] = store.read(pick)                          # on load
store.save(iid, record(), st.session_state.get("rev"), evidence=dict(prepare_evidence(u.name, u.getvalue()) for u in uploads))  # ConflictError → offer reload / save-as-copy

# Refresh recovery + unsaved-changes prompt
st.query_params["inv"] = iid                     # on startup: if "inv" in st.query_params and no state → store.read(...)
S.unload_guard(S.fingerprint(record()) != st.session_state.get("saved_fp"))
```
Run tests: `pytest tests -q`
