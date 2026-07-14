from __future__ import annotations

from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from plugins.shared.x_monitor.models import XPost
from plugins.shared.x_monitor.media import select_cover_image
from plugins.shared.x_monitor.twscrape_source import TwscrapeTimelineSource


def test_select_cover_image() -> None:
    # 1. Post with photo
    p1 = XPost(
        id="123",
        author_username="test",
        text="text",
        url="https://x.com/test/status/123",
        published_at=datetime.now(UTC),
        photo_urls=["https://pbs.twimg.com/media/1.jpg"],
    )
    assert select_cover_image(p1) == "https://pbs.twimg.com/media/1.jpg"

    # 2. Post with video thumbnail
    p2 = XPost(
        id="123",
        author_username="test",
        text="text",
        url="https://x.com/test/status/123",
        published_at=datetime.now(UTC),
        video_thumbnail_urls=["https://pbs.twimg.com/video/1.jpg"],
    )
    assert select_cover_image(p2) == "https://pbs.twimg.com/video/1.jpg"

    # 3. Post with GIF thumbnail
    p3 = XPost(
        id="123",
        author_username="test",
        text="text",
        url="https://x.com/test/status/123",
        published_at=datetime.now(UTC),
        animated_thumbnail_urls=["https://pbs.twimg.com/gif/1.jpg"],
    )
    assert select_cover_image(p3) == "https://pbs.twimg.com/gif/1.jpg"

    # 4. Post with quoted photo
    p4 = XPost(
        id="123",
        author_username="test",
        text="text",
        url="https://x.com/test/status/123",
        published_at=datetime.now(UTC),
        quoted_photo_urls=["https://pbs.twimg.com/quoted/1.jpg"],
    )
    assert select_cover_image(p4) == "https://pbs.twimg.com/quoted/1.jpg"

    # 5. Empty
    p5 = XPost(
        id="123",
        author_username="test",
        text="text",
        url="https://x.com/test/status/123",
        published_at=datetime.now(UTC),
    )
    assert select_cover_image(p5) is None


@pytest.mark.asyncio
async def test_twscrape_timeline_source_fetches_and_parses() -> None:
    source = TwscrapeTimelineSource()

    mock_context = MagicMock()
    mock_context.get_secret = AsyncMock(return_value="auth_token=123;ct0=456;")

    mock_user = MagicMock()
    mock_user.id = 999

    # Mock twscrape API
    mock_api_instance = MagicMock()
    mock_api_instance.pool.add_account_cookies = AsyncMock()
    mock_api_instance.user_by_login = AsyncMock(return_value=mock_user)
    mock_api_instance.user_tweets = MagicMock()

    # Mock tweet items
    mock_tweet_1 = MagicMock()
    mock_tweet_1.id_str = "1001"
    mock_tweet_1.user.username = "testuser"
    mock_tweet_1.user.displayname = "Test User"
    mock_tweet_1.rawContent = "First tweet #HWG"
    mock_tweet_1.url = "https://x.com/testuser/status/1001"
    mock_tweet_1.date = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    mock_tweet_1.retweetedTweet = None
    mock_tweet_1.inReplyToTweetId = None
    
    mock_photo = MagicMock()
    mock_photo.url = "https://pbs.twimg.com/media/photo.jpg"
    mock_tweet_1.media.photos = [mock_photo]
    mock_tweet_1.media.videos = []
    mock_tweet_1.media.animated = []
    mock_tweet_1.quotedTweet = None

    mock_iterator = MagicMock()
    
    with patch("plugins.shared.x_monitor.twscrape_source.API", return_value=mock_api_instance), \
         patch("plugins.shared.x_monitor.twscrape_source.gather", AsyncMock(return_value=[mock_tweet_1])):
         
         posts = await source.fetch(mock_context, "testuser", 10, False)
         
         assert len(posts) == 1
         p = posts[0]
         assert p.id == "1001"
         assert p.author_username == "testuser"
         assert p.text == "First tweet #HWG"
         assert str(p.photo_urls[0]) == "https://pbs.twimg.com/media/photo.jpg"
         assert p.is_repost is False
         assert p.is_reply is False
