import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Pin file tracing to this app; a stray lockfile elsewhere on the machine must not
  // change what the build considers the workspace root.
  outputFileTracingRoot: import.meta.dirname,
};

export default nextConfig;
