import { createServer } from 'node:http';
import { randomBytes } from 'node:crypto';
import worker from './dist/server/index.js';

const port = Number(process.env.PORT || 8770);
const env = { BILI_COOKIE_KEY: randomBytes(32).toString('base64url') };
createServer(async (incoming, outgoing) => {
  try {
    const chunks = [];
    for await (const chunk of incoming) chunks.push(chunk);
    const request = new Request(`http://127.0.0.1:${port}${incoming.url}`, {
      method: incoming.method,
      headers: incoming.headers,
      body: chunks.length ? Buffer.concat(chunks) : undefined,
    });
    const response = await worker.fetch(request, env);
    outgoing.statusCode = response.status;
    for (const [key, value] of response.headers) {
      if (key !== 'set-cookie') outgoing.setHeader(key, value);
    }
    const cookies = response.headers.getSetCookie();
    if (cookies.length) outgoing.setHeader('set-cookie', cookies);
    outgoing.end(Buffer.from(await response.arrayBuffer()));
  } catch (error) {
    outgoing.statusCode = 500;
    outgoing.end(String(error.message || error));
  }
}).listen(port, '127.0.0.1', () => {
  process.stdout.write(`BiliScribe cloud preview: http://127.0.0.1:${port}\n`);
});
