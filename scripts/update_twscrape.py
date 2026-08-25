#!/usr/bin/env python3
# ruff: noqa: S603, S607

"""Upgrade the locked twscrape dependency without changing unrelated packages."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tomllib
from pathlib import Path

PACKAGE_NAME = "twscrape"
OPTIONAL_PACKAGE_NAMES = ("curl-cffi",)


def locked_versions(lock_path: Path) -> dict[str, str]:
    """Return versions for twscrape and its optional HTTP backend."""
    if not lock_path.is_file():
        raise FileNotFoundError(f"lock file not found: {lock_path}")

    document = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    packages = document.get("package")
    if not isinstance(packages, list):
        raise ValueError(f"invalid package entries in {lock_path}")

    wanted = {PACKAGE_NAME, *OPTIONAL_PACKAGE_NAMES}
    versions: dict[str, str] = {}
    for package in packages:
        if not isinstance(package, dict):
            continue
        name = package.get("name")
        version = package.get("version")
        if isinstance(name, str) and name in wanted and isinstance(version, str):
            versions[name] = version
    missing = wanted - versions.keys()
    if missing:
        missing_names = ", ".join(sorted(missing))
        raise ValueError(f"expected packages are missing from {lock_path}: {missing_names}")
    return versions


def upgrade_lock(project_root: Path) -> None:
    """Ask uv to update only twscrape and the dependencies it needs."""
    try:
        subprocess.run(
            ["uv", "lock", "--upgrade-package", PACKAGE_NAME],
            cwd=project_root,
            check=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("uv was not found on PATH") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Update the locked twscrape dependency.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Print the currently locked versions without changing files.",
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    lock_path = project_root / "uv.lock"

    try:
        before = locked_versions(lock_path)
        if args.check:
            for name in (PACKAGE_NAME, *OPTIONAL_PACKAGE_NAMES):
                if name in before:
                    print(f"{name}=={before[name]}")
            return 0

        print(f"Updating {PACKAGE_NAME} in {lock_path.name}...")
        upgrade_lock(project_root)
        after = locked_versions(lock_path)
    except subprocess.CalledProcessError as exc:
        return exc.returncode or 1
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"twscrape update failed: {exc}", file=sys.stderr)
        return 1

    for name in (PACKAGE_NAME, *OPTIONAL_PACKAGE_NAMES):
        old = before.get(name, "not locked")
        new = after.get(name, "not locked")
        print(f"{name}: {old} -> {new}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
