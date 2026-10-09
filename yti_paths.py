"""Path resolution for the long-form plugin.

All plugin state lives under ``<HERMES_HOME>/plugins-data/long-form/``:

    data.db                     SQLite (videos, snapshots, insights, queue)
    workspace/youtube/...       per-video artifacts (transcript.json/.txt,
                                metadata/<timestamp>.json snapshots)
    workspace/insights/<id>.md  one markdown file per insight (agent-readable)

The workspace layout mirrors the original harper-cmo plugin exactly
(``youtube/{date}/{channel}/{video}/``) so existing skills and scripts that
walk that tree keep working unchanged.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

try:  # Inside a hermes process
    from hermes_constants import get_hermes_home
except ImportError:  # Standalone (tests, dashboard api fallback)
    def get_hermes_home() -> Path:  # type: ignore[misc]
        val = (os.environ.get("HERMES_HOME") or "").strip()
        return Path(val).expanduser() if val else Path.home() / ".hermes"


LEGACY_DATA_DIR_NAME = "youtube-insights"   # the plugin's id until 2026-10-09


def data_dir() -> Path:
    """``<HERMES_HOME>/plugins-data/long-form``. The plugin was called
    youtube-insights until 2026-10-09; an existing data folder under the old
    name is moved here once, and a symlink is left behind so anything still
    reading the old path keeps working."""
    base = get_hermes_home() / "plugins-data"
    d = base / "long-form"
    legacy = base / LEGACY_DATA_DIR_NAME
    if not d.exists() and legacy.is_dir() and not legacy.is_symlink():
        base.mkdir(parents=True, exist_ok=True)
        legacy.rename(d)
        try:
            legacy.symlink_to("long-form")
        except OSError:
            pass
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return data_dir() / "data.db"


def workspace_dir() -> Path:
    d = data_dir() / "workspace"
    d.mkdir(parents=True, exist_ok=True)
    return d


def youtube_dir() -> Path:
    d = workspace_dir() / "youtube"
    d.mkdir(parents=True, exist_ok=True)
    return d


def insights_dir() -> Path:
    d = workspace_dir() / "insights"
    d.mkdir(parents=True, exist_ok=True)
    return d


def sanitize(text: str) -> str:
    """Slug helper — identical semantics to the original plugin's sanitize()."""
    out = re.sub(r"[^a-z0-9-]+", "-", (text or "").lower())
    out = re.sub(r"-+", "-", out).strip("-")
    return out[:80]
