import { useState } from 'react'
import type { FormEvent } from 'react'
import { voiceSettings } from './voiceApi'

export default function VoiceSettings() {
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!key.trim() || busy) return
    setBusy(true); setSaved(false); setError(null)
    try {
      await voiceSettings(key)
      setSaved(true)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Не удалось сохранить ключ.') }
    finally { setBusy(false) }
  }

  return <section className="panel voice-settings" aria-label="Настройки Groq">
    <h2>Groq: голосовой ввод</h2>
    <form onSubmit={save} autoComplete="off">
      <label className="mt-4 block text-sm font-bold">API-ключ Groq
        <input type="text" value={key} autoCapitalize="none" spellCheck={false} maxLength={8192} disabled={busy}
          onChange={event => { setKey(event.target.value); setSaved(false); setError(null) }} />
      </label>
      <button type="submit" className="primary mt-4" disabled={busy || !key.trim()}>{busy ? 'Сохраняем…' : 'Сохранить ключ Groq'}</button>
    </form>
    {saved && <p role="status" className="mt-2 text-sm">Ключ сохранён.</p>}
    {error && <p role="alert" className="error-note">{error}</p>}
  </section>
}
