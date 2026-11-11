import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { getKonturSettings, saveKonturSettings, getSimulatorQuota, resetSimulatorQuota } from './konturSettingsApi'
import type { KonturConnection, SimulatorQuota } from './konturSettingsApi'
import './KonturSettings.css'

const groups: Record<string, string> = {
  search: 'Поисковые страницы',
  purchase: 'Подробные карточки закупок',
  results: 'Результаты закупок',
  favorites: 'Избранные закупки',
}
const format = new Intl.NumberFormat('ru-RU')

export default function KonturSettings() {
  const [connection, setConnection] = useState<KonturConnection | null>(null)
  const [key, setKey] = useState('')
  const [reading, setReading] = useState(true)
  const [refreshVersion, setRefreshVersion] = useState(0)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')
  const [simulator, setSimulator] = useState<SimulatorQuota | null>(null)
  const [simulatorBusy, setSimulatorBusy] = useState(true)
  const [simulatorError, setSimulatorError] = useState('')
  const [simulatorMessage, setSimulatorMessage] = useState('')

  useEffect(() => {
    const controller=new AbortController()
    setSimulatorBusy(true)
    void getSimulatorQuota(controller.signal).then(value=>{
      if(!controller.signal.aborted){setSimulator(value);setSimulatorError('')}
    },(reason:Error)=>{if(!controller.signal.aborted)setSimulatorError(reason.message)})
      .finally(()=>{if(!controller.signal.aborted)setSimulatorBusy(false)})
    return()=>controller.abort()
  },[refreshVersion])

  async function resetSimulation() {
    if(simulatorBusy||!simulator?.resettable)return
    setSimulatorBusy(true);setSimulatorError('');setSimulatorMessage('')
    try {
      const value=await resetSimulatorQuota()
      setSimulator(value)
      setSimulatorMessage(value.unlimited?'Симуляция уже работает без расходуемой квоты.':'Квота симуляции сброшена.')
    } catch(reason){setSimulatorError((reason as Error).message)}
    finally{setSimulatorBusy(false)}
  }

  useEffect(() => {
    const controller = new AbortController()
    void getKonturSettings(controller.signal).then(value => {
      if (!controller.signal.aborted) setConnection(value)
    }, (reason: Error) => {
      if (!controller.signal.aborted) { setConnection(null); setError(reason.message) }
    }).finally(() => {
      if (!controller.signal.aborted) setReading(false)
    })
    return () => controller.abort()
  }, [refreshVersion])

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (saving || reading || !key.trim()) return
    setSaving(true); setSaved(false); setError('')
    try {
      setConnection(await saveKonturSettings(key))
      setKey(''); setSaved(true)
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return <section className="panel kontur-settings" aria-labelledby="kontur-settings-title">
    <h2 id="kontur-settings-title">Контур.Закупки</h2>
    <form onSubmit={save} autoComplete="off">
      <label className="mt-4 block text-sm font-bold">API-ключ Контура
        <input type="password" value={key} autoComplete="new-password" autoCapitalize="none" spellCheck={false}
          placeholder={connection?.configured ? 'Ключ подключён' : undefined} maxLength={1024} disabled={saving}
          onChange={event => { setKey(event.target.value); setSaved(false) }} />
      </label>
      <button type="submit" className="primary" disabled={reading || saving || !key.trim()}>
        {saving ? 'Проверяем ключ…' : 'Сохранить ключ Контура'}
      </button>
    </form>
    {saved && <p role="status" className="mt-2 text-sm">Ключ сохранён.</p>}
    {error && <p role="alert" className="error-note">{error}</p>}
    {reading && <p role="status" className="mt-2 text-sm text-muted">Получаем лимиты…</p>}
    {connection?.configured && connection.limits.length > 0 && <div className="kontur-quota">
      <div className="kontur-quota-head">
        <h3>Лимиты API</h3>
        <small>Остаток / максимум</small>
      </div>
      <dl aria-label="Квоты Контур.Закупок">
        {connection.limits.map(limit => <div key={limit.group}>
          <dt>{groups[limit.group] || limit.group}{limit.group === 'search' && <small>До 50 закупок на странице</small>}</dt>
          <dd>{format.format(limit.remaining)} / {format.format(limit.maximum)}</dd>
        </div>)}
      </dl>
    </div>}
    {(connection?.configured || error) && <button type="button" className="secondary mt-4"
      disabled={reading || saving || simulatorBusy} onClick={() => { setReading(true); setError(''); setRefreshVersion(value => value + 1) }}>Обновить лимиты</button>}
    <div className="kontur-quota">
      <h3>Симуляция API</h3>
      {simulator?.unlimited && <p>Квота симуляции не ограничена.</p>}
      {simulator?.available && !simulator.unlimited && <dl aria-label="Квоты симуляции API">{simulator.limits.map(limit=><div key={limit.group}>
        <dt>{groups[limit.group]||limit.group}</dt><dd>{format.format(limit.remaining)} / {format.format(limit.maximum)}</dd>
      </div>)}</dl>}
      {simulator && !simulator.available && <p>Симуляция API не подключена.</p>}
      {simulator?.available && !simulator.resettable && <p>Квоты синхронизируются с действующим API.</p>}
      {simulatorError && <p role="alert" className="error-note">{simulatorError}</p>}
      {simulatorMessage && <p role="status">{simulatorMessage}</p>}
      <button type="button" className="secondary mt-4" disabled={simulatorBusy||!simulator?.resettable} onClick={()=>void resetSimulation()}>
        {simulatorBusy?'Получаем квоту…':'Сбросить квоту симуляции API'}
      </button>
      {simulatorError && <button type="button" className="secondary mt-4" disabled={simulatorBusy} onClick={()=>setRefreshVersion(value=>value+1)}>Повторить чтение квоты</button>}
    </div>
  </section>
}
