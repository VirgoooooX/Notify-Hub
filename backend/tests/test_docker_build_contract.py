from __future__ import annotations

import re
import tomllib
from pathlib import Path


def test_python_builder_copies_project_metadata_files() -> None:
    project_root = Path(__file__).resolve().parents[2]
    project = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))
    dockerfile = (project_root / "deploy" / "Dockerfile").read_text(encoding="utf-8")
    metadata_files = {
        project["project"]["readme"],
        project["project"]["license"]["file"],
    }
    copy_sources = {
        token
        for instruction in re.findall(r"^COPY\s+([^\n]+?)\s+\./$", dockerfile, re.MULTILINE)
        for token in instruction.split()
    }
    assert metadata_files <= copy_sources
