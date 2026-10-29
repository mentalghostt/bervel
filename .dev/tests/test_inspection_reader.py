"""Attack the read boundary, rather than merely checking its implementation shape."""
import io
import json
import tarfile

import pytest
from sqlalchemy import text

from bervel.core.errors import CoreError
from bervel.modules.inspection.redaction import SecretRedactor
from bervel.modules.inspection.infrastructure.files import InspectionFiles
from bervel.modules.inspection.infrastructure.database import InspectionDatabase
from bervel.modules.inspection.infrastructure.docker import InspectionDocker
from bervel.modules.inspection.service import InspectionReader


def test_secrets_are_hidden_in_structures_free_text_urls_and_pem():
    secret = 'a-real-token-not-matching-a-provider-prefix'
    redactor = SecretRedactor([secret])
    value = {'model': 'actual-model', 'api_key': secret, 'nested': {'password': 'unseen-password'},
        'text': f'plain echo {secret}\nDATABASE_URL=postgres://reader:dbpass@postgres/db\nAuthorization: Bearer other-secret',
        'private': '-----BEGIN PRIVATE KEY-----\nABCDEF\n-----END PRIVATE KEY-----'}
    cleaned = json.dumps(redactor.clean(value))
    for hidden in [secret, 'unseen-password', 'dbpass', 'other-secret', 'ABCDEF']:
        assert hidden not in cleaned
    assert 'actual-model' in cleaned
    assert redactor.clean({'api_key_configured': True}) == {'api_key_configured': True}


@pytest.mark.parametrize('value', ['PWD=synthetic-secret', 'KEY=synthetic-secret', 'Cookie: session=synthetic-secret; another=another-secret', 'Set-Cookie: session=synthetic-secret; HttpOnly'])
def test_text_secret_names_and_headers_match_structured_redaction(value):
    redactor = SecretRedactor()
    assert 'synthetic-secret' not in redactor.clean(value)
    assert 'synthetic-secret' not in redactor.clean('unlabelled echo synthetic-secret')


@pytest.mark.parametrize('value', ['database:\n  password: nested-secret', 'config:\n  token: nested-secret', 'foo = { password = "nested-secret" }', 'URL=https://example.test/path?token=nested-secret&safe=yes', 'token: |\n  nested-secret\n', '<password>nested-secret</password>'])
def test_nested_text_formats_do_not_hide_credentials_from_the_scanner(value):
    assert 'nested-secret' not in SecretRedactor().clean(value)


def test_timestamped_headers_hide_all_cookie_pairs_and_learn_bare_auth_tokens():
    redactor = SecretRedactor()
    value = redactor.clean('2026-10-29T10:00:00Z Cookie: session=first-fake-value; other=second-fake-value')
    assert 'first-fake-value' not in value and 'second-fake-value' not in value
    assert 'second-fake-value' not in redactor.clean('echo second-fake-value')
    redactor.clean('Authorization: Bearer synthetic-header-token')
    assert 'synthetic-header-token' not in redactor.clean('echo synthetic-header-token')


@pytest.mark.parametrize('secret', ['opaque-model-secret-with-no-prefix', 'c3ludGhldGljLXNlY3JldA==', 'opaque:value'])
def test_opaque_credential_file_is_hidden_and_teaches_unlabelled_echoes(tmp_path, secret):
    (tmp_path / 'model-credentials').write_text(secret)
    redactor = SecretRedactor()
    files = InspectionFiles({'project': tmp_path}, redactor)
    assert secret not in files.read(root='project', path='model-credentials')['text']
    assert secret not in redactor.clean('echo ' + secret)


def test_file_masks_before_paging_and_can_continue_without_losing_characters(tmp_path):
    secret = 'my-very-long-password-0123456789'
    (tmp_path / '.env').write_text('MODEL=actual\nPASSWORD=' + secret + '\nEND=yes\n')
    files = InspectionFiles({'project': tmp_path}, SecretRedactor())
    whole = files.read(root='project', path='.env', limit=32000)
    parts, offset = [], 0
    while True:
        page = files.read(root='project', path='.env', offset=offset, limit=9)
        parts.append(page['text'])
        if page['next_offset'] is None:
            break
        offset = page['next_offset']
    assert ''.join(parts) == whole['text']
    assert secret not in whole['text']
    assert 'MODEL=actual' in whole['text'] and 'END=yes' in whole['text']
    assert (tmp_path / '.env').read_text().endswith('END=yes\n')


def test_file_scope_rejects_traversal_absolute_paths_and_outside_symlinks(tmp_path):
    root = tmp_path / 'root'; root.mkdir()
    outside = tmp_path / 'outside'; outside.write_text('outside')
    (root / 'escape').symlink_to(outside)
    files = InspectionFiles({'project': root}, SecretRedactor())
    for path in ['../outside', '/etc/passwd', 'escape', 'C:\\Windows\\win.ini']:
        with pytest.raises(CoreError):
            files.read(root='project', path=path)
    assert files.list(root='project')['entries'][0]['kind'] == 'symlink'


def test_special_files_are_rejected_without_opening_them(tmp_path):
    import os
    os.mkfifo(tmp_path / 'fifo')
    with pytest.raises(CoreError):
        InspectionFiles({'project': tmp_path}, SecretRedactor()).read(root='project', path='fifo')


def test_intermediate_symlink_swap_cannot_escape_after_path_resolution(tmp_path, monkeypatch):
    root = tmp_path / 'root'; root.mkdir()
    folder = root / 'folder'; folder.mkdir(); (folder / 'file').write_text('inside')
    outside = tmp_path / 'outside'; outside.mkdir(); (outside / 'file').write_text('outside')
    files = InspectionFiles({'project': root}, SecretRedactor())
    original = files._path
    def race(root_name, path):
        value = original(root_name, path)
        folder.rename(root / 'old'); folder.symlink_to(outside)
        return value
    monkeypatch.setattr(files, '_path', race)
    with pytest.raises(CoreError): files.read(root='project', path='folder/file')


def test_database_only_exposes_structured_base_table_reads(pg_engine):
    with pg_engine.begin() as c:
        c.execute(text('CREATE TABLE inspection_fixture (id integer PRIMARY KEY, label text, password text)'))
        c.execute(text("INSERT INTO inspection_fixture VALUES (1, 'actual', 'hidden-db-value'), (2, 'next', NULL)"))
        schema = c.execute(text('SELECT current_schema()')).scalar_one()
    reader = InspectionDatabase(pg_engine, SecretRedactor(), schema=schema)
    assert 'inspection_fixture' in [row['name'] for row in reader.tables()['tables']]
    assert {c['name'] for c in reader.schema(table='inspection_fixture')['columns']} == {'id', 'label', 'password'}
    result = reader.rows(table='inspection_fixture', limit=1)
    assert result['rows'][0]['label'] == 'actual'
    assert 'hidden-db-value' not in json.dumps(result)
    assert result['next_offset'] == 1
    assert reader.rows(table='inspection_fixture', offset=1)['rows'][0]['label'] == 'next'
    assert reader.rows(table='inspection_fixture', where={'label': 'actual'})['total'] == 1
    with pytest.raises(CoreError):
        reader.rows(table='inspection_fixture; DROP TABLE inspection_fixture')
    with pytest.raises(CoreError):
        reader.rows(table='inspection_fixture', columns=['pg_sleep(10)'])


class DockerTransport:
    def __init__(self): self.requests = []
    def get(self, path, query=None, *, raw=False):
        self.requests.append((path, query))
        if path == '/containers/json':
            return [{'Id': 'owned-id', 'Names': ['/bervel-api-1'], 'Labels': {'com.docker.compose.project': 'bervel'},
                'State': 'running', 'Image': 'bervel-api:local'}]
        if path == '/containers/owned-id/json':
            return {'Id': 'owned-id', 'Config': {'Labels': {'com.docker.compose.project': 'bervel'},
                'Env': ['MODEL=actual', 'API_KEY=container-secret']}}
        if path.endswith('/logs'): return b'2026-10-29T09:00:00Z Authorization: Bearer log-secret\nhealthy\n'
        if path.endswith('/archive'):
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode='w') as archive:
                data = b'PASSWORD=volume-secret\nactual\n'
                member = tarfile.TarInfo('settings.env'); member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
            return stream.getvalue()
        raise AssertionError('Unexpected Docker endpoint: ' + path)


def test_docker_is_project_scoped_and_masks_inspect_logs_and_archive():
    transport = DockerTransport()
    reader = InspectionDocker(transport, SecretRedactor())
    assert reader.list()['containers'][0]['name'] == 'bervel-api-1'
    for data in [reader.inspect(container='owned-id'), reader.logs(container='owned-id'),
                 reader.files(container='owned-id', path='/data/settings.env')]:
        saved = json.dumps(data)
        assert all(secret not in saved for secret in ['container-secret', 'log-secret', 'volume-secret'])
    with pytest.raises(CoreError): reader.inspect(container='unrelated-project')
    assert all('exec' not in path and 'start' not in path for path, _ in transport.requests)


@pytest.mark.parametrize('operation', ['file.create', 'ticket.edit', 'settings.update', 'container.exec', 'database.sql', '/containers/create'])
def test_reader_rejects_every_non_read_operation_before_calling_any_adapter(operation):
    reader = InspectionReader(files=None, database=None, docker=None, redactor=SecretRedactor())
    with pytest.raises(CoreError) as error:
        reader.read(operation, {})
    assert error.value.code == 'FORBIDDEN'
