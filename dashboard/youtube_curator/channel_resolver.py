from __future__ import annotations

import html
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from .config import DEFAULT_CONFIG, write_default_config


CHANNEL_ID_RE = re.compile(r"\bUC[a-zA-Z0-9_-]{22}\b")


@dataclass(slots=True)
class ResolvedChannel:
    source: str
    channel_id: str
    title: str
    url: str


@dataclass(slots=True)
class ConfigAddResult:
    added: list[ResolvedChannel]
    skipped: list[ResolvedChannel]
    updated: list[ResolvedChannel]


class ChannelResolveError(RuntimeError):
    pass


def collect_sources(values: list[str], file_path: str | None, stdin_text: str = "") -> list[str]:
    sources: list[str] = []
    sources.extend(_split_sources("\n".join(values)))

    if file_path:
        sources.extend(_split_sources(Path(file_path).read_text(encoding="utf-8")))

    if stdin_text:
        sources.extend(_split_sources(stdin_text))

    return _dedupe_preserving_order(sources)


def resolve_channels(sources: list[str], *, timeout_seconds: int = 20) -> tuple[list[ResolvedChannel], list[str]]:
    resolved: list[ResolvedChannel] = []
    errors: list[str] = []
    for source in sources:
        try:
            resolved.append(resolve_channel(source, timeout_seconds=timeout_seconds))
        except ChannelResolveError as exc:
            errors.append(f"{source}: {exc}")
    return resolved, errors


def resolve_channel(source: str, *, timeout_seconds: int = 20) -> ResolvedChannel:
    source = source.strip()
    if not source:
        raise ChannelResolveError("empty source")

    direct_id = extract_channel_id(source)
    url = _normalize_source_url(source, direct_id)
    title = ""

    if direct_id:
        title = _fetch_feed_title(direct_id, timeout_seconds=timeout_seconds)
        if not title and url:
            title = _fetch_page_title(url, timeout_seconds=timeout_seconds)
        return ResolvedChannel(source=source, channel_id=direct_id, title=title, url=url)

    if not url:
        raise ChannelResolveError("expected a YouTube URL, handle, or channel ID")

    page = _fetch_text(url, timeout_seconds=timeout_seconds)
    channel_id = extract_channel_id_from_page(page)
    if not channel_id:
        raise ChannelResolveError("could not find a UC... channel ID in the page")

    title = extract_channel_title_from_page(page)
    return ResolvedChannel(source=source, channel_id=channel_id, title=title, url=url)


def extract_channel_id(value: str) -> str:
    parsed = urlparse(value)
    if parsed.query:
        channel_id = parse_qs(parsed.query).get("channel_id", [""])[0]
        if CHANNEL_ID_RE.fullmatch(channel_id):
            return channel_id

    match = CHANNEL_ID_RE.search(value)
    return match.group(0) if match else ""


def extract_channel_id_from_page(page: str) -> str:
    for pattern in (
        r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']https://www\.youtube\.com/channel/(UC[a-zA-Z0-9_-]{22})["\']',
        r'<meta[^>]+property=["\']og:url["\'][^>]+content=["\']https://www\.youtube\.com/channel/(UC[a-zA-Z0-9_-]{22})["\']',
        r'"channelId"\s*:\s*"(UC[a-zA-Z0-9_-]{22})"',
        r'"externalId"\s*:\s*"(UC[a-zA-Z0-9_-]{22})"',
    ):
        match = re.search(pattern, page)
        if match:
            return match.group(1)

    return extract_channel_id(page)


def extract_channel_title_from_page(page: str) -> str:
    patterns = (
        r'"channelMetadataRenderer"\s*:\s*\{\s*"title"\s*:\s*"((?:\\.|[^"\\])*)"',
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']title["\'][^>]+content=["\']([^"\']+)["\']',
        r"<title>(.*?)</title>",
    )
    for pattern in patterns:
        match = re.search(pattern, page, flags=re.DOTALL)
        if not match:
            continue
        title = _decode_title(match.group(1))
        if title:
            return title.removesuffix(" - YouTube").strip()
    return ""


def add_channels_to_config(
    config_path: str | Path,
    channels: list[ResolvedChannel],
    *,
    dry_run: bool = False,
    update_titles: bool = True,
) -> ConfigAddResult:
    path = Path(config_path)
    if not path.exists() and not dry_run:
        write_default_config(path)

    raw = _load_raw_config(path) if path.exists() else deepcopy(DEFAULT_CONFIG)
    raw_channels = raw.setdefault("channels", [])
    existing_by_id = {
        str(channel.get("id", "")).strip(): channel
        for channel in raw_channels
        if isinstance(channel, dict)
    }

    added: list[ResolvedChannel] = []
    skipped: list[ResolvedChannel] = []
    updated: list[ResolvedChannel] = []

    for channel in channels:
        existing = existing_by_id.get(channel.channel_id)
        if existing is not None:
            if update_titles and channel.title and not str(existing.get("title", "")).strip():
                existing["title"] = channel.title
                updated.append(channel)
            else:
                skipped.append(channel)
            continue

        raw_channels.append(
            {
                "id": channel.channel_id,
                "title": channel.title,
                "weight": 1.0,
                "topics": [],
                "enabled": True,
            }
        )
        existing_by_id[channel.channel_id] = raw_channels[-1]
        added.append(channel)

    if not dry_run and (added or updated):
        _write_raw_config(path, raw)

    return ConfigAddResult(added=added, skipped=skipped, updated=updated)


def _split_sources(text: str) -> list[str]:
    sources: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        sources.extend(part.strip().strip(",") for part in line.split() if part.strip().strip(","))
    return sources


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _normalize_source_url(source: str, channel_id: str = "") -> str:
    if source.startswith("@"):
        return f"https://www.youtube.com/{source}"
    if source.startswith("www."):
        return f"https://{source}"
    if source.startswith("youtube.com/"):
        return f"https://www.{source}"
    if source.startswith("http://") or source.startswith("https://"):
        return source
    if channel_id:
        return f"https://www.youtube.com/channel/{channel_id}"
    return ""


def _fetch_page_title(url: str, *, timeout_seconds: int) -> str:
    try:
        return extract_channel_title_from_page(_fetch_text(url, timeout_seconds=timeout_seconds))
    except ChannelResolveError:
        return ""


def _fetch_feed_title(channel_id: str, *, timeout_seconds: int) -> str:
    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    try:
        xml_text = _fetch_text(feed_url, timeout_seconds=timeout_seconds)
        root = ElementTree.fromstring(xml_text)
    except (ChannelResolveError, ElementTree.ParseError):
        return ""
    title = root.findtext("{http://www.w3.org/2005/Atom}title") or ""
    return title.strip()


def _fetch_text(url: str, *, timeout_seconds: int) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": "personal-dashboard-youtube-curator/0.1",
            "Accept": "text/html, application/xml;q=0.9, */*;q=0.8",
        },
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except (OSError, URLError) as exc:
        raise ChannelResolveError(f"failed to fetch {url}: {exc}") from exc


def _decode_title(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    try:
        value = json.loads(f'"{value}"')
    except json.JSONDecodeError:
        pass
    return html.unescape(value).strip()


def _load_raw_config(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_raw_config(path: Path, raw: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(raw, handle, indent=2)
        handle.write("\n")
