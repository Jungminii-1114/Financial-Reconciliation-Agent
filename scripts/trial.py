#!/usr/bin/env python3
"""Run the trial adapter from a checkout without installing dependencies."""

import sys
from pathlib import Path

if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10 이상이 필요합니다. python3.11 scripts/trial.py ... 로 실행하세요.")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from reconcile_agent.trials.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
