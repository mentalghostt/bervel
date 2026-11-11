"""Resolve Linux locks in a disposable Python container using the tested constraints."""
import importlib.metadata
from pathlib import Path
import subprocess
import sys
import tomllib

source = Path('/source')
project = tomllib.loads((source / 'pyproject.toml').read_text())
constraints = source / '.dev/requirements-core-stage-3.1.lock'


def packages():
    return {d.metadata['Name']: d.version for d in importlib.metadata.distributions()
            if d.metadata['Name'].lower() not in {'pip'}}


def install(dependencies, report):
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-cache-dir',
                    '--constraint', str(constraints), '--report', '/reports/' + report,
                    *dependencies], check=True)


install(project['project']['dependencies'], 'runtime-install.json')
runtime = packages()
Path('/locks/runtime.lock').write_text('# Linux Python 3.12 runtime; regenerate with docker_lock.py\n' +
    ''.join(f'{name}=={version}\n' for name, version in sorted(runtime.items(), key=lambda p: p[0].lower())))
install(project['project']['optional-dependencies']['dev'], 'dev-install.json')
dev = {name: version for name, version in packages().items() if name not in runtime}
Path('/dev-locks/python-dev.lock').write_text('# Development additions to requirements/runtime.lock\n' +
    ''.join(f'{name}=={version}\n' for name, version in sorted(dev.items(), key=lambda p: p[0].lower())))
subprocess.run([sys.executable, '-m', 'pip', 'check'], check=True)
