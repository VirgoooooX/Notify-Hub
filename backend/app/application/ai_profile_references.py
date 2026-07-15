from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.plugin_runtime.manifest import PluginManifest


def referenced_ai_profiles(manifest: PluginManifest, config: Mapping[str, Any]) -> set[str]:
    """Resolve a plugin's active Profile dependencies from its declared permissions."""
    allowed = set(manifest.permissions.ai_profiles)
    if not allowed or config.get("decision_mode") == "rules":
        return set()

    configured = allowed.intersection(_string_values(config))
    return configured or allowed


def _string_values(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value.lower()}
    if isinstance(value, Mapping):
        result: set[str] = set()
        for item in value.values():
            result.update(_string_values(item))
        return result
    if isinstance(value, (list, tuple, set)):
        result = set()
        for item in value:
            result.update(_string_values(item))
        return result
    return set()
