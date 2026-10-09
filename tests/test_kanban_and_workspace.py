"""Kanban routing for analysis work items + workspace deliverables helpers."""
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import yti_analysis
import yti_paths
import yti_store
import yti_workspace

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def _mk_video(conn, workspace: Path, vid: str, *, views=100, hours_old=10,
              status="transcribed"):
    pub = (NOW - timedelta(hours=hours_old)).isoformat()
    rel = f"youtube/2026-08-01/chan/{vid}/transcript.json"
    tpath = workspace / rel
    tpath.parent.mkdir(parents=True, exist_ok=True)
    tpath.write_text("{}")
    yti_store.upsert_video(conn, {
        "video_id": vid, "title": f"Video {vid}", "published": pub,
        "view_count": views, "channel_handle": "@chan",
        "transcript_path": rel, "status": status,
        "link": f"https://www.youtube.com/watch?v={vid}",
    })
    yti_store.add_snapshot(conn, vid, views, ts=NOW.isoformat())


class FakeKanban:
    """Stub of hermes_cli.kanban_db with just the surface yti_analysis uses."""

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
                    skills, assignee=None):
        assert assignee, "plugin tasks must be born assigned (NEEDS ASSIGNEE rule)"
        self.counter += 1
        tid = f"t_fake{self.counter}"
        record = {"id": tid, "title": title, "body": body, "status": "ready",
                  "created_by": created_by, "skills": skills,
                  "assignee": assignee}
        self.tasks[tid] = record
        self.created.append(record)
        return tid

    def get_task(self, conn, task_id):
        return self.tasks.get(task_id)


@pytest.fixture()
def fake_kanban(monkeypatch):
    fake = FakeKanban()
    mod = types.ModuleType("hermes_cli")
    kb_mod = fake  # module-like: attribute access hits the instance
    mod.kanban_db = kb_mod
    monkeypatch.setitem(sys.modules, "hermes_cli", mod)
    monkeypatch.setitem(sys.modules, "hermes_cli.kanban_db", kb_mod)
    return fake


def test_trigger_routes_items_to_kanban(conn, tmp_home, fake_kanban):
    ws = yti_paths.workspace_dir()
    _mk_video(conn, ws, "vid1", views=1000, hours_old=5)
    _mk_video(conn, ws, "vid2", views=100, hours_old=5)
    result = yti_analysis.trigger_analysis(conn)
    assert result["triggered"] == 2
    assert result["kanbanRouted"] == 2
    titles = [t["title"] for t in fake_kanban.created]
    assert titles == ["Analyze: Video vid1", "Analyze: Video vid2"]
    assert all(t["skills"] == ["long-form:youtube-video-analyst"]
               for t in fake_kanban.created)
    assert "yt_add_insight" in fake_kanban.created[0]["body"]
    row = conn.execute(
        "SELECT kanban_task_id FROM analysis_queue WHERE video_id='vid1'"
    ).fetchone()
    assert row["kanban_task_id"] == result["items"][0]["kanbanTaskId"]


def test_retrigger_dedupes_open_tasks(conn, tmp_home, fake_kanban):
    ws = yti_paths.workspace_dir()
    _mk_video(conn, ws, "vid1")
    first = yti_analysis.trigger_analysis(conn)
    tid = first["items"][0]["kanbanTaskId"]
    # Simulate the validator resetting the video for retry while the kanban
    # task is still open — retrigger must reuse, not duplicate.
    yti_store.set_video_status(conn, "vid1", "transcribed")
    second = yti_analysis.trigger_analysis(conn)
    assert second["items"][0]["kanbanTaskId"] == tid
    assert len(fake_kanban.created) == 1


def test_retrigger_recreates_after_task_closed(conn, tmp_home, fake_kanban):
    ws = yti_paths.workspace_dir()
    _mk_video(conn, ws, "vid1")
    first = yti_analysis.trigger_analysis(conn)
    fake_kanban.tasks[first["items"][0]["kanbanTaskId"]]["status"] = "done"
    yti_store.set_video_status(conn, "vid1", "transcribed")
    second = yti_analysis.trigger_analysis(conn)
    assert second["items"][0]["kanbanTaskId"] != first["items"][0]["kanbanTaskId"]
    assert len(fake_kanban.created) == 2


def test_graceful_fallback_without_kanban(conn, tmp_home, monkeypatch):
    ws = yti_paths.workspace_dir()
    _mk_video(conn, ws, "vid1")
    monkeypatch.setattr(yti_analysis, "_kanban", lambda: None)
    result = yti_analysis.trigger_analysis(conn)
    assert result["triggered"] == 1
    assert result["kanbanRouted"] == 0
    assert "kanbanTaskId" not in result["items"][0]
    assert "instructions" in result["items"][0]


def test_kanban_completion_validates_and_retries(conn, tmp_home, fake_kanban):
    ws = yti_paths.workspace_dir()
    _mk_video(conn, ws, "vid1")
    first = yti_analysis.trigger_analysis(conn)
    tid = first["items"][0]["kanbanTaskId"]
    fake_kanban.tasks[tid]["status"] = "done"

    # Worker completed but produced nothing → validator fails → retry task.
    result = yti_analysis.handle_kanban_completion(conn, tid)
    assert result is not None and result["videoId"] == "vid1"
    assert result.get("retry") == 1
    assert result.get("retryKanbanTaskId")
    assert len(fake_kanban.created) == 2

    # Unknown task ids are ignored.
    assert yti_analysis.handle_kanban_completion(conn, "t_not_ours") is None


def test_kanban_completion_pass(conn, tmp_home, fake_kanban):
    import yti_insights
    ws = yti_paths.workspace_dir()
    _mk_video(conn, ws, "vid1")
    first = yti_analysis.trigger_analysis(conn)
    tid = first["items"][0]["kanbanTaskId"]
    # Produce the deliverables the validator wants.
    analysis = Path(first["items"][0]["analysisOutput"])
    analysis.parent.mkdir(parents=True, exist_ok=True)
    analysis.write_text("# analysis")
    for i in range(10):
        yti_insights.add_insight(conn, text=f"insight number {i} about topic {i}",
                                 category="strategy", source_video_id="vid1",
                                 insights_dir=ws / "insights")
    result = yti_analysis.handle_kanban_completion(conn, tid)
    assert result["ok"] is True


# -- workspace deliverables ---------------------------------------------------

def _seed_workspace(ws: Path):
    d = ws / "youtube" / "2026-08-01" / "recommended" / "topic-a"
    d.mkdir(parents=True)
    (d / "concept.md").write_text("# Concept A\n\nbody")
    (d / "assets").mkdir()
    (d / "assets" / "beat1.jpg").write_bytes(b"\xff\xd8\xff\xdbfakejpeg")
    (ws / "insights").mkdir()
    (ws / "insights" / "x.md").write_text("hidden from tree")


def test_tree_shape_and_ordering(tmp_home):
    ws = yti_paths.workspace_dir()
    _seed_workspace(ws)
    tree = yti_workspace.build_tree(ws)
    assert [n["name"] for n in tree] == ["youtube"]  # insights/ excluded
    day = tree[0]["children"][0]
    rec = day["children"][0]
    topic = rec["children"][0]
    names = [n["name"] for n in topic["children"]]
    assert names == ["assets", "concept.md"]  # dirs first, then files
    concept = topic["children"][1]
    assert concept["kind"] == "file" and concept["ext"] == ".md"
    assert concept["size"] > 0


def test_traversal_guard(tmp_home):
    ws = yti_paths.workspace_dir()
    _seed_workspace(ws)
    assert yti_workspace.resolve_inside_workspace(ws, "../secrets") is None
    assert yti_workspace.resolve_inside_workspace(ws, "/etc/passwd") is None
    assert yti_workspace.resolve_inside_workspace(
        ws, "youtube/../../outside.md") is None
    assert yti_workspace.resolve_inside_workspace(ws, "insights/x.md") is None
    ok = yti_workspace.resolve_inside_workspace(
        ws, "youtube/2026-08-01/recommended/topic-a/concept.md")
    assert ok is not None
    read = yti_workspace.read_file("../secrets", workspace=ws)
    assert read["ok"] is False


def test_read_text_vs_binary(tmp_home):
    ws = yti_paths.workspace_dir()
    _seed_workspace(ws)
    text = yti_workspace.read_file(
        "youtube/2026-08-01/recommended/topic-a/concept.md", workspace=ws)
    assert text["ok"] and text["kind"] == "text" and "Concept A" in text["text"]
    binary = yti_workspace.read_file(
        "youtube/2026-08-01/recommended/topic-a/assets/beat1.jpg", workspace=ws)
    assert binary["ok"] and binary["kind"] == "binary"
    assert binary["mimeType"] == "image/jpeg" and binary["base64"]


def test_write_allowlist(tmp_home):
    ws = yti_paths.workspace_dir()
    _seed_workspace(ws)
    ok = yti_workspace.write_file(
        "youtube/2026-08-01/recommended/topic-a/concept.md", "# edited",
        workspace=ws)
    assert ok["ok"] is True
    assert "edited" in (ws / "youtube/2026-08-01/recommended/topic-a/concept.md").read_text()
    denied = yti_workspace.write_file(
        "youtube/2026-08-01/recommended/topic-a/assets/beat1.jpg", "nope",
        workspace=ws)
    assert denied["ok"] is False
    outside = yti_workspace.write_file("../evil.md", "nope", workspace=ws)
    assert outside["ok"] is False
