"""Iterate (script rewrite) and Generate-from-topic kanban task creation."""
from pathlib import Path

import pytest

import yti_generate
import yti_paths


class FakeKanban:
    """Stub of hermes_cli.kanban_db with the surface yti_generate uses."""

    def __init__(self):
        self.tasks: dict[str, dict] = {}
        self.counter = 0
        self.created: list[dict] = []

    class _Ctx:
        def __init__(self, outer):
            self.outer = outer

        def __enter__(self):
            return self.outer

        def __exit__(self, *exc):
            return False

    def connect_closing(self):
        return FakeKanban._Ctx(self)

    def create_task(self, conn, *, title, body, created_by, workspace_kind,
                    skills, assignee=None, priority=None):
        assert assignee, "plugin tasks must be born assigned"
        self.counter += 1
        tid = f"t_gen{self.counter}"
        record = {"id": tid, "title": title, "body": body, "status": "ready",
                  "created_by": created_by, "skills": skills,
                  "assignee": assignee, "priority": priority}
        self.tasks[tid] = record
        self.created.append(record)
        return tid

    def get_task(self, conn, task_id):
        rec = self.tasks.get(task_id)
        if rec is None:
            return None
        return type("T", (), rec)()


@pytest.fixture()
def gen_kanban(monkeypatch):
    fake = FakeKanban()
    monkeypatch.setattr(yti_generate, "_kanban", lambda: fake)
    monkeypatch.setattr(yti_generate, "kick_dispatcher", lambda: None)
    monkeypatch.setattr(yti_generate, "resolve_kanban_assignee",
                        lambda: "default")
    return fake


def _mk_script(rel: str) -> Path:
    ws = yti_paths.workspace_dir()
    p = ws / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# script\n")
    return p


def test_concept_for_script_variant_mapping():
    f = yti_generate._concept_for_script
    assert f(Path("/x/script-outline.md")).name == "concepts.md"
    assert f(Path("/x/script-outline-hot-take.md")).name == "concepts-hot-take.md"
    assert f(Path("/x/script-outline-contrarian.md")).name == "concepts-contrarian.md"


def test_iterate_requires_existing_markdown(conn, tmp_home, gen_kanban):
    assert "error" in yti_generate.create_iterate_task("nope/missing.md", "x")
    ws = yti_paths.workspace_dir()
    (ws / "a.txt").write_text("hi")
    assert "error" in yti_generate.create_iterate_task("a.txt", "x")


def test_iterate_creates_steered_task(conn, tmp_home, gen_kanban):
    rel = "youtube/2026-08-12/recommended/topic-a/script-outline-hot-take.md"
    script = _mk_script(rel)
    result = yti_generate.create_iterate_task(rel, "make beat 3 meatier")
    assert result["ok"]
    body = gen_kanban.created[0]["body"]
    assert "make beat 3 meatier" in body
    assert str(script) in body
    assert "concepts-hot-take.md" in body
    assert "Phase 4b" in body
    assert gen_kanban.created[0]["skills"] == list(yti_generate.ITERATE_SKILLS)


def test_iterate_dedupes_open_task(conn, tmp_home, gen_kanban):
    rel = "youtube/2026-08-12/recommended/topic-a/script-outline.md"
    _mk_script(rel)
    first = yti_generate.create_iterate_task(rel, "v1")
    second = yti_generate.create_iterate_task(rel, "v2")
    assert second["taskId"] == first["taskId"]
    assert second.get("already") is True
    assert len(gen_kanban.created) == 1
    # closed task -> a new one is created
    gen_kanban.tasks[first["taskId"]]["status"] = "done"
    third = yti_generate.create_iterate_task(rel, "v3")
    assert third["taskId"] != first["taskId"]


def test_topic_requires_topic(conn, tmp_home, gen_kanban):
    assert "error" in yti_generate.create_topic_task("   ")


def test_topic_creates_insights_grounded_task(conn, tmp_home, gen_kanban):
    result = yti_generate.create_topic_task(
        "Securing AI coding agents", "for vCISOs, avoid vendor pitches")
    assert result["ok"]
    out_dir = Path(result["outDir"])
    assert out_dir.name == "securing-ai-coding-agents"
    assert out_dir.parent.name == "recommended"
    body = gen_kanban.created[0]["body"]
    assert "Mode D" in body
    assert "yt_search_insights" in body
    assert "for vCISOs, avoid vendor pitches" in body
    assert "script-outline-contrarian.md" in body
    assert gen_kanban.created[0]["skills"] == list(yti_generate.TOPIC_SKILLS)


def test_topic_dedupes_open_task(conn, tmp_home, gen_kanban):
    first = yti_generate.create_topic_task("Same Topic")
    second = yti_generate.create_topic_task("Same Topic")
    assert second["taskId"] == first["taskId"]
    assert len(gen_kanban.created) == 1


def test_states_reflect_task_lifecycle(conn, tmp_home, gen_kanban, monkeypatch):
    monkeypatch.setattr(yti_generate, "_find_worker_session", lambda tid: None)
    rel = "youtube/2026-08-12/recommended/topic-b/script-outline.md"
    _mk_script(rel)
    res = yti_generate.create_iterate_task(rel, "steer")
    states = yti_generate.iterate_states()
    assert states[rel]["status"] == "open"
    gen_kanban.tasks[res["taskId"]]["status"] = "done"
    assert yti_generate.iterate_states()[rel]["status"] == "done"
    tres = yti_generate.create_topic_task("Another Topic")
    tstates = yti_generate.topic_states()
    key = next(iter(tstates))
    assert tstates[key]["taskId"] == tres["taskId"]
    assert tstates[key]["status"] == "open"


# ---------------------------------------------------------------------------
# Pipeline on-demand trigger ("3 More" button)
# ---------------------------------------------------------------------------

def _load_plugin_api(monkeypatch, jobs_mod, execs_mod):
    import importlib.util as ilu
    import sys
    import types
    cron_pkg = types.ModuleType("cron")
    cron_pkg.jobs = jobs_mod
    cron_pkg.executions = execs_mod
    monkeypatch.setitem(sys.modules, "cron", cron_pkg)
    monkeypatch.setitem(sys.modules, "cron.jobs", jobs_mod)
    monkeypatch.setitem(sys.modules, "cron.executions", execs_mod)
    root = Path(__file__).resolve().parent.parent
    spec = ilu.spec_from_file_location(
        "pa_pipeline_test", str(root / "dashboard" / "plugin_api.py"))
    mod = ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _cron_stubs(status="completed", trigger_ok=True):
    import types
    jobs_mod = types.ModuleType("cron.jobs")
    triggered = []
    jobs_mod.resolve_job_ref = lambda ref: (
        {"id": "job1", "name": ref} if ref == "youtube-content-pipeline" else None)
    jobs_mod.trigger_job = lambda jid: (
        triggered.append(jid) or {"next_run_at": "now"}) if trigger_ok else None
    jobs_mod.update_job = lambda jid, patch: None
    jobs_mod._triggered = triggered
    execs_mod = types.ModuleType("cron.executions")
    execs_mod.list_executions = lambda job_id, limit: (
        [{"status": status, "started_at": "s", "finished_at": "f"}]
        if status else [])
    return jobs_mod, execs_mod


def test_pipeline_run_triggers_job(conn, tmp_home, monkeypatch):
    jobs_mod, execs_mod = _cron_stubs(status="completed")
    api = _load_plugin_api(monkeypatch, jobs_mod, execs_mod)
    result = api.post_pipeline_run()
    assert result["ok"] is True
    assert jobs_mod._triggered == ["job1"]


def test_pipeline_run_skips_when_already_running(conn, tmp_home, monkeypatch):
    jobs_mod, execs_mod = _cron_stubs(status="running")
    api = _load_plugin_api(monkeypatch, jobs_mod, execs_mod)
    result = api.post_pipeline_run()
    assert result.get("alreadyRunning") is True
    assert jobs_mod._triggered == []


def test_pipeline_state_shape(conn, tmp_home, monkeypatch):
    jobs_mod, execs_mod = _cron_stubs(status="running")
    api = _load_plugin_api(monkeypatch, jobs_mod, execs_mod)
    state = api.get_pipeline_state()
    assert state["available"] is True
    assert state["running"] is True
    jobs_mod.resolve_job_ref = lambda ref: None
    state = api.get_pipeline_state()
    assert state == {"available": False, "running": False}


# ---------------------------------------------------------------------------
# Script-completion lint gate
# ---------------------------------------------------------------------------

BAD_SCRIPT = """## Beat 1: X (0:00-1:30)
- Terse fragment one.
- Terse fragment two.
- **-> HOOK INTO NEXT**: Fragment.
"""


def test_script_completion_opens_fix_task(conn, tmp_home, gen_kanban):
    result = yti_generate.create_topic_task("Lint Gate Topic")
    tid = result["taskId"]
    out_dir = Path(result["outDir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in ("script-outline.md", "script-outline-hot-take.md",
                 "script-outline-contrarian.md"):
        (out_dir / name).write_text(BAD_SCRIPT)
    gen_kanban.tasks[tid]["status"] = "done"
    r = yti_generate.handle_script_completion(conn, tid)
    assert r and r.get("retry") == 1 and r.get("fixTaskId")
    fix = gen_kanban.tasks[r["fixTaskId"]]
    assert "yt_lint_script" in fix["body"]
    assert "thin_beat" in fix["body"] or "fragment" in fix["body"]
    # second failure -> retry 2; third -> exhausted
    gen_kanban.tasks[r["fixTaskId"]]["status"] = "done"
    r2 = yti_generate.handle_script_completion(conn, r["fixTaskId"])
    assert r2 and r2.get("retry") == 2
    gen_kanban.tasks[r2["fixTaskId"]]["status"] = "done"
    r3 = yti_generate.handle_script_completion(conn, r2["fixTaskId"])
    assert r3 and r3.get("exhausted")


def test_script_completion_clean_passes(conn, tmp_home, gen_kanban):
    good = (
        "## Beat 1: X (0:00-1:00)\n"
        "- This opening line is a full conversational sentence with enough "
        "words to sound like a person talking to a smart friend on camera.\n"
        "- And this second line continues that exact thought with more "
        "substance, a concrete number like forty percent, and natural "
        "speech rhythm carrying it forward.\n"
        "- So here's the payoff line that lands the whole beat with a "
        "concrete claim the viewer can repeat to someone else tomorrow.\n"
        "- Which is why the last stretch of this beat keeps talking through "
        "the consequence, because the word budget for a full minute of "
        "speech needs roughly one hundred and fifty words of real talk.\n"
        "- That's also the reason we keep adding complete sentences here, "
        "so the mechanical check sees a beat that genuinely fills its "
        "claimed sixty seconds of screen time without any padding words.\n"
        "- And to be honest, hitting that number with substance is exactly "
        "what separates a script you can record from a list of headlines "
        "nobody could ever read aloud.\n"
        "- **-> HOOK INTO NEXT**: So next, let me show you the one rule "
        "that makes this automatic every single time.\n"
        "- **Visual**: diagram\n")
    result = yti_generate.create_topic_task("Lint Gate Clean")
    tid = result["taskId"]
    out_dir = Path(result["outDir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in ("script-outline.md", "script-outline-hot-take.md",
                 "script-outline-contrarian.md"):
        (out_dir / name).write_text(good)
    gen_kanban.tasks[tid]["status"] = "done"
    r = yti_generate.handle_script_completion(conn, tid)
    assert r and r.get("clean") is True


def test_script_completion_ignores_unknown_tasks(conn, tmp_home, gen_kanban):
    assert yti_generate.handle_script_completion(conn, "t_unknown") is None


# ---- outline -> presentation ------------------------------------------------

def test_create_present_task_creates_assigned_task(tmp_home, gen_kanban):
    result = yti_generate.create_present_task(
        "Vibe Audit Trends", "1. Problem\n    1. Auth\n2. Fix")
    assert result.get("ok"), result
    rec = gen_kanban.created[-1]
    assert rec["title"] == "Presentation: Vibe Audit Trends"
    assert rec["assignee"] == "default"
    assert rec["skills"] == list(yti_generate.PRESENT_SKILLS)
    assert "youtube-insights:outline-to-presentation" in rec["skills"]
    # the outline rides in the brief VERBATIM, and images are forbidden
    assert "1. Problem" in rec["body"] and "    1. Auth" in rec["body"]
    assert "Do NOT generate images" in rec["body"]
    assert "script-outline.md" in rec["body"]
    assert "/presentations/" in result["outDir"]


def test_create_present_task_requires_topic_and_outline(tmp_home, gen_kanban):
    assert yti_generate.create_present_task("", "1. x")["error"] == "topic required"
    assert yti_generate.create_present_task("T", " ")["error"] == "outline required"


def test_create_present_task_dedupes_open_task(tmp_home, gen_kanban):
    first = yti_generate.create_present_task("Same Talk", "1. a")
    again = yti_generate.create_present_task("Same Talk", "1. a")
    assert again.get("already") is True
    assert again["taskId"] == first["taskId"]
    assert len(gen_kanban.created) == 1


def test_present_states_shape(tmp_home, gen_kanban):
    yti_generate.create_present_task("Stateful Talk", "1. a")
    states = yti_generate.present_states()
    assert len(states) == 1
    entry = next(iter(states.values()))
    assert entry["status"] in ("open", "stale", "done")


def test_outline_slide_map_levels():
    outline = ("1. Problem\n"
               "    1. Vibe app\n"
               "    2. Findings\n"
               "        1. Auth\n"
               "        2. Input\n"
               "2. Fix\n"
               "    1. Awareness\n")
    m = yti_generate._outline_slide_map(outline)
    # top-level -> dividers, second-level -> slides, third-level folds in
    assert m == ["[divider] Problem", "Vibe app", "Findings",
                 "[divider] Fix", "Awareness"]
    assert yti_generate._outline_slide_map("free text, no numbers") == []


def test_present_brief_carries_beat_contract(tmp_home, gen_kanban):
    r = yti_generate.create_present_task(
        "Contract Talk", "1. A\n    1. B\n    2. C")
    body = gen_kanban.created[-1]["body"]
    assert "AT LEAST" in body and "3" in body
    assert "NO helper scripts" in body


def test_present_completion_bounces_short_script(conn, tmp_home, gen_kanban):
    r = yti_generate.create_present_task(
        "Short Deck", "1. A\n    1. B\n    2. C\n    3. D")
    tid = r["taskId"]
    out = Path(r["outDir"])
    out.mkdir(parents=True, exist_ok=True)
    (out / "concepts.md").write_text("**PRESENTATION MODE**")
    # only 2 beats where the outline requires 5 (1 divider + 3 slides... 4)
    (out / "script-outline.md").write_text(
        "# T\n## Hook (0:00-1:00)\n- **Visual**: x\n"
        "## Beat 1: A (1:00-2:00)\n- line\n- **Visual**: x\n"
        "## Beat 2: B (2:00-3:00)\n- line\n- **Visual**: x\n")
    gen_kanban.tasks[tid]["status"] = "done"
    res = yti_generate.handle_present_completion(conn, tid)
    assert res and res.get("fixTask"), res
    fix = gen_kanban.created[-1]
    assert fix["title"].startswith("Fix presentation:")
    assert "truncated" in fix["body"] or "only 2" in fix["body"]
    # retry bookkeeping: the fix task replaces the original in state
    states = yti_generate.present_states()
    assert next(iter(states.values()))["taskId"] == res["fixTask"]


def test_present_completion_clean_passes_no_chain(conn, tmp_home, gen_kanban):
    r = yti_generate.create_present_task("Full Deck", "1. A\n    1. B")
    tid = r["taskId"]
    out = Path(r["outDir"])
    out.mkdir(parents=True, exist_ok=True)
    (out / "concepts.md").write_text("**PRESENTATION MODE**")
    (out / "script-outline.md").write_text(
        "# T\n## Hook (0:00-1:00)\n- **Visual**: x\n"
        "## Beat 1: A (1:00-2:00)\n- line\n- **Visual**: x\n"
        "## Beat 2: B (2:00-3:00)\n- line\n- **Visual**: x\n")
    gen_kanban.tasks[tid]["status"] = "done"
    n_before = len(gen_kanban.created)
    res = yti_generate.handle_present_completion(conn, tid)
    assert res and res.get("clean") is True
    # review pause is deliberate: NO produce task auto-created
    assert len(gen_kanban.created) == n_before


def test_present_completion_retry_cap(conn, tmp_home, gen_kanban):
    r = yti_generate.create_present_task("Cap Deck", "1. A\n    1. B")
    tid = r["taskId"]
    Path(r["outDir"]).mkdir(parents=True, exist_ok=True)  # no files at all
    gen_kanban.tasks[tid]["status"] = "done"
    for expect_fix in (True, True, False):
        res = yti_generate.handle_present_completion(conn, tid)
        if expect_fix:
            assert res.get("fixTask"), res
            tid = res["fixTask"]
            gen_kanban.tasks[tid]["status"] = "done"
        else:
            assert res.get("exhausted") is True


def test_produce_completion_sweeps_non_images(conn, tmp_home, gen_kanban):
    rel = "youtube/2026-08-31/presentations/sweep-deck/script-outline.md"
    script = _mk_script(rel)
    r = yti_generate.create_produce_task(rel)
    tid = r["taskId"]
    assets = script.parent / "assets"
    assets.mkdir()
    (assets / "01-hook.jpg").write_bytes(b"jpg")
    (assets / "thumb-a-x.png").write_bytes(b"png")
    (assets / "_gen_beats.sh").write_text("#!/bin/bash")
    (assets / "_log01.txt").write_text("log")
    (assets / "verify-pdf.py").write_text("print()")
    gen_kanban.tasks[tid]["status"] = "done"
    (script.parent / "verify-pdf.py").write_text("print()")
    res = yti_generate.handle_produce_completion(conn, tid)
    assert res and res["swept"] == 4, res
    assert not (script.parent / "verify-pdf.py").exists()
    assert (script.parent / "script-outline.md").exists()
    left = sorted(p.name for p in assets.iterdir())
    assert left == ["01-hook.jpg", "thumb-a-x.png"]
    # brief carries the hygiene + portrait conventions
    body = gen_kanban.created[-1]["body"]
    assert "ONLY generated image files" in body
    assert "portrait-black-shirt.jpg" in body


# ---- targeted regeneration --------------------------------------------------

def test_create_regen_task_image(tmp_home, gen_kanban):
    rel = "youtube/2026-08-31/presentations/deck/assets/05-critical-20260831.jpg"
    ws = yti_paths.workspace_dir()
    p = ws / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"jpg")
    r = yti_generate.create_regen_task(rel, "AUTHENTICATION is misspelled")
    assert r.get("ok"), r
    rec = gen_kanban.created[-1]
    assert rec["title"].startswith("Regenerate image:")
    assert "AUTHENTICATION is misspelled" in rec["body"]
    assert "ONE image only" in rec["body"]
    assert "rebuild" in rec["body"].lower() and "960, 540" in rec["body"]
    # dedupe while open
    again = yti_generate.create_regen_task(rel, "same")
    assert again.get("already") is True


def test_create_regen_task_pdf_and_rejects_other(tmp_home, gen_kanban):
    ws = yti_paths.workspace_dir()
    pdf = ws / "youtube/2026-08-31/presentations/deck/script-outline.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF")
    r = yti_generate.create_regen_task(str(pdf.relative_to(ws)))
    assert r.get("ok"), r
    assert gen_kanban.created[-1]["title"].startswith("Rebuild PDF:")
    assert "Generate NO images" in gen_kanban.created[-1]["body"]
    md = ws / "youtube/2026-08-31/presentations/deck/notes.md"
    md.write_text("x")
    bad = yti_generate.create_regen_task(str(md.relative_to(ws)))
    assert "error" in bad


def test_regen_completion_sweeps(conn, tmp_home, gen_kanban):
    rel = "youtube/2026-08-31/presentations/deck2/assets/03-x-20260831.jpg"
    ws = yti_paths.workspace_dir()
    p = ws / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"jpg")
    r = yti_generate.create_regen_task(rel, "fix")
    tid = r["taskId"]
    (p.parent / "_scratch.sh").write_text("x")
    (p.parent.parent / "helper.py").write_text("x")
    gen_kanban.tasks[tid]["status"] = "done"
    res = yti_generate.handle_produce_completion(conn, tid)
    assert res and res["swept"] == 2, res
    assert p.exists()


# ---- image style selection --------------------------------------------------

def test_style_catalog_and_selection(tmp_home, gen_kanban):
    cat = yti_generate.style_catalog()
    ids = [s["id"] for s in cat["styles"]]
    assert "00-default-whiteboard" in ids
    assert len(ids) >= 21  # default + 20 bundled styles
    assert cat["selected"] == "00-default-whiteboard"
    assert yti_generate.select_style("14-chalkboard")["ok"]
    assert yti_generate.style_catalog()["selected"] == "14-chalkboard"
    assert "error" in yti_generate.select_style("nope")
    # brief carries the selected baseline path
    from pathlib import Path as _P
    rel = "youtube/2026-08-31/presentations/styled/script-outline.md"
    _mk_script(rel)
    yti_generate.create_produce_task(rel)
    assert "14-chalkboard" in gen_kanban.created[-1]["body"]


def test_custom_style_upload_and_precedence(tmp_home, gen_kanban):
    assert "error" in yti_generate.save_custom_style(b"", "png")
    assert "error" in yti_generate.save_custom_style(b"x", "exe")
    r = yti_generate.save_custom_style(b"imgbytes", "jpg")
    assert r.get("ok") and r["selected"] == "custom"
    p = yti_generate.selected_baseline_path()
    assert p.name == "custom-style.jpg" and p.read_bytes() == b"imgbytes"
    # replacing swaps extension cleanly
    yti_generate.save_custom_style(b"png2", "png")
    assert yti_generate.selected_baseline_path().name == "custom-style.png"
    # falls back to default when selection points at a removed file
    yti_generate.selected_baseline_path().unlink()
    assert yti_generate.selected_baseline_path().name == "00-default-whiteboard.png"


# ---- voice projection -------------------------------------------------------

def test_voice_fallback_without_honcho(tmp_home, gen_kanban):
    import yti_voice
    assert yti_voice.honcho_config() is None
    lines = "\n".join(yti_voice.voice_brief_lines())
    assert "SOUL.md" in lines and "USER.md" in lines
    assert "No recorded-voice profile" in lines
    # every script-writing brief carries the VOICE block either way
    r = yti_generate.create_present_task("Voice Talk", "1. A\n    1. B")
    body = gen_kanban.created[-1]["body"]
    assert "### VOICE" in body and "SOUL.md" in body


def test_voice_profile_lines_when_present(tmp_home, gen_kanban):
    import yti_voice
    yti_voice._write_profile([
        "So here's the thing about auth, and I say this in every audit "
        "I run: nobody budgets for the boring parts.",
        "Look, the framework will happily let you ship the happy path "
        "and that is exactly the trap I keep seeing.",
    ])
    lines = "\n".join(yti_voice.voice_brief_lines())
    assert "VERBATIM samples" in lines
    assert "HARD PROVENANCE RULE" in lines
    text = yti_voice.profile_path().read_text()
    assert "boring parts" in text and text.startswith("# Voice Profile")


def test_voice_profile_char_budget(tmp_home):
    import yti_voice
    yti_voice._write_profile(["old " * 50] * 200 + ["NEWEST sample " * 5])
    text = yti_voice.profile_path().read_text()
    assert len(text) < yti_voice.MAX_PROFILE_CHARS + 1000
    assert "NEWEST sample" in text  # newest wins the budget


# ---- produce set versioning -------------------------------------------------

def test_first_produce_targets_set_one(tmp_home, gen_kanban):
    rel = "youtube/2026-08-31/presentations/v-deck/script-outline.md"
    _mk_script(rel)
    yti_generate.create_produce_task(rel)
    body = gen_kanban.created[-1]["body"]
    assert "assets/1/" in body and "1.script-outline.pdf" in body
    assert "(set" not in gen_kanban.created[-1]["title"]


def test_reproduce_migrates_legacy_and_targets_set_two(tmp_home, gen_kanban):
    rel = "youtube/2026-08-31/presentations/v2-deck/script-outline.md"
    script = _mk_script(rel)
    assets = script.parent / "assets"
    assets.mkdir()
    (assets / "01-hook.jpg").write_bytes(b"a")
    (assets / "thumb-a-x.jpg").write_bytes(b"b")
    (script.parent / "script-outline.pdf").write_bytes(b"%PDF")
    yti_generate.create_produce_task(rel)
    # legacy set migrated to assets/1/, old pdf renamed 1.<stem>.pdf
    assert (assets / "1" / "01-hook.jpg").exists()
    assert (assets / "1" / "thumb-a-x.jpg").exists()
    assert not (script.parent / "script-outline.pdf").exists()
    assert (script.parent / "1.script-outline.pdf").exists()
    body = gen_kanban.created[-1]["body"]
    assert "assets/2/" in body and "2.script-outline.pdf" in body
    assert "(set 2)" in gen_kanban.created[-1]["title"]


def test_reproduce_numbered_targets_next(tmp_home, gen_kanban):
    rel = "youtube/2026-08-31/presentations/v3-deck/script-outline.md"
    script = _mk_script(rel)
    for n in ("1", "2"):
        d = script.parent / "assets" / n
        d.mkdir(parents=True)
        (d / "x.jpg").write_bytes(b"i")
    yti_generate.create_produce_task(rel)
    body = gen_kanban.created[-1]["body"]
    assert "assets/3/" in body and "3.script-outline.pdf" in body


def test_versioned_pdf_rebuild_scopes_to_its_set(tmp_home, gen_kanban):
    ws = yti_paths.workspace_dir()
    pdf = ws / "youtube/2026-08-31/presentations/v4-deck/2.script-outline.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF")
    yti_generate.create_regen_task(str(pdf.relative_to(ws)))
    body = gen_kanban.created[-1]["body"]
    assert "assets/2/" in body and "ONLY" in body


def test_style_language_injected_into_briefs(tmp_home, gen_kanban):
    yti_generate.select_style("14-chalkboard")
    rel = "youtube/2026-08-31/presentations/lang-deck/script-outline.md"
    _mk_script(rel)
    yti_generate.create_produce_task(rel)
    body = gen_kanban.created[-1]["body"]
    assert "STYLE LANGUAGE" in body and "14-chalkboard" in body
    assert "chalk" in body.lower()  # the actual vocabulary rides along
    # custom style falls back to study-the-image guidance
    yti_generate.save_custom_style(b"img", "png")
    ws = yti_paths.workspace_dir()
    img = ws / "youtube/2026-08-31/presentations/lang-deck/assets/1/01-x.jpg"
    img.parent.mkdir(parents=True, exist_ok=True)
    img.write_bytes(b"j")
    yti_generate.create_regen_task(str(img.relative_to(ws)), "fix")
    body = gen_kanban.created[-1]["body"]
    assert "STYLE LANGUAGE" in body and "STUDYING the baseline image" in body
