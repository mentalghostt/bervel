export type ArtifactRef = { artifact_id: string }
export type UsageRole = 'general' | 'procurement' | 'stock' | 'economics'
export const roleLabels: Record<UsageRole, string> = { general: 'Общий файл', procurement: 'Закупки', stock: 'Склад', economics: 'Экономика и производство' }
export type UploadedArtifact = ArtifactRef & {
  filename: string
  media_type: string
  size: number
  created_at: string
  usage_role?: UsageRole
  processing_state?: KnowledgeStatus['state']
  completed_chunks?: number
  total_chunks?: number
  processing_error?: string | null
}
export const artifactReady = (file: UploadedArtifact) => file.processing_state === 'ready' && typeof file.total_chunks === 'number' && file.completed_chunks === file.total_chunks
export type ArtifactPage = { artifacts: UploadedArtifact[]; next_cursor: number | null }
export type KnowledgeStatus = { state: 'pending' | 'running' | 'ready' | 'partial' | 'unsupported' | 'failed'; error: string | null; total_chunks: number; completed_chunks: number }
const unexpectedResponse = 'API вернул неожиданный ответ. Проверьте, что Python API запущен и адрес сервера указан правильно.'
export class ApiError extends Error {
  readonly rejected: boolean
  constructor(message: string, rejected = false) { super(message); this.rejected = rejected }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function readReference(value: unknown, field: 'artifact_id') {
  if (!isObject(value)) throw new Error(unexpectedResponse)
  const id = value[field]
  if (typeof id !== 'string' || !id) {
    throw new Error(unexpectedResponse)
  }
  return { id }
}

function serverError(status: number) {
  if (status === 401) return 'Доступ к API не настроен на сервере. Проверьте серверные настройки приложения.'
  if (status === 403) return 'Операция недоступна. Проверьте права и настройки доступа на сервере.'
  if (status === 404) return 'Файл или результат не найден в области данных приложения.'
  if (status === 410) return 'Файл удалён.'
  if (status === 409) return 'Запрос конфликтует с уже выполненной операцией. Проверьте файл.'
  if (status === 413) return 'Сервер отклонил размер запроса. Проверьте настройки сервера передачи файлов.'
  if (status === 503) return 'База данных или хранилище сейчас недоступны. Попробуйте повторить.'
  if (status === 422) return 'Проверьте файл и обязательные поля запроса.'
  return 'Сервер не смог завершить операцию. Попробуйте повторить.'
}

async function requestJson(path: string, options: RequestInit = {}): Promise<unknown> {
  let response: Response
  try {
    response = await fetch(`/api/v0.1${path}`, {
      ...options,
      headers: { ...options.headers, 'X-Bervel-Web': '1' },
      ...(options.body instanceof FormData ? {} : { signal: AbortSignal.timeout(30_000) }),
    })
  } catch {
    throw new Error('Не удалось получить ответ от API. Проверьте запуск Python API и повторите попытку.')
  }
  let data: unknown
  if (response.status === 204) return null
  try {
    data = await response.json()
  } catch {
    throw new Error(unexpectedResponse)
  }
  if (!response.ok) {
    if (isObject(data) && isObject(data.error)) {
      if (data.error.code === 'ARTIFACT_BUSY') throw new Error('Файл сейчас подготавливается для ИИ. Повторите удаление после завершения текущей порции.')
      if (data.error.code === 'ARTIFACT_CLEANUP_PENDING') throw new Error('Файл убран из каталога, но очистка оригинала не завершена. Повторите удаление.')
    }
    throw new ApiError(serverError(response.status), response.status >= 400 && response.status < 500)
  }
  return data
}

export async function uploadOriginal(file: File, commandKey: string): Promise<ArtifactRef> {
  const body = new FormData()
  // Windows may label .csv as an Excel file; the API expects the CSV media type.
  body.append('file', new Blob([file], { type: file.name.toLowerCase().endsWith('.csv') ? 'text/csv' : file.type || 'application/octet-stream' }), file.name)
  body.append('command_key', commandKey)
  body.append('origin_system', 'manual-upload')
  body.append('source_role', 'test_fixture')
  const { id } = readReference(await requestJson('/artifacts', { method: 'POST', body }), 'artifact_id')
  return { artifact_id: id }
}

function nextCursor(value: unknown, cursor: number): number | null {
  if (value === null) return null
  if (typeof value !== 'number' || !Number.isInteger(value) || value <= cursor) throw new Error(unexpectedResponse)
  return value
}

export async function listUploadedArtifacts(cursor = 0): Promise<ArtifactPage> {
  const data = await requestJson(`/artifacts?cursor=${cursor}&limit=20`)
  if (!isObject(data) || !Array.isArray(data.artifacts)) throw new Error(unexpectedResponse)
  const artifacts = data.artifacts.map((item): UploadedArtifact => {
    const { id } = readReference(item, 'artifact_id')
    if (!isObject(item) || typeof item.filename !== 'string' || typeof item.media_type !== 'string'
      || typeof item.size !== 'number' || !Number.isInteger(item.size) || item.size < 1
      || typeof item.created_at !== 'string' || !Number.isFinite(Date.parse(item.created_at))) {
      throw new Error(unexpectedResponse)
    }
    return { artifact_id: id, filename: item.filename, media_type: item.media_type,
      size: item.size, created_at: item.created_at,
      usage_role: ['general','procurement','stock','economics'].includes(String(item.usage_role)) ? item.usage_role as UsageRole : 'general',
      processing_state: ['pending','running','ready','partial','unsupported','failed'].includes(String(item.processing_state)) ? item.processing_state as KnowledgeStatus['state'] : 'pending',
      completed_chunks: typeof item.completed_chunks === 'number' ? item.completed_chunks : 0,
      total_chunks: typeof item.total_chunks === 'number' ? item.total_chunks : 0,
      processing_error: typeof item.processing_error === 'string' ? item.processing_error : null }
  })
  return { artifacts, next_cursor: nextCursor(data.next_cursor, cursor) }
}

export async function listAllUploadedArtifacts(): Promise<UploadedArtifact[]> {
  const files: UploadedArtifact[] = []
  let cursor: number | null = 0
  while (cursor !== null) {
    const page = await listUploadedArtifacts(cursor)
    files.push(...page.artifacts.filter(file => !files.some(old => old.artifact_id === file.artifact_id)))
    cursor = page.next_cursor
  }
  return files
}

export async function readUploadedArtifact(ref: ArtifactRef): Promise<UploadedArtifact> {
  const value = await requestJson(`/artifacts/${encodeURIComponent(ref.artifact_id)}`)
  const { id } = readReference(value, 'artifact_id')
  if (!isObject(value) || id !== ref.artifact_id || typeof value.filename !== 'string'
    || typeof value.media_type !== 'string' || typeof value.size !== 'number' || value.size < 1
    || typeof value.created_at !== 'string' || !Number.isFinite(Date.parse(value.created_at))) throw new Error(unexpectedResponse)
  return { artifact_id: id, filename: value.filename, media_type: value.media_type, size: value.size,
    created_at: value.created_at, usage_role: Object.hasOwn(roleLabels, String(value.usage_role)) ? value.usage_role as UsageRole : 'general' }
}

export async function deleteUploadedArtifact(ref: ArtifactRef): Promise<void> {
  await requestJson(`/artifacts/${encodeURIComponent(ref.artifact_id)}`, { method: 'DELETE' })
}

function readKnowledge(value: unknown): KnowledgeStatus {
  if (!isObject(value) || !['pending','running','ready','partial','unsupported','failed'].includes(String(value.state))
    || (value.error !== null && typeof value.error !== 'string')
    || typeof value.total_chunks !== 'number' || !Number.isInteger(value.total_chunks) || value.total_chunks < 0
    || typeof value.completed_chunks !== 'number' || !Number.isInteger(value.completed_chunks) || value.completed_chunks < 0
    || value.completed_chunks > value.total_chunks) throw new Error(unexpectedResponse)
  return value as KnowledgeStatus
}

export async function knowledgeStatus(ref: ArtifactRef): Promise<KnowledgeStatus> {
  return readKnowledge(await requestJson(`/artifacts/${ref.artifact_id}/knowledge`))
}

export async function resumeKnowledge(ref: ArtifactRef): Promise<KnowledgeStatus> {
  return readKnowledge(await requestJson(`/artifacts/${ref.artifact_id}/knowledge/resume`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
  }))
}

export const isCsvFile = (file: UploadedArtifact) => file.size > 0 && /\.csv$/i.test(file.filename)
export const isStructuredStock = (file: UploadedArtifact) => file.usage_role === 'stock' && isCsvFile(file)
