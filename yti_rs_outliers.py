"""Outlier mathematics (spec §8): bucketed trailing-median baselines, age
normalisation via a maturity curve, robust log/MAD z-scores, per-niche
signal decay, classification, and the viewer-satisfaction proxies.

Pure functions first (unit-testable, no DB), then ``score_all`` which
recomputes every ``scores`` row from raw ``videos`` rows (P6).
"""
from __future__ import annotations

import math
import sqlite3
import statistics
from datetime import datetime, timezone
from typing import Any, Optional

try:
    from . import yti_rs_db, yti_rs_normalize, yti_rs_relevance
    from .yti_rs_config import DEFAULT_MATURITY_CURVE
except ImportError:  # pragma: no cover
    import yti_rs_db  # type: ignore
    import yti_rs_normalize  # type: ignore
    import yti_rs_relevance  # type: ignore
    from yti_rs_config import DEFAULT_MATURITY_CURVE  # type: ignore

CLASS_IMMATURE = "immature"


# -- §8.1 bucketing ---------------------------------------------------------------

def bucket_for(video: dict[str, Any]) -> Optional[str]:
    s = video.get("is_short")
    if s is None:
        s = yti_rs_normalize.is_short(video.get("duration_seconds"))
    if s is None:
        return None
    return "short" if s else "long"


# -- §8.2 baseline ------------------------------------------------------------------

def median_baseline(prior_views: list[int], window: int = 20, min_n: int = 6
                    ) -> tuple[Optional[float], int]:
    """Trailing-only median of the ``window`` videos published immediately
    before the target (caller passes them newest-first, self excluded)."""
    vals = [v for v in prior_views[:window] if v is not None and v >= 0]
    if len(vals) < min_n:
        return None, len(vals)
    return float(statistics.median(vals)), len(vals)


# -- §8.3 maturity ------------------------------------------------------------------

def maturity_fraction(age_days: float, curve: Optional[list] = None) -> float:
    """M(d): median fraction of day-28 views reached by day d (linear
    interpolation between curve points; day 0 → 5% floor)."""
    pts = sorted((float(d), float(f)) for d, f in (curve or DEFAULT_MATURITY_CURVE))
    if age_days >= pts[-1][0]:
        return 1.0
    if age_days <= 0:
        return 0.05
    prev_d, prev_f = 0.0, 0.05
    for d, f in pts:
        if age_days <= d:
            span = d - prev_d
            return prev_f + (f - prev_f) * ((age_days - prev_d) / span if span else 1.0)
        prev_d, prev_f = d, f
    return 1.0


def project_views(views: float, age_days: Optional[float], curve: Optional[list] = None,
                  maturity_days: int = 28) -> float:
    if age_days is None or age_days >= maturity_days:
        return float(views)
    return float(views) / max(0.05, maturity_fraction(age_days, curve))


def fit_maturity_curve(conn: sqlite3.Connection, min_videos: int = 200,
                       niche: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Replace the prior with empirical medians once ``video_snapshots`` holds
    ≥ ``min_videos`` videos with a day-≤3 and a day-≥28 observation."""
    sql = ("SELECT s.video_id, s.captured_at, s.views, v.published_at FROM video_snapshots s "
           "JOIN videos v ON v.video_id = s.video_id WHERE v.published_at IS NOT NULL "
           "AND v.published_approx = 0")
    params: list[Any] = []
    if niche:
        sql += " AND v.niche = ?"
        params.append(niche)
    per_video: dict[str, list[tuple[float, int]]] = {}
    for r in conn.execute(sql, params):
        try:
            pub = datetime.fromisoformat(r["published_at"])
            cap = datetime.fromisoformat(r["captured_at"] + ("T00:00:00+00:00" if len(r["captured_at"]) == 10 else ""))
        except ValueError:
            continue
        age = (cap - pub).total_seconds() / 86400
        per_video.setdefault(r["video_id"], []).append((age, int(r["views"])))
    checkpoints = [1, 3, 7, 14]
    samples: dict[int, list[float]] = {d: [] for d in checkpoints}
    n_ok = 0
    for obs in per_video.values():
        obs.sort()
        if obs[0][0] > 3 or obs[-1][0] < 28:
            continue
        day28 = _interp(obs, 28)
        if not day28 or day28 <= 0:
            continue
        n_ok += 1
        for d in checkpoints:
            if obs[0][0] <= d:
                v = _interp(obs, d)
                if v is not None:
                    samples[d].append(v / day28)
    if n_ok < min_videos:
        return None
    curve = [[d, round(statistics.median(samples[d]), 4)] for d in checkpoints if samples[d]] + [[28, 1.0]]
    return {"curve": curve, "n": n_ok, "source": "fitted", "niche": niche or "global"}


def _interp(obs: list[tuple[float, int]], day: float) -> Optional[float]:
    prev = None
    for age, views in obs:
        if age == day:
            return float(views)
        if age > day:
            if prev is None:
                return None
            (a0, v0) = prev
            return v0 + (views - v0) * ((day - a0) / (age - a0) if age != a0 else 0)
        prev = (age, views)
    return None


# -- §8.4 robust z -----------------------------------------------------------------

def log_mad_z(views: float, baseline_views: list[int]) -> Optional[float]:
    vals = [math.log(v) for v in baseline_views if v is not None and v > 0]
    if len(vals) < 2 or views <= 0:
        return None
    med = statistics.median(vals)
    mad = statistics.median(abs(x - med) for x in vals)
    sigma = 1.4826 * mad
    if sigma <= 0:
        return None
    return (math.log(views) - med) / sigma


# -- §8.5 decay + class --------------------------------------------------------------

def signal_weight(age_days: Optional[float], half_life_days: float) -> float:
    if age_days is None or age_days < 0:
        return 1.0
    return 0.5 ** (age_days / max(1.0, float(half_life_days)))


def classify(projected_multiple: Optional[float], baseline_n: int, min_n: int,
             *, hit: float = 3.0, strong: float = 5.0, under: float = 0.4,
             bucket_known: bool = True) -> str:
    if not bucket_known or projected_multiple is None or baseline_n < min_n:
        return CLASS_IMMATURE
    if projected_multiple >= strong:
        return "strong_hit"
    if projected_multiple >= hit:
        return "hit"
    if projected_multiple <= under:
        return "under"
    return "normal"


# -- §8.6 satisfaction ------------------------------------------------------------

def percentile_rank(values: list[float], x: float) -> float:
    """0..100 rank of x within values (mean of < and <= ranks)."""
    if not values:
        return 50.0
    lo = sum(1 for v in values if v < x)
    le = sum(1 for v in values if v <= x)
    return 100.0 * (lo + le) / (2 * len(values))


def organic_flag(comment_pct: Optional[float], views_pct: Optional[float]) -> str:
    if comment_pct is None or views_pct is None:
        return "unknown"
    if comment_pct <= 10 and views_pct >= 90:
        return "suspect_paid"
    if comment_pct >= 25:
        return "ok"
    return "unknown"


def trajectory_suspect(snapshots: list[tuple[str, int]]) -> Optional[bool]:
    """Spike-and-cliff test where history exists: True when >70% of the
    accrued views over the window landed in the first interval and the last
    three intervals together added <10%."""
    if len(snapshots) < 5:
        return None
    views = [v for _, v in snapshots]
    total = views[-1] - views[0]
    if total <= 0:
        return None
    first = views[1] - views[0]
    tail = views[-1] - views[-4]
    return first / total > 0.7 and tail / total < 0.10


def age_days_of(published_at: Optional[str], now: datetime) -> Optional[float]:
    if not published_at:
        return None
    try:
        pub = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=timezone.utc)
    return max(0.0, (now - pub).total_seconds() / 86400)


def resolve_unknown_form(conn: sqlite3.Connection) -> int:
    """Videos with no duration and no Shorts flag sit in the 'unknown' bucket
    and are never scored. RSS rows keep YouTube's own answer in their raw
    payload (``/shorts/<id>`` vs ``/watch?v=<id>``) — derive ``is_short`` from
    it. Returns how many rows were resolved."""
    import json
    n = 0
    for r in conn.execute("SELECT video_id, raw_json FROM videos WHERE is_short IS NULL "
                          "AND duration_seconds IS NULL AND raw_json LIKE '%\"link\"%'").fetchall():
        try:
            link = str(json.loads(r["raw_json"]).get("link") or "")
        except (TypeError, ValueError):
            continue
        form = yti_rs_normalize.is_short_rss(None, link)
        if form is None:
            continue
        conn.execute("UPDATE videos SET is_short = ? WHERE video_id = ?", (int(form), r["video_id"]))
        n += 1
    conn.commit()
    return n


def niche_verdicts(videos: list[dict[str, Any]], cfg: dict[str, Any], trusted_channels: set[str]
                   ) -> tuple[dict[str, int], dict[tuple[str, str], Optional[bool]]]:
    """Per-video in-niche verdict (1/0) and per-(niche, channel) on-topic verdict.

    A video is tagged OUT only on positive evidence: its channel has enough
    long-form titles to judge, too few of them mention the niche's vocabulary,
    the operator does not follow/track the channel, and the video's own title
    does not carry at least two of the niche's core words. Everything else
    stays in — a thin vocabulary must never hide data on its own.
    """
    c = cfg.get("crawl", {})
    min_share = float(c.get("channel_relevance_min", 0.20))
    min_titles = int(c.get("relevance_min_titles", 5))
    vocabs = {n["name"]: yti_rs_relevance.niche_vocab(n) for n in cfg.get("niches") or []}
    cores = {n["name"]: yti_rs_relevance.niche_core_vocab(n) for n in cfg.get("niches") or []}
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for v in videos:
        groups.setdefault((v["niche"], v["channel_id"]), []).append(v)
    in_niche: dict[str, int] = {}
    channel_verdict: dict[tuple[str, str], Optional[bool]] = {}
    for (niche, cid), rows in groups.items():
        vocab = vocabs.get(niche)
        if not vocab or cid in trusted_channels:
            verdict: Optional[bool] = True
        else:
            longs = [r["title"] for r in rows if bucket_for(r) == "long"]
            verdict = yti_rs_relevance.channel_on_topic(longs, vocab, min_share=min_share, min_titles=min_titles)
        channel_verdict[(niche, cid)] = verdict
        for r in rows:
            # on an off-topic channel one broad word is not evidence ("attack" on a
            # news network is war coverage): require two of the niche's core words
            keep = verdict is not False or yti_rs_relevance.match_count(r["title"], cores.get(niche) or vocab) >= 2
            in_niche[r["video_id"]] = 1 if keep else 0
    return in_niche, channel_verdict


# -- score_all ------------------------------------------------------------------------

def score_all(conn: sqlite3.Connection, cfg: dict[str, Any], *, now: Optional[datetime] = None,
              refit_curve: bool = False, followed: Optional[set[str]] = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    s = cfg.get("scoring", {})
    window = int(s.get("baseline_window", 20))
    min_n = int(s.get("min_baseline_videos", 6))
    maturity_days = int(s.get("maturity_days", 28))
    half_lives = {n["name"]: float(n.get("signal_half_life_days") or 365) for n in cfg.get("niches") or []}

    curve_meta = yti_rs_db.get_meta_json(conn, "maturity_curve") or {}
    if refit_curve or not curve_meta:
        fitted = fit_maturity_curve(conn)
        if fitted:
            curve_meta = fitted
        elif not curve_meta:
            curve_meta = {"curve": DEFAULT_MATURITY_CURVE, "n": 0, "source": "prior", "niche": "global"}
        yti_rs_db.set_meta(conn, "maturity_curve", curve_meta)
    curve = curve_meta.get("curve") or DEFAULT_MATURITY_CURVE
    curve_src = curve_meta.get("source", "prior")

    resolved = resolve_unknown_form(conn)
    videos = yti_rs_db.rows(conn, "SELECT * FROM videos WHERE views IS NOT NULL")
    by_channel: dict[str, list[dict[str, Any]]] = {}
    for v in videos:
        by_channel.setdefault(v["channel_id"], []).append(v)

    # topical relevance: channels the operator follows or tracks are in-niche by choice
    fol = {h.lower() for h in (followed or set())}
    trusted = {r["channel_id"] for r in conn.execute("SELECT channel_id, handle, is_tracked FROM channels")
               if r["is_tracked"] or (r["handle"] or "").lower() in fol}
    in_niche, _channel_verdict = niche_verdicts(videos, cfg, trusted)

    comment_stats = {r["video_id"]: r for r in yti_rs_db.rows(conn, """
        SELECT video_id, COUNT(*) AS n, AVG(CASE WHEN sentiment > 0.2 THEN 1.0 ELSE 0.0 END) AS pos_share,
               AVG(COALESCE(reply_count, 0)) AS reply_depth
        FROM comments GROUP BY video_id""")}

    computed: list[dict[str, Any]] = []
    for cid, rows in by_channel.items():
        # order newest-first by published_at (catalog_index breaks ties/unknowns)
        rows.sort(key=lambda r: (r.get("published_at") or "", -(r.get("catalog_index") or 0)), reverse=True)
        for bucket in ("long", "short"):
            brows = [r for r in rows if bucket_for(r) == bucket]
            for i, v in enumerate(brows):
                prior = [x["views"] for x in brows[i + 1:i + 1 + window]]
                baseline, n = median_baseline(prior, window, min_n)
                age = age_days_of(v.get("published_at"), now)
                gran = v.get("published_granularity_days")
                raw_only = bool(v.get("published_approx")) and (
                    gran is None or gran > yti_rs_normalize.MAX_PROJECTABLE_GRANULARITY_DAYS)
                multiple = (v["views"] / baseline) if baseline and baseline > 0 else None
                if raw_only or age is None:
                    projected = float(v["views"])
                else:
                    projected = project_views(v["views"], age, curve, maturity_days)
                pmult = (projected / baseline) if baseline and baseline > 0 else None
                computed.append({
                    "video": v, "bucket": bucket, "age": age, "baseline": baseline, "n": n,
                    "multiple": multiple, "projected": projected, "pmult": pmult,
                    "z": log_mad_z(v["views"], prior[:window]) if baseline else None,
                    "weight": signal_weight(age, half_lives.get(v["niche"], 365)),
                    "raw_only": raw_only,
                })
        unknown = [r for r in rows if bucket_for(r) is None]
        for v in unknown:
            computed.append({"video": v, "bucket": "unknown", "age": age_days_of(v.get("published_at"), now),
                             "baseline": None, "n": 0, "multiple": None, "projected": None,
                             "pmult": None, "z": None, "weight": 1.0, "raw_only": True})

    # satisfaction proxies: percentile ranks within the niche cohort
    for c in computed:
        v = c["video"]
        views = v["views"] or 0
        c["like_rate"] = (v["likes"] / views) if v.get("likes") is not None and views > 0 else None
        c["comment_rate"] = (v["comment_count"] / views) if v.get("comment_count") is not None and views > 0 else None
        cs = comment_stats.get(v["video_id"])
        c["pos_share"] = cs["pos_share"] if cs else None
        c["reply_depth"] = cs["reply_depth"] if cs else None
        c["pos_comment_rate"] = (c["comment_rate"] * c["pos_share"]) if c["comment_rate"] is not None and c["pos_share"] is not None else None
    # satisfaction percentiles rank within niche AND format: Shorts carry very
    # different like/comment rates and view counts, so a mixed cohort would
    # skew every long-form percentile (and the paid/breakout flags built on them)
    cohorts: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for c in computed:
        # off-topic videos are scored but kept out of the niche's percentile cohort
        if in_niche.get(c["video"]["video_id"], 1):
            cohorts.setdefault((c["video"]["niche"], c["bucket"]), []).append(c)
    bw_age = float(s.get("breakout_watch_max_age_days", 14))
    bw_pct = float(s.get("breakout_watch_comment_pct", 80))
    for (niche, _bucket), group in cohorts.items():
        def dist(key: str) -> list[float]:
            return [g[key] for g in group if g.get(key) is not None]
        d_like, d_comment, d_pos, d_reply = dist("like_rate"), dist("comment_rate"), dist("pos_comment_rate"), dist("reply_depth")
        d_views = [float(g["video"]["views"]) for g in group if g["video"].get("views")]
        for c in group:
            p_like = percentile_rank(d_like, c["like_rate"]) if c["like_rate"] is not None and d_like else None
            p_comment = percentile_rank(d_comment, c["comment_rate"]) if c["comment_rate"] is not None and d_comment else None
            p_pos = percentile_rank(d_pos, c["pos_comment_rate"]) if c["pos_comment_rate"] is not None and d_pos else None
            p_reply = percentile_rank(d_reply, c["reply_depth"]) if c["reply_depth"] is not None and d_reply else None
            p_views = percentile_rank(d_views, float(c["video"]["views"])) if d_views else None
            parts = [(0.25, p_like), (0.55, p_pos if p_pos is not None else p_comment), (0.20, p_reply)]
            wsum = sum(w for w, p in parts if p is not None)
            c["vs_pct"] = (sum(w * p for w, p in parts if p is not None) / wsum) if wsum else None
            flag = organic_flag(p_comment, p_views)
            if flag != "suspect_paid":
                snaps = yti_rs_db.rows(conn, "SELECT captured_at, views FROM video_snapshots WHERE video_id = ? ORDER BY captured_at",
                                       (c["video"]["video_id"],))
                if trajectory_suspect([(r["captured_at"], r["views"]) for r in snaps]):
                    flag = "suspect_paid"
            c["organic"] = flag
            p_signal = p_pos if p_pos is not None else p_comment
            c["breakout"] = int(c["age"] is not None and c["age"] <= bw_age and (c["pmult"] or 0) < 1.0
                                and p_signal is not None and p_signal >= bw_pct and flag != "suspect_paid")
            c["fade"] = int(c["age"] is not None and c["age"] <= bw_age and p_views is not None and p_views >= 80
                            and p_comment is not None and p_comment <= 20)

    hit, strong, under = float(s.get("hit_multiple", 3)), float(s.get("strong_multiple", 5)), float(s.get("underperformer_multiple", 0.4))
    ts = yti_rs_db.now_iso()
    conn.execute("DELETE FROM scores")
    tally: dict[str, int] = {}
    for c in computed:
        v = c["video"]
        cls = classify(c["pmult"], c["n"], min_n, hit=hit, strong=strong, under=under,
                       bucket_known=c["bucket"] != "unknown")
        tally[cls] = tally.get(cls, 0) + 1
        conn.execute("""INSERT INTO scores(video_id, computed_at, format_bucket, age_days, baseline_views,
            baseline_n, multiple, projected_views, projected_multiple, log_mad_z, signal_weight, like_rate,
            comment_rate, positive_comment_rate, vs_percentile, organic_flag, breakout_watch, fade_watch,
            maturity_source, raw_only, in_niche, class) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (v["video_id"], ts, c["bucket"], c["age"], c["baseline"], c["n"], c["multiple"], c["projected"],
             c["pmult"], c["z"], c["weight"], c.get("like_rate"), c.get("comment_rate"),
             c.get("pos_comment_rate"), c.get("vs_pct"), c.get("organic", "unknown"),
             c.get("breakout", 0), c.get("fade", 0), curve_src, int(c["raw_only"]),
             in_niche.get(v["video_id"], 1), cls))
    conn.commit()
    yti_rs_db.set_meta(conn, "last_score_run", ts)
    by_bucket: dict[str, int] = {}
    for c in computed:
        by_bucket[c["bucket"]] = by_bucket.get(c["bucket"], 0) + 1
    off_channels = sorted({cid for (_n, cid), verdict in _channel_verdict.items() if verdict is False})
    return {"scored": len(computed), "classes": tally, "buckets": by_bucket,
            "form_resolved": resolved, "out_of_niche": sum(1 for x in in_niche.values() if not x),
            "off_topic_channels": len(off_channels), "maturity_curve": curve_meta}
