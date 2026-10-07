"""Tier 2 (Apify precision) and Tier 3 (transcripts + comments) for the
shortlist only (P5). Everything here goes through the tolerant field mapper
and quarantines unparseable records instead of defaulting to zero.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import json
import sqlite3
from typing import Any, Callable, Optional

try:
    from . import yti_rs_db, yti_rs_normalize, yti_rs_sentiment
    from .yti_rs_clients import ClientError, CreditsExhausted
    from .yti_rs_normalize import pick
except ImportError:  # pragma: no cover
    import yti_rs_db  # type: ignore
    import yti_rs_normalize  # type: ignore
    import yti_rs_sentiment  # type: ignore
    from yti_rs_clients import ClientError, CreditsExhausted  # type: ignore
    from yti_rs_normalize import pick  # type: ignore

DEFAULT_CLASSES = ("hit", "strong_hit")


def focus_window(cfg: dict[str, Any], params: Optional[dict[str, Any]] = None) -> Optional[tuple[float, float]]:
    """(days, multiple) of the focus quadrant Tier 3 is limited to, or None
    when the operator asked for every hit (``params.all`` or
    teardown.focus_only off). Explicit ``focus_days`` / ``focus_multiple``
    params (the Supply / Demand view's current settings) win over config."""
    params = params or {}
    t = cfg.get("teardown", {}) or {}
    if params.get("all"):
        return None
    if params.get("focus_days") is None and not t.get("focus_only", True):
        return None
    days = float(params.get("focus_days") or t.get("focus_days", 7) or 7)
    mult = float(params.get("focus_multiple") or t.get("focus_multiple") or 0) \
        or float(cfg.get("scoring", {}).get("hit_multiple", 3.0))
    return days, mult


def shortlist(conn: sqlite3.Connection, classes: tuple[str, ...] = DEFAULT_CLASSES, *,
              limit: int = 200, tier_below: Optional[int] = 2, hit_multiple: float = 3.0,
              include_under: bool = False, focus: Optional[tuple[float, float]] = None,
              now: Optional[datetime] = None) -> list[dict[str, Any]]:
    """Videos worth paying for: scored hits (or provisional outliers when
    unscored), long-form, optionally only those still at precision tier 1.
    With ``focus`` = (days, multiple) only the focus quadrant: in-niche,
    published within ``days`` and at ``multiple``× the channel's normal views
    or more — the videos the Supply / Demand view highlights."""
    cls = list(classes) + (["under"] if include_under else [])
    marks = ",".join("?" for _ in cls)
    sql = f"""
        SELECT v.*, s.class, s.projected_multiple FROM videos v
        LEFT JOIN scores s ON s.video_id = v.video_id
        WHERE (v.is_short IS NULL OR v.is_short = 0)
          AND ((s.class IN ({marks})) OR (s.class IS NULL AND v.provisional_multiple >= ?))"""
    params: list[Any] = cls + [hit_multiple]
    if focus is not None:
        days, mult = focus
        since = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).isoformat()
        sql += (" AND v.published_at >= ? AND COALESCE(s.in_niche, 1) = 1"
                " AND COALESCE(s.projected_multiple, v.provisional_multiple) >= ?")
        params += [since, mult]
    if tier_below is not None:
        sql += " AND v.precision_tier < ?"
        params.append(tier_below)
    sql += " ORDER BY COALESCE(s.projected_multiple, v.provisional_multiple) DESC LIMIT ?"
    params.append(limit)
    return yti_rs_db.rows(conn, sql, params)


# -- Tier 2 ---------------------------------------------------------------------------------

def run_enrich(conn: sqlite3.Connection, cfg: dict[str, Any], apify, *, classes: tuple[str, ...] = DEFAULT_CLASSES,
               max_results: int = 200, batch: int = 20, include_under: bool = False,
               log: Callable[[str], None] = lambda m: None) -> dict[str, Any]:
    actor = cfg.get("apify", {}).get("search_actor", "streamers/youtube-scraper")
    hit = float(cfg.get("scoring", {}).get("hit_multiple", 3.0))
    todo = shortlist(conn, classes, limit=max_results, hit_multiple=hit, include_under=include_under)
    summary: dict[str, Any] = {"shortlisted": len(todo), "enriched": 0, "quarantined": 0, "errors": [],
                               "field_hits": {}}
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        urls = [f"https://www.youtube.com/watch?v={v['video_id']}" for v in chunk]
        try:
            items = apify.scrape_videos(actor, urls)
        except CreditsExhausted:
            raise
        except ClientError as exc:
            summary["errors"].append(str(exc))
            log(f"apify batch failed: {exc}")
            continue
        for item in items:
            rec, hits = yti_rs_normalize.map_apify_video(item)
            if rec is None:
                yti_rs_db.quarantine(conn, "apify", "views/id unresolved", item)
                summary["quarantined"] += 1
                continue
            summary["field_hits"] = hits
            existing = yti_rs_db.one(conn, "SELECT niche, channel_id FROM videos WHERE video_id = ?", (rec["video_id"],))
            niche = existing["niche"] if existing else "enriched"
            cid = rec.pop("channel_id") or (existing or {}).get("channel_id")
            subs, subs_approx = rec.pop("subscriber_count"), rec.pop("subscriber_approx")
            cname, chandle = rec.pop("channel_name"), rec.pop("channel_handle")
            if cid:
                yti_rs_db.upsert_channel(conn, {"channel_id": cid, "title": cname,
                                                "handle": yti_rs_normalize.channel_handle_clean(chandle),
                                                "subscriber_count": subs, "subscriber_approx": subs_approx,
                                                "niche": niche})
            rec["channel_id"] = cid
            rec["niche"] = niche
            rec["raw_json"] = item
            yti_rs_db.upsert_video(conn, rec)
            yti_rs_db.add_snapshot(conn, rec["video_id"], rec["views"], rec.get("likes"), rec.get("comment_count"))
            summary["enriched"] += 1
        conn.commit()
        log(f"enriched {summary['enriched']}/{len(todo)}")
    if summary["field_hits"]:
        yti_rs_db.set_meta(conn, "apify_field_hits", summary["field_hits"])
    yti_rs_db.set_meta(conn, "last_enrich_run", yti_rs_db.now_iso())
    return summary


# -- channel sizes (approximate, from channel search) -------------------------------------------

def lookup_channel_size(conn: sqlite3.Connection, tapi, channel: dict[str, Any]) -> Optional[int]:
    """Subscriber count for one channel from a TranscriptAPI channel search
    (1 credit; the text is rounded, e.g. "712K subscribers" -> 712000, so the
    value is stored with ``subscriber_approx = 1``). Every other channel the
    search returns that we already know is updated too. Returns the count, or
    None when the search did not return this channel."""
    query = channel.get("handle") or channel.get("title") or channel["channel_id"]
    data = tapi.search(str(query), "channel")
    found: Optional[int] = None
    for r in (data or {}).get("results") or []:
        cid = str(r.get("channelId") or "")
        subs, _ = yti_rs_normalize.parse_views(r.get("subscriberCount"))
        if not cid or subs is None:
            continue
        known = conn.execute("SELECT subscriber_count, subscriber_approx FROM channels WHERE channel_id = ?",
                             (cid,)).fetchone()
        if known is None:
            continue
        if known["subscriber_count"] is None or known["subscriber_approx"]:
            conn.execute("UPDATE channels SET subscriber_count = ?, subscriber_approx = ?, handle = COALESCE(handle, ?) "
                         "WHERE channel_id = ?", (subs, 0 if r.get("_exact") else 1,
                                                  yti_rs_normalize.channel_handle_clean(r.get("handle")), cid))
        if cid == channel["channel_id"]:
            found = subs if known["subscriber_count"] is None or known["subscriber_approx"] else known["subscriber_count"]
    conn.commit()
    return found


def run_sizes(conn: sqlite3.Connection, cfg: dict[str, Any], tapi, *, max_channels: int = 100,
              log: Callable[[str], None] = lambda m: None) -> dict[str, Any]:
    """Fill in missing subscriber counts, most useful first: channels with
    scored in-niche long-form videos, ordered by hits then video count."""
    todo = yti_rs_db.rows(conn, """
        SELECT c.channel_id, c.handle, c.title,
               SUM(CASE WHEN s.class IN ('hit','strong_hit') THEN 1 ELSE 0 END) AS hits, COUNT(*) AS n
        FROM channels c JOIN videos v ON v.channel_id = c.channel_id JOIN scores s ON s.video_id = v.video_id
        WHERE c.subscriber_count IS NULL AND s.format_bucket = 'long' AND s.in_niche = 1 AND s.class != 'immature'
        GROUP BY c.channel_id ORDER BY hits DESC, n DESC LIMIT ?""", (int(max_channels),))
    summary: dict[str, Any] = {"candidates": len(todo), "sized": 0, "not_found": 0, "errors": []}
    for ch in todo:
        if conn.execute("SELECT subscriber_count FROM channels WHERE channel_id = ?", (ch["channel_id"],)).fetchone()[0] is not None:
            summary["sized"] += 1          # a previous search in this run already returned it
            continue
        try:
            subs = lookup_channel_size(conn, tapi, ch)
        except CreditsExhausted:
            raise
        except ClientError as exc:
            summary["errors"].append(f"{ch.get('handle') or ch['channel_id']}: {exc}")
            continue
        if subs is None:
            summary["not_found"] += 1
            log(f"size {ch.get('handle') or ch['channel_id']}: not returned by channel search")
        else:
            summary["sized"] += 1
            log(f"size {ch.get('handle') or ch['channel_id']}: ~{subs:,} subscribers")
    yti_rs_db.set_meta(conn, "last_sizes_run", yti_rs_db.now_iso())
    return summary


# -- Tier 3: transcripts ----------------------------------------------------------------------

TranscriptFn = Callable[[str], Optional[dict[str, Any]]]


def _transcriptapi_fetch(tapi) -> TranscriptFn:
    def fetch(video_id: str) -> Optional[dict[str, Any]]:
        # free availability check first — never spend a credit on a captionless video
        info = tapi.info(video_id)
        langs = (info or {}).get("available_languages") or []
        if not langs:
            return None
        pref = None
        for lang in langs:
            code = str(lang.get("code") or "")
            if code == "en":
                pref = code
                break
            if code.startswith("asr-en") and pref is None:
                pref = code
        if pref is None:
            pref = str(langs[0].get("code") or "") or None
        try:
            data = tapi.transcript(video_id, pref)
        except ClientError as exc:
            if exc.status == 404:
                return None
            raise
        segs = data.get("transcript") or data.get("segments") or []
        if not segs:
            return None
        return {"language": pref, "segments": segs, "metadata": data.get("metadata") or {}}
    return fetch


def run_transcripts(conn: sqlite3.Connection, cfg: dict[str, Any], tapi=None, *, fetch: Optional[TranscriptFn] = None,
                    classes: tuple[str, ...] = DEFAULT_CLASSES, max_n: int = 50, include_under: bool = True,
                    focus: Optional[tuple[float, float]] = None,
                    log: Callable[[str], None] = lambda m: None) -> dict[str, Any]:
    """Transcripts are immutable: cached forever, never refetched. 404 / no
    captions is a normal terminal outcome recorded in ``transcript_misses``."""
    fetch = fetch or _transcriptapi_fetch(tapi)
    hit = float(cfg.get("scoring", {}).get("hit_multiple", 3.0))
    if focus:
        log(f"transcripts limited to the focus quadrant: ≥{focus[1]:g}× and under {focus[0]:g} days old")
    todo = [v for v in shortlist(conn, classes, limit=max_n * 3, tier_below=None, hit_multiple=hit,
                                 include_under=include_under, focus=focus)
            if not conn.execute("SELECT 1 FROM transcripts WHERE video_id = ?", (v["video_id"],)).fetchone()
            and not conn.execute("SELECT 1 FROM transcript_misses WHERE video_id = ?", (v["video_id"],)).fetchone()]
    todo = todo[:max_n]
    summary: dict[str, Any] = {"candidates": len(todo), "fetched": 0, "missing": 0, "errors": []}
    for v in todo:
        vid = v["video_id"]
        try:
            res = fetch(vid)
        except CreditsExhausted:
            raise
        except ClientError as exc:
            summary["errors"].append(f"{vid}: {exc}")
            log(f"transcript {vid} failed: {exc}")
            continue
        if not res:
            conn.execute("INSERT OR REPLACE INTO transcript_misses(video_id, checked_at, reason) VALUES (?,?,?)",
                         (vid, yti_rs_db.now_iso(), "no captions"))
            summary["missing"] += 1
            continue
        segs = res["segments"]
        text = " ".join(str(s.get("text") or "") for s in segs)
        last = segs[-1]
        length = int(float(last.get("start") or 0) + float(last.get("duration") or 0))
        lang = res.get("language") or ""
        conn.execute("""INSERT OR REPLACE INTO transcripts(video_id, language, is_autogen, fetched_at, length_seconds,
                        text, segments_json) VALUES (?,?,?,?,?,?,?)""",
                     (vid, lang, int(lang.startswith("asr")), yti_rs_db.now_iso(), length, text, json.dumps(segs)))
        if not v.get("duration_seconds") and length:
            yti_rs_db.upsert_video(conn, {"video_id": vid, "duration_seconds": length,
                                          "is_short": v["is_short"] if v.get("is_short") is not None
                                          else int(length <= 180)})
        summary["fetched"] += 1
        log(f"transcript {vid}: {length}s, {lang}")
    conn.commit()
    yti_rs_db.set_meta(conn, "last_transcripts_run", yti_rs_db.now_iso())
    return summary


# -- Tier 3: comments -------------------------------------------------------------------------

C_ID = ("cid", "id", "commentId", "comment_id")
C_TEXT = ("comment", "text", "content")
C_AUTHOR = ("author", "authorName", "authorText")
C_LIKES = ("voteCount", "likes", "likeCount", "votes")
C_DATE = ("publishedTimeText", "date", "publishedAt", "time")
C_REPLIES = ("replyCount", "replies", "numberOfReplies")


def map_comment(item: dict[str, Any], video_id: str, idx: int) -> Optional[dict[str, Any]]:
    text = pick(item, C_TEXT)
    if not text:
        return None
    cid = pick(item, C_ID) or f"{video_id}:{idx}"
    likes, _ = yti_rs_normalize.parse_views(pick(item, C_LIKES))
    replies, _ = yti_rs_normalize.parse_views(pick(item, C_REPLIES))
    published, _, _ = yti_rs_normalize.parse_published(pick(item, C_DATE))
    sent, early = yti_rs_sentiment.score_comment(str(text))
    return {"comment_id": str(cid), "video_id": video_id, "author": pick(item, C_AUTHOR),
            "text": str(text), "like_count": likes, "published_at": published, "reply_count": replies,
            "sentiment": sent, "is_early_adopter": int(early)}


def run_comments(conn: sqlite3.Connection, cfg: dict[str, Any], apify, *, classes: tuple[str, ...] = DEFAULT_CLASSES,
                 max_per_video: int = 300, max_videos: int = 30, include_under: bool = True,
                 focus: Optional[tuple[float, float]] = None,
                 log: Callable[[str], None] = lambda m: None) -> dict[str, Any]:
    actor = cfg.get("apify", {}).get("comments_actor", "streamers/youtube-comments-scraper")
    hit = float(cfg.get("scoring", {}).get("hit_multiple", 3.0))
    if focus:
        log(f"comments limited to the focus quadrant: ≥{focus[1]:g}× and under {focus[0]:g} days old")
    todo = [v for v in shortlist(conn, classes, limit=max_videos * 3, tier_below=None, hit_multiple=hit,
                                 include_under=include_under, focus=focus)
            if not conn.execute("SELECT 1 FROM comments WHERE video_id = ? LIMIT 1", (v["video_id"],)).fetchone()][:max_videos]
    summary: dict[str, Any] = {"videos": len(todo), "comments": 0, "early_adopter": 0, "errors": []}
    for v in todo:
        vid = v["video_id"]
        try:
            items = apify.scrape_comments(actor, f"https://www.youtube.com/watch?v={vid}", max_per_video)
        except CreditsExhausted:
            raise
        except ClientError as exc:
            summary["errors"].append(f"{vid}: {exc}")
            log(f"comments {vid} failed: {exc}")
            continue
        n = 0
        for i, item in enumerate(items):
            c = map_comment(item, vid, i)
            if not c:
                continue
            conn.execute("""INSERT OR REPLACE INTO comments(comment_id, video_id, author, text, like_count, published_at,
                            reply_count, sentiment, is_early_adopter) VALUES (?,?,?,?,?,?,?,?,?)""",
                         (c["comment_id"], vid, c["author"], c["text"], c["like_count"], c["published_at"],
                          c["reply_count"], c["sentiment"], c["is_early_adopter"]))
            n += 1
            summary["early_adopter"] += c["is_early_adopter"]
        conn.commit()
        summary["comments"] += n
        log(f"comments {vid}: {n}")
    yti_rs_db.set_meta(conn, "last_comments_run", yti_rs_db.now_iso())
    return summary
