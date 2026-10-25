/** Where the browser reaches the agent API (chaser_agent, :8773). Same origin, through the
 *  dashboard's /chaser-agent-api rewrite (next.config.mjs), so a judge's browser never needs
 *  localhost. NEXT_PUBLIC_AGENTS_URL overrides it. */
export const AGENTS_URL = process.env.NEXT_PUBLIC_AGENTS_URL || "/chaser-agent-api";
