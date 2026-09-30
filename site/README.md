# BiliScribe Cloud

The Sites deployment of BiliScribe. The Windows/Python version remains in the parent directory.

This site starts private. It does not include or upload `../data/cookies.json`; sign in again through the site's Bilibili QR flow.

The cloud edition uses a Cloudflare Worker instead of the local FastAPI service. It stores the Bilibili session only in an encrypted, Secure, HttpOnly browser cookie for up to 30 days. Exports are generated for browser download and are not stored on the Site. Each request accepts up to three links and eight pages per video; the cloud runtime does not provide the local serial job queue or 24-hour disk cache.

For local development: `npm install`, `npm run build`, `npm test`, then `npm run dev`. The preview runs at `http://127.0.0.1:8770`. The deployed Site requires the `BILI_COOKIE_KEY` secret (32 random bytes, base64url-encoded) configured through Sites.
