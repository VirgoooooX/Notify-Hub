from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
from pathlib import Path


def read_versions(project_root: Path) -> dict[str, str]:
    pyproject = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))
    package = json.loads((project_root / "frontend/package.json").read_text(encoding="utf-8"))
    package_lock = json.loads(
        (project_root / "frontend/package-lock.json").read_text(encoding="utf-8")
    )
    lock_content = (project_root / "uv.lock").read_text(encoding="utf-8")
    lock_match = re.search(
        r'\[\[package\]\]\r?\nname = "notify-hub"\r?\nversion = "([^"]+)"',
        lock_content,
    )
    if lock_match is None:
        raise RuntimeError("Could not find notify-hub in uv.lock")
    root_lock = package_lock.get("packages", {}).get("", {})
    return {
        "pyproject.toml": str(pyproject["project"]["version"]),
        "uv.lock": lock_match.group(1),
        "frontend/package.json": str(package["version"]),
        "frontend/package-lock.json": str(package_lock["version"]),
        "frontend/package-lock.json#root": str(root_lock.get("version", "")),
    }


def check_versions(project_root: Path, tag: str | None = None) -> str:
    versions = read_versions(project_root)
    expected = versions["pyproject.toml"]
    mismatches = {name: value for name, value in versions.items() if value != expected}
    if mismatches:
        details = ", ".join(f"{name}={value!r}" for name, value in mismatches.items())
        raise RuntimeError(f"Version metadata does not match {expected!r}: {details}")
    if tag is not None:
        expected_tag = f"v{expected}"
        if tag != expected_tag:
            raise RuntimeError(f"Git tag {tag!r} does not match {expected_tag!r}")
    return expected


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate Notify Hub release version metadata.")
    parser.add_argument("--tag", default=os.environ.get("GITHUB_REF_NAME"))
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parent.parent
    try:
        resolved = check_versions(project_root, args.tag)
    except (KeyError, OSError, RuntimeError, ValueError) as exc:
        print(f"Version check failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"Version metadata is consistent: {resolved}")


if __name__ == "__main__":
    main()
