"""The Research tab is long-form only: Shorts stay in the DB (scored in their
own bucket) but never reach a register, demand map, count, format or crawl
expansion."""
from datetime import datetime, timezone

import yti_rs_budget
import yti_rs_crawl as C
import yti_rs_db
import yti_rs_normalize as N
import yti_rs_outliers as O
import yti_rs_report as R
import yti_rs_snapshot as S
from yti_rs_config import DEFAULT_CONFIG

NOW = datetime(2026, 10, 6, tzinfo=timezone.utc)


def _cfg():
    cfg = dict(DEFAULT_CONFIG)
    cfg["niches"] = [{"name": "n1", "is_target": True, "signal_half_life_days": 365, "seed_terms": ["alpha", "long"]}]
    return cfg


def _seed(conn):
    yti_rs_db.upsert_channel(conn, {"channel_id": "UC1", "niche": "n1"})
    for i in range(8):      # long-form baseline ~1000 views, short baseline ~50k views
        yti_rs_db.upsert_video(conn, {"video_id": f"L{i}", "channel_id": "UC1", "title": f"long {i}", "niche": "n1",
                                      "published_at": f"2026-0{1 + i // 4}-{10 + (i % 4) * 3:02d}T00:00:00+00:00",
                                      "views": 1000 + i, "likes": 50, "comment_count": 10,
                                      "duration_seconds": 900, "is_short": 0})
        yti_rs_db.upsert_video(conn, {"video_id": f"S{i}", "channel_id": "UC1", "title": f"short {i}", "niche": "n1",
                                      "published_at": f"2026-0{1 + i // 4}-{11 + (i % 4) * 3:02d}T00:00:00+00:00",
                                      "views": 50000 + i, "likes": 5000, "comment_count": 5,
                                      "duration_seconds": 40, "is_short": 1})
    yti_rs_db.upsert_video(conn, {"video_id": "LHIT", "channel_id": "UC1", "title": "long form winner", "niche": "n1",
                                  "published_at": "2026-07-14T00:00:00+00:00", "views": 6000, "likes": 300,
                                  "comment_count": 60, "duration_seconds": 900, "is_short": 0})
    yti_rs_db.upsert_video(conn, {"video_id": "SHIT", "channel_id": "UC1", "title": "viral short winner", "niche": "n1",
                                  "published_at": "2026-07-15T00:00:00+00:00", "views": 900000, "likes": 90000,
                                  "comment_count": 50, "duration_seconds": 40, "is_short": 1})


def test_register_demand_map_and_counts_are_long_form_only(tmp_home):
    conn = yti_rs_db.connect()
    _seed(conn)
    res = O.score_all(conn, _cfg(), now=NOW)
    assert res["buckets"] == {"long": 9, "short": 9}
    # both winners are real hits in their own buckets (nothing is thrown away) ...
    cls = {r["video_id"]: (r["class"], r["format_bucket"]) for r in yti_rs_db.rows(
        conn, "SELECT video_id, class, format_bucket FROM scores WHERE video_id IN ('LHIT','SHIT')")}
    assert cls == {"LHIT": ("strong_hit", "long"), "SHIT": ("strong_hit", "short")}
    # ... but the register shows only long-form
    reg = R.outlier_register(conn, classes=["strong_hit", "hit"])
    assert [r["video_id"] for r in reg["rows"]] == ["LHIT"] and reg["total"] == 1 and reg["bucket"] == "long"
    assert sum(reg["counts"].values()) == 9
    assert [r["video_id"] for r in R.outlier_register(conn, classes=["strong_hit"], bucket="short")["rows"]] == ["SHIT"]
    dm = R.demand_map(conn, _cfg())
    assert dm["hit_videos"] == 1
    c = yti_rs_db.counts(conn)
    assert (c["videos_long"], c["videos_short"], c["hits_long"], c["hits"], c["channels_long"]) == (9, 9, 1, 2, 1)
    conn.close()


def test_satisfaction_percentiles_rank_within_the_format(tmp_home):
    conn = yti_rs_db.connect()
    _seed(conn)
    O.score_all(conn, _cfg(), now=NOW)
    # the long winner has the best like rate among LONG videos (5% vs ~5%): it must not be
    # pushed down by Shorts with a 10% like rate, nor flagged paid because Shorts out-view it
    row = yti_rs_db.one(conn, "SELECT vs_percentile, organic_flag FROM scores WHERE video_id = 'LHIT'")
    assert row["organic_flag"] != "suspect_paid" and row["vs_percentile"] >= 50
    conn.close()


def test_rss_link_form_decides_when_duration_is_unknown(tmp_home):
    assert N.is_short_rss(None, "https://www.youtube.com/shorts/abc") is True
    assert N.is_short_rss(None, "https://www.youtube.com/watch?v=abc") is False
    assert N.is_short_rss(None, "") is None
    assert N.is_short_rss(120, "https://www.youtube.com/watch?v=abc") is True      # a known duration still wins
    conn = yti_rs_db.connect()
    payload = {"channel": {"channelId": "UC1", "title": "C"}, "results": [
        {"videoId": "w", "title": "W", "published": "2026-10-01T00:00:00+00:00", "viewCount": "10",
         "link": "https://www.youtube.com/watch?v=w"},
        {"videoId": "s", "title": "S", "published": "2026-10-01T00:00:00+00:00", "viewCount": "10",
         "link": "https://www.youtube.com/shorts/s"}]}
    S.ingest_latest(conn, payload, niche="n1")
    form = {r["video_id"]: r["is_short"] for r in yti_rs_db.rows(conn, "SELECT video_id, is_short FROM videos")}
    assert form == {"w": 0, "s": 1}
    # rows ingested before this rule (is_short NULL) are resolved from their raw payload at score time
    conn.execute("UPDATE videos SET is_short = NULL"); conn.commit()
    assert O.resolve_unknown_form(conn) == 2
    assert {r["video_id"]: r["is_short"] for r in yti_rs_db.rows(conn, "SELECT video_id, is_short FROM videos")} == form
    conn.close()


def test_crawl_never_scores_or_expands_a_short(tmp_home):
    conn = yti_rs_db.connect()
    yti_rs_db.upsert_channel(conn, {"channel_id": "UCx", "niche": "n1"})
    for i in range(6):
        yti_rs_db.upsert_video(conn, {"video_id": f"b{i}", "channel_id": "UCx", "title": "b", "niche": "n1",
                                      "views": 1000, "duration_seconds": 900, "is_short": 0})

    class T:
        def search(self, q, type_="video", continuation=None):
            return {"results": [
                {"type": "video", "videoId": "bigshort", "channelId": "UCx", "channelTitle": "x", "title": "short",
                 "viewCountText": "2M views", "publishedTimeText": "1 month ago", "lengthText": "0:45"},
                {"type": "video", "videoId": "biglong", "channelId": "UCx", "channelTitle": "x", "title": "long",
                 "viewCountText": "9K views", "publishedTimeText": "1 month ago", "lengthText": "14:00"}], "has_more": False}
    cfg = _cfg(); cfg["crawl"] = dict(DEFAULT_CONFIG["crawl"], term_variants=["{term}"], search_pages_per_term=1)
    crawler = C.Crawler(conn, cfg, T(), yti_rs_budget.Budget(conn, caps={"transcriptapi": 10}), run_id="r")
    children, yield_count = crawler.expand(C.Node("search_term", "alpha", "n1", 1))
    rows = {r["video_id"]: r for r in yti_rs_db.rows(conn, "SELECT video_id, is_short, provisional_multiple FROM videos")}
    assert rows["bigshort"]["is_short"] == 1 and rows["bigshort"]["provisional_multiple"] is None    # recorded, not scored
    assert rows["biglong"]["provisional_multiple"] == 9.0
    assert [(c.type, c.key) for c in children if c.type == "video"] == [("video", "biglong")] and yield_count == 1
    conn.close()


def test_register_sorts_on_any_column_in_either_direction(tmp_home):
    conn = yti_rs_db.connect()
    _seed(conn)
    O.score_all(conn, _cfg(), now=NOW)
    ids = lambda **kw: [r["video_id"] for r in R.outlier_register(conn, classes=None, limit=3, **kw)["rows"]]
    assert ids(sort="views", order="desc")[0] == "LHIT" and ids(sort="views", order="asc")[0] == "L0"
    assert ids(sort="title", order="asc")[0] == "L0" and ids(sort="title", order="desc")[0] == "LHIT"
    assert ids(sort="class", order="desc")[0] == "LHIT"            # strong_hit ranks highest
    assert ids(sort="projected_multiple")[0] == "LHIT"             # default direction is descending
    assert ids(sort="no-such-column")[0] == "LHIT"                 # unknown keys fall back safely
    assert all(v.startswith("L") for v in ids(sort="views", order="asc"))   # still long-form only
    conn.close()
