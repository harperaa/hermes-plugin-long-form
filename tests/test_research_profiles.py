import random

import yti_rs_profiles as PR


def test_changepoint_detects_step_and_rejects_noise():
    rng = random.Random(1)
    series = [7.0 + rng.gauss(0, 0.15) for _ in range(20)] + [8.5 + rng.gauss(0, 0.15) for _ in range(20)]
    cp = PR.changepoint(series, n_perm=500, seed=3)
    assert cp is not None and abs(cp["index"] - 20) <= 1 and cp["p"] < 0.01
    flat = [7.0 + rng.gauss(0, 0.3) for _ in range(40)]
    flat_cp = PR.changepoint(flat, n_perm=500, seed=3)
    assert flat_cp is None or flat_cp["p"] > 0.05
    assert PR.detect_changepoints(flat, n_perm=300) == []
    assert PR.changepoint([1.0] * 20) is None           # MAD == 0 → None, not infinity
    assert PR.changepoint([1.0, 2.0, 3.0]) is None      # too short


def test_cohort_diff_sorted_by_effect():
    before = [{"title": f"my vlog day {i}", "point_count": 3, "has_proof": 0, "awareness_frame": "mechanism_led",
               "published_at": f"2025-01-{1 + i:02d}T00:00:00+00:00", "duration_seconds": 600} for i in range(8)]
    after = [{"title": f"the new rules of saas {i}", "point_count": 20, "has_proof": 1, "awareness_frame": "bridged",
              "published_at": f"2025-03-{1 + i:02d}T00:00:00+00:00", "duration_seconds": 1200} for i in range(8)]
    diff = PR.cohort_diff(before, after)
    feats = {d["feature"]: d for d in diff}
    assert feats["has_proof"]["before"] == 0.0 and feats["has_proof"]["after"] == 1.0
    assert feats["point_count"]["after"] == 20
    assert feats["awareness_frame"]["effect"] == 1.0
    assert [d["effect"] for d in diff] == sorted([d["effect"] for d in diff], reverse=True)


def test_coherence_flags_outlier_doc():
    docs = ["fat loss protein diet walking"] * 5 + ["python tutorial for loops variables"]
    mean, per = PR.coherence(docs)
    assert mean is not None and per[-1] < min(per[:-1])
