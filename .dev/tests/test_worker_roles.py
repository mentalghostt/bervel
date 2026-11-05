"""Each background process owns exactly one queue and a bounded embedding budget."""
from types import SimpleNamespace
import sys
import pytest


@pytest.mark.parametrize('role,budget',[('chat',8000),('knowledge',8000),('inspection',8000)])
def test_worker_process_executes_only_its_own_handler(monkeypatch,role,budget):
    import bervel.worker as worker
    calls=[]; budgets=[]
    def handler(name):return SimpleNamespace(run_once=lambda:calls.append(name) or False)
    services=SimpleNamespace(engine=SimpleNamespace(dispose=lambda:calls.append('dispose')),
        artifacts=SimpleNamespace(cleanup_deleted=lambda:False),
        chat=handler('chat'),knowledge=handler('knowledge'),inspection=handler('inspection'))
    def build(settings,**options):budgets.append(options['embedding_token_budget']);return services
    monkeypatch.setattr(worker,'build_services',build)
    worker.run_worker(SimpleNamespace(),role=role,once=True)
    assert calls==[role,'dispose']
    assert budgets==[budget]


def test_invalid_worker_role_is_rejected_before_connecting(monkeypatch):
    import bervel.worker as worker
    monkeypatch.setattr(worker,'build_services',lambda *_args,**_kw:pytest.fail('Must not connect'))
    with pytest.raises(ValueError):worker.run_worker(SimpleNamespace(),role='all',once=True)


def test_knowledge_worker_resumes_only_confirmed_pending_file_deletions(monkeypatch):
    import bervel.worker as worker
    calls=[]
    services=SimpleNamespace(engine=SimpleNamespace(dispose=lambda:calls.append('dispose')),
        artifacts=SimpleNamespace(cleanup_deleted=lambda:calls.append('cleanup') or False),
        knowledge=SimpleNamespace(run_once=lambda:calls.append('knowledge') or False))
    monkeypatch.setattr(worker,'build_services',lambda *_args,**_kw:services)
    worker.run_worker(SimpleNamespace(),role='knowledge',once=True)
    assert calls==['cleanup','knowledge','dispose']


def test_graceful_stop_finishes_current_portion_and_never_claims_another(monkeypatch):
    import signal
    import bervel.worker as worker
    calls=[]
    def portion():
        calls.append('claimed')
        signal.raise_signal(signal.SIGTERM)
        calls.append('saved')
        return True
    services=SimpleNamespace(engine=SimpleNamespace(dispose=lambda:calls.append('dispose')),
        artifacts=SimpleNamespace(cleanup_deleted=lambda:False),knowledge=SimpleNamespace(run_once=portion))
    monkeypatch.setattr(worker,'build_services',lambda *_args,**_kw:services)
    worker.run_worker(SimpleNamespace(),role='knowledge')
    assert calls==['claimed','saved','dispose']


@pytest.mark.parametrize('arguments',[
    ['worker'],['worker','--module','all'],['serve','--module','chat'],['migrate','--module','radar'],
])
def test_cli_requires_a_role_only_for_worker(monkeypatch,arguments):
    from bervel.__main__ import main
    monkeypatch.setattr(sys,'argv',['bervel',*arguments])
    with pytest.raises(SystemExit) as error:main()
    assert error.value.code==2


def test_cli_passes_selected_role_and_once(monkeypatch):
    from bervel.__main__ import main
    import bervel.worker as worker
    from bervel.core.config import Settings
    settings=SimpleNamespace();calls=[]
    monkeypatch.setattr(Settings,'from_environment',lambda:settings)
    monkeypatch.setattr(worker,'run_worker',lambda supplied,**options:calls.append((supplied,options)))
    monkeypatch.setattr(sys,'argv',['bervel','worker','--module','chat','--once'])
    assert main()==0
    assert calls==[(settings,{'role':'chat','once':True})]
