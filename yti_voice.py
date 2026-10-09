"""Voice projection: make generated scripts sound like the OPERATOR.

Provenance is strict, by design. The voice corpus is built ONLY from the
operator's own recorded speech — their utterances in meeting-transcript
sessions (delivery-kit ingests these into honcho.dev as ``meet-*`` sessions
with one peer per speaker). Explicitly excluded: other speakers' peers, AI
messages, prior generated scripts, YouTube competitor transcripts, and the
insight base — none of those are the operator's voice, and injecting them
would corrupt the projection.

When honcho isn't configured (or holds no transcripts yet) the feature
degrades to nothing: briefs keep today's behavior. Either way, briefs always
direct the writer to consult SOUL.md and memories/USER.md for tone.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional
import logging

logger = logging.getLogger("long-form.voice")

try:
    from . import yti_paths, yti_store
except ImportError:  # standalone import (tests, workers)
    import yti_paths  # type: ignore
    import yti_store  # type: ignore

VOICE_STATE_KEY = "voice_profile_state"
PROFILE_NAME = "voice-profile.md"
MEETING_SESSION_PREFIX = "meet-"
MIN_WORDS = 8            # short interjections carry no voice signal
MAX_PROFILE_CHARS = 15000
REFRESH_SECONDS = 6 * 3600


def _hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))


def honcho_config() -> Optional[dict[str, Any]]:
    """Mirror the native connector's resolution: $HERMES_HOME/honcho.json,
    then ~/.honcho/config.json. Returns {baseUrl, apiKey, workspace, peer}
    or None when the memory system isn't wired."""
    for p in (_hermes_home() / "honcho.json",
              Path.home() / ".honcho" / "config.json"):
        try:
            cfg = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        block = (cfg.get("hosts") or {}).get("hermes") or {}
        base = (cfg.get("baseUrl") or block.get("baseUrl") or "").strip()
        key = (block.get("apiKey") or "").strip()
        if base and key:
            return {
                "baseUrl": base.rstrip("/"),
                "apiKey": key,
                "workspace": (block.get("workspace") or "hermes").strip(),
                "peer": (block.get("peerName") or "user").strip(),
            }
    return None


def _post(cfg: dict, path: str, body: dict) -> Any:
    req = urllib.request.Request(cfg["baseUrl"] + path, method="POST")
    req.add_header("Authorization", "Bearer " + cfg["apiKey"])
    req.add_header("Content-Type", "application/json")
    return json.loads(urllib.request.urlopen(
        req, json.dumps(body).encode(), timeout=20).read())


def _list_meeting_sessions(cfg: dict) -> list[str]:
    ws = cfg["workspace"]
    ids: list[str] = []
    page = 1
    while page <= 20:
        data = _post(cfg, f"/v3/workspaces/{ws}/sessions/list?page={page}"
                          "&size=100", {})
        items = data.get("items") or []
        for s in items:
            sid = s.get("id") or s.get("name") or ""
            if sid.startswith(MEETING_SESSION_PREFIX):
                ids.append(sid)
        if len(items) < 100:
            break
        page += 1
    return ids


def _user_lines_in_session(cfg: dict, session_id: str) -> list[str]:
    ws, peer = cfg["workspace"], cfg["peer"]
    lines: list[str] = []
    page = 1
    while page <= 30:
        data = _post(cfg, f"/v3/workspaces/{ws}/sessions/{session_id}"
                          f"/messages/list?page={page}&size=100", {})
        items = data.get("items") or []
        for m in items:
            if (m.get("peer_id") or m.get("peer_name")) != peer:
                continue
            text = (m.get("content") or "").strip()
            if len(text.split()) >= MIN_WORDS:
                lines.append(text)
        if len(items) < 100:
            break
        page += 1
    return lines


def profile_path() -> Path:
    return yti_paths.data_dir() / PROFILE_NAME


def _load_state(conn) -> dict[str, Any]:
    raw = yti_store.get_meta(conn, VOICE_STATE_KEY)
    try:
        st = json.loads(raw) if raw else {}
        return st if isinstance(st, dict) else {}
    except ValueError:
        return {}


def _write_profile(samples: list[str]) -> None:
    total = 0
    kept: list[str] = []
    for s in reversed(samples):          # newest meetings win the budget
        if total + len(s) > MAX_PROFILE_CHARS:
            continue
        kept.append(s)
        total += len(s)
    kept.reverse()
    body = "\n\n".join(f"> {s}" for s in kept)
    profile_path().parent.mkdir(parents=True, exist_ok=True)
    profile_path().write_text(
        "# Voice Profile — the operator's OWN recorded words\n\n"
        "Provenance: verbatim utterances by the operator (and no one else)\n"
        "from recorded meeting transcripts. This is the ONLY sanctioned\n"
        "voice source — never imitate other speakers, prior scripts, or\n"
        "competitor transcripts.\n\n"
        f"{body}\n")


def refresh_voice_profile(budget_seconds: float = 8.0) -> dict[str, Any]:
    """Incrementally pull the operator's own meeting utterances into the
    profile file. Budgeted and resumable; NEVER raises."""
    cfg = honcho_config()
    if cfg is None:
        return {"available": False, "reason": "honcho not configured"}
    conn = yti_store.connect()
    try:
        st = _load_state(conn)
        now = time.time()
        if st.get("updatedAt") and now - st["updatedAt"] < REFRESH_SECONDS \
                and profile_path().exists():
            logger.debug("voice profile fresh (cache <6h)")
            return {"available": True, "fresh": True}
        done = set(st.get("done") or [])
        samples = list(st.get("samples") or [])
        deadline = now + budget_seconds
        try:
            sessions = _list_meeting_sessions(cfg)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return {"available": profile_path().exists(),
                    "error": str(exc)[:120]}
        scanned = 0
        for sid in sessions:
            if sid in done:
                continue
            if time.time() > deadline:
                break
            try:
                samples.extend(_user_lines_in_session(cfg, sid))
                done.add(sid)
                scanned += 1
            except (urllib.error.URLError, OSError, ValueError):
                break
        if scanned or not profile_path().exists():
            _write_profile(samples)
        logger.info(
            "voice profile: scanned %d new session(s) (%d/%d done), "
            "%d samples, %.1fs", scanned, len(done), len(sessions),
            len(samples), time.time() - now)
        st = {"done": sorted(done), "samples": samples[-400:],
              "updatedAt": now}
        yti_store.set_meta(conn, VOICE_STATE_KEY, json.dumps(st))
        return {"available": profile_path().exists(), "scanned": scanned,
                "sessions": len(sessions), "samples": len(samples)}
    except Exception as exc:  # never break task creation over voice
        return {"available": profile_path().exists(), "error": str(exc)[:120]}
    finally:
        conn.close()


def voice_brief_lines() -> list[str]:
    """Brief block for every script-writing task. Always includes the
    SOUL.md/USER.md tone consult; adds the voice profile when it exists."""
    home = _hermes_home()
    lines = [
        "### VOICE (how the words must sound)",
        f"- Read {home}/SOUL.md and {home}/memories/USER.md (when present)",
        "  and match their tone in everything you write.",
    ]
    p = profile_path()
    if p.exists() and p.stat().st_size > 200:
        lines += [
            f"- Read {p} — VERBATIM samples of the operator's own recorded",
            "  speech. Write every spoken line the way THIS person actually",
            "  talks: their rhythm, sentence length, transitions, recurring",
            "  phrases, level of formality, and humor. Mimic patterns, not",
            "  content — never copy the samples' subject matter.",
            "- HARD PROVENANCE RULE: the voice comes ONLY from that profile",
            "  (plus SOUL.md/USER.md tone). Do NOT imitate the style of",
            "  prior scripts, competitor transcripts, the insight base, or",
            "  any other speaker quoted anywhere — those are other people's",
            "  voices and must not leak into the projection.",
        ]
    else:
        lines += [
            "- No recorded-voice profile is available on this instance —",
            "  keep the normal writing contract (Sound Human rules).",
        ]
    return lines
