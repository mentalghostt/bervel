"""Check the real Compose model and launcher without starting project services."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = {
    'postgres', 'migrate', 'api', 'frontend', 'codex',
    'worker-knowledge', 'worker-chat', 'worker-ai-tender-radar', 'worker-presales',
}
INSPECTION = {'inspection-reader', 'inspection-test-postgres', 'worker-inspection'}


def compose_model(*profiles, overlay=None):
    if not shutil.which('docker'):
        pytest.skip('Docker CLI required for Compose model validation')
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(('BERVEL_', 'COMPOSE_')) and key != 'KONTUR_API_KEY'}
    command = ['docker', 'compose', '--env-file', '.env.example', '-f', 'compose.yaml']
    if overlay:
        command += ['-f', overlay]
    for profile in profiles:
        command += ['--profile', profile]
    result = subprocess.run(command + ['config', '--format', 'json'], cwd=ROOT,
                            env=environment, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)['services']


def test_default_start_contains_all_product_workers_and_no_test_services():
    assert set(compose_model()) == RUNTIME


def test_inspection_is_explicit_and_keeps_its_dependencies():
    services = compose_model('inspection')
    assert set(services) == RUNTIME | INSPECTION
    assert {'inspection-reader', 'inspection-test-postgres', 'codex'} <= set(
        services['worker-inspection']['depends_on'])


def test_e2e_remains_available_only_when_requested():
    services = compose_model('test', overlay='.dev/docker/compose.e2e.yaml')
    assert {'tests', 'migrate-test', 'api-e2e', 'frontend-e2e', 'e2e',
            'worker-knowledge-e2e', 'worker-chat-e2e'} <= set(services)
    assert not INSPECTION & set(services)


def test_dev_watches_presales_without_starting_inspection():
    services = compose_model(overlay='.dev/docker/compose.dev.yaml')
    assert set(services) == RUNTIME
    assert services['worker-presales']['develop']['watch']


def run_launcher(tmp_path, action, *, inspection=False, fail_e2e=False, fail_cleanup=False):
    powershell = shutil.which('pwsh') or shutil.which('powershell')
    if not powershell:
        pytest.skip('PowerShell required for launcher validation')
    calls_file = tmp_path / 'calls.jsonl'
    mock = tmp_path / 'docker-mock.ps1'
    mock.write_text(
        'ConvertTo-Json -InputObject @($args) -Compress | Add-Content -LiteralPath $env:DOCKER_TEST_CALLS\n'
        '$global:LASTEXITCODE = 0\n'
        'if ($env:DOCKER_TEST_FAIL -eq "1" -and $args -contains "test:frontend") {\n'
        '    $global:LASTEXITCODE = 41\n'
        '}\n'
        'if ($env:DOCKER_TEST_CLEANUP_FAIL -eq "1" -and $args -contains "rm") {\n'
        '    $global:LASTEXITCODE = 42\n'
        '}\n', encoding='utf-8')
    harness = tmp_path / 'harness.ps1'
    harness.write_text(
        'function Get-Command { param($Name, $ErrorAction)\n'
        '    if ($Name -ne "docker") { throw "Unexpected command lookup" }\n'
        '    [pscustomobject]@{Source=$env:DOCKER_TEST_MOCK}\n'
        '}\n'
        '& $env:DOCKER_TEST_LAUNCHER @args\n', encoding='utf-8')
    environment = dict(os.environ, DOCKER_TEST_CALLS=str(calls_file), DOCKER_TEST_MOCK=str(mock),
                       DOCKER_TEST_LAUNCHER=str(ROOT / '.dev/scripts/docker.ps1'),
                       DOCKER_TEST_FAIL='1' if fail_e2e else '0',
                       DOCKER_TEST_CLEANUP_FAIL='1' if fail_cleanup else '0')
    command = [powershell, '-NoProfile', '-File', str(harness), '-Action', action]
    if inspection:
        command.append('-Inspection')
    result = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True)
    calls = [json.loads(line) for line in calls_file.read_text(encoding='utf-8-sig').splitlines()]
    return result, calls


@pytest.mark.parametrize('action', ['start', 'dev', 'worker'])
def test_launcher_starts_presales_and_excludes_inspection_by_default(tmp_path, action):
    result, calls = run_launcher(tmp_path, action)
    assert result.returncode == 0, result.stderr
    up = next(call for call in calls if 'up' in call)
    assert 'worker-presales' in up
    assert not INSPECTION & set(up)
    assert not any('inspection-backend-tests' in call for call in calls)


def test_launcher_can_opt_into_inspection(tmp_path):
    result, calls = run_launcher(tmp_path, 'start', inspection=True)
    assert result.returncode == 0, result.stderr
    up = next(call for call in calls if 'up' in call)
    assert 'worker-inspection' in up
    assert 'inspection' in up
    assert any('inspection-backend-tests' in call for call in calls)


@pytest.mark.parametrize('fail_e2e', [False, True])
def test_e2e_removes_only_its_dependencies_even_after_failure(tmp_path, fail_e2e):
    result, calls = run_launcher(tmp_path, 'e2e', fail_e2e=fail_e2e)
    assert (result.returncode != 0) == fail_e2e
    cleanup = [call for call in calls if 'rm' in call]
    assert len(cleanup) == 1
    removal = cleanup[0]
    assert {'--stop', '--force', 'frontend-e2e', 'api-e2e', 'migrate-test'} <= set(removal)
    assert not {'down', '--volumes', '-v', 'postgres', 'api', 'frontend', 'codex'} & set(removal)
    assert all('--rm' in call for call in calls if 'run' in call)


def test_cleanup_failure_keeps_the_original_e2e_error(tmp_path):
    result, _ = run_launcher(tmp_path, 'e2e', fail_e2e=True, fail_cleanup=True)
    assert result.returncode != 0
    assert 'code 41' in result.stderr
    assert 'code 42' in result.stdout


def test_cleanup_failure_fails_an_otherwise_successful_e2e(tmp_path):
    result, _ = run_launcher(tmp_path, 'e2e', fail_cleanup=True)
    assert result.returncode != 0
    assert 'code 42' in result.stderr
