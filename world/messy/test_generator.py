from dataclasses import fields

import pytest

from world.messy import Noise, generate, seal, verify
from world.messy.generator import canonical


def test_same_seed_same_bytes_and_no_truth_in_public(tmp_path):
    a, truth = generate(13, instances=2)
    assert (a, truth) == generate(13, instances=2)
    assert a != generate(14, instances=2)[0]
    assert all(not x["document"]["data"] for x in a["arrivals"])
    assert not {"truth", "expected", "cause", "scenario", "ledger"} & a.keys()
    assert all(x["on"] <= "2026-04-22" for x in a["arrivals"])
    assert {r["scenario"] for r in truth["invoices"].values()} >= {0, 1, 2, 3, 4, 5, 6, 7, 8, 9}
    seal(tmp_path, a, truth)
    assert verify(tmp_path) == (a, truth)
    (tmp_path / "truth.json").write_text("{}")
    with pytest.raises(ValueError, match="commitment"):
        verify(tmp_path)


@pytest.mark.parametrize("knob", [f.name for f in fields(Noise)])
def test_independent_noise_settings(knob):
    all_off = {f.name: 0 for f in fields(Noise)}
    a, _ = generate(noise=Noise(**all_off))
    b, _ = generate(noise=Noise(**{**all_off, knob: 1}))
    assert canonical(a) != canonical(b)


def test_invalid_knobs_and_scale_rejected():
    with pytest.raises(ValueError):
        Noise(typos=1.1)
    with pytest.raises(ValueError):
        generate(instances=0)


def test_held_out_seeds_and_wording_are_reserved(tmp_path):
    from world.messy import HELD_OUT_SEEDS, held_out_noise
    from world.messy.phrasing import DEV, HELD_OUT
    with pytest.raises(ValueError, match="reserved"):
        generate(HELD_OUT_SEEDS.start)
    with pytest.raises(ValueError, match="reserved"):
        generate(13, split="held_out")
    public, _ = generate(HELD_OUT_SEEDS.start, split="held_out", noise=held_out_noise())
    text = "\n".join(a["document"]["text"] for a in public["arrivals"])
    dev_only = {s for k in DEV for s in DEV[k]} - {s for k in HELD_OUT for s in HELD_OUT[k]}
    assert not [s for s in dev_only if "{" not in s and len(s) > 12 and s in text]
    assert "-----Original Message-----" in text and "Posted;Description;Reference;Amount" in text
    pool = tmp_path / "pool.json"
    pool.write_text('{"reply_paid": ["Judge wording: the money left our account."]}')
    public, _ = generate(HELD_OUT_SEEDS.start, split="held_out", pool_file=pool,
                         noise=held_out_noise(nuanced_replies=1))
    assert any("Judge wording" in a["document"]["text"] for a in public["arrivals"])
    pool.write_text('{"unknown_key": ["x"]}')
    with pytest.raises(ValueError, match="pool"):
        generate(HELD_OUT_SEEDS.start, split="held_out", pool_file=pool)


def test_sealed_control_removes_only_planted_instructions(tmp_path):
    from world.messy import control_input
    public, truth = generate(13, noise=Noise(adversarial=1))
    control = control_input(public, truth)
    assert truth["adversarial_pairs"]
    for a, c in zip(public["arrivals"], control["arrivals"]):
        did = a["document"]["id"]
        assert c["document"]["text"] == truth["adversarial_pairs"].get(did, a["document"]["text"])
    seal(tmp_path, public, truth)
    (tmp_path / "control.json").write_text(canonical(public) + "\n")
    with pytest.raises(ValueError, match="commitment"):
        verify(tmp_path)
