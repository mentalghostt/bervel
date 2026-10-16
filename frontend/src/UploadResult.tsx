import { useState } from 'react'
import type { ArtifactRef, DatasetSummary } from './api'

export default function UploadResult({ artifact, result }: { artifact: ArtifactRef; result: DatasetSummary }) {
  const [copyMessage, setCopyMessage] = useState('')
  const rowForm = new Intl.PluralRules('ru').select(result.record_count)
  const rowWord = rowForm === 'one' ? 'строка' : rowForm === 'few' ? 'строки' : 'строк'
  const quality = { COMPLETE: 'Полные данные', PARTIAL: 'Есть пропуски', CONFLICTED: 'Есть противоречия' }[result.quality_state]

  async function copyId(label: string, id: string) {
    try {
      await navigator.clipboard.writeText(id)
      setCopyMessage(`${label} скопирован.`)
    } catch {
      setCopyMessage('Не удалось скопировать. Выделите ID и скопируйте вручную.')
    }
  }

  return (
    <section aria-labelledby="result-heading" className="mt-6 rounded-xl border border-accent/25 bg-accent-soft/50 p-5">
      <p className="text-xs font-bold tracking-wider text-accent uppercase">Результат загрузки</p>
      <h2 id="result-heading" className="mt-2 font-display text-xl font-semibold">Сохранено в PostgreSQL</h2>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <p className="text-lg font-bold">{result.record_count} {rowWord}</p>
        <span className={`rounded-full px-3 py-1 text-xs font-bold ${result.quality_state === 'COMPLETE' ? 'bg-accent-soft text-accent' : 'bg-amber-100 text-amber-900'}`}>{quality}</span>
      </div>
      {result.quality_state !== 'COMPLETE' && (
        <p className="mt-3 text-sm leading-6 text-muted">Строки сохранены, но данные требуют проверки. Посмотрите результат в DBeaver.</p>
      )}
      <dl className="mt-5 space-y-4 border-t border-accent/15 pt-4">
        {[
          ['ID оригинала', artifact.artifact_id, artifact.revision],
          ['ID результата', result.dataset_id, result.revision],
        ].map(([label, id, revision]) => (
          <div key={label}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <dt className="text-xs font-bold text-muted">{label} · редакция {revision}</dt>
              <button type="button" onClick={() => copyId(String(label), String(id))} aria-label={`Копировать ${label}`} className="rounded px-1 py-1 text-xs font-bold text-accent underline decoration-accent/30 underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">Копировать</button>
            </div>
            <dd className="mt-1 font-mono text-xs leading-6 wrap-anywhere">{id}</dd>
          </div>
        ))}
      </dl>
      <p role="status" className="mt-3 text-xs leading-5 text-muted">{copyMessage || 'Найдите сохранённые строки в DBeaver по ID результата.'}</p>
    </section>
  )
}
