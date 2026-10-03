"""Concurrency-safe storage. Optimistic locking on the *blob SHA captured at load time* + atomic multi-file commits
(Git Data API, non-force ref update). Images are resized/re-encoded/EXIF-stripped and content-addressed before commit."""
from __future__ import annotations
import base64, hashlib, io, json, os, re, tempfile, time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

class ConflictError(RuntimeError): """Someone else changed the record since you loaded it."""

def git_blob_sha(data: bytes) -> str: return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()

# ── evidence handling ──
def compress_image(data: bytes, max_px=1600, quality=78, max_bytes=350_000) -> bytes:
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))          # honour rotation, then drop all EXIF/GPS on save
    im = im.convert("RGB") if im.mode not in ("RGB", "L") else im
    im.thumbnail((max_px, max_px)); q = quality
    while True:
        buf = io.BytesIO(); im.save(buf, "JPEG", quality=q, optimize=True, progressive=True)
        if buf.tell() <= max_bytes or q <= 40: return buf.getvalue()
        q -= 8
        if q < 60 and max(im.size) > 900: im = im.resize((int(im.width * .85), int(im.height * .85)))

def prepare_evidence(name: str, data: bytes, pdf_max=3_000_000) -> tuple[str, bytes]:
    """Return (content-addressed name, bytes). Identical files collapse to one object; name collisions can't overwrite."""
    stem, ext = os.path.splitext(re.sub(r"[^A-Za-z0-9_.-]", "_", name)); ext = ext.lower()
    if ext in (".jpg", ".jpeg", ".png", ".webp", ".heic", ".bmp"):
        data, ext = compress_image(data), ".jpg"
    elif ext == ".pdf":
        if len(data) > pdf_max: raise ValueError(f"{name}: PDF > {pdf_max // 1_000_000} MB – store externally (Git LFS / release asset / object storage).")
    else: raise ValueError(f"{name}: unsupported type {ext}")
    return f"{stem[:30]}-{hashlib.sha1(data).hexdigest()[:10]}{ext}", data

# ── GitHub ──
class GitHubStore:
    API = "https://api.github.com"
    def __init__(self, token: str, repo: str, branch="main", root="investigations"):
        self.repo, self.branch, self.root = repo, branch, root.strip("/")
        self.s = requests.Session(); self.s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
        self.s.mount("https://", HTTPAdapter(max_retries=Retry(total=4, backoff_factor=1.0, status_forcelist=(500, 502, 503, 504), allowed_methods=None)))
    def _r(self, method, path, **kw):
        for _ in range(3):
            r = self.s.request(method, f"{self.API}/repos/{self.repo}{path}", timeout=30, **kw)
            if r.status_code in (403, 429) and (r.headers.get("retry-after") or r.headers.get("x-ratelimit-remaining") == "0"):
                time.sleep(min(60, float(r.headers.get("retry-after", 5)))); continue          # secondary rate limit / throttle
            return r
        return r
    def _sha_at(self, ref: str, path: str):
        r = self._r("GET", f"/contents/{path}", params={"ref": ref})
        if r.status_code == 404: return None
        r.raise_for_status(); return r.json()["sha"]
    def path(self, rec_id: str) -> str: return f"{self.root}/{rec_id}.json"
    def read(self, rec_id: str) -> tuple[dict, str]:
        p = self.path(rec_id); r = self._r("GET", f"/contents/{p}", params={"ref": self.branch})
        if r.status_code == 404: raise FileNotFoundError(rec_id)
        r.raise_for_status(); j = r.json()
        raw = base64.b64decode(j["content"]) if j.get("content") else self._r("GET", f"/contents/{p}", params={"ref": self.branch}, headers={"Accept": "application/vnd.github.raw+json"}).content
        return json.loads(raw), j["sha"]                                 # keep this sha → pass back as expected_sha on save
    def list(self) -> list[str]:
        r = self._r("GET", f"/git/trees/{self.branch}:{self.root}")      # trees API: no 1000-entry cap like /contents
        return [] if r.status_code == 404 else sorted(e["path"][:-5] for e in r.json()["tree"] if e["type"] == "blob" and e["path"].endswith(".json"))
    def repo_size_mb(self) -> float: return self._r("GET", "").json().get("size", 0) / 1024
    def save(self, rec_id: str, record: dict, expected_sha: str | None, evidence: dict[str, bytes] | None = None, message: str | None = None) -> str:
        """Atomic: JSON + evidence in ONE commit. expected_sha=None means 'must not exist yet'. Raises ConflictError."""
        data = json.dumps(record, indent=1, sort_keys=True).encode()
        files = {self.path(rec_id): data, **{f"{self.root}/{rec_id}/evidence/{n}": b for n, b in (evidence or {}).items()}}
        self._commit(files, message or f"investigation {rec_id}", {self.path(rec_id): expected_sha})
        return git_blob_sha(data)
    def _commit(self, files: dict[str, bytes], message: str, expected: dict[str, str | None]):
        for _ in range(5):
            head = self._r("GET", f"/git/ref/heads/{self.branch}").json()["object"]["sha"]
            for p, exp in expected.items():
                if self._sha_at(head, p) != exp: raise ConflictError(f"{p} was modified by someone else – reload or 'save as copy'.")
            base = self._r("GET", f"/git/commits/{head}").json()["tree"]["sha"]; tree = []
            for p, b in files.items():
                if self._sha_at(head, p) == git_blob_sha(b): continue                # unchanged → no commit noise
                blob = self._r("POST", "/git/blobs", json={"content": base64.b64encode(b).decode(), "encoding": "base64"}); blob.raise_for_status()
                tree.append({"path": p, "mode": "100644", "type": "blob", "sha": blob.json()["sha"]})
            if not tree: return
            t = self._r("POST", "/git/trees", json={"base_tree": base, "tree": tree}); t.raise_for_status()
            c = self._r("POST", "/git/commits", json={"message": message, "tree": t.json()["sha"], "parents": [head]}); c.raise_for_status()
            u = self._r("PATCH", f"/git/refs/heads/{self.branch}", json={"sha": c.json()["sha"], "force": False})
            if u.status_code == 200: return
            if u.status_code != 422: u.raise_for_status()                          # 422 = branch moved meanwhile → re-validate & retry
        raise ConflictError("Branch kept moving; try again.")

class LocalStore:
    """Same interface, atomic writes (tempfile + os.replace) and sha check. Dev/offline use only."""
    def __init__(self, root="data"): self.root = root; os.makedirs(root, exist_ok=True)
    def _p(self, i): return os.path.join(self.root, f"{i}.json")
    def read(self, rec_id):
        try: raw = open(self._p(rec_id), "rb").read()
        except FileNotFoundError: raise FileNotFoundError(rec_id)
        return json.loads(raw), git_blob_sha(raw)
    def list(self): return sorted(f[:-5] for f in os.listdir(self.root) if f.endswith(".json"))
    def save(self, rec_id, record, expected_sha, evidence=None, message=None):
        cur = git_blob_sha(open(self._p(rec_id), "rb").read()) if os.path.exists(self._p(rec_id)) else None
        if cur != expected_sha: raise ConflictError(f"{rec_id} changed since load")
        data = json.dumps(record, indent=1, sort_keys=True).encode()
        for n, b in (evidence or {}).items():
            os.makedirs(os.path.join(self.root, rec_id), exist_ok=True); open(os.path.join(self.root, rec_id, n), "wb").write(b)
        fd, tmp = tempfile.mkstemp(dir=self.root); os.write(fd, data); os.close(fd); os.replace(tmp, self._p(rec_id)); return git_blob_sha(data)
