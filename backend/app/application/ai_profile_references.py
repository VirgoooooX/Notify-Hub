from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.plugin_runtime.manifest import PluginManifest


def referenced_ai_profiles(manifest: PluginManifest, config: Mapping[str, Any]) -> set[str]:
    """Resolve a plugin's active Profile dependencies from its declared permissions."""
    allowed = set(manifest.permissions.ai_profiles)
    capabilities = set(manifest.permissions.ai_capabilities)
    if (not allowed and not capabilities) or config.get("decision_mode") == "rules":
        return set()

    configured = _profile_values(config)
    if capabilities and configured:
        return configured
    explicitly_allowed = allowed.intersection(configured or _string_values(config))
    return explicitly_allowed or allowed


def _profile_values(value: Any) -> set[str]:
    if isinstance(value, Mapping):
        result: set[str] = set()
        for key, item in value.items():
            if key in {"ai_profile", "profile"} and isinstance(item, str):
                result.add(item.lower())
            else:
                result.update(_profile_values(item))
        return result
    if isinstance(value, (list, tuple, set)):
        result = set()
        for item in value:
            result.update(_profile_values(item))
        return result
    return set()


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
