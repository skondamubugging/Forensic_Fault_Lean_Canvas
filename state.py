"""Session-state hardening: schema migration, dirty detection, refresh-recovery helpers, unload guard."""
from __future__ import annotations
import hashlib, json
SCHEMA = 2

def _m1_to_2(r: dict) -> dict:           # v1 (app.py) → v2: add revision/profile slots
    r = {**r, "schema": 2}; r.setdefault("profile", "230/400 V 50 Hz (IN/EU/UK/AU)"); r.setdefault("limits", {}); return r
MIGRATIONS = {1: _m1_to_2}

def migrate(rec: dict) -> dict:
    v = rec.get("schema", 1)
    while v < SCHEMA: rec = MIGRATIONS[v](rec); v = rec["schema"]
    return rec

def fingerprint(rec: dict) -> str:
    """Stable hash excluding volatile keys → compare with last-saved fingerprint to know if unsaved edits exist."""
    r = {k: v for k, v in rec.items() if k not in ("saved_at", "revision")}
    return hashlib.sha256(json.dumps(r, sort_keys=True, default=str).encode()).hexdigest()

def unload_guard(dirty: bool):
    """Best-effort 'Leave site?' prompt (runs in a same-origin component iframe). Streamlit cannot intercept refresh server-side."""
    import streamlit.components.v1 as c
    c.html(f"<script>window.parent.onbeforeunload = {'function(e){e.preventDefault();e.returnValue=\"\";}' if dirty else 'null'};</script>", height=0)
