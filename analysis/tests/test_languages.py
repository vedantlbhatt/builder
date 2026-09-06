"""What you build in, and the four ways this number lies if you let it.

Every code-stats product has a language breakdown and it is one of the easiest places to
print a confident wrong number. Three of the cases below correspond to something found by
running this on a real corpus rather than by thinking about it.
"""

from __future__ import annotations

import datetime as dt
import unittest

from analysis import languages as lg
from analysis import patterns as pt
from analysis.digest import Ev

T0 = dt.datetime(2026, 9, 1, 9, 0, tzinfo=dt.UTC).timestamp()


def wrote(n, path, added):
    return Ev(n, T0 + n, "tool", "", tool="Write", added=added, path=path)


def sess(events):
    return pt.SessionEvents(
        session_id="s",
        started_at=T0,
        ended_at=T0 + 3600,
        active_seconds=3600.0,
        attended_seconds=3600.0,
        tz_offset_minutes=0,
        events=events,
    )


def by(out, name):
    return next((x for x in out["languages"] or [] if x["name"] == name), None)


class WhatCounts(unittest.TestCase):
    def test_lines_are_the_unit_not_files(self):
        """A file count would weigh a one-line config change against a 400-line module."""
        out = lg.split([sess([wrote(1, "/r/big.py", 400), wrote(2, "/r/tiny.json", 1)])])
        self.assertGreater(by(out, "Python")["share"], 0.9)

    def test_a_language_is_counted_across_every_file_in_it(self):
        out = lg.split([sess([wrote(1, "/r/a.ts", 150), wrote(2, "/r/b.tsx", 150)])])
        self.assertEqual(by(out, "TypeScript"), {
            "name": "TypeScript", "lines": 300, "files": 2, "share": 1.0
        })

    def test_the_same_file_twice_is_one_file_and_both_edits(self):
        out = lg.split([sess([wrote(1, "/r/a.py", 200), wrote(2, "/r/a.py", 50)])])
        self.assertEqual(by(out, "Python")["files"], 1)
        self.assertEqual(by(out, "Python")["lines"], 250)


class NobodyWroteThese(unittest.TestCase):
    """MEASURED on this repository: `bun.lock` alone is over 3,000 lines. Counting it makes
    a lockfile format the top language on any day somebody ran an install."""

    def test_a_lockfile_is_not_a_language_you_chose(self):
        out = lg.split([sess([wrote(1, "/r/bun.lock", 3000), wrote(2, "/r/a.py", 300)])])
        self.assertIsNone(by(out, "JSON"))
        self.assertEqual(by(out, "Python")["share"], 1.0)

    def test_the_excluded_lines_are_reported_rather_than_hidden(self):
        """A person whose line count drops by three thousand deserves to know where it
        went. A silent exclusion is indistinguishable from a parser that missed the file."""
        out = lg.split([sess([wrote(1, "/r/bun.lock", 3000), wrote(2, "/r/a.py", 300)])])
        self.assertEqual(out["generated_lines_excluded"], 3000)
        self.assertEqual(out["lines"], 300)

    def test_lockfiles_are_excluded_by_name_and_not_by_extension(self):
        """`package-lock.json` is generated and `tsconfig.json` is not, and they share an
        extension. Excluding `.json` would delete work somebody did by hand."""
        self.assertIsNone(lg.language_of("/r/package-lock.json"))
        self.assertEqual(lg.language_of("/r/tsconfig.json"), "JSON")

    def test_this_repositorys_own_generated_files_are_excluded_too(self):
        """`make gen` writes Swift, TypeScript and Python from three specs. Those lines are
        real and they are not language choices."""
        self.assertIsNone(lg.language_of("/r/Sources/BuilderSync/Generated/UploadContract.swift"))
        self.assertIsNone(lg.language_of("/r/server/builder/report_spec.py"))
        self.assertIsNone(lg.language_of("/r/node_modules/x/index.js"))
        self.assertEqual(lg.language_of("/r/server/builder/report.py"), "Python")


class Unknowns(unittest.TestCase):
    def test_an_extension_nobody_mapped_is_other_and_is_still_counted(self):
        """`other` and EXCLUDED are different: one changes the denominator and the other
        does not. A guess here would read exactly like a measurement."""
        out = lg.split([sess([wrote(1, "/r/x.wat", 300), wrote(2, "/r/a.py", 300)])])
        self.assertEqual(by(out, "other")["lines"], 300)
        self.assertEqual(out["lines"], 600)

    def test_a_file_with_no_extension_is_named_when_the_name_is_the_language(self):
        self.assertEqual(lg.language_of("/r/Makefile"), "Make")
        self.assertEqual(lg.language_of("/r/Dockerfile"), "Dockerfile")

    def test_a_dotfile_is_not_an_extension(self):
        """`.gitignore` has no extension; reading `gitignore` as one would invent a
        language called that on every repository in the world."""
        self.assertEqual(lg.language_of("/r/.gitignore"), "other")

    def test_a_windows_path_is_read_the_same_way(self):
        self.assertEqual(lg.language_of(r"C:\\repo\\src\\main.rs"), "Rust")


class Refusals(unittest.TestCase):
    def test_too_few_lines_is_refused_with_the_count_that_forced_it(self):
        """A pie chart over 40 lines is a picture of one commit."""
        out = lg.split([sess([wrote(1, "/r/a.py", 40)])])
        self.assertIsNone(out["languages"])
        self.assertIn("40", out["reason"])
        self.assertIn(str(lg.MIN_LINES), out["reason"])

    def test_nothing_written_at_all_is_a_refusal_and_not_an_empty_chart(self):
        out = lg.split([sess([])])
        self.assertIsNone(out["languages"])
        self.assertEqual(out["lines"], 0)

    def test_a_long_tail_sums_into_other_rather_than_being_dropped(self):
        """Dropping it would make the shares not add up, and a chart whose slices sum to
        70% is a chart people stop trusting."""
        events = [wrote(i, f"/r/f{i}.{ext}", 100) for i, ext in enumerate(
            ["py", "ts", "go", "rs", "rb", "java", "kt", "swift", "lua", "dart", "hs", "ex"]
        )]
        out = lg.split([sess(events)])
        self.assertLessEqual(len(out["languages"]), lg.TOP_N + 1)
        self.assertAlmostEqual(sum(x["share"] for x in out["languages"]), 1.0, places=2)
        self.assertEqual(sum(x["lines"] for x in out["languages"]), out["lines"])


class OneDefinitionOfAWrite(unittest.TestCase):
    def test_only_events_the_line_total_already_counts_are_read(self):
        """`patterns._wrote` is what `lines_added_agent` sums. Reading a different set here
        would let this chart disagree with the total printed beside it."""
        events = [wrote(1, "/r/a.py", 300), Ev(2, T0 + 5, "tool", "ls", tool="Bash")]
        self.assertEqual(lg.split([sess(events)])["lines"], 300)
