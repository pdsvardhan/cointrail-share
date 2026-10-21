import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  eslint: { ignoreDuringBuilds: true },
  output: "standalone",
  serverExternalPackages: ["better-sqlite3"],
};

export default nextConfig;
