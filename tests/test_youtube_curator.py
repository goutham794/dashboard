from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from dashboard.youtube_curator.channel_resolver import (
    ResolvedChannel,
    add_channels_to_config,
    collect_sources,
    extract_channel_id,
    extract_channel_id_from_page,
    extract_channel_title_from_page,
)
from dashboard.youtube_curator.cli import GenerationResult, _recommend_from_db
from dashboard.youtube_curator.config import DEFAULT_CONFIG, parse_config
from dashboard.youtube_curator.db import Database
from dashboard.youtube_curator.fetch import parse_youtube_feed, parse_youtube_search_results
from dashboard.youtube_curator.models import RankedVideo, Video
from dashboard.youtube_curator.rank import rank_videos
from dashboard.youtube_curator.render import render_daily_json, youtube_watch_url
from dashboard.youtube_curator.tui import DashboardTuiApp, load_dashboard_items
from textual.widgets import Static


SAMPLE_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>yt:video:abc123</id>
    <yt:videoId>abc123</yt:videoId>
    <yt:channelId>UC_test</yt:channelId>
    <title>Building useful Python agents</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=abc123"/>
    <author>
      <name>Useful Systems</name>
      <uri>https://www.youtube.com/channel/UC_test</uri>
    </author>
    <published>2026-05-28T10:00:00+00:00</published>
    <media:group>
      <media:title>Building useful Python agents</media:title>
      <media:thumbnail url="https://i.ytimg.com/vi/abc123/hqdefault.jpg"/>
      <media:description>Practical AI engineering with queues and evals.</media:description>
      <media:community>
        <media:statistics views="12000"/>
      </media:community>
    </media:group>
  </entry>
</feed>
"""

SAMPLE_CHANNEL_PAGE = """
<html>
  <head>
    <link rel="canonical" href="https://www.youtube.com/channel/UC1234567890123456789012">
    <meta property="og:title" content="Unsolicited Advice">
  </head>
  <body>
    {"channelMetadataRenderer":{"title":"Unsolicited Advice","externalId":"UC1234567890123456789012"}}
  </body>
</html>
"""

SAMPLE_SEARCH_HTML = """
<html>
  <script>
    var ytInitialData = {
      "contents": {
        "twoColumnSearchResultsRenderer": {
          "primaryContents": {
            "sectionListRenderer": {
              "contents": [
                {
                  "itemSectionRenderer": {
                    "contents": [
                      {
                        "videoRenderer": {
                          "videoId": "discover123",
                          "title": {
                            "runs": [
                              {"text": "Practical AI engineering patterns"}
                            ]
                          },
                          "ownerText": {
                            "runs": [
                              {
                                "text": "New Systems Creator",
                                "navigationEndpoint": {
                                  "browseEndpoint": {"browseId": "UC_discovery"}
                                }
                              }
                            ]
                          },
                          "publishedTimeText": {"simpleText": "3 weeks ago"},
                          "lengthText": {"simpleText": "18:42"},
                          "viewCountText": {"simpleText": "12K views"},
                          "thumbnail": {
                            "thumbnails": [
                              {"url": "https://example.test/small.jpg"},
                              {"url": "https://example.test/large.jpg"}
                            ]
                          },
                          "descriptionSnippet": {
                            "runs": [
                              {"text": "Agents, evals, and backend systems."}
                            ]
                          }
                        }
                      }
                    ]
                  }
                }
              ]
            }
          }
        }
      }
    };
  </script>
</html>
"""


class YouTubeCuratorTests(unittest.TestCase):
    def test_collect_sources_accepts_args_files_and_stdin_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "channels.txt"
            path.write_text("https://www.youtube.com/@one\n# skip me\n@two\n", encoding="utf-8")
            sources = collect_sources(
                ["UC1234567890123456789012"],
                str(path),
                "https://www.youtube.com/@three\n",
            )
        self.assertEqual(
            sources,
            [
                "UC1234567890123456789012",
                "https://www.youtube.com/@one",
                "@two",
                "https://www.youtube.com/@three",
            ],
        )

    def test_extract_channel_id_and_title_from_page(self) -> None:
        self.assertEqual(
            extract_channel_id("https://www.youtube.com/channel/UC1234567890123456789012"),
            "UC1234567890123456789012",
        )
        self.assertEqual(
            extract_channel_id_from_page(SAMPLE_CHANNEL_PAGE),
            "UC1234567890123456789012",
        )
        self.assertEqual(extract_channel_title_from_page(SAMPLE_CHANNEL_PAGE), "Unsolicited Advice")

    def test_add_channels_to_config_appends_and_skips_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "youtube.json"
            channel = ResolvedChannel(
                source="https://www.youtube.com/@example",
                channel_id="UC1234567890123456789012",
                title="Example Channel",
                url="https://www.youtube.com/@example",
            )
            first = add_channels_to_config(path, [channel])
            second = add_channels_to_config(path, [channel])

            self.assertEqual(len(first.added), 1)
            self.assertEqual(len(second.skipped), 1)
            self.assertIn("Example Channel", path.read_text(encoding="utf-8"))

    def test_parse_youtube_feed(self) -> None:
        videos = parse_youtube_feed(SAMPLE_FEED)
        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0].video_id, "abc123")
        self.assertEqual(videos[0].channel_title, "Useful Systems")
        self.assertEqual(videos[0].view_count, 12000)

    def test_parse_youtube_search_results(self) -> None:
        fetched_at = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)
        videos = parse_youtube_search_results(SAMPLE_SEARCH_HTML, fetched_at=fetched_at)
        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0].video_id, "discover123")
        self.assertEqual(videos[0].channel_id, "UC_discovery")
        self.assertEqual(videos[0].channel_title, "New Systems Creator")
        self.assertEqual(videos[0].duration_seconds, 1122)
        self.assertEqual(videos[0].view_count, 12000)
        self.assertEqual(videos[0].published_at, datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(videos[0].thumbnail_url, "https://example.test/large.jpg")

    def test_rank_filters_blocked_terms_and_picks_relevant_video(self) -> None:
        config = parse_config(
            {
                **DEFAULT_CONFIG,
                "youtube": {
                    **DEFAULT_CONFIG["youtube"],
                    "max_videos_per_day": 5,
                    "min_duration_seconds": 60,
                },
                "channels": [
                    {"id": "UC1", "title": "Systems", "weight": 1.0, "topics": ["python"]},
                    {"id": "UC2", "title": "Noise", "weight": 1.0, "topics": []},
                ],
            }
        )
        videos = [
            Video(
                video_id="good",
                channel_id="UC1",
                channel_title="Systems",
                title="Python backend systems for AI agents",
                description="Practical AI engineering.",
                url="https://www.youtube.com/watch?v=good",
                thumbnail_url="",
                published_at=datetime(2026, 5, 28, tzinfo=timezone.utc),
                duration_seconds=1200,
                view_count=10000,
            ),
            Video(
                video_id="bad",
                channel_id="UC2",
                channel_title="Noise",
                title="#shorts productivity drama",
                description="Should be blocked.",
                url="https://www.youtube.com/watch?v=bad",
                thumbnail_url="",
                published_at=datetime(2026, 5, 28, tzinfo=timezone.utc),
                duration_seconds=30,
                view_count=999999,
            ),
        ]

        ranked = rank_videos(videos, config, date(2026, 5, 29))
        self.assertEqual([item.video.video_id for item in ranked], ["good"])

    def test_rank_can_relax_lookback_for_discovery_backfill(self) -> None:
        config = parse_config(
            {
                **DEFAULT_CONFIG,
                "profile": {"interests": ["AI engineering"]},
                "youtube": {
                    **DEFAULT_CONFIG["youtube"],
                    "lookback_days": 7,
                    "min_duration_seconds": 60,
                },
            }
        )
        old_discovery = Video(
            video_id="old-discovery",
            channel_id="UC_discovery",
            channel_title="New Creator",
            title="AI engineering systems",
            description="Practical agents and backend evals.",
            url="https://www.youtube.com/watch?v=old-discovery",
            thumbnail_url="",
            published_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
            duration_seconds=1200,
            view_count=10000,
        )

        self.assertEqual(rank_videos([old_discovery], config, date(2026, 5, 29)), [])
        ranked = rank_videos(
            [old_discovery],
            config,
            date(2026, 5, 29),
            allow_outside_lookback_video_ids={"old-discovery"},
        )
        self.assertEqual([item.video.video_id for item in ranked], ["old-discovery"])

    def test_prior_recommendation_repeats_until_marked_watched(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = parse_config(
                {
                    **DEFAULT_CONFIG,
                    "storage": {"database_path": str(Path(temp_dir) / "curator.sqlite3")},
                    "youtube": {
                        **DEFAULT_CONFIG["youtube"],
                        "max_videos_per_day": 5,
                        "min_duration_seconds": 60,
                    },
                }
            )
            video = Video(
                video_id="repeat",
                channel_id="UC1",
                channel_title="Systems",
                title="Backend systems with Python",
                description="Practical engineering.",
                url="https://www.youtube.com/watch?v=repeat",
                thumbnail_url="",
                published_at=datetime(2026, 5, 28, tzinfo=timezone.utc),
                duration_seconds=900,
                view_count=1234,
            )
            database = Database(config.storage.database_path)
            database.init()
            database.upsert_videos([video])

            first = _recommend_from_db(database, config, date(2026, 5, 29))
            database.replace_recommendations(date(2026, 5, 29), first)
            second = _recommend_from_db(database, config, date(2026, 5, 30))
            self.assertEqual([item.video.video_id for item in second], ["repeat"])

            database.set_feedback("repeat", "watched", active=True)
            self.assertEqual(database.feedback_video_ids({"watched"}), {"repeat"})
            third = _recommend_from_db(database, config, date(2026, 5, 31))
            self.assertEqual(third, [])

            database.set_feedback("repeat", "watched", active=False)
            self.assertEqual(database.feedback_video_ids({"watched"}), set())
            fourth = _recommend_from_db(database, config, date(2026, 6, 1))
            self.assertEqual([item.video.video_id for item in fourth], ["repeat"])

    def test_database_and_render_json_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = parse_config(
                {
                    **DEFAULT_CONFIG,
                    "storage": {"database_path": str(Path(temp_dir) / "curator.sqlite3")},
                    "output": {"site_dir": str(Path(temp_dir) / "site"), "page_title": "Test Watchlist"},
                    "youtube": {
                        **DEFAULT_CONFIG["youtube"],
                        "min_duration_seconds": 60,
                    },
                }
            )
            video = Video(
                video_id="stored",
                channel_id="UC1",
                channel_title="Systems",
                title="Backend systems with Python",
                description="Practical engineering.",
                url="https://www.youtube.com/watch?v=stored",
                thumbnail_url="https://i.ytimg.com/vi/stored/hqdefault.jpg",
                published_at=datetime(2026, 5, 28, tzinfo=timezone.utc),
                duration_seconds=900,
                view_count=1234,
            )
            database = Database(config.storage.database_path)
            database.init()
            self.assertEqual(database.upsert_videos([video]), 1)
            ranked = rank_videos(database.list_videos_since(datetime(2026, 5, 20, tzinfo=timezone.utc)), config, date(2026, 5, 29))
            json_path = render_daily_json(config, date(2026, 5, 29), ranked)
            self.assertTrue(json_path.exists())
            payload = json_path.read_text(encoding="utf-8")
            self.assertIn("Test Watchlist", payload)
            self.assertIn("stored", payload)
            self.assertIn("Backend systems with Python", payload)
            self.assertIn(
                '"watch_url": "https://www.youtube-nocookie.com/embed/stored?autoplay=1&rel=0&modestbranding=1&playsinline=1"',
                payload,
            )

    def test_youtube_watch_url_uses_nocookie_embed_player(self) -> None:
        self.assertEqual(
            youtube_watch_url(_test_video("id with spaces")),
            "https://www.youtube-nocookie.com/embed/id%20with%20spaces?autoplay=1&rel=0&modestbranding=1&playsinline=1",
        )


class DashboardTuiTests(unittest.TestCase):
    def test_tui_loads_generated_json_and_upserts_feedback_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = _test_config(temp_dir)
            target_date = date(2026, 5, 29)
            video = _test_video("json-video")
            ranked = [
                RankedVideo(
                    video=video,
                    score=4.25,
                    reason="Matches backend systems",
                    matched_interests=["backend systems"],
                )
            ]

            render_daily_json(config, target_date, ranked)
            items = load_dashboard_items(config, target_date)

            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].rank, 1)
            self.assertEqual(items[0].video.video_id, "json-video")
            self.assertEqual(items[0].ranked_video.matched_interests, ["backend systems"])

            database = Database(config.storage.database_path)
            database.set_feedback("json-video", "saved", active=True)
            reloaded = load_dashboard_items(config, target_date)
            self.assertEqual(reloaded[0].feedback_actions, {"saved"})

    def test_tui_falls_back_to_sqlite_recommendations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = _test_config(temp_dir)
            target_date = date(2026, 5, 29)
            video = _test_video("db-video")
            database = Database(config.storage.database_path)
            database.init()
            database.upsert_videos([video])
            database.replace_recommendations(
                target_date,
                [RankedVideo(video=video, score=3.5, reason="Stored recommendation")],
            )

            items = load_dashboard_items(config, target_date)

            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].video.video_id, "db-video")
            self.assertEqual(items[0].ranked_video.reason, "Stored recommendation")
            self.assertEqual(items[0].ranked_video.score, 3.5)

    def test_tui_open_uses_clean_player_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = _test_config(temp_dir)
            target_date = date(2026, 5, 29)
            video = _test_video("open-video")
            ranked = [RankedVideo(video=video, score=1.0, reason="Test")]
            render_daily_json(config, target_date, ranked)
            items = load_dashboard_items(config, target_date)

            app = _RecordingDashboardTuiApp(config, target_date, items)
            app._open_item(items[0])

            self.assertEqual(app.opened_urls, [youtube_watch_url(video)])


class DashboardTuiAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_tui_renders_detail_and_records_watched_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = _test_config(temp_dir)
            target_date = date(2026, 5, 29)
            video = _test_video("tui-video")
            render_daily_json(
                config,
                target_date,
                [
                    RankedVideo(
                        video=video,
                        score=6.0,
                        reason="A useful systems video",
                        matched_interests=["systems"],
                    )
                ],
            )
            app = DashboardTuiApp(config, target_date, load_dashboard_items(config, target_date))

            async with app.run_test() as pilot:
                await pilot.pause()
                detail = pilot.app.query_one("#detail", Static)
                self.assertIn("Backend systems with Python", str(detail.content))
                await pilot.press("w")
                await pilot.pause()

            database = Database(config.storage.database_path)
            self.assertEqual(database.feedback_actions_for_video("tui-video"), {"watched"})

    async def test_tui_can_generate_and_reload_daily_feed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = _test_config(temp_dir)
            target_date = date(2026, 5, 29)
            generated_video = _test_video("generated-video")

            def fake_generate(config, recommendation_date):
                ranked = [
                    RankedVideo(
                        video=generated_video,
                        score=8.0,
                        reason="Generated from the TUI",
                        matched_interests=["systems"],
                    )
                ]
                json_path = render_daily_json(config, recommendation_date, ranked)
                database = Database(config.storage.database_path)
                database.init()
                database.upsert_videos([generated_video])
                database.replace_recommendations(recommendation_date, ranked)
                return GenerationResult(
                    recommendation_date=recommendation_date,
                    recommendations=ranked,
                    json_path=json_path,
                    messages=["Generated test feed"],
                )

            app = DashboardTuiApp(config, target_date, [], feed_generator=fake_generate)

            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press("g")
                await pilot.pause()
                detail = pilot.app.query_one("#detail", Static)
                self.assertIn("Generated from the TUI", str(detail.content))

            self.assertTrue((Path(temp_dir) / "site" / "daily" / "2026-05-29.json").exists())


def _test_config(temp_dir: str):
    return parse_config(
        {
            **DEFAULT_CONFIG,
            "storage": {"database_path": str(Path(temp_dir) / "curator.sqlite3")},
            "output": {"site_dir": str(Path(temp_dir) / "site"), "page_title": "Test Watchlist"},
            "youtube": {
                **DEFAULT_CONFIG["youtube"],
                "min_duration_seconds": 60,
            },
        }
    )


def _test_video(video_id: str) -> Video:
    return Video(
        video_id=video_id,
        channel_id="UC1",
        channel_title="Systems",
        title="Backend systems with Python",
        description="Practical engineering.",
        url=f"https://www.youtube.com/watch?v={video_id}",
        thumbnail_url=f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        published_at=datetime(2026, 5, 28, tzinfo=timezone.utc),
        duration_seconds=900,
        view_count=1234,
    )


class _RecordingDashboardTuiApp(DashboardTuiApp):
    def __init__(
        self,
        config,
        recommendation_date,
        items,
    ) -> None:
        super().__init__(config, recommendation_date, items)
        self.opened_urls: list[str] = []

    def open_url(self, url: str, **kwargs) -> None:
        self.opened_urls.append(url)

    def notify(self, *args, **kwargs) -> None:
        return None


if __name__ == "__main__":
    unittest.main()
