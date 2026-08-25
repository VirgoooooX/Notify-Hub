from __future__ import annotations

import html
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Protocol
from urllib.parse import quote, urlsplit
from xml.etree.ElementTree import Element

import httpx
from app.config import Settings
from app.domain.x_source import (
    XPostRecord,
    XSourceAccountError,
    XSourceError,
    XSourceParseError,
    XSourceRateLimited,
    XSourceUnavailable,
)
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

HTTP_TIMEOUT_SECONDS = 20.0
POST_ID_RE = re.compile(r"(?:/status/|x-post-|^)(\d+)(?:\D*$|$)")
URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


class XTimelineProvider(Protocol):
    async def fetch(
        self, username: str, *, limit: int, include_replies: bool
    ) -> list[XPostRecord]: ...

    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class XHealthTarget:
    plugin_id: str
    username: str
    fetch_limit: int = 40
    include_replies: bool = False
    silence_enabled: bool = False
    silence_seconds: int = 86_400


class _MediaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.media: list[str] = []
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag.lower() in {"script", "style"}:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        for key in ("src", "poster", "href"):
            value = attributes.get(key)
            if value:
                self.media.append(value)
                break

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth and data.strip():
            self.parts.append(data)


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
        raise XSourceParseError("feed entry is missing a publication time")
    normalized = value.strip()
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(normalized)
        except (TypeError, ValueError) as exc:
            raise XSourceParseError("invalid feed publication time") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _clean_html(value: str) -> tuple[str, list[str]]:
    parser = _MediaParser()
    try:
        parser.feed(html.unescape(value))
        parser.close()
    except Exception as exc:
        raise XSourceParseError("invalid HTML in feed entry") from exc
    text = " ".join(" ".join(parser.parts).split())
    candidates = list(parser.media)
    candidates.extend(URL_RE.findall(html.unescape(value)))
    return text, candidates


def _media_urls(entry: Element) -> list[str]:
    candidates: list[str] = []
    for node in entry.iter():
        for key in ("url", "href"):
            value = node.attrib.get(key)
            if value:
                candidates.append(value)
    for child in entry:
        if _local_name(child.tag) in {"content", "description", "summary", "title"}:
            serialized = ElementTree.tostring(child, encoding="unicode")
            _, inline = _clean_html(serialized)
            candidates.extend(inline)
    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = html.unescape(candidate).strip().rstrip(")],.;")
        if not normalized.startswith(("http://", "https://")):
            continue
        parsed = urlsplit(normalized)
        if parsed.hostname is None:
            continue
        if "pbs.twimg.com" not in parsed.hostname.lower():
            continue
        if normalized not in seen:
            seen.add(normalized)
            unique.append(normalized)
    return unique[:20]


def parse_rsshub_feed(xml_text: str, username: str) -> list[XPostRecord]:
    try:
        root = ElementTree.fromstring(xml_text)
    except (ElementTree.ParseError, DefusedXmlException) as exc:
        raise XSourceParseError("invalid RSS/Atom XML") from exc

    entries = [node for node in root.iter() if _local_name(node.tag) in {"item", "entry"}]
    posts: list[XPostRecord] = []
    seen_ids: set[str] = set()
    for entry in entries:
        guid = _child_text(entry, "guid", "id")
        link = _entry_link(entry)
        post_id = _extract_post_id(guid, link)
        if post_id is None or post_id in seen_ids:
            continue
        seen_ids.add(post_id)
        raw_text = _child_text(entry, "content", "encoded", "description", "summary", "title") or ""
        text, inline_media = _clean_html(raw_text)
        media = _media_urls(entry)
        for candidate in inline_media:
            if candidate not in media and "pbs.twimg.com" in urlsplit(candidate).netloc.lower():
                media.append(candidate)
        published_at = _parse_datetime(
            _child_text(entry, "published", "updated", "pubdate", "date")
        )
        canonical_url = link or f"https://x.com/{quote(username, safe='')}/status/{post_id}"
        lowered = text.casefold()
        posts.append(
            XPostRecord(
                id=post_id,
                author_username=username,
                author_display_name=_child_text(entry, "author", "creator"),
                text=text,
                url=canonical_url,
                published_at=published_at,
                is_repost=lowered.startswith(("rt @", "reposted ")),
                is_reply=lowered.startswith("@"),
                photo_urls=media,
            )
        )
    posts.sort(key=lambda post: (post.published_at, int(post.id)))
    return posts


def _extract_post_id(*candidates: str | None) -> str | None:
    for candidate in candidates:
        if not candidate:
            continue
        match = POST_ID_RE.search(candidate.strip())
        if match:
            return match.group(1)
    return None


class RssHubProvider:
    def __init__(
        self,
        base_url: str,
        *,
        access_key: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._access_key = access_key
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(HTTP_TIMEOUT_SECONDS, connect=5.0),
            follow_redirects=False,
        )
        self._owns_client = client is None

    async def fetch(self, username: str, *, limit: int, include_replies: bool) -> list[XPostRecord]:
        del include_replies  # RSSHub's user route currently returns the canonical timeline.
        route = f"{self._base_url}/twitter/user/{quote(username, safe='')}"
        params = {"key": self._access_key} if self._access_key else None
        try:
            response = await self._client.get(route, params=params)
        except httpx.TimeoutException as exc:
            raise XSourceUnavailable("RSSHub request timed out") from exc
        except httpx.HTTPError as exc:
            raise XSourceUnavailable("RSSHub request failed") from exc
        if response.status_code == 429:
            raise XSourceRateLimited("RSSHub rate limited the request")
        if response.status_code == 404:
            raise XSourceAccountError(f"RSSHub route for @{username} was not found")
        if response.status_code < 200 or response.status_code >= 300:
            if response.status_code >= 500:
                raise XSourceUnavailable(f"RSSHub returned HTTP {response.status_code}")
            raise XSourceAccountError(f"RSSHub returned HTTP {response.status_code}")
        posts = parse_rsshub_feed(response.text, username)
        return posts[-max(1, min(limit, 100)) :]

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class TwscrapeProvider:
    """Cold standby provider. It is never selected unless platform config opts in."""

    def __init__(self, cookie: str | None) -> None:
        self._cookie = cookie

    async def fetch(self, username: str, *, limit: int, include_replies: bool) -> list[XPostRecord]:
        cookie = (self._cookie or "").strip()
        if not cookie:
            raise XSourceUnavailable("twscrape cold standby credentials are not configured")
        cookie_lower = cookie.casefold()
        if "auth_token=" not in cookie_lower or "ct0=" not in cookie_lower:
            raise XSourceUnavailable("twscrape cold standby credentials are invalid")
        from twscrape import API, gather
        from twscrape.accounts_pool import NoAccountError

        os.environ.setdefault("TWS_TELEMETRY", "0")
        os.environ.setdefault("TWS_LOG_LEVEL", "WARNING")
        os.environ.setdefault("TWS_HTTP_BACKEND", "curl")
        try:
            with TemporaryDirectory(prefix="notify-hub-twscrape-") as temp_dir:
                api = API(str(Path(temp_dir) / "accounts.db"), raise_when_no_account=True)
                await api.pool.add_account_cookies("notify-hub", cookie)
                user = await api.user_by_login(username)
                if user is None:
                    raise XSourceAccountError(f"X user @{username} was not found")
                iterator = (
                    api.user_tweets_and_replies(user.id, limit=limit)
                    if include_replies
                    else api.user_tweets(user.id, limit=limit)
                )
                tweets = await gather(iterator)
        except NoAccountError as exc:
            raise XSourceRateLimited("twscrape has no usable account") from exc
        except XSourceError:
            raise
        except Exception as exc:
            raise XSourceUnavailable(f"twscrape request failed: {type(exc).__name__}") from exc

        posts: list[XPostRecord] = []
        for tweet in tweets:
            if tweet.user.username.casefold() != username.casefold():
                continue
            media = getattr(tweet, "media", None)
            photos = [p.url for p in getattr(media, "photos", []) or [] if getattr(p, "url", None)]
            videos = [
                v.thumbnailUrl
                for v in getattr(media, "videos", []) or []
                if getattr(v, "thumbnailUrl", None)
            ]
            animated = [
                a.thumbnailUrl
                for a in getattr(media, "animated", []) or []
                if getattr(a, "thumbnailUrl", None)
            ]
            quoted_photos: list[str] = []
            quoted = getattr(tweet, "quotedTweet", None)
            quoted_media = getattr(quoted, "media", None) if quoted else None
            quoted_photos.extend(
                p.url for p in getattr(quoted_media, "photos", []) or [] if getattr(p, "url", None)
            )
            posts.append(
                XPostRecord(
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

    async def close(self) -> None:
        return None


class XSourceService:
    """Platform-owned X timeline capability shared by plugins and health checks."""

    def __init__(self, settings: Settings, *, client: httpx.AsyncClient | None = None) -> None:
        access_key = (
            settings.rsshub_access_key.get_secret_value()
            if settings.rsshub_access_key is not None
            else None
        )
        self.provider_name = settings.x_source_provider
        self._rsshub = RssHubProvider(
            settings.rsshub_base_url,
            access_key=access_key,
            client=client,
        )
        self._twscrape = TwscrapeProvider(
            settings.x_twscrape_cookie.get_secret_value()
            if settings.x_twscrape_cookie is not None
            else None
        )

    async def fetch_records(
        self, username: str, *, limit: int = 40, include_replies: bool = False
    ) -> list[XPostRecord]:
        provider = self._rsshub if self.provider_name == "rsshub" else self._twscrape
        return await provider.fetch(
            username,
            limit=max(1, min(limit, 100)),
            include_replies=include_replies,
        )

    async def timeline(
        self, username: str, *, limit: int = 40, include_replies: bool = False
    ) -> list[Mapping[str, Any]]:
        return [
            record.model_dump(mode="json")
            for record in await self.fetch_records(
                username, limit=limit, include_replies=include_replies
            )
        ]

    async def close(self) -> None:
        await self._rsshub.close()
        await self._twscrape.close()
