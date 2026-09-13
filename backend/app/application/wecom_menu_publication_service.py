from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.application.wecom_menu_service import build_wecom_menu_payload
from app.channels.base import ChannelResult
from app.channels.wecom.client import WeComClient
from app.profiles.registry import ProfileRegistry
from app.profiles.routing import ProfileRoutingService


@dataclass(frozen=True, slots=True)
class MenuPublication:
    profile_id: str
    agent_id: int | None
    payload: dict[str, Any]
    result: ChannelResult


class WeComMenuPublicationService:
    """Publish a profile's menu after resolving its isolated runtime."""

    def __init__(self, registry: ProfileRegistry, routing: ProfileRoutingService) -> None:
        self._registry = registry
        self._routing = routing

    async def publish(
        self,
        profile_ref: str | None = None,
        *,
        client_override: Any = None,
    ) -> MenuPublication:
        profile_id = await self._routing.resolve(profile_ref, capability="menu_enabled")
        payload = build_wecom_menu_payload()
        if client_override is None:
            runtime = await self._registry.runtime(profile_id)
            client: WeComClient = runtime.client
            agent_id = runtime.credentials.agent_id
        else:
            # Explicit test/integration injection only; normal production calls
            # always obtain the client from ProfileRegistry.
            client = client_override
            agent_id = None
        result = await client.create_menu(payload)
        return MenuPublication(profile_id, agent_id, payload, result)
