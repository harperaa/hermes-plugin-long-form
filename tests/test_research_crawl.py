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
