"""The wire contract, asserted from the server's side of the trust boundary.

The client's guarantee is structural — it encodes through a generated key enum with no
synthesized Codable, so an undeclared field is unrepresentable. These tests are the other
half: even a hostile or stale client must not be able to push a field into Postgres.
"""

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from builder.contract import CONTRACT_VERSION, ENUM_VALUES, SessionUpload
from builder.routes.sync import sanity_gate

CONTRACT_JSON = Path(__file__).resolve().parents[2] / "privacy" / "upload-contract.json"
PUBLISHED = Path(__file__).resolve().parents[1] / "builder" / "static" / "upload-fields.json"


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

    # 'flat' must not be a legal token scope: it would let the ~3x subagent overcount
    # into the database wearing a legitimate label.
    assert "flat" not in ENUM_VALUES["token_scope"]
    with pytest.raises(ValidationError):
        valid_payload(token_scope="flat")


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


def test_gate_rejects_title_without_repo_name():
    """Titles are public-repo-only. A title on an anonymous session means the client's
    mode allowlist was not applied."""
    p = valid_payload(repo_name=None, title="Wired up Stripe webhooks")
    assert "without repo_name" in (sanity_gate(p) or "")


def test_valid_payload_passes():
    assert sanity_gate(valid_payload()) is None
