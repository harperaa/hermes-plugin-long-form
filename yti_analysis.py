"""Analysis fan-out queue + completion validator.

Port of the original runTriggerAnalysis, re-expressed for hermes: instead of
creating paperclip issues assigned to a Researcher agent, trigger-analysis
selects up to ``limit`` transcribed-but-unanalyzed videos (ordered by "vph"
highest-signal-first, or "oldest" FIFO), marks them 'analyzing', enqueues
them, and returns a work item per video whose ``instructions`` text tells the
agent exactly what the original issue description did — run the
youtube-video-analyst skill on the transcript, save analysis.md next to it,
and record 10-15 insights — except insights are recorded with the native
``yt_add_insight`` tool instead of unauthenticated curl calls.

The completion validator preserves the original acceptance gate: analysis.md
exists AND >= MIN_INSIGHTS insights recorded for the video; otherwise retry
up to MAX_RETRIES then mark failed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

try:
    from . import yti_paths, yti_store, yti_fetcher
except ImportError:  # pragma: no cover
    import yti_paths  # type: ignore
    import yti_store  # type: ignore
    import yti_fetcher  # type: ignore

DEFAULT_ANALYSIS_LIMIT = 20
ANALYSIS_ORDER_BY = ("vph", "oldest")
DEFAULT_ANALYSIS_ORDER_BY = "vph"
MIN_INSIGHTS = 10
MAX_RETRIES = 2

ANALYST_SKILLS = ["youtube-insights:youtube-video-analyst"]
_OPEN_KANBAN_STATUSES = {"triage", "todo", "scheduled", "ready", "claimed",
                         "in_progress", "in-progress", "review", "blocked",
                         "running", "in_review"}


def _kanban():
    """Return hermes_cli.kanban_db, or None when running outside hermes.

    Upstream split kanban_db into modules (Sep–Oct 2026): ``connect_closing``
    moved to ``kanban_db_connect`` and ``dispatch_once`` to
    ``kanban_db_dispatch`` and are no longer re-exported. The facade below
    resolves each name from wherever the installed Hermes keeps it, so the
    Artifacts tab's generation task works on either shape."""
    try:
        from hermes_cli import kanban_db
    except ImportError:  # pragma: no cover - exercised via monkeypatch in tests
        return None
    needed = ("connect_closing", "create_task", "get_task", "dispatch_once")
    if all(hasattr(kanban_db, n) for n in needed):
        return kanban_db

    class _Facade:
        pass
    kb = _Facade()
    for name in needed:
        fn = getattr(kanban_db, name, None)
        if fn is None:
            for mod in ("kanban_db_connect", "kanban_db_dispatch", "kanban_db_workspace", "kanban_db_notify"):
                try:
                    m = __import__(f"hermes_cli.{mod}", fromlist=[name])
                except ImportError:
                    continue
                fn = getattr(m, name, None)
                if fn is not None:
                    break
        if fn is None:
            return None
        setattr(kb, name, fn)
    return kb


def _kanban_task_open(kb, conn_kb, task_id: str) -> bool:
    try:
        task = kb.get_task(conn_kb, task_id)
    except Exception:
        return False
    if task is None:
        return False
    status = getattr(task, "status", None) or (
        task.get("status") if isinstance(task, dict) else None)
    return str(status) in _OPEN_KANBAN_STATUSES


def kick_dispatcher(max_spawn: int = 1) -> bool:
    """One dispatcher pass NOW so freshly routed analysis tasks start without
    waiting for the gateway's next 60s tick. Board-locked and best-effort."""
    try:
        kb = _kanban()
        with kb.connect_closing() as conn:
            kb.dispatch_once(
                conn,
                max_spawn=max_spawn,
                default_assignee=resolve_kanban_assignee(),
            )
        return True
    except Exception:
        return False


def resolve_kanban_assignee() -> str:
    """kanban.default_assignee from hermes config, else the base profile.

    Tasks created unassigned sit on the board flagged NEEDS ASSIGNEE and the
    dispatcher never claims them — every task we create must be born assigned.
    """
    try:
        from hermes_cli.config import load_config
        val = ((load_config() or {}).get("kanban", {}) or {}).get("default_assignee")
        if isinstance(val, str) and val.strip():
            return val.strip()
    except Exception:
        pass
    return "default"


def analysis_task_title(video_title: str) -> str:
    return f"Analyze: {video_title}"


def create_analysis_kanban_task(conn, item: dict[str, Any]) -> Optional[str]:
    """Create one kanban task for a queued work item, deduping against a
    still-open task recorded on the queue row. Returns the kanban task id,
    or None when kanban is unavailable (graceful fallback)."""
    kb = _kanban()
    if kb is None:
        return None
    row = conn.execute(
        "SELECT kanban_task_id FROM analysis_queue WHERE video_id = ?",
        (item["videoId"],),
    ).fetchone()
    existing = row["kanban_task_id"] if row else None
    try:
        with kb.connect_closing() as conn_kb:
            if existing and _kanban_task_open(kb, conn_kb, existing):
                return existing
            task_id = kb.create_task(
                conn_kb,
                title=analysis_task_title(item["title"]),
                body=item["instructions"],
                assignee=resolve_kanban_assignee(),
                created_by="youtube-insights",
                workspace_kind="scratch",
                skills=list(ANALYST_SKILLS),
            )
    except Exception:
        return None
    conn.execute(
        "UPDATE analysis_queue SET kanban_task_id = ? WHERE video_id = ?",
        (task_id, item["videoId"]),
    )
    conn.commit()
    return task_id


def _analysis_path(transcript_path: str, workspace: Path) -> Path:
    return (workspace / transcript_path).parent / "analysis.md"


def work_item_instructions(video: dict[str, Any], transcript_abs: Path,
                           analysis_abs: Path) -> str:
    return "\n".join([
        "## MANDATORY: Follow these instructions EXACTLY. Do NOT improvise.",
        "",
        f"**Video:** {video['title']}",
        f"**Channel:** {video.get('author') or video.get('channel_handle', '')}",
        f"**Video ID:** {video['videoId'] if 'videoId' in video else video['video_id']}",
        f"**Transcript:** {transcript_abs}",
        f"**Analysis output:** {analysis_abs}",
        "",
        "## Phase 1: Analyze",
        "Load the `youtube-insights:youtube-video-analyst` skill and run it on "
        "the transcript.",
        f"Save the output to: {analysis_abs}",
        f'Verify it exists: `test -f "{analysis_abs}" && echo VERIFIED`',
        "",
        "## Phase 2: Extract Insights via the yt_add_insight tool",
        "Extract 10-15 insights and call the `yt_add_insight` tool for EACH ONE "
        "with fields:",
        '  text: "10-20 word generalizable principle"',
        '  detail: "2-3 sentences with specific context"',
        "  category: one of strategy|technical|creativity|productivity|business|psychology|trend|career",
        f"  source_video_id: \"{video['videoId'] if 'videoId' in video else video['video_id']}\"",
        '  context: "direct quote from transcript"',
        '  timestamp_ref: "MM:SS"',
        "",
        "## CRITICAL RULES — VIOLATION MEANS TASK FAILURE",
        "- You MUST call the yt_add_insight tool for each insight. It is the "
        "ONLY way to store insights.",
        "- Do NOT write insight files to disk yourself. Do NOT create .jsonl "
        "files or category folders.",
        "- This work item is complete ONLY when analysis.md exists AND "
        "yt_add_insight was called 10+ times.",
        "- The completion validator WILL re-queue this video (max 2 retries) "
        "if these conditions are not met.",
    ])


def trigger_analysis(
    conn,
    *,
    limit: Optional[int] = None,
    order_by: str = DEFAULT_ANALYSIS_ORDER_BY,
    workspace: Optional[Path] = None,
) -> dict[str, Any]:
    limit = int(limit) if isinstance(limit, (int, float)) and limit and limit > 0 \
        else DEFAULT_ANALYSIS_LIMIT
    if order_by not in ANALYSIS_ORDER_BY:
        order_by = DEFAULT_ANALYSIS_ORDER_BY
    workspace = workspace or yti_paths.workspace_dir()

    videos = yti_fetcher.trends_from_db(conn)
    if order_by == "oldest":
        videos.sort(key=lambda v: v.get("published") or "")

    queued: list[dict[str, Any]] = []
    for video in videos:
        if len(queued) >= limit:
            break
        state = yti_store.get_video(conn, video["videoId"])
        if not state or state["status"] != "transcribed":
            continue
        if not state.get("transcript_path"):
            continue
        transcript_abs = workspace / state["transcript_path"]
        analysis_abs = _analysis_path(state["transcript_path"], workspace)

        yti_store.set_video_status(conn, video["videoId"], "analyzing")
        conn.execute(
            """INSERT OR REPLACE INTO analysis_queue
               (video_id, created_at, retries, status, kanban_task_id)
               VALUES (?, ?, COALESCE((SELECT retries FROM analysis_queue
                                       WHERE video_id = ?), 0), 'pending',
                       (SELECT kanban_task_id FROM analysis_queue
                        WHERE video_id = ?))""",
            (video["videoId"], yti_store.now_iso(), video["videoId"],
             video["videoId"]),
        )
        conn.commit()
        item = {
            "videoId": video["videoId"],
            "title": video["title"],
            "vph": video["vph"],
            "transcript": str(transcript_abs),
            "analysisOutput": str(analysis_abs),
            "instructions": work_item_instructions(video, transcript_abs,
                                                   analysis_abs),
        }
        kanban_id = create_analysis_kanban_task(conn, item)
        if kanban_id:
            item["kanbanTaskId"] = kanban_id
        queued.append(item)

    yti_store.set_meta(conn, "last_analysis_run", yti_store.now_iso())
    routed = sum(1 for q in queued if q.get("kanbanTaskId"))
    if routed:
        # Start the first worker immediately; the gateway ticker (which
        # honours the configured concurrency caps) drains the rest.
        kick_dispatcher(max_spawn=1)
    return {"triggered": len(queued), "limit": limit, "orderBy": order_by,
            "kanbanRouted": routed, "items": queued}


def validate_analysis(conn, video_id: str,
                      workspace: Optional[Path] = None) -> dict[str, Any]:
    """Acceptance gate: analysis.md exists AND >= MIN_INSIGHTS recorded."""
    try:
        from . import yti_insights
    except ImportError:  # pragma: no cover
        import yti_insights  # type: ignore

    workspace = workspace or yti_paths.workspace_dir()
    state = yti_store.get_video(conn, video_id)
    if not state:
        return {"ok": False, "error": f"unknown video {video_id}"}

    analysis_ok = False
    if state.get("transcript_path"):
        analysis_ok = _analysis_path(state["transcript_path"], workspace).exists()
    insight_count = yti_insights.count_insights_for_video(conn, video_id)
    passed = analysis_ok and insight_count >= MIN_INSIGHTS

    row = conn.execute(
        "SELECT retries FROM analysis_queue WHERE video_id = ?", (video_id,)
    ).fetchone()
    retries = row["retries"] if row else 0

    if passed:
        conn.execute(
            "UPDATE analysis_queue SET status = 'done' WHERE video_id = ?",
            (video_id,),
        )
        yti_store.set_video_status(conn, video_id, "analyzed")
        conn.commit()
        return {"ok": True, "analysis": analysis_ok, "insights": insight_count}

    if retries < MAX_RETRIES:
        conn.execute(
            """UPDATE analysis_queue SET retries = retries + 1,
                   status = 'pending',
                   last_error = ? WHERE video_id = ?""",
            (f"analysis={analysis_ok} insights={insight_count}", video_id),
        )
        yti_store.set_video_status(conn, video_id, "transcribed")
        conn.commit()
        return {"ok": False, "retry": retries + 1, "analysis": analysis_ok,
                "insights": insight_count}

    conn.execute(
        "UPDATE analysis_queue SET status = 'failed', last_error = ? "
        "WHERE video_id = ?",
        (f"exhausted retries: analysis={analysis_ok} insights={insight_count}",
         video_id),
    )
    conn.commit()
    return {"ok": False, "failed": True, "analysis": analysis_ok,
            "insights": insight_count}


def handle_kanban_completion(conn, kanban_task_id: str,
                             workspace: Optional[Path] = None,
                             ) -> Optional[dict[str, Any]]:
    """kanban_task_completed hook path: validate the finished analysis task's
    video; on a retryable failure, open a fresh kanban retry task. Returns
    None when the completed task is not one of ours."""
    row = conn.execute(
        "SELECT video_id FROM analysis_queue WHERE kanban_task_id = ?",
        (kanban_task_id,),
    ).fetchone()
    if not row:
        return None
    video_id = row["video_id"]
    workspace = workspace or yti_paths.workspace_dir()
    result = validate_analysis(conn, video_id, workspace=workspace)

    if result.get("retry"):
        state = yti_store.get_video(conn, video_id)
        if state and state.get("transcript_path"):
            transcript_abs = workspace / state["transcript_path"]
            analysis_abs = _analysis_path(state["transcript_path"], workspace)
            video = {"videoId": video_id,
                     "title": state.get("title") or video_id,
                     "author": state.get("author") or "",
                     "vph": state.get("vph") or 0}
            item = {
                "videoId": video_id,
                "title": video["title"],
                "instructions": work_item_instructions(
                    video, transcript_abs, analysis_abs),
            }
            # Clear the stale id so dedupe doesn't resurrect the closed task.
            conn.execute(
                "UPDATE analysis_queue SET kanban_task_id = NULL "
                "WHERE video_id = ?", (video_id,))
            conn.commit()
            retry_task = create_analysis_kanban_task(conn, item)
            if retry_task:
                result["retryKanbanTaskId"] = retry_task
    result["videoId"] = video_id
    return result
