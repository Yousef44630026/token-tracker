"""Run the live dashboard from this checkout, never from an installed copy."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tracker.export.live_dashboard import main  # noqa: E402, I001


if __name__ == "__main__":
    raise SystemExit(main())
