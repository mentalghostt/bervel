"""Build using an in-memory context containing application files only, with no .dev."""
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

root = Path(__file__).resolve().parents[2]
docker = shutil.which('docker') or r'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
selected = [root / name for name in ('Dockerfile', 'pyproject.toml', 'requirements/build.lock', 'requirements/runtime.lock')]
selected += [path for path in (root / 'src').rglob('*') if path.is_file()
             and '__pycache__' not in path.parts and path.suffix != '.pyc' and not any(p.endswith('.egg-info') for p in path.parts)]
stream = io.BytesIO()
with tarfile.open(fileobj=stream, mode='w:gz') as archive:
    for path in selected:
        archive.add(path, arcname=path.relative_to(root).as_posix(), recursive=False)
log_path = root / '.dev/local/docker/backend/portable-build.log'
with log_path.open('wb') as log:
    subprocess.run([docker, 'build', '--tag', 'bervel-api:portable-check', '--file', 'Dockerfile', '-'],
                   input=stream.getvalue(), stdout=log, stderr=subprocess.STDOUT, check=True)
probe = """import json,os,pathlib,bervel
from importlib.resources import files
assert 'site-packages' in bervel.__file__
assert not pathlib.Path('/app/.dev').exists()
assert files('bervel').joinpath('migrations/versions/0002_core_extraction.py').is_file()
assert files('bervel.modules.document_review').joinpath('manifest.json').is_file()
assert os.getuid()==10001
print(json.dumps({'installed_package':True,'schemas_and_migrations':True,'nonroot':True}))
"""
result = json.loads(subprocess.check_output([docker, 'run', '--rm', 'bervel-api:portable-check', 'python', '-c', probe], text=True))
result.update(context_files=len(selected), development_directory_in_context=False)
(root / '.dev/local/docker/backend/portability.json').write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2))
