"""RSS/Atom and optional X API adapters producing the standard XPost model."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Protocol
from xml.etree.ElementTree import Element

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from twscrape import API, gather
from twscrape.accounts_pool import NoAccountError

from .schemas import CodexXMonitorConfig, PluginContext, XPost

HTTP_TIMEOUT_SECONDS = 20.0
POST_ID_RE = re.compile(r"(?:/status/|x-post-|^)(\d+)(?:\D*$|$)")
TAG_RE = re.compile(r"<[^>]+>")


class SourceError(RuntimeError):
    pass


class SourceParseError(SourceError):
    pass


class SourceRateLimited(SourceError):
    def __init__(self, reset_at: str | None = None) -> None:
        self.reset_at = reset_at
        super().__init__("source rate limited" + (f" until {reset_at}" if reset_at else ""))


class PostSource(Protocol):
    async def fetch(self, context: PluginContext, config: CodexXMonitorConfig) -> list[XPost]: ...


def extract_post_id(*candidates: str | None) -> str | None:
    for candidate in candidates:
        if not candidate:
            continue
        match = POST_ID_RE.search(candidate.strip())
        if match:
            return match.group(1)
    return None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(node: Element, *names: str) -> str | None:
    wanted = set(names)
    for child in node:
        if _local_name(child.tag) in wanted:
            value = "".join(child.itertext()).strip()
            if value:
                return value
    return None


def _entry_link(node: Element) -> str | None:
    for child in node:
        if _local_name(child.tag) != "link":
            continue
        href = child.attrib.get("href")
        rel = child.attrib.get("rel", "alternate")
        if href and rel == "alternate":
            return href.strip()
        if child.text and child.text.strip():
            return child.text.strip()
    return None


def _parse_datetime(value: str | None) -> datetime:
    if not value:
        raise SourceParseError("feed entry is missing a publication time")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError) as exc:
            raise SourceParseError("invalid feed publication time") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def parse_feed(xml_text: str, username: str) -> list[XPost]:
    try:
        root = ElementTree.fromstring(xml_text)
    except (ElementTree.ParseError, DefusedXmlException) as exc:
        raise SourceParseError("invalid RSS/Atom XML") from exc

    entries = [node for node in root.iter() if _local_name(node.tag) in {"item", "entry"}]
    posts: list[XPost] = []
    seen_ids: set[str] = set()
    for entry in entries:
        guid = _child_text(entry, "guid", "id")
        link = _entry_link(entry)
        post_id = extract_post_id(guid, link)
        if post_id is None or post_id in seen_ids:
            continue
        seen_ids.add(post_id)
        text = _child_text(entry, "content", "description", "summary", "title") or ""
        text = TAG_RE.sub(" ", text).strip()
        published_at = _parse_datetime(_child_text(entry, "published", "updated", "pubdate"))
        canonical_url = link or f"https://x.com/{username}/status/{post_id}"
        lowered = text.casefold()
        posts.append(
            XPost(
                id=post_id,
                author_username=username,
                author_display_name=_child_text(entry, "author", "creator"),
                text=text,
                url=canonical_url,
                published_at=published_at,
                is_repost=lowered.startswith(("rt @", "reposted ")),
                is_reply=lowered.startswith("@"),
            )
        )
    return posts


class RssAtomSource:
    async def fetch(self, context: PluginContext, config: CodexXMonitorConfig) -> list[XPost]:
        if config.feed_url is None:
            raise SourceError("RSS source requires feed_url")
        response = await context.http.get(str(config.feed_url), timeout=HTTP_TIMEOUT_SECONDS)
        if response.status_code == 429:
            raise SourceRateLimited(response.headers.get("retry-after"))
        if response.status_code < 200 or response.status_code >= 300:
            raise SourceError(f"RSS source returned HTTP {response.status_code}")
        return parse_feed(response.text, config.username)


def _json_mapping(response: Any) -> Mapping[str, Any]:
    payload = response.json()
    if not isinstance(payload, Mapping):
        raise SourceParseError("X API returned a non-object response")
    return payload


class XApiSource:
    """Optional X API v2 adapter. The bearer token is obtained only via PluginContext."""

    async def fetch(self, context: PluginContext, config: CodexXMonitorConfig) -> list[XPost]:
        token = await context.get_secret("x_api_bearer_token")
        headers = {"Authorization": f"Bearer {token}"}
        user_response = await context.http.get(
            f"https://api.x.com/2/users/by/username/{config.username}",
            headers=headers,
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        self._raise_for_status(user_response)
        user_payload = _json_mapping(user_response)
        user = user_payload.get("data")
        if not isinstance(user, Mapping) or not user.get("id"):
            raise SourceParseError("X API user response is missing data.id")

        exclude = "" if config.include_reposts or config.include_replies else "retweets,replies"
        posts_response = await context.http.get(
            f"https://api.x.com/2/users/{user['id']}/tweets",
            params={
                "max_results": "100",
                "tweet.fields": "created_at,author_id,referenced_tweets",
                "exclude": exclude,
            },
            headers=headers,
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        self._raise_for_status(posts_response)
        payload = _json_mapping(posts_response)
        rows = payload.get("data", [])
        if not isinstance(rows, list):
            raise SourceParseError("X API posts response has invalid data")

        posts: list[XPost] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            references = row.get("referenced_tweets") or []
            reference_types = {
                item.get("type")
                for item in references
                if isinstance(item, Mapping) and item.get("type")
            }
            posts.append(
                XPost(
                    id=str(row.get("id", "")),
                    author_username=config.username,
                    text=str(row.get("text", "")),
                    url=f"https://x.com/{config.username}/status/{row.get('id', '')}",
                    published_at=_parse_datetime(str(row.get("created_at", ""))),
                    is_repost="retweeted" in reference_types,
                    is_reply="replied_to" in reference_types,
                )
            )
        return posts

    @staticmethod
    def _raise_for_status(response: Any) -> None:
        if response.status_code == 429:
            raise SourceRateLimited(response.headers.get("x-rate-limit-reset"))
        if response.status_code < 200 or response.status_code >= 300:
            raise SourceError(f"X API returned HTTP {response.status_code}")


class TwscrapeSource:
    """Use a temporary twscrape account pool seeded from an encrypted plugin secret."""

    async def fetch(
        self,
        context: PluginContext,
        config: CodexXMonitorConfig,
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

                user = await api.user_by_login(config.username)
                if user is None:
                    raise SourceParseError(f"X user @{config.username} was not found")

                if config.include_replies:
                    iterator = api.user_tweets_and_replies(
                        user.id,
                        limit=config.twscrape_fetch_limit,
                    )
                else:
                    iterator = api.user_tweets(
                        user.id,
                        limit=config.twscrape_fetch_limit,
                    )

                tweets = await gather(iterator)

        except NoAccountError as exc:
            raise SourceRateLimited() from exc
        except (SourceError, SourceParseError):
            raise
        except Exception as exc:
            # Do not persist third-party exception bodies that might contain
            # account details or response data.
            raise SourceError(f"twscrape request failed: {type(exc).__name__}") from exc

        posts: list[XPost] = []

        for tweet in tweets:
            # Defensive filter in case the timeline contains an injected item.
            if tweet.user.username.casefold() != config.username.casefold():
                continue

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
                )
            )

        return posts

    @staticmethod
    def _validate_cookie(cookie: str) -> None:
        normalized = cookie.casefold()

        if "auth_token=" not in normalized:
            raise SourceError("twscrape_cookie does not contain auth_token")

        if "ct0=" not in normalized:
            raise SourceError("twscrape_cookie does not contain ct0")
