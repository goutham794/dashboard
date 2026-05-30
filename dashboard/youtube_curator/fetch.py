from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import quote
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from .models import ChannelConfig, Video

ATOM_NS = "http://www.w3.org/2005/Atom"
YT_NS = "http://www.youtube.com/xml/schemas/2015"
MEDIA_NS = "http://search.yahoo.com/mrss/"

NS = {
    "atom": ATOM_NS,
    "yt": YT_NS,
    "media": MEDIA_NS,
}


class FetchError(RuntimeError):
    pass


def fetch_channel_feed(channel: ChannelConfig, *, timeout_seconds: int = 20) -> list[Video]:
    if not channel.id and not channel.feed_url:
        raise FetchError("channel needs either id or feed_url")

    feed_url = channel.feed_url or (
        "https://www.youtube.com/feeds/videos.xml?channel_id=" + quote(channel.id)
    )
    request = Request(
        feed_url,
        headers={
            "User-Agent": "personal-dashboard-youtube-curator/0.1",
            "Accept": "application/atom+xml, application/xml;q=0.9, */*;q=0.8",
        },
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            xml_text = response.read().decode("utf-8")
    except OSError as exc:
        raise FetchError(f"failed to fetch {feed_url}: {exc}") from exc

    return parse_youtube_feed(
        xml_text,
        default_channel_id=channel.id,
        default_channel_title=channel.title,
    )


def fetch_all_channels(channels: Iterable[ChannelConfig]) -> tuple[list[Video], list[str]]:
    videos: list[Video] = []
    errors: list[str] = []
    for channel in channels:
        if not channel.enabled:
            continue
        try:
            videos.extend(fetch_channel_feed(channel))
        except FetchError as exc:
            label = channel.title or channel.id or channel.feed_url
            errors.append(f"{label}: {exc}")
    return videos, errors


def parse_youtube_feed(
    xml_text: str,
    *,
    default_channel_id: str = "",
    default_channel_title: str = "",
) -> list[Video]:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise FetchError(f"invalid YouTube feed XML: {exc}") from exc

    videos: list[Video] = []
    for entry in root.findall("atom:entry", NS):
        video_id = _text(entry, "yt:videoId")
        if not video_id:
            continue

        channel_id = _text(entry, "yt:channelId") or default_channel_id
        channel_title = _text(entry, "atom:author/atom:name") or default_channel_title
        title = _text(entry, "atom:title") or _text(entry, "media:group/media:title")
        description = _text(entry, "media:group/media:description")
        published_at = _parse_datetime(_text(entry, "atom:published"))

        link = entry.find("atom:link[@rel='alternate']", NS)
        url = link.attrib.get("href", "") if link is not None else ""
        if not url:
            url = f"https://www.youtube.com/watch?v={video_id}"

        thumbnail = entry.find("media:group/media:thumbnail", NS)
        thumbnail_url = thumbnail.attrib.get("url", "") if thumbnail is not None else ""
        if not thumbnail_url:
            thumbnail_url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

        stats = entry.find("media:group/media:community/media:statistics", NS)
        view_count = _optional_int(stats.attrib.get("views")) if stats is not None else None

        videos.append(
            Video(
                video_id=video_id,
                channel_id=channel_id,
                channel_title=channel_title,
                title=title,
                description=description,
                url=url,
                thumbnail_url=thumbnail_url,
                published_at=published_at,
                duration_seconds=None,
                view_count=view_count,
            )
        )

    return videos


def _text(node: ElementTree.Element, path: str) -> str:
    value = node.findtext(path, namespaces=NS)
    return value.strip() if value else ""


def _parse_datetime(value: str) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _optional_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None

