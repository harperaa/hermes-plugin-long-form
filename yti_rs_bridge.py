"""Research → Artifacts bridge.

A focus-quadrant video lives in research.db. The three existing artifact
workflows (transcript + metadata on disk, the analysis task that writes
analysis.md, the ✨ generation task that writes concepts + scripts) all work
from the insight store (data.db) and the workspace layout
``youtube/{date}/{channel}/{slug}/``. This module drops a research video
into that layout — reusing the transcript Tier 3 already paid for — so
those workflows run on it unchanged.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    from . import yti_analysis, yti_generate, yti_paths, yti_rs_db, yti_store
    from .yti_rs_clients import ClientError
except ImportError:  # pragma: no cover
    import yti_analysis, yti_generate, yti_paths, yti_rs_db, yti_store  # type: ignore
    from yti_rs_clients import ClientError  # type: ignore


def _video(rconn: sqlite3.Connection, video_id: str) -> Optional[dict[str, Any]]:
    return yti_rs_db.one(rconn, """SELECT v.*, c.handle, c.title AS channel_title FROM videos v
                                   LEFT JOIN channels c ON c.channel_id = v.channel_id WHERE v.video_id = ?""", (video_id,))


def workspace_dir_for(v: dict[str, Any], workspace: Optional[Path] = None) -> Path:
    """The same path fetch would have used: youtube/{published date}/{channel}/{slug}."""
    workspace = workspace or yti_paths.workspace_dir()
    pub = None
    try:
        pub = datetime.fromisoformat(str(v.get("published_at") or "").replace("Z", "+00:00"))
    except ValueError:
        pass
    date_str = (pub or datetime.now(timezone.utc)).date().isoformat()
    channel = (v.get("handle") or v.get("channel_title") or v.get("channel_id") or "channel").lstrip("@")
    return workspace / "youtube" / date_str / yti_paths.sanitize(channel) / yti_paths.sanitize(v.get("title") or v["video_id"])


def state(rconn: sqlite3.Connection, sconn: sqlite3.Connection, video_id: str,
          workspace: Optional[Path] = None) -> dict[str, Any]:
    """What exists in the artifacts folder for this video."""
    workspace = workspace or yti_paths.workspace_dir()
    v = _video(rconn, video_id)
    if not v:
        return {"id": video_id, "known": False}
    d = workspace_dir_for(v, workspace)
    rel = str(d.relative_to(workspace)) if d.exists() else None
    sv = yti_store.get_video(sconn, video_id)
    gen = {}
    try:
        gen = yti_generate.generation_states().get(video_id) or {}
    except Exception:  # noqa: BLE001 — kanban may be unavailable
        gen = {}
    analysis = d / "analysis.md"
    meta_dir = d / "metadata"
    return {"id": video_id, "known": True, "dir": rel,
            "transcript": (d / "transcript.json").exists(),
            "transcript_in_research": bool(rconn.execute("SELECT 1 FROM transcripts WHERE video_id = ?", (video_id,)).fetchone()),
            "no_captions": bool(rconn.execute("SELECT 1 FROM transcript_misses WHERE video_id = ?", (video_id,)).fetchone()),
            "metadata": sorted(p.name for p in meta_dir.glob("*.json"))[-1] if meta_dir.is_dir() and any(meta_dir.glob("*.json")) else None,
            "store_status": (sv or {}).get("status"),
            "analysis": analysis.exists(),
            "analysis_queued": (sv or {}).get("status") == "analyzing",
            "scripts": gen,
            "scripts_dir": (rel + "/scripts") if rel and (d / "scripts").is_dir() else None}


def ensure_transcript(rconn: sqlite3.Connection, sconn: sqlite3.Connection, video_id: str, tapi=None,
                      workspace: Optional[Path] = None) -> dict[str, Any]:
    """Transcript + metadata on disk and the video in the insight store, so
    the analysis and generation workflows can see it. Uses the transcript
    Tier 3 already read when there is one; otherwise one TranscriptAPI call."""
    workspace = workspace or yti_paths.workspace_dir()
    v = _video(rconn, video_id)
    if not v:
        return {"error": f"unknown research video {video_id}"}
    d = workspace_dir_for(v, workspace)
    d.mkdir(parents=True, exist_ok=True)
    (d / "metadata").mkdir(exist_ok=True)
    tj = d / "transcript.json"
    source = "existing"
    if not tj.exists():
        row = rconn.execute("SELECT language, segments_json FROM transcripts WHERE video_id = ?", (video_id,)).fetchone()
        segs, lang = None, None
        if row:
            segs, lang, source = json.loads(row["segments_json"] or "[]"), row["language"], "research"
        elif tapi is not None:
            try:
                res = tapi.transcript(video_id)
            except ClientError as exc:
                return {"error": f"transcript fetch failed: {exc}"}
            segs = (res or {}).get("transcript") or (res or {}).get("segments") or []
            lang = (res or {}).get("language")
            source = "fetched"
            if segs:
                text = " ".join(str(s.get("text") or "") for s in segs)
                last = segs[-1]
                length = int(float(last.get("start") or 0) + float(last.get("duration") or 0))
                rconn.execute("""INSERT OR REPLACE INTO transcripts(video_id, language, is_autogen, fetched_at, length_seconds,
                                 text, segments_json) VALUES (?,?,?,?,?,?,?)""",
                              (video_id, lang or "", int(str(lang or "").startswith("asr")), yti_rs_db.now_iso(), length,
                               text, json.dumps(segs)))
                rconn.commit()
        if not segs:
            rconn.execute("INSERT OR REPLACE INTO transcript_misses(video_id, checked_at, reason) VALUES (?,?,?)",
                          (video_id, yti_rs_db.now_iso(), "no captions"))
            rconn.commit()
            return {"error": "this video has no captions — nothing to transcribe", "no_captions": True}
        tj.write_text(json.dumps({"video_id": video_id, "language": lang, "transcript": segs}, indent=2))
        (d / "transcript.txt").write_text(" ".join(str(s.get("text") or "") for s in segs))
    # metadata snapshot, shaped like fetch's
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")
    meta = {"video_id": video_id, "title": v.get("title"), "author_name": v.get("handle") or v.get("channel_title"),
            "published": v.get("published_at"), "viewCount": str(v.get("views") or 0),
            "thumbnail_url": v.get("thumbnail_url"), "link": f"https://www.youtube.com/watch?v={video_id}",
            "duration_seconds": v.get("duration_seconds"), "source": "research"}
    (d / "metadata" / f"{stamp}.json").write_text(json.dumps(meta, indent=2))
    yti_store.upsert_video(sconn, {
        "video_id": video_id, "title": v.get("title") or video_id,
        "channel_handle": "@" + str(v.get("handle") or "").lstrip("@") if v.get("handle") else (v.get("channel_title") or ""),
        "channel_slug": yti_paths.sanitize(str(v.get("handle") or v.get("channel_title") or "").lstrip("@")),
        "published": v.get("published_at") or "", "thumbnail": v.get("thumbnail_url") or "",
        "link": meta["link"], "view_count": int(v.get("views") or 0), "duration_seconds": v.get("duration_seconds"),
        "transcript_path": str(tj.relative_to(workspace)), "status": "transcribed"})
    sconn.commit()
    return {"ok": True, "dir": str(d.relative_to(workspace)), "transcript": source, "metadata": f"{stamp}.json"}


def run_analysis(rconn: sqlite3.Connection, sconn: sqlite3.Connection, video_id: str, tapi=None) -> dict[str, Any]:
    """Queue the analysis task (Video Summary + Top 20 Insights → analysis.md)."""
    ens = ensure_transcript(rconn, sconn, video_id, tapi)
    if ens.get("error"):
        return ens
    res = yti_analysis.trigger_analysis(sconn, video_ids=[video_id])
    return {"ok": True, "queued": res.get("queued", []), "ensure": ens}


def run_scripts(rconn: sqlite3.Connection, sconn: sqlite3.Connection, video_id: str, tapi=None) -> dict[str, Any]:
    """Create the ✨ generation task (gap-finder Mode A → concepts + scripts)."""
    ens = ensure_transcript(rconn, sconn, video_id, tapi)
    if ens.get("error"):
        return ens
    res = yti_generate.create_generation_task(video_id)
    if res.get("error"):
        return res
    return {"ok": True, **res, "ensure": ens}
