"""SQLite store for the long-form plugin.

Single-file database with WAL mode. Tables:

    channels(handle PK, added_at)
    videos(video_id PK, title, channel_handle, channel_slug, published,
           thumbnail, link, view_count, duration_seconds, transcript_path,
           status, created_at, updated_at)
    vph_snapshots(video_id, ts, views)
    insights(id PK, text, detail, category, source_count, first_seen, last_seen)
    insight_sources(insight_id, video_id, context, timestamp_ref, source_url,
                    added_at)
    insights_fts(text, detail)   -- FTS5 external-content index w/ triggers
    analysis_queue(video_id PK, created_at, retries, status, last_error, kanban_task_id)

The insight full-text index replaces the original plugin's qmd
(BM25 + embeddings) dependency: candidate retrieval for dedup and the
search tool both run on FTS5 bm25 ranking.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    from . import yti_paths  # package context (hermes plugin manager)
except ImportError:  # pragma: no cover - direct file context (dashboard api)
    import yti_paths  # type: ignore

_LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
    handle TEXT PRIMARY KEY,
    added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS videos (
    video_id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT 'Unknown',
    channel_handle TEXT NOT NULL DEFAULT '',
    channel_slug TEXT NOT NULL DEFAULT '',
    published TEXT NOT NULL DEFAULT '',
    thumbnail TEXT NOT NULL DEFAULT '',
    link TEXT NOT NULL DEFAULT '',
    view_count INTEGER NOT NULL DEFAULT 0,
    duration_seconds INTEGER,
    transcript_path TEXT,
    status TEXT NOT NULL DEFAULT 'discovered',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vph_snapshots (
    video_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    views INTEGER NOT NULL,
    PRIMARY KEY (video_id, ts)
);
CREATE TABLE IF NOT EXISTS insights (
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    detail TEXT,
    category TEXT NOT NULL,
    source_count INTEGER NOT NULL DEFAULT 1,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS insight_sources (
    insight_id TEXT NOT NULL,
    video_id TEXT NOT NULL,
    context TEXT,
    timestamp_ref TEXT,
    source_url TEXT NOT NULL,
    added_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sources_insight ON insight_sources(insight_id);
CREATE VIRTUAL TABLE IF NOT EXISTS insights_fts USING fts5(
    text, detail, content='insights', content_rowid='rowid'
);
CREATE TRIGGER IF NOT EXISTS insights_ai AFTER INSERT ON insights BEGIN
    INSERT INTO insights_fts(rowid, text, detail)
    VALUES (new.rowid, new.text, new.detail);
END;
CREATE TRIGGER IF NOT EXISTS insights_ad AFTER DELETE ON insights BEGIN
    INSERT INTO insights_fts(insights_fts, rowid, text, detail)
    VALUES ('delete', old.rowid, old.text, old.detail);
END;
CREATE TRIGGER IF NOT EXISTS insights_au AFTER UPDATE ON insights BEGIN
    INSERT INTO insights_fts(insights_fts, rowid, text, detail)
    VALUES ('delete', old.rowid, old.text, old.detail);
    INSERT INTO insights_fts(rowid, text, detail)
    VALUES (new.rowid, new.text, new.detail);
END;
CREATE TABLE IF NOT EXISTS analysis_queue (
    video_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    retries INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    last_error TEXT,
    kanban_task_id TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(path: Optional[Path] = None) -> sqlite3.Connection:
    """Open (and lazily initialize) the plugin database."""
    p = path or yti_paths.db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    with _LOCK:
        conn.executescript(SCHEMA)
        cols = {r["name"] for r in conn.execute(
            "PRAGMA table_info(analysis_queue)")}
        if "kanban_task_id" not in cols:
            conn.execute(
                "ALTER TABLE analysis_queue ADD COLUMN kanban_task_id TEXT")
            conn.commit()
    return conn


# -- channels ----------------------------------------------------------------

def normalize_handle(handle: str) -> str:
    h = (handle or "").strip()
    return h if h.startswith("@") else f"@{h}" if h else ""


def add_channel(conn: sqlite3.Connection, handle: str) -> list[str]:
    h = normalize_handle(handle)
    if not h:
        raise ValueError("handle required")
    conn.execute(
        "INSERT OR IGNORE INTO channels(handle, added_at) VALUES (?, ?)",
        (h, now_iso()),
    )
    conn.commit()
    return list_channels(conn)


def remove_channel(conn: sqlite3.Connection, handle: str) -> list[str]:
    h = normalize_handle(handle)
    conn.execute("DELETE FROM channels WHERE handle = ?", (h,))
    conn.commit()
    return list_channels(conn)


def list_channels(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT handle FROM channels ORDER BY added_at").fetchall()
    return [r["handle"] for r in rows]


# -- videos / snapshots ------------------------------------------------------

def upsert_video(conn: sqlite3.Connection, video: dict[str, Any]) -> None:
    ts = now_iso()
    existing = conn.execute(
        "SELECT video_id FROM videos WHERE video_id = ?", (video["video_id"],)
    ).fetchone()
    if existing:
        sets, params = [], []
        for col in ("title", "channel_handle", "channel_slug", "published",
                    "thumbnail", "link", "view_count", "duration_seconds",
                    "transcript_path", "status"):
            if col in video and video[col] is not None:
                sets.append(f"{col} = ?")
                params.append(video[col])
        sets.append("updated_at = ?")
        params.append(ts)
        params.append(video["video_id"])
        conn.execute(f"UPDATE videos SET {', '.join(sets)} WHERE video_id = ?", params)
    else:
        conn.execute(
            """INSERT INTO videos(video_id, title, channel_handle, channel_slug,
                   published, thumbnail, link, view_count, duration_seconds,
                   transcript_path, status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                video["video_id"],
                video.get("title", "Unknown"),
                video.get("channel_handle", ""),
                video.get("channel_slug", ""),
                video.get("published", ""),
                video.get("thumbnail", ""),
                video.get("link", ""),
                int(video.get("view_count", 0) or 0),
                video.get("duration_seconds"),
                video.get("transcript_path"),
                video.get("status", "discovered"),
                ts,
                ts,
            ),
        )
    conn.commit()


def get_video(conn: sqlite3.Connection, video_id: str) -> Optional[dict[str, Any]]:
    row = conn.execute("SELECT * FROM videos WHERE video_id = ?", (video_id,)).fetchone()
    return dict(row) if row else None


def set_video_status(conn: sqlite3.Connection, video_id: str, status: str) -> None:
    conn.execute(
        "UPDATE videos SET status = ?, updated_at = ? WHERE video_id = ?",
        (status, now_iso(), video_id),
    )
    conn.commit()


def add_snapshot(conn: sqlite3.Connection, video_id: str, views: int,
                 ts: Optional[str] = None) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO vph_snapshots(video_id, ts, views) VALUES (?,?,?)",
        (video_id, ts or now_iso(), int(views)),
    )
    conn.commit()


def snapshots_for(conn: sqlite3.Connection, video_id: str) -> list[tuple[str, int]]:
    rows = conn.execute(
        "SELECT ts, views FROM vph_snapshots WHERE video_id = ? ORDER BY ts",
        (video_id,),
    ).fetchall()
    return [(r["ts"], r["views"]) for r in rows]


# -- meta --------------------------------------------------------------------

def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?,?)", (key, value))
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None
