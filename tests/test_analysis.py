from datetime import datetime, timedelta, timezone
from pathlib import Path

import yti_analysis
import yti_insights
import yti_store
import yti_paths

# dynamic "now": VPH math uses the real clock, so a pinned date lets
# fixture ages drift and near-equal rankings flip as days pass
NOW = datetime.now(timezone.utc)


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


def test_trigger_caps_and_orders_by_vph(conn, tmp_home):
    ws = yti_paths.workspace_dir()
    for i in range(5):
        _mk_video(conn, ws, f"v{i}", views=(i + 1) * 100)
    res = yti_analysis.trigger_analysis(conn, limit=3, workspace=ws)
    assert res["triggered"] == 3
    # highest VPH first: v4, v3, v2
    assert [i["videoId"] for i in res["items"]] == ["v4", "v3", "v2"]
    assert yti_store.get_video(conn, "v4")["status"] == "analyzing"
    assert yti_store.get_video(conn, "v0")["status"] == "transcribed"


def test_trigger_order_oldest(conn, tmp_home):
    ws = yti_paths.workspace_dir()
    _mk_video(conn, ws, "new", views=1000, hours_old=1)
    _mk_video(conn, ws, "old", views=10, hours_old=100)
    res = yti_analysis.trigger_analysis(conn, order_by="oldest", workspace=ws)
    assert [i["videoId"] for i in res["items"]] == ["old", "new"]


def test_trigger_skips_non_transcribed(conn, tmp_home):
    ws = yti_paths.workspace_dir()
    _mk_video(conn, ws, "done", status="analyzed")
    _mk_video(conn, ws, "pending")
    res = yti_analysis.trigger_analysis(conn, workspace=ws)
    assert [i["videoId"] for i in res["items"]] == ["pending"]


def test_trigger_default_limit(conn, tmp_home):
    ws = yti_paths.workspace_dir()
    for i in range(25):
        _mk_video(conn, ws, f"v{i:02d}", views=100 + i)
    res = yti_analysis.trigger_analysis(conn, workspace=ws)
    assert res["triggered"] == yti_analysis.DEFAULT_ANALYSIS_LIMIT == 20


def test_instructions_mention_tool_and_skill(conn, tmp_home):
    ws = yti_paths.workspace_dir()
    _mk_video(conn, ws, "v1")
    res = yti_analysis.trigger_analysis(conn, workspace=ws)
    instr = res["items"][0]["instructions"]
    assert "long-form:youtube-video-analyst" in instr
    assert "yt_add_insight" in instr
    assert "curl" not in instr
    assert "PAPERCLIP" not in instr


DISTINCT_TEXTS = [
    "Open every video with a concrete unresolved question",
    "Thumbnails must communicate emotion before information",
    "Retention cliffs cluster around topic transitions",
    "Publish cadence trains audience expectations over months",
    "Payoff density matters more than production polish",
    "Titles promising transformation outperform titles promising topics",
    "End screens should tee up one specific next video",
    "Analogies compress complex explanations into memorable frames",
    "Pattern interrupts reset drifting viewer attention",
    "Community posts warm the algorithm between uploads",
]


def _pass_validation(conn, ws, vid):
    # write analysis.md + 10 genuinely distinct insights
    state = yti_store.get_video(conn, vid)
    analysis = (ws / state["transcript_path"]).parent / "analysis.md"
    analysis.write_text("# analysis")
    for text in DISTINCT_TEXTS:
        yti_insights.add_insight(
            conn, text=text, category="strategy", source_video_id=vid)


def test_validator_passes(conn, tmp_home):
    ws = yti_paths.workspace_dir()
    _mk_video(conn, ws, "v1")
    yti_analysis.trigger_analysis(conn, workspace=ws)
    _pass_validation(conn, ws, "v1")
    res = yti_analysis.validate_analysis(conn, "v1", workspace=ws)
    assert res["ok"] is True
    assert yti_store.get_video(conn, "v1")["status"] == "analyzed"


def test_validator_retries_then_fails(conn, tmp_home):
    ws = yti_paths.workspace_dir()
    _mk_video(conn, ws, "v1")
    yti_analysis.trigger_analysis(conn, workspace=ws)

    r1 = yti_analysis.validate_analysis(conn, "v1", workspace=ws)
    assert r1["ok"] is False and r1["retry"] == 1
    assert yti_store.get_video(conn, "v1")["status"] == "transcribed"

    yti_analysis.trigger_analysis(conn, workspace=ws)
    r2 = yti_analysis.validate_analysis(conn, "v1", workspace=ws)
    assert r2["ok"] is False and r2["retry"] == 2

    yti_analysis.trigger_analysis(conn, workspace=ws)
    r3 = yti_analysis.validate_analysis(conn, "v1", workspace=ws)
    assert r3["ok"] is False and r3.get("failed") is True
    row = conn.execute(
        "SELECT status FROM analysis_queue WHERE video_id='v1'").fetchone()
    assert row["status"] == "failed"


def test_validator_needs_min_insights(conn, tmp_home):
    ws = yti_paths.workspace_dir()
    _mk_video(conn, ws, "v1")
    yti_analysis.trigger_analysis(conn, workspace=ws)
    state = yti_store.get_video(conn, "v1")
    analysis = (ws / state["transcript_path"]).parent / "analysis.md"
    analysis.write_text("# analysis")
    for text in DISTINCT_TEXTS[:5]:  # only 5 < MIN_INSIGHTS
        yti_insights.add_insight(
            conn, text=text, category="strategy", source_video_id="v1")
    res = yti_analysis.validate_analysis(conn, "v1", workspace=ws)
    assert res["ok"] is False
    assert res["insights"] == 5
