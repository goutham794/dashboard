from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import quote, urlencode
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

YOUTUBE_SEARCH_URL = "https://www.youtube.com/results"
VIDEO_RENDERER_KEYS = ("videoRenderer", "gridVideoRenderer", "compactVideoRenderer")
RELATIVE_TIME_RE = re.compile(
    r"(?P<count>\d+)\s+(?P<unit>second|minute|hour|day|week|month|year)s?\s+ago",
    flags=re.IGNORECASE,
)
COUNT_RE = re.compile(r"(?P<number>\d+(?:[.,]\d+)?)\s*(?P<suffix>[kmb])?", flags=re.IGNORECASE)


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


def fetch_interest_discovery_videos(
    interests: Iterable[str],
    *,
    max_results: int,
    results_per_interest: int = 10,
    exclude_channel_ids: Iterable[str] = (),
    exclude_video_ids: Iterable[str] = (),
    timeout_seconds: int = 20,
) -> tuple[list[Video], list[str]]:
    queries = _unique_nonempty(interests)
    if max_results <= 0 or results_per_interest <= 0 or not queries:
        return [], []

    excluded_channels = set(_unique_nonempty(exclude_channel_ids))
    seen_video_ids = set(_unique_nonempty(exclude_video_ids))
    videos: list[Video] = []
    errors: list[str] = []

    for query in queries:
        if len(videos) >= max_results:
            break

        request = Request(
            _youtube_search_url(query),
            headers={
                "User-Agent": "personal-dashboard-youtube-curator/0.1",
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            },
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                html_text = response.read().decode("utf-8")
        except OSError as exc:
            errors.append(f"{query}: failed to fetch YouTube search results: {exc}")
            continue

        accepted_for_query = 0
        for video in parse_youtube_search_results(html_text):
            if len(videos) >= max_results or accepted_for_query >= results_per_interest:
                break
            if video.video_id in seen_video_ids:
                continue
            if video.channel_id in excluded_channels:
                continue
            videos.append(video)
            seen_video_ids.add(video.video_id)
            accepted_for_query += 1

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


def parse_youtube_search_results(
    html_text: str, *, fetched_at: datetime | None = None
) -> list[Video]:
    loaded = _load_yt_initial_data(html_text)
    if loaded is None:
        return []

    fetched_dt = (fetched_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    videos: list[Video] = []
    for renderer in _iter_video_renderers(loaded):
        video = _video_from_search_renderer(renderer, fetched_dt)
        if video is not None:
            videos.append(video)
    return _dedupe_videos(videos)


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


def _youtube_search_url(query: str) -> str:
    return f"{YOUTUBE_SEARCH_URL}?{urlencode({'search_query': query})}"


def _load_yt_initial_data(html_text: str) -> Any:
    search_start = 0
    while True:
        marker_start = html_text.find("ytInitialData", search_start)
        if marker_start == -1:
            return None

        brace_start = html_text.find("{", marker_start)
        if brace_start == -1:
            return None

        raw_json = _extract_balanced_json(html_text, brace_start)
        if raw_json:
            try:
                return json.loads(raw_json)
            except json.JSONDecodeError:
                pass
        search_start = marker_start + len("ytInitialData")


def _extract_balanced_json(text: str, start: int) -> str:
    depth = 0
    in_string = False
    escaped = False

    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return ""


def _iter_video_renderers(node: Any) -> Iterable[dict[str, Any]]:
    if isinstance(node, dict):
        for key in VIDEO_RENDERER_KEYS:
            value = node.get(key)
            if isinstance(value, dict):
                yield value
        for value in node.values():
            yield from _iter_video_renderers(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_video_renderers(value)


def _video_from_search_renderer(
    renderer: dict[str, Any], fetched_at: datetime
) -> Video | None:
    video_id = str(renderer.get("videoId", "")).strip()
    title = _text_value(renderer.get("title"))
    if not video_id or not title:
        return None

    channel_title, channel_id = _search_channel(renderer)
    channel_id = channel_id or _fallback_search_channel_id(channel_title)
    thumbnail_url = _search_thumbnail_url(renderer, video_id)
    published_text = _text_value(renderer.get("publishedTimeText"))

    return Video(
        video_id=video_id,
        channel_id=channel_id,
        channel_title=channel_title,
        title=title,
        description=_search_description(renderer),
        url=f"https://www.youtube.com/watch?v={video_id}",
        thumbnail_url=thumbnail_url,
        published_at=_parse_relative_published_at(published_text, fetched_at),
        duration_seconds=_parse_duration(_text_value(renderer.get("lengthText"))),
        view_count=_parse_view_count(_text_value(renderer.get("viewCountText"))),
    )


def _search_channel(renderer: dict[str, Any]) -> tuple[str, str]:
    for field in ("ownerText", "shortBylineText", "longBylineText"):
        value = renderer.get(field)
        title = _text_value(value)
        if title:
            return title, _first_browse_id(value)
    return "YouTube Discovery", ""


def _search_description(renderer: dict[str, Any]) -> str:
    snippets = [
        _text_value(renderer.get("descriptionSnippet")),
        _snippet_list_text(renderer.get("detailedMetadataSnippets")),
        _snippet_list_text(renderer.get("metadataSnippets")),
    ]
    return "\n".join(snippet for snippet in snippets if snippet)


def _snippet_list_text(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for item in value:
        if isinstance(item, dict):
            text = _text_value(item.get("snippetText"))
            if text:
                parts.append(text)
    return "\n".join(parts)


def _search_thumbnail_url(renderer: dict[str, Any], video_id: str) -> str:
    thumbnail = renderer.get("thumbnail")
    if isinstance(thumbnail, dict):
        thumbnails = thumbnail.get("thumbnails")
        if isinstance(thumbnails, list):
            for item in reversed(thumbnails):
                if isinstance(item, dict) and item.get("url"):
                    return str(item["url"])
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def _text_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        simple_text = value.get("simpleText")
        if isinstance(simple_text, str):
            return simple_text.strip()
        runs = value.get("runs")
        if isinstance(runs, list):
            return "".join(
                str(run.get("text", "")) for run in runs if isinstance(run, dict)
            ).strip()
    if isinstance(value, list):
        return "".join(_text_value(item) for item in value).strip()
    return ""


def _first_browse_id(value: Any) -> str:
    if isinstance(value, dict):
        endpoint = value.get("browseEndpoint")
        if isinstance(endpoint, dict):
            browse_id = endpoint.get("browseId")
            if isinstance(browse_id, str):
                return browse_id.strip()
        for child in value.values():
            browse_id = _first_browse_id(child)
            if browse_id:
                return browse_id
    elif isinstance(value, list):
        for child in value:
            browse_id = _first_browse_id(child)
            if browse_id:
                return browse_id
    return ""


def _parse_relative_published_at(value: str, fetched_at: datetime) -> datetime:
    match = RELATIVE_TIME_RE.search(value)
    if not match:
        return fetched_at

    count = int(match.group("count"))
    unit = match.group("unit").lower()
    if unit == "second":
        delta = timedelta(seconds=count)
    elif unit == "minute":
        delta = timedelta(minutes=count)
    elif unit == "hour":
        delta = timedelta(hours=count)
    elif unit == "day":
        delta = timedelta(days=count)
    elif unit == "week":
        delta = timedelta(weeks=count)
    elif unit == "month":
        delta = timedelta(days=count * 30)
    else:
        delta = timedelta(days=count * 365)
    return fetched_at - delta


def _parse_duration(value: str) -> int | None:
    parts = value.strip().split(":")
    if not parts or not all(part.isdigit() for part in parts):
        return None
    total = 0
    for part in parts:
        total = total * 60 + int(part)
    return total


def _parse_view_count(value: str) -> int | None:
    normalized = value.lower().replace(",", "").strip()
    if normalized.startswith("no views"):
        return 0
    match = COUNT_RE.search(normalized)
    if not match:
        return None
    number = float(match.group("number"))
    suffix = match.group("suffix").lower() if match.group("suffix") else ""
    multiplier = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(suffix, 1)
    return int(number * multiplier)


def _fallback_search_channel_id(channel_title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", channel_title.lower()).strip("-")
    return f"youtube-search:{slug or 'unknown'}"


def _unique_nonempty(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value).strip()
        if normalized and normalized not in seen:
            result.append(normalized)
            seen.add(normalized)
    return result


def _dedupe_videos(videos: Iterable[Video]) -> list[Video]:
    result: list[Video] = []
    seen: set[str] = set()
    for video in videos:
        if video.video_id in seen:
            continue
        result.append(video)
        seen.add(video.video_id)
    return result
