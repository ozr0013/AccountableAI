/** Live feed: /api/* goes to the Python session (dashboard/feed/serve.py). */
const FEED_URL = process.env.FEED_URL || "http://127.0.0.1:8765";
/** Google profile connector for dashboard sign-in (agreement_agent/server.py). */
const AGREEMENT_URL = process.env.AGREEMENT_URL || "http://127.0.0.1:8771";

export default {
  reactStrictMode: true,
  devIndicators: false,
  agentRules: false,
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${FEED_URL}/api/:path*` },
      { source: "/agreements-api/:path*", destination: `${AGREEMENT_URL}/:path*` },
    ];
  },
};
