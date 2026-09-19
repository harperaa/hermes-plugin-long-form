import yti_rs_packaging as P


def _segs(lines, step=10.0):
    return [{"text": t, "start": i * step, "duration": step} for i, t in enumerate(lines)]


def test_point_count_dense_vs_short():
    dense = _segs([f"number {i} is about focus" if i % 2 else "and here is why it matters" for i in range(1, 51)], step=25)
    assert P.count_points(dense) >= 15
    assert P.structure_class(P.count_points(dense), " ".join(s["text"] for s in dense)) == "listicle_dense"
    short = _segs(["first, sleep more", "some talk", "second, lift weights", "more talk", "third, eat protein",
                   "talk", "the last one is walking"], step=40)
    pc = P.count_points(short)
    assert 2 <= pc <= 8
    assert P.structure_class(pc, "plain talk") == "listicle_short"
    assert P.structure_class(0, "a story about my life") == "narrative"
    assert P.structure_class(3, "as you can see on my screen click here go to settings type in the value scroll down hit enter") == "walkthrough"


def test_promise_proof_plan_persona():
    title = "How to lose visceral fat without cardio"
    segs = _segs(["hey everyone welcome back", "today we're talking about how to lose visceral fat without doing any cardio",
                  "I've helped 2,000 clients do this over 12 years", "by the end of this video you'll have a plan",
                  "if you're a busy parent this is for you"], step=15)
    ok, sec = P.promise(title, segs)
    assert ok and sec == 15.0
    ok, sec = P.proof(segs)
    assert ok and sec == 30.0
    assert P.plan(segs) and P.persona(segs)
    assert P.promise("unrelated quantum chromodynamics", segs) == (False, None)


def test_awareness_frame_bridged():
    assert P.awareness_frame("Get hired faster with threat modeling", ["get hired"], ["threat modeling"]) == "bridged"
    assert P.awareness_frame("Get hired faster", ["get hired"], ["threat modeling"]) == "outcome_led"
    assert P.awareness_frame("Threat modeling 101", ["get hired"], ["threat modeling"]) == "mechanism_led"
    assert P.awareness_frame("Tuesday stream", ["get hired"], ["threat modeling"]) == "unclear"


def test_delivery_register_and_mismatch():
    assert P.title_lowercase("i quit my job to make videos") is True
    assert P.title_lowercase("I Quit My Job") is False
    scripted = " ".join(["This is a sentence with exactly eight words here."] * 20)
    assert P.filler_rate(scripted) == 0.0
    assert P.delivery_class(P.filler_rate(scripted), P.sentence_len_cv(scripted)) == "scripted"
    raw = "um so like you know I uh kind of think. Yeah. So basically what happened was literally insane and we um went there and it was like wow. Ok."
    assert P.delivery_class(P.filler_rate(raw), 0.9) == "raw"


def test_cta_give_vs_take():
    segs = _segs(["intro"] * 30 + ["grab the free template link in the description"] + ["bye"], step=10)
    kind, pos = P.cta(segs, 320)
    assert kind == "give" and pos is not None and pos > 75
    segs2 = _segs(["intro"] * 30 + ["book a call with my team"] + ["bye"], step=10)
    assert P.cta(segs2, 320)[0] == "take"
    assert P.cta(_segs(["nothing here"] * 10), 100)[0] == "none"


def test_compute_packaging_shape():
    segs = _segs(["welcome, today: how to lose visceral fat", "first, protein", "second, walk", "third, sleep",
                  "I've coached 500 clients", "for free, link in the description"], step=15)
    p = P.compute_packaging("how to lose visceral fat", segs, " ".join(s["text"] for s in segs), 90,
                            ["lose visceral fat"], ["protein"])
    assert p["has_promise"] == 1 and p["has_proof"] == 1 and p["cta_kind"] == "give"
    assert p["awareness_frame"] == "outcome_led" and p["title_lowercase"] == 1
    assert p["structure_class"] in ("listicle_short",)
