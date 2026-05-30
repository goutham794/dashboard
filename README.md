## Personal Dashboard

This repo currently contains the first slice of the dashboard idea: a YouTube
daily curator. It builds a finite watchlist from configured YouTube channels
using RSS feeds, local SQLite storage, deterministic filters, and a static HTML
page.

### Run the demo

```bash
uv run python -m dashboard.youtube_curator demo
```

This generates:

- `site/index.html`
- `site/daily/YYYY-MM-DD.html`
- `site/daily/YYYY-MM-DD.json`
- `data/youtube_curator.sqlite3`

Serve the generated site locally to view the watchlist with embedded playback:

```bash
uv run python -m dashboard.youtube_curator serve
```

Then open the printed `http://127.0.0.1:8000/` URL. Directly opening
`site/index.html` from disk can still show the page, but YouTube embeds require
an HTTP referrer and may fail with player error 153 from a `file://` URL.

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

### Useful commands

```bash
uv run python -m dashboard.youtube_curator init
uv run python -m dashboard.youtube_curator add-channels --file channels.txt
uv run python -m dashboard.youtube_curator run --date 2026-05-29
uv run python -m dashboard.youtube_curator run --skip-fetch
uv run python -m dashboard.youtube_curator serve
uv run python -m unittest discover -s tests
```

### Publish with GitHub Pages

The workflow in `.github/workflows/pages.yml` deploys the generated `site/`
directory when `main` is pushed or when the workflow is run manually. GitHub
Pages sites are publicly accessible, so review the generated HTML and JSON
before publishing.

Create an empty GitHub repository, then push this project:

```bash
git branch -M main
git remote add origin git@github.com:YOUR_USER/YOUR_REPOSITORY.git
git add .
git commit -m "Initial dashboard"
git push -u origin main
```

In the GitHub repository, open **Settings > Pages** and select **GitHub
Actions** as the source under **Build and deployment**.

To publish a refreshed daily snapshot, generate the page locally and push the
updated static files:

```bash
uv run python -m dashboard.youtube_curator run
git add site
git commit -m "Refresh daily dashboard"
git push
```
