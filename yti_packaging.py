"""Packaging for a page: the thumbnails, titles and hooks block that sits
collapsed at the top of an artifact, plus what it is modelled on.

Two inputs feed it:

- the operator's portrait (``$HERMES_HOME/media/``: portrait-black-shirt.jpg,
  source-portrait.*; the dashboard upload writes source-portrait.<ext>), used
  image-to-image so the real person appears on every thumbnail;
- the reference set: the top 20 videos by time-adjusted demand from the
  research engine's Supply / Demand table when it holds enough points, else
  the Trends tab's top 20 by views per hour. Title and thumbnail of each, so
  the worker follows winning patterns instead of inventing a format.

The block itself (``packaging`` in artifact.json) is validated and rendered
here so the simple and the full page share one look. Run as a script from a
worker task:

    python3 yti_packaging.py examples   # the reference set, JSON
    python3 yti_packaging.py formats    # the winning title frames, JSON
    python3 yti_packaging.py portrait   # the portrait path, or nothing
"""
from __future__ import annotations

import html as _html
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

try:
    from . import yti_paths
except ImportError:  # standalone (script / builder) import
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import yti_paths  # type: ignore

EXAMPLE_LIMIT = 20
MIN_SUPPLY_DEMAND_POINTS = 20
THUMB_COUNT = 5
TITLE_COUNT = 20
FRAME_LIMIT = 20
HOOK_COUNT = 5
THUMB_MAX_WORDS = 5
TITLE_MAX_CHARS = 70
MIN_IMAGE_BYTES = 20_000
_PORTRAIT_EXTS = (".jpg", ".jpeg", ".png", ".webp")


# ---------------------------------------------------------------------------
# Portrait
# ---------------------------------------------------------------------------

def media_dir() -> Path:
    home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    return home / "media"


def portrait_path() -> Optional[Path]:
    """The operator's standing portrait: the newest of the known names."""
    d = media_dir()
    if not d.is_dir():
        return None
    cands = []
    for p in d.iterdir():
        if not p.is_file() or p.suffix.lower() not in _PORTRAIT_EXTS:
            continue
        if p.stem in ("portrait-black-shirt", "source-portrait", "portrait") or p.stem.startswith("source-portrait"):
            cands.append(p)
    if not cands:
        return None
    return max(cands, key=lambda p: p.stat().st_mtime)


def portrait_status() -> dict[str, Any]:
    p = portrait_path()
    return {"present": p is not None, "path": str(p) if p else None,
            "name": p.name if p else None, "bytes": p.stat().st_size if p else 0}


def save_portrait(payload: bytes, ext: str) -> dict[str, Any]:
    """Store the operator's portrait as source-portrait.<ext> (replacing any
    earlier source-portrait.*). It becomes the thumbnail source immediately."""
    ext = (ext or "").lower().lstrip(".")
    if f".{ext}" not in _PORTRAIT_EXTS:
        return {"error": f"unsupported image type: .{ext}"}
    if not payload or len(payload) > 15 * 1024 * 1024:
        return {"error": "image must be non-empty and under 15 MB"}
    d = media_dir()
    d.mkdir(parents=True, exist_ok=True)
    for old in d.glob("source-portrait.*"):
        try:
            old.unlink()
        except OSError:
            pass
    dst = d / f"source-portrait.{ext}"
    dst.write_bytes(payload)
    return {"ok": True, **portrait_status()}


# ---------------------------------------------------------------------------
# Reference set
# ---------------------------------------------------------------------------

def _supply_demand_examples(limit: int) -> Optional[list[dict[str, Any]]]:
    try:
        import yti_rs_config
        import yti_rs_db
        import yti_rs_report
    except ImportError:
        return None
    try:
        conn = yti_rs_db.connect()
    except Exception:  # noqa: BLE001
        return None
    try:
        cfg = yti_rs_config.load_config(conn)
        sd = yti_rs_report.supply_demand(conn, cfg)
    except Exception:  # noqa: BLE001
        return None
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    # the engine's own hit filters, as the Supply / Demand view applies them:
    # hits only (at least the hit multiple), no suspected paid traffic, and
    # channels in the comparable size band or followed by the operator
    lo = float(sd.get("min_subs") or 0)
    hi = float(sd.get("max_subs") or 0)

    def comparable(p: dict[str, Any]) -> bool:
        if p.get("fol"):
            return True
        sub = p.get("sub")
        if sub is None:                      # size unknown: not a proven comparable
            return False
        return (lo <= float(sub)) and (hi <= 0 or float(sub) <= hi)

    def same_language(title: str) -> bool:
        # the operator packages in the script their audience reads; a title
        # written mostly in another script is not a pattern to follow
        letters = [ch for ch in title if ch.isalpha()]
        return not letters or sum(ch.isascii() for ch in letters) / len(letters) >= 0.6
    points = [p for p in (sd.get("points") or []) if p.get("id") and p.get("t")
              and p.get("c") in ("hit", "strong_hit") and "paid?" not in (p.get("f") or [])
              and comparable(p) and same_language(p["t"])]
    if len(points) < MIN_SUPPLY_DEMAND_POINTS:
        return None
    hl = sd.get("half_life") or {}
    default_hl = float(sd.get("default_half_life") or 365.0)

    def adjusted(p: dict[str, Any]) -> float:
        h = float(hl.get(p.get("n"), default_hl) if isinstance(hl, dict) else default_hl) or default_hl
        return float(p.get("m") or 0.0) * (0.5 ** (float(p.get("a") or 0.0) / h))
    ranked = sorted(points, key=adjusted, reverse=True)
    out, seen = [], set()
    for p in ranked:
        if p["id"] in seen:
            continue
        seen.add(p["id"])
        out.append({"id": p["id"], "title": p["t"], "channel": p.get("ch") or "", "niche": p.get("n") or "",
                    "thumbnail": f"https://i.ytimg.com/vi/{p['id']}/hqdefault.jpg",
                    "url": f"https://www.youtube.com/watch?v={p['id']}",
                    "metric": round(adjusted(p), 2), "metric_label": "x normal, time-adjusted",
                    "views": p.get("v"), "age_days": p.get("a")})
        if len(out) >= limit:
            break
    return out


def _trending_examples(limit: int) -> list[dict[str, Any]]:
    try:
        import yti_fetcher
        import yti_store
    except ImportError:
        return []
    try:
        conn = yti_store.connect()
    except Exception:  # noqa: BLE001
        return []
    try:
        vids = yti_fetcher.trends_from_db(conn) or []
    except Exception:  # noqa: BLE001
        return []
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    vids = sorted((v for v in vids if v.get("videoId") and v.get("title")),
                  key=lambda v: float(v.get("vph") or 0), reverse=True)
    return [{"id": v["videoId"], "title": v["title"], "channel": v.get("author") or "", "niche": "",
             "thumbnail": v.get("thumbnail") or f"https://i.ytimg.com/vi/{v['videoId']}/hqdefault.jpg",
             "url": v.get("link") or f"https://www.youtube.com/watch?v={v['videoId']}",
             "metric": v.get("vph"), "metric_label": "views per hour", "views": v.get("views"),
             "age_days": None} for v in vids[:limit]]


def formats(limit: int = FRAME_LIMIT) -> dict[str, Any]:
    """The winning title frames: the research engine's D3 format library.
    `mined` frames are title shapes that recur across channels, ranked by the
    Wilson lower bound of their hit rate and kept only when they have enough
    videos behind them (the engine's min_actionable_n); `seeded` frames are
    the curated templates with their psychology. Titles are written in
    these, each tagged with the frame it uses."""
    try:
        import yti_rs_config
        import yti_rs_db
        import yti_rs_formats
    except ImportError:
        return {"source": "none", "min_n": 0, "mined": [], "seeded": []}
    try:
        conn = yti_rs_db.connect()
    except Exception:  # noqa: BLE001
        return {"source": "none", "min_n": 0, "mined": [], "seeded": []}
    try:
        cfg = yti_rs_config.load_config(conn)
        lib = yti_rs_formats.library(conn)
    except Exception:  # noqa: BLE001
        return {"source": "none", "min_n": 0, "mined": [], "seeded": []}
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    min_n = int((cfg.get("formats") or {}).get("min_actionable_n", 10)) if isinstance(cfg, dict) else 10
    mined = [f for f in lib if f.get("kind") == "mined" and int(f.get("n_total") or 0) >= min_n]
    mined.sort(key=lambda f: (-(float(f.get("wilson_lb") or 0)), -int(f.get("n_total") or 0)))
    out_m = [{"frame": f.get("label"), "hits": int(f.get("n_hits") or 0), "n": int(f.get("n_total") or 0),
              "hit_rate": round(float(f.get("hit_rate") or 0), 2), "wilson_lb": round(float(f.get("wilson_lb") or 0), 2),
              "examples": [e.get("title") for e in (f.get("examples") or [])[:2] if e.get("title")]}
             for f in mined[:limit]]
    out_s = [{"frame": f.get("label"), "psychology": f.get("psychology") or "", "caution": f.get("caution") or ""}
             for f in lib if f.get("kind") == "seeded" and f.get("label")]
    return {"source": "format-library", "label": "D3 format library, winning title frames",
            "min_n": min_n, "mined": out_m, "seeded": out_s}


def examples(limit: int = EXAMPLE_LIMIT) -> dict[str, Any]:
    """The reference set: Supply / Demand top N when that table is populated
    (at least MIN_SUPPLY_DEMAND_POINTS points), else the Trends tab top N."""
    items = _supply_demand_examples(limit)
    if items:
        return {"source": "supply-demand", "label": "Supply / Demand, top by time-adjusted demand", "items": items}
    return {"source": "trending", "label": "Trends tab, top by views per hour", "items": _trending_examples(limit)}


# ---------------------------------------------------------------------------
# The packaging block: validation and rendering, shared by both builders
# ---------------------------------------------------------------------------

def _words(s: str) -> int:
    return len([w for w in re.split(r"\s+", (s or "").strip()) if w])


def validate(pk: Any, base_dir: Path, promise: Optional[str]) -> list[str]:
    """Problems with a `packaging` block, or [] when it is sound. *promise*
    is the page's own promise line; the block has to carry it verbatim so
    thumbnails, titles and hooks are written to make that promise."""
    p: list[str] = []
    w = "packaging"
    if not isinstance(pk, dict):
        return [f"{w}: must be an object (see schema.md)"]
    if not isinstance(pk.get("promise"), str) or not pk["promise"].strip():
        p.append(f"{w}.promise: required; the page's promise line, verbatim")
    elif promise and pk["promise"].strip() != promise.strip():
        p.append(f"{w}.promise: must equal the page's promise line verbatim ({promise!r}); thumbnails, titles and hooks make that promise")
    hooks = pk.get("hooks")
    if not isinstance(hooks, list) or len(hooks) != HOOK_COUNT:
        p.append(f"{w}.hooks: exactly {HOOK_COUNT} of {{text, given?: true}}; the operator's own hook first when one was given")
    else:
        for i, h in enumerate(hooks):
            if not isinstance(h, dict) or not isinstance(h.get("text"), str) or not h["text"].strip():
                p.append(f"{w}.hooks[{i}]: needs text")
            elif _words(h["text"]) > 40:
                p.append(f"{w}.hooks[{i}]: {_words(h['text'])} words; a hook is one or two spoken sentences, under 40")
        if any(isinstance(h, dict) and h.get("given") for h in hooks) and not (isinstance(hooks[0], dict) and hooks[0].get("given")):
            p.append(f"{w}.hooks: the operator's given hook comes first")
    titles = pk.get("titles")
    if not isinstance(titles, list) or len(titles) != TITLE_COUNT:
        p.append(f"{w}.titles: exactly {TITLE_COUNT} of {{text, frame}}, written in the winning frames from `yti_packaging.py formats`")
    else:
        seen_t = set()
        for i, t in enumerate(titles):
            if not isinstance(t, dict) or not isinstance(t.get("text"), str) or not t["text"].strip():
                p.append(f"{w}.titles[{i}]: needs {{text, frame}}")
                continue
            if len(t["text"]) > TITLE_MAX_CHARS:
                p.append(f"{w}.titles[{i}]: {len(t['text'])} characters; keep a title under {TITLE_MAX_CHARS}")
            if not isinstance(t.get("frame"), str) or not t["frame"].strip():
                p.append(f"{w}.titles[{i}].frame: name the title frame it uses (a mined frame label, a seeded template, or a pattern from the reference set)")
            key = t["text"].strip().lower()
            if key in seen_t:
                p.append(f"{w}.titles[{i}]: duplicate title")
            seen_t.add(key)
    fr = pk.get("frames")
    if not isinstance(fr, dict) or not isinstance(fr.get("mined"), list) or not isinstance(fr.get("seeded"), list):
        p.append(f"{w}.frames: the JSON `yti_packaging.py formats` printed, unchanged ({{source, label, min_n, mined, seeded}})")
    thumbs = pk.get("thumbnails")
    if not isinstance(thumbs, list) or len(thumbs) != THUMB_COUNT:
        p.append(f"{w}.thumbnails: exactly {THUMB_COUNT} of {{image, text, image_alt?}}")
    else:
        seen = set()
        for i, t in enumerate(thumbs):
            where = f"{w}.thumbnails[{i}]"
            if not isinstance(t, dict):
                p.append(f"{where}: must be an object")
                continue
            txt = t.get("text")
            if not isinstance(txt, str) or not txt.strip():
                p.append(f"{where}.text: the words on the thumbnail (1 to {THUMB_MAX_WORDS}; 2 or 3 when they carry the point)")
            elif _words(txt) > THUMB_MAX_WORDS:
                p.append(f"{where}.text: {_words(txt)} words; no more than {THUMB_MAX_WORDS}, less is more")
            rel = t.get("image")
            if not isinstance(rel, str) or not rel.startswith("img/") or ".." in rel:
                p.append(f"{where}.image: must be a path like img/thumb-1.jpg")
                continue
            if rel in seen:
                p.append(f"{where}.image: {rel} used twice")
            seen.add(rel)
            f = base_dir / rel
            if not f.is_file():
                p.append(f"{where}.image: {rel} not found")
            elif f.stat().st_size < MIN_IMAGE_BYTES:
                p.append(f"{where}.image: {rel} is {f.stat().st_size} bytes; a generated thumbnail is at least {MIN_IMAGE_BYTES}")
    ex = pk.get("examples")
    if not isinstance(ex, dict) or ex.get("source") not in ("supply-demand", "trending") \
            or not isinstance(ex.get("items"), list) or not ex["items"]:
        p.append(f"{w}.examples: {{source: supply-demand|trending, label?, items: [..]}} from `yti_packaging.py examples`; the patterns the block follows")
    else:
        for i, it in enumerate(ex["items"][:EXAMPLE_LIMIT]):
            if not isinstance(it, dict) or not it.get("title") or not it.get("thumbnail"):
                p.append(f"{w}.examples.items[{i}]: needs title and thumbnail")
    if isinstance(pk.get("patterns"), list):
        for i, s in enumerate(pk["patterns"]):
            if not isinstance(s, str) or not s.strip():
                p.append(f"{w}.patterns[{i}]: must be a non-empty string")
    return p


CSS = r"""
.pack{margin:20px 0 0;border:1px solid var(--line);border-radius:10px;background:var(--surface)}
.pack>summary{cursor:pointer;padding:12px 16px;font-family:var(--display);font-size:18px;font-weight:600;list-style:none;display:flex;align-items:center;gap:10px}
.pack>summary::-webkit-details-marker{display:none}
.pack>summary::before{content:"▸";color:var(--accent);font-size:16px}
.pack[open]>summary::before{content:"▾"}
.pack>summary small{font-family:var(--body);font-size:13px;font-weight:400;color:var(--muted)}
.pack .body{padding:0 16px 16px}
.pack h4{font-family:var(--display);font-size:15px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:18px 0 8px}
.pack .promise{border-left:4px solid var(--ok);background:var(--ok-bg);padding:8px 14px;border-radius:0 6px 6px 0;font-family:var(--display);font-size:18px;font-weight:600;margin-top:4px}
.pack ol{margin:0;padding-left:24px}
.pack ol li{margin:6px 0}
.pack .given{font-family:var(--mono);font-size:11px;color:var(--ok);background:var(--ok-bg);padding:1px 6px;border-radius:999px;margin-left:6px;vertical-align:middle}
.thumbs{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px}
.thumbs figure{margin:0}
.thumbs img{width:100%;height:auto;aspect-ratio:16/9;object-fit:cover;border-radius:6px;border:1px solid var(--line);background:#000}
.thumbs figcaption{font-family:var(--display);font-size:14px;font-weight:600;margin-top:4px}
.thumbs figcaption small{display:block;font-family:var(--body);font-weight:400;color:var(--muted);font-size:12px}
.refs{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}
.refs a{display:block;text-decoration:none;color:var(--ink);font-size:12px;line-height:1.3}
.refs img{width:100%;height:auto;aspect-ratio:16/9;object-fit:cover;border-radius:4px;border:1px solid var(--line);background:#000;margin-bottom:4px}
.refs b{display:block;font-weight:600;font-size:12.5px}
.refs span{color:var(--muted)}
.pack ul.pat{margin:0;padding-left:20px;font-size:15px}
.pack ul.pat li{margin:4px 0}
.pack ol.titles li .frame{display:block;font-family:var(--mono);font-size:11.5px;color:var(--muted)}
.pack ul.frames{margin:0;padding-left:20px;font-size:14px;columns:2;column-gap:24px}
@media (max-width:600px){.pack ul.frames{columns:1}}
.pack ul.frames li{margin:3px 0;break-inside:avoid}
.pack ul.frames li b{font-weight:600}
.pack ul.frames li span{color:var(--muted);font-size:12.5px}
.pack ul.frames li.sep{list-style:none;margin:10px 0 4px -20px;font-family:var(--display);font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
"""


def _esc(s: Any) -> str:
    return _html.escape(str(s if s is not None else ""), quote=True)


def render(pk: dict[str, Any], inline_fn=None) -> str:
    """The collapsed block. *inline_fn* is the builder's inline-markup
    function (escape + `code`/**bold**/links); escaping only by default."""
    inl = inline_fn or _esc
    given_tag = '<span class="given">yours</span>'
    hooks = "".join(f"<li>{inl(h['text'])}{given_tag if h.get('given') else ''}</li>" for h in pk["hooks"])
    titles = "".join(
        f"<li>{inl(t['text'])}<span class=\"frame\">{inl(t.get('frame') or '')}</span></li>" for t in pk["titles"])
    fr = pk.get("frames") or {}
    mined = "".join(
        f"<li><b>{inl(m.get('frame') or '')}</b> <span>{m.get('hits', 0)} of {m.get('n', 0)} hit"
        f"{(' · e.g. ' + inl(m['examples'][0])) if m.get('examples') else ''}</span></li>"
        for m in (fr.get("mined") or [])[:FRAME_LIMIT])
    seeded = "".join(
        f"<li><b>{inl(s_.get('frame') or '')}</b> <span>{inl(s_.get('psychology') or '')}</span></li>"
        for s_ in (fr.get("seeded") or []))
    frames_html = ""
    if mined or seeded:
        frames_html = ("<h4>Winning title frames (D3 format library)</h4><ul class=\"frames\">" + mined +
                       (f"<li class=\"sep\">Seeded templates</li>{seeded}" if seeded else "") + "</ul>")
    thumbs = "".join(
        f'<figure><img src="{_esc(t["image"])}" alt="{_esc(t.get("image_alt") or t["text"])}" loading="lazy">'
        f'<figcaption>{_esc(t["text"])}{("<small>" + inl(t["note"]) + "</small>") if t.get("note") else ""}</figcaption></figure>'
        for t in pk["thumbnails"])
    ex = pk["examples"]
    refs = "".join(
        f'<a href="{_esc(it.get("url") or "#")}" target="_blank" rel="noopener">'
        f'<img src="{_esc(it["thumbnail"])}" alt="" loading="lazy"><b>{_esc(it["title"])}</b>'
        f'<span>{_esc(it.get("channel") or "")}'
        f'{(" · " + _esc(it["metric"]) + " " + _esc(it.get("metric_label") or "")) if it.get("metric") is not None else ""}</span></a>'
        for it in ex["items"][:EXAMPLE_LIMIT])
    patterns = ""
    if pk.get("patterns"):
        patterns = "<h4>Patterns followed</h4><ul class=\"pat\">" + "".join(f"<li>{inl(s)}</li>" for s in pk["patterns"]) + "</ul>"
    n = len(ex["items"][:EXAMPLE_LIMIT])
    return (
        '<details class="pack"><summary>Packaging</summary><div class="body">'
        f'<h4>The promise every piece makes</h4><div class="promise">{inl(pk["promise"])}</div>'
        f'<h4>Thumbnails</h4><div class="thumbs">{thumbs}</div>'
        f'<h4>Titles ({len(pk["titles"])}, each in a winning frame)</h4><ol class="titles">{titles}</ol>'
        f'<h4>Hooks</h4><ol>{hooks}</ol>'
        f'{patterns}'
        f'{frames_html}'
        f'<h4>Reference set: {n} winning videos ({_esc(ex.get("label") or ex["source"])})</h4><div class="refs">{refs}</div>'
        "</div></details>"
    )


def count_images(pk: Any) -> int:
    return len(pk.get("thumbnails") or []) if isinstance(pk, dict) else 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "examples"
    if cmd == "examples":
        print(json.dumps(examples(), indent=2, ensure_ascii=False))
        return 0
    if cmd == "formats":
        print(json.dumps(formats(), indent=2, ensure_ascii=False))
        return 0
    if cmd == "portrait":
        p = portrait_path()
        if p is None:
            print("", end="")
            return 1
        print(p)
        return 0
    print("usage: yti_packaging.py examples | formats | portrait", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
