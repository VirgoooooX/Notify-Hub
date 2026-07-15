"""Deterministic, source-independent matching rules."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from .schemas import BUILTIN_QUESTION_PATTERNS, CodexXMonitorConfig, XPost

URL_OR_MENTION_RE = re.compile(r"https?://\S+|(?<!\w)@[A-Za-z0-9_]+")
WHITESPACE_RE = re.compile(r"\s+")
QUOTA_SIGNAL_PATTERNS = (
    r"\busage\b",
    r"\b(?:rate|weekly|daily|hourly)\s+limits?\b",
    r"\bquota\b",
    r"\ballowance\b",
    r"\bbanked\s+reset\b",
)
DECLARATIVE_CHANGE_PATTERNS = (
    r"\b(?:has|have|had|was|were|is|are)\s+(?:\w+\s+){0,4}"
    r"(?:reset|refreshed|restored|replenished|increased|removed)\b",
    r"\b(?:we|i|openai)\s+(?:have\s+|just\s+|now\s+|will\s+|are\s+)?"
    r"(?:reset|resetting|restore|restored|refresh|refreshed|grant|granted|add|added)\b",
    r"\b(?:reset|restoration|refresh)\s+(?:is\s+)?"
    r"(?:live|complete|completed|rolling\s+out|propagating)\b",
    r"\b(?:resets?|restores?|refreshes?)\s+(?:\w+\s+){0,3}"
    r"(?:usage|quota|allowance|limits?)\b",
    r"\bback\s+to\s+normal\b",
)


@dataclass(frozen=True)
class MatchResult:
    matched: bool
    confidence: float
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
    excluded = tuple(
        dict.fromkeys(
            (
                *_matching_patterns(BUILTIN_QUESTION_PATTERNS, text),
                *_matching_patterns(config.negative_patterns, text),
            )
        )
    )
    if excluded:
        return MatchResult(matched=False, confidence=0.99, excluded_by=excluded)

    contexts = _matching_patterns(config.required_context_patterns, text)
    if not contexts:
        return MatchResult(matched=False, confidence=0.99)

    positives = _matching_patterns(config.positive_patterns, text)
    if not positives:
        return MatchResult(matched=False, confidence=0.6, matched_rules=contexts)

    quota_signals = _matching_patterns(QUOTA_SIGNAL_PATTERNS, text)
    declarative_changes = _matching_patterns(DECLARATIVE_CHANGE_PATTERNS, text)
    confidence = 0.55
    if quota_signals:
        confidence += 0.15
    if declarative_changes:
        confidence += 0.2
    if len(positives) > 1:
        confidence += 0.1
    return MatchResult(
        matched=True,
        confidence=min(confidence, 0.99),
        matched_rules=contexts + positives,
    )
