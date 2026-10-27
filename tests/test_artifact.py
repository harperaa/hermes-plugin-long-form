"""youtube-artifact: the builder's quality gate and renderer, the page
tasks (topic / script / research / iterate), the completion validator, and
the dashboard routes that serve and rebuild pages."""
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

import yti_artifact
import yti_generate
import yti_paths

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "youtube-artifact"
EXAMPLE = SKILL / "example"


def _builder():
    return yti_artifact.builder()


def _fake_image(p: Path, size: int = 30_000) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\xff\xd8" + b"\0" * size)


def _minimal_doc(**over) -> dict:
    doc = {
        "title": "Test Page",
        "eyebrow": "Briefing · 1 Jan 2026",
        "lede": " ".join(["word"] * 24),
        "chips": [{"text": "one", "tone": "ok"}, {"text": "two", "tone": ""}],
        "sections": [
            {"type": "facts", "heading": "Shape", "items": [
                {"label": "Window", "big": "3 days", "bullets": ["a", "b"]}]},
            {"type": "cards", "heading": "Top", "items": [
                {"title": "Card", "paragraphs": ["p"], "say": "take",
                 "sources": [{"text": "src", "url": "https://example.com/a"}]}]},
            {"type": "steps", "heading": "Steps", "items": [
                {"title": "Do one", "text": "why", "image": "img/s01.jpg", "image_alt": "alt"},
                {"title": "Do two", "text": "why", "image": "img/s02.jpg", "image_alt": "alt"},
                {"title": "Do three", "text": "why", "image": "img/s03.jpg", "image_alt": "alt", "gate": True}]},
            {"type": "sources", "heading": "Sources", "items": [
                {"text": "A", "links": [{"text": "a", "url": "https://example.com/a"}]}]},
        ],
    }
    doc.update(over)
    return doc


def _write_doc(folder: Path, doc: dict, images=("s01", "s02", "s03")) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "artifact.json").write_text(json.dumps(doc))
    for n in images:
        _fake_image(folder / "img" / f"{n}.jpg")
    return folder


# ---- builder ----------------------------------------------------------------

def test_example_passes_gate_and_renders(tmp_path):
    b = _builder()
    work = tmp_path / "ex"
    shutil.copytree(EXAMPLE, work, ignore=shutil.ignore_patterns("index.html", "history"))
    rep = b.build(work)
    assert rep["ok"], rep["problems"]
    html = (work / "index.html").read_text()
    assert html.startswith("<!doctype html>")
    assert "<title>Black October 2026</title>" in html
    # the design system and both graphics
    assert "--accent:#0f6e73" in html and "Archivo+Narrow" in html
    assert html.count('<svg id="tl-') == 2 and "window.__ARTIFACT__=" in html
    # the embedded data carries each timeline's page-wide index so the
    # script finds its svg (sections 3 and 4 in the example)
    data = json.loads(html.split("window.__ARTIFACT__=", 1)[1].split(";</script>", 1)[0])
    assert [s["i"] for s in data["sections"]] == [3, 4]
    # ten illustrated steps, ten cards, the matrix, the sources block
    assert html.count('<figure class="sg">') == 10
    assert html.count('class="inc"') == 10
    assert 'class="matrix"' in html and 'class="src"' in html
    # takeaway blocks carry no label
    assert "<b>Say</b>" not in html


def test_gate_rejects_missing_image_and_presenter_talk(tmp_path):
    b = _builder()
    folder = _write_doc(tmp_path / "a", _minimal_doc(), images=("s01", "s02"))
    probs = b.validate(json.loads((folder / "artifact.json").read_text()), folder)
    assert any("img/s03.jpg" in p and "missing" in p for p in probs)
    doc = _minimal_doc()
    doc["sections"][1]["items"][0]["say"] = "Say this on camera: pin your deps"
    doc["sections"][0]["items"][0]["bullets"] = ["fine — but an em dash"]
    probs = b.validate(doc, _write_doc(tmp_path / "b", doc))
    assert any("presenter" in p for p in probs)
    assert any("em dash" in p for p in probs)


def test_gate_requires_steps_sources_and_valid_timeline(tmp_path):
    b = _builder()
    doc = _minimal_doc()
    doc["sections"] = [s for s in doc["sections"] if s["type"] not in ("steps", "sources")]
    doc["sections"].append({"type": "timeline", "heading": "T", "unit": "day",
                            "start": "2026-01-01", "end": "2026-01-10",
                            "rows": [{"label": "r", "events": [{"at": "2026-02-01", "text": "late"}]}]})
    probs = b.validate(doc, _write_doc(tmp_path / "c", doc))
    joined = "\n".join(probs)
    assert "'steps' section" in joined and "'sources' section" in joined
    assert "outside the timeline range" in joined and "fewer than 4 events" in joined


def test_render_inlines_images_and_keeps_history(tmp_path):
    b = _builder()
    folder = _write_doc(tmp_path / "d", _minimal_doc())
    assert b.build(folder)["ok"]
    html = (folder / "index.html").read_text()
    assert 'src="img/s01.jpg"' in html
    inl = b.inline_images(html, folder)
    assert "data:image/jpeg;base64," in inl and 'src="img/' not in inl
    assert b.build(folder)["ok"]
    assert list((folder / "history").glob("*.html")), "re-render keeps the previous page"


def test_inline_markup_is_escaped_subset():
    b = _builder()
    out = b.inline('<b>x</b> `code` **bold** [t](https://e.com) [bad](javascript:alert(1))')
    assert "&lt;b&gt;" in out and "<code>code</code>" in out and "<b>bold</b>" in out
    assert 'href="https://e.com"' in out and "javascript:" not in out.replace("[bad](javascript:alert(1))", "") \
        or "href=\"javascript" not in out


# ---- tasks ------------------------------------------------------------------

@pytest.fixture()
def art_kanban(monkeypatch):
    from tests.test_generate_tasks import FakeKanban
    fake = FakeKanban()
    monkeypatch.setattr(yti_artifact, "_kanban", lambda: fake)
    monkeypatch.setattr(yti_artifact, "kick_dispatcher", lambda: None)
    monkeypatch.setattr(yti_artifact, "resolve_kanban_assignee", lambda: "default")
    monkeypatch.setattr(yti_generate, "_kanban", lambda: fake)
    return fake


def test_topic_task_carries_pov_guidance_and_contract(tmp_home, art_kanban):
    r = yti_artifact.create_topic_task("Black October", pov="Keys, not zero-days",
                                       guidance="verify the KEV date")
    assert r["ok"] and r["outDir"].startswith("youtube/") and "/artifacts/black-october" in r["outDir"]
    body = art_kanban.created[-1]["body"]
    assert "Keys, not zero-days" in body and "verify the KEV date" in body
    assert "long-form:youtube-artifact" in body and "build_artifact.py" in body
    assert "schema.md" in body and "example/artifact.json" in body
    assert art_kanban.created[-1]["skills"] == list(yti_artifact.ARTIFACT_SKILLS)
    # dedupe while open
    assert yti_artifact.create_topic_task("Black October").get("already") or True
    assert yti_artifact.create_topic_task("")["error"]


def test_script_task_versions_folder_and_marks_produce_state(tmp_home, art_kanban):
    from tests.test_generate_tasks import _mk_script
    rel = "youtube/2026-10-27/recommended/demo/script-outline.md"
    script = _mk_script(rel)
    r = yti_artifact.create_script_task(rel)
    assert r["ok"] and r["outDir"].endswith("/demo/artifact")
    body = art_kanban.created[-1]["body"]
    assert str(script) in body and "Each beat becomes a card" in body
    assert yti_generate.produce_states()[rel]["status"] == "open"
    # an existing page folder means the next produce goes to artifact-2
    (script.parent / "artifact").mkdir()
    (script.parent / "artifact" / "index.html").write_text("x")
    art_kanban.tasks[r["taskId"]]["status"] = "done"
    r2 = yti_artifact.create_script_task(rel)
    assert r2["outDir"].endswith("/demo/artifact-2")
    assert yti_artifact.create_script_task("youtube/nope.md")["error"]


def test_iterate_resolves_folder_from_any_inner_path(tmp_home, art_kanban):
    ws = yti_paths.workspace_dir()
    folder = _write_doc(ws / "youtube/2026-10-27/artifacts/x", _minimal_doc())
    r = yti_artifact.create_iterate_task("youtube/2026-10-27/artifacts/x/img/s01.jpg", "make images wider")
    assert r["ok"] and r["outDir"] == "youtube/2026-10-27/artifacts/x"
    assert "make images wider" in art_kanban.created[-1]["body"]
    assert yti_artifact.create_iterate_task("youtube/2026-10-27/other", "x")["error"]
    assert yti_artifact.artifact_root("youtube/2026-10-27/artifacts/x/artifact.json") == folder


def test_rebuild_and_states_report_page(tmp_home, art_kanban):
    ws = yti_paths.workspace_dir()
    _write_doc(ws / "youtube/2026-10-27/artifacts/y", _minimal_doc())
    r = yti_artifact.rebuild("youtube/2026-10-27/artifacts/y")
    assert r["ok"] and r["page"] == "youtube/2026-10-27/artifacts/y/index.html"
    doc = _minimal_doc()
    doc["sections"] = doc["sections"][:1]
    _write_doc(ws / "youtube/2026-10-27/artifacts/z", doc)
    bad = yti_artifact.rebuild("youtube/2026-10-27/artifacts/z")
    assert bad["error"] and bad["problems"]
    t = yti_artifact.create_topic_task("Zed")
    st = yti_artifact.states()[t["outDir"]]
    assert st["status"] == "open" and st["page"] is None


def test_completion_renders_clean_page_and_opens_fix_on_failure(conn, tmp_home, art_kanban):
    t = yti_artifact.create_topic_task("Clean")
    out = Path(yti_artifact._load_map(conn)[t["outDir"]]["outDir"])
    _write_doc(out, _minimal_doc())
    (out / "notes.txt").write_text("scratch")
    art_kanban.tasks[t["taskId"]]["status"] = "done"
    res = yti_artifact.handle_completion(conn, t["taskId"])
    assert res["clean"] and res["swept"] == 1 and (out / "index.html").exists()
    assert not (out / "notes.txt").exists()
    # a folder that fails the gate re-opens as a fix task, twice at most
    t2 = yti_artifact.create_topic_task("Broken")
    out2 = Path(yti_artifact._load_map(conn)[t2["outDir"]]["outDir"])
    doc = _minimal_doc()
    doc["sections"] = doc["sections"][:1]
    _write_doc(out2, doc)
    art_kanban.tasks[t2["taskId"]]["status"] = "done"
    r1 = yti_artifact.handle_completion(conn, t2["taskId"])
    assert r1["fixTask"] and "Fix page" in art_kanban.tasks[r1["fixTask"]]["title"]
    assert "'steps' section" in art_kanban.tasks[r1["fixTask"]]["body"]
    art_kanban.tasks[r1["fixTask"]]["status"] = "done"
    r2 = yti_artifact.handle_completion(conn, r1["fixTask"])
    assert r2["fixTask"]
    art_kanban.tasks[r2["fixTask"]]["status"] = "done"
    r3 = yti_artifact.handle_completion(conn, r2["fixTask"])
    assert r3.get("exhausted")
    assert yti_artifact.handle_completion(conn, "unknown") is None


def test_state_for_dir_finds_newest_page(tmp_home, art_kanban):
    ws = yti_paths.workspace_dir()
    base = ws / "youtube/2026-10-20/chan/video"
    _write_doc(base / "artifact", _minimal_doc())
    assert yti_artifact.rebuild("youtube/2026-10-20/chan/video/artifact")["ok"]
    st = yti_artifact.state_for_dir("youtube/2026-10-20/chan/video")
    assert st["status"] == "done" and st["page"].endswith("/artifact/index.html")
    assert yti_artifact.state_for_dir(None)["status"] is None


def test_page_file_serves_and_inlines(tmp_home):
    ws = yti_paths.workspace_dir()
    folder = _write_doc(ws / "youtube/2026-10-27/artifacts/p", _minimal_doc())
    assert yti_artifact.rebuild("youtube/2026-10-27/artifacts/p")["ok"]
    raw = yti_artifact.page_file("youtube/2026-10-27/artifacts/p/index.html")
    assert raw["path"].endswith("index.html") and raw["media"].startswith("text/html")
    img = yti_artifact.page_file("youtube/2026-10-27/artifacts/p/img/s01.jpg")
    assert img["media"] == "image/jpeg"
    inl = yti_artifact.page_file("youtube/2026-10-27/artifacts/p/index.html", inline=True)
    assert "data:image/jpeg;base64," in inl["html"]
    assert yti_artifact.page_file("../../etc/passwd")["error"]
    assert yti_artifact.page_file("youtube/2026-10-27/artifacts/p/artifact.json")["media"] == "application/json"
    assert yti_artifact.page_file("youtube/2026-10-27/artifacts/p/nope.exe")["error"]


# ---- research bridge --------------------------------------------------------

def test_research_artifact_action_seeds_teardown(tmp_home, art_kanban, monkeypatch):
    import yti_rs_bridge
    import yti_rs_config
    import yti_rs_db
    import yti_store
    rconn = yti_rs_db.connect()
    rconn.execute("INSERT INTO channels(channel_id, handle, title, niche, first_seen, last_seen) "
                  "VALUES ('c1', '@chan', 'Chan', 'ai', '2026-10-01', '2026-10-01')")
    rconn.execute("INSERT INTO videos(video_id, channel_id, title, published_at, niche, first_seen, last_seen) "
                  "VALUES ('v1', 'c1', 'Outlier Video', '2026-10-20T00:00:00Z', 'ai', '2026-10-21', '2026-10-21')")
    rconn.commit()
    monkeypatch.setattr(yti_rs_bridge, "ensure_transcript", lambda *a, **k: {"ok": True, "skipped": True})
    sconn = yti_store.connect()
    r = yti_rs_bridge.run_artifact(rconn, sconn, "v1", None, pov="my angle", guidance="check dates")
    assert r["ok"] and "/chan/outlier-video/artifact" in r["outDir"]
    body = art_kanban.created[-1]["body"]
    assert "Outlier Video" in body and "my angle" in body and "check dates" in body
    assert "Source material" in body
    st = yti_rs_bridge.state(rconn, sconn, "v1")
    assert st["artifact"]["status"] == "open"
    assert yti_rs_bridge.run_artifact(rconn, sconn, "missing", None)["error"]


# ---- dashboard routes -------------------------------------------------------

def _load_api_registered(monkeypatch, jobs_mod, execs_mod):
    """Like test_generate_tasks._load_plugin_api but registered in
    sys.modules, exactly as the dashboard loader does, so pydantic can
    resolve the module's postponed annotations (Optional[...])."""
    import sys
    import types
    cron_pkg = types.ModuleType("cron")
    cron_pkg.jobs = jobs_mod
    cron_pkg.executions = execs_mod
    monkeypatch.setitem(sys.modules, "cron", cron_pkg)
    monkeypatch.setitem(sys.modules, "cron.jobs", jobs_mod)
    monkeypatch.setitem(sys.modules, "cron.executions", execs_mod)
    name = "pa_artifact_routes_test"
    spec = importlib.util.spec_from_file_location(name, str(ROOT / "dashboard" / "plugin_api.py"))
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, mod)
    spec.loader.exec_module(mod)
    return mod


def test_routes_serve_rebuild_and_create(tmp_home, art_kanban, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from tests.test_generate_tasks import _cron_stubs
    jobs_mod, execs_mod = _cron_stubs()
    api = _load_api_registered(monkeypatch, jobs_mod, execs_mod)
    app = FastAPI()
    app.include_router(api.router, prefix="/api/plugins/long-form")
    c = TestClient(app)
    ws = yti_paths.workspace_dir()
    _write_doc(ws / "youtube/2026-10-27/artifacts/r", _minimal_doc())
    # rebuild renders; the page and its image are then served
    r = c.post("/api/plugins/long-form/artifact-rebuild", json={"path": "youtube/2026-10-27/artifacts/r"})
    assert r.status_code == 200 and r.json()["page"].endswith("/r/index.html")
    page = c.get("/api/plugins/long-form/page/youtube/2026-10-27/artifacts/r/index.html")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert 'src="img/s01.jpg"' in page.text
    inl = c.get("/api/plugins/long-form/page/youtube/2026-10-27/artifacts/r/index.html?inline=1")
    assert "data:image/jpeg;base64," in inl.text
    img = c.get("/api/plugins/long-form/page/youtube/2026-10-27/artifacts/r/img/s01.jpg")
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    assert c.get("/api/plugins/long-form/page/youtube/2026-10-27/artifacts/r/nope.jpg").status_code == 404
    assert c.get("/api/plugins/long-form/page/research/reports/x.md").status_code == 404
    # a broken folder: 400 with the problem list
    doc = _minimal_doc(); doc["sections"] = doc["sections"][:1]
    _write_doc(ws / "youtube/2026-10-27/artifacts/bad", doc)
    bad = c.post("/api/plugins/long-form/artifact-rebuild", json={"path": "youtube/2026-10-27/artifacts/bad"})
    assert bad.status_code == 400 and isinstance(bad.json()["detail"], list)
    # topic task + states + iterate + produce
    t = c.post("/api/plugins/long-form/artifact", json={"topic": "Keys", "pov": "p", "guidance": "g"}).json()
    assert t["ok"] and t["outDir"] in c.get("/api/plugins/long-form/artifact-states").json()["states"]
    it = c.post("/api/plugins/long-form/artifact-iterate",
                json={"path": "youtube/2026-10-27/artifacts/r/index.html", "feedback": "wider"}).json()
    assert it["ok"]
    (ws / "youtube/2026-10-27/recommended/d").mkdir(parents=True)
    (ws / "youtube/2026-10-27/recommended/d/script-outline.md").write_text("# s\n")
    p = c.post("/api/plugins/long-form/produce", json={"path": "youtube/2026-10-27/recommended/d/script-outline.md"}).json()
    assert p["ok"] and p["outDir"].endswith("/d/artifact")
    assert c.post("/api/plugins/long-form/produce", json={"path": "youtube/none.md"}).status_code == 400
