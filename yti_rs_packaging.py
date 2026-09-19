"""Packaging + transcript analysis (spec §9): point count / structure class,
Promise-Proof-Plan-Persona in the first 90s, awareness frame, delivery
register, and give-vs-take CTA. All heuristics on title + timestamped
transcript; the optional LLM pass only adds readable notes.
"""
from __future__ import annotations

import re
import sqlite3
import statistics
from typing import Any, Optional

try:
    from . import yti_rs_db
except ImportError:  # pragma: no cover
    import yti_rs_db  # type: ignore

STOP = set("""a an the and or but if then so of to in on at for with by from as is are was were be been
being this that these those it its i you he she they we me him her them my your our their his
what which who whom whose when where why how all any both each few more most other some such no nor
not only own same than too very can will just do does did doing have has had having would should
could about into over after before between out up down off again further once here there s t don
ve ll re m d y get got gets go going im ive youre thats dont cant wont""".split())

ORDINALS = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth",
            "tenth", "eleventh", "twelfth"]
_ENUM_RES = [
    re.compile(r"\bnumber\s+(?:\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|twenty)\b", re.I),
    re.compile(r"\b(?:" + "|".join(ORDINALS) + r")(?:\s*,|\s+(?:one|thing|tip|step|lesson|point|rule|mistake|reason|way|is))", re.I),
    re.compile(r"\b(?:step|tip|lesson|point|rule|mistake|reason|principle|habit|strategy|tactic)\s+(?:number\s+)?\d{1,2}\b", re.I),
    re.compile(r"\b(?:the\s+)?next\s+(?:one|tip|step|lesson|point|rule|mistake|reason|thing)\b", re.I),
    re.compile(r"\banother\s+(?:one|tip|step|lesson|point|rule|mistake|reason|thing)\b", re.I),
    re.compile(r"\b(?:last|final|finally,?)\s+(?:one|tip|step|lesson|point|rule|mistake|reason|thing)?\b", re.I),
]
_WALKTHROUGH = re.compile(r"\b(?:as you can see|on my screen|on the screen|let me show you|click(?:ing)? (?:on|here)|"
                          r"go to|type in|open up|right here|over here|in the terminal|in the console|"
                          r"this window|this tab|scroll down|paste|copy this|run this|hit enter)\b", re.I)
_PROOF = [
    re.compile(r"\$\s?\d[\d,\.]*\s*(?:k|m|b|million|billion|thousand)?", re.I),
    re.compile(r"\b\d[\d,\.]*\s*(?:k|m|million|thousand)?\s*(?:subscribers|subs|clients|customers|students|"
               r"followers|users|views|downloads|sales|employees|people|companies|videos|posts|emails|years)\b", re.I),
    re.compile(r"\b(?:i(?:'ve| have)? (?:been|worked|spent|built|helped|grown|scaled|sold|coached|taught|run|ran))\b", re.I),
    re.compile(r"\b(?:phd|md|ceo|founder|ex-|former|certified|licensed|professor|engineer at|worked at)\b", re.I),
    re.compile(r"\b(?:revenue|profit|arr|mrr|figures?|seven[- ]figure|six[- ]figure|eight[- ]figure)\b", re.I),
    re.compile(r"\b(?:google|meta|facebook|amazon|apple|netflix|microsoft|openai|anthropic|nvidia|tesla|"
               r"mckinsey|goldman|forbes|ted|harvard|stanford|mit|nasa|fbi|cia|nsa)\b", re.I),
]
_PLAN = re.compile(r"\b(?:i'?m going to (?:show|walk|teach|give|break|share|explain)|i'?ll (?:show|walk|teach|give|break|share|explain)|"
                   r"by the end of (?:this|the) video|in this video|we'?re going to (?:cover|go|look|break|talk|walk)|"
                   r"first,? (?:we'?ll|i'?ll|we're going to)|here'?s what we'?ll|let'?s (?:go through|break down|dive|walk)|"
                   r"you'?re going to learn|(?:three|four|five|six|seven|\d) (?:things|steps|ways|tips|lessons|rules|mistakes|reasons))\b", re.I)
_PERSONA = re.compile(r"\b(?:if you'?re (?:a|an|someone|new|just|struggling|trying|building|running|working)|if you are (?:a|an|someone)|"
                      r"for (?:anyone|people|those|founders|creators|developers|beginners|engineers|marketers|coaches|"
                      r"agencies|freelancers|students|parents|entrepreneurs|professionals|business owners) who|"
                      r"this (?:video )?is for|whether you'?re (?:a|an)|as a (?:founder|creator|developer|beginner|engineer|marketer|coach))\b", re.I)
_FILLER = re.compile(r"\b(?:um+|uh+|er+|ah+|like|you know|sort of|kind of|i mean|basically|literally|actually|okay so|right\?)\b", re.I)
_CTA_GIVE = re.compile(r"\b(?:link in (?:the )?(?:description|bio)|free (?:template|guide|checklist|sheet|tracker|library|"
                       r"course|download|resource|workbook|swipe|pdf|ebook|notion|kit|cheat ?sheet|system)|"
                       r"(?:template|checklist|spreadsheet|tracker|library|prompt|prompts|notion|worksheet|"
                       r"cheat ?sheet|swipe file|toolkit|kit)s? (?:for free|is free|in the description|below|linked)|"
                       r"for free|download (?:it|this|the)|grab (?:it|the|your)|i'?ll (?:give|send) you)\b", re.I)
_CTA_TAKE = re.compile(r"\b(?:book a (?:call|consult|strategy)|schedule a call|apply (?:here|now|below|to work)|"
                       r"work with me|hire (?:me|us)|buy (?:my|the|it|now)|purchase|sign up for (?:my|the|our) (?:newsletter|"
                       r"program|course|community|mastermind|coaching)|join (?:my|the|our) (?:program|course|community|"
                       r"mastermind|coaching|membership|skool|discord|patreon)|enroll|get (?:my|the) course|"
                       r"limited spots|discount code|use code|sponsor(?:ed|s)? (?:by|of)|promo code)\b", re.I)
_CTA_GENERIC = re.compile(r"\b(?:subscribe|like (?:this|the) video|hit the (?:like|bell)|leave a comment|comment below)\b", re.I)

_SENT_SPLIT = re.compile(r"(?<=[\.!?])\s+|\n+")


def content_tokens(text: str) -> set[str]:
    toks = re.findall(r"[a-z0-9$%']+", (text or "").lower())
    out = set()
    for t in toks:
        t = t.strip("'")
        if len(t) < 3 or t in STOP:
            continue
        out.add(_stem(t))
    return out


def _stem(t: str) -> str:
    for suf in ("ing", "ers", "ies", "ed", "es", "er", "ly", "s"):
        if len(t) > 4 and t.endswith(suf):
            return t[:-len(suf)] if suf != "ies" else t[:-3] + "y"
    return t


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _seg_text(seg: dict[str, Any]) -> str:
    return str(seg.get("text") or "")


def _seg_start(seg: dict[str, Any]) -> float:
    try:
        return float(seg.get("start") or 0)
    except (TypeError, ValueError):
        return 0.0


# -- §9.1 structure -------------------------------------------------------------------

def count_points(segments: list[dict[str, Any]], dedupe_window_sec: float = 20.0) -> int:
    hits: list[float] = []
    for seg in segments:
        text = _seg_text(seg)
        if any(p.search(text) for p in _ENUM_RES):
            hits.append(_seg_start(seg))
    hits.sort()
    count, last = 0, -1e9
    for t in hits:
        if t - last >= dedupe_window_sec:
            count += 1
            last = t
    return count


def structure_class(point_count: int, full_text: str) -> str:
    words = max(1, len(full_text.split()))
    wt = len(_WALKTHROUGH.findall(full_text)) / words * 1000
    if wt >= 3.0 and point_count < 15:
        return "walkthrough"
    if point_count >= 15:
        return "listicle_dense"
    if 2 <= point_count <= 8:
        return "listicle_short"
    if point_count <= 1:
        return "narrative"
    return "listicle_short" if point_count < 15 else "listicle_dense"


# -- §9.2 promise / proof / plan / persona -----------------------------------------------

def opening(segments: list[dict[str, Any]], seconds: float = 90.0) -> list[dict[str, Any]]:
    return [s for s in segments if _seg_start(s) <= seconds]


def promise(title: str, segments: list[dict[str, Any]]) -> tuple[bool, Optional[float]]:
    tt = content_tokens(title)
    if not tt:
        return False, None
    window: list[dict[str, Any]] = []
    for seg in opening(segments):
        window.append(seg)
        window = [w for w in window if _seg_start(seg) - _seg_start(w) <= 12]
        toks = content_tokens(" ".join(_seg_text(w) for w in window))
        overlap = tt & toks
        if len(overlap) >= 2 or (len(tt) <= 2 and overlap) or jaccard(tt, toks) >= 0.25:
            return True, _seg_start(window[0])
    return False, None


def proof(segments: list[dict[str, Any]]) -> tuple[bool, Optional[float]]:
    for seg in opening(segments):
        text = _seg_text(seg)
        if any(p.search(text) for p in _PROOF):
            return True, _seg_start(seg)
    return False, None


def plan(segments: list[dict[str, Any]]) -> bool:
    return any(_PLAN.search(_seg_text(s)) for s in opening(segments))


def persona(segments: list[dict[str, Any]]) -> bool:
    return any(_PERSONA.search(_seg_text(s)) for s in opening(segments))


# -- §9.3 awareness -------------------------------------------------------------------

def awareness_frame(title: str, outcome_terms: list[str], mechanism_terms: list[str]) -> str:
    t = " " + re.sub(r"[^a-z0-9$% ]+", " ", (title or "").lower()) + " "
    has_o = any(f" {o.lower().strip()} " in t or o.lower().strip() in t for o in outcome_terms if o.strip())
    has_m = any(f" {m.lower().strip()} " in t or m.lower().strip() in t for m in mechanism_terms if m.strip())
    if has_o and has_m:
        return "bridged"
    if has_o:
        return "outcome_led"
    if has_m:
        return "mechanism_led"
    return "unclear"


# -- §9.4 delivery ----------------------------------------------------------------------

def title_lowercase(title: str) -> bool:
    letters = [c for c in (title or "") if c.isalpha()]
    if not letters:
        return False
    return all(c.islower() for c in letters)


def filler_rate(text: str) -> float:
    words = len(text.split())
    if not words:
        return 0.0
    return 100.0 * len(_FILLER.findall(text)) / words


def sentence_len_cv(text: str) -> Optional[float]:
    sents = [s for s in _SENT_SPLIT.split(text or "") if s.strip()]
    lens = [len(s.split()) for s in sents if s.split()]
    if len(lens) < 5:
        return None
    mean = statistics.mean(lens)
    return (statistics.pstdev(lens) / mean) if mean else None


def delivery_class(filler: float, cv: Optional[float]) -> str:
    if filler < 0.8 and (cv is not None and cv < 0.45):
        return "scripted"
    if filler >= 2.0 or (cv is not None and cv >= 0.7):
        return "raw"
    return "mixed"


# -- §9.5 CTA ---------------------------------------------------------------------------

def cta(segments: list[dict[str, Any]], length_seconds: Optional[float]) -> tuple[str, Optional[float]]:
    if not segments:
        return "none", None
    total = length_seconds or (_seg_start(segments[-1]) + float(segments[-1].get("duration") or 0)) or 1.0
    start_at = total * 0.75
    tail = [s for s in segments if _seg_start(s) >= start_at]
    give = take = None
    for s in tail:
        text = _seg_text(s)
        if give is None and _CTA_GIVE.search(text):
            give = _seg_start(s)
        if take is None and _CTA_TAKE.search(text):
            take = _seg_start(s)
    if give is not None and take is not None:
        return "mixed", min(give, take) / total * 100
    if give is not None:
        return "give", give / total * 100
    if take is not None:
        return "take", take / total * 100
    return "none", None


# -- compose -----------------------------------------------------------------------------

def compute_packaging(title: str, segments: list[dict[str, Any]], full_text: str,
                      length_seconds: Optional[float], outcome_terms: list[str],
                      mechanism_terms: list[str]) -> dict[str, Any]:
    pc = count_points(segments)
    has_promise, promise_sec = promise(title, segments)
    has_proof, proof_sec = proof(segments)
    fr = filler_rate(full_text)
    cv = sentence_len_cv(full_text)
    dc = delivery_class(fr, cv)
    lower = title_lowercase(title)
    kind, pos = cta(segments, length_seconds)
    return {
        "point_count": pc,
        "structure_class": structure_class(pc, full_text),
        "promise_restated_sec": promise_sec,
        "has_promise": int(has_promise), "has_proof": int(has_proof),
        "has_plan": int(plan(segments)), "has_persona": int(persona(segments)),
        "proof_sec": proof_sec,
        "awareness_frame": awareness_frame(title, outcome_terms, mechanism_terms),
        "title_lowercase": int(lower),
        "filler_rate": round(fr, 3),
        "sentence_len_cv": round(cv, 3) if cv is not None else None,
        "delivery_class": dc,
        "mismatch_risk": int(lower and dc == "scripted"),
        "cta_kind": kind, "cta_position_pct": round(pos, 1) if pos is not None else None,
    }


def run_packaging(conn: sqlite3.Connection, cfg: dict[str, Any], only_missing: bool = True) -> dict[str, Any]:
    import json
    niches = {n["name"]: n for n in cfg.get("niches") or []}
    sql = ("SELECT v.video_id, v.title, v.niche, t.segments_json, t.text, t.length_seconds FROM transcripts t "
           "JOIN videos v ON v.video_id = t.video_id")
    if only_missing:
        sql += " WHERE v.video_id NOT IN (SELECT video_id FROM packaging)"
    n = 0
    for r in yti_rs_db.rows(conn, sql):
        try:
            segs = json.loads(r["segments_json"] or "[]")
        except json.JSONDecodeError:
            segs = []
        niche = niches.get(r["niche"], {})
        p = compute_packaging(r["title"], segs, r["text"] or "", r["length_seconds"],
                              niche.get("outcome_terms") or [], niche.get("mechanism_terms") or [])
        conn.execute("""INSERT OR REPLACE INTO packaging(video_id, computed_at, point_count, structure_class,
            promise_restated_sec, has_promise, has_proof, has_plan, has_persona, proof_sec, awareness_frame,
            title_lowercase, filler_rate, sentence_len_cv, delivery_class, mismatch_risk, cta_kind,
            cta_position_pct, llm_notes_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, (SELECT llm_notes_json FROM packaging WHERE video_id = ?))""",
            (r["video_id"], yti_rs_db.now_iso(), p["point_count"], p["structure_class"], p["promise_restated_sec"],
             p["has_promise"], p["has_proof"], p["has_plan"], p["has_persona"], p["proof_sec"], p["awareness_frame"],
             p["title_lowercase"], p["filler_rate"], p["sentence_len_cv"], p["delivery_class"], p["mismatch_risk"],
             p["cta_kind"], p["cta_position_pct"], r["video_id"]))
        n += 1
    conn.commit()
    return {"packaged": n}
