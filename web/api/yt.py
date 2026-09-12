"""Vercel Python serverless function: GET /api/yt?url=<youtube-url>

Exposes the same yt-dlp extraction as python/fetch_video_info.py over HTTP so
the Next.js route can use it on hosts that have no Python interpreter (Vercel's
Node runtime can't spawn Python, but Vercel does run Python functions).

Note: Vercel only bundles files under the project root (web/), so the ~30 lines
of extraction/formatting logic are inlined here instead of importing from
python/. In a real codebase this would be a shared package.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

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


def fetch_metadata(url: str) -> dict:
    import yt_dlp

    options = {
        "quiet": True,
        "no_warnings": True,
        "logger": _SilentLogger(),
        "skip_download": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)

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
    }


class handler(BaseHTTPRequestHandler):  # Vercel looks for a class named `handler`
    def do_GET(self) -> None:
        query = parse_qs(urlparse(self.path).query)
        url = (query.get("url") or [""])[0].strip()

        if not is_youtube_url(url):
            self._json(400, {"error": "Please provide a valid YouTube video URL."})
            return

        try:
            data = fetch_metadata(url)
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
