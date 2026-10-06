import yti_rs_db
import yti_rs_formats as F
from yti_rs_config import DEFAULT_CONFIG


def test_wilson_p2_regression_guard():
    assert F.wilson_lb(2, 2) < F.wilson_lb(40, 60)
    assert round(F.wilson_lb(2, 2), 2) == 0.34
    assert round(F.wilson_lb(40, 60), 2) == 0.54
    assert F.wilson_lb(0, 0) == 0.0


def _vid(i, ch, niche, title):
    return {"video_id": f"v{i}", "channel_id": ch, "niche": niche, "title": title}


def test_mine_rejects_house_style_accepts_cross_niche_frame():
    house = [_vid(i, "UC1", "n1", f"my weekly vlog episode {i}") for i in range(10)]
    assert F.mine(house) == []          # one channel, one niche → not a format
    frame = [_vid(100, "UCa", "n1", "the new rules of fat loss"), _vid(101, "UCb", "n2", "the new rules of saas pricing"),
             _vid(102, "UCc", "n1", "the new rules of hiring in 2026"), _vid(103, "UCa", "n2", "the new rules of sleep")]
    out = F.mine(frame + house)
    assert any(m["skeleton"] == "the new rules of" for m in out)
    m = [m for m in out if m["skeleton"] == "the new rules of"][0]
    assert m["support"] == 4 and m["distinct_channels"] == 3 and m["distinct_niches"] == 2
    # sub-shingles ("new rules of") are suppressed in favour of the longest frame
    assert not any(m["skeleton"] == "new rules of" for m in out)


def test_mine_drops_purely_topical_shingles():
    rows = [_vid(i, f"UC{i}", "n1" if i % 2 else "n2", "visceral fat loss diet") for i in range(4)]
    assert F.mine(rows, topical_terms={"visceral", "fat", "loss", "diet"}) == []
    assert F.mine(rows) != []


def test_normalize_title_digits_to_hash():
    assert F.normalize_title("I sent 1,000 cold emails & learned this!") == ["i", "sent", "#", "cold", "emails", "learned", "this"]


def test_seeded_load_and_match(tmp_home):
    conn = yti_rs_db.connect()
    n = F.load_seeded(conn)
    assert n >= 9
    yti_rs_db.upsert_channel(conn, {"channel_id": "UC1", "niche": "n1"})
    yti_rs_db.upsert_video(conn, {"video_id": "a", "channel_id": "UC1", "niche": "n1", "is_short": 0,
                                  "title": "How to learn Rust so fast it feels like cheating"})
    yti_rs_db.upsert_video(conn, {"video_id": "b", "channel_id": "UC1", "niche": "n1", "is_short": 0,
                                  "title": "If you don't understand tokens, you don't understand LLM costs"})
    yti_rs_db.upsert_video(conn, {"video_id": "c", "channel_id": "UC1", "niche": "n1", "is_short": 0, "title": "plain title"})
    # a Short and a video of unknown length with a matching title are NOT format evidence
    yti_rs_db.upsert_video(conn, {"video_id": "s", "channel_id": "UC1", "niche": "n1", "is_short": 1,
                                  "title": "The new rules of prompting #shorts"})
    yti_rs_db.upsert_video(conn, {"video_id": "u", "channel_id": "UC1", "niche": "n1",
                                  "title": "The new rules of everything"})
    assert F.match_seeded(conn) == 2
    rows = yti_rs_db.rows(conn, "SELECT format_id, slots_json FROM format_matches ORDER BY format_id")
    assert rows[0]["format_id"] == "if-you-dont-understand-x-y" and "mechanism" in rows[0]["slots_json"]
    conn.close()


def test_validate_and_gap_report(tmp_home):
    conn = yti_rs_db.connect()
    cfg = dict(DEFAULT_CONFIG)
    cfg["niches"] = [{"name": "target", "is_target": True, "signal_half_life_days": 365},
                     {"name": "adj1", "is_target": False, "signal_half_life_days": 365},
                     {"name": "adj2", "is_target": False, "signal_half_life_days": 365}]
    F.load_seeded(conn)
    ts = yti_rs_db.now_iso()
    # 12 matches of one seeded format in two adjacent niches: 8 hits, 4 under → proven elsewhere, unused in target
    for i in range(12):
        niche = "adj1" if i % 2 else "adj2"
        yti_rs_db.upsert_channel(conn, {"channel_id": f"UC{i}", "niche": niche})
        yti_rs_db.upsert_video(conn, {"video_id": f"v{i}", "channel_id": f"UC{i}", "niche": niche, "is_short": 0,
                                      "title": f"the new rules of thing {i}", "views": 1000})
        cls = "hit" if i < 8 else "under"
        conn.execute("INSERT INTO scores(video_id, computed_at, format_bucket, baseline_n, projected_multiple, signal_weight, organic_flag, class)"
                     " VALUES (?,?,?,?,?,?,?,?)", (f"v{i}", ts, "long", 10, 4.0 if cls == "hit" else 0.2, 0.8, "ok", cls))
    conn.commit()
    F.match_seeded(conn)
    F.validate(conn, cfg)
    lib = {f["format_id"]: f for f in F.library(conn)}
    st = lib["new-rules-of"]
    assert st["n_total"] == 12 and st["n_hits"] == 8 and st["n_under"] == 4
    assert round(st["wilson_lb"], 3) == round(F.wilson_lb(8, 12), 3)
    assert st["distinct_niches"] == 2 and st["target_niche_uses"] == 0
    gap = F.gap_report(conn, cfg)
    assert [g["format_id"] for g in gap["gaps"]] == ["new-rules-of"]
    assert gap["gaps"][0]["actionable"] is True
    assert gap["caveat"] == F.GAP_CAVEAT
    conn.close()


def test_discriminators_require_both_arms():
    rows = [{"is_hit": 1, "structure_class": "listicle_dense"} for _ in range(6)] + \
           [{"is_hit": 0, "structure_class": "listicle_short"} for _ in range(6)] + \
           [{"is_hit": 1, "structure_class": "narrative"} for _ in range(2)]
    out = F.discriminators(rows)
    feats = {(d["feature"], d["value"]): d for d in out}
    assert ("structure_class", "listicle_dense") in feats
    assert ("structure_class", "narrative") not in feats        # arm n=2 < 5
    assert feats[("structure_class", "listicle_dense")]["hit_rate_with"] == 1.0


def test_mine_into_db_ignores_shorts(tmp_home):
    conn = yti_rs_db.connect()
    cfg = dict(DEFAULT_CONFIG); cfg["niches"] = []
    for i in range(4):          # a cross-niche frame that exists ONLY in Shorts
        yti_rs_db.upsert_channel(conn, {"channel_id": f"UC{i}", "niche": f"n{i % 2}"})
        yti_rs_db.upsert_video(conn, {"video_id": f"s{i}", "channel_id": f"UC{i}", "niche": f"n{i % 2}",
                                      "is_short": 1, "title": f"wait for the end {i}"})
    assert F.mine_into_db(conn, cfg) == {"mined": 0}
    conn.execute("UPDATE videos SET is_short = 0"); conn.commit()
    assert F.mine_into_db(conn, cfg)["mined"] >= 1
    conn.close()
