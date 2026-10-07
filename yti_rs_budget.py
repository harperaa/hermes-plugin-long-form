"""Credit ledger, hard stops and client-side rate limiting (spec §15).

Every API call writes one ``api_usage`` row before returning — including
failures, with ``credits=0`` where the provider does not charge. The
``ytdfs budget`` view reads only that table.
"""
from __future__ import annotations

import random
import sqlite3
import threading
import time
from typing import Any, Callable, Optional

try:
    from . import yti_rs_db
except ImportError:  # pragma: no cover
    import yti_rs_db  # type: ignore


class BudgetExceeded(RuntimeError):
    def __init__(self, provider: str, spent: int, cap: int) -> None:
        super().__init__(f"{provider} budget reached ({spent}/{cap}) — run stopped cleanly; "
                         "resume with the run_id or raise the cap")
        self.provider, self.spent, self.cap = provider, spent, cap


class TokenBucket:
    """Token bucket at ``rate_per_min`` (default 80% of TranscriptAPI's 300/min)."""

    def __init__(self, rate_per_min: float = 240.0, burst: Optional[int] = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.rate = rate_per_min / 60.0
        self.capacity = float(burst or max(1, int(rate_per_min / 4)))
        self.tokens = self.capacity
        self.clock, self.sleep = clock, sleep
        self.last = clock()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = self.clock()
            self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate)
            self.last = now
            if self.tokens >= 1:
                self.tokens -= 1
                return
            wait = (1 - self.tokens) / self.rate
        self.sleep(wait)
        with self._lock:
            self.tokens = max(0.0, self.tokens - 1)
            self.last = self.clock()


class Budget:
    """Per-run ledger + caps. ``caps`` maps provider → max credits for this run."""

    def __init__(self, conn: sqlite3.Connection, run_id: Optional[str] = None,
                 caps: Optional[dict[str, int]] = None) -> None:
        self.conn = conn
        self.run_id = run_id
        self.caps = dict(caps or {})
        self._spent: dict[str, int] = {}
        self._lock = threading.Lock()

    def record(self, provider: str, endpoint: str, credits: int, status: Optional[int],
               ms: Optional[int] = None) -> None:
        with self._lock:
            self._spent[provider] = self._spent.get(provider, 0) + int(credits)
            yti_rs_db.record_usage(self.conn, provider, endpoint, credits, status, ms, self.run_id)

    def spent(self, provider: str) -> int:
        return self._spent.get(provider, 0)

    def ok(self, provider: str, upcoming: int = 1) -> bool:
        cap = self.caps.get(provider)
        if cap is None:
            return True
        return self.spent(provider) + upcoming <= cap

    def check(self, provider: str, upcoming: int = 1) -> None:
        if not self.ok(provider, upcoming):
            raise BudgetExceeded(provider, self.spent(provider), self.caps[provider])

    def remaining(self, provider: str) -> Optional[int]:
        cap = self.caps.get(provider)
        return None if cap is None else max(0, cap - self.spent(provider))

    def summary(self) -> dict[str, Any]:
        return {p: {"spent": self.spent(p), "cap": self.caps.get(p)}
                for p in set(self.caps) | set(self._spent)}


def backoff_delay(attempt: int, base: float = 1.0, cap: float = 30.0,
                  retry_after: Optional[float] = None,
                  rng: Optional[random.Random] = None) -> float:
    """Exponential backoff with full jitter; honours Retry-After when present."""
    if retry_after is not None and retry_after >= 0:
        return min(cap, float(retry_after))
    rng = rng or random
    return min(cap, base * (2 ** attempt)) * (0.5 + rng.random() / 2)


def ledger(conn: sqlite3.Connection, days: int = 30) -> dict[str, Any]:
    """Credits by provider / endpoint / day from ``api_usage`` only."""
    by_day = yti_rs_db.rows(conn, """
        SELECT substr(ts, 1, 10) AS day, provider, SUM(credits) AS credits, COUNT(*) AS calls
        FROM api_usage WHERE ts >= date('now', ?)
        GROUP BY day, provider ORDER BY day DESC, provider""", (f"-{int(days)} days",))
    by_endpoint = yti_rs_db.rows(conn, """
        SELECT provider, endpoint, SUM(credits) AS credits, COUNT(*) AS calls,
               SUM(CASE WHEN status >= 400 OR status IS NULL THEN 1 ELSE 0 END) AS failures
        FROM api_usage WHERE ts >= date('now', ?)
        GROUP BY provider, endpoint ORDER BY credits DESC""", (f"-{int(days)} days",))
    totals = yti_rs_db.rows(conn, """
        SELECT provider, SUM(credits) AS credits, COUNT(*) AS calls
        FROM api_usage GROUP BY provider""")
    today = yti_rs_db.rows(conn, """
        SELECT provider, SUM(credits) AS credits FROM api_usage
        WHERE substr(ts,1,10) = date('now') GROUP BY provider""")
    return {"byDay": by_day, "byEndpoint": by_endpoint, "totals": totals, "today": today,
            "days": days}


# -- what is left ------------------------------------------------------------------------------

def _pt_day_start_utc() -> str:
    from datetime import datetime, timezone
    try:
        from zoneinfo import ZoneInfo
        now_pt = datetime.now(ZoneInfo("America/Los_Angeles"))
        start = now_pt.replace(hour=0, minute=0, second=0, microsecond=0)
        return start.astimezone(timezone.utc).isoformat()
    except Exception:  # noqa: BLE001
        return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def available(conn, secrets, *, youtube_daily_units: int = 10000, apify_fetch=None) -> dict:
    """Remaining balance per provider, as far as each one lets us know.

    - youtube: Google's quota is per Pacific day; remaining = quota - units
      recorded since midnight Pacific.
    - transcriptapi: the API has no balance endpoint, so the operator records
      the balance shown on transcriptapi.com once (meta ``transcriptapi_balance``)
      and the ledger counts down from there.
    - apify: the account's monthly usage and limit, from its limits endpoint.
    """
    import json as _json
    from datetime import datetime, timezone
    out: dict = {}
    used = conn.execute("SELECT COALESCE(SUM(credits), 0) FROM api_usage WHERE provider='youtube' AND ts >= ?",
                        (_pt_day_start_utc(),)).fetchone()[0]
    out["youtube"] = {"configured": bool(getattr(secrets, "youtube_key", "")), "used_today": int(used),
                      "quota": youtube_daily_units, "remaining": max(0, youtube_daily_units - int(used)),
                      "resets": "midnight Pacific"}
    row = conn.execute("SELECT value FROM meta WHERE key='transcriptapi_balance'").fetchone()
    t = {"configured": bool(getattr(secrets, "transcriptapi_key", "")), "balance": None, "set_at": None,
         "charged_since": None, "remaining": None}
    if row:
        try:
            rec = _json.loads(row[0])
            since = rec.get("at")
            charged = conn.execute("SELECT COALESCE(SUM(credits), 0) FROM api_usage WHERE provider='transcriptapi' AND ts >= ?",
                                   (since,)).fetchone()[0]
            t.update({"balance": int(rec.get("balance") or 0), "set_at": since, "charged_since": int(charged),
                      "remaining": max(0, int(rec.get("balance") or 0) - int(charged))})
        except (ValueError, TypeError):
            pass
    out["transcriptapi"] = t
    a = {"configured": bool(getattr(secrets, "apify_token", "")), "usage_usd": None, "limit_usd": None, "cycle_ends": None}
    if a["configured"] and apify_fetch is not None:
        try:
            lim = apify_fetch(secrets.apify_token) or {}
            cur, limits = lim.get("current") or {}, lim.get("limits") or {}
            a.update({"usage_usd": round(float(cur.get("monthlyUsageUsd") or 0), 2),
                      "limit_usd": limits.get("maxMonthlyUsageUsd"),
                      "cycle_ends": (lim.get("monthlyUsageCycle") or {}).get("endAt")})
        except Exception as exc:  # noqa: BLE001 — a balance lookup never breaks the page
            a["error"] = str(exc)[:120]
    out["apify"] = a
    out["checked_at"] = datetime.now(timezone.utc).isoformat()
    return out


def fetch_apify_limits(token: str) -> dict:
    import json as _json
    import urllib.request
    req = urllib.request.Request("https://api.apify.com/v2/users/me/limits",
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return (_json.load(r) or {}).get("data") or {}
