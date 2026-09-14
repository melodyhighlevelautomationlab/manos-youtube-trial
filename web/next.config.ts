import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Stop `next dev` from regenerating AGENTS.md / CLAUDE.md in the repo.
  agentRules: false,
  // Trial task 2 moved to its own repo/deployment; keep the old link working.
  async redirects() {
    return [
      { source: "/version2", destination: "https://manos-youtube-trial2.vercel.app", permanent: false },
      { source: "/version2/:path*", destination: "https://manos-youtube-trial2.vercel.app/:path*", permanent: false },
    ];
  },
};

export default nextConfig;
