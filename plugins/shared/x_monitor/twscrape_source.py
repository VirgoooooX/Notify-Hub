from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from twscrape import API, gather
from twscrape.accounts_pool import NoAccountError

from .models import XPost


class XMonitorError(Exception):
    pass


class XMonitorRateLimitedError(XMonitorError):
    pass


class TwscrapeTimelineSource:
    async def fetch(
        self,
        context: Any,
        username: str,
        fetch_limit: int,
        include_replies: bool,
    ) -> list[XPost]:
        cookie = await context.get_secret("twscrape_cookie")
        self._validate_cookie(cookie)

        # Disable third-party telemetry for the self-hosted deployment.
        os.environ.setdefault("TWS_TELEMETRY", "0")
        os.environ.setdefault("TWS_LOG_LEVEL", "WARNING")

        try:
            with TemporaryDirectory(prefix="notify-hub-twscrape-") as temp_dir:
                account_db = Path(temp_dir) / "accounts.db"

                api = API(
                    str(account_db),
                    raise_when_no_account=True,
                )

                await api.pool.add_account_cookies(
                    "notify-hub",
                    cookie,
                )

                user = await api.user_by_login(username)
                if user is None:
                    raise XMonitorError(f"X user @{username} was not found")

                if include_replies:
                    iterator = api.user_tweets_and_replies(
                        user.id,
                        limit=fetch_limit,
                    )
                else:
                    iterator = api.user_tweets(
                        user.id,
                        limit=fetch_limit,
                    )

                tweets = await gather(iterator)

        except NoAccountError as exc:
            raise XMonitorRateLimitedError("twscrape rate limited or no account available") from exc
        except XMonitorError:
            raise
        except Exception as exc:
            # Do not persist third-party exception bodies that might contain
            # account details or response data.
            raise XMonitorError(f"twscrape request failed: {type(exc).__name__}") from exc

        posts: list[XPost] = []

        for tweet in tweets:
            if tweet.user.username.casefold() != username.casefold():
                continue

            photos = []
            videos = []
            animated = []
            media = getattr(tweet, "media", None)
            if media:
                if getattr(media, "photos", None):
                    photos = [p.url for p in media.photos if getattr(p, "url", None)]
                if getattr(media, "videos", None):
                    videos = [v.thumbnailUrl for v in media.videos if getattr(v, "thumbnailUrl", None)]
                if getattr(media, "animated", None):
                    animated = [a.thumbnailUrl for a in media.animated if getattr(a, "thumbnailUrl", None)]

            quoted_photos = []
            quoted = getattr(tweet, "quotedTweet", None)
            if quoted:
                quoted_media = getattr(quoted, "media", None)
                if quoted_media and getattr(quoted_media, "photos", None):
                    quoted_photos = [p.url for p in quoted_media.photos if getattr(p, "url", None)]

            posts.append(
                XPost(
                    id=tweet.id_str,
                    author_username=tweet.user.username,
                    author_display_name=tweet.user.displayname,
                    text=tweet.rawContent,
                    url=tweet.url,
                    published_at=tweet.date,
                    is_repost=tweet.retweetedTweet is not None,
                    is_reply=tweet.inReplyToTweetId is not None,
                    photo_urls=photos,
                    video_thumbnail_urls=videos,
                    animated_thumbnail_urls=animated,
                    quoted_photo_urls=quoted_photos,
                )
            )

        return posts

    @staticmethod
    def _validate_cookie(cookie: str) -> None:
        normalized = cookie.casefold()

        if "auth_token=" not in normalized:
            raise XMonitorError("twscrape_cookie does not contain auth_token")

        if "ct0=" not in normalized:
            raise XMonitorError("twscrape_cookie does not contain ct0")
