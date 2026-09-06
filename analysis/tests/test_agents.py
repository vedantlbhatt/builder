"""Several agents at once: who ran, whether they overlapped, and what must never happen.

The rule that outranks every measurement here: a sidecar NEVER contributes a token, a
line or a commit to any total. The parent's `Agent` tool result already reports that work
in aggregate, so counting it twice is the globbing revert in CLAUDE.md happening again.
This module reads the sidecars to describe the DELEGATION and nothing else.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import tempfile
import unittest

from analysis import agents

T0 = dt.datetime(2026, 9, 1, 9, 0, tzinfo=dt.UTC)


def stamp(offset: float) -> str:
    return (T0 + dt.timedelta(seconds=offset)).isoformat().replace("+00:00", "Z")


def rec(offset: float, **kw) -> str:
    return json.dumps({"timestamp": stamp(offset), "type": "assistant", **kw})


def tool(name: str, **inp) -> dict:
    return {"message": {"content": [{"type": "tool_use", "name": name, "input": inp}]}}


class Tree(unittest.TestCase):
    """A project directory with one root transcript and its subagent sidecars."""

    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp())
        self.sid = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
        self.transcript = self.root / f"{self.sid}.jsonl"
        self.transcript.write_text("")

    def parent(self, *lines: str):
        self.transcript.write_text("\n".join(lines) + "\n")

    def agent(self, agent_id: str, *lines: str, dirname: str = agents.SIDECAR_DIR):
        d = self.root / self.sid / dirname
        d.mkdir(parents=True, exist_ok=True)
        (d / f"agent-{agent_id}.jsonl").write_text("\n".join(lines) + "\n")

    def delegation(self, offset: float, description: str, subagent_type="general-purpose"):
        return rec(offset, **tool("Agent", description=description, subagent_type=subagent_type))


class Discovery(Tree):
    def test_a_sidecar_beside_the_transcript_is_found(self):
        self.agent("a1", rec(0, agentId="a1"))
        self.assertEqual([p.name for p in agents.sidecar_paths(self.transcript)], ["agent-a1.jsonl"])

    def test_a_sibling_directory_is_not_a_subagent_directory(self):
        """An ALLOWLIST on path shape, never a denylist on the name: the tree has sibling
        `workflows/` and `tool-results/` directories and a denylist waves them through."""
        self.agent("w1", rec(0, agentId="w1"), dirname="workflows")
        self.agent("t1", rec(0, agentId="t1"), dirname="tool-results")
        self.assertEqual(agents.sidecar_paths(self.transcript), [])

    def test_another_sessions_sidecars_do_not_leak_in(self):
        other = self.root / "ffffffff-bbbb-4ccc-8ddd-eeeeeeeeeeee" / agents.SIDECAR_DIR
        other.mkdir(parents=True)
        (other / "agent-x.jsonl").write_text(rec(0, agentId="x") + "\n")
        self.assertEqual(agents.sidecar_paths(self.transcript), [])

    def test_a_transcript_with_no_subagents_is_not_an_error(self):
        self.assertEqual(agents.sidecar_paths(self.transcript), [])
        self.assertEqual(agents.spans(self.transcript), [])


class Spans(Tree):
    def test_an_agent_is_described_by_what_it_was_asked_to_do(self):
        self.parent(self.delegation(0, "Rebuild the mobile data modules"))
        self.agent(
            "a1",
            rec(10, agentId="a1", attributionAgent="general-purpose"),
            rec(70, agentId="a1", **tool("Edit", file_path="/repo/a.py")),
        )
        s = agents.spans(self.transcript)[0]
        self.assertEqual(s.asked, "Rebuild the mobile data modules")
        self.assertEqual(s.agent_type, "general-purpose")
        self.assertEqual(s.landed, 1)
        self.assertAlmostEqual(s.seconds, 60, delta=1)

    def test_two_agents_spawned_in_one_turn_do_not_share_a_brief(self):
        self.parent(self.delegation(0, "first job"), self.delegation(1, "second job"))
        self.agent("a1", rec(10, agentId="a1"))
        self.agent("a2", rec(11, agentId="a2"))
        asked = sorted(s.asked for s in agents.spans(self.transcript))
        self.assertEqual(asked, ["first job", "second job"])

    def test_an_agent_with_no_matching_delegation_says_so_rather_than_guessing(self):
        self.agent("a1", rec(10, agentId="a1"))
        self.assertIsNone(agents.spans(self.transcript)[0].asked)

    def test_a_delegation_after_the_agent_started_is_not_its_brief(self):
        self.parent(self.delegation(99, "a later job"))
        self.agent("a1", rec(10, agentId="a1"))
        self.assertIsNone(agents.spans(self.transcript)[0].asked)

    def test_a_failed_tool_result_is_counted(self):
        self.agent(
            "a1",
            rec(0, agentId="a1"),
            json.dumps(
                {
                    "timestamp": stamp(5),
                    "type": "user",
                    "message": {"content": [{"type": "tool_result", "is_error": True}]},
                }
            ),
        )
        self.assertEqual(agents.spans(self.transcript)[0].failures, 1)

    def test_a_read_is_not_something_landing(self):
        self.agent("a1", rec(0, agentId="a1"), rec(5, agentId="a1", **tool("Read", file_path="/a")))
        s = agents.spans(self.transcript)[0]
        self.assertEqual(s.tool_calls, 1)
        self.assertEqual(s.landed, 0)
        self.assertFalse(s.produced)

    def test_a_partial_trailing_line_is_never_consumed(self):
        d = self.root / self.sid / agents.SIDECAR_DIR
        d.mkdir(parents=True)
        (d / "agent-a1.jsonl").write_text(rec(0, agentId="a1") + "\n" + rec(60, agentId="a1")[:20])
        s = agents.spans(self.transcript)[0]
        self.assertEqual(s.records, 1)
        self.assertEqual(s.seconds, 0.0)

    def test_the_spans_come_back_in_start_order(self):
        self.agent("late", rec(500, agentId="late"))
        self.agent("early", rec(10, agentId="early"))
        self.assertEqual([s.agent_id for s in agents.spans(self.transcript)], ["early", "late"])


class Concurrency(unittest.TestCase):
    @staticmethod
    def span(start, end, agent_id="a", agent_type="general-purpose", landed=0):
        return agents.AgentSpan(
            agent_id=agent_id,
            agent_type=agent_type,
            asked=None,
            started_at=start,
            ended_at=end,
            records=1,
            tool_calls=1,
            landed=landed,
            failures=0,
        )

    def test_agents_one_after_another_are_never_concurrent(self):
        fo = agents.fanout([self.span(0, 100, "a"), self.span(100, 200, "b")], 200)
        self.assertEqual(fo.max_concurrent, 1)
        self.assertEqual(fo.parallelism, 1.0)

    def test_a_handoff_at_the_same_instant_is_not_two_agents_at_once(self):
        """Ends sort before starts. Otherwise every consecutive chain reads as parallel."""
        fo = agents.fanout([self.span(0, 100, "a"), self.span(100, 300, "b")], 300)
        self.assertEqual(fo.max_concurrent, 1)

    def test_overlapping_agents_are_counted_at_their_peak(self):
        spans = [self.span(0, 100, "a"), self.span(10, 100, "b"), self.span(20, 30, "c")]
        self.assertEqual(agents.fanout(spans, 100).max_concurrent, 3)

    def test_the_peak_is_the_peak_and_not_the_total(self):
        # Three agents, never more than two at once.
        spans = [self.span(0, 50, "a"), self.span(40, 90, "b"), self.span(80, 120, "c")]
        fo = agents.fanout(spans, 120)
        self.assertEqual(fo.agents, 3)
        self.assertEqual(fo.max_concurrent, 2)

    def test_parallelism_is_agent_time_over_wall_time(self):
        """The honest statement of what fanning out bought: four hours of agent work
        inside one hour of your life."""
        spans = [self.span(0, 3600, f"a{i}") for i in range(4)]
        fo = agents.fanout(spans, 3600)
        self.assertEqual(fo.parallelism, 4.0)
        self.assertEqual(fo.agent_seconds, 14400)

    def test_a_span_shorter_than_the_timestamp_resolution_is_not_concurrency(self):
        spans = [self.span(0, 0.5, "blip"), self.span(0, 0.5, "blip2")]
        self.assertEqual(agents.fanout(spans, 100).max_concurrent, 1)

    def test_the_types_are_counted_and_an_unknown_one_is_named_unknown(self):
        spans = [
            self.span(0, 10, "a", "general-purpose"),
            self.span(0, 10, "b", "Explore"),
            self.span(0, 10, "c", None),
        ]
        self.assertEqual(
            agents.fanout(spans, 10).by_type, {"general-purpose": 1, "Explore": 1, "unknown": 1}
        )

    def test_how_many_delegations_produced_anything(self):
        spans = [self.span(0, 10, "a", landed=3), self.span(0, 10, "b", landed=0)]
        self.assertEqual(agents.fanout(spans, 10).produced, 1)

    def test_no_agents_is_zero_and_not_a_crash(self):
        fo = agents.fanout([], 100)
        self.assertEqual(fo.agents, 0)
        self.assertEqual(fo.max_concurrent, 0)
        self.assertEqual(fo.parallelism, 0.0)

    def test_no_wall_clock_refuses_a_ratio_rather_than_dividing_by_zero(self):
        self.assertEqual(agents.fanout([self.span(0, 10)], 0).parallelism, 0.0)


if __name__ == "__main__":
    unittest.main()
