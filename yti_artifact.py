"""youtube-artifact tasks: the Produce button, the Research tab's Artifact
button and the Artifacts page's Artifact ✦ button all create ONE kanban task
whose worker runs the `long-form:youtube-artifact` skill and leaves a
rendered page (index.html + artifact.json + img/) in the workspace. The
dashboard previews that page and the Iterate button opens a feedback task
against the same folder.

Same lifecycle as the other generate families (yti_generate): born-assigned
task, meta-map keyed by the artifact folder, open|stale|done states, and a
completion validator that re-runs the skill's own quality gate and opens a
fix task (max 2) when the folder does not pass.
"""
from __future__ import annotations

import importlib.util
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    from . import yti_generate, yti_paths, yti_store, yti_workspace
    from .yti_analysis import (
        _kanban,
        _kanban_task_open,
        kick_dispatcher,
        resolve_kanban_assignee,
    )
except ImportError:  # standalone import (dashboard plugin_api path)
    import yti_generate  # type: ignore
    import yti_paths  # type: ignore
    import yti_store  # type: ignore
    import yti_workspace  # type: ignore
    from yti_analysis import (  # type: ignore
        _kanban,
        _kanban_task_open,
        kick_dispatcher,
        resolve_kanban_assignee,
    )

ARTIFACT_META_KEY = "artifact_tasks"
ARTIFACT_SKILLS = (
    "long-form:youtube-artifact",
    "long-form:generate-image",
)
ARTIFACT_MAX_RETRIES = 2
_SKILL_DIR = Path(__file__).resolve().parent / "skills" / "youtube-artifact"
_BUILDER = _SKILL_DIR / "build_artifact.py"
_PAGE_FILES = {"index.html", "artifact.json"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:60] or "topic"


def builder():
    """The skill's build_artifact module (validate / render / inline)."""
    spec = importlib.util.spec_from_file_location("yti_build_artifact", str(_BUILDER))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _load_map(conn) -> dict[str, Any]:
    raw = yti_store.get_meta(conn, ARTIFACT_META_KEY)
    try:
        m = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return m if isinstance(m, dict) else {}


def _save_map(conn, mapping: dict[str, Any]) -> None:
    yti_store.set_meta(conn, ARTIFACT_META_KEY, json.dumps(mapping))


# ---------------------------------------------------------------------------
# Output folders
# ---------------------------------------------------------------------------

def _versioned(parent: Path, stem: str = "artifact") -> Path:
    """artifact/, then artifact-2/, artifact-3/ ... never overwrite a page."""
    first = parent / stem
    if not first.exists():
        return first
    n = 2
    while (parent / f"{stem}-{n}").exists():
        n += 1
    return parent / f"{stem}-{n}"


def out_dir_for_topic(topic: str) -> Path:
    return _versioned(yti_paths.workspace_dir() / "youtube" / _today() / "artifacts", _slug(topic))


def artifact_root(rel_path: str) -> Optional[Path]:
    """The artifact folder for a path inside it (folder, index.html,
    artifact.json or an img/ file), or None when the path is not inside one."""
    ws = yti_paths.workspace_dir()
    p = yti_workspace.resolve_inside_workspace(ws, rel_path or "")
    if p is None:
        return None
    for cand in (p, *p.parents):
        if cand == ws or ws not in cand.parents:
            break
        if cand.is_dir() and ((cand / "artifact.json").exists() or (cand / "index.html").exists()):
            return cand
    return None


# ---------------------------------------------------------------------------
# Briefs
# ---------------------------------------------------------------------------

def _dashboard_link(out_dir: Path) -> str:
    rel = out_dir.relative_to(yti_paths.workspace_dir()).as_posix()
    return f"/longform?tab=artifacts&path={rel}"


def _common_tail(out_dir: Path) -> list[str]:
    return [
        "",
        "### Phase 3 to 5 (images, render, publish) — from the skill",
        "Images: ONE sketchnote per recommendation through the",
        "`long-form:generate-image` skill, in parallel, anchored on the",
        f"selected style baseline `--input \"{yti_generate.selected_baseline_path()}\"`,",
        "with every rendered word in `--expect-text` and the step's meaning in",
        f"`--context`. Save them under {out_dir}/img/. Look at each one once.",
        "Render with:",
        f"  python3 {_BUILDER} {out_dir}",
        "and fix every problem it prints until it says OK. A completion",
        "validator re-runs that exact gate; a folder that fails re-opens as a",
        "fix task, so do not complete until it is clean.",
        "Then `kanban_attach` index.html and artifact.json, and `kanban_complete`",
        "with a summary naming the folder, the page link",
        f"`{_dashboard_link(out_dir)}` (Artifacts tab, select index.html for the",
        "preview and Open page), the section and image counts, every",
        "corrected figure, and anything left unverified.",
        "",
        "### CRITICAL RULES",
        f"- Every output file goes under exactly {out_dir}/ (artifact.json,",
        "  index.html, img/). Scratch, logs and prompts stay in your task",
        "  workspace; the completion sweep deletes strays from the folder.",
        "- The page carries content only: no instructions to the presenter,",
        "  no 'say this', no slide or camera talk (the validator rejects them).",
        "- No claim without a source you fetched. No em dashes. No images from",
        "  anything but generate-image.",
        "- Never hand-edit index.html; it is rendered from artifact.json.",
    ] + yti_generate.selected_style_block()


def _pov_block(pov: str, guidance: str) -> list[str]:
    return [
        "### The operator's point of view (the page argues THIS)",
        "",
        "```",
        (pov or "").strip() or "(none given: take the angle the source material and the company context support best)",
        "```",
        "",
        "### Research guidance (every line is a to-do)",
        "",
        "```",
        (guidance or "").strip() or "(none given: verify every claim against a primary source and a trade source)",
        "```",
    ]


def _context_block() -> list[str]:
    return [
        "### Company context (audience)",
        "Read $HERMES_HOME/plugins-data/ai-cyber-value-creator/company-context.md",
        "(default HERMES_HOME ~/.hermes; /opt/data in the container). If present,",
        "write for that ideal customer in their vocabulary; the boxes section",
        "(stop / do before / keep doing) is addressed to them. If missing, note",
        "it in the completion summary and proceed.",
    ]


def build_topic_brief(topic: str, pov: str, guidance: str, out_dir: Path,
                      source: Optional[dict[str, Any]] = None) -> str:
    head = [
        "## MANDATORY: Build ONE youtube-artifact page on a topic.",
        "",
        f"**Topic:** {topic}",
        f"**Output directory:** {out_dir}",
        f"**Worked example (style and depth):** {_SKILL_DIR / 'example' / 'artifact.json'}",
        f"**Contract:** {_SKILL_DIR / 'schema.md'}",
        "",
        "You do ALL phases yourself in this session: no subtasks, no",
        "delegation. Load `long-form:youtube-artifact` and follow it.",
        "",
    ]
    src: list[str] = []
    if source:
        src = ["### Source material (seeds the structure; NOT a source for claims)"]
        for k, v in source.items():
            if v in (None, "", [], {}):
                continue
            if isinstance(v, (list, dict)):
                v = json.dumps(v, ensure_ascii=False)[:1500]
            src.append(f"- **{k}:** {v}")
        src.append("")
    return "\n".join(head + src + _pov_block(pov, guidance) + [""] + _context_block() + [
        "",
        "### Phase 1 to 2 (research, write) — from the skill",
        "Build the claim list from the topic, the point of view and the source",
        "material; verify every claim with `web_search` + `web_extract` against",
        "a primary source and a trade source; record corrections and",
        "not-founds; then write artifact.json in the contract, in the section",
        "order the example uses (drop a section only when the topic has",
        "nothing for it). The research guidance above overrides the default",
        "order of work.",
    ] + _common_tail(out_dir))


def build_script_brief(script_abs: Path, out_dir: Path) -> str:
    return "\n".join([
        "## MANDATORY: Build ONE youtube-artifact page from an approved script.",
        "",
        f"**Script:** {script_abs}",
        f"**Output directory:** {out_dir}",
        f"**Worked example (style and depth):** {_SKILL_DIR / 'example' / 'artifact.json'}",
        f"**Contract:** {_SKILL_DIR / 'schema.md'}",
        "",
        "You do ALL phases yourself in this session: no subtasks, no",
        "delegation. Load `long-form:youtube-artifact` and follow it.",
        "",
        "### How the script maps to the page",
        "- The script's title, hook and thesis become the title, eyebrow and lede.",
        "- Each beat becomes a card (its spoken lines condensed to the",
        "  mechanism and the takeaway; the beat's `Visual` is NOT the image",
        "  source, the steps are).",
        "- The script's framework, rules or recommendations become the steps",
        "  (one sketchnote image each). If the script has no explicit",
        "  recommendations, derive 5 to 10 from its beats and the synthesis.",
        "- Dated events in the script become the timeline; undated ones the list.",
        "- The script's 'Based on outlier' / 'Based on gap' block and its",
        "  Links for Description are the starting claim list.",
        "",
        "### Point of view",
        "The script's thesis and the concept doc beside it (concepts*.md) are",
        "the point of view; the company context below names the audience.",
        "",
    ] + _context_block() + [
        "",
        "### Phase 1 to 2 (research, write) — from the skill",
        "Verify every claim the script makes with `web_search` + `web_extract`",
        "against a primary source and a trade source before it goes on the",
        "page; a claim that does not verify is dropped or moved to the FAQ as",
        "unverified. Then write artifact.json in the contract.",
    ] + _common_tail(out_dir))


def build_iterate_brief(out_dir: Path, feedback: str) -> str:
    return "\n".join([
        "## MANDATORY: Iterate ONE existing youtube-artifact page from feedback.",
        "",
        f"**Artifact folder:** {out_dir}",
        f"**Contract:** {_SKILL_DIR / 'schema.md'}",
        "",
        "### The operator's feedback (the whole job)",
        "",
        "```",
        (feedback or "").strip() or "(none given: re-render and re-check the page)",
        "```",
        "",
        "### Steps",
        "1. Read artifact.json and the current index.html; understand exactly",
        "   what the feedback asks for. Make the SMALLEST change that satisfies",
        "   it: edit artifact.json (content, order, a new section, a corrected",
        "   figure with its source fetched and cited), and regenerate ONLY the",
        "   images the feedback names, through `long-form:generate-image` with",
        "   the same invocation the skill prescribes and the feedback folded",
        "   into the prompt and `--context`. A new recommendation needs a new",
        "   image. Never regenerate every image for a one-line note.",
        "2. A purely visual request that the contract cannot express goes into",
        "   `css_extra` (a few declarations), never into index.html by hand.",
        f"3. Render: `python3 {_BUILDER} {out_dir}` until it prints OK (the",
        "   previous page is kept under history/ automatically).",
        "4. `kanban_attach` index.html, then `kanban_complete` naming what",
        "   changed and the page link",
        f"   `{_dashboard_link(out_dir)}`.",
        "",
        "### CRITICAL RULES",
        "- Content only on the page: no presenter instructions, no em dashes.",
        "- Scratch stays out of the folder; strays are swept on completion.",
    ] + yti_generate.selected_style_block())


def build_fix_brief(entry: dict[str, Any], problems: list[str]) -> str:
    out_dir = entry.get("outDir") or ""
    return "\n".join([
        "## MANDATORY: Fix a youtube-artifact folder that FAILED its quality gate.",
        "",
        "A worker completed this page task, but the gate found:",
        "",
    ] + [f"- {p}" for p in problems[:40]] + [
        "",
        f"**Artifact folder:** {out_dir}",
        f"**Contract:** {_SKILL_DIR / 'schema.md'}",
        f"**Topic:** {entry.get('topic') or ''}",
        "",
        "Load `long-form:youtube-artifact`. Fix exactly the problems listed:",
        "missing or thin sections get written (with sources fetched), missing",
        "images get generated through `long-form:generate-image`, presenter",
        "instructions get rewritten as content, strays get removed. Then run",
        f"`python3 {_BUILDER} {out_dir}` until it prints OK, `kanban_attach`",
        "index.html, and `kanban_complete`.",
    ])


# ---------------------------------------------------------------------------
# Task creation
# ---------------------------------------------------------------------------

def _create(conn, key: str, title: str, body: str, extra: dict[str, Any]) -> dict[str, Any]:
    kb = _kanban()
    if kb is None:
        return {"error": "kanban unavailable"}
    mapping = _load_map(conn)
    entry = mapping.get(key) or {}
    existing = entry.get("taskId")
    with kb.connect_closing() as conn_kb:
        if existing and _kanban_task_open(kb, conn_kb, existing):
            age = yti_generate._age_minutes(entry.get("createdAt"))
            if age is not None and age < yti_generate.STALE_MINUTES:
                return {"ok": True, "taskId": existing, "already": True, "outDir": key}
        task_id = kb.create_task(
            conn_kb, title=title, body=yti_generate._with_voice(body),
            assignee=resolve_kanban_assignee(), created_by="long-form",
            workspace_kind="scratch", skills=list(ARTIFACT_SKILLS), priority=10)
    mapping[key] = {"taskId": task_id, "createdAt": _now_iso(), "outDir": extra.get("outDirAbs"),
                    "topic": extra.get("topic"), "kind": extra.get("kind"),
                    "fixRetries": 0, "videoId": extra.get("videoId")}
    _save_map(conn, mapping)
    kick_dispatcher()
    return {"ok": True, "taskId": task_id, "outDir": key}


def create_topic_task(topic: str, pov: str = "", guidance: str = "",
                      source: Optional[dict[str, Any]] = None,
                      out_dir: Optional[Path] = None, video_id: Optional[str] = None) -> dict[str, Any]:
    """Artifacts page Artifact ✦ (no source) or Research tab Artifact (with a
    teardown as source and the video's own folder as out_dir)."""
    topic = (topic or "").strip()
    if not topic:
        return {"error": "topic is required"}
    out_dir = out_dir or out_dir_for_topic(topic)
    ws = yti_paths.workspace_dir()
    try:
        key = out_dir.relative_to(ws).as_posix()
    except ValueError:
        return {"error": "output directory must be inside the workspace"}
    conn = yti_store.connect()
    try:
        return _create(conn, key, f"Page: {topic[:70]}",
                       build_topic_brief(topic, pov, guidance, out_dir, source),
                       {"outDirAbs": str(out_dir), "topic": topic, "kind": "topic", "videoId": video_id})
    except Exception as exc:  # noqa: BLE001
        return {"error": f"could not create the page task: {exc}"}
    finally:
        conn.close()


def create_script_task(rel_path: str) -> dict[str, Any]:
    """Artifacts tab Produce button: a page from one approved script."""
    ws = yti_paths.workspace_dir()
    script_abs = yti_workspace.resolve_inside_workspace(ws, rel_path or "")
    if script_abs is None or not script_abs.exists():
        return {"error": f"script not found in workspace: {rel_path}"}
    if script_abs.suffix.lower() != ".md":
        return {"error": "produce runs on markdown script files"}
    out_dir = _versioned(script_abs.parent, "artifact")
    key = out_dir.relative_to(ws).as_posix()
    conn = yti_store.connect()
    try:
        res = _create(conn, key, f"Page: {script_abs.stem}", build_script_brief(script_abs, out_dir),
                      {"outDirAbs": str(out_dir), "topic": script_abs.stem, "kind": "script"})
        if res.get("ok") and not res.get("already"):
            # the Produce button's spinner is keyed by the SCRIPT path
            raw = yti_store.get_meta(conn, yti_generate.PRODUCE_META_KEY)
            try:
                pm = json.loads(raw) if raw else {}
            except ValueError:
                pm = {}
            if not isinstance(pm, dict):
                pm = {}
            pm[rel_path] = {"taskId": res["taskId"], "createdAt": _now_iso(), "artifact": key}
            yti_store.set_meta(conn, yti_generate.PRODUCE_META_KEY, json.dumps(pm))
        return res
    except Exception as exc:  # noqa: BLE001
        return {"error": f"could not create the page task: {exc}"}
    finally:
        conn.close()


def create_iterate_task(rel_path: str, feedback: str = "") -> dict[str, Any]:
    root = artifact_root(rel_path)
    if root is None:
        return {"error": f"not inside an artifact folder: {rel_path}"}
    ws = yti_paths.workspace_dir()
    key = root.relative_to(ws).as_posix()
    conn = yti_store.connect()
    try:
        prev = _load_map(conn).get(key) or {}
        return _create(conn, key, f"Iterate page: {root.name if root.name.startswith('artifact') else root.name}",
                       build_iterate_brief(root, feedback),
                       {"outDirAbs": str(root), "topic": prev.get("topic") or root.name, "kind": "iterate",
                        "videoId": prev.get("videoId")})
    except Exception as exc:  # noqa: BLE001
        return {"error": f"could not create the iterate task: {exc}"}
    finally:
        conn.close()


def rebuild(rel_path: str) -> dict[str, Any]:
    """Deterministic re-render of index.html from artifact.json (no agent)."""
    root = artifact_root(rel_path)
    if root is None:
        return {"error": f"not inside an artifact folder: {rel_path}", "problems": []}
    rep = builder().build(root)
    if not rep.get("ok"):
        return {"error": "the page does not pass its quality gate", "problems": rep.get("problems") or []}
    ws = yti_paths.workspace_dir()
    return {"ok": True, "page": (root / "index.html").relative_to(ws).as_posix()}


# ---------------------------------------------------------------------------
# States and completion
# ---------------------------------------------------------------------------

def states() -> dict[str, dict[str, Any]]:
    out = yti_generate._states_for(ARTIFACT_META_KEY)
    ws = yti_paths.workspace_dir()
    for key, st in out.items():
        page = ws / key / "index.html"
        st["page"] = f"{key}/index.html" if page.exists() else None
    return out


def state_for_dir(rel_dir: Optional[str]) -> dict[str, Any]:
    """The Research row's artifact state: the newest artifact folder under a
    video's workspace folder, its task state and page."""
    if not rel_dir:
        return {"status": None, "taskId": None, "dir": None, "page": None}
    ws = yti_paths.workspace_dir()
    base = ws / rel_dir
    cands = sorted((p for p in base.glob("artifact*") if p.is_dir()), key=lambda p: p.stat().st_mtime) \
        if base.is_dir() else []
    all_states = states()
    best: dict[str, Any] = {"status": None, "taskId": None, "dir": None, "page": None}
    for p in cands:
        key = p.relative_to(ws).as_posix()
        st = all_states.get(key) or {}
        page = f"{key}/index.html" if (p / "index.html").exists() else None
        best = {"status": st.get("status") or ("done" if page else None), "taskId": st.get("taskId"),
                "dir": key, "page": page, "sessionId": st.get("sessionId")}
    # an open task whose folder does not exist yet
    for key, st in all_states.items():
        if st.get("status") == "open" and key.startswith(rel_dir.rstrip("/") + "/"):
            best = {"status": "open", "taskId": st.get("taskId"), "dir": key, "page": None,
                    "sessionId": st.get("sessionId")}
    return best


def _sweep(root: Path) -> list[str]:
    removed: list[str] = []
    if not root.is_dir():
        return removed
    keep = {"index.html", "artifact.json", "img", "history", "README.md", "SUMMARY.md"}
    for p in root.iterdir():
        if p.name in keep or p.name.startswith("."):
            continue
        if p.is_file():
            try:
                p.unlink(); removed.append(p.name)
            except OSError:
                pass
    img = root / "img"
    if img.is_dir():
        for p in img.iterdir():
            if p.is_file() and p.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                try:
                    p.unlink(); removed.append(f"img/{p.name}")
                except OSError:
                    pass
    return removed


def handle_completion(conn, kanban_task_id: str) -> Optional[dict[str, Any]]:
    """kanban_task_completed hook: sweep strays, re-run the skill's gate,
    open a fix task (max ARTIFACT_MAX_RETRIES) when the page does not pass."""
    mapping = _load_map(conn)
    for key, entry in mapping.items():
        if not isinstance(entry, dict) or entry.get("taskId") != kanban_task_id:
            continue
        root = Path(entry.get("outDir") or (yti_paths.workspace_dir() / key))
        removed = _sweep(root)
        rep = builder().build(root)  # renders when clean, so the page is always current
        problems = rep.get("problems") or []
        if not problems:
            return {"ok": True, "clean": True, "key": key, "swept": len(removed), "page": rep.get("page")}
        retries = int(entry.get("fixRetries") or 0)
        if retries >= ARTIFACT_MAX_RETRIES:
            return {"ok": False, "exhausted": True, "key": key, "problems": problems, "swept": len(removed)}
        kb = _kanban()
        if kb is None:
            return {"ok": False, "key": key, "problems": problems}
        with kb.connect_closing() as conn_kb:
            fix_id = kb.create_task(
                conn_kb, title=f"Fix page: {root.name}",
                body=yti_generate._with_voice(build_fix_brief(entry, problems)),
                assignee=resolve_kanban_assignee(), created_by="long-form",
                workspace_kind="scratch", skills=list(ARTIFACT_SKILLS), priority=10)
        entry.update({"taskId": fix_id, "fixRetries": retries + 1, "createdAt": _now_iso()})
        mapping[key] = entry
        _save_map(conn, mapping)
        kick_dispatcher()
        return {"ok": True, "fixTask": fix_id, "problems": problems, "key": key, "swept": len(removed)}
    return None


# ---------------------------------------------------------------------------
# Page serving (the dashboard route)
# ---------------------------------------------------------------------------

_MIME = {".html": "text/html; charset=utf-8", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".png": "image/png", ".webp": "image/webp", ".json": "application/json", ".svg": "image/svg+xml"}


def page_file(rel_path: str, inline: bool = False) -> dict[str, Any]:
    """Resolve a workspace path for the /page route. Returns {path, media}
    or {html, media} when inline=True on an index.html (images embedded)."""
    ws = yti_paths.workspace_dir()
    p = yti_workspace.resolve_inside_workspace(ws, rel_path or "")
    if p is None or not p.is_file():
        return {"error": "not found"}
    ext = p.suffix.lower()
    media = _MIME.get(ext)
    if media is None:
        return {"error": "unsupported file type"}
    if inline and ext == ".html":
        mod = builder()
        return {"html": mod.inline_images(p.read_text(encoding="utf-8"), p.parent), "media": media}
    return {"path": str(p), "media": media}
