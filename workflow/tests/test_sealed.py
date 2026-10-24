"""Sealed eval: the locks must catch cheating, and the hashes must catch tampering."""

import json
import subprocess
import sys
import textwrap

import pytest

from workflow.sealed.__main__ import main
from workflow.sealed.common import ANSWER_FILES, REPO
from workflow.sealed.reveal import reveal


@pytest.fixture(scope="module")
def sealed_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("sealed")
    assert main(["--dir", str(out), "--n-clean", "20"]) == 0
    return out


def _fresh_copy(src, tmp_path):
    import shutil
    dst = tmp_path / "copy"
    shutil.copytree(src, dst)
    c = json.loads((dst / "commitment.json").read_text())
    c["sealed_files"] = {(str(dst / "truth.json") if p.endswith("truth.json") else p): h
                         for p, h in c["sealed_files"].items()}
    (dst / "commitment.json").write_text(json.dumps(c))
    return dst


def test_honest_run_passes_with_every_hash_matching(sealed_run):
    r = json.loads((sealed_run / "report.json").read_text())
    assert r["valid"] and r["passed"] and r["blocked_attempts"] == []
    assert len(r["hash_checks"]) == 5 and all(c["match"] for c in r["hash_checks"])
    assert [s["passed"] for s in r["scenarios"]] == [True] * 9


def test_outputs_carry_no_answers(sealed_run):
    outputs = (sealed_run / "outputs.json").read_text()
    for word in ("expected_cause", "must_cite", "manager_alert", "answer_key"):
        assert word not in outputs


_CHEAT = textwrap.dedent("""
    import importlib, json, sys
    from workflow.sealed.blind_run import install_guard, _attempts
    install_guard({files!r})
    caught = []
    try:
        open({key!r}).read()
    except PermissionError:
        caught.append("open")
    try:
        importlib.import_module("evals.scorer")
    except ImportError:
        caught.append("importlib")
    try:
        from world.generator import MOMENT  # noqa: F401
    except ImportError:
        caught.append("import")
    print(json.dumps({{"caught": caught, "attempts": _attempts}}))
""")


def test_an_agent_trying_to_read_or_import_the_answers_is_blocked_and_recorded():
    code = _CHEAT.format(files=[str(p) for p in ANSWER_FILES], key=str(ANSWER_FILES[0]))
    run = subprocess.run([sys.executable, "-c", code], check=False, cwd=REPO,
                         capture_output=True, text=True)
    got = json.loads(run.stdout.strip().splitlines()[-1])
    assert got["caught"] == ["open", "importlib", "import"]
    assert any(a.startswith("open ") and a.endswith("answer_key.json") for a in got["attempts"])
    assert {"import evals", "import world"} <= set(got["attempts"])     # stopped at the package


def test_editing_the_answers_after_the_run_voids_the_eval(sealed_run, tmp_path):
    out = _fresh_copy(sealed_run, tmp_path)
    truth = json.loads((out / "truth.json").read_text())
    truth[0]["cause"] = "unexplained"                        # quietly "fix" an answer
    (out / "truth.json").write_text(json.dumps(truth))
    r = reveal(out)
    assert not r["valid"] and "scenarios" not in r           # never graded
    assert [c["what"] for c in r["hash_checks"] if not c["match"]] == ["answer file truth.json"]


def test_editing_the_outputs_after_the_run_voids_the_eval(sealed_run, tmp_path):
    out = _fresh_copy(sealed_run, tmp_path)
    outputs = json.loads((out / "outputs.json").read_text())
    outputs["alerts"] = outputs["alerts"][:1]                # drop the inconvenient alerts
    (out / "outputs.json").write_text(json.dumps(outputs))
    r = reveal(out)
    assert not r["valid"]
    assert [c["what"] for c in r["hash_checks"] if not c["match"]] == ["outputs (outputs.json)"]


def test_editing_the_inputs_after_sealing_voids_the_eval(sealed_run, tmp_path):
    out = _fresh_copy(sealed_run, tmp_path)
    with (out / "events.jsonl").open("a") as f:
        f.write("\n")
    assert not reveal(out)["valid"]
