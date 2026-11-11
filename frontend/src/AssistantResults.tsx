import { useEffect,useMemo,useState } from 'react'
import type { AssistantResult,ActionReceipt } from './chatApi'
import { downloadOriginal,readOriginalBlob } from './downloadOriginal'
import FilePreview from './FilePreview'
import { ArtifactImage } from './InspectionMarkdown'
import { HtmlArtifact } from './InspectionVisuals'
import { InspectionProposal, InspectionTests } from './InspectionEngineering'

const operationLabels:Record<string,string>={'file.list':'Список файлов','file.latest_ready':'Последние готовые файлы','file.read':'Чтение файла','file.download':'Скачивание файла','file.knowledge':'Готовность файла','file.prepare':'Подготовка файла','file.role':'Назначение файла','file.delete':'Удаление файла','file.create':'Создание файла','settings.read':'Настройки','settings.test':'Проверка подключения','settings.update':'Изменение настроек','ticket.create':'Создание обращения','ticket.edit':'Изменение обращения','ticket.read':'Обращение','ticket.list':'Список обращений','web.search':'Веб-поиск'}
const safeWeb=(url:string)=>/^https?:\/\//i.test(url)
Object.assign(operationLabels,{'system.list':'Каталог системы','system.read':'Чтение исходника','database.tables':'Таблицы БД','database.schema':'Структура таблицы','database.rows':'Чтение данных БД','container.list':'Контейнеры BERVEL','container.inspect':'Устройство контейнера','container.logs':'Чтение логов','container.files':'Файлы контейнера'})
Object.assign(operationLabels,{'knowledge.search':'Поиск по базе знаний','file.read_page':'Страница файла','file.query':'Расчёт по таблице'})
Object.assign(operationLabels,{'conversation.read':'Чтение истории диалога','diagnostic.run':'Диагностика проекта','api.read':'Чтение API','test.run':'Запуск тестов','code.propose':'Подготовка патча','document.create':'Создание документа'})
Object.assign(operationLabels,{'command.run':'Команда','code.change':'Изменение файлов','tool.call':'Инструмент CLI'})
const columnLabel=(name:string)=>({free_quantity:'Свободный остаток',unit:'Единица измерения'}[name]||name)
function TableQueryResult({value}:{value:unknown}){
 if(typeof value!=='object'||value===null||!('count' in value)||typeof value.count!=='number'||!('complete' in value)||value.complete!==true)return <p className="text-sm">Результат расчёта недоступен.</p>
 const data=value as Record<string,unknown>
 const sums=typeof data.sums==='object'&&data.sums!==null?Object.entries(data.sums).filter(([,sum])=>typeof sum==='string'):[]
 const groups=Array.isArray(data.groups)?data.groups.filter((g):g is Record<string,unknown>=>typeof g==='object'&&g!==null):[]
 return <section className="assistant-result text-sm" aria-label="Расчёт по таблице"><p className="eyebrow">РАСЧЁТ ПО ТАБЛИЦЕ</p><h3>{typeof data.filename==='string'?data.filename:'Таблица'}</h3>
  {data.is_mock===true&&<p className="file-state mt-2">Демонстрационные данные</p>}
  {typeof data.notice==='string'&&data.notice&&<p className="mt-2">{data.notice}</p>}
  <p className="mt-3">Строк данных: {value.count}</p>
  {!groups.length&&!!sums.length&&<div className="mt-3 overflow-auto"><table className="assistant-query-table"><thead><tr><th>Показатель</th><th>Сумма</th></tr></thead><tbody>{sums.map(([name,sum])=><tr key={name}><td>{columnLabel(name)}</td><td>{String(sum)}</td></tr>)}</tbody></table></div>}
  {!!groups.length&&<div className="mt-3 overflow-auto"><table className="assistant-query-table"><thead><tr><th>{typeof data.group_by==='string'?columnLabel(data.group_by):'Группа'}</th><th>Строк</th>{sums.map(([name])=><th key={name}>{columnLabel(name)}</th>)}</tr></thead><tbody>{groups.map((group,index)=><tr key={index}><td>{String(group.value??'')}</td><td>{String(group.count??'')}</td>{sums.map(([name])=><td key={name}>{typeof group.sums==='object'&&group.sums!==null?String((group.sums as Record<string,unknown>)[name]??''):''}</td>)}</tr>)}</tbody></table></div>}
 </section>
}
function SvgImage({file}:{file:Extract<AssistantResult,{kind:'file'}>}){
 const [url,setUrl]=useState(''),[error,setError]=useState<string|null>(null)
 useEffect(()=>{const controller=new AbortController();let current=true;let objectUrl:string|undefined
  readOriginalBlob(file,controller.signal).then(blob=>{if(current){objectUrl=URL.createObjectURL(new Blob([blob],{type:'image/svg+xml'}));setUrl(objectUrl)}}).catch(reason=>{if(current&&!controller.signal.aborted)setError(reason.message)})
  return()=>{current=false;controller.abort();if(objectUrl)URL.revokeObjectURL(objectUrl)}
 },[file])
 return <>{url&&<img className="generated-diagram" src={url} alt={file.filename}/>}{error&&<p role="alert" className="text-xs text-red-800">{error}</p>}</>
}
function FileResult({result,inspection}:{result:Extract<AssistantResult,{kind:'file'}>;inspection:boolean}){
 const [preview,setPreview]=useState(false),[error,setError]=useState<string|null>(null),[busy,setBusy]=useState(false)
 const previewFile=useMemo(()=>({artifact_id:result.artifact_id,filename:result.filename,media_type:result.media_type,size:1,created_at:''}),[result.artifact_id,result.filename,result.media_type])
 const svg=result.media_type==='image/svg+xml',image=inspection&&['image/svg+xml','image/png','image/jpeg','image/gif','image/webp','image/avif'].includes(result.media_type)
 const html=inspection&&result.media_type.split(';')[0].toLowerCase()==='text/html'
 return <section className="assistant-result assistant-file" aria-label="Созданный файл">
 <div className="assistant-file-header"><h3 title={result.filename}>{result.filename}</h3><button className="secondary" disabled={busy} onClick={async()=>{setBusy(true);setError(null);try{await downloadOriginal(result,result.filename)}catch(reason){setError(reason instanceof Error?reason.message:'Не удалось скачать файл.')}finally{setBusy(false)}}}>{busy?'Скачиваем…':'Скачать файл'}</button>{!svg&&<button className="secondary" onClick={()=>setPreview(v=>!v)}>{preview?'Скрыть просмотр':'Просмотреть файл'}</button>}</div>
 {result.is_mock===true&&<p className="file-state mt-2">{result.notice||'Демонстрационные данные (mock)'}</p>}
 {html?<HtmlArtifact id={result.artifact_id} title={result.filename}/>:image?<ArtifactImage id={result.artifact_id} alt={result.filename}/>:svg&&<SvgImage file={result}/>}
 {preview&&<FilePreview file={previewFile} showDownload={false}/>}{error&&<p role="alert" className="mt-2 text-xs text-red-800">{error}</p>}
 </section>
}
function WebResults({results}:{results:Extract<AssistantResult,{kind:'web'}>[]}){
 const saved=results.flatMap(result=>result.sources)
 const sources=[...new Map(saved.map(source=>[source.url,source])).values()]
 const link=(source:{title:string;url:string})=>safeWeb(source.url)?<a href={source.url} target="_blank" rel="noreferrer" className="text-accent underline wrap-anywhere">{source.title||source.url}</a>:<span className="wrap-anywhere">{source.title||source.url}</span>
 return <section className="assistant-result text-xs" aria-label="Ответ из веба"><p className="eyebrow">ВЕБ-ПОИСК</p>
  <details aria-label={`Веб-источники (${sources.length})`}><summary>Веб-источники ({sources.length})</summary><ul className="mt-3 space-y-2">{sources.map(source=><li key={source.url}>{link(source)}</li>)}</ul></details>
  <details aria-label={`Полный сохранённый список (${saved.length})`} className="mt-3"><summary>Полный сохранённый список ({saved.length})</summary>{results.map((result,index)=><div key={index} className="mt-3">
   <p className="text-sm whitespace-pre-wrap wrap-anywhere">{result.answer}</p><ul className="mt-3 space-y-2">{result.sources.map((source,index)=><li key={index}>{link(source)}</li>)}</ul>
  </div>)}</details>
 </section>
}
export default function AssistantResults({results,actions,inspection=false,active=true,native=false}:{results:AssistantResult[];actions:ActionReceipt[];inspection?:boolean;active?:boolean;native?:boolean}){
 const stateLabels={running:'Выполняется',completed:'Выполнено',failed:'Ошибка',unknown:'Исход неизвестен'}
 const web=results.filter(result=>result.kind==='web')
 return <div className="assistant-results">
 {!inspection&&!!actions.length&&<details className="mt-4 text-xs text-muted"><summary>Действия ({actions.length})</summary><ul className="mt-2 space-y-1">{actions.map((action,i)=><li key={action.action_id||i}>{operationLabels[action.operation] || action.operation}: {stateLabels[action.state]}{action.state==='unknown'?' · проверьте результат перед новой командой':''}{!!action.error&&<p className="text-red-800">{typeof action.error==='string'?action.error:typeof action.error==='object'&&action.error!==null&&'message' in action.error?String(action.error.message):'Действие не завершено.'}</p>}</li>)}</ul></details>}
 {results.filter(result=>result.kind!=='web'&&(result.kind!=='data'||result.operation==='file.query')).map((result,i)=>result.kind==='file'?<FileResult key={i} result={result} inspection={inspection}/>:result.kind==='ticket'?<section key={i} className="assistant-result" aria-label="Созданное обращение"><p className="eyebrow">ОБРАЩЕНИЕ · {result.status==='sent'?'ОТПРАВЛЕНО':'СОЗДАНО'}</p><h3>{result.title}</h3><a className="text-sm text-accent underline mt-3 inline-block" href={`#/tickets?ticket=${encodeURIComponent(result.ticket_id)}`}>Открыть обращение</a></section>:result.kind==='data'?<TableQueryResult key={i} value={result.data}/>:inspection&&result.kind==='inspection_test'?<InspectionTests key={i} result={result}/>:inspection&&active&&result.kind==='code_proposal'?<InspectionProposal key={result.proposal_id} result={result} readOnly={native}/>:null)}
 {!!web.length&&<WebResults results={web}/>}
 </div>
}
