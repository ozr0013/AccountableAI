# Ask with Claude streaming

Set `ANTHROPIC_API_KEY` in the repository's ignored `.env`. Optionally set
`ANTHROPIC_BRIEFING_MODEL` (default `claude-sonnet-5-5`). No key goes into
`NEXT_PUBLIC_*` configuration or browser code.

Start the feed from the repository root:

```sh
python dashboard/feed/serve.py
```

Start the dashboard in a second terminal:

```sh
cd dashboard
NEXT_PUBLIC_FEED=live npm run dev
```

Open `/ask` on the dashboard, enter a question, and click **Ask**. The page
posts to `/api/brief/stream`, which calls Claude with the current workflow
record. Draft answer text appears as it arrives. The final **Claude briefing**
includes checked citations and prior rulings. Model or validation failures show
an error instead of substituting a rules answer.

The route uses the existing estimated `FEED_BUDGET` limit. It does not send emails
or change invoices. Existing replay mode and the non-streaming briefing API remain
available.

Offline verification:

```sh
python -m pytest -q agents/briefing/test_briefing.py
cd dashboard
node --test lib/brief-stream.test.mjs
npx tsc --noEmit
```
