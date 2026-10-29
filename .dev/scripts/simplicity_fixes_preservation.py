"""Compare only fingerprints/counts, never print source records or credentials."""
from collections import Counter
import json
from pathlib import Path
import sys

folder = Path(__file__).resolve().parents[1] / 'local/simplicity-fixes'
suffix = sys.argv[1] if len(sys.argv) > 1 else 'final'
before = json.loads((folder / 'data-before.json').read_text(encoding='utf-8-sig'))
after = json.loads((folder / f'data-{suffix}.json').read_text(encoding='utf-8-sig'))
http_before = json.loads((folder / 'http-before.json').read_text(encoding='utf-8-sig'))
http_after = json.loads((folder / f'http-{suffix}.json').read_text(encoding='utf-8-sig'))
assert before['tables'].keys() == after['tables'].keys(), 'SQL table set changed'
added = {}
for name, old in before['tables'].items():
    new = after['tables'][name]
    assert not Counter(old['rows']) - Counter(new['rows']), f'Previous rows changed in {name}'
    if new['count'] != old['count']:
        added[name] = new['count'] - old['count']
assert all(after['objects'].get(key) == size for key, size in before['objects'].items()), 'Original objects changed'
assert after['openapi'] == before['openapi'], 'OpenAPI contract changed'
changed_http = [path for path, value in http_before.items() if http_after.get(path) != value]
assert not changed_http, f'Previous HTTP reads changed: {changed_http}'
result = {'previous_rows_preserved': sum(t['count'] for t in before['tables'].values()),
          'tables_unchanged': len(before['tables']), 'original_objects_preserved': len(before['objects']),
          'previous_http_reads_unchanged': len(http_before), 'openapi_equal': True,
          'added_rows': added, 'added_objects': len(after['objects']) - len(before['objects'])}
(folder / f'preservation-{suffix}.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result, indent=2))
