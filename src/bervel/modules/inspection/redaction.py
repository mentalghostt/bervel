"""Remove credentials before a source can reach a model or durable chat data."""
import json
import re
from urllib.parse import urlsplit

HIDDEN = '[СКРЫТО]'
SENSITIVE = re.compile(r'(?i)(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|authorization|cookie|credentials|private[_-]?key|encryption[_-]?key|^key$|пароль)')
SECRET_NAME = r'(?:[\w.-]*(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|authorization|cookie|credentials|private[_-]?key|encryption[_-]?key|пароль)[\w.-]*|key)'
ASSIGNMENT = re.compile(r'''(?im)(?<![\w.-])(["']?(''' + SECRET_NAME + r''')["']?[ \t]*[:=][ \t]*)("(?:\\.|[^"\\])*"|'[^']*'|[^\r\n,;}&]+)''')
BLOCK = re.compile(r'(?im)^([ \t]*)(' + SECRET_NAME + r')[ \t]*:[ \t]*[|>][^\r\n]*\r?\n((?:(?:\1[ \t]+)[^\r\n]*(?:\r?\n|$))+)')
XML_SECRET = re.compile(r'(?is)<(' + SECRET_NAME + r')(?:\s[^>]*)?>(.*?)</\1\s*>')
# Match the authority delimiter directly. Searching an unbounded protocol name
# from every character makes a long minified/base64 line quadratic.
URL_PASSWORD = re.compile(r'(://[^\s/:@]+:)([^\s/@]+)(@)')


def sensitive(name: str) -> bool:
    return name != 'authorization_quote' and bool(SENSITIVE.search(name)) and not name.endswith(('_configured', '_available', '_file', '_path', '_url'))


class SecretRedactor:
    def __init__(self, secrets=()):
        self.secrets = {str(value) for value in secrets if value and len(str(value)) >= 4}

    def observe(self, value) -> None:
        if isinstance(value, dict):
            for name, item in value.items():
                if sensitive(str(name)) and isinstance(item, (str, int)) and not isinstance(item, bool):
                    if len(str(item)) >= 4: self.secrets.add(str(item))
                self.observe(item)
        elif isinstance(value, list):
            for item in value: self.observe(item)
        elif isinstance(value, str):
            for match in BLOCK.finditer(value):
                for line in match.group(3).splitlines():
                    if len(line.strip()) >= 4: self.secrets.add(line.strip())
            for match in XML_SECRET.finditer(value):
                if len(match.group(2).strip()) >= 4: self.secrets.add(match.group(2).strip())
            for line in value.splitlines():
                if '=' in line:
                    name, item = line.split('=', 1)
                    if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name.strip()):
                        try: self.observe(json.loads(item.strip().strip("'")))
                        except ValueError: pass
            for match in ASSIGNMENT.finditer(value):
                item = match.group(3).strip().strip('"\'')
                if sensitive(match.group(2)) and len(item) >= 4: self.secrets.add(item)
            for match in re.finditer(r'(?im)(?<![\w-])(?:cookie|set-cookie)[ \t]*:[ \t]*([^\r\n]+)', value):
                for part in match.group(1).split(';'):
                    if '=' in part and len(part.split('=', 1)[1]) >= 4: self.secrets.add(part.split('=', 1)[1])
            for match in re.finditer(r'(?i)\b(?:bearer|basic)[ \t]+([^\s,"\';}]+)', value):
                if len(match.group(1)) >= 4: self.secrets.add(match.group(1))
            for match in URL_PASSWORD.finditer(value): self.secrets.add(match.group(2))
            try:
                parsed = json.loads(value)
                if isinstance(parsed, (dict, list)): self.observe(parsed)
            except (ValueError, TypeError):
                pass

    def clean(self, value):
        self.observe(value)
        return self._clean(value)

    def source(self, value: str, path: str) -> str:
        name = path.replace('\\', '/').rsplit('/', 1)[-1]
        if 'secrets' in path.replace('\\', '/').split('/') or name in {'model-credentials', 'api-key', 'token', 'password', 'credentials.json', 'secrets.json'}:
            try: parsed = json.loads(value)
            except ValueError: parsed = None
            if not isinstance(parsed, (dict, list)):
                if value.strip(): self.secrets.add(value.strip())
                return HIDDEN + '\n' * value.count('\n')
            def collect(item):
                if isinstance(item, dict):
                    for part in item.values(): collect(part)
                elif isinstance(item, list):
                    for part in item: collect(part)
                elif isinstance(item, str) and len(item) >= 4: self.secrets.add(item)
            collect(parsed)
        return self.clean(value)

    def _clean(self, value):
        if isinstance(value, dict):
            return {str(name): HIDDEN if sensitive(str(name)) and item is not None and not isinstance(item, bool)
                else self._clean(item) for name, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._clean(item) for item in value]
        if isinstance(value, bytes): return {'binary_bytes': len(value), 'notice': 'Бинарное значение не раскрывается.'}
        if not isinstance(value, str): return value
        def hidden(match): return HIDDEN + '\n' * match.group(0).count('\n')
        value = re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----', hidden, value, flags=re.S)
        value = BLOCK.sub(lambda m: m.group(1) + m.group(2) + ': ' + HIDDEN + '\n' * m.group(0).count('\n'), value)
        value = XML_SECRET.sub(lambda m: '<' + m.group(1) + '>' + HIDDEN + '\n' * m.group(2).count('\n') + '</' + m.group(1) + '>', value)
        value = URL_PASSWORD.sub(lambda m: m.group(1) + HIDDEN + m.group(3), value)
        value = re.sub(r'(?im)((?:authorization\s*:\s*|\bbearer\s+))[^\r\n]+', lambda m: m.group(1) + HIDDEN, value)
        value = re.sub(r'(?im)(?<![\w-])((?:cookie|set-cookie)[ \t]*:[ \t]*)[^\r\n]+', lambda m: m.group(1) + HIDDEN, value)
        value = ASSIGNMENT.sub(lambda m: m.group(1) + HIDDEN if sensitive(m.group(2)) else m.group(0), value)
        for secret in sorted(self.secrets, key=len, reverse=True): value = value.replace(secret, HIDDEN + '\n' * secret.count('\n'))
        return value
