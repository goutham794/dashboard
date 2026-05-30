from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

from .models import RankedVideo, Video


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def init(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS videos (
                    video_id TEXT PRIMARY KEY,
                    channel_id TEXT NOT NULL,
                    channel_title TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    url TEXT NOT NULL,
                    thumbnail_url TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    duration_seconds INTEGER,
                    view_count INTEGER,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS recommendations (
                    recommendation_date TEXT NOT NULL,
                    video_id TEXT NOT NULL,
                    rank INTEGER NOT NULL,
                    score REAL NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (recommendation_date, video_id),
                    FOREIGN KEY (video_id) REFERENCES videos(video_id)
                );

                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (video_id) REFERENCES videos(video_id)
                );

                CREATE INDEX IF NOT EXISTS idx_videos_published_at
                    ON videos(published_at);
                CREATE INDEX IF NOT EXISTS idx_recommendations_date
                    ON recommendations(recommendation_date);
                """
            )

    def upsert_videos(self, videos: Iterable[Video]) -> int:
        now = _now_iso()
        rows = [
            (
                video.video_id,
                video.channel_id,
                video.channel_title,
                video.title,
                video.description,
                video.url,
                video.thumbnail_url,
                video.published_at.astimezone(timezone.utc).isoformat(),
                video.duration_seconds,
                video.view_count,
                now,
                now,
            )
            for video in videos
        ]
        if not rows:
            return 0

        with self.connect() as connection:
            connection.executemany(
                """
                INSERT INTO videos (
                    video_id, channel_id, channel_title, title, description, url,
                    thumbnail_url, published_at, duration_seconds, view_count,
                    first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(video_id) DO UPDATE SET
                    channel_id = excluded.channel_id,
                    channel_title = excluded.channel_title,
                    title = excluded.title,
                    description = excluded.description,
                    url = excluded.url,
                    thumbnail_url = excluded.thumbnail_url,
                    published_at = excluded.published_at,
                    duration_seconds = excluded.duration_seconds,
                    view_count = excluded.view_count,
                    last_seen_at = excluded.last_seen_at
                """,
                rows,
            )
        return len(rows)

    def list_videos_since(self, since: datetime) -> list[Video]:
        since_iso = since.astimezone(timezone.utc).isoformat()
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM videos
                WHERE published_at >= ?
                ORDER BY published_at DESC
                """,
                (since_iso,),
            ).fetchall()
        return [_video_from_row(row) for row in rows]

    def recommended_video_ids_before(self, recommendation_date: date) -> set[str]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT video_id
                FROM recommendations
                WHERE recommendation_date < ?
                """,
                (recommendation_date.isoformat(),),
            ).fetchall()
        return {str(row["video_id"]) for row in rows}

    def replace_recommendations(
        self, recommendation_date: date, recommendations: list[RankedVideo]
    ) -> None:
        now = _now_iso()
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM recommendations WHERE recommendation_date = ?",
                (recommendation_date.isoformat(),),
            )
            connection.executemany(
                """
                INSERT INTO recommendations (
                    recommendation_date, video_id, rank, score, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        recommendation_date.isoformat(),
                        item.video.video_id,
                        rank,
                        item.score,
                        item.reason,
                        now,
                    )
                    for rank, item in enumerate(recommendations, start=1)
                ],
            )


def _video_from_row(row: sqlite3.Row) -> Video:
    published_at = datetime.fromisoformat(str(row["published_at"]))
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    return Video(
        video_id=str(row["video_id"]),
        channel_id=str(row["channel_id"]),
        channel_title=str(row["channel_title"]),
        title=str(row["title"]),
        description=str(row["description"]),
        url=str(row["url"]),
        thumbnail_url=str(row["thumbnail_url"]),
        published_at=published_at.astimezone(timezone.utc),
        duration_seconds=row["duration_seconds"],
        view_count=row["view_count"],
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

