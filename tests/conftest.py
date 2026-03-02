"""Pytest bootstrap for src-layout imports.

Ensures ``import memsearch`` works when running tests from repository root
without requiring callers to set ``PYTHONPATH=src`` manually.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
