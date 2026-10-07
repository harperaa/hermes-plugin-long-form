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
