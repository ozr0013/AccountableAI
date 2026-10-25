/** Live feed: /api/* goes to the Python session (dashboard/feed/serve.py). */
const FEED_URL = process.env.FEED_URL || "http://127.0.0.1:8765";
/** Google profile connector for dashboard sign-in (agreement_agent/server.py). */
const AGREEMENT_URL = process.env.AGREEMENT_URL || "http://127.0.0.1:8771";

const AGREEMENT_AGENT_URL = process.env.AGREEMENT_AGENT_URL || "http://127.0.0.1:8772";
const CHASER_AGENT_URL = process.env.CHASER_AGENT_URL || "http://127.0.0.1:8773";

export default {
  reactStrictMode: true,
  devIndicators: false,
  agentRules: false,
  async rewrites() {
    return [
      { source: "/agreement-agent-api/:path*", destination: `${AGREEMENT_AGENT_URL}/:path*` },
      { source: "/chaser-agent-api/:path*", destination: `${CHASER_AGENT_URL}/:path*` },
      { source: "/api/:path*", destination: `${FEED_URL}/api/:path*` },
      { source: "/agreements-api/:path*", destination: `${AGREEMENT_URL}/:path*` },
    ];
  },
};
