"""The measured builder report over the wire, AS builder_app.

The server computes none of this and cannot: the document rests on subagent sidecar
transcripts, shell command text, prompt text and commit times, none of which the contract
puts on the wire. So it is responsible for exactly three things, and each has a case here:
that a document the spec does not describe cannot be stored, that a null stays null, and
that one person's numbers are invisible to everybody else.

The isolation test is a real negative test rather than one that passes for the wrong
reason (0004, and the write-isolation lesson in CLAUDE.md): the victim's row is seeded as
the OWNER, and read back, so it is provably there before the attacker fails to see it.
"""

import copy

import pytest
from test_sync import (  # noqa: F401 - fixtures are picked up by name
    TEST_DB,
    _pair,
    app_env,
    client,
    created_users,
    paired,
)

pytestmark = pytest.mark.skipif(not TEST_DB, reason="set BUILDER_TEST_DB to run")

_SHARED_FIXTURES = (app_env, client, created_users, paired)

#: The real shape, taken from `python -m analysis report` over this container's own
#: corpus, so the fixture cannot describe a document the builder does not produce.
REPORT = {
    "report_version": 1,
    "generated_at": "2026-09-06T16:49:55Z",
    "window_days": 7,
    "trend_headline": (
        "How often you test is up 100% on the 7 days before, which is the way you want it."
    ),
    "trends": [
        {
            "metric": "test_runs_per_hour",
            "label": "how often you test",
            "before": 2.0,
            "now": 4.0,
            "move": 1.0,
            "direction": "up",
            "good": True,
            "sessions_before": 5,
            "sessions_now": 6,
        }
    ],
    "agents": {
        "agents": 53,
        "produced": 51,
        "max_concurrent": 8,
        "agent_seconds": 42752.0,
        "wall_seconds": 69562.2,
        "busy_seconds": 14581.2,
        "parallelism": 2.93,
        "by_type": [
            {"name": "general-purpose", "agents": 52},
            {"name": "Explore", "agents": 1},
        ],
    },
    "contributions": {
        "assisted": 96,
        "alone": 8,
        "active_days": 2,
        "longest_streak": 2,
        "current_streak": 2,
        "days": [
            {"day": "2026-09-05", "assisted": 92, "alone": 0},
            {"day": "2026-09-06", "assisted": 4, "alone": 8},
        ],
    },
    "quality": {
        "runs": 41,
        "passed": 36,
        "failed": 5,
        "first_try_rate": 0.878,
        "time_to_green": {
            "n": 3,
            "median_seconds": 117,
            "worst_seconds": 466,
            "median_attempts": 2,
        },
        "reason": None,
    },
    "languages": {
        "lines": 10280,
        "generated_lines_excluded": 0,
        "languages": [
            {"name": "Python", "lines": 7942, "files": 45, "share": 0.773},
            {"name": "TypeScript", "lines": 922, "files": 11, "share": 0.09},
        ],
        "reason": None,
    },
    "prompting": {
        "attempts": 35,
        "clean": 8,
        "costly": 27,
        "clean_share": 0.229,
        "reason": None,
    },
}


def doc(**overrides) -> dict:
    d = copy.deepcopy(REPORT)
    d.update(overrides)
    return d


def test_a_report_round_trips_through_the_profile(client, paired):
    _, headers = paired
    r = client.put("/v1/profile/report", json=doc(), headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["report_version"] == 1

    got = client.get("/v1/profile/builder", headers=headers).json()["report"]
    assert got["agents"]["parallelism"] == 2.93
    assert got["quality"]["time_to_green"]["median_seconds"] == 117
    assert got["contributions"]["days"][0]["day"] == "2026-09-05"
    assert got["trends"][0]["metric"] == "test_runs_per_hour"
    assert got["languages"]["languages"][0]["name"] == "Python"


def test_a_refused_block_stays_null_rather_than_becoming_a_zero(client, paired):
    """The whole point of the refusals. A person whose corpus cannot support a number
    must not be shown one, and the round trip is where a null quietly becomes a 0."""
    _, headers = paired
    thin = doc(
        agents=None,
        contributions=None,
        trends=[],
        trend_headline=None,
        quality={
            "runs": 2,
            "passed": None,
            "failed": None,
            "first_try_rate": None,
            "time_to_green": None,
            "reason": "2 test run(s), 5 needed",
        },
        prompting=None,
        languages={
            "lines": 40,
            "generated_lines_excluded": 3000,
            "languages": None,
            "reason": "40 attributable line(s), 200 needed",
        },
    )
    assert client.put("/v1/profile/report", json=thin, headers=headers).status_code == 200

    got = client.get("/v1/profile/builder", headers=headers).json()["report"]
    assert got["agents"] is None
    assert got["contributions"] is None
    assert got["prompting"] is None
    assert got["quality"]["first_try_rate"] is None
    assert got["quality"]["reason"] == "2 test run(s), 5 needed"
    # A refused split still reports what it EXCLUDED. A person whose line count dropped by
    # three thousand deserves to know where it went.
    assert got["languages"]["languages"] is None
    assert got["languages"]["generated_lines_excluded"] == 3000


def test_a_second_report_replaces_the_first(client, paired):
    """One row per person. A report describes a corpus as it stands, and keeping the one
    it replaced would only let a screen show a description of a corpus that is gone."""
    _, headers = paired
    client.put("/v1/profile/report", json=doc(), headers=headers)
    client.put("/v1/profile/report", json=doc(window_days=30), headers=headers)
    got = client.get("/v1/profile/builder", headers=headers).json()["report"]
    assert got["window_days"] == 30


def test_a_field_the_spec_does_not_have_is_refused(client, paired):
    """`extra='forbid'` is the WHOLE of the enforcement here. Unlike the narrative there
    was no constrained decoder upstream that already knew this document's shape: the
    builder is ordinary Python and can grow a key without the spec growing one."""
    _, headers = paired
    bad = doc()
    bad["cost_per_agent_usd"] = 0.42
    assert client.put("/v1/profile/report", json=bad, headers=headers).status_code == 422


def test_a_nested_field_the_spec_does_not_have_is_refused(client, paired):
    """The nested half matters more: a block is where a module would grow a field, and a
    top-level-only check would wave it through."""
    _, headers = paired
    bad = doc()
    bad["agents"] = dict(bad["agents"], tokens=91_000)
    assert client.put("/v1/profile/report", json=bad, headers=headers).status_code == 422


def test_an_enum_value_the_spec_does_not_have_is_refused(client, paired):
    _, headers = paired
    bad = doc()
    bad["trends"] = [dict(bad["trends"][0], direction="sideways")]
    assert client.put("/v1/profile/report", json=bad, headers=headers).status_code == 422


def test_a_label_longer_than_the_spec_allows_is_refused(client, paired):
    """The bounds are enforced here or nowhere: nothing upstream checks a string length."""
    _, headers = paired
    bad = doc()
    bad["trends"] = [dict(bad["trends"][0], label="x" * 200)]
    assert client.put("/v1/profile/report", json=bad, headers=headers).status_code == 422


def test_one_persons_report_is_invisible_to_another(client, created_users, paired):
    _, victim = paired
    assert client.put("/v1/profile/report", json=doc(), headers=victim).status_code == 200
    assert client.get("/v1/profile/builder", headers=victim).json()["report"] is not None

    _, attacker = _pair(client, created_users)
    assert client.get("/v1/profile/builder", headers=attacker).json()["report"] is None


def test_an_attackers_put_writes_their_own_row_and_not_the_victims(client, created_users, paired):
    """The upsert is keyed on the viewer, so there is no id to point at somebody else's."""
    _, victim = paired
    client.put("/v1/profile/report", json=doc(), headers=victim)

    _, attacker = _pair(client, created_users)
    client.put("/v1/profile/report", json=doc(window_days=365), headers=attacker)

    assert client.get("/v1/profile/builder", headers=victim).json()["report"]["window_days"] == 7


def test_the_route_needs_a_device(client):
    assert client.put("/v1/profile/report", json=doc()).status_code == 401
    assert client.get("/v1/profile/builder").status_code == 401
