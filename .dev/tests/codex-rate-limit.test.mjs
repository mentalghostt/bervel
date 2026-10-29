import { test } from 'node:test'
import assert from 'node:assert/strict'
import http from 'node:http'
import { observeWebResponses } from '../../codex-runner/web-evidence.mjs'
import { createRunner } from '../../codex-runner/server.mjs'

async function transport(handler, action, signal=AbortSignal.timeout(6000)) {
 const provider=http.createServer(handler)
 await new Promise(r=>provider.listen(0,'127.0.0.1',r))
 const observer=await observeWebResponses({baseUrl:`http://127.0.0.1:${provider.address().port}/v1`,apiKey:'private-fixture'},signal)
 try{await action(observer)}finally{await observer.close();provider.closeAllConnections();await new Promise(r=>provider.close(r))}
}
const post=observer=>fetch(observer.baseUrl+'/responses',{method:'POST',headers:{Authorization:`Bearer ${observer.token}`,'Content-Type':'application/json'},body:'{"model":"test","input":[]}'})

test('explicit HTTP429 is retried after waiting, with identical body and no successful request replay',async()=>{
 const bodies=[],times=[]
 await transport(async(request,response)=>{
  let body='';for await(const part of request)body+=part
  bodies.push(body);times.push(Date.now())
  if(bodies.length===1){response.writeHead(429,{'Retry-After':'0.5'});response.end('{"error":{"message":"private"}}');return}
  response.writeHead(200,{'Content-Type':'text/event-stream'});response.end('data: [DONE]\n\n')
 },async observer=>{
  const response=await post(observer)
  assert.equal(response.status,200);await response.text()
  assert.equal(bodies.length,2)
  assert.equal(bodies[0],bodies[1])
  assert.ok(times[1]-times[0]>=400,'Retry must wait without resetting the caller deadline')
  assert.equal(observer.failure,null)
 })
})

test('repeated429 stops after the original request plus three retries and keeps only safe failure metadata',async()=>{
 let calls=0
 await transport((_request,response)=>{calls++;response.writeHead(429,{'Retry-After':'0'});response.end('{"error":{"code":"rate_limit_exceeded","message":"private request and credentials"}}')},async observer=>{
  const response=await post(observer)
  assert.equal(response.status,429)
  assert.equal(calls,4)
  assert.equal(observer.failure.status,429)
  assert.equal((await response.text()).includes('private'),false)
 })
})

test('cancellation while waiting on429 prevents the next attempt',async()=>{
 const cancellation=new AbortController();let calls=0,entered
 const first=new Promise(r=>entered=r)
 await transport((_request,response)=>{calls++;response.writeHead(429,{'Retry-After':'10'});response.end('{}');entered()},async observer=>{
  const pending=post(observer)
  await first;cancellation.abort()
  const response=await pending;await response.text()
  assert.equal(calls,1)
 },cancellation.signal)
})

test('Retry-After longer than the bounded wait is surfaced immediately instead of retrying too early',async()=>{
 for(const header of ['71',new Date(Date.now()+72_000).toUTCString()]){
  let calls=0
  await transport((_request,response)=>{calls++;response.writeHead(429,{'Retry-After':header});response.end('{}')},async observer=>{
   const response=await post(observer)
   assert.equal(response.status,429);await response.text()
   assert.equal(calls,1)
  })
 }
})

test('Retry-After HTTP-date is honored before retrying an explicitly rejected model request',async()=>{
 const times=[]
 await transport((_request,response)=>{
  times.push(Date.now())
  if(times.length===1){response.writeHead(429,{'Retry-After':new Date(Date.now()+2500).toUTCString()});response.end('{}');return}
  response.writeHead(200,{'Content-Type':'text/event-stream'});response.end('data: [DONE]\n\n')
 },async observer=>{
  const response=await post(observer);assert.equal(response.status,200);await response.text()
  assert.equal(times.length,2);assert.ok(times[1]-times[0]>=1400)
 })
})

test('transport failures, HTTP500 and failures after a200 stream never retry unknown work',async()=>{
 for(const status of [0,500,200]){
  let calls=0
  await transport((_request,response)=>{calls++;if(status===0){response.destroy();return}response.writeHead(status,{'Content-Type':'text/event-stream'});if(status===200)response.write('data: {"type":"response.created"}\n\n');setImmediate(()=>response.destroy())},async observer=>{
   try{const response=await post(observer);await response.text()}catch{}
   assert.equal(calls,1)
  })
 }
})

test('SDK retry-limit429 is classified safely while arbitrary429 text is not trusted as retry evidence',async()=>{
 const env={BERVEL_CODEX_MODEL:'test',BERVEL_CODEX_API_KEY:'private',BERVEL_CODEX_BASE_URL:'https://provider.example/v1'}
 for(const [detail,code,status]of[['exceeded retry limit, last status: 429 Too Many Requests private','MODEL_RATE_LIMITED',429],['The source contains 429 records private','MODEL_FAILED',502]]){
  const server=createRunner({env,execute:async()=>{throw Error(detail)}})
  await new Promise(r=>server.listen(0,'127.0.0.1',r))
  try{
   const response=await fetch(`http://127.0.0.1:${server.address().port}/v1/generate`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{"request_id":"rate","prompt":"hello"}'})
   assert.equal(response.status,status)
   const result=await response.json();assert.equal(result.error.code,code);assert.equal(JSON.stringify(result).includes('private'),false)
  }finally{server.closeAllConnections();await new Promise(r=>server.close(r))}
 }
})

test('a late model429 retries only that round after an already completed native search',async()=>{
 let models=0,searches=0
 await transport(async(request,response)=>{
  for await(const part of request){}
  if(request.url.endsWith('/alpha/search')){
   searches++;response.writeHead(200,{'Content-Type':'application/json'});response.end('{"output":"native","results":[{"type":"text_result","title":"Official","url":"https://example.com/verified"}]}');return
  }
  models++
  if(models===2){response.writeHead(429,{'Retry-After':'0'});response.end('{}');return}
  response.writeHead(200,{'Content-Type':'text/event-stream'});response.end('data: [DONE]\n\n')
 },async observer=>{
  await (await post(observer)).text()
  const search=await fetch(observer.baseUrl+'/alpha/search',{method:'POST',headers:{Authorization:`Bearer ${observer.token}`,'Content-Type':'application/json'},body:'{}'})
  await search.text()
  assert.equal((await post(observer)).status,200)
  assert.equal(models,3);assert.equal(searches,1)
  assert.deepEqual(observer.sources(),[{title:'Official',url:'https://example.com/verified'}])
 })
})
