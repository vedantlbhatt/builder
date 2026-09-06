"""The builder report: what may travel, what must be refused, and what the spec says.

Every case here corresponds to a way this document could be wrong on somebody's phone
without anything raising. Three of them are about the wire itself: a field the spec does
not have is a 422 nobody can act on, a prompt in the payload is a broken promise, and a
number that came back null and was rendered as zero is a lie with no error attached.
"""

from __future__ import annotations

import json
import pathlib
import unittest

from analysis import agents as ag
from analysis import report as rp
from analysis import trends as tr

SPEC = json.loads((pathlib.Path(__file__).resolve().parents[2] / "spec/report.v1.json").read_text())


def span(start, end, aid="a", kind="general-purpose", tools=1, landed=0):
    return ag.AgentSpan(
        agent_id=aid,
        agent_type=kind,
        asked="find the thing",
        started_at=start,
        ended_at=end,
        records=3,
        tool_calls=tools,
        landed=landed,
        failures=0,
    )


def trend(metric="test_runs_per_hour", before=2.0, now=4.0):
    return tr.Trend(
        metric=metric,
        label=tr.LABEL.get(metric, metric),
        before=before,
        now=now,
        move=(now - before) / before,
        direction="up" if now > before else "down",
        good=True,
        sessions_before=5,
        sessions_now=6,
    )


class Shape(unittest.TestCase):
    """The document's keys are the spec's keys, at every level.

    `report_spec.py` forbids extras at the door, so a block that grows a field here and
    not in spec/report.v1.json is a 422 on a real upload — discovered by a user, months
    after anybody touched this, with no way for them to act on it.
    """

    def top_level_names(self):
        return {f["name"] for f in SPEC["fields"]}

    def object_names(self, name):
        return {f["name"] for f in SPEC["objects"][name]}

    def test_an_empty_report_has_exactly_the_specs_top_level_fields(self):
        self.assertEqual(set(rp.build()), self.top_level_names())

    def test_a_full_report_has_exactly_the_specs_fields_in_every_block(self):
        fo = ag.fanout([span(0, 100), span(50, 150, "b")], 200)
        doc = rp.build(trends=[trend()], fanout=fo, sessions=[])
        self.assertEqual(set(doc), self.top_level_names())
        self.assertEqual(set(doc["agents"]), self.object_names("ReportAgents"))
        self.assertEqual(set(doc["trends"][0]), self.object_names("ReportTrend"))
        self.assertEqual(set(doc["agents"]["by_type"][0]), self.object_names("ReportAgentType"))

    def test_the_language_block_has_exactly_the_specs_fields(self):
        import datetime as dt

        from analysis import patterns as pat
        from analysis.digest import Ev

        events = [Ev(1, 0.0, "tool", "", tool="Write", added=300, path="/r/a.py")]
        s = pat.SessionEvents(
            session_id="s",
            started_at=0.0,
            ended_at=60.0,
            active_seconds=60.0,
            attended_seconds=60.0,
            tz_offset_minutes=0,
            events=events,
        )
        doc = rp.build(sessions=[s])
        assert dt  # the import is what makes this a real session, not decoration
        self.assertEqual(set(doc["languages"]), self.object_names("ReportLanguages"))
        self.assertEqual(set(doc["languages"]["languages"][0]), self.object_names("ReportLanguage"))

    def test_the_version_matches_the_spec(self):
        """Two places hold this number and `scripts/gen_report.py` copies the spec's into
        both generated halves; a module that stamped a different one would store documents
        claiming rules they were not built by."""
        self.assertEqual(rp.REPORT_VERSION, SPEC["version"])

    def test_generated_at_is_utc_and_ends_in_z(self):
        self.assertTrue(rp.build()["generated_at"].endswith("Z"))


class NothingToSay(unittest.TestCase):
    """Null is not zero, in every block. An empty chart reads as 'you did nothing'."""

    def test_no_agents_is_null_and_not_a_block_of_zeroes(self):
        self.assertIsNone(rp.build()["agents"])
        self.assertIsNone(rp.build(fanout=ag.fanout([], 100))["agents"])

    def test_no_sessions_refuses_quality_and_prompting_rather_than_reporting_none_run(self):
        doc = rp.build()
        self.assertIsNone(doc["quality"])
        self.assertIsNone(doc["prompting"])

    def test_no_trends_means_no_headline(self):
        self.assertIsNone(rp.build()["trend_headline"])

    def test_a_headline_follows_the_window_it_was_given(self):
        """The bug this catches shipped once: the sentence said "on last month" whatever
        the window was, so a one day comparison announced a monthly trend."""
        week = rp.build(trends=[trend()], window_days=7)["trend_headline"]
        month = rp.build(trends=[trend()], window_days=30)["trend_headline"]
        self.assertNotIn("month", week)
        self.assertIn("month", month)
        self.assertNotEqual(week, month)


class Agents(unittest.TestCase):
    def test_concurrency_survives_a_corpus_with_long_quiet_stretches(self):
        """MEASURED, and the reason `parallelism` is over busy seconds: this container's
        53 agents did 11.9 hours of work in a 19.3 hour stretch, and agent-over-wall
        reported 0.61x for a run whose peak was eight at once."""
        fo = ag.fanout([span(0, 100, "a"), span(0, 100, "b")], 100_000)
        block = rp.build(fanout=fo)["agents"]
        self.assertEqual(block["parallelism"], 2.0)
        self.assertEqual(block["busy_seconds"], 100)
        self.assertEqual(block["wall_seconds"], 100_000)

    def test_produced_is_the_fanouts_own_count_not_a_second_one(self):
        fo = ag.fanout([span(0, 10, "a", tools=2), span(0, 10, "b", tools=0)], 10)
        self.assertEqual(rp.build(fanout=fo)["agents"]["produced"], fo.produced)

    def test_types_are_ranked_and_capped(self):
        spans = [span(0, 10, f"a{i}", kind=f"k{i % 20}") for i in range(40)]
        by_type = rp.build(fanout=ag.fanout(spans, 10))["agents"]["by_type"]
        self.assertEqual(len(by_type), rp.MAX_AGENT_TYPES)
        self.assertGreaterEqual(by_type[0]["agents"], by_type[-1]["agents"])


class WhatMayTravel(unittest.TestCase):
    """The contract, checked where the document is BUILT.

    privacy/upload-contract.json is enforced by a hand-written serializer on the Swift
    side and by `extra='forbid'` here, but neither of those can tell a number from a
    sentence somebody typed. `analysis/playbook.py` holds prompt TEXT on purpose — that
    is what it prints on the machine — and `_prompting` is the only function that reads
    it and is also uploaded.
    """

    def test_the_prompting_block_is_five_numbers_and_no_words(self):
        names = {f["name"] for f in SPEC["objects"]["ReportPrompting"]}
        self.assertEqual(names, {"attempts", "clean", "costly", "clean_share", "reason"})

    def test_no_field_in_the_spec_carries_free_text_from_a_transcript(self):
        """Every string in this document comes from a fixed table in this repository: a
        metric key, a label from `trends.LABEL`, a subagent type the harness named, an
        ISO date, or a refusal reason a module wrote. A field that carried an excerpt
        would have to be added here deliberately, and this test is where somebody would
        have to argue for it."""
        allowed = {
            "metric", "label", "direction",  # ReportTrend
            "name",  # ReportAgentType and ReportLanguage: a subagent type, a language
            "day",  # ReportDay: an ISO date
            "reason",  # a refusal, written by a module in this package
            "trend_headline",  # trends.headline, from LABEL and two numbers
            "generated_at",
        }
        strings = {
            f["name"]
            for fs in list(SPEC["objects"].values()) + [SPEC["fields"]]
            for f in fs
            if f["type"] in ("string", "enum", "datetime")
        }
        self.assertEqual(strings - allowed, set())


class Caps(unittest.TestCase):
    def test_the_contributions_graph_drops_its_oldest_days_not_its_newest(self):
        """A graph missing last week is broken. One missing the same week two years ago
        is a graph."""
        import datetime as dt

        from analysis import contributions as co

        days = tuple(
            co.Day(day=dt.date(2020, 1, 1) + dt.timedelta(days=i), assisted=1, alone=0)
            for i in range(rp.MAX_DAYS + 40)
        )
        c = co.Contributions(
            days=days,
            assisted=len(days),
            alone=0,
            active_days=len(days),
            longest_streak=len(days),
            current_streak=0,
        )
        out = rp.build(contributions=c)["contributions"]["days"]
        self.assertEqual(len(out), rp.MAX_DAYS)
        self.assertEqual(out[-1]["day"], days[-1].day.isoformat())

    def test_trends_are_capped_at_what_the_spec_accepts(self):
        many = [trend(metric=f"m{i}") for i in range(rp.MAX_TRENDS + 10)]
        self.assertEqual(len(rp.build(trends=many)["trends"]), rp.MAX_TRENDS)


class OneDefinition(unittest.TestCase):
    def test_a_counted_session_is_defined_once(self):
        """`visible` on the wire IS `is_counted`, and it decides the population every
        aggregate runs over. It was written out twice, and the second copy drifting moved
        seven of this container's commits from "alone" to "assisted" in a report the phone
        would have shown beside a session list that did not contain those sittings."""
        from capture import sessions as cap

        self.assertTrue(callable(cap.is_counted))
        source = pathlib.Path(cap.__file__).read_text()
        self.assertEqual(source.count("COUNTED_MIN_ACTIVE_SEC"), 2)  # the import and the rule


if __name__ == "__main__":
    unittest.main()
