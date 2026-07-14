"""Deterministic, source-independent matching rules."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from .schemas import CodexXMonitorConfig, XPost

URL_OR_MENTION_RE = re.compile(r"https?://\S+|(?<!\w)@[A-Za-z0-9_]+")
WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class MatchResult:
    matched: bool
    matched_rules: tuple[str, ...] = ()
    excluded_by: tuple[str, ...] = ()


def normalize_for_matching(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).lower()
    normalized = URL_OR_MENTION_RE.sub(" ", normalized)
    return WHITESPACE_RE.sub(" ", normalized).strip()


def _matching_patterns(patterns: Iterable[str], text: str) -> tuple[str, ...]:
    return tuple(pattern for pattern in patterns if re.search(pattern, text, flags=re.IGNORECASE))


def match_post(post: XPost, config: CodexXMonitorConfig) -> MatchResult:
    text = normalize_for_matching(post.text)
    excluded = _matching_patterns(config.negative_patterns, text)
    if excluded:
        return MatchResult(matched=False, excluded_by=excluded)

    contexts = _matching_patterns(config.required_context_patterns, text)
    positives = _matching_patterns(config.positive_patterns, text)
    if not contexts or not positives:
        return MatchResult(matched=False)
    return MatchResult(matched=True, matched_rules=contexts + positives)
