"""Publish a youtube-artifact page to surge.sh (public hosting).

Same wire protocol shorts-lab uses for ad packs (read from the surge CLI
packages): basic auth "token:<account token>", `PUT /:domain` with a
gzipped tarball whose entries sit under a directory prefix, success only
when the NDJSON stream carries a `type:"info"` event. No node runtime.

The account token lives in $HERMES_HOME/.env as SURGE_TOKEN (shared with
shorts-lab, so connecting once serves both plugins). A password is used for
exactly one login request and never stored.
"""
from __future__ import annotations

import io
import json
import os
import re
import tarfile
import time
from pathlib import Path
from typing import Any, Optional

import requests

try:
    from . import yti_artifact, yti_paths, yti_store
except ImportError:  # standalone import (dashboard plugin_api path)
    import yti_artifact  # type: ignore
    import yti_paths  # type: ignore
    import yti_store  # type: ignore

BASE_URL = os.environ.get("SURGE_BASE_URL", "https://surge.surge.sh")
_TIMEOUT = 60
_CLI_VERSION = "0.41.2"
_ALLOWED_KEYS = {"SURGE_TOKEN", "SURGE_LOGIN"}
PAGES_META_KEY = "surge_pages"
PUBLISHED_FILE = "published.json"
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
_MAX_BYTES = 40 * 1024 * 1024


def _env_file() -> Path:
    return yti_paths.get_hermes_home() / ".env"


def _read_env(name: str) -> str:
    v = (os.environ.get(name) or "").strip()
    if v:
        return v
    try:
        for line in _env_file().read_text().splitlines():
            line = line.strip()
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def token() -> str:
    return _read_env("SURGE_TOKEN")


def is_connected() -> bool:
    return bool(token())


def default_email() -> str:
    for var in ("SURGE_LOGIN", "MENTEE_EMAIL", "HERMES_USER_EMAIL", "EMAIL"):
        v = _read_env(var)
        if "@" in v:
            return v
    return ""


def store_key(env_var: str, value: str) -> None:
    """Persist into $HERMES_HOME/.env (replace, never duplicate) and the
    process env so it applies without a restart; same pattern shorts-lab
    and delivery-kit use."""
    value = (value or "").strip()
    if not value or "\n" in value:
        raise ValueError("key must be a single non-empty line")
    if env_var not in _ALLOWED_KEYS:
        raise ValueError(f"unknown env var: {env_var}")
    path = _env_file()
    lines: list[str] = []
    try:
        lines = [l for l in path.read_text().splitlines() if not l.strip().startswith(env_var + "=")]
    except FileNotFoundError:
        pass
    lines.append(f"{env_var}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".lf-tmp")
    tmp.write_text("\n".join(lines) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    os.environ[env_var] = value


def _auth(tok: str = "") -> tuple:
    return ("token", tok or token())


def login(email: str, password: str) -> dict[str, Any]:
    """Mint an account token (POST /token, basic auth email:password). A new
    email creates the account, like `surge login`."""
    email = (email or "").strip()
    if not email or "@" not in email:
        return {"ok": False, "error": "a valid email is required"}
    if not password:
        return {"ok": False, "error": "a password is required"}
    try:
        r = requests.post(f"{BASE_URL}/token", auth=(email, password),
                          json={"msg": "login from hermes long-form"}, timeout=_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"surge unreachable: {exc}"}
    if r.status_code == 401:
        return {"ok": False, "error": "wrong password for an existing surge account"}
    if r.status_code >= 300:
        return {"ok": False, "error": f"surge login failed (HTTP {r.status_code})"}
    try:
        tok = (r.json() or {}).get("token") or ""
    except ValueError:
        tok = ""
    if not tok:
        return {"ok": False, "error": "surge returned no token"}
    return {"ok": True, "token": tok}


def validate(tok: str) -> dict[str, Any]:
    try:
        r = requests.get(f"{BASE_URL}/list", auth=_auth(tok), timeout=_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"surge unreachable: {exc}"}
    return {"ok": True} if r.status_code == 200 else \
        {"ok": False, "error": f"surge rejected the token (HTTP {r.status_code})"}


def connect(email: str, secret: str) -> dict[str, Any]:
    """Dashboard connect: email + password mints and stores a token; an
    empty email means `secret` is an existing token to validate and store."""
    email = (email or "").strip()
    secret = (secret or "").strip()
    if not secret:
        return {"ok": False, "error": "enter a password or a surge token"}
    if email:
        minted = login(email, secret)
        if not minted.get("ok"):
            return minted
        store_key("SURGE_LOGIN", email)
        store_key("SURGE_TOKEN", minted["token"])
    else:
        check = validate(secret)
        if not check.get("ok"):
            return check
        store_key("SURGE_TOKEN", secret)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------

def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:40] or "page"


def default_domain(root: Path) -> str:
    title = ""
    try:
        title = json.loads((root / "artifact.json").read_text(encoding="utf-8")).get("title") or ""
    except (OSError, ValueError, AttributeError):
        pass
    return f"{_slug(title or root.parent.name)}-{time.strftime('%Y%m%d')}.surge.sh"


def normalize_domain(domain: str, root: Path) -> str:
    d = (domain or "").strip().lower()
    d = re.sub(r"^https?://", "", d).strip("/")
    if not d:
        return default_domain(root)
    if "." not in d:
        d = _slug(d) + ".surge.sh"
    if not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", d):
        raise ValueError("domain may contain letters, digits, dots and dashes only")
    return d


def published_for(root: Path) -> Optional[dict[str, Any]]:
    try:
        d = json.loads((root / PUBLISHED_FILE).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and d.get("url") else None
    except (OSError, ValueError):
        return None


def _bundle(root: Path) -> dict[str, bytes]:
    page = root / "index.html"
    if not page.is_file():
        raise RuntimeError("index.html is missing; rebuild the page first")
    files: dict[str, bytes] = {"index.html": page.read_bytes()}
    img = root / "img"
    if img.is_dir():
        for f in sorted(img.iterdir()):
            if f.is_file() and f.suffix.lower() in _IMAGE_EXTS:
                files[f"img/{f.name}"] = f.read_bytes()
    total = sum(len(v) for v in files.values())
    if total > _MAX_BYTES:
        raise RuntimeError(f"page bundle is {total // (1024 * 1024)} MB; surge pages over 40 MB are refused")
    return files


def _tarball(files: dict[str, bytes], prefix: str) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, payload in files.items():
            info = tarfile.TarInfo(name=f"{prefix}/{name}")
            info.size = len(payload)
            info.mtime = 0
            tar.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


def publish(rel_path: str, domain: str = "") -> dict[str, Any]:
    """Publish the artifact folder that contains rel_path. Returns the
    entry recorded in <folder>/published.json and the surge_pages meta."""
    if not token():
        raise RuntimeError("surge.sh is not connected")
    root = yti_artifact.artifact_root(rel_path)
    if root is None:
        raise RuntimeError(f"not inside an artifact folder: {rel_path}")
    # the gate must pass before anything goes public
    rep = yti_artifact.builder().build(root, check_only=True)
    if not rep.get("ok"):
        raise RuntimeError("the page does not pass its quality gate: " + "; ".join(rep.get("problems") or [])[:400])
    domain = normalize_domain(domain, root)
    files = _bundle(root)
    prefix = re.sub(r"[^a-z0-9-]", "-", domain.split(".")[0]) or "page"
    body = _tarball(files, prefix)
    r = requests.put(
        f"{BASE_URL}/{domain}", data=body, auth=_auth(),
        headers={"Content-Type": "application/gzip", "Accept": "application/x-ndjson",
                 "version": _CLI_VERSION, "file-count": str(len(files)),
                 "project-size": str(len(body)), "timestamp": str(int(time.time()))},
        timeout=300)
    if r.status_code >= 300:
        raise RuntimeError(f"surge publish failed (HTTP {r.status_code}): {(r.text or '')[:200]}")
    ok, err = False, ""
    for line in (r.text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except ValueError:
            continue
        if evt.get("type") == "info":
            ok = True
        if evt.get("type") in ("error", "fail"):
            err = str(evt.get("message") or evt)[:200]
    if not ok:
        raise RuntimeError("surge publish did not confirm: " + (err or "no info event in response"))
    ws = yti_paths.workspace_dir()
    entry = {"domain": domain, "url": f"https://{domain}", "at": time.time(),
             "files": len(files), "dir": root.relative_to(ws).as_posix()}
    (root / PUBLISHED_FILE).write_text(json.dumps(entry, indent=2) + "\n", encoding="utf-8")
    conn = yti_store.connect()
    try:
        raw = yti_store.get_meta(conn, PAGES_META_KEY)
        try:
            pages = json.loads(raw) if raw else []
        except ValueError:
            pages = []
        pages = [p for p in pages if isinstance(p, dict) and p.get("domain") != domain]
        pages.insert(0, entry)
        yti_store.set_meta(conn, PAGES_META_KEY, json.dumps(pages[:50]))
    finally:
        conn.close()
    return entry


def recent_pages() -> list[dict[str, Any]]:
    conn = yti_store.connect()
    try:
        raw = yti_store.get_meta(conn, PAGES_META_KEY)
    finally:
        conn.close()
    try:
        pages = json.loads(raw) if raw else []
    except ValueError:
        return []
    return [p for p in pages if isinstance(p, dict)][:20]


def status() -> dict[str, Any]:
    return {"connected": is_connected(), "email": default_email(), "pages": recent_pages()}
