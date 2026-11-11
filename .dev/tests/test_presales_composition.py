from types import SimpleNamespace
from unittest.mock import patch


def test_worker_cli_accepts_presales_role_without_starting_another_module(monkeypatch):
    from bervel.__main__ import main
    from bervel.core.config import Settings
    configured = SimpleNamespace()
    monkeypatch.setattr('sys.argv', ['bervel', 'worker', '--module', 'presales', '--once'])
    with patch.object(Settings, 'from_environment', return_value=configured), patch('bervel.worker.run_worker') as worker:
        assert main() == 0
    worker.assert_called_once_with(configured, role='presales', once=True)


def test_public_presales_builder_has_dedicated_queue_and_company_service(services, pg_engine, tmp_path):
    from bervel.presales_setup import build_presales
    from test_presales_service import Company
    from test_presales_jobs import Model
    company = Company()
    result = build_presales(pg_engine, services[0], company, Model(), workspace=tmp_path / 'processing')
    assert result.company is company
    assert result.repository.engine is pg_engine
    assert result.reader is not None and result.renderer is not None
