import net from 'node:net'
import { ProviderSettings } from './settings.mjs'

// A byte tunnel preserves the original TLS hostname and end-to-end encryption.
export async function openMoxBridge({ targetHost, targetPort, port, bindHost = '127.0.0.1' }) {
  if (!['127.0.0.1', '::1'].includes(bindHost)) throw new Error('MOX bridge must use loopback')
  const sockets = new Set()
  const server = net.createServer(client => {
    const upstream = net.connect({ host: targetHost, port: targetPort })
    sockets.add(client); sockets.add(upstream)
    const close = () => { client.destroy(); upstream.destroy() }
    for (const socket of [client, upstream]) {
      socket.setTimeout(210_000, close)
      socket.on('error', close)
      socket.on('close', () => { sockets.delete(socket); close() })
    }
    client.pipe(upstream); upstream.pipe(client)
  })
  server.maxConnections = 64
  await new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(port, bindHost, () => { server.off('error', reject); resolve() })
  })
  return { address: () => server.address(), close: async () => {
    for (const socket of sockets) socket.destroy()
    if (server.listening) await new Promise(resolve => server.close(resolve))
  } }
}

export async function startMoxBridge(env) {
  if (!env.BERVEL_MOX_TUNNEL_HOST) return { close: async () => {} }
  const profile = (await new ProviderSettings(env).profiles()).mox
  if (profile.error || !profile.base_url) return { close: async () => {} }
  const url = new URL(profile.base_url)
  if (!['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)) return { close: async () => {} }
  const port = Number(url.port || 443)
  const hosts = url.hostname === 'localhost' ? ['127.0.0.1', '::1'] : [url.hostname.replace(/[\[\]]/g, '')]
  const bridges = []
  try {
    for (const bindHost of hosts) bridges.push(await openMoxBridge({ targetHost: env.BERVEL_MOX_TUNNEL_HOST, targetPort: port, port, bindHost }))
  } catch (error) { await Promise.all(bridges.map(bridge => bridge.close())); throw error }
  return { close: async () => { await Promise.all(bridges.map(bridge => bridge.close())) } }
}
