from datetime import datetime, timezone

import yti_rs_normalize as N

FETCHED = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)


def test_parse_views_variants():
    assert N.parse_views("3.4M views") == (3_400_000, True)
    assert N.parse_views("1,234 views") == (1234, False)
    assert N.parse_views("No views") == (0, False)
    assert N.parse_views("12K views") == (12_000, True)
    assert N.parse_views("1.2B views") == (1_200_000_000, True)
    assert N.parse_views("3.2M views 2 weeks ago") == (3_200_000, True)
    assert N.parse_views("96454") == (96454, False)
    assert N.parse_views(102225) == (102225, False)
    assert N.parse_views(None) == (None, False)
    assert N.parse_views("garbage") == (None, False)


def test_split_combined_form():
    views, approx, age = N.split_views_and_age("3.2M views 2 weeks ago")
    assert views == 3_200_000 and approx and age == "2 weeks ago"


def test_parse_published_relative_resolves_against_fetch_time():
    iso, approx, gran = N.parse_published("2 years ago", FETCHED)
    assert approx and gran == 183.0
    assert iso.startswith("2024-09")
    iso, approx, gran = N.parse_published("3 weeks ago", FETCHED)
    assert iso == "2026-08-22T12:00:00+00:00" and gran == 3.5
    iso, approx, gran = N.parse_published("Streamed 4 days ago", FETCHED)
    assert iso == "2026-09-08T12:00:00+00:00" and gran == 0.5
    iso, approx, gran = N.parse_published("1 hour ago", FETCHED)
    assert iso == "2026-09-12T11:00:00+00:00" and gran < 0.1
    iso, approx, gran = N.parse_published("Premiered Jan 5, 2026", FETCHED)
    assert iso.startswith("2026-01-05") and approx


def test_parse_published_iso_is_exact():
    iso, approx, gran = N.parse_published("2026-09-11T16:01:32+00:00")
    assert not approx and gran == 0.0 and iso.startswith("2026-09-11T16:01:32")
    iso, approx, _ = N.parse_published("2026-09-11T16:01:32.000Z")
    assert not approx and iso.startswith("2026-09-11T16:01:32")


def test_parse_duration():
    assert N.parse_duration("12:34") == 754
    assert N.parse_duration("1:02:45") == 3765
    assert N.parse_duration("0:47") == 47
    assert N.parse_duration("00:36:09") == 2169
    assert N.parse_duration("PT36M9S") == 2169
    assert N.parse_duration(None) is None
    assert N.parse_duration("") is None


def test_is_short_rules():
    assert N.is_short(170) is True
    assert N.is_short(181) is False
    assert N.is_short(None) is None
    assert N.is_short(900, explicit_flag=True) is True     # actor flag wins
    assert N.is_short(None, link="https://youtube.com/shorts/x") is True


def test_map_apify_video_tolerant_and_quarantine():
    item = {"id": "XqS7kIWMIDE", "title": "T", "viewCount": 102225, "date": "2026-09-11T16:01:32.000Z",
            "likes": 2400, "commentsCount": 124, "duration": "00:36:09", "numberOfSubscribers": 1420000,
            "channelId": "UC1", "channelUsername": "DanKoeTalks", "channelName": "Dan Koe", "url": "u"}
    rec, hits = N.map_apify_video(item)
    assert rec["views"] == 102225 and rec["likes"] == 2400 and rec["comment_count"] == 124
    assert rec["duration_seconds"] == 2169 and rec["is_short"] == 0 and rec["precision_tier"] == 2
    assert rec["published_approx"] == 0 and rec["subscriber_count"] == 1420000
    assert hits["views"] == "viewCount" and hits["comments"] == "commentsCount"
    # drifted key still resolves
    rec2, hits2 = N.map_apify_video({"videoId": "a", "numberOfViews": "1.2M", "durationMs": 90000})
    assert rec2["views"] == 1_200_000 and rec2["duration_seconds"] == 90 and rec2["is_short"] == 1
    assert hits2["views"] == "numberOfViews"
    # no views → None (caller quarantines), never zero
    rec3, _ = N.map_apify_video({"id": "b", "title": "no views key"})
    assert rec3 is None


def test_channel_handle_clean():
    assert N.channel_handle_clean("/@DanKoeTalks") == "@DanKoeTalks"
    assert N.channel_handle_clean("DanKoeTalks") == "@DanKoeTalks"
    assert N.channel_handle_clean("https://www.youtube.com/@X") == "@X"
    assert N.channel_handle_clean("") is None
