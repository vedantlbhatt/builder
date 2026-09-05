"""Import the Python reference implementations that live outside any package.

`scripts/measure_boundaries.py` is a script, not a module on the path. Rather than copy
its rules — which is how two implementations start to disagree — this shim puts
`scripts/` on `sys.path` and imports it as `mb`. The fixtures under
`spec/fixtures/boundaries` are generated from that same module, so anything capture
computes through it is held to the same ground truth as the Swift engine.
"""

from __future__ import annotations

import importlib
import sys

from . import ROOT

_SCRIPTS = str(ROOT / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

mb = importlib.import_module("measure_boundaries")

__all__ = ["mb"]
