#!/usr/bin/env python3
"""Render a youtube-artifact page (index.html) from artifact.json.

The agent writes CONTENT (artifact.json, see schema.md); this script owns the
LOOK (tokens, type, layout, the two timeline renderers) so every page built by
the long-form plugin matches the reference pages. It also runs the quality
gate the completion validator re-runs after the worker finishes: a page that
fails here never counts as done.

Usage:
  python3 build_artifact.py <artifact-dir>            # validate + render index.html
  python3 build_artifact.py <artifact-dir> --check    # validate only, no write
  python3 build_artifact.py <artifact-dir> --json     # machine-readable report

Exit code 0 = clean, 1 = problems (listed), 2 = cannot read artifact.json.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TONES = {"", "crit", "warn", "ok", "na", "info"}
BOX_TONES = {"yes", "no", "maybe"}
SECTION_TYPES = {"facts", "list", "timeline", "cards", "table", "matrix",
                 "steps", "callout", "boxes", "checklist", "faq", "sources",
                 "prose"}
# The page plan: the parts the dashboard's Artifact modal lets the operator
# keep or skip, in page order. A doc may carry `plan: [type, ...]` (a subset
# of these, always with "sources"); the gate then demands every planned type
# and refuses any section outside the plan. Without a plan the classic rules
# apply (4+ sections, steps and sources required).
PLAN_PARTS: list[dict[str, Any]] = [
    {"type": "callout", "label": "Framing callout", "desc": "What the name or claim is and is not; corrections"},
    {"type": "facts", "label": "Summary tiles", "desc": "The shape of it: 3 or 4 tiles, one number each"},
    {"type": "list", "label": "Lead-up list", "desc": "Dated list of what was loaded before the window"},
    {"type": "timeline", "label": "Timeline graphics", "desc": "Day timeline, plus the worst day hour by hour"},
    {"type": "cards", "label": "Top problems", "desc": "The ranked incidents or items, each with its source"},
    {"type": "table", "label": "Also-rans table", "desc": "Verified but outside the ranking, and the not-founds"},
    {"type": "matrix", "label": "Failure-pattern matrix", "desc": "What failed, item by item"},
    {"type": "steps", "label": "Solutions with images", "desc": "Numbered recommendations, one sketchnote each"},
    {"type": "boxes", "label": "Stop / do before / keep doing", "desc": "What it means for the audience"},
    {"type": "faq", "label": "FAQ", "desc": "Questions the viewer will ask, and what stayed unverified"},
    {"type": "sources", "label": "Sources", "desc": "The bibliography; always included", "locked": True},
]
PLAN_TYPES = [x["type"] for x in PLAN_PARTS]
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
MIN_IMAGE_BYTES = 20_000
BANNED_IN_PROSE = ("—",)          # em dash: the house style never uses it
# The page is the deliverable, not a script for whoever presents it: no stage
# directions, no "say this", no slide or camera talk. (Case-insensitive.)
PRESENTER_RE = re.compile(
    r"\b(before you record|on camera|on screen|talk track|this slide|the slide|"
    r"say (this|that|it) (out loud|on)|do not say|don't say|say \"|presenter'?s? (rule|note)|"
    r"recording checklist|when you present|tell (the|your) (viewers|audience|mentees))\b",
    re.IGNORECASE)
URL_RE = re.compile(r"^https?://[^\s]+$")
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?)?(Z|[+-]\d{2}:\d{2})?$")


# ---------------------------------------------------------------------------
# Inline markup: text is escaped first, then a tiny markdown subset is applied
# (`code`, **bold**, [text](https://url)). Nothing else becomes HTML.
# ---------------------------------------------------------------------------

def esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")


def inline(s: Any) -> str:
    t = esc(s)
    t = _LINK.sub(lambda m: f'<a href="{m.group(2)}" target="_blank" rel="noopener">{m.group(1)}</a>', t)
    t = _CODE.sub(r"<code>\1</code>", t)
    t = _BOLD.sub(r"<b>\1</b>", t)
    return t


def chip(text: str, tone: str = "") -> str:
    cls = "chip" + (f" {tone}" if tone else "")
    return f'<span class="{cls}">{inline(text)}</span>'


def paras(items: list[str], cls: str = "") -> str:
    attr = f' class="{cls}"' if cls else ""
    return "".join(f"<p{attr}>{inline(p)}</p>" for p in items)


def _ts(v: str) -> float:
    """ISO date/datetime -> epoch seconds (naive values are UTC)."""
    s = str(v)
    if len(s) == 10:
        s += "T00:00"
    if not re.search(r"(Z|[+-]\d{2}:\d{2})$", s):
        s += "+00:00"
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _walk_text(node: Any, path: str, out: list[tuple[str, str]]) -> None:
    if isinstance(node, str):
        out.append((path, node))
    elif isinstance(node, dict):
        for k, v in node.items():
            if k in ("url", "image", "css_extra", "id"):
                continue
            _walk_text(v, f"{path}.{k}", out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk_text(v, f"{path}[{i}]", out)


def plan_errors(plan: Any) -> list[str]:
    """Why a plan (list of section types) is unusable; empty when fine."""
    if not isinstance(plan, list) or not all(isinstance(t, str) for t in plan):
        return ["must be a list of section type names"]
    bad = [t for t in plan if t not in PLAN_TYPES]
    if bad:
        return [f"unknown part(s) {bad}; allowed {PLAN_TYPES}"]
    if len(set(plan)) != len(plan):
        return ["lists a part twice"]
    if "sources" not in plan:
        return ["must include 'sources'; every claim on the page needs a source"]
    if len(plan) < 2:
        return ["needs at least one content section besides 'sources'"]
    return []


def normalize_plan(parts: Any) -> list[str]:
    """Order a plan in page order (sources always last). Raises ValueError."""
    if not isinstance(parts, (list, tuple)):
        raise ValueError("plan must be a list")
    chosen = {str(t).strip() for t in parts if str(t).strip()}
    unknown = sorted(chosen - set(PLAN_TYPES))
    if unknown:
        raise ValueError(f"unknown part(s) {unknown}; allowed {PLAN_TYPES}")
    chosen.add("sources")
    plan = [t for t in PLAN_TYPES if t in chosen]
    errs = plan_errors(plan)
    if errs:
        raise ValueError("; ".join(errs))
    return plan


def is_full_plan(plan: Any) -> bool:
    return isinstance(plan, list) and sorted(plan) == sorted(PLAN_TYPES)


def validate(doc: Any, base_dir: Path) -> list[str]:
    p: list[str] = []
    if not isinstance(doc, dict):
        return ["artifact.json must be a JSON object"]

    def need(obj: dict, key: str, where: str, typ=str) -> Any:
        v = obj.get(key)
        if v is None or (typ is str and not str(v).strip()):
            p.append(f"{where}: missing '{key}'")
            return None
        if not isinstance(v, typ):
            p.append(f"{where}: '{key}' must be {typ.__name__}")
            return None
        return v

    title = need(doc, "title", "top level")
    if title and len(title.split()) > 6:
        p.append("title: keep it to a name of 2 to 6 words; put the explanation in the lede")
    lede = need(doc, "lede", "top level")
    if lede and len(lede.split()) < 20:
        p.append("lede: under 20 words; say what the page is and who it is for")
    need(doc, "eyebrow", "top level")
    chips = doc.get("chips") or []
    if not isinstance(chips, list) or not chips:
        p.append("chips: give 2 to 4 status chips under the lede")
    for i, c in enumerate(chips if isinstance(chips, list) else []):
        if not isinstance(c, dict) or not c.get("text"):
            p.append(f"chips[{i}]: needs 'text'")
        elif (c.get("tone") or "") not in TONES:
            p.append(f"chips[{i}]: tone must be one of {sorted(TONES)}")

    sections = doc.get("sections")
    sections = sections if isinstance(sections, list) else []
    types = [s.get("type") for s in sections if isinstance(s, dict)]
    plan = doc.get("plan")
    if plan is not None:
        plan_problems = plan_errors(plan)
        p.extend(f"plan: {e}" for e in plan_problems)
        if not plan_problems:
            for t in plan:
                if t not in types:
                    p.append(f"sections: the plan asks for a '{t}' section and none is present")
            for i, t in enumerate(types):
                if t not in plan:
                    p.append(f"sections[{i}] ({t}): not in this page's plan {plan}; remove it (do not pad the page)")
    else:
        if len(sections) < 4:
            p.append("sections: at least 4 sections (facts, one graphic or cards, steps, sources)")
        if "steps" not in types:
            p.append("sections: a 'steps' section (the numbered recommendations with images) is required")
        if "sources" not in types:
            p.append("sections: a 'sources' section is required; every claim on the page needs a source")

    for i, s in enumerate(sections):
        w = f"sections[{i}]"
        if not isinstance(s, dict):
            p.append(f"{w}: must be an object"); continue
        t = s.get("type")
        if t not in SECTION_TYPES:
            p.append(f"{w}: unknown type {t!r}; allowed {sorted(SECTION_TYPES)}"); continue
        if t not in ("callout",) and not str(s.get("heading") or "").strip():
            p.append(f"{w} ({t}): missing 'heading'")
        items = s.get("items")
        if t in ("facts", "list", "cards", "steps", "boxes", "checklist", "faq", "sources") \
                and (not isinstance(items, list) or not items):
            p.append(f"{w} ({t}): 'items' must be a non-empty list"); continue
        if t == "facts":
            for j, it in enumerate(items):
                if not it.get("label") or not (it.get("bullets") or it.get("text")):
                    p.append(f"{w}.items[{j}]: facts need 'label' and 'bullets' (2 to 6 short items; 'text' is the fallback), 'big' optional")
                elif it.get("bullets") and (not isinstance(it["bullets"], list) or len(it["bullets"]) > 6
                                            or any(len(str(b).split()) > 16 for b in it["bullets"])):
                    p.append(f"{w}.items[{j}]: fact bullets are 1 to 6 items of at most 16 words each")
        elif t == "list":
            for j, it in enumerate(items):
                if not it.get("text"):
                    p.append(f"{w}.items[{j}]: list items need 'text' ('when' optional)")
        elif t == "timeline":
            _validate_timeline(s, w, p)
        elif t == "cards":
            for j, it in enumerate(items):
                cw = f"{w}.items[{j}]"
                if not it.get("title"):
                    p.append(f"{cw}: card needs 'title'")
                if not it.get("paragraphs"):
                    p.append(f"{cw}: card needs 'paragraphs'")
                if not it.get("say"):
                    p.append(f"{cw}: card needs 'say' (the one-line talk track)")
                srcs = it.get("sources") or []
                if not srcs:
                    p.append(f"{cw}: card needs at least one source link")
                for k, l in enumerate(srcs):
                    if not isinstance(l, dict) or not URL_RE.match(str(l.get("url") or "")) or not l.get("text"):
                        p.append(f"{cw}.sources[{k}]: needs 'text' and an http(s) 'url'")
                for k, c in enumerate(it.get("chips") or []):
                    if (c.get("tone") or "") not in TONES:
                        p.append(f"{cw}.chips[{k}]: bad tone")
        elif t == "table":
            cols = s.get("columns") or []
            if not cols:
                p.append(f"{w}: table needs 'columns'")
            for j, r in enumerate(s.get("rows") or []):
                if not isinstance(r, list) or len(r) != len(cols):
                    p.append(f"{w}.rows[{j}]: must have {len(cols)} cells")
            if not s.get("rows"):
                p.append(f"{w}: table needs 'rows'")
        elif t == "matrix":
            cols = s.get("columns") or []
            if len(cols) < 2:
                p.append(f"{w}: matrix needs at least 2 columns")
            for j, r in enumerate(s.get("rows") or []):
                marks = r.get("marks") if isinstance(r, dict) else None
                if not isinstance(marks, list) or len(marks) != len(cols) or not r.get("label"):
                    p.append(f"{w}.rows[{j}]: needs 'label' and 'marks' with {len(cols)} 0/1 entries")
            if not s.get("rows"):
                p.append(f"{w}: matrix needs 'rows'")
        elif t == "steps":
            if len(items) < 3:
                p.append(f"{w}: give at least 3 steps")
            for j, it in enumerate(items):
                sw = f"{w}.items[{j}]"
                if not it.get("title") or not it.get("text"):
                    p.append(f"{sw}: step needs 'title' and 'text'")
                img = it.get("image")
                if not img:
                    p.append(f"{sw}: step needs 'image' (img/<name>.jpg made by generate-image)")
                else:
                    _validate_image(img, base_dir, sw, p)
                if not it.get("image_alt"):
                    p.append(f"{sw}: step needs 'image_alt'")
        elif t == "callout":
            if (s.get("tone") or "") not in TONES or not s.get("paragraphs"):
                p.append(f"{w}: callout needs 'paragraphs' and a tone in {sorted(TONES)}")
        elif t == "boxes":
            for j, it in enumerate(items):
                if it.get("tone") not in BOX_TONES or not it.get("title") or not it.get("bullets"):
                    p.append(f"{w}.items[{j}]: box needs tone yes|no|maybe, 'title', 'bullets'")
        elif t == "checklist":
            for j, it in enumerate(items):
                if not isinstance(it, str) or not it.strip():
                    p.append(f"{w}.items[{j}]: checklist items are strings")
        elif t == "faq":
            for j, it in enumerate(items):
                if not it.get("q") or not it.get("a"):
                    p.append(f"{w}.items[{j}]: faq needs 'q' and 'a'")
        elif t == "sources":
            for j, it in enumerate(items):
                links = it.get("links") or []
                if not it.get("text") or not links:
                    p.append(f"{w}.items[{j}]: source needs 'text' and 'links'")
                for k, l in enumerate(links):
                    if not isinstance(l, dict) or not URL_RE.match(str(l.get("url") or "")) or not l.get("text"):
                        p.append(f"{w}.items[{j}].links[{k}]: needs 'text' and an http(s) 'url'")
        elif t == "prose":
            if not s.get("paragraphs"):
                p.append(f"{w}: prose needs 'paragraphs'")

    # house style across every text field
    texts: list[tuple[str, str]] = []
    _walk_text(doc, "doc", texts)
    for path, t in texts:
        for bad in BANNED_IN_PROSE:
            if bad in t:
                p.append(f"{path}: contains an em dash; rewrite as two sentences or a comma")
                break
        if "lorem ipsum" in t.lower():
            p.append(f"{path}: placeholder text")
        if re.search(r"\bTODO\b|\bTBD\b|\[citation needed\]", t):
            p.append(f"{path}: unfinished marker (TODO/TBD)")
        if PRESENTER_RE.search(t):
            p.append(f"{path}: reads as an instruction to the presenter (\"{PRESENTER_RE.search(t).group(0)}\"); the page carries content only")

    # stray files: only the page, its data, images and history belong here
    if base_dir.is_dir():
        allowed_top = {"index.html", "artifact.json", "img", "history", "README.md", "SUMMARY.md",
                       "published.json"}
        for f in base_dir.iterdir():
            if f.name.startswith(".") or f.name in allowed_top:
                continue
            p.append(f"stray file in artifact folder: {f.name} (scratch belongs in the task workspace)")
        img_dir = base_dir / "img"
        if img_dir.is_dir():
            for f in img_dir.iterdir():
                if f.is_file() and f.suffix.lower() not in IMAGE_EXTS and not f.name.startswith("."):
                    p.append(f"non-image file in img/: {f.name}")
    return p


def _validate_image(rel: str, base_dir: Path, where: str, p: list[str]) -> None:
    if not str(rel).startswith("img/") or "/" in str(rel)[4:] or ".." in str(rel):
        p.append(f"{where}: image must be 'img/<file>' inside the artifact folder, got {rel!r}")
        return
    f = base_dir / rel
    if f.suffix.lower() not in IMAGE_EXTS:
        p.append(f"{where}: image must be jpg/png/webp")
    if not f.is_file():
        p.append(f"{where}: image file missing on disk: {rel}")
    elif f.stat().st_size < MIN_IMAGE_BYTES:
        p.append(f"{where}: image {rel} is only {f.stat().st_size} bytes; a real sketchnote is >20 KB")


def _validate_timeline(s: dict, w: str, p: list[str]) -> None:
    unit = s.get("unit") or "day"
    if unit not in ("day", "hour"):
        p.append(f"{w}: timeline unit must be 'day' or 'hour'")
    try:
        t0, t1 = _ts(s.get("start")), _ts(s.get("end"))
    except (TypeError, ValueError):
        p.append(f"{w}: timeline needs ISO 'start' and 'end'"); return
    if t1 <= t0:
        p.append(f"{w}: timeline end must be after start")
    span = (t1 - t0) / 3600
    if unit == "day" and span / 24 > 40:
        p.append(f"{w}: a day timeline wider than 40 days is unreadable; split it")
    if unit == "hour" and span > 24:
        p.append(f"{w}: an hour timeline wider than 24 hours is unreadable; use unit 'day'")
    rows = s.get("rows") or []
    if not rows:
        p.append(f"{w}: timeline needs 'rows'")
    n_events = 0
    for i, r in enumerate(rows):
        rw = f"{w}.rows[{i}]"
        if not isinstance(r, dict) or not r.get("label"):
            p.append(f"{rw}: row needs 'label'"); continue
        evs = r.get("events") or []
        if not evs:
            p.append(f"{rw}: row needs at least one event")
        for j, e in enumerate(evs):
            ew = f"{rw}.events[{j}]"
            if not isinstance(e, dict) or not e.get("text"):
                p.append(f"{ew}: event needs 'text'"); continue
            n_events += 1
            try:
                if "from" in e or "to" in e:
                    a, b = _ts(e.get("from")), _ts(e.get("to"))
                    if b <= a:
                        p.append(f"{ew}: span 'to' must be after 'from'")
                    if a < t0 or b > t1 + 1:
                        p.append(f"{ew}: span lies outside the timeline range")
                else:
                    a = _ts(e.get("at"))
                    if a < t0 or a > t1:
                        p.append(f"{ew}: 'at' lies outside the timeline range")
                    if (e.get("tone") or "") not in TONES:
                        p.append(f"{ew}: bad tone")
            except (TypeError, ValueError):
                p.append(f"{ew}: needs ISO 'at' or 'from'+'to'")
    if n_events < 4:
        p.append(f"{w}: fewer than 4 events; a timeline this thin should be a list")
    for k, pair in enumerate(s.get("shade") or []):
        try:
            if not isinstance(pair, list) or len(pair) != 2 or _ts(pair[1]) <= _ts(pair[0]):
                raise ValueError
        except (TypeError, ValueError):
            p.append(f"{w}.shade[{k}]: must be [start, end] ISO pair")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

CSS = r"""
/* Layout: one reading column; scaled timeline graphics drawn by script from the page's own event list; cards and a failure-pattern matrix feed the numbered recommendations, one sketchnote image each. */
:root{
  --bg:#f6f5f1; --surface:#ffffff; --ink:#1d2026; --muted:#5c6270; --line:#d9d7cf;
  --accent:#0f6e73; --accent-ink:#ffffff;
  --crit:#b3261e; --crit-bg:#fbeae8; --warn:#8a5a00; --warn-bg:#fdf2dc; --ok:#1f6b3a; --ok-bg:#e4f3e8;
  --code-bg:#edf0ee; --grid:#e7e5de; --span:#9fd3d6;
  --display:"Archivo Narrow","Arial Narrow",system-ui,sans-serif;
  --body:"Public Sans",system-ui,-apple-system,"Segoe UI",sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
}
@media (prefers-color-scheme: dark){ :root:not([data-theme="light"]){
  --bg:#15171b; --surface:#1e2127; --ink:#ebebe6; --muted:#a3a8b3; --line:#343941;
  --accent:#5fc3c7; --accent-ink:#0d1517;
  --crit:#ff8a80; --crit-bg:#3a1f1d; --warn:#f2c46d; --warn-bg:#3a2e14; --ok:#8fd9a8; --ok-bg:#1c3324;
  --code-bg:#272b32; --grid:#2a2e36; --span:#2f6d70; color-scheme:dark } }
:root[data-theme="dark"]{
  --bg:#15171b; --surface:#1e2127; --ink:#ebebe6; --muted:#a3a8b3; --line:#343941;
  --accent:#5fc3c7; --accent-ink:#0d1517;
  --crit:#ff8a80; --crit-bg:#3a1f1d; --warn:#f2c46d; --warn-bg:#3a2e14; --ok:#8fd9a8; --ok-bg:#1c3324;
  --code-bg:#272b32; --grid:#2a2e36; --span:#2f6d70; color-scheme:dark }
*{box-sizing:border-box}
html{margin:0;padding:0}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--body);font-size:16px;line-height:1.55;
  padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));padding-block:0 64px}
.wrap{max-width:860px;margin:0 auto}
h1,h2,h3{font-family:var(--display);line-height:1.1;text-wrap:balance;margin:0}
h1{font-size:clamp(36px,7vw,56px);font-weight:700;letter-spacing:-.01em}
h2{font-size:26px;font-weight:600;margin-top:48px;padding-top:20px;border-top:1px solid var(--line)}
h3{font-size:19px;font-weight:600;margin-top:22px}
p{margin:10px 0;max-width:68ch}
a{color:var(--accent)}
.eyebrow{font-family:var(--display);text-transform:uppercase;letter-spacing:.08em;font-size:13px;color:var(--muted);font-weight:600}
header{padding-top:40px}
.lede{font-size:18px;color:var(--muted);max-width:62ch}
.status{display:flex;flex-wrap:wrap;gap:8px;margin-top:16px}
.chip{display:inline-flex;align-items:center;gap:6px;font-size:13px;font-weight:600;padding:4px 10px;border-radius:999px;border:1px solid var(--line);background:var(--surface);white-space:nowrap}
.chip.crit{color:var(--crit);background:var(--crit-bg);border-color:transparent}
.chip.warn{color:var(--warn);background:var(--warn-bg);border-color:transparent}
.chip.ok{color:var(--ok);background:var(--ok-bg);border-color:transparent}
.chip.na{color:var(--muted)}
.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-top:20px}
.fact{background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:12px 14px}
.fact b{display:block;font-family:var(--display);font-size:15px;margin-bottom:4px}
.fact span{font-size:14px;color:var(--muted)}
.fact .big{font-family:var(--display);font-size:34px;font-weight:700;line-height:1;display:block;margin:2px 0 6px;font-variant-numeric:tabular-nums;color:var(--ink)}
.fact ul{margin:4px 0 0;padding-left:16px;font-size:14px;color:var(--muted);line-height:1.4}
.fact li{margin:3px 0}
.callout{border-left:4px solid var(--crit);background:var(--crit-bg);padding:12px 16px;border-radius:0 6px 6px 0;margin:18px 0}
.callout.warn{border-color:var(--warn);background:var(--warn-bg)}
.callout.ok{border-color:var(--ok);background:var(--ok-bg)}
.callout.info,.callout.na{border-color:var(--accent);background:var(--surface)}
.callout p{margin:4px 0}
table{border-collapse:collapse;width:100%;font-size:14.5px;margin-top:14px}
.tablewrap{overflow-x:auto}
th,td{text-align:left;vertical-align:top;padding:10px 10px 10px 0;border-bottom:1px solid var(--line)}
th{font-family:var(--display);font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:600}
code{font-family:var(--mono);font-size:.9em;background:var(--code-bg);padding:1px 5px;border-radius:3px;word-break:break-all}
.num{font-variant-numeric:tabular-nums}
.chart{background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:14px 10px 8px;margin-top:16px}
.chart .cap{font-size:13px;color:var(--muted);padding:0 6px 6px;display:flex;justify-content:space-between;flex-wrap:wrap;gap:6px}
.chart .scroll{overflow-x:auto}
.chart svg{display:block;width:100%;height:auto;min-width:720px;font-family:var(--body)}
.chart svg text{fill:var(--ink)}
.chart svg .mut{fill:var(--muted)}
.chart svg .lab{font-family:var(--display);font-size:13px;font-weight:600}
.chart svg .day{font-size:11px;font-variant-numeric:tabular-nums}
.chart svg .grid{stroke:var(--grid);stroke-width:1}
.chart svg .wk{fill:var(--code-bg)}
.chart svg .span{fill:var(--span);opacity:.9}
.chart svg .dot{fill:var(--accent);stroke:var(--surface);stroke-width:2}
.chart svg .dot.crit{fill:var(--crit)}
.chart svg .dot.ok{fill:var(--ok)}
.chart svg .dot.warn{fill:var(--warn)}
.chart svg .hit{fill:transparent;cursor:pointer}
.chart svg .hit:focus-visible{outline:2px solid var(--accent)}
.chart svg .tick{font-size:10.5px}
.legend{display:flex;flex-wrap:wrap;gap:14px;font-size:13px;color:var(--muted);padding:8px 6px 0}
.legend span{display:inline-flex;align-items:center;gap:6px}
.legend i{width:10px;height:10px;border-radius:50%;display:inline-block;background:var(--accent)}
.legend i.crit{background:var(--crit)}.legend i.ok{background:var(--ok)}.legend i.warn{background:var(--warn)}
.legend i.span{width:18px;height:8px;border-radius:2px;background:var(--span)}
.tip{font-size:14px;min-height:44px;padding:8px 6px 0;border-top:1px dashed var(--line);margin-top:6px;color:var(--ink)}
.tip b{font-family:var(--display)}
.tip .mut{color:var(--muted)}
.chart .note{font-size:14px;color:var(--muted);padding:0 6px}
.pre{list-style:none;margin:14px 0 0;padding:0;display:grid;gap:8px}
.pre li{display:grid;grid-template-columns:120px 1fr;gap:12px;font-size:14.5px;align-items:baseline}
.pre li b{font-family:var(--mono);font-size:13px;color:var(--muted);font-weight:500}
.pre.plain li{grid-template-columns:1fr}
@media (max-width:480px){.pre li{grid-template-columns:1fr}}
.inc{background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:16px 18px;margin-top:14px;position:relative}
.inc .top{display:flex;gap:12px;align-items:flex-start;flex-wrap:wrap}
.inc .n{flex:0 0 auto;width:34px;height:34px;border-radius:50%;background:var(--accent);color:var(--accent-ink);font-family:var(--display);font-weight:700;display:flex;align-items:center;justify-content:center;font-size:16px}
.inc .hd{flex:1 1 300px;min-width:0}
.inc h3{margin:0}
.inc .when{font-family:var(--mono);font-size:12.5px;color:var(--muted);margin-top:2px}
.inc .chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px}
.inc p{font-size:15px;margin:8px 0 0}
.inc .say{margin-top:10px;padding:10px 12px;background:var(--code-bg);border-radius:4px;font-size:14.5px}
.inc .chase{font-size:13.5px;color:var(--muted);margin-top:8px}
.matrix td,.matrix th{text-align:center;padding:8px 6px}
.matrix td:first-child,.matrix th:first-child{text-align:left;white-space:nowrap}
.matrix .y{display:inline-block;width:18px;height:18px;border-radius:50%;background:var(--accent);color:var(--accent-ink);font-size:12px;line-height:18px;font-weight:700}
.matrix tfoot td{font-family:var(--display);font-weight:700;font-size:16px;color:var(--accent)}
ol.steps{padding-left:0;list-style:none;counter-reset:s;margin:18px 0 0}
ol.steps>li{counter-increment:s;position:relative;padding-left:44px;margin:0 0 22px}
ol.steps>li::before{content:counter(s);position:absolute;left:0;top:2px;width:30px;height:30px;border-radius:50%;background:var(--accent);color:var(--accent-ink);font-family:var(--display);font-weight:700;display:flex;align-items:center;justify-content:center;font-size:15px}
ol.steps>li>b{font-family:var(--display);font-size:18px;display:block;margin-bottom:4px}
ol.steps>li>p{margin:4px 0;font-size:15px}
ol.steps .ev{font-size:13.5px;color:var(--muted)}
ol.steps .ev b{font-family:var(--display);font-size:13px;text-transform:uppercase;letter-spacing:.05em;display:inline;margin-right:4px}
ol.steps li.gate::before{background:var(--crit)}
.sg{margin:10px 0 8px;border:1px solid var(--line);border-radius:6px;overflow:hidden;background:var(--surface);width:100%}
.sg img{display:block;width:100%;height:auto;aspect-ratio:16/9;max-width:100%}
.twocol{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px;margin-top:12px}
.twocol .box{border:1px solid var(--line);border-radius:6px;padding:12px 14px;background:var(--surface)}
.twocol .box b{font-family:var(--display);font-size:16px;display:block;margin-bottom:6px}
.twocol .box.yes b{color:var(--ok)}.twocol .box.no b{color:var(--crit)}.twocol .box.maybe b{color:var(--warn)}
.twocol ul{margin:0;padding-left:18px;font-size:14.5px}
.twocol li{margin:3px 0}
.check{list-style:none;padding:0;margin:14px 0 0}
.check li{display:flex;gap:10px;align-items:flex-start;padding:8px 0;border-bottom:1px solid var(--line)}
.check input{margin-top:5px;accent-color:var(--accent)}
.check input:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
details{border:1px solid var(--line);border-radius:6px;background:var(--surface);padding:10px 14px;margin-top:10px}
summary{font-weight:600;cursor:pointer;font-family:var(--display);font-size:17px}
details p{font-size:15px}
.src{font-size:13.5px;color:var(--muted);margin-top:40px;border-top:1px solid var(--line);padding-top:14px}
.src ul{padding-left:18px;margin:6px 0}
.src li{margin:3px 0}
.foot{font-size:12.5px;color:var(--muted);margin-top:28px}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
"""

JS = r"""
(function(){
  var DOC=window.__ARTIFACT__||{};
  function esc(t){return String(t).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');}
  function ts(v){var s=String(v);if(s.length===10)s+='T00:00';if(!/(Z|[+-]\d{2}:\d{2})$/.test(s))s+='Z';return Date.parse(s);}
  function pad(n){return (n<10?'0':'')+n;}
  function drawTimeline(sec, svgEl, tipEl){
    var unit=sec.unit||'day', T0=ts(sec.start), T1=ts(sec.end), rows=sec.rows||[];
    var L=unit==='hour'?132:112, R=16, T=30, B=8, RH=unit==='hour'?34:30, W=980;
    var H=T+rows.length*RH+B, pw=W-L-R;
    function x(t){return L+(t-T0)/(T1-T0)*pw;}
    var s='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 '+W+' '+H+'">';
    (sec.shade||[]).forEach(function(w){var a=x(ts(w[0])),b=x(ts(w[1]));s+='<rect class="wk" x="'+a+'" y="'+T+'" width="'+(b-a)+'" height="'+(rows.length*RH)+'"/>';});
    var step=unit==='hour'?3600e3:86400e3, firstLabel=true;
    for(var t=T0;t<=T1+1;t+=step){
      var xx=x(t); if(xx>W-R+0.5)break;
      s+='<line class="grid" x1="'+xx+'" y1="'+T+'" x2="'+xx+'" y2="'+(T+rows.length*RH)+'"/>';
      var d=new Date(t);
      if(unit==='hour'){s+='<text class="day mut" x="'+xx+'" y="'+(T-10)+'" text-anchor="middle">'+pad(d.getUTCHours())+':00</text>';}
      else if(t<T1){var mid=x(t+step/2);s+='<text class="day mut" x="'+mid+'" y="'+(T-10)+'" text-anchor="middle">'+d.getUTCDate()+'</text>';
        if(firstLabel||d.getUTCDate()===1){s+='<text class="tick mut" x="'+(xx+2)+'" y="'+(T-20)+'">'+['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][d.getUTCMonth()]+'</text>';firstLabel=false;}}
    }
    rows.forEach(function(r,i){
      var y=T+i*RH+RH/2;
      s+='<line class="grid" x1="'+L+'" y1="'+(T+i*RH)+'" x2="'+(W-R)+'" y2="'+(T+i*RH)+'"/>';
      s+='<text class="lab" x="'+(L-10)+'" y="'+(y+4)+'" text-anchor="end">'+esc(r.label)+'</text>';
      (r.events||[]).forEach(function(e){
        if(e.from){var x0=x(ts(e.from)),x1=Math.max(x(ts(e.to)),x0+4);
          s+='<rect class="span" x="'+x0+'" y="'+(y-7)+'" width="'+(x1-x0)+'" height="14" rx="2"><title>'+esc(e.text)+'</title></rect>';
          s+='<rect class="hit" tabindex="0" data-l="'+esc(e.text)+'" x="'+(x0-4)+'" y="'+(y-12)+'" width="'+(x1-x0+8)+'" height="24"/>';}
      });
      (r.events||[]).forEach(function(e){
        if(!e.from){var cx=x(ts(e.at));
          s+='<circle class="dot '+(e.tone||'')+'" cx="'+cx+'" cy="'+y+'" r="5.5"><title>'+esc(e.text)+'</title></circle>';
          s+='<circle class="hit" tabindex="0" data-l="'+esc(e.text)+'" cx="'+cx+'" cy="'+y+'" r="11"/>';}
      });
    });
    s+='<line class="grid" x1="'+L+'" y1="'+(T+rows.length*RH)+'" x2="'+(W-R)+'" y2="'+(T+rows.length*RH)+'"/></svg>';
    var wrap=svgEl.parentNode; wrap.innerHTML=s; var svg=wrap.querySelector('svg'); svg.setAttribute('role','img'); svg.setAttribute('aria-label',sec.heading||'timeline');
    function show(el){var l=el.getAttribute('data-l')||'';var m=l.match(/^([^:]{1,40}):\s*(.*)$/s);tipEl.innerHTML=m?'<b>'+esc(m[1])+'</b> '+esc(m[2]):esc(l);}
    ['mouseover','focusin','click'].forEach(function(ev){wrap.addEventListener(ev,function(e){if(e.target.classList&&e.target.classList.contains('hit'))show(e.target);});});
  }
  (DOC.sections||[]).forEach(function(sec){
    if(sec.type!=='timeline')return;
    var svg=document.getElementById('tl-'+sec.i), tip=document.getElementById('tip-'+sec.i);
    if(svg&&tip)drawTimeline(sec,svg,tip);
  });
  try{
    var key='ya-checks-'+(DOC.id||DOC.title||'');
    var saved=JSON.parse(localStorage.getItem(key)||'{}');
    document.querySelectorAll('.check input').forEach(function(cb){
      if(saved[cb.id])cb.checked=true;
      cb.addEventListener('change',function(){saved[cb.id]=cb.checked;try{localStorage.setItem(key,JSON.stringify(saved));}catch(e){}});
    });
  }catch(e){}
})();
"""

FONTS = ('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo+Narrow:wght@500;600;700'
         '&family=Public+Sans:ital,wght@0,400;0,500;0,600;1,400&family=IBM+Plex+Mono:wght@400;500&display=swap">')


def _render_section(i: int, s: dict) -> str:
    t = s["type"]
    head = f"<h2>{inline(s['heading'])}</h2>" if s.get("heading") else ""
    intro = paras(s.get("intro") if isinstance(s.get("intro"), list) else [s["intro"]]) if s.get("intro") else ""
    items = s.get("items") or []
    body = ""
    if t == "facts":
        def fact_body(it):
            if it.get("bullets"):
                return "<ul>" + "".join(f"<li>{inline(b)}</li>" for b in it["bullets"]) + "</ul>"
            return f'<span>{inline(it["text"])}</span>'
        body = '<div class="facts">' + "".join(
            f'<div class="fact"><b>{inline(it["label"])}</b>'
            + (f'<span class="big">{inline(it["big"])}</span>' if it.get("big") else "")
            + fact_body(it) + "</div>" for it in items) + "</div>"
    elif t == "list":
        plain = not any(it.get("when") for it in items)
        body = f'<ul class="pre{" plain" if plain else ""}">' + "".join(
            "<li>" + (f'<b>{inline(it["when"])}</b>' if it.get("when") else "")
            + f'<span>{inline(it["text"])}</span></li>' for it in items) + "</ul>"
    elif t == "timeline":
        legend = s.get("legend") or ["Live / exploited window", "Disclosure or report",
                                     "Attack or exposure", "Fix, block or revocation", "Deadline or correction"]
        lg = "".join(f'<span><i class="{c}"></i>{inline(x)}</span>' for c, x in
                     zip(["span", "", "crit", "ok", "warn"], legend))
        body = (f'<div class="chart" id="c-{i}"><div class="cap"><span>{inline(s.get("caption") or "")}</span>'
                f'<span>{inline(s.get("caption_right") or "")}</span></div>'
                f'<div class="scroll"><svg id="tl-{i}"></svg></div>'
                f'<div class="legend">{lg}</div>'
                f'<div class="tip" id="tip-{i}"><span class="mut">Hover a mark to read it here.</span></div>'
                + (f'<p class="note">{inline(s["note"])}</p>' if s.get("note") else "") + "</div>")
    elif t == "cards":
        out = []
        for n, it in enumerate(items, 1):
            chips = "".join(chip(c["text"], c.get("tone") or "") for c in it.get("chips") or [])
            srcs = " · ".join(f'<a href="{esc(l["url"])}" target="_blank" rel="noopener">{inline(l["text"])}</a>'
                              for l in it.get("sources") or [])
            out.append(
                f'<div class="inc"><div class="top"><span class="n">{n}</span><div class="hd">'
                f'<h3>{inline(it["title"])}</h3>'
                + (f'<div class="when">{inline(it["when"])}</div>' if it.get("when") else "")
                + (f'<div class="chips">{chips}</div>' if chips else "") + "</div></div>"
                + paras(it.get("paragraphs") or [])
                + f'<div class="say">{inline(it["say"])}</div>'
                + f'<div class="chase">{inline(it.get("sources_label") or "Sources")}: {srcs}'
                + (f' {inline(it["note"])}' if it.get("note") else "") + "</div></div>")
        body = "".join(out)
    elif t == "table":
        def cell(c):
            if isinstance(c, dict):
                return chip(c["text"], c.get("tone") or "") if c.get("chip") else inline(c.get("text"))
            return inline(c)
        body = ('<div class="tablewrap"><table><thead><tr>' + "".join(f"<th>{inline(c)}</th>" for c in s["columns"])
                + "</tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{cell(c)}</td>" for c in r) + "</tr>"
                                                   for r in s["rows"]) + "</tbody></table></div>")
    elif t == "matrix":
        cols = s["columns"]
        rows = s["rows"]
        totals = [sum(1 for r in rows if r["marks"][k]) for k in range(len(cols))]
        body = ('<div class="tablewrap"><table class="matrix"><thead><tr><th>' + inline(s.get("row_label") or "")
                + "</th>" + "".join(f"<th>{inline(c)}</th>" for c in cols) + "</tr></thead><tbody>"
                + "".join("<tr><td>" + inline(r["label"]) + "</td>"
                          + "".join('<td><span class="y">✓</span></td>' if m else "<td></td>" for m in r["marks"])
                          + "</tr>" for r in rows)
                + "</tbody>" + (f'<tfoot><tr><td>Count of {len(rows)}</td>'
                                + "".join(f'<td class="num">{n}</td>' for n in totals) + "</tr></tfoot>"
                                if s.get("totals", True) else "") + "</table></div>"
                + (f'<p style="font-size:14px;color:var(--muted)">{inline(s["note"])}</p>' if s.get("note") else ""))
    elif t == "steps":
        def step(it):
            gate = ' class="gate"' if it.get("gate") else ""
            proof_label = inline(it.get("proof_label") or "Proof")
            proof = (f'<div class="ev"><b>{proof_label}</b>{inline(it["proof"])}</div>'
                     if it.get("proof") else "")
            return (f'<li{gate}><b>{inline(it["title"])}</b>'
                    f'<figure class="sg"><img src="{esc(it["image"])}" alt="{esc(it["image_alt"])}" loading="lazy"></figure>'
                    f'<p>{inline(it["text"])}</p>{proof}</li>')
        body = '<ol class="steps">' + "".join(step(it) for it in items) + "</ol>"
    elif t == "callout":
        body = f'<div class="callout {esc(s.get("tone") or "")}">' + paras(s["paragraphs"]) + "</div>"
    elif t == "boxes":
        body = '<div class="twocol">' + "".join(
            f'<div class="box {esc(it["tone"])}"><b>{inline(it["title"])}</b><ul>'
            + "".join(f"<li>{inline(b)}</li>" for b in it["bullets"]) + "</ul></div>" for it in items) + "</div>"
    elif t == "checklist":
        body = f'<ul class="check" id="checks-{i}">' + "".join(
            f'<li><input type="checkbox" id="k{i}-{n}"><label for="k{i}-{n}">{inline(x)}</label></li>'
            for n, x in enumerate(items, 1)) + "</ul>"
    elif t == "faq":
        body = "".join(f"<details><summary>{inline(it['q'])}</summary><p>{inline(it['a'])}</p></details>" for it in items)
    elif t == "sources":
        body = ('<div class="src"><b>' + inline(s.get("heading")) + "</b><ul>" + "".join(
            "<li>" + inline(it["text"]) + ": " + "; ".join(
                f'<a href="{esc(l["url"])}" target="_blank" rel="noopener">{inline(l["text"])}</a>' for l in it["links"])
            + "</li>" for it in items) + "</ul></div>")
        head = ""  # the sources block carries its own heading
    elif t == "prose":
        body = paras(s["paragraphs"])
    return head + intro + body


def render(doc: dict) -> str:
    chips = "".join(chip(c["text"], c.get("tone") or "") for c in doc.get("chips") or [])
    sections = "".join(_render_section(i, s) for i, s in enumerate(doc.get("sections") or []))
    generated = doc.get("generated_at") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data = json.dumps({"id": doc.get("id") or doc.get("title"), "title": doc.get("title"),
                       "sections": [dict(s, i=i) for i, s in enumerate(doc.get("sections") or [])
                                    if s.get("type") == "timeline"]},
                      ensure_ascii=False).replace("</", "<\\/")
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">\n"
        f"<title>{esc(doc['title'])}</title>\n"
        + (f'<meta name="description" content="{esc(doc.get("description") or doc.get("lede"))}">\n')
        + FONTS + "\n<style>" + CSS + (doc.get("css_extra") or "") + "</style>\n</head>\n<body>\n"
        '<div class="wrap" id="page">\n<header>'
        f'<div class="eyebrow">{inline(doc["eyebrow"])}</div>'
        f'<h1 style="margin-top:8px">{inline(doc["title"])}</h1>'
        f'<p class="lede">{inline(doc["lede"])}</p>'
        f'<div class="status">{chips}</div></header>\n'
        + sections
        + (f'<p class="foot">{inline(doc["credit"])} · {esc(generated)}</p>\n' if doc.get("credit") else "")
        + "</div>\n"
        + f"<script>window.__ARTIFACT__={data};</script>\n<script>" + JS + "</script>\n</body>\n</html>\n"
    )


_IMG_SRC = re.compile(r'(<img\b[^>]*\bsrc=")(img/[^"]+)(")')
_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def inline_images(html_text: str, artifact_dir: Path) -> str:
    """Rewrite every img/<file> reference to a data: URI so the page renders
    from a blob URL (the dashboard preview) with no relative fetches."""
    import base64

    def swap(m: re.Match) -> str:
        rel = html.unescape(m.group(2))
        f = (Path(artifact_dir) / rel)
        try:
            if ".." in rel or not f.is_file():
                return m.group(0)
            mime = _MIME.get(f.suffix.lower(), "application/octet-stream")
            data = base64.b64encode(f.read_bytes()).decode()
        except OSError:
            return m.group(0)
        return f"{m.group(1)}data:{mime};base64,{data}{m.group(3)}"
    return _IMG_SRC.sub(swap, html_text)


# ---------------------------------------------------------------------------
# CLI / library entry points
# ---------------------------------------------------------------------------

def build(artifact_dir: Path, check_only: bool = False) -> dict[str, Any]:
    """Validate (and unless check_only, render) one artifact folder."""
    artifact_dir = Path(artifact_dir)
    src = artifact_dir / "artifact.json"
    if not src.is_file():
        return {"ok": False, "problems": [f"artifact.json not found in {artifact_dir}"], "fatal": True}
    try:
        doc = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"ok": False, "problems": [f"artifact.json is not valid JSON: {exc}"], "fatal": True}
    problems = validate(doc, artifact_dir)
    out = artifact_dir / "index.html"
    if problems:
        return {"ok": False, "problems": problems, "page": str(out) if out.exists() else None}
    if not check_only:
        html_text = render(doc)
        if out.exists():
            hist = artifact_dir / "history"
            hist.mkdir(exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            try:
                (hist / f"{stamp}.html").write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
            except OSError:
                pass
        out.write_text(html_text, encoding="utf-8")
    return {"ok": True, "problems": [], "page": str(out),
            "sections": [s.get("type") for s in doc.get("sections") or []],
            "images": sum(1 for s in doc.get("sections") or [] if s.get("type") == "steps"
                          for _ in s.get("items") or [])}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("artifact_dir", type=Path)
    ap.add_argument("--check", action="store_true", help="validate only")
    ap.add_argument("--json", action="store_true", help="print a JSON report")
    a = ap.parse_args(argv)
    rep = build(a.artifact_dir, check_only=a.check)
    if a.json:
        print(json.dumps(rep, indent=2))
    elif rep["ok"]:
        print(f"OK: {rep['page']} ({len(rep['sections'])} sections, {rep['images']} step images)")
    else:
        print(f"FAIL: {len(rep['problems'])} problem(s)")
        for x in rep["problems"]:
            print(f" - {x}")
    return 0 if rep["ok"] else (2 if rep.get("fatal") else 1)


if __name__ == "__main__":
    sys.exit(main())
