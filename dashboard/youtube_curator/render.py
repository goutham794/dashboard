from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode

from .models import AppConfig, RankedVideo, Video


def render_daily_json(
    config: AppConfig,
    recommendation_date: date,
    recommendations: list[RankedVideo],
) -> Path:
    daily_dir = Path(config.output.site_dir) / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)

    json_path = daily_dir / f"{recommendation_date.isoformat()}.json"
    json_path.write_text(
        json.dumps(_json_payload(config, recommendation_date, recommendations), indent=2),
        encoding="utf-8",
    )
    return json_path


def _json_payload(
    config: AppConfig,
    recommendation_date: date,
    recommendations: list[RankedVideo],
) -> dict[str, object]:
    return {
        "date": recommendation_date.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "title": config.output.page_title,
        "items": [
            {
                "rank": rank,
                "score": round(item.score, 3),
                "reason": item.reason,
                "matched_interests": item.matched_interests,
                "video": _video_json(item.video),
            }
            for rank, item in enumerate(recommendations, start=1)
        ],
    }


def _video_json(video: Video) -> dict[str, object]:
    return {
        "id": video.video_id,
        "channel_id": video.channel_id,
        "channel_title": video.channel_title,
        "title": video.title,
        "description": video.description,
        "url": video.url,
        "watch_url": youtube_watch_url(video),
        "thumbnail_url": video.thumbnail_url,
        "published_at": video.published_at.isoformat(),
        "duration_seconds": video.duration_seconds,
        "view_count": video.view_count,
    }


def youtube_watch_url(video: Video) -> str:
    params = {
        "autoplay": "1",
        "rel": "0",
        "modestbranding": "1",
        "playsinline": "1",
    }
    return (
        "https://www.youtube-nocookie.com/embed/"
        f"{quote(video.video_id, safe='')}?{urlencode(params)}"
    )
