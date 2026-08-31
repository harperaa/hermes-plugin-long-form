"""Single-video content generation — the Trends page ✨ button.

Port of the paperclip harper-cmo "Generate similar video script" flow onto
the hermes chat-only worker architecture: one kanban task per click (born
assigned, priority, instant dispatch — same lifecycle as Value Creator
steps), the worker runs gap-finder Mode A + content-creator itself, the
scripts land in the plugin workspace (surfaced on the Artifacts tab) AND as
kanban attachments, and the row's ✨ / spinner / ↗ states derive from the
task exactly like paperclip's issue states (open → spinner, 30-minute
staleness window, done → re-generate + review link).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    from . import yti_fetcher, yti_paths, yti_store
    from .yti_analysis import (
        _kanban,
        _kanban_task_open,
        kick_dispatcher,
        resolve_kanban_assignee,
    )
except ImportError:  # standalone import (dashboard plugin_api path)
    import yti_fetcher  # type: ignore
    import yti_paths  # type: ignore
    import yti_store  # type: ignore
    from yti_analysis import (  # type: ignore
        _kanban,
        _kanban_task_open,
        kick_dispatcher,
        resolve_kanban_assignee,
    )

GENERATION_META_KEY = "generation_tasks"
STALE_MINUTES = 30
GENERATION_SKILLS = (
    "youtube-insights:youtube-gap-finder",
    "youtube-insights:youtube-content-creator",
    "youtube-insights:youtube-video-analyst",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    return s[:60] or "video"


def generation_task_title(video_title: str) -> str:
    return f"Video Script: {video_title}"


def _load_map(conn) -> dict[str, Any]:
    raw = yti_store.get_meta(conn, GENERATION_META_KEY)
    try:
        data = json.loads(raw) if raw else {}
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def _save_map(conn, mapping: dict[str, Any]) -> None:
    yti_store.set_meta(conn, GENERATION_META_KEY, json.dumps(mapping))


def _build_brief(video: dict[str, Any], workspace: Path) -> str:
    video_id = video["video_id"]
    title = video.get("title") or video_id
    url = video.get("link") or f"https://www.youtube.com/watch?v={video_id}"
    transcript_rel = video.get("transcript_path") or ""
    transcript_abs = (workspace / transcript_rel) if transcript_rel else None
    transcript_exists = bool(transcript_abs and transcript_abs.exists())
    analysis_abs = transcript_abs.parent / "analysis.md" if transcript_abs else None
    analysis_exists = bool(analysis_abs and analysis_abs.exists())

    today = _now().strftime("%Y-%m-%d")
    out_dir = workspace / "youtube" / today / "recommended" / _slug(title)

    if analysis_exists:
        step0 = [
            "### Step 0 — Prerequisites (already satisfied — skip)",
            f"`analysis.md` is already present at {analysis_abs}. Proceed to Step 1.",
        ]
    elif transcript_exists:
        step0 = [
            "### Step 0 — Prerequisite: generate analysis.md (run this FIRST, yourself)",
            f"`analysis.md` is not yet present. The transcript IS present at",
            f"{transcript_abs}. Run the `youtube-insights:youtube-video-analyst`",
            f"skill on it and save the output to exactly {analysis_abs}. Verify",
            f'with `test -f "{analysis_abs}" && echo VERIFIED`, then continue.',
            "Do NOT stop after Step 0 because analysis.md now exists — that is",
            "the expected state; Steps 1-3 are the actual deliverable.",
        ]
    else:
        step0 = [
            "### Step 0 — Blocked: transcript missing",
            "Neither `analysis.md` nor a transcript exists for this video yet",
            "(the intelligence-refresh job has not fetched it). Post a kanban",
            "comment explaining the block, then block this task with kind",
            "`needs_input` and reason 'transcript missing: waiting on the next",
            "intelligence refresh'. Do NOT invent content without the source.",
        ]

    return "\n".join([
        "## MANDATORY: Generate similar-but-unique content based on this ONE video.",
        "",
        f"**Source video:** {title}",
        f"**Channel:** {video.get('channel_name') or ''}"
        f"{' (' + video['channel_handle'] + ')' if video.get('channel_handle') else ''}",
        f"**Video ID:** {video_id}",
        f"**Video URL:** {url}",
        f"**Video workspace directory:** {transcript_abs.parent if transcript_abs else '_not yet available_'}",
        f"**Transcript:** {transcript_abs or '_not yet available_'}{' ✓' if transcript_exists else ' (missing)'}",
        f"**Analysis:** {analysis_abs or '_n/a_'}{' ✓' if analysis_exists else ' (missing — Step 0)'}",
        f"**Output directory:** {out_dir}",
        "",
        "You do ALL steps yourself in this session — no subtasks, no delegation.",
        "Work autonomously; only block (needs_input) if Step 0 says so.",
        "",
        *step0,
        "",
        "### Step 0.5 — Company context (ICP grounding)",
        "Read $HERMES_HOME/plugins-data/ai-cyber-value-creator/",
        "company-context.md (default HERMES_HOME ~/.hermes; /opt/data in",
        "the container). If present, extract the ideal customer (ICP),",
        "their current problems, and the positioning/voice — every concept",
        "and script below is written FOR that ICP: the straight take",
        "teaches their problem directly, the hot take confronts what they",
        "believe or fear about it, and the contrarian take argues against",
        "the prevailing advice the source video gives them. Prioritize",
        "what the source video LEAVES UNSAID that this ICP needs, so the",
        "scripts break through with a unique point of view and voice. If",
        "the file is missing, note it in SUMMARY.md and proceed.",
        "",
        "### Step 1 — Concepts (gap-finder Mode A)",
        "Run the `youtube-insights:youtube-gap-finder` skill in **Mode A",
        "(Single-Source Video)** — its dedicated branch. Do NOT run the",
        "workspace sweep. Pass explicitly:",
        f"  - Source video URL: {url}",
        f"  - Source video ID: {video_id}",
        f"  - Source analysis.md: {analysis_abs or '(from Step 0)'}",
        f"  - Output directory: {out_dir}/",
        "Also call `yt_search_insights` for the video's main themes so concepts",
        "cite the knowledge base. Mode A output is EXACTLY 3 files:",
        f"  - {out_dir}/concepts.md",
        f"  - {out_dir}/concepts-hot-take.md",
        f"  - {out_dir}/concepts-contrarian.md",
        "",
        "### Step 2 — Scripts (content-creator on ALL 3 concepts)",
        "Run the `youtube-insights:youtube-content-creator` skill on each",
        "concept file to produce matching script-outline files in the same",
        "folder (script-outline.md, script-outline-hot-take.md,",
        "script-outline-contrarian.md — all formats the skill prescribes,",
        "exact spoken lines, a Visual field per beat). The scripts must be",
        "SIMILAR in topic/angle to the source video but UNIQUE — net-new",
        "information gain; use the source analysis.md to see what it already",
        "covers and deliberately go beyond it, speaking to the ICP and",
        "the problems from Step 0.5 in their vocabulary. Do NOT generate",
        "images — the",
        "graphics stage runs only after a human approves the scripts.",
        "",
        "### Step 2.5 — Format gate (yt_lint_script, NOT optional)",
        "Every spoken bullet must be a full conversational sentence or two",
        "(15-35 words) that CONTINUES the previous bullet's thought — read",
        "the beat aloud; if it sounds like a list of headlines, rewrite it.",
        "Each beat's word count must match its timestamps (~150 wpm), and",
        "every HOOK INTO NEXT is a complete spoken sentence, never a title",
        "fragment. Then PROVE it: run the `yt_lint_script` tool on each of",
        "the 3 script files and fix every finding, re-running until all 3",
        "report ok:true. A completion validator re-lints after you finish —",
        "a script that fails it re-opens this work as a fix task, so do not",
        "complete until the linter is clean.",
        "",
        "### Step 3 — Publish the artifacts",
        f"Write {out_dir}/SUMMARY.md (source video, the 3 concepts' titles and",
        "angles, evidence/insights cited, file paths). Then attach SUMMARY.md",
        "and all 3 script-outline files to THIS kanban task with the",
        "`kanban_attach` tool so reviewers see them on the task, and finish",
        "with `kanban_complete` whose summary lists every file path. The",
        "files under the workspace youtube/ tree appear on the YouTube",
        "Insights Artifacts tab — that is the review surface; leave them in",
        "place.",
        "",
        "### CRITICAL RULES",
        f"- Every output file goes under exactly {out_dir}/ — no alternate paths.",
        "- Do NOT run a gap-finder workspace sweep over the top-10 — Mode A,",
        "  this one source video only.",
    ])


def create_generation_task(video_id: str) -> dict[str, Any]:
    """Create (or reuse a still-open) generation task for one video."""
    kb = _kanban()
    if kb is None:
        return {"error": "kanban unavailable"}
    conn = yti_store.connect()
    try:
        video = yti_store.get_video(conn, video_id)
        if not video:
            # Fetched-but-unindexed video (e.g. DB rebuilt): reindex from the
            # workspace metadata files before giving up.
            try:
                yti_fetcher.reindex_from_disk(conn)
                video = yti_store.get_video(conn, video_id)
            except Exception:
                video = None
        if not video:
            return {"error": f"unknown videoId: {video_id}"}
        mapping = _load_map(conn)
        entry = mapping.get(video_id) or {}
        existing = entry.get("taskId")
        with kb.connect_closing() as conn_kb:
            if existing and _kanban_task_open(kb, conn_kb, existing):
                task = kb.get_task(conn_kb, existing)
                age_min = _age_minutes(entry.get("createdAt"))
                if task is not None and age_min is not None and age_min < STALE_MINUTES:
                    return {"ok": True, "taskId": existing, "already": True}
            task_id = kb.create_task(
                conn_kb,
                title=generation_task_title(video.get("title") or video_id),
                body=_build_brief(video, yti_paths.workspace_dir()),
                assignee=resolve_kanban_assignee(),
                created_by="youtube-insights",
                workspace_kind="scratch",
                skills=list(GENERATION_SKILLS),
                priority=10,  # user-initiated: jump the queue
            )
        out_dir = (yti_paths.workspace_dir() / "youtube"
                   / _now().strftime("%Y-%m-%d") / "recommended"
                   / _slug(video.get("title") or video_id))
        mapping[video_id] = {"taskId": task_id, "createdAt": _now_iso(),
                             "outDir": str(out_dir)}
        _save_map(conn, mapping)
    except Exception as exc:
        return {"error": f"could not create the generation task: {exc}"}
    finally:
        conn.close()
    kick_dispatcher()
    return {"ok": True, "taskId": task_id}


def _age_minutes(created_at: Optional[str]) -> Optional[float]:
    if not created_at:
        return None
    try:
        t = datetime.fromisoformat(created_at)
        return (_now() - t).total_seconds() / 60.0
    except ValueError:
        return None


def _find_worker_session(kanban_task_id: str) -> Optional[str]:
    """Exact-match lookup of the dispatcher-spawned worker session (the same
    rule the Value Creator page uses — LIKE matches bleed across tasks)."""
    try:
        import os
        import sqlite3
        try:
            from hermes_constants import get_hermes_home
            db = str(get_hermes_home() / "state.db")
        except ImportError:
            db = os.path.expanduser(
                os.path.join(os.environ.get("HERMES_HOME", "~/.hermes"), "state.db"))
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT session_id FROM messages WHERE role = 'user' "
                "AND content = ? ORDER BY id DESC LIMIT 1",
                (f"work kanban task {kanban_task_id}",),
            ).fetchone()
        finally:
            conn.close()
        return row[0] if row else None
    except Exception:
        return None


def generation_states() -> dict[str, dict[str, Any]]:
    """Per-video ✨ state for the Trends page: open | stale | done (+ links)."""
    kb = _kanban()
    if kb is None:
        return {}
    conn = yti_store.connect()
    try:
        mapping = _load_map(conn)
    finally:
        conn.close()
    if not mapping:
        return {}
    out: dict[str, dict[str, Any]] = {}
    try:
        with kb.connect_closing() as conn_kb:
            for video_id, entry in mapping.items():
                task_id = entry.get("taskId")
                if not task_id:
                    continue
                task = kb.get_task(conn_kb, task_id)
                if task is None or getattr(task, "status", "") == "archived":
                    continue
                status = getattr(task, "status", "")
                age = _age_minutes(entry.get("createdAt"))
                if status == "done":
                    ui = "done"
                elif age is not None and age >= STALE_MINUTES:
                    ui = "stale"
                else:
                    ui = "open"
                out[video_id] = {
                    "taskId": task_id,
                    "status": ui,
                    "kanbanStatus": status,
                    "sessionId": _find_worker_session(task_id),
                }
    except Exception:
        return out
    return out


# ---------------------------------------------------------------------------
# Produce: graphics + PDF for ONE approved script (Artifacts tab button).
# Paperclip's Phase 6/6b flow — images per beat + thumbnails + production
# PDF — run by a single hermes worker on an existing script-outline file.
# ---------------------------------------------------------------------------

PRODUCE_META_KEY = "produce_tasks"
PRODUCE_SKILLS = (
    "youtube-insights:youtube-content-creator",
    "youtube-insights:generate-image",
)


def _build_produce_brief(script_abs: Path) -> str:
    script_dir = script_abs.parent
    assets_dir = script_dir / "assets"
    pdf_path = script_dir / (script_abs.stem + ".pdf")
    return "\n".join([
        "## MANDATORY: Produce graphics + production PDF for ONE approved script.",
        "",
        f"**Script:** {script_abs}",
        f"**Assets directory:** {assets_dir}/",
        f"**PDF output:** {pdf_path}",
        "",
        "You run EVERY phase yourself in this session. The",
        "youtube-content-creator skill's Mode B text says to delegate Phase 6",
        "and 6b to a Graphics Creator agent — that applies to a multi-agent",
        "deployment and is OVERRIDDEN here: there is no other agent, YOU run",
        "Phase 6 and Phase 6b directly.",
        "",
        "1. Load `youtube-insights:youtube-content-creator` and run **Mode B**",
        "   (images-and-pdf) against the script above.",
        "2. **Phase 6 — images:** one image per beat (from each beat's Visual",
        "   field) plus ONE THUMBNAIL PER OPTION listed in the script's",
        "   Production Notes -> Thumbnail Options section (video scripts list",
        "   A-C, presentations list A-F — the script is the authority), ALL",
        "   via the",
        "   `youtube-insights:generate-image` skill (grok imagine + its",
        "   mandatory verify_image QA and baseline-reference check). Beat",
        "   images MUST pass the baseline as the source image:",
        "   `--input \"<generate-image skill dir>/youtube-baseline-reference.png\"`",
        "   (image-to-image via xAI images/edits) — thumbnails do NOT take the",
        "   baseline input. Its Absolute Rules apply: never substitute",
        "   PIL/canvas/ImageMagick or any hand-rolled generator. Save",
        f"   everything under {assets_dir}/ using the skill's naming",
        "   convention.",
        f"3. **Phase 6b — PDF:** produce the production PDF at {pdf_path}",
        "   (landscape, one image per page — thumbnails first, then each beat",
        "   image in order).",
        "4. **Publish:** attach the PDF and every thumbnail to THIS kanban",
        "   task with `kanban_attach`; leave every file in place (they appear",
        "   on the YouTube Insights Artifacts tab). Then `kanban_complete`",
        "   with a summary listing every file produced.",
    ])


def create_produce_task(rel_path: str) -> dict[str, Any]:
    """Create (or reuse a still-open) produce task for one script file."""
    kb = _kanban()
    if kb is None:
        return {"error": "kanban unavailable"}
    try:
        from . import yti_workspace
    except ImportError:  # standalone import
        import yti_workspace  # type: ignore
    workspace = yti_paths.workspace_dir()
    script_abs = yti_workspace.resolve_inside_workspace(workspace, rel_path or "")
    if script_abs is None or not script_abs.exists():
        return {"error": f"script not found in workspace: {rel_path}"}
    if script_abs.suffix.lower() != ".md":
        return {"error": "produce runs on markdown script files"}
    conn = yti_store.connect()
    try:
        mapping_raw = yti_store.get_meta(conn, PRODUCE_META_KEY)
        try:
            mapping = json.loads(mapping_raw) if mapping_raw else {}
            if not isinstance(mapping, dict):
                mapping = {}
        except ValueError:
            mapping = {}
        entry = mapping.get(rel_path) or {}
        existing = entry.get("taskId")
        with kb.connect_closing() as conn_kb:
            if existing and _kanban_task_open(kb, conn_kb, existing):
                age = _age_minutes(entry.get("createdAt"))
                if age is not None and age < STALE_MINUTES:
                    return {"ok": True, "taskId": existing, "already": True}
            task_id = kb.create_task(
                conn_kb,
                title=f"Produce: {script_abs.stem}",
                body=_build_produce_brief(script_abs),
                assignee=resolve_kanban_assignee(),
                created_by="youtube-insights",
                workspace_kind="scratch",
                skills=list(PRODUCE_SKILLS),
                priority=10,
            )
        mapping[rel_path] = {"taskId": task_id, "createdAt": _now_iso()}
        yti_store.set_meta(conn, PRODUCE_META_KEY, json.dumps(mapping))
    except Exception as exc:
        return {"error": f"could not create the produce task: {exc}"}
    finally:
        conn.close()
    kick_dispatcher()
    return {"ok": True, "taskId": task_id}


def _states_for(meta_key: str) -> dict[str, dict[str, Any]]:
    """Per-key task state for a meta-map of kanban tasks (open|stale|done)."""
    kb = _kanban()
    if kb is None:
        return {}
    conn = yti_store.connect()
    try:
        raw = yti_store.get_meta(conn, meta_key)
    finally:
        conn.close()
    try:
        mapping = json.loads(raw) if raw else {}
        if not isinstance(mapping, dict):
            return {}
    except ValueError:
        return {}
    out: dict[str, dict[str, Any]] = {}
    try:
        with kb.connect_closing() as conn_kb:
            for key, entry in mapping.items():
                task_id = entry.get("taskId")
                if not task_id:
                    continue
                task = kb.get_task(conn_kb, task_id)
                if task is None or getattr(task, "status", "") == "archived":
                    continue
                status = getattr(task, "status", "")
                age = _age_minutes(entry.get("createdAt"))
                ui = ("done" if status == "done"
                      else "stale" if (age is not None and age >= STALE_MINUTES)
                      else "open")
                out[key] = {
                    "taskId": task_id,
                    "status": ui,
                    "kanbanStatus": status,
                    "sessionId": _find_worker_session(task_id),
                }
    except Exception:
        return out
    return out


def produce_states() -> dict[str, dict[str, Any]]:
    """Per-script produce state for the Artifacts tab (open|stale|done)."""
    return _states_for(PRODUCE_META_KEY)


# ---------------------------------------------------------------------------
# Iterate: re-do ONE script with user steering (Artifacts tab ↻ button next
# to Produce). Re-runs the content-creator on the script's own concept doc,
# treating the current draft as the prior attempt and the user's steering as
# direction, and overwrites the same file in place.
# ---------------------------------------------------------------------------

ITERATE_META_KEY = "iterate_tasks"
ITERATE_SKILLS = (
    "youtube-insights:youtube-content-creator",
)


def _concept_for_script(script_abs: Path) -> Path:
    """script-outline.md -> concepts.md; -hot-take/-contrarian follow suit."""
    m = re.match(r"script-outline(-[a-z0-9-]+)?\.md$", script_abs.name)
    variant = (m.group(1) or "") if m else ""
    return script_abs.parent / f"concepts{variant}.md"


def _build_iterate_brief(script_abs: Path, steering: str) -> str:
    concept_abs = _concept_for_script(script_abs)
    steering = (steering or "").strip()
    return "\n".join([
        "## MANDATORY: Rewrite ONE existing script — same concept, better execution.",
        "",
        f"**Script to rewrite (overwrite in place):** {script_abs}",
        f"**Concept doc:** {concept_abs}{' ✓' if concept_abs.exists() else ' (missing — derive the concept from the current script)'}",
        "",
        "### The user's steering (highest priority — this is WHY the rewrite exists)",
        "",
        "```",
        steering or "(no steering text — improve format compliance and substance)",
        "```",
        "",
        "### Steps (all yourself, this session — no subtasks)",
        "",
        "1. Read the current script draft AND the concept doc above. Read",
        "   $HERMES_HOME/plugins-data/ai-cyber-value-creator/company-context.md",
        "   for the ICP if present (default HERMES_HOME ~/.hermes; /opt/data",
        "   in the container).",
        "2. Load `youtube-insights:youtube-content-creator` and rewrite the",
        "   script from the concept doc, treating the current draft as the",
        "   prior attempt: keep what the steering praises or doesn't mention,",
        "   fix what it criticizes, and follow the skill's format contract",
        "   COMPLETELY — beat word budgets vs timestamps, flowing spoken",
        "   lines, spoken-sentence HOOK INTO NEXT, loop ledger closed, Sound",
        "   human rules, and the mandatory Phase 4b self-check pass.",
        f"3. Overwrite exactly {script_abs} with the rewritten script — same",
        "   file, no siblings, no suffixes. Do NOT touch the other variants",
        "   or generate images.",
        "4. Format gate: run the `yt_lint_script` tool on the rewritten file",
        "   and fix every finding, re-running until it reports ok:true. A",
        "   completion validator re-lints after you finish — do not complete",
        "   while findings remain.",
        "5. Attach the rewritten file to THIS kanban task with `kanban_attach`,",
        "   then `kanban_complete` with a summary of what the steering asked",
        "   for and what changed.",
    ])


def create_iterate_task(rel_path: str, steering: str = "") -> dict[str, Any]:
    """Create (or reuse a still-open) iterate task for one script file."""
    kb = _kanban()
    if kb is None:
        return {"error": "kanban unavailable"}
    try:
        from . import yti_workspace
    except ImportError:  # standalone import
        import yti_workspace  # type: ignore
    workspace = yti_paths.workspace_dir()
    script_abs = yti_workspace.resolve_inside_workspace(workspace, rel_path or "")
    if script_abs is None or not script_abs.exists():
        return {"error": f"script not found in workspace: {rel_path}"}
    if script_abs.suffix.lower() != ".md":
        return {"error": "iterate runs on markdown script files"}
    conn = yti_store.connect()
    try:
        mapping_raw = yti_store.get_meta(conn, ITERATE_META_KEY)
        try:
            mapping = json.loads(mapping_raw) if mapping_raw else {}
            if not isinstance(mapping, dict):
                mapping = {}
        except ValueError:
            mapping = {}
        entry = mapping.get(rel_path) or {}
        existing = entry.get("taskId")
        with kb.connect_closing() as conn_kb:
            if existing and _kanban_task_open(kb, conn_kb, existing):
                age = _age_minutes(entry.get("createdAt"))
                if age is not None and age < STALE_MINUTES:
                    return {"ok": True, "taskId": existing, "already": True}
            task_id = kb.create_task(
                conn_kb,
                title=f"Iterate: {script_abs.stem} ({script_abs.parent.name})",
                body=_build_iterate_brief(script_abs, steering),
                assignee=resolve_kanban_assignee(),
                created_by="youtube-insights",
                workspace_kind="scratch",
                skills=list(ITERATE_SKILLS),
                priority=10,
            )
        mapping[rel_path] = {"taskId": task_id, "createdAt": _now_iso()}
        yti_store.set_meta(conn, ITERATE_META_KEY, json.dumps(mapping))
    except Exception as exc:
        return {"error": f"could not create the iterate task: {exc}"}
    finally:
        conn.close()
    kick_dispatcher()
    return {"ok": True, "taskId": task_id}


def iterate_states() -> dict[str, dict[str, Any]]:
    """Per-script iterate state for the Artifacts tab (open|stale|done)."""
    return _states_for(ITERATE_META_KEY)


# ---------------------------------------------------------------------------
# Generate-from-topic: the Artifacts page Generate button. One kanban task
# that grounds in the insight knowledge base (gap-finder Mode D) and writes
# a full 3-variant script set into today's recommended folder.
# ---------------------------------------------------------------------------

TOPIC_META_KEY = "topic_tasks"
TOPIC_SKILLS = (
    "youtube-insights:youtube-gap-finder",
    "youtube-insights:youtube-content-creator",
)


def _build_topic_brief(topic: str, context: str, out_dir: Path) -> str:
    context = (context or "").strip()
    return "\n".join([
        "## MANDATORY: Generate a full 3-variant script set for ONE user-chosen topic.",
        "",
        f"**Topic:** {topic}",
        f"**Output directory:** {out_dir}",
        "",
        "### The user's context / guidance (steering for every file below)",
        "",
        "```",
        context or "(none given — steer by the company context ICP)",
        "```",
        "",
        "You do ALL steps yourself in this session — no subtasks, no",
        "delegation. Work autonomously.",
        "",
        "### Step 0 — Company context (ICP grounding)",
        "Read $HERMES_HOME/plugins-data/ai-cyber-value-creator/",
        "company-context.md (default HERMES_HOME ~/.hermes; /opt/data in",
        "the container). If present, extract the ideal customer (ICP),",
        "their current problems, and the positioning/voice — every concept",
        "and script below is written FOR that ICP: the straight take",
        "teaches their problem directly, the hot take confronts what they",
        "believe or fear about it, and the contrarian take argues against",
        "the prevailing advice they hear. If the file is missing, note it",
        "in SUMMARY.md and proceed.",
        "",
        "### Step 1 — Concepts (gap-finder Mode D, topic-only)",
        "Run the `youtube-insights:youtube-gap-finder` skill in **Mode D",
        "(Topic-Only, Insights-Grounded)**. Pass explicitly:",
        f"  - Topic: {topic}",
        "  - Context/guidance: the user's block above",
        f"  - Output directory: {out_dir}/",
        "Ground the concepts in the insight knowledge base",
        "(`yt_search_insights` — 3-5 queries around the topic) plus the",
        "cited videos' analysis.md files where present. Mode D output is",
        "EXACTLY 3 files:",
        f"  - {out_dir}/concepts.md",
        f"  - {out_dir}/concepts-hot-take.md",
        f"  - {out_dir}/concepts-contrarian.md",
        "",
        "### Step 2 — Scripts (content-creator on ALL 3 concepts)",
        "Run the `youtube-insights:youtube-content-creator` skill on each",
        "concept file to produce matching script-outline files in the same",
        "folder (script-outline.md, script-outline-hot-take.md,",
        "script-outline-contrarian.md). Follow the skill's format contract",
        "COMPLETELY — beat word budgets vs timestamps, flowing spoken lines,",
        "spoken-sentence HOOK INTO NEXT, loop ledger closed, Sound human",
        "rules, and the mandatory Phase 4b self-check pass. Speak to the",
        "ICP from Step 0 in their vocabulary, honoring the user's guidance",
        "block in every variant. Do NOT generate images — graphics run only",
        "after a human approves the scripts.",
        "",
        "### Step 2.5 — Format gate (yt_lint_script, NOT optional)",
        "Every spoken bullet is a full conversational sentence or two",
        "(15-35 words) continuing the previous bullet's thought; each beat's",
        "word count matches its timestamps (~150 wpm); every HOOK INTO NEXT",
        "is a complete spoken sentence. PROVE it: run the `yt_lint_script`",
        "tool on all 3 script files and fix every finding, re-running until",
        "each reports ok:true. A completion validator re-lints after you",
        "finish and re-opens failing work — do not complete until clean.",
        "",
        "### Step 3 — Publish the artifacts",
        f"Write {out_dir}/SUMMARY.md (topic, the user's guidance, the 3",
        "angles, insights cited with source counts, file paths). Then attach",
        "SUMMARY.md and all 3 script-outline files to THIS kanban task with",
        "`kanban_attach`, and finish with `kanban_complete` whose summary",
        "lists every file path. The files appear on the YouTube Insights",
        "Artifacts tab — that is the review surface; leave them in place.",
        "",
        "### CRITICAL RULES",
        f"- Every output file goes under exactly {out_dir}/ — no alternate paths.",
        "- No yt_trending sweep, no fetching new videos — the insight",
        "  knowledge base and existing analyses are the sources.",
    ])


def create_topic_task(topic: str, context: str = "") -> dict[str, Any]:
    """Create (or reuse a still-open) topic-generation task."""
    kb = _kanban()
    if kb is None:
        return {"error": "kanban unavailable"}
    topic = (topic or "").strip()
    if not topic:
        return {"error": "topic required"}
    slug = _slug(topic)
    today = _now().strftime("%Y-%m-%d")
    out_dir = yti_paths.workspace_dir() / "youtube" / today / "recommended" / slug
    key = f"{today}/{slug}"
    conn = yti_store.connect()
    try:
        mapping_raw = yti_store.get_meta(conn, TOPIC_META_KEY)
        try:
            mapping = json.loads(mapping_raw) if mapping_raw else {}
            if not isinstance(mapping, dict):
                mapping = {}
        except ValueError:
            mapping = {}
        entry = mapping.get(key) or {}
        existing = entry.get("taskId")
        with kb.connect_closing() as conn_kb:
            if existing and _kanban_task_open(kb, conn_kb, existing):
                age = _age_minutes(entry.get("createdAt"))
                if age is not None and age < STALE_MINUTES:
                    return {"ok": True, "taskId": existing, "already": True}
            task_id = kb.create_task(
                conn_kb,
                title=f"Topic Scripts: {topic}",
                body=_build_topic_brief(topic, context, out_dir),
                assignee=resolve_kanban_assignee(),
                created_by="youtube-insights",
                workspace_kind="scratch",
                skills=list(TOPIC_SKILLS),
                priority=10,
            )
        mapping[key] = {"taskId": task_id, "createdAt": _now_iso(),
                        "topic": topic, "outDir": str(out_dir)}
        yti_store.set_meta(conn, TOPIC_META_KEY, json.dumps(mapping))
    except Exception as exc:
        return {"error": f"could not create the topic task: {exc}"}
    finally:
        conn.close()
    kick_dispatcher()
    return {"ok": True, "taskId": task_id, "outDir": str(out_dir)}


def topic_states() -> dict[str, dict[str, Any]]:
    """Per-topic generation state for the Artifacts page (open|stale|done)."""
    return _states_for(TOPIC_META_KEY)


# ---------------------------------------------------------------------------
# Outline -> Presentation: the Artifacts page's second entry path. One kanban
# task EXPANDS a speaker outline into a presentation script — one slide per
# bullet, each beat's Visual a rich whiteboard-image spec. No images here:
# the user reviews the script, then the normal Produce button runs the
# existing pipeline (generate-image QA, 3 thumbnails, PDF) unchanged.
# ---------------------------------------------------------------------------

PRESENT_META_KEY = "present_tasks"
PRESENT_SKILLS = ("youtube-insights:outline-to-presentation",)
PRESENT_MAX_RETRIES = 2

_OUTLINE_ITEM_RE = re.compile(r"^(\s*)(\d+)[.)]\s+(.+)$")


def _outline_slide_map(outline: str) -> list[str]:
    """Slide list an outline expands to: one divider per top-level item,
    one slide per second-level item (deeper levels fold into their parent).
    Returns [] when nothing parses (free-form outline — count not enforced)."""
    indents: list[int] = []
    slides: list[str] = []
    for line in outline.splitlines():
        m = _OUTLINE_ITEM_RE.match(line)
        if not m:
            continue
        depth_ws = len(m.group(1).replace("\t", "    "))
        if depth_ws not in indents:
            indents.append(depth_ws)
            indents.sort()
        level = indents.index(depth_ws)
        if level == 0:
            slides.append(f"[divider] {m.group(3).strip()}")
        elif level == 1:
            slides.append(m.group(3).strip())
    return slides


def _build_present_brief(topic: str, outline: str, out_dir: Path) -> str:
    slide_map = _outline_slide_map(outline)
    contract: list[str] = []
    if slide_map:
        contract = [
            "### The beat contract (mechanically checked after you finish)",
            f"script-outline.md MUST contain the Hook section plus AT LEAST",
            f"{len(slide_map)} `## Beat N:` sections — one per line below, in",
            "this exact order. A validator counts them; a short script is",
            "REJECTED and reopened:",
            "",
        ] + [f"  {i+1}. {s}" for i, s in enumerate(slide_map)] + [""]
    return "\n".join([
        "## MANDATORY: Expand ONE speaker outline into a presentation script.",
        "",
        f"**Topic / hook:** {topic}",
        f"**Output directory:** {out_dir}",
        "",
        "### The speaker's outline (a CONTRACT — keep order and hierarchy)",
        "",
        "```",
        outline,
        "```",
        "",
        "You do ALL steps yourself in this session — no subtasks, no",
        "delegation. Work autonomously.",
        "",
        "### Step 1 — Expand (outline-to-presentation skill)",
        "Load `youtube-insights:outline-to-presentation` and follow it",
        "COMPLETELY. Write exactly two files — AND NOTHING ELSE:",
        f"  - {out_dir}/concepts.md   (PRESENTATION MODE header, topic,",
        "    outline verbatim, slide map, visual language)",
        f"  - {out_dir}/script-outline.md   (standard script format: Hook +",
        "    one Beat per slide in outline order, talking-point spoken",
        "    lines, a rich **Visual** per slide with quoted label text,",
        "    Production Notes with SIX Thumbnail Options A-F)",
        "",
    ] + contract + [
        "### Step 2 — Format gate (yt_lint_script, NOT optional)",
        f"Run the `yt_lint_script` tool on {out_dir}/script-outline.md and",
        "fix every finding, re-running until it reports ok:true.",
        "",
        "### Step 3 — Publish",
        "Attach both files to THIS kanban task with `kanban_attach` and",
        "finish with `kanban_complete` whose summary lists both paths and",
        "says: review the script on the Artifacts tab, then press Produce",
        "on script-outline.md for the slide images, 6 thumbnail options,",
        "and the PDF.",
        "",
        "### CRITICAL RULES",
        f"- Every output file goes under exactly {out_dir}/ — no alternate paths.",
        "- Write ONLY the two files named above. NO helper scripts (no .py,",
        "  .sh, .mjs), no notes files, no assets, no extra markdown — a",
        "  worker once littered the folder with scratch tooling; that is a",
        "  contract violation.",
        "- Do NOT generate images, do NOT run generate-image, do NOT write",
        "  PDFs — that is the Produce step, which runs only after a human",
        "  approves this script.",
        "- Keep the outline's order and hierarchy exactly: one slide per",
        "  second-level bullet, dividers for top-level items, third-level",
        "  items as labeled elements inside their parent's Visual. EVERY",
        "  outline item must be represented — never truncate a long",
        "  outline.",
    ])


def create_present_task(topic: str, outline: str) -> dict[str, Any]:
    """Create (or reuse a still-open) outline->presentation task."""
    kb = _kanban()
    if kb is None:
        return {"error": "kanban unavailable"}
    topic = (topic or "").strip()
    outline = (outline or "").strip()
    if not topic:
        return {"error": "topic required"}
    if not outline:
        return {"error": "outline required"}
    slug = _slug(topic)
    today = _now().strftime("%Y-%m-%d")
    out_dir = yti_paths.workspace_dir() / "youtube" / today / "presentations" / slug
    key = f"{today}/{slug}"
    conn = yti_store.connect()
    try:
        mapping_raw = yti_store.get_meta(conn, PRESENT_META_KEY)
        try:
            mapping = json.loads(mapping_raw) if mapping_raw else {}
            if not isinstance(mapping, dict):
                mapping = {}
        except ValueError:
            mapping = {}
        entry = mapping.get(key) or {}
        existing = entry.get("taskId")
        with kb.connect_closing() as conn_kb:
            if existing and _kanban_task_open(kb, conn_kb, existing):
                age = _age_minutes(entry.get("createdAt"))
                if age is not None and age < STALE_MINUTES:
                    return {"ok": True, "taskId": existing, "already": True}
            task_id = kb.create_task(
                conn_kb,
                title=f"Presentation: {topic}",
                body=_build_present_brief(topic, outline, out_dir),
                assignee=resolve_kanban_assignee(),
                created_by="youtube-insights",
                workspace_kind="scratch",
                skills=list(PRESENT_SKILLS),
                priority=10,
            )
        mapping[key] = {"taskId": task_id, "createdAt": _now_iso(),
                        "topic": topic, "outDir": str(out_dir),
                        "outline": outline,
                        "expectedBeats": len(_outline_slide_map(outline)) or None}
        yti_store.set_meta(conn, PRESENT_META_KEY, json.dumps(mapping))
    except Exception as exc:
        return {"error": f"could not create the presentation task: {exc}"}
    finally:
        conn.close()
    kick_dispatcher()
    return {"ok": True, "taskId": task_id, "outDir": str(out_dir)}


def present_states() -> dict[str, dict[str, Any]]:
    """Per-presentation expansion state for the Artifacts page."""
    return _states_for(PRESENT_META_KEY)


def _build_present_fix_brief(entry: dict[str, Any], problems: list[str]) -> str:
    out_dir = entry.get("outDir") or ""
    return "\n".join([
        "## MANDATORY: Fix an INCOMPLETE outline->presentation expansion.",
        "",
        "A worker completed this presentation task, but validation FAILED:",
        "",
    ] + [f"- {p}" for p in problems] + [
        "",
        f"**Output directory:** {out_dir}",
        f"**Topic:** {entry.get('topic') or ''}",
        "",
        "### The speaker's outline (the CONTRACT — every item, in order)",
        "",
        "```",
        entry.get("outline") or "(see concepts.md)",
        "```",
        "",
        "Load `youtube-insights:outline-to-presentation` and rewrite",
        f"{out_dir}/script-outline.md IN PLACE so the Hook plus one",
        "`## Beat N:` section per slide covers the ENTIRE outline (dividers",
        "for top-level items, one slide per second-level item, deeper items",
        "as labeled elements in their parent's Visual). Keep what is already",
        "good; add what is missing; run `yt_lint_script` until ok:true.",
        "Write ONLY the two contract files — remove any stray helper files",
        "you find in the output directory (scripts, notes, scratch files).",
        "Then `kanban_complete`; the user reviews and presses Produce.",
    ])


def handle_present_completion(conn, kanban_task_id: str) -> Optional[dict[str, Any]]:
    """kanban_task_completed hook: when an outline->presentation expansion
    (or its fix task) finishes, validate completeness and AUTO-CHAIN the
    produce task — the user's one button goes all the way to images + PDF."""
    kb = _kanban()
    if kb is None:
        return None
    raw = yti_store.get_meta(conn, PRESENT_META_KEY)
    try:
        mapping = json.loads(raw) if raw else {}
    except ValueError:
        return None
    if not isinstance(mapping, dict):
        return None
    for key, entry in mapping.items():
        if not isinstance(entry, dict) or entry.get("taskId") != kanban_task_id:
            continue
        out_dir = Path(entry.get("outDir") or "")
        script = out_dir / "script-outline.md"
        problems: list[str] = []
        if not script.exists():
            problems.append(f"script-outline.md was never written at {script}")
        else:
            beats = len(re.findall(r"^## Beat \d+", script.read_text(),
                                   re.MULTILINE))
            expected = entry.get("expectedBeats")
            if expected and beats < int(expected):
                problems.append(
                    f"script has only {beats} '## Beat' sections but the "
                    f"outline requires at least {expected} (one per divider "
                    "and per second-level bullet) — the outline was "
                    "truncated")
        if not (out_dir / "concepts.md").exists():
            problems.append(f"concepts.md was never written in {out_dir}")
        if problems:
            retries = int(entry.get("presentRetries") or 0)
            if retries >= PRESENT_MAX_RETRIES:
                return {"ok": False, "exhausted": True, "key": key,
                        "problems": problems}
            with kb.connect_closing() as conn_kb:
                fix_id = kb.create_task(
                    conn_kb,
                    title=f"Fix presentation: {key.rsplit('/', 1)[-1]}",
                    body=_build_present_fix_brief(entry, problems),
                    assignee=resolve_kanban_assignee(),
                    created_by="youtube-insights",
                    workspace_kind="scratch",
                    skills=list(PRESENT_SKILLS),
                    priority=10,
                )
            entry["taskId"] = fix_id
            entry["presentRetries"] = retries + 1
            entry["createdAt"] = _now_iso()
            mapping[key] = entry
            yti_store.set_meta(conn, PRESENT_META_KEY, json.dumps(mapping))
            kick_dispatcher()
            return {"ok": True, "fixTask": fix_id, "problems": problems}
        # Clean and complete: the script now waits for HUMAN review — the
        # user presses Produce on it when satisfied (deliberate pause).
        return {"ok": True, "clean": True, "key": key}
    return None


# ---------------------------------------------------------------------------
# Completion validation: script tasks must LINT CLEAN, not just complete.
# Mirrors the analysis validator: when a generation/topic/iterate worker
# finishes, lint what it wrote; on findings open ONE fix task (max 2) whose
# brief embeds the exact linter output.
# ---------------------------------------------------------------------------

LINT_MAX_RETRIES = 2
FIX_SKILLS = ("youtube-insights:youtube-content-creator",)


def _lint_targets_for_entry(meta_key: str, key: str,
                            entry: dict[str, Any]) -> list[Path]:
    try:
        from . import yti_lint  # noqa: F401  (import check)
    except ImportError:
        import yti_lint  # type: ignore # noqa: F401
    if meta_key == ITERATE_META_KEY:
        p = yti_paths.workspace_dir() / key
        return [p]
    out_dir = entry.get("outDir")
    if not out_dir:
        return []
    d = Path(out_dir)
    return [d / n for n in ("script-outline.md", "script-outline-hot-take.md",
                            "script-outline-contrarian.md")]


def _lint_paths(paths: list[Path]) -> list[dict[str, Any]]:
    try:
        from . import yti_lint
    except ImportError:
        import yti_lint  # type: ignore
    findings: list[dict[str, Any]] = []
    for p in paths:
        if not p.exists():
            findings.append({"path": str(p), "section": "(file)",
                             "kind": "missing_file",
                             "message": f"{p.name} was not produced at {p}"})
            continue
        result = yti_lint.lint_file(p)
        for f in result["findings"]:
            findings.append({"path": str(p), **f})
    return findings


def _build_fix_brief(findings: list[dict[str, Any]]) -> str:
    by_path: dict[str, list[dict[str, Any]]] = {}
    for f in findings:
        by_path.setdefault(f["path"], []).append(f)
    lines = [
        "## MANDATORY: Fix format-linter findings in already-produced scripts.",
        "",
        "A worker completed these scripts, but they FAIL the format contract",
        "(the youtube-content-creator skill's Phase 4 rules). Rewrite each",
        "listed file IN PLACE until `yt_lint_script` reports ok:true for it.",
        "This is a rewrite for flow and substance — keep the topic, angle,",
        "structure, and Visual fields; fix exactly what the findings say:",
        "expand thin beats with real substance (worked examples, numbers,",
        "stories, failure cases — never filler), merge fragment bullets into",
        "full conversational sentences that continue one thread of thought,",
        "and turn fragment hooks into complete spoken sentences.",
        "",
    ]
    for path, items in by_path.items():
        lines.append(f"### {path}")
        for f in items:
            lines.append(f"- [{f['kind']}] {f['section']}: {f['message']}")
        lines.append("")
    lines += [
        "### Steps",
        "1. Read each file and the findings above.",
        "2. Rewrite in place (same filenames — no siblings, no subfolders).",
        "3. Run `yt_lint_script` on each file; repeat until every file is",
        "   ok:true. Do NOT complete while any finding remains.",
        "4. `kanban_complete` with a summary of what changed per file.",
    ]
    return "\n".join(lines)


def handle_script_completion(conn, kanban_task_id: str) -> Optional[dict[str, Any]]:
    """kanban_task_completed hook: when a generation/topic/iterate task
    finishes, lint its scripts; open a fix task on findings (max 2)."""
    kb = _kanban()
    if kb is None:
        return None
    for meta_key in (GENERATION_META_KEY, TOPIC_META_KEY, ITERATE_META_KEY):
        raw = yti_store.get_meta(conn, meta_key)
        try:
            mapping = json.loads(raw) if raw else {}
        except ValueError:
            continue
        if not isinstance(mapping, dict):
            continue
        for key, entry in mapping.items():
            if not isinstance(entry, dict):
                continue
            if entry.get("taskId") != kanban_task_id:
                continue
            targets = _lint_targets_for_entry(meta_key, key, entry)
            if not targets:
                return {"ok": True, "skipped": "no lint targets"}
            findings = _lint_paths(targets)
            if not findings:
                return {"ok": True, "clean": True, "key": key}
            retries = int(entry.get("lintRetries") or 0)
            if retries >= LINT_MAX_RETRIES:
                return {"ok": False, "exhausted": True, "key": key,
                        "findings": len(findings)}
            with kb.connect_closing() as conn_kb:
                fix_id = kb.create_task(
                    conn_kb,
                    title=f"Fix script format: {key.rsplit('/', 1)[-1]}",
                    body=_build_fix_brief(findings),
                    assignee=resolve_kanban_assignee(),
                    created_by="youtube-insights",
                    workspace_kind="scratch",
                    skills=list(FIX_SKILLS),
                    priority=10,
                )
            entry["taskId"] = fix_id
            entry["lintRetries"] = retries + 1
            entry["createdAt"] = _now_iso()
            mapping[key] = entry
            yti_store.set_meta(conn, meta_key, json.dumps(mapping))
            kick_dispatcher()
            return {"ok": False, "retry": retries + 1, "fixTaskId": fix_id,
                    "key": key, "findings": len(findings)}
    return None
