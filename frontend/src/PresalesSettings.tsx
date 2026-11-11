import { useEffect, useState } from 'react'
import { usePresalesCommands } from './PresalesCommands'
import PresalesCommandNotice from './PresalesCommandNotice'
import WorkspaceDialog from './WorkspaceDialog'
import { readPresalesSettings } from './presalesApi'
import type { PresalesSettingsSnapshot } from './presalesApi'
import './Presales.css'

export default function PresalesSettings({ onClose, onSaved }: { onClose: () => void; onSaved?: (snapshot: PresalesSettingsSnapshot) => void }) {
  const [snapshot, setSnapshot] = useState<PresalesSettingsSnapshot | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [reload, setReload] = useState(0)
  const command = usePresalesCommands((pending, result) => {
    if (pending.path === '/settings') { const saved = result as PresalesSettingsSnapshot; setSnapshot(saved); setNotice('Настройки сохранены.'); onSaved?.(saved) }
    else { const receipt = result as { request_id: string; revision_id: string }; window.location.hash = `/presales?request=${encodeURIComponent(receipt.request_id)}&revision=${encodeURIComponent(receipt.revision_id)}` }
  })
  useEffect(() => {
    let active = true
    readPresalesSettings().then(value => { if (active) { setSnapshot(value); setError('') } }).catch(reason => { if (active) setError(reason.message) })
    return () => { active = false }
  }, [reload])
  const values = snapshot?.settings
  const target = Number(values?.target_margin_percent), special = Number(values?.special_min_margin_percent)
  const validDecimal = (value: string | undefined) => !!value && /^\d+(?:\.\d+)?$/.test(value)
  const valid = validDecimal(values?.target_margin_percent) && validDecimal(values?.special_min_margin_percent) && target >= 0 && target < 100 && special > target && special < 100
  return <WorkspaceDialog title="Настройки пресейла" className="presales-settings-dialog" onClose={() => { if (!command.busy) onClose() }}>
    <PresalesCommandNotice command={command} />
    {error && <div className="presales-alert"><p role="alert">{error}</p><button type="button" className="secondary" onClick={() => setReload(value => value + 1)}>Повторить загрузку</button></div>}
    {!values ? <p role="status">Получаем настройки…</p> : <form className="presales-settings-form" onSubmit={event => {
      event.preventDefault(); if (!valid) return; setNotice(''); void command.run('/settings', { base_snapshot_id: snapshot.snapshot_id, settings: values }, 'PUT')
    }}>
      <fieldset disabled={command.busy || !!command.pending}>
        <legend>Цена предложения</legend>
        <p className="presales-muted">Маржа задаётся как доля цены продажи. Цена рассчитывается от подтверждённой складской себестоимости.</p>
        <div className="presales-form-grid">{([{ key: 'target_margin_percent', label: 'Целевая маржа, %' }, { key: 'special_min_margin_percent', label: 'Минимальная маржа спецзаказа, %' }] as const).map(({ key, label }) => <label key={key}>{label}<input inputMode="decimal" value={values[key]} onChange={event => { setNotice(''); setSnapshot({ ...snapshot, settings: { ...values, [key]: event.target.value } }) }} required aria-invalid={!valid} /></label>)}</div>
        {!valid && <p role="alert" className="presales-validation">Введите маржу от 0 до 100% без включения 100. Маржа спецзаказа должна быть выше обычной. Десятичный разделитель — точка.</p>}
        <label>Контакты продавца<textarea value={values.seller_contacts} rows={3} onChange={event => { setNotice(''); setSnapshot({ ...snapshot, settings: { ...values, seller_contacts: event.target.value } }) }} placeholder="Имя, телефон, электронная почта" /></label>
        <label>Условия КП<textarea value={values.offer_terms} rows={4} onChange={event => { setNotice(''); setSnapshot({ ...snapshot, settings: { ...values, offer_terms: event.target.value } }) }} placeholder="Согласованные условия и примечания предложения" /></label>
        <div className="presales-form-footer"><p className="presales-muted">{snapshot.requires_save ? 'Перед первой заявкой сохраните настройки.' : 'Изменения применяются к новым редакциям заявок.'}</p><button className="primary" disabled={!valid} type="submit">{command.busy ? 'Сохраняем…' : 'Сохранить настройки'}</button></div>
      </fieldset>
      {notice && <p role="status" className="presales-notice">{notice}</p>}
    </form>}
  </WorkspaceDialog>
}
