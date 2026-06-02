from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime, time, timezone

from .models import AppConfig, ChannelConfig, RankedVideo, Video

TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+#.-]*")

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "for",
    "from",
    "how",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "this",
    "to",
    "with",
    "your",
}

CLICKBAIT_PATTERNS = (
    "you won't believe",
    "will shock you",
    "destroyed",
    "destroys",
    "insane",
    "gone wrong",
    "must watch",
    "they don't want you to know",
)


def rank_videos(
    videos: list[Video],
    config: AppConfig,
    recommendation_date: date,
    *,
    excluded_video_ids: set[str] | None = None,
    allow_outside_lookback_video_ids: set[str] | None = None,
) -> list[RankedVideo]:
    excluded_ids = set(excluded_video_ids or set())
    relaxed_lookback_ids = allow_outside_lookback_video_ids or set()
    channel_map = {channel.id: channel for channel in config.channels if channel.id}
    target_dt = datetime.combine(recommendation_date, time.max, tzinfo=timezone.utc)

    ranked: list[RankedVideo] = []
    for video in videos:
        if not _candidate_allowed(video, config, target_dt, excluded_ids, relaxed_lookback_ids):
            continue
        channel = channel_map.get(video.channel_id, ChannelConfig(id=video.channel_id))
        score, matched_interests = _score_video(video, channel, config, target_dt)
        reason = _reason_for(video, channel, matched_interests, recommendation_date)
        ranked.append(
            RankedVideo(
                video=video,
                score=score,
                reason=reason,
                matched_interests=matched_interests,
            )
        )

    ranked.sort(key=lambda item: (item.score, item.video.published_at), reverse=True)
    return _select_diverse(ranked, config.youtube.max_videos_per_day, config.youtube.max_per_channel)


def _candidate_allowed(
    video: Video,
    config: AppConfig,
    target_dt: datetime,
    excluded_video_ids: set[str],
    allow_outside_lookback_video_ids: set[str],
) -> bool:
    text = _combined_text(video)
    if config.youtube.exclude_watched and video.video_id in excluded_video_ids:
        return False
    if any(term.lower() in text for term in config.profile.blocked_terms):
        return False

    age_days = (target_dt - video.published_at).total_seconds() / 86400
    if (
        age_days > config.youtube.lookback_days
        and video.video_id not in allow_outside_lookback_video_ids
    ):
        return False
    if age_days < -2:
        return False

    if (
        config.youtube.min_duration_seconds is not None
        and video.duration_seconds is not None
        and video.duration_seconds < config.youtube.min_duration_seconds
    ):
        return False
    if (
        config.youtube.max_duration_seconds is not None
        and video.duration_seconds is not None
        and video.duration_seconds > config.youtube.max_duration_seconds
    ):
        return False
    return True


def _score_video(
    video: Video,
    channel: ChannelConfig,
    config: AppConfig,
    target_dt: datetime,
) -> tuple[float, list[str]]:
    text = _combined_text(video)
    tokens = set(_tokens(text))

    matched_interests: list[str] = []
    interest_score = 0.0
    for interest in config.profile.interests:
        match = _interest_match(interest, text, tokens)
        if match > 0:
            matched_interests.append(interest)
            interest_score += match

    preferred_score = sum(1.0 for term in config.profile.preferred_terms if term.lower() in text)
    topic_score = sum(1.0 for topic in channel.topics if topic.lower() in text)
    freshness_score = _freshness_score(video, config.youtube.lookback_days, target_dt)
    duration_score = _duration_score(video.duration_seconds)
    clickbait_penalty = _clickbait_penalty(text)
    view_score = _view_score(video.view_count)

    score = (
        float(channel.weight) * 1.5
        + interest_score * 3.0
        + preferred_score * 0.7
        + topic_score * 0.5
        + freshness_score
        + duration_score
        + view_score
        - clickbait_penalty
    )
    return score, matched_interests[:3]


def _select_diverse(
    ranked: list[RankedVideo], max_items: int, max_per_channel: int
) -> list[RankedVideo]:
    selected: list[RankedVideo] = []
    selected_ids: set[str] = set()
    per_channel: Counter[str] = Counter()

    for item in ranked:
        if len(selected) >= max_items:
            return selected
        if max_per_channel > 0 and per_channel[item.video.channel_id] >= max_per_channel:
            continue
        selected.append(item)
        selected_ids.add(item.video.video_id)
        per_channel[item.video.channel_id] += 1

    for item in ranked:
        if len(selected) >= max_items:
            break
        if item.video.video_id in selected_ids:
            continue
        selected.append(item)
        selected_ids.add(item.video.video_id)
    return selected


def _combined_text(video: Video) -> str:
    return f"{video.title}\n{video.description}\n{video.channel_title}".lower()


def _tokens(text: str) -> list[str]:
    return [token for token in TOKEN_RE.findall(text.lower()) if token not in STOPWORDS]


def _interest_match(interest: str, text: str, tokens: set[str]) -> float:
    interest_text = interest.lower().strip()
    if not interest_text:
        return 0.0
    if interest_text in text:
        return 1.5

    interest_tokens = [token for token in _tokens(interest_text) if token not in STOPWORDS]
    if not interest_tokens:
        return 0.0
    hits = sum(1 for token in interest_tokens if token in tokens)
    if hits == 0:
        return 0.0
    return hits / len(interest_tokens)


def _freshness_score(video: Video, lookback_days: int, target_dt: datetime) -> float:
    if lookback_days <= 0:
        return 0.0
    age_days = max(0.0, (target_dt - video.published_at).total_seconds() / 86400)
    return max(0.0, (lookback_days - age_days) / lookback_days) * 2.0


def _duration_score(duration_seconds: int | None) -> float:
    if duration_seconds is None:
        return 0.2
    if 8 * 60 <= duration_seconds <= 45 * 60:
        return 1.2
    if 5 * 60 <= duration_seconds <= 60 * 60:
        return 0.7
    return 0.0


def _clickbait_penalty(text: str) -> float:
    return sum(1.0 for pattern in CLICKBAIT_PATTERNS if pattern in text)


def _view_score(view_count: int | None) -> float:
    if view_count is None:
        return 0.0
    if view_count >= 500_000:
        return 0.8
    if view_count >= 100_000:
        return 0.5
    if view_count >= 10_000:
        return 0.2
    return 0.0


def _reason_for(
    video: Video,
    channel: ChannelConfig,
    matched_interests: list[str],
    recommendation_date: date,
) -> str:
    parts: list[str] = []
    if matched_interests:
        parts.append("matches " + ", ".join(matched_interests[:2]))
    if channel.weight > 1.0:
        parts.append("from a prioritized channel")
    published_date = video.published_at.date()
    if published_date == recommendation_date:
        parts.append("published today")
    elif (recommendation_date - published_date).days == 1:
        parts.append("published yesterday")
    if video.duration_seconds:
        minutes = max(1, round(video.duration_seconds / 60))
        parts.append(f"about {minutes} min")
    if not parts:
        parts.append("fits the current watchlist filters")
    return "; ".join(parts).capitalize() + "."
