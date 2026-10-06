"""Markdown deliverables D1–D5 (spec §1.1). Written under the plugin
workspace so the Artifacts tab can browse them:

    workspace/research/reports/<date>/D1-demand-map.md … D5-<channel>.md
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    from . import yti_paths, yti_rs_db, yti_rs_formats, yti_rs_profiles
    from .yti_rs_packaging import content_tokens
except ImportError:  # pragma: no cover
    import yti_paths  # type: ignore
    import yti_rs_db  # type: ignore
    import yti_rs_formats  # type: ignore
    import yti_rs_profiles  # type: ignore
    from yti_rs_packaging import content_tokens  # type: ignore

_REQUEST = re.compile(r"(?i)\b(?:make|do|create|post|upload|drop) (?:a |an |more )?(?:video|vid|one|tutorial|episode|series)s? (?:on|about|covering|explaining) (?P<topic>[^.!?\n]{4,80})"
                      r"|(?:can|could|would) you (?:please )?(?:make|do|cover|explain|talk about|go over|review) (?P<topic2>[^.!?\n]{4,80})"
                      r"|please (?:make|do|cover|explain) (?P<topic3>[^.!?\n]{4,80})")


def reports_dir(date: Optional[str] = None) -> Path:
    d = yti_paths.workspace_dir() / "research" / "reports" / (date or datetime.now(timezone.utc).date().isoformat())
    d.mkdir(parents=True, exist_ok=True)
    return d


def _approx_note(v: dict[str, Any]) -> str:
    flags = []
    if v.get("views_approx"):
        flags.append("views≈")
    if v.get("published_approx"):
        flags.append("date≈")
    if v.get("raw_only"):
        flags.append("raw")
    return " ".join(flags)


def _fmt(x: Any, nd: int = 2) -> str:
    if x is None:
        return "—"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


# -- D1 -------------------------------------------------------------------------------------

def demand_map(conn: sqlite3.Connection, cfg: dict[str, Any], bucket: str = "long") -> dict[str, Any]:
    rows = yti_rs_db.rows(conn, """
        SELECT v.video_id, v.title, v.niche, v.channel_id, s.class, s.projected_multiple, s.signal_weight
        FROM videos v JOIN scores s ON s.video_id = v.video_id
        WHERE s.class IN ('hit','strong_hit') AND s.format_bucket = ? AND s.in_niche = 1""", (bucket,))
    per_niche: dict[str, Counter] = defaultdict(Counter)
    channels: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for r in rows:
        w = (r["projected_multiple"] or 1.0) * (r["signal_weight"] or 1.0)
        for t in content_tokens(r["title"]):
            per_niche[r["niche"]][t] += w
            channels[r["niche"]][t].add(r["channel_id"])
    subjects = {}
    for niche, ctr in per_niche.items():
        subjects[niche] = [{"term": t, "weight": round(w, 2), "channels": len(channels[niche][t])}
                           for t, w in ctr.most_common(40) if len(channels[niche][t]) >= 2][:25]
    terms = yti_rs_db.rows(conn, """
        SELECT niche, node_key AS term, SUM(yield_count) AS yield, COUNT(*) AS runs
        FROM crawl_nodes WHERE node_type = 'search_term' AND status != 'pending'
        GROUP BY niche, node_key ORDER BY yield DESC LIMIT 60""")
    requests: Counter = Counter()
    for c in yti_rs_db.rows(conn, "SELECT text FROM comments"):
        m = _REQUEST.search(c["text"] or "")
        if m:
            topic = (m.group("topic") or m.group("topic2") or m.group("topic3") or "").strip().lower()
            if topic:
                requests[topic] += 1
    return {"subjects": subjects, "search_terms": terms,
            "requests": [{"topic": t, "n": n} for t, n in requests.most_common(30)],
            "hit_videos": len(rows)}


def render_d1(d: dict[str, Any]) -> str:
    out = ["# D1 — Demand map", "", f"Subjects with proven pull, derived from {d['hit_videos']} hit videos "
           "(term weight = Σ projected multiple × signal weight; ≥2 channels).", ""]
    for niche, subs in d["subjects"].items():
        out.append(f"## {niche}")
        out.append("")
        out.append("| term | weight | channels |")
        out.append("|---|---|---|")
        for s in subs:
            out.append(f"| {s['term']} | {s['weight']} | {s['channels']} |")
        out.append("")
    out += ["## Search terms by outlier yield", "", "| niche | term | new outliers |", "|---|---|---|"]
    out += [f"| {t['niche']} | {t['term']} | {t['yield']} |" for t in d["search_terms"]]
    if d["requests"]:
        out += ["", "## Viewer requests mined from comments", ""]
        out += [f"- {r['topic']} ({r['n']})" for r in d["requests"]]
    return "\n".join(out) + "\n"


# -- D2 -------------------------------------------------------------------------------------

def outlier_register(conn: sqlite3.Connection, *, niche: Optional[str] = None,
                     classes: Optional[list[str]] = None, limit: int = 200, offset: int = 0,
                     sort: str = "projected_multiple", bucket: str = "long",
                     order: str = "desc", min_subs: Optional[int] = None,
                     max_subs: Optional[int] = None) -> dict[str, Any]:
    """Scored videos in ONE format bucket (default long-form). Shorts are
    scored against their own baselines and kept in the DB, but never mixed
    into a long-form register."""
    # in-niche only; channel size is a filter (followed/tracked channels and
    # channels whose size is not known yet are never filtered out by it)
    where, params = ["s.format_bucket = ?", "s.in_niche = 1"], [bucket]
    if niche:
        where.append("v.niche = ?"); params.append(niche)
    if max_subs is not None:
        where.append("(c.subscriber_count IS NULL OR c.is_tracked = 1 OR c.subscriber_count <= ?)"); params.append(int(max_subs))
    if min_subs is not None:
        where.append("(c.subscriber_count IS NULL OR c.is_tracked = 1 OR c.subscriber_count >= ?)"); params.append(int(min_subs))
    base_where, base_params = list(where), list(params)
    if classes:
        where.append("s.class IN (" + ",".join("?" for _ in classes) + ")"); params.extend(classes)
    w = (" WHERE " + " AND ".join(where)) if where else ""
    # every column of the register is sortable; sorting happens here because the
    # tab only loads a page of rows
    sort_col = {
        "projected_multiple": "s.projected_multiple", "multiple": "s.multiple", "views": "v.views",
        "age": "s.age_days", "vs": "s.vs_percentile", "z": "s.log_mad_z", "weight": "s.signal_weight",
        "title": "v.title COLLATE NOCASE", "niche": "v.niche COLLATE NOCASE", "subs": "c.subscriber_count",
        "channel": "COALESCE(c.handle, c.title, v.channel_id) COLLATE NOCASE",
        "class": "CASE s.class WHEN 'strong_hit' THEN 5 WHEN 'hit' THEN 4 WHEN 'normal' THEN 3 "
                 "WHEN 'under' THEN 2 ELSE 1 END",
        # data quality: exact (tier 2) first, then exact views/dates, then the watch flags
        "flags": "(v.precision_tier * 8 + (1 - v.views_approx) * 4 + (1 - v.published_approx) * 2 "
                 "+ s.breakout_watch)",
    }.get(sort, "s.projected_multiple")
    direction = "ASC" if str(order).lower() == "asc" else "DESC"
    join = ("FROM videos v JOIN scores s ON s.video_id = v.video_id "
            "LEFT JOIN channels c ON c.channel_id = v.channel_id")
    total = conn.execute(f"SELECT COUNT(*) {join}{w}", params).fetchone()[0]
    rows = yti_rs_db.rows(conn, f"""
        SELECT v.video_id, v.title, v.niche, v.channel_id, c.handle, c.title AS channel_title, v.published_at,
               v.published_approx, v.views, v.views_approx, v.likes, v.comment_count, v.duration_seconds,
               v.thumbnail_url, v.precision_tier, v.discovered_via, c.subscriber_count, c.subscriber_approx,
               c.is_tracked, s.*
        {join}
        {w} ORDER BY {sort_col} {direction} NULLS LAST, s.projected_multiple DESC NULLS LAST, v.video_id
        LIMIT ? OFFSET ?""", params + [limit, offset])
    counts = {r["class"]: r["n"] for r in yti_rs_db.rows(conn, f"""
        SELECT s.class, COUNT(*) AS n {join} WHERE {" AND ".join(base_where)} GROUP BY s.class""", base_params)}
    return {"rows": rows, "total": total, "counts": counts, "bucket": bucket,
            "min_subs": min_subs, "max_subs": max_subs}


# -- supply / demand ------------------------------------------------------------------------

_REL_AGE = re.compile(r'"publishedTimeText":\s*"[^"]*?(\d+)\s*(second|sec|minute|min|hour|hr|day|week|month|year)s?\s+ago', re.I)
_UNIT_DAYS = {"second": 0.0, "sec": 0.0, "minute": 0.0, "min": 0.0, "hour": 1 / 24, "hr": 1 / 24,
              "day": 1.0, "week": 7.0, "month": 30.44, "year": 365.25}


def supply_demand(conn: sqlite3.Connection, cfg: dict[str, Any], *, niche: Optional[str] = None,
                  bucket: str = "long", now: Optional[datetime] = None) -> dict[str, Any]:
    """Points for the supply/demand view: demand = multiple of the channel's
    normal views, supply = time since publish (the longer an idea has been
    out, the more of it exists and the less a multiple is worth).

    Each point carries an age RANGE, not a false-precise age: ``a`` is the
    youngest the video can be and ``s`` the width of the uncertainty in days
    (0 for exact dates). "2 months ago" means two-to-three months, so its
    range is [age, age + one month); a date estimated from a channel's
    upload cadence is symmetric around the estimate.
    """
    now = now or datetime.now(timezone.utc)
    where, params = ["s.format_bucket = ?", "s.class != 'immature'", "s.projected_multiple IS NOT NULL",
                     "v.published_at IS NOT NULL", "s.in_niche = 1"], [bucket]
    if niche:
        where.append("v.niche = ?"); params.append(niche)
    rows = conn.execute(f"""
        SELECT v.video_id, v.title, v.niche, v.channel_id, c.handle, c.title AS channel_title, v.published_at,
               v.published_approx, v.published_granularity_days, v.views, v.views_approx, v.raw_json,
               c.subscriber_count, c.is_tracked,
               s.projected_multiple, s.multiple, s.class, s.raw_only, s.organic_flag
        FROM videos v JOIN scores s ON s.video_id = v.video_id
        LEFT JOIN channels c ON c.channel_id = v.channel_id
        WHERE {" AND ".join(where)}""", params).fetchall()
    points = []
    for r in rows:
        try:
            pub = datetime.fromisoformat(str(r["published_at"]).replace("Z", "+00:00"))
        except ValueError:
            continue
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
        age = max(0.0, (now - pub).total_seconds() / 86400)
        lo, span = age, 0.0
        if r["published_approx"]:
            m = _REL_AGE.search(r["raw_json"] or "")
            if m:                                    # "N units ago": N..N+1 units old
                span = _UNIT_DAYS.get(m.group(2).lower(), 0.0)
            else:                                    # cadence estimate: symmetric
                g = float(r["published_granularity_days"] or 0.0)
                lo, span = max(0.0, age - g), 2 * g
        flags = []
        if r["views_approx"]:
            flags.append("views≈")
        if r["organic_flag"] == "suspect_paid":
            flags.append("paid?")
        if not r["raw_only"] and age < 28:
            flags.append("projected")
        points.append({"id": r["video_id"], "t": r["title"], "ch": r["handle"] or r["channel_title"] or r["channel_id"],
                       "n": r["niche"], "a": round(lo, 2), "s": round(span, 2),
                       "m": round(float(r["projected_multiple"]), 3), "v": r["views"], "c": r["class"], "f": flags,
                       "sub": r["subscriber_count"], "fol": int(bool(r["is_tracked"]))})
    s_cfg = cfg.get("scoring", {})
    c_cfg = cfg.get("crawl", {})
    return {"points": points, "bucket": bucket,
            "min_subs": int(c_cfg.get("min_subscribers", 0) or 0), "max_subs": int(c_cfg.get("max_subscribers", 0) or 0),
            "half_life": {n["name"]: float(n.get("signal_half_life_days") or 365) for n in cfg.get("niches") or []},
            "default_half_life": 365.0, "hit_multiple": float(s_cfg.get("hit_multiple", 3.0)),
            "niches": sorted({p["n"] for p in points}), "generated_at": now.isoformat()}


def render_d2(d: dict[str, Any]) -> str:
    out = ["# D2 — Outlier register", "", "Baseline-relative (P1): multiple = views ÷ trailing median of the "
           "channel's previous 20 uploads in the same bucket. ≈ marks approximate Tier-1 numbers; raw = age "
           "too coarse for projection.", "", f"Class counts: {json.dumps(d['counts'])}", "",
           "| class | ×proj | ×raw | views | age d | channel | title | niche | flags |", "|---|---|---|---|---|---|---|---|---|"]
    for r in d["rows"]:
        out.append(f"| {r['class']} | {_fmt(r['projected_multiple'])} | {_fmt(r['multiple'])} | {r['views']} | "
                   f"{_fmt(r['age_days'], 0)} | {r.get('handle') or r.get('channel_title') or r['channel_id']} | "
                   f"[{(r['title'] or '')[:70]}](https://www.youtube.com/watch?v={r['video_id']}) | {r['niche']} | "
                   f"{_approx_note(r)} {r.get('organic_flag') or ''} {'BREAKOUT' if r.get('breakout_watch') else ''} |")
    return "\n".join(out) + "\n"


# -- D3 / D4 ------------------------------------------------------------------------------------

def render_d3(lib: list[dict[str, Any]], min_n: int) -> str:
    out = ["# D3 — Format library", "", "Ranked by the Wilson 95% lower bound of the hit rate (P2), never by hit "
           f"count. Formats with n < {min_n} are shown but not actionable.", "",
           "| format | kind | wilson_lb | hits/total | under | median × | weighted hit | channels | niches | target uses |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for f in lib:
        if not f.get("n_total"):
            continue
        out.append(f"| {f['label']} | {f['kind']} | {_fmt(f['wilson_lb'], 3)} | {f['n_hits']}/{f['n_total']} | {f['n_under']} | "
                   f"{_fmt(f['median_multiple'])} | {_fmt(f['weighted_hit_rate'], 3)} | {f['distinct_channels']} | "
                   f"{', '.join(f['niches'])} | {f['target_niche_uses']} |")
    out += ["", "## Discriminators (hit rate with vs without; both arms n ≥ 5)", ""]
    for f in lib:
        if not f.get("discriminators"):
            continue
        out.append(f"### {f['label']}")
        for d in f["discriminators"][:6]:
            out.append(f"- {d['feature']} = {d['value']}: {d['hit_rate_with']:.2f} (n={d['n_with']}) vs "
                       f"{d['hit_rate_without']:.2f} (n={d['n_without']}) → Δ {d['diff']:+.2f}")
        out.append("")
    return "\n".join(out) + "\n"


def render_d4(g: dict[str, Any]) -> str:
    out = [f"# D4 — Format gap report (target niche: {g.get('target_niche') or 'not configured'})", "",
           f"> {yti_rs_formats.GAP_CAVEAT}", "",
           f"Gap = proven in ≥2 niches, wilson_lb ≥ {g['min_wilson_lb']}, zero uses in the target niche. "
           f"Recommendations are refused below n_total = {g['min_actionable_n']}.", ""]

    def block(rows: list[dict[str, Any]]) -> None:
        for r in rows:
            head = f"## {r['label']}" + ("" if r["actionable"] else "  ⚠ n too small — not actionable")
            out.append(head)
            if r.get("psychology"):
                out.append(f"*Psychology:* {r['psychology']}")
            if r.get("caution"):
                out.append(f"*Caution:* {r['caution']}")
            out.append(f"- wilson_lb {r['wilson_lb']:.3f} · hits {r['n_hits']}/{r['n_total']} · under {r['n_under']} · "
                       f"median × {_fmt(r['median_multiple'])} · proven in: {', '.join(r['niches'])}")
            for e in r.get("examples") or []:
                out.append(f"  - {e['multiple']}× [{e['title'][:80]}](https://www.youtube.com/watch?v={e['video_id']}) ({e['niche']}){' ≈' if e.get('approx') else ''}")
            for d in (r.get("discriminators") or [])[:4]:
                out.append(f"  - discriminator {d['feature']}={d['value']}: {d['hit_rate_with']:.2f} vs {d['hit_rate_without']:.2f} (n {d['n_with']}/{d['n_without']})")
            if r.get("known_discriminator"):
                out.append(f"  - known discriminator: {r['known_discriminator']}")
            out.append("")

    if not g["gaps"]:
        out.append("_No gap formats yet — crawl more niches or lower the Wilson threshold._")
        out.append("")
    block(g["gaps"])
    if g["near_gaps"]:
        out += ["---", "", "# Near-gaps (1–2 uses in the target niche)", ""]
        block(g["near_gaps"])
    return "\n".join(out) + "\n"


# -- D5 -------------------------------------------------------------------------------------

def render_d5(conn: sqlite3.Connection, channel_id: str, p: dict[str, Any]) -> str:
    ch = yti_rs_db.one(conn, "SELECT * FROM channels WHERE channel_id = ?", (channel_id,)) or {}
    name = ch.get("handle") or ch.get("title") or channel_id
    out = [f"# D5 — Channel teardown: {name}", "", f"{p.get('n_videos')} long-form uploads analysed."]
    cps = p.get("changepoints") or ([{"date": p["changepoint_date"], "p": p["changepoint_p"]}] if p.get("changepoint_date") else [])
    if cps:
        for cp in cps:
            out.append(f"- **Changepoint** {str(cp.get('date'))[:10]} (permutation p = {cp.get('p')})")
        out.append(f"- **Lift ratio** (median views after ÷ before): {_fmt(p.get('lift_ratio'))}")
    else:
        out.append("- No statistically significant changepoint (p > 0.05) — the channel has not inflected.")
    out.append(f"- **Coherence score** (mean pairwise TF-IDF cosine, last 20): {_fmt(p.get('coherence_score'), 3)}")
    if p.get("off_topic_hits"):
        out += ["", "## Off-topic hits (large multiple, poor model to double down on)", ""]
        out += [f"- {o['title']} — similarity {o['similarity']} < p20 {o['p20']}" for o in p["off_topic_hits"]]
    if p.get("cohort_diff"):
        out += ["", "## Cohort diff (sorted by effect size)", "", "| feature | before | after | n | effect |", "|---|---|---|---|---|"]
        for d in p["cohort_diff"][:30]:
            b = d["before"] if not isinstance(d["before"], (dict, list)) else json.dumps(d["before"])
            a = d["after"] if not isinstance(d["after"], (dict, list)) else json.dumps(d["after"])
            out.append(f"| {d['feature']} | {b} | {a} | {d['n_before']}/{d['n_after']} | {d['effect']} |")
    if p.get("doubling_down"):
        out += ["", "## Doubling-down candidates", ""]
        for c in p["doubling_down"]:
            out.append(f"- **{c['multiple']}×** {c['title']}")
            for f in c.get("formats") or []:
                out.append(f"  - format: {f['label']} · slots {json.dumps(f['slots'])}")
                for o in f.get("proven_elsewhere") or []:
                    out.append(f"    - proven in {o['niche']} at {o['multiple']}× with slots {json.dumps(o['slots'])}")
            out.append(f"  - profile: {json.dumps(c['profile'])}")
    if p.get("llm_teardown"):
        out += ["", "## Structural teardown", "", p["llm_teardown"]]
    return "\n".join(out) + "\n"


# -- write all ---------------------------------------------------------------------------------

def write_reports(conn: sqlite3.Connection, cfg: dict[str, Any], only: Optional[list[str]] = None,
                  date: Optional[str] = None) -> dict[str, Any]:
    only = [o.upper() for o in (only or ["D1", "D2", "D3", "D4", "D5"])]
    d = reports_dir(date)
    written: list[str] = []
    min_n = int(cfg.get("formats", {}).get("min_actionable_n", 10))
    if "D1" in only:
        (d / "D1-demand-map.md").write_text(render_d1(demand_map(conn, cfg)))
        written.append(str(d / "D1-demand-map.md"))
    if "D2" in only:
        reg = outlier_register(conn, classes=["strong_hit", "hit", "under"], limit=300)
        (d / "D2-outlier-register.md").write_text(render_d2(reg))
        written.append(str(d / "D2-outlier-register.md"))
    if "D3" in only:
        (d / "D3-format-library.md").write_text(render_d3(yti_rs_formats.library(conn), min_n))
        written.append(str(d / "D3-format-library.md"))
    if "D4" in only:
        (d / "D4-format-gap-report.md").write_text(render_d4(yti_rs_formats.gap_report(conn, cfg)))
        written.append(str(d / "D4-format-gap-report.md"))
    if "D5" in only:
        for r in yti_rs_db.rows(conn, "SELECT channel_id FROM channel_profiles"):
            p = yti_rs_profiles.load_profile(conn, r["channel_id"])
            if p:
                path = d / f"D5-teardown-{yti_paths.sanitize(r['channel_id'])}.md"
                path.write_text(render_d5(conn, r["channel_id"], p))
                written.append(str(path))
    yti_rs_db.set_meta(conn, "last_report_run", yti_rs_db.now_iso())
    yti_rs_db.set_meta(conn, "last_report_dir", str(d))
    return {"written": written, "dir": str(d), "relDir": str(d.relative_to(yti_paths.workspace_dir()))}
