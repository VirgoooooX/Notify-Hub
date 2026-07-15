from __future__ import annotations

import subprocess
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

RELEASE_SCRIPT = Path(__file__).parents[2] / "scripts" / "release.py"
SPEC = spec_from_file_location("notify_hub_release", RELEASE_SCRIPT)
assert SPEC is not None and SPEC.loader is not None
RELEASE_MODULE = module_from_spec(SPEC)
SPEC.loader.exec_module(RELEASE_MODULE)
git_operations = RELEASE_MODULE.git_operations


def _completed(
    args: list[str], returncode: int = 0, *, stdout: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr="")


def test_git_operations_skips_empty_commit_and_creates_tag(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.6.0"\n')
    calls: list[list[str]] = []

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[:4] == ["git", "diff", "--cached", "--quiet"]:
            return _completed(args, 0)
        if args[:3] == ["git", "rev-parse", "--verify"]:
            return _completed(args, 1)
        return _completed(args)

    with patch.object(RELEASE_MODULE.subprocess, "run", side_effect=run):
        git_operations(tmp_path, "0.6.0", auto_push=False)

    assert not any(args[:2] == ["git", "commit"] for args in calls)
    assert ["git", "tag", "-a", "v0.6.0", "-m", "Release v0.6.0"] in calls


def test_git_operations_accepts_existing_tag_on_head(tmp_path: Path) -> None:
    run = Mock(
        side_effect=[
            _completed(["git", "--version"]),
            _completed(["uv"]),
            _completed(["git", "diff"], 0),
            _completed(["git", "rev-parse"], 0, stdout="abc\n"),
            _completed(["git", "rev-parse", "HEAD"], 0, stdout="abc\n"),
        ]
    )

    with patch.object(RELEASE_MODULE.subprocess, "run", run):
        git_operations(tmp_path, "0.6.0", auto_push=False)

    assert not any(call.args[0][:2] == ["git", "tag"] for call in run.call_args_list)


def test_git_operations_rejects_existing_tag_on_other_commit(tmp_path: Path) -> None:
    run = Mock(
        side_effect=[
            _completed(["git", "--version"]),
            _completed(["uv"]),
            _completed(["git", "diff"], 0),
            _completed(["git", "rev-parse"], 0, stdout="old\n"),
            _completed(["git", "rev-parse", "HEAD"], 0, stdout="new\n"),
        ]
    )

    with (
        patch.object(RELEASE_MODULE.subprocess, "run", run),
        pytest.raises(RuntimeError, match="different commit"),
    ):
        git_operations(tmp_path, "0.6.0", auto_push=False)
