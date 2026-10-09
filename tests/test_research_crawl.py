"""Recorded-fixture crawl: node tree shape, provisional outliers, barren pruning."""
import yti_rs_budget
import yti_rs_db
import yti_rs_crawl as C
from yti_rs_config import DEFAULT_CONFIG


class FakeTAPI:
    """Plays canned TranscriptAPI responses; records every call."""

    def __init__(self, searches, channels, latest=None):
        self.searches, self.channels, self.latest_map = searches, channels, latest or {}
        self.calls = []

    def search(self, q, type_="video", continuation=None):
        self.calls.append(("search", q))
        return {"results": self.searches.get(q.lower(), []), "has_more": False}

    def latest(self, channel):
        self.calls.append(("latest", channel))
        return self.latest_map.get(channel, {})

    def resolve(self, handle):
        self.calls.append(("resolve", handle))
        return {"channel_id": "UC_" + handle.lstrip("@")}

    def channel_videos(self, channel, continuation=None):
        self.calls.append(("channel_videos", channel))
        return {"results": self.channels.get(channel, []), "has_more": False}


def _sr(vid, cid, views, title="t", age="1 month ago", handle=None):
    return {"type": "video", "videoId": vid, "channelId": cid, "channelTitle": cid, "channelHandle": handle or "@" + cid,
            "title": title, "viewCountText": views, "publishedTimeText": age, "lengthText": "12:00"}


def _cv(vid, views, idx):
    return {"videoId": vid, "title": f"{vid} title", "viewCountText": views, "lengthText": "12:00", "index": str(idx)}


def _cfg():
    cfg = dict(DEFAULT_CONFIG)
    cfg["crawl"] = dict(DEFAULT_CONFIG["crawl"], term_variants=["{term}"], search_pages_per_term=1,
                        channel_pages_per_channel=1, outliers_to_expand_per_node=2, prune_after_barren_nodes=2,
                        recommendations_per_video=0, max_depth=3)
    cfg["niches"] = [{"name": "n1", "is_target": True, "seed_terms": ["alpha"], "signal_half_life_days": 365}]
    return cfg


def test_crawl_records_outliers_and_prunes_barren_branch(tmp_home):
    conn = yti_rs_db.connect()
    # "alpha" → two channels; UCgood has a 10× outlier in its catalogue; UCdead has a flat one
    searches = {"alpha": [_sr("a1", "UCgood", "50K views"), _sr("a2", "UCdead", "40K views")]}
    channels = {"@UCgood": [_cv("g0", "100K views", 0)] + [_cv(f"g{i}", "10K views", i) for i in range(1, 12)],
                "@UCdead": [_cv(f"d{i}", "9K views", i) for i in range(12)]}
    tapi = FakeTAPI(searches, channels)
    budget = yti_rs_budget.Budget(conn, caps={"transcriptapi": 100})
    crawler = C.Crawler(conn, _cfg(), tapi, budget, run_id="run-test")
    crawler.seed()
    res = crawler.run()
    assert res["stop_reason"] == "exhausted"
    nodes = yti_rs_db.rows(conn, "SELECT node_type, node_key, status, yield_count, depth FROM crawl_nodes ORDER BY id")
    types = [(n["node_type"], n["node_key"]) for n in nodes]
    assert ("seed_term", "alpha") in types and ("search_term", "alpha") in types
    assert ("channel", "UCgood") in types and ("channel", "UCdead") in types
    good = [n for n in nodes if n["node_key"] == "UCgood"][0]
    assert good["yield_count"] == 1 and good["status"] == "done"
    g0 = yti_rs_db.one(conn, "SELECT provisional_multiple, views_approx, published_at FROM videos WHERE video_id = 'g0'")
    assert g0["provisional_multiple"] == 10.0 and g0["views_approx"] == 1
    # the outlier became a video node (depth 3) and no scores row exists yet (provisional ≠ §8)
    assert ("video", "g0") in types
    assert conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0] == 0
    conn.close()


def test_barren_pruning_drops_siblings(tmp_home):
    conn = yti_rs_db.connect()
    # search returns 4 flat channels; after 2 zero-yield channel expansions the rest are pruned
    searches = {"alpha": [_sr(f"v{i}", f"UC{i}", "10K views") for i in range(4)]}
    channels = {f"@UC{i}": [_cv(f"c{i}_{j}", "10K views", j) for j in range(10)] for i in range(4)}
    cfg = _cfg()
    cfg["crawl"]["outliers_to_expand_per_node"] = 4
    tapi = FakeTAPI(searches, channels)
    crawler = C.Crawler(conn, cfg, tapi, yti_rs_budget.Budget(conn, caps={"transcriptapi": 100}), run_id="r2")
    crawler.seed()
    res = crawler.run()
    expanded = [c for c in tapi.calls if c[0] == "channel_videos"]
    assert len(expanded) == 2 and res["pruned"] == 2
    assert yti_rs_db.one(conn, "SELECT status FROM crawl_nodes WHERE node_type='search_term'")["status"] == "pruned"
    conn.close()


def test_budget_stop_persists_stack_and_resumes(tmp_home):
    conn = yti_rs_db.connect()
    searches = {"alpha": [_sr("a1", "UCgood", "50K views"), _sr("a2", "UCdead", "40K views")]}
    channels = {"@UCgood": [_cv("g0", "100K views", 0)] + [_cv(f"g{i}", "10K views", i) for i in range(1, 12)],
                "@UCdead": [_cv(f"d{i}", "9K views", i) for i in range(12)]}
    tapi = FakeTAPI(searches, channels)
    budget = yti_rs_budget.Budget(conn, caps={"transcriptapi": 1})     # only the search fits
    for ep in ("search", "channel_videos"):
        pass
    # make the fake charge the ledger like the real client does
    orig_search, orig_cv = tapi.search, tapi.channel_videos
    tapi.search = lambda q, t="video", c=None: (budget.check("transcriptapi", 1), budget.record("transcriptapi", "search", 1, 200), orig_search(q, t, c))[2]
    tapi.channel_videos = lambda ch, c=None: (budget.check("transcriptapi", 1), budget.record("transcriptapi", "channel/videos", 1, 200), orig_cv(ch, c))[2]
    crawler = C.Crawler(conn, _cfg(), tapi, budget, run_id="r3")
    crawler.seed()
    res = crawler.run()
    assert res["stop_reason"].startswith("transcriptapi budget reached") and res["remaining_stack"] > 0
    assert yti_rs_db.one(conn, "SELECT status FROM crawl_runs WHERE run_id='r3'")["status"] == "paused"
    # resume with a bigger cap finishes the run
    budget2 = yti_rs_budget.Budget(conn, run_id="r3", caps={"transcriptapi": 100})
    tapi2 = FakeTAPI(searches, channels)
    crawler2 = C.Crawler(conn, _cfg(), tapi2, budget2)
    assert crawler2.resume("r3")
    res2 = crawler2.run()
    assert res2["stop_reason"] == "exhausted"
    assert yti_rs_db.one(conn, "SELECT status FROM crawl_runs WHERE run_id='r3'")["status"] == "done"
    conn.close()


def test_plan_dry_run_costs_nothing():
    plan = C.plan(_cfg())
    assert plan["credits_worst_case"] > 0 and plan["niches"][0]["seed_terms"] == 1


def test_estimate_catalog_dates():
    rows = [{"catalog_index": 0, "published_at": "2026-09-10T00:00:00+00:00"},
            {"catalog_index": 1, "published_at": "2026-09-03T00:00:00+00:00"},
            {"catalog_index": 2, "published_at": None}, {"catalog_index": 4, "published_at": None}]
    out = C.estimate_catalog_dates(rows)
    assert out[2]["published_at"].startswith("2026-08-27") and out[2]["published_approx"] == 1
    assert out[3]["published_at"].startswith("2026-08-13") and out[3]["published_granularity_days"] >= 7


def test_followed_channels_seed_as_channel_nodes(tmp_home):
    conn = yti_rs_db.connect()
    channels = {"@fol": [_cv("f0", "50K views", 0)] + [_cv(f"f{i}", "5K views", i) for i in range(1, 10)]}
    tapi = FakeTAPI({"alpha": []}, channels)
    crawler = C.Crawler(conn, _cfg(), tapi, yti_rs_budget.Budget(conn, caps={"transcriptapi": 100}), run_id="r4")
    crawler.seed()
    assert crawler.seed_channels(["@fol", "fol"]) == 1          # deduped, resolved for free
    assert crawler.stack[-1].type == "seed_term"                 # search still pops first
    res = crawler.run()
    assert res["stop_reason"] == "exhausted"
    ch = yti_rs_db.one(conn, "SELECT niche, is_tracked FROM channels WHERE channel_id = 'UC_fol'")
    assert ch["niche"] == "n1" and ch["is_tracked"] == 1
    assert yti_rs_db.one(conn, "SELECT provisional_multiple FROM videos WHERE video_id = 'f0'")["provisional_multiple"] == 10.0
    conn.close()


class SortingTAPI(FakeTAPI):
    """Channel pages like the real API: page 1 says has_more for big
    catalogues; sort=popular is a separate ~30-row feed."""

    def __init__(self, searches, channels, popular, big=()):
        super().__init__(searches, channels)
        self.popular, self.big = popular, set(big)

    def channel_videos(self, channel, continuation=None, *, sort=None):
        self.calls.append(("channel_videos", channel, sort))
        if sort == "popular":
            return {"results": self.popular.get(channel, []), "has_more": True, "continuation_token": "x"}
        return {"results": self.channels.get(channel, []), "has_more": channel in self.big,
                "continuation_token": "tok" if channel in self.big else None}


def test_second_channel_page_is_the_popular_feed(tmp_home):
    conn = yti_rs_db.connect()
    searches = {"alpha": [_sr("a1", "UCbig", "50K views"), _sr("a2", "UCsmall", "40K views")]}
    channels = {"@UCbig": [_cv(f"b{i}", "10K views", i) for i in range(12)],
                "@UCsmall": [_cv(f"s{i}", "10K views", i) for i in range(12)]}
    popular = {"@UCbig": [dict(_cv("b_old_hit", "900K views", 250), publishedTimeText="3 years ago"),
                          _cv("b1", "10K views", 1)]}                      # b1 is already on page 1
    tapi = SortingTAPI(searches, channels, popular, big=["@UCbig"])
    cfg = _cfg()
    cfg["crawl"]["channel_pages_per_channel"] = 2
    crawler = C.Crawler(conn, cfg, tapi, yti_rs_budget.Budget(conn, caps={"transcriptapi": 100}), run_id="r_pop")
    crawler.seed()
    crawler.run()
    pages = [c for c in tapi.calls if c[0] == "channel_videos"]
    assert ("channel_videos", "@UCbig", None) in pages and ("channel_videos", "@UCbig", "popular") in pages
    assert ("channel_videos", "@UCsmall", "popular") not in pages         # whole catalogue fit on page 1: 1 credit
    hit = yti_rs_db.one(conn, "SELECT * FROM videos WHERE video_id = 'b_old_hit'")
    assert hit["discovered_via"] == "channel_popular" and hit["catalog_index"] is None
    assert hit["published_at"].startswith("20") and hit["published_approx"] == 1
    assert hit["provisional_multiple"] == 90.0                             # against page 1's typical upload
    assert yti_rs_db.one(conn, "SELECT COUNT(*) AS n FROM videos WHERE video_id = 'b1'")["n"] == 1
    assert yti_rs_db.one(conn, "SELECT result_hash, new_count FROM crawl_nodes WHERE node_key = 'UCbig'")["new_count"] == 13
    conn.close()


def test_known_channels_refresh_free_and_fresh_nodes_are_skipped(tmp_home):
    conn = yti_rs_db.connect()
    searches = {"alpha": [_sr("a1", "UCgood", "50K views")]}
    # titles mention the niche so the relevance gate keeps the channel on later runs
    channels = {"@UCgood": [dict(_cv(f"g{i}", "10K views", i), title=f"alpha lesson {i}") for i in range(12)]}
    tapi = FakeTAPI(searches, channels)
    cfg = _cfg()
    cfg["crawl"]["search_pages_per_term"] = 2
    crawler = C.Crawler(conn, cfg, tapi, yti_rs_budget.Budget(conn, caps={"transcriptapi": 100}), run_id="r_first")
    crawler.seed()
    first = crawler.run()
    assert [c for c in tapi.calls if c[0] == "channel_videos"] == [("channel_videos", "@UCgood")]

    # a second run the same day: the search is inside the refresh window and
    # costs nothing; nothing is paged again
    tapi2 = FakeTAPI(searches, channels)
    crawler2 = C.Crawler(conn, cfg, tapi2, yti_rs_budget.Budget(conn, caps={"transcriptapi": 100}), run_id="r_same_day")
    crawler2.seed()
    second = crawler2.run()
    assert second["skipped_fresh"] == 1 and not [c for c in tapi2.calls if c[0] in ("search", "channel_videos")]
    assert yti_rs_db.one(conn, "SELECT status FROM crawl_nodes WHERE run_id='r_same_day' AND node_type='search_term'")["status"] == "skipped"

    # the next day: the seed search runs again (new videos can only arrive
    # through searches), but the channel we already paged is refreshed from
    # the free channel/latest call only — and a new upload that beats the
    # baseline still becomes an outlier node
    from datetime import datetime, timedelta, timezone
    later = datetime.now(timezone.utc) + timedelta(days=1, hours=2)
    latest = {"@UCgood": {"results": [{"videoId": "g_new", "title": "alpha: the new hit", "viewCount": "120000",
                                       "published": later.isoformat(), "link": "https://youtube.com/watch?v=g_new"}]}}
    tapi3 = FakeTAPI(searches, channels, latest)
    crawler3 = C.Crawler(conn, cfg, tapi3, yti_rs_budget.Budget(conn, caps={"transcriptapi": 100}), run_id="r_later", now=later)
    crawler3.seed()
    third = crawler3.run()
    assert ("latest", "@UCgood") in tapi3.calls and not [c for c in tapi3.calls if c[0] == "channel_videos"]
    assert tapi3.calls.count(("search", "alpha")) == 1                   # a repeated search re-reads page 1 only
    assert third["channels_refreshed_free"] == 1
    new = yti_rs_db.one(conn, "SELECT * FROM videos WHERE video_id = 'g_new'")
    assert new["views"] == 120000 and new["provisional_multiple"] == 12.0
    assert yti_rs_db.one(conn, "SELECT status, new_count FROM crawl_nodes WHERE run_id='r_later' AND node_key='UCgood'")["new_count"] == 1
    conn.close()
