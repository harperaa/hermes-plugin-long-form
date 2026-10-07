"""Provider clients: TranscriptAPI + Apify (spec §3, §15).

Rules baked in here, not in callers:
- Header auth only (never ``?token=``).
- Timeouts on every request.
- Retry 408/429/503 + connection errors with jittered backoff, honouring
  Retry-After, max 3 attempts. 400/401/402/404/422 are terminal; 402 raises
  ``CreditsExhausted`` so the whole run aborts.
- One ``api_usage`` row per call, credits only on HTTP 200 for TranscriptAPI.
- Token bucket at 80% of 300/min; proactive back-off on X-RateLimit-Remaining.
- Search results cache 24h keyed on (endpoint, params); resolve caches forever.
- ``clients`` know nothing about analysis — they return parsed JSON only.

HTTP is injectable (``http``) so tests never touch the network.
"""
from __future__ import annotations

import json
import re
import logging
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Optional

try:
    from . import yti_rs_budget, yti_rs_db
    from .yti_rs_logging import get_logger, redact
except ImportError:  # pragma: no cover
    import yti_rs_budget  # type: ignore
    import yti_rs_db  # type: ignore
    from yti_rs_logging import get_logger, redact  # type: ignore

log = get_logger("yti.research.clients")

TRANSCRIPTAPI_BASE = "https://transcriptapi.com/api/v2"
APIFY_BASE = "https://api.apify.com/v2"

CONNECT_TIMEOUT = 10
READ_TIMEOUT = 60
APIFY_SYNC_READ_TIMEOUT = 300

RETRYABLE = {408, 429, 503}
TERMINAL = {400, 401, 402, 404, 422}


class HttpResponse:
    __slots__ = ("status", "headers", "body")

    def __init__(self, status: int, headers: dict[str, str], body: str) -> None:
        self.status = status
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.body = body

    def json(self) -> Any:
        try:
            return json.loads(self.body) if self.body else None
        except json.JSONDecodeError:
            return {"error": f"non-JSON response: {self.body[:200]}"}


# http(method, url, headers, body_str_or_None, timeout_secs) -> HttpResponse
Http = Callable[[str, str, dict[str, str], Optional[str], float], HttpResponse]


def default_http(method: str, url: str, headers: dict[str, str], body: Optional[str],
                 timeout: float) -> HttpResponse:
    data = body.encode("utf-8") if body is not None else None
    # transcriptapi.com sits behind Cloudflare, which rejects Python-urllib's
    # default signature; a curl-style UA passes.
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"User-Agent": "curl/8.4.0", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return HttpResponse(resp.status, dict(resp.headers.items()),
                                resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        return HttpResponse(exc.code, dict(exc.headers.items()) if exc.headers else {},
                            exc.read().decode("utf-8", errors="replace"))


class ClientError(RuntimeError):
    def __init__(self, provider: str, status: Optional[int], message: str) -> None:
        super().__init__(f"{provider} HTTP {status}: {redact(message)[:300]}")
        self.provider, self.status = provider, status
        self.body = message


class QuotaExhausted(ClientError):
    """The YouTube Data API's daily quota is spent (resets at midnight Pacific)."""


class CreditsExhausted(ClientError):
    """HTTP 402 — abort the whole run immediately."""


class ConnectionFailed(ClientError):
    pass


def _retry_after(resp: HttpResponse) -> Optional[float]:
    ra = resp.headers.get("retry-after")
    if not ra:
        return None
    try:
        return float(ra)
    except ValueError:
        return None


class _Base:
    provider = "base"

    def __init__(self, *, http: Optional[Http] = None, budget: Optional[yti_rs_budget.Budget] = None,
                 conn: Optional[sqlite3.Connection] = None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 max_attempts: int = 3) -> None:
        self.http = http or default_http
        self.budget = budget
        self.conn = conn if conn is not None else (budget.conn if budget else None)
        self.sleep = sleep
        self.clock = clock
        self.max_attempts = max_attempts

    def _record(self, endpoint: str, credits: int, status: Optional[int], ms: int) -> None:
        if self.budget is not None:
            self.budget.record(self.provider, endpoint, credits, status, ms)
        elif self.conn is not None:
            yti_rs_db.record_usage(self.conn, self.provider, endpoint, credits, status, ms)

    def _request(self, method: str, url: str, headers: dict[str, str], body: Optional[str],
                 timeout: float, endpoint: str, credits_on_200: int,
                 pre_hook: Optional[Callable[[], None]] = None,
                 no_retry: frozenset = frozenset()) -> HttpResponse:
        """Retry policy + ledger. Never raises for a retryable status until the
        attempts are exhausted; raises ClientError for terminal statuses."""
        last: Optional[HttpResponse] = None
        for attempt in range(self.max_attempts):
            if pre_hook:
                pre_hook()
            t0 = self.clock()
            try:
                resp = self.http(method, url, headers, body, timeout)
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                ms = int((self.clock() - t0) * 1000)
                self._record(endpoint, 0, None, ms)
                log.warning("%s %s connection error (attempt %s): %s", self.provider,
                            endpoint, attempt + 1, exc)
                if attempt + 1 >= self.max_attempts:
                    raise ConnectionFailed(self.provider, None, str(exc))
                self.sleep(yti_rs_budget.backoff_delay(attempt))
                continue
            ms = int((self.clock() - t0) * 1000)
            last = resp
            charged = credits_on_200 if resp.status == 200 else 0
            self._record(endpoint, charged, resp.status, ms)
            self._after_response(resp)
            if resp.status < 400:
                return resp
            if resp.status == 402:
                raise CreditsExhausted(self.provider, 402,
                                       "credits exhausted — top up before re-running")
            if resp.status in TERMINAL or resp.status in no_retry:
                raise ClientError(self.provider, resp.status, resp.body)
            if resp.status in RETRYABLE or resp.status >= 500:
                if attempt + 1 >= self.max_attempts:
                    break
                self.sleep(yti_rs_budget.backoff_delay(attempt, retry_after=_retry_after(resp)))
                continue
            raise ClientError(self.provider, resp.status, resp.body)
        assert last is not None
        raise ClientError(self.provider, last.status, last.body)

    def _after_response(self, resp: HttpResponse) -> None:  # pragma: no cover - hook
        return None


class TranscriptAPI(_Base):
    provider = "transcriptapi"

    SEARCH_CACHE_SECS = 24 * 3600

    def __init__(self, api_key: str, *, base: str = TRANSCRIPTAPI_BASE,
                 bucket: Optional[yti_rs_budget.TokenBucket] = None, **kw: Any) -> None:
        super().__init__(**kw)
        if not api_key:
            raise RuntimeError("TRANSCRIPT_API_KEY is not set")
        self._headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
        self.base = base.rstrip("/")
        self.bucket = bucket or yti_rs_budget.TokenBucket(240.0, sleep=self.sleep, clock=self.clock)
        self._ratelimit_reset: Optional[float] = None
        self._ratelimit_remaining: Optional[int] = None

    # -- plumbing ------------------------------------------------------------
    def _after_response(self, resp: HttpResponse) -> None:
        rem = resp.headers.get("x-ratelimit-remaining")
        reset = resp.headers.get("x-ratelimit-reset")
        try:
            self._ratelimit_remaining = int(rem) if rem is not None else None
            self._ratelimit_reset = float(reset) if reset is not None else None
        except ValueError:
            pass

    def _pre(self) -> None:
        # Proactive back-off: if the window is nearly spent, wait for the reset
        # rather than eating a 429.
        if self._ratelimit_remaining is not None and self._ratelimit_remaining <= 3 \
                and self._ratelimit_reset:
            wait = max(0.0, self._ratelimit_reset - time.time())
            if 0 < wait <= 65:
                log.info("transcriptapi rate window nearly spent; sleeping %.1fs", wait)
                self.sleep(wait)
            self._ratelimit_remaining = None
        self.bucket.acquire()

    def _cache_key(self, endpoint: str, params: dict[str, Any]) -> str:
        return endpoint + "?" + urllib.parse.urlencode(sorted((k, str(v)) for k, v in params.items()
                                                             if v is not None))

    def _cached(self, key: str, max_age: Optional[float]) -> Optional[Any]:
        if self.conn is None:
            return None
        row = self.conn.execute("SELECT fetched_at, status, body FROM http_cache WHERE cache_key = ?",
                                (key,)).fetchone()
        if not row:
            return None
        if max_age is not None:
            from datetime import datetime, timezone
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(row["fetched_at"])).total_seconds()
            except ValueError:
                return None
            if age > max_age:
                return None
        try:
            return json.loads(row["body"])
        except json.JSONDecodeError:
            return None

    def _store(self, key: str, status: int, body: str) -> None:
        if self.conn is None:
            return
        self.conn.execute("INSERT OR REPLACE INTO http_cache(cache_key, fetched_at, status, body)"
                          " VALUES (?,?,?,?)", (key, yti_rs_db.now_iso(), status, body))
        self.conn.commit()

    def get(self, endpoint: str, params: dict[str, Any], *, credits: int = 1,
            cache_secs: Optional[float] = None) -> Any:
        """GET ``/youtube/<endpoint>``; returns parsed JSON. ``cache_secs``
        None = no cache, float('inf') = forever."""
        key = self._cache_key(endpoint, params)
        if cache_secs is not None:
            hit = self._cached(key, None if cache_secs == float("inf") else cache_secs)
            if hit is not None:
                return hit
        if self.budget is not None and credits:
            self.budget.check(self.provider, credits)
        qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        url = f"{self.base}/youtube/{endpoint}?{qs}"
        resp = self._request("GET", url, self._headers, None, READ_TIMEOUT,
                             endpoint, credits, pre_hook=self._pre)
        data = resp.json()
        if cache_secs is not None and resp.status == 200:
            self._store(key, resp.status, resp.body)
        return data

    # -- endpoints (§3.2) ----------------------------------------------------
    # Pagination rule (verified live): a continuation token is the ONLY
    # parameter on a follow-up page — sending it alongside channel/q is a 400.
    def search(self, q: str, type_: str = "video", continuation: Optional[str] = None) -> dict[str, Any]:
        params = {"continuation": continuation} if continuation else {"q": q, "type": type_}
        return self.get("search", params, credits=1, cache_secs=self.SEARCH_CACHE_SECS)

    def channel_videos(self, channel: str, continuation: Optional[str] = None, *,
                       sort: Optional[str] = None) -> dict[str, Any]:
        """One page of the channel's Videos tab (never Shorts). Unsorted pages
        hold ~100 uploads newest-first; ``sort="popular"`` is YouTube's sorted
        feed (~30 most-viewed uploads). Each page is one credit."""
        params = {"continuation": continuation} if continuation else {"channel": channel}
        if sort and not continuation:
            params["sort"] = sort
        return self.get("channel/videos", params, credits=1, cache_secs=self.SEARCH_CACHE_SECS)

    def channel_search(self, channel: str, q: str, continuation: Optional[str] = None) -> dict[str, Any]:
        params = {"continuation": continuation} if continuation else {"channel": channel, "q": q}
        return self.get("channel/search", params, credits=1, cache_secs=self.SEARCH_CACHE_SECS)

    def playlist_videos(self, playlist: str, continuation: Optional[str] = None) -> dict[str, Any]:
        params = {"continuation": continuation} if continuation else {"playlist": playlist}
        return self.get("playlist/videos", params, credits=1, cache_secs=self.SEARCH_CACHE_SECS)

    def resolve(self, handle_or_url: str) -> dict[str, Any]:
        return self.get("channel/resolve", {"input": handle_or_url}, credits=0,
                        cache_secs=float("inf"))

    def info(self, video_id: str) -> dict[str, Any]:
        return self.get("info", {"video_url": video_id}, credits=0, cache_secs=7 * 24 * 3600)

    def latest(self, channel: str) -> dict[str, Any]:
        """Free RSS: latest ~15 uploads with EXACT published + viewCount (§3.3)."""
        return self.get("channel/latest", {"channel": channel}, credits=0)

    def transcript(self, video_id: str, language: Optional[str] = None) -> dict[str, Any]:
        return self.get("transcript", {"video_url": video_id, "format": "json",
                                       "include_timestamp": "true", "send_metadata": "true",
                                       "language": language}, credits=1)


class Apify(_Base):
    provider = "apify"

    def __init__(self, token: str, *, base: str = APIFY_BASE, sync_timeout_secs: int = 290,
                 async_timeout_secs: int = 1800, poll_secs: float = 5.0, **kw: Any) -> None:
        super().__init__(**kw)
        if not token:
            raise RuntimeError("APIFY_API_TOKEN is not set")
        self._headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                         "Accept": "application/json"}
        self.base = base.rstrip("/")
        self.sync_timeout_secs = sync_timeout_secs
        self.async_timeout_secs = async_timeout_secs
        self.poll_secs = poll_secs

    @staticmethod
    def actor_path(actor: str) -> str:
        """``streamers/youtube-scraper`` → ``streamers~youtube-scraper`` (URL form)."""
        return actor.replace("/", "~")

    def run_items(self, actor: str, run_input: dict[str, Any], *, expected_results: int = 1,
                  force_async: bool = False) -> list[dict[str, Any]]:
        """Run an actor and return its dataset items. Sync first (≤300s), then
        falls back to async run + poll + dataset paging on 408 or when asked."""
        if self.budget is not None:
            self.budget.check(self.provider, expected_results)
        body = json.dumps(run_input)
        ap = self.actor_path(actor)
        if not force_async:
            url = (f"{self.base}/acts/{ap}/run-sync-get-dataset-items"
                   f"?timeout={self.sync_timeout_secs}&memory=1024&clean=true")
            try:
                # a 408 here means "still running after 300s", not a transient
                # failure — fall through to the async path instead of retrying
                resp = self._request("POST", url, self._headers, body, APIFY_SYNC_READ_TIMEOUT,
                                     f"{actor}:sync", 0, no_retry=frozenset({408}))
                items = resp.json()
                if isinstance(items, list):
                    self._record(f"{actor}:results", len(items), 200, 0)
                    return items
                raise ClientError(self.provider, resp.status, "unexpected sync payload")
            except ClientError as exc:
                if exc.status != 408:
                    raise
                log.info("apify sync run exceeded 300s; switching to async")
        run = self._request("POST", f"{self.base}/acts/{ap}/runs?memory=1024",
                            self._headers, body, READ_TIMEOUT, f"{actor}:runs", 0).json()
        run_id = (run or {}).get("data", {}).get("id")
        if not run_id:
            raise ClientError(self.provider, None, "async run did not return an id")
        deadline = self.clock() + self.async_timeout_secs
        status, dataset_id = "RUNNING", None
        while self.clock() < deadline:
            info = self._request("GET", f"{self.base}/actor-runs/{run_id}", self._headers, None,
                                 READ_TIMEOUT, "actor-runs:poll", 0).json()
            data = (info or {}).get("data", {})
            status = data.get("status", "RUNNING")
            dataset_id = data.get("defaultDatasetId")
            if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
                break
            self.sleep(self.poll_secs)
        if status != "SUCCEEDED" or not dataset_id:
            raise ClientError(self.provider, None, f"actor run {run_id} ended {status}")
        items: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self._request("GET", f"{self.base}/datasets/{dataset_id}/items"
                                 f"?offset={offset}&limit=1000&clean=true",
                                 self._headers, None, READ_TIMEOUT, "datasets:items", 0).json()
            if not isinstance(page, list) or not page:
                break
            items.extend(page)
            offset += len(page)
            if len(page) < 1000:
                break
        self._record(f"{actor}:results", len(items), 200, 0)
        return items

    # -- convenience wrappers ------------------------------------------------
    def scrape_videos(self, actor: str, video_urls: list[str]) -> list[dict[str, Any]]:
        if not video_urls:
            return []
        return self.run_items(actor, {
            "startUrls": [{"url": u} for u in video_urls],
            "maxResults": len(video_urls), "maxResultsShorts": 0, "maxResultStreams": 0,
        }, expected_results=len(video_urls))

    def scrape_channel(self, actor: str, channel_url: str, max_results: int = 50) -> list[dict[str, Any]]:
        return self.run_items(actor, {
            "startUrls": [{"url": channel_url}],
            "maxResults": max_results, "maxResultsShorts": 0, "maxResultStreams": 0,
            "sortVideosBy": "NEWEST",
        }, expected_results=max_results)

    def scrape_comments(self, actor: str, video_url: str, max_comments: int = 300) -> list[dict[str, Any]]:
        return self.run_items(actor, {
            "startUrls": [{"url": video_url}],
            "maxComments": max_comments, "sortCommentsBy": "TOP_COMMENTS",
        }, expected_results=max_comments)

    def probe_actor(self, actor: str) -> dict[str, Any]:
        """Cheap existence/auth check used by doctor (no run)."""
        resp = self._request("GET", f"{self.base}/acts/{self.actor_path(actor)}", self._headers,
                             None, READ_TIMEOUT, f"{actor}:probe", 0)
        data = (resp.json() or {}).get("data", {})
        return {"name": data.get("name"), "username": data.get("username"),
                "title": data.get("title")}


# ---------------------------------------------------------------------------
# YouTube Data API v3 — Google's own, free: 10,000 units per day.
# search.list is 100 units; channels / playlistItems / videos are 1 unit per
# call (50 rows). Results are EXACT (views, dates, durations), so rows carry
# ``_exact`` and are stored at precision tier 2.
# Responses are shaped like TranscriptAPI's so the crawler never knows which
# provider answered.
# ---------------------------------------------------------------------------
YOUTUBE_BASE = "https://www.googleapis.com/youtube/v3"
YOUTUBE_DAILY_UNITS = 10000
_ISO_DUR = re.compile(r"P(?:(?P<d>\d+)D)?T?(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?")


def _duration_text(iso: Any) -> Optional[str]:
    """``PT1H2M3S`` -> ``1:02:03``; ``PT4M5S`` -> ``4:05`` (what parse_duration reads)."""
    m = _ISO_DUR.fullmatch(str(iso or ""))
    if not m or not any(m.groupdict().values()):
        return None
    d, h, mi, s = (int(m.group(k) or 0) for k in ("d", "h", "m", "s"))
    h += d * 24
    return f"{h}:{mi:02d}:{s:02d}" if h else f"{mi}:{s:02d}"


def _handle_from(snippet: dict[str, Any]) -> Optional[str]:
    cu = str(snippet.get("customUrl") or "")
    return cu if cu.startswith("@") else None


class YouTubeData(_Base):
    provider = "youtube"
    UNITS = {"search": 100, "videos": 1, "channels": 1, "playlistItems": 1}
    SEARCH_CACHE_SECS = 24 * 3600

    def __init__(self, api_key: str, *, base: str = YOUTUBE_BASE, daily_units: int = YOUTUBE_DAILY_UNITS,
                 reserve_units: int = 300, **kw: Any) -> None:
        super().__init__(**kw)
        if not api_key:
            raise RuntimeError("YOUTUBE_API_KEY is not set")
        # the key travels in a header, never in the URL (URLs reach logs)
        self._headers = {"X-Goog-Api-Key": api_key, "Accept": "application/json"}
        self.base = base.rstrip("/")
        self.daily_units = int(daily_units)
        self.reserve_units = int(reserve_units)
        self._quota_hit = False

    # -- quota ---------------------------------------------------------------
    def units_used_today(self) -> int:
        """Units recorded since the quota day began (midnight Pacific)."""
        if self.conn is None:
            return 0
        from datetime import datetime, timezone
        try:
            from zoneinfo import ZoneInfo
            now_pt = datetime.now(ZoneInfo("America/Los_Angeles"))
            start = now_pt.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        except Exception:  # noqa: BLE001 — no tz database: fall back to UTC day
            start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        row = self.conn.execute("SELECT COALESCE(SUM(credits), 0) AS u FROM api_usage WHERE provider = 'youtube' AND ts >= ?",
                                (start.isoformat(),)).fetchone()
        return int(row["u"] if row else 0)

    def can_afford(self, units: int) -> bool:
        if self._quota_hit:
            return False
        return self.units_used_today() + units <= self.daily_units - self.reserve_units

    # -- plumbing ------------------------------------------------------------
    def get(self, resource: str, params: dict[str, Any], *, cache_secs: Optional[float] = None) -> Any:
        units = self.UNITS.get(resource, 1)
        key = "yt:" + resource + "?" + urllib.parse.urlencode(sorted((k, str(v)) for k, v in params.items() if v is not None))
        if cache_secs is not None and self.conn is not None:
            hit = TranscriptAPI._cached(self, key, None if cache_secs == float("inf") else cache_secs)  # type: ignore[arg-type]
            if hit is not None:
                return hit
        if not self.can_afford(units):
            raise QuotaExhausted(self.provider, 403, f"daily quota nearly spent ({self.units_used_today()} units used today)")
        if self.budget is not None:
            self.budget.check(self.provider, units)
        qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        url = f"{self.base}/{resource}?{qs}"
        try:
            resp = self._request("GET", url, self._headers, None, READ_TIMEOUT, resource, units,
                                 no_retry=frozenset({403}))
        except ClientError as exc:
            if exc.status == 403:
                reason = ""
                try:
                    reason = str((json.loads(exc.body or "{}").get("error") or {}).get("errors", [{}])[0].get("reason") or "")
                except (ValueError, AttributeError, IndexError):
                    pass
                if reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded", "userRateLimitExceeded"):
                    self._quota_hit = True
                    raise QuotaExhausted(self.provider, 403, f"YouTube Data API quota exhausted ({reason}); resets at midnight Pacific")
                if reason == "accessNotConfigured":
                    raise ClientError(self.provider, 403, "YouTube Data API v3 is not enabled for this key's project — "
                                      "enable it in the Google Cloud console (APIs & Services → Library)")
            raise
        data = resp.json()
        if cache_secs is not None and self.conn is not None and resp.status == 200:
            TranscriptAPI._store(self, key, resp.status, resp.body)  # type: ignore[arg-type]
        return data

    # -- lookups ---------------------------------------------------------------
    def _videos(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        ids = [i for i in dict.fromkeys(ids) if i]
        for i in range(0, len(ids), 50):
            data = self.get("videos", {"part": "snippet,statistics,contentDetails", "id": ",".join(ids[i:i + 50]), "maxResults": 50})
            for it in data.get("items") or []:
                out[str(it.get("id"))] = it
        return out

    def _video_row(self, it: dict[str, Any], index: Optional[int] = None) -> dict[str, Any]:
        sn, st, cd = it.get("snippet") or {}, it.get("statistics") or {}, it.get("contentDetails") or {}
        thumbs = [{"url": t.get("url"), "width": t.get("width"), "height": t.get("height")}
                  for t in (sn.get("thumbnails") or {}).values() if isinstance(t, dict)]
        return {"type": "video", "videoId": str(it.get("id")), "channelId": sn.get("channelId"),
                "channelTitle": sn.get("channelTitle"), "channelHandle": None, "title": sn.get("title"),
                "description": sn.get("description"),
                "viewCountText": str(st.get("viewCount")) if st.get("viewCount") is not None else None,
                "publishedTimeText": sn.get("publishedAt"), "lengthText": _duration_text(cd.get("duration")),
                "thumbnails": thumbs, "index": index, "_exact": True}

    def channel(self, ident: str) -> Optional[dict[str, Any]]:
        """channels.list by @handle or UC id (cached forever): id, title, handle,
        subscriberCount (exact), uploads playlist."""
        ident = str(ident or "").strip()
        if not ident:
            return None
        params = {"part": "snippet,statistics,contentDetails"}
        if ident.startswith("UC") and len(ident) == 24:
            params["id"] = ident
        else:
            params["forHandle"] = ident if ident.startswith("@") else "@" + ident
        data = self.get("channels", params, cache_secs=7 * 24 * 3600)
        items = data.get("items") or []
        if not items:
            return None
        it = items[0]
        sn, st, cd = it.get("snippet") or {}, it.get("statistics") or {}, it.get("contentDetails") or {}
        return {"channel_id": str(it.get("id")), "title": sn.get("title"), "handle": _handle_from(sn),
                "subscriberCount": str(st.get("subscriberCount")) if st.get("subscriberCount") is not None else None,
                "uploads": (cd.get("relatedPlaylists") or {}).get("uploads") or ("UU" + str(it.get("id"))[2:]),
                "_exact": True}

    # -- TranscriptAPI-shaped endpoints ------------------------------------------
    def search(self, q: str, type_: str = "video", continuation: Optional[str] = None) -> dict[str, Any]:
        params = {"part": "snippet", "q": q, "type": "channel" if type_ == "channel" else "video",
                  "maxResults": 50, "pageToken": continuation or None}
        if type_ != "channel":
            params["videoDuration"] = "any"
        data = self.get("search", params, cache_secs=self.SEARCH_CACHE_SECS)
        items = data.get("items") or []
        if type_ == "channel":
            ids = [str((it.get("id") or {}).get("channelId") or "") for it in items]
            rows = []
            for i in range(0, len(ids), 50):
                chunk = [c for c in ids[i:i + 50] if c]
                if not chunk:
                    continue
                d = self.get("channels", {"part": "snippet,statistics", "id": ",".join(chunk), "maxResults": 50})
                for it in d.get("items") or []:
                    sn, st = it.get("snippet") or {}, it.get("statistics") or {}
                    rows.append({"type": "channel", "channelId": str(it.get("id")), "title": sn.get("title"),
                                 "handle": _handle_from(sn),
                                 "subscriberCount": str(st.get("subscriberCount")) if st.get("subscriberCount") is not None else None,
                                 "_exact": True})
        else:
            ids = [str((it.get("id") or {}).get("videoId") or "") for it in items]
            full = self._videos(ids)
            rows = [self._video_row(full[i]) for i in ids if i in full]
        nxt = data.get("nextPageToken")
        return {"results": rows, "has_more": bool(nxt), "continuation_token": nxt}

    def channel_videos(self, channel: str, continuation: Optional[str] = None, *,
                       sort: Optional[str] = None, pages: int = 2) -> dict[str, Any]:
        """~100 uploads newest-first per call (two playlist pages + stats),
        with absolute catalogue positions. ``sort`` is ignored: the Data API
        has no cheap popular feed, and whole catalogues are cheap enough to
        page instead."""
        ch = self.channel(channel)
        if not ch:
            return {"results": [], "has_more": False, "continuation_token": None}
        rows: list[dict[str, Any]] = []
        token = continuation
        for _ in range(max(1, pages)):
            data = self.get("playlistItems", {"part": "contentDetails,snippet", "playlistId": ch["uploads"],
                                              "maxResults": 50, "pageToken": token or None})
            items = data.get("items") or []
            ids = [str((it.get("contentDetails") or {}).get("videoId") or "") for it in items]
            pos = {str((it.get("contentDetails") or {}).get("videoId") or ""): (it.get("snippet") or {}).get("position")
                   for it in items}
            full = self._videos(ids)
            for vid in ids:
                if vid in full:
                    rows.append(self._video_row(full[vid], index=pos.get(vid)))
            token = data.get("nextPageToken")
            if not token:
                break
        return {"results": rows, "has_more": bool(token), "continuation_token": token,
                "channel": {"title": ch["title"], "handle": ch["handle"]}}

    def latest(self, channel: str) -> dict[str, Any]:
        """Newest ~15 uploads with exact numbers, shaped like channel/latest."""
        ch = self.channel(channel)
        if not ch:
            return {}
        data = self.get("playlistItems", {"part": "contentDetails", "playlistId": ch["uploads"], "maxResults": 15})
        ids = [str((it.get("contentDetails") or {}).get("videoId") or "") for it in data.get("items") or []]
        full = self._videos(ids)
        results = []
        for vid in ids:
            it = full.get(vid)
            if not it:
                continue
            sn, st = it.get("snippet") or {}, it.get("statistics") or {}
            results.append({"videoId": vid, "title": sn.get("title"), "viewCount": str(st.get("viewCount") or 0),
                            "published": sn.get("publishedAt"), "link": f"https://www.youtube.com/watch?v={vid}",
                            "description": sn.get("description"),
                            "lengthText": _duration_text((it.get("contentDetails") or {}).get("duration"))})
        return {"channel": {"title": ch["title"], "handle": ch["handle"], "channel_id": ch["channel_id"]},
                "results": results}

    def resolve(self, handle_or_url: str) -> dict[str, Any]:
        ident = str(handle_or_url or "").rstrip("/").split("/")[-1]
        ch = self.channel(ident)
        return {"channel_id": ch["channel_id"], "title": ch["title"], "handle": ch["handle"]} if ch else {}


class DataSource:
    """One object the pipeline talks to. Each call goes to the cheapest
    provider that can serve it: discovery (search, channel pages, channel
    sizes) to the YouTube Data API when a key is set and today's quota
    allows, otherwise TranscriptAPI; free snapshots stay on TranscriptAPI's
    free endpoint; transcripts are TranscriptAPI only. When the Data API runs
    out of quota mid-run the call is retried on TranscriptAPI, so a run
    never stops because one provider did."""
    provider = "router"

    def __init__(self, tapi: Optional[TranscriptAPI], yt: Optional[YouTubeData],
                 log: Callable[[str], None] = lambda m: None) -> None:
        if tapi is None and yt is None:
            raise RuntimeError("set YOUTUBE_API_KEY (free) or TRANSCRIPT_API_KEY before running discovery")
        self.tapi, self.yt, self.log = tapi, yt, log
        self.fallbacks = 0
        self._next: dict[str, Optional[str]] = {}

    @property
    def supports_popular(self) -> bool:
        return self.tapi is not None

    def _use_yt(self, units: int, continuation: Optional[str] = None) -> bool:
        if continuation is not None:
            return continuation.startswith("yt:")
        return self.yt is not None and self.yt.can_afford(units)

    def _fell_back(self, what: str, exc: Exception) -> None:
        self.fallbacks += 1
        self.log(f"{what}: YouTube Data API unavailable ({exc}); using TranscriptAPI")

    @staticmethod
    def _tag(data: dict[str, Any]) -> dict[str, Any]:
        tok = data.get("continuation_token")
        if tok:
            data["continuation_token"] = "yt:" + str(tok)
        return data

    def search(self, q: str, type_: str = "video", continuation: Optional[str] = None, *,
               purpose: str = "term") -> dict[str, Any]:
        units = 100 + (2 if type_ == "channel" else 1)
        # recommendation searches (one per outlier video) are the Data API's
        # worst value: 100 units for 50 rows we mostly discard. TranscriptAPI
        # does them for 1 credit, so they go there whenever it is configured.
        if purpose == "recommendation" and self.tapi is not None and continuation is None:
            return self.tapi.search(q, type_, continuation)
        if self._use_yt(units, continuation):
            try:
                return self._tag(self.yt.search(q, type_, continuation[3:] if continuation else None))  # type: ignore[union-attr]
            except QuotaExhausted as exc:
                if self.tapi is None or continuation:
                    raise
                self._fell_back(f"search {q!r}", exc)
        if self.tapi is None:
            raise QuotaExhausted("youtube", 403, "YouTube Data API quota is spent for today and no TRANSCRIPT_API_KEY to fall back to")
        return self.tapi.search(q, type_, continuation)

    def channel_videos(self, channel: str, continuation: Optional[str] = None, *,
                       sort: Optional[str] = None) -> dict[str, Any]:
        if sort == "popular" and self.yt is not None and self.yt.can_afford(8):
            # no popular feed on the Data API — the next ~100 chronological
            # uploads cost about 4 units, so page on instead
            tok = self._next.get(channel)
            if not tok:
                return {"results": [], "has_more": False, "continuation_token": None}
            try:
                data = self.yt.channel_videos(channel, tok)
                self._next[channel] = data.get("continuation_token")
                return self._tag(data)
            except QuotaExhausted as exc:
                if self.tapi is None:
                    raise
                self._fell_back(f"channel {channel} page 2", exc)
            return self.tapi.channel_videos(channel, sort="popular") if self.tapi else {"results": [], "has_more": False}
        if self._use_yt(6, continuation):
            try:
                data = self.yt.channel_videos(channel, continuation[3:] if continuation else None)  # type: ignore[union-attr]
                self._next[channel] = data.get("continuation_token")
                return self._tag(data)
            except QuotaExhausted as exc:
                if self.tapi is None or continuation:
                    raise
                self._fell_back(f"channel {channel}", exc)
        if self.tapi is None:
            raise QuotaExhausted("youtube", 403, "YouTube Data API quota is spent for today and no TRANSCRIPT_API_KEY to fall back to")
        return self.tapi.channel_videos(channel, continuation, sort=sort)

    def latest(self, channel: str) -> dict[str, Any]:
        if self.tapi is not None:                       # free there; 2 units here
            return self.tapi.latest(channel)
        return self.yt.latest(channel)                   # type: ignore[union-attr]

    def resolve(self, handle_or_url: str) -> dict[str, Any]:
        if self.tapi is not None:
            return self.tapi.resolve(handle_or_url)
        return self.yt.resolve(handle_or_url)            # type: ignore[union-attr]

    def info(self, video_id: str) -> dict[str, Any]:
        if self.tapi is None:
            raise ClientError("router", None, "video info needs TRANSCRIPT_API_KEY")
        return self.tapi.info(video_id)

    def transcript(self, video_id: str, language: Optional[str] = None) -> dict[str, Any]:
        if self.tapi is None:
            raise ClientError("router", None, "transcripts need TRANSCRIPT_API_KEY")
        return self.tapi.transcript(video_id, language)
