import type { ArtifactRef } from './api'
import type { ChatSource } from './chatApi'
export type RadarBatch={batch_id:string;artifact_id:string;filename:string;created_at:string;count:number;warnings:string[];skipped_rows:number;duplicate_rows:number}
export type Decision='take_in_work'|'needs_review'|'excluded'|'not_analysed'
export type ProductFit='relevant'|'uncertain'|'irrelevant'
export type StockSource=ArtifactRef&{filename:string}
export type RadarSelection={stock_source:StockSource|null;max_shortage_percent:string}
export type StockCheck={status:'pass'|'insufficient'|'unknown';allowed_shortage_percent:string;reasons:string[];checks:{sku:string;product:string;unit:string;required:string;available:string;shortage:string;shortage_percent:string;within_tolerance:boolean;source_row:number}[]}
export type EconomicsCheck={status:'pass'|'fail'|'unknown';currency:string|null;revenue:string|null;total_cost:string|null;profit:string|null;margin_percent:string|null;budget:string|null;required_days:string|null;available_days:string|null;reasons:string[];lines:{sku:string;quantity:string;sale_price:string|null;revenue:string|null;cost:string|null;source_row:number}[]}
export type RadarItem={id:string;number:string;title:string;decision:Decision;reason:string;price:string|null;currency:string;deadline_at:string|null;source_url:string;source_observed_at:string|null;stage:string;demo:boolean;provenance:string;risks:string[];next_steps:string[];evidence_indexes:number[];fit?:ProductFit|null;product_group?:string|null;tender_quote?:string|null;profile_quote?:string|null;decision_stage?:string|null;meaning_reason?:string|null;stock_check?:StockCheck|null;mock?:boolean;origins?:{sheet:string|null;row:number}[];economics_check?:EconomicsCheck;participation_ready?:boolean;technical_readiness?:string;production_readiness?:string}
export type RadarReport={schema_version?:string;as_of:string;counts:Record<Decision|'total'|'model_analysed',number>;items:RadarItem[];sources:ChatSource[];notice:string;batch:{filename:string;artifact_id:string};selection?:RadarSelection|null;stock_source?:StockSource|null;stock_kind?:'mock'|'reported'|null;economics_source?:StockSource|null;economics_kind?:'mock'|'reported'|null;analysis_complete?:boolean;coverage?:{input_rows:number;unique_tenders:number;duplicate_rows:number;skipped_rows:number;accounted_rows:number;model_failed_rows:number;complete:boolean};portion_errors?:{ids:string[];code:string;message:string}[];funnel?:{total:number;after_eligibility:number;after_names:number;semantic_relevant:number;quantity_passed:number;model_requests:number}|null}
export type RadarRun={run_id:string;batch_id:string;created_at:string;state:'queued'|'running'|'completed'|'failed';focus:string;sources_snapshot:(ArtifactRef&{filename:string})[];report:RadarReport|null;model:string|null;error:string|null;selection?:RadarSelection}
export type RunAttempt={command_key:string;batch_id:string;sources:ArtifactRef[];focus:string;stock_source?:ArtifactRef|null;max_shortage_percent?:string}
export class RadarError extends Error{readonly rejected:boolean;constructor(message:string,rejected=false){super(message);this.rejected=rejected}}

async function request(path:string,body?:unknown):Promise<unknown>{
 let response:Response
 try{response=await fetch('/api/v0.1/radar'+path,{method:body===undefined?'GET':'POST',headers:{'X-Bervel-Web':'1',...(body===undefined?{}:{'Content-Type':'application/json'})},body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(30000)})}
 catch{throw Error('Связь с радаром прервана. Повтор отправки использует прежний ключ команды.')}
 const rejected=!response.ok&&[400,401,403,404,409,413,422].includes(response.status)
 let value:unknown
 try{value=await response.json()}catch{throw new RadarError('API радара вернул неверный формат ответа.',rejected)}
 if(!response.ok){
  if(response.status===422 && typeof value==='object' && value && 'error' in value){const error=value.error;if(error && typeof error==='object' && 'message' in error && typeof error.message==='string')throw new RadarError(error.message,rejected)}
  throw new RadarError(({401:'Доступ не настроен на сервере.',403:'Недостаточно прав.',404:'Источник или отчёт не найден.',409:'Конфликт команды; проверьте историю.',413:'Файл превышает допустимый размер.',422:'Проверьте партию и выбранные файлы.'} as Record<number,string>)[response.status]||'Радар временно недоступен. Повторите чтение.',rejected)
 }
 return value
}
function object(value:unknown):value is Record<string,unknown>{return !!value&&typeof value==='object'&&!Array.isArray(value)}
function run(value:unknown):RadarRun{
 if(!object(value)||typeof value.run_id!=='string'||!['queued','running','completed','failed'].includes(String(value.state))||!Array.isArray(value.sources_snapshot))throw Error('Неверный ответ радара.')
 if(value.state==='completed'&&(!object(value.report)||!Array.isArray(value.report.items)||!object(value.report.counts)))throw Error('Неверный формат отчёта.')
 return value as RadarRun
}
export async function listBatches():Promise<RadarBatch[]>{const value=await request('/batches');if(!Array.isArray(value)||value.some(v=>!object(v)||typeof v.batch_id!=='string'||typeof v.count!=='number'))throw Error('Не удалось прочитать партии.');return value as RadarBatch[]}
export async function importBatch(ref:ArtifactRef,key:string):Promise<RadarBatch>{const value=await request('/batches',{...ref,command_key:key});if(!object(value)||typeof value.batch_id!=='string'||typeof value.count!=='number')throw Error('Неверный результат импорта.');return value as RadarBatch}
export async function startRadar(attempt:RunAttempt){return run(await request('/runs',attempt))}
export type AnalyseAttempt={command_key:string;procurement_source:ArtifactRef;stock_source:ArtifactRef;economics_source:ArtifactRef;filters:{min_price:string|null;max_price:string|null;min_days_remaining:number;focus:string;max_shortage_percent:string;min_margin_percent:string}}
export async function analyseRadar(attempt:AnalyseAttempt){return run(await request('/analyse',attempt))}
export async function readRadar(id:string){const value=run(await request('/runs/'+encodeURIComponent(id)));if(value.run_id!==id)throw Error('Получен другой отчёт.');return value}
export async function listRadarRuns():Promise<Pick<RadarRun,'run_id'|'batch_id'|'state'|'created_at'|'focus'>[]>{const value=await request('/runs');if(!Array.isArray(value))throw Error('Неверная история радара.');return value}

export async function downloadRadarPdf(id:string):Promise<Blob>{
 const response=await fetch('/api/v0.1/radar/runs/'+encodeURIComponent(id)+'/report.pdf',
  {headers:{'X-Bervel-Web':'1'},signal:AbortSignal.timeout(120000)})
 if(!response.ok||!response.headers.get('Content-Type')?.startsWith('application/pdf'))throw Error('Не удалось скачать PDF. Проверьте доступ и завершение отчёта.')
 return response.blob()
}
