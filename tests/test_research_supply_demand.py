from datetime import datetime, timezone

import yti_rs_db
import yti_rs_report as R
from yti_rs_config import DEFAULT_CONFIG

NOW = datetime(2026, 10, 6, tzinfo=timezone.utc)


def _add(conn, vid, *, published, multiple, bucket="long", cls="hit", approx=0, gran=None, raw=None,
         raw_only=0, niche="n1", flag="ok"):
    yti_rs_db.upsert_video(conn, {"video_id": vid, "channel_id": "UC1", "title": f"title {vid}", "niche": niche,
                                  "published_at": published, "published_approx": approx,
                                  "published_granularity_days": gran, "views": 1000,
                                  "is_short": int(bucket == "short"), "raw_json": raw or {}})
    conn.execute("INSERT INTO scores(video_id, computed_at, format_bucket, projected_multiple, raw_only, organic_flag, class)"
                 " VALUES (?,?,?,?,?,?,?)", (vid, "x", bucket, multiple, raw_only, flag, cls))
    conn.commit()


def test_points_carry_an_age_range_not_false_precision(tmp_home):
    conn = yti_rs_db.connect()
    cfg = dict(DEFAULT_CONFIG); cfg["niches"] = [{"name": "n1", "is_target": True, "signal_half_life_days": 180}]
    yti_rs_db.upsert_channel(conn, {"channel_id": "UC1", "handle": "@c", "niche": "n1"})
    _add(conn, "exact", published="2026-09-26T00:00:00+00:00", multiple=4.0)
    # "2 months ago" seen at crawl time: 2..3 months old -> range [age, age + 30.44)
    _add(conn, "rel", published="2026-08-06T00:00:00+00:00", multiple=6.5, approx=1, gran=30.0,
         raw={"publishedTimeText": "2 months ago"}, raw_only=1)
    # date estimated from upload cadence: symmetric around the estimate
    _add(conn, "est", published="2026-06-28T00:00:00+00:00", multiple=1.2, approx=1, gran=14.0, cls="normal", raw_only=1)
    _add(conn, "short", published="2026-09-26T00:00:00+00:00", multiple=50.0, bucket="short")
    _add(conn, "immature", published="2026-09-26T00:00:00+00:00", multiple=None, cls="immature")
    d = R.supply_demand(conn, cfg, now=NOW)
    pts = {p["id"]: p for p in d["points"]}
    assert set(pts) == {"exact", "rel", "est"}                      # long-form, scored, dated only
    assert (pts["exact"]["a"], pts["exact"]["s"]) == (10.0, 0.0) and pts["exact"]["f"] == ["projected"]
    assert pts["rel"]["a"] == 61.0 and pts["rel"]["s"] == 30.44 and pts["rel"]["m"] == 6.5
    assert (pts["est"]["a"], pts["est"]["s"]) == (86.0, 28.0)
    assert pts["exact"]["ch"] == "@c"
    assert d["half_life"] == {"n1": 180.0} and d["hit_multiple"] == 3.0 and d["bucket"] == "long"
    assert R.supply_demand(conn, cfg, niche="other", now=NOW)["points"] == []
    conn.close()


def test_research_brief_gates_on_populated_research(tmp_home):
    """The content pipeline asks for a brief; with nothing scored it must say
    ready:false (and the pipeline falls back), never a half-empty brief."""
    import yti_rs_db, yti_rs_report
    from yti_rs_config import DEFAULT_CONFIG
    conn = yti_rs_db.connect()
    cfg = dict(DEFAULT_CONFIG)
    assert yti_rs_report.research_brief(conn, cfg)["ready"] is False            # no target niche
    cfg["niches"] = [{"name": "n1", "is_target": True, "seed_terms": ["a"], "signal_half_life_days": 365}]
    out = yti_rs_report.research_brief(conn, cfg)
    assert out["ready"] is False and "needs 100" in out["reason"] and out["target_niche"] == "n1"
    conn.close()


def test_brief_focus_quadrant_applies_the_band_to_followed_channels(tmp_home):
    """A 1M-subscriber channel you follow is not a comparable: its outliers
    stay out of the brief's focus quadrant even though the channel is tracked."""
    conn = yti_rs_db.connect()
    cfg = dict(DEFAULT_CONFIG); cfg["niches"] = [{"name": "n1", "is_target": True, "seed_terms": ["a"], "signal_half_life_days": 365}]
    yti_rs_db.upsert_channel(conn, {"channel_id": "UC1", "handle": "@small", "niche": "n1", "subscriber_count": 20000})
    yti_rs_db.upsert_channel(conn, {"channel_id": "UCbig", "handle": "@big", "niche": "n1", "subscriber_count": 1_050_000})
    conn.execute("UPDATE channels SET is_tracked = 1 WHERE channel_id = 'UCbig'")
    for i in range(100):
        _add(conn, f"v{i}", published="2026-09-01T00:00:00+00:00", multiple=1.0, cls="normal")
    for i in range(5):
        _add(conn, f"h{i}", published="2026-10-04T00:00:00+00:00", multiple=5.0)
    yti_rs_db.upsert_video(conn, {"video_id": "bigv", "channel_id": "UCbig", "title": "big hit", "niche": "n1",
                                  "published_at": "2026-10-04T00:00:00+00:00", "views": 900000, "is_short": 0, "raw_json": {}})
    conn.execute("INSERT INTO scores(video_id, computed_at, format_bucket, projected_multiple, raw_only, organic_flag, class)"
                 " VALUES ('bigv','x','long',9.0,0,'ok','strong_hit')")
    conn.commit()
    out = R.research_brief(conn, cfg, now=NOW)
    assert out["ready"] is True
    titles = {f["title"] for f in out["focus_quadrant"]}
    assert "big hit" not in titles and "title h0" in titles
    conn.close()
