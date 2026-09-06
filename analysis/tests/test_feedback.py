"""One sitting's feedback, for the card you read once and close.

The bar here is deliberately different from `patterns.py` and the difference is the whole
design. A profile finding is a claim about a PERSON and needs a sample; a session note is
a claim about one hour the reader was present for, so the only bar is whether it was big
enough to have been worth their attention while it was happening. A card that flags
something every session is a card people stop reading.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import unittest

from analysis import feedback as fb
from analysis import patterns as pt
from analysis.digest import Ev

T0 = dt.datetime(2026, 9, 1, 9, 0, tzinfo=dt.UTC).timestamp()


def ev(n, ts, kind, text="", tool=None, added=None, path=None):
    return Ev(n, ts, kind, text, tool=tool, added=added, path=path)


def sess(events):
    return pt.SessionEvents(
        session_id="s",
        started_at=T0,
        ended_at=T0 + 7200,
        active_seconds=7200.0,
        attended_seconds=7200.0,
        tz_offset_minutes=0,
        events=events,
    )


def idle(n, ts):
    return ev(n, ts, "tool", "ls", tool="Bash")


def busywork(count, *, seconds_each=30.0, start=T0, n0=0):
    return [idle(n0 + i, start + i * seconds_each) for i in range(count)]


def by(notes, nid):
    return next((x for x in notes if x.id == nid), None)


def checkpoints(count, *, start=T0, n0=900):
    """Edits, so the sitting can be shown to contain progress this parser CAN see.

    Every spin case below needs these. A sitting with no write, test or commit anywhere in
    it is refused outright (`MIN_CHECKPOINT_DENSITY`), because the gaps in it measure what
    the parser could see rather than what the person did — and a test built out of pure
    busywork would otherwise pass on the refusal rather than on the bar it names, which is
    the negative test that passes for the wrong reason.
    """
    return [
        ev(n0 + i, start + i, "tool", "", tool="Edit", added=3, path=f"/repo/seen{i}.py")
        for i in range(count)
    ]


def seen(events, *, n=6):
    """`events` preceded by enough checkpoints to clear the density floor."""
    return checkpoints(n, start=events[0].ts - n - 1) + events


class TheBar(unittest.TestCase):
    def test_a_short_sitting_says_nothing(self):
        self.assertEqual(fb.notes(sess(busywork(10))), [])

    def test_a_long_run_that_also_cost_real_time_is_worth_a_line(self):
        got = by(fb.notes(sess(seen(busywork(50, seconds_each=30)))), "went_nowhere")
        self.assertIsNotNone(got)
        self.assertIn("50 tool calls", got.text)

    def test_forty_fast_greps_are_not_a_problem(self):
        """Calls alone are not the cost. A stretch that took ninety seconds is not
        something anybody would have interrupted."""
        self.assertEqual(fb.notes(sess(seen(busywork(50, seconds_each=2)))), [])

    def test_a_long_slow_stretch_under_the_call_bar_is_left_alone(self):
        # 30 calls over an hour: slow, but not the agent going nowhere.
        self.assertEqual(fb.notes(sess(seen(busywork(30, seconds_each=120)))), [])

    def test_the_session_bar_is_higher_than_the_profile_bar(self):
        """The profile is looking for a habit across sittings and can afford to notice a
        25 call run. A card that flags one of those every session gets ignored."""
        self.assertGreater(fb.NOTABLE_SPIN_CALLS, pt.SPIN_TOOL_CALLS)


class Notes(unittest.TestCase):
    def test_a_checkpoint_ends_the_stretch(self):
        events = busywork(30) + [
            ev(30, T0 + 900, "tool", "", tool="Edit", added=3, path="/repo/a.py")
        ] + busywork(30, start=T0 + 1000, n0=31)
        # Two runs of 30, neither over the 40 call bar.
        self.assertIsNone(by(fb.notes(sess(events)), "went_nowhere"))

    def test_several_stretches_report_the_worst_and_the_total(self):
        events = busywork(50, start=T0, n0=0)
        events += [ev(100, T0 + 1600, "tool", "", tool="Edit", added=3, path="/a.py")]
        events += busywork(45, start=T0 + 2000, n0=101)
        got = by(fb.notes(sess(seen(events))), "went_nowhere")
        self.assertEqual(got.numbers["runs"], 2)
        self.assertIn("in total", got.text)

    def test_a_failure_run_names_the_command(self):
        events = busywork(20)
        t = T0 + 1000
        for i in range(6):
            events.append(ev(100 + i * 2, t, "tool", "make test", tool="Bash"))
            events.append(ev(101 + i * 2, t + 5, "result_error", "boom"))
            t += 60
        got = by(fb.notes(sess(events)), "failed_in_a_row")
        self.assertIsNotNone(got)
        self.assertIn("6 failures in a row", got.text)
        self.assertIn("make test", got.text)

    def test_three_failures_is_debugging_and_not_a_note(self):
        events = busywork(20)
        t = T0 + 1000
        for i in range(3):
            events.append(ev(100 + i * 2, t, "tool", "make test", tool="Bash"))
            events.append(ev(101 + i * 2, t + 5, "result_error", "boom"))
            t += 60
        self.assertIsNone(by(fb.notes(sess(events)), "failed_in_a_row"))

    def test_a_non_shell_tool_is_named_rather_than_its_payload(self):
        events = busywork(20)
        t = T0 + 1000
        for i in range(6):
            events.append(
                ev(100 + i * 2, t, "tool", '{"a":1,"b":2}', tool="StructuredOutput")
            )
            events.append(ev(101 + i * 2, t + 5, "result_error", "schema"))
            t += 60
        got = by(fb.notes(sess(events)), "failed_in_a_row")
        self.assertIn("StructuredOutput", got.text)
        self.assertNotIn("{", got.text)

    def test_one_file_rewritten_over_and_over(self):
        events = busywork(20) + [
            ev(100 + i, T0 + 2000 + i * 120, "tool", "", tool="Edit", added=3, path="/r/Map.js")
            for i in range(6)
        ]
        got = by(fb.notes(sess(events)), "one_file_over_and_over")
        self.assertIsNotNone(got)
        self.assertIn("Map.js", got.text)
        self.assertIn("6 times", got.text)

    def test_four_passes_at_a_file_is_ordinary_editing(self):
        events = busywork(20) + [
            ev(100 + i, T0 + 2000 + i * 120, "tool", "", tool="Edit", added=3, path="/r/Map.js")
            for i in range(4)
        ]
        self.assertIsNone(by(fb.notes(sess(events)), "one_file_over_and_over"))

    def test_the_most_expensive_note_comes_first(self):
        events = busywork(50, seconds_each=30)
        t = T0 + 3000
        for i in range(6):
            events.append(ev(200 + i * 2, t, "tool", "make test", tool="Bash"))
            events.append(ev(201 + i * 2, t + 5, "result_error", "boom"))
            t += 10
        got = fb.notes(sess(events))
        self.assertEqual(got[0].id, "went_nowhere")
        self.assertGreater(got[0].seconds, got[1].seconds)

    def test_a_clean_sitting_gets_no_notes_rather_than_an_invented_one(self):
        events = []
        for i in range(20):
            events.append(
                ev(i * 2, T0 + i * 60, "tool", "", tool="Edit", added=3, path=f"/repo/f{i}.py")
            )
            events.append(ev(i * 2 + 1, T0 + i * 60 + 5, "tool", "pytest -q", tool="Bash"))
        self.assertEqual(fb.notes(sess(events)), [])


class Wording(unittest.TestCase):
    def busy(self):
        return fb.notes(sess(busywork(60, seconds_each=30)))

    def test_every_note_carries_its_number(self):
        for note in self.busy():
            self.assertRegex(note.text, r"\d")
            self.assertTrue(note.numbers)

    def test_no_note_uses_a_dash_as_punctuation(self):
        for note in self.busy():
            self.assertNotRegex(note.text, r"[—–―−]")

    def test_minutes_are_said_the_way_a_person_says_them(self):
        self.assertEqual(fb._mins(30), "under a minute")
        self.assertEqual(fb._mins(60), "1 minute")
        self.assertEqual(fb._mins(600), "10 minutes")
        self.assertEqual(fb._mins(3900), "1h 05m")


class TheParsersBlindSpot(unittest.TestCase):
    """A sitting where no progress is VISIBLE cannot be called a sitting with no progress.

    FOUND BY RUNNING IT, the first time a payload was built: 38 of the 45 boundary
    fixtures came back "the agent ran a long way with nothing to show", one of them for 22
    hours. Those fixtures contain no write, test or commit at all — and a real harness
    whose transcripts hide file writes (an edit made by a script the agent wrote is a Bash
    call with no line count anywhere in it) produces exactly the same shape. That card
    would be a statement about the parser printed as a statement about the person, on the
    screen people read most.
    """

    def test_a_sitting_with_no_visible_progress_says_nothing_about_spinning(self):
        self.assertEqual(fb.notes(sess(busywork(80, seconds_each=60))), [])

    def test_the_same_sitting_with_progress_in_it_does(self):
        got = by(fb.notes(sess(seen(busywork(80, seconds_each=60)))), "went_nowhere")
        self.assertIsNotNone(got)

    def test_the_bar_is_the_profiles_bar_and_not_a_second_number(self):
        """Two thresholds for one question would disagree about one session on two
        screens."""
        self.assertEqual(pt.MIN_CHECKPOINT_DENSITY, 1 / 20)

    def test_the_other_notes_do_not_need_progress_to_be_visible(self):
        """The guard is about STRETCHES BETWEEN checkpoints and nothing else. Failures and
        rewrites are counted from events that are there, so refusing them on a parser
        blind spot would be a second bug rather than a fix for the first."""
        events = busywork(20)
        t = T0 + 1000
        for i in range(6):
            events.append(ev(100 + i * 2, t, "tool", "make test", tool="Bash"))
            events.append(ev(101 + i * 2, t + 5, "result_error", "boom"))
            t += 60
        self.assertIsNotNone(by(fb.notes(sess(events)), "failed_in_a_row"))


class TheWire(unittest.TestCase):
    """What travels, and the two things that deliberately do not.

    The local note names the failing COMMAND and the FILE that was rewritten. Both are on
    privacy/upload-contract.json's never-list, and this is the function that has to keep
    them off the wire — the server cannot check for them, because to the server they would
    just be a string that validated.
    """

    def failing_session(self):
        events = busywork(20)
        t = T0 + 1000
        for i in range(6):
            events.append(ev(100 + i * 2, t, "tool", "bun test --coverage", tool="Bash"))
            events.append(ev(101 + i * 2, t + 5, "result_error", "boom"))
            t += 60
        return sess(events)

    def rewriting_session(self):
        events = busywork(20)
        for i in range(6):
            events.append(
                ev(200 + i, T0 + 2000 + i * 120, "tool", "", tool="Write",
                   added=40, path="/repo/mobile/src/components/Map.js")
            )
        return sess(events)

    def test_the_failing_command_stays_on_the_machine(self):
        s = self.failing_session()
        self.assertIn("bun test", by(fb.notes(s), "failed_in_a_row").text)
        self.assertNotIn("bun test", json.dumps(fb.wire(s)))

    def test_the_file_name_stays_on_the_machine(self):
        s = self.rewriting_session()
        self.assertIn("Map.js", by(fb.notes(s), "one_file_over_and_over").text)
        self.assertNotIn("Map.js", json.dumps(fb.wire(s)))

    def test_the_sentence_itself_does_not_travel(self):
        """The client writes it from the id, so rewording a note is a client release and
        not a re-upload of everybody's history."""
        for note in fb.wire(self.failing_session()):
            self.assertEqual(set(note), {"id", "seconds", "count"})

    def test_every_id_on_the_wire_is_one_the_contract_declares(self):
        contract = json.loads(
            (pathlib.Path(__file__).resolve().parents[2] / "privacy/upload-contract.json").read_text()
        )
        declared = next(f for f in contract["fields"] if f["name"] == "feedback")["values"]
        for s in (self.failing_session(), self.rewriting_session()):
            for note in fb.wire(s):
                self.assertIn(note["id"], declared)

    def test_the_count_means_the_right_thing_per_note(self):
        """Three notes count three different things. A shared "count" that meant stretches
        in one and failures in another is a plausible wrong number on the card."""
        failed = fb.wire(self.failing_session())[0]
        self.assertEqual(failed["id"], "failed_in_a_row")
        self.assertEqual(failed["count"], 6)

        rewrote = fb.wire(self.rewriting_session())[0]
        self.assertEqual(rewrote["id"], "one_file_over_and_over")
        self.assertEqual(rewrote["count"], 6)

    def test_nothing_worth_saying_is_none_and_not_an_empty_list(self):
        """A sitting with nothing to say and a sitting the client could not read must not
        look the same, and only one of them gets a key on the wire."""
        self.assertIsNone(fb.wire(sess(busywork(10))))

    def test_seconds_are_whole_numbers(self):
        """`FeedbackNoteWire.seconds` is an int at the door; a float here is a 422 on a
        real upload."""
        for note in fb.wire(self.failing_session()):
            self.assertIsInstance(note["seconds"], int)


if __name__ == "__main__":
    unittest.main()
