# Forensic Electrical Fault Lean Canvas (Streamlit)

Run locally: `pip install -r requirements.txt && streamlit run app.py`

## GitHub storage (persistent)
1. Create a private repo (e.g. `gvmc-fault-data`) with a `main` branch.
2. Create a fine-grained Personal Access Token: *Repository permissions → Contents: Read & write* for that repo.
3. Streamlit Community Cloud → App → Settings → Secrets (or `.streamlit/secrets.toml` locally; never commit it):
```toml
[github]
token  = "github_pat_xxx"
repo   = "your-user/gvmc-fault-data"
branch = "main"
folder = "investigations"
```
Each investigation is saved as `investigations/<ID>.json`; uploaded evidence goes to `investigations/<ID>/evidence/`.
Without secrets the app falls back to `./data` (temporary on cloud hosts).
