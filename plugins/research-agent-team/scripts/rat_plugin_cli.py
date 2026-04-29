#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PLUGIN_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from research_agent_team.platform.codex.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
