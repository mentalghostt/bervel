import { useEffect, useRef, useState } from 'react'
import { listUploadedArtifacts } from './api'
import type { UploadedArtifact } from './api'
import FilePreview from './FilePreview'
import WorkspaceDialog from './WorkspaceDialog'

export default function PresalesFilePicker({ onClose, onCreate, disabled }: { onClose: () => void; onCreate: (ids: string[]) => void; disabled: boolean }) {
  const [files, setFiles] = useState<UploadedArtifact[]>([])
  const [selected, setSelected] = useState<string[]>([])
  const [cursor, setCursor] = useState<number | null>(0)
  const [query, setQuery] = useState('')
  const [preview, setPreview] = useState<UploadedArtifact | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(true)
  const loading = useRef(false), mounted = useRef(false)
  async function load(position: number) {
    if (loading.current) return
    loading.current = true; setBusy(true); setError('')
    try { const page = await listUploadedArtifacts(position); if (mounted.current) { setFiles(old => [...old, ...page.artifacts.filter(file => !old.some(other => other.artifact_id === file.artifact_id))]); setCursor(page.next_cursor) } }
    catch (reason) { if (mounted.current) setError(reason instanceof Error ? reason.message : 'Не удалось получить список файлов.') }
    finally { loading.current = false; if (mounted.current) setBusy(false) }
  }
  useEffect(() => {
    mounted.current = true; loading.current = true
    listUploadedArtifacts(0).then(page => { if (mounted.current) { setFiles(page.artifacts); setCursor(page.next_cursor) } }).catch(reason => { if (mounted.current) setError(reason.message) }).finally(() => { loading.current = false; if (mounted.current) setBusy(false) })
    return () => { mounted.current = false }
  }, [])
  const visible = files.filter(file => file.filename.toLocaleLowerCase('ru').includes(query.toLocaleLowerCase('ru')))
  return <WorkspaceDialog title="Файлы заявки" onClose={onClose} wide className="presales-picker">
    <p className="presales-muted">Выберите комплект из уже загруженных оригиналов. Поиск действует по загруженным страницам.</p>
    <label className="presales-search">Найти файл<input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Название файла" /></label>
    <div className="presales-picker-grid"><div className="presales-file-list">
      {visible.map(file => <div className="presales-file-row ui-choice" key={file.artifact_id}><label><input type="checkbox" checked={selected.includes(file.artifact_id)} onChange={event => setSelected(old => event.target.checked ? [...old, file.artifact_id] : old.filter(id => id !== file.artifact_id))} disabled={disabled} /><span>{file.filename}<small>{new Intl.NumberFormat('ru-RU').format(file.size)} байт</small></span></label><button type="button" className="secondary" aria-label={`Просмотреть ${file.filename}`} onClick={() => setPreview(file)}>Просмотр</button></div>)}
      {!busy && !visible.length && <p className="presales-muted">Файлы не найдены.{cursor !== null ? ' Загрузите следующую страницу или измените поиск.' : ' Загрузите оригиналы в разделе «Файлы».'}</p>}
      {error && <p role="alert" className="presales-alert">{error}</p>}
      {cursor !== null && <button type="button" className="secondary" disabled={busy} onClick={() => void load(cursor)}>{busy ? 'Получаем файлы…' : error ? 'Повторить загрузку файлов' : 'Показать ещё файлы'}</button>}
    </div><div className="presales-file-preview">{preview ? <><h3>{preview.filename}</h3><FilePreview file={preview} showDownload={false} /></> : <div className="presales-preview-empty"><span aria-hidden="true">↗</span><p>Откройте файл, чтобы сверить содержимое перед анализом.</p></div>}</div></div>
    <footer className="presales-picker-footer"><span>Выбрано файлов: {selected.length}</span><button className="primary" type="button" disabled={!selected.length || disabled} onClick={() => onCreate(selected)}>Создать заявку</button></footer>
  </WorkspaceDialog>
}
