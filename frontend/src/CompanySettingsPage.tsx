import {Fragment, useEffect, useEffectEvent, useMemo, useRef, useState} from 'react'
import type {FormEvent} from 'react'
import {isCsvFile, listAllUploadedArtifacts} from './api'
import type {UploadedArtifact} from './api'
import {CompanySettingsError, getCompanySettings, previewCompanySettings, readSaveCommand, saveCompanySettings} from './companySettingsApi'
import type {Capability, ExperimentalProduct, Product, Profile, Resource, SaveCommand, ScoringPolicy, Snapshot} from './companySettingsApi'
import {companyRecoveryKey, settleCompanySave, rememberCompanySave, restoreCompanySave} from './companySettingsRecovery'
import './CompanySettingsPage.css'

const pageSize = 25
function initialRecovery(): {command: SaveCommand | null; reference: string | null; blocked: boolean} {
  try {
    const raw = sessionStorage.getItem(companyRecoveryKey)
    const value = raw ? JSON.parse(raw) : null
    if (value?.storage === 'indexeddb' && typeof value.command_key === 'string' && value.command_key) {
      return {command:null,reference:value.command_key,blocked:false}
    }
    return {command:value ? readSaveCommand(value) : null,reference:null,blocked:false}
  } catch {return {command:null,reference:null,blocked:true}}
}
const errorMessage = (e: unknown) => e instanceof Error ? e.message : 'Не удалось выполнить действие.'
const localDate = (v: string | null) => v ? new Date(Date.parse(v) - new Date(v).getTimezoneOffset() * 60_000).toISOString().slice(0, 16) : ''
const serverDate = (v: string) => v ? new Date(v).toISOString() : null
const optional = (v: string) => v === '' ? null : v
const stockFilenameLabel = (filename: string) => filename.replace(/(^|[_ -])SIMULATION(?=[_ .-]|$)/gi, '').replace(/^[_ -]+/, '')
function draftError(profile: Profile): string | null {
  const decimal = (v: string | null) => v === null || /^\d+(?:\.\d+)?$/.test(v)
  for (const p of profile.products) {
    if (!p.name.trim() || !p.unit.trim()) return `Продукция ${p.sku ?? p.id}: заполните название и единицу измерения.`
    if (Object.entries(p.attributes).some(([key, value]) => !key.trim() || !value.trim())) return `Продукция ${p.sku ?? p.id}: атрибуты не должны быть пустыми. Заполните значение либо удалите атрибут.`
  }
  for (const p of profile.experimental_products) {
    if (!p.name.trim() || !p.unit.trim()) return 'Заполните название и единицу каждого экспериментального продукта.'
    if (Object.entries(p.attributes).some(([key, value]) => !key.trim() || !value.trim())) return `Экспериментальная продукция ${p.name}: заполните или удалите пустые атрибуты.`
  }
  const names = profile.resources.map(r => r.group_id.trim())
  if (names.some(n => !n) || new Set(names).size !== names.length) return 'У каждого станка или линии должно быть непустое уникальное название.'
  const products = new Map([...profile.products, ...profile.experimental_products].map(product => [product.id, product]))
  for (const p of profile.production) {
    const product = products.get(p.product_id)
    const prefix = `Выпуск продукции ${product?.sku ?? product?.name ?? p.product_id}`
    if (!profile.resources.some(r => r.group_id === p.resource_group_id)) return `${prefix}: выберите существующий станок или линию.`
    if (!p.setup_workdays || !decimal(p.setup_workdays)) return `${prefix}: укажите подтверждённое число рабочих дней подготовки. Ноль введите явно.`
    if (![p.units_per_workday, p.production_unit_cost, p.setup_cost ?? null].every(decimal)) return `${prefix}: числа вводятся с точкой, без пробелов; неизвестное оставьте пустым.`
    if (p.currency !== null && !/^[A-Z]{3}$/.test(p.currency)) return `${prefix}: валюта указывается тремя латинскими буквами, например RUB.`
    if ((p.production_unit_cost !== null || p.setup_cost != null) && !p.currency) return `${prefix}: укажите валюту затрат.`
    if ((p.units_per_workday !== null || p.production_unit_cost !== null || p.setup_cost != null) && !p.evidence?.trim()) return `${prefix}: числовые производственные данные требуют источника.`
  }
  for (const r of profile.resources) {
    if (r.default_daily_capacity == null) continue
    if (!decimal(r.default_daily_capacity) || Number(r.default_daily_capacity) > 1) return `Станок или линия «${r.group_id}»: свободная доля рабочего дня должна быть от 0 до 1.`
    if (!r.calendar.length) return `Станок или линия «${r.group_id}»: выберите хотя бы один рабочий день для указанной свободной доли.`
  }
  return null
}

function ProductAttributes<T extends Product | ExperimentalProduct>({product, onChange}: {product: T; onChange: (value: T) => void}) {
  const [name, setName] = useState('')
  const [content, setContent] = useState('')
  const {attributes} = product
  const setAttributes = (next: Record<string, string>) => onChange({...product, attributes: next})
  return <div>
    <h3>Атрибуты продукции</h3>
    <div className="company-product-attributes">
      <label>Артикул (необязательно)<input maxLength={100} value={product.sku ?? ''} onChange={e => onChange({...product, sku: optional(e.target.value)})} placeholder="Без артикула"/></label>
      {Object.entries(attributes).map(([key, value]) => <label key={key}>{key}<span><input required aria-label={`Значение атрибута ${key}`} value={value} onChange={e => setAttributes({...attributes, [key]: e.target.value})}/><button type="button" className="company-small" aria-label={`Удалить атрибут ${key}`} onClick={() => setAttributes(Object.fromEntries(Object.entries(attributes).filter(([attribute]) => attribute !== key)))}>Удалить</button></span></label>)}
      <div className="company-attribute-add"><label>Новый атрибут<input value={name} onChange={e => setName(e.target.value)} placeholder="Например, ГОСТ"/></label><label>Значение<input value={content} onChange={e => setContent(e.target.value)}/></label><button type="button" className="company-small" disabled={!name.trim() || !content.trim() || Object.hasOwn(attributes, name.trim())} onClick={() => {setAttributes({...attributes, [name.trim()]: content}); setName(''); setContent('')}}>Добавить атрибут</button></div>
    </div>
  </div>
}

function CapabilityEditor({value, resources, onChange, onRemove}: {value: Capability; resources: Resource[]; onChange: (v: Capability) => void; onRemove: () => void}) {
  const patch = (v: Partial<Capability>) => onChange({...value, ...v})
  return <div className="company-capability">
    <div className="company-fields">
      <label>Станок или линия<select aria-label="Станок или линия" required value={value.resource_group_id} onChange={e => patch({resource_group_id: e.target.value})}><option value="">Выберите станок или линию</option>{resources.map((r, i) => <option key={i} value={r.group_id}>{r.group_id || 'Станок или линия без названия'}</option>)}</select></label>
      <label>Выпуск за рабочий день<input inputMode="decimal" pattern="\d+(\.\d+)?" placeholder="Неизвестно" value={value.units_per_workday ?? ''} onChange={e => patch({units_per_workday: optional(e.target.value)})}/></label>
      <label>Себестоимость за единицу<input inputMode="decimal" pattern="\d+(\.\d+)?" placeholder="Неизвестно" value={value.production_unit_cost ?? ''} onChange={e => patch({production_unit_cost: optional(e.target.value)})}/></label>
      <label>Валюта<input placeholder="Неизвестно" maxLength={3} pattern="[A-Z]{3}" value={value.currency ?? ''} onChange={e => patch({currency: optional(e.target.value.toUpperCase())})}/></label>
      <label>Подготовка, рабочих дней<input required inputMode="decimal" pattern="\d+(\.\d+)?" placeholder="Укажите подтверждённое значение" value={value.setup_workdays} onChange={e => patch({setup_workdays: e.target.value})}/></label>
      <label>Стоимость подготовки<input inputMode="decimal" pattern="\d+(\.\d+)?" placeholder="Неизвестно" value={value.setup_cost ?? ''} onChange={e => patch({setup_cost: optional(e.target.value)})}/></label>
      <label>Материалы готовы к<input type="datetime-local" value={localDate(value.material_ready_at)} onChange={e => patch({material_ready_at: serverDate(e.target.value)})}/></label>
      <label>Основание производственных данных<input placeholder="Документ, ответственный или источник" value={value.evidence ?? ''} onChange={e => patch({evidence: e.target.value || undefined})}/></label>
    </div><button type="button" className="company-small" onClick={onRemove}>Удалить производственную запись</button>
  </div>
}

function ResourceEditor({value, onChange, onRemove}: {value: Resource; onChange: (r: Resource) => void; onRemove: () => void}) {
  const patch = (v: Partial<Resource>) => onChange({...value, ...v})
  const days = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
  return <article className="company-resource">
    <label className="company-resource-active"><input type="checkbox" checked={value.enabled !== false} onChange={e => patch({enabled: e.target.checked})}/>Станок или линия активны</label>
    <div className="company-fields"><label>Название станка или линии<input required value={value.group_id} onChange={e => patch({group_id: e.target.value})} placeholder="Например, линия № 1"/></label>
      <label>Свободная доля рабочего дня по умолчанию<input type="number" min="0" max="1" step="any" placeholder="Неизвестно" value={value.default_daily_capacity ?? ''} onChange={e => patch({default_daily_capacity: optional(e.target.value)})}/></label></div>
    <p className="company-help">0 — рабочий день полностью занят. 1 — полностью свободен.</p>
    <fieldset className="company-calendar"><legend>Рабочие дни недели</legend>{days.map((day, i) => <label key={day}><input type="checkbox" checked={value.calendar.includes(i)} onChange={e => patch({calendar: e.target.checked ? [...value.calendar, i].sort() : value.calendar.filter(d => d !== i)})}/>{day}</label>)}</fieldset>
    <button type="button" className="company-small" onClick={onRemove}>Удалить станок или линию</button>
  </article>
}

function ProductTable<T extends Product | ExperimentalProduct>({products, experimental = false, expanded, onExpand, onChange, onRemove, production, resources, onProduction}: {
  products: T[]; experimental?: boolean; expanded: string | null; onExpand: (id: string | null) => void; onChange: (product: T) => void;
  onRemove?: (id: string) => void; production: Capability[]; resources: Resource[]; onProduction: (next: Capability[]) => void;
}) {
  return <div className="company-table-scroll" role="region" aria-label={experimental ? 'Таблица экспериментальной продукции' : 'Таблица продукции'} tabIndex={0}>
    <table className="company-table">
      <thead><tr><th scope="col" className="company-activity">Активность</th><th scope="col">Артикул / продукция</th><th scope="col">Единица</th><th scope="col">Данные</th></tr></thead>
      <tbody>{products.map(product => {
        const open = expanded === product.id
        const id = `company-product-${product.id}`
        const caption = product.name || product.sku || 'нового продукта'
        return <Fragment key={product.id}>
          <tr className={`company-product-row${open ? ' company-product-row-open' : ''}`}>
            <td className="company-activity"><input type="checkbox" aria-label={experimental ? `Включить эксперимент ${product.name}` : `Включить ${product.name} в поиск`} checked={product.enabled} onChange={e => onChange({...product, enabled: e.target.checked})}/></td>
            <td><input aria-label={experimental ? 'Название экспериментального продукта' : `Название продукции ${product.sku ?? product.id}`} required value={product.name} onChange={e => onChange({...product, name: e.target.value})}/><small>{product.sku ?? 'Без артикула'}</small></td>
            <td><input aria-label={experimental ? 'Единица экспериментального продукта' : `Единица ${product.sku ?? product.id}`} required value={product.unit} onChange={e => onChange({...product, unit: e.target.value})}/></td>
            <td><button type="button" className="company-small company-product-toggle" aria-label={`Редактировать данные ${caption}`} aria-expanded={open} aria-controls={open ? id : undefined} onClick={() => onExpand(open ? null : product.id)}>{open ? 'Свернуть' : 'Открыть'}</button></td>
          </tr>
          {open && <tr className="company-product-editor-row"><td colSpan={4}>
            <div className="company-product-details" id={id}>
              <ProductAttributes product={product} onChange={onChange}/>
              <div className="company-product-production">
                <h3>Производственные возможности</h3>
                <p className="company-help">Пустое поле означает, что значение неизвестно. Если выпуск, себестоимость или стоимость подготовки равны нулю, укажите 0 и источник данных.</p>
                {production.map((cap, i) => cap.product_id === product.id ? <CapabilityEditor key={i} value={cap} resources={resources} onChange={next => onProduction(production.map((old, index) => index === i ? next : old))} onRemove={() => onProduction(production.filter((_, index) => index !== i))}/> : null)}
                <button type="button" className="company-small" onClick={() => onProduction([...production, {product_id: product.id, resource_group_id: '', units_per_workday: null, production_unit_cost: null, setup_cost: null, currency: null, setup_workdays: '', material_ready_at: null}])}>Добавить возможность выпуска</button>
              </div>
              {onRemove && <div className="company-product-footer"><button type="button" className="company-small" onClick={() => onRemove(product.id)}>Удалить эксперимент</button></div>}
            </div>
          </td></tr>}
        </Fragment>
      })}</tbody>
    </table>
  </div>
}

const policyFields = {
  version: {label: 'Метка набора правил', hint: 'В истории прогона'},
  target_margin: {label: 'Маржа для максимума по экономике', hint: 'Доля от 0 до 1, выше 0'},
  comfortable_utilization: {label: 'Загрузка без снижения балла', hint: 'Доля от 0 до 1, ниже 1'},
  minimum_production_factor: {label: 'Вес допроизводства при полной загрузке', hint: 'Доля от 0 до 1'},
  high_score: {label: 'Балл для высокого приоритета', hint: 'От 0 до 100'},
  medium_score: {label: 'Балл для среднего приоритета', hint: 'От 0 до 100'},
}
type PolicyKey = keyof ScoringPolicy
type PolicyDraft = Partial<Record<PolicyKey, string>>
type PolicySubmission = {key: string; edits: PolicyDraft}
// This page unmounts during hash navigation. Keep the small draft in this tab;
// the existing IndexedDB recovery stores the exact command across full reloads.
let policyDraftBuffer: {baseId: string; edits: PolicyDraft; submission?: PolicySubmission} | null = null
let policyWriteInFlight: Promise<void> | null = null
function keepPolicyDraft(baseId: string, edits: PolicyDraft, submission?: PolicySubmission) {
  policyDraftBuffer = Object.keys(edits).length ? {baseId, edits, submission} : null
}
const policyKeys = Object.keys(policyFields) as PolicyKey[]
// The server supplies effective values. These are for older snapshot responses.
const legacyPolicyDefaults: ScoringPolicy = {version: 'score-v1', target_margin: '0.30', comfortable_utilization: '0.70', minimum_production_factor: '0.60', high_score: 80, medium_score: 50}
const scoreKey = (key: PolicyKey) => key === 'high_score' || key === 'medium_score'
const policyValues = (policy: Record<string, unknown> | undefined, effective?: ScoringPolicy): Record<PolicyKey, string> => ({
  version: String(effective?.version ?? policy?.version ?? legacyPolicyDefaults.version),
  target_margin: String(effective?.target_margin ?? policy?.target_margin ?? legacyPolicyDefaults.target_margin),
  comfortable_utilization: String(effective?.comfortable_utilization ?? policy?.comfortable_utilization ?? legacyPolicyDefaults.comfortable_utilization),
  minimum_production_factor: String(effective?.minimum_production_factor ?? policy?.minimum_production_factor ?? legacyPolicyDefaults.minimum_production_factor),
  high_score: String(effective?.high_score ?? policy?.high_score ?? legacyPolicyDefaults.high_score),
  medium_score: String(effective?.medium_score ?? policy?.medium_score ?? legacyPolicyDefaults.medium_score),
})
function policyError(value: Record<PolicyKey, string>): string | null {
  const decimal = (v: string) => /^\d{1,19}(?:\.\d{1,12})?$/.test(v)
  const score = (v: string) => /^\d+$/.test(v) && Number(v) <= 100
  if (!value.version.trim() || value.version.length > 64) return 'Укажите метку набора правил длиной до 64 символов.'
  if (!decimal(value.target_margin) || Number(value.target_margin) <= 0 || Number(value.target_margin) > 1) return 'Введите маржу больше 0 и не выше 1. Для десятичных значений используйте точку.'
  if (!decimal(value.comfortable_utilization) || Number(value.comfortable_utilization) >= 1) return 'Введите загрузку от 0 до 1, исключая 1.'
  if (!decimal(value.minimum_production_factor) || Number(value.minimum_production_factor) > 1) return 'Введите вес допроизводства от 0 до 1.'
  if (!score(value.high_score) || !score(value.medium_score)) return 'Введите целые баллы от 0 до 100.'
  if (Number(value.medium_score) > Number(value.high_score)) return 'Балл для среднего приоритета должен быть не выше балла для высокого приоритета.'
  return null
}
function PolicyFields({value, onChange}: {value: Record<PolicyKey, string>; onChange: (key: PolicyKey, value: string) => void}) {
  return <div className="company-fields company-policy-fields">{policyKeys.map(key => {
    const hintId = `company-policy-${key}-hint`
    return <div className="company-policy-field" key={key}>
      <label>{policyFields[key].label}<input aria-describedby={hintId} inputMode={key === 'version' ? 'text' : scoreKey(key) ? 'numeric' : 'decimal'} maxLength={key === 'version' ? 64 : undefined} required value={value[key]} onChange={e => onChange(key, e.target.value)}/></label>
      <small id={hintId}>{policyFields[key].hint}</small>
    </div>
  })}</div>
}

export default function CompanySettingsPage({presentation = 'company'}: {presentation?: 'company' | 'radar-policy'}) {
  const policyOnly = presentation === 'radar-policy'
  const [recovery] = useState(initialRecovery)
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [profile, setProfile] = useState<Profile | null>(recovery.command?.profile ?? null)
  const [files, setFiles] = useState<UploadedArtifact[]>([])
  const [stockId, setStockId] = useState(recovery.command?.profile.stock_artifact_id ?? '')
  const [loaded, setLoaded] = useState(recovery.command !== null)
  const [dirty, setDirty] = useState(recovery.command !== null)
  const [busy, setBusy] = useState<'load' | 'preview' | 'save' | null>('load')
  const [error, setError] = useState<string | null>(recovery.blocked ? 'Не удалось прочитать сохранённую команду. Не очищайте данные вкладки: восстановите доступ к хранилищу браузера и обновите страницу.' : null)
  const [notice, setNotice] = useState<string | null>(recovery.command ? 'Есть отправленная команда без подтверждённого результата. Повторите её с теми же данными.' : null)
  const [warnings, setWarnings] = useState<string[]>([])
  const [pending, setPending] = useState<SaveCommand | null>(recovery.command)
  const [conflict, setConflict] = useState(false)
  const [recoveryBlocked, setRecoveryBlocked] = useState(recovery.blocked)
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState('all')
  const [page, setPage] = useState(0)
  const [expandedProduct, setExpandedProduct] = useState<string | null>(null)
  const [expandedExperiment, setExpandedExperiment] = useState<string | null>(null)
  const [newProductIds, setNewProductIds] = useState<string[]>([])
  const [policyDraft, setPolicyDraft] = useState<PolicyDraft>({})
  const alive = useRef(false)
  const revision = useRef(0)
  const operation = useRef(false)

  const apply = (v: Snapshot | null) => {setSnapshot(v);setProfile(v?.profile ?? null);setStockId(v?.profile.stock_artifact_id ?? '');setDirty(false);setWarnings([]);setPage(0);setNewProductIds([])}
  useEffect(() => {
    alive.current = true
    const current = ++revision.current
    async function load() {
      await policyWriteInFlight
      if (!alive.current || revision.current !== current) return
      const activeRecovery = initialRecovery()
      const [settings, artifacts, restored] = await Promise.allSettled([getCompanySettings(), policyOnly ? Promise.resolve([]) : listAllUploadedArtifacts(),
        activeRecovery.reference ? restoreCompanySave(activeRecovery.reference) : Promise.resolve(activeRecovery.command)])
      if (!alive.current || revision.current !== current) return
      const recovered = restored.status === 'fulfilled' ? restored.value : null
      setPending(recovered)
      setRecoveryBlocked(activeRecovery.blocked || restored.status === 'rejected')
      if (recovered) {
        setPending(recovered);setProfile(recovered.profile);setStockId(recovered.profile.stock_artifact_id)
        setLoaded(true);setDirty(true);setNotice('Есть отправленная команда без подтверждённого результата. Повторите её с теми же данными.')
      }
      if (artifacts.status === 'fulfilled') setFiles(artifacts.value.filter(isCsvFile))
      if (settings.status === 'fulfilled') {
        if (!recovered) {apply(settings.value);setNotice(null)} else setSnapshot(settings.value)
        setLoaded(true)
        if (policyOnly && policyDraftBuffer && (settings.value || recovered)) {
          setPolicyDraft(policyDraftBuffer.edits)
          if (settings.value?.id !== policyDraftBuffer.baseId && (!recovered || recovered.command_key !== policyDraftBuffer.submission?.key)) setConflict(true)
        }
      }
      const failed = [settings, artifacts, restored].filter(v => v.status === 'rejected')
      if (failed.length) setError(failed.map(v => v.status === 'rejected' ? errorMessage(v.reason) : '').join(' '))
      setBusy(null)
    }
    void load()
    return () => {alive.current = false;revision.current = current + 1}
  }, [recovery, policyOnly])
  const locked = busy !== null || pending !== null || conflict || recoveryBlocked || !loaded
  const policyBase = policyValues(profile?.scoring_policy, profile === snapshot?.profile ? snapshot?.effective_scoring_policy : undefined)
  const policyValue = {...policyBase, ...policyDraft}
  const changedPolicy = policyKeys.filter(key => policyDraft[key] !== undefined && policyValue[key] !== policyBase[key])
  const policyDirty = changedPolicy.length > 0
  const policyProblem = policyDirty ? policyError(policyValue) : null
  const savingPolicy = policyOnly && busy === 'save' && pending !== null && policyDraftBuffer?.submission?.key === pending.command_key
  const hasUnsentPolicy = policyOnly && policyKeys.some(key => policyDraft[key] !== undefined && policyDraft[key] !== policyDraftBuffer?.submission?.edits[key])
  useEffect(() => {
    const beforeUnload = (e: BeforeUnloadEvent) => {
      if ((dirty && !pending) || hasUnsentPolicy) {e.preventDefault();e.returnValue = ''}
    }
    window.addEventListener('beforeunload', beforeUnload)
    return () => window.removeEventListener('beforeunload', beforeUnload)
  }, [dirty, pending, hasUnsentPolicy])

  function edit(next: Profile) {if (locked) return;setProfile(next);setDirty(true);setError(null);setNotice(null)}
  function editPolicy(key: PolicyKey, value: string) {
    if (!snapshot || (locked && !savingPolicy)) return
    const next = {...policyDraft, [key]: value}
    if (!savingPolicy && value === policyBase[key]) delete next[key]
    keepPolicyDraft(policyDraftBuffer?.baseId ?? snapshot.id, next, policyDraftBuffer?.submission)
    setPolicyDraft(next)
    setError(null);setNotice(null)
  }
  async function preview() {
    if (locked || !stockId || operation.current) return
    operation.current = true;setBusy('preview');setError(null);setNotice(null)
    const current = ++revision.current
    try {const v = await previewCompanySettings(stockId, snapshot?.id ?? null);if (!alive.current || current !== revision.current) return;setProfile(v.profile);setWarnings(v.warnings);setDirty(true);setPage(0);setNotice('Предпросмотр готов. Проверьте изменения и сохраните профиль.')}
    catch(e) {if (alive.current && current === revision.current) {setError(errorMessage(e));if (e instanceof CompanySettingsError && e.status === 409) setConflict(true)}}
    finally {operation.current = false;if (alive.current && current === revision.current) setBusy(null)}
  }
  async function send(command: SaveCommand, submission?: PolicySubmission) {
    operation.current = true;setPending(command);setBusy('save');setError(null);setNotice(null)
    const current = ++revision.current
    const isCurrent = () => alive.current && current === revision.current
    const clearPolicySubmission = () => {
      if (submission && policyDraftBuffer?.submission?.key === submission.key) keepPolicyDraft(policyDraftBuffer.baseId, policyDraftBuffer.edits)
    }
    try {await rememberCompanySave(command)} catch {
      operation.current = false
      clearPolicySubmission()
      if (isCurrent()) {setPending(null);setBusy(null);setError('Хранилище браузера недоступно. Сохранение не отправлено: без него нельзя безопасно повторить команду после потери соединения.')}
      return
    }
    if (!isCurrent() && !submission) {operation.current = false;return}
    try {
      const result = await saveCompanySettings(command)
      if (!isCurrent() && !submission) return
      await settleCompanySave(command,true)
      if (submission && policyDraftBuffer?.submission?.key === submission.key) {
        const next = {...policyDraftBuffer.edits}
        for (const key of policyKeys) if (next[key] === submission.edits[key]) delete next[key]
        keepPolicyDraft(result.id, next)
        if (isCurrent()) setPolicyDraft(next)
      }
      if (!isCurrent()) return
      setPending(null);apply(result);setConflict(policyOnly && !!policyDraftBuffer && policyDraftBuffer.baseId !== result.id);setNotice('Профиль сохранён. Новые прогоны используют эту версию; прежняя история сохраняется.')
    } catch(e) {
      if (!isCurrent() && !submission) return
      if (isCurrent()) setError(errorMessage(e))
      if (e instanceof CompanySettingsError && e.rejected) {
        try {await settleCompanySave(command,false)} catch {if (isCurrent()) setError('Команда отклонена сервером, но её не удалось убрать из хранилища браузера. Восстановите доступ к хранилищу браузера и повторите эту команду для подтверждения отказа.');return}
        clearPolicySubmission()
        if (isCurrent()) {setPending(null);if (e.status === 409) setConflict(true)}
      }
      else if (isCurrent()) setNotice('Результат сохранения неизвестен. Редактирование заблокировано до явного повтора этой же команды.')
    } finally {operation.current = false;if (isCurrent()) setBusy(null)}
  }
  function startSave(command: SaveCommand) {
    if (operation.current || policyWriteInFlight) return
    const submission = policyDraftBuffer?.submission?.key === command.command_key ? policyDraftBuffer.submission : undefined
    const writing = send(command, submission)
    if (submission) {
      policyWriteInFlight = writing
      void writing.then(() => {if (policyWriteInFlight === writing) policyWriteInFlight = null})
    }
  }
  const autosavePolicy = useEffectEvent(() => {
    if (!profile || !snapshot || !policyDirty || policyProblem || locked || operation.current || policyWriteInFlight || error) return
    const scoringPolicy = {...profile.scoring_policy}
    const edits: PolicyDraft = {}
    for (const key of changedPolicy) {
      edits[key] = policyValue[key]
      scoringPolicy[key] = scoreKey(key) ? Number(policyValue[key]) : policyValue[key].trim()
    }
    const key = crypto.randomUUID()
    keepPolicyDraft(snapshot.id, policyDraft, {key, edits})
    startSave({command_key: key, base_snapshot_id: snapshot.id, profile: {...profile, scoring_policy: scoringPolicy}})
  })
  useEffect(() => {
    if (!policyOnly || locked || !policyDirty || policyProblem || error) return
    const timer = window.setTimeout(autosavePolicy, 500)
    return () => window.clearTimeout(timer)
  }, [policyOnly, locked, policyDirty, policyDraft, policyProblem, error])

  function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();if (locked || !profile || !dirty) return
    const problem = draftError(profile)
    if (problem) {setError(problem);return}
    const invalid = e.currentTarget.querySelector<HTMLElement>('input:invalid, select:invalid, textarea:invalid')
    if (invalid) {let parent = invalid.parentElement;while (parent) {if (parent instanceof HTMLDetailsElement) parent.open = true;parent = parent.parentElement} e.currentTarget.reportValidity();return}
    startSave({command_key: crypto.randomUUID(), base_snapshot_id: snapshot?.id ?? null, profile: structuredClone(profile)})
  }
  async function reloadCurrent() {
    if (operation.current || pending || recoveryBlocked) return
    operation.current = true;setBusy('load');setError(null)
    const current = ++revision.current
    try {const [next, artifacts] = await Promise.all([getCompanySettings(), policyOnly ? Promise.resolve([]) : listAllUploadedArtifacts()]);if (alive.current && current === revision.current) {apply(next);if (policyOnly) {policyDraftBuffer = null;setPolicyDraft({})}setFiles(artifacts.filter(isCsvFile));setLoaded(true);setConflict(false);setNotice('Актуальная версия загружена. Несохранённые правки заменены.')}} catch(e) {if (alive.current && current === revision.current) setError(errorMessage(e))}
    finally {operation.current = false;if (alive.current && current === revision.current) setBusy(null)}
  }

  const filtered = useMemo(() => profile?.products.filter(p => (!search || `${p.name} ${p.sku ?? ''} ${Object.values(p.attributes).join(' ')}`.toLocaleLowerCase('ru').includes(search.toLocaleLowerCase('ru'))) && (filter === 'all' || (filter === 'enabled' ? p.enabled : !p.enabled))) ?? [], [profile, search, filter])
  const lastPage = Math.max(0, Math.ceil(filtered.length / pageSize) - 1)
  const visiblePage = Math.min(page, lastPage)
  const rows = filtered.slice(visiblePage * pageSize, (visiblePage + 1) * pageSize)
  function productChange(product: Product) {if (profile) edit({...profile, products: profile.products.map(p => p.id === product.id ? product : p)})}
  function addProduct() {
    if (!profile || locked) return
    const id = crypto.randomUUID()
    edit({...profile, products: [...profile.products, {id, sku: null, name: '', unit: 'шт', attributes: {}, enabled: true}]})
    setSearch('');setFilter('all');setPage(Math.floor(profile.products.length / pageSize));setExpandedProduct(id);setNewProductIds(previous => [...previous, id])
  }
  function experimentalChange(product: ExperimentalProduct) {if (profile) edit({...profile, experimental_products: profile.experimental_products.map(p => p.id === product.id ? product : p)})}
  function addExperiment() {
    if (!profile || locked) return
    const id = crypto.randomUUID()
    edit({...profile, experimental_products: [...profile.experimental_products, {id, sku: null, name: '', unit: 'шт', attributes: {}, enabled: false}]})
    setExpandedExperiment(id)
  }
  function removeExperiment(id: string) {
    if (!profile || locked) return
    edit({...profile, experimental_products: profile.experimental_products.filter(p => p.id !== id), production: profile.production.filter(cap => cap.product_id !== id)})
    setExpandedExperiment(null)
  }

  const feedback = <>
    {error && <div role="alert" className="company-alert">{error}</div>}
    {notice && (!policyOnly || !policyDirty || pending) && <p role="status" className="company-notice">{notice}</p>}
    {pending && !savingPolicy && <div className="company-recovery"><p>Команда сохранена в этой вкладке. При повторе сервер использует тот же ключ и не создаёт вторую версию.</p><button type="button" className="primary" disabled={busy !== null} onClick={() => startSave(pending)}>{busy === 'save' ? 'Проверяем сохранение…' : 'Повторить сохранение'}</button></div>}
    {(conflict || (!loaded && !busy)) && <div className="company-recovery"><p>{conflict ? 'Другой пользователь сохранил новую версию. Загрузка заменит ваши несохранённые правки актуальным профилем.' : 'Настройки не загружены.'}</p><button type="button" className="secondary" disabled={busy !== null || pending !== null} onClick={() => void reloadCurrent()}>{conflict ? 'Заменить правки актуальной версией' : 'Повторить загрузку'}</button></div>}
  </>
  const saveActions = <div className="company-savebar">{(pending || dirty) && <span className="company-save-state">{pending ? 'Ожидается подтверждение сохранения' : 'Есть несохранённые изменения'}</span>}<button type="submit" className="primary" disabled={locked || !profile || !dirty}>{busy === 'save' ? 'Сохраняем…' : 'Сохранить профиль'}</button>{dirty && !pending && !conflict && <button type="button" className="secondary" disabled={busy !== null} onClick={() => void reloadCurrent()}>Отменить правки и загрузить сохранённое</button>}</div>

  if (policyOnly) return <section className="panel company-settings" aria-labelledby="radar-policy-title">
    <h2 id="radar-policy-title">Настройки тендерного радара</h2>
    <p className="company-help">Правила оценки для новых прогонов. Изменения сохраняются автоматически, когда все значения допустимы.</p>
    {busy === 'load' && <p role="status">Получаем настройки радара…</p>}
    {loaded && !profile && <p className="company-help">Сначала сохраните профиль в разделе <a href="#/company-settings">Настройки предприятия</a>.</p>}
    <fieldset className="company-editor" aria-label="Правила оценки" disabled={locked && !savingPolicy}>{profile && <PolicyFields value={policyValue} onChange={editPolicy}/>}</fieldset>
    {feedback}
    {policyProblem && !locked && <p role="alert" className="company-policy-error">{policyProblem}</p>}
    {savingPolicy && <p role="status" className="company-save-state">Сохраняем настройки радара…</p>}
    {policyDirty && !locked && !policyProblem && !error && <p role="status" className="company-save-state">Ожидается автосохранение…</p>}
    {policyDirty && !locked && !policyProblem && error && <button type="button" className="secondary" onClick={() => setError(null)}>Повторить автосохранение</button>}
  </section>

  return <main className="company-settings" aria-label="Настройки предприятия">
    <div className="company-toolbar"><a href="#/settings" className="secondary">Подключение модели</a></div>
    {feedback}
    <form onSubmit={save} noValidate aria-busy={busy !== null}>
      <fieldset disabled={locked} className="company-editor">
        <section className="company-section company-import" aria-labelledby="company-stock-title"><div><h2 id="company-stock-title">Складской источник</h2></div>
          <div className="company-import-controls"><label>Складской CSV<select aria-label="Складской CSV" value={stockId} onChange={e => setStockId(e.target.value)}><option value="">Выберите файл</option>{files.map(f => <option key={f.artifact_id} value={f.artifact_id}>{stockFilenameLabel(f.filename)}</option>)}</select></label><button type="button" className="secondary" disabled={!stockId || dirty} onClick={() => void preview()}>{busy === 'preview' ? 'Читаем склад…' : 'Предпросмотр импорта'}</button></div>
          {dirty && <p className="company-help">Перед новым импортом сохраните текущие правки или явно загрузите сохранённую версию.</p>}{!files.length && loaded && <p className="company-help">Нет файлов CSV. <a href="#/files">Загрузите складской файл.</a></p>}
          {profile && <p className="company-source">Источник профиля: {stockFilenameLabel(files.find(f => f.artifact_id === profile.stock_artifact_id)?.filename ?? 'Складской CSV')}</p>}
          {warnings.length > 0 && <div className="company-warnings"><h3>Проверьте импорт</h3><ul>{warnings.map((warning, i) => <li key={i}>{warning}</li>)}</ul></div>}
        </section>
        {profile && <>
          <section className="company-section" aria-labelledby="company-products-title"><div className="company-section-heading"><div><h2 id="company-products-title">Продукция <small>{profile.products.length}</small></h2></div><span className="company-count">Активны {profile.products.filter(p => p.enabled).length}</span></div>
            <div className="company-table-tools"><label>Найти продукцию<input type="search" value={search} onChange={e => {setSearch(e.target.value);setPage(0)}} placeholder="Название, артикул или атрибут"/></label><label>Показывать<select aria-label="Показывать" value={filter} onChange={e => {setFilter(e.target.value);setPage(0)}}><option value="all">Всю продукцию</option><option value="enabled">Включённую в поиск</option><option value="disabled">Исключённую из поиска</option></select></label></div>
            <ProductTable products={rows} expanded={expandedProduct} onExpand={setExpandedProduct} onChange={productChange} resources={profile.resources} production={profile.production} onProduction={production => edit({...profile, production})}/>
            {!rows.length && <p className="company-empty">По этим условиям продукция не найдена.</p>}<div className="company-pagination"><span>Найдено: {filtered.length} · страница {visiblePage + 1} из {lastPage + 1}</span><button type="button" className="company-small" disabled={visiblePage === 0} onClick={() => setPage(visiblePage - 1)}>Назад</button><button type="button" className="company-small" disabled={visiblePage === lastPage} onClick={() => setPage(visiblePage + 1)}>Далее</button></div><button type="button" className="secondary" onClick={addProduct}>Добавить продукт</button>
            {rows.filter(p => newProductIds.includes(p.id)).map(p => <button type="button" className="company-small" key={p.id} onClick={() => {edit({...profile, products: profile.products.filter(old => old.id !== p.id), production: profile.production.filter(cap => cap.product_id !== p.id)});setNewProductIds(ids => ids.filter(id => id !== p.id));setExpandedProduct(null)}}>Убрать новую строку{p.name ? ` «${p.name}»` : ''}</button>)}
          </section>
          <section className="company-section" aria-labelledby="company-experimental-title"><h2 id="company-experimental-title">Экспериментальная продукция</h2><label className="company-check"><input type="checkbox" checked={profile.include_experimental} onChange={e => edit({...profile, include_experimental: e.target.checked})}/>Учитывать экспериментальную продукцию в новых прогонах</label>
            <ProductTable products={profile.experimental_products} experimental expanded={expandedExperiment} onExpand={setExpandedExperiment} onChange={experimentalChange} onRemove={removeExperiment} resources={profile.resources} production={profile.production} onProduction={production => edit({...profile, production})}/>
            {!profile.experimental_products.length && <p className="company-empty">Экспериментальные позиции ещё не добавлены.</p>}<button type="button" className="secondary" onClick={addExperiment}>Добавить экспериментальный продукт</button>
          </section>
          <section className="company-section" aria-labelledby="company-resources-title"><h2 id="company-resources-title">Производственные мощности</h2>
            {profile.resources.map((r, i) => <ResourceEditor key={i} value={r} onChange={v => edit({...profile, resources: profile.resources.map((old, index) => index === i ? v : old), production: profile.production.map(cap => cap.resource_group_id === r.group_id && r.group_id !== v.group_id ? {...cap, resource_group_id: v.group_id} : cap)})} onRemove={() => edit({...profile, resources: profile.resources.filter((_, index) => index !== i), production: profile.production.map(cap => cap.resource_group_id === r.group_id ? {...cap, resource_group_id: ''} : cap)})}/>)}
            {!profile.resources.length && <p className="company-empty">Станки и линии не заданы. Свободная производственная мощность неизвестна.</p>}<button type="button" className="secondary" onClick={() => edit({...profile, resources: [...profile.resources, {group_id: '', calendar: [], enabled: true}]})}>Добавить станок или линию</button>
          </section>
        </>}
      </fieldset>
      {saveActions}
    </form>
  </main>
}

