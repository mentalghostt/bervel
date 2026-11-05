"""Engineering boundaries: real files, explicit decisions, isolated jobs."""
from uuid import uuid4
import json
import pytest
from bervel.core.errors import CoreError
from bervel.modules.inspection.redaction import SecretRedactor


def adapter(tmp_path):
    from bervel.modules.inspection.infrastructure.engineering import PatchFiles
    (tmp_path / 'src').mkdir(exist_ok=True)
    return PatchFiles(tmp_path, SecretRedactor())


def test_proposal_is_immutable_and_does_not_write_until_confirmed(tmp_path):
    files = adapter(tmp_path)
    source = tmp_path / 'src/a.py'; source.write_text('x = 1\n')
    prepared = files.prepare([{'path': 'src/a.py', 'content': 'x = 2\n'}])
    assert source.read_text() == 'x = 1\n'
    assert prepared['changes'][0]['baseline']
    result = files.apply(str(uuid4()), prepared)
    assert result['status'] == 'applied'
    assert source.read_text() == 'x = 2\n'


def test_all_baselines_preflight_before_any_write(tmp_path):
    files = adapter(tmp_path)
    for name in ('a', 'b'): (tmp_path / f'src/{name}.py').write_text('x=1\n')
    prepared = files.prepare([{'path': f'src/{n}.py', 'content': 'x=2\n'} for n in ('a', 'b')])
    (tmp_path / 'src/b.py').write_text('human=3\n')
    with pytest.raises(CoreError, match='baseline'):
        files.apply(str(uuid4()), prepared)
    assert (tmp_path / 'src/a.py').read_text() == 'x=1\n'


@pytest.mark.parametrize('path', ['../out.py', '/out.py', '.git/config', '.env', 'src/../x.py', 'src/auth.json', 'src/a.py:stream', 'src//a.py'])
def test_patch_paths_fail_closed(tmp_path, path):
    with pytest.raises(CoreError): adapter(tmp_path).prepare([{'path': path, 'content': 'x=1'}])


def test_symlink_and_secret_or_redacted_replacements_rejected(tmp_path):
    files = adapter(tmp_path)
    (tmp_path / 'src/a.py').symlink_to(tmp_path / 'outside')
    with pytest.raises(CoreError): files.prepare([{'path': 'src/a.py', 'content': 'x=1'}])
    for content in ('password="secret-value"', 'x="[СКРЫТО]"'):
        with pytest.raises(CoreError): files.prepare([{'path': 'src/b.py', 'content': content}])
    (tmp_path / 'src/secret.py').write_text('password="old-secret-value"')
    with pytest.raises(CoreError): files.prepare([{'path': 'src/secret.py', 'content': 'x=1'}])


def test_journal_replay_never_reapplies_and_rejects_mutated_payload(tmp_path):
    files = adapter(tmp_path)
    proposal = files.prepare([{'path': 'src/a.py', 'content': 'x=2\n'}]); identity = str(uuid4())
    assert files.apply(identity, proposal)['status'] == 'applied'
    (tmp_path / 'src/a.py').write_text('human=4\n')
    assert files.apply(identity, proposal)['status'] == 'applied'
    assert (tmp_path / 'src/a.py').read_text() == 'human=4\n'
    proposal['changes'][0]['content'] = 'x=99'
    with pytest.raises(CoreError): files.apply(identity, proposal)


def test_sandbox_specs_exclude_production_mounts_credentials_and_network():
    from bervel.modules.inspection.infrastructure.engineering import TestSandbox
    sandbox = TestSandbox(None, SecretRedactor(), host_root='C:/OSPanel/home/bervel.local',
        database_url='postgresql+psycopg://bervel_core_stage31_test:fake@inspection-test-postgres:5432/bervel_core_stage31_test')
    spec = sandbox.spec('backend', ['.dev/tests/test_inspection_engineering.py'])
    assert spec['HostConfig']['ReadonlyRootfs']
    assert spec['HostConfig']['CapDrop'] == ['ALL']
    assert all(m['ReadOnly'] for m in spec['HostConfig']['Mounts'])
    assert not any('/var/run' in m['Target'] or m['Target'] in ('/sources/project', '/data') for m in spec['HostConfig']['Mounts'])
    assert spec['HostConfig']['NetworkMode'] == 'bervel_inspection_tests'
    assert sandbox.spec('runner', [])['HostConfig']['NetworkMode'] == 'none'
    for targets in [['--help'], ['../test.py'], ['.dev/local/evil.py']]:
        with pytest.raises(CoreError): sandbox.spec('backend', targets)


def test_api_read_rejects_urls_settings_and_redirects():
    from bervel.modules.inspection.infrastructure.engineering import InternalApiReader
    reader = InternalApiReader('http://app:8000', lambda actor: 'private-key', SecretRedactor())
    for path in ['https://evil.test', '//evil.test', '/api/v0.1/settings', '/api/v0.1/artifacts/../settings', '/health/live?url=evil']:
        with pytest.raises(CoreError): reader.read(None, path)


def test_test_sources_never_mount_runner_or_frontend_root():
    from bervel.modules.inspection.infrastructure.engineering import TestSandbox
    sandbox = TestSandbox(None, SecretRedactor(), host_root='/host/project')
    for suite in ('frontend', 'runner'):
        mounts = sandbox.spec(suite, [])['HostConfig']['Mounts']
        assert not any(m['Source'] in {'/host/project/frontend', '/host/project/codex-runner'} for m in mounts)


def test_confirmed_patch_preserves_executable_mode(tmp_path):
    import os
    files = adapter(tmp_path); source = tmp_path / 'src/script.py'
    source.write_text('print(1)\n'); source.chmod(0o755)
    prepared = files.prepare([{'path': 'src/script.py', 'content': 'print(2)\n'}])
    files.apply(str(uuid4()), prepared)
    assert source.stat().st_mode & 0o777 == 0o755


def test_private_executor_requires_bearer_and_exposes_no_confirm_model_operation(tmp_path):
    from fastapi.testclient import TestClient
    from bervel.modules.inspection.infrastructure.engineering import create_engineering_app
    files = adapter(tmp_path)
    client = TestClient(create_engineering_app(files, None, None, SecretRedactor(), 'x' * 32))
    assert client.post('/prepare', json={'changes': []}).status_code == 401
    result = client.post('/run', headers={'Authorization': 'Bearer ' + 'x' * 32}, json={'operation': 'code.confirm', 'arguments': {}})
    assert result.status_code == 422


@pytest.mark.parametrize('path', ['src/CON.py', 'src/a\n.py', 'src/token', 'src/password.txt'])
def test_ambiguous_windows_paths_and_credential_files_are_forbidden(tmp_path, path):
    with pytest.raises(CoreError): adapter(tmp_path).prepare([{'path': path, 'content': 'opaque-value'}])


def test_sandbox_cannot_be_configured_to_production_network_or_database():
    from bervel.modules.inspection.infrastructure.engineering import TestSandbox
    for options in ({'network': 'bervel_default'}, {'database_url': 'postgresql://admin:x@postgres/bervel'}):
        with pytest.raises(ValueError): TestSandbox(None, SecretRedactor(), host_root='/project', **options)


def test_test_failure_is_honest_and_output_is_redacted():
    from bervel.modules.inspection.infrastructure.engineering import TestSandbox
    class FailedJob:
        def run(self, spec): return 7, 'secret-password\nFAILED a_test'
    sandbox = TestSandbox(FailedJob(), SecretRedactor(['secret-password']), host_root='/project')
    result = sandbox.run('runner')
    assert result['state'] == 'failed' and result['exit_code'] == 7
    assert 'secret-password' not in result['output'] and 'FAILED' in result['output']


def test_partial_write_has_durable_unknown_and_is_not_repeated(tmp_path, monkeypatch):
    import os
    files = adapter(tmp_path)
    for name in ('a', 'b'): (tmp_path / f'src/{name}.py').write_text('x=1\n')
    prepared = files.prepare([{'path': f'src/{n}.py', 'content': 'x=2\n'} for n in ('a', 'b')])
    original = os.replace
    def fail_second(source, target, **kwargs):
        if target == 'b.py': raise OSError('interrupted second write')
        return original(source, target, **kwargs)
    monkeypatch.setattr(os, 'replace', fail_second)
    identity = str(uuid4())
    assert files.apply(identity, prepared)['status'] == 'unknown'
    assert (tmp_path / 'src/a.py').read_text() == 'x=2\n'
    assert (tmp_path / 'src/b.py').read_text() == 'x=1\n'
    monkeypatch.setattr(os, 'replace', original)
    assert files.apply(identity, prepared)['status'] == 'unknown'
    assert (tmp_path / 'src/b.py').read_text() == 'x=1\n'


def test_journal_temp_symlink_cannot_truncate_external_file(tmp_path):
    files = adapter(tmp_path); outside = tmp_path / 'outside'; outside.write_text('untouched')
    identity = str(uuid4()); journal = tmp_path / '.dev/local/inspection-engineering-journal'; journal.mkdir(parents=True)
    (journal / (identity + '.tmp')).symlink_to(outside)
    prepared = files.prepare([{'path': 'src/a.py', 'content': 'x=1\n'}])
    files.apply(identity, prepared)
    assert outside.read_text() == 'untouched'


def test_delete_requires_confirmed_exact_baseline(tmp_path):
    files = adapter(tmp_path); target = tmp_path / 'src/a.py'; target.write_text('x=1\n')
    prepared = files.prepare([{'path': 'src/a.py', 'content': None}])
    assert target.exists() and '+++ /dev/null' in prepared['diff']
    assert files.apply(str(uuid4()), prepared)['status'] == 'applied'
    assert not target.exists()


def test_python_syntax_is_targeted_and_never_executes_source(tmp_path):
    from bervel.modules.inspection.infrastructure.engineering import Diagnostics
    (tmp_path / 'src').mkdir()
    (tmp_path / 'src/good.py').write_text('raise RuntimeError("must not execute")\n')
    (tmp_path / 'src/bad.py').write_text('def broken(:\n')
    diagnostics = Diagnostics(tmp_path, SecretRedactor())
    assert diagnostics.run('python.syntax', 'src/good.py')['exit_code'] == 0
    assert diagnostics.run('python.syntax', 'src/bad.py')['exit_code'] == 1
    assert not (tmp_path / 'src/__pycache__').exists()
    with pytest.raises(CoreError): diagnostics.run('python.syntax')


def test_api_redirect_does_not_forward_authorization_to_another_route():
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from threading import Thread
    from bervel.modules.inspection.infrastructure.engineering import InternalApiReader
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(302); self.send_header('Location', '/steal'); self.end_headers()
        def log_message(self, *args): pass
    server = HTTPServer(('127.0.0.1', 0), Handler)
    worker = Thread(target=server.serve_forever); worker.start()
    try:
        reader = InternalApiReader(f'http://127.0.0.1:{server.server_port}', lambda actor: 'private-token', SecretRedactor())
        with pytest.raises(CoreError): reader.read(None, '/health/live')
        assert requests == ['/health/live']
    finally:
        server.shutdown(); worker.join(); server.server_close()


def test_api_allowlist_supports_scoped_ticket_and_radar_reads_and_redacts_results():
    from bervel.modules.inspection.infrastructure.engineering import InternalApiReader
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size): return b'{"password":"never-expose","ok":true}'
    class FixedAPI:
        def open(self, request, timeout):
            assert request.get_method() == 'GET'
            assert request.host == 'api:8011'
            return Response()
    reader = InternalApiReader('http://api:8011', lambda actor: 'private-token', SecretRedactor())
    reader.opener = FixedAPI()
    for path in ['/openapi.json', '/api/v0.1/tickets']:
        value = reader.read(None, path)
        assert value['kind'] == 'inspection_api' and 'never-expose' not in json.dumps(value)
    for path in ['/api/v0.1/radar/batches', '/api/v0.1/radar/runs']:
        with pytest.raises(CoreError) as rejected:
            reader.read(None, path)
        assert rejected.value.code == 'FORBIDDEN'


def test_project_env_inventory_blocks_opaque_credential_source_and_new_content(tmp_path):
    files = adapter(tmp_path)
    secret = 'sample-production-credential-8877'
    (tmp_path / '.env').write_text('BERVEL_DB_PASSWORD=' + secret + '\n')
    (tmp_path / 'src/client.py').write_text('def credential():\n    return "' + secret + '"\n')
    for change in ({'path': 'src/client.py', 'content': 'def credential(): return None\n'},
                   {'path': 'src/other.py', 'content': 'value="' + secret + '"\n'}):
        with pytest.raises(CoreError): files.prepare([change])


def test_private_redaction_refreshes_project_credentials_each_time(tmp_path):
    from fastapi.testclient import TestClient
    from bervel.modules.inspection.infrastructure.engineering import create_engineering_app
    files = adapter(tmp_path)
    client = TestClient(create_engineering_app(files, None, None, files.redactor, 'x' * 32))
    secret = 'new-production-credential-9988'
    (tmp_path / '.env').write_text('BERVEL_DB_PASSWORD=' + secret + '\n')
    response = client.post('/redact', headers={'Authorization': 'Bearer ' + 'x' * 32}, json={'value': {'diff': 'return "' + secret + '"'}})
    assert response.status_code == 200 and secret not in response.text
