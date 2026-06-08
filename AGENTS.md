# AGENTS.md

## Project Goal

This application is a personal, intentional-content dashboard. The main goal is
to replace habit-driven infinite scrolling with a finite daily page of content
that is worth consuming.

The first product slice is focused on YouTube, but the broader goal is a
personal daily page that helps the user decide what is worth attention that day.

The broader direction is to add more personal modules over time: articles,
Reddit, X/Twitter if cost-effective, todos, calendar, health data, and feedback
signals. The page should remain finite and intentional rather than becoming
another feed.

## Product Principles

- Keep the daily output small. The page should end.
- Prefer configured sources over broad search or open-ended browsing.
- Minimize LLM usage. Use deterministic filters, scoring, embeddings, and local
  data first; reserve LLMs for final selection or short explanations.
- Keep costs low and make expensive integrations optional.
- Favor user-controlled configuration over account scraping.
- Store enough local state to avoid recommending the same items repeatedly.
- Make feedback useful: watched, saved, less-like-this, and blocked sources
  should eventually influence ranking.

## Current Implementation

The current codebase is a Python MVP:

- CLI entry point: `python -m dashboard.youtube_curator`
- TUI entry point: `python -m dashboard.youtube_curator tui`
- Config: `config/youtube.json`
- SQLite database: `data/youtube_curator.sqlite3`
- Daily JSON output: `site/daily/*.json`
- Channel resolver: `dashboard/youtube_curator/channel_resolver.py`

Use `uv` for Python commands:

```bash
uv run python -m dashboard.youtube_curator demo
uv run python -m dashboard.youtube_curator add-channels --file channels.txt
uv run python -m dashboard.youtube_curator run
uv run python -m dashboard.youtube_curator tui
uv run python -m unittest discover -s tests
```

## Engineering Guidance

- Keep dependencies minimal unless there is a clear payoff.
- Avoid building a general web-browsing agent until the source-specific pipeline
  has proven useful.
- Treat generated JSON output and local database state as local artifacts.
- Keep ranking logic transparent and easy to tune from config.
