from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest
from app.infrastructure.security.tokens import verify_media_signature
from app.media.public_urls import PublicMediaUrlBuilder


def test_public_media_url_builder_supports_static_and_signed_images() -> None:
    builder = PublicMediaUrlBuilder("https://notify.example.com/", "test-signing-key")

    assert builder.static_url("codex_wechat_cover.png") == (
        "https://notify.example.com/codex_wechat_cover.png"
    )
    url = builder.signed_image_url(
        "med_cover",
        lifetime_seconds=3600,
        now=datetime(2026, 7, 16, 12, 0, tzinfo=UTC),
    )

    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    expires = int(query["expires"][0])
    assert parsed.path == "/public/media/med_cover"
    assert verify_media_signature("med_cover", expires, query["sig"][0], "test-signing-key")


@pytest.mark.parametrize("path", ["", "../secret", "images/../secret", "/./image.png"])
def test_public_media_url_builder_rejects_unsafe_static_paths(path: str) -> None:
    with pytest.raises(ValueError):
        PublicMediaUrlBuilder(None, "test-signing-key").static_url(path)
