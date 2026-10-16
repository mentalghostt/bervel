import { useRef, useState } from 'react'
import type { ChangeEvent, DragEvent } from 'react'
import { processCsv, readDataset, uploadOriginal } from './api'
import type { ArtifactRef, DatasetRef, DatasetSummary } from './api'
import UploadResult from './UploadResult'

const MAX_FILE_BYTES = 5 * 1024 * 1024
type Phase = 'idle' | 'uploading' | 'processing' | 'reading' | 'success' | 'error'
type UploadAttempt = { uploadKey: string; processingKey: string; artifact?: ArtifactRef; dataset?: DatasetRef }

function formatFileSize(bytes: number) {
  const number = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 })
  if (bytes < 1024) return `${number.format(bytes)} байт`
  if (bytes < 1024 * 1024) return `${number.format(bytes / 1024)} КиБ`
  return `${number.format(bytes / (1024 * 1024))} МиБ`
}

export default function App() {
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [isDragging, setIsDragging] = useState(false)
  const [accessKey, setAccessKey] = useState('')
  const [phase, setPhase] = useState<Phase>('idle')
  const [attempt, setAttempt] = useState<UploadAttempt | null>(null)
  const [result, setResult] = useState<DatasetSummary | null>(null)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const chooseButtonRef = useRef<HTMLButtonElement>(null)
  const dragDepth = useRef(0)
  const uploadInFlight = useRef(false)
  const isBusy = phase === 'uploading' || phase === 'processing' || phase === 'reading'
  const currentStep = result || attempt?.dataset ? 3 : phase === 'processing' || attempt?.artifact ? 2 : 1
  const phaseMessage = {
    idle: 'Добавьте CSV и ключ доступа, затем нажмите «Загрузить в БД».',
    uploading: 'Загружаем файл…',
    processing: 'Обрабатываем CSV…',
    reading: 'Получаем результат…',
    success: result
      ? `Сохранено в PostgreSQL. Строк: ${result.record_count}. Для новой загрузки выберите файл заново.`
      : 'Для новой загрузки выберите файл заново.',
    error: attempt?.dataset
      ? 'Результат уже записан. Повторите получение сводки.'
      : attempt?.artifact ? 'Оригинал сохранён. Повтор продолжит обработку.' : 'Повторите попытку с тем же файлом и ключом.',
  }[phase]

  function resetUpload() {
    setAttempt(null)
    setResult(null)
    setUploadError(null)
    setPhase('idle')
  }

  async function handleUpload() {
    const key = accessKey.trim()
    if (uploadInFlight.current || !selectedFile || !key || result) return
    uploadInFlight.current = true
    setUploadError(null)
    setPhase('uploading')
    try {
      // Keep the same keys and confirmed references for retries on this page.
      let current = attempt ?? { uploadKey: crypto.randomUUID(), processingKey: crypto.randomUUID() }
      setAttempt(current)
      const artifact = current.artifact ?? await uploadOriginal(selectedFile, key, current.uploadKey)
      current = { ...current, artifact }
      setAttempt(current)
      if (!current.dataset) setPhase('processing')
      const dataset = current.dataset ?? await processCsv(artifact, key, current.processingKey)
      current = { ...current, dataset }
      setAttempt(current)
      setPhase('reading')
      setResult(await readDataset(dataset, key))
      setPhase('success')
    } catch (reason) {
      setUploadError(reason instanceof Error ? reason.message : 'Не удалось завершить загрузку. Попробуйте повторить.')
      setPhase('error')
    } finally {
      uploadInFlight.current = false
    }
  }

  function selectFiles(files: File[]) {
    if (uploadInFlight.current) return
    resetUpload()
    const file = files[0]
    let message: string | null = null

    if (files.length !== 1) {
      message = 'Выберите один файл CSV за раз.'
    } else if (!file.name.toLowerCase().endsWith('.csv')) {
      message = 'Нужен файл с расширением .csv. Выберите другой файл.'
    } else if (file.size === 0) {
      message = 'Файл пуст. Выберите CSV с данными.'
    } else if (file.size > MAX_FILE_BYTES) {
      message = 'Файл превышает 5 МиБ. Выберите файл меньшего размера.'
    }

    setError(message)
    setSelectedFile(message ? null : file)
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.currentTarget.files ?? [])
    if (files.length) selectFiles(files)
    // Allow selecting the same file again after clearing or an invalid attempt.
    event.currentTarget.value = ''
  }

  function resetDrag() {
    dragDepth.current = 0
    setIsDragging(false)
  }

  function handleDragEnter(event: DragEvent<HTMLElement>) {
    if (!event.dataTransfer.types.includes('Files') || uploadInFlight.current) return
    event.preventDefault()
    dragDepth.current += 1
    setIsDragging(true)
  }

  function handleDragLeave(event: DragEvent<HTMLElement>) {
    if (!event.dataTransfer.types.includes('Files')) return
    dragDepth.current = Math.max(0, dragDepth.current - 1)
    if (dragDepth.current === 0) setIsDragging(false)
  }

  function handleDrop(event: DragEvent<HTMLElement>) {
    if (!event.dataTransfer.types.includes('Files')) return
    event.preventDefault()
    event.stopPropagation()
    resetDrag()
    selectFiles(Array.from(event.dataTransfer.files))
  }

  return (
    <div
      className="min-h-screen"
      onDragOver={event => {
        if (event.dataTransfer.types.includes('Files')) {
          event.preventDefault()
          event.dataTransfer.dropEffect = 'none'
        }
      }}
      onDrop={event => {
        if (event.dataTransfer.types.includes('Files')) {
          event.preventDefault()
          resetDrag()
        }
      }}
    >
      <header className="border-b border-line bg-white">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-5 py-5 sm:px-8">
          <div className="flex items-center gap-3">
            <span aria-hidden="true" className="grid size-8 grid-cols-2 gap-1 rounded-lg bg-accent p-2">
              <span className="bg-white" /><span className="bg-white/50" />
              <span className="bg-white/50" /><span className="bg-white" />
            </span>
            <span className="font-display text-xl font-semibold tracking-[0.12em]">BERVEL</span>
          </div>
          <span className="rounded-full border border-line px-3 py-1 text-xs text-muted">Учебный CSV</span>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-5 py-10 sm:px-8 lg:py-14">
        <p className="mb-3 text-xs font-bold tracking-[0.18em] text-accent uppercase">Импорт данных</p>
        <h1 className="font-display text-4xl leading-tight font-semibold tracking-tight sm:text-5xl">Загрузка CSV</h1>
        <p className="mt-4 max-w-xl text-base leading-7 text-muted">
          Добавьте файл с позициями, чтобы подготовить его к загрузке.
        </p>

        <ol aria-label="Этапы загрузки" className="mt-8 flex flex-wrap gap-x-7 gap-y-3 text-sm sm:gap-x-10">
          {['Выбор файла', 'Обработка', 'Результат'].map((label, index) => (
            <li key={label} aria-current={currentStep === index + 1 ? 'step' : undefined} className={`flex items-center gap-2.5 ${currentStep === index + 1 ? 'font-bold text-accent' : 'text-muted'}`}>
              <span className={`flex size-6 items-center justify-center rounded-full text-xs ${currentStep === index + 1 ? 'bg-accent text-white' : 'border border-line'}`}>{index + 1}</span>
              {label}
            </li>
          ))}
        </ol>

        <div className="mt-7 grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
          <section aria-labelledby="file-heading" className="min-w-0 rounded-2xl border border-line bg-white p-5 sm:p-7">
            <div className="mb-5 flex items-baseline justify-between gap-4">
              <h2 id="file-heading" className="font-display text-xl font-semibold">Файл с позициями</h2>
              <span className="text-xs text-muted">Один файл за раз</span>
            </div>

            <section
              aria-label="Выбор CSV-файла"
              aria-describedby="file-requirements"
              onDragEnter={handleDragEnter}
              onDragLeave={handleDragLeave}
              onDragOver={event => {
                if (!event.dataTransfer.types.includes('Files')) return
                event.preventDefault()
                event.stopPropagation()
                event.dataTransfer.dropEffect = uploadInFlight.current ? 'none' : 'copy'
              }}
              onDrop={handleDrop}
              className={`flex min-h-72 flex-col items-center justify-center rounded-xl border-2 border-dashed px-4 py-9 text-center transition-colors motion-reduce:transition-none ${
                isDragging ? 'border-accent bg-accent-soft' : 'border-line bg-paper/60'
              }`}
            >
              <span aria-hidden="true" className="mb-5 flex size-14 items-center justify-center rounded-2xl bg-accent-soft text-accent">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="size-7">
                  <path d="M12 16V4m-4 4 4-4 4 4M4 15v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </span>
              <h3 className="font-display text-xl font-semibold">
                {isDragging ? 'Отпустите CSV здесь' : 'Перетащите CSV-файл'}
              </h3>
              <p className="mt-2 text-sm text-muted">или выберите его на компьютере</p>
              <input
                ref={inputRef}
                type="file"
                accept=".csv,text/csv"
                aria-label="CSV-файл"
                aria-describedby="file-requirements"
                aria-invalid={error ? true : undefined}
                onChange={handleFileChange}
                disabled={isBusy}
                className="hidden"
              />
              <button
                ref={chooseButtonRef}
                type="button"
                onClick={() => inputRef.current?.click()}
                disabled={isBusy}
                className="mt-5 rounded-lg bg-accent px-5 py-2.5 text-sm font-bold text-white transition-colors hover:bg-accent-hover focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-50 motion-reduce:transition-none"
              >
                {selectedFile ? 'Заменить файл' : 'Выбрать CSV'}
              </button>
              <p id="file-requirements" className="mt-4 text-xs text-muted">.csv · до 5 МиБ</p>
            </section>

            {error && (
              <p role="alert" className="mt-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm leading-6 text-red-800">
                {error}
              </p>
            )}

            {selectedFile && (
              <div role="status" aria-atomic="true" className="mt-4 flex items-start gap-3 rounded-lg border border-accent/20 bg-accent-soft/50 p-4">
                <span aria-hidden="true" className="flex size-10 shrink-0 items-center justify-center rounded-lg border border-accent/20 bg-white text-[10px] font-bold tracking-wider text-accent">CSV</span>
                <div className="min-w-0 flex-1">
                  <p className="text-xs font-bold text-accent">Файл выбран</p>
                  <p className="mt-1 text-sm font-bold wrap-anywhere">{selectedFile.name}</p>
                  <p className="mt-1 text-xs text-muted">{formatFileSize(selectedFile.size)}</p>
                </div>
                <button
                  type="button"
                  aria-label="Убрать выбранный файл"
                  disabled={isBusy}
                  onClick={() => {
                    if (uploadInFlight.current) return
                    setSelectedFile(null)
                    setError(null)
                    resetUpload()
                    chooseButtonRef.current?.focus()
                  }}
                  className="flex size-8 shrink-0 items-center justify-center rounded-md text-muted hover:bg-white hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="size-5">
                    <path d="m6 6 12 12M6 18 18 6" strokeLinecap="round" />
                  </svg>
                </button>
              </div>
            )}

            <div className="mt-7 border-t border-line pt-5">
              <label htmlFor="access-key" className="block text-sm font-bold">Ключ доступа к API</label>
              <input
                id="access-key"
                type="password"
                value={accessKey}
                autoComplete="off"
                spellCheck={false}
                disabled={isBusy}
                aria-describedby="access-key-help"
                onChange={event => {
                  if (uploadInFlight.current) return
                  setAccessKey(event.currentTarget.value)
                  resetUpload()
                }}
                className="mt-2 w-full rounded-lg border border-line bg-white px-3 py-3 text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-50"
              />
              <p id="access-key-help" className="mt-2 text-xs leading-5 text-muted">Вставьте ключ без слова Bearer. Он хранится только в памяти этой страницы.</p>
              <button
                type="button"
                disabled={!selectedFile || !accessKey.trim() || isBusy || !!result}
                aria-busy={isBusy}
                aria-describedby="upload-hint"
                onClick={handleUpload}
                className="mt-5 flex w-full items-center justify-center gap-2 rounded-lg bg-accent px-5 py-3 text-sm font-bold text-white hover:bg-accent-hover focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent disabled:cursor-not-allowed disabled:bg-ink/10 disabled:text-ink/45 sm:w-auto"
              >
                {isBusy ? 'Подождите…' : result ? 'Сохранено' : uploadError ? 'Повторить' : 'Загрузить в БД'}
              </button>
              <p id="upload-hint" role="status" aria-atomic="true" className="mt-3 text-sm leading-6 text-muted">{phaseMessage}</p>
              {uploadError && <p role="alert" className="mt-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm leading-6 text-red-800">{uploadError}</p>}
              <p className="mt-2 text-xs leading-5 text-muted">Оригинал хранится отдельно, обработанные строки сохраняются в PostgreSQL.</p>
            </div>
            {result && attempt?.artifact && <UploadResult artifact={attempt.artifact} result={result} />}
          </section>

          <aside aria-labelledby="requirements-heading" className="min-w-0 rounded-2xl border border-line bg-white p-5 sm:p-6">
            <p className="text-xs font-bold tracking-[0.14em] text-accent uppercase">Перед загрузкой</p>
            <h2 id="requirements-heading" className="mt-2 font-display text-xl font-semibold">Что должно быть в CSV</h2>
            <dl className="mt-5 divide-y divide-line text-sm">
              {[
                ['Размер', 'До 5 МиБ'],
                ['Строки с данными', 'До 1000'],
                ['Кодировка', 'UTF-8'],
                ['Разделитель', 'Запятая'],
              ].map(([label, value]) => (
                <div key={label} className="flex justify-between gap-3 py-3">
                  <dt className="text-muted">{label}</dt>
                  <dd className="font-bold">{value}</dd>
                </div>
              ))}
            </dl>
            <div className="mt-5 border-t border-line pt-5">
              <p className="text-xs font-bold text-ink">Колонки в этом порядке</p>
              <pre className="mt-3 overflow-x-auto rounded-lg bg-paper px-3 py-3 font-mono text-xs leading-6 text-accent"><code>line,name,quantity,unit</code></pre>
              <p className="mt-4 text-xs leading-6 text-muted">Содержимое файла будет проверено при обработке.</p>
            </div>
          </aside>
        </div>
      </main>
    </div>
  )
}
