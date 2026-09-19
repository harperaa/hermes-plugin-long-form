"""youtube-insights — hermes plugin entry point.

YouTube competitive intelligence: channel tracking, transcript fetch,
VPH/trend analytics, and a deduplicated insight knowledge base. Content
production is intentionally out of scope (pair with a marketing plugin such
as digital-marketing-pro for that).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

try:
    from . import schemas, tools, cli, yti_insights
except ImportError:  # imported outside package context (tests, tooling)
    import schemas, tools, cli, yti_insights  # type: ignore

logger = logging.getLogger(__name__)

_TOOLS = (
    ("yt_add_channel", schemas.YT_ADD_CHANNEL, tools.yt_add_channel, None),
    ("yt_remove_channel", schemas.YT_REMOVE_CHANNEL, tools.yt_remove_channel, None),
    ("yt_list_channels", schemas.YT_LIST_CHANNELS, tools.yt_list_channels, None),
    ("yt_fetch_videos", schemas.YT_FETCH_VIDEOS, tools.yt_fetch_videos,
     ["TRANSCRIPT_API_KEY"]),
    ("yt_trending", schemas.YT_TRENDING, tools.yt_trending, None),
    ("yt_trigger_analysis", schemas.YT_TRIGGER_ANALYSIS,
     tools.yt_trigger_analysis, None),
    ("yt_add_insight", schemas.YT_ADD_INSIGHT, tools.yt_add_insight, None),
    ("yt_search_insights", schemas.YT_SEARCH_INSIGHTS,
     tools.yt_search_insights, None),
    ("yt_lint_script", schemas.YT_LINT_SCRIPT, tools.yt_lint_script, None),
    ("yt_research", schemas.YT_RESEARCH, tools.yt_research, None),
)


def _cmd_yt(raw_args: str) -> str:
    """/yt — trends + insight-base summary."""
    from . import yti_store, yti_fetcher

    conn = yti_store.connect()
    channels = yti_store.list_channels(conn)
    videos = yti_fetcher.trends_from_db(conn)[:10]
    stats = yti_insights.insight_stats(conn)
    conn.close()

    lines = [f"**YouTube Insights** — {len(channels)} channels tracked, "
             f"{stats['totalInsights']} insights from {stats['totalSources']} videos."]
    if videos:
        lines.append("")
        lines.append("Top videos by VPH:")
        for v in videos:
            lines.append(f"- {v['vph']:>6} vph · {v['title'][:70]} "
                         f"({v['author']}, {v['trendDirection']})")
    else:
        lines.append("No videos tracked yet — add channels with yt_add_channel "
                     "and run yt_fetch_videos.")
    return "\n".join(lines)


def _cmd_yt_analyze(raw_args: str) -> str:
    """/yt-analyze — queue analysis work items now."""
    result = json.loads(tools.yt_trigger_analysis({}))
    if result.get("error"):
        return f"trigger-analysis failed: {result['error']}"
    n = result.get("triggered", 0)
    if not n:
        return ("No videos are waiting for analysis. Run yt_fetch_videos "
                "first (or every video is already analyzed).")
    titles = "\n".join(f"- {i['title']}" for i in result.get("items", []))
    return (f"Queued {n} video(s) for analysis:\n{titles}\n\n"
            "Work each item by following its 'instructions' field from "
            "yt_trigger_analysis (analyst skill → analysis.md → yt_add_insight).")


def _on_kanban_task_completed(task_id: str, **kwargs) -> None:
    """Validate an 'Analyze: <video>' task's deliverables when its worker
    completes; open a retry task (max 2) when the acceptance gate fails."""
    try:
        from . import yti_store, yti_analysis
    except ImportError:  # pragma: no cover
        import yti_store, yti_analysis  # type: ignore
    try:
        conn = yti_store.connect()
        result = yti_analysis.handle_kanban_completion(conn, task_id)
        if result is None:
            # Not an analysis task — maybe a script task (generation/topic/
            # iterate): those must lint clean, not just complete.
            try:
                from . import yti_generate
            except ImportError:  # pragma: no cover
                import yti_generate  # type: ignore
            script_result = yti_generate.handle_script_completion(conn, task_id)
            if script_result is None:
                # Produce task? Sweep scratch files out of assets/ so the
                # Artifacts tab shows only images.
                sweep = yti_generate.handle_produce_completion(conn, task_id)
                if sweep is not None:
                    conn.close()
                    if sweep.get("swept"):
                        logger.info("produce %s: swept %s scratch files from "
                                    "assets/", task_id, sweep["swept"])
                    return
                # Maybe an outline->presentation expansion: validate
                # completeness (every outline item became a beat).
                present_result = yti_generate.handle_present_completion(
                    conn, task_id)
                conn.close()
                if present_result is None:
                    return
                if present_result.get("fixTask"):
                    logger.warning(
                        "presentation %s incomplete -> fix task %s (%s)",
                        task_id, present_result["fixTask"],
                        "; ".join(present_result.get("problems") or []))
                elif present_result.get("exhausted"):
                    logger.error(
                        "presentation %s still incomplete after retries: %s",
                        task_id,
                        "; ".join(present_result.get("problems") or []))
                else:
                    logger.info("presentation %s validated complete", task_id)
                return
            conn.close()
            if script_result.get("clean"):
                logger.info("script task %s lints clean", task_id)
            elif script_result.get("retry"):
                logger.warning(
                    "script task %s failed the format linter (%s findings) — "
                    "fix task #%s opened (retry %s)", task_id,
                    script_result.get("findings"),
                    script_result.get("fixTaskId"), script_result.get("retry"))
            elif script_result.get("exhausted"):
                logger.error("script task %s exhausted lint retries: %s "
                             "findings remain", task_id,
                             script_result.get("findings"))
            return
        conn.close()
        if result.get("ok"):
            logger.info("analysis task %s validated: %s insights",
                        task_id, result.get("insights"))
        elif result.get("retry"):
            logger.warning("analysis task %s failed validation — retry #%s "
                           "opened (%s)", task_id, result.get("retry"),
                           result.get("retryKanbanTaskId"))
        else:
            logger.error("analysis task %s exhausted retries: %s",
                         task_id, result)
    except Exception:  # noqa: BLE001 - hooks must never break the worker
        logger.exception("kanban completion handling failed for %s", task_id)


def register(ctx) -> None:
    # Tools
    for name, schema, handler, req_env in _TOOLS:
        ctx.register_tool(
            name=name,
            toolset="youtube_insights",
            schema=schema,
            handler=handler,
            requires_env=req_env,
        )

    # Borderline-dedup judge on the host LLM (best-effort; never required)
    try:
        llm = ctx.llm

        def _complete_text(prompt: str) -> str:
            result = llm.complete([{"role": "user", "content": prompt}],
                                  max_tokens=50, purpose="insight-dedup")
            return getattr(result, "text", "") or ""

        tools.set_llm_judge(yti_insights.make_llm_judge(_complete_text))
    except Exception:  # pragma: no cover - judge is optional
        tools.set_llm_judge(None)

    # Analysis tasks are dispatched through kanban; validate on completion.
    ctx.register_hook("kanban_task_completed", _on_kanban_task_completed)

    # Slash commands
    ctx.register_command("yt", handler=_cmd_yt,
                         description="YouTube trends + insight summary")
    ctx.register_command("yt-analyze", handler=_cmd_yt_analyze,
                         description="Queue transcribed videos for insight extraction")

    # CLI subcommand: hermes youtube-insights {setup-cron,status,reindex}
    ctx.register_cli_command(
        "youtube-insights",
        help="YouTube intelligence utilities (setup-cron, status, reindex)",
        setup_fn=cli.setup,
        handler_fn=cli.handle,
    )

    # Bundled skills → youtube-insights:<name>
    skills_dir = Path(__file__).parent / "skills"
    if skills_dir.is_dir():
        for child in sorted(skills_dir.iterdir()):
            skill_md = child / "SKILL.md"
            if child.is_dir() and skill_md.exists():
                try:
                    ctx.register_skill(child.name, skill_md)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("skill %s failed to register: %s",
                                   child.name, exc)
