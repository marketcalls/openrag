import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

test('nginx serves SPA routes and proxies the OpenRAG API', () => {
  const config = readFileSync(resolve(process.cwd(), 'nginx.conf'), 'utf8');
  expect(config).toContain('try_files $uri $uri/ /index.html');
  expect(config).toContain('proxy_pass http://api:8000');
  expect(config).toContain('location /api/');
  expect(config).toContain('client_max_body_size 101m');
  expect(config).toContain('proxy_request_buffering off');
});
