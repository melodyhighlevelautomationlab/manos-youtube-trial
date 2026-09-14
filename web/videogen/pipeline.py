"""Topic -> mp4. The whole prototype pipeline in one module.

Stages (each is a plain function so they can be swapped or tested alone):
  1. resolve_topic     Wikipedia title search
  2. fetch_article     summary text + article images (no API key needed)
  3. write_script      Claude rewrites the summary into narration if ANTHROPIC_API_KEY is set,
                       otherwise the summary's own sentences are used
  4. synthesize        edge-tts neural voice per scene, with word-level timings
  5. render_frames     Pillow: image + synced caption chunk per frame
  6. make_music        procedurally generated ambient pad (pure Python, royalty-free by construction)
  7. assemble          ffmpeg concatenates frames + narration + music into an H.264 mp4

Runs locally (python -m videogen "topic") and inside a Vercel Python function (api/generate.py).
"""
from __future__ import annotations

import asyncio
import io
import json
import math
import os
import re
import shutil
import ssl
import struct
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import wave
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# --------------------------------------------------------------------------- config

USER_AGENT = "manos-video-prototype/0.1 (trial task; https://github.com/melodyhighlevelautomationlab/manos-youtube-trial)"
DEFAULT_VOICE = os.environ.get("VIDEO_VOICE", "en-US-AriaNeural")
DEFAULT_SIZE = (854, 480)
MAX_SCENES = int(os.environ.get("VIDEO_MAX_SCENES", "6"))
MAX_SENTENCE_CHARS = 200
SCENE_PAD_SECONDS = 0.4
FPS = 24
CAPTION_MAX_WORDS = 5
CAPTION_MAX_CHARS = 30
MUSIC_GAIN = 0.10


class PipelineError(Exception):
    """User-facing failure (bad topic, upstream down). Message is safe to show."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


@dataclass
class Scene:
    text: str
    image_path: Path | None = None
    audio_path: Path | None = None
    duration: float = 0.0
    words: list[tuple[float, str]] = field(default_factory=list)  # (start_seconds, word)


@dataclass
class VideoResult:
    path: Path
    title: str
    source_url: str
    duration_seconds: float
    scenes: list[Scene]
    voice: str
    script_provider: str

    def meta(self) -> dict:
        return {
            "title": self.title,
            "source_url": self.source_url,
            "duration_seconds": round(self.duration_seconds, 1),
            "scenes": [{"text": s.text, "image": s.image_path.name if s.image_path else None} for s in self.scenes],
            "voice": self.voice,
            "script_provider": self.script_provider,
            "bytes": self.path.stat().st_size,
        }


def log(msg: str) -> None:
    print(f"[videogen] {msg}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- http helpers

def _ssl_context() -> ssl.SSLContext | None:
    """Use certifi's CA bundle when available (some Windows Pythons ship a stale store)."""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return None


_CTX = _ssl_context()


def http_get(url: str, *, timeout: int = 20, headers: dict | None = None) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout, context=_CTX) as response:
        return response.read()


def http_json(url: str, **kwargs) -> dict:
    return json.loads(http_get(url, headers={"Accept": "application/json"}, **kwargs))


# --------------------------------------------------------------------------- 1 + 2: wikipedia

def resolve_topic(topic: str) -> str:
    """Return the Wikipedia page key for a free-text topic."""
    query = urllib.parse.quote(topic.strip())
    try:
        data = http_json(f"https://en.wikipedia.org/w/rest.php/v1/search/title?q={query}&limit=1")
    except urllib.error.URLError as exc:
        raise PipelineError(f"Could not reach Wikipedia: {exc.reason}") from exc
    pages = data.get("pages") or []
    if not pages:
        raise PipelineError(f'No Wikipedia article found for "{topic}". Try a more specific topic.', 404)
    return pages[0]["key"]


def fetch_article(key: str) -> tuple[str, str, str, list[str]]:
    """Return (title, extract, page_url, image_urls)."""
    summary = http_json(f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(key)}")
    if summary.get("type") == "disambiguation":
        raise PipelineError(f'"{summary.get("title")}" is ambiguous on Wikipedia. Try a more specific topic.', 404)
    title = summary.get("title") or key.replace("_", " ")
    page_url = (summary.get("content_urls") or {}).get("desktop", {}).get("page") or f"https://en.wikipedia.org/wiki/{key}"

    # The REST summary is only the first paragraph. The lead section (everything above the
    # first heading) is usually 3-5 paragraphs, which is enough for six narrated sentences.
    extract = ""
    try:
        params = urllib.parse.urlencode({
            "action": "query", "prop": "extracts", "exintro": 1, "explaintext": 1,
            "redirects": 1, "format": "json", "formatversion": 2, "titles": key.replace("_", " "),
        })
        pages = http_json(f"https://en.wikipedia.org/w/api.php?{params}").get("query", {}).get("pages") or []
        extract = (pages[0].get("extract") or "").strip() if pages else ""
    except Exception as exc:  # fall back to the shorter summary text
        log(f"extracts API failed: {exc}")
    if len(extract) < 200:
        extract = summary.get("extract") or extract

    urls: list[str] = []
    original = (summary.get("originalimage") or {}).get("source")
    if original:
        urls.append(original)
    try:
        media = http_json(f"https://en.wikipedia.org/api/rest_v1/page/media-list/{urllib.parse.quote(key)}")
    except Exception as exc:  # media list is optional
        log(f"media-list failed: {exc}")
        media = {}
    for item in media.get("items") or []:
        if item.get("type") != "image":
            continue
        name = (item.get("title") or "").lower()
        if name.endswith((".svg", ".gif")) or any(w in name for w in ("icon", "logo", "flag", "map", "symbol")):
            continue
        srcset = item.get("srcset") or []
        if not srcset:
            continue
        src = srcset[-1]["src"]
        if src.startswith("//"):
            src = "https:" + src
        # media-list gives small thumbs like /320px-Foo.jpg; ask for a bigger render.
        # Wikimedia only serves a whitelist of widths now (960 and 1280 are in it, 1024 is not).
        src = re.sub(r"/\d+px-", "/960px-", src)
        urls.append(src)
    # de-duplicate, keep order
    seen: set[str] = set()
    urls = [u for u in urls if not (u in seen or seen.add(u))]
    return title, extract, page_url, urls


def download_images(urls: list[str], workdir: Path, want: int) -> list[Path]:
    paths: list[Path] = []
    for i, url in enumerate(urls):
        if len(paths) >= want:
            break
        try:
            raw = http_get(url, timeout=15)
            img = Image.open(io.BytesIO(raw))
            img.load()
            if img.width < 320 or img.height < 200:
                continue
            path = workdir / f"img_{i:02d}.jpg"
            img.convert("RGB").save(path, "JPEG", quality=88)
            paths.append(path)
        except Exception as exc:
            log(f"skip image {url[:80]}: {exc}")
    return paths


# --------------------------------------------------------------------------- 3: script

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


def _clean_sentence(text: str) -> str:
    text = re.sub(r"\([^)]*\)", "", text)  # (pronunciation, born 1815, ...)
    text = re.sub(r"\[[^\]]*\]", "", text)  # [1]
    text = re.sub(r"\s+", " ", text).strip()
    return text


def script_from_extract(title: str, extract: str) -> list[str]:
    sentences = [_clean_sentence(s) for s in _SENTENCE_SPLIT.split(extract)]
    sentences = [s for s in sentences if 20 <= len(s) <= MAX_SENTENCE_CHARS]
    if not sentences:
        raise PipelineError(f'The Wikipedia summary for "{title}" is too short to narrate.', 422)
    return sentences[:MAX_SCENES]


def script_from_claude(title: str, extract: str, api_key: str) -> list[str]:
    """Ask Claude for a tighter narration. Falls back to the extract on any failure."""
    model = os.environ.get("VIDEO_SCRIPT_MODEL", "claude-sonnet-5")
    prompt = (
        f"Write a narration script for a {MAX_SCENES}-scene, 40-second explainer video about \"{title}\".\n"
        f"Use only facts from this text:\n\n{extract}\n\n"
        f"Rules: exactly {MAX_SCENES} sentences, each 10-22 words, spoken-word style, no lists, "
        "no intro like 'Welcome'. Reply with a JSON array of strings and nothing else."
    )
    body = json.dumps({"model": model, "max_tokens": 600, "messages": [{"role": "user", "content": prompt}]}).encode()
    request = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "User-Agent": USER_AGENT,
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(request, timeout=30, context=_CTX) as response:
        payload = json.load(response)
    text = "".join(block.get("text", "") for block in payload.get("content", []))
    match = re.search(r"\[.*\]", text, re.S)
    sentences = json.loads(match.group(0) if match else text)
    sentences = [_clean_sentence(str(s)) for s in sentences if str(s).strip()]
    if len(sentences) < 2:
        raise ValueError("Claude returned too few sentences")
    return sentences[:MAX_SCENES]


def write_script(title: str, extract: str) -> tuple[list[str], str]:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if api_key:
        try:
            return script_from_claude(title, extract, api_key), "claude"
        except Exception as exc:
            log(f"Claude script failed, using extract: {exc}")
    return script_from_extract(title, extract), "wikipedia"


# --------------------------------------------------------------------------- 4: voice

def ffmpeg_exe() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def run_ffmpeg(args: list[str]) -> None:
    proc = subprocess.run([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise PipelineError(f"ffmpeg failed: {proc.stderr.strip()[-400:]}", 500)


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


async def _tts_one(scene: Scene, index: int, voice: str, workdir: Path) -> None:
    import edge_tts

    mp3 = workdir / f"voice_{index:02d}.mp3"
    communicate = edge_tts.Communicate(scene.text, voice, boundary="WordBoundary")
    words: list[tuple[float, str]] = []
    with open(mp3, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                words.append((chunk["offset"] / 10_000_000, chunk["text"]))
    if mp3.stat().st_size == 0:
        raise PipelineError("The text-to-speech service returned no audio.", 502)
    wav = workdir / f"voice_{index:02d}.wav"
    # Pad a short silence after each sentence so scenes breathe; frames use the same length.
    run_ffmpeg(["-i", str(mp3), "-af", f"apad=pad_dur={SCENE_PAD_SECONDS}", "-ar", "24000", "-ac", "1", str(wav)])
    scene.audio_path = wav
    scene.duration = wav_duration(wav)
    scene.words = words


async def _synthesize_all(scenes: list[Scene], voice: str, workdir: Path) -> None:
    await asyncio.gather(*(_tts_one(s, i, voice, workdir) for i, s in enumerate(scenes)))


def synthesize(scenes: list[Scene], voice: str, workdir: Path) -> None:
    try:
        asyncio.run(_synthesize_all(scenes, voice, workdir))
    except PipelineError:
        raise
    except Exception as exc:
        raise PipelineError(f"Text-to-speech failed: {exc}", 502) from exc


# --------------------------------------------------------------------------- 5: frames

def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in ("DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf"):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)  # Pillow >= 10.1 bundles a scalable font


def _cover(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    w, h = size
    scale = max(w / img.width, h / img.height)
    resized = img.resize((math.ceil(img.width * scale), math.ceil(img.height * scale)), Image.LANCZOS)
    left, top = (resized.width - w) // 2, (resized.height - h) // 2
    return resized.crop((left, top, left + w, top + h))


def _caption_chunks(scene: Scene) -> list[tuple[float, str]]:
    """Split a scene's words into (start_offset, text) caption chunks using TTS timings."""
    if not scene.words:
        return [(0.0, scene.text)]
    chunks: list[tuple[float, str]] = []
    current: list[str] = []
    start = 0.0
    for offset, word in scene.words:
        if current and (len(current) >= CAPTION_MAX_WORDS or len(" ".join(current + [word])) > CAPTION_MAX_CHARS):
            chunks.append((start, " ".join(current)))
            current, start = [], offset
        if not current:
            start = offset
        current.append(word)
    if current:
        chunks.append((start, " ".join(current)))
    if chunks and chunks[0][0] > 0:
        chunks[0] = (0.0, chunks[0][1])
    return chunks


def _draw_frame(base: Image.Image, title: str, caption: str, size: tuple[int, int]) -> Image.Image:
    w, h = size
    frame = base.copy()
    # bottom gradient so captions stay legible on bright photos
    gradient = Image.new("L", (1, h))
    for y in range(h):
        t = max(0.0, (y - h * 0.55) / (h * 0.45))
        gradient.putpixel((0, y), int(200 * t * t))
    frame.paste(Image.new("RGB", (w, h), (0, 0, 0)), (0, 0), gradient.resize((w, h)))
    draw = ImageDraw.Draw(frame)

    # title tag, top-left
    tf = _font(max(16, h // 26))
    draw.text((w * 0.04, h * 0.05), title.upper(), font=tf, fill=(255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0))

    # caption, bottom-centre, wrapped
    cf = _font(max(22, h // 12))
    words, lines, line = caption.split(), [], ""
    for word in words:
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=cf) > w * 0.9 and line:
            lines.append(line)
            line = word
        else:
            line = trial
    if line:
        lines.append(line)
    line_h = cf.size * 1.25
    y = h * 0.88 - line_h * len(lines)
    for text in lines:
        tw = draw.textlength(text, font=cf)
        draw.text(((w - tw) / 2, y), text, font=cf, fill=(255, 255, 255), stroke_width=3, stroke_fill=(0, 0, 0))
        y += line_h
    return frame


def render_frames(scenes: list[Scene], images: list[Path], title: str, size: tuple[int, int], workdir: Path) -> list[tuple[Path, float]]:
    """Return [(frame_png, duration_seconds)] covering every scene end-to-end."""
    frames: list[tuple[Path, float]] = []
    fallback = Image.new("RGB", size, (24, 24, 32))
    n = 0
    for i, scene in enumerate(scenes):
        if images:
            scene.image_path = images[i % len(images)]
            base = _cover(Image.open(scene.image_path).convert("RGB"), size)
        else:
            base = fallback.filter(ImageFilter.GaussianBlur(2))
        chunks = _caption_chunks(scene)
        for j, (start, text) in enumerate(chunks):
            end = chunks[j + 1][0] if j + 1 < len(chunks) else scene.duration
            duration = max(0.15, end - start)
            path = workdir / f"frame_{n:03d}.png"
            _draw_frame(base, title, text, size).save(path, "PNG", compress_level=1)
            frames.append((path, duration))
            n += 1
    return frames


# --------------------------------------------------------------------------- 6: music

def make_music(duration: float, workdir: Path) -> Path:
    """Soft ambient pad: slow chord progression built from sines. Pure Python, no samples."""
    rate = 22050
    chords = [(220.0, 261.63, 329.63), (174.61, 220.0, 261.63), (196.0, 246.94, 293.66), (164.81, 196.0, 246.94)]  # Am F G Em
    bar = 4.0
    total = int(rate * (duration + 0.5))
    samples = bytearray()
    for n in range(total):
        t = n / rate
        chord = chords[int(t // bar) % len(chords)]
        pos = (t % bar) / bar
        env = min(1.0, pos * 4) * min(1.0, (1 - pos) * 4)  # attack/release inside each bar
        fade = min(1.0, t / 2.0, max(0.0, (duration - t) / 2.5))  # fade in/out
        value = 0.0
        for k, freq in enumerate(chord):
            value += math.sin(2 * math.pi * freq * t) * (0.6 if k else 1.0)
            value += 0.25 * math.sin(2 * math.pi * freq * 2 * t)  # gentle octave shimmer
        value *= 0.18 * env * fade
        samples += struct.pack("<h", int(max(-1.0, min(1.0, value)) * 32767))
    path = workdir / "music.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(samples))
    return path


# --------------------------------------------------------------------------- 7: assemble

def assemble(frames: list[tuple[Path, float]], scenes: list[Scene], music: Path, size: tuple[int, int], out: Path, workdir: Path) -> None:
    frames_txt = workdir / "frames.txt"
    with open(frames_txt, "w", encoding="utf-8") as f:
        for path, duration in frames:
            f.write(f"file '{path.as_posix()}'\nduration {duration:.3f}\n")
        f.write(f"file '{frames[-1][0].as_posix()}'\n")  # concat demuxer quirk: repeat last frame
    audio_txt = workdir / "audio.txt"
    with open(audio_txt, "w", encoding="utf-8") as f:
        for scene in scenes:
            f.write(f"file '{scene.audio_path.as_posix()}'\n")  # type: ignore[union-attr]
    w, h = size
    run_ffmpeg([
        "-f", "concat", "-safe", "0", "-i", str(frames_txt),
        "-f", "concat", "-safe", "0", "-i", str(audio_txt),
        "-i", str(music),
        "-filter_complex", f"[2:a]volume={MUSIC_GAIN}[m];[1:a][m]amix=inputs=2:duration=first:dropout_transition=3[a]",
        "-map", "0:v", "-map", "[a]",
        "-vf", f"scale={w}:{h},format=yuv420p", "-r", str(FPS),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "27",
        "-c:a", "aac", "-b:a", "96k", "-shortest", "-movflags", "+faststart",
        str(out),
    ])


# --------------------------------------------------------------------------- entry point

def generate(topic: str, out: Path, *, voice: str = DEFAULT_VOICE, size: tuple[int, int] = DEFAULT_SIZE, workdir: Path | None = None) -> VideoResult:
    topic = topic.strip()
    if not 2 <= len(topic) <= 80:
        raise PipelineError("Topic must be between 2 and 80 characters.", 400)
    workdir = workdir or Path(tempfile.mkdtemp(prefix="videogen_"))
    workdir.mkdir(parents=True, exist_ok=True)

    log(f"resolve {topic!r}")
    key = resolve_topic(topic)
    title, extract, page_url, image_urls = fetch_article(key)
    log(f"article {title!r}: {len(extract)} chars, {len(image_urls)} candidate images")

    sentences, provider = write_script(title, extract)
    scenes = [Scene(text=s) for s in sentences]
    log(f"script via {provider}: {len(scenes)} scenes")

    images = download_images(image_urls, workdir, want=len(scenes))
    log(f"images downloaded: {len(images)}")

    synthesize(scenes, voice, workdir)
    total = sum(s.duration for s in scenes)
    log(f"voice ok: {total:.1f}s")

    frames = render_frames(scenes, images, title, size, workdir)
    music = make_music(total, workdir)
    assemble(frames, scenes, music, size, out, workdir)
    log(f"done: {out} ({out.stat().st_size / 1e6:.2f} MB)")
    return VideoResult(out, title, page_url, total, scenes, voice, provider)
