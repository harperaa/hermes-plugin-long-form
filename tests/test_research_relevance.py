"""Topical relevance: a niche label says which crawl found a video, not what
it is about. Off-topic videos are tagged (never deleted) and kept out of the
views; the crawler stops expanding general-interest and oversized channels."""
from datetime import datetime, timezone

import yti_rs_budget
import yti_rs_crawl as C
import yti_rs_db
import yti_rs_enrich as E
import yti_rs_outliers as O
import yti_rs_relevance as R
import yti_rs_report as RP
from yti_rs_config import DEFAULT_CONFIG

NOW = datetime(2026, 10, 6, tzinfo=timezone.utc)
SEC = {"name": "sec", "is_target": True, "signal_half_life_days": 180,
       "seed_terms": ["prompt injection explained", "how to secure ai agents"],
       "outcome_terms": ["get hired", "make money"], "mechanism_terms": ["red teaming"],
       "topic_terms": ["hacking", "exploit", "malware"]}


def _cfg():
    cfg = dict(DEFAULT_CONFIG)
    cfg["crawl"] = dict(DEFAULT_CONFIG["crawl"], term_variants=["{term}"], search_pages_per_term=1,
                        channel_pages_per_channel=1, recommendations_per_video=2)
    cfg["niches"] = [SEC]
    return cfg


def test_vocabulary_and_matching():
    v = R.niche_vocab(SEC)
    assert {"prompt", "injection", "secure", "ai", "agent", "teaming", "hacking", "exploit", "malware"} <= v
    assert not ({"how", "to", "explained", "red", "hired", "money"} & v)       # filler, ambiguous, outcome words
    assert R.title_matches("Why AI Agents keep breaking loose", v)             # plural folded
    assert R.title_matches("This EXPLOIT is insane", v)
    assert not R.title_matches("BRICS Summit 2026 LIVE | PM Modi welcomes leaders", v)
    assert R.channel_on_topic(["a exploit", "b", "c", "d"], v) is None         # too few titles to judge
    assert R.channel_on_topic(["exploit 1", "malware 2", "x", "y", "z"], v) is True     # 40% >= 20%
    assert R.channel_on_topic(["exploit 1"] + ["news"] * 9, v) is False        # 10% < 20%
    assert DEFAULT_CONFIG["crawl"]["max_subscribers"] == 100000


def _news_channel(conn):
    yti_rs_db.upsert_channel(conn, {"channel_id": "UCnews", "handle": "@news", "niche": "sec", "subscriber_count": 19_900_000})
    yti_rs_db.upsert_channel(conn, {"channel_id": "UCsec", "handle": "@sec", "niche": "sec", "subscriber_count": 60_000})
    # one genuinely relevant video, one that only shares a broad word ("exploit"), the rest plain news
    titles = ["Why AI agents keep breaking loose", "Smugglers exploit border chaos"] + [f"World news bulletin {i}" for i in range(9)]
    for i, t in enumerate(titles):          # 2 of 11 titles (18%) mention the vocabulary -> off-topic channel
        yti_rs_db.upsert_video(conn, {"video_id": f"n{i}", "channel_id": "UCnews", "title": t, "niche": "sec",
                                      "published_at": f"2026-09-{10 + i:02d}T00:00:00+00:00",
                                      "views": 90000 if i == 10 else 5000 + i, "duration_seconds": 600, "is_short": 0})
    for i in range(9):
        yti_rs_db.upsert_video(conn, {"video_id": f"s{i}", "channel_id": "UCsec", "niche": "sec",
                                      "title": "Buffer overflow exploit walkthrough" if i % 2 else "My desk setup tour",
                                      "published_at": f"2026-09-{10 + i:02d}T00:00:00+00:00",
                                      "views": 30000 if i == 8 else 1000 + i, "duration_seconds": 600, "is_short": 0})


def test_off_topic_videos_are_tagged_not_deleted_and_leave_the_views(tmp_home):
    conn = yti_rs_db.connect()
    _news_channel(conn)
    res = O.score_all(conn, _cfg(), now=NOW)
    assert res["out_of_niche"] == 10 and res["off_topic_channels"] == 1
    assert conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0] == 20            # nothing deleted
    flags = dict(conn.execute("SELECT video_id, in_niche FROM scores").fetchall())
    assert flags["n0"] == 1                      # the one relevant video on the news channel stays
    assert flags["n1"] == 0                      # a lone broad word on an off-topic channel is not enough
    assert flags["n10"] == 0                     # its news hit does not
    assert all(flags[f"s{i}"] == 1 for i in range(9))    # on-topic channel: every video stays, desk tour included
    ids = [r["video_id"] for r in RP.outlier_register(conn, classes=["strong_hit", "hit"])["rows"]]
    assert ids == ["s8"]
    assert [p["id"] for p in RP.supply_demand(conn, _cfg(), now=NOW)["points"] if p["id"].startswith("n")] == []
    c = yti_rs_db.counts(conn)
    assert c["videos_out_of_niche"] == 10 and c["videos_long"] == 10 and c["hits_long"] == 1
    # a channel the operator follows is in-niche by choice
    res = O.score_all(conn, _cfg(), now=NOW, followed={"@news"})
    assert res["out_of_niche"] == 0
    conn.close()


def test_channel_size_is_a_filter_that_never_hides_unknown_or_followed(tmp_home):
    conn = yti_rs_db.connect()
    _news_channel(conn)
    yti_rs_db.upsert_channel(conn, {"channel_id": "UCbig", "handle": "@bigsec", "niche": "sec", "subscriber_count": 2_600_000})
    yti_rs_db.upsert_channel(conn, {"channel_id": "UCunk", "handle": "@unknownsec", "niche": "sec"})
    for cid in ("UCbig", "UCunk"):
        for i in range(9):
            yti_rs_db.upsert_video(conn, {"video_id": f"{cid}{i}", "channel_id": cid, "niche": "sec", "title": "malware analysis",
                                          "published_at": f"2026-09-{10 + i:02d}T00:00:00+00:00",
                                          "views": 50000 if i == 8 else 1000 + i, "duration_seconds": 600, "is_short": 0})
    O.score_all(conn, _cfg(), now=NOW)
    hits = lambda **kw: sorted(r["video_id"] for r in RP.outlier_register(conn, classes=["strong_hit", "hit"], **kw)["rows"])
    assert hits() == ["UCbig8", "UCunk8", "s8"]                                   # no size filter
    assert hits(max_subs=100000, min_subs=1000) == ["UCunk8", "s8"]               # big channel filtered, unknown kept
    conn.execute("UPDATE channels SET is_tracked = 1 WHERE channel_id = 'UCbig'"); conn.commit()
    assert hits(max_subs=100000) == ["UCbig8", "UCunk8", "s8"]                    # followed/tracked always shown
    conn.close()


class FakeTAPI:
    def __init__(self, latest=None, channels=None, sizes=None, searches=None):
        self.latest_map, self.channels, self.sizes, self.searches = latest or {}, channels or {}, sizes or {}, searches or {}
        self.calls = []

    def latest(self, ident):
        self.calls.append(("latest", ident)); return self.latest_map.get(ident, {})

    def channel_videos(self, ident, continuation=None):
        self.calls.append(("channel_videos", ident)); return {"results": self.channels.get(ident, []), "has_more": False}

    def search(self, q, type_="video", continuation=None):
        self.calls.append(("search", type_, q))
        if type_ == "channel":
            return {"results": self.sizes.get(q, [])}
        return {"results": self.searches.get(q, []), "has_more": False}


def _latest(cid, titles):
    return {"channel": {"channelId": cid, "title": cid}, "results": [
        {"videoId": f"{cid}{i}", "channelId": cid, "title": t, "published": "2026-10-01T00:00:00+00:00",
         "viewCount": "1000", "link": f"https://www.youtube.com/watch?v={cid}{i}"} for i, t in enumerate(titles)]}


def _crawler(conn, tapi):
    return C.Crawler(conn, _cfg(), tapi, yti_rs_budget.Budget(conn, caps={"transcriptapi": 50}), run_id="r")


def test_crawler_does_not_expand_a_general_interest_channel(tmp_home):
    conn = yti_rs_db.connect()
    yti_rs_db.upsert_channel(conn, {"channel_id": "UCnews", "handle": "@news", "niche": "sec"})
    tapi = FakeTAPI(latest={"@news": _latest("UCnews", ["AI agents escaped"] + [f"World news {i}" for i in range(11)])})
    cr = _crawler(conn, tapi)
    assert cr.expand(C.Node("channel", "UCnews", "sec", 2)) == ([], 0)
    assert cr.stats["skipped_offtopic"] == 1
    assert not [c for c in tapi.calls if c[0] == "channel_videos"]              # no paid catalogue page
    assert not [c for c in tapi.calls if c[0] == "search"]                      # and no size lookup either
    conn.close()


def test_crawler_applies_the_subscriber_band_before_paying_for_a_catalogue(tmp_home):
    conn = yti_rs_db.connect()
    yti_rs_db.upsert_channel(conn, {"channel_id": "UCbig", "handle": "@bigsec", "niche": "sec"})
    titles = [f"malware analysis {i}" for i in range(12)]
    tapi = FakeTAPI(latest={"@bigsec": _latest("UCbig", titles)},
                    sizes={"@bigsec": [{"type": "channel", "channelId": "UCbig", "handle": "@bigsec",
                                        "subscriberCount": "2.64M subscribers"}]})
    cr = _crawler(conn, tapi)
    assert cr.expand(C.Node("channel", "UCbig", "sec", 2)) == ([], 0) and cr.stats["skipped_size"] == 1
    assert not [c for c in tapi.calls if c[0] == "channel_videos"]
    row = yti_rs_db.one(conn, "SELECT subscriber_count, subscriber_approx FROM channels WHERE channel_id = 'UCbig'")
    assert (row["subscriber_count"], row["subscriber_approx"]) == (2_640_000, 1)
    # the same channel, followed by the operator, is expanded regardless of size
    conn.execute("UPDATE channels SET is_tracked = 1 WHERE channel_id = 'UCbig'"); conn.commit()
    tapi2 = FakeTAPI(latest={"@bigsec": _latest("UCbig", titles)})
    cr2 = _crawler(conn, tapi2)
    cr2.expand(C.Node("channel", "UCbig", "sec", 2))
    assert [c for c in tapi2.calls if c[0] == "channel_videos"] and cr2.stats["skipped_size"] == 0
    conn.close()


def test_recommendations_that_drift_off_topic_are_recorded_but_not_followed(tmp_home):
    conn = yti_rs_db.connect()
    yti_rs_db.upsert_channel(conn, {"channel_id": "UCa", "handle": "@a", "niche": "sec"})
    yti_rs_db.upsert_video(conn, {"video_id": "src", "channel_id": "UCa", "title": "Prompt injection is everywhere",
                                  "niche": "sec", "views": 9000, "duration_seconds": 600, "is_short": 0})
    for cid in ("UCx", "UCy"):                                  # two channels with a known ~1,000-view baseline
        yti_rs_db.upsert_channel(conn, {"channel_id": cid, "niche": "sec"})
        for i in range(5):
            yti_rs_db.upsert_video(conn, {"video_id": f"{cid}b{i}", "channel_id": cid, "title": "b", "niche": "sec",
                                          "views": 1000, "duration_seconds": 600, "is_short": 0})
    res = lambda vid, cid, title: {"type": "video", "videoId": vid, "channelId": cid, "channelTitle": cid, "title": title,
                                   "viewCountText": "9K views", "publishedTimeText": "1 month ago", "lengthText": "12:00"}
    tapi = FakeTAPI(searches={"Prompt injection is everywhere": [
        res("ontopic", "UCx", "Jailbreak exploit against AI agents"), res("drift", "UCy", "Houthi advance tightens grip")]})
    cr = _crawler(conn, tapi)
    cr.visited.add(("channel", "UCa"))
    children, yield_count = cr.expand(C.Node("video", "src", "sec", 2))
    assert [(c.type, c.key) for c in children] == [("video", "ontopic")] and yield_count == 1
    rows = {r["video_id"]: r["provisional_multiple"] for r in yti_rs_db.rows(conn, "SELECT video_id, provisional_multiple FROM videos")}
    assert rows["ontopic"] == 9.0 and "drift" in rows and rows["drift"] is None
    conn.close()


def test_sizes_job_fills_missing_subscriber_counts_most_useful_first(tmp_home):
    conn = yti_rs_db.connect()
    _news_channel(conn)
    conn.execute("UPDATE channels SET subscriber_count = NULL"); conn.commit()
    O.score_all(conn, _cfg(), now=NOW)
    tapi = FakeTAPI(sizes={"@sec": [{"type": "channel", "channelId": "UCsec", "handle": "@sec", "subscriberCount": "61.2K subscribers"},
                                    {"type": "channel", "channelId": "UCnews", "handle": "@news", "subscriberCount": "19.9M subscribers"}]})
    out = E.run_sizes(conn, _cfg(), tapi)
    subs = dict(conn.execute("SELECT channel_id, subscriber_count FROM channels").fetchall())
    assert subs == {"UCsec": 61200, "UCnews": 19_900_000}
    # only the in-niche channel needed a lookup; its one search also sized the other channel it returned
    assert out["candidates"] == 1 and out["sized"] == 1 and len([c for c in tapi.calls if c[0] == "search"]) == 1
    conn.close()
