import { test } from 'node:test'
import assert from 'node:assert/strict'
import { checkSchema, matchesSchema } from '../../codex-runner/structured.mjs'

test('planner nullable references and uuid contracts are validated independently of provider',()=>{
 const schema={type:'object',$defs:{ref:{type:'object',properties:{id:{type:'string',format:'uuid'}},required:['id'],additionalProperties:false}},properties:{file:{$ref:'#/$defs/ref'},limit:{anyOf:[{type:'integer',minimum:1,maximum:100},{type:'null'}]}},required:['file','limit'],additionalProperties:false}
 checkSchema(schema)
 assert.equal(matchesSchema({file:{id:'df429b20-f24f-4e14-94b8-4d6f4d4b17c3'},limit:null},schema),true)
 for(const value of [{file:{id:'guessed'},limit:null},{file:{id:'df429b20-f24f-4e14-94b8-4d6f4d4b17c3'},limit:101},{file:{id:'df429b20-f24f-4e14-94b8-4d6f4d4b17c3'},limit:true}])assert.equal(matchesSchema(value,schema),false)
})
test('unsupported assertions and invalid references are explicit, never silently ignored',()=>{
 for(const schema of [{type:'object',patternProperties:{}},{type:'string',format:'anything'},{type:'object',$ref:'https://outside/schema'},{$ref:'#/$defs/missing'}, {type:'integer',minimum:'1'}])assert.throws(()=>checkSchema(schema))
})
test('JSON enum/const object equality does not depend on property order',()=>{
 const schema={enum:[{a:1,b:2}]};checkSchema(schema)
 assert.equal(matchesSchema({b:2,a:1},schema),true)
 assert.equal(matchesSchema({b:3,a:1},schema),false)
})
