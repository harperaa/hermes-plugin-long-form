"""Pulse: free exact re-reads for recent high-demand videos → trajectory → momentum and trails."""
from datetime import datetime, timedelta, timezone

import yti_rs_db
import yti_rs_pulse as P
import yti_rs_report as R
from yti_rs_config import DEFAULT_CONFIG


def _cfg():
    return dict(DEFAULT_CONFIG)


class FakeYT:
    def __init__(self, views): self.views, self.calls = views, []
    def can_afford(self, units): return True
    def _videos(self, ids):
        self.calls.append(list(ids))
        return {i: {"statistics": {"viewCount": str(self.views[i]), "likeCount": "10", "commentCount": "2"}} for i in ids if i in self.views}


class FakeSource:
    def __init__(self, yt=None, latest=None): self.yt, self._latest = yt, latest or {}
    def latest(self, ident): return self._latest.get(ident, {})


def _seed(conn, now):
    yti_rs_db.upsert_channel(conn, {"channel_id": "UC1", "handle": "@c1", "niche": "n1"})
    def vid(i, age, pm, short=0):
        yti_rs_db.upsert_video(conn, {"video_id": f"v{i}", "channel_id": "UC1", "title": f"v{i}", "niche": "n1",
                                      "published_at": (now - timedelta(days=age)).isoformat(), "views": 1000,
                                      "is_short": short, "provisional_multiple": pm, "duration_seconds": 600})
    vid(1, 2, 5.0)      # focus
    vid(2, 3, 1.6)      # riser (≥ half the hit multiple) — watched too
    vid(3, 3, 0.9)      # ordinary — not watched
    vid(4, 40, 9.0)     # old — not watched
    vid(5, 1, 4.0, 1)   # Short — never


def test_momentum_rule():
    t = datetime(2026, 10, 7, tzinfo=timezone.utc)
    pts = [(t, 1000), (t + timedelta(hours=6), 1600), (t + timedelta(hours=12), 2500)]   # 100/h then 150/h
    assert P.momentum(pts) == {"vph": 150.0, "dir": "up", "n": 3}
    assert P.momentum(pts[:2]) == {"vph": 100.0, "dir": None, "n": 2}
    assert P.momentum([(t, 1000), (t + timedelta(hours=6), 1600), (t + timedelta(hours=12), 2150)])["dir"] == "flat"
    assert P.momentum([(t, 1000), (t + timedelta(hours=6), 1600), (t + timedelta(hours=12), 1700)])["dir"] == "down"
    assert P.momentum([(t, 1000), (t + timedelta(minutes=5), 1001)]) == {"vph": None, "dir": None, "n": 2}   # too close


def test_pulse_watches_only_recent_high_demand_and_writes_timestamped_snapshots(tmp_home):
    conn = yti_rs_db.connect()
    now = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)
    _seed(conn, now)
    assert [r["video_id"] for r in P.watchlist(conn, _cfg(), now=now)] == ["v1", "v2"]
    yt = FakeYT({"v1": 5400, "v2": 1700})
    out = P.run_pulse(conn, _cfg(), FakeSource(yt), now=now)
    assert out["watched"] == 2 and out["updated"] == 2 and out["via"] == "youtube" and out["rising"] == 2
    assert yt.calls == [["v1", "v2"]]                                                        # one videos.list, 1 unit
    snap = yti_rs_db.rows(conn, "SELECT * FROM video_snapshots WHERE video_id='v1'")
    assert len(snap) == 1 and snap[0]["captured_at"] == now.isoformat() and snap[0]["views"] == 5400 and snap[0]["likes"] == 10
    v1 = yti_rs_db.one(conn, "SELECT views, views_approx, precision_tier FROM videos WHERE video_id='v1'")
    assert (v1["views"], v1["views_approx"], v1["precision_tier"]) == (5400, 0, 2)
    # six hours later, no Data API: the free channel/latest covers it
    later = now + timedelta(hours=6)
    src = FakeSource(None, {"@c1": {"results": [{"videoId": "v1", "viewCount": "7200"}, {"videoId": "v9", "viewCount": "1"}]}})
    out2 = P.run_pulse(conn, _cfg(), src, now=later)
    assert out2["via"] == "transcriptapi" and out2["updated"] == 1
    assert len(yti_rs_db.rows(conn, "SELECT * FROM video_snapshots WHERE video_id='v1'")) == 2
    conn.close()


def test_supply_demand_carries_trail_and_momentum(tmp_home):
    conn = yti_rs_db.connect()
    now = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)
    _seed(conn, now)
    # three readings over 12 hours, then a score row as score_all would write it
    for hrs, views in ((-12, 3000), (-6, 4200), (0, 6000)):
        yti_rs_db.add_snapshot(conn, "v1", views, captured_at=(now + timedelta(hours=hrs)).isoformat())
    conn.execute("UPDATE videos SET views = 6000 WHERE video_id = 'v1'")
    yti_rs_db.set_meta(conn, "maturity_curve", {"curve": [[1, 0.15], [7, 0.55], [28, 1.0]], "source": "prior"})   # shaped as the scorer stores it
    conn.execute("""INSERT INTO scores(video_id, computed_at, format_bucket, age_days, baseline_views, baseline_n, multiple,
                    projected_views, projected_multiple, class, raw_only, in_niche)
                    VALUES ('v1', ?, 'long', 2, 1000, 20, 6.0, 6000, 6.0, 'strong_hit', 1, 1)""", (now.isoformat(),))
    conn.commit()
    d = R.supply_demand(conn, _cfg(), now=now)
    p = [x for x in d["points"] if x["id"] == "v1"][0]
    assert len(p["h"]) == 2 and p["h"][0][0] < p["h"][1][0] < 2.0          # earlier ages, oldest first
    assert p["h"][0][1] == 3.0 and p["h"][1][1] == 4.2                      # raw_only score → plain multiples
    assert p["mo"]["dir"] == "up" and p["mo"]["vph"] == 300.0 and p["mo"]["n"] == 3
    conn.close()
