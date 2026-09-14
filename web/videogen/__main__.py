"""CLI: python -m videogen "Great Barrier Reef" --out reef.mp4 [--size 1280x720] [--voice en-GB-RyanNeural]"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .pipeline import DEFAULT_SIZE, DEFAULT_VOICE, PipelineError, generate


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Generate a short narrated video about a topic.")
    parser.add_argument("topic")
    parser.add_argument("--out", default=None, help="output mp4 (default: <topic>.mp4)")
    parser.add_argument("--size", default=f"{DEFAULT_SIZE[0]}x{DEFAULT_SIZE[1]}", help="WxH, e.g. 1280x720")
    parser.add_argument("--voice", default=DEFAULT_VOICE, help="edge-tts voice name")
    parser.add_argument("--workdir", default=None, help="keep intermediate files here")
    args = parser.parse_args(argv)

    w, h = (int(x) for x in args.size.lower().split("x"))
    out = Path(args.out or (args.topic.strip().replace(" ", "_") + ".mp4"))
    started = time.time()
    try:
        result = generate(args.topic, out, voice=args.voice, size=(w, h), workdir=Path(args.workdir) if args.workdir else None)
    except PipelineError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        sys.exit(1)
    meta = result.meta()
    meta["seconds_to_generate"] = round(time.time() - started, 1)
    meta["output"] = str(out.resolve())
    print(json.dumps(meta, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
