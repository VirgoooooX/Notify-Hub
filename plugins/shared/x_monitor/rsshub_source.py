from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .models import XPost


class RssHubTimelineSource:
    """Thin plugin adapter over the platform-owned ``context.x`` capability."""

    async def fetch(
        self,
        context: Any,
        username: str,
        fetch_limit: int,
        include_replies: bool,
    ) -> list[XPost]:
        client = getattr(context, "x", None)
        if client is None:
            raise RuntimeError("X source capability is not available")
        rows = await client.timeline(
            username,
            limit=fetch_limit,
            include_replies=include_replies,
        )
        posts: list[XPost] = []
        for row in rows:
            if isinstance(row, Mapping):
                post = XPost.model_validate(row)
            else:
                post = XPost.model_validate(row, from_attributes=True)
            if post.author_username.casefold() == username.casefold():
                posts.append(post)
        return posts
