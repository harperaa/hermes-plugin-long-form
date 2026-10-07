"""Redacting logger + Secrets container (spec §14 / P7).

Every log line from the research engine passes through ``RedactingFilter``,
which scrubs API keys and bearer tokens from ``msg`` and ``args`` both. A
``Secrets`` object never reveals its values in ``repr``/``str``.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

_PATTERNS = [
    re.compile(r"sk_[A-Za-z0-9_\-]{6,}"),
    re.compile(r"apify_api_[A-Za-z0-9_\-]{6,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{6,}"),
    re.compile(r"oat_[A-Za-z0-9_\-]{6,}"),
    re.compile(r"AIza[A-Za-z0-9_\-]{20,}"),                       # Google API keys
    re.compile(r"(?i)(x-goog-api-key[\"']?\s*[:=]\s*[\"']?)[A-Za-z0-9_\-]{6,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9_\-\.=]{6,}"),
    re.compile(r"(?i)([?&](?:token|key|api_key|apikey|access_token)=)[^&\s\"']+"),
]


def redact(text: Any) -> str:
    s = str(text)
    for pat in _PATTERNS:
        if pat.groups:
            s = pat.sub(lambda m: m.group(1) + "***", s)
        else:
            s = pat.sub("***", s)
    return s


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = redact(record.msg)
            elif record.msg is not None and not isinstance(record.msg, (int, float)):
                record.msg = redact(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {k: _redact_arg(v) for k, v in record.args.items()}
                else:
                    record.args = tuple(_redact_arg(a) for a in record.args)
        except Exception:  # noqa: BLE001 - never break logging
            pass
        return True


def _redact_arg(value: Any) -> Any:
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact(value)


_INSTALLED = False


def get_logger(name: str = "yti.research") -> logging.Logger:
    """Logger with the redaction filter attached (once per process, on the
    ``yti.research`` root so every child inherits it)."""
    global _INSTALLED
    root = logging.getLogger("yti.research")
    if not _INSTALLED:
        root.addFilter(RedactingFilter())
        for h in root.handlers:
            h.addFilter(RedactingFilter())
        _INSTALLED = True
    return logging.getLogger(name)


class Secrets:
    """Holds API credentials; repr/str never leak them."""

    __slots__ = ("transcriptapi_key", "apify_token", "anthropic_key", "youtube_key")

    def __init__(self, transcriptapi_key: str = "", apify_token: str = "",
                 anthropic_key: str = "", youtube_key: str = "") -> None:
        self.transcriptapi_key = transcriptapi_key or ""
        self.apify_token = apify_token or ""
        self.anthropic_key = anthropic_key or ""
        self.youtube_key = youtube_key or ""

    def present(self) -> dict[str, bool]:
        return {
            "transcriptapi": bool(self.transcriptapi_key),
            "apify": bool(self.apify_token),
            "anthropic": bool(self.anthropic_key),
            "youtube": bool(self.youtube_key),
        }

    def missing(self, required: tuple[str, ...] = ("transcriptapi",)) -> list[str]:
        names = {"transcriptapi": "TRANSCRIPT_API_KEY", "apify": "APIFY_API_TOKEN",
                 "anthropic": "ANTHROPIC_API_KEY", "youtube": "YOUTUBE_API_KEY"}
        have = self.present()
        return [names[r] for r in required if not have.get(r)]

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "Secrets(***)"

    __str__ = __repr__
