import yti_rs_budget
import yti_rs_clients as CL
import yti_rs_db
import yti_rs_snapshot as S


def _http_factory(script):
    """script: list of (status, headers, body) consumed in order."""
    calls = []

    def http(method, url, headers, body, timeout):
        calls.append((method, url, dict(headers), body))
        st, hd, bd = script.pop(0)
        return CL.HttpResponse(st, hd, bd)
    http.calls = calls
    return http


def test_transcriptapi_retry_then_success_ledger_and_header_auth(tmp_home):
    conn = yti_rs_db.connect()
    http = _http_factory([(429, {"Retry-After": "0"}, "{}"), (503, {}, "{}"), (200, {"X-RateLimit-Remaining": "250"}, '{"results": [], "has_more": false}')])
    slept = []
    budget = yti_rs_budget.Budget(conn, run_id="t")
    t = CL.TranscriptAPI("sk_test", http=http, budget=budget, sleep=slept.append, clock=lambda: 0.0)
    out = t.search("x")
    assert out == {"results": [], "has_more": False}
    assert len(http.calls) == 3 and http.calls[0][2]["Authorization"] == "Bearer sk_test"
    assert "token=" not in http.calls[0][1]
    rows = yti_rs_db.rows(conn, "SELECT status, credits FROM api_usage ORDER BY id")
    assert [(r["status"], r["credits"]) for r in rows] == [(429, 0), (503, 0), (200, 1)]   # charged only on 200
    # second identical call is served from the 24h cache: no HTTP, no credit
    t.search("x")
    assert len(http.calls) == 3 and budget.spent("transcriptapi") == 1
    conn.close()


def test_terminal_statuses_and_402(tmp_home):
    conn = yti_rs_db.connect()
    t = CL.TranscriptAPI("sk_test", http=_http_factory([(404, {}, '{"error":"nope"}')]), conn=conn, sleep=lambda s: None)
    try:
        t.transcript("v")
        assert False
    except CL.ClientError as exc:
        assert exc.status == 404 and not isinstance(exc, CL.CreditsExhausted)
    t2 = CL.TranscriptAPI("sk_test", http=_http_factory([(402, {}, "{}")]), conn=conn, sleep=lambda s: None)
    try:
        t2.search("q")
        assert False
    except CL.CreditsExhausted:
        pass
    conn.close()


def test_budget_cap_blocks_before_call(tmp_home):
    conn = yti_rs_db.connect()
    http = _http_factory([])
    budget = yti_rs_budget.Budget(conn, caps={"transcriptapi": 0})
    t = CL.TranscriptAPI("sk_test", http=http, budget=budget)
    try:
        t.search("q")
        assert False
    except yti_rs_budget.BudgetExceeded:
        assert http.calls == []
    conn.close()


def test_apify_sync_then_async_fallback(tmp_home):
    conn = yti_rs_db.connect()
    script = [(408, {}, "{}"),                                               # sync timed out
              (201, {}, '{"data": {"id": "run1"}}'),                         # async start
              (200, {}, '{"data": {"status": "RUNNING"}}'),
              (200, {}, '{"data": {"status": "SUCCEEDED", "defaultDatasetId": "ds1"}}'),
              (200, {}, '[{"id": "v1", "viewCount": 5}]')]
    http = _http_factory(script)
    a = CL.Apify("apify_api_test", http=http, conn=conn, sleep=lambda s: None, clock=lambda: 0.0)
    items = a.scrape_videos("streamers/youtube-scraper", ["https://www.youtube.com/watch?v=v1"])
    assert items == [{"id": "v1", "viewCount": 5}]
    assert "streamers~youtube-scraper/run-sync-get-dataset-items" in http.calls[0][1]
    assert "/acts/streamers~youtube-scraper/runs" in http.calls[1][1]
    assert all("token=" not in c[1] for c in http.calls) and http.calls[0][2]["Authorization"] == "Bearer apify_api_test"
    conn.close()


def test_snapshot_ingests_exact_rows(tmp_home):
    conn = yti_rs_db.connect()
    payload = {"channel": {"channelId": "UC1", "title": "Dan"}, "results": [
        {"videoId": "a", "title": "A", "published": "2026-09-11T16:01:32+00:00", "viewCount": "96454"},
        {"videoId": "b", "title": "B", "published": "2026-09-01T00:00:00+00:00", "viewCount": "1000"},
        {"videoId": "", "title": "broken", "viewCount": None}]}

    class T:
        def latest(self, ident):
            return payload
    s = S.run_snapshot(conn, T(), ["@dan"], default_niche="n1", durations={"a": 900})
    assert s["channels"] == 1 and s["snapshots"] == 2 and s["new_videos"] == 2
    v = yti_rs_db.one(conn, "SELECT * FROM videos WHERE video_id = 'a'")
    assert v["views"] == 96454 and v["views_approx"] == 0 and v["published_approx"] == 0 and v["niche"] == "n1"
    assert v["duration_seconds"] == 900 and v["is_short"] == 0
    assert yti_rs_db.one(conn, "SELECT is_short FROM videos WHERE video_id = 'b'")["is_short"] is None
    assert conn.execute("SELECT COUNT(*) FROM quarantine").fetchone()[0] == 1
    ch = yti_rs_db.one(conn, "SELECT * FROM channels WHERE channel_id = 'UC1'")
    assert ch["is_tracked"] == 1 and ch["handle"] == "@dan"
    # an approximate Tier-1 observation never downgrades the exact row
    yti_rs_db.upsert_video(conn, {"video_id": "a", "views": 90000, "views_approx": 1, "published_at": "x", "published_approx": 1})
    v2 = yti_rs_db.one(conn, "SELECT views, published_at FROM videos WHERE video_id = 'a'")
    assert v2["views"] == 96454 and v2["published_at"].startswith("2026-09-11")
    # every snapshot is a timestamped reading: a second run the same day is a second point
    S.run_snapshot(conn, T(), ["@dan"])
    rows = yti_rs_db.rows(conn, "SELECT captured_at FROM video_snapshots WHERE video_id='a' ORDER BY captured_at")
    assert len(rows) == 2 and all(len(r["captured_at"]) > 10 and "T" in r["captured_at"] for r in rows)
    yti_rs_db.add_snapshot(conn, "a", 97000, captured_at="2099-01-01")
    assert len(yti_rs_db.rows(conn, "SELECT * FROM video_snapshots WHERE video_id='a'")) == 3
    conn.close()


def test_continuation_is_the_only_param_on_follow_up_pages(tmp_home):
    conn = yti_rs_db.connect()
    http = _http_factory([(200, {}, '{"results": [], "has_more": true, "continuation_token": "TOK"}'),
                          (200, {}, '{"results": [], "has_more": false}')])
    t = CL.TranscriptAPI("sk_test", http=http, conn=conn, sleep=lambda s: None, clock=lambda: 0.0)
    first = t.channel_videos("@dan")
    t.channel_videos("@dan", first["continuation_token"])
    assert "channel=%40dan" in http.calls[0][1] and "continuation" not in http.calls[0][1]
    assert "continuation=TOK" in http.calls[1][1] and "channel=" not in http.calls[1][1]
    conn.close()
