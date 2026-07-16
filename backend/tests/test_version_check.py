from __future__ import annotations

import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "check_version.py"
SPEC = spec_from_file_location("notify_hub_version_check", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _write_version_files(root: Path, version: str) -> None:
    (root / "frontend").mkdir()
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "notify-hub"\nversion = "{version}"\n', encoding="utf-8"
    )
    (root / "uv.lock").write_text(
        f'[[package]]\nname = "notify-hub"\nversion = "{version}"\n', encoding="utf-8"
    )
    (root / "frontend/package.json").write_text(
        json.dumps({"name": "notify-hub-web", "version": version}), encoding="utf-8"
    )
    (root / "frontend/package-lock.json").write_text(
        json.dumps({"version": version, "packages": {"": {"version": version}}}),
        encoding="utf-8",
    )


def test_version_check_accepts_matching_metadata_and_tag(tmp_path: Path) -> None:
    _write_version_files(tmp_path, "1.2.3")
    assert MODULE.check_versions(tmp_path, "v1.2.3") == "1.2.3"


def test_version_check_rejects_mismatched_metadata(tmp_path: Path) -> None:
    _write_version_files(tmp_path, "1.2.3")
    package = json.loads((tmp_path / "frontend/package.json").read_text(encoding="utf-8"))
    package["version"] = "1.2.4"
    (tmp_path / "frontend/package.json").write_text(json.dumps(package), encoding="utf-8")

    with pytest.raises(RuntimeError, match=r"frontend/package\.json"):
        MODULE.check_versions(tmp_path)


def test_version_check_rejects_mismatched_tag(tmp_path: Path) -> None:
    _write_version_files(tmp_path, "1.2.3")
    with pytest.raises(RuntimeError, match="Git tag"):
        MODULE.check_versions(tmp_path, "v1.2.4")
