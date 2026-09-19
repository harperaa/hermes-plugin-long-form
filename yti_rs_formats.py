"""The format engine (spec §10): seeded library, shingle mining, validation
with negative evidence (Wilson lower bound ranking — P2), discriminator
analysis, and the D4 cross-niche gap report.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

try:
    from . import yti_rs_db
    from .yti_rs_config import target_niche
    from .yti_rs_packaging import STOP
except ImportError:  # pragma: no cover
    import yti_rs_db  # type: ignore
    from yti_rs_config import target_niche  # type: ignore
    from yti_rs_packaging import STOP  # type: ignore

SEEDED_PATH = Path(__file__).resolve().parent / "research" / "seeded_formats.json"

GAP_CAVEAT = ("Pre-publish research is a hypothesis; the market casts the final vote. Nothing here "
              "is a guarantee, and a format's discriminators are the conditions under which the "
              "evidence held, not a recipe.")


# -- §10.3 Wilson ---------------------------------------------------------------------

def wilson_lb(hits: int, n: int, z: float = 1.96) -> float:
    if n <= 0:
        return 0.0
    p = hits / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return max(0.0, (centre - margin) / denom)


# -- §10.1 seeded ---------------------------------------------------------------------

def load_seeded(conn: sqlite3.Connection, path: Optional[Path] = None) -> int:
    data = json.loads((path or SEEDED_PATH).read_text())
    n = 0
    for f in data:
        try:
            pat = re.compile(f["pattern"])
        except re.error as exc:
            raise ValueError(f"seeded format {f['id']}: bad regex: {exc}")
        conn.execute("""INSERT OR REPLACE INTO formats(format_id, kind, label, pattern, skeleton, slot_names,
            psychology, caution, known_discriminator, created_at)
            VALUES (?,?,?,?,?,?,?,?,?, COALESCE((SELECT created_at FROM formats WHERE format_id = ?), ?))""",
            (f["id"], "seeded", f["label"], f["pattern"], None, json.dumps(list(pat.groupindex.keys())),
             f.get("psychology"), f.get("caution"), f.get("known_discriminator"), f["id"], yti_rs_db.now_iso()))
        n += 1
    conn.commit()
    return n


def match_seeded(conn: sqlite3.Connection) -> int:
    formats = yti_rs_db.rows(conn, "SELECT format_id, pattern FROM formats WHERE kind = 'seeded' AND pattern IS NOT NULL")
    videos = yti_rs_db.rows(conn, "SELECT video_id, title FROM videos")
    conn.execute("DELETE FROM format_matches WHERE format_id IN (SELECT format_id FROM formats WHERE kind = 'seeded')")
    n = 0
    for f in formats:
        pat = re.compile(f["pattern"])
        for v in videos:
            m = pat.search(v["title"] or "")
            if m:
                conn.execute("INSERT OR REPLACE INTO format_matches(format_id, video_id, slots_json) VALUES (?,?,?)",
                             (f["format_id"], v["video_id"], json.dumps(m.groupdict())))
                n += 1
    conn.commit()
    return n


# -- §10.2 mining ------------------------------------------------------------------------

def normalize_title(title: str) -> list[str]:
    t = (title or "").lower()
    t = re.sub(r"\d[\d,\.]*", "#", t)          # digits → # before punctuation is stripped
    t = re.sub(r"[^a-z#$%\s]+", " ", t)
    return t.split()


def shingles(tokens: list[str], min_n: int = 3, max_n: int = 7) -> set[str]:
    out = set()
    for n in range(min_n, max_n + 1):
        for i in range(0, len(tokens) - n + 1):
            out.add(" ".join(tokens[i:i + n]))
    return out


def mine(videos: list[dict[str, Any]], *, min_support: int = 4, min_channels: int = 3,
         min_niches: int = 2, min_words: int = 3, max_words: int = 7,
         topical_terms: Optional[set[str]] = None) -> list[dict[str, Any]]:
    """videos: [{video_id, channel_id, niche, title}] → retained shingles with
    their support sets. Pure; no DB."""
    support: dict[str, set[str]] = defaultdict(set)
    channels: dict[str, set[str]] = defaultdict(set)
    niches: dict[str, set[str]] = defaultdict(set)
    for v in videos:
        toks = normalize_title(v["title"])
        for sh in shingles(toks, min_words, max_words):
            support[sh].add(v["video_id"])
            channels[sh].add(v["channel_id"])
            niches[sh].add(v["niche"])
    kept = {sh for sh in support
            if len(support[sh]) >= min_support and len(channels[sh]) >= min_channels
            and len(niches[sh]) >= min_niches}
    # suppress sub-shingles of a longer retained shingle carrying ≥80% of its support
    by_len = sorted(kept, key=lambda s: -len(s.split()))
    drop: set[str] = set()
    for longer in by_len:
        for shorter in kept:
            if shorter == longer or shorter in drop or len(shorter) >= len(longer):
                continue
            if f" {shorter} " in f" {longer} " and len(support[longer]) >= 0.8 * len(support[shorter]):
                drop.add(shorter)
    kept -= drop
    topical = {t.lower() for t in (topical_terms or set())}
    out = []
    for sh in kept:
        toks = sh.split()
        content = [t for t in toks if t not in STOP and t != "#"]
        if content and topical and all(t in topical for t in content):
            continue        # purely topical vocabulary, not a transferable frame
        if not content:
            continue        # stopwords / numbers only
        out.append({"skeleton": sh, "support": len(support[sh]), "video_ids": sorted(support[sh]),
                    "distinct_channels": len(channels[sh]), "distinct_niches": len(niches[sh]),
                    "niches": sorted(niches[sh])})
    out.sort(key=lambda r: (-r["support"], r["skeleton"]))
    return out


def topical_term_set(cfg: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for n in cfg.get("niches") or []:
        for key in ("outcome_terms", "mechanism_terms"):
            for term in n.get(key) or []:
                out.update(normalize_title(term))
    return out


def mine_into_db(conn: sqlite3.Connection, cfg: dict[str, Any]) -> dict[str, Any]:
    f = cfg.get("formats", {})
    videos = yti_rs_db.rows(conn, "SELECT video_id, channel_id, niche, title FROM videos")
    mined = mine(videos, min_support=int(f.get("min_support", 4)),
                 min_channels=int(f.get("min_distinct_channels", 3)),
                 min_niches=int(f.get("min_distinct_niches", 2)),
                 min_words=int(f.get("min_shingle_words", 3)), max_words=int(f.get("max_shingle_words", 7)),
                 topical_terms=topical_term_set(cfg))
    conn.execute("DELETE FROM format_matches WHERE format_id IN (SELECT format_id FROM formats WHERE kind = 'mined')")
    conn.execute("DELETE FROM format_stats WHERE format_id IN (SELECT format_id FROM formats WHERE kind = 'mined')")
    conn.execute("DELETE FROM formats WHERE kind = 'mined'")
    titles = {v["video_id"]: v["title"] for v in videos}
    for m in mined:
        fid = "mined:" + re.sub(r"[^a-z0-9#$%]+", "-", m["skeleton"]).strip("-")[:80]
        conn.execute("""INSERT OR REPLACE INTO formats(format_id, kind, label, pattern, skeleton, slot_names,
                        psychology, caution, known_discriminator, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                     (fid, "mined", "… " + m["skeleton"] + " …", None, m["skeleton"],
                      json.dumps(["before", "after"]), None, None, None, yti_rs_db.now_iso()))
        for vid in m["video_ids"]:
            norm = " ".join(normalize_title(titles.get(vid, "")))
            before, _, after = norm.partition(m["skeleton"])
            conn.execute("INSERT OR REPLACE INTO format_matches(format_id, video_id, slots_json) VALUES (?,?,?)",
                         (fid, vid, json.dumps({"before": before.strip(), "after": after.strip()})))
    conn.commit()
    return {"mined": len(mined)}


# -- §10.3 validation -----------------------------------------------------------------------

DISCRIMINATORS = ("structure_class", "point_bucket", "awareness_frame", "delivery_class", "mismatch_risk",
                  "has_proof", "duration_bucket", "subs_band", "cta_kind")


def _point_bucket(pc: Optional[int]) -> Optional[str]:
    if pc is None:
        return None
    return "<8" if pc < 8 else "8-14" if pc < 15 else ">=15"


def _duration_bucket(sec: Optional[int]) -> Optional[str]:
    if sec is None:
        return None
    m = sec / 60
    return "<8m" if m < 8 else "8-20m" if m < 20 else "20-45m" if m < 45 else "45m+"


def _subs_band(subs: Optional[int]) -> Optional[str]:
    if subs is None:
        return None
    return "<10k" if subs < 10_000 else "10k-100k" if subs < 100_000 else "100k-1M" if subs < 1_000_000 else "1M+"


def discriminators(rows: list[dict[str, Any]], min_arm: int = 5) -> list[dict[str, Any]]:
    """For each feature value: hit rate with vs without; only splits where both
    arms have n ≥ min_arm. No p-values (multiple-comparison mining)."""
    out = []
    for feat in DISCRIMINATORS:
        values = sorted({str(r[feat]) for r in rows if r.get(feat) is not None})
        for val in values:
            with_ = [r for r in rows if r.get(feat) is not None and str(r[feat]) == val]
            without = [r for r in rows if r.get(feat) is not None and str(r[feat]) != val]
            if len(with_) < min_arm or len(without) < min_arm:
                continue
            hw = sum(r["is_hit"] for r in with_) / len(with_)
            ho = sum(r["is_hit"] for r in without) / len(without)
            out.append({"feature": feat, "value": val, "hit_rate_with": round(hw, 3),
                        "hit_rate_without": round(ho, 3), "n_with": len(with_), "n_without": len(without),
                        "diff": round(hw - ho, 3)})
    out.sort(key=lambda d: -abs(d["diff"]))
    return out


def validate(conn: sqlite3.Connection, cfg: dict[str, Any]) -> dict[str, Any]:
    tgt = target_niche(cfg)
    tname = tgt["name"] if tgt else None
    exclude_paid = bool(cfg.get("scoring", {}).get("exclude_suspect_paid_from_formats", True))
    rows = yti_rs_db.rows(conn, """
        SELECT fm.format_id, v.video_id, v.title, v.channel_id, v.niche, v.duration_seconds, v.views,
               v.views_approx, c.subscriber_count, s.class, s.projected_multiple, s.signal_weight,
               s.organic_flag, p.structure_class, p.point_count, p.awareness_frame, p.delivery_class,
               p.mismatch_risk, p.has_proof, p.cta_kind
        FROM format_matches fm
        JOIN videos v ON v.video_id = fm.video_id
        LEFT JOIN channels c ON c.channel_id = v.channel_id
        LEFT JOIN scores s ON s.video_id = v.video_id
        LEFT JOIN packaging p ON p.video_id = v.video_id""")
    by_format: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_format[r["format_id"]].append(r)
    conn.execute("DELETE FROM format_stats")
    ts = yti_rs_db.now_iso()
    n_formats = 0
    for fid, matches in by_format.items():
        target_uses = sum(1 for m in matches if tname and m["niche"] == tname)
        scored = [m for m in matches if m["class"] and m["class"] != "immature"
                  and not (exclude_paid and m["organic_flag"] == "suspect_paid")]
        n_total = len(scored)
        hits = [m for m in scored if m["class"] in ("hit", "strong_hit")]
        n_hits = len(hits)
        n_under = sum(1 for m in scored if m["class"] == "under")
        weights = [m["signal_weight"] or 0.0 for m in scored]
        wsum = sum(weights)
        weighted_hit = (sum(w for m, w in zip(scored, weights) if m["class"] in ("hit", "strong_hit")) / wsum) if wsum else None
        mults = [m["projected_multiple"] for m in scored if m["projected_multiple"] is not None]
        feats = [{
            "is_hit": 1 if m["class"] in ("hit", "strong_hit") else 0,
            "structure_class": m["structure_class"], "point_bucket": _point_bucket(m["point_count"]),
            "awareness_frame": m["awareness_frame"], "delivery_class": m["delivery_class"],
            "mismatch_risk": m["mismatch_risk"], "has_proof": m["has_proof"],
            "duration_bucket": _duration_bucket(m["duration_seconds"]),
            "subs_band": _subs_band(m["subscriber_count"]), "cta_kind": m["cta_kind"],
        } for m in scored]
        examples = sorted(hits, key=lambda m: -(m["projected_multiple"] or 0))[:3]
        conn.execute("""INSERT OR REPLACE INTO format_stats(format_id, computed_at, n_total, n_hits, n_under,
            hit_rate, wilson_lb, median_multiple, weighted_hit_rate, mean_signal_weight, distinct_channels,
            distinct_niches, niches_json, target_niche_uses, examples_json, discriminators_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (fid, ts, n_total, n_hits, n_under, (n_hits / n_total) if n_total else None,
             wilson_lb(n_hits, n_total) if n_total else 0.0,
             statistics.median(mults) if mults else None, weighted_hit,
             (wsum / len(weights)) if weights else None,
             len({m["channel_id"] for m in matches}), len({m["niche"] for m in matches}),
             json.dumps(sorted({m["niche"] for m in matches})), target_uses,
             json.dumps([{"video_id": m["video_id"], "title": m["title"], "niche": m["niche"],
                          "multiple": round(m["projected_multiple"] or 0, 2), "approx": bool(m["views_approx"])}
                         for m in examples]),
             json.dumps(discriminators(feats))))
        n_formats += 1
    conn.commit()
    yti_rs_db.set_meta(conn, "last_formats_run", ts)
    return {"formats": n_formats}


def library(conn: sqlite3.Connection, niche: Optional[str] = None) -> list[dict[str, Any]]:
    rows = yti_rs_db.rows(conn, """
        SELECT f.format_id, f.kind, f.label, f.psychology, f.caution, f.known_discriminator, f.skeleton,
               s.* FROM formats f LEFT JOIN format_stats s ON s.format_id = f.format_id
        ORDER BY COALESCE(s.wilson_lb, 0) DESC, COALESCE(s.n_total, 0) DESC""")
    out = []
    for r in rows:
        r["niches"] = json.loads(r.get("niches_json") or "[]")
        r["examples"] = json.loads(r.get("examples_json") or "[]")
        r["discriminators"] = json.loads(r.get("discriminators_json") or "[]")
        for k in ("niches_json", "examples_json", "discriminators_json"):
            r.pop(k, None)
        if niche and niche not in r["niches"]:
            continue
        out.append(r)
    return out


# -- §10.4 D4 --------------------------------------------------------------------------------

def gap_report(conn: sqlite3.Connection, cfg: dict[str, Any], min_wilson: Optional[float] = None) -> dict[str, Any]:
    f = cfg.get("formats", {})
    min_w = float(min_wilson if min_wilson is not None else f.get("gap_min_wilson_lb", 0.25))
    min_n = int(f.get("min_actionable_n", 10))
    tgt = target_niche(cfg)
    rows = [r for r in library(conn) if r.get("n_total")]
    def rank(r: dict[str, Any]) -> float:
        return (r.get("wilson_lb") or 0) * (r.get("mean_signal_weight") or 0)
    gaps = [dict(r, rank=rank(r), actionable=(r["n_total"] or 0) >= min_n)
            for r in rows if (r.get("distinct_niches") or 0) >= 2 and (r.get("wilson_lb") or 0) >= min_w
            and (r.get("target_niche_uses") or 0) == 0]
    near = [dict(r, rank=rank(r), actionable=(r["n_total"] or 0) >= min_n)
            for r in rows if (r.get("distinct_niches") or 0) >= 2 and (r.get("wilson_lb") or 0) >= min_w
            and 1 <= (r.get("target_niche_uses") or 0) <= 2]
    gaps.sort(key=lambda r: -r["rank"])
    near.sort(key=lambda r: -r["rank"])
    return {"target_niche": tgt["name"] if tgt else None, "min_wilson_lb": min_w, "min_actionable_n": min_n,
            "gaps": gaps, "near_gaps": near, "caveat": GAP_CAVEAT}
