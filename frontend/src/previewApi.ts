import type {ArtifactRef} from './api'

export type FilePreview={kind:'table'|'text'|'image'|'pdf'|'unsupported';media_type:string;columns:string[];rows:string[][];total_rows:number;next_cursor:number|null;text:string;notice:string}

export async function readPreview(ref:ArtifactRef,cursor=0,signal?:AbortSignal):Promise<FilePreview>{
 const response=await fetch(`/api/v0.1/artifacts/${encodeURIComponent(ref.artifact_id)}/preview?cursor=${cursor}&limit=100`,
  {headers:{'X-Bervel-Web':'1'},signal:signal?AbortSignal.any([signal,AbortSignal.timeout(30000)]):AbortSignal.timeout(30000)})
 if(response.status===410)throw Error('Файл удалён.')
 if(!response.ok)throw Error('Не удалось открыть файл. Проверьте доступ и повторите просмотр.')
 const data=await response.json() as FilePreview
 if(!data||!['table','text','image','pdf','unsupported'].includes(data.kind)||typeof data.media_type!=='string'
  ||!Array.isArray(data.columns)||data.columns.some(c=>typeof c!=='string')||!Array.isArray(data.rows)
  ||data.rows.some(row=>!Array.isArray(row)||row.length!==data.columns.length||row.some(c=>typeof c!=='string'))
  ||!Number.isInteger(data.total_rows)||data.total_rows<0||typeof data.text!=='string'||typeof data.notice!=='string'
  ||(data.next_cursor!==null&&(!Number.isInteger(data.next_cursor)||data.next_cursor<=cursor)))throw Error('Неверный формат просмотра файла.')
 if(data.kind==='pdf'&&data.media_type!=='application/pdf')throw Error('Неверный тип PDF.')
 if(data.kind==='image'&&!['image/png','image/jpeg','image/webp','image/gif','image/bmp'].includes(data.media_type))throw Error('Изображение не поддерживается.')
 return data
}
