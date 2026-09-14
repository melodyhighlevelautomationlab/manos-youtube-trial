"use client";

import Image from "next/image";
import { FormEvent, useState } from "react";
import type { VideoInfo, VideoInfoSource } from "@/lib/video-info";

type ApiResponse = { data: VideoInfo; source: VideoInfoSource } | { error: string };

const numberFormat = new Intl.NumberFormat("en-US");

function formatViews(count: number | null) {
  return count === null ? "—" : numberFormat.format(count);
}

function formatUploadDate(iso: string | null) {
  if (!iso) return "—";
  const date = new Date(`${iso}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString("en-US", {
    year: "numeric",
    month: "long",
    day: "numeric",
    timeZone: "UTC",
  });
}

export default function Home() {
  const [url, setUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ info: VideoInfo; source: VideoInfoSource } | null>(
    null,
  );

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = url.trim();
    if (!trimmed) return;

    setLoading(true);
    setError(null);
    setResult(null);

    try {
      const response = await fetch("/api/video-info", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: trimmed }),
      });
      const payload = (await response.json()) as ApiResponse;
      if (!response.ok || !("data" in payload)) {
        setError(
          "error" in payload ? payload.error : `Request failed with status ${response.status}.`,
        );
        return;
      }
      setResult({ info: payload.data, source: payload.source });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Network error.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="flex flex-1 items-start justify-center bg-zinc-50 px-4 py-16 dark:bg-zinc-950">
      <div className="w-full max-w-2xl space-y-6">
        <header className="space-y-1">
          <div className="flex items-center justify-between gap-4">
            <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
              YouTube Video Info
            </h1>
            <a
              href="/version2"
              className="text-xs text-zinc-500 underline decoration-zinc-300 underline-offset-2 hover:text-zinc-800 dark:hover:text-zinc-200"
            >
              Version 2: topic to video
            </a>
          </div>
          <p className="text-sm text-zinc-600 dark:text-zinc-400">
            Paste a YouTube link and we&apos;ll pull its title, duration, views and upload date.
          </p>
        </header>

        <form onSubmit={onSubmit} className="flex flex-col gap-2 sm:flex-row">
          <label htmlFor="url" className="sr-only">
            YouTube URL
          </label>
          <input
            id="url"
            type="url"
            inputMode="url"
            required
            placeholder="https://www.youtube.com/watch?v=dQw4w9WgXcQ"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            disabled={loading}
            className="flex-1 rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 shadow-sm outline-none placeholder:text-zinc-400 focus:border-zinc-500 focus:ring-2 focus:ring-zinc-300 disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100 dark:focus:ring-zinc-600"
          />
          <button
            type="submit"
            disabled={loading || url.trim() === ""}
            className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
          >
            {loading ? "Fetching…" : "Fetch"}
          </button>
        </form>

        {error && (
          <div
            role="alert"
            className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-200"
          >
            {error}
          </div>
        )}

        {result && (
          <section className="overflow-hidden rounded-lg border border-zinc-200 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
            {result.info.thumbnail && (
              <div className="relative aspect-video w-full bg-zinc-100 dark:bg-zinc-800">
                <Image
                  src={result.info.thumbnail}
                  alt=""
                  fill
                  sizes="(max-width: 672px) 100vw, 672px"
                  className="object-cover"
                  unoptimized
                />
              </div>
            )}

            <div className="space-y-4 p-5">
              <div>
                <h2 className="text-lg font-semibold leading-snug text-zinc-900 dark:text-zinc-50">
                  {result.info.title ?? "Untitled"}
                </h2>
                {result.info.channel && (
                  <p className="text-sm text-zinc-600 dark:text-zinc-400">{result.info.channel}</p>
                )}
              </div>

              <dl className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-3">
                <div>
                  <dt className="text-xs uppercase tracking-wide text-zinc-500">Duration</dt>
                  <dd className="mt-0.5 font-medium text-zinc-900 dark:text-zinc-100">
                    {result.info.duration ?? "—"}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-zinc-500">Views</dt>
                  <dd className="mt-0.5 font-medium text-zinc-900 dark:text-zinc-100">
                    {formatViews(result.info.view_count)}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-zinc-500">Uploaded</dt>
                  <dd className="mt-0.5 font-medium text-zinc-900 dark:text-zinc-100">
                    {formatUploadDate(result.info.upload_date)}
                  </dd>
                </div>
              </dl>

              <div className="flex flex-wrap items-center justify-between gap-2 border-t border-zinc-100 pt-4 text-xs text-zinc-500 dark:border-zinc-800">
                <a
                  href={result.info.url}
                  target="_blank"
                  rel="noreferrer"
                  className="underline decoration-zinc-300 underline-offset-2 hover:text-zinc-800 dark:hover:text-zinc-200"
                >
                  Open on YouTube
                </a>
                <span
                  className={
                    result.source === "python"
                      ? "rounded-full bg-emerald-50 px-2 py-0.5 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300"
                      : "rounded-full bg-amber-50 px-2 py-0.5 text-amber-700 dark:bg-amber-950 dark:text-amber-300"
                  }
                >
                  {result.source === "mock"
                    ? "Mock data"
                    : result.info.provider === "youtube-data-api"
                      ? "Live via YouTube Data API"
                      : "Live via yt-dlp"}
                </span>
              </div>
            </div>
          </section>
        )}
      </div>
    </main>
  );
}
