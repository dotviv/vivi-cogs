#!/usr/bin/env python3
"""Validate every cog's info.json.

Downloader reads these at install time and largely fails quietly when they are
wrong: a malformed file, a missing end-user data statement, or a Python floor
that does not match the code all surface as confusing installs rather than
errors. This runs in CI so they surface as a failed build instead.

    python tools/check_manifests.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The Python floor every cog declares. The code uses `X | Y` unions in
#: runtime-evaluated annotations, so 3.10 is the hard technical minimum; 3.11 is
#: the deliberate target. Red 3.5.24 itself allows >=3.8.1,<3.12.
TARGET_PYTHON = [3, 11, 0]

#: Required in each cog's info.json.
REQUIRED_KEYS = (
    "author",
    "name",
    "short",
    "description",
    "min_bot_version",
    "min_python_version",
    "end_user_data_statement",
)

#: Required in the repo-level info.json. Downloader's REPO_SCHEMA reads only
#: these four; min_python_version is per-cog and meaningless here.
REPO_REQUIRED_KEYS = ("author", "description", "install_msg", "short")

#: Vendoring source. Must never carry an info.json, or Downloader would install
#: it as a cog and the per-cog copies would be pointless.
VENDOR_SOURCE = "common"

#: Python packages in this repo that are not cogs and must not be treated as such.
NON_COG_DIRS = {VENDOR_SOURCE, "tools", "tests"}


def _cog_dirs() -> List[Path]:
    """Directories that present themselves as cogs."""
    return sorted(
        path
        for path in REPO_ROOT.iterdir()
        if path.is_dir()
        and not path.name.startswith((".", "_"))
        and (path / "__init__.py").is_file()
        and path.name not in NON_COG_DIRS
    )


def _check_cog(path: Path) -> List[str]:
    problems: List[str] = []
    info_file = path / "info.json"

    if not info_file.is_file():
        return [f"{path.name}/ has an __init__.py but no info.json, so Downloader will not install it"]

    try:
        info = json.loads(info_file.read_text())
    except json.JSONDecodeError as error:
        return [f"{path.name}/info.json is not valid JSON: {error}"]

    for key in REQUIRED_KEYS:
        if key not in info:
            problems.append(f"{path.name}/info.json is missing the '{key}' key")

    version = info.get("min_python_version")
    if version is not None and version != TARGET_PYTHON:
        problems.append(
            f"{path.name}/info.json declares min_python_version {version}, "
            f"expected {TARGET_PYTHON}"
        )

    statement = info.get("end_user_data_statement")
    if isinstance(statement, str) and not statement.strip():
        problems.append(f"{path.name}/info.json has an empty end_user_data_statement")

    return problems


def main() -> int:
    problems: List[str] = []

    cogs = _cog_dirs()

    if not cogs:
        print("error: no cogs found", file=sys.stderr)
        return 1

    for path in cogs:
        problems.extend(_check_cog(path))

    # Repo-level manifest.
    repo_info_file = REPO_ROOT / "info.json"
    if not repo_info_file.is_file():
        problems.append("the repo-level info.json is missing")
    else:
        try:
            repo_info = json.loads(repo_info_file.read_text())
        except json.JSONDecodeError as error:
            problems.append(f"the repo-level info.json is not valid JSON: {error}")
        else:
            for key in REPO_REQUIRED_KEYS:
                if key not in repo_info:
                    problems.append(f"the repo-level info.json is missing the '{key}' key")

    # The vendoring source must stay uninstallable.
    if (REPO_ROOT / VENDOR_SOURCE / "info.json").is_file():
        problems.append(
            f"{VENDOR_SOURCE}/ has an info.json. It is the vendoring source, not a cog; "
            f"adding one would make Downloader install it and defeat tools/sync_common.py."
        )

    if problems:
        print("manifest problems:\n", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(f"manifests are valid: {', '.join(path.name for path in cogs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
