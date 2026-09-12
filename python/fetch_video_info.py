#!/usr/bin/env python3
"""Fetch basic YouTube video metadata with yt-dlp and print it as clean JSON.

Usage:
    python fetch_video_info.py <youtube_url>
    python fetch_video_info.py <youtube_url> --out video.json   # bonus: save to file

Output (stdout, always JSON):
    success -> {"id": ..., "title": ..., "duration_seconds": ..., "duration": ...,
                "view_count": ..., "upload_date": "YYYY-MM-DD", ...}
    failure -> {"error": "<message>"}

Exit codes: 0 = ok, 1 = bad input, 2 = yt-dlp could not fetch the video.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

# Make sure emoji / non-ASCII titles don't crash on Windows consoles (cp1252).
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:  # very old Pythons
        pass

YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}


def emit(payload: dict, code: int = 0) -> None:
    """Print a JSON object to stdout and exit with the given code."""
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    sys.exit(code)


def is_youtube_url(url: str) -> bool:
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and parsed.netloc.lower() in YOUTUBE_HOSTS


def format_duration(seconds: int | float | None) -> str | None:
    """1234 -> '20:34', 3661 -> '1:01:01'."""
    if seconds is None:
        return None
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_upload_date(raw: str | None) -> str | None:
    """yt-dlp gives 'YYYYMMDD'; return ISO 'YYYY-MM-DD'."""
    if not raw or len(raw) != 8 or not raw.isdigit():
        return raw
    return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"


def fetch_metadata(url: str) -> dict:
    """Run yt-dlp in metadata-only mode and normalise the fields we care about."""
    import yt_dlp  # imported lazily so a missing install gives a clean JSON error

    class _SilentLogger:
        """Swallow yt-dlp's own log lines so stdout stays pure JSON."""

        def debug(self, msg): ...
        def info(self, msg): ...
        def warning(self, msg): ...
        def error(self, msg): ...

    options = {
        "quiet": True,
        "no_warnings": True,
        "logger": _SilentLogger(),
        "skip_download": True,
        "noplaylist": True,  # a watch URL with &list=... should still return one video
        "extract_flat": False,
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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Fetch YouTube video metadata as JSON.")
    parser.add_argument("url", help="YouTube video URL (youtube.com/watch?v=... or youtu.be/...)")
    parser.add_argument("--out", metavar="FILE", help="Also save the JSON to this file (bonus)")
    args = parser.parse_args(argv)

    if not is_youtube_url(args.url):
        emit({"error": "Please provide a valid YouTube video URL."}, code=1)

    try:
        data = fetch_metadata(args.url)
    except ImportError:
        emit({"error": "yt-dlp is not installed. Run: pip install -r requirements.txt"}, code=2)
    except Exception as exc:  # yt_dlp.utils.DownloadError and friends
        message = str(exc).replace("ERROR: ", "", 1).strip()
        emit({"error": message or "Could not fetch video metadata."}, code=2)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Saved metadata to {out_path.resolve()}", file=sys.stderr)

    emit(data)


if __name__ == "__main__":
    main()
