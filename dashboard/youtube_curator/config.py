from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import (
    AppConfig,
    ChannelConfig,
    OutputSettings,
    ProfileConfig,
    StorageSettings,
    YouTubeSettings,
)


DEFAULT_CONFIG: dict[str, Any] = {
    "profile": {
        "interests": [
            "practical AI engineering",
            "backend systems",
            "personal finance",
            "fitness and health",
            "Indian technology ecosystem",
        ],
        "preferred_terms": [
            "python",
            "agents",
            "systems",
            "engineering",
            "health",
        ],
        "blocked_terms": [
            "#shorts",
            "youtube shorts",
            "celebrity drama",
            "reaction video",
            "prank",
            "drama",
        ],
    },
    "youtube": {
        "lookback_days": 14,
        "max_videos_per_day": 10,
        "min_duration_seconds": 300,
        "max_duration_seconds": 5400,
        "max_per_channel": 2,
        "exclude_watched": True,
        "discovery_enabled": True,
        "discovery_results_per_interest": 10,
    },
    "storage": {
        "database_path": "data/youtube_curator.sqlite3",
    },
    "output": {
        "site_dir": "site",
        "page_title": "Daily Watchlist",
    },
    "channels": [],
}


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    return parse_config(raw)


def parse_config(raw: dict[str, Any]) -> AppConfig:
    profile_raw = {**DEFAULT_CONFIG["profile"], **raw.get("profile", {})}
    youtube_raw = {**DEFAULT_CONFIG["youtube"], **raw.get("youtube", {})}
    storage_raw = {**DEFAULT_CONFIG["storage"], **raw.get("storage", {})}
    output_raw = {**DEFAULT_CONFIG["output"], **raw.get("output", {})}

    channels = [
        ChannelConfig(
            id=str(channel.get("id", "")).strip(),
            title=str(channel.get("title", "")).strip(),
            weight=float(channel.get("weight", 1.0)),
            topics=[str(topic).strip() for topic in channel.get("topics", []) if str(topic).strip()],
            feed_url=str(channel.get("feed_url", "")).strip(),
            enabled=bool(channel.get("enabled", True)),
        )
        for channel in raw.get("channels", [])
    ]

    return AppConfig(
        profile=ProfileConfig(
            interests=_list_of_strings(profile_raw.get("interests", [])),
            preferred_terms=_list_of_strings(profile_raw.get("preferred_terms", [])),
            blocked_terms=_list_of_strings(profile_raw.get("blocked_terms", [])),
        ),
        youtube=YouTubeSettings(
            lookback_days=int(youtube_raw.get("lookback_days", 14)),
            max_videos_per_day=int(youtube_raw.get("max_videos_per_day", 10)),
            min_duration_seconds=_optional_int(youtube_raw.get("min_duration_seconds")),
            max_duration_seconds=_optional_int(youtube_raw.get("max_duration_seconds")),
            max_per_channel=int(youtube_raw.get("max_per_channel", 2)),
            exclude_watched=bool(
                youtube_raw.get(
                    "exclude_watched",
                    youtube_raw.get("exclude_previously_recommended", True),
                )
            ),
            discovery_enabled=bool(youtube_raw.get("discovery_enabled", True)),
            discovery_results_per_interest=int(
                youtube_raw.get("discovery_results_per_interest", 10)
            ),
        ),
        storage=StorageSettings(database_path=str(storage_raw.get("database_path"))),
        output=OutputSettings(
            site_dir=str(output_raw.get("site_dir")),
            page_title=str(output_raw.get("page_title")),
        ),
        channels=channels,
    )


def write_default_config(path: str | Path, *, force: bool = False) -> Path:
    config_path = Path(path)
    if config_path.exists() and not force:
        return config_path
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as handle:
        json.dump(DEFAULT_CONFIG, handle, indent=2)
        handle.write("\n")
    return config_path


def config_to_dict(config: AppConfig) -> dict[str, Any]:
    return asdict(config)


def _list_of_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)
