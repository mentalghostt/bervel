import { test } from 'node:test'
import assert from 'node:assert/strict'
import net from 'node:net'
import { once } from 'node:events'
const { openMoxBridge } = await import('../../codex-runner/mox-bridge.mjs')

test('loopback tunnel forwards unchanged bytes without terminating TLS or exposing a LAN listener', async () => {
  const upstream = net.createServer(socket => socket.pipe(socket))
  await new Promise(resolve => upstream.listen(0, '127.0.0.1', resolve))
  const bridge = await openMoxBridge({ targetHost: '127.0.0.1', targetPort: upstream.address().port, port: 0 })
  try {
    const { address, port } = bridge.address()
    assert.equal(address, '127.0.0.1')
    const client = net.connect({ host: '127.0.0.1', port })
    const bytes = Buffer.from([0x16, 0x03, 0x01, 0x00, 0x05, 0x00, 0xff])
    client.write(bytes)
    const [received] = await once(client, 'data')
    assert.deepEqual(received, bytes)
    client.destroy()
  } finally { await bridge.close(); await new Promise(resolve => upstream.close(resolve)) }
})

test('target connection failure closes the client without leaking unhandled errors', async () => {
  const bridge = await openMoxBridge({ targetHost: '127.0.0.1', targetPort: 1, port: 0 })
  try {
    const client = net.connect({ host: '127.0.0.1', port: bridge.address().port })
    client.on('error', () => {})
    await once(client, 'close')
  } finally { await bridge.close() }
})
