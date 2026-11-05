"""Architectural contracts: dependency direction and immutable migration history."""
import ast
from importlib import import_module
from pathlib import Path

import bervel
import pytest


ROOT = Path(bervel.__file__).parent


def imports(path, root=ROOT):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    package = 'bervel.' + '.'.join(path.relative_to(root).parts[:-1])
    from importlib.util import resolve_name
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            name = '.' * node.level + (node.module or '')
            yield resolve_name(name, package) if node.level else name


def test_core_does_not_select_business_modules():
    violations = [(str(p.relative_to(ROOT)), name) for p in (ROOT / 'core').rglob('*.py')
                  for name in imports(p) if name.startswith('bervel.modules') or name == 'bervel.app_setup']
    assert violations == []


def test_rules_do_not_import_transport_storage_or_siblings():
    violations = []
    paths = list((ROOT / 'core').glob('*.py'))
    paths += [p for p in (ROOT / 'modules').rglob('*.py')
              if 'infrastructure' not in p.parts and p.stem != 'api']
    for path in paths:
        for name in imports(path):
            if name.split('.')[0] in {'sqlalchemy', 'fastapi', 'pydantic', 'httpx', 'reportlab', 'openpyxl'} or '.infrastructure' in name or '.api.' in name:
                violations.append((str(path.relative_to(ROOT)), name))
    assert violations == []


def module_violations(root):
    from importlib.util import resolve_name
    violations = []
    graph = {}
    for path in (root / 'modules').rglob('*.py'):
        own = path.relative_to(root / 'modules').parts[0]
        graph.setdefault(own, set())
        source = path.read_text(encoding='utf-8')
        tree = ast.parse(source)
        aliases = {}

        def check(target, member=None):
            parts = target.split('.')
            if parts[:2] != ['bervel', 'modules'] or len(parts) < 3 or parts[2] == own:
                return
            other = parts[2]
            graph[own].add(other)
            folder = root / 'modules' / other
            internal = member and (member.startswith('_') or member == '*'
                                   or (folder / member).is_dir() or (folder / (member + '.py')).is_file())
            if len(parts) != 3 or internal:
                violations.append((str(path), target, member))

        # A public entry is bervel.modules.<name>; importing its implementation
        # or reaching it through a module alias is still internal access.
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    check(alias.name)
                    aliases[alias.asname or alias.name] = alias.name
            elif isinstance(node, ast.ImportFrom):
                package = 'bervel.' + '.'.join(path.relative_to(root).parts[:-1])
                base = resolve_name('.' * node.level + (node.module or ''), package) if node.level else node.module or ''
                for alias in node.names:
                    if base == 'bervel.modules':
                        target = base + '.' + alias.name
                        check(target)
                        aliases[alias.asname or alias.name] = target
                    else:
                        check(base, alias.name)
                        if base == 'bervel' and alias.name == 'modules':
                            aliases[alias.asname or alias.name] = 'bervel.modules'
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                value = ast.unparse(node.value)
                for alias, target in aliases.items():
                    if value == alias or value.startswith(alias + '.'):
                        check(target + value[len(alias):], node.attr)
        assert 'receipts._repeat' not in source
        assert 'receipts._finish' not in source

    def visit(name, trail=()):
        if name in trail:
            violations.append(('cycle', *trail, name))
            return
        for dependency in graph.get(name, ()):
            visit(dependency, (*trail, name))
    for name in graph:
        visit(name)
    return violations


def test_modules_use_public_entries_without_cycles_or_private_repository_helpers():
    violations = module_violations(ROOT)
    assert violations == []


def module_tree(tmp_path, first, second=''):
    root = tmp_path / 'bervel'
    for name, source in (('a', first), ('b', second)):
        folder = root / 'modules' / name
        folder.mkdir(parents=True)
        (folder / '__init__.py').write_text('def run(): pass\n')
        (folder / 'service.py').write_text(source)
        (folder / 'infrastructure').mkdir()
    return root


@pytest.mark.parametrize('source', [
    'from bervel.modules.b import run',
    'import bervel.modules.b as other\nother.run()',
])
def test_boundary_checker_allows_public_module_calls(tmp_path, source):
    assert module_violations(module_tree(tmp_path, source)) == []


@pytest.mark.parametrize('source', [
    'from bervel.modules.b.service import run',
    'from bervel.modules.b.infrastructure.tables import data',
    'from bervel.modules.b import _private',
    'from bervel.modules.b import service',
    'import bervel.modules.b as other\nother._private()',
    'import bervel.modules.b as other\nother.infrastructure.tables',
    'import bervel.modules as modules\nmodules.b._private()',
    'from bervel import modules\nmodules.b.service.run()',
    'import bervel as platform\nplatform.modules.b.infrastructure.tables',
])
def test_boundary_checker_rejects_internal_module_access(tmp_path, source):
    assert module_violations(module_tree(tmp_path, source))


@pytest.mark.parametrize('first,second', [
    ('from bervel.modules.b import run', 'from bervel.modules.a import run'),
    ('import bervel.modules as m\nm.b.run()', 'from bervel import modules as m\nm.a.run()'),
])
def test_boundary_checker_rejects_cycles_even_through_public_entries(tmp_path, first, second):
    root = module_tree(tmp_path, first, second)
    assert any('cycle' in str(v) for v in module_violations(root))


def test_history_does_not_import_live_tables():
    paths = list(ROOT.rglob('versions/000*.py'))
    assert len(paths) == 9
    assert [(p.name, n) for p in paths for n in imports(p) if n.startswith('bervel')] == []


def test_tables_have_one_metadata_and_known_owners():
    from bervel.core.infrastructure.database import metadata
    from bervel.modules.chat.infrastructure.tables import chat_sessions, chat_turns
    from bervel.modules.ai_tender_radar.infrastructure.repository import jobs
    assert all(t.metadata is metadata for t in (chat_sessions, chat_turns, jobs))
    platform = import_module('bervel.core.infrastructure.database')
    assert not hasattr(platform, 'radar_runs') and not hasattr(platform, 'chat_turns')




