"""The builder profile: one person, read as an aggregate of their session analyses.

docs/analysis.md draws the line this module lives on. A session analysis is one model's
reading of one digest, scored per session so that a profile "can be an honest aggregate
rather than one run's impression" — and it says outright that a profile is never a single
run. So the profile is null below `MIN_SESSIONS`, and every number in it says how many
sessions it stands on.

Dimension means and trends are computed in SQL over `session_analysis.body`
(`jsonb_array_elements` on the dimensions list); the categorical parts — archetype,
build-style modes, tags, decision patterns — are counted in Python from the same rows,
because a mode with a tie-break is clearer in six lines of Python than in a window
function nobody will re-read.

Windowing is by the session's `started_at`, not the analysis's `generated_at`: the
question is "how have I been building lately", and a re-run analysis of an old session
does not make that session recent. Live snapshots are excluded, like every other
aggregate on the profile; a live checkpoint analysis describes work in progress.
"""

from __future__ import annotations

from collections import Counter

from sqlalchemy import text

#: docs/analysis.md: never compute an archetype from one run. Three is the smallest count
#: at which "most common" and "the other one" are different things.
MIN_SESSIONS = 3

DEFAULT_WINDOW_DAYS = 90
MAX_WINDOW_DAYS = 365

#: Enough tags to be a cloud, few enough to fit one line on the phone.
TOP_TAGS = 8
TOP_PATTERNS = 5

BUILD_STYLE_KEYS = ("planning", "iteration", "steering", "verification", "scope_control")

#: The window in SQL, shared by both queries so they cannot drift apart. Final and
#: visible, like the profile's totals; started inside the window.
_WINDOW = """
    FROM sessions s JOIN session_analysis a ON a.session_id = s.id
    WHERE s.user_id = :u AND s.state = 'final' AND s.visible
      AND s.started_at > now() - make_interval(days => :days)
"""


def builder_profile(db, user_id: str, window_days: int) -> tuple[dict | None, int]:
    """Returns (profile or None, sessions_analysed).

    The count travels separately so the phone can say "2 of 3 sessions analysed" instead
    of showing an empty card with no explanation.
    """
    bodies = [
        r.body
        for r in db.execute(
            text(f"SELECT a.body {_WINDOW} ORDER BY s.started_at DESC, s.id"),
            {"u": user_id, "days": window_days},
        ).all()
    ]
    n = len(bodies)
    if n < MIN_SESSIONS:
        return None, n

    # Per dimension: mean, count, and trend = mean over the most recent half minus mean
    # over the older half, in points. Halves are by session order; with an odd count the
    # median session goes to the older half (rn * 2 <= total is the recent half), so the
    # recent half is never larger than what it is compared against.
    dims = db.execute(
        text(
            f"""
            WITH analysed AS (
              SELECT a.body,
                     ROW_NUMBER() OVER (ORDER BY s.started_at DESC, s.id) AS rn,
                     COUNT(*) OVER () AS total
              {_WINDOW}
            )
            SELECT d->>'dimension' AS dimension,
                   AVG(CAST(d->>'score' AS numeric)) AS mean,
                   COUNT(*) AS n,
                   AVG(CASE WHEN rn * 2 <= total THEN CAST(d->>'score' AS numeric) END)
                     - AVG(CASE WHEN rn * 2 > total THEN CAST(d->>'score' AS numeric) END)
                     AS trend
            FROM analysed, jsonb_array_elements(body->'dimensions') AS d
            GROUP BY d->>'dimension'
            ORDER BY d->>'dimension'
            """
        ),
        {"u": user_id, "days": window_days},
    ).all()

    profile = {
        "window_days": window_days,
        "sessions_analysed": n,
        "confidence_mean": _mean([b.get("confidence") for b in bodies], 3),
        "dimensions": {
            d.dimension: {
                "mean": round(float(d.mean), 1),
                "sessions": int(d.n),
                "trend": round(float(d.trend), 1) if d.trend is not None else None,
            }
            for d in dims
        },
        "archetype": _archetype(bodies),
        "build_style": _build_style(bodies),
        "prompting": _prompting(bodies),
        "tags": _tags(bodies),
        "decision_patterns": _decision_patterns(bodies),
    }
    return profile, n


def _archetype(bodies: list[dict]) -> dict:
    """The modal non-null archetype and the whole distribution.

    `share` is out of the sessions that HAD an archetype, and `with_archetype` says how
    many that was: a session under about fifteen minutes gets none (docs/analysis.md),
    and diluting the share with those would make a person look less like anything.
    """
    counts = Counter(b["archetype"] for b in bodies if b.get("archetype") is not None)
    total = sum(counts.values())
    modal, modal_n = _mode(counts)
    return {
        "modal": modal,
        "share": round(modal_n / total, 3) if total else None,
        "with_archetype": total,
        "distribution": dict(_ranked(counts)),
    }


def _build_style(bodies: list[dict]) -> dict:
    out = {}
    for key in BUILD_STYLE_KEYS:
        values = [b["build_style"][key] for b in bodies if b.get("build_style", {}).get(key)]
        counts = Counter(values)
        mode, mode_n = _mode(counts)
        out[key] = {
            "mode": mode,
            "share": round(mode_n / len(values), 3) if values else None,
            "distribution": dict(_ranked(counts)),
        }
    return out


def _prompting(bodies: list[dict]) -> dict:
    ps = [b["prompting"] for b in bodies if b.get("prompting")]
    return {
        "specificity_mean": _mean([p.get("specificity") for p in ps], 1),
        "correction_share_mean": _mean([p.get("correction_share") for p in ps], 3),
        "question_share_mean": _mean([p.get("question_share") for p in ps], 3),
        "tone_distribution": dict(_ranked(Counter(p["tone"] for p in ps if p.get("tone")))),
    }


def _tags(bodies: list[dict]) -> list[dict]:
    # A tag counts once per session however many times a model repeats it.
    counts = Counter(t for b in bodies for t in set(b.get("tags") or []))
    return [{"tag": t, "sessions": c} for t, c in _ranked(counts)[:TOP_TAGS]]


def _decision_patterns(bodies: list[dict]) -> list[dict]:
    """The most frequent `pattern` strings, case-folded, each with one verbatim excerpt.

    Bodies arrive newest first, so the example and the display casing are the most
    recent time the model named the move.
    """
    counts: Counter[str] = Counter()
    first_seen: dict[str, tuple[str, str]] = {}
    for b in bodies:
        for dp in b.get("decision_patterns") or []:
            pattern = (dp.get("pattern") or "").strip()
            if not pattern:
                continue
            key = pattern.casefold()
            counts[key] += 1
            first_seen.setdefault(key, (pattern, dp.get("prompt_excerpt") or ""))
    return [
        {"pattern": first_seen[k][0], "sessions": c, "example": first_seen[k][1]}
        for k, c in _ranked(counts)[:TOP_PATTERNS]
    ]


def _ranked(counts: Counter) -> list[tuple]:
    """Most common first; ties broken by name so two pulls agree on the order."""
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def _mode(counts: Counter) -> tuple[str | None, int]:
    ranked = _ranked(counts)
    return ranked[0] if ranked else (None, 0)


def _mean(values: list, digits: int) -> float | None:
    xs = [float(v) for v in values if v is not None]
    return round(sum(xs) / len(xs), digits) if xs else None
