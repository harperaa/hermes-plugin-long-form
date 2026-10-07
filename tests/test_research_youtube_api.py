"""YouTube Data API provider: TranscriptAPI-shaped answers, quota accounting,
and the router's fallback to TranscriptAPI."""
import json

import pytest

import yti_rs_budget
import yti_rs_clients as C
import yti_rs_db
from yti_rs_logging import redact


def _resp(status, body):
    return C.HttpResponse(status, {}, json.dumps(body))


class FakeGoogle:
    """Answers Data API requests from canned tables; records every resource hit."""

    def __init__(self):
        self.calls = []
        self.quota_after = None            # raise quotaExceeded after N calls

    def __call__(self, method, url, headers, body, timeout):
        assert "key=" not in url and headers.get("X-Goog-Api-Key") == "AIzaTESTKEY0000000000000000000000000000"
        from urllib.parse import urlparse, parse_qs
        u = urlparse(url); q = {k: v[0] for k, v in parse_qs(u.query).items()}
        res = u.path.rsplit("/", 1)[-1]
        self.calls.append((res, q))
        if self.quota_after is not None and len(self.calls) > self.quota_after:
            return _resp(403, {"error": {"errors": [{"reason": "quotaExceeded"}], "message": "quota"}})
        if res == "search":
            if q.get("type") == "channel":
                return _resp(200, {"items": [{"id": {"channelId": "UCaaaaaaaaaaaaaaaaaaaaaa"}}]})
            return _resp(200, {"items": [{"id": {"videoId": "v1"}}, {"id": {"videoId": "v2"}}],
                               "nextPageToken": "P2" if not q.get("pageToken") else None})
        if res == "videos":
            ids = q["id"].split(",")
            return _resp(200, {"items": [{"id": i, "snippet": {"channelId": "UCaaaaaaaaaaaaaaaaaaaaaa", "channelTitle": "Chan",
                                                                "title": f"title {i}", "publishedAt": "2026-09-30T12:00:00Z",
                                                                "thumbnails": {"high": {"url": "u", "width": 480, "height": 360}}},
                                          "statistics": {"viewCount": "12345"}, "contentDetails": {"duration": "PT1H2M3S"}}
                                         for i in ids]})
        if res == "channels":
            return _resp(200, {"items": [{"id": "UCaaaaaaaaaaaaaaaaaaaaaa", "snippet": {"title": "Chan", "customUrl": "@chan"},
                                          "statistics": {"subscriberCount": "71234"},
                                          "contentDetails": {"relatedPlaylists": {"uploads": "UUaaaaaaaaaaaaaaaaaaaaaa"}}}]})
        if res == "playlistItems":
            page = q.get("pageToken")
            items = [{"contentDetails": {"videoId": f"u{page or 0}_{i}"}, "snippet": {"position": i}} for i in range(3)]
            return _resp(200, {"items": items, "nextPageToken": None if page == "B" else ("B" if page == "A" else "A")})
        return _resp(404, {})


def _yt(conn, budget=None):
    return C.YouTubeData("AIzaTESTKEY0000000000000000000000000000", http=FakeGoogle(), conn=conn, budget=budget)


def test_duration_and_redaction():
    assert C._duration_text("PT1H2M3S") == "1:02:03" and C._duration_text("PT4M5S") == "4:05" and C._duration_text("PT45S") == "0:45"
    assert C._duration_text("") is None
    assert "AIzaTESTKEY" not in redact("key AIzaTESTKEY0000000000000000000000000000 leaked")


def test_search_is_shaped_like_transcriptapi_and_exact(tmp_home):
    conn = yti_rs_db.connect()
    yt = _yt(conn)
    out = yt.search("alpha")
    r = out["results"][0]
    assert r["videoId"] == "v1" and r["viewCountText"] == "12345" and r["lengthText"] == "1:02:03"
    assert r["publishedTimeText"].startswith("2026-09-30") and r["_exact"] is True and r["channelId"].startswith("UC")
    assert out["has_more"] and out["continuation_token"] == "P2"
    assert [c[0] for c in yt.http.calls] == ["search", "videos"]
    assert yt.units_used_today() == 101                              # 100 for search + 1 for videos
    # channel search carries an exact subscriber count for the size gate
    ch = yt.search("chan", "channel")["results"][0]
    assert ch["channelId"] == "UCaaaaaaaaaaaaaaaaaaaaaa" and ch["subscriberCount"] == "71234" and ch["handle"] == "@chan"
    conn.close()


def test_channel_videos_pages_the_uploads_playlist(tmp_home):
    conn = yti_rs_db.connect()
    yt = _yt(conn)
    out = yt.channel_videos("@chan")                                   # two playlist pages per call
    assert [r["videoId"] for r in out["results"]] == ["u0_0", "u0_1", "u0_2", "uA_0", "uA_1", "uA_2"]
    assert out["results"][3]["index"] == 0 and out["has_more"] and out["continuation_token"] == "B"
    assert yt.units_used_today() == 1 + 2 + 2                          # channels + 2 playlist pages + 2 videos.list
    nxt = yt.channel_videos("@chan", "B")
    assert [r["videoId"] for r in nxt["results"]] == ["uB_0", "uB_1", "uB_2"] and not nxt["has_more"]
    # the channel lookup is cached: no second channels.list call
    assert [c[0] for c in yt.http.calls].count("channels") == 1
    lat = yt.latest("UCaaaaaaaaaaaaaaaaaaaaaa")
    assert lat["results"][0]["viewCount"] == "12345" and lat["results"][0]["link"].endswith("u0_0")
    conn.close()


def test_quota_is_respected_and_router_falls_back(tmp_home):
    conn = yti_rs_db.connect()
    yt = _yt(conn)
    yt.daily_units, yt.reserve_units = 150, 0                          # room for one search, not two
    yt.search("one")
    with pytest.raises(C.QuotaExhausted):
        yt.search("two")

    class FakeTAPI:
        def __init__(self): self.calls = []
        def search(self, q, type_="video", continuation=None):
            self.calls.append(("search", q, continuation)); return {"results": [], "has_more": False}
        def channel_videos(self, channel, continuation=None, *, sort=None):
            self.calls.append(("channel_videos", channel, sort)); return {"results": [], "has_more": False}
        def latest(self, channel): self.calls.append(("latest", channel)); return {"results": []}
    tapi = FakeTAPI()
    src = C.DataSource(tapi, yt)
    assert src.search("two")["results"] == [] and tapi.calls == [("search", "two", None)] and src.fallbacks == 0   # routed, not failed
    src.latest("@chan")                                                # free on TranscriptAPI: never spends units
    assert tapi.calls[-1] == ("latest", "@chan")
    # a 403 quotaExceeded from Google marks the day as spent, and later calls go straight to TranscriptAPI
    yt2 = _yt(conn); yt2.http.quota_after = 0
    src2 = C.DataSource(tapi, yt2)
    src2.channel_videos("@chan")
    assert tapi.calls[-1] == ("channel_videos", "@chan", None) and yt2._quota_hit and src2.fallbacks == 1
    src2.search("three")
    assert tapi.calls[-1] == ("search", "three", None) and [c[0] for c in yt2.http.calls] == ["channels"]
    # with no TranscriptAPI key the router says so instead of silently stopping
    with pytest.raises(C.QuotaExhausted):
        C.DataSource(None, yt2).search("four")
    with pytest.raises(RuntimeError):
        C.DataSource(None, None)
    conn.close()


def test_router_tags_continuations_and_pages_instead_of_popular(tmp_home):
    conn = yti_rs_db.connect()
    src = C.DataSource(None, _yt(conn))
    assert src.supports_popular is False
    first = src.channel_videos("@chan")
    assert first["continuation_token"] == "yt:B"
    more = src.channel_videos("@chan", sort="popular")                # the crawler's "page 2": next uploads
    assert [r["videoId"] for r in more["results"]] == ["uB_0", "uB_1", "uB_2"]
    assert src.channel_videos("@chan", sort="popular")["results"] == []   # catalogue exhausted
    s = src.search("alpha")
    assert s["continuation_token"] == "yt:P2" and src.search("alpha", continuation="yt:P2")["has_more"] is False
    conn.close()
