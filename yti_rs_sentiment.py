"""Lexicon sentiment (default implementation, §8.7). No network, no cost,
deterministic. Includes the ``early_adopter`` phrase class — a distinct,
highly predictive signal ("here before this blows up") that gets its own
boolean rather than being folded into positive sentiment.

An optional LLM batch implementation sits behind the same interface in
``yti_rs_llm``.
"""
from __future__ import annotations

import re
from typing import Any, Optional

POSITIVE = {
    "love": 2, "loved": 2, "amazing": 2, "awesome": 2, "excellent": 2, "brilliant": 2,
    "fantastic": 2, "incredible": 2, "perfect": 2, "best": 2, "great": 1.5, "good": 1,
    "helpful": 1.5, "helped": 1.5, "useful": 1.5, "clear": 1, "thank": 1.5, "thanks": 1.5,
    "thankyou": 1.5, "appreciate": 1.5, "gold": 1.5, "goat": 2, "legend": 2, "underrated": 1.5,
    "insightful": 1.5, "valuable": 1.5, "wow": 1, "inspiring": 1.5, "inspired": 1.5,
    "finally": 0.5, "exactly": 0.5, "agree": 1, "right": 0.5, "true": 0.5, "well": 0.5,
    "recommend": 1.5, "subscribed": 1.5, "subscribe": 1, "liked": 1, "like": 0.5,
    "enjoyed": 1.5, "enjoy": 1, "masterpiece": 2, "quality": 1, "learned": 1, "learnt": 1,
    "works": 1, "worked": 1.5, "genius": 2, "nice": 1, "solid": 1, "fire": 1.5, "🔥": 1.5,
    "❤": 1.5, "❤️": 1.5, "👏": 1, "🙏": 1, "💯": 1.5, "😍": 1.5,
}
NEGATIVE = {
    "hate": -2, "hated": -2, "terrible": -2, "awful": -2, "worst": -2, "bad": -1.5,
    "boring": -1.5, "clickbait": -2, "scam": -2, "fake": -2, "wrong": -1.5, "waste": -2,
    "useless": -2, "misleading": -2, "lie": -2, "lies": -2, "lying": -2, "stupid": -1.5,
    "dumb": -1.5, "garbage": -2, "trash": -2, "disappointed": -1.5, "disappointing": -1.5,
    "annoying": -1.5, "confusing": -1, "unsubscribed": -2, "unsubscribe": -1.5,
    "dislike": -1.5, "meh": -1, "cringe": -1.5, "nonsense": -1.5, "bs": -1.5, "ad": -0.5,
    "sponsored": -0.5, "🤮": -2, "👎": -1.5, "😴": -1,
}
NEGATORS = {"not", "no", "never", "dont", "don't", "doesnt", "doesn't", "didnt", "didn't",
            "isnt", "isn't", "wasnt", "wasn't", "cant", "can't", "wont", "won't", "hardly"}
INTENSIFIERS = {"very": 1.4, "so": 1.3, "really": 1.3, "absolutely": 1.5, "super": 1.3,
                "extremely": 1.5, "truly": 1.3, "insanely": 1.4}

EARLY_ADOPTER = [
    re.compile(r"(?i)\bhere before (?:this|it|he|she|they|you) (?:blows?|blew) up\b"),
    re.compile(r"(?i)\bhere before (?:\d[\dkm,\.]*|a million|1m|100k)\b"),
    re.compile(r"(?i)\b(?:this|it|he|she|they|you|channel)(?:'s| is| are|'re)? (?:going|gonna|about) to (?:blow up|go viral|explode|be huge|be big)\b"),
    re.compile(r"(?i)\bwhy (?:does|do) (?:this|it|he|she|they|you) (?:only )?(?:have|has|got) (?:only )?[\d,\.]+\s*[km]? (?:views|subs|subscribers)\b"),
    re.compile(r"(?i)\bdeserves? (?:way |so |a lot )?more (?:views|subs|subscribers|attention|recognition)\b"),
    re.compile(r"(?i)\b(?:criminally|massively|so|seriously|extremely) underrated\b"),
    re.compile(r"(?i)\bunderrated (?:channel|video|creator|gem)\b"),
    re.compile(r"(?i)\bhidden gem\b"),
    re.compile(r"(?i)\bthe algorithm (?:finally )?(?:blessed|brought) me\b"),
    re.compile(r"(?i)\b(?:will|gonna|going to) (?:hit|reach) [\d,\.]+\s*[km]? (?:subs|subscribers)\b"),
]

_TOKEN_RE = re.compile(r"[a-z']+|[\U0001F300-\U0001FAFF☀-➿❤️]")


def is_early_adopter(text: str) -> bool:
    return any(p.search(text or "") for p in EARLY_ADOPTER)


def score_comment(text: str) -> tuple[float, bool]:
    """Return (sentiment in -1..+1, is_early_adopter)."""
    if not text:
        return 0.0, False
    tokens = _TOKEN_RE.findall(text.lower())
    total, hits = 0.0, 0
    for i, tok in enumerate(tokens):
        w = POSITIVE.get(tok)
        if w is None:
            w = NEGATIVE.get(tok)
        if w is None:
            continue
        window = tokens[max(0, i - 3):i]
        if any(t in NEGATORS for t in window):
            w = -w * 0.8
        for t in window:
            if t in INTENSIFIERS:
                w *= INTENSIFIERS[t]
        total += w
        hits += 1
    early = is_early_adopter(text)
    if early:
        total += 1.5
        hits += 1
    if hits == 0:
        return 0.0, early
    # squash: average weight → tanh-ish bound
    avg = total / hits
    score = max(-1.0, min(1.0, avg / 2.0))
    return round(score, 3), early


def score_batch(comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for c in comments:
        s, e = score_comment(str(c.get("text") or ""))
        out.append({**c, "sentiment": s, "is_early_adopter": int(e)})
    return out


def positive_share(sentiments: list[Optional[float]], threshold: float = 0.2) -> Optional[float]:
    vals = [s for s in sentiments if s is not None]
    if not vals:
        return None
    return sum(1 for s in vals if s > threshold) / len(vals)
