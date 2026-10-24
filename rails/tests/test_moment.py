"""The judge moment, end to end: world -> workflow (rules agents) -> Judge Console preset."""

import logging

import pytest

from rails.console.moment import run_moment
from world.generator import MOMENT


@pytest.fixture(scope="module")
def report():
    logging.getLogger("langgraph").setLevel(logging.ERROR)
    return run_moment()


def test_three_unexplained_items_total_3140_before(report):
    assert report.before == {"INV-KW-0301": 1240.0, "INV-KW-0402": 900.0, "INV-KW-0901": 1000.0}
    assert report.total_before == 3140.0


def test_correction_rechecks_only_the_electrician(report):
    assert report.rechecked == ["INV-KW-0402"]
    assert MOMENT["fact_key"].startswith("invoice:INV-KW-0402.")


def test_exactly_one_alert_withdrawn_two_kept(report):
    assert report.withdrawn == ["INV-KW-0402"] and report.withdrawn_amount == 900.0
    assert report.kept == ["INV-KW-0301", "INV-KW-0901"]
    assert report.total_after == 2240.0
