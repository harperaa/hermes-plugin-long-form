"""Normalisation: view-count strings, relative dates, durations, and the
tolerant Apify field mapper (spec §6).

Every parser returns ``None`` (never zero) when a value cannot be resolved,
so callers quarantine instead of corrupting a baseline with a silent 0.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

log = logging.getLogger("yti.research.normalize")

_SUFFIX = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}

_VIEWS_RE = re.compile(
    r"(?i)(?P<num>\d[\d,]*(?:\.\d+)?)\s*(?P<suf>[kmb])?\s*(?:views?|watching)?")
_NO_VIEWS_RE = re.compile(r"(?i)^\s*no\s+views?")
_REL_RE = re.compile(
    r"(?i)(?:streamed|premiered|published|uploaded)?\s*(?P<n>\d+)\s*"
    r"(?P<unit>second|sec|minute|min|hour|hr|day|week|month|year)s?\s+ago")
_ABS_RE = re.compile(
    r"(?i)(?:streamed|premiered|published)?\s*(?:on\s+)?"
    r"(?P<mon>jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+"
    r"(?P<day>\d{1,2}),?\s+(?P<year>\d{4})")
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}

# Uncertainty (±days) attached to each relative-date granularity.
GRANULARITY_DAYS = {
    "second": 1 / 1440, "sec": 1 / 1440, "minute": 1 / 24, "min": 1 / 24,
    "hour": 1 / 24, "hr": 1 / 24, "day": 0.5, "week": 3.5, "month": 15.0,
    "year": 183.0,
}
# Anything coarser than a week is excluded from age-sensitive projection.
MAX_PROJECTABLE_GRANULARITY_DAYS = 7.0


def parse_views(text: Any) -> tuple[Optional[int], bool]:
    """``"3.4M views"`` → (3400000, True); ``"1,234 views"`` → (1234, False);
    ``"No views"`` → (0, False); ``"3.2M views 2 weeks ago"`` → (3200000, True).
    Returns (None, False) when unparseable."""
    if text is None:
        return None, False
    if isinstance(text, bool):
        return None, False
    if isinstance(text, (int, float)):
        return int(text), False
    s = str(text).strip()
    if not s:
        return None, False
    if _NO_VIEWS_RE.match(s):
        return 0, False
    if re.fullmatch(r"\d+", s):
        return int(s), False
    m = _VIEWS_RE.search(s)
    if not m or not m.group("num"):
        return None, False
    num = float(m.group("num").replace(",", ""))
    suf = (m.group("suf") or "").lower()
    if suf:
        return int(round(num * _SUFFIX[suf])), True
    return int(round(num)), False


def split_views_and_age(text: str) -> tuple[Optional[int], bool, Optional[str]]:
    """The combined ``"3.2M views 2 weeks ago"`` form: returns
    (views, approx, remaining_age_text)."""
    if not text:
        return None, False, None
    views, approx = parse_views(text)
    rel = _REL_RE.search(text)
    return views, approx, (rel.group(0).strip() if rel else None)


def parse_published(text: Any, fetched_at: Optional[datetime] = None
                    ) -> tuple[Optional[str], bool, Optional[float]]:
    """Return (published_at_iso, approx, granularity_days).

    Relative dates resolve against ``fetched_at`` (never ``now()`` at read
    time) and always set approx=True. ISO strings are exact.
    """
    if text is None:
        return None, False, None
    if isinstance(text, datetime):
        return text.astimezone(timezone.utc).isoformat(), False, 0.0
    s = str(text).strip()
    if not s:
        return None, False, None
    # exact ISO-8601 (RSS, Apify)
    iso = _try_iso(s)
    if iso is not None:
        return iso.isoformat(), False, 0.0
    ref = fetched_at or datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    m = _REL_RE.search(s)
    if m:
        n = int(m.group("n"))
        unit = m.group("unit").lower()
        delta = _unit_delta(unit, n)
        gran = GRANULARITY_DAYS.get(unit, 183.0) * max(1, n) if unit in ("month", "year") \
            else GRANULARITY_DAYS.get(unit, 0.5)
        # "2 years ago" is ±~6 months per the spec; clamp so one unit never
        # exceeds the spec's stated uncertainty.
        gran = min(gran, 183.0) if unit == "year" else min(gran, 45.0) if unit == "month" else gran
        return (ref - delta).isoformat(), True, float(gran)
    m = _ABS_RE.search(s)
    if m:
        mon = _MONTHS.get(m.group("mon").lower()[:3])
        if mon:
            try:
                dt = datetime(int(m.group("year")), mon, int(m.group("day")), tzinfo=timezone.utc)
                return dt.isoformat(), True, 0.5
            except ValueError:
                return None, False, None
    return None, False, None


def _try_iso(s: str) -> Optional[datetime]:
    t = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        try:
            dt = datetime.strptime(s[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _unit_delta(unit: str, n: int) -> timedelta:
    if unit in ("second", "sec"):
        return timedelta(seconds=n)
    if unit in ("minute", "min"):
        return timedelta(minutes=n)
    if unit in ("hour", "hr"):
        return timedelta(hours=n)
    if unit == "day":
        return timedelta(days=n)
    if unit == "week":
        return timedelta(weeks=n)
    if unit == "month":
        return timedelta(days=30.44 * n)
    return timedelta(days=365.25 * n)


def parse_duration(text: Any) -> Optional[int]:
    """``"12:34"`` → 754; ``"1:02:45"`` → 3765; ``"0:47"`` → 47; ``None``
    (live) → None. Also accepts integers, ``"PT36M9S"`` and ``"36:09"``-style
    Apify strings; milliseconds when the key says so is the mapper's job."""
    if text is None or isinstance(text, bool):
        return None
    if isinstance(text, (int, float)):
        return int(text) if text >= 0 else None
    s = str(text).strip()
    if not s:
        return None
    if re.fullmatch(r"\d+", s):
        return int(s)
    m = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{2})", s)
    if m:
        h = int(m.group(1) or 0)
        return h * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    m = re.fullmatch(r"(?i)P(?:T)?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s)
    if m and any(m.groups()):
        return int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0)
    return None


def is_short(duration_seconds: Optional[int], explicit_flag: Optional[bool] = None,
             link: Optional[str] = None) -> Optional[bool]:
    """Shorts rule: an actor's explicit flag wins; else duration <= 180s.
    Returns None when nothing is known."""
    if explicit_flag is not None:
        return bool(explicit_flag)
    if link and "/shorts/" in link:
        return True
    if duration_seconds is None:
        return None
    return duration_seconds <= 180


# -- tolerant Apify field mapping ------------------------------------------------

VIEW_KEYS = ("viewCount", "views", "viewCountInt", "numberOfViews", "statistics.viewCount")
LIKE_KEYS = ("likes", "likeCount", "numberOfLikes")
DATE_KEYS = ("date", "uploadDate", "publishedAt", "publishDate", "uploadedAt")
DURATION_KEYS = ("duration", "durationSeconds", "lengthSeconds", "durationMs")
COMMENT_KEYS = ("commentsCount", "commentCount", "numberOfComments")
SUBS_KEYS = ("numberOfSubscribers", "subscriberCount", "channelSubscriberCount", "subscribers")
ID_KEYS = ("id", "videoId", "video_id")
TITLE_KEYS = ("title", "name")
CHANNEL_ID_KEYS = ("channelId", "channel_id", "channel.id")
CHANNEL_NAME_KEYS = ("channelName", "channelTitle", "author", "channel.name")
CHANNEL_HANDLE_KEYS = ("channelUsername", "channelHandle", "handle")
THUMB_KEYS = ("thumbnailUrl", "thumbnail", "thumbnails.0.url")
DESC_KEYS = ("text", "description")
SHORT_FLAG_KEYS = ("isShort", "isShorts", "short")


def _dig(item: dict[str, Any], dotted: str) -> Any:
    cur: Any = item
    for part in dotted.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit():
            idx = int(part)
            cur = cur[idx] if idx < len(cur) else None
        else:
            return None
        if cur is None:
            return None
    return cur


def pick(item: dict[str, Any], keys: tuple[str, ...], hits: Optional[dict[str, str]] = None,
         field: str = "") -> Any:
    """First candidate key with a non-None value; records which key hit so
    schema drift is visible in a single grep of the log."""
    for k in keys:
        val = _dig(item, k)
        if val is not None and val != "":
            if hits is not None:
                hits[field or keys[0]] = k
            return val
    return None


def map_apify_video(item: dict[str, Any]) -> tuple[Optional[dict[str, Any]], dict[str, str]]:
    """Map one Apify youtube-scraper item onto the videos row shape.

    Returns (record, key_hits). ``record`` is None when views cannot be
    resolved — the caller must quarantine the payload rather than default.
    """
    hits: dict[str, str] = {}
    vid = pick(item, ID_KEYS, hits, "id")
    raw_views = pick(item, VIEW_KEYS, hits, "views")
    views, _ = parse_views(raw_views)
    if not vid or views is None:
        return None, hits
    likes, _ = parse_views(pick(item, LIKE_KEYS, hits, "likes"))
    comments, _ = parse_views(pick(item, COMMENT_KEYS, hits, "comments"))
    subs, subs_approx = parse_views(pick(item, SUBS_KEYS, hits, "subs"))
    published, pub_approx, gran = parse_published(pick(item, DATE_KEYS, hits, "date"))
    dur_raw = pick(item, DURATION_KEYS, hits, "duration")
    if hits.get("duration") == "durationMs" and dur_raw is not None:
        duration = int(float(dur_raw) / 1000)
    else:
        duration = parse_duration(dur_raw)
    flag = pick(item, SHORT_FLAG_KEYS, hits, "short")
    url = str(item.get("url") or "")
    short = is_short(duration, bool(flag) if flag is not None else None, url)
    thumb = pick(item, THUMB_KEYS, hits, "thumb")
    rec = {
        "video_id": str(vid),
        "title": str(pick(item, TITLE_KEYS, hits, "title") or "untitled"),
        "description": pick(item, DESC_KEYS, hits, "description"),
        "channel_id": pick(item, CHANNEL_ID_KEYS, hits, "channel_id"),
        "channel_name": pick(item, CHANNEL_NAME_KEYS, hits, "channel_name"),
        "channel_handle": pick(item, CHANNEL_HANDLE_KEYS, hits, "channel_handle"),
        "published_at": published,
        "published_approx": 1 if pub_approx else 0,
        "published_granularity_days": gran,
        "duration_seconds": duration,
        "is_short": None if short is None else int(short),
        "views": views,
        "views_approx": 0,
        "likes": likes,
        "comment_count": comments,
        "subscriber_count": subs,
        "subscriber_approx": 1 if subs_approx else 0,
        "thumbnail_url": str(thumb) if thumb else None,
        "precision_tier": 2,
    }
    log.debug("apify field hits: %s", hits)
    return rec, hits


def channel_handle_clean(handle: Any) -> Optional[str]:
    """``"/@DanKoeTalks"`` / ``"DanKoeTalks"`` / ``"@DanKoeTalks"`` → ``"@DanKoeTalks"``."""
    if not handle:
        return None
    h = str(handle).strip().strip("/")
    if h.startswith("https://") or h.startswith("http://"):
        h = h.rstrip("/").rsplit("/", 1)[-1]
    if not h:
        return None
    return h if h.startswith("@") else "@" + h
