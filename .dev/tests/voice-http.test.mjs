import { test } from 'node:test'
import assert from 'node:assert/strict'
import http from 'node:http'
import { mkdtemp } from 'node:fs/promises'
import { resolve } from 'node:path'
import { createRunner } from '../../codex-runner/server.mjs'
async function fixture(options,action){
 const directory=await mkdtemp(resolve('.dev/local/assistant-capabilities-2026-10-28/native/http-test-'))
 const server=createRunner({env:{CODEX_HOME:directory,BERVEL_CODEX_MODEL:'test',BERVEL_CODEX_API_KEY:'model-private',BERVEL_CODEX_BASE_URL:'https://provider.example/v1'},...options})
 await new Promise(r=>server.listen(0,'127.0.0.1',r))
 try{await action(`http://127.0.0.1:${server.address().port}`,directory)}finally{server.closeAllConnections();await new Promise(r=>server.close(r))}
}
const request=(base,path,body,method='POST')=>fetch(base+path,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
const audio={audio_base64:'AQID',mime_type:'audio/wav',filename:'voice.wav'}
const web={request_id:'web',prompt:'Search a public fact',web_search:true}
test('voice settings and transcribe routes survive model unavailability; no key is an isolated explicit state',async()=>{
 let seen
 await fixture({transcribeAudio:async(key,body)=>{seen={key,body};return{text:'Draft'}}},async(base)=>{
  assert.equal((await (await fetch(base+'/v1/voice/settings')).json()).configured,false)
  let response=await request(base,'/v1/transcribe',audio)
  assert.equal(response.status,503);assert.equal((await response.json()).error.code,'VOICE_UNCONFIGURED')
  response=await request(base,'/v1/voice/settings',{api_key:'groq-private'},'PUT')
  assert.equal(response.status,200);assert.equal((await response.text()).includes('groq-private'),false)
  response=await request(base,'/v1/transcribe',audio)
  assert.deepEqual(await response.json(),{text:'Draft'});assert.deepEqual(seen,{key:'groq-private',body:audio})
  assert.equal((await fetch(base+'/health/ready')).status,200)
 })
})
test('native search failure stays explicit after saving a voice key and is retried natively on the next request',async()=>{
 const nativeCalls=[]
 await fixture({execute:async(_config,body)=>{nativeCalls.push(body);if(body.web_search)throw Object.assign(Error('Native unsupported'),{code:'WEB_SEARCH_UNAVAILABLE'});return'Ordinary MOX response'}},async base=>{
  let response=await request(base,'/v1/generate',web)
  assert.equal(response.status,502);assert.equal((await response.json()).error.code,'WEB_SEARCH_UNAVAILABLE')
  await request(base,'/v1/voice/settings',{api_key:'groq-private'},'PUT')
  response=await request(base,'/v1/generate',web)
  assert.equal(response.status,502);assert.equal((await response.json()).error.code,'WEB_SEARCH_UNAVAILABLE')
  assert.equal(nativeCalls.length,2)
  response=await request(base,'/v1/generate',{request_id:'regular',prompt:'Hello'})
  assert.deepEqual(await response.json(),{answer:'Ordinary MOX response',model:'test'})
  assert.equal(nativeCalls.length,3)
 })
})
test('provider auth/TLS failures stay separate from native search capability',async()=>{
 for(const message of ['HTTP 401 unauthorized','certificate verify failed']){
  await fixture({execute:async()=>{throw Error(message)}},async base=>{
   assert.equal((await request(base,'/v1/generate',web)).status,502)
  })
 }
})
test('closing the client connection aborts the pending STT signal',async()=>{
 let entered,aborted
 const enteredPromise=new Promise(r=>entered=r),abortedPromise=new Promise(r=>aborted=r)
 await fixture({transcribeAudio:async(_key,_body,signal)=>{entered();await new Promise(resolve=>signal.addEventListener('abort',()=>{aborted();resolve()},{once:true}));throw Object.assign(Error('cancelled'),{name:'AbortError'})}},async base=>{
  await request(base,'/v1/voice/settings',{api_key:'groq-private'},'PUT')
  const pending=http.request(base+'/v1/transcribe',{method:'POST',headers:{'Content-Type':'application/json'}})
  pending.on('error',()=>{})
  pending.end(JSON.stringify(audio))
  await enteredPromise;pending.destroy()
  await Promise.race([abortedPromise,new Promise((_,reject)=>setTimeout(()=>reject(Error('Cancellation not propagated')),1500))])
 })
})
