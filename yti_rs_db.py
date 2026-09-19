"""research.db — schema + upsert helpers for the YouTube Research engine.

Separate SQLite file from ``data.db`` (the followed-channel / VPH store) so
the research schema can follow the spec verbatim without colliding with the
existing ``channels`` / ``videos`` tables. WAL mode, one file:

    <HERMES_HOME>/plugins-data/youtube-insights/research.db

Raw provider payloads are retained in ``raw_json`` columns so every derived
score is recomputable without re-purchasing data (P6).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

try:
    from . import yti_paths
except ImportError:  # pragma: no cover - dashboard api / tests
    import yti_paths  # type: ignore

_LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
  channel_id        TEXT PRIMARY KEY,
  handle            TEXT,
  title             TEXT,
  subscriber_count  INTEGER,
  subscriber_approx INTEGER NOT NULL DEFAULT 0,
  video_count       INTEGER,
  niche             TEXT NOT NULL,
  is_tracked        INTEGER NOT NULL DEFAULT 0,
  first_seen        TEXT NOT NULL,
  last_seen         TEXT NOT NULL,
  raw_json          TEXT
);

CREATE TABLE IF NOT EXISTS videos (
  video_id          TEXT PRIMARY KEY,
  channel_id        TEXT NOT NULL,
  title             TEXT NOT NULL,
  description       TEXT,
  published_at      TEXT,
  published_approx  INTEGER NOT NULL DEFAULT 0,
  published_granularity_days REAL,
  catalog_index     INTEGER,
  duration_seconds  INTEGER,
  is_short          INTEGER,
  views             INTEGER,
  views_approx      INTEGER NOT NULL DEFAULT 0,
  likes             INTEGER,
  comment_count     INTEGER,
  thumbnail_url     TEXT,
  niche             TEXT NOT NULL,
  discovered_via    TEXT,
  discovery_depth   INTEGER,
  precision_tier    INTEGER NOT NULL DEFAULT 1,
  provisional_multiple REAL,
  first_seen        TEXT NOT NULL,
  last_seen         TEXT NOT NULL,
  raw_json          TEXT
);
CREATE INDEX IF NOT EXISTS idx_videos_channel ON videos(channel_id, published_at);
CREATE INDEX IF NOT EXISTS idx_videos_niche ON videos(niche);

CREATE TABLE IF NOT EXISTS video_snapshots (
  video_id      TEXT NOT NULL,
  captured_at   TEXT NOT NULL,
  views         INTEGER NOT NULL,
  likes         INTEGER,
  comment_count INTEGER,
  PRIMARY KEY (video_id, captured_at)
);

CREATE TABLE IF NOT EXISTS transcripts (
  video_id      TEXT PRIMARY KEY,
  language      TEXT,
  is_autogen    INTEGER NOT NULL DEFAULT 0,
  fetched_at    TEXT NOT NULL,
  length_seconds INTEGER,
  text          TEXT NOT NULL,
  segments_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transcript_misses (
  video_id      TEXT PRIMARY KEY,
  checked_at    TEXT NOT NULL,
  reason        TEXT
);

CREATE TABLE IF NOT EXISTS comments (
  comment_id    TEXT PRIMARY KEY,
  video_id      TEXT NOT NULL,
  author        TEXT,
  text          TEXT NOT NULL,
  like_count    INTEGER,
  published_at  TEXT,
  reply_count   INTEGER,
  sentiment     REAL,
  is_early_adopter INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_comments_video ON comments(video_id);

CREATE TABLE IF NOT EXISTS scores (
  video_id             TEXT PRIMARY KEY,
  computed_at          TEXT NOT NULL,
  format_bucket        TEXT NOT NULL,
  age_days             REAL,
  baseline_views       REAL,
  baseline_n           INTEGER,
  multiple             REAL,
  projected_views      REAL,
  projected_multiple   REAL,
  log_mad_z            REAL,
  signal_weight        REAL,
  like_rate            REAL,
  comment_rate         REAL,
  positive_comment_rate REAL,
  vs_percentile        REAL,
  organic_flag         TEXT,
  breakout_watch       INTEGER NOT NULL DEFAULT 0,
  fade_watch           INTEGER NOT NULL DEFAULT 0,
  maturity_source      TEXT,
  raw_only             INTEGER NOT NULL DEFAULT 0,
  class                TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS packaging (
  video_id             TEXT PRIMARY KEY,
  computed_at          TEXT NOT NULL,
  point_count          INTEGER,
  structure_class      TEXT,
  promise_restated_sec REAL,
  has_promise          INTEGER, has_proof INTEGER, has_plan INTEGER, has_persona INTEGER,
  proof_sec            REAL,
  awareness_frame      TEXT,
  title_lowercase      INTEGER,
  filler_rate          REAL,
  sentence_len_cv      REAL,
  delivery_class       TEXT,
  mismatch_risk        INTEGER,
  cta_kind             TEXT,
  cta_position_pct     REAL,
  llm_notes_json       TEXT
);

CREATE TABLE IF NOT EXISTS formats (
  format_id    TEXT PRIMARY KEY,
  kind         TEXT NOT NULL,
  label        TEXT NOT NULL,
  pattern      TEXT,
  skeleton     TEXT,
  slot_names   TEXT,
  psychology   TEXT,
  caution      TEXT,
  known_discriminator TEXT,
  created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS format_matches (
  format_id    TEXT NOT NULL,
  video_id     TEXT NOT NULL,
  slots_json   TEXT,
  PRIMARY KEY (format_id, video_id)
);

CREATE TABLE IF NOT EXISTS format_stats (
  format_id         TEXT PRIMARY KEY,
  computed_at       TEXT NOT NULL,
  n_total           INTEGER NOT NULL DEFAULT 0,
  n_hits            INTEGER NOT NULL DEFAULT 0,
  n_under           INTEGER NOT NULL DEFAULT 0,
  hit_rate          REAL,
  wilson_lb         REAL,
  median_multiple   REAL,
  weighted_hit_rate REAL,
  mean_signal_weight REAL,
  distinct_channels INTEGER,
  distinct_niches   INTEGER,
  niches_json       TEXT,
  target_niche_uses INTEGER NOT NULL DEFAULT 0,
  examples_json     TEXT,
  discriminators_json TEXT
);

CREATE TABLE IF NOT EXISTS channel_profiles (
  channel_id        TEXT PRIMARY KEY,
  computed_at       TEXT NOT NULL,
  n_videos          INTEGER,
  changepoint_date  TEXT,
  changepoint_index INTEGER,
  changepoint_p     REAL,
  changepoint_stat  REAL,
  lift_ratio        REAL,
  cohort_diff_json  TEXT,
  coherence_score   REAL,
  off_topic_hits_json TEXT,
  doubling_down_json TEXT,
  llm_teardown      TEXT
);

CREATE TABLE IF NOT EXISTS crawl_runs (
  run_id       TEXT PRIMARY KEY,
  started_at   TEXT NOT NULL,
  finished_at  TEXT,
  status       TEXT NOT NULL,
  niches_json  TEXT,
  stack_json   TEXT,
  stats_json   TEXT
);

CREATE TABLE IF NOT EXISTS crawl_nodes (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id       TEXT NOT NULL,
  node_type    TEXT NOT NULL,
  node_key     TEXT NOT NULL,
  niche        TEXT NOT NULL,
  depth        INTEGER NOT NULL,
  parent_id    INTEGER,
  status       TEXT NOT NULL,
  yield_count  INTEGER NOT NULL DEFAULT 0,
  created_at   TEXT NOT NULL,
  UNIQUE(run_id, node_type, node_key)
);

CREATE TABLE IF NOT EXISTS api_usage (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  ts         TEXT NOT NULL,
  run_id     TEXT,
  provider   TEXT NOT NULL,
  endpoint   TEXT NOT NULL,
  status     INTEGER,
  credits    INTEGER NOT NULL,
  ms         INTEGER
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON api_usage(ts);

CREATE TABLE IF NOT EXISTS quarantine (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  ts         TEXT NOT NULL,
  provider   TEXT NOT NULL,
  reason     TEXT NOT NULL,
  raw_json   TEXT
);

CREATE TABLE IF NOT EXISTS http_cache (
  cache_key  TEXT PRIMARY KEY,
  fetched_at TEXT NOT NULL,
  status     INTEGER NOT NULL,
  body       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def db_path() -> Path:
    return yti_paths.data_dir() / "research.db"


def connect(path: Optional[Path] = None) -> sqlite3.Connection:
    p = path or db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    with _LOCK:
        conn.executescript(SCHEMA)
    return conn


# -- meta ---------------------------------------------------------------------

def set_meta(conn: sqlite3.Connection, key: str, value: Any) -> None:
    if not isinstance(value, str):
        value = json.dumps(value)
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def get_meta_json(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    raw = get_meta(conn, key)
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return default


# -- generic upsert -------------------------------------------------------------

def _upsert(conn: sqlite3.Connection, table: str, key: str, row: dict[str, Any],
            *, keep_first_seen: bool = True) -> None:
    """Insert or update ``row`` (only the provided, non-None columns on update)."""
    ts = now_iso()
    row = {k: v for k, v in row.items() if v is not None}
    existing = conn.execute(
        f"SELECT 1 FROM {table} WHERE {key} = ?", (row[key],)).fetchone()
    if existing:
        sets = {k: v for k, v in row.items() if k != key and k != "first_seen"}
        sets["last_seen"] = ts
        cols = ", ".join(f"{k} = ?" for k in sets)
        conn.execute(f"UPDATE {table} SET {cols} WHERE {key} = ?",
                     list(sets.values()) + [row[key]])
    else:
        row.setdefault("first_seen", ts)
        row.setdefault("last_seen", ts)
        cols = ", ".join(row.keys())
        marks = ", ".join("?" for _ in row)
        conn.execute(f"INSERT INTO {table}({cols}) VALUES ({marks})", list(row.values()))


def upsert_channel(conn: sqlite3.Connection, ch: dict[str, Any]) -> None:
    if "raw_json" in ch and not isinstance(ch["raw_json"], (str, type(None))):
        ch["raw_json"] = json.dumps(ch["raw_json"])[:20000]
    _upsert(conn, "channels", "channel_id", ch)


def upsert_video(conn: sqlite3.Connection, v: dict[str, Any]) -> bool:
    """Upsert a video. Returns True when the row is new.

    Precision rule: a Tier-2 (exact) row is never downgraded by a later
    Tier-1 (approximate) observation of views or publish date.
    """
    if "raw_json" in v and not isinstance(v["raw_json"], (str, type(None))):
        v["raw_json"] = json.dumps(v["raw_json"])[:20000]
    existing = conn.execute(
        "SELECT precision_tier, views_approx, published_approx FROM videos WHERE video_id = ?",
        (v["video_id"],)).fetchone()
    if existing:
        if existing["views_approx"] == 0 and v.get("views_approx", 0) == 1:
            v.pop("views", None); v.pop("views_approx", None)
        if existing["published_approx"] == 0 and v.get("published_approx", 0) == 1:
            v.pop("published_at", None); v.pop("published_approx", None)
            v.pop("published_granularity_days", None)
        if v.get("precision_tier", 1) < existing["precision_tier"]:
            v["precision_tier"] = existing["precision_tier"]
        _upsert(conn, "videos", "video_id", v)
        return False
    _upsert(conn, "videos", "video_id", v)
    return True


def add_snapshot(conn: sqlite3.Connection, video_id: str, views: int,
                 likes: Optional[int] = None, comment_count: Optional[int] = None,
                 captured_at: Optional[str] = None) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO video_snapshots(video_id, captured_at, views, likes, comment_count)"
        " VALUES (?,?,?,?,?)",
        (video_id, captured_at or today(), int(views), likes, comment_count))


def record_usage(conn: sqlite3.Connection, provider: str, endpoint: str,
                 credits: int, status: Optional[int], ms: Optional[int] = None,
                 run_id: Optional[str] = None) -> None:
    conn.execute(
        "INSERT INTO api_usage(ts, run_id, provider, endpoint, status, credits, ms)"
        " VALUES (?,?,?,?,?,?,?)",
        (now_iso(), run_id, provider, endpoint, status, int(credits), ms))
    conn.commit()


def quarantine(conn: sqlite3.Connection, provider: str, reason: str, payload: Any) -> None:
    try:
        raw = json.dumps(payload)[:20000]
    except (TypeError, ValueError):
        raw = repr(payload)[:20000]
    conn.execute("INSERT INTO quarantine(ts, provider, reason, raw_json) VALUES (?,?,?,?)",
                 (now_iso(), provider, reason, raw))
    conn.commit()


def rows(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]


def one(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> Optional[dict[str, Any]]:
    r = conn.execute(sql, tuple(params)).fetchone()
    return dict(r) if r else None


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    out: dict[str, int] = {}
    for table in ("channels", "videos", "video_snapshots", "transcripts", "comments",
                  "scores", "packaging", "formats", "format_matches", "channel_profiles",
                  "quarantine"):
        out[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    out["tracked_channels"] = conn.execute(
        "SELECT COUNT(*) FROM channels WHERE is_tracked = 1").fetchone()[0]
    out["hits"] = conn.execute(
        "SELECT COUNT(*) FROM scores WHERE class IN ('hit','strong_hit')").fetchone()[0]
    return out
