## Personal Dashboard

This repo currently contains the first slice of the dashboard idea: a YouTube
daily curator. It builds a finite watchlist from configured YouTube channels
using RSS feeds, local SQLite storage, deterministic filters, and a terminal UI.

### Run the demo

```bash
uv run python -m dashboard.youtube_curator demo
```

This generates:

- `site/daily/YYYY-MM-DD.json`
- `data/youtube_curator.sqlite3`

Open the finite daily watchlist in a keyboard-driven terminal UI:

```bash
uv run python -m dashboard.youtube_curator tui
```

The TUI reads the generated daily JSON first and falls back to stored SQLite
recommendations for the selected date. Use `Enter` or `o` to open the selected
video in a clean YouTube embed player without the normal recommendations
sidebar, `g` to generate the day's feed, `w` to mark it watched, `s` to save it,
`l` for less-like-this, `r` to reload, and `q` to quit.

### Configure real channels

You can paste channel URLs, handles, or direct `UC...` IDs and let the CLI add
them to `config/youtube.json`:

```bash
uv run python -m dashboard.youtube_curator add-channels <<'EOF'
https://www.youtube.com/@unsolicitedadvice9198
https://www.youtube.com/@Fireship
EOF
```

To only print IDs and names without modifying the config:

```bash
uv run python -m dashboard.youtube_curator add-channels --print-only <<'EOF'
https://www.youtube.com/@unsolicitedadvice9198
EOF
```

There is also a thin script wrapper if you prefer a file path:

```bash
uv run python scripts/add_youtube_channels.py --file channels.txt
```

Or edit `config/youtube.json` manually and add channel IDs:

```json
{
  "id": "UCxxxxxxxxxxxxxxxxxxxxxx",
  "title": "Example Channel",
  "weight": 1.0,
  "topics": ["python", "engineering"],
  "enabled": true
}
```

Then run the curator:

```bash
uv run python -m dashboard.youtube_curator run
```

The RSS-only version does not need a YouTube API key. It can filter by title,
description, channel weight, configured interests, blocked terms, lookback
window, and previously recommended videos. Duration filtering is applied when
duration data is available; RSS feeds usually do not include duration, so the
next useful upgrade is YouTube Data API enrichment.

If the ranked channel videos do not fill `max_videos_per_day`, the curator can
top up the watchlist with YouTube search results for your configured interests.
This is enabled by default with `youtube.discovery_enabled` and skips configured
channel IDs so the extra slots favor new creators. Tune
`youtube.discovery_results_per_interest` to control how many search candidates
are considered for each interest.

### Useful commands

```bash
uv run python -m dashboard.youtube_curator init
uv run python -m dashboard.youtube_curator add-channels --file channels.txt
uv run python -m dashboard.youtube_curator run --date 2026-05-29
uv run python -m dashboard.youtube_curator run --skip-fetch
uv run python -m dashboard.youtube_curator tui
uv run python -m unittest discover -s tests
```
