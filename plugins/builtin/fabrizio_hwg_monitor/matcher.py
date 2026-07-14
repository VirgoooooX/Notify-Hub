from __future__ import annotations

import re

HERE_WE_GO_PATTERN = re.compile(r"(?i)(?:\bhere[\s._\-–—]*we[\s._\-–—]*go\b|\bhwg\b)")


def match_hwg(text: str) -> bool:
    return bool(HERE_WE_GO_PATTERN.search(text))
