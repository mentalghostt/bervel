import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp } from 'node:fs/promises'
import { resolve } from 'node:path'
const module = await import('../../codex-runner/server.mjs')
const config = { BERVEL_CODEX_MODEL:'test',BERVEL_CODEX_API_KEY:'private-fixture',BERVEL_CODEX_BASE_URL:'https://provider.example/v1' }
async function withServer(execute,action){
 const server=module.createRunner({env:config,execute});await new Promise(r=>server.listen(0,'127.0.0.1',r))
 try{await action(`http://127.0.0.1:${server.address().port}`)}finally{await new Promise(r=>server.close(r))}
}
const post=(base,body)=>fetch(base+'/v1/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
const schema={type:'object',properties:{action:{type:'string',enum:['reply','web']}},required:['action'],additionalProperties:false}
test('schema and explicit web flag reach runner; invalid flag/schema fail before model',async()=>{
 const calls=[]
 await withServer(async(_config,body)=>{calls.push(body);return body.web_search?{answer:'Verified',sources:[{title:'Official',url:'https://example.com/official'}]}:'{"action":"reply"}'},async base=>{
  const structured=await post(base,{request_id:'a',prompt:'plan',output_schema:schema})
  assert.equal(structured.status,200)
  const web=await post(base,{request_id:'b',prompt:'Search',web_search:true})
  assert.equal(web.status,200);assert.equal((await web.json()).sources[0].url,'https://example.com/official')
  assert.equal(calls[0].output_schema.type,'object');assert.equal(calls[1].web_search,true)
  for(const bad of [{web_search:'true'},{web_search:1},{output_schema:[]},{output_schema:null},{output_schema:'object'}])assert.equal((await post(base,{request_id:'bad',prompt:'x',...bad})).status,422)
  assert.equal(calls.length,2)
 })
})
test('schema-invalid JSON, no actual web sources and unsafe citations fail explicitly',async()=>{
 for(const [answer,request,code] of [['not json',{output_schema:schema},'MODEL_INVALID_OUTPUT'],['{"action":"delete"}',{output_schema:schema},'MODEL_INVALID_OUTPUT'],['no search',{web_search:true},'WEB_SEARCH_FAILED'],[{answer:'x',sources:[{title:'x',url:'javascript:alert(1)'}]},{web_search:true},'WEB_SEARCH_FAILED']]){
  await withServer(async()=>answer,async base=>{const response=await post(base,{request_id:'a',prompt:'x',...request});assert.equal(response.status,502);assert.equal((await response.json()).error.code,code)})
 }
})
test('native SDK gets schema, live only explicitly; sandbox shell/apps/network remain disabled',async()=>{
 let codexOptions,threadOptions,runOptions
 class FakeCodex {constructor(options){codexOptions=options}startThread(options){threadOptions=options;return{run:async(input,options)=>{runOptions=options;return{finalResponse:'{"action":"reply"}',items:[]}}}}}
 const home=await mkdtemp(resolve('.dev/local/assistant-capabilities-2026-10-28/native/sdk-test-'))
 await module.executeCodex({home,cwd:home,model:'test',apiKey:'private',baseUrl:'https://provider.example/v1'},{prompt:'x',output_schema:schema},AbortSignal.timeout(1000),{CodexClass:FakeCodex})
 assert.deepEqual(runOptions.outputSchema,schema)
 assert.equal(threadOptions.webSearchMode,'disabled');assert.equal(threadOptions.networkAccessEnabled,false);assert.equal(threadOptions.sandboxMode,'read-only');assert.equal(threadOptions.approvalPolicy,'never')
 assert.equal(codexOptions.config.features.shell_tool,false);assert.equal(codexOptions.config.features.apps,false)
})


test('native web does not reclassify model authentication/TLS problems as an unsupported search capability',async()=>{
 for(const [message,code]of[['HTTP 401 unauthorized private','MODEL_AUTH_ERROR'],['certificate verify failed private','MODEL_TLS_ERROR']]){
  class FailedCodex{startThread(){return{run:async()=>{throw Error(message)}}}}
  await assert.rejects(module.executeCodex({home:'.',cwd:'.',model:'test',apiKey:'private',baseUrl:'https://provider.example/v1'},{prompt:'Search',web_search:true},AbortSignal.timeout(1000),{CodexClass:FailedCodex}),error=>error.code===code)
 }
})

test('native web enables the custom provider standalone capability without changing model or output schema',async()=>{
 let options,threadOptions
 class FakeCodex{constructor(value){options=value}startThread(value){threadOptions=value;return{run:async()=>({finalResponse:'No native evidence',items:[]})}}}
 await assert.rejects(module.executeCodex({home:'.',cwd:'.',model:'test-native',apiKey:'private',baseUrl:'https://provider.example/v1'},{prompt:'Search',web_search:true},AbortSignal.timeout(1000),{CodexClass:FakeCodex}),error=>error.code==='WEB_SEARCH_UNAVAILABLE')
 assert.equal(options.config.model_providers.bervel.supports_standalone_web_search,true)
 assert.equal(threadOptions.model,'test-native');assert.equal(threadOptions.webSearchMode,'live')
})

test('observer preserves TLS classification for ordinary and web model requests after redacting the upstream failure',async()=>{
 const originalFetch=globalThis.fetch
 globalThis.fetch=async(url,options)=>{
  if(String(url)==='https://provider.example/v1/responses')throw new TypeError('fetch failed',{cause:Error('certificate verify failed private')})
  return originalFetch(url,options)
 }
 try{
  for(const web_search of [false,true]){
   let options
   class FakeCodex{constructor(value){options=value}startThread(){return{run:async()=>{
    const response=await originalFetch(options.config.model_providers.bervel.base_url+'/responses',{method:'POST',headers:{Authorization:`Bearer ${options.env.BERVEL_CODEX_API_KEY}`,'Content-Type':'application/json'},body:'{}'})
    assert.equal((await response.text()).includes('private'),false)
    throw Error('Provider transport failed')
   }}}}
   await assert.rejects(module.executeCodex({home:'.',cwd:'.',model:'test',apiKey:'private',baseUrl:'https://provider.example/v1'},{prompt:'test',web_search},AbortSignal.timeout(1000),{CodexClass:FakeCodex}),error=>error.code==='MODEL_TLS_ERROR')
  }
 }finally{globalThis.fetch=originalFetch}
})
