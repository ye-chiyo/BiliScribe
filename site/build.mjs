import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';

const qr = readFileSync('node_modules/qrcode-generator/qrcode.js', 'utf8');
const client = readFileSync('client.js', 'utf8');
const page = readFileSync('index.html', 'utf8');
const safeScript = value => value.replaceAll('</script', '<\\/script');
const html = page.replace('<!-- cloud-client -->',
  `<script>${safeScript(qr)}</script><script>${safeScript(client)}</script>`);
if (html === page) throw new Error('Missing cloud-client placeholder');

const worker = readFileSync('worker.mjs', 'utf8')
  .replace('/*__HTML__*/', JSON.stringify(html));
mkdirSync('dist/server', { recursive: true });
writeFileSync('dist/server/index.js', worker);
