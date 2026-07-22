import type { NextConfig } from "next";
const config: NextConfig = { experimental: { serverActions: { bodySizeLimit: "1mb" } } };
export default config;
