"""The wire contract, asserted from the server's side of the trust boundary.

The client's guarantee is structural — it encodes through a generated key enum with no
synthesized Codable, so an undeclared field is unrepresentable. These tests are the other
half: even a hostile or stale client must not be able to push a field into Postgres.
"""

import base64
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from builder.contract import CONTRACT_VERSION, ENUM_VALUES, SessionUpload
from builder.routes.sync import sanity_gate

CONTRACT_JSON = Path(__file__).resolve().parents[2] / "privacy" / "upload-contract.json"
ANALYSIS_JSON = Path(__file__).resolve().parents[2] / "spec" / "analysis.v1.json"
PUBLISHED = Path(__file__).resolve().parents[1] / "builder" / "static" / "upload-fields.json"

#: A complete SessionAnalysis, every field populated, every enum a legal value. Shared
#: with test_sync.py so the document that validates here is the one that round-trips
#: through Postgres there.
SAMPLE_ANALYSIS: dict = {
    "analysis_version": 1,
    "model": "claude-opus-5",
    "generated_at": "2026-08-15T10:05:00Z",
    "digest_hash": "e" * 64,
    "digest_coverage": 1.0,
    "headline": "Wired the sync endpoint to the two-clock session fields",
    "summary": (
        "Added attended and autonomous seconds to the upload contract and the server. "
        "Ended on a green suite."
    ),
    "highlights": ["two clocks on every payload", "the sanity gate checks they sum to active"],
    "outcome": "shipped",
    "build_style": {
        "planning": "light",
        "iteration": "linear",
        "steering": "guided",
        "verification": "ran_tests",
        "scope_control": "held",
        "architecture_note": None,
    },
    "dimensions": [
        {"dimension": d, "score": 70, "rationale": "grounded in the digest"}
        for d in ["steering", "execution", "engineering", "product_instinct", "planning"]
    ],
    "archetype": "architect",
    "decision_patterns": [
        {
            "pattern": "asks for the measurement before the constant",
            "prompt_excerpt": "what does the corpus say before we pick a number",
            "effect": None,
        }
    ],
    "prompting": {
        "tone": "terse",
        "specificity": 80,
        "correction_share": 0.1,
        "question_share": 0.2,
        "note": None,
    },
    "growth_edge": ["write the negative test before the migration"],
    "tags": ["sync", "contract"],
    "confidence": 0.8,
    "contains_sensitive": False,
}


def valid_payload(**overrides) -> SessionUpload:
    started = datetime(2026, 8, 15, 9, 0, tzinfo=UTC)
    # A strip whose non-idle share matches active_seconds, or the strip/active gate fires.
    cols = bytes([0b0000_0010] * 1024)
    base = {
        "client_session_id": "a" * 64,
        "machine_id": "b" * 64,
        "content_hash": "c" * 64,
        "client_version": "0.1.0",
        "sessionizer_version": 1,
        "active_calc_version": 1,
        "harness": "claude_code",
        "agent_observed_at": started + timedelta(hours=2),
        "client_clock_offset_ms": 0,
        "started_at": started,
        "ended_at": started + timedelta(hours=1),
        "active_seconds": 3600,
        "idle_seconds": 0,
        "tz_offset_minutes": -420,
        "time_quality": "ok",
        "state": "final",
        "end_reason": "idle_gap",
        "attended_seconds": 3600,
        "autonomous_seconds": 0,
        "presence_count": 10,
        "unattended": False,
        "visible": True,
        "notable": True,
        "strip_columns": base64.b64encode(cols).decode(),
        "strip_marks": [],
        "timeline_fidelity": "full",
        "human_prompt_count": 10,
        "prompt_count_basis": "typed_promptsource",
        "tool_calls": {"Bash": 100},
        "files_touched": 5,
        "files_created": 1,
        "lines_added_agent": 200,
        "lines_removed_agent": 10,
        "commit_count": 2,
        "commit_insertions": 150,
        "commit_deletions": 20,
        "human_edit_events": 1,
        "agent_line_bucket": "nine_in_ten",
        "attrib_confidence": "high",
        "tokens_reported": True,
        "tokens": {
            "input": 100,
            "output": 200,
            "cache_read": 5000,
            "cache_w5m": 10,
            "cache_w1h": 20,
        },
        "abandoned_branch_tokens": 0,
        "token_dedupe": "message_id",
        "token_scope": "parent_aggregated",
        "token_coverage": "complete",
        "models": [{"model_id": "claude-opus-5[1m]", "output_token_share": 1.0}],
        "model_state": "known",
        "repo_hash": "d" * 64,
        "repo_pepper_version": 1,
        "repo_id_basis": "origin",
    }
    base.update(overrides)
    return SessionUpload(**base)


def test_undeclared_fields_are_rejected():
    """extra='forbid' is the belt to the client's braces.

    The client cannot represent an undeclared field; this ensures a modified or
    third-party client cannot either.
    """
    with pytest.raises(ValidationError):
        valid_payload(prompt_text="how do I center a div")

    with pytest.raises(ValidationError):
        valid_payload(file_paths=["/Users/me/secret/project/main.swift"])


def test_never_list_fields_have_no_home_in_the_model():
    """The things we promise never leave must not even be nameable."""
    forbidden = [
        "prompt",
        "prompt_text",
        "content",
        "diff",
        "patch",
        "structured_patch",
        "file_path",
        "file_paths",
        "file_name",
        "cwd",
        "branch",
        "branch_name",
        "commit_message",
        "commit_sha",
        "origin_url",
        "hostname",
        "ip",
        "mcp_servers",
        "command",
        "stdout",
    ]
    declared = set(SessionUpload.model_fields)
    assert declared.isdisjoint(forbidden), declared & set(forbidden)


def test_published_field_list_matches_the_model():
    """The file the privacy page tells people to `curl` must match reality.

    If these diverge, the verification command on /privacy fails in public — which is
    worse than not offering one.
    """
    published = set(json.loads(PUBLISHED.read_text())["fields"])
    declared = set(SessionUpload.model_fields)
    assert published == declared, {
        "declared_not_published": declared - published,
        "published_not_declared": published - declared,
    }


def test_contract_version_matches_source():
    source = json.loads(CONTRACT_JSON.read_text())
    assert source["version"] == CONTRACT_VERSION


def test_public_only_fields_are_marked_as_such():
    source = json.loads(CONTRACT_JSON.read_text())
    public_only = {f["name"] for f in source["fields"] if f["modes"] == ["public"]}
    assert public_only == {"repo_name", "title", "title_source"}


def test_enum_values_are_enforced():
    with pytest.raises(ValidationError):
        valid_payload(harness="cursor")  # the real value is cursor_ide
    assert "cursor_ide" in ENUM_VALUES["harness"]
    # The full list, pinned.
    assert sorted(ENUM_VALUES["harness"]) == [
        "aider",
        "claude_code",
        "cline",
        "codex",
        "cursor_agent",
        "cursor_ide",
        "gemini_cli",
        "opencode",
    ]
    for value in ("gemini_cli", "cline", "opencode", "aider"):
        assert valid_payload(harness=value).harness == value

    # 'flat' must not be a legal token scope: it would let the ~3x subagent overcount
    # into the database wearing a legitimate label.
    assert "flat" not in ENUM_VALUES["token_scope"]
    with pytest.raises(ValidationError):
        valid_payload(token_scope="flat")


def test_every_contract_harness_exists_in_the_postgres_enum():
    """A contract value the `harness` TYPE does not know is a 500 on every upload.

    MEASURED, 2026-09-06: adding `opencode` and `aider` to the contract regenerated the
    Pydantic model, the Swift enum and the TypeScript union, and all three accepted an
    `aider` payload. The database did not — `invalid input value for enum harness: "aider"`
    on the INSERT, after validation had already passed. The contract generator cannot see a
    Postgres type, so a contract change that adds an enum value is ALWAYS also a migration,
    and this reads the migrations rather than trusting a comment to be remembered.
    """
    labels: set[str] = set()
    versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    for path in sorted(versions.glob("*.py")):
        text_ = path.read_text()
        # 0001 creates the type; every later migration grows it with ADD VALUE.
        for m in re.finditer(r"CREATE TYPE harness AS ENUM \(([^)]*)\)", text_):
            labels |= set(re.findall(r"'([a-z_]+)'", m.group(1)))
        if "ALTER TYPE harness ADD VALUE" in text_:
            for m in re.finditer(r"NEW_VALUES = \(([^)]*)\)", text_):
                labels |= set(re.findall(r'"([a-z_]+)"', m.group(1)))
    missing = sorted(set(ENUM_VALUES["harness"]) - labels)
    assert not missing, f"harness values with no migration: {missing}"


def test_contract_v2_boundary_fields_and_enums():
    """v2: live snapshots and the two clocks. The server's cache-schema names (open, idle,
    finalizing) are not wire states — only what the Mac actually uploads is legal."""
    assert CONTRACT_VERSION == 2
    assert sorted(ENUM_VALUES["state"]) == ["final", "live"]
    assert sorted(ENUM_VALUES["end_reason"]) == [
        "cleared",
        "day_boundary",
        "human_returned",
        "idle_gap",
        "still_running",
        "switched_repo",
    ]
    for bad_state in ("open", "idle", "finalizing"):
        with pytest.raises(ValidationError):
            valid_payload(state=bad_state)
    with pytest.raises(ValidationError):
        valid_payload(end_reason="timeout")

    p = valid_payload(state="live", end_reason="still_running")
    assert p.state == "live"
    # All five boundary fields are required; a v1 client that omits them is a 422, not a
    # row of silent zeros.
    for missing in (
        "attended_seconds",
        "autonomous_seconds",
        "presence_count",
        "end_reason",
        "unattended",
    ):
        data = valid_payload().model_dump()
        del data[missing]
        with pytest.raises(ValidationError):
            SessionUpload(**data)


def test_analysis_validates_a_full_document_and_rejects_a_nested_extra_key():
    """`analysis` is the one prose field, and its shape is closed at every level: an
    undeclared key three objects deep is as unstorable as one at the top."""
    p = valid_payload(analysis=SAMPLE_ANALYSIS)
    assert p.analysis is not None
    assert p.analysis.model_dump(mode="json") == SAMPLE_ANALYSIS

    nested_extra = {
        **SAMPLE_ANALYSIS,
        "build_style": {**SAMPLE_ANALYSIS["build_style"], "vibe": "good"},
    }
    with pytest.raises(ValidationError):
        valid_payload(analysis=nested_extra)

    with pytest.raises(ValidationError):
        valid_payload(analysis={**SAMPLE_ANALYSIS, "raw_prompts": ["how do I center a div"]})

    # Bounded on the way in: the excerpt cap is what keeps "short verbatim quote" true.
    too_long = {
        **SAMPLE_ANALYSIS,
        "decision_patterns": [
            {"pattern": "p", "prompt_excerpt": "x" * 161, "effect": None},
        ],
    }
    with pytest.raises(ValidationError):
        valid_payload(analysis=too_long)

    # Absent and null are both legal, and both mean "no analysis".
    assert valid_payload().analysis is None
    assert valid_payload(analysis=None).analysis is None


def test_published_leaf_paths_cover_every_analysis_scalar():
    """The verification command walks every scalar path of a real payload. If a nested
    analysis path is missing from `leaf_paths`, the first person to run it in public sees
    "sent but not declared" on correct output — the failure the CLAUDE.md note describes.

    Reproduce the command's jq/sed pipeline over the sample document and require every
    path it yields to be published."""
    leaf_paths = set(json.loads(PUBLISHED.read_text())["leaf_paths"])

    def scalar_paths(value, at: str) -> list[str]:
        if isinstance(value, dict):
            return [p for k, v in value.items() for p in scalar_paths(v, f"{at}.{k}")]
        if isinstance(value, list):
            # The command strips list indices mid-path and trailing.
            return [p for v in value for p in scalar_paths(v, at)]
        return [at]

    actual = set(scalar_paths(SAMPLE_ANALYSIS, "analysis"))
    assert actual, "the sample should produce scalar paths"
    assert actual <= leaf_paths, {"sent_but_not_declared": sorted(actual - leaf_paths)}

    # And the walker did not invent paths the spec does not have.
    spec = json.loads(ANALYSIS_JSON.read_text())
    top = {f["name"] for f in spec["fields"]}
    assert {p.split(".")[1] for p in leaf_paths if p.startswith("analysis.")} == top


# --------------------------------------------------------------------- sanity gates


def test_gate_rejects_active_exceeding_elapsed():
    p = valid_payload(active_seconds=7200)
    assert "exceeds span" in (sanity_gate(p) or "")


def test_gate_rejects_undeduplicated_tokens():
    """The 1.878x content-block overcount, arriving unlabelled."""
    p = valid_payload(token_dedupe="none")
    assert "token_dedupe" in (sanity_gate(p) or "")


def test_gate_rejects_tokens_on_a_harness_that_reports_none():
    """Cursor writes {0,0} locally. Absent must stay absent, never become zero."""
    p = valid_payload(tokens_reported=False)
    assert "tokens_reported is false" in (sanity_gate(p) or "")


def test_gate_rejects_prompt_inflation():
    """More prompts than tool calls means the typed-prompt filter broke — the naive count
    over-reports by ~13x."""
    p = valid_payload(human_prompt_count=500, tool_calls={"Bash": 10})
    assert "exceeds tool_calls" in (sanity_gate(p) or "")


def test_gate_rejects_wrong_sized_strip():
    p = valid_payload(strip_columns=base64.b64encode(bytes([0] * 512)).decode())
    assert "512 bytes" in (sanity_gate(p) or "")


def test_gate_rejects_reserved_bits():
    """Bits 4-7 are reserved. A non-zero one means a client is writing a field we have
    not defined, and accepting it would burn the only expansion room the format has."""
    cols = bytearray([0b0000_0010] * 1024)
    cols[7] = 0b1000_0010
    p = valid_payload(strip_columns=base64.b64encode(bytes(cols)).decode())
    assert "reserved bits" in (sanity_gate(p) or "")


def test_gate_rejects_strip_disagreeing_with_active_time():
    """A strip built from a different event set than the session it describes is the
    hardest client bug to notice by eye, so the server checks they agree."""
    p = valid_payload(strip_columns=base64.b64encode(bytes([0] * 1024)).decode())
    assert "strip disagrees" in (sanity_gate(p) or "")


def test_gate_rejects_clocks_that_do_not_sum_to_active():
    """attended + autonomous is active by construction on the client. A mismatch means
    the two were computed from different event sets."""
    p = valid_payload(attended_seconds=3000, autonomous_seconds=0)
    assert "active_seconds is 3600" in (sanity_gate(p) or "")
    # A rounding second is fine.
    assert sanity_gate(valid_payload(attended_seconds=3599, autonomous_seconds=0)) is None
    assert sanity_gate(valid_payload(attended_seconds=1800, autonomous_seconds=1800)) is None


def test_gate_pins_unattended_to_presence():
    """The 5h40m robot: zero presence over a notable span must arrive as unattended, or
    it is a personal record again. And a session with a presence signal is a sitting."""
    p = valid_payload(presence_count=0, unattended=False, active_seconds=3600)
    assert "presence_count is 0" in (sanity_gate(p) or "")
    assert sanity_gate(valid_payload(presence_count=0, unattended=True)) is None

    p = valid_payload(presence_count=3, unattended=True)
    assert "unattended is true with presence_count 3" in (sanity_gate(p) or "")

    # Below the notable floor the flag is not read by anything, so it is not pinned.
    started = datetime(2026, 8, 15, 9, 0, tzinfo=UTC)
    short = valid_payload(
        presence_count=0,
        unattended=False,
        active_seconds=600,
        attended_seconds=600,
        ended_at=started + timedelta(minutes=10),
    )
    assert sanity_gate(short) is None


def test_gate_requires_state_and_end_reason_to_agree():
    assert "does not agree" in (sanity_gate(valid_payload(state="live")) or "")
    assert "does not agree" in (
        sanity_gate(valid_payload(state="final", end_reason="still_running")) or ""
    )
    assert sanity_gate(valid_payload(state="live", end_reason="still_running")) is None


def test_gate_gives_a_young_live_snapshot_strip_grace_only():
    """A 60-second-old live session has a nearly empty strip; the 25% tolerance would
    reject nearly every first snapshot. Above the grace window a live payload is held to
    the same rule as a final one, and every other strip check applies regardless."""
    started = datetime(2026, 8, 15, 9, 0, tzinfo=UTC)
    empty = base64.b64encode(bytes([0] * 1024)).decode()
    young = valid_payload(
        state="live",
        end_reason="still_running",
        active_seconds=120,
        attended_seconds=120,
        ended_at=started + timedelta(minutes=2),
        strip_columns=empty,
    )
    assert sanity_gate(young) is None

    grown = valid_payload(state="live", end_reason="still_running", strip_columns=empty)
    assert "strip disagrees" in (sanity_gate(grown) or "")

    bad_size = valid_payload(
        state="live",
        end_reason="still_running",
        active_seconds=120,
        attended_seconds=120,
        ended_at=started + timedelta(minutes=2),
        strip_columns=base64.b64encode(bytes([0] * 512)).decode(),
    )
    assert "512 bytes" in (sanity_gate(bad_size) or "")


def test_gate_rejects_title_without_repo_name():
    """Titles are public-repo-only. A title on an anonymous session means the client's
    mode allowlist was not applied."""
    p = valid_payload(repo_name=None, title="Wired up Stripe webhooks")
    assert "without repo_name" in (sanity_gate(p) or "")


def test_valid_payload_passes():
    assert sanity_gate(valid_payload()) is None
