"""Research configuration (niches, crawl, scoring, formats, apify, budget).

Stored as JSON in ``research.db`` (meta key ``config``) and edited from the
dashboard tab; defaults mirror the spec's ``config.example.yaml``. Secrets
come ONLY from the environment / ``~/.hermes/.env`` — never from config.
"""
from __future__ import annotations

import copy
import os
from typing import Any, Optional

try:
    from . import yti_paths, yti_rs_db
    from .yti_rs_logging import Secrets
except ImportError:  # pragma: no cover
    import yti_paths  # type: ignore
    import yti_rs_db  # type: ignore
    from yti_rs_logging import Secrets  # type: ignore

CONFIG_KEY = "config"

DEFAULT_MATURITY_CURVE = [[1, 0.15], [3, 0.35], [7, 0.55], [14, 0.75], [28, 1.0]]

DEFAULT_CONFIG: dict[str, Any] = {
    "niches": [],
    "crawl": {
        "max_depth": 3,
        "search_pages_per_term": 2,
        "channel_pages_per_channel": 2,
        "outliers_to_expand_per_node": 4,
        "prune_after_barren_nodes": 2,
        "min_subscribers": 1000,
        # research channels of a comparable size: a 4x on a 60k channel is a
        # lesson you can use; a 4x on a 20M news network is not
        "max_subscribers": 100000,
        # a channel is on-topic for a niche when at least this share of its
        # long-form titles mention the niche's vocabulary (yti_rs_relevance)
        "channel_relevance_min": 0.20,
        "relevance_min_titles": 5,
        "term_variants": ["{term}", "how to {term}", "{term} mistakes"],
        "recommendations_per_video": 3,
        "include_followed_channels": True,
        # pay for a node once: a search or channel expanded within this many
        # days (any run) is skipped; a channel whose catalogue is already in
        # the DB is only refreshed through the free channel/latest call
        "refresh_after_days": 14,
        "repage_known_channels": False,
    },
    "scoring": {
        "baseline_window": 20,
        "min_baseline_videos": 6,
        "maturity_days": 28,
        "hit_multiple": 3.0,
        "strong_multiple": 5.0,
        "underperformer_multiple": 0.4,
        "breakout_watch_max_age_days": 14,
        "breakout_watch_comment_pct": 80,
        "exclude_suspect_paid_from_formats": True,
    },
    # Tier 3 (transcripts, comments) is spent on the focus quadrant of the
    # Supply / Demand view only: recent AND running well above normal.
    # focus_multiple 0 means "use scoring.hit_multiple"; focus_only False
    # tears down every hit (the old behaviour).
    "teardown": {
        "focus_only": True,
        "focus_days": 7,
        "focus_multiple": 0,
    },
    "formats": {
        "min_distinct_channels": 3,
        "min_distinct_niches": 2,
        "min_shingle_words": 3,
        "max_shingle_words": 7,
        "min_support": 4,
        "gap_min_wilson_lb": 0.25,
        "min_actionable_n": 10,
    },
    "apify": {
        "search_actor": "streamers/youtube-scraper",
        "channel_actor": "streamers/youtube-channel-scraper",
        "comments_actor": "streamers/youtube-comments-scraper",
        "sync_timeout_secs": 290,
        "async_timeout_secs": 1800,
        "max_comments_per_video": 300,
    },
    "budget": {
        "transcriptapi_credits": 2000,
        "apify_max_results": 5000,
        "anthropic_max_calls": 200,
        "crawl_default_credits": 150,
    },
}

NICHE_TEMPLATE: dict[str, Any] = {
    "name": "",
    "is_target": False,
    "signal_half_life_days": 365,
    "seed_terms": [],
    "outcome_terms": [],
    "mechanism_terms": [],
    # extra subject vocabulary used only to decide what is in/out of the niche
    "topic_terms": [],
}


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_config(conn) -> dict[str, Any]:
    stored = yti_rs_db.get_meta_json(conn, CONFIG_KEY, {}) or {}
    cfg = _merge(DEFAULT_CONFIG, stored)
    cfg["niches"] = [_merge(NICHE_TEMPLATE, n) for n in (stored.get("niches") or [])]
    return cfg


def validate_config(cfg: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    niches = cfg.get("niches") or []
    names = [str(n.get("name") or "").strip() for n in niches]
    if any(not n for n in names):
        problems.append("every niche needs a name")
    if len(set(names)) != len(names):
        problems.append("niche names must be unique")
    targets = [n for n in niches if n.get("is_target")]
    if niches and len(targets) != 1:
        problems.append("exactly one niche must be marked as the target")
    for n in niches:
        try:
            if float(n.get("signal_half_life_days") or 0) <= 0:
                problems.append(f"niche {n.get('name')}: signal_half_life_days must be > 0")
        except (TypeError, ValueError):
            problems.append(f"niche {n.get('name')}: signal_half_life_days must be numeric")
        if not [t for t in (n.get("seed_terms") or []) if str(t).strip()]:
            problems.append(f"niche {n.get('name')}: at least one seed term")
    return problems


def save_config(conn, cfg: dict[str, Any]) -> dict[str, Any]:
    clean = _merge(DEFAULT_CONFIG, {k: v for k, v in cfg.items() if k != "niches"})
    clean["niches"] = []
    for n in cfg.get("niches") or []:
        niche = _merge(NICHE_TEMPLATE, n)
        niche["name"] = str(niche["name"]).strip()
        for key in ("seed_terms", "outcome_terms", "mechanism_terms", "topic_terms"):
            vals = niche.get(key) or []
            if isinstance(vals, str):
                vals = [v for v in vals.replace("\n", ",").split(",")]
            if key == "topic_terms":       # one per line or comma-separated
                vals = [part for v in vals for part in str(v).split(",")]
            niche[key] = [str(v).strip() for v in vals if str(v).strip()]
        niche["is_target"] = bool(niche.get("is_target"))
        niche["signal_half_life_days"] = float(niche.get("signal_half_life_days") or 365)
        clean["niches"].append(niche)
    problems = validate_config(clean)
    if problems:
        raise ValueError("; ".join(problems))
    yti_rs_db.set_meta(conn, CONFIG_KEY, clean)
    return clean


def target_niche(cfg: dict[str, Any]) -> Optional[dict[str, Any]]:
    for n in cfg.get("niches") or []:
        if n.get("is_target"):
            return n
    return None


def niche_by_name(cfg: dict[str, Any], name: str) -> Optional[dict[str, Any]]:
    for n in cfg.get("niches") or []:
        if n.get("name") == name:
            return n
    return None


# -- secrets ----------------------------------------------------------------

def env_value(name: str) -> str:
    """Environment first, then ``<HERMES_HOME>/.env`` (the dashboard's Keys page)."""
    val = (os.environ.get(name) or "").strip()
    if val:
        return val
    env_file = yti_paths.get_hermes_home() / ".env"
    try:
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def get_secrets() -> Secrets:
    return Secrets(
        transcriptapi_key=env_value("TRANSCRIPT_API_KEY"),
        apify_token=env_value("APIFY_API_TOKEN") or env_value("APIFY_TOKEN"),
        anthropic_key=env_value("ANTHROPIC_API_KEY"),
    )


def require_secrets(*providers: str) -> Secrets:
    """Fail before any network call with the list of missing variables."""
    s = get_secrets()
    missing = s.missing(tuple(providers))
    if missing:
        raise RuntimeError("missing required environment variables: " + ", ".join(missing)
                           + " — set them on the dashboard Keys page or in ~/.hermes/.env")
    return s
