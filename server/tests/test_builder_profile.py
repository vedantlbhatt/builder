"""The builder profile: an aggregate of session analyses, per person, AS builder_app.

docs/analysis.md: a profile is an honest aggregate of sessions, never one run's
impression. These tests seed real analyses through `/v1/sync/sessions:batch` — varying the
dimension scores, archetypes, tags and build style across four sessions with a known order
— and check that the means, the modal archetype, the sign of the trend and the tag counts
are the ones a person could recompute by hand. Then: two sessions is not a profile, live
snapshots and sessions outside the window do not count, and another user's analyses never
enter the aggregate even when the query names their id.

Same harness as test_sync.py, whose fixtures are reused directly.
"""

import copy
from datetime import UTC, datetime, timedelta

import pytest
from test_contract import SAMPLE_ANALYSIS
from test_sync import (  # noqa: F401 - fixtures are picked up by name
    TEST_DB,
    _live,
    _pair,
    _payload,
    _upload,
    app_env,
    client,
    created_users,
    paired,
)

pytestmark = pytest.mark.skipif(not TEST_DB, reason="set BUILDER_TEST_DB to run")

# pytest finds the imported fixtures through this module's namespace. Referencing them
# once here is what tells the linter the test parameters below do not shadow unused names.
_SHARED_FIXTURES = (app_env, client, created_users, paired)

DIMENSIONS = ("steering", "execution", "engineering", "product_instinct", "planning")


def _analysis(*scores: int, **overrides) -> dict:
    """SAMPLE_ANALYSIS with the five dimension scores (in DIMENSIONS order) and any
    top-level overrides."""
    a = copy.deepcopy(SAMPLE_ANALYSIS)
    a["dimensions"] = [
        {"dimension": d, "score": s, "rationale": "grounded in the digest"}
        for d, s in zip(DIMENSIONS, scores, strict=True)
    ]
    a.update(overrides)
    return a


def _session_at(days_ago: int, analysis: dict, **overrides) -> dict:
    started = datetime.now(UTC).replace(microsecond=0) - timedelta(days=days_ago)
    return _payload(
        started_at=started,
        ended_at=started + timedelta(hours=1),
        analysis=analysis,
        **overrides,
    )


#: Oldest first. steering rises across the four (60,60 -> 80,80: trend +20), execution
#: falls (90,90 -> 70,70: -20), engineering is flat at 50.
FOUR = [
    _session_at(
        10,
        _analysis(
            60,
            90,
            50,
            40,
            40,
            archetype="architect",
            tags=["sync", "contract"],
            build_style={**SAMPLE_ANALYSIS["build_style"], "planning": "light"},
            prompting={**SAMPLE_ANALYSIS["prompting"], "specificity": 60, "tone": "terse"},
            decision_patterns=[
                {
                    "pattern": "Asks for the measurement before the constant",
                    "prompt_excerpt": "what does the corpus say",
                    "effect": None,
                }
            ],
            confidence=0.6,
        ),
    ),
    _session_at(
        7,
        _analysis(
            60,
            90,
            50,
            50,
            50,
            archetype="architect",
            tags=["sync"],
            build_style={**SAMPLE_ANALYSIS["build_style"], "planning": "light"},
            prompting={**SAMPLE_ANALYSIS["prompting"], "specificity": 70, "tone": "neutral"},
            decision_patterns=[],
            confidence=0.8,
        ),
    ),
    _session_at(
        4,
        _analysis(
            80,
            70,
            50,
            60,
            60,
            archetype="explorer",
            tags=["sync", "contract", "maps"],
            build_style={**SAMPLE_ANALYSIS["build_style"], "planning": "plan_mode"},
            prompting={**SAMPLE_ANALYSIS["prompting"], "specificity": 80, "tone": "terse"},
            decision_patterns=[
                {
                    "pattern": "asks for the measurement before the constant",
                    "prompt_excerpt": "measure it first",
                    "effect": None,
                },
                {"pattern": "names the file", "prompt_excerpt": "in Tuning.swift", "effect": None},
            ],
            confidence=0.9,
        ),
    ),
    _session_at(
        1,
        _analysis(
            80,
            70,
            50,
            70,
            70,
            archetype=None,  # too short to say: counted as analysed, not as an archetype
            tags=[],
            build_style={**SAMPLE_ANALYSIS["build_style"], "planning": "light"},
            prompting={**SAMPLE_ANALYSIS["prompting"], "specificity": 90, "tone": "neutral"},
            decision_patterns=[],
            confidence=0.7,
        ),
    ),
]


def test_four_analysed_sessions_aggregate_the_way_a_person_would_recompute(client, paired):
    uid, headers = paired
    assert _upload(client, headers, *FOUR)["accepted"] == 4

    bp = client.get("/v1/profile", headers=headers).json()["builder_profile"]
    assert bp is not None
    assert bp["sessions_analysed"] == 4
    assert bp["window_days"] == 90
    assert bp["confidence_mean"] == pytest.approx(0.75)

    dims = bp["dimensions"]
    assert set(dims) == set(DIMENSIONS)
    assert dims["steering"] == {"mean": 70.0, "sessions": 4, "trend": 20.0}
    assert dims["execution"] == {"mean": 80.0, "sessions": 4, "trend": -20.0}
    assert dims["engineering"] == {"mean": 50.0, "sessions": 4, "trend": 0.0}
    # Rising by ten a session: recent half (60,70) minus older half (40,50).
    assert dims["product_instinct"]["trend"] == 20.0

    # Modal over the three sessions that had one; the null does not dilute the share.
    assert bp["archetype"] == {
        "modal": "architect",
        "share": pytest.approx(2 / 3, abs=1e-3),
        "with_archetype": 3,
        "distribution": {"architect": 2, "explorer": 1},
    }

    assert bp["build_style"]["planning"]["mode"] == "light"
    assert bp["build_style"]["planning"]["share"] == 0.75
    assert bp["build_style"]["planning"]["distribution"] == {"light": 3, "plan_mode": 1}
    # The other four keys were the sample's value on every session.
    for key, value in [
        ("iteration", "linear"),
        ("steering", "guided"),
        ("verification", "ran_tests"),
        ("scope_control", "held"),
    ]:
        assert bp["build_style"][key] == {"mode": value, "share": 1.0, "distribution": {value: 4}}

    assert bp["prompting"]["specificity_mean"] == 75.0
    assert bp["prompting"]["correction_share_mean"] == pytest.approx(0.1)
    assert bp["prompting"]["question_share_mean"] == pytest.approx(0.2)
    assert bp["prompting"]["tone_distribution"] == {"neutral": 2, "terse": 2}

    assert bp["tags"] == [
        {"tag": "sync", "sessions": 3},
        {"tag": "contract", "sessions": 2},
        {"tag": "maps", "sessions": 1},
    ]

    # Case-folded grouping, display casing and example from the most recent occurrence.
    assert bp["decision_patterns"] == [
        {
            "pattern": "asks for the measurement before the constant",
            "sessions": 2,
            "example": "measure it first",
        },
        {"pattern": "names the file", "sessions": 1, "example": "in Tuning.swift"},
    ]

    # The standalone route serves the same object, with the count beside it.
    alone = client.get("/v1/profile/builder", headers=headers).json()
    assert alone["builder_profile"] == bp
    assert alone["sessions_analysed"] == 4 and alone["min_sessions"] == 3
    assert alone["window_days"] == 90


def test_live_snapshots_and_sessions_outside_the_window_do_not_count(client, paired):
    uid, headers = paired
    _upload(client, headers, *FOUR)

    # A live checkpoint with an analysis describes work in progress: excluded.
    live_started = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=30)
    _upload(client, headers, _live(live_started, 20, analysis=SAMPLE_ANALYSIS))
    assert client.get("/v1/profile/builder", headers=headers).json()["sessions_analysed"] == 4

    # 200 days ago: outside the default 90, inside 365.
    old = _session_at(200, _analysis(10, 10, 10, 10, 10))
    _upload(client, headers, old)
    default = client.get("/v1/profile/builder", headers=headers).json()
    assert default["sessions_analysed"] == 4
    assert default["builder_profile"]["dimensions"]["engineering"]["mean"] == 50.0
    wide = client.get("/v1/profile/builder?window_days=365", headers=headers).json()
    assert wide["sessions_analysed"] == 5
    assert wide["builder_profile"]["dimensions"]["engineering"]["mean"] == 42.0

    assert client.get("/v1/profile/builder?window_days=366", headers=headers).status_code == 422
    assert client.get("/v1/profile/builder?window_days=0", headers=headers).status_code == 422


def test_two_analysed_sessions_is_not_a_profile(client, paired):
    """docs/analysis.md: do not compute an archetype from one run. Null, with the count
    beside it so the phone can say how far off the person is."""
    uid, headers = paired
    _upload(client, headers, *FOUR[:2])

    assert client.get("/v1/profile", headers=headers).json()["builder_profile"] is None
    alone = client.get("/v1/profile/builder", headers=headers).json()
    assert alone == {
        "builder_profile": None,
        "sessions_analysed": 2,
        "min_sessions": 3,
        "window_days": 90,
    }

    # The third one tips it.
    _upload(client, headers, FOUR[2])
    assert client.get("/v1/profile/builder", headers=headers).json()["sessions_analysed"] == 3
    assert client.get("/v1/profile", headers=headers).json()["builder_profile"] is not None


def test_another_users_analyses_never_enter_the_aggregate(client, created_users):
    """RLS on session_analysis, as builder_app. Through the route, and then through the
    aggregate itself with the OTHER user's id in the query and the viewer set to this
    one — the case where a `user_id = :u` filter alone would leak."""
    from builder.builder_profile import builder_profile
    from builder.db import db_session

    uid_a, headers_a = _pair(client, created_users)
    uid_b, headers_b = _pair(client, created_users)
    _upload(client, headers_b, *FOUR)

    assert client.get("/v1/profile/builder", headers=headers_b).json()["sessions_analysed"] == 4
    a = client.get("/v1/profile/builder", headers=headers_a).json()
    assert a["builder_profile"] is None and a["sessions_analysed"] == 0

    with db_session(viewer_id=uid_a) as db:
        assert builder_profile(db, uid_b, 90) == (None, 0)
    with db_session(viewer_id=uid_b) as db:
        assert builder_profile(db, uid_b, 90)[1] == 4
