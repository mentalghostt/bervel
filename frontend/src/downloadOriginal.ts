import type { ArtifactRef } from './api'

export async function readOriginalBlob(ref: ArtifactRef, signal?: AbortSignal):Promise<Blob> {
  const response = await fetch(`/api/v0.1/artifacts/${encodeURIComponent(ref.artifact_id)}/content`,
    { headers: { 'X-Bervel-Web': '1' }, signal: signal ? AbortSignal.any([signal,AbortSignal.timeout(30_000)]) : AbortSignal.timeout(30_000) })
  if (response.status === 410) throw new Error('Файл удалён.')
  if (!response.ok) throw new Error('Не удалось получить оригинал. Проверьте доступ к файлу.')
  return response.blob()
}

export async function downloadOriginal(ref: ArtifactRef, filename: string) {
  const url = URL.createObjectURL(await readOriginalBlob(ref))
  const anchor = document.createElement('a')
  anchor.href = url; anchor.download = filename; anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
