import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import type { IncomingMessage, ServerResponse } from 'node:http'

function webRequestGuard(request: IncomingMessage, response: ServerResponse, next: () => void) {
  if (!request.url?.startsWith('/api/')) return next()
  let status = 0
  if (!request.url.startsWith('/api/v0.1/')) status = 404
  else if (request.headers['x-bervel-web'] !== '1' || request.headers['sec-fetch-site'] === 'cross-site') status = 403
  if (status) {
    response.writeHead(status, { 'Content-Type': 'application/json' })
    response.end(JSON.stringify({ error: { code: status === 404 ? 'NOT_FOUND' : 'WEB_REQUEST_REQUIRED' } }))
    return
  }
  response.setHeader('Cache-Control', 'no-store')
  next()
}

export default defineConfig(({ command, mode }) => {
  // This configuration runs in Node, never in the browser bundle.
  const env = { ...loadEnv(mode, '..', 'BERVEL_'), ...process.env }
  const key = env.BERVEL_WEB_ACCESS_KEY || ''
  if (command === 'serve' && !/^[A-Za-z0-9_-]{32,}$/.test(key)) {
    throw new Error('Set a valid server-side BERVEL_WEB_ACCESS_KEY before starting Vite.')
  }
  return {
    plugins: [react(), tailwindcss(), {
      name: 'bervel-web-access',
      configureServer(server) {
        server.middlewares.use(webRequestGuard)
      },
      configurePreviewServer(server) {
        server.middlewares.use(webRequestGuard)
      },
    }],
    server: {
      allowedHosts: ['frontend'],
      proxy: {
        '/api/v0.1/': {
          target: env.BERVEL_API_TARGET || 'http://127.0.0.1:8011',
          configure(proxy) {
            proxy.on('proxyReq', (proxyRequest, request) => {
              if (request.url?.startsWith('/api/v0.1/')) proxyRequest.setHeader('Authorization', `Bearer ${key}`)
            })
          },
        },
      },
    },
  }
})
