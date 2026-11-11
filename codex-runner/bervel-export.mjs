#!/usr/bin/env node
import { createHash } from 'node:crypto'
import { openAsBlob } from 'node:fs'
import { appendFile, mkdir, realpath, stat } from 'node:fs/promises'
import { basename, dirname, extname, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const mimeTypes={'.pdf':'application/pdf','.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','.doc':'application/msword','.svg':'image/svg+xml','.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.gif':'image/gif','.webp':'image/webp','.avif':'image/avif','.bmp':'image/bmp','.tif':'image/tiff','.tiff':'image/tiff','.txt':'text/plain','.log':'text/plain','.csv':'text/csv','.tsv':'text/tab-separated-values','.md':'text/markdown','.markdown':'text/markdown','.json':'application/json','.html':'text/html','.htm':'text/html','.xml':'application/xml','.yaml':'application/yaml','.yml':'application/yaml','.xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','.xls':'application/vnd.ms-excel','.pptx':'application/vnd.openxmlformats-officedocument.presentationml.presentation','.zip':'application/zip','.gz':'application/gzip'}
const uuid=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i
const sha=value=>createHash('sha256').update(value).digest('hex')

export async function exportFile(args,env=process.env) {
  if(!args[0]||!(args.length===1||args.length===3&&args[1]==='--name'&&args[2]))throw new Error('Использование: bervel-export <path> [--name filename]')
  const apiKey=env.BERVEL_API_KEY,turn=env.BERVEL_INSPECTION_TURN_ID,record=env.BERVEL_EXPORT_RECORD
  if(!apiKey||!turn||!record)throw new Error('Не удалось экспортировать файл: отсутствуют API credentials, ID запроса или путь записи результата.')
  let base
  try{base=new URL(env.BERVEL_API_URL||'http://api:8011');if(!['http:','https:'].includes(base.protocol)||base.username||base.password)throw new Error()}catch{throw new Error('Не удалось экспортировать файл: неверный адрес API.')}
  const path=resolve(args[0]),filename=args[2]||basename(path)
  if(!filename||filename.length>255)throw new Error('Не удалось экспортировать файл: имя должно содержать от 1 до 255 символов.')
  const media=mimeTypes[extname(filename).toLowerCase()]||'application/octet-stream'
  const fingerprintHash=createHash('sha256').update(path).update('\0').update(filename).update('\0'),contentHash=createHash('sha256');let size=0
  let blob
  try{
    const info=await stat(path)
    if(!info.isFile())throw new Error('not-file')
    blob=await openAsBlob(path,{type:media})
    for await(const chunk of blob.stream()){size+=chunk.length;fingerprintHash.update(chunk);contentHash.update(chunk)}
  }catch(error){
    if(error?.code==='ENOENT')throw new Error('Файл не найден. Проверьте путь к созданному файлу.')
    if(error?.message==='not-file')throw new Error('Указанный путь не является обычным файлом.')
    throw new Error('Не удалось прочитать файл. Проверьте права доступа и доступность диска.')
  }
  if(!size)throw new Error('Не удалось экспортировать пустой файл.')
  const checksum=contentHash.digest('hex')
  const fingerprint=fingerprintHash.digest('hex')
  const turnKey=turn.length<=58?turn:sha(turn).slice(0,32),key=`cli:${turnKey}:${fingerprint}`
  const form=new FormData();form.set('file',blob,filename);form.set('command_key',key);form.set('origin_system','bervel-inspection-cli');form.set('source_role','test_fixture')
  async function request(path,options={}){
    let response
    try{response=await fetch(new URL(path,base),{...options,headers:{Authorization:`Bearer ${apiKey}`},redirect:'error',...(options.method==='POST'?{}:{signal:AbortSignal.timeout(30_000)})})}catch{throw new Error('Передача файла не подтверждена: соединение с API прервано или файл изменился. Проверьте состояние перед повтором; та же команда использует прежний ключ.')}
    if(!response.ok)throw new Error(`Экспорт не подтверждён: API вернул HTTP ${response.status}. Повтор той же команды использует прежний ключ.`)
    try{return await response.json()}catch{throw new Error('Экспорт не подтверждён: API вернул несовместимый ответ.')}
  }
  const receipt=await request('/api/v0.1/artifacts',{method:'POST',body:form})
  if(!receipt||typeof receipt.artifact_id!=='string'||!uuid.test(receipt.artifact_id))throw new Error('Экспорт не подтверждён: API не вернул действительную ссылку на файл.')
  const metadata=await request(`/api/v0.1/artifacts/${receipt.artifact_id}`)
  if(!metadata||metadata.artifact_id!==receipt.artifact_id||typeof metadata.filename!=='string'||!metadata.filename||typeof metadata.media_type!=='string'||!metadata.media_type||metadata.size!==size||metadata.sha256!==checksum)throw new Error('Экспорт не подтверждён: размер или контрольная сумма загруженного файла не совпадают с оригиналом.')
  const artifactPath=`/api/v0.1/artifacts/${receipt.artifact_id}`
  const card={kind:'file',artifact_id:receipt.artifact_id,filename:metadata.filename,media_type:metadata.media_type,download_url:`${artifactPath}/content`,preview_url:`${artifactPath}/preview`}
  try{await mkdir(dirname(record),{recursive:true});await appendFile(record,JSON.stringify(card)+'\n',{mode:0o600})}catch{throw new Error('Файл зарегистрирован, но не удалось сохранить подтверждение экспорта. Повтор той же команды использует прежний ключ.')}
  return card
}
if(process.argv[1]&&import.meta.url===pathToFileURL(await realpath(process.argv[1])).href){
  try{console.log(JSON.stringify(await exportFile(process.argv.slice(2))))}
  catch(error){console.error(error instanceof Error?error.message:'Не удалось экспортировать файл.');process.exitCode=1}
}
