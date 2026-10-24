/** Live feed: /api/* goes to the Python session (dashboard/feed/serve.py). */
const FEED_URL = process.env.FEED_URL || "http://127.0.0.1:8765";

export default {
  reactStrictMode: true,
  devIndicators: false,
  agentRules: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${FEED_URL}/api/:path*` }];
  },
};
