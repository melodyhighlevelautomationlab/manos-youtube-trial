"""Vercel Python serverless function: GET /api/yt?url=<youtube-url>

Exposes the same metadata extraction as python/fetch_video_info.py over HTTP so
the Next.js route can use it on hosts that have no Python interpreter (Vercel's
Node runtime can't spawn Python, but Vercel does run Python functions).

Providers, in order:
  1. YouTube Data API v3  when YOUTUBE_API_KEY is set. Works from any IP.
  2. yt-dlp               otherwise (or if the Data API call fails). Note that
                          YouTube bot-blocks yt-dlp from datacenter IP ranges
                          such as Vercel's, so on Vercel you want the key.

Note: Vercel only bundles files under the project root (web/), so the ~30 lines
of yt-dlp extraction/formatting logic are inlined here instead of importing from
python/. In a real codebase this would be a shared package.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlencode, urlparse

YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}


def is_youtube_url(url: str) -> bool:
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and parsed.netloc.lower() in YOUTUBE_HOSTS


def format_duration(seconds: int | float | None) -> str | None:
    if seconds is None:
        return None
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def format_upload_date(raw: str | None) -> str | None:
    if not raw or len(raw) != 8 or not raw.isdigit():
        return raw
    return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"


class _SilentLogger:
    def debug(self, msg): ...
    def info(self, msg): ...
    def warning(self, msg): ...
    def error(self, msg): ...


BOT_CHECK_MARKER = "not a bot"


def _fallback_clients() -> list[str]:
    """Comma-separated yt-dlp YouTube player clients to try when the default one is blocked.

    YouTube's "confirm you're not a bot" check mostly targets the default web client from
    datacenter IPs (like serverless functions); other clients often still work.
    Example: YTDLP_PLAYER_CLIENTS=android_vr,ios,tv
    """
    raw = os.environ.get("YTDLP_PLAYER_CLIENTS", "")
    return [c.strip() for c in raw.split(",") if c.strip()]


def _extract(url: str, player_clients: list[str] | None) -> dict:
    import yt_dlp

    options: dict = {
        "quiet": True,
        "no_warnings": True,
        "logger": _SilentLogger(),
        "skip_download": True,
        "noplaylist": True,
    }
    if player_clients:
        options["extractor_args"] = {"youtube": {"player_client": player_clients}}
    with yt_dlp.YoutubeDL(options) as ydl:
        return ydl.extract_info(url, download=False)


def fetch_via_ytdlp(url: str) -> dict:
    attempts: list[list[str] | None] = [None] + [[c] for c in _fallback_clients()]
    last_error: Exception | None = None
    info = None
    for clients in attempts:
        try:
            info = _extract(url, clients)
            print(f"[yt] ok via player_client={clients or 'default'}", file=sys.stderr)
            break
        except Exception as exc:
            last_error = exc
            if BOT_CHECK_MARKER not in str(exc):
                raise
            print(f"[yt] blocked via player_client={clients or 'default'}", file=sys.stderr)
    if info is None:
        assert last_error is not None
        raise last_error

    return {
        "id": info.get("id"),
        "title": info.get("title"),
        "duration_seconds": info.get("duration"),
        "duration": format_duration(info.get("duration")),
        "view_count": info.get("view_count"),
        "upload_date": format_upload_date(info.get("upload_date")),
        "channel": info.get("channel") or info.get("uploader"),
        "thumbnail": info.get("thumbnail"),
        "url": info.get("webpage_url") or url,
        "provider": "yt-dlp",
    }


# --- YouTube Data API v3 -----------------------------------------------------

VIDEO_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")
ISO_DURATION_RE = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?")


class VideoNotFound(Exception):
    """The Data API returned no item: wrong ID, private, or deleted video."""


def extract_video_id(url: str) -> str | None:
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower()
    candidate: str | None = None
    if host in {"youtu.be", "www.youtu.be"}:
        candidate = parsed.path.strip("/").split("/")[0] if parsed.path.strip("/") else None
    else:
        query = parse_qs(parsed.query)
        if query.get("v"):
            candidate = query["v"][0]
        else:
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) >= 2 and parts[0] in {"shorts", "embed", "live", "v"}:
                candidate = parts[1]
    return candidate if candidate and VIDEO_ID_RE.fullmatch(candidate) else None


def parse_iso_duration(value: str | None) -> int | None:
    """'PT1H2M3S' -> 3723. Live streams report 'P0D' -> 0."""
    if not value:
        return None
    match = ISO_DURATION_RE.fullmatch(value)
    if not match:
        return None
    days, hours, minutes, seconds = (int(x) if x else 0 for x in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def fetch_via_data_api(url: str, api_key: str) -> dict:
    video_id = extract_video_id(url)
    if not video_id:
        raise VideoNotFound("Could not find a video ID in that URL.")

    params = urlencode(
        {"part": "snippet,contentDetails,statistics", "id": video_id, "key": api_key}
    )
    request = urllib.request.Request(
        f"https://www.googleapis.com/youtube/v3/videos?{params}",
        headers={"Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.load(exc)["error"]["message"]
        except Exception:
            detail = exc.reason
        raise RuntimeError(f"YouTube Data API error {exc.code}: {detail}") from exc

    items = payload.get("items") or []
    if not items:
        raise VideoNotFound("Video not found. It may be private, deleted, or the ID is wrong.")

    item = items[0]
    snippet = item.get("snippet", {})
    stats = item.get("statistics", {})
    thumbs = snippet.get("thumbnails", {})
    thumbnail = next(
        (thumbs[k]["url"] for k in ("maxres", "standard", "high", "medium", "default") if k in thumbs),
        None,
    )
    seconds = parse_iso_duration(item.get("contentDetails", {}).get("duration"))
    published = snippet.get("publishedAt")  # e.g. 2009-10-25T06:57:33Z
    views = stats.get("viewCount")  # absent when the channel hides view counts

    return {
        "id": item.get("id"),
        "title": snippet.get("title"),
        "duration_seconds": seconds,
        "duration": format_duration(seconds),
        "view_count": int(views) if views is not None else None,
        "upload_date": published[:10] if published else None,
        "channel": snippet.get("channelTitle"),
        "thumbnail": thumbnail,
        "url": f"https://www.youtube.com/watch?v={item.get('id')}",
        "provider": "youtube-data-api",
    }


def fetch_metadata(url: str) -> dict:
    api_key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if api_key:
        try:
            return fetch_via_data_api(url, api_key)
        except VideoNotFound:
            raise
        except Exception as exc:
            print(f"[yt] Data API failed, falling back to yt-dlp: {exc}", file=sys.stderr)
    return fetch_via_ytdlp(url)


class handler(BaseHTTPRequestHandler):  # Vercel looks for a class named `handler`
    def do_GET(self) -> None:
        query = parse_qs(urlparse(self.path).query)
        url = (query.get("url") or [""])[0].strip()

        if not is_youtube_url(url):
            self._json(400, {"error": "Please provide a valid YouTube video URL."})
            return

        try:
            data = fetch_metadata(url)
        except VideoNotFound as exc:
            self._json(404, {"error": str(exc)})
            return
        except Exception as exc:  # yt_dlp.utils.DownloadError and friends
            message = str(exc).replace("ERROR: ", "", 1).strip()
            self._json(502, {"error": message or "Could not fetch video metadata."})
            return

        self._json(200, data)

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if status == 200:
            # Metadata barely changes; let Vercel's edge cache absorb repeat lookups.
            self.send_header("Cache-Control", "s-maxage=3600, stale-while-revalidate=86400")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:  # keep function logs quiet
        return
