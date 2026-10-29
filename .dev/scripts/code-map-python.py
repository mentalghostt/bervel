"""Read Python source as syntax only; never import or execute application code."""
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if len(sys.argv) > 1:
    fixture_root = Path(sys.argv[1]).resolve()
    if not fixture_root.is_relative_to(ROOT / '.dev'):
        raise ValueError('Resolver fixtures must stay inside this project .dev')
    ROOT = fixture_root
nodes, edges, errors, files = [], [], [], {}
by_id, scopes, trees, modules, scope_of = {}, {}, {}, {}, {}
pending_imports, expressions, assignments = [], [], []


def segment(tree, file, limit=300):
    return (ast.get_source_segment(files[file]['source'], tree) or '')[:limit]


def add(name, kind, parent, file, tree, **extra):
    line = getattr(tree, 'lineno', 1)
    end = getattr(tree, 'end_lineno', line) or line
    identity = f'{parent}/{kind}:{name}'
    if identity in by_id:
        n = by_id[identity]
        if kind in ('variable', 'field', 'constant'):
            n.setdefault('writes', []).append(line)
            return identity
        identity += f'@{line}:{getattr(tree, "col_offset", 0)}'
    n = dict(id=identity, name=name, kind=kind, parent=parent, file=file, line=line, end=end, **extra)
    nodes.append(n)
    by_id[identity] = n
    return identity


def new_scope(identity, parent):
    scopes[identity] = {'parent': parent, 'names': {}, 'global': set(), 'nonlocal': set()}


def put(scope, name, identity):
    scopes[scope]['names'][name] = identity


def declare_target(tree, scope, file, value=None):
    if isinstance(tree, (ast.Tuple, ast.List)):
        for i, item in enumerate(tree.elts):
            component = value.elts[i] if isinstance(value, (ast.Tuple, ast.List)) and len(value.elts) == len(tree.elts) else value
            declare_target(item, scope, file, component)
    elif isinstance(tree, ast.Starred):
        declare_target(tree.value, scope, file, value)
    elif isinstance(tree, ast.Name):
        dest = scope
        if tree.id in scopes[scope]['global']:
            dest = 'file:' + file
        elif tree.id in scopes[scope]['nonlocal']:
            dest = scopes[scope]['parent']
            while dest and tree.id not in scopes[dest]['names']:
                dest = scopes[dest]['parent']
            dest = dest or scope
        old = scopes[dest]['names'].get(tree.id)
        if old and by_id[old]['kind'] in ('parameter', 'variable', 'constant', 'field'):
            by_id[old].setdefault('writes', []).append(tree.lineno)
            identity = old
        else:
            kind = 'field' if by_id[dest]['kind'] == 'class' else 'constant' if tree.id.isupper() else 'variable'
            identity = add(tree.id, kind, dest, file, tree, writes=[tree.lineno])
            put(dest, tree.id, identity)
        if value:
            by_id[identity].setdefault('value', segment(value, file))
            assignments.append((identity, value, scope, file))
    elif isinstance(tree, ast.Attribute) and isinstance(tree.value, ast.Name) and tree.value.id in ('self', 'cls'):
        dest = receiver_class(tree.value.id, scope)
        if dest:
            identity = add(tree.attr, 'field', dest, file, tree, writes=[tree.lineno])
            put(dest, tree.attr, identity)
            if value:
                by_id[identity].setdefault('value', segment(value, file))
                assignments.append((identity, value, scope, file))


def walk(tree, scope, file):
    scope_of[id(tree)] = scope
    if isinstance(tree, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
        is_class = isinstance(tree, ast.ClassDef)
        name = getattr(tree, 'name', f'λ@{tree.lineno}:{tree.col_offset}')
        kind = 'class' if is_class else 'method' if by_id[scope]['kind'] == 'class' else 'function'
        info = {'doc': ast.get_docstring(tree) or ''} if not isinstance(tree, ast.Lambda) else {}
        identity = add(name, kind, scope, file, tree, decorators=[segment(d, file) for d in getattr(tree, 'decorator_list', [])], **info)
        if not isinstance(tree, ast.Lambda):
            put(scope, name, identity)
        new_scope(identity, scope)
        if not is_class:
            args = tree.args
            params = [*args.posonlyargs, *args.args, *args.kwonlyargs]
            if args.vararg:
                params.append(args.vararg)
            if args.kwarg:
                params.append(args.kwarg)
            for index, arg in enumerate(params):
                decorators = by_id[identity]['decorators']
                receiver = index == 0 and by_id[scope]['kind'] == 'class' and 'staticmethod' not in decorators and (arg.arg == 'self' or arg.arg == 'cls' and 'classmethod' in decorators)
                pid = add(arg.arg, 'parameter', identity, file, arg, annotation=segment(arg.annotation, file) if arg.annotation else '', receiver=receiver)
                put(identity, arg.arg, pid)
            by_id[identity]['signature'] = segment(tree, file).split('\n')[0]
            by_id[identity]['returns'] = segment(tree.returns, file) if getattr(tree, 'returns', None) else ''
            by_id[identity]['returnExpressions'] = []
            for default in [*args.defaults, *[d for d in args.kw_defaults if d]]:
                walk(default, scope, file)
        for dec in getattr(tree, 'decorator_list', []):
            walk(dec, scope, file)
        if is_class:
            by_id[identity]['bases'] = [segment(b, file) for b in tree.bases]
            by_id[identity]['decorators'] = [segment(d, file) for d in tree.decorator_list]
        if isinstance(tree, ast.Lambda):
            walk(tree.body, identity, file)
        else:
            # global/nonlocal applies to the whole function, not only subsequent lines.
            for item in tree.body:
                if isinstance(item, ast.Global):
                    scopes[identity]['global'].update(item.names)
                if isinstance(item, ast.Nonlocal):
                    scopes[identity]['nonlocal'].update(item.names)
            for item in tree.body:
                walk(item, identity, file)
        return
    if isinstance(tree, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        identity = add(f'comprehension@{tree.lineno}:{tree.col_offset}', 'scope', scope, file, tree)
        new_scope(identity, scope)
        # The first iterable is evaluated in the enclosing scope.
        for i, gen in enumerate(tree.generators):
            walk(gen.iter, scope if i == 0 else identity, file)
            declare_target(gen.target, identity, file)
            walk(gen.target, identity, file)
            for condition in gen.ifs:
                walk(condition, identity, file)
        for item in ('elt', 'key', 'value'):
            if hasattr(tree, item):
                walk(getattr(tree, item), identity, file)
        return
    if isinstance(tree, (ast.Import, ast.ImportFrom)):
        module = modules[file]
        for alias in tree.names:
            if isinstance(tree, ast.Import):
                target = alias.name
                name = alias.asname or alias.name.split('.')[0]
                if not alias.asname:
                    target = alias.name.split('.')[0]
            else:
                prefix = module.split('.') if file.endswith('/__init__.py') else module.split('.')[:-1]
                if tree.level:
                    prefix = prefix[:len(prefix) - tree.level + 1]
                    target = '.'.join(prefix + ([tree.module] if tree.module else []) + [alias.name])
                else:
                    target = (tree.module + '.' if tree.module else '') + alias.name
                name = alias.asname or alias.name
            identity = add(name, 'import', scope, file, tree, importPath=target)
            put(scope, name, identity)
            pending_imports.append((identity, target))
        return
    if isinstance(tree, (ast.Assign, ast.AnnAssign, ast.NamedExpr, ast.AugAssign)):
        targets = tree.targets if isinstance(tree, ast.Assign) else [tree.target]
        for target in targets:
            declare_target(target, scope, file, tree.value)
            if isinstance(tree, ast.AnnAssign) and isinstance(target, ast.Name):
                by_id[scopes[scope]['names'][target.id]]['annotation'] = segment(tree.annotation, file)
    if isinstance(tree, (ast.For, ast.AsyncFor)):
        declare_target(tree.target, scope, file, tree.iter)
    if isinstance(tree, (ast.With, ast.AsyncWith)):
        for item in tree.items:
            if item.optional_vars:
                declare_target(item.optional_vars, scope, file, item.context_expr)
    if isinstance(tree, ast.ExceptHandler) and tree.name:
        dummy = ast.Name(id=tree.name, ctx=ast.Store(), lineno=tree.lineno, end_lineno=tree.lineno)
        declare_target(dummy, scope, file)
    if isinstance(tree, ast.Return) and 'returnExpressions' in by_id[scope]:
        by_id[scope]['returnExpressions'].append(segment(tree.value, file) if tree.value else 'None')
    if isinstance(tree, (ast.Call, ast.Name, ast.Attribute)):
        expressions.append((tree, scope, file))
    for child in ast.iter_child_nodes(tree):
        walk(child, scope, file)


def lookup(name, scope):
    first = True
    while scope:
        current = scopes[scope]
        if name in current['global']:
            file_scope = scope
            while scopes[file_scope]['parent']:
                file_scope = scopes[file_scope]['parent']
            return scopes[file_scope]['names'].get(name)
        # Class namespaces do not act as closures for method bodies.
        if (first or by_id[scope]['kind'] != 'class') and name in current['names']:
            return current['names'][name]
        scope = current['parent']
        first = False


def receiver_class(name, scope):
    binding = lookup(name, scope)
    if not binding or not by_id[binding].get('receiver'):
        return None
    method = by_id[by_id[binding]['parent']]
    return method['parent']


module_ids = {}
def deref(identity, seen=None):
    seen = seen or set()
    if identity in seen:
        return identity
    seen.add(identity)
    n = by_id.get(identity, {})
    if n.get('target'):
        return deref(n['target'], seen)
    return identity


def resolve_path(value):
    if value in module_ids:
        return module_ids[value]
    parts = value.split('.')
    for cut in range(len(parts)-1, 0, -1):
        parent = module_ids.get('.'.join(parts[:cut]))
        if parent:
            for part in parts[cut:]:
                parent = deref(scopes.get(parent, {}).get('names', {}).get(part))
                if not parent:
                    break
            if parent:
                return parent


def resolve(tree, scope):
    if isinstance(tree, ast.Name):
        return deref(lookup(tree.id, scope))
    if isinstance(tree, ast.Attribute):
        if isinstance(tree.value, ast.Name) and tree.value.id in ('self', 'cls'):
            parent = receiver_class(tree.value.id, scope)
        else:
            parent = resolve(tree.value, scope)
        if not parent:
            return None
        parent = by_id[parent].get('instanceOf', parent)
        if parent in scopes:
            found = scopes[parent]['names'].get(tree.attr)
            if found:
                return deref(found)
        n = by_id[parent]
        if n['kind'] == 'import':
            return resolve_path(n['importPath'] + '.' + tree.attr)
    return None


def edge(a, b, kind, file, line, **extra):
    if a and b and a != b:
        edges.append(dict(from_=a, to=b, kind=kind, file=file, line=line, **{'certainty': 'syntax', **extra}))


for path in sorted((ROOT / 'src/bervel').rglob('*.py')):
    file = path.relative_to(ROOT).as_posix()
    with path.open(encoding='utf-8', newline='') as source_file:
        source = source_file.read()
    files[file] = {'source': source, 'parser': 'Python ast'}
    module = file.removeprefix('src/').removesuffix('.py').replace('/', '.')
    if module.endswith('.__init__'):
        module = module[:-9]
    modules[file] = module
    identity = 'file:' + file
    nodes.append(dict(id=identity, name=path.name, kind='file', file=file, line=1, end=len(source.split('\n'))))
    by_id[identity] = nodes[-1]
    new_scope(identity, None)
    module_ids[module] = identity
    try:
        trees[file] = ast.parse(source, filename=file)
        walk(trees[file], identity, file)
    except SyntaxError as error:
        errors.append(dict(file=file, message=str(error)))

# Export/re-export bindings can form several hops.
for _ in range(5):
    for identity, target in pending_imports:
        found = resolve_path(target)
        if found:
            by_id[identity]['target'] = found
for identity, target in pending_imports:
    n = by_id[identity]
    found = deref(identity)
    if found != identity:
        edge(n['parent'], found, 'imports', n['file'], n['line'])
        edge(identity, found, 'imports', n['file'], n['line'])

for _ in range(2):
    for identity, value, scope, file in assignments:
        # More than one assignment means the runtime receiver is not established.
        # Never retain the class from an earlier assignment or pick the last branch.
        if isinstance(value, ast.Call) and len(by_id[identity].get('writes', [])) == 1:
            target = resolve(value.func, scope)
            if target and by_id[target]['kind'] == 'class':
                by_id[identity]['instanceOf'] = target
        if isinstance(value, (ast.Name, ast.Attribute)):
            target = resolve(value, scope)
            if target:
                edge(identity, target, 'uses', file, value.lineno)

for tree, scope, file in expressions:
    if isinstance(tree, ast.Call):
        target = resolve(tree.func, scope)
        if isinstance(tree.func, ast.Name) and tree.func.id == 'cls':
            current = receiver_class('cls', scope)
            target = current or target
        n = by_id[scope]
        call = dict(text=segment(tree.func, file), line=tree.lineno, arguments=[segment(a, file, 160) for a in tree.args], keywords=[(k.arg or '**') + '=' + segment(k.value, file, 160) for k in tree.keywords])
        if target and by_id[target]['kind'] in ('function', 'method', 'class'):
            call['target'] = target
            kind, certainty = 'calls', 'syntax'
            proof = 'Лексическое имя / импорт разрешены до определения; адресат не выбирается по совпадению имени метода.'
            if isinstance(tree.func, ast.Attribute):
                receiver = tree.func.value
                parent = resolve(receiver, scope)
                if isinstance(receiver, ast.Name) and receiver.id in ('self', 'cls'):
                    kind, certainty = 'method_ref', 'member-reference'
                    proof = 'self/cls текущего класса: ссылка на его объявленный метод. Реальная диспетчеризация и переопределения во время работы не доказаны.'
                elif parent and by_id[parent].get('instanceOf'):
                    kind, certainty = 'method_ref', 'inferred'
                    proof = f"Тип получателя {segment(receiver, file)} выведен из единственного присваивания конструктора: {by_id[parent].get('value', '')}. Реальный объект во время работы не доказан."
                elif isinstance(receiver, ast.Attribute):
                    # Chained fields may carry constructor-derived types.
                    kind, certainty = 'method_ref', 'member-reference'
                    proof = 'Цепочка членов разрешена по объявлениям и присваиваниям полей; это синтаксическая ссылка, а не доказательство runtime-объекта.'
                else:
                    proof = 'Квалифицированное имя класса/модуля и его член разрешены через локальную область и импорты.'
            elif isinstance(tree.func, ast.Name) and tree.func.id == 'cls':
                kind, certainty = 'method_ref', 'class-reference'
                proof = 'cls связан с текущим классом через classmethod; конкретный класс при наследовании во время работы не доказан.'
            call.update(bindingKind=kind, certainty=certainty, proof=proof)
            edge(scope, target, kind, file, tree.lineno, certainty=certainty, proof=proof)
        else:
            call['reason'] = 'Внешняя функция или динамический получатель; конкретная реализация статически не установлена.'
        n.setdefault('calls', []).append(call)
    elif isinstance(tree, ast.Name) and isinstance(tree.ctx, ast.Load):
        target = resolve(tree, scope)
        if target:
            edge(scope, target, 'uses', file, tree.lineno)
    elif isinstance(tree, ast.Attribute) and isinstance(tree.ctx, ast.Load):
        target = resolve(tree, scope)
        if target:
            edge(scope, target, 'uses', file, tree.lineno)

for e in edges:
    e['from'] = e.pop('from_')
print(json.dumps(dict(nodes=nodes, edges=edges, errors=errors, files=files), ensure_ascii=False))
