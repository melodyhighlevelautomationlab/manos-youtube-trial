"use client";

import Link from "next/link";
import { FormEvent, useEffect, useRef, useState } from "react";

type Meta = {
  title: string;
  source_url: string;
  duration_seconds: number;
  scenes: { text: string; image: string | null }[];
  voice: string;
  script_provider: "claude" | "wikipedia";
  bytes: number;
};

const STAGES = [
  "Looking up the topic…",
  "Writing the script…",
  "Fetching images…",
  "Recording the voiceover…",
  "Rendering captions and music…",
  "Encoding the mp4…",
];

export default function Version2() {
  const [topic, setTopic] = useState("");
  const [loading, setLoading] = useState(false);
  const [stage, setStage] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [meta, setMeta] = useState<Meta | null>(null);
  const startedAt = useRef<number>(0);

  // Fake-but-honest progress: one request, so we advance the label on elapsed time.
  useEffect(() => {
    if (!loading) return;
    const id = setInterval(() => {
      const elapsed = (Date.now() - startedAt.current) / 1000;
      setStage(Math.min(STAGES.length - 1, Math.floor(elapsed / 6)));
    }, 500);
    return () => clearInterval(id);
  }, [loading]);

  useEffect(() => () => void (videoUrl && URL.revokeObjectURL(videoUrl)), [videoUrl]);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = topic.trim();
    if (!trimmed) return;

    setLoading(true);
    setStage(0);
    setError(null);
    setMeta(null);
    if (videoUrl) URL.revokeObjectURL(videoUrl);
    setVideoUrl(null);
    startedAt.current = Date.now();

    try {
      const response = await fetch("/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ topic: trimmed }),
      });
      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as { error?: string } | null;
        setError(payload?.error ?? `Request failed with status ${response.status}.`);
        return;
      }
      const metaHeader = response.headers.get("X-Video-Meta");
      const blob = await response.blob();
      setVideoUrl(URL.createObjectURL(blob));
      if (metaHeader) {
        try {
          setMeta(JSON.parse(decodeURIComponent(metaHeader)) as Meta);
        } catch {
          /* metadata is optional */
        }
      }
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
              Topic to Video
            </h1>
            <Link
              href="/"
              className="text-xs text-zinc-500 underline decoration-zinc-300 underline-offset-2 hover:text-zinc-800 dark:hover:text-zinc-200"
            >
              Version 1: video info
            </Link>
          </div>
          <p className="text-sm text-zinc-600 dark:text-zinc-400">
            Type a topic and get back a short mp4 with images, a voiceover, captions and music.
            Takes about 30 to 60 seconds.
          </p>
        </header>

        <form onSubmit={onSubmit} className="flex flex-col gap-2 sm:flex-row">
          <label htmlFor="topic" className="sr-only">
            Topic
          </label>
          <input
            id="topic"
            type="text"
            required
            maxLength={80}
            placeholder="e.g. Great Barrier Reef, Ada Lovelace, the Moon landing"
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            disabled={loading}
            className="flex-1 rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 shadow-sm outline-none placeholder:text-zinc-400 focus:border-zinc-500 focus:ring-2 focus:ring-zinc-300 disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100 dark:focus:ring-zinc-600"
          />
          <button
            type="submit"
            disabled={loading || topic.trim() === ""}
            className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
          >
            {loading ? "Generating…" : "Generate"}
          </button>
        </form>

        {loading && (
          <div
            role="status"
            className="flex items-center gap-3 rounded-md border border-zinc-200 bg-white px-4 py-3 text-sm text-zinc-700 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300"
          >
            <span className="h-2 w-2 animate-pulse rounded-full bg-emerald-500" />
            {STAGES[stage]}
          </div>
        )}

        {error && (
          <div
            role="alert"
            className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-200"
          >
            {error}
          </div>
        )}

        {videoUrl && (
          <section className="overflow-hidden rounded-lg border border-zinc-200 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
            <video src={videoUrl} controls autoPlay className="aspect-video w-full bg-black" />
            <div className="space-y-4 p-5">
              {meta && (
                <div>
                  <h2 className="text-lg font-semibold leading-snug text-zinc-900 dark:text-zinc-50">
                    {meta.title}
                  </h2>
                  <p className="text-sm text-zinc-600 dark:text-zinc-400">
                    {meta.scenes.length} scenes · {Math.round(meta.duration_seconds)}s ·{" "}
                    {(meta.bytes / 1_000_000).toFixed(1)} MB · voice {meta.voice} · script from{" "}
                    {meta.script_provider === "claude" ? "Claude" : "Wikipedia"}
                  </p>
                </div>
              )}
              {meta && (
                <ol className="space-y-1 text-sm text-zinc-700 dark:text-zinc-300">
                  {meta.scenes.map((scene, i) => (
                    <li key={i} className="flex gap-2">
                      <span className="w-5 shrink-0 text-zinc-400">{i + 1}.</span>
                      <span>{scene.text}</span>
                    </li>
                  ))}
                </ol>
              )}
              <div className="flex flex-wrap items-center justify-between gap-2 border-t border-zinc-100 pt-4 text-xs text-zinc-500 dark:border-zinc-800">
                <a
                  href={videoUrl}
                  download={`${(meta?.title ?? topic).replace(/[^\w-]+/g, "_")}.mp4`}
                  className="underline decoration-zinc-300 underline-offset-2 hover:text-zinc-800 dark:hover:text-zinc-200"
                >
                  Download mp4
                </a>
                {meta && (
                  <a
                    href={meta.source_url}
                    target="_blank"
                    rel="noreferrer"
                    className="underline decoration-zinc-300 underline-offset-2 hover:text-zinc-800 dark:hover:text-zinc-200"
                  >
                    Source article
                  </a>
                )}
              </div>
            </div>
          </section>
        )}

        <p className="text-xs text-zinc-500">
          Prototype for Manos Corp trial task 2. Images and text come from Wikipedia, voice from
          edge-tts, music is generated procedurally, assembly by ffmpeg.{" "}
          <a
            href="/version2/sample.mp4"
            className="underline decoration-zinc-300 underline-offset-2 hover:text-zinc-800 dark:hover:text-zinc-200"
          >
            Sample output
          </a>
        </p>
      </div>
    </main>
  );
}
