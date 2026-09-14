"""Vercel Python function: topic in, mp4 out.

  POST /api/generate   body {"topic": "Great Barrier Reef"}
  GET  /api/generate?topic=Great%20Barrier%20Reef

Success: 200, Content-Type video/mp4, body is the file, X-Video-Meta header holds
URL-encoded JSON (title, scenes, duration, provider). Errors: JSON {"error": ...}.

The pipeline itself lives in videogen/pipeline.py so the CLI and this function share it.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.parse
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # make `videogen` importable

from videogen import PipelineError, generate  # noqa: E402


def _run(topic: str) -> tuple[int, dict, bytes]:
    workdir = Path(tempfile.mkdtemp(prefix="videogen_", dir="/tmp" if os.path.isdir("/tmp") else None))
    out = workdir / "video.mp4"
    try:
        result = generate(topic, out, workdir=workdir)
    except PipelineError as exc:
        return exc.status, {"Content-Type": "application/json; charset=utf-8"}, json.dumps({"error": str(exc)}).encode()
    except Exception as exc:  # anything unexpected: still answer with JSON
        print(f"[generate] unexpected: {exc!r}", file=sys.stderr)
        return 500, {"Content-Type": "application/json; charset=utf-8"}, json.dumps({"error": "Video generation failed unexpectedly."}).encode()

    data = out.read_bytes()
    meta = urllib.parse.quote(json.dumps(result.meta(), ensure_ascii=False), safe="")
    filename = "".join(c if c.isalnum() or c in "-_" else "_" for c in result.title)[:60] or "video"
    return 200, {
        "Content-Type": "video/mp4",
        "Content-Disposition": f'inline; filename="{filename}.mp4"',
        "X-Video-Meta": meta,
        "Cache-Control": "no-store",
    }, data


class handler(BaseHTTPRequestHandler):  # Vercel looks for a class named `handler`
    def do_GET(self) -> None:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        self._respond((query.get("topic") or [""])[0])

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            topic = body.get("topic", "")
        except (ValueError, AttributeError):
            self._send(400, {"Content-Type": "application/json"}, b'{"error": "Body must be JSON with a `topic` field."}')
            return
        self._respond(topic if isinstance(topic, str) else "")

    def _respond(self, topic: str) -> None:
        if not topic.strip():
            self._send(400, {"Content-Type": "application/json"}, b'{"error": "Please provide a topic."}')
            return
        status, headers, body = _run(topic)
        self._send(status, headers, body)

    def _send(self, status: int, headers: dict, body: bytes) -> None:
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        return
