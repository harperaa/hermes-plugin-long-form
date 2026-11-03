#!/usr/bin/env python3
"""simple-artifact: the quality gate and renderer for a brief page.

A brief page is the outline the operator speaks from: six fixed sections
(promise, problem, value, apply, summary, cta), bullets instead of prose, a
sketchnote image on every beat, and no instructions to the presenter. The
content lives in artifact.json with `"kind": "simple"` (contract in
schema.md); this script validates it and writes index.html. The look is fixed
here so every brief page matches.

Usage:
  python3 build_simple.py <artifact-dir>            validate + render
  python3 build_simple.py <artifact-dir> --check    validate only
  python3 build_simple.py <artifact-dir> --json     machine-readable report
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

# the packaging block (thumbnails, titles, hooks) is shared with the full
# page's builder and lives at the plugin root. It is loaded from disk on every
# builder load (never from sys.modules): the dashboard re-imports this builder
# per call, and a long-running process must gate with the contract on disk,
# not the one it started with.
_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent


def _load_packaging():
    import importlib.util
    src = _PLUGIN_ROOT / "yti_packaging.py"
    spec = importlib.util.spec_from_file_location(f"yti_packaging_{id(src)}", str(src))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


yti_packaging = _load_packaging()

SECTION_ORDER = ["promise", "problem", "value", "apply", "summary", "cta"]
SECTION_LABELS = {"promise": "Promise", "problem": "Problem", "value": "Value",
                  "apply": "Apply today", "summary": "Summary", "cta": "Call to action"}
TONES = {"", "ok", "crit", "warn"}
MIN_IMAGE_BYTES = 20_000
MAX_BULLET_WORDS = 32
MAX_BULLETS = 8
IMAGE_TARGETS = (10, 20)      # one sketch per beat: 10 drives a ~10 minute talk, 20 a ~20 minute one
IMAGE_UNDER, IMAGE_OVER = 1, 2   # "roughly": one beat under or two over the target is fine

# The page is the outline, not a script for whoever presents it.
PRESENTER_RE = re.compile(
    r"\b(before you record|on camera|on screen|talk track|this slide|the slide|"
    r"say (this|that|it) (out loud|on)|do not say|don't say|say \"|presenter'?s? (rule|note)|"
    r"recording checklist|when you present|tell (the|your) (viewers|audience|mentees)|"
    r"mention (this|that|the link)|point (up|down|to the card)|callback to the tease)\b",
    re.IGNORECASE)
URL_RE = re.compile(r"^https?://[^\s]+$")
_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
_IMG_SRC = re.compile(r'(<img[^>]*\ssrc=")(img/[^"]+)(")')


# ---------------------------------------------------------------------------
# Inline markup: escape first, then `code`, **bold**, [text](https://url).
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


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _walk_text(node: Any, path: str, out: list[tuple[str, str]]) -> None:
    if isinstance(node, str):
        out.append((path, node))
    elif isinstance(node, dict):
        for k, v in node.items():
            if k in ("url", "image", "id", "thumbnail", "examples"):
                continue
            _walk_text(v, f"{path}.{k}", out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk_text(v, f"{path}[{i}]", out)


def _bullets(v: Any, where: str, p: list[str], *, lo: int = 1, hi: int = MAX_BULLETS) -> None:
    if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
        p.append(f"{where}: bullets must be a list of non-empty strings")
        return
    if not (lo <= len(v) <= hi):
        p.append(f"{where}: {len(v)} bullets; expected {lo} to {hi}")
    for i, b in enumerate(v):
        if len(b.split()) > MAX_BULLET_WORDS:
            p.append(f"{where}[{i}]: {len(b.split())} words; a bullet is a cue, keep it under {MAX_BULLET_WORDS}")


def _question(v: Any, where: str, p: list[str]) -> None:
    """An open loop: a question read to the viewer here and answered later on
    the page. Optional on the promise and on any beat."""
    if v is None:
        return
    if not isinstance(v, str) or not v.strip():
        p.append(f"{where}.question: must be a non-empty string")
        return
    if not v.strip().endswith("?"):
        p.append(f"{where}.question: ends with a question mark; it is read to the viewer as a question")
    if len(v.split()) > 32:
        p.append(f"{where}.question: {len(v.split())} words; keep the loop under 32")


def _key(v: Any, where: str, p: list[str]) -> None:
    if v is None:
        return
    if not isinstance(v, dict) or not isinstance(v.get("text"), str) or not v["text"].strip():
        p.append(f"{where}.key: needs {{text, tone?}}")
        return
    if v.get("tone", "") not in TONES:
        p.append(f"{where}.key.tone: must be one of ok, crit, warn or empty")


def _image(sec: dict, where: str, base: Path, p: list[str], images: list[str]) -> None:
    rel = sec.get("image")
    if not isinstance(rel, str) or not rel.startswith("img/") or ".." in rel:
        p.append(f"{where}.image: must be a path like img/s01.jpg")
        return
    if not isinstance(sec.get("image_alt"), str) or not sec["image_alt"].strip():
        p.append(f"{where}.image_alt: required (one sentence describing the sketch)")
    f = base / rel
    if not f.is_file():
        p.append(f"{where}.image: {rel} not found")
    elif f.stat().st_size < MIN_IMAGE_BYTES:
        p.append(f"{where}.image: {rel} is {f.stat().st_size} bytes; a generated image is at least {MIN_IMAGE_BYTES}")
    images.append(rel)


def _beat(b: Any, where: str, base: Path, p: list[str], images: list[str]) -> None:
    if not isinstance(b, dict):
        p.append(f"{where}: must be an object")
        return
    if not isinstance(b.get("heading"), str) or not b["heading"].strip():
        p.append(f"{where}.heading: required")
    _image(b, where, base, p, images)
    has_bullets = "bullets" in b
    has_contrast = "contrast" in b
    if not has_bullets and not has_contrast:
        p.append(f"{where}: needs bullets, a contrast, or both")
    if has_bullets:
        _bullets(b["bullets"], f"{where}.bullets", p)
    if has_contrast:
        c = b["contrast"]
        if not isinstance(c, dict) or "crowd" not in c or "me" not in c:
            p.append(f"{where}.contrast: needs {{crowd: [..], me: [..]}}")
        else:
            _bullets(c["crowd"], f"{where}.contrast.crowd", p, hi=4)
            _bullets(c["me"], f"{where}.contrast.me", p, hi=4)
    if "story" in b:
        _bullets(b["story"], f"{where}.story", p, lo=2, hi=14)
    if "label" in b and not isinstance(b["label"], str):
        p.append(f"{where}.label: must be a string")
    _question(b.get("question"), where, p)
    _key(b.get("key"), where, p)


def validate(doc: Any, base_dir: Path) -> list[str]:
    p: list[str] = []
    if not isinstance(doc, dict):
        return ["artifact.json must be a JSON object"]
    if doc.get("kind") != "simple":
        p.append('kind: must be "simple" (the marker that routes this folder to the simple-artifact renderer)')
    for k in ("title", "eyebrow", "lede"):
        if not isinstance(doc.get(k), str) or not doc[k].strip():
            p.append(f"{k}: required")
    if isinstance(doc.get("title"), str) and not (1 <= len(doc["title"].split()) <= 8):
        p.append("title: 1 to 8 words")
    if isinstance(doc.get("lede"), str) and not (12 <= len(doc["lede"].split()) <= 70):
        p.append("lede: 12 to 70 words")
    promise_line = None
    for s in (doc.get("sections") or []):
        if isinstance(s, dict) and s.get("type") == "promise" and isinstance(s.get("key"), dict):
            promise_line = s["key"].get("text")
    if "packaging" not in doc:
        p.append("packaging: required; the collapsed block at the top with thumbnails, titles and hooks (see schema.md)")
    else:
        p += yti_packaging.validate(doc["packaging"], base_dir, promise_line)
    src = doc.get("source")
    if src is not None:
        if not isinstance(src, dict):
            p.append("source: must be an object {url?, title?}")
        elif src.get("url") and not URL_RE.match(str(src["url"])):
            p.append("source.url: must be an http(s) URL")
    target = doc.get("target_images")
    if target is not None and target not in IMAGE_TARGETS:
        p.append(f"target_images: must be one of {list(IMAGE_TARGETS)}")
        target = None
    secs = doc.get("sections")
    if not isinstance(secs, list):
        return p + ["sections: required list"]
    types = [s.get("type") if isinstance(s, dict) else None for s in secs]
    if types != SECTION_ORDER:
        p.append(f"sections: must be exactly {SECTION_ORDER} in that order; found {types}")
        return p
    images: list[str] = []
    for s in secs:
        t = s["type"]
        w = f"sections.{t}"
        if not isinstance(s.get("heading"), str) or not s["heading"].strip():
            p.append(f"{w}.heading: required")
        if t == "promise":
            _image(s, w, base_dir, p, images)
            _bullets(s.get("bullets"), f"{w}.bullets", p, lo=2, hi=6)
            _question(s.get("question"), w, p)
            _key(s.get("key"), w, p)
        elif t in ("problem", "value"):
            beats = s.get("beats")
            lo = 2 if t == "problem" else 3
            if not isinstance(beats, list) or len(beats) < lo:
                p.append(f"{w}.beats: at least {lo} beats")
            else:
                for i, b in enumerate(beats):
                    _beat(b, f"{w}.beats[{i}]", base_dir, p, images)
            if t == "problem" and isinstance(beats, list) and not any(
                    isinstance(b, dict) and "contrast" in b for b in beats):
                p.append(f"{w}: at least one beat carries a contrast (what the crowd says vs what I say)")
        elif t == "apply":
            _image(s, w, base_dir, p, images)
            steps = s.get("steps")
            if not isinstance(steps, list) or not (2 <= len(steps) <= 5):
                p.append(f"{w}.steps: 2 to 5 steps, each {{title, text}}")
            else:
                for i, st in enumerate(steps):
                    if not isinstance(st, dict) or not st.get("title") or not st.get("text"):
                        p.append(f"{w}.steps[{i}]: needs title and text")
                    elif len(str(st["text"]).split()) > 30:
                        p.append(f"{w}.steps[{i}].text: keep it under 30 words; the win must feel fast")
            _key(s.get("key"), w, p)
        elif t == "summary":
            _bullets(s.get("bullets"), f"{w}.bullets", p, lo=3, hi=7)
        elif t == "cta":
            _image(s, w, base_dir, p, images)
            _bullets(s.get("bullets"), f"{w}.bullets", p, lo=2, hi=5)
            joined = " ".join(s.get("bullets") or []).lower() if isinstance(s.get("bullets"), list) else ""
            if "subscribe" not in joined:
                p.append(f"{w}.bullets: the close always asks for more content, like and subscribe")
    for t in (doc.get("packaging") or {}).get("thumbnails") or [] if isinstance(doc.get("packaging"), dict) else []:
        if isinstance(t, dict) and t.get("image") in images:
            p.append(f"packaging.thumbnails: {t['image']} is also a beat image; thumbnails are their own files")
    if len(images) != len(set(images)):
        p.append("images: the same file is used twice; every beat has its own sketch")
    if target is not None and not (target - IMAGE_UNDER <= len(images) <= target + IMAGE_OVER):
        verb = "consolidate" if len(images) > target else "split"
        p.append(f"images: {len(images)} illustrated beats on the page; target_images is {target}, so "
                 f"{verb} the problem and value beats until the page has {target - IMAGE_UNDER} to "
                 f"{target + IMAGE_OVER} (promise, apply and cta included)")
    texts: list[tuple[str, str]] = []
    _walk_text(doc, "brief", texts)
    for path, t in texts:
        if "—" in t:
            p.append(f"{path}: contains an em dash; rewrite as two sentences or a comma")
        if PRESENTER_RE.search(t):
            p.append(f"{path}: reads as an instruction to the presenter (\"{PRESENTER_RE.search(t).group(0)}\"); "
                     "the page carries content only")
        if re.search(r"\b(TODO|TBD|lorem ipsum)\b", t, re.IGNORECASE):
            p.append(f"{path}: placeholder text")
    return p


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

CSS = r"""
:root{
  --bg:#f6f5f1; --surface:#ffffff; --ink:#1d2026; --muted:#5c6270; --line:#d9d7cf;
  --accent:#0f6e73; --accent-ink:#ffffff;
  --crit:#b3261e; --crit-bg:#fbeae8; --warn:#8a5a00; --warn-bg:#fdf2dc; --ok:#1f6b3a; --ok-bg:#e4f3e8;
  --code-bg:#edf0ee;
  --display:"Archivo Narrow","Arial Narrow",system-ui,sans-serif;
  --body:"Public Sans",system-ui,-apple-system,"Segoe UI",sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
}
@media (prefers-color-scheme: dark){ :root:not([data-theme="light"]){
  --bg:#15171b; --surface:#1e2127; --ink:#ebebe6; --muted:#a3a8b3; --line:#343941;
  --accent:#5fc3c7; --accent-ink:#0d1517;
  --crit:#ff8a80; --crit-bg:#3a1f1d; --warn:#f2c46d; --warn-bg:#3a2e14; --ok:#8fd9a8; --ok-bg:#1c3324;
  --code-bg:#272b32; color-scheme:dark } }
:root[data-theme="dark"]{
  --bg:#15171b; --surface:#1e2127; --ink:#ebebe6; --muted:#a3a8b3; --line:#343941;
  --accent:#5fc3c7; --accent-ink:#0d1517;
  --crit:#ff8a80; --crit-bg:#3a1f1d; --warn:#f2c46d; --warn-bg:#3a2e14; --ok:#8fd9a8; --ok-bg:#1c3324;
  --code-bg:#272b32; color-scheme:dark }
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--body);font-size:21.2px;line-height:1.5;
  padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));padding-block:0 80px}
.wrap{max-width:1125px;margin:0 auto}
h1,h2,h3{font-family:var(--display);line-height:1.1;text-wrap:balance;margin:0}
h1{font-size:clamp(50px,10vw,80px);font-weight:700;letter-spacing:-.01em}
h2{font-size:45px;font-weight:700;margin-top:4px}
h3{font-size:31.2px;font-weight:600;margin-top:2px}
p{margin:10px 0;max-width:68ch}
a{color:var(--accent)}
code{font-family:var(--mono);font-size:.9em;background:var(--code-bg);padding:1px 5px;border-radius:3px}
.eyebrow{font-family:var(--display);text-transform:uppercase;letter-spacing:.08em;font-size:16.2px;color:var(--muted);font-weight:600}
header{padding-top:44px}
.lede{font-size:23.8px;color:var(--muted);max-width:62ch}
.status{display:flex;flex-wrap:wrap;gap:8px;margin-top:16px}
.chip{display:inline-flex;align-items:center;gap:6px;font-size:16.2px;font-weight:600;padding:4px 10px;border-radius:999px;border:1px solid var(--line);background:var(--surface);white-space:nowrap}
.chip.crit{color:var(--crit);background:var(--crit-bg);border-color:transparent}
.chip.warn{color:var(--warn);background:var(--warn-bg);border-color:transparent}
.chip.ok{color:var(--ok);background:var(--ok-bg);border-color:transparent}
.flow{display:grid;grid-template-columns:repeat(auto-fit,minmax(163px,1fr));gap:8px;margin:28px 0 8px}
.flow a{display:block;background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:10px 12px;text-decoration:none;color:var(--ink);font-size:17.5px;line-height:1.3}
.flow a b{display:block;font-family:var(--mono);font-size:15px;color:var(--muted);margin-bottom:2px}
.part{margin-top:60px;padding-top:18px;border-top:2px solid var(--ink)}
.part .eyebrow{color:var(--accent)}
.beat{margin-top:36px;padding-top:16px;border-top:1px solid var(--line)}
.beat .n{font-family:var(--mono);font-size:16.2px;color:var(--muted)}
figure{margin:14px 0 0}
figure img{display:block;width:100%;height:auto;border-radius:8px;border:1px solid var(--line);background:#fff}
ul.cues{margin:14px 0 0;padding-left:22px}
ul.cues li{margin:6px 0}
ul.cues li::marker{color:var(--accent)}
.key{border-left:4px solid var(--accent);background:var(--surface);padding:10px 16px;border-radius:0 6px 6px 0;margin:16px 0 0;font-family:var(--display);font-size:25px;font-weight:600}
.key.crit{border-color:var(--crit);background:var(--crit-bg)}
.key.ok{border-color:var(--ok);background:var(--ok-bg)}
.key.warn{border-color:var(--warn);background:var(--warn-bg)}
.loop{display:flex;gap:12px;align-items:flex-start;margin:16px 0 0;padding:12px 16px;border:1px dashed var(--accent);border-radius:8px;background:var(--surface)}
.loop .q{flex:none;width:30px;height:30px;border-radius:50%;background:var(--accent);color:var(--accent-ink);font-family:var(--display);font-weight:700;font-size:23.8px;display:flex;align-items:center;justify-content:center}
.loop p{margin:2px 0 0;font-family:var(--display);font-size:25px;font-weight:600;line-height:1.25}
.vs{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:14px}
@media (max-width:600px){.vs{grid-template-columns:1fr}}
.vs div{background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:12px 14px}
.vs b{display:block;font-family:var(--display);font-size:17.5px;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px}
.vs .crowd b{color:var(--muted)}
.vs .me{border-color:var(--accent);background:var(--ok-bg)} .vs .me b{color:var(--ok)}
.vs ul{margin:0;padding-left:18px;font-size:19.4px} .vs li{margin:4px 0}
details{margin:10px 0 0;border:1px dashed var(--line);border-radius:6px;padding:6px 12px;background:var(--surface)}
summary{cursor:pointer;font-family:var(--display);font-weight:600;font-size:18.8px;color:var(--muted)}
details ul{margin:6px 0 4px;padding-left:20px;font-size:18.8px}
details li{margin:4px 0}
.steps{counter-reset:s;list-style:none;margin:14px 0 0;padding:0}
.steps li{counter-increment:s;position:relative;padding:10px 12px 10px 52px;background:var(--surface);border:1px solid var(--line);border-radius:6px;margin:8px 0}
.steps li::before{content:counter(s);position:absolute;left:12px;top:9px;width:28px;height:28px;border-radius:50%;background:var(--accent);color:var(--accent-ink);font-family:var(--display);font-weight:700;display:flex;align-items:center;justify-content:center}
.steps li b{font-family:var(--display);font-size:21.2px;display:block}
.steps li span{color:var(--muted);font-size:18.8px}
.cta{margin-top:40px;background:var(--accent);color:var(--accent-ink);border-radius:10px;padding:22px 24px}
.cta h2{color:var(--accent-ink);font-size:37.5px;margin:0}
.cta ul{margin:10px 0 0;padding-left:22px;font-size:21.2px}
.cta li{margin:6px 0}
.cta a{color:var(--accent-ink)}
footer{margin-top:48px;padding-top:14px;border-top:1px solid var(--line);font-size:16.2px;color:var(--muted)}
.theme{position:fixed;top:10px;right:12px;display:flex;gap:2px;background:var(--surface);border:1px solid var(--line);border-radius:999px;padding:2px;font-size:15px;font-family:var(--display);z-index:5}
.theme button{border:0;background:transparent;color:var(--muted);padding:3px 9px;border-radius:999px;cursor:pointer;font:inherit}
.theme button[aria-pressed="true"]{background:var(--accent);color:var(--accent-ink)}
@media print{ body{font-size:16.2px} .beat{break-inside:avoid} figure img{max-height:280px;width:auto} details{display:block} .theme{display:none} }
""" + yti_packaging.CSS

THEME_JS = ("(function(){var k='brief-theme',r=document.documentElement,bs=document.querySelectorAll('.theme button');"
            "function ap(v){if(v==='auto'){r.removeAttribute('data-theme');}else{r.setAttribute('data-theme',v);}"
            "bs.forEach(function(b){b.setAttribute('aria-pressed',b.dataset.t===v?'true':'false');});}"
            "var v='auto';try{v=localStorage.getItem(k)||'auto';}catch(e){}ap(v);"
            "bs.forEach(function(b){b.addEventListener('click',function(){try{localStorage.setItem(k,b.dataset.t);}catch(e){}ap(b.dataset.t);});});})();")

FONTS = ('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo+Narrow:wght@500;600;700'
         '&family=Public+Sans:ital,wght@0,400;0,500;0,600;1,400&family=IBM+Plex+Mono:wght@400;500&display=swap">')


def _ul(items: list[str], cls: str = "cues") -> str:
    return f'<ul class="{cls}">' + "".join(f"<li>{inline(x)}</li>" for x in items) + "</ul>"


def _figure(sec: dict) -> str:
    return f'<figure><img src="{esc(sec["image"])}" alt="{esc(sec.get("image_alt", ""))}" loading="lazy"></figure>'


def _keyline(k: Any) -> str:
    if not k:
        return ""
    tone = k.get("tone") or ""
    return f'<div class="key{(" " + tone) if tone else ""}">{inline(k["text"])}</div>'


def _loop(q: Any) -> str:
    if not q:
        return ""
    return f'<div class="loop"><span class="q">?</span><p>{inline(q)}</p></div>'


def _render_beat(num: str, b: dict) -> str:
    label = b.get("label")
    n = f"{num}" + (f" · {esc(label)}" if label else "")
    out = [f'<section class="beat"><div class="n">{n}</div><h3>{inline(b["heading"])}</h3>', _figure(b)]
    c = b.get("contrast")
    if c:
        out.append('<div class="vs"><div class="crowd"><b>What the crowd says</b>' + _ul(c["crowd"], "") +
                   '</div><div class="me"><b>What I say</b>' + _ul(c["me"], "") + "</div></div>")
    if b.get("bullets"):
        out.append(_ul(b["bullets"]))
    if b.get("story"):
        out.append("<details><summary>Full story</summary>" + _ul(b["story"], "") + "</details>")
    out.append(_keyline(b.get("key")))
    out.append(_loop(b.get("question")))
    out.append("</section>")
    return "".join(out)


def render(doc: dict) -> str:
    secs = {s["type"]: s for s in doc["sections"]}
    tones = {"promise": "ok", "problem": "crit", "value": "", "apply": "warn", "summary": "", "cta": ""}
    chips = "".join(f'<span class="chip{(" " + tones[t]) if tones[t] else ""}">{SECTION_LABELS[t]}</span>'
                    for t in SECTION_ORDER)
    flow = "".join(f'<a href="#{t}"><b>{i + 1}</b>{SECTION_LABELS[t]}</a>' for i, t in enumerate(SECTION_ORDER))
    parts: list[str] = []
    # 1 promise
    s = secs["promise"]
    # the promise's key line is the hook: it sits right under the image, before the bullets
    parts.append(f'<section class="part" id="promise"><div class="eyebrow">1 · Promise</div><h2>{inline(s["heading"])}</h2>'
                 + _figure(s) + _keyline(s.get("key")) + _ul(s["bullets"]) + _loop(s.get("question")) + "</section>")
    # 2 problem, 3 value
    for idx, t in ((2, "problem"), (3, "value")):
        s = secs[t]
        parts.append(f'<section class="part" id="{t}"><div class="eyebrow">{idx} · {SECTION_LABELS[t]}</div>'
                     f'<h2>{inline(s["heading"])}</h2></section>')
        for j, b in enumerate(s["beats"]):
            parts.append(_render_beat(f"{idx}.{j + 1}", b))
    # 4 apply
    s = secs["apply"]
    steps = '<ol class="steps">' + "".join(
        f'<li><b>{inline(st["title"])}</b><span>{inline(st["text"])}</span></li>' for st in s["steps"]) + "</ol>"
    parts.append(f'<section class="part" id="apply"><div class="eyebrow">4 · Apply today</div><h2>{inline(s["heading"])}</h2>'
                 + _figure(s) + steps + _keyline(s.get("key")) + "</section>")
    # 5 summary
    s = secs["summary"]
    parts.append(f'<section class="part" id="summary"><div class="eyebrow">5 · Summary</div><h2>{inline(s["heading"])}</h2>'
                 + _ul(s["bullets"]) + "</section>")
    # 6 cta
    s = secs["cta"]
    parts.append(f'<section class="part" id="cta"><div class="eyebrow">6 · Call to action</div><h2>{inline(s["heading"])}</h2>'
                 + _figure(s) + '<div class="cta"><h2>Keep going</h2>' + _ul(s["bullets"], "") + "</div></section>")
    src = doc.get("source") or {}
    foot = f"Video brief · {esc(doc['title'])}"
    if src.get("url"):
        foot += f' · Source: <a href="{esc(src["url"])}" target="_blank" rel="noopener">{esc(src.get("title") or src["url"])}</a>'
    elif src.get("title"):
        foot += f" · Source: {esc(src['title'])}"
    desc = doc.get("description") or doc["lede"]
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">\n"
        f"<title>{esc(doc['title'])}</title>\n<meta name=\"description\" content=\"{esc(desc)}\">\n"
        f"{FONTS}\n<style>{CSS}</style>\n</head>\n<body>\n"
        '<div class="theme" role="group" aria-label="Theme"><button data-t="auto">Auto</button>'
        '<button data-t="light">Light</button><button data-t="dark">Dark</button></div>\n'
        '<div class="wrap">\n<header>'
        f'<div class="eyebrow">{inline(doc["eyebrow"])}</div><h1>{inline(doc["title"])}</h1>'
        f'<p class="lede">{inline(doc["lede"])}</p><div class="status">{chips}</div><div class="flow">{flow}</div>'
        "</header>\n" + "\n".join(parts) +
        f"\n<footer>{foot} · {yti_packaging.LINK}</footer>\n" + yti_packaging.render(doc["packaging"], inline) +
        f"\n</div>\n<script>{THEME_JS}</script>\n<script>{yti_packaging.LINK_JS}</script>\n</body>\n</html>\n"
    )


def inline_images(html_text: str, artifact_dir: Path) -> str:
    """Rewrite every img/<file> reference to a data: URI (dashboard preview)."""
    import base64

    def swap(m: re.Match) -> str:
        rel = html.unescape(m.group(2))
        f = Path(artifact_dir) / rel
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

def count_images(doc: dict) -> int:
    n = 0
    for s in doc.get("sections") or []:
        if s.get("image"):
            n += 1
        for b in s.get("beats") or []:
            if b.get("image"):
                n += 1
    return n


def build(artifact_dir: Path, check_only: bool = False) -> dict[str, Any]:
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
    return {"ok": True, "problems": [], "page": str(out), "sections": SECTION_ORDER, "images": count_images(doc),
            "thumbnails": yti_packaging.count_images(doc.get("packaging"))}


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
        print(f"OK: {rep['page']} ({len(rep['sections'])} sections, {rep['images']} images)")
    else:
        print(f"FAIL: {len(rep['problems'])} problem(s)")
        for x in rep["problems"]:
            print(f" - {x}")
    return 0 if rep["ok"] else (2 if rep.get("fatal") else 1)


if __name__ == "__main__":
    sys.exit(main())
