# BiliScribe Cloud

The Sites deployment of BiliScribe. The Windows/Python version remains in the parent directory.

This site starts private. It does not include or upload `../data/cookies.json`; sign in again through the site's Bilibili QR flow.

The cloud edition uses a Cloudflare Worker instead of the local FastAPI service. It extracts existing Bilibili CC/AI tracks and performs no ASR. It stores the Bilibili session only in an encrypted, Secure, HttpOnly browser cookie for up to 30 days. Exports are generated for browser download and are not stored on the Site. Each request accepts up to three links and eight pages per video; subtitle tracks and bodies use a 24-hour in-memory cache capped at 500 entries and 16 MiB, which disappears when an instance is recycled. The cloud runtime does not provide the local serial job queue or disk cache.

The Worker applies request spacing, subtitle ownership validation, and the same language-family matching used by the local adapter. All automated tests mock Bilibili endpoints.

For local development: `npm install`, `npm run build`, `npm test`, then `npm run dev`. The preview runs at `http://127.0.0.1:8770`. The deployed Site requires the `BILI_COOKIE_KEY` secret (32 random bytes, base64url-encoded) configured through Sites.
