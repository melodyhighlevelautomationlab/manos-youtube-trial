import { NextResponse } from "next/server";
import { fetchVideoInfo, isYouTubeUrl, VideoInfoError } from "@/lib/video-info";

// Needs Node APIs (child_process), so opt out of the Edge runtime.
export const runtime = "nodejs";

async function handle(rawUrl: unknown) {
  if (typeof rawUrl !== "string" || rawUrl.trim() === "") {
    return NextResponse.json({ error: "Please provide a YouTube URL." }, { status: 400 });
  }
  const url = rawUrl.trim();
  if (!isYouTubeUrl(url)) {
    return NextResponse.json(
      { error: "That doesn't look like a YouTube URL (expected youtube.com or youtu.be)." },
      { status: 400 },
    );
  }

  try {
    const { info, source } = await fetchVideoInfo(url);
    return NextResponse.json({ data: info, source });
  } catch (err) {
    if (err instanceof VideoInfoError) {
      return NextResponse.json({ error: err.message }, { status: err.status });
    }
    console.error("[api/video-info] unexpected error:", err);
    return NextResponse.json({ error: "Unexpected server error." }, { status: 500 });
  }
}

/** POST /api/video-info  body: { "url": "https://www.youtube.com/watch?v=..." } */
export async function POST(request: Request) {
  let body: { url?: unknown };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json(
      { error: "Request body must be JSON with a `url` field." },
      { status: 400 },
    );
  }
  return handle(body.url);
}

/** GET /api/video-info?url=...  (handy for testing in a browser or with curl) */
export async function GET(request: Request) {
  return handle(new URL(request.url).searchParams.get("url"));
}
