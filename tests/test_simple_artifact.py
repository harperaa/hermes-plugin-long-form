"""simple-artifact: the brief page's gate and renderer, the Simple artifact
task from a URL or pasted content, and the shared folder lifecycle (iterate,
rebuild, completion, inline serving) dispatching to the simple builder."""
import json
import shutil
from pathlib import Path

import pytest

import yti_artifact
import yti_generate
import yti_paths

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "simple-artifact"
EXAMPLE = SKILL / "example"


def _b():
    return yti_artifact.simple_builder()


def _fake_image(p: Path, size: int = 30_000) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\xff\xd8" + b"\0" * size)


def _beat(n: int, **over) -> dict:
    d = {"heading": f"Beat {n}", "image": f"img/b{n}.jpg", "image_alt": "a sketch",
         "bullets": ["one cue", "two cue"]}
    d.update(over)
    return d


def _minimal_doc(**over) -> dict:
    doc = {
        "title": "Test Brief",
        "eyebrow": "Video brief · test",
        "lede": " ".join(["word"] * 20),
        "sections": [
            {"type": "promise", "heading": "You leave with", "image": "img/p.jpg", "image_alt": "a",
             "bullets": ["a promise", "another"]},
            {"type": "problem", "heading": "The problem", "beats": [
                _beat(1, contrast={"crowd": ["build first"], "me": ["audience first"]}),
                _beat(2, story=["it happened", "then this"], key={"text": "land it", "tone": "crit"})]},
            {"type": "value", "heading": "The value", "beats": [_beat(3), _beat(4), _beat(5, label="Proof")]},
            {"type": "apply", "heading": "Today", "image": "img/a.jpg", "image_alt": "a",
             "steps": [{"title": "Pick", "text": "one platform"}, {"title": "Comment", "text": "three times"}],
             "key": {"text": "fast win", "tone": "ok"}},
            {"type": "summary", "heading": "Covered", "bullets": ["one", "two", "three"]},
            {"type": "cta", "heading": "Next", "image": "img/c.jpg", "image_alt": "a",
             "bullets": ["watch the next one", "like and subscribe"]},
        ],
    }
    doc.update(over)
    return doc


def _write_doc(folder: Path, doc: dict, images=("p", "b1", "b2", "b3", "b4", "b5", "a", "c")) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "brief.json").write_text(json.dumps(doc))
    for n in images:
        _fake_image(folder / "img" / f"{n}.jpg")
    return folder


# ---- builder ----------------------------------------------------------------

def test_example_passes_gate_and_renders(tmp_path):
    work = tmp_path / "ex"
    shutil.copytree(EXAMPLE, work, ignore=shutil.ignore_patterns("index.html", "history"))
    rep = _b().build(work)
    assert rep["ok"], rep["problems"]
    assert rep["images"] >= 10
    html = (work / "index.html").read_text()
    assert html.startswith("<!doctype html>") and "<title>Leads Every Day</title>" in html
    # the six fixed parts, in order, and the theme follows the system with an override
    ids = [html.index(f'id="{t}"') for t in _b().SECTION_ORDER]
    assert ids == sorted(ids)
    assert "prefers-color-scheme: dark" in html and 'data-theme="dark"' in html and 'class="theme"' in html
    assert html.count("<figure>") == rep["images"]
    assert "What the crowd says" in html and "<details><summary>Full story</summary>" in html


def test_gate_rejects_presenter_talk_order_and_missing_images(tmp_path):
    b = _b()
    doc = _minimal_doc()
    folder = _write_doc(tmp_path / "d", doc)
    assert b.build(folder)["ok"]
    # presenter instructions are content faults
    doc["sections"][2]["beats"][0]["bullets"] = ["Mention the link in the bio here"]
    assert any("instruction to the presenter" in x for x in b.validate(doc, folder))
    # em dashes
    doc = _minimal_doc(); doc["lede"] = "a — b " + " ".join(["w"] * 18)
    assert any("em dash" in x for x in b.validate(doc, folder))
    # section order is fixed
    doc = _minimal_doc(); doc["sections"].reverse()
    assert any("must be exactly" in x for x in b.validate(doc, folder))
    # every beat has its own image, present and real
    doc = _minimal_doc(); doc["sections"][2]["beats"][1]["image"] = "img/b3.jpg"
    assert any("used twice" in x for x in b.validate(doc, folder))
    doc = _minimal_doc(); doc["sections"][0]["image"] = "img/missing.jpg"
    assert any("not found" in x for x in b.validate(doc, folder))
    _fake_image(folder / "img" / "tiny.jpg", size=100)
    doc = _minimal_doc(); doc["sections"][0]["image"] = "img/tiny.jpg"
    assert any("at least 20000" in x for x in b.validate(doc, folder))
    # the problem section argues against the crowd at least once; the close always asks to subscribe
    doc = _minimal_doc(); del doc["sections"][1]["beats"][0]["contrast"]
    assert any("contrast" in x for x in b.validate(doc, folder))
    doc = _minimal_doc(); doc["sections"][5]["bullets"] = ["watch more"]
    assert any("like and subscribe" in x for x in b.validate(doc, folder))
    # bullets are cues
    doc = _minimal_doc(); doc["sections"][4]["bullets"] = [" ".join(["w"] * 40), "b", "c"]
    assert any("keep it under" in x for x in b.validate(doc, folder))
    # the chosen length is enforced, roughly: the minimal page has 8 images
    doc = _minimal_doc(target_images=10)
    assert any("target_images is 10" in x and "split" in x for x in b.validate(doc, folder))
    doc = _minimal_doc(target_images=12)
    assert any("must be one of [10, 20]" in x for x in b.validate(doc, folder))


def test_render_inlines_images_and_keeps_history(tmp_path):
    b = _b()
    folder = _write_doc(tmp_path / "d", _minimal_doc())
    assert b.build(folder)["ok"]
    html = (folder / "index.html").read_text()
    assert 'src="img/p.jpg"' in html
    inl = b.inline_images(html, folder)
    assert "data:image/jpeg;base64," in inl and 'src="img/' not in inl
    assert b.build(folder)["ok"]
    assert list((folder / "history").glob("*.html"))


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


def test_simple_task_from_url_or_content(conn, tmp_home, art_kanban):
    r = yti_artifact.create_simple_task("Leads Every Day", url="https://example.com/note",
                                        content="Most businesses lack leads.\nBuild first is a trap.")
    assert r["ok"] and r["outDir"] == f"youtube/{yti_artifact._today()}/artifacts/leads-every-day-brief"
    t = art_kanban.created[-1]
    assert t["title"] == "Brief page: Leads Every Day"
    assert t["skills"] == list(yti_artifact.SIMPLE_SKILLS)
    body = t["body"]
    assert "https://example.com/note" in body and "Build first is a trap." in body
    assert "long-form:simple-artifact" in body and "build_simple.py" in body
    assert "example/brief.json" in body and "like and subscribe" in body
    assert yti_artifact._load_map(conn)[r["outDir"]]["kind"] == "simple"
    assert yti_artifact._load_map(conn)[r["outDir"]]["images"] == 10
    assert "about 10 minutes" in body and '"target_images": 10' in body and "CONSOLIDATE" in body
    # the 20 image length changes the brief; anything else is refused
    r20 = yti_artifact.create_simple_task("Long one", content="x", images=20)
    assert r20["ok"] and "about 20 minutes" in art_kanban.created[-1]["body"] and "EXPAND" in art_kanban.created[-1]["body"]
    assert "10 or 20" in yti_artifact.create_simple_task("Bad", content="x", images=15)["error"]
    # a second click never reuses the folder
    r2 = yti_artifact.create_simple_task("Leads Every Day", content="x")
    assert r2["outDir"] == r["outDir"] + "-2"
    # title derived from the content when absent; refusals
    r3 = yti_artifact.create_simple_task("", content="Why nobody buys\nmore text")
    assert r3["outDir"].endswith("/why-nobody-buys-brief")
    assert yti_artifact.create_simple_task("", url="", content="")["error"]
    assert "http" in yti_artifact.create_simple_task("", url="ftp://x")["error"]
    # long pastes are clipped, not refused
    big = yti_artifact.create_simple_task("Big", content="z" * (yti_artifact.SIMPLE_CONTENT_MAX + 500))
    assert big["ok"] and "content clipped" in art_kanban.created[-1]["body"]


def test_simple_folder_lifecycle_dispatches_to_simple_builder(conn, tmp_home, art_kanban):
    r = yti_artifact.create_simple_task("Lifecycle", content="some notes")
    key = r["outDir"]
    root = yti_paths.workspace_dir() / key
    task_id = r["taskId"]
    # worker writes a bad page: completion opens a fix task with the simple brief
    _write_doc(root, _minimal_doc(sections=[]))
    (root / "scratch.log").write_text("x")
    res = yti_artifact.handle_completion(conn, task_id)
    assert res and res.get("fixTask") and res["swept"] == 1
    fix = art_kanban.created[-1]
    assert "simple-artifact folder that FAILED" in fix["body"] and fix["skills"] == list(yti_artifact.SIMPLE_SKILLS)
    # a page that ignores the chosen length is sent back; a 9 image page passes for 10
    _write_doc(root, _minimal_doc())
    res = yti_artifact.handle_completion(conn, fix["id"])
    assert res and res.get("fixTask") and any("target_images" in p for p in res["problems"])
    fix = art_kanban.created[-1]
    doc = _minimal_doc(target_images=10)
    doc["sections"][2]["beats"].append(_beat(6))
    _write_doc(root, doc, images=("p", "b1", "b2", "b3", "b4", "b5", "b6", "a", "c"))
    res = yti_artifact.handle_completion(conn, fix["id"])
    assert res and res.get("clean") and (root / "index.html").exists()
    assert yti_artifact.states()[key]["simple"] is True
    # rebuild, iterate and the inline page route all resolve the simple folder
    assert yti_artifact.rebuild(key)["ok"]
    (root / "brief.json").write_text("{bad json")
    assert "problems" in yti_artifact.rebuild(key)
    (root / "brief.json").write_text(json.dumps(_minimal_doc()))
    art_kanban.tasks[fix["id"]]["status"] = "done"
    it = yti_artifact.create_iterate_task(key + "/img/p.jpg", "shorter promise")
    assert it["ok"] and "simple-artifact brief page from feedback" in art_kanban.created[-1]["body"]
    assert yti_artifact._load_map(conn)[key]["kind"] == "simple-iterate"
    served = yti_artifact.page_file(key + "/index.html", inline=True)
    assert "data:image/jpeg;base64," in served["html"]


def test_full_folders_keep_the_full_builder(tmp_path):
    full = tmp_path / "full"; full.mkdir()
    (full / "artifact.json").write_text("{}")
    assert yti_artifact.builder_for(full).__name__ == "yti_build_artifact"
    simple = tmp_path / "simple"; simple.mkdir()
    (simple / "brief.json").write_text("{}")
    assert yti_artifact.builder_for(simple).__name__ == "yti_build_simple"
    empty = tmp_path / "empty"; empty.mkdir()
    assert yti_artifact.builder_for(empty, "simple").__name__ == "yti_build_simple"
    assert yti_artifact.builder_for(empty).__name__ == "yti_build_artifact"
