import type { ArtifactRef } from './api'

export async function readOriginalBlob(ref: ArtifactRef, signal?: AbortSignal):Promise<Blob> {
  let response:Response
  try{response=await fetch(`/api/v0.1/artifacts/${encodeURIComponent(ref.artifact_id)}/content`,
    { headers: { 'X-Bervel-Web': '1' }, signal })}
  catch(reason){if(signal?.aborted)throw reason;throw new Error('Не удалось начать скачивание: соединение с сервером недоступно.')}
  if (response.status === 410) throw new Error('Файл удалён.')
  if (response.status === 404) throw new Error('Оригинал файла не найден.')
  if (response.status === 401||response.status===403) throw new Error('Нет доступа к оригиналу файла.')
  if (response.status === 503) throw new Error('Хранилище файла временно недоступно. Повторите скачивание позже.')
  if (!response.ok) throw new Error('Сервер не смог подготовить оригинал для скачивания.')
  try{return await response.blob()}
  catch(reason){if(signal?.aborted)throw reason;throw new Error('Скачивание прервано: файл получен не полностью. Повторите скачивание.')}
}

export async function downloadOriginal(ref: ArtifactRef, filename: string) {
  const url = URL.createObjectURL(await readOriginalBlob(ref))
  const anchor = document.createElement('a')
  anchor.href = url; anchor.download = filename; anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
