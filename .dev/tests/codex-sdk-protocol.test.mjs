// Real SDK and CLI against a test Responses server. This is NOT a real model test.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import http from 'node:http'
import { mkdir } from 'node:fs/promises'
import { resolve } from 'node:path'
import { gunzipSync } from 'node:zlib'
const { executeCodex } = await import(process.env.BERVEL_CODEX_SERVER_MODULE || '../../codex-runner/server.mjs')

test('real CLI receives document context and returns the test Responses server output with shell tools disabled', { timeout: 30000 }, async () => {
  const calls = []
  const expected = 'CSV_FIXTURE_482: гайка — 42 шт.'
  const provider = http.createServer(async (request, response) => {
    if (request.method === 'GET') { response.writeHead(200, { 'Content-Type': 'application/json' }); response.end('{"data":[]}'); return }
    const chunks = []
    for await (const chunk of request) chunks.push(chunk)
    let body = Buffer.concat(chunks)
    if (request.headers['content-encoding'] === 'gzip') body = gunzipSync(body)
    calls.push(JSON.parse(body.toString('utf8')))
    response.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' })
    const item = { id: 'msg_fixture', type: 'message', role: 'assistant', status: 'completed', content: [{ type: 'output_text', text: expected, annotations: [] }] }
    const event = data => response.write(`event: ${data.type}\ndata: ${JSON.stringify(data)}\n\n`)
    event({ type: 'response.created', response: { id: 'resp_fixture', model: 'test-model', object: 'response', status: 'in_progress', output: [] } })
    event({ type: 'response.output_item.added', output_index: 0, item: { ...item, status: 'in_progress', content: [] } })
    event({ type: 'response.output_text.delta', item_id: item.id, output_index: 0, content_index: 0, delta: expected })
    event({ type: 'response.output_item.done', output_index: 0, item })
    event({ type: 'response.completed', response: { id: 'resp_fixture', model: 'test-model', object: 'response', status: 'completed', output: [item], usage: { input_tokens: 10, output_tokens: 10, total_tokens: 20 } } })
    response.end()
  })
  await new Promise(done => provider.listen(0, '127.0.0.1', done))
  const home = resolve('.dev/local/assistant-capabilities-2026-10-28/native/protocol-home-' + process.platform)
  const cwd = resolve('.dev/local/assistant-capabilities-2026-10-28/native/protocol-workspace')
  await mkdir(home, { recursive: true }); await mkdir(cwd, { recursive: true })
  try {
    const answer = await executeCodex({ model: 'test-model', baseUrl: `http://127.0.0.1:${provider.address().port}/v1`, apiKey: 'fixture-key', home, cwd },
      { request_id: 'fixture', prompt: 'Что в последнем CSV? SOURCE: CSV_FIXTURE_482; name=гайка; quantity=42' }, AbortSignal.timeout(25000))
    assert.equal(answer, expected)
    assert.equal(calls.length > 0, true)
    assert.equal(JSON.stringify(calls[0]).includes('CSV_FIXTURE_482'), true)
    assert.equal(calls[0].model, 'test-model')
    assert.equal((calls[0].tools || []).some(tool => /shell|exec|apply_patch/.test(tool.name || tool.type || '')), false)
  } finally { await new Promise(done => provider.close(done)) }
})

test('two real SDK threads sharing runner home reach the provider concurrently without mixing prompts', { timeout: 35000 }, async()=>{
  const pending=[],calls=[]
  const provider=http.createServer(async(request,response)=>{
    if(request.method==='GET'){response.writeHead(200,{'Content-Type':'application/json'});response.end('{"data":[]}');return}
    const pieces=[]
    for await(const part of request)pieces.push(part)
    let raw=Buffer.concat(pieces)
    if(request.headers['content-encoding']==='gzip')raw=gunzipSync(raw)
    const supplied=raw.toString('utf8');calls.push(supplied)
    const marker=supplied.includes('PARALLEL_CHAT')?'PARALLEL_CHAT':'PARALLEL_RADAR'
    pending.push({response,marker})
    if(pending.length!==2)return
    for(const {response:out,marker:answer} of pending){
      out.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-cache'})
      const item={id:'msg_'+answer,type:'message',role:'assistant',status:'completed',content:[{type:'output_text',text:answer,annotations:[]}]}
      const event=data=>out.write(`event: ${data.type}\ndata: ${JSON.stringify(data)}\n\n`)
      event({type:'response.created',response:{id:'resp_'+answer,model:'test-model',object:'response',status:'in_progress',output:[]}})
      event({type:'response.output_item.added',output_index:0,item:{...item,status:'in_progress',content:[]}})
      event({type:'response.output_text.delta',item_id:item.id,output_index:0,content_index:0,delta:answer})
      event({type:'response.output_item.done',output_index:0,item})
      event({type:'response.completed',response:{id:'resp_'+answer,model:'test-model',object:'response',status:'completed',output:[item],usage:{input_tokens:10,output_tokens:5,total_tokens:15}}})
      out.end()
    }
  })
  await new Promise(done=>provider.listen(0,'127.0.0.1',done))
  const home=resolve('.dev/local/assistant-capabilities-2026-10-28/native/parallel-sdk/home-' + process.platform)
  const cwd=resolve('.dev/local/assistant-capabilities-2026-10-28/native/parallel-sdk/workspace')
  await mkdir(home,{recursive:true});await mkdir(cwd,{recursive:true})
  try{
    const config={model:'test-model',baseUrl:`http://127.0.0.1:${provider.address().port}/v1`,apiKey:'fixture-key',home,cwd}
    const answers=await Promise.all(['PARALLEL_CHAT','PARALLEL_RADAR'].map(marker=>executeCodex(config,
      {request_id:marker,prompt:'Return this source marker: '+marker},AbortSignal.timeout(30000))))
    assert.deepEqual(answers,['PARALLEL_CHAT','PARALLEL_RADAR'])
    assert.equal(calls.length,2)
    assert.equal(calls.every(body=>body.includes('PARALLEL_CHAT')!==body.includes('PARALLEL_RADAR')),true)
  }finally{await new Promise(done=>provider.close(done))}
})
