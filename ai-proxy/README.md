# Research Compass AI email writer

A tiny Vercel serverless function (`api/draft.js`) that writes Research Compass emails with
**Google Gemini's free tier**. The public site can't hide an API key, so the key lives here as a
Vercel environment variable. There is nothing to pay: no billing is turned on.

## One-time setup (about 10 minutes)

1. **Free Gemini key:** sign in at https://aistudio.google.com with a Google account, click
   **Get API key → Create API key**. Do **not** set up billing, so it stays free.
2. **Vercel:** in Vercel choose **Add New → Project**, import `Knarf24/research-compass`, set
   **Root Directory** to `ai-proxy`, and add the environment variable `GEMINI_API_KEY` = your key. Deploy.
3. Send the project URL (like `https://research-compass-ai.vercel.app`) so `web/config.js` can point at
   `<url>/api/draft`.

## How the free tier behaves

- Google gives a daily free quota. When it's used up, Google refuses requests until it resets (no charge),
  and the site falls back to its built-in template writer.
- On the free tier Google may use what's sent to improve its products. The email tab tells students
  not to type anything private.

## Guard rails

- Only `https://knarf24.github.io` may call it (`ALLOWED_ORIGINS` to change).
- Inputs are trimmed to fixed sizes.
- `PER_IP_PER_HOUR` (default 15) and `DAILY_LIMIT` (default 200) are best-effort limits per server instance.
- Nothing is stored. Each request is one Gemini call (model `gemini-flash-latest`, or `gemini-flash-lite-latest` when that is busy; change with `GEMINI_MODEL` and `GEMINI_BACKUP_MODEL`), JSON output.
