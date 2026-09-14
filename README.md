# YouTube Video Info — Manos Corp trial task

A tiny end-to-end feature: a Python script pulls YouTube metadata with `yt-dlp`, and a Next.js page calls it through an API route and renders the result.

```
manos-youtube-trial/
├── python/
│   ├── fetch_video_info.py   # Part 1: URL in → clean JSON out (optional --out FILE)
│   └── requirements.txt
└── web/                      # Part 2: Next.js 16 + TypeScript + Tailwind v4
    ├── app/page.tsx                    # input + Fetch button + results card
    ├── app/api/video-info/route.ts     # POST/GET /api/video-info
    ├── lib/video-info.ts               # spawns the Python script, mock fallback
    └── .env.example
```

## Part 1 — Python

```bash
cd python
pip install -r requirements.txt
python fetch_video_info.py "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

Output:

```json
{
  "id": "dQw4w9WgXcQ",
  "title": "Rick Astley - Never Gonna Give You Up (Official Video) (4K Remaster)",
  "duration_seconds": 213,
  "duration": "3:33",
  "view_count": 1814720616,
  "upload_date": "2009-10-25",
  "channel": "Rick Astley",
  "thumbnail": "https://i.ytimg.com/vi_webp/dQw4w9WgXcQ/maxresdefault.webp",
  "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
}
```

Bonus: `--out video.json` also writes the JSON to a file.

Errors are JSON too, so a caller can always parse stdout:

| Case | stdout | exit code |
| --- | --- | --- |
| Not a YouTube URL | `{"error": "Please provide a valid YouTube video URL."}` | 1 |
| yt-dlp failure (private, removed, network) | `{"error": "<yt-dlp message>"}` | 2 |

Small details worth noting: `noplaylist` is on so a `watch?v=...&list=...` link returns one video, yt-dlp's own logging is silenced so stdout is pure JSON, and stdout is forced to UTF-8 so emoji titles don't crash on Windows consoles.

## Part 2 — Next.js

```bash
cd web
npm install
npm run dev
# open http://localhost:3000
```

The API route runs the Python script for real (no mocking needed if Python + yt-dlp are on PATH):

```
POST /api/video-info   { "url": "https://youtu.be/jNQXAC9IVRw" }
GET  /api/video-info?url=https://youtu.be/jNQXAC9IVRw

200 { "data": { ...same shape as the script... }, "source": "python" | "mock" }
400 { "error": "..." }   bad / non-YouTube URL
502 { "error": "..." }   yt-dlp could not fetch the video
504 { "error": "..." }   script timed out (30 s)
```

How the wiring works (`web/lib/video-info.ts`), in order of precedence:

1. **Mock** (`VIDEO_INFO_MOCK=1`): sample data, and the UI shows a "Mock data" badge instead of "Live via yt-dlp".
2. **Python over HTTP** (`VIDEO_INFO_PY_URL`, or automatic on Vercel): the route calls `web/api/yt.py`, a Vercel Python function that returns the same JSON shape (YouTube Data API when `YOUTUBE_API_KEY` is set, yt-dlp otherwise). This is how the deployed demo works, since Vercel's Node runtime can't spawn Python.
3. **Python child process** (local default): the route runs `execFile(python, [fetch_video_info.py, url])`, parses stdout as JSON, and maps the script's exit code to an HTTP status. If the interpreter isn't found it falls back to mock data.

All knobs are listed in `web/.env.example`.

## Deploying to Vercel

Live demo: **https://manos-youtube-trial.vercel.app**

The `web/` folder is the Vercel project root. Vercel builds the Next.js app and, because `web/api/yt.py` and `web/requirements.txt` exist, also deploys the Python function at `/api/yt`.

```bash
cd web
npx vercel login
npx vercel env add YOUTUBE_API_KEY production   # see below
npx vercel --prod
```

**Why the hosted function uses the YouTube Data API.** yt-dlp deploys and runs fine on Vercel, but YouTube answers every request from Vercel's IP range with "Sign in to confirm you're not a bot", for every yt-dlp player client I tried (`android_vr`, `ios`, `tv`, `mweb`, `web_embedded`, `android`; the fallback loop is still in `web/api/yt.py` behind `YTDLP_PLAYER_CLIENTS`). The alternatives were a residential proxy, a logged-in account's cookies (not something to commit to a trial repo), or the official YouTube Data API v3, which returns the same four fields from any IP with a free key (10,000 lookups/day). So `web/api/yt.py` uses the Data API when `YOUTUBE_API_KEY` is set and falls back to yt-dlp otherwise, and the UI badge says which one served the data. Locally, nothing changes: the route runs the yt-dlp script directly.

To create a key: Google Cloud Console → APIs & Services → Library → enable "YouTube Data API v3" → Credentials → Create credentials → API key. Restrict it to that API.

`web/api/yt.py` duplicates about 30 lines from `python/fetch_video_info.py` on purpose: Vercel only bundles files under the project root. In a real repo both would import a shared package.

## Trial task 2

The topic-to-video prototype lives in its own repo: https://github.com/melodyhighlevelautomationlab/manos-youtube-trial2 (live at https://manos-youtube-trial2.vercel.app). `/version2` on this site redirects there.

## Part 3 — Write-up

**AI tooling.** I used Claude Code (Anthropic's CLI agent) for this task. I gave it the brief and had it scaffold the project (`create-next-app`), write the first pass of the Python script, the API route and the page, and run the build/lint loop. I reviewed each file, decided the API contract (JSON-only stdout with exit codes 1/2 so the route can map them to 400/502), asked for the mock fallback to be explicit and visible in the UI rather than silent, and verified the script by hand against a normal watch URL, a `youtu.be` short link with a playlist param, a non-YouTube URL and a nonexistent video ID. I also chose to spawn the script as a child process rather than mock the route, since Python was available locally.

**What I'd change for production.**
- Don't shell out per request. Put the yt-dlp logic behind a small Python service (FastAPI) or a queue worker, and have the Next.js route call it over HTTP. That gives proper concurrency limits, health checks and independent deploys, and avoids needing Python in the Node container.
- Cache results by video ID (Redis or the Next.js data cache) with a TTL of a few hours. Titles and dates never change; view counts can be stale.
- Rate-limit the endpoint per IP/user and add request timeouts and retries with backoff. YouTube will throttle or block a busy IP, so plan for proxy rotation or cookies and treat yt-dlp as an upstream that fails.
- Validate input with a schema (zod), log structured errors with a request ID, and pin the yt-dlp version with a scheduled bump because YouTube changes break it regularly.
- Tests: unit tests for the URL validator and duration/date formatters, a contract test for the script's JSON shape, and a route test with the Python call stubbed.

**What broke / didn't work as expected.**
- `next build` failed type-checking on the first pass: a discriminated union for the API response didn't narrow the way I expected, and `NodeJS.ErrnoException` types `code` as a string so comparing it to the numeric exit code was flagged. Fixed by simplifying the union to `{ data, source } | { error }` with an `in` check, and typing the exec error explicitly.
- `yt-dlp` still printed `ERROR: ...` lines even with `quiet: true`, which would have polluted output if anything read stderr. Solved with a no-op logger so the script's stdout is the only channel.
- The first Vercel deploy returned live data once and then YouTube's bot check blocked 5 of the next 6 lookups (the one success was almost certainly the edge cache). I added a player-client fallback loop and retried with six alternate clients from a fresh deployment; all were blocked, each request taking ~8 s for seven failed attempts. Conclusion: the block is on the IP range, not the client. I shipped the hosted demo in mock mode first so it never showed an error, then added the YouTube Data API v3 path to the Python function (yt-dlp stays as the fallback and as the local path), which is what the live demo uses now.
- With more time I'd want to see how the child-process approach behaves under concurrent requests (each spawn is a fresh interpreter, roughly 1–2 s of overhead) and load-test it before deciding between the HTTP-service and queue options above.
