"""Tier 0 — free daily RSS snapshots (spec §3.3, P4).

``GET /youtube/channel/latest`` is free and returns EXACT publish timestamps
and integer view counts for the latest ~15 uploads. Run daily for every
tracked channel (the dashboard's followed channels plus any research channel
flagged ``is_tracked``) and the ``video_snapshots`` table accumulates the
view trajectories that make age-normalisation and inflection detection
possible. Costs nothing; start on day one.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable, Optional

try:
    from . import yti_rs_db, yti_rs_normalize
    from .yti_rs_clients import ClientError, CreditsExhausted
except ImportError:  # pragma: no cover
    import yti_rs_db  # type: ignore
    import yti_rs_normalize  # type: ignore
    from yti_rs_clients import ClientError, CreditsExhausted  # type: ignore


def tracked_channels(rconn: sqlite3.Connection, followed_handles: list[str]) -> list[dict[str, Any]]:
    """Union of dashboard-followed handles and research channels with
    ``is_tracked=1``. Each entry: {identifier, channel_id?, handle?, source}."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    known_by_handle = {
        (r["handle"] or "").lower(): r for r in yti_rs_db.rows(
            rconn, "SELECT channel_id, handle, niche FROM channels WHERE handle IS NOT NULL")}
    for h in followed_handles:
        h = yti_rs_normalize.channel_handle_clean(h) or ""
        if not h or h.lower() in seen:
            continue
        seen.add(h.lower())
        row = known_by_handle.get(h.lower())
        out.append({"identifier": h, "handle": h, "channel_id": row["channel_id"] if row else None,
                    "niche": row["niche"] if row else None, "source": "followed"})
    for r in yti_rs_db.rows(rconn, "SELECT channel_id, handle, niche FROM channels WHERE is_tracked = 1"):
        key = (r["handle"] or r["channel_id"]).lower()
        if key in seen or r["channel_id"].lower() in seen:
            continue
        seen.add(key)
        out.append({"identifier": r["handle"] or r["channel_id"], "handle": r["handle"],
                    "channel_id": r["channel_id"], "niche": r["niche"], "source": "research"})
    return out


def ingest_latest(rconn: sqlite3.Connection, payload: dict[str, Any], *, niche: str,
                  handle: Optional[str] = None, captured_at: Optional[str] = None,
                  mark_tracked: bool = True, discovered_via: str = "rss",
                  durations: Optional[dict[str, int]] = None) -> dict[str, Any]:
    """Upsert channel + videos from a ``channel/latest`` payload and write one
    snapshot row per video stamped ``captured_at`` (default: now, to the
    second — a reading's time matters for velocity and for projecting a
    young video's multiple)."""
    ch = payload.get("channel") or {}
    results = payload.get("results") or []
    channel_id = str(ch.get("channelId") or (results[0].get("channelId") if results else "") or "")
    if not channel_id:
        raise ValueError("latest payload has no channelId")
    yti_rs_db.upsert_channel(rconn, {
        "channel_id": channel_id, "handle": handle, "title": ch.get("title") or ch.get("author"),
        "niche": niche, "is_tracked": 1 if mark_tracked else None, "raw_json": ch,
    })
    n_new = n_snap = 0
    for v in results:
        vid = str(v.get("videoId") or "")
        views, _ = yti_rs_normalize.parse_views(v.get("viewCount"))
        published, _, _ = yti_rs_normalize.parse_published(v.get("published"))
        if not vid or views is None:
            yti_rs_db.quarantine(rconn, "transcriptapi", "latest: missing videoId/viewCount", v)
            continue
        thumb = (v.get("thumbnail") or {}).get("url") if isinstance(v.get("thumbnail"), dict) else None
        # RSS carries no duration; the dashboard's transcript store often does
        # (free), and a /shorts/ link is definitive.
        dur = (durations or {}).get(vid)
        short = yti_rs_normalize.is_short_rss(dur, str(v.get("link") or ""))
        new = yti_rs_db.upsert_video(rconn, {
            "video_id": vid, "channel_id": channel_id, "title": str(v.get("title") or "untitled"),
            "description": v.get("description"), "published_at": published, "published_approx": 0,
            "published_granularity_days": 0.0, "views": views, "views_approx": 0,
            "duration_seconds": dur, "is_short": None if short is None else int(short),
            "thumbnail_url": thumb or f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
            "niche": niche, "discovered_via": discovered_via, "raw_json": v,
        })
        n_new += int(new)
        yti_rs_db.add_snapshot(rconn, vid, views, captured_at=captured_at)
        n_snap += 1
    rconn.commit()
    return {"channel_id": channel_id, "videos": len(results), "new": n_new, "snapshots": n_snap}


def run_snapshot(rconn: sqlite3.Connection, tapi, followed_handles: list[str], *,
                 default_niche: str = "followed", now: Optional[datetime] = None,
                 durations: Optional[dict[str, int]] = None,
                 log: Callable[[str], None] = lambda m: None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    captured_at = now.isoformat()
    targets = tracked_channels(rconn, followed_handles)
    summary: dict[str, Any] = {"channels": len(targets), "videos": 0, "new_videos": 0,
                               "snapshots": 0, "errors": [], "captured_at": captured_at}
    for t in targets:
        try:
            payload = tapi.latest(t["identifier"])
        except CreditsExhausted:
            raise
        except ClientError as exc:
            summary["errors"].append(f"{t['identifier']}: {exc}")
            log(f"snapshot {t['identifier']} failed: {exc}")
            continue
        if not isinstance(payload, dict) or payload.get("error") or "results" not in payload:
            msg = f"{t['identifier']}: {(payload or {}).get('error') or 'no results'}"
            summary["errors"].append(msg)
            log(f"snapshot {msg}")
            continue
        try:
            res = ingest_latest(rconn, payload, niche=t.get("niche") or default_niche,
                                handle=t.get("handle"), captured_at=captured_at, durations=durations)
        except ValueError as exc:
            summary["errors"].append(f"{t['identifier']}: {exc}")
            continue
        summary["videos"] += res["videos"]
        summary["new_videos"] += res["new"]
        summary["snapshots"] += res["snapshots"]
        log(f"snapshot {t['identifier']}: {res['videos']} videos, {res['new']} new")
    yti_rs_db.set_meta(rconn, "last_snapshot_run", yti_rs_db.now_iso())
    yti_rs_db.set_meta(rconn, "last_snapshot_summary", summary)
    return summary
