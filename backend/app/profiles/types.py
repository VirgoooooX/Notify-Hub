from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CAPABILITY_NAMES = (
    "outbound_enabled",
    "callback_enabled",
    "menu_enabled",
    "conversation_enabled",
    "interactive_enabled",
    "mobile_enabled",
    "broadcast_enabled",
)

DEFAULT_CAPABILITIES: dict[str, bool] = {
    "outbound_enabled": True,
    "callback_enabled": True,
    "menu_enabled": True,
    "conversation_enabled": True,
    "interactive_enabled": True,
    "mobile_enabled": True,
    "broadcast_enabled": True,
}


@dataclass(frozen=True, slots=True)
class ProfileCapabilities:
    outbound_enabled: bool = True
    callback_enabled: bool = True
    menu_enabled: bool = True
    conversation_enabled: bool = True
    interactive_enabled: bool = True
    mobile_enabled: bool = True
    broadcast_enabled: bool = True

    @classmethod
    def from_mapping(cls, value: object) -> ProfileCapabilities:
        mapping = value if isinstance(value, dict) else {}
        values = {
            name: bool(mapping.get(name, default)) for name, default in DEFAULT_CAPABILITIES.items()
        }
        return cls(**values)

    def as_dict(self) -> dict[str, bool]:
        return {name: bool(getattr(self, name)) for name in CAPABILITY_NAMES}

    def allows(self, capability: str) -> bool:
        if capability not in CAPABILITY_NAMES:
            raise ValueError(f"unknown profile capability: {capability}")
        return bool(getattr(self, capability))


@dataclass(frozen=True, slots=True)
class ProfileMetadata:
    id: str
    key: str
    name: str
    enabled: bool
    is_default: bool
    capabilities: ProfileCapabilities
    created_at: Any = None
    updated_at: Any = None
