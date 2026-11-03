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
# The simple-artifact: a brief page (promise / problem / value / apply /
# summary / cta) from a URL or pasted content; its own skill and renderer,
# the same artifact.json file name marked `"kind": "simple"`, and the same
# folder lifecycle as the full page.
SIMPLE_SKILLS = (
    "long-form:simple-artifact",
    "long-form:generate-image",
)
_SIMPLE_SKILL_DIR = Path(__file__).resolve().parent / "skills" / "simple-artifact"
_SIMPLE_BUILDER = _SIMPLE_SKILL_DIR / "build_simple.py"
SIMPLE_CONTENT_MAX = 60_000   # characters of pasted content carried in the brief


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


def simple_builder():
    """The simple-artifact skill's build_simple module (same surface:
    build / validate / render / inline_images)."""
    spec = importlib.util.spec_from_file_location("yti_build_simple", str(_SIMPLE_BUILDER))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _artifact_kind(root: Path) -> Optional[str]:
    """The `kind` declared in the folder's artifact.json (None when the file
    is missing, unreadable, or a full page, which declares no kind)."""
    try:
        doc = json.loads((root / "artifact.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    k = doc.get("kind") if isinstance(doc, dict) else None
    return k if isinstance(k, str) else None


def is_simple(root: Path) -> bool:
    """A folder is a simple-artifact when its artifact.json says `"kind": "simple"`."""
    return _artifact_kind(root) == "simple"


def builder_for(root: Path, kind: Optional[str] = None):
    """The renderer that owns *root*. Every caller that validates, renders,
    inlines or gates a page folder goes through here (a test enforces it):
    artifact.json's own `kind` decides; the task's recorded kind decides for
    a folder the worker has not written yet."""
    declared = _artifact_kind(root)
    if declared == "simple" or (declared is None and not (root / "artifact.json").exists() and kind == "simple"):
        return simple_builder()
    return builder()


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

def reserved_dirs() -> set[str]:
    """Every folder a page task was ever given (absolute paths). A task that
    is still running has not created its folder yet, so folder existence
    alone cannot tell two clicks apart; the map can."""
    try:
        conn = yti_store.connect()
    except Exception:  # noqa: BLE001
        return set()
    try:
        return {str(e.get("outDir")) for e in _load_map(conn).values()
                if isinstance(e, dict) and e.get("outDir")}
    except Exception:  # noqa: BLE001
        return set()
    finally:
        conn.close()


def _versioned(parent: Path, stem: str = "artifact", taken: Optional[set[str]] = None) -> Path:
    """artifact/, then artifact-2/, artifact-3/ ... never overwrite a page:
    a folder counts as used when it exists on disk OR a task (open or done)
    was already pointed at it, so every click builds into its own folder."""
    if taken is None:
        taken = reserved_dirs()

    def used(d: Path) -> bool:
        return d.exists() or str(d) in taken

    first = parent / stem
    if not used(first):
        return first
    n = 2
    while used(parent / f"{stem}-{n}"):
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


def _common_tail(out_dir: Path, plan: Optional[list[str]] = None) -> list[str]:
    images = [
        "Images: ONE sketchnote per recommendation through the",
        "`long-form:generate-image` skill, in parallel, anchored on the",
        f"selected style baseline `--input \"{yti_generate.selected_baseline_path()}\"`,",
        "with every rendered word in `--expect-text` and the step's meaning in",
        f"`--context`. Save them under {out_dir}/img/. Look at each one once.",
    ] if (plan is None or "steps" in plan) else [
        "Images: none. The plan has no steps section, so this page carries no",
        "generated images and no img/ folder.",
    ]
    return [
        "",
        "### Phase 3 to 5 (images, render, publish) — from the skill",
    ] + images + [
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
    ] + (yti_generate.selected_style_block() if (plan is None or "steps" in plan) else [])


def plan_parts() -> list[dict[str, Any]]:
    """The section catalogue the Artifact modal shows (page order)."""
    return [dict(x) for x in builder().PLAN_PARTS]


def normalize_plan(parts: Any) -> Optional[list[str]]:
    """Dashboard input -> ordered plan, or None when it is the whole page
    (so the classic brief and gate apply). Raises ValueError on bad parts."""
    if parts in (None, "", [], ()):
        return None
    b = builder()
    plan = b.normalize_plan(parts)
    return None if b.is_full_plan(plan) else plan


def _plan_block(plan: Optional[list[str]]) -> list[str]:
    if plan is None:
        return [
            "### Page plan",
            "The whole page: write every section in the order the example uses",
            "(drop one only when the topic genuinely has nothing for it; never pad).",
        ]
    parts = {x["type"]: x for x in builder().PLAN_PARTS}
    skipped = [t for t in builder().PLAN_TYPES if t not in plan]
    out = [
        "### Page plan (the operator chose these sections; nothing else)",
        "Write exactly these sections, in this order:",
    ] + [f"- `{t}`: {parts[t]['label']}. {parts[t]['desc']}." for t in plan] + [
        "",
        "Do NOT write: " + ", ".join(f"`{t}`" for t in skipped) + ".",
        "The page is deliberately narrower than the example. Research only",
        "what the chosen sections need, and give those sections the full depth",
        "the example gives them. Put the plan at the top level of artifact.json",
        "exactly as:",
        "```",
        json.dumps({"plan": plan}),
        "```",
        "The gate refuses a section outside the plan and a missing planned one.",
    ]
    return out


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
                      source: Optional[dict[str, Any]] = None,
                      plan: Optional[list[str]] = None) -> str:
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
    return "\n".join(head + src + _pov_block(pov, guidance) + [""] + _plan_block(plan) + [""]
                     + _context_block() + [
        "",
        "### Phase 1 to 2 (research, write) — from the skill",
        "Build the claim list from the topic, the point of view and the source",
        "material; verify every claim with `web_search` + `web_extract` against",
        "a primary source and a trade source; record corrections and",
        "not-founds; then write artifact.json in the contract, following the",
        "page plan above. The research guidance above overrides the default",
        "order of work.",
    ] + _common_tail(out_dir, plan))


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


def build_simple_iterate_brief(out_dir: Path, feedback: str) -> str:
    return "\n".join([
        "## MANDATORY: Iterate ONE existing simple-artifact brief page from feedback.",
        "",
        f"**Artifact folder:** {out_dir}",
        f"**Contract:** {_SIMPLE_SKILL_DIR / 'schema.md'}",
        "",
        "### The operator's feedback (the whole job)",
        "",
        "```",
        (feedback or "").strip() or "(none given: re-render and re-check the page)",
        "```",
        "",
        "### Steps",
        "1. Read artifact.json and the current index.html; understand exactly what",
        "   the feedback asks for. Make the SMALLEST change that satisfies it:",
        "   edit artifact.json (keep `\"kind\": \"simple\"`), and regenerate ONLY the images the feedback names",
        "   through `long-form:generate-image` with the skill's invocation and",
        "   the feedback folded into the prompt and `--context`. A new beat",
        "   needs a new image. Never regenerate every image for a one-line note.",
        f"2. Render: `python3 {_SIMPLE_BUILDER} {out_dir}` until it prints OK",
        "   (the previous page is kept under history/ automatically).",
        "3. `kanban_attach` index.html, then `kanban_complete` naming what",
        f"   changed and the page link `{_dashboard_link(out_dir)}`.",
        "",
        "### CRITICAL RULES",
        "- Content only on the page: no presenter instructions, no em dashes.",
        "- Scratch stays out of the folder; strays are swept on completion.",
    ] + yti_generate.selected_style_block())


def build_simple_fix_brief(entry: dict[str, Any], problems: list[str]) -> str:
    out_dir = entry.get("outDir") or ""
    return "\n".join([
        "## MANDATORY: Fix a simple-artifact folder that FAILED its quality gate.",
        "",
        "A worker completed this brief page task, but the gate found:",
        "",
    ] + [f"- {p}" for p in problems[:40]] + [
        "",
        f"**Artifact folder:** {out_dir}",
        f"**Contract:** {_SIMPLE_SKILL_DIR / 'schema.md'}",
        f"**Title:** {entry.get('topic') or ''}",
        f"**Length:** {entry.get('images') or 10} illustrated beats (`target_images` in artifact.json).",
        "",
        "Load `long-form:simple-artifact`. Fix exactly the problems listed:",
        "missing sections or beats get written from the source already in the",
        "folder's artifact.json, missing images get generated through",
        "`long-form:generate-image`, presenter instructions get rewritten as",
        f"content, strays get removed. Then run `python3 {_SIMPLE_BUILDER} {out_dir}`",
        "until it prints OK, `kanban_attach` index.html, and `kanban_complete`.",
    ])


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
    ] + ([f"**Page plan (artifact.json `plan`, nothing outside it):** {json.dumps(entry['plan'])}"]
         if entry.get("plan") else []) + [
        "",
        "Load `long-form:youtube-artifact`. Fix exactly the problems listed:",
        "missing or thin sections get written (with sources fetched), missing",
        "images get generated through `long-form:generate-image`, presenter",
        "instructions get rewritten as content, strays get removed. Then run",
        f"`python3 {_BUILDER} {out_dir}` until it prints OK, `kanban_attach`",
        "index.html, and `kanban_complete`.",
    ])


def out_dir_for_simple(title: str) -> Path:
    return _versioned(yti_paths.workspace_dir() / "youtube" / _today() / "artifacts",
                      _slug(title) + "-brief")


def build_simple_brief(title: str, url: str, content: str, out_dir: Path, images: int = 10,
                       hook: str = "") -> str:
    title = (title or "").strip()
    url = (url or "").strip()
    content = (content or "").strip()
    hook = (hook or "").strip()
    plugin_root = Path(__file__).resolve().parent
    clipped = ""
    if len(content) > SIMPLE_CONTENT_MAX:
        clipped = (f"\n\n(…content clipped at {SIMPLE_CONTENT_MAX} characters; "
                   f"{len(content) - SIMPLE_CONTENT_MAX} more were pasted. Ask for the rest if the page needs it.)")
        content = content[:SIMPLE_CONTENT_MAX]
    head = [
        "## MANDATORY: Build ONE simple-artifact brief page from the source below.",
        "",
        f"**Title:** {title or '(derive one from the content, 1 to 8 words)'}",
        f"**Length:** {images} illustrated beats, one image each: a talk of about {images} minutes.",
        f"The gate holds the page to {images - 1} to {images + 2} images in total.",
        f"**Operator's hook:** {hook or '(none given: write five, strongest first)'}",
        f"**Output directory:** {out_dir}",
        f"**Worked example (style and depth):** {_SIMPLE_SKILL_DIR / 'example' / 'artifact.json'}",
        f"**Contract:** {_SIMPLE_SKILL_DIR / 'schema.md'}",
        "",
        "You do ALL phases yourself in this session: no subtasks, no",
        "delegation. Load `long-form:simple-artifact` and follow it.",
        "",
        "### The source (the whole job: nothing dropped, nothing added from research)",
    ]
    src: list[str] = []
    if url:
        src += [f"- **URL:** {url}",
                "  Fetch it with `web_extract` and read all of it. A login page or a",
                "  failed fetch ends the task with that finding; never invent content."]
    if content:
        src += ["- **Pasted content:**", "", "```text", content + clipped, "```"]
    if not url and not content:
        src += ["(no source given: complete the task with that finding)"]
    tail = [
        "",
        "### Phase 1 to 2 (read, restructure) — from the skill",
        "List every point, example, story, number, name and link in the source;",
        "every one lands on the page. Then write artifact.json in the contract",
        f"with `\"kind\": \"simple\"` and `\"target_images\": {images}` at the top level. Promise, apply and cta",
        f"carry one image each, so the problem and value sections hold about {images - 3}",
        f"beats between them ({images - 4} to {images - 1}). "
        + ("CONSOLIDATE: the source has more points than beats, so group related points into one beat "
           "with up to 8 cue bullets, and keep every example and story with its detail under `story`. "
           "Consolidating means grouping, never cutting: every salient point, number, name, example "
           "and link in the source still lands on the page as a bullet, a story line or a contrast "
           "line. Ten beats is a ten minute talk, not a shorter talk." if images <= 10 else
           "EXPAND: give every framework, rule, step, example and story its own beat with 2 to 5 cue "
           "bullets, keep more of each story's detail in the open bullets, and let the problem section "
           "run 3 to 5 beats. Twenty beats is a twenty minute talk."),
        "Sections:",
        "promise, problem (what the crowd says vs what I say), value (the",
        "teaching in logical order; stories and examples as beats with a one or",
        "two bullet cue and the facts under `story`), apply (2 to 5 easy, fast",
        "steps for an immediate win), summary, cta (always ends with: more",
        "content like this, like and subscribe). Bullets over prose. No",
        "instructions to the presenter: the page carries content only.",
        "Coverage check before you render: re-read the source against artifact.json",
        "and move anything missing onto the page. The completion summary lists",
        "any point you could not place; the expected list is empty.",
        "",
        "### Phase 2b (packaging) — from the skill",
        "The page opens with a collapsed `packaging` block: 5 thumbnails, 5 titles,",
        "5 hooks, all making the SAME promise the page makes (the promise section's",
        "key line, copied verbatim into `packaging.promise`). Steps:",
        f"1. `python3 {plugin_root / 'yti_packaging.py'} examples` prints the reference",
        "   set: the 20 winning videos (Supply / Demand top by time-adjusted demand",
        "   when that table is populated, else the Trends tab top by views per",
        "   hour), each with title and thumbnail URL. Fetch the 20 thumbnails into",
        "   your scratch workspace (curl -4) and LOOK at them. Write 3 to 6",
        "   `patterns` you see: word count, accent color, face placement, props,",
        "   title frames (question, number, contrarian). Store the printed JSON as",
        "   `packaging.examples` unchanged.",
        f"2. `python3 {plugin_root / 'yti_packaging.py'} portrait` prints the operator's",
        "   portrait path. Every thumbnail is generated with that file as",
        "   `--input` so the real person appears, plus TWO reference thumbnails",
        "   from the set as `--reference` (maxresdefault.jpg, else hqdefault.jpg,",
        "   fetched with curl -4) so the result looks like the winners: same",
        "   typography treatment, colour grading, contrast and energy, not a plain",
        "   text-on-black. The prompt says the person comes from the first image",
        "   only. Vary the pair across the five. The prompt names the exact words",
        "   (`--expect-text`) and nothing else. Words on a thumbnail: no more than",
        "   5, and 2 or 3 when they carry the point.",
        "   Save as img/thumb-1.jpg to img/thumb-5.jpg. If no portrait prints, stop",
        "   and complete the task saying the operator must upload one (Artifacts",
        "   tab, Upload portrait).",
        f"3. `python3 {plugin_root / 'yti_packaging.py'} formats` prints the winning title",
        "   frames: the research engine's D3 format library (mined frames with",
        "   their hit counts and example titles, plus the seeded templates with",
        "   their psychology). Store it as `packaging.frames` unchanged. Write 20",
        "   titles under 70 characters, each `{text, frame}`: spread them across",
        "   the strongest mined frames first, then the seeded templates, then the",
        "   patterns you saw in the reference set; name the frame on each. Every",
        "   title makes the page's promise.",
        "   Hooks: 5 spoken openers of one or two sentences; when the",
        "   operator gave a hook it is hooks[0] with `given: true`, and the other",
        "   four are alternatives that could beat it.",
        "",
        "### Phase 3 to 5 (images, render, publish) — from the skill",
        f"Images: ONE sketchnote per beat (promise, every problem and value beat,"
        f"apply, cta: {images} on this page, {images - 1} to {images + 2} accepted) through the",
        "`long-form:generate-image` skill, at most six at a time, anchored on the",
        f"selected style baseline `--input \"{yti_generate.selected_baseline_path()}\"`,",
        "with every rendered word in `--expect-text` and the beat's meaning in",
        f"`--context`. Save them under {out_dir}/img/. Look at each one once.",
        "Render with:",
        f"  python3 {_SIMPLE_BUILDER} {out_dir}",
        "and fix every problem it prints until it says OK. A completion",
        "validator re-runs that exact gate; a folder that fails re-opens as a",
        "fix task, so do not complete until it is clean.",
        "Then `kanban_attach` index.html and artifact.json, and `kanban_complete`",
        "with a summary naming the folder, the page link",
        f"`{_dashboard_link(out_dir)}` (Artifacts tab, select index.html for the",
        "preview and Open page), the beat and image counts, and anything from",
        "the source that did not fit.",
        "",
        "### CRITICAL RULES",
        f"- Every output file goes under exactly {out_dir}/ (artifact.json,",
        "  index.html, img/). Scratch, logs and prompts stay in your task",
        "  workspace; the completion sweep deletes strays from the folder.",
        "- Content only on the page: no 'say this', no 'mention the link', no",
        "  'point up' (the validator rejects them). No em dashes.",
        "- No images from anything but generate-image. Never hand-edit index.html.",
    ] + yti_generate.selected_style_block()
    return "\n".join(head + src + tail)


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
        skills = SIMPLE_SKILLS if extra.get("kind") == "simple" else ARTIFACT_SKILLS
        task_id = kb.create_task(
            conn_kb, title=title, body=yti_generate._with_voice(body),
            assignee=resolve_kanban_assignee(), created_by="long-form",
            workspace_kind="scratch", skills=list(skills), priority=10)
    mapping[key] = {"taskId": task_id, "createdAt": _now_iso(), "outDir": extra.get("outDirAbs"),
                    "topic": extra.get("topic"), "kind": extra.get("kind"),
                    "fixRetries": 0, "videoId": extra.get("videoId"), "plan": extra.get("plan"),
                    "images": extra.get("images")}
    _save_map(conn, mapping)
    kick_dispatcher()
    return {"ok": True, "taskId": task_id, "outDir": key}


def create_topic_task(topic: str, pov: str = "", guidance: str = "",
                      source: Optional[dict[str, Any]] = None,
                      out_dir: Optional[Path] = None, video_id: Optional[str] = None,
                      plan: Any = None) -> dict[str, Any]:
    """Artifacts page Artifact ✦ (no source) or Research tab Artifact (with a
    teardown as source and the video's own folder as out_dir). `plan` is the
    section list the operator kept in the modal (None/empty = whole page)."""
    topic = (topic or "").strip()
    if not topic:
        return {"error": "topic is required"}
    try:
        plan = normalize_plan(plan)
    except ValueError as exc:
        return {"error": f"page plan: {exc}"}
    out_dir = out_dir or out_dir_for_topic(topic)
    ws = yti_paths.workspace_dir()
    try:
        key = out_dir.relative_to(ws).as_posix()
    except ValueError:
        return {"error": "output directory must be inside the workspace"}
    conn = yti_store.connect()
    try:
        return _create(conn, key, f"Page: {topic[:70]}",
                       build_topic_brief(topic, pov, guidance, out_dir, source, plan),
                       {"outDirAbs": str(out_dir), "topic": topic, "kind": "topic", "videoId": video_id,
                        "plan": plan})
    except Exception as exc:  # noqa: BLE001
        return {"error": f"could not create the page task: {exc}"}
    finally:
        conn.close()


def create_simple_task(title: str = "", url: str = "", content: str = "",
                       images: int = 10, hook: str = "") -> dict[str, Any]:
    """Artifacts page Simple artifact ✦ button: a brief page from a URL
    and/or pasted content; *images* (10 or 20) sets the page length."""
    title = (title or "").strip()
    url = (url or "").strip()
    content = (content or "").strip()
    if not url and not content:
        return {"error": "a URL or pasted content is required"}
    try:
        images = int(images)
    except (TypeError, ValueError):
        images = 0
    if images not in simple_builder().IMAGE_TARGETS:
        return {"error": "images must be 10 or 20"}
    if url and not re.match(r"^https?://\S+$", url):
        return {"error": "the URL must start with http:// or https://"}
    name = title or (content.strip().splitlines()[0][:60] if content else url.rstrip("/").rsplit("/", 1)[-1][:60]) or "brief"
    out_dir = out_dir_for_simple(name)
    ws = yti_paths.workspace_dir()
    key = out_dir.relative_to(ws).as_posix()
    conn = yti_store.connect()
    try:
        return _create(conn, key, f"Brief page: {(title or name)[:70]}",
                       build_simple_brief(title, url, content, out_dir, images, hook),
                       {"outDirAbs": str(out_dir), "topic": title or name, "kind": "simple", "images": images,
                        "hook": (hook or "").strip() or None})
    except Exception as exc:  # noqa: BLE001
        return {"error": f"could not create the brief page task: {exc}"}
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
        simple = is_simple(root)
        brief = build_simple_iterate_brief(root, feedback) if simple else build_iterate_brief(root, feedback)
        return _create(conn, key, f"Iterate page: {root.name if root.name.startswith('artifact') else root.name}",
                       brief,
                       {"outDirAbs": str(root), "topic": prev.get("topic") or root.name,
                        "kind": "simple-iterate" if simple else "iterate", "videoId": prev.get("videoId")})
    except Exception as exc:  # noqa: BLE001
        return {"error": f"could not create the iterate task: {exc}"}
    finally:
        conn.close()


def rebuild(rel_path: str) -> dict[str, Any]:
    """Deterministic re-render of index.html from artifact.json (no agent)."""
    root = artifact_root(rel_path)
    if root is None:
        return {"error": f"not inside an artifact folder: {rel_path}", "problems": []}
    rep = builder_for(root).build(root)
    if not rep.get("ok"):
        return {"error": "the page does not pass its quality gate", "problems": rep.get("problems") or []}
    ws = yti_paths.workspace_dir()
    return {"ok": True, "page": (root / "index.html").relative_to(ws).as_posix()}


# ---------------------------------------------------------------------------
# States and completion
# ---------------------------------------------------------------------------

def _published(root: Path) -> Optional[dict[str, Any]]:
    try:
        d = json.loads((root / "published.json").read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and d.get("url") else None
    except (OSError, ValueError):
        return None


def states() -> dict[str, dict[str, Any]]:
    out = yti_generate._states_for(ARTIFACT_META_KEY)
    ws = yti_paths.workspace_dir()
    kinds: dict[str, str] = {}
    try:
        conn = yti_store.connect()
        try:
            kinds = {k: str(e.get("kind") or "") for k, e in _load_map(conn).items() if isinstance(e, dict)}
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        pass
    for key, st in out.items():
        page = ws / key / "index.html"
        st["page"] = f"{key}/index.html" if page.exists() else None
        st["published"] = _published(ws / key)
        # the folder's own marker once written; the task's kind while the worker is still on it
        st["simple"] = is_simple(ws / key) or kinds.get(key, "").startswith("simple")
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
                "dir": key, "page": page, "sessionId": st.get("sessionId"), "published": _published(p)}
    # an open task whose folder does not exist yet
    for key, st in all_states.items():
        if st.get("status") == "open" and key.startswith(rel_dir.rstrip("/") + "/"):
            best = {"status": "open", "taskId": st.get("taskId"), "dir": key, "page": None,
                    "sessionId": st.get("sessionId"), "published": None}
    return best


def _sweep(root: Path) -> list[str]:
    removed: list[str] = []
    if not root.is_dir():
        return removed
    keep = {"index.html", "artifact.json", "img", "history", "README.md", "SUMMARY.md", "published.json"}
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


def _plan_mismatch(root: Path, plan: Optional[list[str]]) -> list[str]:
    """The task carried a section plan: artifact.json must declare the same
    one, so the gate enforced it and the page is what the operator asked for."""
    if not plan:
        return []
    try:
        doc = json.loads((root / "artifact.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []  # the builder already reported the missing/invalid file
    got = doc.get("plan") if isinstance(doc, dict) else None
    if got != plan:
        return [f"plan: artifact.json must declare exactly {json.dumps(plan)} (the sections the operator chose); "
                f"found {json.dumps(got)}"]
    return []


def _images_mismatch(root: Path, images: Optional[int]) -> list[str]:
    """A simple task carried a length (10 or 20 images): artifact.json must
    declare the same target so the gate enforced it."""
    if not images:
        return []
    try:
        doc = json.loads((root / "artifact.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    got = doc.get("target_images") if isinstance(doc, dict) else None
    if got != images:
        return [f"target_images: artifact.json must declare {images} (the length the operator chose); found {got}"]
    return []


def handle_completion(conn, kanban_task_id: str) -> Optional[dict[str, Any]]:
    """kanban_task_completed hook: sweep strays, re-run the skill's gate,
    open a fix task (max ARTIFACT_MAX_RETRIES) when the page does not pass."""
    mapping = _load_map(conn)
    for key, entry in mapping.items():
        if not isinstance(entry, dict) or entry.get("taskId") != kanban_task_id:
            continue
        root = Path(entry.get("outDir") or (yti_paths.workspace_dir() / key))
        removed = _sweep(root)
        simple = str(entry.get("kind") or "").startswith("simple")
        rep = builder_for(root, "simple" if simple else None).build(root)  # renders when clean
        problems = list(rep.get("problems") or [])
        if simple:
            problems += _images_mismatch(root, entry.get("images"))
        else:
            problems += _plan_mismatch(root, entry.get("plan"))
        if not problems:
            return {"ok": True, "clean": True, "key": key, "swept": len(removed), "page": rep.get("page")}
        retries = int(entry.get("fixRetries") or 0)
        if retries >= ARTIFACT_MAX_RETRIES:
            return {"ok": False, "exhausted": True, "key": key, "problems": problems, "swept": len(removed)}
        kb = _kanban()
        if kb is None:
            return {"ok": False, "key": key, "problems": problems}
        fix_body = build_simple_fix_brief(entry, problems) if simple else build_fix_brief(entry, problems)
        with kb.connect_closing() as conn_kb:
            fix_id = kb.create_task(
                conn_kb, title=f"Fix page: {root.name}",
                body=yti_generate._with_voice(fix_body),
                assignee=resolve_kanban_assignee(), created_by="long-form",
                workspace_kind="scratch", skills=list(SIMPLE_SKILLS if simple else ARTIFACT_SKILLS), priority=10)
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
        mod = builder_for(p.parent)
        return {"html": mod.inline_images(p.read_text(encoding="utf-8"), p.parent), "media": media}
    return {"path": str(p), "media": media}
