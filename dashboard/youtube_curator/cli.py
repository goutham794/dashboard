from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .channel_resolver import add_channels_to_config, collect_sources, resolve_channels
from .config import DEFAULT_CONFIG, load_config, parse_config, write_default_config
from .db import Database
from .fetch import fetch_all_channels, fetch_interest_discovery_videos
from .models import AppConfig, RankedVideo, Video
from .rank import rank_videos
from .render import render_daily_json


DEFAULT_CONFIG_PATH = Path("config/youtube.json")
WATCHED_FEEDBACK_ACTIONS = {"seen", "watched"}


@dataclass(slots=True)
class GenerationResult:
    recommendation_date: date
    recommendations: list[RankedVideo]
    json_path: Path
    messages: list[str] = field(default_factory=list)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="yt-curator",
        description="Build a finite daily YouTube watchlist from configured channels.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create a starter config file.")
    init_parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    init_parser.add_argument("--force", action="store_true")

    run_parser = subparsers.add_parser("run", help="Fetch feeds, rank videos, and write daily JSON.")
    run_parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    run_parser.add_argument("--date", default=None, help="Recommendation date in YYYY-MM-DD format.")
    run_parser.add_argument("--skip-fetch", action="store_true", help="Rank existing DB videos only.")

    demo_parser = subparsers.add_parser(
        "demo", help="Generate daily JSON from built-in sample videos without network access."
    )
    demo_parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    demo_parser.add_argument("--date", default=None, help="Recommendation date in YYYY-MM-DD format.")

    tui_parser = subparsers.add_parser(
        "tui",
        help="Open a terminal UI for the generated daily watchlist.",
    )
    tui_parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    tui_parser.add_argument("--date", default=None, help="Recommendation date in YYYY-MM-DD format.")

    add_channels_parser = subparsers.add_parser(
        "add-channels",
        help="Resolve YouTube URLs/handles to channel IDs and add them to config.",
    )
    add_channels_parser.add_argument("sources", nargs="*", help="YouTube URLs, @handles, or UC... IDs.")
    add_channels_parser.add_argument("--file", help="Text file containing one URL/handle/ID per line.")
    add_channels_parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    add_channels_parser.add_argument(
        "--print-only",
        action="store_true",
        help="Print resolved channels without writing to config.",
    )
    add_channels_parser.add_argument(
        "--no-update-titles",
        action="store_true",
        help="Do not fill missing titles for channels already present in config.",
    )

    args = parser.parse_args(argv)

    if args.command == "init":
        path = write_default_config(args.config, force=args.force)
        print(f"Config ready: {path}")
        return

    if args.command == "run":
        config = _load_or_create_config(Path(args.config))
        target_date = _parse_date(args.date)
        _run(config, target_date, skip_fetch=args.skip_fetch)
        return

    if args.command == "demo":
        config = _load_config_or_default(Path(args.config))
        target_date = _parse_date(args.date)
        _run_demo(config, target_date)
        return

    if args.command == "tui":
        from .tui import run_tui

        config = _load_config_or_default(Path(args.config))
        target_date = _parse_date(args.date)
        run_tui(config, target_date)
        return

    if args.command == "add-channels":
        stdin_text = "" if sys.stdin.isatty() else sys.stdin.read()
        sources = collect_sources(args.sources, args.file, stdin_text)
        _add_channels(
            sources,
            Path(args.config),
            print_only=args.print_only,
            update_titles=not args.no_update_titles,
        )
        return


def _run(config: AppConfig, target_date: date, *, skip_fetch: bool) -> None:
    result = generate_daily_feed(config, target_date, skip_fetch=skip_fetch)
    for message in result.messages:
        print(message)
    print(f"Selected {len(result.recommendations)} videos for {target_date.isoformat()}.")
    print(f"JSON: {result.json_path}")


def generate_daily_feed(
    config: AppConfig,
    target_date: date,
    *,
    skip_fetch: bool = False,
) -> GenerationResult:
    enabled_channels = [channel for channel in config.channels if channel.enabled]
    if not enabled_channels:
        raise SystemExit(
            "No YouTube channels configured. Edit config/youtube.json or run `python -m dashboard.youtube_curator demo`."
        )

    database = Database(config.storage.database_path)
    database.init()
    messages: list[str] = []

    if not skip_fetch:
        videos, errors = fetch_all_channels(enabled_channels)
        inserted = database.upsert_videos(videos)
        messages.append(f"Fetched {len(videos)} videos and upserted {inserted} rows.")
        for error in errors:
            messages.append(f"Fetch warning: {error}")

    recommendations = _recommend_from_db(database, config, target_date)
    if not skip_fetch:
        recommendations = _top_up_with_discovery(
            database,
            config,
            target_date,
            recommendations,
            messages=messages,
        )
    json_path = render_daily_json(config, target_date, recommendations)
    database.replace_recommendations(target_date, recommendations)

    return GenerationResult(
        recommendation_date=target_date,
        recommendations=recommendations,
        json_path=json_path,
        messages=messages,
    )


def _run_demo(config: AppConfig, target_date: date) -> None:
    database = Database(config.storage.database_path)
    database.init()
    videos = _demo_videos(target_date)
    inserted = database.upsert_videos(videos)
    recommendations = _recommend_from_db(database, config, target_date)
    json_path = render_daily_json(config, target_date, recommendations)
    database.replace_recommendations(target_date, recommendations)
    print(f"Loaded {inserted} demo videos.")
    print(f"Selected {len(recommendations)} videos for {target_date.isoformat()}.")
    print(f"JSON: {json_path}")


def _add_channels(
    sources: list[str],
    config_path: Path,
    *,
    print_only: bool,
    update_titles: bool,
) -> None:
    if not sources:
        raise SystemExit(
            "Paste URLs through stdin, pass them as arguments, or use --file. Example: "
            "uv run python -m dashboard.youtube_curator add-channels --file channels.txt"
        )

    resolved, errors = resolve_channels(sources)
    for channel in resolved:
        title = f" - {channel.title}" if channel.title else ""
        print(f"{channel.channel_id}{title}")

    for error in errors:
        print(f"Resolve warning: {error}", file=sys.stderr)

    if print_only:
        return

    result = add_channels_to_config(
        config_path,
        resolved,
        update_titles=update_titles,
    )
    print(
        f"Config updated: {config_path} "
        f"({len(result.added)} added, {len(result.updated)} title updates, {len(result.skipped)} skipped)."
    )


def _recommend_from_db(
    database: Database, config: AppConfig, target_date: date
):
    since = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc) - timedelta(
        days=config.youtube.lookback_days
    )
    videos = database.list_videos_since(since)
    watched_video_ids = database.feedback_video_ids(WATCHED_FEEDBACK_ACTIONS)
    return rank_videos(
        videos,
        config,
        target_date,
        excluded_video_ids=watched_video_ids,
    )


def _top_up_with_discovery(
    database: Database,
    config: AppConfig,
    target_date: date,
    recommendations: list[RankedVideo],
    messages: list[str] | None = None,
) -> list[RankedVideo]:
    target_count = config.youtube.max_videos_per_day
    if target_count <= 0 or len(recommendations) >= target_count:
        return recommendations
    if not config.youtube.discovery_enabled:
        return recommendations

    interests = config.profile.interests or config.profile.preferred_terms
    if not interests:
        return recommendations

    watched_video_ids = database.feedback_video_ids(WATCHED_FEEDBACK_ACTIONS)
    selected_video_ids = {item.video.video_id for item in recommendations}
    configured_channel_ids = {channel.id for channel in config.channels if channel.id}
    needed = target_count - len(recommendations)
    results_per_interest = max(config.youtube.discovery_results_per_interest, target_count)
    max_results = max(target_count * 4, needed * 4)

    discovery_videos, errors = fetch_interest_discovery_videos(
        interests,
        max_results=max_results,
        results_per_interest=results_per_interest,
        exclude_channel_ids=configured_channel_ids,
        exclude_video_ids=watched_video_ids | selected_video_ids,
    )
    for error in errors:
        _append_message(messages, f"Discovery warning: {error}")

    inserted = database.upsert_videos(discovery_videos)
    if discovery_videos:
        _append_message(
            messages,
            f"Fetched {len(discovery_videos)} discovery candidates and upserted {inserted} rows.",
        )

    discovery_ranked = rank_videos(
        discovery_videos,
        config,
        target_date,
        excluded_video_ids=watched_video_ids | selected_video_ids,
        allow_outside_lookback_video_ids={video.video_id for video in discovery_videos},
    )
    filled = _append_recommendations(recommendations, discovery_ranked, target_count)
    if len(filled) < target_count:
        _append_message(
            messages,
            f"Discovery filled {len(filled) - len(recommendations)} of {needed} open slots.",
        )
    return filled


def _append_message(messages: list[str] | None, message: str) -> None:
    if messages is None:
        print(message)
        return
    messages.append(message)


def _append_recommendations(
    recommendations: list[RankedVideo],
    candidates: list[RankedVideo],
    target_count: int,
) -> list[RankedVideo]:
    filled = list(recommendations)
    selected_video_ids = {item.video.video_id for item in filled}
    for candidate in candidates:
        if len(filled) >= target_count:
            break
        if candidate.video.video_id in selected_video_ids:
            continue
        filled.append(candidate)
        selected_video_ids.add(candidate.video.video_id)
    return filled


def _load_or_create_config(path: Path) -> AppConfig:
    if not path.exists():
        write_default_config(path)
        raise SystemExit(f"Created {path}. Add YouTube channel IDs, then run again.")
    return load_config(path)


def _load_config_or_default(path: Path) -> AppConfig:
    if path.exists():
        return load_config(path)
    return parse_config(DEFAULT_CONFIG)


def _parse_date(value: str | None) -> date:
    if not value:
        return date.today()
    return date.fromisoformat(value)


def _demo_videos(target_date: date) -> list[Video]:
    base = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc)
    samples = [
        (
            "demo-ai-systems",
            "Practical AI Systems",
            "Building reliable AI agents with Python queues and evals",
            "A grounded walkthrough of agent orchestration, task queues, state, and simple evaluations.",
            34 * 60,
            184000,
        ),
        (
            "demo-backend",
            "Backend Notes",
            "SQLite is enough for more apps than you think",
            "A practical tour through SQLite patterns for personal tools, cron jobs, and small web apps.",
            22 * 60,
            96000,
        ),
        (
            "demo-fitness",
            "Training Log",
            "How to build a sustainable strength routine",
            "Programming basics, recovery, progressive overload, and avoiding noisy fitness advice.",
            28 * 60,
            121000,
        ),
        (
            "demo-finance",
            "Long Term Money",
            "Simple personal finance systems that reduce decisions",
            "Budget automation, investment checklists, and low-maintenance personal finance routines.",
            18 * 60,
            76000,
        ),
        (
            "demo-india-tech",
            "India Tech Brief",
            "What is changing in India's developer tooling market",
            "A concise look at engineering teams, AI adoption, infrastructure, and product opportunities.",
            26 * 60,
            52000,
        ),
        (
            "demo-short",
            "Noisy Channel",
            "#shorts insane productivity hack",
            "A short reaction clip that should be filtered out.",
            45,
            999999,
        ),
    ]
    videos: list[Video] = []
    for index, (video_id, channel, title, description, duration, views) in enumerate(samples):
        videos.append(
            Video(
                video_id=video_id,
                channel_id=f"demo-channel-{index}",
                channel_title=channel,
                title=title,
                description=description,
                url=f"https://www.youtube.com/watch?v={video_id}",
                thumbnail_url=f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                published_at=base - timedelta(hours=index * 8),
                duration_seconds=duration,
                view_count=views,
            )
        )
    return videos


if __name__ == "__main__":
    main()
