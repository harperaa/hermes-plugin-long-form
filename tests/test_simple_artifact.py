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


def _packaging(**over) -> dict:
    pk = {
        "promise": "land it first",
        "thumbnails": [{"image": f"img/thumb-{i}.jpg", "text": "TWO WORDS"} for i in range(1, 6)],
        "titles": [{"text": f"Title {i}", "frame": "… how to build …"} for i in range(1, 21)],
        "frames": {"source": "format-library", "label": "D3", "min_n": 10,
                   "mined": [{"frame": "… how to build …", "hits": 16, "n": 82, "hit_rate": 0.2, "wilson_lb": 0.12, "examples": ["How to Build an LLM"]}],
                   "seeded": [{"frame": "the new rules of {subject}", "psychology": "shift frame", "caution": ""}]},
        "hooks": [{"text": "Mine first.", "given": True}] + [{"text": f"Hook {i}."} for i in range(2, 6)],
        "examples": {"source": "trending", "label": "Trends tab", "items": [
            {"id": "x1", "title": "A winning title", "channel": "@c", "thumbnail": "https://i.ytimg.com/vi/x1/hqdefault.jpg",
             "url": "https://www.youtube.com/watch?v=x1", "metric": 12.5, "metric_label": "views per hour"}]},
        "patterns": ["two words, one yellow"],
    }
    pk.update(over)
    return pk


def _minimal_doc(**over) -> dict:
    doc = {
        "kind": "simple",
        "title": "Test Brief",
        "eyebrow": "Video brief · test",
        "lede": " ".join(["word"] * 20),
        "packaging": _packaging(),
        "sections": [
            {"type": "promise", "heading": "You leave with", "image": "img/p.jpg", "image_alt": "a",
             "bullets": ["a promise", "another"], "key": {"text": "land it first", "tone": "ok"}},
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
    (folder / "artifact.json").write_text(json.dumps(doc))
    for n in images:
        _fake_image(folder / "img" / f"{n}.jpg")
    for i in range(1, 6):
        _fake_image(folder / "img" / f"thumb-{i}.jpg")
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
    assert html.count("<figure>") == rep["images"] + rep["thumbnails"]
    assert "What the crowd says" in html and "<details><summary>Full story</summary>" in html
    # the packaging block opens the page, collapsed: five portrait thumbnails, titles, hooks, the reference set
    assert rep["thumbnails"] == 5
    pack = html.split('<details class="pack" id="packaging">', 1)[1].split("</details>", 1)[0]
    # the block sits after the footer, closed, reached only through the footer's Packaging link
    assert html.index('<details class="pack" id="packaging">') > html.index("<footer>")
    assert 'class="pack-link" data-pack-link>Packaging</a></footer>' in html and "<summary>Packaging" in html
    assert pack.count('src="img/thumb-') == 5 and 'class="refs"' in pack
    assert pack.count('<span class="frame">') == 20 and "Winning title frames" in pack and "Seeded templates" in pack
    assert "If you are not getting leads every day, this is for you." in pack
    # four open loops on the example, and the promise hook sits between its image and its bullets
    assert html.count('<div class="loop">') == 4
    promise = html.split('id="promise"', 1)[1].split('id="problem"', 1)[0]
    assert promise.index("</figure>") < promise.index('<div class="key') < promise.index('<ul class="cues">')


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
    # packaging: required, five of each, the promise verbatim, words on a thumbnail capped
    doc = _minimal_doc(); del doc["packaging"]
    assert any(x.startswith("packaging: required") for x in b.validate(doc, folder))
    doc = _minimal_doc(packaging=_packaging(promise="a different promise"))
    assert any("verbatim" in x for x in b.validate(doc, folder))
    doc = _minimal_doc(packaging=_packaging(titles=[{"text": "only one", "frame": "x"}]))
    assert any("exactly 20" in x for x in b.validate(doc, folder))
    pk = _packaging(); pk["titles"][3] = {"text": "No frame named"}
    assert any("titles[3].frame" in x for x in b.validate(_minimal_doc(packaging=pk), folder))
    pk = _packaging(); pk["titles"][4]["text"] = pk["titles"][2]["text"]
    assert any("duplicate title" in x for x in b.validate(_minimal_doc(packaging=pk), folder))
    pk = _packaging(); del pk["frames"]
    assert any("packaging.frames" in x for x in b.validate(_minimal_doc(packaging=pk), folder))
    pk = _packaging(); pk["thumbnails"][0]["text"] = "six words is one too many here"
    assert any("no more than 5" in x for x in b.validate(_minimal_doc(packaging=pk), folder))
    pk = _packaging(); pk["hooks"] = pk["hooks"][1:] + [pk["hooks"][0]]
    assert any("given hook comes first" in x for x in b.validate(_minimal_doc(packaging=pk), folder))
    pk = _packaging(); pk["thumbnails"][0]["image"] = "img/p.jpg"
    assert any("also a beat image" in x for x in b.validate(_minimal_doc(packaging=pk), folder))
    pk = _packaging(examples={"source": "nowhere", "items": []})
    assert any("packaging.examples" in x for x in b.validate(_minimal_doc(packaging=pk), folder))
    # open loops are questions, short, and read as such
    doc = _minimal_doc(); doc["sections"][0]["question"] = "What costs more than lost sales"
    assert any("question mark" in x for x in b.validate(doc, folder))
    doc = _minimal_doc(); doc["sections"][2]["beats"][0]["question"] = " ".join(["w"] * 40) + "?"
    assert any("under 32" in x for x in b.validate(doc, folder))
    doc = _minimal_doc(); doc["sections"][0]["question"] = "Why does the crowd get this backwards?"
    assert not b.validate(doc, folder)
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
    assert "example/artifact.json" in body and "like and subscribe" in body
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
    (root / "artifact.json").write_text("{bad json")
    assert "problems" in yti_artifact.rebuild(key)
    (root / "artifact.json").write_text(json.dumps(_minimal_doc()))
    art_kanban.tasks[fix["id"]]["status"] = "done"
    it = yti_artifact.create_iterate_task(key + "/img/p.jpg", "shorter promise")
    assert it["ok"] and "simple-artifact brief page from feedback" in art_kanban.created[-1]["body"]
    assert yti_artifact._load_map(conn)[key]["kind"] == "simple-iterate"
    served = yti_artifact.page_file(key + "/index.html", inline=True)
    assert "data:image/jpeg;base64," in served["html"]


def test_publish_gates_a_simple_folder_with_the_simple_builder(tmp_home, monkeypatch):
    import io
    import tarfile
    import yti_publish
    from tests.test_artifact import _Resp
    ws = yti_paths.workspace_dir()
    folder = _write_doc(ws / "youtube/2026-11-03/artifacts/pub-brief", _minimal_doc())
    assert yti_artifact.rebuild("youtube/2026-11-03/artifacts/pub-brief")["ok"]
    monkeypatch.setenv("SURGE_TOKEN", "tok-123")
    sent = {}

    def fake_put(url, data=None, auth=None, headers=None, timeout=None):
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            sent["names"] = sorted(m.name for m in tar.getmembers())
        return _Resp(200, '{"type":"info","message":"Success"}\n')
    monkeypatch.setattr(yti_publish.requests, "put", fake_put)
    entry = yti_publish.publish("youtube/2026-11-03/artifacts/pub-brief/index.html", "")
    # the default name comes from artifact.json's title, and the bundle is the page plus its images
    assert entry["domain"].startswith("test-brief-")
    assert "test-brief-" in sent["names"][-1] and sent["names"][-1].endswith("/index.html")
    assert sum(1 for n in sent["names"] if "/img/" in n) == 13
    # a broken brief is refused before any upload
    (folder / "artifact.json").write_text("{}")
    with pytest.raises(RuntimeError, match="quality gate"):
        yti_publish.publish("youtube/2026-11-03/artifacts/pub-brief/index.html", "x")


def test_full_folders_keep_the_full_builder(tmp_path):
    full = tmp_path / "full"; full.mkdir()
    (full / "artifact.json").write_text("{}")
    assert yti_artifact.builder_for(full).__name__ == "yti_build_artifact"
    assert yti_artifact.builder_for(full, "simple").__name__ == "yti_build_artifact", "the file's own kind wins"
    simple = tmp_path / "simple"; simple.mkdir()
    (simple / "artifact.json").write_text('{"kind": "simple"}')
    assert yti_artifact.builder_for(simple).__name__ == "yti_build_simple"
    assert yti_artifact.is_simple(simple) and not yti_artifact.is_simple(full)
    # the simple gate names the missing marker
    assert any('kind: must be "simple"' in x for x in _b().validate(_minimal_doc(kind=None), simple))
    empty = tmp_path / "empty"; empty.mkdir()
    assert yti_artifact.builder_for(empty, "simple").__name__ == "yti_build_simple"
    assert yti_artifact.builder_for(empty).__name__ == "yti_build_artifact"


def test_every_page_gate_goes_through_builder_for():
    """Publish, rebuild, completion, the inline page route: anything that
    validates or renders a folder must dispatch on the folder's kind. A
    direct `builder()` call outside yti_artifact is the bug that let Publish
    reject a simple page."""
    import re
    offenders = []
    for py in ROOT.glob("*.py"):
        if py.name == "yti_artifact.py":
            continue
        for i, line in enumerate(py.read_text().splitlines(), 1):
            if re.search(r"\byti_artifact\.(simple_)?builder\(\)", line) or re.search(r"(?<![\w.])builder\(\)", line):
                offenders.append(f"{py.name}:{i}: {line.strip()}")
    assert not offenders, offenders
    for py in (ROOT / "dashboard").glob("*.py"):
        for i, line in enumerate(py.read_text().splitlines(), 1):
            if re.search(r"\.(simple_)?builder\(\)", line):
                offenders.append(f"dashboard/{py.name}:{i}: {line.strip()}")
    assert not offenders, offenders


def test_routes_create_rebuild_serve_and_publish_a_simple_page(tmp_home, art_kanban, monkeypatch):
    """The dashboard's own API, end to end for the simple kind: the Simple
    artifact button, states, rebuild, the page route, iterate and publish."""
    import io
    import tarfile
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import yti_publish
    from tests.test_artifact import _Resp, _load_api_registered
    from tests.test_generate_tasks import _cron_stubs
    jobs_mod, execs_mod = _cron_stubs()
    api = _load_api_registered(monkeypatch, jobs_mod, execs_mod)
    app = FastAPI()
    app.include_router(api.router, prefix="/api/plugins/long-form")
    c = TestClient(app)
    base = "/api/plugins/long-form"
    # the button: a URL or pasted content, 10 or 20 images
    t = c.post(f"{base}/artifact-simple", json={"title": "Leads", "content": "notes", "images": 20,
                                                 "hook": "If you have no leads, watch this."}).json()
    assert t["ok"] and t["outDir"].endswith("/leads-brief")
    body = art_kanban.created[-1]["body"]
    assert "If you have no leads, watch this." in body and "yti_packaging.py examples" in body and "thumb-1.jpg" in body
    # the portrait: absent on a fresh instance, present after the upload route
    assert c.get(f"{base}/portrait").json()["present"] is False
    import base64 as _b64
    up = c.post(f"{base}/portrait/upload", json={"filename": "me.jpg", "dataBase64": _b64.b64encode(b"\xff\xd8" + b"\0" * 100).decode()})
    assert up.status_code == 200 and up.json()["present"] and up.json()["name"] == "source-portrait.jpg"
    assert c.get(f"{base}/portrait").json()["present"] is True
    assert c.post(f"{base}/portrait/upload", json={"filename": "me.gif", "dataBase64": "AAAA"}).status_code == 400
    # the reference set and frame routes answer even on an empty instance
    ex = c.get(f"{base}/packaging-examples").json()
    assert ex["source"] in ("supply-demand", "trending") and isinstance(ex["items"], list)
    fr = c.get(f"{base}/packaging-formats").json()
    assert "mined" in fr and "seeded" in fr
    # the states report knows the kind before the worker has written a folder
    assert c.get(f"{base}/artifact-states").json()["states"][t["outDir"]]["simple"] is True
    assert art_kanban.created[-1]["skills"] == list(yti_artifact.SIMPLE_SKILLS)
    assert c.post(f"{base}/artifact-simple", json={"title": "x"}).status_code == 400
    assert c.post(f"{base}/artifact-simple", json={"content": "x", "images": 7}).status_code == 400
    # the worker's folder: rebuild, states, page route, inline preview
    ws = yti_paths.workspace_dir()
    key = t["outDir"]
    doc = _minimal_doc(target_images=10)
    doc["sections"][2]["beats"].append(_beat(6))
    _write_doc(ws / key, doc, images=("p", "b1", "b2", "b3", "b4", "b5", "b6", "a", "c"))
    r = c.post(f"{base}/artifact-rebuild", json={"path": key})
    assert r.status_code == 200 and r.json()["page"].endswith("/leads-brief/index.html")
    st = c.get(f"{base}/artifact-states").json()["states"][key]
    assert st["simple"] is True and st["page"] == key + "/index.html"
    page = c.get(f"{base}/page/{key}/index.html")
    assert page.status_code == 200 and 'id="promise"' in page.text
    assert "data:image/jpeg;base64," in c.get(f"{base}/page/{key}/index.html?inline=1").text
    # a broken simple page: 400 with the simple gate's problems, not "artifact.json not found"
    (ws / key / "artifact.json").write_text(json.dumps({"kind": "simple"}))
    bad = c.post(f"{base}/artifact-rebuild", json={"path": key})
    assert bad.status_code == 400 and any("sections" in p for p in bad.json()["detail"])
    (ws / key / "artifact.json").write_text(json.dumps(doc))
    # iterate on it from any inner path
    art_kanban.tasks[t["taskId"]]["status"] = "done"
    it = c.post(f"{base}/artifact-iterate", json={"path": key + "/img/p.jpg", "feedback": "tighter"}).json()
    assert it["ok"] and "simple-artifact brief page from feedback" in art_kanban.created[-1]["body"]
    # publish: the simple gate runs, then the bundle goes up
    monkeypatch.setenv("SURGE_TOKEN", "tok")
    sent = {}

    def fake_put(url, data=None, auth=None, headers=None, timeout=None):
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            sent["names"] = sorted(m.name for m in tar.getmembers())
        return _Resp(200, '{"type":"info","message":"Success"}\n')
    monkeypatch.setattr(yti_publish.requests, "put", fake_put)
    pub = c.post(f"{base}/artifact-publish", json={"path": key, "domain": "leads-test"})
    assert pub.status_code == 200, pub.text
    assert "leads-test/index.html" in sent["names"] and sum("/img/" in n for n in sent["names"]) == 14
    assert c.get(f"{base}/artifact-states").json()["states"][key]["published"]["url"] == "https://leads-test.surge.sh"


def test_packaging_examples_prefer_supply_demand_then_trending(tmp_home, monkeypatch):
    import sys as _sys
    import types
    import yti_packaging
    # a thin Supply / Demand table falls back to the Trends tab
    monkeypatch.setattr(yti_packaging, "_supply_demand_examples", lambda limit: None)
    monkeypatch.setattr(yti_packaging, "_trending_examples",
                        lambda limit: [{"id": "v1", "title": "T", "channel": "@a", "thumbnail": "u", "url": "w", "metric": 3, "metric_label": "views per hour"}])
    ex = yti_packaging.examples()
    assert ex["source"] == "trending" and ex["items"][0]["id"] == "v1"
    # a populated one wins, ranked by time-adjusted demand with the engine's hit filters
    monkeypatch.undo()
    sd = {"min_subs": 1000, "max_subs": 100000, "half_life": {"n": 100.0}, "default_half_life": 365.0, "points": []}

    def pt(i, m, a, **o):
        d = {"id": f"id{i}", "t": f"Title {i}", "ch": "@c", "n": "n", "m": m, "a": a, "c": "hit", "f": [], "fol": 0, "sub": 5000, "v": 1}
        d.update(o)
        return d
    sd["points"] = [pt(i, 10, 0) for i in range(25)] + [
        pt(90, 100, 0, c="normal"),                 # not a hit
        pt(91, 100, 0, f=["paid?"]),                 # suspected paid
        pt(92, 100, 0, sub=5_000_000),               # outside the comparable band
        pt(93, 100, 0, sub=None),                    # size unknown
        pt(94, 100, 0, t="被漁霸逼入絕境，窮小子覺醒逆天系統"),   # another script
        pt(95, 50, 0),                               # the winner: 50x, brand new
        pt(96, 90, 300),                             # 90x but 300 days old at a 100 day half-life: 11x
    ]

    class _C:
        def close(self):
            pass
    monkeypatch.setitem(_sys.modules, "yti_rs_db", types.SimpleNamespace(connect=lambda: _C()))
    monkeypatch.setitem(_sys.modules, "yti_rs_config", types.SimpleNamespace(load_config=lambda c: {}))
    monkeypatch.setitem(_sys.modules, "yti_rs_report", types.SimpleNamespace(supply_demand=lambda c, cfg: sd))
    ex = yti_packaging.examples()
    ids = [it["id"] for it in ex["items"]]
    assert ex["source"] == "supply-demand" and len(ids) == 20
    assert ids[0] == "id95" and "id96" in ids[:3]
    assert not any(i in ids for i in ("id90", "id91", "id92", "id93", "id94"))
    assert ex["items"][0]["thumbnail"].endswith("/id95/hqdefault.jpg") and ex["items"][0]["metric_label"].startswith("x normal")


def test_portrait_resolution_prefers_newest(tmp_home):
    import os
    import time
    import yti_packaging
    assert yti_packaging.portrait_path() is None
    media = Path(os.environ["HERMES_HOME"]) / "media"
    media.mkdir()
    (media / "portrait-black-shirt.jpg").write_bytes(b"old")
    time.sleep(0.02)
    assert yti_packaging.save_portrait(b"new", "png")["name"] == "source-portrait.png"
    assert yti_packaging.portrait_path().name == "source-portrait.png"
    assert yti_packaging.save_portrait(b"", "png")["error"]


def test_formats_rank_actionable_mined_frames_and_keep_seeded(tmp_home, monkeypatch):
    import sys as _sys
    import types
    import yti_packaging
    lib = [
        {"kind": "mined", "label": "… in # minutes …", "n_total": 69, "n_hits": 26, "hit_rate": 0.38, "wilson_lb": 0.27,
         "examples": [{"title": "Master X in 35 minutes"}, {"title": "Master Y in 28 minutes"}, {"title": "third"}]},
        {"kind": "mined", "label": "… thin …", "n_total": 4, "n_hits": 4, "hit_rate": 1.0, "wilson_lb": 0.51, "examples": []},
        {"kind": "mined", "label": "… how to build …", "n_total": 82, "n_hits": 16, "hit_rate": 0.2, "wilson_lb": 0.12, "examples": []},
        {"kind": "seeded", "label": "the new rules of {subject}", "psychology": "shift frame", "caution": "", "n_total": 0},
    ]

    class _C:
        def close(self):
            pass
    monkeypatch.setitem(_sys.modules, "yti_rs_db", types.SimpleNamespace(connect=lambda: _C()))
    monkeypatch.setitem(_sys.modules, "yti_rs_config", types.SimpleNamespace(load_config=lambda c: {"formats": {"min_actionable_n": 10}}))
    monkeypatch.setitem(_sys.modules, "yti_rs_formats", types.SimpleNamespace(library=lambda c, niche=None: lib))
    fr = yti_packaging.formats()
    assert fr["source"] == "format-library" and fr["min_n"] == 10
    # the thin frame (4 videos) is out even with a higher Wilson bound; ranking is by the bound
    assert [m["frame"] for m in fr["mined"]] == ["… in # minutes …", "… how to build …"]
    assert fr["mined"][0]["hits"] == 26 and fr["mined"][0]["examples"] == ["Master X in 35 minutes", "Master Y in 28 minutes"]
    assert fr["seeded"] == [{"frame": "the new rules of {subject}", "psychology": "shift frame", "caution": ""}]
