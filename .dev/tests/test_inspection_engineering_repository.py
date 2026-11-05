"""Real PostgreSQL decisions and public HTTP authorization."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import UUID
from sqlalchemy import select
import pytest
from bervel.core.errors import CoreError
from bervel.core.models import ActorContext
from bervel.core.infrastructure.documents import PostgresDocumentRepository
from bervel.modules.chat.infrastructure.repository import PostgresChatRepository
from bervel.modules.inspection.engineering import EngineeringService
from bervel.modules.inspection.infrastructure.engineering import PatchFiles
from bervel.modules.inspection.infrastructure.engineering_repository import PostgresEngineeringRepository
from bervel.modules.inspection.infrastructure.engineering_tables import engineering_proposals
from bervel.modules.inspection.redaction import SecretRedactor


class LocalExecutor:
    def __init__(self, root): self.files, self.calls = PatchFiles(root, SecretRedactor()), 0
    def prepare(self, changes): return self.files.prepare(changes)
    def redact(self, value): return self.files.redactor.clean(value)
    def apply(self, proposal_id, prepared):
        self.calls += 1
        return self.files.apply(proposal_id, prepared)


@pytest.fixture
def engineering(pg_engine, tmp_path):
    actor = ActorContext('reviewer', 'shared', frozenset({'chat:read', 'chat:write', 'artifact:read', 'dataset:read'}))
    chat = PostgresChatRepository(pg_engine, PostgresDocumentRepository(pg_engine), mode='inspection')
    session_id = UUID(chat.create(actor, 'create')['session_id'])
    turn_id = UUID(chat.enqueue(actor, session_id, 'send', 'change code')['turn_id'])
    executor = LocalExecutor(tmp_path); (tmp_path / 'src').mkdir()
    repository = PostgresEngineeringRepository(pg_engine, chat.read)
    service = EngineeringService(repository, executor, None)
    return service, repository, executor, actor, session_id, turn_id, chat


def proposal(setup):
    service, _, _, actor, session_id, turn_id, _ = setup
    return service.perform(actor, session_id, turn_id, 1, 'code.propose', {'title': 'Change', 'explanation': 'Explicit patch',
        'changes': [{'path': 'src/a.py', 'content': 'x=2\n'}]})


def test_propose_and_exact_confirmation_are_durable_and_idempotent(engineering, tmp_path, pg_engine):
    service, repository, executor, actor, *_ = engineering
    card = proposal(engineering); pid = UUID(card['proposal_id'])
    assert card['status'] == 'pending' and not (tmp_path / 'src/a.py').exists()
    assert proposal(engineering) == card
    with pytest.raises(CoreError) as mismatch: service.confirm(actor, pid, 'confirm', 'f' * 64)
    assert mismatch.value.code == 'STALE_REVISION' and executor.calls == 0
    applied = service.confirm(actor, pid, 'confirm', card['fingerprint'])
    assert applied['status'] == 'applied' and executor.calls == 1
    assert service.confirm(actor, pid, 'confirm', card['fingerprint']) == applied
    assert executor.calls == 1
    with pytest.raises(CoreError): service.confirm(actor, pid, 'another-key', card['fingerprint'])
    assert (tmp_path / 'src/a.py').read_text() == 'x=2\n'


def test_concurrent_same_confirmation_only_applies_once(engineering):
    service, _, executor, actor, *_ = engineering
    card = proposal(engineering)
    def confirm(_): return service.confirm(actor, UUID(card['proposal_id']), 'concurrent', card['fingerprint'])
    with ThreadPoolExecutor(max_workers=2) as pool: cards = list(pool.map(confirm, range(2)))
    assert all(item['status'] == 'applied' for item in cards)
    assert executor.calls == 1


def test_scope_actor_permission_and_rejection_are_enforced(engineering, tmp_path):
    service, _, executor, actor, *_ = engineering
    card = proposal(engineering); pid = UUID(card['proposal_id'])
    for foreign in (replace(actor, actor_id='foreign'), replace(actor, data_scope='foreign'), replace(actor, permissions=frozenset({'chat:read'}))):
        with pytest.raises(CoreError): service.confirm(foreign, pid, 'confirm', card['fingerprint'])
    result = service.reject(actor, pid, 'reject', card['fingerprint'])
    assert result['status'] == 'rejected' and not (tmp_path / 'src/a.py').exists()
    with pytest.raises(CoreError): service.confirm(actor, pid, 'confirm', card['fingerprint'])
    assert executor.calls == 0


def test_stale_baseline_is_failed_and_cannot_be_retried(engineering, tmp_path):
    service, _, executor, actor, *_ = engineering
    card = proposal(engineering); pid = UUID(card['proposal_id'])
    (tmp_path / 'src/a.py').write_text('human=3\n')
    with pytest.raises(CoreError): service.confirm(actor, pid, 'confirm', card['fingerprint'])
    assert service.read(actor, pid)['status'] == 'failed'
    assert service.confirm(actor, pid, 'confirm', card['fingerprint'])['status'] == 'failed'
    assert executor.calls == 1 and (tmp_path / 'src/a.py').read_text() == 'human=3\n'


def test_lost_response_never_repeats_write(engineering):
    service, _, executor, actor, *_ = engineering
    card = proposal(engineering); pid = UUID(card['proposal_id'])
    original = executor.apply
    def disconnected(*args): original(*args); raise OSError('lost connection')
    executor.apply = disconnected
    with pytest.raises(CoreError): service.confirm(actor, pid, 'confirm', card['fingerprint'])
    assert service.confirm(actor, pid, 'confirm', card['fingerprint'])['status'] == 'unknown'
    assert executor.calls == 1


def test_session_deletion_removes_proposals_without_fk_conflict(engineering, pg_engine):
    service, _, _, actor, session_id, turn_id, chat = engineering
    card = proposal(engineering)
    with chat.claim() as claimed:
        assert claimed['turn_id'] == turn_id
        chat.finish(turn_id, answer='ready', results=[card])
    chat.delete(actor, session_id, 'delete')
    with pytest.raises(CoreError): service.read(actor, UUID(card['proposal_id']))
    with pg_engine.connect() as connection:
        assert not connection.execute(select(engineering_proposals)).first()


def test_confirmation_command_key_cannot_be_replayed_for_another_proposal(engineering):
    service, _, executor, actor, session_id, turn_id, _ = engineering
    first = proposal(engineering)
    second = service.perform(actor, session_id, turn_id, 2, 'code.propose', {'title': 'Other', 'explanation': 'Other file',
        'changes': [{'path': 'src/b.py', 'content': 'x=3\n'}]})
    service.confirm(actor, UUID(first['proposal_id']), 'one-browser-command', first['fingerprint'])
    with pytest.raises(CoreError) as replay:
        service.confirm(actor, UUID(second['proposal_id']), 'one-browser-command', second['fingerprint'])
    assert replay.value.code == 'IDEMPOTENCY_CONFLICT'
    assert executor.calls == 1


def test_http_confirmation_cannot_supply_changed_content(engineering, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from bervel.modules.inspection.api import engineering_router
    service, _, executor, actor, *_ = engineering
    card = proposal(engineering)
    app = FastAPI(); app.include_router(engineering_router(service, lambda: actor))
    http = TestClient(app)
    path = '/api/v0.1/inspection/proposals/' + card['proposal_id']
    assert http.get(path).json()['fingerprint'] == card['fingerprint']
    body = {'command_key': 'http-confirm', 'fingerprint': card['fingerprint']}
    assert http.post(path + '/confirm', json={**body, 'changes': []}).status_code == 422
    assert not (tmp_path / 'src/a.py').exists()
    result = http.post(path + '/confirm', json=body)
    assert result.status_code == 200 and result.json()['status'] == 'applied'
    assert executor.calls == 1


def test_proposal_card_read_redacts_credentials_discovered_after_creation(engineering):
    service, _, executor, actor, session_id, turn_id, _ = engineering
    secret = 'later-identified-credential-7766'
    card = service.perform(actor, session_id, turn_id, 1, 'code.propose', {'title': 'Change', 'explanation': 'New value',
        'changes': [{'path': 'src/a.py', 'content': 'value="' + secret + '"\n'}]})
    executor.files.redactor.observe({'password': secret})
    assert secret not in __import__('json').dumps(service.read(actor, UUID(card['proposal_id'])))


def test_central_known_external_credential_is_rejected_before_persistence(engineering, pg_engine):
    service, repository, executor, actor, session_id, turn_id, _ = engineering
    secret = 'external-mox-credential-5511'
    service.safety_filter = SecretRedactor([secret]).clean
    with pytest.raises(CoreError):
        service.perform(actor, session_id, turn_id, 1, 'code.propose', {'title': 'Change', 'explanation': 'New value',
            'changes': [{'path': 'src/a.py', 'content': 'value="' + secret + '"\n'}]})
    with pg_engine.connect() as connection:
        assert not connection.execute(select(engineering_proposals)).first()


def test_new_central_credential_prevents_confirming_previously_clean_proposal(engineering, tmp_path):
    service, _, executor, actor, session_id, turn_id, _ = engineering
    secret = 'external-provider-credential-4411'
    central = SecretRedactor(); service.safety_filter = central.clean
    card = service.perform(actor, session_id, turn_id, 1, 'code.propose', {'title': 'Change', 'explanation': 'New value',
        'changes': [{'path': 'src/a.py', 'content': 'value="' + secret + '"\n'}]})
    central.observe({'password': secret})
    assert secret not in __import__('json').dumps(service.read(actor, UUID(card['proposal_id'])))
    with pytest.raises(CoreError): service.confirm(actor, UUID(card['proposal_id']), 'confirm', card['fingerprint'])
    assert executor.calls == 0 and not (tmp_path / 'src/a.py').exists()
    assert service.read(actor, UUID(card['proposal_id']))['status'] == 'failed'
