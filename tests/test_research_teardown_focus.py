"""Tier 3 is spent on the focus quadrant only: recent AND well above normal."""
from datetime import datetime, timedelta, timezone

import yti_rs_db
import yti_rs_enrich as E
from yti_rs_config import DEFAULT_CONFIG


def _cfg(**teardown):
    cfg = dict(DEFAULT_CONFIG)
    cfg["teardown"] = dict(DEFAULT_CONFIG["teardown"], **teardown)
    return cfg


def test_focus_window_resolution():
    assert E.focus_window(_cfg()) == (7.0, 3.0)                              # defaults; multiple = hit_multiple
    assert E.focus_window(_cfg(focus_days=14, focus_multiple=4)) == (14.0, 4.0)
    assert E.focus_window(_cfg(focus_only=False)) is None                   # old behaviour: every hit
    assert E.focus_window(_cfg(focus_only=False), {"focus_days": 7, "focus_multiple": 2}) == (7.0, 2.0)   # the view's window wins
    assert E.focus_window(_cfg(), {"all": True}) is None


def test_only_focus_quadrant_videos_get_transcripts(tmp_home):
    conn = yti_rs_db.connect()
    now = datetime.now(timezone.utc)
    yti_rs_db.upsert_channel(conn, {"channel_id": "UC1", "handle": "@c1", "niche": "n1"})

    def vid(i, age_days, pm, short=0):
        yti_rs_db.upsert_video(conn, {"video_id": f"v{i}", "channel_id": "UC1", "title": f"v{i}", "niche": "n1",
                                      "published_at": (now - timedelta(days=age_days)).isoformat(), "views": 1000,
                                      "is_short": short, "provisional_multiple": pm, "duration_seconds": 600})
    vid(1, 2, 5.0)           # recent + high  -> focus
    vid(2, 30, 8.0)          # old + high     -> not focus (supply already out there)
    vid(3, 2, 1.2)           # recent + low   -> not a hit at all
    vid(4, 5, 3.0, short=1)  # a Short        -> never
    vid(5, 6, 3.5)           # recent + high  -> focus
    fetched = []

    def fake_fetch(v):
        fetched.append(v)
        return {"segments": [{"text": "hello", "start": 0, "duration": 5}], "language": "en"}
    out = E.run_transcripts(conn, _cfg(), fetch=fake_fetch, focus=E.focus_window(_cfg()))
    assert out["fetched"] == 2 and sorted(fetched) == ["v1", "v5"]
    # without the window every hit is pulled (v2 too)
    fetched.clear()
    out = E.run_transcripts(conn, _cfg(), fetch=fake_fetch, focus=None)
    assert sorted(fetched) == ["v2"]                                        # v1/v5 already cached
    conn.close()
