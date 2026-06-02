from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from datetime import date, datetime, timedelta, timezone
from errno import EADDRINUSE
from pathlib import Path
from urllib.parse import urlparse

from .channel_resolver import add_channels_to_config, collect_sources, resolve_channels
from .config import DEFAULT_CONFIG, load_config, parse_config, write_default_config
from .db import Database
from .fetch import fetch_all_channels, fetch_interest_discovery_videos
from .models import AppConfig, RankedVideo, Video
from .rank import rank_videos
from .render import render_daily_page


DEFAULT_CONFIG_PATH = Path("config/youtube.json")
WATCHED_FEEDBACK_ACTIONS = {"seen", "watched"}
ALLOWED_FEEDBACK_ACTIONS = WATCHED_FEEDBACK_ACTIONS | {"saved", "less_like_this"}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="yt-curator",
        description="Build a finite daily YouTube watchlist from configured channels.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create a starter config file.")
    init_parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    init_parser.add_argument("--force", action="store_true")

    run_parser = subparsers.add_parser("run", help="Fetch feeds, rank videos, and render the page.")
    run_parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    run_parser.add_argument("--date", default=None, help="Recommendation date in YYYY-MM-DD format.")
    run_parser.add_argument("--skip-fetch", action="store_true", help="Rank existing DB videos only.")

    demo_parser = subparsers.add_parser(
        "demo", help="Generate a page from built-in sample videos without network access."
    )
    demo_parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    demo_parser.add_argument("--date", default=None, help="Recommendation date in YYYY-MM-DD format.")

    serve_parser = subparsers.add_parser(
        "serve",
        help="Serve the generated static site over localhost for embedded playback.",
    )
    serve_parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)

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

    if args.command == "serve":
        config = _load_config_or_default(Path(args.config))
        _serve_site(config, host=args.host, port=args.port)
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
    enabled_channels = [channel for channel in config.channels if channel.enabled]
    if not enabled_channels:
        raise SystemExit(
            "No YouTube channels configured. Edit config/youtube.json or run `python -m dashboard.youtube_curator demo`."
        )

    database = Database(config.storage.database_path)
    database.init()

    if not skip_fetch:
        videos, errors = fetch_all_channels(enabled_channels)
        inserted = database.upsert_videos(videos)
        print(f"Fetched {len(videos)} videos and upserted {inserted} rows.")
        for error in errors:
            print(f"Fetch warning: {error}")

    recommendations = _recommend_from_db(database, config, target_date)
    if not skip_fetch:
        recommendations = _top_up_with_discovery(database, config, target_date, recommendations)
    html_path, json_path = render_daily_page(config, target_date, recommendations)
    database.replace_recommendations(target_date, recommendations)

    print(f"Selected {len(recommendations)} videos for {target_date.isoformat()}.")
    print(f"HTML: {html_path}")
    print(f"JSON: {json_path}")


def _run_demo(config: AppConfig, target_date: date) -> None:
    database = Database(config.storage.database_path)
    database.init()
    videos = _demo_videos(target_date)
    inserted = database.upsert_videos(videos)
    recommendations = _recommend_from_db(database, config, target_date)
    html_path, json_path = render_daily_page(config, target_date, recommendations)
    database.replace_recommendations(target_date, recommendations)
    print(f"Loaded {inserted} demo videos.")
    print(f"Selected {len(recommendations)} videos for {target_date.isoformat()}.")
    print(f"HTML: {html_path}")
    print(f"JSON: {json_path}")


def _serve_site(config: AppConfig, *, host: str, port: int) -> None:
    site_dir = Path(config.output.site_dir)
    index_path = site_dir / "index.html"
    if not index_path.exists():
        raise SystemExit(
            f"{index_path} does not exist yet. Run `uv run python -m dashboard.youtube_curator run` first."
        )

    handler = _site_handler(site_dir, config.storage.database_path)
    server = _create_server(host, port, handler)
    actual_host, actual_port = server.server_address[:2]
    display_host = "127.0.0.1" if actual_host in ("0.0.0.0", "") else actual_host
    print(f"Serving {site_dir} at http://{display_host}:{actual_port}/", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server.server_close()


def _create_server(host: str, port: int, handler) -> ThreadingHTTPServer:
    candidates = [0] if port == 0 else range(port, port + 20)
    last_error: OSError | None = None
    for candidate in candidates:
        try:
            return ThreadingHTTPServer((host, candidate), handler)
        except OSError as exc:
            if exc.errno != EADDRINUSE:
                raise
            last_error = exc
    raise SystemExit(f"Could not bind a local server near port {port}: {last_error}")


def _site_handler(site_dir: Path, database_path: str):
    class DashboardRequestHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(site_dir), **kwargs)

        def do_POST(self) -> None:
            if urlparse(self.path).path != "/api/feedback":
                self.send_error(404, "Not found")
                return

            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.send_error(400, "Invalid feedback payload")
                return
            if content_length <= 0 or content_length > 4096:
                self.send_error(400, "Invalid feedback payload")
                return

            try:
                payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
            except json.JSONDecodeError:
                self.send_error(400, "Invalid feedback JSON")
                return

            video_id = str(payload.get("video_id", "")).strip()
            action = str(payload.get("action", "")).strip()
            active = bool(payload.get("active", True))
            if not video_id or action not in ALLOWED_FEEDBACK_ACTIONS:
                self.send_error(400, "Invalid feedback action")
                return

            try:
                database = Database(database_path)
                database.init()
                database.set_feedback(video_id, action, active=active)
            except (OSError, sqlite3.DatabaseError, ValueError) as exc:
                self.send_error(500, f"Could not record feedback: {exc}")
                return

            self.send_response(204)
            self.end_headers()

    return DashboardRequestHandler


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
        print(f"Discovery warning: {error}")

    inserted = database.upsert_videos(discovery_videos)
    if discovery_videos:
        print(
            f"Fetched {len(discovery_videos)} discovery candidates and upserted {inserted} rows."
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
        print(f"Discovery filled {len(filled) - len(recommendations)} of {needed} open slots.")
    return filled


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
