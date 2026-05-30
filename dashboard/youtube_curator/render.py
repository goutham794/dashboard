from __future__ import annotations

import json
from datetime import date, datetime, timezone
from html import escape
from pathlib import Path
from urllib.parse import quote

from .models import AppConfig, RankedVideo, Video


def render_daily_page(
    config: AppConfig,
    recommendation_date: date,
    recommendations: list[RankedVideo],
) -> tuple[Path, Path]:
    site_dir = Path(config.output.site_dir)
    daily_dir = site_dir / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)

    payload = _json_payload(config, recommendation_date, recommendations)
    json_path = daily_dir / f"{recommendation_date.isoformat()}.json"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    html = _html_page(config, recommendation_date, recommendations)
    html_path = daily_dir / f"{recommendation_date.isoformat()}.html"
    html_path.write_text(html, encoding="utf-8")

    index_path = site_dir / "index.html"
    index_path.write_text(html, encoding="utf-8")
    return html_path, json_path


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
        "thumbnail_url": video.thumbnail_url,
        "published_at": video.published_at.isoformat(),
        "duration_seconds": video.duration_seconds,
        "view_count": video.view_count,
    }


def _html_page(
    config: AppConfig,
    recommendation_date: date,
    recommendations: list[RankedVideo],
) -> str:
    items = "\n".join(
        _video_item(rank, item) for rank, item in enumerate(recommendations, start=1)
    )
    if not items:
        items = """
        <section class="empty-state">
          <h2>No videos selected today</h2>
          <p>Add channel IDs to the config or widen the lookback window, then run the curator again.</p>
        </section>
        """

    interests = ", ".join(config.profile.interests[:5])
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    count_label = f"{len(recommendations)} video" if len(recommendations) == 1 else f"{len(recommendations)} videos"

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(config.output.page_title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --surface: #ffffff;
      --surface-strong: #eef4f1;
      --text: #172026;
      --muted: #5f6b75;
      --line: #d8dee5;
      --accent: #0f6b57;
      --accent-strong: #0b4f41;
      --warn: #9a4b15;
      --shadow: 0 18px 45px rgba(23, 32, 38, 0.08);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}

    * {{
      box-sizing: border-box;
    }}

    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
    }}

    a {{
      color: inherit;
    }}

    .page {{
      max-width: 1120px;
      margin: 0 auto;
      padding: 32px 20px 48px;
    }}

    .topbar {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 20px;
      align-items: end;
      padding: 10px 0 28px;
      border-bottom: 1px solid var(--line);
    }}

    .eyebrow {{
      margin: 0 0 8px;
      color: var(--accent);
      font-size: 0.78rem;
      font-weight: 760;
      letter-spacing: 0;
      text-transform: uppercase;
    }}

    h1 {{
      margin: 0;
      font-size: clamp(2rem, 5vw, 4.4rem);
      line-height: 0.96;
      letter-spacing: 0;
    }}

    .subtitle {{
      max-width: 760px;
      margin: 14px 0 0;
      color: var(--muted);
      font-size: 1rem;
      line-height: 1.6;
    }}

    .summary {{
      min-width: 190px;
      padding: 16px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface);
      box-shadow: var(--shadow);
    }}

    .summary strong {{
      display: block;
      font-size: 1.6rem;
      line-height: 1;
    }}

    .summary span {{
      display: block;
      margin-top: 8px;
      color: var(--muted);
      font-size: 0.9rem;
    }}

    .feed {{
      display: grid;
      gap: 16px;
      margin-top: 28px;
    }}

    .video {{
      display: grid;
      grid-template-columns: minmax(220px, 34%) minmax(0, 1fr);
      gap: 18px;
      padding: 16px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface);
      box-shadow: var(--shadow);
    }}

    .player {{
      position: relative;
      width: 100%;
      min-height: 180px;
      aspect-ratio: 16 / 9;
      overflow: hidden;
      border-radius: 6px;
      background: #26323a;
    }}

    .player iframe {{
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      border: 0;
      display: block;
      background: #111820;
    }}

    .player-trigger {{
      position: relative;
      display: block;
      width: 100%;
      height: 100%;
      min-height: 180px;
      overflow: hidden;
      border: 0;
      padding: 0;
      background: #26323a;
      color: inherit;
      cursor: pointer;
    }}

    .player-trigger img {{
      width: 100%;
      height: 100%;
      min-height: 180px;
      object-fit: cover;
      display: block;
    }}

    .player-trigger::after {{
      content: "";
      position: absolute;
      inset: 0;
      background: linear-gradient(180deg, rgba(17, 24, 32, 0.08), rgba(17, 24, 32, 0.28));
      pointer-events: none;
    }}

    .play-badge {{
      position: absolute;
      left: 50%;
      top: 50%;
      z-index: 2;
      width: 64px;
      height: 44px;
      border-radius: 8px;
      background: rgba(15, 107, 87, 0.94);
      box-shadow: 0 10px 28px rgba(0, 0, 0, 0.24);
      transform: translate(-50%, -50%);
    }}

    .play-badge::before {{
      content: "";
      position: absolute;
      left: 26px;
      top: 12px;
      width: 0;
      height: 0;
      border-top: 10px solid transparent;
      border-bottom: 10px solid transparent;
      border-left: 16px solid #ffffff;
    }}

    .player-trigger:hover .play-badge,
    .player-trigger:focus-visible .play-badge {{
      background: var(--accent-strong);
    }}

    .player-message {{
      position: absolute;
      inset: 0;
      display: grid;
      place-items: center;
      padding: 18px;
      color: #ffffff;
      text-align: center;
      line-height: 1.5;
      background: #172026;
    }}

    .player-message code {{
      white-space: nowrap;
    }}

    .rank {{
      position: absolute;
      left: 10px;
      top: 10px;
      display: grid;
      place-items: center;
      width: 36px;
      height: 36px;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.92);
      color: var(--accent-strong);
      font-weight: 780;
    }}

    .video-body {{
      min-width: 0;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      gap: 18px;
    }}

    .meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px 12px;
      margin-bottom: 8px;
      color: var(--muted);
      font-size: 0.88rem;
      line-height: 1.4;
    }}

    .video h2 {{
      margin: 0;
      font-size: clamp(1.2rem, 3vw, 1.75rem);
      line-height: 1.18;
      letter-spacing: 0;
    }}

    .reason {{
      margin: 10px 0 0;
      color: var(--muted);
      line-height: 1.55;
    }}

    .actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
    }}

    .watch-link,
    .action-button {{
      min-height: 40px;
      border-radius: 6px;
      border: 1px solid var(--line);
      padding: 9px 13px;
      font: inherit;
      font-weight: 680;
      letter-spacing: 0;
      text-decoration: none;
      cursor: pointer;
    }}

    .watch-link {{
      background: var(--accent);
      border-color: var(--accent);
      color: #ffffff;
    }}

    .watch-link:hover {{
      background: var(--accent-strong);
    }}

    .action-button {{
      background: #ffffff;
      color: var(--text);
    }}

    .action-button[data-active="true"] {{
      background: var(--surface-strong);
      border-color: #9ab8ae;
      color: var(--accent-strong);
    }}

    .empty-state {{
      margin-top: 28px;
      padding: 28px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface);
    }}

    .empty-state h2 {{
      margin: 0 0 8px;
    }}

    .empty-state p {{
      margin: 0;
      color: var(--muted);
    }}

    @media (max-width: 760px) {{
      .page {{
        padding: 22px 14px 36px;
      }}

      .topbar,
      .video {{
        grid-template-columns: 1fr;
      }}

      .summary {{
        min-width: 0;
      }}

      .player,
      .player-trigger,
      .player-trigger img {{
        min-height: 190px;
      }}
    }}
  </style>
</head>
<body>
  <main class="page">
    <header class="topbar">
      <div>
        <p class="eyebrow">{escape(recommendation_date.strftime("%A, %B %d, %Y"))}</p>
        <h1>{escape(config.output.page_title)}</h1>
        <p class="subtitle">A finite YouTube feed for today, selected from your configured channels. Current interests: {escape(interests or "none configured")}.</p>
      </div>
      <div class="summary">
        <strong>{escape(count_label)}</strong>
        <span>Generated {escape(generated_at)}</span>
      </div>
    </header>

    <section class="feed" aria-label="Recommended videos">
      {items}
    </section>
  </main>

  <script>
    const storeKey = "yt-curator-actions";
    const state = JSON.parse(localStorage.getItem(storeKey) || "{{}}");

    function persist() {{
      localStorage.setItem(storeKey, JSON.stringify(state));
    }}

    function hydrateButtons() {{
      document.querySelectorAll("[data-video-id][data-action]").forEach((button) => {{
        const videoId = button.dataset.videoId;
        const action = button.dataset.action;
        const videoState = state[videoId] || {{}};
        button.dataset.active = videoState[action] ? "true" : "false";
      }});
    }}

    function embedUrlWithOrigin(url) {{
      if (window.location.protocol !== "http:" && window.location.protocol !== "https:") {{
        return url;
      }}
      const embedUrl = new URL(url);
      embedUrl.searchParams.set("origin", window.location.origin);
      return embedUrl.toString();
    }}

    function showLocalFileMessage(player) {{
      const message = document.createElement("div");
      message.className = "player-message";
      message.innerHTML = "Embedded playback needs a local server. Run <code>uv run python -m dashboard.youtube_curator serve</code> and open the printed URL.";
      player.replaceChildren(message);
      player.dataset.loaded = "true";
    }}

    document.addEventListener("click", (event) => {{
      const playerTrigger = event.target.closest("[data-embed-url]");
      if (playerTrigger) {{
        const article = playerTrigger.closest(".video");
        const player = article ? article.querySelector("[data-player]") : null;
        if (!player || player.dataset.loaded === "true") return;

        if (window.location.protocol === "file:") {{
          showLocalFileMessage(player);
          return;
        }}

        const iframe = document.createElement("iframe");
        iframe.src = embedUrlWithOrigin(playerTrigger.dataset.embedUrl);
        iframe.title = playerTrigger.dataset.embedTitle || "YouTube video player";
        iframe.loading = "lazy";
        iframe.allow = "accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share";
        iframe.referrerPolicy = "strict-origin-when-cross-origin";
        iframe.allowFullscreen = true;

        player.replaceChildren(iframe);
        player.dataset.loaded = "true";
        return;
      }}

      const button = event.target.closest("[data-video-id][data-action]");
      if (!button) return;
      const videoId = button.dataset.videoId;
      const action = button.dataset.action;
      state[videoId] = state[videoId] || {{}};
      state[videoId][action] = !state[videoId][action];
      persist();
      hydrateButtons();
    }});

    hydrateButtons();
  </script>
</body>
</html>
"""


def _video_item(rank: int, item: RankedVideo) -> str:
    video = item.video
    published = video.published_at.strftime("%b %d, %Y")
    duration = _format_duration(video.duration_seconds)
    views = _format_views(video.view_count)
    embed_url = _youtube_embed_url(video)
    meta = " &middot; ".join(
        escape(part)
        for part in [video.channel_title, published, duration, views]
        if part
    )
    return f"""
      <article class="video">
        <div class="player" data-player>
          <button class="player-trigger" type="button" data-embed-url="{escape(embed_url, quote=True)}" data-embed-title="{escape(video.title, quote=True)}" aria-label="Play {escape(video.title, quote=True)} on this page">
            <span class="rank">{rank}</span>
            <img src="{escape(video.thumbnail_url, quote=True)}" alt="" loading="lazy">
            <span class="play-badge" aria-hidden="true"></span>
          </button>
        </div>
        <div class="video-body">
          <div>
            <div class="meta">{meta}</div>
            <h2>{escape(video.title)}</h2>
            <p class="reason">{escape(item.reason)}</p>
          </div>
          <div class="actions">
            <button class="watch-link" type="button" data-embed-url="{escape(embed_url, quote=True)}" data-embed-title="{escape(video.title, quote=True)}">Play here</button>
            <a class="action-button" href="{escape(video.url, quote=True)}" target="_blank" rel="noreferrer">Open YouTube</a>
            <button class="action-button" type="button" data-video-id="{escape(video.video_id, quote=True)}" data-action="watched">Watched</button>
            <button class="action-button" type="button" data-video-id="{escape(video.video_id, quote=True)}" data-action="saved">Save</button>
            <button class="action-button" type="button" data-video-id="{escape(video.video_id, quote=True)}" data-action="less_like_this">Less like this</button>
          </div>
        </div>
      </article>
    """


def _youtube_embed_url(video: Video) -> str:
    return f"https://www.youtube-nocookie.com/embed/{quote(video.video_id, safe='')}?autoplay=1&rel=0"


def _format_duration(duration_seconds: int | None) -> str:
    if duration_seconds is None:
        return ""
    minutes = max(1, round(duration_seconds / 60))
    return f"{minutes} min"


def _format_views(view_count: int | None) -> str:
    if view_count is None:
        return ""
    if view_count >= 1_000_000:
        return f"{view_count / 1_000_000:.1f}M views"
    if view_count >= 1_000:
        return f"{view_count / 1_000:.0f}K views"
    return f"{view_count} views"
