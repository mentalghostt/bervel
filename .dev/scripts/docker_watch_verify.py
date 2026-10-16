"""Exercise actual Compose Watch with temporary, non-code files under the project root."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from urllib.request import build_opener, ProxyHandler
from uuid import uuid4

root = Path(__file__).resolve().parents[2]
docker = shutil.which('docker') or r'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
compose = [docker, 'compose', '-f', 'compose.yaml', '-f', 'compose.orchestration.yaml',
           '-f', '.dev/docker/compose.dev.yaml']
marker = uuid4().hex
backend = root / 'src/bervel/docker_watch_probe.txt'
frontend = root / 'frontend/public/docker-watch-probe.txt'
if backend.exists() or frontend.exists():
    raise RuntimeError('Probe paths must not already exist')
log_path = root / '.dev/local/docker/watch.log'
opener = build_opener(ProxyHandler({}))


def wait_for(predicate, description, timeout=40):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except (OSError, subprocess.CalledProcessError):
            pass
        time.sleep(0.3)
    raise TimeoutError(description)


def backend_text():
    return subprocess.check_output([*compose, 'exec', '-T', 'api', 'python', '-c',
        "from pathlib import Path;p=Path('/usr/local/lib/python3.12/site-packages/bervel/docker_watch_probe.txt');print(p.read_text() if p.exists() else '')"],
        cwd=root, text=True, stderr=subprocess.DEVNULL).strip()


def api_started_at():
    container = subprocess.check_output([*compose, 'ps', '-q', 'api'], cwd=root, text=True).strip()
    return subprocess.check_output([docker, 'inspect', '--format', '{{.State.StartedAt}}', container], text=True).strip()


def frontend_text():
    return opener.open('http://127.0.0.1:5173/docker-watch-probe.txt', timeout=2).read().decode()


with log_path.open('w', encoding='utf-8') as log:
    process = subprocess.Popen([*compose, 'watch', '--no-up', 'api', 'frontend'], cwd=root,
        stdout=log, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    try:
        def watch_ready():
            if process.poll() is not None:
                raise RuntimeError('Compose Watch exited; inspect the project-local watch.log')
            return 'watch enabled' in log_path.read_text().lower()
        wait_for(watch_ready, 'Compose Watch did not start')
        before = api_started_at()
        backend.write_text(marker)
        wait_for(lambda: backend_text() == marker, 'Backend file was not synced')
        wait_for(lambda: api_started_at() != before, 'Backend was not restarted after synchronization')
        frontend.write_text(marker)
        wait_for(lambda: frontend_text() == marker, 'Frontend file was not synced')
        backend.unlink()
        frontend.unlink()
        wait_for(lambda: backend_text() == '', 'Backend deletion was not synced')
        wait_for(lambda: frontend_text() != marker, 'Frontend deletion was not synced')
        report = {'backend_sync_and_restart': True, 'frontend_sync': True, 'deletions_synced': True}
        (root / '.dev/local/docker/watch-verification.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
    finally:
        backend.unlink(missing_ok=True)
        frontend.unlink(missing_ok=True)
        if process.poll() is None:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], check=True, capture_output=True)
            else:
                process.terminate()
        process.wait(timeout=15)
