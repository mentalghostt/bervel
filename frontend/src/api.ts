export type ArtifactRef = { artifact_id: string; revision: number }
export type DatasetRef = { dataset_id: string; revision: number }
export type DatasetSummary = DatasetRef & {
  record_count: number
  quality_state: 'COMPLETE' | 'PARTIAL' | 'CONFLICTED'
}

const unexpectedResponse = 'API вернул неожиданный ответ. Проверьте, что Python API запущен и адрес сервера указан правильно.'

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function readReference(value: unknown, field: 'artifact_id' | 'dataset_id') {
  if (!isObject(value)) throw new Error(unexpectedResponse)
  const id = value[field]
  const revision = value.revision
  if (typeof id !== 'string' || !id || typeof revision !== 'number' || !Number.isInteger(revision) || revision < 1) {
    throw new Error(unexpectedResponse)
  }
  return { id, revision }
}

function serverError(status: number, value: unknown) {
  if (status === 401) return 'Ключ доступа не принят. Вставьте ключ от текущего запуска Python API.'
  if (status === 403) return 'У этого ключа нет прав на загрузку и обработку CSV.'
  if (status === 404) return 'Файл или результат не найден в области данных этого ключа.'
  if (status === 409) return 'Запрос конфликтует с уже выполненной операцией. Проверьте файл и ключ доступа.'
  if (status === 413) return 'Превышен лимит: файл до 5 МиБ и не более 1000 строк с данными.'
  if (status === 503) return 'База данных или хранилище сейчас недоступны. Попробуйте повторить.'
  if (status === 422) {
    const message = isObject(value) && isObject(value.error) ? value.error.message : null
    const messages: Record<string, string> = {
      'CSV must use UTF-8': 'Сохраните CSV в кодировке UTF-8 и выберите его заново.',
      'Expected columns: line,name,quantity,unit': 'В первой строке нужны колонки в таком порядке: line,name,quantity,unit.',
      'CSV has no data rows': 'В CSV нет строк с данными.',
      'Malformed CSV': 'Нарушен формат CSV. Проверьте запятые и парные кавычки.',
      'CSV contains a null byte': 'CSV содержит недопустимый нулевой байт. Сохраните файл как текстовый CSV в UTF-8.',
    }
    if (typeof message === 'string') {
      if (messages[message]) return messages[message]
      const row = message.match(/at CSV row (\d+)$/)?.[1]
      if (row) return `Ошибка в строке ${row} CSV. Проверьте номер позиции, название, количество и единицу pcs или piece.`
    }
    return 'CSV не прошёл проверку. Проверьте колонки, строки и значения полей.'
  }
  return 'Сервер не смог завершить операцию. Попробуйте повторить.'
}

async function requestJson(path: string, key: string, options: RequestInit = {}): Promise<unknown> {
  let response: Response
  try {
    response = await fetch(`/api/v0.1${path}`, {
      ...options,
      headers: { ...options.headers, Authorization: `Bearer ${key}` },
      signal: AbortSignal.timeout(30_000),
    })
  } catch {
    throw new Error('Не удалось получить ответ от API. Проверьте запуск Python API и повторите попытку.')
  }
  let data: unknown
  try {
    data = await response.json()
  } catch {
    throw new Error(unexpectedResponse)
  }
  if (!response.ok) throw new Error(serverError(response.status, data))
  return data
}

export async function uploadOriginal(file: File, key: string, commandKey: string): Promise<ArtifactRef> {
  const body = new FormData()
  // Windows may label .csv as an Excel file; the API expects the CSV media type.
  body.append('file', new Blob([file], { type: 'text/csv' }), file.name)
  body.append('command_key', commandKey)
  body.append('origin_system', 'manual-upload')
  body.append('source_role', 'test_fixture')
  const { id, revision } = readReference(await requestJson('/artifacts', key, { method: 'POST', body }), 'artifact_id')
  return { artifact_id: id, revision }
}

export async function processCsv(artifact: ArtifactRef, key: string, commandKey: string): Promise<DatasetRef> {
  const body = JSON.stringify({ artifact_id: artifact.artifact_id, artifact_revision: artifact.revision, command_key: commandKey })
  const { id, revision } = readReference(await requestJson('/demo/document-review/extractions', key, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body,
  }), 'dataset_id')
  return { dataset_id: id, revision }
}

export async function readDataset(ref: DatasetRef, key: string): Promise<DatasetSummary> {
  const data = await requestJson(`/datasets/${encodeURIComponent(ref.dataset_id)}/revisions/${ref.revision}`, key)
  const { id, revision } = readReference(data, 'dataset_id')
  if (!isObject(data) || id !== ref.dataset_id || revision !== ref.revision
    || typeof data.record_count !== 'number' || !Number.isInteger(data.record_count) || data.record_count < 0
    || typeof data.quality_state !== 'string' || !['COMPLETE', 'PARTIAL', 'CONFLICTED'].includes(data.quality_state)) {
    throw new Error(unexpectedResponse)
  }
  return { dataset_id: id, revision, record_count: data.record_count, quality_state: data.quality_state as DatasetSummary['quality_state'] }
}
