import math
from datetime import datetime, timezone

import pytest

import yti_rs_db
import yti_rs_outliers as O
from yti_rs_config import DEFAULT_CONFIG, DEFAULT_MATURITY_CURVE

NOW = datetime(2026, 9, 12, tzinfo=timezone.utc)


def test_median_baseline_trailing_and_immature():
    prior = [100, 120, 90, 110, 105, 95, 10000]   # newest-first; the 10000 is older history
    b, n = O.median_baseline(prior, window=6, min_n=6)
    assert n == 6 and b == 102.5                    # window excludes the 7th (older) value
    b, n = O.median_baseline([100, 120, 90], min_n=6)
    assert b is None and n == 3


def test_log_mad_z_none_when_mad_zero_and_fixture():
    assert O.log_mad_z(500, [100, 100, 100, 100, 100, 100]) is None
    base = [100, 110, 90, 120, 80, 105, 95, 115]
    z = O.log_mad_z(400, base)
    logs = sorted(math.log(v) for v in base)
    med = (logs[3] + logs[4]) / 2
    mad = sorted(abs(x - med) for x in logs)
    mad = (mad[3] + mad[4]) / 2
    assert z == pytest.approx((math.log(400) - med) / (1.4826 * mad))


def test_maturity_projection_7_day_video():
    # a 7-day-old video at 55% of eventual views projects to ~1.0× its final multiple
    final = 10000.0
    views_day7 = final * O.maturity_fraction(7, DEFAULT_MATURITY_CURVE)
    assert O.maturity_fraction(7) == pytest.approx(0.55)
    assert O.project_views(views_day7, 7) == pytest.approx(final)
    assert O.project_views(final, 40) == final                # mature → unchanged
    assert O.maturity_fraction(2) == pytest.approx(0.25)      # interpolation between day 1 and 3


def test_signal_weight_and_classify():
    assert O.signal_weight(365, 365) == pytest.approx(0.5)
    assert O.signal_weight(None, 365) == 1.0
    assert O.classify(6.0, 10, 6) == "strong_hit"
    assert O.classify(3.5, 10, 6) == "hit"
    assert O.classify(0.3, 10, 6) == "under"
    assert O.classify(1.0, 10, 6) == "normal"
    assert O.classify(9.0, 3, 6) == "immature"
    assert O.classify(9.0, 10, 6, bucket_known=False) == "immature"


def test_organic_and_percentile():
    assert O.organic_flag(5, 95) == "suspect_paid"
    assert O.organic_flag(40, 95) == "ok"
    assert O.organic_flag(None, 95) == "unknown"
    assert O.percentile_rank([1, 2, 3, 4], 4) == 87.5
    assert O.trajectory_suspect([("d1", 0), ("d2", 900), ("d3", 950), ("d4", 960), ("d5", 962)]) is True
    assert O.trajectory_suspect([("d1", 0), ("d2", 100), ("d3", 300), ("d4", 600), ("d5", 1000)]) is False


def test_score_all_end_to_end(tmp_home):
    conn = yti_rs_db.connect()
    cfg = dict(DEFAULT_CONFIG)
    cfg["niches"] = [{"name": "n1", "is_target": True, "signal_half_life_days": 365}]
    yti_rs_db.upsert_channel(conn, {"channel_id": "UC1", "niche": "n1"})
    # 8 baseline videos at ~1000 views, then one at 6000 views (60 days old, exact date)
    for i in range(8):
        yti_rs_db.upsert_video(conn, {"video_id": f"v{i}", "channel_id": "UC1", "title": f"t{i}", "niche": "n1",
                                      "published_at": f"2026-0{1 + i // 4}-{10 + (i % 4) * 3:02d}T00:00:00+00:00",
                                      "views": 1000 + i * 10, "duration_seconds": 600, "is_short": 0})
    yti_rs_db.upsert_video(conn, {"video_id": "hit", "channel_id": "UC1", "title": "the hit", "niche": "n1",
                                  "published_at": "2026-07-14T00:00:00+00:00", "views": 6000, "duration_seconds": 600, "is_short": 0})
    yti_rs_db.upsert_video(conn, {"video_id": "short", "channel_id": "UC1", "title": "s", "niche": "n1",
                                  "published_at": "2026-07-15T00:00:00+00:00", "views": 50, "duration_seconds": 40, "is_short": 1})
    res = O.score_all(conn, cfg, now=NOW)
    assert res["scored"] == 10
    row = yti_rs_db.one(conn, "SELECT * FROM scores WHERE video_id = 'hit'")
    assert row["class"] == "strong_hit" and row["baseline_n"] == 8 and row["format_bucket"] == "long"
    assert row["multiple"] == pytest.approx(6000 / 1035)
    assert row["projected_multiple"] == pytest.approx(row["multiple"])   # 60 days old → no projection
    # the short is in its own bucket with no baseline → immature, not "under"
    assert yti_rs_db.one(conn, "SELECT class FROM scores WHERE video_id = 'short'")["class"] == "immature"
    # provisional scores are never written to scores (only §8 math is)
    assert res["maturity_curve"]["source"] == "prior"
    conn.close()
