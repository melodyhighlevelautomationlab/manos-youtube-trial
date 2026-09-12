import { execFile } from "node:child_process";
import path from "node:path";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

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

/**
 * Fetch metadata for a YouTube URL.
 *
 * Runs `python/fetch_video_info.py` as a child process and parses its JSON
 * stdout. Configure with:
 *   PYTHON_BIN                    interpreter (default: `python` on Windows, `python3` elsewhere)
 *   PYTHON_SCRIPT                 absolute path to the script (default: ../python/fetch_video_info.py)
 *   VIDEO_INFO_MOCK=1             skip Python entirely and return sample data
 *   VIDEO_INFO_FALLBACK_TO_MOCK=0 fail instead of returning sample data when Python is missing
 */
export async function fetchVideoInfo(
  url: string,
): Promise<{ info: VideoInfo; source: VideoInfoSource }> {
  if (process.env.VIDEO_INFO_MOCK === "1") {
    return { info: { ...MOCK_VIDEO_INFO, url }, source: "mock" };
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
