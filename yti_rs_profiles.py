"""Channel teardown (spec §11): permutation-tested changepoint, cohort diff,
coherence check, and doubling-down candidates. Pure stdlib.
"""
from __future__ import annotations

import json
import math
import random
import sqlite3
import statistics
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Optional

try:
    from . import yti_rs_db
    from .yti_rs_packaging import content_tokens
except ImportError:  # pragma: no cover
    import yti_rs_db  # type: ignore
    from yti_rs_packaging import content_tokens  # type: ignore


# -- §11.1 changepoint -------------------------------------------------------------------

def _mad_sigma(x: list[float]) -> float:
    med = statistics.median(x)
    mad = statistics.median(abs(v - med) for v in x)
    return 1.4826 * mad


def _max_split(x: list[float], min_seg: int, sigma: float) -> tuple[int, float]:
    n = len(x)
    best_i, best_t = -1, -1.0
    total = sum(x)
    left = 0.0
    for i in range(1, n):
        left += x[i - 1]
        if i < min_seg or n - i < min_seg:
            continue
        t = abs(left / i - (total - left) / (n - i)) / sigma
        if t > best_t:
            best_i, best_t = i, t
    return best_i, best_t


def changepoint(series: list[float], *, min_seg: int = 5, n_perm: int = 2000,
                seed: int = 0) -> Optional[dict[str, Any]]:
    """Return {index, stat, p} for the strongest mean shift, or None when the
    series is too short or the pooled MAD is zero. ``p`` comes from a
    permutation test over the *max* statistic, so it accounts for having
    maximised over every split point."""
    x = [float(v) for v in series]
    if len(x) < 2 * min_seg:
        return None
    sigma = _mad_sigma(x)
    if sigma <= 0:
        return None
    idx, t_obs = _max_split(x, min_seg, sigma)
    if idx < 0:
        return None
    rng = random.Random(seed)
    ge = 0
    perm = list(x)
    for _ in range(n_perm):
        rng.shuffle(perm)
        _, t = _max_split(perm, min_seg, sigma)
        if t >= t_obs:
            ge += 1
    return {"index": idx, "stat": t_obs, "p": (ge + 1) / (n_perm + 1)}


def detect_changepoints(series: list[float], *, min_seg: int = 5, n_perm: int = 2000,
                        alpha: float = 0.05, max_points: int = 2, seed: int = 0) -> list[dict[str, Any]]:
    """Binary segmentation capped at two changepoints; each must pass alpha."""
    first = changepoint(series, min_seg=min_seg, n_perm=n_perm, seed=seed)
    if not first or first["p"] > alpha:
        return []
    found = [first]
    if max_points > 1:
        i = first["index"]
        left, right = series[:i], series[i:]
        seg, offset = (left, 0) if len(left) >= len(right) else (right, i)
        second = changepoint(seg, min_seg=min_seg, n_perm=n_perm, seed=seed + 1)
        if second and second["p"] <= alpha:
            found.append({**second, "index": second["index"] + offset})
    return sorted(found, key=lambda c: c["index"])


# -- §11.2 cohort diff -------------------------------------------------------------------

NUMERIC = ["title_words", "point_count", "duration_seconds", "promise_restated_sec", "filler_rate",
           "like_rate", "comment_rate", "vs_percentile", "projected_multiple"]
RATES = ["title_lowercase", "has_promise", "has_proof", "has_plan", "has_persona", "mismatch_risk",
         "is_short", "question_title", "numeral_title", "uses_seeded_format", "lead_magnet"]
CATEGORICAL = ["awareness_frame", "structure_class", "delivery_class", "cta_kind", "weekday"]


def _feature_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        title = r.get("title") or ""
        out.append({**r,
                    "title_words": len(title.split()),
                    "question_title": int(title.strip().endswith("?") or title.lower().startswith(("how", "why", "what", "when", "should", "is ", "are ", "can "))),
                    "numeral_title": int(any(c.isdigit() for c in title)),
                    "lead_magnet": int((r.get("cta_kind") or "") in ("give", "mixed")),
                    "weekday": _weekday(r.get("published_at"))})
    return out


def _weekday(published_at: Optional[str]) -> Optional[str]:
    if not published_at:
        return None
    try:
        return datetime.fromisoformat(published_at.replace("Z", "+00:00")).strftime("%a")
    except ValueError:
        return None


def cohort_diff(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> list[dict[str, Any]]:
    b, a = _feature_rows(before), _feature_rows(after)
    out: list[dict[str, Any]] = []

    def vals(rows: list[dict[str, Any]], k: str) -> list[float]:
        return [float(r[k]) for r in rows if r.get(k) is not None]

    for k in NUMERIC:
        vb, va = vals(b, k), vals(a, k)
        if len(vb) < 2 or len(va) < 2:
            continue
        mb, ma = statistics.median(vb), statistics.median(va)
        eff = abs(ma - mb) / (abs(mb) if mb else (abs(ma) or 1.0))
        out.append({"feature": k, "kind": "median", "before": round(mb, 3), "after": round(ma, 3),
                    "n_before": len(vb), "n_after": len(va), "effect": round(eff, 3)})
    for k in RATES:
        vb, va = vals(b, k), vals(a, k)
        if len(vb) < 2 or len(va) < 2:
            continue
        rb, ra = statistics.mean(vb), statistics.mean(va)
        out.append({"feature": k, "kind": "rate", "before": round(rb, 3), "after": round(ra, 3),
                    "n_before": len(vb), "n_after": len(va), "effect": round(abs(ra - rb), 3)})
    for k in CATEGORICAL:
        cb = Counter(r[k] for r in b if r.get(k))
        ca = Counter(r[k] for r in a if r.get(k))
        if sum(cb.values()) < 2 or sum(ca.values()) < 2:
            continue
        nb, na = sum(cb.values()), sum(ca.values())
        keys = set(cb) | set(ca)
        tvd = 0.5 * sum(abs(cb[x] / nb - ca[x] / na) for x in keys)
        out.append({"feature": k, "kind": "distribution",
                    "before": {x: round(cb[x] / nb, 2) for x in keys if cb[x]},
                    "after": {x: round(ca[x] / na, 2) for x in keys if ca[x]},
                    "n_before": nb, "n_after": na, "effect": round(tvd, 3)})
    # cadence
    for label, rows in (("before", b), ("after", a)):
        pass
    cad_b, cad_a = _cadence(b), _cadence(a)
    if cad_b and cad_a:
        out.append({"feature": "uploads_per_week", "kind": "rate", "before": cad_b["per_week"],
                    "after": cad_a["per_week"], "n_before": len(b), "n_after": len(a),
                    "effect": round(abs(cad_a["per_week"] - cad_b["per_week"]) / (cad_b["per_week"] or 1), 3)})
        out.append({"feature": "gap_variance_days", "kind": "median", "before": cad_b["gap_var"],
                    "after": cad_a["gap_var"], "n_before": len(b), "n_after": len(a),
                    "effect": round(abs(cad_a["gap_var"] - cad_b["gap_var"]) / (cad_b["gap_var"] or 1), 3)})
    # topic shift: top content terms by frequency delta
    tb, ta = Counter(), Counter()
    for r in b:
        tb.update(content_tokens(r.get("title") or ""))
    for r in a:
        ta.update(content_tokens(r.get("title") or ""))
    nb, na = max(1, len(b)), max(1, len(a))
    shift = sorted(((ta[t] / na - tb[t] / nb), t) for t in set(tb) | set(ta))
    out.append({"feature": "topic_terms", "kind": "terms", "before": [t for _, t in shift[:8]],
                "after": [t for _, t in reversed(shift[-8:])], "n_before": len(b), "n_after": len(a),
                "effect": round(abs(shift[-1][0]) if shift else 0, 3)})
    out.sort(key=lambda d: -d["effect"])
    return out


def _cadence(rows: list[dict[str, Any]]) -> Optional[dict[str, float]]:
    dates = []
    for r in rows:
        try:
            dates.append(datetime.fromisoformat(str(r.get("published_at")).replace("Z", "+00:00")))
        except (ValueError, TypeError):
            continue
    if len(dates) < 3:
        return None
    dates.sort()
    span = (dates[-1] - dates[0]).total_seconds() / 86400
    gaps = [(d2 - d1).total_seconds() / 86400 for d1, d2 in zip(dates, dates[1:])]
    return {"per_week": round(len(dates) / max(span / 7, 1 / 7), 2),
            "gap_var": round(statistics.pvariance(gaps), 2) if len(gaps) > 1 else 0.0}


# -- §11.3 coherence ---------------------------------------------------------------------

def tfidf_vectors(docs: list[str]) -> list[dict[str, float]]:
    toks = [list(content_tokens(d)) for d in docs]
    df: Counter = Counter()
    for t in toks:
        df.update(set(t))
    n = max(1, len(docs))
    vecs = []
    for t in toks:
        tf = Counter(t)
        v = {w: (c / max(1, len(t))) * math.log((1 + n) / (1 + df[w])) + 1e-9 for w, c in tf.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        vecs.append({w: x / norm for w, x in v.items()})
    return vecs


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(w, 0.0) for w, x in a.items())


def coherence(docs: list[str]) -> tuple[Optional[float], list[float]]:
    """(mean pairwise cosine, per-doc similarity to the centroid-excluding-self)."""
    if len(docs) < 3:
        return None, []
    vecs = tfidf_vectors(docs)
    sims = []
    for i in range(len(vecs)):
        for j in range(i + 1, len(vecs)):
            sims.append(cosine(vecs[i], vecs[j]))
    mean = statistics.mean(sims) if sims else None
    per_doc = []
    for i, v in enumerate(vecs):
        cent: dict[str, float] = {}
        for j, u in enumerate(vecs):
            if j == i:
                continue
            for w, x in u.items():
                cent[w] = cent.get(w, 0.0) + x
        norm = math.sqrt(sum(x * x for x in cent.values())) or 1.0
        per_doc.append(cosine(v, {w: x / norm for w, x in cent.items()}))
    return mean, per_doc


# -- profile --------------------------------------------------------------------------------

def profile_channel(conn: sqlite3.Connection, cfg: dict[str, Any], channel_id: str, *,
                    n_perm: int = 2000, now: Optional[datetime] = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    rows = yti_rs_db.rows(conn, """
        SELECT v.*, s.class, s.projected_views, s.projected_multiple, s.like_rate, s.comment_rate,
               s.vs_percentile, p.point_count, p.structure_class, p.promise_restated_sec, p.has_promise,
               p.has_proof, p.has_plan, p.has_persona, p.awareness_frame, p.title_lowercase, p.filler_rate,
               p.delivery_class, p.mismatch_risk, p.cta_kind, t.text AS transcript_text
        FROM videos v LEFT JOIN scores s ON s.video_id = v.video_id
        LEFT JOIN packaging p ON p.video_id = v.video_id
        LEFT JOIN transcripts t ON t.video_id = v.video_id
        WHERE v.channel_id = ? AND v.published_at IS NOT NULL AND v.views IS NOT NULL
          AND v.is_short = 0
        ORDER BY v.published_at ASC""", (channel_id,))
    seeded = {r["video_id"] for r in yti_rs_db.rows(conn, """
        SELECT fm.video_id FROM format_matches fm JOIN formats f ON f.format_id = fm.format_id
        JOIN videos v ON v.video_id = fm.video_id WHERE v.channel_id = ?""", (channel_id,))}
    for r in rows:
        r["uses_seeded_format"] = int(r["video_id"] in seeded)
    result: dict[str, Any] = {"channel_id": channel_id, "n_videos": len(rows), "changepoints": [],
                              "lift_ratio": None, "cohort_diff": [], "coherence_score": None,
                              "off_topic_hits": [], "doubling_down": []}
    series = [math.log(max(1.0, float(r["projected_views"] or r["views"]))) for r in rows]
    cps = detect_changepoints(series, n_perm=n_perm) if len(series) >= 10 else []
    if cps:
        cp = cps[0]
        i = cp["index"]
        before, after = rows[:i], rows[i:]
        vb = [float(r["views"]) for r in before]
        va = [float(r["views"]) for r in after]
        result["changepoints"] = [{"date": rows[c["index"]]["published_at"], "index": c["index"],
                                   "p": round(c["p"], 4), "stat": round(c["stat"], 3)} for c in cps]
        result["lift_ratio"] = round(statistics.median(va) / statistics.median(vb), 3) if vb and va and statistics.median(vb) else None
        result["cohort_diff"] = cohort_diff(before, after)
    last20 = rows[-20:]
    docs = [(r["title"] or "") + " " + (r.get("description") or "") + " " +
            " ".join((r.get("transcript_text") or "").split()[:500]) for r in last20]
    coh, per_doc = coherence(docs)
    result["coherence_score"] = round(coh, 4) if coh is not None else None
    if per_doc:
        sorted_sims = sorted(per_doc)
        p20 = sorted_sims[max(0, int(0.2 * len(sorted_sims)) - 1)]
        for r, sim in zip(last20, per_doc):
            if r.get("class") == "strong_hit" and sim < p20:
                result["off_topic_hits"].append({"video_id": r["video_id"], "title": r["title"],
                                                 "similarity": round(sim, 4), "p20": round(p20, 4)})
    off = {o["video_id"] for o in result["off_topic_hits"]}
    fmt_rows = yti_rs_db.rows(conn, """
        SELECT fm.video_id, fm.format_id, fm.slots_json, f.label, f.kind, s.discriminators_json
        FROM format_matches fm JOIN formats f ON f.format_id = fm.format_id
        LEFT JOIN format_stats s ON s.format_id = fm.format_id
        JOIN videos v ON v.video_id = fm.video_id WHERE v.channel_id = ?""", (channel_id,))
    fmt_by_video: dict[str, list[dict[str, Any]]] = {}
    for fr in fmt_rows:
        fmt_by_video.setdefault(fr["video_id"], []).append(fr)
    for r in rows:
        if r.get("class") != "strong_hit" or r["video_id"] in off:
            continue
        formats = []
        for fr in fmt_by_video.get(r["video_id"], []):
            others = yti_rs_db.rows(conn, """
                SELECT v.niche, fm.slots_json, s.projected_multiple FROM format_matches fm
                JOIN videos v ON v.video_id = fm.video_id LEFT JOIN scores s ON s.video_id = v.video_id
                WHERE fm.format_id = ? AND v.channel_id != ? AND s.class IN ('hit','strong_hit')
                ORDER BY s.projected_multiple DESC LIMIT 5""", (fr["format_id"], channel_id))
            formats.append({"format_id": fr["format_id"], "label": fr["label"],
                            "slots": json.loads(fr["slots_json"] or "{}"),
                            "discriminators": json.loads(fr["discriminators_json"] or "[]")[:5],
                            "proven_elsewhere": [{"niche": o["niche"], "slots": json.loads(o["slots_json"] or "{}"),
                                                  "multiple": round(o["projected_multiple"] or 0, 2)} for o in others]})
        result["doubling_down"].append({
            "video_id": r["video_id"], "title": r["title"], "multiple": round(r.get("projected_multiple") or 0, 2),
            "formats": formats,
            "profile": {k: r.get(k) for k in ("structure_class", "point_count", "awareness_frame",
                                             "delivery_class", "has_proof", "cta_kind")},
            "framing": "preserve the reason it worked; find a new application of the same signal, "
                       "not a reissue of the same video"})
    cp0 = result["changepoints"][0] if result["changepoints"] else None
    conn.execute("""INSERT OR REPLACE INTO channel_profiles(channel_id, computed_at, n_videos, changepoint_date,
        changepoint_index, changepoint_p, changepoint_stat, lift_ratio, cohort_diff_json, coherence_score,
        off_topic_hits_json, doubling_down_json, llm_teardown)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?, (SELECT llm_teardown FROM channel_profiles WHERE channel_id = ?))""",
        (channel_id, yti_rs_db.now_iso(), len(rows), cp0["date"] if cp0 else None, cp0["index"] if cp0 else None,
         cp0["p"] if cp0 else None, cp0["stat"] if cp0 else None, result["lift_ratio"],
         json.dumps(result["cohort_diff"]), result["coherence_score"], json.dumps(result["off_topic_hits"]),
         json.dumps(result["doubling_down"]), channel_id))
    conn.commit()
    return result


def load_profile(conn: sqlite3.Connection, channel_id: str) -> Optional[dict[str, Any]]:
    r = yti_rs_db.one(conn, "SELECT * FROM channel_profiles WHERE channel_id = ?", (channel_id,))
    if not r:
        return None
    r["cohort_diff"] = json.loads(r.pop("cohort_diff_json") or "[]")
    r["off_topic_hits"] = json.loads(r.pop("off_topic_hits_json") or "[]")
    r["doubling_down"] = json.loads(r.pop("doubling_down_json") or "[]")
    return r
