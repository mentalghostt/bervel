import { useEffect, useRef, useState } from 'react'
import UploadFile from './UploadFile'
import FilePreview from './FilePreview'
import DeleteFile from './DeleteFile'
import { listUploadedArtifacts, listAllUploadedArtifacts, readUploadedArtifact, knowledgeStatus, resumeKnowledge, artifactReady, setUsageRole, roleLabels } from './api'
import type { ArtifactRef, UploadedArtifact, KnowledgeStatus, UsageRole } from './api'
import { isStructuredStock } from './api'

const buttonClass = 'rounded-lg border border-line bg-white px-4 py-2.5 text-sm font-bold text-accent hover:bg-accent-soft focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-50'
const dateFormat = new Intl.DateTimeFormat('ru-RU', { dateStyle: 'medium', timeStyle: 'short' })

function KnowledgeState({ file, onResumed }: { file: UploadedArtifact; onResumed: (status: KnowledgeStatus) => void }) {
  const [status, setStatus] = useState<KnowledgeStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [retry, setRetry] = useState(0)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    let active = true
    let timer: ReturnType<typeof setTimeout> | undefined
    async function poll() {
      try {
        const value = await knowledgeStatus(file)
        if (!active) return
        setStatus(value); setError(null)
        if (value.state === 'pending' || value.state === 'running' || (value.state === 'ready' && value.completed_chunks !== value.total_chunks)) timer = setTimeout(() => void poll(), 2500)
      } catch (reason) {
        if (active) setError(reason instanceof Error ? reason.message : 'Не удалось проверить готовность файла.')
      }
    }
    void poll()
    return () => { active = false; clearTimeout(timer) }
  }, [file, retry])
  async function resume() {
    setBusy(true); setError(null)
    try { const value = await resumeKnowledge(file); setStatus(value); onResumed(value); setRetry(value => value + 1) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Не удалось продолжить подготовку.') }
    finally { setBusy(false) }
  }
  const labels = { pending: 'Обработка', running: 'Обработка', ready: 'Готов к ИИ анализу',
    partial: 'Обработка: доступна только часть файла', unsupported: 'Обработка: формат не поддерживается', failed: 'Обработка остановлена' }
  return <section role="region" aria-label="Подготовка файла для ИИ" className="file-knowledge">
    <p aria-live="polite" className={`text-sm font-bold ${status?.state === 'ready' ? 'text-accent' : 'text-ink'}`}>
      {status ? status.state === 'ready' && status.completed_chunks !== status.total_chunks ? 'Обработка' : labels[status.state] : 'Проверяем готовность для ИИ…'}
    </p>
    {status && status.total_chunks > 0 && <p className="mt-1 text-xs text-muted">Обработано фрагментов: {status.completed_chunks} из {status.total_chunks}</p>}
    {(error || status?.error) && <p className="mt-2 text-sm text-red-800">{error || status?.error}</p>}
    {status?.state === 'failed' && <button type="button" disabled={busy} onClick={() => void resume()} className={`${buttonClass} mt-3`}>Продолжить подготовку</button>}
    {error && <button type="button" onClick={() => setRetry(value => value + 1)} className={`${buttonClass} mt-3`}>Проверить состояние</button>}
  </section>
}

function FileRole({ file, onChanged }: { file: UploadedArtifact; onChanged: (role: UsageRole) => void }) {
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null)
  return <div className="file-role"><label className="text-sm font-bold">Назначение файла<select aria-label="Назначение выбранного файла" value={file.usage_role || 'general'} disabled={busy} onChange={async event => {
    const role = event.target.value as UsageRole
    setBusy(true); setError(null)
    try { await setUsageRole(file.artifact_id, role); onChanged(role) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Не удалось изменить назначение.') }
    finally { setBusy(false) }
  }}>{Object.entries(roleLabels).map(([value,label]) => <option key={value} value={value}>{label}</option>)}</select></label>
  {error && <p role="alert" className="text-red-800 text-sm">{error}</p>}</div>
}

function linkedSource(): { ref: ArtifactRef | null; error: string | null } {
  const params = new URLSearchParams(window.location.hash.split('?')[1] || '')
  const id = params.get('artifact_id')
  if (params.has('revision')) return { ref: null, error: 'Ссылка устарела. Откройте файл из списка или перейдите по новой ссылке источника.' }
  if (!id) return { ref: null, error: null }
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)) return { ref: null, error: 'Некорректная ссылка на файл.' }
  return { ref: { artifact_id: id }, error: null }
}

function SavedFiles() {
  const [files, setFiles] = useState<UploadedArtifact[]>([])
  const [cursor, setCursor] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<UploadedArtifact | null>(null)
  const [retry, setRetry] = useState(0)
  const activeRequest = useRef({ active: false })
  const [deleted, setDeleted] = useState(false)
  const [source] = useState(linkedSource)
  const [sourceError, setSourceError] = useState<string | null>(source.error)
  const [sourceLoading, setSourceLoading] = useState(!!source.ref)
  const sourceRequest = useRef({ active: false })

  useEffect(() => {
    if (!source.ref) return
    const request = { active: true }; sourceRequest.current = request
    readUploadedArtifact(source.ref).then(file => { if (request.active) { setSelected(file); setSourceError(null) } })
      .catch(reason => { if (request.active) setSourceError(reason instanceof Error ? reason.message : 'Не удалось открыть источник.') })
      .finally(() => { if (request.active) setSourceLoading(false) })
    return () => { request.active = false }
  }, [source])

  useEffect(() => {
    if (!files.some(file => !isStructuredStock(file)&&(file.processing_state === 'pending' || file.processing_state === 'running' || (file.processing_state === 'ready' && !artifactReady(file))))) return
    let active = true
    const timer = setTimeout(async () => {
      try {
        const value = await listAllUploadedArtifacts()
        if (!active) return
        setFiles(old => old.map(file => value.find(item => item.artifact_id === file.artifact_id) || file))
      } catch (reason) { if (active) setError(reason instanceof Error ? reason.message : 'Не удалось обновить готовность файлов.') }
    }, 2500)
    return () => { active = false; clearTimeout(timer) }
  }, [files])

  useEffect(() => {
    const request = { active: true }
    activeRequest.current = request
    listUploadedArtifacts().then(result => {
      if (!request.active) return
      setFiles(result.artifacts); setCursor(result.next_cursor)
    }).catch(reason => {
      if (request.active) setError(reason instanceof Error ? reason.message : 'Не удалось получить список файлов.')
    }).finally(() => {
      if (request.active) setLoading(false)
    })
    return () => { request.active = false }
  }, [retry])

  async function loadMore() {
    if (loading || cursor === null) return
    const request = activeRequest.current
    setLoading(true); setError(null)
    try {
      const result = await listUploadedArtifacts(cursor)
      if (!request.active) return
      setFiles(current => [...current, ...result.artifacts.filter(item => !current.some(old => old.artifact_id === item.artifact_id))])
      setCursor(result.next_cursor)
    } catch (reason) {
      if (request.active) setError(reason instanceof Error ? reason.message : 'Не удалось получить список файлов.')
    } finally {
      if (request.active) setLoading(false)
    }
  }

  return (
    <div className="files-columns workspace-columns">
      <section role="region" aria-label="Список файлов" className="min-w-0 rounded-xl border border-line bg-white p-4 sm:p-5">
        {deleted && <p role="status" className="mb-4 rounded-lg bg-accent-soft p-3 text-sm font-bold text-accent">Файл удалён.</p>}
        <div className="file-list-scroll space-y-2">
          {files.map(file => (
            <article key={file.artifact_id} className={`file-card ${selected?.artifact_id===file.artifact_id?'selected':''}`}>
            <button type="button" aria-label={file.filename} aria-pressed={selected?.artifact_id===file.artifact_id} aria-controls="file-detail"
              onClick={()=>{sourceRequest.current.active=false;setSelected(file);setSourceError(null);setSourceLoading(false)}} className="file-select">
              <span className="file-name-row"><span className="file-name text-sm font-bold text-accent wrap-anywhere">{file.filename}</span>
              <span className={`file-readiness ${isStructuredStock(file)||artifactReady(file) ? 'ready' : ''}`}>{isStructuredStock(file)?'Доступен для радара':artifactReady(file) ? 'Готов к ИИ анализу' : 'Обработка'}</span></span>
              {!isStructuredStock(file)&&['failed','partial','unsupported'].includes(file.processing_state || '') && <span className="mt-1 block text-xs text-red-800">{file.processing_state === 'unsupported' ? 'Формат не поддерживается для анализа' : file.processing_state === 'partial' ? 'Обработана только часть файла' : 'Обработка остановлена'}{file.processing_error ? `: ${file.processing_error}` : ''}</span>}
              <span className="mt-1 block text-xs text-muted">{roleLabels[file.usage_role || 'general']}</span>
            </button>
            <div className="file-card-footer">
            <div className="file-card-meta">
              <span>{new Intl.NumberFormat('ru-RU').format(file.size)} байт</span>
              <time dateTime={file.created_at}>{dateFormat.format(new Date(file.created_at))}</time>
            </div>
            <div className="file-card-actions"><DeleteFile file={file} onDeleted={() => {
              if (!activeRequest.current.active) return
              if (sourceRequest.current.active && source.ref?.artifact_id === file.artifact_id) {
                sourceRequest.current.active = false
                setSourceLoading(false); setSourceError('Файл удалён.')
              }
              activeRequest.current.active = false
              setFiles(current => current.filter(item => item.artifact_id !== file.artifact_id))
              setSelected(current => current?.artifact_id === file.artifact_id ? null : current)
              setCursor(null); setError(null); setLoading(true); setDeleted(true); setRetry(value => value + 1)
            }} /></div>
            </div>
            </article>
          ))}
        </div>
        {loading&&<p role="status" className="py-8 text-sm text-muted">Получаем список файлов…</p>}
        {!loading&&!error&&files.length===0&&<p className="rounded-lg bg-paper p-4 text-sm text-muted">В этой области данных пока нет загруженных файлов.</p>}
        {error&&<div className="mt-4"><p role="alert" className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-800">{error}</p><button type="button" onClick={()=>{
          if(files.length&&cursor!==null){void loadMore();return}
          setLoading(true);setError(null);setRetry(value=>value+1)
        }} className={`${buttonClass} mt-3`}>Повторить</button></div>}
        {!loading&&!error&&cursor!==null&&<button type="button" onClick={()=>void loadMore()} className={`${buttonClass} mt-4`}>Показать ещё файлы</button>}
      </section>
      <section id="file-detail" role="region" aria-label="Просмотр файла" className="min-w-0 rounded-xl border border-line bg-white p-4 sm:p-6">
        {sourceLoading ? <p role="status">Открываем источник…</p> : sourceError ? <p role="alert" className="error-note">{sourceError}</p> : selected?<>
          <div className="file-detail-heading"><div>
          <h2 className="font-display text-2xl font-semibold wrap-anywhere">{selected.filename}</h2>
          <p className="mt-2 text-xs text-muted">{new Intl.NumberFormat('ru-RU').format(selected.size)} байт</p>
          </div>
          {isStructuredStock(selected)?<section className="file-knowledge"><p className="text-sm font-bold text-accent">Доступен для радара</p><p className="mt-1 text-xs text-muted">Склад читается целиком как таблица при запуске. Общая индексация файла для радара не требуется.</p></section>:<KnowledgeState key={selected.artifact_id} file={selected} onResumed={status => {
            setFiles(current => current.map(file => file.artifact_id === selected.artifact_id ? {
              ...file, processing_state: status.state, completed_chunks: status.completed_chunks,
              total_chunks: status.total_chunks, processing_error: status.error,
            } : file))
          }} />}
          </div>
          <FilePreview key={selected.artifact_id} file={selected} toolbarLeading={<FileRole key={selected.artifact_id} file={selected} onChanged={role => {
            setSelected(current => current?.artifact_id === selected.artifact_id ? { ...current, usage_role: role } : current)
            setFiles(current => current.map(file => file.artifact_id === selected.artifact_id ? { ...file, usage_role: role } : file))
          }} />} />
        </>:<div className="flex min-h-80 flex-col items-center justify-center px-4 py-16 text-center"><p className="font-display text-xl font-semibold">Выберите файл слева</p><p className="mt-3 max-w-md text-sm leading-6 text-muted">Изображения, PDF и таблицы открываются здесь. Для просмотра не требуется запускать обработку.</p></div>}
      </section>
    </div>
  )
}

export default function FilesPage() {
  const [refresh, setRefresh] = useState(0)

  return (
    <main className="files-page mx-auto px-5 py-10 sm:px-8 lg:py-14">
      <div className="files-toolbar">
        <h2>Все файлы</h2><div className="flex flex-wrap gap-3"><UploadFile onUploaded={() => setRefresh(value => value + 1)} />
        <button type="button" onClick={() => setRefresh(value => value + 1)} className={buttonClass}>Обновить</button></div>
      </div>
      <SavedFiles key={refresh} />
    </main>
  )
}
