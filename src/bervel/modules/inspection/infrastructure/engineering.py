"""Private engineering adapters: confined patches, fixed diagnostics and isolated jobs."""
from contextlib import contextmanager
import ast
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
from uuid import UUID, uuid4

from bervel.core.errors import CoreError
from bervel.modules.inspection.redaction import SecretRedactor, HIDDEN
from bervel.modules.inspection.infrastructure.docker import UnixConnection

MAX_BYTES = 1024 * 1024


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def invalid(message='Недопустимые параметры инженерной операции.'):
    raise CoreError('VALIDATION_ERROR', message)


def path_parts(path):
    if not isinstance(path, str) or not path or '\\' in path or ':' in path or any(ord(char) < 32 or ord(char) == 127 for char in path):
        invalid()
    parts = path.split('/')
    if any(p in ('', '.', '..') or p.endswith((' ', '.')) for p in parts): invalid()
    if any(re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', part) for part in parts): invalid()
    if Path(parts[-1]).stem.lower() in {'password', 'passwd', 'credentials', 'secrets', 'token', 'api-key', 'private-key'}:
        raise CoreError('FORBIDDEN', 'Файл учётных данных защищён.')
    if any(p.lower() in {'.git', '.env', '.ssh', 'secrets', 'auth.json', 'credentials.json', 'provider-settings.json', 'voice-settings.json', 'node_modules', '__pycache__'} or p.lower().startswith('.env.') for p in parts):
        raise CoreError('FORBIDDEN', 'Защищённый путь.')
    allowed = parts[0] in {'src', 'frontend', 'codex-runner', 'docker'} or parts[:2] in [['.dev', 'tests'], ['.dev', 'docs'], ['.dev', 'scripts']]
    allowed = allowed or len(parts) == 1 and (path in {'Dockerfile', 'compose.yaml', 'pyproject.toml'} or path.endswith('.md'))
    if not allowed or Path(path).suffix.lower() not in {'.py', '.ts', '.tsx', '.js', '.mjs', '.cjs', '.json', '.md', '.css', '.html', '.yaml', '.yml', '.toml', '.txt', '.sh', '.ps1', ''}:
        raise CoreError('FORBIDDEN', 'Путь вне исходного кода и документации.')
    return parts


class PatchFiles:
    def __init__(self, root, redactor):
        self.root, self.redactor = Path(root), redactor

    def refresh_secrets(self):
        """Observe the project credential inventory without returning or persisting it."""
        root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            try: fd = os.open('.env', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root_fd)
            except FileNotFoundError: return
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_BYTES:
                    raise CoreError('FORBIDDEN', 'Невозможно безопасно проверить инвентарь секретов проекта.')
                raw = stream.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES: raise CoreError('QUOTA_EXCEEDED', 'Инвентарь секретов слишком велик.')
                self.redactor.observe(raw.decode('utf-8'))
        except (OSError, UnicodeError):
            raise CoreError('FORBIDDEN', 'Невозможно безопасно проверить инвентарь секретов проекта.') from None
        finally:
            os.close(root_fd)

    @contextmanager
    def parent(self, path):
        parts = path_parts(path)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fd = os.open(self.root, flags)
        try:
            try:
                for part in parts[:-1]:
                    next_fd = os.open(part, flags, dir_fd=fd)
                    os.close(fd); fd = next_fd
            except OSError:
                raise CoreError('FORBIDDEN', 'Родитель должен быть существующим каталогом без символических ссылок.') from None
            yield fd, parts[-1]
        finally:
            os.close(fd)

    def read(self, path):
        with self.parent(path) as (fd, name):
            try:
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                return None
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_BYTES:
                raise CoreError('FORBIDDEN', 'Допустим только обычный небольшой файл без ссылок.')
            raw_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            with os.fdopen(raw_fd, 'rb') as source:
                opened = os.fstat(source.fileno())
                if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                    raise CoreError('FORBIDDEN', 'Ссылка или специальный файл запрещены.')
                raw = source.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES: invalid()
            try: return raw.decode('utf-8')
            except UnicodeError: invalid('Допустим только UTF-8 текст.')

    def prepare(self, changes):
        self.refresh_secrets()
        if not isinstance(changes, list) or not 1 <= len(changes) <= 20: invalid()
        result, diffs, seen = [], [], set()
        for change in changes:
            if not isinstance(change, dict) or set(change) != {'path', 'content'}: invalid()
            path, content = change['path'], change['content']; path_parts(path)
            if path.casefold() in seen: invalid('Повтор пути.')
            seen.add(path.casefold())
            if content is not None and (not isinstance(content, str) or len(content.encode()) > MAX_BYTES): invalid()
            old = self.read(path)
            for value in (old, content):
                if value is not None and (HIDDEN in value or self.redactor.source(value, path) != value):
                    raise CoreError('FORBIDDEN', 'Файл с секретами или скрытыми фрагментами не может быть заменён.')
            if old is None and content is None: invalid('Удаляемый файл отсутствует.')
            result.append({'path': path, 'content': content, 'baseline': digest(old)})
            diffs.extend(difflib.unified_diff((old or '').splitlines(keepends=True), (content or '').splitlines(keepends=True),
                fromfile='a/' + path if old is not None else '/dev/null', tofile='b/' + path if content is not None else '/dev/null'))
        if len(json.dumps(result).encode()) > 2 * MAX_BYTES: invalid('Патч слишком велик.')
        return {'changes': result, 'diff': ''.join(diffs)}

    @contextmanager
    def journal_directory(self):
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fd = os.open(self.root, flags)
        try:
            for part in ('.dev', 'local', 'inspection-engineering-journal'):
                try: os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError: pass
                next_fd = os.open(part, flags, dir_fd=fd)
                os.close(fd); fd = next_fd
            # A pinned directory descriptor prevents intermediate symlink swaps.
            yield Path('/proc/self/fd') / str(fd), fd
        finally:
            os.close(fd)

    def apply(self, proposal_id, prepared):
        import fcntl
        self.refresh_secrets()
        UUID(proposal_id)
        # The durable journal is outside patchable paths and contains no original secrets.
        with self.journal_directory() as (journal_root, journal_fd), os.fdopen(os.open(
                journal_root / 'lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600), 'a+b') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            journal = journal_root / (proposal_id + '.json')
            fingerprint = digest(prepared)
            if journal.exists():
                with os.fdopen(os.open(journal, os.O_RDONLY | os.O_NOFOLLOW), 'r') as stream:
                    saved = json.load(stream)
                if saved['fingerprint'] != fingerprint: raise CoreError('IDEMPOTENCY_CONFLICT', 'Изменённый патч не повторяется.')
                return {'status': saved['status'] if saved['status'] == 'applied' else 'unknown'}
            # Revalidate all content and baseline before the first source mutation.
            clean = self.prepare([{'path': c['path'], 'content': c['content']} for c in prepared['changes']])
            if clean != prepared: raise CoreError('STALE_REVISION', 'Исходный baseline изменён; создайте новое предложение.')
            staged = journal_root / proposal_id; staged.mkdir()
            for index, change in enumerate(prepared['changes']):
                if change['content'] is not None:
                    with open(staged / str(index), 'x', encoding='utf-8', newline='') as stream:
                        stream.write(change['content']); stream.flush(); os.fsync(stream.fileno())
            state = {'fingerprint': fingerprint, 'status': 'unknown', 'paths': [c['path'] for c in prepared['changes']]}
            def save():
                temp = journal_root / (uuid4().hex + '.tmp')
                with open(temp, 'x', encoding='utf-8') as stream:
                    json.dump(state, stream); stream.flush(); os.fsync(stream.fileno())
                os.replace(temp, journal)
                os.fsync(journal_fd)
            save()
            try:
                for index, change in enumerate(prepared['changes']):
                    if digest(self.read(change['path'])) != change['baseline']:
                        return {'status': 'unknown'}
                    with self.parent(change['path']) as (fd, name):
                        if change['content'] is None: os.unlink(name, dir_fd=fd)
                        else:
                            try: mode = stat.S_IMODE(os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode)
                            except FileNotFoundError: mode = 0o644
                            os.chmod(staged / str(index), mode)
                            os.replace(staged / str(index), name, dst_dir_fd=fd)
                        os.fsync(fd)
                state['status'] = 'applied'; save()
                return {'status': 'applied'}
            except (OSError, CoreError):
                return {'status': 'unknown'}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CoreError('FORBIDDEN', 'Перенаправление внутреннего API запрещено.')


class InternalApiReader:
    def __init__(self, base_url, token_for_actor, redactor):
        self.base_url, self.token_for_actor, self.redactor = base_url.rstrip('/'), token_for_actor, redactor
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def read(self, actor, path):
        patterns = [r'/health/(live|ready)', r'/openapi\.json',
            r'/api/v0\.1/artifacts(?:/[0-9a-fA-F-]{36})?', r'/api/v0\.1/tickets(?:/[0-9a-fA-F-]{36})?']
        if not isinstance(path, str) or not any(re.fullmatch(pattern, path) for pattern in patterns):
            raise CoreError('FORBIDDEN', 'Этот GET-маршрут не разрешён для проверки.')
        token = self.token_for_actor(actor)
        if not token: raise CoreError('FORBIDDEN', 'Нет действующей авторизации пользователя.')
        self.redactor.observe({'token': token})
        request = Request(self.base_url + path, headers={'Authorization': 'Bearer ' + token}, method='GET')
        try:
            with self.opener.open(request, timeout=30) as response:
                raw = response.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES: raise CoreError('QUOTA_EXCEEDED', 'Ответ API слишком велик.')
                value = json.loads(raw)
            return {'kind': 'inspection_api', 'path': path, 'status': response.status, 'result': self.redactor.clean(value)}
        except HTTPError as error:
            code = 'FORBIDDEN' if error.code in {401, 403} else 'NOT_FOUND' if error.code == 404 else 'TRANSIENT_DEPENDENCY_ERROR'
            raise CoreError(code, 'GET-проверка внутреннего API не выполнена.') from None
        except (URLError, ValueError):
            raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'GET-проверка внутреннего API не выполнена.') from None


class DockerJobs:
    """No caller-supplied daemon route; only this instance's newly created jobs."""
    def __init__(self, socket_path='/var/run/docker.sock'):
        self.socket_path, self.owned = socket_path, set()

    def _request(self, method, path, body=None, raw=False):
        connection = UnixConnection(self.socket_path); connection.timeout = 900
        try:
            connection.request(method, '/v1.53' + path, body=json.dumps(body).encode() if body is not None else None,
                headers={'Content-Type': 'application/json'})
            response = connection.getresponse(); data = response.read(4 * MAX_BYTES + 1)
            if response.status not in {200, 201, 204}: raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'Изолированный тестовый контейнер недоступен.')
            if len(data) > 4 * MAX_BYTES: raise CoreError('QUOTA_EXCEEDED', 'Вывод тестов превышает 4 МиБ.')
            return data if raw else json.loads(data) if data else {}
        except OSError:
            raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'Docker тестов недоступен.') from None
        finally: connection.close()

    def run(self, spec):
        if spec.get('Labels', {}).get('bervel.inspection.job') != 'test': invalid()
        identifier = self._request('POST', '/containers/create', spec)['Id']
        if not re.fullmatch('[a-f0-9]{64}', identifier): invalid()
        self.owned.add(identifier)
        try:
            self._request('POST', '/containers/' + identifier + '/start')
            result = self._request('POST', '/containers/' + identifier + '/wait?condition=not-running')
            raw = self._request('GET', '/containers/' + identifier + '/logs?stdout=1&stderr=1&tail=20000', raw=True)
            return result['StatusCode'], raw.decode('utf-8', errors='replace')
        finally:
            if identifier in self.owned:
                self._request('DELETE', '/containers/' + identifier + '?force=1&v=1')
                self.owned.remove(identifier)


class TestSandbox:
    def __init__(self, transport, redactor, *, host_root, database_url='', network='bervel_inspection_tests'):
        self.transport, self.redactor, self.host_root = transport, redactor, host_root.rstrip('/\\')
        self.database_url, self.network = database_url, network
        if network != 'bervel_inspection_tests': raise ValueError('Dedicated isolated test network required')
        if database_url:
            parsed = urlsplit(database_url)
            if parsed.path != '/bervel_core_stage31_test' or parsed.username != 'bervel_core_stage31_test' or parsed.hostname != 'inspection-test-postgres' or parsed.query:
                raise ValueError('Dedicated isolated test database required')
            self.redactor.observe({'password': parsed.password})

    def spec(self, suite, targets):
        if suite not in {'backend', 'frontend', 'runner'} or not isinstance(targets, list) or len(targets) > 20: invalid()
        for target in targets:
            pattern = r'\.dev/tests/test_[A-Za-z0-9_]+\.py(?:::[A-Za-z0-9_]+)?' if suite == 'backend' else r'\.dev/tests/[A-Za-z0-9_-]+\.test\.mjs'
            if not isinstance(target, str) or not re.fullmatch(pattern, target): invalid()
        mounts = []
        scratch = {'/tmp': 'rw,nosuid,nodev,size=1073741824,mode=1777'}
        def mount(path, target=None): mounts.append({'Type': 'bind', 'Source': self.host_root + '/' + path, 'Target': target or '/workspace/' + path, 'ReadOnly': True})
        mount('.dev/tests'); mount('.dev/scripts'); mount('.dev/pytest.ini')
        env = ['HOME=/tmp', 'TMPDIR=/tmp']
        if suite == 'backend':
            if not self.database_url: raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'Изолированная тестовая БД не настроена.')
            mount('src')
            for filename in ('pyproject.toml', 'Dockerfile', 'requirements', '.dev/templates', '.dev/docs', '.dev/fixtures'):
                mount(filename)
            command = ['python', '-m', 'pytest', '-c', '.dev/pytest.ini', '--rootdir=.', '--basetemp=/tmp/pytest', '-q', *(targets or ['.dev/tests'])]
            env += ['PYTHONPATH=/workspace/src', 'BERVEL_TEST_DATABASE_URL=' + self.database_url, 'PYTHONDONTWRITEBYTECODE=1']
            image, network = 'bervel-tests:inspection', self.network
        elif suite == 'runner':
            for filename in ('images.mjs', 'mox-bridge.mjs', 'server.mjs', 'settings.mjs', 'structured.mjs', 'voice.mjs', 'web-evidence.mjs', 'package.json'):
                mount('codex-runner/' + filename)
            script = "const fs=require('fs'),cp=require('child_process');fs.mkdirSync('/tmp/workspace/.dev/local/assistant-capabilities-2026-10-28/native',{recursive:true});fs.cpSync('/workspace/.dev/tests','/tmp/workspace/.dev/tests',{recursive:true});fs.cpSync('/workspace/codex-runner','/tmp/workspace/codex-runner',{recursive:true});fs.symlinkSync('/app/node_modules','/tmp/workspace/node_modules');const r=cp.spawnSync('node',['--test',...process.argv.slice(1)],{cwd:'/tmp/workspace',stdio:'inherit'});process.exit(r.status===null?1:r.status)"
            command = ['node', '-e', script, *(targets or ['.dev/tests/runner-capabilities.test.mjs', '.dev/tests/structured-output.test.mjs'])]
            env += ['CODEX_HOME=/tmp/codex', 'BERVEL_CODEX_WORKSPACE=/tmp/workspace']
            image, network = 'bervel-codex:local', 'none'
        else:
            if targets: invalid('Frontend запускает фиксированную проверку сборки.')
            for cache in ('.tmp', '.vite-temp'):
                scratch['/app/node_modules/' + cache] = 'rw,nosuid,nodev,size=33554432,mode=1777'
            for filename in ('src', 'public', 'package.json', 'package-lock.json', 'tsconfig.json', 'tsconfig.app.json', 'tsconfig.node.json', 'vite.config.ts', 'index.html'):
                mount('frontend/' + filename, '/source/frontend/' + filename)
            # Copy source into disposable scratch; original mount remains read-only.
            script = "const fs=require('fs'),cp=require('child_process');fs.cpSync('/source/frontend','/tmp/frontend',{recursive:true});fs.symlinkSync('/app/node_modules','/tmp/frontend/node_modules');for(const args of [['node_modules/typescript/bin/tsc','-b'],['node_modules/vite/bin/vite.js','build']]){const r=cp.spawnSync('node',args,{cwd:'/tmp/frontend',stdio:'inherit'});if(r.status!==0)process.exit(r.status||1)}"
            command = ['node', '-e', script]; image, network = 'bervel-frontend-tests:inspection', 'none'
        return {'Image': image, 'Cmd': command, 'Entrypoint': [], 'WorkingDir': '/workspace', 'Env': env,
            'User': '65534:65534', 'Tty': True, 'Labels': {'bervel.inspection.job': 'test', 'bervel.inspection.id': uuid4().hex},
            'HostConfig': {'ReadonlyRootfs': True, 'NetworkMode': network, 'CapDrop': ['ALL'], 'SecurityOpt': ['no-new-privileges'],
                'PidsLimit': 256, 'Memory': 2 * 1024**3, 'NanoCpus': 2_000_000_000, 'Mounts': mounts,
                'Tmpfs': scratch, 'LogConfig': {'Type': 'json-file', 'Config': {'max-size': '4m', 'max-file': '1'}}}}

    def run(self, suite, targets=None):
        code, output = self.transport.run(self.spec(suite, targets or []))
        clean = self.redactor.clean(output)
        return {'kind': 'inspection_test', 'suite': suite, 'state': 'passed' if code == 0 else 'failed', 'exit_code': code,
            'summary': 'Проверки прошли.' if code == 0 else 'Проверки завершились ошибкой.', 'output': clean[:24000],
            'total_chars': len(clean), 'truncated': len(clean) > 24000, 'coverage': 'Именованный набор в изолированном контейнере; вывод ограничен последними 20000 строками и первыми 24000 символами.'}


class Diagnostics:
    def __init__(self, root, redactor): self.root, self.redactor = Path(root), redactor
    def run(self, check, path=None):
        if check == 'python.syntax':
            if not path or not path.endswith('.py'): invalid()
            source = PatchFiles(self.root, self.redactor).read(path)
            if source is None: raise CoreError('NOT_FOUND', 'Файл отсутствует.')
            try: ast.parse(source, filename=path); code, output = 0, 'Синтаксис Python корректен.'
            except SyntaxError as error: code, output = 1, f'Ошибка синтаксиса: строка {error.lineno}.'
        elif check in {'git.status', 'git.diff'}:
            if path is not None: path_parts(path)
            args = ['git', '--no-optional-locks', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null', '-c', 'core.untrackedCache=false', '-c', 'safe.directory=' + str(self.root)]
            args += ['status', '--porcelain=v1', '--untracked-files=normal', '--ignore-submodules=all'] if check == 'git.status' else ['diff', '--no-ext-diff', '--no-textconv', '--no-color', '--ignore-submodules=all']
            if path: args += ['--', path]
            result = subprocess.run(args, cwd=self.root, env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': '/tmp', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_TERMINAL_PROMPT': '0'}, capture_output=True, timeout=30)
            code, output = result.returncode, (result.stdout + result.stderr).decode('utf-8', errors='replace')
        else: invalid()
        clean = self.redactor.clean(output)
        return {'kind': 'inspection_diagnostic', 'check': check, 'exit_code': code, 'output': clean[:24000], 'total_chars': len(clean), 'truncated': len(clean) > 24000}

class EngineeringClient:
    def __init__(self, base_url, token):
        self.base_url, self.token = base_url.rstrip('/'), token
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def _request(self, path, body):
        request = Request(self.base_url + path, data=json.dumps(body).encode(),
            headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'}, method='POST')
        try:
            with self.opener.open(request, timeout=960) as response:
                raw = response.read(4 * MAX_BYTES + 1)
                if len(raw) > 4 * MAX_BYTES: raise CoreError('QUOTA_EXCEEDED', 'Ответ слишком велик.')
                return json.loads(raw)
        except HTTPError as error:
            try: code = json.loads(error.read(4096)).get('error', {}).get('code')
            except ValueError: code = None
            allowed = {'FORBIDDEN', 'NOT_FOUND', 'VALIDATION_ERROR', 'STALE_REVISION', 'IDEMPOTENCY_CONFLICT', 'QUOTA_EXCEEDED'}
            raise CoreError(code if code in allowed else 'TRANSIENT_DEPENDENCY_ERROR', 'Инженерная операция не выполнена.') from None
        except (URLError, OSError, ValueError):
            raise CoreError('ENGINE_RESPONSE_UNKNOWN', 'Сервис инженерных операций недоступен; исход записи требует проверки.') from None

    def prepare(self, changes): return self._request('/prepare', {'changes': changes})
    def apply(self, proposal_id, prepared): return self._request('/apply', {'proposal_id': proposal_id, 'prepared': prepared})
    def run(self, operation, arguments): return self._request('/run', {'operation': operation, 'arguments': arguments})
    def redact(self, value): return self._request('/redact', {'value': value})['value']


def create_engineering_app(files, diagnostics, tests, redactor, token):
    from typing import Annotated, Any
    from fastapi import FastAPI, Header, HTTPException, Depends
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel, ConfigDict, Field
    if len(token) < 32: raise ValueError('Private inspection token is required')
    def check(authorization: Annotated[str | None, Header()] = None):
        if not authorization or not secrets.compare_digest(authorization, 'Bearer ' + token): raise HTTPException(401)
    app = FastAPI(title='BERVEL engineering executor', docs_url=None, redoc_url=None, openapi_url=None)
    class Body(BaseModel):
        model_config = ConfigDict(extra='forbid')
    class Prepare(Body):
        changes: list[dict] = Field(min_length=1, max_length=20)
    class Apply(Body):
        proposal_id: UUID
        prepared: dict
    class Run(Body):
        operation: str
        arguments: dict
    class Redact(Body):
        value: Any
    @app.exception_handler(CoreError)
    async def failure(request, error):
        status = {'FORBIDDEN': 403, 'NOT_FOUND': 404, 'VALIDATION_ERROR': 422, 'STALE_REVISION': 409, 'IDEMPOTENCY_CONFLICT': 409}.get(error.code, 503)
        return JSONResponse({'error': {'code': error.code, 'message': 'Инженерная операция не выполнена.'}}, status_code=status)
    @app.exception_handler(Exception)
    async def unexpected(request, error):
        return JSONResponse({'error': {'code': 'ENGINE_RESPONSE_UNKNOWN', 'message': 'Исход операции требует проверки.'}}, status_code=503)
    @app.get('/health/live')
    def health(): return {'status': 'ok'}
    @app.post('/prepare', dependencies=[Depends(check)])
    def prepare(body: Prepare): return files.prepare(body.changes)
    @app.post('/apply', dependencies=[Depends(check)])
    def apply(body: Apply): return files.apply(str(body.proposal_id), body.prepared)
    @app.post('/redact', dependencies=[Depends(check)])
    def redact(body: Redact):
        files.refresh_secrets()
        return {'value': redactor.clean(body.value)}
    @app.post('/run', dependencies=[Depends(check)])
    def run(body: Run):
        files.refresh_secrets()
        if body.operation == 'diagnostic.run' and set(body.arguments) <= {'check', 'path'}: return diagnostics.run(**body.arguments)
        if body.operation == 'test.run' and set(body.arguments) <= {'suite', 'targets'}: return tests.run(**body.arguments)
        invalid()
    return app


if __name__ == '__main__':
    import uvicorn
    redactor = SecretRedactor(); redactor.observe(dict(os.environ))
    root = Path(os.environ.get('BERVEL_ENGINEERING_PROJECT_ROOT', '/sources/project'))
    tests = TestSandbox(DockerJobs(), redactor, host_root=os.environ['BERVEL_ENGINEERING_HOST_PROJECT_ROOT'],
        database_url=os.environ.get('BERVEL_ENGINEERING_TEST_DATABASE_URL', ''),
        network=os.environ.get('BERVEL_ENGINEERING_TEST_NETWORK', 'bervel_inspection_tests'))
    uvicorn.run(create_engineering_app(PatchFiles(root, redactor),
        Diagnostics(Path('/sources/diagnostics'), redactor), tests, redactor, os.environ['BERVEL_INSPECTION_TOKEN']),
        host='0.0.0.0', port=8092, access_log=False)
