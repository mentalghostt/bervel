import { mkdtemp, writeFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, dirname, resolve } from 'node:path'

export function decodeImages(images) {
  if (images === undefined) return []
  if (!Array.isArray(images) || images.length > 2) throw Error('Invalid images')
  let remaining = 5 * 1024 * 1024
  return images.map(image => {
    if (!image || typeof image !== 'object' || Object.keys(image).some(key => !['media_type', 'data', 'label'].includes(key))
      || typeof image.data !== 'string' || image.data.length > 7 * 1024 * 1024
      || typeof image.label !== 'string' || image.label.length > 512) throw Error('Invalid image fields')
    const bytes = Buffer.from(image.data, 'base64')
    if (bytes.toString('base64') !== image.data) throw Error('Invalid base64')
    remaining -= bytes.length
    const valid = (image.media_type === 'image/png' && bytes.subarray(0, 8).equals(Buffer.from([137,80,78,71,13,10,26,10])))
      || (image.media_type === 'image/jpeg' && bytes.subarray(0, 3).equals(Buffer.from([255,216,255])))
      || (image.media_type === 'image/webp' && bytes.subarray(0,4).toString() === 'RIFF' && bytes.subarray(8,12).toString() === 'WEBP')
    if (!valid || remaining < 0) throw Error('Invalid image type or size')
    return { bytes, label: image.label, extension: image.media_type.split('/')[1] }
  })
}

export async function withImageInputs(request, run, root = tmpdir()) {
  const images = decodeImages(request.images)
  if (!images.length) return run(request.prompt)
  const base = resolve(root)
  const folder = await mkdtemp(join(base, 'bervel-vision-'))
  try {
    const input = [{ type: 'text', text: request.prompt }]
    for (const [index, image] of images.entries()) {
      const path = join(folder, `${index}.${image.extension}`)
      await writeFile(path, image.bytes, { mode: 0o600 })
      input.push({ type: 'text', text: image.label }, { type: 'local_image', path })
    }
    return await run(input)
  } finally {
    if (dirname(folder) !== base) throw Error('Invalid temporary directory')
    await rm(folder, { recursive: true, force: true })
  }
}
