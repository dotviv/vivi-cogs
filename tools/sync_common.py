#!/usr/bin/env python3
"""Vendor ``common/`` into each cog that depends on it.

Red's Downloader installs one folder per cog and nothing else. A top-level
``common/`` package has no ``info.json``, so it is classified
``InstallableType.UNKNOWN`` and never copied to the cogs directory -- meaning
``from common.interactions import ...`` resolves in a dev checkout and fails on
a real ``[p]cog install``. Red's own shared-library mechanism would solve this,
but it is deprecated and makes Downloader print a "marked for removal" warning
naming this repo to every user who installs from it.

So ``common/`` stays the single editable source of truth, and this script
copies it into ``<cog>/_common/`` for each cog in ``VENDORED_COGS``. Those
copies are committed, because Downloader installs straight from git.

    python tools/sync_common.py            # write the copies
    python tools/sync_common.py --check    # verify they are current (CI)

Because every cog carries its own copy, classes from ``_common`` must never be
passed between cogs -- two cogs' copies are unrelated types at runtime even
though they came from the same source. Cross-cog calls go through
``bot.get_cog(...)`` and primitives.
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path
from typing import Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent

SOURCE_DIR = REPO_ROOT / "common"

#: Cogs that receive a vendored copy. Add a cog here the moment it needs
#: anything from ``common/``; CI fails if a cog imports ``_common`` without
#: being listed, or is listed without using it.
VENDORED_COGS = (
    "audit",
    "moderation",
    "modlog",
    "quarantine",
    "topics",
    "verification",
)

VENDOR_DIR_NAME = "_common"

HEADER = """\
# ---------------------------------------------------------------------------
# GENERATED FILE -- DO NOT EDIT.
#
# Vendored from common/{name} by tools/sync_common.py.
# Edit the source file and re-run the script; CI rejects drift.
# ---------------------------------------------------------------------------

"""


def _source_files() -> List[Path]:
    """Every module in ``common/``, sorted so output is deterministic."""
    return sorted(SOURCE_DIR.glob("*.py"))


def _render(source: Path) -> str:
    """The exact content the vendored copy of ``source`` should have.

    The banner is written as comments rather than a docstring so that a source
    file starting with ``from __future__ import annotations`` stays valid --
    comments may precede a future import, another statement may not.
    """
    return HEADER.format(name=source.name) + source.read_text()


def _expected(cog: str) -> Dict[Path, str]:
    """Map of vendored path -> intended content for one cog."""
    vendor_dir = REPO_ROOT / cog / VENDOR_DIR_NAME
    return {vendor_dir / source.name: _render(source) for source in _source_files()}


def _existing(cog: str) -> List[Path]:
    vendor_dir = REPO_ROOT / cog / VENDOR_DIR_NAME
    if not vendor_dir.is_dir():
        return []
    return sorted(vendor_dir.glob("*.py"))


def _write(cog: str) -> List[str]:
    """Sync one cog. Returns a description of each change made."""
    changes: List[str] = []
    expected = _expected(cog)
    vendor_dir = REPO_ROOT / cog / VENDOR_DIR_NAME
    vendor_dir.mkdir(parents=True, exist_ok=True)

    for path, content in expected.items():
        rel = path.relative_to(REPO_ROOT)
        if not path.exists():
            path.write_text(content)
            changes.append(f"created {rel}")
        elif path.read_text() != content:
            path.write_text(content)
            changes.append(f"updated {rel}")

    # A module deleted from common/ must not linger in the copies.
    for path in _existing(cog):
        if path not in expected:
            path.unlink()
            changes.append(f"removed {path.relative_to(REPO_ROOT)} (no longer in common/)")

    return changes


def _check(cog: str) -> List[str]:
    """Verify one cog's copy is current. Returns a problem per stale file."""
    problems: List[str] = []
    expected = _expected(cog)

    for path, content in expected.items():
        rel = path.relative_to(REPO_ROOT)
        if not path.exists():
            problems.append(f"{rel} is missing")
            continue

        actual = path.read_text()

        if actual != content:
            diff = "\n".join(
                difflib.unified_diff(
                    content.splitlines(),
                    actual.splitlines(),
                    fromfile=f"expected/{rel}",
                    tofile=f"actual/{rel}",
                    lineterm="",
                )
            )

            problems.append(f"{rel} has drifted from common/{path.name}:\n{diff}")

    for path in _existing(cog):
        if path not in expected:
            rel = path.relative_to(REPO_ROOT)
            problems.append(f"{rel} is stale -- no matching file in common/")

    return problems


def _cog_dirs() -> List[str]:
    """Every installable cog in the repo, identified the way Downloader does."""
    return sorted(
        path.name
        for path in REPO_ROOT.iterdir()
        if path.is_dir() and (path / "info.json").is_file()
    )


def _check_imports() -> List[str]:
    """Guard the invariant the vendoring exists to protect.

    Two ways to get this wrong: importing the top-level ``common`` package from
    inside a cog (works in a checkout, breaks once installed), and letting
    ``VENDORED_COGS`` fall out of step with which cogs actually use ``_common``.
    """
    problems: List[str] = []

    for cog in _cog_dirs():

        uses_vendored = False

        for path in sorted((REPO_ROOT / cog).rglob("*.py")):

            if VENDOR_DIR_NAME in path.parts:
                continue

            rel = path.relative_to(REPO_ROOT)

            for number, line in enumerate(path.read_text().splitlines(), start=1):
                stripped = line.strip()

                if stripped.startswith(("from common.", "import common")) or stripped == "from common import":
                    problems.append(
                        f"{rel}:{number} imports the top-level 'common' package, which "
                        f"Downloader does not install. Use 'from .{VENDOR_DIR_NAME}...' instead."
                    )

                if f".{VENDOR_DIR_NAME}" in stripped and stripped.startswith(("from", "import")):
                    uses_vendored = True

        if uses_vendored and cog not in VENDORED_COGS:
            problems.append(
                f"{cog}/ imports '{VENDOR_DIR_NAME}' but is not in VENDORED_COGS "
                f"in tools/sync_common.py -- its copy will never be synced."
            )

        if not uses_vendored and cog in VENDORED_COGS:
            problems.append(
                f"{cog}/ is in VENDORED_COGS but imports nothing from '{VENDOR_DIR_NAME}' "
                f"-- remove it so the cog stops shipping dead code."
            )

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the vendored copies are current instead of writing them",
    )
    args = parser.parse_args()

    if not SOURCE_DIR.is_dir():
        print(f"error: {SOURCE_DIR} does not exist", file=sys.stderr)
        return 1

    missing = [cog for cog in VENDORED_COGS if not (REPO_ROOT / cog).is_dir()]

    if missing:
        print(f"error: VENDORED_COGS names missing cogs: {', '.join(missing)}", file=sys.stderr)
        return 1

    if args.check:

        problems = _check_imports()

        for cog in VENDORED_COGS:
            problems.extend(_check(cog))

        if problems:
            print("common/ vendoring is out of date:\n", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            print("\nRun: python tools/sync_common.py", file=sys.stderr)
            return 1

        print(f"common/ vendoring is current in: {', '.join(VENDORED_COGS)}")

        return 0

    changes: List[str] = []
    for cog in VENDORED_COGS:
        changes.extend(_write(cog))

    if changes:
        for change in changes:
            print(change)
    else:
        print("already up to date")

    problems = _check_imports()
    if problems:

        print("\nwarning: import problems remain:", file=sys.stderr)

        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
