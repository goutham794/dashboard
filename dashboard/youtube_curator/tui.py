from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from textwrap import shorten
from typing import Callable

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static
from textual.worker import Worker, WorkerState

from .cli import GenerationResult, generate_daily_feed
from .db import Database
from .models import AppConfig, RankedVideo, Video
from .render import youtube_watch_url


FEEDBACK_LABELS = {
    "seen": "seen",
    "watched": "watched",
    "saved": "saved",
    "less_like_this": "less like this",
}

FeedGenerator = Callable[[AppConfig, date], GenerationResult]


@dataclass(slots=True)
class DashboardItem:
    rank: int
    ranked_video: RankedVideo
    feedback_actions: set[str] = field(default_factory=set)

    @property
    def video(self) -> Video:
        return self.ranked_video.video


class DashboardListItem(ListItem):
    def __init__(self, item: DashboardItem) -> None:
        self.dashboard_item = item
        super().__init__(Label(_list_label(item), markup=False))


class DashboardTuiApp(App[None]):
    CSS_PATH = "tui.tcss"
    TITLE = "Daily Watchlist"
    SUB_TITLE = "Finite content dashboard"
    BINDINGS = [
        Binding("q", "quit", "Quit", priority=True),
        Binding("o", "open_selected", "Open"),
        Binding("g", "generate_feed", "Generate"),
        Binding("w", "mark_watched", "Watched"),
        Binding("s", "save_selected", "Save"),
        Binding("l", "less_like_this", "Less Like This"),
        Binding("r", "reload", "Reload"),
    ]

    def __init__(
        self,
        config: AppConfig,
        recommendation_date: date,
        items: list[DashboardItem],
        feed_generator: FeedGenerator | None = None,
    ) -> None:
        super().__init__()
        self.config = config
        self.recommendation_date = recommendation_date
        self.items = items
        self.database = Database(config.storage.database_path)
        self.feed_generator = feed_generator or _generate_feed
        self.is_generating = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="layout"):
            with Vertical(id="sidebar"):
                yield Static(self._summary_text(), id="summary", markup=False)
                yield ListView(id="items")
            with ScrollableContainer(id="detail-pane"):
                yield Static("", id="detail", markup=False)
        yield Footer()

    async def on_mount(self) -> None:
        await self._populate_items()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if isinstance(event.item, DashboardListItem):
            self._show_item(event.item.dashboard_item)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, DashboardListItem):
            self._open_item(event.item.dashboard_item)

    def action_open_selected(self) -> None:
        item = self._selected_item()
        if item is not None:
            self._open_item(item)

    def action_mark_watched(self) -> None:
        self._record_feedback("watched", "Marked watched")

    def action_save_selected(self) -> None:
        self._record_feedback("saved", "Saved")

    def action_less_like_this(self) -> None:
        self._record_feedback("less_like_this", "Marked less like this")

    async def action_reload(self) -> None:
        self.items = load_dashboard_items(self.config, self.recommendation_date)
        await self._populate_items()
        self.notify("Reloaded today's watchlist")

    def action_generate_feed(self) -> None:
        if self.is_generating:
            self.notify("Feed generation is already running")
            return

        self.is_generating = True
        self.query_one("#summary", Static).update(self._summary_text())
        self.notify(f"Generating feed for {self.recommendation_date.isoformat()}")
        self.run_worker(
            lambda: self.feed_generator(self.config, self.recommendation_date),
            name="generate-feed",
            group="generation",
            description="Fetch, rank, and write the daily feed",
            exclusive=True,
            exit_on_error=False,
            thread=True,
        )

    async def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.name != "generate-feed":
            return
        if event.state == WorkerState.SUCCESS:
            result = event.worker.result
            self.is_generating = False
            self.items = load_dashboard_items(self.config, self.recommendation_date)
            await self._populate_items()
            details = _generation_summary(result)
            self.notify(details, title="Feed generated", timeout=8)
            return
        if event.state == WorkerState.ERROR:
            self.is_generating = False
            self.query_one("#summary", Static).update(self._summary_text())
            error = event.worker.error
            self.notify(str(error or "Could not generate feed"), severity="error", timeout=10)

    async def _populate_items(self) -> None:
        self.query_one("#summary", Static).update(self._summary_text())
        list_view = self.query_one("#items", ListView)
        await list_view.clear()
        if not self.items:
            self.query_one("#detail", Static).update(_empty_detail(self.config, self.recommendation_date))
            return

        for item in self.items:
            await list_view.append(DashboardListItem(item))
        list_view.index = 0
        self._show_item(self.items[0])

    def _selected_item(self) -> DashboardItem | None:
        list_view = self.query_one("#items", ListView)
        index = list_view.index
        if index is None or index < 0 or index >= len(self.items):
            return None
        return self.items[index]

    def _show_item(self, item: DashboardItem) -> None:
        self.query_one("#detail", Static).update(_detail_text(item))

    def _open_item(self, item: DashboardItem) -> None:
        self.open_url(youtube_watch_url(item.video))
        self.notify(f"Opened clean player for {item.video.title}")

    def _record_feedback(self, action: str, message: str) -> None:
        item = self._selected_item()
        if item is None:
            return

        self.database.init()
        self.database.set_feedback(item.video.video_id, action, active=True)
        item.feedback_actions = self.database.feedback_actions_for_video(item.video.video_id)
        self._refresh_selected_label(item)
        self._show_item(item)
        self.notify(message)

    def _refresh_selected_label(self, item: DashboardItem) -> None:
        list_view = self.query_one("#items", ListView)
        index = list_view.index
        if index is None:
            return
        selected = list_view.children[index] if index < len(list_view.children) else None
        if isinstance(selected, DashboardListItem):
            label = selected.query_one(Label)
            label.update(_list_label(item))

    def _summary_text(self) -> str:
        count = len(self.items)
        enabled_channels = sum(1 for channel in self.config.channels if channel.enabled)
        plural = "item" if count == 1 else "items"
        status = "\nGenerating feed..." if self.is_generating else ""
        return (
            f"{self.config.output.page_title}\n"
            f"{self.recommendation_date.isoformat()} | {count} {plural}\n"
            f"{enabled_channels} enabled YouTube channels"
            f"{status}"
        )


def run_tui(config: AppConfig, recommendation_date: date) -> None:
    app = DashboardTuiApp(
        config,
        recommendation_date,
        load_dashboard_items(config, recommendation_date),
    )
    app.run()


def _generate_feed(config: AppConfig, recommendation_date: date) -> GenerationResult:
    try:
        return generate_daily_feed(config, recommendation_date)
    except SystemExit as exc:
        raise RuntimeError(str(exc)) from None


def load_dashboard_items(config: AppConfig, recommendation_date: date) -> list[DashboardItem]:
    database = Database(config.storage.database_path)
    database.init()
    recommendations = _load_json_recommendations(config, recommendation_date)
    if recommendations is None:
        recommendations = database.list_recommendations(recommendation_date)
    else:
        database.upsert_videos(recommendation.video for recommendation in recommendations)

    return [
        DashboardItem(
            rank=rank,
            ranked_video=recommendation,
            feedback_actions=database.feedback_actions_for_video(recommendation.video.video_id),
        )
        for rank, recommendation in enumerate(recommendations, start=1)
    ]


def _load_json_recommendations(
    config: AppConfig,
    recommendation_date: date,
) -> list[RankedVideo] | None:
    json_path = Path(config.output.site_dir) / "daily" / f"{recommendation_date.isoformat()}.json"
    if not json_path.exists():
        return None

    with json_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    items = payload.get("items", [])
    if not isinstance(items, list):
        return []

    recommendations: list[RankedVideo] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        video_payload = item.get("video", {})
        if not isinstance(video_payload, dict):
            continue
        recommendations.append(
            RankedVideo(
                video=_video_from_json(video_payload),
                score=float(item.get("score", 0.0)),
                reason=str(item.get("reason", "")).strip(),
                matched_interests=[
                    str(value).strip()
                    for value in item.get("matched_interests", [])
                    if str(value).strip()
                ],
            )
        )
    return recommendations


def _video_from_json(payload: dict[str, object]) -> Video:
    published_at = datetime.fromisoformat(str(payload.get("published_at", "")))
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)

    return Video(
        video_id=str(payload.get("id", "")).strip(),
        channel_id=str(payload.get("channel_id", "")).strip(),
        channel_title=str(payload.get("channel_title", "")).strip(),
        title=str(payload.get("title", "")).strip(),
        description=str(payload.get("description", "")).strip(),
        url=str(payload.get("url", "")).strip(),
        thumbnail_url=str(payload.get("thumbnail_url", "")).strip(),
        published_at=published_at.astimezone(timezone.utc),
        duration_seconds=_optional_int(payload.get("duration_seconds")),
        view_count=_optional_int(payload.get("view_count")),
    )


def _list_label(item: DashboardItem) -> str:
    video = item.video
    flags = _feedback_text(item.feedback_actions)
    flag_suffix = f" | {flags}" if flags else ""
    return (
        f"{item.rank:>2}. {shorten(video.title, width=76, placeholder='...')}\n"
        f"    {shorten(video.channel_title, width=34, placeholder='...')} | "
        f"{_duration_text(video.duration_seconds)} | score {item.ranked_video.score:.2f}{flag_suffix}"
    )


def _detail_text(item: DashboardItem) -> str:
    video = item.video
    interests = ", ".join(item.ranked_video.matched_interests) or "none"
    feedback = _feedback_text(item.feedback_actions) or "none"
    description = video.description.strip() or "No description available."
    return (
        f"#{item.rank} {video.title}\n\n"
        f"Channel: {video.channel_title}\n"
        f"Published: {video.published_at.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"Duration: {_duration_text(video.duration_seconds)}\n"
        f"Views: {_views_text(video.view_count)}\n"
        f"Score: {item.ranked_video.score:.3f}\n"
        f"Feedback: {feedback}\n\n"
        f"Why this is here\n"
        f"{item.ranked_video.reason or 'No ranking reason recorded.'}\n\n"
        f"Matched interests: {interests}\n\n"
        f"Description\n"
        f"{description}\n\n"
        f"Clean player URL\n"
        f"{youtube_watch_url(video)}\n\n"
        f"Original YouTube URL\n"
        f"{video.url}\n\n"
        f"Actions: Enter/o watch clean | g generate | w watched | s save | l less-like-this | r reload | q quit"
    )


def _generation_summary(result: GenerationResult) -> str:
    messages = result.messages[-2:]
    selected = (
        f"Selected {len(result.recommendations)} items for "
        f"{result.recommendation_date.isoformat()}"
    )
    if not messages:
        return selected
    return f"{selected}\n" + "\n".join(messages)


def _empty_detail(config: AppConfig, recommendation_date: date) -> str:
    return (
        f"No items found for {recommendation_date.isoformat()}.\n\n"
        f"Press g to generate this day's feed from the TUI.\n\n"
        f"Or generate it from the CLI:\n"
        f"uv run python -m dashboard.youtube_curator run\n\n"
        f"Or run the demo:\n"
        f"uv run python -m dashboard.youtube_curator demo"
    )


def _duration_text(seconds: int | None) -> str:
    if seconds is None:
        return "unknown length"
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:d}:{seconds:02d}"


def _views_text(view_count: int | None) -> str:
    if view_count is None:
        return "unknown"
    return f"{view_count:,}"


def _feedback_text(actions: set[str]) -> str:
    labels = [FEEDBACK_LABELS.get(action, action) for action in sorted(actions)]
    return ", ".join(labels)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)
