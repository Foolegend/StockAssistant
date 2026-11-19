#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Keep the virtualenv in sync with requirements.txt.

The virtualenv is created once and then reused, so it silently drifts away
from the pinned dependencies in requirements.txt: the service keeps running
against whatever was installed the first time.  This script compares a hash
of requirements.txt with the hash recorded inside the venv and reinstalls when
they differ.

Usage:
    python ensure_env.py            # install when out of sync
    python ensure_env.py --check    # report only, never install

Only the standard library is used, so it runs before the dependencies
themselves are installed.
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

BACK_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACK_ROOT.parent
REQUIREMENTS = BACK_ROOT / "requirements.txt"
HASH_FILE = PROJECT_ROOT / ".venv" / ".requirements-hash"


def requirements_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def recorded_hash(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def install(python: str) -> int:
    print("[env] requirements.txt changed, reinstalling dependencies...")
    completed = subprocess.run(
        [python, "-m", "pip", "install", "-r", str(REQUIREMENTS), "--quiet"],
        check=False,
    )
    if completed.returncode != 0:
        print("[env] dependency installation failed", file=sys.stderr)
        return completed.returncode
    print("[env] dependencies are up to date")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync the venv with requirements.txt")
    parser.add_argument(
        "--check",
        action="store_true",
        help="report whether the venv matches requirements.txt without installing",
    )
    args = parser.parse_args()

    if not REQUIREMENTS.is_file():
        print(f"[env] requirements file missing: {REQUIREMENTS}", file=sys.stderr)
        return 1

    python = sys.executable
    current = requirements_hash(REQUIREMENTS)
    stored = recorded_hash(HASH_FILE)

    if stored == current:
        print("[env] virtualenv matches requirements.txt")
        return 0

    if args.check:
        print("[env] virtualenv is out of sync with requirements.txt")
        return 1

    result = install(python)
    if result != 0:
        return result

    HASH_FILE.parent.mkdir(parents=True, exist_ok=True)
    HASH_FILE.write_text(current, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
