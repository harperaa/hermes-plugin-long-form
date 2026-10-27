"""Workspace deliverables tree + guarded file read/write.

Python port of the original workspace-tree.ts: surfaces the ``youtube/``
subtree of the plugin workspace as a collapsible tree, reads files (text vs
base64 by extension), and writes text files — all behind the same
workspace-boundary and extension guards as the original. ``insights/`` is
deliberately excluded (that's the Insights page's domain).
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    from . import yti_paths
except ImportError:  # pragma: no cover
    import yti_paths  # type: ignore

SURFACED_ROOTS = ["youtube"]
HIDDEN_ENTRIES = {".DS_Store", ".git", "node_modules"}

TEXT_EXTS = {".md", ".txt", ".json", ".yml", ".yaml", ".log", ".csv"}
EDITABLE_EXTS = {".md", ".txt", ".json", ".yml", ".yaml", ".csv"}
MIME_BY_EXT = {
    ".html": "text/html",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".pdf": "application/pdf",
}
READ_CAP = 25 * 1024 * 1024
WRITE_CAP = 5 * 1024 * 1024


def _mtime_iso(p: Path) -> str:
    return datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat()


def _read_dir(workspace: Path, rel_dir: str) -> list[dict[str, Any]]:
    abs_dir = workspace / rel_dir
    try:
        entries = list(abs_dir.iterdir())
    except OSError:
        return []
    nodes: list[dict[str, Any]] = []
    for entry in entries:
        if entry.name in HIDDEN_ENTRIES:
            continue
        rel = f"{rel_dir}/{entry.name}"
        try:
            if entry.is_dir():
                nodes.append({
                    "kind": "dir", "name": entry.name, "relPath": rel,
                    "mtime": _mtime_iso(entry),
                    "children": _read_dir(workspace, rel),
                })
            elif entry.is_file():
                nodes.append({
                    "kind": "file", "name": entry.name, "relPath": rel,
                    "mtime": _mtime_iso(entry),
                    "size": entry.stat().st_size,
                    "ext": entry.suffix.lower(),
                })
        except OSError:
            continue  # skip unreadable entries
    nodes.sort(key=lambda n: (n["kind"] != "dir", n["name"].lower()))
    return nodes


def build_tree(workspace: Optional[Path] = None) -> list[dict[str, Any]]:
    workspace = workspace or yti_paths.workspace_dir()
    out: list[dict[str, Any]] = []
    for root_name in SURFACED_ROOTS:
        abs_root = workspace / root_name
        if not abs_root.is_dir():
            continue
        out.append({
            "kind": "dir", "name": root_name, "relPath": root_name,
            "mtime": _mtime_iso(abs_root),
            "children": _read_dir(workspace, root_name),
        })
    return out


def resolve_inside_workspace(workspace: Path, rel_path: str) -> Optional[Path]:
    """Same guard as the original: no absolute paths, no escaping the
    workspace, and the top segment must be a surfaced root."""
    if not isinstance(rel_path, str) or not rel_path:
        return None
    if Path(rel_path).is_absolute():
        return None
    abs_workspace = workspace.resolve()
    abs_target = (abs_workspace / rel_path).resolve()
    try:
        rel = abs_target.relative_to(abs_workspace)
    except ValueError:
        return None
    parts = rel.parts
    if not parts or parts[0] not in SURFACED_ROOTS:
        return None
    return abs_target


def read_file(rel_path: str, workspace: Optional[Path] = None) -> dict[str, Any]:
    workspace = workspace or yti_paths.workspace_dir()
    abs_path = resolve_inside_workspace(workspace, rel_path)
    if abs_path is None:
        return {"ok": False, "error": "Path is outside the workspace deliverables area."}
    if not abs_path.exists():
        return {"ok": False, "error": "File not found."}
    if not abs_path.is_file():
        return {"ok": False, "error": "Not a regular file."}
    stat = abs_path.stat()
    if stat.st_size > READ_CAP:
        return {"ok": False, "error": "File exceeds 25 MB cap."}
    ext = abs_path.suffix.lower()
    meta = {"ok": True, "mtime": _mtime_iso(abs_path), "size": stat.st_size,
            "ext": ext}
    if ext in TEXT_EXTS:
        return {**meta, "kind": "text",
                "text": abs_path.read_text(encoding="utf-8", errors="replace")}
    return {**meta, "kind": "binary",
            "base64": base64.b64encode(abs_path.read_bytes()).decode(),
            "mimeType": MIME_BY_EXT.get(ext, "application/octet-stream")}


def write_file(rel_path: str, content: str,
               workspace: Optional[Path] = None) -> dict[str, Any]:
    workspace = workspace or yti_paths.workspace_dir()
    abs_path = resolve_inside_workspace(workspace, rel_path)
    if abs_path is None:
        return {"ok": False, "error": "Path is outside the workspace deliverables area."}
    ext = abs_path.suffix.lower()
    if ext not in EDITABLE_EXTS:
        return {"ok": False, "error": f'Refusing to write file with extension "{ext}".'}
    if not isinstance(content, str):
        return {"ok": False, "error": "content must be a string."}
    if len(content) > WRITE_CAP:
        return {"ok": False, "error": "Refusing to write file larger than 5 MB."}
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(content, encoding="utf-8")
    return {"ok": True, "mtime": _mtime_iso(abs_path),
            "size": abs_path.stat().st_size}
