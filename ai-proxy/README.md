# Research Compass AI email writer

A tiny Vercel serverless function (`api/draft.js`) that writes Research Compass emails with Claude.
The public site can't hide an API key, so the key lives here as a Vercel environment variable.

## One-time setup (about 10 minutes)

1. **Anthropic API key:** sign in at https://console.anthropic.com, add a payment method, and create an API key.
2. **Spend limit (the hard safety cap):** in the Console's limits settings, set a monthly spend limit
   (for example $10). Requests stop when it's reached, so the bill can never run away.
3. **Vercel:** in Vercel choose **Add New → Project**, import `Knarf24/research-compass`, set
   **Root Directory** to `ai-proxy`, and add the environment variable `ANTHROPIC_API_KEY` = your key. Deploy.
4. Send the project URL (like `https://research-compass-ai.vercel.app`) so `web/config.js` can point at
   `<url>/api/draft`.

## Guard rails

- Only `https://knarf24.github.io` may call it (`ALLOWED_ORIGINS` to change).
- Inputs are trimmed to fixed sizes.
- `PER_IP_PER_HOUR` (default 15) and `DAILY_LIMIT` (default 400) are best-effort limits per server
  instance; the Console spend limit is the real cap.
- Nothing is stored. Each request is a single Claude call (model `claude-opus-5-5`, low effort, JSON output).

Rough cost: a draft uses about 1,500 tokens, so roughly 1 to 2 cents per email at current prices.
