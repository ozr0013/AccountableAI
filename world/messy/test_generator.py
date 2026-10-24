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
