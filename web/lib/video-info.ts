import path from "node:path";
import { promisify } from "node:util";

/**
 * child_process is imported lazily so this module also bundles on runtimes
 * without it (Cloudflare Workers via OpenNext), where the route runs in mock mode.
 */
async function execFileAsync(
  file: string,
  args: string[],
  options: { timeout: number; maxBuffer: number; windowsHide: boolean; env: NodeJS.ProcessEnv },
): Promise<{ stdout: string; stderr: string }> {
  const { execFile } = await import("node:child_process");
  return promisify(execFile)(file, args, { ...options, encoding: "utf8" });
}

/** Shape returned by python/fetch_video_info.py (and by the mock). */
export type VideoInfo = {
  id: string | null;
  title: string | null;
  duration_seconds: number | null;
  duration: string | null;
  view_count: number | null;
  upload_date: string | null; // ISO YYYY-MM-DD
  channel: string | null;
  thumbnail: string | null;
  url: string;
  /** Which upstream produced the data. Absent from the CLI script (always yt-dlp) and the mock. */
  provider?: "yt-dlp" | "youtube-data-api";
};

export type VideoInfoSource = "python" | "mock";

export class VideoInfoError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "VideoInfoError";
  }
}

const YOUTUBE_HOSTS = new Set([
  "youtube.com",
  "www.youtube.com",
  "m.youtube.com",
  "music.youtube.com",
  "youtu.be",
  "www.youtu.be",
]);

export function isYouTubeUrl(value: string): boolean {
  try {
    const parsed = new URL(value.trim());
    return (
      (parsed.protocol === "http:" || parsed.protocol === "https:") &&
      YOUTUBE_HOSTS.has(parsed.hostname.toLowerCase())
    );
  } catch {
    return false;
  }
}

/** Sample payload used when VIDEO_INFO_MOCK=1 or Python is unavailable. */
export const MOCK_VIDEO_INFO: VideoInfo = {
  id: "dQw4w9WgXcQ",
  title: "Rick Astley - Never Gonna Give You Up (Official Video) (4K Remaster)",
  duration_seconds: 213,
  duration: "3:33",
  view_count: 1_814_720_616,
  upload_date: "2009-10-25",
  channel: "Rick Astley",
  thumbnail: "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
  url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
};

/** Shape of the rejection from promisified execFile: exit code (number) or errno string (e.g. "ENOENT"). */
type ExecError = Error & {
  stdout?: string;
  stderr?: string;
  killed?: boolean;
  code?: number | string;
};

function tryParseScriptError(stdout: string | undefined): string | null {
  if (!stdout) return null;
  try {
    const parsed = JSON.parse(stdout) as { error?: unknown };
    return typeof parsed.error === "string" ? parsed.error : null;
  } catch {
    return null;
  }
}

/** Where the Python HTTP function lives, if we should use one instead of spawning a process. */
function resolvePythonServiceUrl(): string | null {
  if (process.env.VIDEO_INFO_PY_URL) return process.env.VIDEO_INFO_PY_URL;
  // On Vercel, web/api/yt.py is deployed as a Python function next to this app.
  if (process.env.VERCEL) {
    const host =
      process.env.VERCEL_ENV === "production"
        ? (process.env.VERCEL_PROJECT_PRODUCTION_URL ?? process.env.VERCEL_URL)
        : process.env.VERCEL_URL;
    if (host) return `https://${host}/api/yt`;
  }
  return null;
}

async function fetchFromPythonService(
  endpoint: string,
  url: string,
): Promise<{ info: VideoInfo; source: VideoInfoSource }> {
  let response: Response;
  try {
    response = await fetch(`${endpoint}?url=${encodeURIComponent(url)}`, {
      signal: AbortSignal.timeout(30_000),
      cache: "no-store",
    });
  } catch (err) {
    const timedOut = err instanceof Error && err.name === "TimeoutError";
    throw new VideoInfoError(
      timedOut
        ? "Timed out while fetching metadata from YouTube."
        : "Could not reach the metadata service.",
      timedOut ? 504 : 502,
    );
  }

  const body = (await response.json().catch(() => null)) as
    | (VideoInfo & { error?: undefined })
    | { error: string }
    | null;
  if (!response.ok || body === null || typeof body.error === "string") {
    const message =
      body !== null && typeof body.error === "string"
        ? body.error
        : `Metadata service returned ${response.status}.`;
    const status = response.status === 400 || response.status === 404 ? response.status : 502;
    throw new VideoInfoError(message, status);
  }
  return { info: body, source: "python" };
}

/**
 * Fetch metadata for a YouTube URL. Three modes, checked in order:
 *
 *   1. VIDEO_INFO_MOCK=1          return sample data, no Python involved
 *   2. Python over HTTP           VIDEO_INFO_PY_URL, or automatically on Vercel (web/api/yt.py)
 *   3. Python as a child process  runs ../python/fetch_video_info.py locally
 *        PYTHON_BIN                    interpreter (default: `python` on Windows, `python3` elsewhere)
 *        PYTHON_SCRIPT                 absolute path to the script
 *        VIDEO_INFO_FALLBACK_TO_MOCK=0 fail instead of returning sample data when Python is missing
 */
export async function fetchVideoInfo(
  url: string,
): Promise<{ info: VideoInfo; source: VideoInfoSource }> {
  if (process.env.VIDEO_INFO_MOCK === "1") {
    return { info: { ...MOCK_VIDEO_INFO, url }, source: "mock" };
  }

  const serviceUrl = resolvePythonServiceUrl();
  if (serviceUrl) {
    return fetchFromPythonService(serviceUrl, url);
  }

  const pythonBin =
    process.env.PYTHON_BIN ?? (process.platform === "win32" ? "python" : "python3");
  const script =
    process.env.PYTHON_SCRIPT ??
    path.resolve(process.cwd(), "..", "python", "fetch_video_info.py");

  try {
    const { stdout } = await execFileAsync(pythonBin, [script, url], {
      timeout: 30_000,
      maxBuffer: 1024 * 1024,
      windowsHide: true,
      env: { ...process.env, PYTHONIOENCODING: "utf-8" },
    });
    return { info: JSON.parse(stdout) as VideoInfo, source: "python" };
  } catch (err) {
    const error = err as ExecError;

    // Interpreter not found on PATH.
    if (error.code === "ENOENT") {
      if (process.env.VIDEO_INFO_FALLBACK_TO_MOCK !== "0") {
        console.warn(
          `[video-info] "${pythonBin}" not found; returning mock data. Set PYTHON_BIN or VIDEO_INFO_FALLBACK_TO_MOCK=0.`,
        );
        return { info: { ...MOCK_VIDEO_INFO, url }, source: "mock" };
      }
      throw new VideoInfoError(
        `Python interpreter "${pythonBin}" was not found. Set PYTHON_BIN in .env.local.`,
        500,
      );
    }

    if (error.killed) {
      throw new VideoInfoError("Timed out while fetching metadata from YouTube.", 504);
    }

    // Script exited non-zero: it prints {"error": "..."} on stdout.
    const scriptMessage = tryParseScriptError(error.stdout);
    if (scriptMessage) {
      // Exit code 1 = bad input (client's fault), 2 = yt-dlp failure (upstream).
      throw new VideoInfoError(scriptMessage, error.code === 1 ? 400 : 502);
    }

    console.error("[video-info] script failed:", error.stderr || error.message);
    throw new VideoInfoError("The metadata script failed unexpectedly.", 500);
  }
}
