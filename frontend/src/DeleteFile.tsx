import { useId, useRef, useState } from 'react'
import { deleteUploadedArtifact } from './api'
import type { UploadedArtifact } from './api'

const button = 'rounded-lg border px-4 py-2.5 text-sm font-bold focus-visible:outline-2 focus-visible:outline-offset-4 disabled:cursor-not-allowed disabled:opacity-50'
const removeButton = `${button} border-red-200 bg-red-50 text-red-800 hover:bg-red-100 focus-visible:outline-red-800`

export default function DeleteFile({ file, onDeleted }: { file: UploadedArtifact; onDeleted: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const id = useId()
  const inFlight = useRef(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function remove() {
    if (inFlight.current) return
    inFlight.current = true
    setBusy(true); setError(null)
    try {
      await deleteUploadedArtifact(file)
      dialog.current?.close()
      onDeleted()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Не удалось удалить файл. Повторите попытку.')
    } finally { inFlight.current = false; setBusy(false) }
  }

  return <>
    <button type="button" className={removeButton} onClick={() => { setError(null); dialog.current?.showModal() }}>Удалить навсегда</button>
    <dialog ref={dialog} aria-labelledby={`${id}-title`} aria-describedby={`${id}-description`}
      onCancel={event => { if (busy) event.preventDefault() }}
      className="fixed inset-0 m-auto max-h-[90dvh] w-[calc(100%-2rem)] max-w-lg overflow-y-auto rounded-xl border border-line bg-white p-5 text-ink shadow-xl backdrop:bg-ink/40 sm:p-7">
      <h2 id={`${id}-title`} className="font-display text-2xl font-semibold">Удалить файл навсегда?</h2>
      <p className="mt-4 text-sm font-bold wrap-anywhere">{file.filename}</p>
      <p id={`${id}-description`} className="mt-3 text-sm leading-7">Будут удалены оригинал и данные для поиска ИИ. Восстановить файл через приложение нельзя.</p>
      <p className="mt-3 text-sm leading-7 text-muted">История чатов и отчёты сохранятся. При открытии источника появится «Файл удалён».</p>
      {error && <p role="alert" className="mt-4 rounded-lg bg-red-50 p-3 text-sm leading-6 text-red-800">{error}</p>}
      {busy && <p role="status" className="mt-4 text-sm text-muted">Удаляем файл…</p>}
      <div className="mt-6 flex flex-wrap justify-end gap-3">
        <button type="button" disabled={busy} onClick={() => dialog.current?.close()}
          className={`${button} border-line bg-white text-accent hover:bg-accent-soft focus-visible:outline-accent`}>Отмена</button>
        <button type="button" disabled={busy} onClick={() => void remove()} className={removeButton}>{busy ? 'Удаляем…' : 'Удалить навсегда'}</button>
      </div>
    </dialog>
  </>
}
