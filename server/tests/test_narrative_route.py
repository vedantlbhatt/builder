"""The "how you work" page over the wire, AS builder_app.

The document is written on the user's own machine and arrives here already finished, so
the two things worth testing are the two things the server is actually responsible for:
that a claim which broke the spec cannot be stored, and that one person's narrative is
invisible to everyone else. The second is a real negative test, not one that passes for
the wrong reason (0004, and the write-isolation lesson in CLAUDE.md): the victim's row is
seeded as the OWNER, so it is definitely there before the attacker fails to read it.
"""

import copy
import uuid

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

NARRATIVE = {
    "archetype_line": "You run 5.75 test commands per active hour against a 3.0 threshold.",
    "how_you_work": [
        "You front-load: your opening prompt runs 340 characters and the rest average 188.",
        "Then you give it room: the median gap between your prompts is 9 tool calls.",
    ],
    "strengths": [{"text": "You verify before moving on.", "evidence": "13 of 14 edit bursts."}],
    "watch_outs": [{"text": "You correct course often.", "evidence": "steer_rate 0.433."}],
    "one_experiment": "Add a line to the opening prompt, see if corrections drop below 11.",
    "narrative_version": 1,
    "model": "sonnet",
    "generated_at": "2026-09-06T02:48:00Z",
    "dashes_rewritten": 0,
    "invented_numbers_dropped": 0,
}


def doc(**overrides) -> dict:
    d = copy.deepcopy(NARRATIVE)
    d.update(overrides)
    return d


def test_a_narrative_round_trips_through_the_profile(client, paired):
    _, headers = paired
    r = client.put("/v1/profile/narrative", json=doc(), headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["narrative_version"] == 1

    got = client.get("/v1/profile/builder", headers=headers).json()["narrative"]
    assert got["archetype_line"] == NARRATIVE["archetype_line"]
    assert len(got["how_you_work"]) == 2
    assert got["strengths"][0]["evidence"] == "13 of 14 edit bursts."


def test_a_person_with_no_narrative_gets_null_rather_than_an_empty_page(client, paired):
    _, headers = paired
    assert client.get("/v1/profile/builder", headers=headers).json()["narrative"] is None


def test_a_second_put_replaces_the_first(client, paired):
    _, headers = paired
    assert client.put("/v1/profile/narrative", json=doc(), headers=headers).status_code == 200
    second = doc(archetype_line="Something else entirely.", narrative_version=1, dashes_rewritten=2)
    assert client.put("/v1/profile/narrative", json=second, headers=headers).status_code == 200

    got = client.get("/v1/profile/builder", headers=headers).json()["narrative"]
    assert got["archetype_line"] == "Something else entirely."
    assert got["dashes_rewritten"] == 2


def test_a_paragraph_over_its_cap_is_refused(client, paired):
    _, headers = paired
    long_para = "x" * 401
    r = client.put(
        "/v1/profile/narrative", json=doc(how_you_work=[long_para, "ok"]), headers=headers
    )
    assert r.status_code == 422, r.text


def test_a_fifth_paragraph_is_refused(client, paired):
    _, headers = paired
    r = client.put("/v1/profile/narrative", json=doc(how_you_work=["a"] * 5), headers=headers)
    assert r.status_code == 422, r.text


def test_a_field_the_spec_does_not_have_is_refused(client, paired):
    """`extra='forbid'`: prose the contract never declared cannot be smuggled in beside it."""
    _, headers = paired
    r = client.put("/v1/profile/narrative", json=doc(repo_name="builder"), headers=headers)
    assert r.status_code == 422, r.text


def test_a_claim_missing_its_evidence_is_refused(client, paired):
    _, headers = paired
    r = client.put(
        "/v1/profile/narrative", json=doc(strengths=[{"text": "You verify."}]), headers=headers
    )
    assert r.status_code == 422, r.text


def test_one_persons_narrative_is_invisible_to_another(client, created_users, paired):
    """RLS, exercised through the route as builder_app.

    The victim stores theirs first and reads it back, so the row provably exists; only then
    does the attacker look. A test where the row was never written would pass without ever
    reaching a policy.
    """
    _, victim = paired
    assert client.put("/v1/profile/narrative", json=doc(), headers=victim).status_code == 200
    assert client.get("/v1/profile/builder", headers=victim).json()["narrative"] is not None

    _, attacker = _pair(client, created_users)
    assert client.get("/v1/profile/builder", headers=attacker).json()["narrative"] is None


def test_an_attackers_put_writes_their_own_row_and_not_the_victims(client, created_users, paired):
    """The upsert is keyed on the viewer, so there is no id to point at somebody else's."""
    _, victim = paired
    client.put("/v1/profile/narrative", json=doc(), headers=victim)

    _, attacker = _pair(client, created_users)
    client.put("/v1/profile/narrative", json=doc(archetype_line="Overwritten."), headers=attacker)

    assert (
        client.get("/v1/profile/builder", headers=victim).json()["narrative"]["archetype_line"]
        == NARRATIVE["archetype_line"]
    )


def test_the_route_needs_a_device(client):
    assert client.put("/v1/profile/narrative", json=doc()).status_code == 401
    assert (
        client.put(
            "/v1/profile/narrative",
            json=doc(),
            headers={"Authorization": f"Bearer {uuid.uuid4().hex}"},
        ).status_code
        == 401
    )
