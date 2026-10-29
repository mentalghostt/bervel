import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdir, readFile, access } from 'node:fs/promises'
import { resolve } from 'node:path'
const { decodeImages, withImageInputs } = await import('../../codex-runner/images.mjs')
const png = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10, 0])
const picture = { media_type: 'image/png', data: png.toString('base64'), label: 'scan.png, revision 1' }
test('passes selected image bytes as local_image and removes temporary originals after model failure', async () => {
  const root = resolve('.dev/local/assistant-capabilities-2026-10-28/native/image-tests')
  await mkdir(root, { recursive: true })
  let path
  await assert.rejects(withImageInputs({ prompt: 'Read scan', images: [picture] }, async input => {
    assert.equal(input[0].text, 'Read scan')
    assert.equal(input[1].text, picture.label)
    path = input[2].path
    assert.equal(input[2].type, 'local_image')
    assert.deepEqual(await readFile(path), png)
    throw Error('fixture failure')
  }, root), /fixture failure/)
  await assert.rejects(access(path))
})
test('rejects arbitrary paths, non-images, invalid base64, extra fields and excess images', () => {
  for (const images of [[{ path: '/private/auth.json' }], [{ ...picture, media_type: 'text/html' }],
    [{ ...picture, data: '../x' }], [{ ...picture, data: Buffer.from('not a PNG').toString('base64') }],
    [{ ...picture, path: 'x' }], [picture, picture, picture]]) assert.throws(() => decodeImages(images))
})
test('accepts a large valid attachment within the byte budget without regex stack overflow', () => {
  const bytes = Buffer.alloc(1024 * 1024)
  png.copy(bytes)
  assert.equal(decodeImages([{ ...picture, data: bytes.toString('base64') }])[0].bytes.length, bytes.length)
})
