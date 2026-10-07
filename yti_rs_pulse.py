"""Pulse — a free, frequent re-read of exact views for the videos that matter
right now: recent uploads running above normal (the Supply / Demand focus
quadrant and the ones about to enter it).

Each pulse writes one ``video_snapshots`` row per video with a full
timestamp, so several a day accumulate into a view trajectory. The Supply /
Demand view draws that trajectory as a trail behind the current dot and reads
momentum from it: is the video still gaining faster than it was, or fading?

Cost: with the YouTube Data API, 1 unit per 50 videos (videos.list). Without
it, TranscriptAPI's free channel/latest per channel — which covers a recent
upload almost always, since it lists the newest 15.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

try:
    from . import yti_rs_db, yti_rs_normalize
    from .yti_rs_clients import ClientError, QuotaExhausted
except ImportError:  # pragma: no cover
    import yti_rs_db  # type: ignore
    import yti_rs_normalize  # type: ignore
    from yti_rs_clients import ClientError, QuotaExhausted  # type: ignore

MAX_VIDEOS = 300


def watchlist(conn: sqlite3.Connection, cfg: dict[str, Any], *, now: Optional[datetime] = None,
              limit: int = MAX_VIDEOS) -> list[dict[str, Any]]:
    """Recent, in-niche, long-form videos at half the hit multiple or more —
    the focus quadrant plus the risers that may enter it, watched for twice
    the focus window so a video that leaves the quadrant still gets its last
    points."""
    now = now or datetime.now(timezone.utc)
    t = cfg.get("teardown", {}) or {}
    days = float(t.get("focus_days", 7) or 7) * 2
    hit = float(t.get("focus_multiple") or 0) or float(cfg.get("scoring", {}).get("hit_multiple", 3.0))
    since = (now - timedelta(days=days)).isoformat()
    return yti_rs_db.rows(conn, """
        SELECT v.video_id, v.channel_id, c.handle, v.views, v.published_at,
               COALESCE(s.projected_multiple, v.provisional_multiple) AS m
        FROM videos v LEFT JOIN scores s ON s.video_id = v.video_id
        LEFT JOIN channels c ON c.channel_id = v.channel_id
        WHERE v.published_at >= ? AND (v.is_short IS NULL OR v.is_short = 0)
          AND COALESCE(s.in_niche, 1) = 1
          AND COALESCE(s.projected_multiple, v.provisional_multiple) >= ?
        ORDER BY COALESCE(s.projected_multiple, v.provisional_multiple) DESC LIMIT ?""",
        [since, hit / 2.0, limit])


def _exact_from_data_api(source, ids: list[str]) -> dict[str, dict[str, Any]]:
    yt = getattr(source, "yt", None)
    if yt is None or not yt.can_afford(1 + len(ids) // 50):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for vid, it in yt._videos(ids).items():
        st = it.get("statistics") or {}
        try:
            out[vid] = {"views": int(st.get("viewCount")), "likes": _int(st.get("likeCount")),
                        "comments": _int(st.get("commentCount"))}
        except (TypeError, ValueError):
            continue
    return out


def _exact_from_latest(source, todo: list[dict[str, Any]], log: Callable[[str], None]) -> dict[str, dict[str, Any]]:
    """Free fallback: channel/latest per distinct channel; only videos that are
    still among the channel's newest uploads get a reading."""
    wanted = {r["video_id"] for r in todo}
    out: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for r in todo:
        ident = r["handle"] or r["channel_id"]
        if ident in seen:
            continue
        seen.add(ident)
        try:
            payload = source.latest(ident)
        except ClientError as exc:
            log(f"latest {ident} failed: {exc}")
            continue
        for item in (payload or {}).get("results") or []:
            vid = str(item.get("videoId") or "")
            views, _ = yti_rs_normalize.parse_views(item.get("viewCount"))
            if vid in wanted and views is not None:
                out[vid] = {"views": views, "likes": None, "comments": None}
    return out


def _int(v: Any) -> Optional[int]:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def run_pulse(conn: sqlite3.Connection, cfg: dict[str, Any], source, *, now: Optional[datetime] = None,
              log: Callable[[str], None] = lambda m: None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    todo = watchlist(conn, cfg, now=now)
    summary: dict[str, Any] = {"watched": len(todo), "updated": 0, "via": None, "rising": 0, "falling": 0}
    if not todo:
        log("pulse: nothing recent above half the hit multiple — nothing to watch")
        return summary
    ids = [r["video_id"] for r in todo]
    exact = {}
    try:
        exact = _exact_from_data_api(source, ids)
        summary["via"] = "youtube" if exact else None
    except QuotaExhausted as exc:
        log(f"pulse: {exc}")
    if not exact:
        exact = _exact_from_latest(source, todo, log)
        summary["via"] = "transcriptapi" if exact else None
    stamp = now.isoformat()
    for r in todo:
        e = exact.get(r["video_id"])
        if not e:
            continue
        prev = r["views"]
        yti_rs_db.add_snapshot(conn, r["video_id"], e["views"], e["likes"], e["comments"], captured_at=stamp)
        yti_rs_db.upsert_video(conn, {"video_id": r["video_id"], "views": e["views"], "views_approx": 0,
                                      "precision_tier": 2, "last_seen": stamp,
                                      **({"likes": e["likes"]} if e["likes"] is not None else {}),
                                      **({"comment_count": e["comments"]} if e["comments"] is not None else {})})
        summary["updated"] += 1
        if prev is not None:
            summary["rising" if e["views"] > prev else "falling"] += int(e["views"] != prev)
    conn.commit()
    yti_rs_db.set_meta(conn, "last_pulse", stamp)
    log(f"pulse: {summary['updated']} of {len(todo)} watched videos re-read via {summary['via']}")
    return summary


# -- trajectory → momentum (read by the Supply / Demand view) ------------------------------------

def momentum(points: list[tuple[datetime, int]]) -> dict[str, Any]:
    """From timestamped view readings: the latest views-per-hour and whether
    the pace is picking up or fading (latest interval vs the one before,
    ±10% — the same rule the Trends tab uses)."""
    pts = sorted((t, v) for t, v in points if v is not None)
    rates: list[float] = []
    for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
        hours = (t1 - t0).total_seconds() / 3600
        if hours >= 0.25:
            rates.append(max(0.0, (v1 - v0) / hours))
    if not rates:
        return {"vph": None, "dir": None, "n": len(pts)}
    cur = rates[-1]
    if len(rates) < 2:
        return {"vph": round(cur, 1), "dir": None, "n": len(pts)}
    prev = rates[-2]
    d = "up" if cur > prev * 1.1 else "down" if cur < prev * 0.9 else "flat"
    return {"vph": round(cur, 1), "dir": d, "n": len(pts)}
