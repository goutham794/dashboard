from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class ChannelConfig:
    id: str
    title: str = ""
    weight: float = 1.0
    topics: list[str] = field(default_factory=list)
    feed_url: str = ""
    enabled: bool = True


@dataclass(slots=True)
class YouTubeSettings:
    lookback_days: int = 14
    max_videos_per_day: int = 10
    min_duration_seconds: int | None = 300
    max_duration_seconds: int | None = 5400
    max_per_channel: int = 2
    exclude_previously_recommended: bool = True
    discovery_enabled: bool = True
    discovery_results_per_interest: int = 10


@dataclass(slots=True)
class StorageSettings:
    database_path: str = "data/youtube_curator.sqlite3"


@dataclass(slots=True)
class OutputSettings:
    site_dir: str = "site"
    page_title: str = "Daily Watchlist"


@dataclass(slots=True)
class ProfileConfig:
    interests: list[str] = field(default_factory=list)
    preferred_terms: list[str] = field(default_factory=list)
    blocked_terms: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AppConfig:
    profile: ProfileConfig
    youtube: YouTubeSettings
    storage: StorageSettings
    output: OutputSettings
    channels: list[ChannelConfig]


@dataclass(slots=True)
class Video:
    video_id: str
    channel_id: str
    channel_title: str
    title: str
    description: str
    url: str
    thumbnail_url: str
    published_at: datetime
    duration_seconds: int | None = None
    view_count: int | None = None


@dataclass(slots=True)
class RankedVideo:
    video: Video
    score: float
    reason: str
    matched_interests: list[str] = field(default_factory=list)
