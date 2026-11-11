import {useRef, useState} from 'react'
import {deleteRun, stopRun} from './aiRadarRunsApi'
import type {HistoryEntry} from './aiRadarRunsApi'
import WorkspaceDialog from './WorkspaceDialog'

const count = new Intl.NumberFormat('ru-RU')

export default function RadarRunAction({entry, disabled = false, onDeleted, onStopRequested, onError}: {
  entry: HistoryEntry
  disabled?: boolean
  onDeleted: (runId: string) => void
  onStopRequested: (runId: string) => void
  onError: (message: string) => void
}) {
  const inFlight = useRef(false)
  const [busy, setBusy] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [error, setError] = useState('')
  const archive = entry.kind === 'legacy'
  const active = entry.state === 'queued' || entry.state === 'running'
  const stopping = active && (busy || entry.stop_requested)
  const action = active ? stopping ? 'Останавливаем прогон' : 'Остановить прогон' : archive ? 'Удалить архив' : 'Удалить прогон'
  const progressText = entry.progress === undefined ? entry.state === 'queued' ? 'В очереди' : 'Объём анализа уточняется' : `${entry.progress}%`

  async function execute(stop: boolean) {
    if (inFlight.current) return
    inFlight.current = true
    setBusy(true)
    setError('')
    try {
      if (stop) {
        await stopRun(entry.id)
        onStopRequested(entry.id)
      } else {
        await deleteRun(entry.id)
        setConfirmDelete(false)
        onDeleted(entry.id)
      }
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : 'Не удалось выполнить действие с прогоном.'
      if (stop) onError(message)
      else setError(message)
    } finally {
      inFlight.current = false
      setBusy(false)
    }
  }

  function close() {
    if (!inFlight.current) setConfirmDelete(false)
  }

  return <>
    <div className={`run-history-action${active ? ' run-history-action-active' : ''}`}>
    {active && <svg className="run-progress-ring" viewBox="0 0 36 36" role="progressbar"
      aria-label={`Прогресс анализа ${entry.title}`} aria-valuemin={0} aria-valuemax={100}
      aria-valuenow={entry.progress} aria-valuetext={progressText}>
      <circle className="run-progress-track" cx="18" cy="18" r="16.5"/>
      <circle className={`run-progress-value${entry.progress === undefined && !stopping ? ' run-progress-indeterminate' : ''}`}
        cx="18" cy="18" r="16.5" pathLength="100"
        strokeDasharray={entry.progress === undefined ? '24 76' : `${entry.progress} 100`}/>
    </svg>}
    <button type="button" className="circle-button run-history-action-button"
      aria-label={`${action} ${entry.title}`} title={active ? `${action}. ${progressText}` : action} disabled={disabled || busy || stopping}
      onClick={() => {
        if (active) void execute(true)
        else {
          setError('')
          setConfirmDelete(true)
        }
      }}>
      <svg width="17" height="17" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        {active ? <rect x="5" y="5" width="10" height="10" rx="1.5"/> : <path d="M3.5 5.5h13m-8.5 0v-2h4v2m2.8 0-.7 11h-8l-.7-11m3.1 3v5m3-5v5"/>}
      </svg>
    </button>
    </div>
    {confirmDelete && <WorkspaceDialog title={archive ? 'Удалить архив навсегда?' : 'Удалить прогон навсегда?'} onClose={close}>
      <p className="font-semibold wrap-anywhere">{entry.title}</p>
      <p className="mt-2 text-sm text-muted">Будут удалены все карточки {archive ? 'этого архива' : 'этого прогона'} и связанные результаты. Карточек тендеров — {count.format(entry.card_count)}.</p>
      {error && <p role="alert" className="mt-4 text-sm text-red-800">{error}</p>}
      <div className="mt-5 flex flex-wrap justify-end gap-3">
        <button type="button" className="secondary" onClick={close} disabled={busy}>Отмена</button>
        <button type="button" className="secondary border-red-200 bg-red-50 text-red-800 hover:bg-red-100"
          onClick={() => void execute(false)} disabled={busy}>{busy ? 'Удаляем…' : 'Удалить навсегда'}</button>
      </div>
    </WorkspaceDialog>}
  </>
}
