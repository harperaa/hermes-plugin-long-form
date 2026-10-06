"""Background job runner + orchestration for the research engine.

One job at a time (they share research.db and the credit budget). State is
in-process (the dashboard backend is long-lived); the last result of each
job is persisted to ``meta`` so the tab can show history after a restart.

Every job is idempotent and resumable: re-running only fills what is
missing, and the crawler persists its stack on a budget stop.
"""
from __future__ import annotations

import threading
import time
import traceback
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable, Optional

try:
    from . import (yti_rs_budget, yti_rs_clients, yti_rs_config, yti_rs_crawl, yti_rs_db, yti_rs_enrich,
                   yti_rs_formats, yti_rs_outliers, yti_rs_packaging, yti_rs_profiles, yti_rs_report,
                   yti_rs_snapshot, yti_store)
    from .yti_rs_logging import get_logger, redact
except ImportError:  # pragma: no cover
    import yti_rs_budget, yti_rs_clients, yti_rs_config, yti_rs_crawl, yti_rs_db, yti_rs_enrich  # type: ignore
    import yti_rs_formats, yti_rs_outliers, yti_rs_packaging, yti_rs_profiles, yti_rs_report  # type: ignore
    import yti_rs_snapshot, yti_store  # type: ignore
    from yti_rs_logging import get_logger, redact  # type: ignore

log = get_logger("yti.research.jobs")

_LOCK = threading.Lock()
_STATE: dict[str, Any] = {"running": False, "job": None, "params": None, "started": None,
                          "finished": None, "result": None, "error": None,
                          "log": deque(maxlen=400), "history": deque(maxlen=20)}

JOB_ORDER = ["snapshot", "crawl", "enrich", "transcripts", "comments", "score", "packaging", "formats", "report"]


def followed_handles() -> list[str]:
    conn = yti_store.connect()
    try:
        return yti_store.list_channels(conn)
    finally:
        conn.close()


def known_durations() -> dict[str, int]:
    """Durations the dashboard already knows from transcripts (free)."""
    conn = yti_store.connect()
    try:
        return {r["video_id"]: int(r["duration_seconds"]) for r in conn.execute(
            "SELECT video_id, duration_seconds FROM videos WHERE duration_seconds IS NOT NULL")}
    except Exception:  # noqa: BLE001
        return {}
    finally:
        conn.close()


def _log_line(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {redact(msg)}"
    _STATE["log"].append(line)
    log.info(msg)


def state() -> dict[str, Any]:
    return {k: (list(v) if isinstance(v, deque) else v) for k, v in _STATE.items()}


def _tapi(budget: yti_rs_budget.Budget) -> yti_rs_clients.TranscriptAPI:
    s = yti_rs_config.require_secrets("transcriptapi")
    return yti_rs_clients.TranscriptAPI(s.transcriptapi_key, budget=budget)


def _apify(budget: yti_rs_budget.Budget, cfg: dict[str, Any]) -> yti_rs_clients.Apify:
    s = yti_rs_config.require_secrets("apify")
    a = cfg.get("apify", {})
    return yti_rs_clients.Apify(s.apify_token, budget=budget, sync_timeout_secs=int(a.get("sync_timeout_secs", 290)),
                                async_timeout_secs=int(a.get("async_timeout_secs", 1800)))


# -- job bodies --------------------------------------------------------------------------------

def job_snapshot(conn, cfg, params, log_fn) -> dict[str, Any]:
    budget = yti_rs_budget.Budget(conn, run_id="snapshot")
    tgt = yti_rs_config.target_niche(cfg)
    return yti_rs_snapshot.run_snapshot(conn, _tapi(budget), followed_handles(),
                                        default_niche=(tgt or {}).get("name") or "followed",
                                        durations=known_durations(), log=log_fn)


def job_crawl(conn, cfg, params, log_fn) -> dict[str, Any]:
    if not cfg.get("niches"):
        raise RuntimeError("configure at least one niche (with seed terms) before crawling")
    niches = params.get("niches") or None
    if params.get("dry_run"):
        return {"dry_run": True, **yti_rs_crawl.plan(cfg, niches)}
    cap = int(params.get("max_credits") or cfg.get("budget", {}).get("crawl_default_credits", 150))
    run_id = params.get("resume") or None
    budget = yti_rs_budget.Budget(conn, run_id=run_id, caps={"transcriptapi": cap})
    crawler = yti_rs_crawl.Crawler(conn, cfg, _tapi(budget), budget, run_id=run_id, log=log_fn)
    budget.run_id = crawler.run_id
    if run_id:
        if not crawler.resume(run_id):
            raise RuntimeError(f"no crawl run {run_id} to resume")
        log_fn(f"resuming {run_id} with {len(crawler.stack)} stacked nodes")
    else:
        crawler.seed(niches)
        if cfg.get("crawl", {}).get("include_followed_channels", True):
            n = crawler.seed_channels(followed_handles(), depth=1)
            log_fn(f"seeded {n} followed channel(s) as channel nodes")
        log_fn(f"run {crawler.run_id}: {len(crawler.stack)} seed nodes, cap {cap} credits")
    return crawler.run()


def job_enrich(conn, cfg, params, log_fn) -> dict[str, Any]:
    cap = int(params.get("max_results") or 200)
    budget = yti_rs_budget.Budget(conn, run_id="enrich", caps={"apify": int(cfg.get("budget", {}).get("apify_max_results", 5000))})
    classes = tuple(params.get("classes") or yti_rs_enrich.DEFAULT_CLASSES)
    return yti_rs_enrich.run_enrich(conn, cfg, _apify(budget, cfg), classes=classes, max_results=cap,
                                    include_under=bool(params.get("include_under", False)), log=log_fn)


def job_transcripts(conn, cfg, params, log_fn) -> dict[str, Any]:
    cap = int(params.get("max") or 50)
    budget = yti_rs_budget.Budget(conn, run_id="transcripts", caps={"transcriptapi": cap + 5})
    classes = tuple(params.get("classes") or yti_rs_enrich.DEFAULT_CLASSES)
    return yti_rs_enrich.run_transcripts(conn, cfg, _tapi(budget), classes=classes, max_n=cap,
                                         include_under=bool(params.get("include_under", True)), log=log_fn)


def job_comments(conn, cfg, params, log_fn) -> dict[str, Any]:
    per = int(params.get("max_per_video") or cfg.get("apify", {}).get("max_comments_per_video", 300))
    max_videos = int(params.get("max_videos") or 30)
    budget = yti_rs_budget.Budget(conn, run_id="comments", caps={"apify": int(cfg.get("budget", {}).get("apify_max_results", 5000))})
    classes = tuple(params.get("classes") or yti_rs_enrich.DEFAULT_CLASSES)
    return yti_rs_enrich.run_comments(conn, cfg, _apify(budget, cfg), classes=classes, max_per_video=per,
                                      max_videos=max_videos, include_under=bool(params.get("include_under", True)), log=log_fn)


def job_score(conn, cfg, params, log_fn) -> dict[str, Any]:
    res = yti_rs_outliers.score_all(conn, cfg, refit_curve=bool(params.get("refit_maturity_curve")),
                                    followed=set(followed_handles()))
    log_fn(f"scored {res['scored']} videos: {res['classes']}; {res['out_of_niche']} tagged out of niche "
           f"on {res['off_topic_channels']} off-topic channel(s)")
    return res


def job_sizes(conn, cfg, params, log_fn) -> dict[str, Any]:
    cap = int(params.get("max_channels") or 100)
    budget = yti_rs_budget.Budget(conn, run_id="sizes", caps={"transcriptapi": cap})
    res = yti_rs_enrich.run_sizes(conn, cfg, _tapi(budget), max_channels=cap, log=log_fn)
    log_fn(f"channel sizes: {res['sized']} sized, {res['not_found']} not found, "
           f"{budget.spent('transcriptapi')} credits")
    return {**res, "credits": budget.spent("transcriptapi")}


def job_packaging(conn, cfg, params, log_fn) -> dict[str, Any]:
    res = yti_rs_packaging.run_packaging(conn, cfg, only_missing=not params.get("all"))
    log_fn(f"packaging computed for {res['packaged']} transcripts")
    return res


def job_formats(conn, cfg, params, log_fn) -> dict[str, Any]:
    n_seed = yti_rs_formats.load_seeded(conn)
    n_match = yti_rs_formats.match_seeded(conn)
    mined = yti_rs_formats.mine_into_db(conn, cfg)
    val = yti_rs_formats.validate(conn, cfg)
    gap = yti_rs_formats.gap_report(conn, cfg)
    log_fn(f"formats: {n_seed} seeded ({n_match} matches), {mined['mined']} mined, {val['formats']} validated, "
           f"{len(gap['gaps'])} gaps, {len(gap['near_gaps'])} near-gaps")
    return {"seeded": n_seed, "seeded_matches": n_match, **mined, **val,
            "gaps": len(gap["gaps"]), "near_gaps": len(gap["near_gaps"])}


def job_profile(conn, cfg, params, log_fn) -> dict[str, Any]:
    ident = str(params.get("channel") or "").strip()
    if not ident:
        raise RuntimeError("channel required")
    row = yti_rs_db.one(conn, "SELECT channel_id FROM channels WHERE channel_id = ? OR lower(handle) = lower(?)",
                        (ident, ident if ident.startswith("@") else "@" + ident))
    if not row:
        raise RuntimeError(f"channel {ident} is not in the research DB — snapshot or crawl it first")
    res = yti_rs_profiles.profile_channel(conn, cfg, row["channel_id"], n_perm=int(params.get("n_perm") or 2000))
    log_fn(f"profile {row['channel_id']}: {len(res['changepoints'])} changepoint(s), lift {res['lift_ratio']}")
    return res


def job_report(conn, cfg, params, log_fn) -> dict[str, Any]:
    res = yti_rs_report.write_reports(conn, cfg, only=params.get("only"))
    log_fn(f"wrote {len(res['written'])} report(s) to {res['relDir']}")
    return res


def job_pipeline(conn, cfg, params, log_fn) -> dict[str, Any]:
    """The whole batch in build order. Apify stages are skipped (not failed)
    when APIFY_API_TOKEN is missing so the free/cheap path still produces D1–D4."""
    out: dict[str, Any] = {}
    have_apify = yti_rs_config.get_secrets().present()["apify"]
    steps: list[tuple[str, Callable]] = [("snapshot", job_snapshot), ("crawl", job_crawl), ("score", job_score),
                                         ("sizes", job_sizes)]
    if have_apify and not params.get("skip_apify"):
        steps.append(("enrich", job_enrich))
    steps += [("transcripts", job_transcripts)]
    if have_apify and not params.get("skip_apify"):
        steps.append(("comments", job_comments))
    steps += [("score2", job_score), ("packaging", job_packaging), ("formats", job_formats), ("report", job_report)]
    for name, fn in steps:
        log_fn(f"── pipeline step: {name}")
        try:
            out[name] = fn(conn, cfg, params, log_fn)
        except yti_rs_clients.CreditsExhausted as exc:
            out[name] = {"error": str(exc)}
            log_fn(f"pipeline aborted at {name}: {exc}")
            break
        except Exception as exc:  # noqa: BLE001 — one failed stage shouldn't hide the others
            out[name] = {"error": str(exc)}
            log_fn(f"step {name} failed: {exc}")
    return out


JOBS: dict[str, Callable] = {
    "snapshot": job_snapshot, "crawl": job_crawl, "enrich": job_enrich, "transcripts": job_transcripts,
    "comments": job_comments, "score": job_score, "packaging": job_packaging, "formats": job_formats,
    "profile": job_profile, "report": job_report, "pipeline": job_pipeline, "sizes": job_sizes,
}


# -- runner ---------------------------------------------------------------------------------------

def run_sync(job: str, params: Optional[dict[str, Any]] = None,
             log_fn: Optional[Callable[[str], None]] = None) -> dict[str, Any]:
    """Run a job in the calling thread (tools / CLI / tests)."""
    if job not in JOBS:
        raise ValueError(f"unknown job {job!r}; one of {', '.join(JOBS)}")
    conn = yti_rs_db.connect()
    try:
        cfg = yti_rs_config.load_config(conn)
        return JOBS[job](conn, cfg, params or {}, log_fn or _log_line)
    finally:
        conn.close()


def start(job: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Start a job in the background; refuses when one is already running."""
    if job not in JOBS:
        raise ValueError(f"unknown job {job!r}")
    with _LOCK:
        if _STATE["running"]:
            return {"ok": False, "alreadyRunning": True, "job": _STATE["job"]}
        _STATE.update({"running": True, "job": job, "params": params or {}, "started": yti_rs_db.now_iso(),
                       "finished": None, "result": None, "error": None})
        _STATE["log"].clear()

    def _run() -> None:
        t0 = time.monotonic()
        try:
            _log_line(f"▶ {job} {params or ''}")
            result = run_sync(job, params, _log_line)
            _STATE["result"] = result
            _log_line(f"✔ {job} done in {time.monotonic() - t0:.0f}s")
        except Exception as exc:  # noqa: BLE001
            _STATE["error"] = redact(str(exc))
            _log_line(f"✖ {job} failed: {exc}")
            log.debug("job traceback: %s", traceback.format_exc())
        finally:
            _STATE["finished"] = yti_rs_db.now_iso()
            _STATE["running"] = False
            _STATE["history"].appendleft({"job": job, "started": _STATE["started"], "finished": _STATE["finished"],
                                          "error": _STATE["error"], "result": _summarize(_STATE["result"])})
            try:
                conn = yti_rs_db.connect()
                yti_rs_db.set_meta(conn, "job_history", list(_STATE["history"]))
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    threading.Thread(target=_run, name=f"yti-research-{job}", daemon=True).start()
    return {"ok": True, "job": job}


def _summarize(result: Any) -> Any:
    if isinstance(result, dict):
        return {k: (v if isinstance(v, (int, float, str, bool, type(None))) else
                    (len(v) if isinstance(v, (list, dict)) else str(v))) for k, v in list(result.items())[:20]}
    return result


def load_history() -> list[dict[str, Any]]:
    if _STATE["history"]:
        return list(_STATE["history"])
    conn = yti_rs_db.connect()
    try:
        hist = yti_rs_db.get_meta_json(conn, "job_history", []) or []
    finally:
        conn.close()
    for h in hist:
        _STATE["history"].append(h)
    return hist


# -- doctor -----------------------------------------------------------------------------------------

def doctor(run_sample: bool = False) -> dict[str, Any]:
    """Env check, API reachability, actor probes, and field-name resolution."""
    secrets = yti_rs_config.get_secrets()
    out: dict[str, Any] = {"env": secrets.present(), "checks": []}
    conn = yti_rs_db.connect()
    try:
        cfg = yti_rs_config.load_config(conn)
        out["config_problems"] = yti_rs_config.validate_config(cfg) if cfg.get("niches") else ["no niches configured"]
        out["counts"] = yti_rs_db.counts(conn)
        out["db_path"] = str(yti_rs_db.db_path())
        budget = yti_rs_budget.Budget(conn, run_id="doctor")
        if secrets.transcriptapi_key:
            try:
                tapi = yti_rs_clients.TranscriptAPI(secrets.transcriptapi_key, budget=budget)
                r = tapi.resolve("@youtube")
                out["checks"].append({"name": "transcriptapi resolve (free)", "ok": bool(r.get("channel_id")),
                                      "detail": r.get("channel_id") or r.get("error")})
                info = tapi.info("dQw4w9WgXcQ")
                out["checks"].append({"name": "transcriptapi info (free)", "ok": bool(info.get("available_languages")),
                                      "detail": f"{len(info.get('available_languages') or [])} caption languages"})
            except Exception as exc:  # noqa: BLE001
                out["checks"].append({"name": "transcriptapi", "ok": False, "detail": redact(str(exc))})
        else:
            out["checks"].append({"name": "transcriptapi", "ok": False, "detail": "TRANSCRIPT_API_KEY missing"})
        if secrets.apify_token:
            try:
                apify = yti_rs_clients.Apify(secrets.apify_token, budget=budget)
                for key in ("search_actor", "channel_actor", "comments_actor"):
                    actor = cfg.get("apify", {}).get(key)
                    if not actor:
                        continue
                    p = apify.probe_actor(actor)
                    out["checks"].append({"name": f"apify {key}", "ok": bool(p.get("name")),
                                          "detail": f"{actor} → {p.get('title') or p.get('name')}"})
                if run_sample:
                    items = apify.scrape_videos(cfg["apify"]["search_actor"], ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"])
                    rec, hits = (None, {}) if not items else __import__("yti_rs_normalize").map_apify_video(items[0])
                    out["checks"].append({"name": "apify field resolution (1 result)", "ok": rec is not None,
                                          "detail": hits})
                    yti_rs_db.set_meta(conn, "apify_field_hits", hits)
                else:
                    out["checks"].append({"name": "apify field resolution (last enrichment)",
                                          "ok": bool(yti_rs_db.get_meta_json(conn, "apify_field_hits")),
                                          "detail": yti_rs_db.get_meta_json(conn, "apify_field_hits") or "not enriched yet"})
            except Exception as exc:  # noqa: BLE001
                out["checks"].append({"name": "apify", "ok": False, "detail": redact(str(exc))})
        else:
            out["checks"].append({"name": "apify", "ok": False,
                                  "detail": "APIFY_API_TOKEN missing — Tier 2/3 (exact metrics, comments) disabled"})
        out["maturity_curve"] = yti_rs_db.get_meta_json(conn, "maturity_curve")
    finally:
        conn.close()
    out["ok"] = all(c["ok"] for c in out["checks"] if c["name"].startswith("transcriptapi")) and not out["config_problems"]
    return out
