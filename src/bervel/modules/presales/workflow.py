"""Durable stages over frozen inputs; model responses and exports have receipts."""
from copy import deepcopy
from hashlib import sha256
import json
from threading import Event, Thread, RLock, local
from uuid import UUID

from bervel.core.errors import CoreError
from bervel.core.models import ActorContext, ArtifactRef, RegisterArtifact
from bervel.modules.presales.analysis import analyze_request, effective_request
from bervel.modules.presales.intake import extract_request
from bervel.modules.presales.offer import build_offer


class LeaseGuard:
    def __init__(self, repository, owned):
        self.repository, self.owned = repository, owned
        self.finished, self.stop = Event(), Event()
        self.failure = None
        self.thread = Thread(target=self._keep_alive, daemon=True, name='presales-lease')

    def _keep_alive(self):
        while not self.finished.wait(5):
            try:
                if self.repository.heartbeat(UUID(self.owned['job_id']), UUID(self.owned['lease_token'])):
                    self.stop.set()
            except Exception as error:
                self.failure = error
                return

    def check(self):
        if self.failure is not None:
            raise self.failure
        if self.stop.is_set() or self.repository.heartbeat(UUID(self.owned['job_id']), UUID(self.owned['lease_token'])):
            raise CoreError('USER_STOPPED', 'Анализ остановлен. Выполненные стадии сохранены.')

    def __enter__(self):
        self.check()
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.finished.set()
        if self.thread.is_alive():
            self.thread.join(timeout=6)


class DurableModel:
    """A completed model response is reused before any network call on resume."""
    def __init__(self, model, repository, owned, guard):
        self.model, self.repository, self.owned, self.guard = model, repository, owned, guard
        self.keys = []
        self.lock, self.current = RLock(), local()
        self.cache = repository.read_job_cache(owned)

    @property
    def last_key(self):
        return getattr(self.current,'last_key',None)

    @last_key.setter
    def last_key(self,key):
        self.current.last_key = key

    def _save(self,key,value):
        with self.lock:
            self.repository.checkpoint(self.owned,key,value)
            self.cache[key] = deepcopy(value)

    def _key(self,request_id,prompt,kwargs):
        fingerprint = sha256(json.dumps([request_id,prompt,kwargs],sort_keys=True,
            ensure_ascii=False,allow_nan=False).encode()).hexdigest()
        return 'model:'+fingerprint

    def cached_response(self,request_id,prompt,**kwargs):
        key = self._key(request_id,prompt,kwargs)
        with self.lock:
            cached = self.cache.get(key)
            if not cached: return None
            if cached['state'] in ('started','unknown'):
                raise CoreError('MODEL_OUTCOME_UNKNOWN','Исход прошлого вызова ИИ неизвестен; требуется явное продолжение.')
            if cached['state'] != 'complete': return None
            self.last_key = key
            if key not in self.keys: self.keys.append(key)
            return deepcopy(cached['result'])

    def validate_last(self):
        with self.lock:
            if self.last_key:
                cached = self.cache.get(self.last_key)
                if cached and cached['state'] == 'complete' and not cached.get('validated'):
                    validated = {**cached,'validated':True}
                    validated.pop('validation_error',None)
                    self._save(self.last_key,validated)

    def reject_last(self,error):
        with self.lock:
            if self.last_key:
                cached = self.cache.get(self.last_key)
                if cached and cached['state']=='complete':
                    self._save(self.last_key,{**cached,'validated':False,'validation_error':deepcopy(error)})

    def validate_all(self):
        original = self.last_key
        with self.lock: keys = list(self.keys)
        for key in keys:
            self.last_key = key
            self.validate_last()
        self.last_key = original

    def generate(self, request_id, prompt, **kwargs):
        self.guard.check()
        key = self._key(request_id,prompt,kwargs)
        with self.lock:
            self.last_key = key
            if key not in self.keys: self.keys.append(key)
            cached = self.cache.get(key)
            if cached and cached['state'] == 'complete': return deepcopy(cached['result'])
            if cached and cached['state'] in ('started','unknown'):
                raise CoreError('MODEL_OUTCOME_UNKNOWN', 'Исход прошлого вызова ИИ неизвестен. Автоматический повтор исключён; явное продолжение может повторить вызов.')
            provider_id = 'presales-' + sha256((self.owned['revision_id'] + key.removeprefix('model:')).encode()).hexdigest()[:56]
            self._save(key, {'state': 'started', 'request_id': provider_id})
        try:
            result = self.model.generate(provider_id, prompt, **kwargs)
        except CoreError as error:
            state = 'unknown' if error.code in ('MODEL_TIMEOUT', 'MODEL_UNAVAILABLE', 'MODEL_OUTCOME_UNKNOWN') else 'failed'
            self._save(key, {'state': state, 'error': {'code': error.code, 'message': error.message}})
            raise
        except Exception:
            self._save(key, {'state': 'unknown'})
            raise CoreError('MODEL_OUTCOME_UNKNOWN', 'Не удалось подтвердить результат вызова ИИ; автоматического повтора нет.') from None
        self._save(key, {'state': 'complete', 'result': result})
        self.guard.check()
        return result


def _checkpoint(repository, owned, guard, model):
    def invalid(payload):
        if isinstance(payload, dict):
            return bool(payload.get('model_error')) or any(invalid(value) for value in payload.values())
        return isinstance(payload, list) and any(invalid(value) for value in payload)
    def save(key, payload):
        valid = not invalid(payload)
        saved = payload
        if key in ('calculate','ai_review') and isinstance(payload,dict):
            # stage:analysis is the full resumable result. Diagnostic stages
            # must not duplicate its entire catalog in the same JSONB object.
            saved = {field:value for field,value in payload.items() if field!='lines'}
            saved['line_count'] = len(payload.get('lines',[]))
        repository.checkpoint(owned, 'detail:' + key, saved)
        if isinstance(payload, dict) and payload.get('batch_id') and valid:
            model.validate_last()
        if key == 'calculate':
            repository.set_phase(owned,'ai_review')
        guard.check()
    return save


def _execute(service, owned, guard):
    repo = service.repository
    actor = ActorContext(owned['actor_id'], owned['data_scope'], frozenset(owned['permissions']))
    request_id, revision_id = UUID(owned['request_id']), UUID(owned['revision_id'])
    revision = repo.read_revision(actor.data_scope, request_id, revision_id)
    inputs, decisions = revision['inputs'], revision['decisions']
    model = DurableModel(service.model, repo, owned, guard)
    save = _checkpoint(repo, owned, guard, model)
    cache = repo.read_job_cache(owned)
    raw = cache.get('stage:extract') or inputs.get('cached_extracted')
    if raw is None:
        guard.check()
        repo.set_phase(owned, 'read_sources')
        sections = []
        for source in inputs['sources']:
            guard.check()
            cache = repo.read_job_cache(owned)
            key = 'source:' + source['artifact_id']
            if key in cache:
                section = cache[key]
            else:
                original = service.artifacts.read(actor, ArtifactRef(UUID(source['artifact_id'])))
                if original.metadata['sha256'] != source['sha256']:
                    raise CoreError('VALIDATION_ERROR', 'Исходный файл отличается от закреплённого снимка.')
                section = list(service.reader.read(original))
                repo.checkpoint(owned, key, section)
            sections.extend(section)
        repo.set_phase(owned, 'extract')
        guard.check()
        raw = extract_request(sections, model, save)
        repo.checkpoint(owned, 'stage:extract', raw)
    extracted = effective_request(deepcopy(raw), decisions)
    guard.check()
    repo.set_phase(owned, 'match', extracted=extracted)
    cache = repo.read_job_cache(owned)
    analysis = cache.get('stage:analysis')
    if analysis is None:
        request = {**extracted, 'settings': inputs['settings']}
        company = {**inputs['company'], 'evaluated_at': revision['evaluated_at']}
        analysis = analyze_request(request, company, decisions, model, save)
        if not analysis.get('model_error'):
            repo.checkpoint(owned, 'stage:analysis', analysis)
    repo.set_phase(owned, 'draft', analysis=analysis)
    guard.check()
    draft_input = {**revision, 'extracted': extracted, 'analysis': analysis}
    offer = build_offer(draft_input, inputs['settings'])
    repo.set_phase(owned, 'draft', offer=offer)
    if analysis.get('model_error'):
        error = analysis['model_error']
        raise CoreError(error.get('code', 'MODEL_FAILED'), error.get('message', 'Анализ ИИ требует продолжения.'))
    guard.check()
    repo.set_phase(owned, 'export')
    artifacts = dict(revision.get('artifacts') or {'pdf': None, 'docx': None})
    # A partially published pair is retained through failure and resumed independently.
    for format, media_type in (('pdf', 'application/pdf'),
                               ('docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')):
        guard.check()
        if artifacts.get(format):
            continue
        key = f'presales-{owned["revision_id"]}-{format}'
        filename = f'KP-{owned["request_id"]}-R{revision["revision_number"]}.{format}'
        ref = service.artifacts.registered_artifact(actor, key)
        if ref is not None:
            metadata = service.artifacts.metadata(actor, ref)
            if (metadata['owner_module_id'] != 'presales' or metadata['filename'] != filename
                    or metadata['media_type'] != media_type
                    or metadata['source'].get('external_id') != owned['revision_id']):
                raise CoreError('IDEMPOTENCY_CONFLICT', 'Ключ экспорта уже принадлежит другому документу.')
        else:
            render = service.renderer.render_pdf if format == 'pdf' else service.renderer.render_docx
            content = render(offer)
            guard.check()
            ref = service.artifacts.register(actor, RegisterArtifact(content, filename, media_type,
                {'origin_system': 'bervel-presales', 'external_id': owned['revision_id'],
                 'received_at': revision['evaluated_at']}, key, owner_module_id='presales'))
        artifacts[format] = str(ref.artifact_id)
        repo.set_phase(owned, 'export', artifacts=artifacts)
    guard.check()
    repo.finish_job(owned, state='complete')


def run_once(service) -> bool:
    owned = service.repository.claim()
    if owned is None:
        return False
    try:
        with LeaseGuard(service.repository, owned) as guard:
            _execute(service, owned, guard)
    except CoreError as error:
        if error.code == 'STALE_ATTEMPT':
            return True
        try:
            service.repository.finish_job(owned, state='stopped' if error.code == 'USER_STOPPED' else 'failed',
                error={'code': error.code, 'message': error.message})
        except CoreError as failure:
            if failure.code != 'STALE_ATTEMPT':
                raise
    except Exception:
        # Expected dependency errors carry their CoreError; unexpected details stay out of public output.
        service.repository.finish_job(owned, state='failed',
            error={'code': 'PRESALES_FAILED', 'message': 'Обработка прервалась. Готовые стадии сохранены; проверьте журнал исполнителя.'})
        import logging
        logging.getLogger(__name__).exception('Presales job failed: %s', owned['job_id'])
    return True
