from __future__ import annotations

from .models import XPost


def select_cover_image(post: XPost) -> str | None:
    if post.photo_urls:
        return str(post.photo_urls[0])
    if post.video_thumbnail_urls:
        return str(post.video_thumbnail_urls[0])
    if post.animated_thumbnail_urls:
        return str(post.animated_thumbnail_urls[0])
    if post.quoted_photo_urls:
        return str(post.quoted_photo_urls[0])
    return None
