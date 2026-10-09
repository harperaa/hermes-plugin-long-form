"""Tool handlers for the youtube-insights plugin.

Contract (hermes): ``handler(args: dict, **kwargs) -> str`` — always return a
JSON string, never raise. Network access only in yt_fetch_videos (gated on
TRANSCRIPT_API_KEY via requires_env at registration).
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Optional

try:
    from . import yti_fetcher, yti_insights, yti_analysis, yti_store, yti_paths
except ImportError:  # pragma: no cover
    import yti_fetcher  # type: ignore
    import yti_insights  # type: ignore
    import yti_analysis  # type: ignore
    import yti_store  # type: ignore
    import yti_paths  # type: ignore

# Wired by register() so tools can use the host LLM for borderline dedup.
_llm_judge: Optional[Callable] = None


def set_llm_judge(judge: Optional[Callable]) -> None:
    global _llm_judge
    _llm_judge = judge


def _err(msg: str, **extra: Any) -> str:
    return json.dumps({"error": msg, **extra})


def yt_add_channel(args: dict, **kwargs) -> str:
    handle = str(args.get("handle") or "").strip()
    if not handle:
        return _err("handle required")
    try:
        conn = yti_store.connect()
        channels = yti_store.add_channel(conn, handle)
        conn.close()
        return json.dumps({"ok": True, "channels": channels})
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


def yt_remove_channel(args: dict, **kwargs) -> str:
    handle = str(args.get("handle") or "").strip()
    if not handle:
        return _err("handle required")
    try:
        conn = yti_store.connect()
        channels = yti_store.remove_channel(conn, handle)
        conn.close()
        return json.dumps({"ok": True, "channels": channels})
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


def yt_list_channels(args: dict, **kwargs) -> str:
    try:
        conn = yti_store.connect()
        channels = yti_store.list_channels(conn)
        conn.close()
        return json.dumps({"channels": channels})
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


def yt_fetch_videos(args: dict, **kwargs) -> str:
    api_key = (os.environ.get("TRANSCRIPT_API_KEY") or "").strip()
    if not api_key:
        return _err("TRANSCRIPT_API_KEY is not set — add it to ~/.hermes/.env")
    lookback = args.get("lookback_days")
    try:
        lookback = int(lookback) if lookback else yti_fetcher.DEFAULT_LOOKBACK_DAYS
    except (TypeError, ValueError):
        lookback = yti_fetcher.DEFAULT_LOOKBACK_DAYS
    try:
        conn = yti_store.connect()
        summary = yti_fetcher.run_fetch(conn, api_key, lookback_days=lookback)
        conn.close()
        return json.dumps({"ok": True, **summary})
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


def yt_trending(args: dict, **kwargs) -> str:
    limit = args.get("limit")
    try:
        limit = int(limit) if limit else 20
    except (TypeError, ValueError):
        limit = 20
    try:
        conn = yti_store.connect()
        videos = yti_fetcher.trends_from_db(conn)[:limit]
        conn.close()
        slim = [{k: v for k, v in vid.items() if k != "sparklinePoints"}
                for vid in videos]
        return json.dumps({"videos": slim, "count": len(slim),
                           "workspaceRoot": str(yti_paths.workspace_dir())})
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


def yt_trigger_analysis(args: dict, **kwargs) -> str:
    try:
        conn = yti_store.connect()
        result = yti_analysis.trigger_analysis(
            conn,
            limit=args.get("limit"),
            order_by=str(args.get("order_by") or yti_analysis.DEFAULT_ANALYSIS_ORDER_BY),
        )
        conn.close()
        return json.dumps(result)
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


def yt_add_insight(args: dict, **kwargs) -> str:
    try:
        conn = yti_store.connect()
        result = yti_insights.add_insight(
            conn,
            text=str(args.get("text") or ""),
            category=str(args.get("category") or ""),
            source_video_id=str(args.get("source_video_id")
                                or args.get("sourceVideoId") or ""),
            detail=args.get("detail"),
            context=args.get("context"),
            timestamp_ref=args.get("timestamp_ref") or args.get("timestampRef"),
            judge=_llm_judge,
        )
        conn.close()
        return json.dumps(result)
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


def yt_search_insights(args: dict, **kwargs) -> str:
    query = str(args.get("query") or "")
    if not query:
        return _err("query is required")
    limit = args.get("limit")
    try:
        limit = int(limit) if limit else 10
    except (TypeError, ValueError):
        limit = 10
    try:
        conn = yti_store.connect()
        result = yti_insights.search_insights(
            conn, query=query, category=str(args.get("category") or ""),
            limit=limit,
        )
        conn.close()
        return json.dumps(result)
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


def yt_lint_script(args: dict, **kwargs) -> str:
    """Format-lint one produced script (or a topic folder of them)."""
    raw = str(args.get("path") or "").strip()
    if not raw:
        return _err("path is required")
    try:
        try:
            from . import yti_lint, yti_paths, yti_workspace
        except ImportError:  # standalone import
            import yti_lint, yti_paths, yti_workspace  # type: ignore
        from pathlib import Path
        p = Path(raw).expanduser()
        if not p.is_absolute():
            workspace = yti_paths.workspace_dir()
            resolved = yti_workspace.resolve_inside_workspace(workspace, raw)
            if resolved is None:
                return _err(f"path escapes the workspace: {raw}")
            p = resolved
        if p.is_dir():
            return json.dumps(yti_lint.lint_folder(p))
        return json.dumps(yti_lint.lint_file(p))
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


def yt_research(args: dict, **kwargs) -> str:
    """Niche research engine (crawl → outliers → formats → gaps → teardown).
    Runs the job inline and returns its summary; the dashboard's YouTube
    Research tab shows the same data."""
    try:
        from . import yti_rs_jobs, yti_rs_db, yti_rs_config, yti_rs_formats, yti_rs_report
    except ImportError:  # pragma: no cover
        import yti_rs_jobs, yti_rs_db, yti_rs_config, yti_rs_formats, yti_rs_report  # type: ignore
    action = str(args.get("action") or "status").strip()
    params = args.get("params") or {}
    try:
        if action == "status":
            conn = yti_rs_db.connect()
            try:
                cfg = yti_rs_config.load_config(conn)
                return json.dumps({"counts": yti_rs_db.counts(conn), "niches": [n["name"] for n in cfg["niches"]],
                                   "job": {k: v for k, v in yti_rs_jobs.state().items() if k != "log"},
                                   "secrets": yti_rs_config.get_secrets().present()})
            finally:
                conn.close()
        if action == "doctor":
            return json.dumps(yti_rs_jobs.doctor(run_sample=bool(params.get("runSample"))))
        if action == "brief":
            conn = yti_rs_db.connect()
            try:
                cfg = yti_rs_config.load_config(conn)
                return json.dumps(yti_rs_report.research_brief(
                    conn, cfg, focus_days=params.get("focus_days"), focus_multiple=params.get("focus_multiple")), default=str)
            finally:
                conn.close()
        if action == "gaps":
            conn = yti_rs_db.connect()
            try:
                g = yti_rs_formats.gap_report(conn, yti_rs_config.load_config(conn), params.get("min_wilson"))
                for key in ("gaps", "near_gaps"):
                    g[key] = [{k: v for k, v in r.items() if k not in ("discriminators",)} for r in g[key][:15]]
                return json.dumps(g)
            finally:
                conn.close()
        if action not in yti_rs_jobs.JOBS:
            return _err(f"unknown action {action}")
        lines: list[str] = []
        result = yti_rs_jobs.run_sync(action, params, lines.append)
        return json.dumps({"ok": True, "action": action, "result": result, "log": lines[-40:]}, default=str)
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))
