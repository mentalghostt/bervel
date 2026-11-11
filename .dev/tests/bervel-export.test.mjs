import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { spawn } from 'node:child_process'
import { mkdir, readFile, writeFile, access } from 'node:fs/promises'
import { createHash, randomUUID } from 'node:crypto'
import { resolve, join } from 'node:path'
import fs from 'node:fs'
import { syncBuiltinESMExports } from 'node:module'
import { pathToFileURL } from 'node:url'
const helper=resolve('codex-runner/bervel-export.mjs'),root=resolve('.dev/local/inspection-engineer-2026-10-30/native-cli/export-tests'),id='44444444-4444-4444-8444-444444444444',apiKey='export-test-private-key-never-print'
const digest=bytes=>createHash('sha256').update(bytes).digest('hex')
async function fixture(t){
 const directory=join(root,randomUUID());await mkdir(directory,{recursive:true});const input=join(directory,'source.svg'),record=join(directory,'exports.jsonl');await writeFile(input,'<svg xmlns="http://www.w3.org/2000/svg"/>')
 const requests=[];let fail=null,metadata=null
 const server=createServer(async(req,res)=>{
  const chunks=[];for await(const part of req)chunks.push(part)
  const item={method:req.method,path:req.url,headers:req.headers};requests.push(item)
  if(req.method==='POST'){
   const form=await new Response(Buffer.concat(chunks),{headers:{'Content-Type':req.headers['content-type']}}).formData();item.form=form;item.bytes=Buffer.from(await form.get('file').arrayBuffer())
   metadata={artifact_id:id,filename:form.get('file').name,media_type:form.get('file').type,size:item.bytes.length,sha256:digest(item.bytes)}
   if(fail==='network'){req.socket.destroy();return}
   if(fail==='upload'){res.writeHead(503,{'Content-Type':'application/json'});res.end(JSON.stringify({error:apiKey}));return}
   res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify({artifact_id:id}));return
  }
  res.writeHead(fail==='metadata'?503:200,{'Content-Type':'application/json'});res.end(JSON.stringify(fail==='metadata'?{error:apiKey}:fail==='digest'?{...metadata,sha256:'0'.repeat(64)}:metadata))
 });await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));t.after(()=>new Promise(resolve=>server.close(resolve)))
 async function run(args=[input],extra={}){return new Promise(resolve=>{const proc=spawn(process.execPath,[helper,...args],{env:{...process.env,BERVEL_API_URL:`http://127.0.0.1:${server.address().port}`,BERVEL_API_KEY:apiKey,BERVEL_INSPECTION_TURN_ID:'33333333-3333-4333-8333-333333333333',BERVEL_EXPORT_RECORD:record,...extra},stdio:['ignore','pipe','pipe']});let stdout='',stderr='';proc.stdout.on('data',x=>stdout+=x);proc.stderr.on('data',x=>stderr+=x);proc.on('close',code=>resolve({code,stdout,stderr}))})}
 return {directory,input,record,requests,run,setFailure:value=>fail=value}
}
test('CLI uploads actual multipart bytes, authenticates metadata and records only confirmed file cards',async t=>{
 const f=await fixture(t),result=await f.run([f.input,'--name','Схема.svg']);assert.equal(result.code,0,result.stderr);const card=JSON.parse(result.stdout);assert.equal(card.kind,'file');assert.equal(card.filename,'Схема.svg');assert.equal(card.media_type,'image/svg+xml');assert.equal(card.download_url,`/api/v0.1/artifacts/${id}/content`);assert.equal(card.preview_url,`/api/v0.1/artifacts/${id}/preview`)
 assert.deepEqual(JSON.parse((await readFile(f.record,'utf8')).trim()),card);assert.equal(f.requests.length,2);assert.equal(f.requests[0].headers.authorization,`Bearer ${apiKey}`);assert.equal(f.requests[1].headers.authorization,`Bearer ${apiKey}`);assert.equal(f.requests[1].path,`/api/v0.1/artifacts/${id}`);assert.equal(f.requests[0].form.get('origin_system'),'bervel-inspection-cli');assert.equal(f.requests[0].form.get('source_role'),'test_fixture');assert.deepEqual(f.requests[0].bytes,await readFile(f.input));assert.ok(!result.stdout.includes(apiKey)&&!result.stderr.includes(apiKey)&&!(await readFile(f.record,'utf8')).includes(apiKey))
})
test('identical manual retry reuses a short key; bytes filename and actual path identify distinct exports',async t=>{
 const f=await fixture(t);for(let i=0;i<2;i++)assert.equal((await f.run()).code,0);const posts=()=>f.requests.filter(x=>x.method==='POST');const key=posts()[0].form.get('command_key');assert.equal(key,posts()[1].form.get('command_key'));assert.ok(key.startsWith('cli:33333333-3333-4333-8333-333333333333:')&&key.length<=128)
 assert.equal((await f.run([f.input,'--name','other.svg'])).code,0);assert.notEqual(posts().at(-1).form.get('command_key'),key)
 const other=join(f.directory,'other.svg');await writeFile(other,await readFile(f.input));assert.equal((await f.run([other])).code,0);assert.notEqual(posts().at(-1).form.get('command_key'),key)
 await writeFile(f.input,'<svg>changed</svg>');assert.equal((await f.run()).code,0);assert.notEqual(posts().at(-1).form.get('command_key'),key)
})
for(const failure of ['upload','metadata','network','digest'])test(`CLI honestly rejects ${failure} without secret disclosure fake card or automatic retry`,async t=>{
 const f=await fixture(t);f.setFailure(failure);const result=await f.run();assert.notEqual(result.code,0);assert.equal(result.stdout,'');assert.ok(!result.stderr.includes(apiKey));assert.equal(f.requests.filter(x=>x.method==='POST').length,1);await assert.rejects(access(f.record));assert.match(result.stderr,/не удалось|не подтвержд|не зарегистрирован|ошибка/i)
})
test('CLI rejects empty files and missing credential before upload',async t=>{
 const f=await fixture(t);await writeFile(f.input,'');assert.notEqual((await f.run()).code,0);await writeFile(f.input,'ok');assert.notEqual((await f.run([f.input],{BERVEL_API_KEY:''})).code,0);assert.equal(f.requests.length,0)
})

test('CLI exports files larger than five MiB and preserves the original bytes',async t=>{
 const f=await fixture(t),bytes=Buffer.alloc(6*1024*1024,81);await writeFile(f.input,bytes);const result=await f.run();assert.equal(result.code,0,result.stderr);assert.deepEqual(f.requests[0].bytes,bytes)
})

test('CLI distinguishes a missing file from an upload failure',async t=>{
 const f=await fixture(t),result=await f.run([join(f.directory,'missing.bin')]);assert.notEqual(result.code,0);assert.match(result.stderr,/файл не найден/i);assert.equal(f.requests.length,0);assert.ok(!result.stderr.includes('5 МиБ'))
})

test('CLI never registers replacement bytes if the source changes after hashing',async t=>{
 const f=await fixture(t),oldBlob=fs.openAsBlob,oldRead=fs.createReadStream,oldFetch=globalThis.fetch;let mutated=false,registered=0
 const mutate=()=>{if(!mutated){mutated=true;fs.writeFileSync(f.input,'replacement data B')}}
 fs.createReadStream=(...args)=>{const stream=oldRead(...args);stream.on('end',mutate);return stream}
 fs.openAsBlob=async(...args)=>{const blob=await oldBlob(...args),stream=blob.stream.bind(blob);blob.stream=()=>new ReadableStream({async start(controller){try{for await(const chunk of stream())controller.enqueue(chunk);mutate();controller.close()}catch(error){controller.error(error)}}});return blob}
 syncBuiltinESMExports()
 globalThis.fetch=async(_url,options)=>{if(options.method==='POST'){const bytes=Buffer.from(await options.body.get('file').arrayBuffer());registered++;return Response.json({artifact_id:id})}return Response.json({artifact_id:id,filename:'source.svg',media_type:'image/svg+xml',size:18,sha256:digest(Buffer.from('replacement data B'))})}
 try{
  const {exportFile}=await import(pathToFileURL(helper).href+'?mutation')
  await assert.rejects(exportFile([f.input],{BERVEL_API_URL:'http://127.0.0.1',BERVEL_API_KEY:apiKey,BERVEL_INSPECTION_TURN_ID:'mutation-check',BERVEL_EXPORT_RECORD:f.record}))
  assert.equal(mutated,true);assert.equal(registered,0);await assert.rejects(access(f.record))
 }finally{fs.openAsBlob=oldBlob;fs.createReadStream=oldRead;globalThis.fetch=oldFetch;syncBuiltinESMExports()}
})
test('CLI uses normal MIME inference without a file type allowlist',async t=>{
 const f=await fixture(t);for(const [name,mime] of [['report.pdf','application/pdf'],['notes.docx','application/vnd.openxmlformats-officedocument.wordprocessingml.document'],['table.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'],['image.png','image/png'],['photo.jpg','image/jpeg'],['note.txt','text/plain'],['table.csv','text/csv'],['note.md','text/markdown'],['result.json','application/json'],['page.html','text/html'],['source.zip','application/zip'],['file.unknown','application/octet-stream']]){const result=await f.run([f.input,'--name',name]);assert.equal(result.code,0,result.stderr);assert.equal(JSON.parse(result.stdout).media_type,mime)}
})
