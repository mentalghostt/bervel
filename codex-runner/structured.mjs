import { isDeepStrictEqual } from 'node:util'

// Small explicit JSON Schema vocabulary used by the assistant planner. Unknown
// assertions are rejected, rather than silently claiming to validate them.
const types = new Set(['object', 'array', 'string', 'number', 'integer', 'boolean', 'null'])
const keywords = new Set(['$schema', '$defs', '$ref', 'title', 'description', 'type', 'properties', 'required', 'additionalProperties',
  'items', 'enum', 'const', 'anyOf', 'oneOf', 'allOf', 'minLength', 'maxLength', 'minItems', 'maxItems', 'minimum', 'maximum', 'format'])
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value)

export function checkSchema(schema, root = schema, depth = 0) {
  if (!object(schema) || depth > 32 || Object.keys(schema).some(key => !keywords.has(key))) throw Error('Unsupported schema')
  if (schema.type !== undefined && !(Array.isArray(schema.type) ? schema.type.length && schema.type.every(type => types.has(type)) : types.has(schema.type))) throw Error('Invalid type')
  if (schema.format !== undefined && schema.format !== 'uuid') throw Error('Unsupported format')
  if (schema.$ref !== undefined) {
    if (typeof schema.$ref !== 'string' || !schema.$ref.startsWith('#/$defs/') || !object(root.$defs?.[schema.$ref.slice(8)])) throw Error('Invalid reference')
  }
  for (const key of ['$defs', 'properties']) if (schema[key] !== undefined) {
    if (!object(schema[key])) throw Error('Invalid mapping')
    for (const child of Object.values(schema[key])) checkSchema(child, root, depth + 1)
  }
  if (schema.required !== undefined && (!Array.isArray(schema.required) || schema.required.some(key => typeof key !== 'string'))) throw Error('Invalid required')
  if (schema.additionalProperties !== undefined && typeof schema.additionalProperties !== 'boolean') checkSchema(schema.additionalProperties, root, depth + 1)
  if (schema.items !== undefined) checkSchema(schema.items, root, depth + 1)
  for (const key of ['anyOf', 'oneOf', 'allOf']) if (schema[key] !== undefined) {
    if (!Array.isArray(schema[key]) || !schema[key].length) throw Error('Invalid composition')
    for (const child of schema[key]) checkSchema(child, root, depth + 1)
  }
  if (schema.enum !== undefined && (!Array.isArray(schema.enum) || !schema.enum.length)) throw Error('Invalid enum')
  for (const key of ['minLength', 'maxLength', 'minItems', 'maxItems']) if (schema[key] !== undefined && (!Number.isInteger(schema[key]) || schema[key] < 0)) throw Error('Invalid bound')
  for (const key of ['minimum', 'maximum']) if (schema[key] !== undefined && (typeof schema[key] !== 'number' || !Number.isFinite(schema[key]))) throw Error('Invalid bound')
}

export function matchesSchema(value, schema, root = schema, depth = 0) {
  if (depth > 64) return false
  if (schema.$ref && !matchesSchema(value, root.$defs[schema.$ref.slice(8)], root, depth + 1)) return false
  if (schema.anyOf && !schema.anyOf.some(child => matchesSchema(value, child, root, depth + 1))) return false
  if (schema.oneOf && schema.oneOf.filter(child => matchesSchema(value, child, root, depth + 1)).length !== 1) return false
  if (schema.allOf && !schema.allOf.every(child => matchesSchema(value, child, root, depth + 1))) return false
  const isType = type => type === 'null' ? value === null : type === 'array' ? Array.isArray(value) : type === 'object' ? object(value)
    : type === 'integer' ? Number.isInteger(value) : typeof value === type && (type !== 'number' || Number.isFinite(value))
  if (schema.type !== undefined && !(Array.isArray(schema.type) ? schema.type.some(isType) : isType(schema.type))) return false
  if (schema.enum && !schema.enum.some(option => isDeepStrictEqual(value, option))) return false
  if ('const' in schema && !isDeepStrictEqual(value, schema.const)) return false
  if (object(value)) {
    if (schema.required?.some(key => !Object.hasOwn(value, key))) return false
    for (const [key, item] of Object.entries(value)) {
      if (Object.hasOwn(schema.properties || {}, key)) {
        if (!matchesSchema(item, schema.properties[key], root, depth + 1)) return false
      } else if (schema.additionalProperties === false || (object(schema.additionalProperties) && !matchesSchema(item, schema.additionalProperties, root, depth + 1))) return false
    }
  }
  if (Array.isArray(value) && ((schema.minItems !== undefined && value.length < schema.minItems) || (schema.maxItems !== undefined && value.length > schema.maxItems)
    || (schema.items && !value.every(item => matchesSchema(item, schema.items, root, depth + 1))))) return false
  if (typeof value === 'string') {
    const length = [...value].length
    if ((schema.minLength !== undefined && length < schema.minLength) || (schema.maxLength !== undefined && length > schema.maxLength)) return false
    if (schema.format === 'uuid' && !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)) return false
  }
  if (typeof value === 'number' && ((schema.minimum !== undefined && value < schema.minimum) || (schema.maximum !== undefined && value > schema.maximum))) return false
  return true
}

export function validSources(sources) {
  if (!Array.isArray(sources) || !sources.length || sources.length > 100) return false
  return sources.every(source => {
    if (!object(source) || typeof source.title !== 'string' || !source.title.trim() || source.title.length > 500
      || typeof source.url !== 'string' || source.url.length > 2048) return false
    try {
      const url = new URL(source.url)
      return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password && Boolean(url.hostname)
    } catch { return false }
  })
}
