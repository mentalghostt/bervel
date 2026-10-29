import { useRef, useState } from 'react'
import { ApiError, uploadOriginal, setUsageRole, roleLabels } from './api'
import type { ArtifactRef, UsageRole } from './api'

export default function UploadFile({ onUploaded }: { onUploaded: () => void }) {
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [role, setRole] = useState<UsageRole>('general')
  const [dragging, setDragging] = useState(false)
  const key = useRef<string | null>(null)
  const uploaded = useRef<ArtifactRef | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const dialog = useRef<HTMLDialogElement>(null)
  const [attempt, setAttempt] = useState(false)
  function select(selected: File) {
    if (busy || attempt) return
    key.current = null; uploaded.current = null; setNotice(null)
    if (!selected.size || selected.size > 5 * 1024 * 1024) { setFile(null); setError('Нужен непустой файл размером до 5 МиБ.'); return }
    setFile(selected); setError(null)
  }
  return <><button type="button" className="primary" onClick={() => dialog.current?.showModal()}>Загрузить файл</button>
  <dialog ref={dialog} aria-labelledby="upload-file-title" className="upload-dialog">
  <div className="flex items-center justify-between gap-4"><h2 id="upload-file-title">Загрузить файл</h2><button type="button" className="secondary" onClick={() => dialog.current?.close()}>Закрыть</button></div>
  <form role="region" aria-label="Загрузка файлов" className={`upload-zone ${dragging ? 'is-dragging' : ''}`} onDragOver={event => {
    event.preventDefault(); event.stopPropagation(); event.dataTransfer.dropEffect = busy || attempt ? 'none' : 'copy'; setDragging(!busy && !attempt)
  }} onDragLeave={() => setDragging(false)} onDrop={event => {
    event.preventDefault(); event.stopPropagation(); setDragging(false)
    if (event.dataTransfer.files[0]) select(event.dataTransfer.files[0])
  }} onSubmit={async event => {
    event.preventDefault()
    if (!file || busy) return
    setBusy(true); setAttempt(true); setError(null); setNotice(null)
    try {
      key.current ??= crypto.randomUUID()
      uploaded.current ??= await uploadOriginal(file, key.current)
      if (role !== 'general') await setUsageRole(uploaded.current.artifact_id, role)
      setNotice(`Файл ${file.name} сохранён. Обработка для ИИ продолжается; готовность появится в списке.`)
      setFile(null); key.current = null; uploaded.current = null; setAttempt(false)
      if (input.current) input.current.value = ''
      onUploaded()
    } catch (reason) {
      if (!uploaded.current && reason instanceof ApiError && reason.rejected) { setAttempt(false); key.current = null }
      setError(reason instanceof Error ? reason.message : 'Не удалось загрузить файл.')
    }
    finally { setBusy(false) }
  }}>
    <div><h3 className="font-display text-lg font-semibold">Перетащите файл сюда</h3>
    <p className="mt-2 text-sm leading-6 text-muted">Любой формат до 5 МиБ. Подготовка для ИИ начнётся после загрузки.</p></div>
    <label className="block text-sm font-bold">Назначение файла<select aria-label="Назначение файла" value={role} disabled={busy || attempt} onChange={event => setRole(event.target.value as UsageRole)}>{Object.entries(roleLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
    <div className="upload-picker"><span className="block text-sm font-bold">Файл</span><input aria-label="Файл" ref={input} type="file" disabled={busy || attempt} className="sr-only" onChange={event => {
      const selected = event.target.files?.[0]
      if (!selected) return
      select(selected)
    }} /><button type="button" onClick={() => input.current?.click()} disabled={busy || attempt} className="secondary">Выбрать файл</button></div>
    <button type="submit" disabled={!file || busy} className="primary">{busy ? 'Загружаем…' : attempt ? 'Повторить загрузку' : 'Загрузить файл'}</button>
    {file && <p className="text-xs wrap-anywhere">Выбран: {file.name}</p>}
    {error && <p role="alert" className="mt-3 text-sm text-red-800">{error}</p>}
    {notice && <p role="status" className="mt-3 text-sm text-accent">{notice}</p>}
  </form></dialog></>
}
