"""The constants capture reads, each with where it comes from.

Every value here mirrors `Packages/BuilderKit/Sources/BuilderModel/Tuning.swift`, which
carries the measurement behind it. The boundary constants are NOT repeated: they are read
from `scripts/measure_boundaries.py`, the reference the fixtures pin, so this file cannot
disagree with it. If you change a number here without changing Tuning.swift and its
comment, the two clients will describe the same transcript differently.
"""

from __future__ import annotations

from . import reference as _ref

#: Tuning.tauSessionSec — the idle gap that ends a session. From the reference.
TAU_SESSION_SEC: float = _ref.mb.TAU_SESSION
#: Tuning.activeGapCapSec — the most one gap credits to active time. From the reference.
ACTIVE_GAP_CAP_SEC: float = _ref.mb.ACTIVE_GAP_CAP
#: Tuning.tauAutonomousSec — silence after which the agent is on its own. From the reference.
TAU_AUTONOMOUS_SEC: float = _ref.mb.TAU_AUTONOMOUS
#: Tuning.dayBoundaryHour — 04:00 local begins a new day. From the reference.
DAY_BOUNDARY_HOUR: int = _ref.mb.DAY_BOUNDARY_HOUR

#: Tuning.sessionizerVersion / activeCalcVersion — carried on every payload so a retuned
#: rule is a recompute on the server, not a migration.
SESSIONIZER_VERSION = 2
ACTIVE_CALC_VERSION = 1

#: Tuning.liveUploadMinIntervalSec (60). "Minimum interval between two uploads of the
#: same live (open/idle) session. UNMEASURED JUDGEMENT CALL: one tick of the daemon, which
#: is the finest cadence anything upstream changes at."
LIVE_UPLOAD_MIN_INTERVAL_SEC: float = 60

#: Tuning.countedMinActiveSec (300) / countedMinMeaningfulEvents (3): below both, a session
#: is not `visible` and the Mac does not upload it. Tuning.notableMinActiveSec (1200):
#: the floor for a card, a record, a notification — and for `unattended`, which the
#: server re-derives and rejects if it disagrees.
COUNTED_MIN_ACTIVE_SEC: float = 300
COUNTED_MIN_MEANINGFUL_EVENTS = 3
NOTABLE_MIN_ACTIVE_SEC: float = 1200

#: Tuning.tauCommitAttributionSec (1800): how far before a session's start `git log`
#: looks when attributing commits to it.
TAU_COMMIT_ATTRIBUTION_SEC: float = 1800

#: Tuning.syntheticModelSentinel — a literal model string on locally generated error and
#: interrupt placeholders (MEASURED: 15 records). Not an API call; carries no real usage.
SYNTHETIC_MODEL_SENTINEL = "<synthetic>"

#: Tuning.repoPepper / repoPepperVersion / repoHashPrefix. GLOBAL and NOT SECRET: two
#: machines must derive the same hash for the same repository (PRIVACY.md says so).
REPO_PEPPER = b"builder-dev-pepper-not-final"
REPO_PEPPER_VERSION = 1
REPO_HASH_PREFIX = "builder-repo-v1|"

#: Tuning.gitExcludePathspecs — vendored and generated files inflate both sides of any
#: line comparison. `--` always precedes them: Claude Code project dir names begin with `-`.
GIT_EXCLUDE_PATHSPECS = [
    ":(exclude)*.lock",
    ":(exclude)package-lock.json",
    ":(exclude)bun.lockb",
    ":(exclude)yarn.lock",
    ":(exclude)Pods/**",
    ":(exclude)node_modules/**",
    ":(exclude)*.pbxproj",
    ":(exclude)*.xcworkspacedata",
]

#: The server's device-grant lifetime (`expires_in: 900`) and its poll interval.
PAIR_TIMEOUT_SEC = 900
