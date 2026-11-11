import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, readFile, writeFile, stat } from 'node:fs/promises'
import { resolve, join } from 'node:path'

const { ProviderSettings } = await import('../../codex-runner/settings.mjs')
async function fixture(action) {
  const directory = await mkdtemp(resolve('.dev/local/assistant-capabilities-2026-10-28/native/settings-test-'))
  const env = { CODEX_HOME: directory, BERVEL_MOX_MODEL: 'mox-model' }
  await action(new ProviderSettings(env), env, directory)
}
test('defaults to MOX without requiring an API key and does not expose credentials', async () => {
  await fixture(async store => {
    const value = await store.read()
    assert.equal(value.provider, 'mox')
    assert.equal(value.configured, false)
    assert.equal(value.api.api_key_configured, false)
  })
})
test('stores a write-only API key, survives restart and preserves it on blank update', async () => {
  await fixture(async (store, env, directory) => {
    let value = await store.save({ provider: 'api_key', model: 'api-model', base_url: 'https://api.openai.com/v1', api_key: 'fixture-private-value' })
    assert.equal(value.configured, true)
    assert.equal(JSON.stringify(value).includes('fixture-private-value'), false)
    const restarted = new ProviderSettings(env)
    value = await restarted.save({ provider: 'api_key', model: 'other-model', base_url: 'https://api.openai.com/v1', api_key: '' })
    assert.equal(value.api.model, 'other-model')
    assert.equal((await restarted.configuration()).apiKey, 'fixture-private-value')
    assert.equal((await stat(join(directory, 'provider-settings.json'))).mode & 0o077, process.platform === 'win32' ? 0o066 : 0)
    await restarted.save({ provider: 'mox', model: 'mox-model' })
    assert.equal((await restarted.read()).api.api_key_configured, true)
  })
})
test('reads external MOX auth and config without changing or copying either file', async () => {
  await fixture(async (_, env, directory) => {
    delete env.BERVEL_MOX_MODEL
    env.BERVEL_MOX_AUTH_FILE = join(directory, 'external-auth.fixture')
    env.BERVEL_MOX_CONFIG_FILE = join(directory, 'external-config.fixture')
    const auth = JSON.stringify({ tokens: { access_token: 'fixture-mox-private', account_id: 'fixture-account' } })
    await writeFile(env.BERVEL_MOX_AUTH_FILE, auth)
    await writeFile(env.BERVEL_MOX_CONFIG_FILE, 'model = "existing-model"\nchatgpt_base_url = "https://localhost:8000/backend-api"\n')
    const store = new ProviderSettings(env)
    assert.equal((await store.read()).configured, true)
    assert.equal((await store.read()).mox.model, 'existing-model')
    const config = await store.configuration()
    assert.equal(config.baseUrl, 'https://localhost:8000/backend-api/codex')
    assert.equal(config.apiKey, 'fixture-mox-private')
    assert.equal(config.accountId, 'fixture-account')
    await store.save({ provider: 'mox', model: 'selected-model' })
    assert.equal(await readFile(env.BERVEL_MOX_AUTH_FILE, 'utf8'), auth)
    assert.equal((await readFile(join(directory, 'provider-settings.json'), 'utf8')).includes('fixture-mox-private'), false)
  })
})
test('rejects unknown providers, unsafe URLs, extra fields and oversized secrets', async () => {
  await fixture(async store => {
    for (const body of [
      { provider: 'other', model: 'x' },
      { provider: 'api_key', model: 'x', base_url: 'http://example.com/v1', api_key: 'x' },
      { provider: 'api_key', model: 'x', base_url: 'https://user:pass@example.com/v1', api_key: 'x' },
      { provider: 'mox', model: 'x', auth_path: '/some/path' },
      { provider: 'api_key', model: 'x', base_url: 'https://example.com', api_key: 'x'.repeat(8193) },
    ]) await assert.rejects(store.save(body), error => error.code === 'INVALID_SETTINGS')
  })
})

test('optional external API credential works without copying it into provider settings', async () => {
  await fixture(async (_, env, directory) => {
    env.BERVEL_CODEX_CREDENTIALS_FILE = join(directory, 'external-api.fixture')
    env.BERVEL_CODEX_MODEL = 'external-model'
    await writeFile(env.BERVEL_CODEX_CREDENTIALS_FILE, 'fixture-external-api-key')
    const store = new ProviderSettings(env)
    assert.equal((await store.read()).provider, 'api_key')
    assert.equal((await store.read()).api.api_key_configured, true)
    await store.save({ provider: 'api_key', model: 'new-model', base_url: 'https://api.openai.com/v1' })
    assert.equal((await store.configuration()).apiKey, 'fixture-external-api-key')
    assert.equal((await readFile(join(directory, 'provider-settings.json'), 'utf8')).includes('fixture-external-api-key'), false)
  })
})

test('broken inactive MOX files do not block a valid API profile', async () => {
  await fixture(async (_, env, directory) => {
    env.BERVEL_CODEX_API_KEY = 'fixture-api-key'
    env.BERVEL_CODEX_MODEL = 'api-model'
    env.BERVEL_MOX_CONFIG_FILE = join(directory, 'broken-config.fixture')
    env.BERVEL_MOX_AUTH_FILE = join(directory, 'broken-auth.fixture')
    await writeFile(env.BERVEL_MOX_CONFIG_FILE, 'chatgpt_base_url = "http://broken.example"\n')
    await writeFile(env.BERVEL_MOX_AUTH_FILE, '{broken')
    const store = new ProviderSettings(env)
    const status = await store.read()
    assert.equal(status.provider, 'api_key')
    assert.equal(status.configured, true)
    assert.equal(status.mox.credentials_available, false)
    assert.equal(typeof status.mox.error, 'string')
    assert.equal((await store.configuration()).apiKey, 'fixture-api-key')
    await store.save({ provider: 'api_key', model: 'new-api-model', base_url: 'https://api.openai.com/v1' })
    await store.save({ provider: 'mox', model: 'mox-model' })
    assert.equal((await store.read()).configured, false)
    await assert.rejects(store.configuration())
  })
})
