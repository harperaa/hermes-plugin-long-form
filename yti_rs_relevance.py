"""Topical relevance: is a video actually about its niche?

A niche label on a video only says which crawl found it. Channel expansion
pulls whole back-catalogues, so one relevant CNN video used to drag 80 news
clips into the niche. Relevance is decided from titles against the niche's
own vocabulary:

  vocabulary = words of the niche's seed terms + mechanism terms + topic
               terms (outcome terms are left out on purpose: "make money",
               "get hired" describe a desire, not a subject)

  a CHANNEL is on-topic for a niche when at least ``min_share`` of its known
  long-form titles mention a vocabulary word (needs ``min_titles`` titles to
  judge), or when the operator follows/tracks it;

  a VIDEO is in-niche unless its channel is judged OFF-topic. On an off-topic
  channel a video stays only when its title carries at least two of the
  niche's CORE words (seed + mechanism terms): one broad word is not enough
  there — "attack", "virus" or "security" on a news network is war, health
  and politics, not the niche.

Pure functions; no DB, no network.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional

# function words, YouTube filler, and words too ambiguous to signal a subject
GENERIC = frozenset("""
a an the of to in on at by for with from into out up as is are was were be been being am do does did
and or but if then so not no yes all any some more most much many very just really only still even ever
i you he she it we they me my your our their his her its this that these those what why how when where who which
can could will would should may might must need want know learn get got make made use using used let
new best top vs versus first next last every now today day days week weeks month months year years time
video videos full course guide tutorial explained explains tips ways things mistakes tools review live
good bad big small real right wrong better great own way work works working start stop
one two three person people system content model red thing stuff
2023 2024 2025 2026 2027
""".split())

_WORD = re.compile(r"[a-z0-9][a-z0-9\-]*")


def _norm(word: str) -> str:
    """Light plural folding: 'agents' -> 'agent', 'hackers' -> 'hacker'."""
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def words(text: Any) -> set[str]:
    return {_norm(w) for w in _WORD.findall(str(text or "").lower())}


def niche_vocab(niche: dict[str, Any]) -> frozenset[str]:
    out: set[str] = set()
    for key in ("seed_terms", "mechanism_terms", "topic_terms"):
        for term in niche.get(key) or []:
            for w in _WORD.findall(str(term).lower()):
                if len(w) > 1 and w not in GENERIC:
                    out.add(_norm(w))
    return frozenset(out)


def niche_core_vocab(niche: dict[str, Any]) -> frozenset[str]:
    """The niche's own phrases only (seed + mechanism terms), without the broad
    topic terms. Used for the stricter test applied to off-topic channels."""
    return niche_vocab({"seed_terms": niche.get("seed_terms"), "mechanism_terms": niche.get("mechanism_terms")})


def match_count(title: Any, vocab: Iterable[str]) -> int:
    vocab = vocab if isinstance(vocab, (set, frozenset)) else set(vocab)
    return len(words(title) & vocab)


def title_matches(title: Any, vocab: Iterable[str]) -> bool:
    vocab = vocab if isinstance(vocab, (set, frozenset)) else set(vocab)
    return bool(vocab) and bool(words(title) & vocab)


def channel_share(titles: Iterable[Any], vocab: Iterable[str]) -> tuple[Optional[float], int]:
    """(share of titles that match, number of titles). Share is None with no titles."""
    vocab = vocab if isinstance(vocab, (set, frozenset)) else set(vocab)
    titles = [t for t in titles if t]
    if not titles:
        return None, 0
    return sum(1 for t in titles if words(t) & vocab) / len(titles), len(titles)


def channel_on_topic(titles: Iterable[Any], vocab: Iterable[str], *, min_share: float = 0.20,
                     min_titles: int = 5) -> Optional[bool]:
    """True/False once there are enough titles to judge; None = not enough evidence."""
    share, n = channel_share(titles, vocab)
    if share is None or n < min_titles:
        return None
    return share >= min_share


# A broad security vocabulary offered as topic terms for security niches, so
# general security channels (which rarely use the exact seed phrases) stay in.
SECURITY_TOPIC_TERMS = [
    "security", "secure", "cybersecurity", "cyber", "infosec", "hack", "hacked", "hacker", "hacking",
    "exploit", "exploited", "exploitation", "vulnerability", "vulnerabilities", "malware", "ransomware",
    "phishing", "breach", "breached", "attack", "attacker", "encryption", "encrypted", "cryptography",
    "password", "authentication", "pentest", "pentesting", "ctf", "zero-day", "backdoor", "rootkit",
    "botnet", "firewall", "privacy", "surveillance", "spyware", "trojan", "virus", "buffer", "overflow",
    "reverse", "engineering", "binary", "kernel", "sandbox", "jailbreak", "injection", "xss", "sql", "rce",
    "cve", "bug", "bounty", "opsec", "owasp", "threat", "guardrails", "teaming", "leak", "leaked", "scam",
    "fraud", "deepfake",
]
