import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Stop `next dev` from regenerating AGENTS.md / CLAUDE.md in the repo.
  agentRules: false,
};

export default nextConfig;
