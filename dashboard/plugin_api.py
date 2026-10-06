"""youtube-insights dashboard backend.

Mounted at /api/plugins/youtube-insights/ by the hermes dashboard. Thin
wrappers over the plugin's yti_* modules — the same code paths the agent
tools use, so the dashboard and tools can't drift.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

try:
    from fastapi.responses import FileResponse
except ImportError:  # pragma: no cover - fastapi always ships it
    FileResponse = None  # type: ignore

# The dashboard imports this file standalone (spec_from_file_location), so the
# plugin package isn't importable by name — put the plugin root on sys.path
# and use the yti_-prefixed module names directly.
_PLUGIN_ROOT = str(Path(__file__).resolve().parent.parent)
if _PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, _PLUGIN_ROOT)

import yti_store  # noqa: E402
import yti_fetcher  # noqa: E402
import yti_insights  # noqa: E402
import yti_analysis  # noqa: E402
import yti_generate  # noqa: E402
import yti_paths  # noqa: E402
import yti_workspace  # noqa: E402

router = APIRouter()

_FETCH_LOCK = threading.Lock()
_FETCH_STATE: dict[str, Any] = {"running": False, "last": None}


def _transcript_api_key() -> str:
    key = (os.environ.get("TRANSCRIPT_API_KEY") or "").strip()
    if key:
        return key
    env_file = yti_paths.get_hermes_home() / ".env"
    try:
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("TRANSCRIPT_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


_CRON_MIGRATED: list = []


def _migrate_cron_prompt() -> None:
    """Upgrade UNMODIFIED scheduled-job prompts to the current defaults
    (intelligence-refresh: adds the ideal-mechanics.md consolidation step;
    content-pipeline: aligns file layout with the gap-finder/content-creator
    skill contract). Only a byte-exact match on a previous default is
    upgraded — any mentee edit means no match, and their prompt is never
    touched. Once per process."""
    if _CRON_MIGRATED:
        return
    _CRON_MIGRATED.append(True)
    try:
        import importlib.util as _ilu
        spec = _ilu.spec_from_file_location(
            "yti_cli_mod", str(Path(_PLUGIN_ROOT) / "cli.py"))
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        from cron import jobs as cron_jobs
        for name, old_prompts, new_prompt in (
            ("youtube-intelligence-refresh",
             (mod.CRON_PROMPT_V1,), mod.CRON_PROMPT),
            ("youtube-content-pipeline",
             (mod.PIPELINE_PROMPT_V1, mod.PIPELINE_PROMPT_V2,
              mod.PIPELINE_PROMPT_V3, mod.PIPELINE_PROMPT_V4,
              mod.PIPELINE_PROMPT_V5),
             mod.PIPELINE_PROMPT),
        ):
            job = cron_jobs.resolve_job_ref(name)
            if job and (job.get("prompt") or "") in old_prompts:
                cron_jobs.update_job(job["id"], {"prompt": new_prompt})
    except Exception:
        pass


@router.get("/videos")
def get_videos() -> dict[str, Any]:
    _migrate_cron_prompt()
    conn = yti_store.connect()
    try:
        videos = yti_fetcher.trends_from_db(conn)
        if not videos:
            videos = []
        # ✨ generation state per row (open/stale/done + chat/task links) —
        # same lifecycle the paperclip trends page had for its CMO issues.
        gen = yti_generate.generation_states()
        for v in videos:
            g = gen.get(v.get("videoId"))
            if g:
                v["generation"] = g
        last_fetch = yti_store.get_meta(conn, "last_fetch_run")
        return {"videos": videos, "lastFetchRun": last_fetch,
                "fetchRunning": _FETCH_STATE["running"],
                "hasApiKey": bool(_transcript_api_key())}
    finally:
        conn.close()


@router.get("/channels")
def get_channels() -> dict[str, Any]:
    conn = yti_store.connect()
    try:
        return {"channels": yti_store.list_channels(conn),
                "lookbackDays": yti_fetcher.DEFAULT_LOOKBACK_DAYS}
    finally:
        conn.close()


class ChannelBody(BaseModel):
    handle: str


@router.post("/channels")
def post_channel(body: ChannelBody) -> dict[str, Any]:
    if not body.handle.strip():
        raise HTTPException(400, "handle required")
    conn = yti_store.connect()
    try:
        return {"ok": True, "channels": yti_store.add_channel(conn, body.handle)}
    finally:
        conn.close()


@router.delete("/channels/{handle}")
def delete_channel(handle: str) -> dict[str, Any]:
    conn = yti_store.connect()
    try:
        return {"ok": True, "channels": yti_store.remove_channel(conn, handle)}
    finally:
        conn.close()


class GenerateBody(BaseModel):
    videoId: str


@router.post("/generate-content")
def post_generate_content(body: GenerateBody) -> dict[str, Any]:
    """✨ button: create the single-video script-generation kanban task
    (gap-finder Mode A → content-creator → artifacts + attachments)."""
    result = yti_generate.create_generation_task(body.videoId)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


class ProduceBody(BaseModel):
    path: str


@router.post("/produce")
def post_produce(body: ProduceBody) -> dict[str, Any]:
    """Artifacts tab Produce button: images + thumbnails + production PDF
    for one approved script (content-creator Mode B, Phase 6 + 6b)."""
    result = yti_generate.create_produce_task(body.path)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/produce-states")
def get_produce_states() -> dict[str, Any]:
    return {"states": yti_generate.produce_states()}


class IterateBody(BaseModel):
    path: str
    steering: str = ""


@router.post("/iterate")
def post_iterate(body: IterateBody) -> dict[str, Any]:
    """Artifacts tab Iterate ↻ button: rewrite one script from its concept
    doc, steered by the user's input (content-creator re-run in place)."""
    result = yti_generate.create_iterate_task(body.path, body.steering)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/iterate-states")
def get_iterate_states() -> dict[str, Any]:
    return {"states": yti_generate.iterate_states()}


class TopicBody(BaseModel):
    topic: str
    context: str = ""


@router.post("/generate-topic")
def post_generate_topic(body: TopicBody) -> dict[str, Any]:
    """Artifacts page Generate button: insights-grounded 3-variant script
    set for a user-chosen topic, into today's recommended folder."""
    result = yti_generate.create_topic_task(body.topic, body.context)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/topic-states")
def get_topic_states() -> dict[str, Any]:
    return {"states": yti_generate.topic_states()}


class PresentBody(BaseModel):
    topic: str
    outline: str


@router.post("/present")
def post_present(body: PresentBody) -> dict[str, Any]:
    """Artifacts page Outline->Presentation button: expand a speaker outline
    into a presentation script (one slide per bullet, rich Visual specs);
    the normal Produce button then generates images + thumbnails + PDF."""
    result = yti_generate.create_present_task(body.topic, body.outline)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/present-states")
def get_present_states() -> dict[str, Any]:
    return {"states": yti_generate.present_states()}


class RegenBody(BaseModel):
    path: str
    feedback: str = ""


@router.post("/regen")
def post_regen(body: RegenBody) -> dict[str, Any]:
    """Artifacts page targeted fix: regenerate ONE asset image from user
    feedback (then rebuild its deck PDF), or rebuild ONE PDF from the
    newest images — never the whole set."""
    result = yti_generate.create_regen_task(body.path, body.feedback)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/regen-states")
def get_regen_states() -> dict[str, Any]:
    return {"states": yti_generate.regen_states()}


@router.get("/styles")
def get_styles() -> dict[str, Any]:
    """Image-style catalog (bundled baselines + custom upload) + selection."""
    return yti_generate.style_catalog()


class StyleSelectBody(BaseModel):
    id: str


@router.post("/styles/select")
def post_style_select(body: StyleSelectBody) -> dict[str, Any]:
    result = yti_generate.select_style(body.id)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


class StyleUploadBody(BaseModel):
    filename: str
    dataBase64: str


@router.post("/styles/upload")
def post_style_upload(body: StyleUploadBody) -> dict[str, Any]:
    """Store the operator's own style example — selected and used for every
    generation until changed."""
    import base64 as _b64
    ext = (body.filename or "").rsplit(".", 1)[-1] if "." in (body.filename or "") else ""
    try:
        payload = _b64.b64decode(body.dataBase64 or "", validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid base64 payload")
    result = yti_generate.save_custom_style(payload, ext)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/styles/preview")
def get_style_preview(id: str):
    """The style's example image, for the picker preview."""
    p = yti_generate.style_path((id or "").strip())
    if FileResponse is None or p is None:
        raise HTTPException(status_code=404, detail="style not found")
    mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
    return FileResponse(str(p), media_type=mime,
                        headers={"Cache-Control": "no-cache"})


@router.post("/pipeline-run")
def post_pipeline_run() -> dict[str, Any]:
    """Artifacts tab '3 More' button: run the twice-daily content pipeline
    on demand (gap-finder sweep -> 3 new topics x 3 scripts each)."""
    _migrate_cron_prompt()
    try:
        from cron import jobs as cron_jobs
    except ImportError:
        raise HTTPException(503, "cron unavailable")
    job = cron_jobs.resolve_job_ref("youtube-content-pipeline")
    if not job:
        raise HTTPException(404, "youtube-content-pipeline job not found")
    state = _pipeline_state(job)
    if state["running"]:
        return {"ok": True, "alreadyRunning": True}
    res = cron_jobs.trigger_job(job["id"])
    if not res:
        raise HTTPException(500, "could not trigger the pipeline job")
    return {"ok": True}


def _pipeline_state(job: dict) -> dict[str, Any]:
    try:
        from cron import executions as cron_execs
        rows = cron_execs.list_executions(job_id=job["id"], limit=1)
    except Exception:
        rows = []
    last = rows[0] if rows else {}
    return {
        "running": (last.get("status") == "running"),
        "lastStatus": last.get("status"),
        "lastStarted": str(last.get("started_at") or "") or None,
        "lastFinished": str(last.get("finished_at") or "") or None,
    }


@router.get("/pipeline-state")
def get_pipeline_state() -> dict[str, Any]:
    try:
        from cron import jobs as cron_jobs
    except ImportError:
        return {"available": False, "running": False}
    job = cron_jobs.resolve_job_ref("youtube-content-pipeline")
    if not job:
        return {"available": False, "running": False}
    return {"available": True, **_pipeline_state(job)}


@router.post("/fetch")
def post_fetch() -> dict[str, Any]:
    api_key = _transcript_api_key()
    if not api_key:
        return {"error": "TRANSCRIPT_API_KEY is not set — add it in "
                         "Settings → Environment or ~/.hermes/.env"}
    with _FETCH_LOCK:
        if _FETCH_STATE["running"]:
            return {"ok": True, "queued": True, "alreadyRunning": True}
        _FETCH_STATE["running"] = True

    def _run() -> None:
        conn = yti_store.connect()
        try:
            summary = yti_fetcher.run_fetch(conn, api_key)
            _FETCH_STATE["last"] = summary
        except Exception as exc:  # noqa: BLE001
            _FETCH_STATE["last"] = {"errors": [str(exc)]}
        finally:
            _FETCH_STATE["running"] = False
            conn.close()

    threading.Thread(target=_run, name="yti-fetch", daemon=True).start()
    return {"ok": True, "queued": True}


@router.post("/trigger-analysis")
def post_trigger_analysis(body: Optional[dict] = None) -> dict[str, Any]:
    body = body or {}
    conn = yti_store.connect()
    try:
        return yti_analysis.trigger_analysis(
            conn, limit=body.get("limit"),
            order_by=str(body.get("orderBy") or "vph"),
        )
    finally:
        conn.close()


@router.get("/insights")
def get_insights(q: str = "", category: str = "", sortBy: str = "sources",
                 limit: int = 30, offset: int = 0) -> dict[str, Any]:
    conn = yti_store.connect()
    try:
        return yti_insights.search_insights(
            conn, query=q, category=category, sort_by=sortBy,
            limit=max(1, min(limit, 200)), offset=max(0, offset),
        )
    finally:
        conn.close()


@router.get("/insights/stats")
def get_insight_stats() -> dict[str, Any]:
    conn = yti_store.connect()
    try:
        return yti_insights.insight_stats(conn)
    finally:
        conn.close()


@router.delete("/insights/{insight_id}")
def delete_insight(insight_id: str) -> dict[str, Any]:
    conn = yti_store.connect()
    try:
        return yti_insights.delete_insight(conn, insight_id)
    finally:
        conn.close()


# -- workspace deliverables (Artifacts tab) ----------------------------------

class WorkspaceWrite(BaseModel):
    path: str
    content: str


@router.get("/workspace/tree")
def get_workspace_tree() -> dict[str, Any]:
    return {"tree": yti_workspace.build_tree(),
            "workspaceRoot": str(yti_paths.workspace_dir())}


@router.get("/workspace/file")
def get_workspace_file(path: str) -> dict[str, Any]:
    result = yti_workspace.read_file(path)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


@router.put("/workspace/file")
def put_workspace_file(body: WorkspaceWrite) -> dict[str, Any]:
    result = yti_workspace.write_file(body.path, body.content)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


# ---------------------------------------------------------------------------
# Accomplishments — read by the acvc /accomplishments aggregator and shown
# on the hermes Achievements page. Full credit when every item is done.
# ---------------------------------------------------------------------------

ACHIEVEMENT = {
    "id": "long-form-scholar",
    "name": "Long Form Scholar",
    "icon": "🎬",
    "description": "Study the long-form winners: track channels, pull "
                   "their uploads, and distill the insights.",
}


def achievements_progress() -> dict:
    conn = yti_store.connect()
    try:
        channels = conn.execute("SELECT COUNT(*) FROM channels").fetchone()[0]
        videos = conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
        insights = conn.execute("SELECT COUNT(*) FROM insights").fetchone()[0]
    finally:
        conn.close()
    items = [
        {"id": "channel", "label": "Track a channel", "done": channels > 0},
        {"id": "videos", "label": "Pull its uploads", "done": videos > 0},
        {"id": "insights", "label": "Generate insights",
         "done": insights > 0},
    ]
    return {"items": items, "complete": all(i["done"] for i in items)}


# ---------------------------------------------------------------------------
# YouTube Research tab — niche crawl / outliers / formats / gap / teardown.
# Thin wrappers over yti_rs_* (the same code the yt_research tool runs).
# ---------------------------------------------------------------------------

import json  # noqa: E402
import yti_rs_db  # noqa: E402
import yti_rs_config  # noqa: E402
import yti_rs_jobs  # noqa: E402
import yti_rs_budget  # noqa: E402
import yti_rs_formats  # noqa: E402
import yti_rs_report  # noqa: E402
import yti_rs_profiles  # noqa: E402
import yti_rs_crawl  # noqa: E402


def _rconn():
    return yti_rs_db.connect()


@router.get("/research/overview")
def research_overview() -> dict[str, Any]:
    conn = _rconn()
    dconn = yti_store.connect()
    try:
        cfg = yti_rs_config.load_config(conn)
        followed = yti_store.list_channels(dconn)
        tracked = conn.execute("SELECT COUNT(*) FROM channels WHERE is_tracked = 1").fetchone()[0]
        last = {k: yti_rs_db.get_meta(conn, f"last_{k}_run") for k in
                ("snapshot", "score", "enrich", "transcripts", "comments", "formats", "report")}
        runs = yti_rs_db.rows(conn, "SELECT run_id, started_at, finished_at, status, stats_json FROM crawl_runs "
                                    "ORDER BY started_at DESC LIMIT 8")
        for r in runs:
            try:
                r["stats"] = json.loads(r.pop("stats_json") or "{}")
            except json.JSONDecodeError:
                r["stats"] = {}
        niches = [n["name"] for n in cfg.get("niches") or []]
        return {
            "config": cfg, "configProblems": yti_rs_config.validate_config(cfg) if cfg.get("niches") else [],
            "secrets": yti_rs_config.get_secrets().present(),
            "followedChannels": followed, "trackedResearchChannels": tracked,
            "counts": yti_rs_db.counts(conn), "lastRuns": last, "crawlRuns": runs,
            "job": yti_rs_jobs.state(), "history": yti_rs_jobs.load_history(),
            "niches": niches, "maturityCurve": yti_rs_db.get_meta_json(conn, "maturity_curve"),
            "lastReportDir": yti_rs_db.get_meta(conn, "last_report_dir"),
        }
    finally:
        conn.close()
        dconn.close()



class ResearchConfigBody(BaseModel):
    config: dict


@router.put("/research/config")
def research_put_config(body: ResearchConfigBody) -> dict[str, Any]:
    conn = _rconn()
    try:
        cfg = yti_rs_config.save_config(conn, body.config)
        return {"ok": True, "config": cfg}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        conn.close()


class ResearchRunBody(BaseModel):
    job: str
    params: dict = {}


@router.post("/research/run")
def research_run(body: ResearchRunBody) -> dict[str, Any]:
    if body.job not in yti_rs_jobs.JOBS:
        raise HTTPException(status_code=400, detail=f"unknown job {body.job}")
    if body.job == "crawl" and body.params.get("dry_run"):
        conn = _rconn()
        try:
            cfg = yti_rs_config.load_config(conn)
            return {"ok": True, "dryRun": True, "plan": yti_rs_crawl.plan(cfg, body.params.get("niches") or None)}
        finally:
            conn.close()
    try:
        return yti_rs_jobs.start(body.job, body.params)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/research/job")
def research_job() -> dict[str, Any]:
    return yti_rs_jobs.state()


def _size_band(conn, size: str) -> tuple[Optional[int], Optional[int]]:
    """'band' = the subscriber band from Setup (default up to 100k); 'any' = no size filter;
    a number = that many subscribers at most."""
    crawl = yti_rs_config.load_config(conn).get("crawl", {})
    if size == "any":
        return None, None
    if size.isdigit():
        return None, int(size)
    return int(crawl.get("min_subscribers", 0) or 0) or None, int(crawl.get("max_subscribers", 0) or 0) or None


@router.get("/research/outliers")
def research_outliers(niche: str = "", classes: str = "strong_hit,hit", limit: int = 100, offset: int = 0,
                      sort: str = "projected_multiple", order: str = "desc",
                      size: str = "band") -> dict[str, Any]:
    conn = _rconn()
    try:
        cls = [c for c in classes.split(",") if c] or None
        lo, hi = _size_band(conn, size)
        return yti_rs_report.outlier_register(conn, niche=niche or None, classes=cls,
                                              limit=max(1, min(limit, 500)), offset=max(0, offset), sort=sort,
                                              order=order, min_subs=lo, max_subs=hi)
    finally:
        conn.close()


@router.get("/research/demand")
def research_demand() -> dict[str, Any]:
    conn = _rconn()
    try:
        return yti_rs_report.demand_map(conn, yti_rs_config.load_config(conn))
    finally:
        conn.close()


@router.get("/research/supply-demand")
def research_supply_demand(niche: str = "") -> dict[str, Any]:
    """Demand (multiple of normal views) against supply (time since publish)."""
    conn = _rconn()
    try:
        return yti_rs_report.supply_demand(conn, yti_rs_config.load_config(conn), niche=niche or None)
    finally:
        conn.close()


@router.get("/research/formats")
def research_formats(niche: str = "") -> dict[str, Any]:
    conn = _rconn()
    try:
        cfg = yti_rs_config.load_config(conn)
        return {"formats": yti_rs_formats.library(conn, niche or None),
                "minActionableN": int(cfg.get("formats", {}).get("min_actionable_n", 10))}
    finally:
        conn.close()


@router.get("/research/gaps")
def research_gaps(minWilson: Optional[float] = None) -> dict[str, Any]:
    conn = _rconn()
    try:
        return yti_rs_formats.gap_report(conn, yti_rs_config.load_config(conn), minWilson)
    finally:
        conn.close()


@router.get("/research/channels")
def research_channels(niche: str = "", limit: int = 300) -> dict[str, Any]:
    conn = _rconn()
    try:
        params: list[Any] = []
        where = ""
        if niche:
            where = " WHERE c.niche = ?"
            params.append(niche)
        rows = yti_rs_db.rows(conn, f"""
            SELECT c.channel_id, c.handle, c.title, c.niche, c.is_tracked, c.subscriber_count, c.subscriber_approx,
                   (SELECT COUNT(*) FROM videos v LEFT JOIN scores s ON s.video_id = v.video_id
                     WHERE v.channel_id = c.channel_id AND v.is_short = 0 AND COALESCE(s.in_niche, 1) = 1) AS videos,
                   (SELECT COUNT(*) FROM videos v JOIN scores s ON s.video_id = v.video_id
                     WHERE v.channel_id = c.channel_id AND s.format_bucket = 'long' AND s.in_niche = 1
                       AND s.class IN ('hit','strong_hit')) AS hits,
                   (SELECT MAX(s.projected_multiple) FROM videos v JOIN scores s ON s.video_id = v.video_id
                     WHERE v.channel_id = c.channel_id AND s.format_bucket = 'long' AND s.in_niche = 1) AS top_multiple,
                   (SELECT COUNT(*) FROM channel_profiles p WHERE p.channel_id = c.channel_id) AS profiled
            FROM channels c{where}{' AND' if where else ' WHERE'}
                 (c.is_tracked = 1 OR EXISTS (SELECT 1 FROM videos v LEFT JOIN scores s ON s.video_id = v.video_id
                                              WHERE v.channel_id = c.channel_id AND v.is_short = 0
                                                AND COALESCE(s.in_niche, 1) = 1))
            ORDER BY hits DESC, videos DESC LIMIT ?""", params + [max(1, min(limit, 1000))])
        return {"channels": rows}
    finally:
        conn.close()


class TrackBody(BaseModel):
    channelId: str
    tracked: bool


@router.post("/research/channels/track")
def research_track(body: TrackBody) -> dict[str, Any]:
    conn = _rconn()
    try:
        conn.execute("UPDATE channels SET is_tracked = ? WHERE channel_id = ?", (int(body.tracked), body.channelId))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.get("/research/profile")
def research_profile(channel: str) -> dict[str, Any]:
    conn = _rconn()
    try:
        ident = channel.strip()
        row = yti_rs_db.one(conn, "SELECT * FROM channels WHERE channel_id = ? OR lower(handle) = lower(?)",
                            (ident, ident if ident.startswith("@") else "@" + ident))
        if not row:
            raise HTTPException(status_code=404, detail="channel not in the research DB")
        prof = yti_rs_profiles.load_profile(conn, row["channel_id"])
        series = yti_rs_db.rows(conn, """
            SELECT v.video_id, v.title, v.published_at, v.views, s.projected_views, s.class, s.projected_multiple
            FROM videos v LEFT JOIN scores s ON s.video_id = v.video_id
            WHERE v.channel_id = ? AND v.published_at IS NOT NULL AND v.is_short = 0
            ORDER BY v.published_at""", (row["channel_id"],))
        md = yti_rs_report.render_d5(conn, row["channel_id"], prof) if prof else None
        return {"channel": row, "profile": prof, "series": series, "markdown": md}
    finally:
        conn.close()


@router.get("/research/budget")
def research_budget(days: int = 30) -> dict[str, Any]:
    conn = _rconn()
    try:
        cfg = yti_rs_config.load_config(conn)
        return {**yti_rs_budget.ledger(conn, max(1, min(days, 365))), "caps": cfg.get("budget", {}),
                "quarantine": conn.execute("SELECT COUNT(*) FROM quarantine").fetchone()[0]}
    finally:
        conn.close()


@router.post("/research/doctor")
def research_doctor(body: Optional[dict] = None) -> dict[str, Any]:
    body = body or {}
    return yti_rs_jobs.doctor(run_sample=bool(body.get("runSample")))


@router.get("/research/reports")
def research_reports() -> dict[str, Any]:
    conn = _rconn()
    try:
        last = yti_rs_db.get_meta(conn, "last_report_dir")
    finally:
        conn.close()
    files: list[dict[str, Any]] = []
    root = yti_paths.workspace_dir()
    base = root / "research" / "reports"
    if base.is_dir():
        for d in sorted(base.iterdir(), reverse=True)[:10]:
            if d.is_dir():
                for f in sorted(d.iterdir()):
                    if f.suffix == ".md":
                        files.append({"date": d.name, "name": f.name, "relPath": str(f.relative_to(root)),
                                      "size": f.stat().st_size})
    return {"files": files, "lastDir": last}


@router.get("/research/report-file")
def research_report_file(path: str) -> dict[str, Any]:
    result = yti_workspace.read_file(path)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


# -- niche interview: the Setup panel talks to the operator and fills the form ----------------

import yti_rs_elicit  # noqa: E402


class NicheSeedBody(BaseModel):
    seed: str = ""


class NicheAnswerBody(BaseModel):
    text: str


@router.get("/research/niche-interview")
def niche_interview_state() -> dict[str, Any]:
    conn = _rconn()
    try:
        return yti_rs_elicit.state(conn)
    finally:
        conn.close()


@router.post("/research/niche-interview/start")
def niche_interview_start(body: NicheSeedBody) -> dict[str, Any]:
    conn = _rconn()
    try:
        return yti_rs_elicit.start(conn, body.seed or "")
    except Exception as exc:  # noqa: BLE001 — model/provider errors reach the page as a message
        raise HTTPException(status_code=502, detail=str(exc))
    finally:
        conn.close()


@router.post("/research/niche-interview/answer")
def niche_interview_answer(body: NicheAnswerBody) -> dict[str, Any]:
    if not (body.text or "").strip():
        raise HTTPException(status_code=400, detail="answer text required")
    conn = _rconn()
    try:
        return yti_rs_elicit.answer(conn, body.text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc))
    finally:
        conn.close()


@router.post("/research/niche-interview/cancel")
def niche_interview_cancel() -> dict[str, Any]:
    conn = _rconn()
    try:
        return yti_rs_elicit.cancel(conn)
    finally:
        conn.close()


@router.post("/research/niche-interview/undo")
def niche_interview_undo() -> dict[str, Any]:
    conn = _rconn()
    try:
        return yti_rs_elicit.undo(conn)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        conn.close()
